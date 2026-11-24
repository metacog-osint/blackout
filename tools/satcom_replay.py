#!/usr/bin/env python3
"""Replay the BO-IND-21 satellite drop rule over Cloudflare Radar's hourly history (ideas#168). Offline analysis: it
reads Radar, writes under .claude/satcom-replay/ (git-ignored) and changes no feed, rule or threshold.

Radar refuses an hourly request much longer than the 29 days the live lane asks for, and scales each answer to its own
peak (MIN0_MAX). So a series is read in 29-day parts that overlap by a day and put on one scale by the median ratio over
the overlap. Parts with too little overlap to scale start a new segment, and a window across that break is not judged.

The real `satcom.drop_rule` judges every hour as if a run ended there. Its two thinness gates need Radar's native steps,
which a stitched series no longer has, so while replaying they read the native part holding the window's last hour
(`sparse` as it is, `quantum` times that part's scale). A run time is an hour; the four run timings are hour mod 4.

    python tools/satcom_replay.py run --start 2025-06-20 --end 2026-11-23
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackout import radar, satcom  # noqa: E402

UTC = timezone.utc
OUT = ROOT / ".claude" / "satcom-replay"
CACHE = OUT / "raw"
SNAPSHOT_URL = "https://blackout.theintelligence.company/data/snapshot.json"
PART_DAYS = satcom.HOURLY_DAYS
PART_STEP_DAYS = PART_DAYS - 1
MIN_OVERLAP_HOURS = 6
EPISODE_GAP_HOURS = 8     # fired hours this close are one episode (two runs apart at most)
STARLINK = 14593

# Independent reports of a satellite-network disruption, each from NetBlocks' archived posts (history/netblocks-index.jsonl)
# except the Ukraine registration drop, the lane's first replay (satcom.py docstring).
CASES = [
    {"id": "starlink-2023-09-13", "asn": STARLINK, "cc": None, "at": "2023-09-13T00:35:00Z", "source": "NetBlocks"},
    {"id": "starlink-2025-07-24", "asn": STARLINK, "cc": None, "at": "2025-07-24T20:26:00Z", "source": "NetBlocks"},
    {"id": "starlink-2025-08-18", "asn": STARLINK, "cc": None, "at": "2025-08-18T18:32:00Z", "source": "NetBlocks"},
    {"id": "starlink-ua-2025-09-15", "asn": STARLINK, "cc": "UA", "at": "2025-09-15T04:30:00Z",
     "source": "NetBlocks (posted 14:01 UTC; the time is Radar's 15-minute low)"},
    {"id": "starlink-ua-2026-02-04", "asn": STARLINK, "cc": "UA", "at": "2026-02-04T16:00:00Z",
     "source": "Blackout's first replay (registration deadline)"},
]


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)


def fetch_part(asn: int, cc: str | None, a: datetime, b: datetime, pause: float = 0.25) -> list[tuple[str, float]]:
    """One native hourly answer, cached by a hash of its URL (the token is a header, never in the URL or the file)."""
    url = satcom.timeseries_url([("s", asn, cc)], a, b, "1h")
    path = CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        for attempt in range(4):           # Radar answers an occasional 500 on a long run (23 Nov 2026)
            try:
                data = radar._get(url, timeout=60)
                break
            except urllib.error.HTTPError as exc:
                if exc.code < 500 or attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        time.sleep(pause)
    return next(iter(satcom.parse_timeseries(data).values()))


def part_ranges(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    out, a = [], start
    while True:
        b = min(a + timedelta(days=PART_DAYS), end)
        out.append((a, b))
        if b >= end:
            return out
        a += timedelta(days=PART_STEP_DAYS)


@dataclass
class Gate:
    why: str | None
    q: float | None


@dataclass
class Series:
    times: list[datetime] = field(default_factory=list)
    stamps: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    gate_of: list[int] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    breaks: list[datetime] = field(default_factory=list)


def stitch(parts: list[list[tuple[str, float]]]) -> Series:
    """One series from overlapping native parts, on the first part's scale. An hour keeps its earliest part's value."""
    s, seen, scale, prev = Series(), {}, 1.0, None
    for i, pts in enumerate(parts):
        cur = dict(pts)
        if prev is not None:
            ratios = [prev[t] / cur[t] for t in cur if t in prev and prev[t] > 0 and cur[t] > 0]
            if len(ratios) >= MIN_OVERLAP_HOURS:
                scale = satcom._median(ratios)
            else:
                scale = 1.0
                if pts:
                    s.breaks.append(_dt(pts[0][0]))
        q = satcom.quantum(pts)
        s.gates.append(Gate(satcom.sparse(pts), q * scale if q else None))
        rescaled = {}
        for t, v in pts:
            rescaled[t] = v * scale
            if t not in seen:
                seen[t] = (v * scale, i)
        prev = rescaled
    for t in sorted(seen, key=_dt):
        s.times.append(_dt(t))
        s.stamps.append(t)
        s.values.append(seen[t][0])
        s.gate_of.append(seen[t][1])
    return s


def replay(s: Series) -> dict:
    """{states, fired}: the real drop rule at every hour that has a full window behind it."""
    states: dict[str, int] = {}
    fired: list[dict] = []
    if not s.times:
        return {"states": states, "fired": fired}
    span = timedelta(days=PART_DAYS)
    gate = Gate(None, None)
    real = satcom.sparse, satcom.quantum
    satcom.sparse = lambda pts: gate.why
    satcom.quantum = lambda pts: gate.q
    try:
        for j in range(len(s.times)):
            last = s.times[j] + timedelta(hours=1)
            first = last - span
            if first < s.times[0]:
                continue
            if any(first <= b < last for b in s.breaks):
                states["no_scale"] = states.get("no_scale", 0) + 1
                continue
            i = bisect.bisect_left(s.times, first)
            g = s.gates[s.gate_of[j]]
            gate.why, gate.q = g.why, g.q
            r = satcom.drop_rule(list(zip(s.stamps[i:j + 1], s.values[i:j + 1])))
            states[r["state"]] = states.get(r["state"], 0) + 1
            if r["state"] == "fired":
                fired.append({"at": satcom._ts(last), "pct": r["weekday_pct"], "prior_pct": r["prior_pct"],
                              "since": r["since"], "hours_below": r["hours_below"], "standing": r["standing"]})
    finally:
        satcom.sparse, satcom.quantum = real
    return {"states": states, "fired": fired}


def episodes(fired: list[dict]) -> list[dict]:
    """Fired hours merged into episodes. `timings` is how many of the 4 run timings would have fired at least once."""
    out: list[dict] = []
    for f in fired:
        t = _dt(f["at"])
        if out and (t - _dt(out[-1]["last_fired"])) <= timedelta(hours=EPISODE_GAP_HOURS):
            e = out[-1]
            e["last_fired"] = f["at"]
            e["min_pct"] = min(e["min_pct"], f["pct"])
            e["since"] = min(e["since"], f["since"])
            e["max_hours_below"] = max(e["max_hours_below"], f["hours_below"])
            e["standing"] = e["standing"] or f["standing"]
            e["_t"].add(t.hour % 4)
        else:
            out.append({"first_fired": f["at"], "last_fired": f["at"], "since": f["since"], "min_pct": f["pct"],
                        "max_hours_below": f["hours_below"], "standing": f["standing"], "_t": {t.hour % 4}})
    for e in out:
        e["run_hours_mod_4"] = sorted(e.pop("_t"))
        e["timings"] = len(e["run_hours_mod_4"])
    return out


def depth(s: Series, at: datetime, before_h: int = 6, after_h: int = 18) -> dict:
    """Each hour near `at` as a percent of the median of the same hour 1-4 weeks earlier: the lowest, and the longest
    unbroken run under 70% (the rule's line, here read hour by hour)."""
    by = dict(zip(s.times, s.values))
    low, run, longest, low_at = None, 0, 0, None
    for h in range(-before_h, after_h + 1):
        t = at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=h)
        past = [by[t - timedelta(days=7 * k)] for k in range(1, 5) if t - timedelta(days=7 * k) in by]
        if t not in by or len(past) < 3 or satcom._median(past) <= 0:
            run = 0
            continue
        pct = 100.0 * by[t] / satcom._median(past)
        if low is None or pct < low:
            low, low_at = pct, satcom._ts(t)
        run = run + 1 if pct < satcom.DROP_BELOW_PCT else 0
        longest = max(longest, run)
    return {"lowest_hour_pct": round(low, 1) if low is not None else None, "lowest_at": low_at,
            "hours_under_70_in_a_row": longest}


