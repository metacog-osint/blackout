"""Replay Blackout's GDELT headline rule (blackout/gdelt.py, BO-IND-19) over past days, at the lane's run times.

    python tools/gdelt_calibrate.py --cache <folder> [--start 2026-11-07T00:00] [--end 2026-11-17T20:30] [--out <file>]

Reads every GKG file from --start to --end (about 6 MB each) and keeps, per file, the matches gdelt.scan finds with
their headlines, one JSON file per stamp in --cache (so a run resumes; the headlines stay in that local folder and are
never committed or published). Then, at each run time (:45 past 01, 05, 09, 13, 17 and 21 UTC, so GDELT's newest
file is the :30 one), it counts each name's headlines in the window's 24 files against the files of the 7 days before
that were read (a run with under 2 days of them before it has no usual and is skipped) and prints, per name, the quietest and busiest windows and the runs
each candidate rule in CANDIDATES would flag, with sample headlines for flagged runs (for checking by eye)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackout import gdelt  # noqa: E402

RUN_HOURS = (1, 5, 9, 13, 17, 21)
CANDIDATES = [(6, 3, 5.0, 1.0), (5, 3, 5.0, 1.0), (4, 2, 4.0, 1.0), (3, 2, 3.0, 1.0), (8, 3, 5.0, 1.0)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--start", default="2026-11-07T00:00")
    ap.add_argument("--end", default="2026-11-17T20:30")
    ap.add_argument("--out")
    a = ap.parse_args()
    utc = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)  # noqa: E731
    first, last = utc(a.start), utc(a.end)
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    all_stamps = sorted(gdelt.stamps(first, last))
    todo = [s for s in all_stamps if not (cache / f"{s}.json").exists()]
    print(f"{len(all_stamps)} files, {len(todo)} to read", file=sys.stderr, flush=True)
    done = [0]

    def read(s):
        try:
            found = gdelt.read_file(gdelt._get(gdelt.file_url(s)), keep_titles=True)
            (cache / f"{s}.json").write_text(json.dumps(found), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            if code == 404:
                (cache / f"{s}.json").write_text(json.dumps({"_missing": True}), encoding="utf-8")
            else:
                print("ERR", s, exc, file=sys.stderr, flush=True)
        done[0] += 1
        if done[0] % 50 == 0:
            print(f"  {done[0]}/{len(todo)}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=gdelt.WORKERS) as pool:
        list(pool.map(read, todo))

    files = {}
    for s in all_stamps:
        p = cache / f"{s}.json"
        files[s] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    runs = []
    d = first.replace(hour=0, minute=0)
    while d <= last:
        for h in RUN_HOURS:
            newest = d.replace(hour=h, minute=30)
            if first + timedelta(hours=6) <= newest <= last:
                runs.append(newest)
        d += timedelta(days=1)
    quiet_end = first + timedelta(days=7)
    keys = [k for k, *_ in gdelt.WATCH]

    def count(stamps_, k):
        n, outlets, titles = 0, set(), []
        for s in stamps_:
            f = files.get(s)
            if not f or f.get("_missing"):
                continue
            for dom, url, title in f.get(k, []):
                n += 1
                outlets.add(dom)
                titles.append(f"{s[8:12]} {dom}: {title[:100]}")
        return n, outlets, titles

    report = {}
    for k in keys:
        rows = []
        for newest in runs:
            win = gdelt.stamps(newest - gdelt.STEP * 23, newest)
            n, outlets, titles = count(win, k)
            base = gdelt.stamps(newest - gdelt.STEP * 24 - timedelta(days=7) + gdelt.STEP, newest - gdelt.STEP * 24)
            have = [s for s in base if files.get(s) is not None and not files[s].get("_missing")]
            if len(have) < 2 * 96:
                continue
            bn = count(have, k)[0]
            usual = bn / len(have) * 24
            rows.append({"run": newest.strftime("%Y-%m-%dT%H:%MZ"), "n": n, "outlets": len(outlets),
                         "usual": round(usual, 2), "titles": titles[:6]})
        flags = {}
        for mh, mo, ra, fl in CANDIDATES:
            flags[f"{mh}/{mo}/{ra}/{fl}"] = [r["run"] for r in rows if r["n"] >= mh and r["outlets"] >= mo
                                             and r["n"] >= ra * max(r["usual"], fl)]
        q = [r for r in rows if r["run"] < quiet_end.strftime("%Y-%m-%dT%H:%MZ")]
        e = [r for r in rows if r not in q]
        qm = max(q, key=lambda r: r["n"]) if q else None
        em = max(e, key=lambda r: r["n"]) if e else None
        report[k] = {"quiet_max": qm, "after_max": em, "flags": flags, "rows": rows}
        print(f"{k:18} quiet max {qm['n'] if qm else '-':>4}/{qm['outlets'] if qm else '-'} (usual {qm['usual'] if qm else '-'}) | "
              f"after max {em['n'] if em else '-':>4}/{em['outlets'] if em else '-'} at {em['run'] if em else '-'} | "
              + " ".join(f"{c}:{len(v)}" for c, v in flags.items()))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
