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
  history/gdelt-matches/YYYY-MM.jsonl.gz  B4: each GKG file's reduction (watched name -> [outlet domain, link] pairs, or a
                                          count and the first links) as it was read, before it leaves the 8-day carry.
                                          One file per month by the GKG file's stamp; new lines are added as a further gzip
                                          member (a gzip file may be several members; gzip.open reads them as one).

No post text, handle or headline text is kept: only what the live lanes already publish (counts, outlets, links).
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