def load_series(asn: int, cc: str | None, start: datetime, end: datetime) -> Series:
    return stitch([fetch_part(asn, cc, a, b) for a, b in part_ranges(start, end)])


def targets() -> list[dict]:
    """Every worldwide series and Starlink country series the rule could judge in the live snapshot. The first run's
    list is kept, so a re-run judges the same series."""
    kept = OUT / "targets.json"
    if kept.exists():
        return json.loads(kept.read_text(encoding="utf-8"))
    req = urllib.request.Request(SNAPSHOT_URL, headers={"User-Agent": "tic-blackout-replay"})
    with urllib.request.urlopen(req, timeout=60) as r:
        snap = json.load(r)
    out = []
    for n in snap["satcom"]["traffic"]["networks"]:
        ra = n.get("radar") or {}
        if ((ra.get("world") or {}).get("weekday") or {}).get("state") == "ok":
            out.append({"asn": n["asn"], "operator": n["operator"], "cc": None})
        if n["asn"] == STARLINK:
            out += [{"asn": STARLINK, "operator": n["operator"], "cc": cc} for cc, m in (ra.get("countries") or {}).items()
                    if (m.get("weekday") or {}).get("state") == "ok"]
    kept.write_text(json.dumps(out), encoding="utf-8")
    return out


