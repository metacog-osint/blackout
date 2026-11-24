"""Retention archives (18 Nov 2026): what a run measures and the live view then drops, kept for good under history/.

Rule (owner, 18 Nov 2026): no TIC program loses data. The live view may filter, window or overwrite; storage keeps
everything. Four small derived archives, all committed to main by the workflow's history commit step, all append-only
and safe to run twice on the same snapshot (each writer skips what it already holds):

  history/blackmarble-nights.jsonl        B2: every city-night Black Marble reading, once per city and night (the
                                          snapshot keeps only the newest KEEP_NIGHTS = 30 for the page and the baseline)
  history/odin/YYYY-MM-DD.jsonl[.gz]      B3: each run's ODIN county rows. One file per UTC day: plain jsonl while the day
                                          is open, gzipped by close_days once the day has closed, then not rewritten.
                                          Lines: kind "run" (read_at, totals, coverage), "county", "unplaced".
  history/public-counts.jsonl             B4: each run's Bluesky counts {run_at, name, window_n, baseline_n}, including the
                                          runs with no surge (a surge item keeps only its own run's counts)
  history/trends-days.jsonl               each settled day of the daily series (blackout/trends.py, older than
                                          SETTLE_DAYS): a "day" row (the day was read) then one "country" row per
                                          country with something reported; written once per day, so the series
                                          outlives its 60-day window.
  history/netblocks-index.jsonl           every NetBlocks post as Blackout parsed it (blackout/netblocks.py), once per
                                          post: time, link and parsed fields. No excerpt and never the post's text.
                                          Posts NetBlocks later deletes stay here and in data/netblocks.json, marked
                                          in_account false there.
  history/gdelt-matches/YYYY-MM.jsonl.gz  B4: each GKG file's reduction (watched name -> [outlet domain, link] pairs, or a
                                          count and the first links) as it was read, before it leaves the 8-day carry.
                                          One file per month by the GKG file's stamp; new lines are added as a further gzip
                                          member (a gzip file may be several members; gzip.open reads them as one).

No post text, handle or headline text is kept: only what the live lanes already publish (counts, outlets, links).

NetBlocks' excerpts (24 Nov 2026): NetBlocks' posts are all rights reserved, and Blackout quotes a post's opening
words only while the post is a feed item. So the posts with their excerpts are kept in the PRIVATE archive only
(metacog-osint/tic-archive, lane blackout-netblocks), as one append-only netblocks-posts.jsonl in the format the
public history/netblocks-posts.jsonl had until then: `netblocks_full` appends to a restored copy and the workflow
uploads it (tools/tic_archive.py). The old public file is folded in and removed by `retire_netblocks_legacy` only once
every post of it is in the private file and the upload succeeded.
Run by `python tools/blackout.py archive` after the history step; the workflow runs it after the deploy.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

NIGHTS_FILE = "blackmarble-nights.jsonl"
COUNTS_FILE = "public-counts.jsonl"
ODIN_DIR = "odin"
GDELT_DIR = "gdelt-matches"


def _dump(row: dict) -> bytes:
    return (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def read_rows(path: Path) -> list[dict]:
    """The lines of a jsonl or jsonl.gz file ([] if absent). A line that is not JSON raises: nothing is skipped."""
    path = Path(path)
    if not path.exists():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def append_jsonl(path: Path, rows: list[dict]) -> int:
    """Append rows to a plain jsonl file; returns how many were written."""
    if not rows:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lead = b""
    if path.exists() and path.stat().st_size:
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                lead = b"\n"
    with open(path, "ab") as fh:
        fh.write(lead + b"".join(_dump(r) for r in rows))
    return len(rows)


def append_gz(path: Path, rows: list[dict]) -> int:
    """Append rows to a jsonl.gz file as one more gzip member; earlier bytes are never touched."""
    if not rows:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
            fh.write(b"".join(_dump(r) for r in rows))
    return len(rows)


# --- B2: Black Marble nights ---------------------------------------------------------------------------------------

def archive_nights(history: Path, snapshot: dict) -> int:
    """Append each (city, night) of the snapshot's night-light readings that history/ does not hold yet."""
    path = Path(history) / NIGHTS_FILE
    held = {(r.get("city"), r.get("date")) for r in read_rows(path)}
    new = []
    for c in (snapshot.get("nightlights") or {}).get("cities") or []:
        for n in c.get("nights") or []:
            key = (c.get("id"), n.get("date"))
            if n.get("date") and key not in held:
                held.add(key)
                new.append({"city": c["id"], **n})
    new.sort(key=lambda r: (r["date"], r["city"]))
    return append_jsonl(path, new)


