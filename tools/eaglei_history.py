"""Offline: reduce EAGLE-I's yearly outage history to Blackout's base-rate tables (blackout/eaglei_rates.json).

Never run by the workflow. Run it by hand when figshare adds a year (2024 came on 10 Apr 2025, 2025 on 24 Feb 2026),
then commit the tables:

    python tools/eaglei_history.py --cache <folder with room for about 10 GB>     # 2018-2025, about 15-30 minutes

Each year file (1.0-1.4 GB) is checked against figshare's own MD5 before it is used. With --cache, a file already
there is read from disk (and checked); one that isn't is downloaded into it. Without --cache, each file is streamed
and nothing is kept. The figshare record is read through its API (api.figshare.com/v2/articles/24237376).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackout import eaglei  # noqa: E402


class Hashing(io.RawIOBase):
    """Wrap a byte stream; MD5 and count what passes through (and copy it to `tee` if given)."""

    def __init__(self, raw, tee=None):
        self.raw, self.tee, self.md5, self.bytes = raw, tee, hashlib.md5(), 0

    def readable(self):
        return True

    def readinto(self, b):
        chunk = self.raw.read(len(b))
        if not chunk:
            return 0
        n = len(chunk)
        b[:n] = chunk
        self.md5.update(chunk)
        self.bytes += n
        if self.tee:
            self.tee.write(chunk)
        return n


def _get(url: str, timeout: float = 120):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": eaglei.USER_AGENT}),
                                  timeout=timeout)


def record() -> dict:
    with _get(eaglei.API, 60) as r:
        return json.loads(r.read().decode("utf-8"))


def reduce_file(year: int, meta: dict, cache: Path | None, caps: dict) -> dict:
    """Read one year (cache or stream), check its MD5, return the reduced year and its provenance."""
    name, want = meta["name"], meta.get("computed_md5")
    local = cache / name if cache else None
    tee = None
    if local and local.exists() and local.stat().st_size == meta["size"]:
        raw, how = open(local, "rb"), "cache"
    else:
        raw, how = _get(meta["download_url"]), "download"
        if local:
            tee = open(local.with_suffix(".part"), "wb")
    h = Hashing(raw, tee)
    try:
        lines = io.TextIOWrapper(io.BufferedReader(h, buffer_size=1 << 20), encoding="utf-8-sig", newline="")
        reduced = eaglei.state_totals(lines, year, caps)
    finally:
        raw.close()
        if tee:
            tee.close()
    got = h.md5.hexdigest()
    if want and got != want:
        raise SystemExit(f"{name}: MD5 {got} is not figshare's {want}; not used")
    if tee:
        local.with_suffix(".part").replace(local)
    reduced["provenance"] = {"file": name, "file_id": meta["id"], "bytes": h.bytes, "md5": got,
                             "md5_matches_figshare": bool(want) and got == want, "read_from": how}
    return reduced


def small(meta: dict) -> str:
    with _get(meta["download_url"]) as r:
        body = r.read()
    if meta.get("computed_md5") and hashlib.md5(body).hexdigest() != meta["computed_md5"]:
        raise SystemExit(f"{meta['name']}: MD5 is not figshare's; not used")
    return body.decode("utf-8-sig")


def coverage_2022(meta: dict | None) -> dict:
    """State -> share of customers EAGLE-I covered in 2022 (coverage_history.csv, max_pct_covered)."""
    if not meta:
        return {}
    text = small(meta)
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("year", "").endswith("/22"):
            try:
                out[row["state"]] = float(row["max_pct_covered"])
            except (KeyError, ValueError):
                pass
    return dict(sorted(out.items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", default=f"{eaglei.BASE_YEARS[0]}-{eaglei.BASE_YEARS[-1]}")
    ap.add_argument("--cache", type=Path, help="folder to keep (or reuse) the year files")
    ap.add_argument("--out", type=Path, default=eaglei.RATES_FILE)
    args = ap.parse_args(argv)
    a, _, b = args.years.partition("-")
    years = list(range(int(a), int(b or a) + 1))
    rec = record()
    files = {f["name"]: f for f in rec["files"]}
    missing = [y for y in years if f"eaglei_outages_{y}.csv" not in files]
    if missing:
        raise SystemExit(f"figshare version {rec.get('version')} has no file for {missing}")
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)
    caps = eaglei.load_caps(small(files["MCC.csv"]))
    by_year, prov = {}, {}
    states: set[str] = set()
    for y in years:
        t0 = datetime.now(timezone.utc)
        red = reduce_file(y, files[f"eaglei_outages_{y}.csv"], args.cache, caps)
        by_year[y] = eaglei.year_episodes(red)
        states |= set(red["totals"])
        prov[str(y)] = {**red["provenance"], "rows": red["rows"], "rows_skipped": red["skipped"],
                        "rows_other_year": red["other_year"], "suspect_rows": red["suspect_rows"],
                        "runs_suspect": {st: len(r) for st, r in red["suspect"].items()}, "runs_present": len(red["runs_present"]),
                        "runs_in_year": eaglei.runs_in(y)}
        secs = (datetime.now(timezone.utc) - t0).total_seconds()
        print(f"[eaglei] {y}: {red['rows']:,} rows, {len(red['runs_present']):,}/{eaglei.runs_in(y):,} runs, "
              f"{len(by_year[y][str(eaglei.LINE)])} episodes over {eaglei.LINE:,}, {secs:.0f}s ({red['provenance']['read_from']})",
              file=sys.stderr)
    lic = rec.get("license") or {}
    doc = {
        "about": "Blackout's base rates from EAGLE-I's outage history: how often each state had more than each line "
                 "of customers out for an hour or more, built by tools/eaglei_history.py (see blackout/eaglei.py for the "
                 "rule). Not a live feed; rebuilt by hand when figshare adds a year.",
        "citation": eaglei.CITATION,
        "doi": rec.get("doi") or eaglei.DOI,
        "version": rec.get("version"),
        "figshare_published": rec.get("published_date"),
        "licence": {"name": lic.get("name"), "url": lic.get("url")},
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_years": years,
        "lines": list(eaglei.LINES),
        "rule": ("A state's total is the sum of its counties' customers_out in a 15-minute run (UTC). A run where any "
                 "of its counties shows more than 100,000 customers out beyond 1.1 times EAGLE-I's modelled customers "
                 "for it (MCC.csv, 2022) is suspect for that state: kept, but it can't make an episode by itself or "
                 "set its peak. Runs missing from the file are unknown and neither join nor split. An episode is "
                 "readings above the line with no more than 3 known readings under it between them and no more than "
                 "6 hours from one to the next, 4 or more of them not suspect; it counts in the month and year it "
                 "starts. Its hours run from its first reading to the end of its last; its peak is the highest total "
                 "held over 4 readings in a row that aren't suspect."),
        "files": prov,
        "coverage_2022": coverage_2022(files.get("coverage_history.csv")),
        "states": eaglei.rate_table(by_year, sorted(states), years),
        "episodes_over_50000": [e for y in years for e in by_year[y][str(eaglei.LINE)]],
    }
    args.out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8",
                        newline="\n")
    print(f"[eaglei] wrote {args.out} ({args.out.stat().st_size:,} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