def cmd_run(a) -> int:
    start, end = _day(a.start), _day(a.end)
    OUT.mkdir(parents=True, exist_ok=True)
    rows, cache = [], {}
    tg = targets()
    print(f"{len(tg)} series, {start:%Y-%m-%d} to {end:%Y-%m-%d}", flush=True)
    for k, t in enumerate(tg, 1):
        key = (t["asn"], t["cc"])
        try:
            s = load_series(t["asn"], t["cc"], start, end)
            r = replay(s)
        except Exception as exc:  # noqa: BLE001 - named in the output, the rest still runs
            rows.append({**t, "error": f"{type(exc).__name__}: {exc}"[:200]})
            print(f"{k}/{len(tg)} AS{t['asn']} {t['cc'] or 'world'}: {rows[-1]['error']}", flush=True)
            continue
        cache[key] = (s, r)
        eps = episodes(r["fired"])
        rows.append({**t, "states": r["states"], "breaks": len(s.breaks), "episodes": eps})
        print(f"{k}/{len(tg)} AS{t['asn']} {t['cc'] or 'world'}: {r['states']} episodes {len(eps)}", flush=True)
    cases = []
    for c in CASES:
        at = _dt(c["at"])
        key = (c["asn"], c["cc"])
        if key in cache and at >= start + timedelta(days=PART_DAYS):
            s, r = cache[key]
        else:
            day = at.replace(hour=0, minute=0, second=0, microsecond=0)
            s = load_series(c["asn"], c["cc"], day - timedelta(days=PART_DAYS + 1), day + timedelta(days=3))
            r = replay(s)
        lo, hi = at - timedelta(hours=6), at + timedelta(hours=30)
        near = [f for f in r["fired"] if lo <= _dt(f["at"]) <= hi]
        cases.append({**c, **depth(s, at), "fired_timings": len({_dt(f["at"]).hour % 4 for f in near}),
                      "first_fired": near[0]["at"] if near else None,
                      "min_pct_when_fired": min((f["pct"] for f in near), default=None)})
        print(c["id"], cases[-1], flush=True)
    doc = {"schema": "blackout-satcom-replay/1", "start": a.start, "end": a.end, "series": rows, "cases": cases,
           "licence": "Cloudflare Radar, CC BY-NC 4.0"}
    (OUT / "result.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print("written", OUT / "result.json", flush=True)
    return 0


# Widely observed days the replay's range holds. Being near one says only that traffic may have a holiday shape.
HOLIDAYS = {"2025-07-04": "US Independence Day", "2025-09-01": "US Labor Day", "2025-11-27": "US Thanksgiving",
            "2025-12-24": "Christmas Eve", "2025-12-25": "Christmas", "2025-12-26": "Boxing Day",
            "2025-12-31": "New Year's Eve", "2026-01-01": "New Year", "2026-02-17": "Lunar New Year",
            "2026-03-20": "Eid al-Fitr (about)", "2026-04-03": "Good Friday", "2026-04-05": "Easter",
            "2026-04-06": "Easter Monday", "2026-05-01": "May Day", "2026-05-25": "US Memorial Day",
            "2026-05-27": "Eid al-Adha (about)", "2026-07-03": "US Independence Day (observed)",
            "2026-09-07": "US Labor Day", "2026-11-26": "US Thanksgiving"}
LIVE_TIMING = 1           # scheduled runs read through 01, 05, ... 21 UTC (`current_to` on the live snapshot, 23 Nov 2026)
EVIDENCE_HOURS = 12       # a report this close to an episode's span is listed beside it
VIASAT_STATUS = "https://status.mss.viasat.com/history.rss"


def _cached_json(name: str, get):
    path = OUT / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    doc = get()
    path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def annotations(start: str, end: str) -> list[dict]:
    limit = 1000
    url = f"{radar.API}/annotations/outages?dateStart={start}T00:00:00Z&dateEnd={end}T00:00:00Z&limit={limit}&format=json"
    rows = _cached_json("annotations.json", lambda: radar._get(url, timeout=60))["result"]["annotations"]
    if len(rows) >= limit:
        raise RuntimeError("Radar annotations hit the request limit")
    return rows


def viasat_times() -> list[str]:
    """Publication times of Viasat MSS status items (times only: its text is not kept)."""
    def get():
        from email.utils import parsedate_to_datetime
        import re
        req = urllib.request.Request(VIASAT_STATUS, headers={"User-Agent": "tic-blackout-replay"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
        items = re.findall(r"<item>.*?</item>", text, re.S)
        return [satcom._ts(parsedate_to_datetime(m).astimezone(UTC))
                for it in items for m in re.findall(r"<pubDate>(.*?)</pubDate>", it)[:1]]
    return _cached_json("viasat-times.json", get)


def netblocks_posts() -> list[dict]:
    from blackout import archive
    return archive.netblocks_kept(ROOT / "history")   # time, link and parsed fields; no excerpt is used here


def evidence(row: dict, e: dict, anns: list[dict], posts: list[dict], viasat: list[str]) -> list[str]:
    """Independent reports near an episode: the same network, or the same country for a country series."""
    lo = _dt(e["since"]) - timedelta(hours=EVIDENCE_HOURS)
    hi = _dt(e["last_fired"]) + timedelta(hours=EVIDENCE_HOURS)
    asn, cc, got = row["asn"], row["cc"], []
    for a in anns:
        start = _dt(a["startDate"])
        end = _dt(a["endDate"]) if a.get("endDate") else hi
        if start > hi or end < lo:
            continue
        if str(asn) in {str(x) for x in a.get("asns") or []}:
            got.append(f"Radar outage {a['id']} (this network)")
        elif cc and cc in (a.get("locations") or []):
            got.append(f"Radar outage {a['id']} ({cc})")
    for p in posts:
        if not lo <= _dt(p["posted_at"]) <= hi:
            continue
        if asn in (p.get("asns") or []):
            got.append(f"NetBlocks {p['posted_at'][:10]} (this network)")
        elif cc and cc in (p.get("countries") or []):
            got.append(f"NetBlocks {p['posted_at'][:10]} ({cc})")
    if any(w in str(row.get("operator")) for w in ("Viasat", "Inmarsat")):
        n = sum(1 for t in viasat if lo <= _dt(t) <= hi)
        if n:
            got.append(f"Viasat MSS status, {n} update(s)")
    return sorted(set(got))


def holiday_near(e: dict) -> str | None:
    first = _dt(e["since"]).date()
    for k in (0, 1, -1):
        name = HOLIDAYS.get((first + timedelta(days=k)).isoformat())
        if name:
            return name
    return None


# Radar counts requests reaching Cloudflare, so Cloudflare's own trouble lowers every network at once. The controls are
# all of Radar's traffic worldwide and in the series' country, read the same way.
CONTROL_LINE = 85.0       # a control hour under this percent of its own normal counts as the control having dipped


def control_low(readings: dict, e: dict) -> float | None:
    """The control's lowest hour over the episode's span (from `since` to the last fired run)."""
    t, end, low = _dt(e["since"]), _dt(e["last_fired"]), None
    while t < end:
        v = readings.get(t)
        if v is not None and (low is None or v < low):
            low = v
        t += timedelta(hours=1)
    return round(low, 1) if low is not None else None


def classify(r: dict) -> str:
    """One label per episode, the measurement's own failure first, because a report near it may be coincidence. All of
    Radar's traffic also dips on the big holidays, so a Radar-wide dip near one is a holiday, not a measurement fault."""
    if r["world_all_low_pct"] is not None and r["world_all_low_pct"] < CONTROL_LINE:
        return "near_holiday" if r["holiday"] else "radar_wide"
    if r["evidence"]:
        return "independent_report"
    if r["holiday"]:
        return "near_holiday"
    if r["country_all_low_pct"] is not None and r["country_all_low_pct"] < CONTROL_LINE:
        return "whole_country_fell"
    return "network_only_unexplained"


def cmd_report(a) -> int:
    doc = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
    anns, posts, viasat = annotations(doc["start"], doc["end"]), netblocks_posts(), viasat_times()
    start, end = _day(doc["start"]), _day(doc["end"])
    control_failed: list[str] = []
    def control(cc):
        try:
            return {t: p for t, p, _ in hour_readings(load_series(0, cc, start, end))}
        except Exception as exc:  # noqa: BLE001 - named in the summary; the episode is then not judged on this control
            control_failed.append(f"{cc or 'world'}: {type(exc).__name__}: {exc}"[:120])
            return {}
    world, by_cc = control(None), {}
    rows = []
    for s in doc["series"]:
        if s["cc"] and s["cc"] not in by_cc and s.get("episodes"):
            by_cc[s["cc"]] = control(s["cc"])
        for e in s.get("episodes") or []:
            ev = evidence(s, e, anns, posts, viasat)
            r = {"asn": s["asn"], "operator": s["operator"], "cc": s["cc"], **e, "evidence": ev,
                 "holiday": holiday_near(e), "weekday": _dt(e["since"]).strftime("%a"),
                 "world_all_low_pct": control_low(world, e),
                 "country_all_low_pct": control_low(by_cc[s["cc"]], e) if s["cc"] else None}
            r["class"] = classify(r)
            rows.append(r)
    rows.sort(key=lambda r: r["since"])
    judged = {f"AS{s['asn']} {s['cc'] or 'world'}": s.get("states") for s in doc["series"]}
    total = len(rows)
    def tally(xs):
        out: dict[str, int] = {}
        for r in xs:
            out[r["class"]] = out.get(r["class"], 0) + 1
        return out
    summary = {"series": len(doc["series"]), "series_failed": [f"AS{s['asn']} {s['cc'] or 'world'}" for s in doc["series"]
                                                               if s.get("error")],
               "hours_judged": sum(n for st in judged.values() if st for k, n in st.items() if k in ("ok", "fired")),
               "hours_not_judged": sum(n for st in judged.values() if st for k, n in st.items()
                                       if k not in ("ok", "fired")),
               "episodes": total, "at_the_scheduled_timing": sum(1 for r in rows if LIVE_TIMING in r["run_hours_mod_4"]),
               "by_class": tally(rows),
               "by_class_at_scheduled_timing": tally([r for r in rows if LIVE_TIMING in r["run_hours_mod_4"]]),
               "one_timing_only": sum(1 for r in rows if r["timings"] == 1),
               "standing": sum(1 for r in rows if r["standing"]),
               "controls_failed": control_failed, "radar_annotations": len(anns), "viasat_items": len(viasat)}
    out = {"summary": summary, "cases": doc["cases"], "episodes": rows, "states": judged}
    (OUT / "report.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


# --- a candidate short-outage reading, for comparison only: nothing here is live -------------------------------------
LINES = (40.0, 50.0, 60.0)
RUNS_IN_A_ROW = (1, 2)
MIN_COUNTS = (20, 100)


def hour_readings(s: Series) -> list[tuple[datetime, float, float]]:
    """(hour, percent of the median of the same hour 1-4 weeks earlier, that normal in Radar counts) for each hour a
    thin series' gate lets through."""
    by = dict(zip(s.times, s.values))
    out = []
    for j, t in enumerate(s.times):
        g = s.gates[s.gate_of[j]]
        if g.why or not g.q or any(t - timedelta(days=28) <= b <= t for b in s.breaks):
            continue
        past = [by[t - timedelta(days=7 * k)] for k in range(1, 5) if t - timedelta(days=7 * k) in by]
        if len(past) < 3:
            continue
        normal = satcom._median(past)
        if normal > 0:
            out.append((t, 100.0 * s.values[j] / normal, normal / g.q))
    return out


def short_hits(readings, line: float, in_a_row: int, min_counts: float) -> list[datetime]:
    """Hours under `line` in an unbroken run of at least `in_a_row` hours. Every hour is read on the next run, so the
    result does not depend on run timing."""
    runs, cur = [], []
    for t, pct, counts in readings:
        ok = pct < line and counts >= min_counts
        if ok and (not cur or t - cur[-1] == timedelta(hours=1)):
            cur.append(t)
        else:
            if len(cur) >= in_a_row:
                runs += cur
            cur = [t] if ok else []
    if len(cur) >= in_a_row:
        runs += cur
    return runs


def hit_episodes(hits: list[datetime]) -> list[dict]:
    out: list[dict] = []
    for t in hits:
        if out and t - _dt(out[-1]["last_fired"]) <= timedelta(hours=EPISODE_GAP_HOURS):
            out[-1]["last_fired"] = satcom._ts(t)
        else:
            out.append({"since": satcom._ts(t), "last_fired": satcom._ts(t)})
    return out


def cmd_compare(a) -> int:
    doc = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
    start, end = _day(doc["start"]), _day(doc["end"])
    anns, posts, viasat = annotations(doc["start"], doc["end"]), netblocks_posts(), viasat_times()
    rows = [r for r in doc["series"] if not r.get("error")]
    world = {t: p for t, p, _ in hour_readings(load_series(0, None, start, end))}
    def clean(readings, ctl):
        """Hours when all of Radar's traffic was not itself under CONTROL_LINE."""
        return [x for x in readings if ctl.get(x[0], 100.0) >= CONTROL_LINE]
    read = {(r["asn"], r["cc"]): clean(hour_readings(load_series(r["asn"], r["cc"], start, end)), world) for r in rows}
    case_read = {}
    for c in CASES:
        at = _dt(c["at"])
        if at >= start + timedelta(days=PART_DAYS):
            case_read[c["id"]] = read[(c["asn"], c["cc"])]
        else:
            day = at.replace(hour=0, minute=0, second=0, microsecond=0)
            a0, b0 = day - timedelta(days=PART_DAYS + 1), day + timedelta(days=3)
            ctl = {t: p for t, p, _ in hour_readings(load_series(0, None, a0, b0))}
            case_read[c["id"]] = clean(hour_readings(load_series(c["asn"], c["cc"], a0, b0)), ctl)
    def group(r):
        return "starlink_world" if r["asn"] == STARLINK and not r["cc"] else (
            "starlink_countries" if r["asn"] == STARLINK else "other_networks_world")
    out = []
    for line in LINES:
        for k in RUNS_IN_A_ROW:
            for mc in MIN_COUNTS:
                tally: dict[str, dict] = {}
                for r in rows:
                    t = tally.setdefault(group(r), {"episodes": 0, "with_a_report": 0, "holiday_no_report": 0})
                    for e in hit_episodes(short_hits(read[(r["asn"], r["cc"])], line, k, mc)):
                        t["episodes"] += 1
                        if evidence(r, e, anns, posts, viasat):
                            t["with_a_report"] += 1
                        elif holiday_near(e):
                            t["holiday_no_report"] += 1
                caught = []
                for c in CASES:
                    at = _dt(c["at"])
                    if any(abs(h - at) <= timedelta(hours=6) for h in short_hits(case_read[c["id"]], line, k, mc)):
                        caught.append(c["id"])
                out.append({"line_pct": line, "hours_in_a_row": k, "min_counts": mc, "cases_caught": caught, **tally})
                print(line, k, mc, len(caught), {g: v["episodes"] for g, v in tally.items()}, flush=True)
    (OUT / "compare.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


# --- the rule as changed by ideas#168, against the rule before it, on the same series ----------------------------------
def short_replay(s: Series, control: dict) -> dict:
    """The real BO-IND-22 reading at every hour with a full window behind it: {states, hits}, hits being the hours it
    fired (each the end of the run's window)."""
    states: dict[str, int] = {}
    hits: list[datetime] = []
    span = timedelta(days=PART_DAYS)
    gate = Gate(None, None)
    real = satcom.sparse, satcom.quantum
    satcom.sparse = lambda pts: gate.why
    satcom.quantum = lambda pts: gate.q
    try:
        for j in range(len(s.times)):
            last = s.times[j] + timedelta(hours=1)
            first = last - span
            if first < s.times[0] or any(first <= b < last for b in s.breaks):
                continue
            i = bisect.bisect_left(s.times, first)
            g = s.gates[s.gate_of[j]]
            gate.why, gate.q = g.why, g.q
            r = satcom.short_reading(list(zip(s.stamps[i:j + 1], s.values[i:j + 1])), control)
            states[r["state"]] = states.get(r["state"], 0) + 1
            if r["state"] == "fired":
                hits.append(s.times[j])
    finally:
        satcom.sparse, satcom.quantum = real
    return {"states": states, "hits": hits}


def cmd_after(a) -> int:
    doc = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
    start, end = _day(doc["start"]), _day(doc["end"])
    control = {t: p for t, p, _ in hour_readings(load_series(0, None, start, end))}
    before, after, held = [], [], {"radar_wide": 0, "no_control": 0}
    held_rows = []
    short = None
    for r in doc["series"]:
        if r.get("error"):
            continue
        s = load_series(r["asn"], r["cc"], start, end)
        fired = replay(s)["fired"]
        kept = []
        for f in fired:
            st = satcom.hold({"state": "fired", "since": f["since"]}, control, _dt(f["at"]))["state"]
            if st == "fired":
                kept.append(f)
        eb, ea = episodes(fired), episodes(kept)
        before += eb
        after += ea
        for e in eb:                       # an episode is held when none of its fired hours survives the hold
            if not any(_dt(e["first_fired"]) <= _dt(f["at"]) <= _dt(e["last_fired"]) for f in kept):
                st = satcom.hold({"state": "fired", "since": e["since"]}, control, _dt(e["last_fired"]))["state"]
                held[st if st in held else "radar_wide"] += 1
                held_rows.append({"asn": r["asn"], "cc": r["cc"], "since": e["since"], "state": st})
        if r["asn"] == STARLINK and not r["cc"]:
            short = short_replay(s, control)
        print(f"AS{r['asn']} {r['cc'] or 'world'}: {len(eb)} -> {len(ea)}", flush=True)
    sched = lambda xs: sum(1 for e in xs if LIVE_TIMING in e["run_hours_mod_4"])
    eps = hit_episodes(short["hits"]) if short else []
    out = {"bo_ind_21": {"episodes_before": len(before), "episodes_after": len(after),
                         "scheduled_before": sched(before), "scheduled_after": sched(after), "held": held,
                         "held_on_18_nov_2025": sum(1 for x in held_rows if x["since"].startswith("2025-11-18")),
                         "left_on_18_nov_2025": sum(1 for e in after if e["since"].startswith("2025-11-18"))},
           "bo_ind_22_starlink_world": {"states": short["states"] if short else None, "episodes": eps}}
    (OUT / "after.json").write_text(json.dumps({**out, "held_rows": held_rows}, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--start", required=True, help="first day read, YYYY-MM-DD")
    r.add_argument("--end", required=True, help="end day (exclusive), YYYY-MM-DD")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("report", help="join the last run's episodes to independent reports").set_defaults(fn=cmd_report)
    sub.add_parser("compare", help="candidate short-outage readings against the same series").set_defaults(fn=cmd_compare)
    sub.add_parser("after", help="the rule with the Radar-wide hold and short reading, against the rule before").set_defaults(fn=cmd_after)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