# --- B3: ODIN county rows ------------------------------------------------------------------------------------------

def close_days(folder: Path, today: str) -> list[str]:
    """Gzip every plain YYYY-MM-DD.jsonl in folder whose day is before `today` (UTC date), then remove the plain file.
    A day whose .gz already exists (a late reading) gets the lines as a further gzip member."""
    closed = []
    for p in sorted(Path(folder).glob("*.jsonl")):
        if p.stem >= today:
            continue
        gz = p.with_suffix(".jsonl.gz")
        data = p.read_bytes()
        if gz.exists():
            with open(gz, "ab") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
                fh.write(data)
        else:
            tmp = gz.with_suffix(".gz.tmp")
            with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
                fh.write(data)
            os.replace(tmp, gz)
        p.unlink()
        closed.append(p.stem)
    return closed


def archive_odin(history: Path, snapshot: dict) -> int:
    """Append this run's ODIN reading (a run line, then one line per county and per unplaced row) to its UTC day's file,
    unless a reading with that read_at is already there. Closes earlier days."""
    folder = Path(history) / ODIN_DIR
    od = snapshot.get("odin")
    today = (snapshot.get("generated_at") or "")[:10]
    wrote = 0
    if od and od.get("read_at"):
        read_at = od["read_at"]
        day = read_at[:10]
        plain, gz = folder / f"{day}.jsonl", folder / f"{day}.jsonl.gz"
        held = {r.get("read_at") for r in read_rows(gz) + read_rows(plain) if r.get("kind") == "run"}
        if read_at not in held:
            counties, unplaced = od.get("counties") or [], od.get("unplaced") or []
            rows = [{"kind": "run", "read_at": read_at, "meters": od.get("meters"), "counties": len(counties),
                     "unplaced": len(unplaced), "coverage": od.get("coverage")}]
            for kind, group in (("county", counties), ("unplaced", unplaced)):
                for c in group:
                    rows.append({"kind": kind, "read_at": read_at, "fips": c.get("fips"), "state": c.get("state"),
                                 "meters": c.get("meters"),
                                 "utilities": [[u.get("name"), u.get("eia_id")] for u in c.get("utilities") or []]})
            wrote = append_jsonl(plain, rows)
    if today:
        close_days(folder, today)
    return wrote


# --- B4: Bluesky counts and GDELT reductions -----------------------------------------------------------------------

def archive_bluesky(history: Path, snapshot: dict) -> int:
    """Append one row per watched name for this run's window (`run_at` = the window's end), once per run_at."""
    bs = snapshot.get("bluesky")
    if not bs or not bs.get("window_end"):
        return 0
    path = Path(history) / COUNTS_FILE
    run_at = bs["window_end"]
    if any(r.get("run_at") == run_at for r in read_rows(path)):
        return 0
    rows = [{"run_at": run_at, "name": r["name"], "key": r["key"], "window_n": r["matches"],
             "baseline_n": r["baseline_matches"]} for r in bs.get("rows") or []]
    return append_jsonl(path, rows)


def _stamp_month(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}"


def archive_gdelt(history: Path, new_files: dict | None, carry: dict | None, missing: list | None = None) -> int:
    """Append GKG-file reductions not held yet: first the files read this run, in full (`new_files`), then any file in
    the carry that was never archived (a first run after this change; those past the window hold counts only).
    Lines: {"file": stamp, "matches": {...}} or {"file": stamp, "missing": true} for a stamp GDELT answered 404 for."""
    folder = Path(history) / GDELT_DIR
    files = {}
    for src in (carry or {}, new_files or {}):          # new_files last, so a full reduction wins over the carry's
        for s, v in src.items():
            if isinstance(v, dict):
                files[s] = v
    for s in missing or []:
        files.setdefault(s, None)
    by_month: dict[str, list] = {}
    for s in sorted(files):
        by_month.setdefault(_stamp_month(s), []).append(s)
    wrote = 0
    for month, stamps in by_month.items():
        path = folder / f"{month}.jsonl.gz"
        held = {r.get("file") for r in read_rows(path)}
        rows = [{"file": s, "matches": files[s]} if files[s] is not None else {"file": s, "missing": True}
                for s in stamps if s not in held]
        wrote += append_gz(path, rows)
    return wrote


