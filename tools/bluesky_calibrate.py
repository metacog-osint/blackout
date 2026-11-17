"""Replay Blackout's Bluesky rule (blackout/bluesky.py, BO-IND-18) over past days, at the times the lane really runs.

    python tools/bluesky_calibrate.py --cache <file.json> [--start 2026-11-03] [--end 2026-11-17T21:45] [--out <file>]

For every watched name and every scheduled run (:45 past 01, 05, 09, 13, 17 and 21 UTC, the tic-dispatch times), it
reads the same two counts the lane reads for its window ('"<name>" down', '"<name>" outage', WINDOW_H hours to the
run). The usual for a run is the mean of the 42 runs before it: runs 4 h apart with 6-hour windows cover each hour
1.5 times, so 42 runs (7 days) sum to 1.5 x the 7-day count, and their mean equals the 7-day count scaled to one
window, which is what the lane computes. Runs in the first 7 days have no 42 before them; their usual is the mean of
those first 42 runs (in-sample, which raises the usual a little and so understates false surges there; said in the
output). Counts are cached by URL in --cache, so an interrupted run resumes. Only hitsTotal is kept, never a post.

Prints, per name, the largest window in the first 7 days (the quiet week) and the largest after, with their usual
and ratio, and the runs the rule would flag for each candidate (MIN_MATCHES, RATIO, FLOOR) in CANDIDATES."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackout import bluesky  # noqa: E402

RUN_HOURS = (1, 5, 9, 13, 17, 21)
RUN_MINUTE = 45
CANDIDATES = [(40, 5.0, 4.0), (30, 5.0, 4.0), (25, 5.0, 4.0), (40, 4.0, 4.0), (30, 6.0, 4.0), (25, 8.0, 3.0)]


def runs(start: datetime, end: datetime) -> list[datetime]:
    out, d = [], start.replace(hour=0, minute=0, second=0)
    while d <= end:
        for h in RUN_HOURS:
            t = d.replace(hour=h, minute=RUN_MINUTE)
            if start <= t <= end:
                out.append(t)
        d += timedelta(days=1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--start", default="2026-11-03T00:00")
    ap.add_argument("--end", default="2026-11-17T21:45")
    ap.add_argument("--out")
    ap.add_argument("--names", nargs="*")
    a = ap.parse_args()
    utc = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)  # noqa: E731
    times = runs(utc(a.start), utc(a.end))
    watch = [w for w in bluesky.WATCH if not a.names or w[0] in a.names]
    cpath = Path(a.cache)
    cache = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}
    lock = threading.Lock()
    todo = []
    for key, name, *_ in watch:
        for t in times:
            for term in bluesky.TERMS:
                url = bluesky.url_for(bluesky.query(name, term), t - timedelta(hours=bluesky.WINDOW_H), t)
                if url not in cache:
                    todo.append(url)
    print(f"{len(watch)} names x {len(times)} runs: {len(todo)} queries to read", file=sys.stderr, flush=True)
    done = [0]

    def read(url):
        try:
            n = bluesky._count(url, bluesky._get, time.sleep, time.monotonic() + 600)
        except Exception as exc:  # noqa: BLE001
            print("ERR", url, exc, file=sys.stderr, flush=True)
            return
        with lock:
            cache[url] = n
            done[0] += 1
            if done[0] % 200 == 0:
                cpath.write_text(json.dumps(cache), encoding="utf-8")
                print(f"  {done[0]}/{len(todo)}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=bluesky.WORKERS) as pool:
        list(pool.map(read, todo))
    cpath.write_text(json.dumps(cache), encoding="utf-8")

    quiet_n = 42
    report = {"runs": [bluesky._utc(t) for t in times], "names": {}}
    for key, name, label, *_ in watch:
        win = []
        for t in times:
            urls = [bluesky.url_for(bluesky.query(name, term), t - timedelta(hours=bluesky.WINDOW_H), t)
                    for term in bluesky.TERMS]
            win.append(sum(cache[u] for u in urls) if all(u in cache for u in urls) else None)
        usual = []
        for i in range(len(win)):
            prev = win[i - quiet_n:i] if i >= quiet_n else win[:quiet_n]
            vals = [v for v in prev if v is not None]
            usual.append(sum(vals) / len(vals) if vals else 0.0)
        rows = [{"run": bluesky._utc(t), "matches": w, "usual": round(u, 2)} for t, w, u in zip(times, win, usual)]
        q, e = rows[:quiet_n], rows[quiet_n:]
        top = lambda rs: max((r for r in rs if r["matches"] is not None), key=lambda r: r["matches"], default=None)  # noqa: E731
        flags = {}
        for mm, ra, fl in CANDIDATES:
            flags[f"{mm}/{ra}/{fl}"] = [r["run"] for r in rows
                                        if r["matches"] is not None and r["matches"] >= mm
                                        and r["matches"] >= ra * max(r["usual"], fl)]
        report["names"][key] = {"name": name, "label": label, "quiet_max": top(q), "after_max": top(e),
                                "flags": flags, "rows": rows}
        qm, am = top(q), top(e)
        print(f"{name:18} quiet max {qm['matches'] if qm else '-':>5} (usual {qm['usual'] if qm else '-'}) | "
              f"after max {am['matches'] if am else '-':>5} at {am['run'] if am else '-'} (usual {am['usual'] if am else '-'}) | "
              + " ".join(f"{k}:{len(v)}" for k, v in flags.items()))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