TRENDS_FILE = "trends-days.jsonl"


def archive_trends(history: Path, doc: dict | None, now=None) -> int:
    """Append the settled days of the daily series that history/trends-days.jsonl does not hold yet. `now` (UTC)
    is for tests; runs use the clock."""
    from blackout import trends
    rows = trends.settled_rows(doc or {}, now)
    if not rows:
        return 0
    path = Path(history) / TRENDS_FILE
    have = {r["day"] for r in read_rows(path) if r.get("kind") == "day"}
    return append_jsonl(path, [r for r in rows if r["day"] not in have])


NETBLOCKS_FILE = "netblocks-posts.jsonl"   # the private file's name, and the public file's until 24 Nov 2026
NETBLOCKS_INDEX = "netblocks-index.jsonl"


def _index_row(post: dict) -> dict:
    return {k: v for k, v in post.items() if k not in ("in_account", "excerpt")}


def netblocks_kept(history: Path) -> list[dict]:
    """Every post the public history holds (no excerpts): the index, then any post only the old public file has."""
    rows = read_rows(Path(history) / NETBLOCKS_INDEX)
    have = {r.get("id") for r in rows}
    return rows + [_index_row(r) for r in read_rows(Path(history) / NETBLOCKS_FILE) if r.get("id") not in have]


def archive_netblocks(history: Path, doc: dict | None) -> int:
    """Append to history/netblocks-index.jsonl the NetBlocks posts it does not hold yet, oldest first, without their
    excerpts: first any the old public file still holds, then those of data/netblocks.json."""
    from blackout import netblocks
    path = Path(history) / NETBLOCKS_INDEX
    have = {r.get("id") for r in read_rows(path)}
    new = []
    for r in read_rows(Path(history) / NETBLOCKS_FILE):
        if r.get("id") and r["id"] not in have:
            have.add(r["id"])
            new.append(_index_row(r))
    if isinstance(doc, dict) and doc.get("format") == netblocks.FORMAT and doc.get("mode") == "live":
        for p in reversed(doc.get("posts") or []):
            if p.get("id") and p["id"] not in have:
                have.add(p["id"])
                new.append(_index_row(p))
    return append_jsonl(path, new)


def netblocks_full(private_dir: Path, posts: list[dict] | None, legacy: Path | None = None) -> dict[str, int]:
    """Bring the private copy of the posts with their excerpts up to date. `private_dir` holds netblocks-posts.jsonl
    as restored from the private archive. Posts of the old public file (`legacy`) it lacks are added first, then this
    run's posts (`posts`, oldest first, each with its excerpt) it lacks. One line per post id, never rewritten."""
    path = Path(private_dir) / NETBLOCKS_FILE
    have = {r.get("id") for r in read_rows(path)}
    n = {"legacy": 0, "posts": 0}
    for key, rows in (("legacy", read_rows(legacy) if legacy is not None else []), ("posts", posts or [])):
        new = []
        for r in rows:
            if r.get("id") and r["id"] not in have:
                have.add(r["id"])
                new.append({k: v for k, v in r.items() if k != "in_account"})
        n[key] = append_jsonl(path, new)
    return n


def retire_netblocks_legacy(private_dir: Path, legacy: Path) -> int:
    """Remove the old public file, but only when the private file holds every post of it, field for field. Returns
    the posts it held (0 when it was already gone). Call it only after the private file was uploaded."""
    legacy = Path(legacy)
    if not legacy.exists():
        return 0
    held = {r.get("id"): r for r in read_rows(Path(private_dir) / NETBLOCKS_FILE)}
    rows = read_rows(legacy)
    missing = sum(1 for r in rows if held.get(r.get("id")) != r)
    if missing:
        raise RuntimeError(f"{missing} of {len(rows)} posts of the public file are not in the private copy; kept")
    legacy.unlink()
    return len(rows)
