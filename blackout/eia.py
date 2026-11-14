"""US grid (BO-WP-5): balancing authorities whose hourly demand falls far below what their own forecast implies,
from EIA-930 (the Hourly and Daily Balancing Authority Operations Report).

Every US balancing authority (BA) reports to the Energy Information Administration each hour its demand (D) and, once a
day, its day-ahead demand forecast (DF). When a hurricane, an ice storm or strikes on the grid take customers off,
their load goes with them, so the BA serves far less than it forecast the day before. Blackout reads both series from
EIA's API v2:

  GET api.eia.gov/v2/electricity/rto/region-data/data/?frequency=hourly&data[0]=value&facets[type][]=D
      &facets[type][]=DF&start=YYYY-MM-DDTHH&end=YYYY-MM-DDTHH&sort...&offset=0&length=5000&api_key=...
  -> {"response": {"total": "3984", "data": [{"period": "2026-11-13T14", "respondent": "TEC",
      "respondent-name": "Tampa Electric Company", "type": "D", "type-name": "Demand", "value": "1873",
      "value-units": "megawatthours"}, ...]}, "request": {...}}   (value is a string; the key is not echoed)

Facts the rule rests on (EIA-930 instructions, eia.gov/survey/form/eia_930/instructions.pdf, OMB 1905-0129, read 13 Nov
2026): values are "hourly integrated values in megawatts by hour ending time", stamped in UTC, so period
"2026-11-13T14" is the hour 13:00-14:00 UTC; a BA that can't report final data on time submits best estimates and
corrects them "within 3 days"; and a BA that doesn't produce a day-ahead forecast "directly comparable to actual
demand" is not required to, so some forecasts run far off every day (Duke Energy Florida's demand ran a median 31%
above its forecast in the week before Hurricane Milton; Salt River Project's ran 33% or more below it in 5% of the
hours of 5-13 Nov 2026). The API serves 83 respondents: BAs plus 14 regional sums (`REGIONS`), which are left out.

Reading (checked on 13 Nov 2026): demand arrives about 2 h behind the clock and the newest hour is often partial
(Arizona Public Service at 14:00 UTC that day: 728 MW against a 4,114 MW forecast); a BA can also post a one-hour
glitch (Southern Company at 15:00 UTC 7 Nov 2026: 9,441 against 23,037). A week of all respondents in one query
answered 504 Gateway Timeout. Queries over a day or two answered in 1-30 s, now and then after a 504 or a wait of
60-85 s (about 60 queries on 13 Nov 2026). A two-day window (about 6,400 rows) needs a second 5,000-row page; a
one-day window (about 4,000) fits in one, for the same number of requests, so Blackout works in nine one-day windows,
four at a time, each retried, under a 180 s deadline. A full read took about 40 s in a live run.

Carried hours (13 Nov 2026). That evening (23:00-23:30 UTC) a one-day query EIA had never been asked took 7-90 s, and
some answered 503 or 504 after 14-60 s; EIA's backend answers a repeated query from its cache (0.7 s, X-Cache: MISS at
its proxy) but only for minutes, not across 4-hourly runs (dropping the sort made no difference); nine queries at once
all answered 504 within 14 s, so WORKERS stays 4. The 22:43 UTC run lost its two oldest days to the deadline, leaving
SWPP (about 34,000 MW), SWPW, BANC and PNM with 117 range hours, under MIN_RANGE_HOURS. So a run reads live only the
newest LIVE_DAYS (4) windows, 96 h, plus any older window the last published run's hours don't hold in full, and takes
the rest from that run, as Black Marble carries its nights:
  - only hours that had ended SETTLED_HOURS (72 h; EIA's "within 3 days") or more before they were read are carried
    (`carry_doc`), so every carried value is past correction; the day between 72 and 96 h is slack, so runs up to a day
    apart read only the newest four windows;
  - the carry is its own file, site/data/eia-hours.json (about 80 KB; the page never loads it), written by collect,
    published by build, and read back from the live site by the next run (`previous_eia_hours` in tools/blackout.py);
    each span of hours keeps the time it was read live;
  - fresh values win: a window read live replaces every carried hour in it; a window that fails keeps the carried hours
    it has and is a problem only when some of its hours were neither read nor carried (listed, never filled in);
  - a first run, a broken or seed carry, or a gap of more than a day reads what the carry lacks (all nine with none);
  - a run that reads nothing live publishes the last carry again unchanged;
  - a value a BA revises more than 3 days after the hour is not seen once carried (each hour is read at 72-96 h).
A deadline miss is named as one ("TimeoutError: not read within the 180 s deadline"). The deadline bounds the run,
not just the wait (14 Nov 2026): no try or retry of a read starts after it and each try's timeout ends at it (`_get`),
and the pool is let go without waiting, its queued windows cancelled, so a late read is never waited for.

Rule (BO-IND-16), per BA with a median forecast of 1,000 MW or more:
  - skip the BA's two newest hours; the 24 hours before them are the window;
  - its own range: the ratio D/DF in each of the 168 hours before the window (at least 120 of them), its median
    (what this BA's demand usually is against its forecast) and its 1st percentile;
  - an hour is low when D/DF is at least `FLOOR` (25%) below that median, and at least `RANGE_MULT` (1.5) times as
    far below it as the 1st percentile is (change = ratio / median - 1 <= -max(0.25, 1.5 * (1 - p1 / median)));
  - present when 3 or more consecutive hours in the window are low; the newest such run is reported.
Calibration on real EIA-930 data, replayed hour by hour as runs would have seen it (`CALIBRATION`): it finds
Hurricane Milton at Tampa Electric and Duke Energy Florida, Hurricane Helene at Duke Energy Carolinas and Winter Storm
Uri at ERCOT; it misses Hurricane Beryl at ERCOT (22% under at worst), and over 1 Oct-13 Nov 2026, every BA of
1,000 MW or more, it gives 2 runs, neither matched to a reported outage. With the 1st percentile alone (x1.0) the same
weeks gave 10 runs, 5 of them at Salt River Project, whose forecasts often miss by 35-50% for a few hours.

What it means: demand far under forecast fits customers losing power (storm, strike, sabotage, failure), and also a
forecast miss (weather the forecast didn't expect, evacuations, holidays), batteries charging (EIA has BAs lower
demand by what storage takes), or a reporting error a later revision fixes. Blackout measures the gap; it never
assesses the cause. Points are hand-placed in each BA's service area (`PLACES`) and stand for an area, not a site.

Licence: EIA states "U.S. government publications are in the public domain and are not subject to copyright
protection" and asks for an acknowledgement with the publication date, as "Source: U.S. Energy Information
Administration (Oct 2008)" (eia.gov/about/copyrights_reuse.php, read 13 Nov 2026).

Key: env EIA_API_KEY (repo secret; set by Tom 13 Nov 2026), else on Windows the same name from HKCU\\Environment, so a
process started before Tom set it still finds it. Never printed; it is added only to the outgoing request and scrubbed
from any error text.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

API = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
PAGE = "https://www.eia.gov/electricity/gridmonitor/"
KEY_ENV = "EIA_API_KEY"
LICENCE = ("U.S. government publications are in the public domain and are not subject to copyright protection "
           "(eia.gov/about/copyrights_reuse.php, read 13 Nov 2026); acknowledged as Source: U.S. Energy Information "
           "Administration, with the date read")
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"

# EIA's regional sums (the respondent facet labels them "Region: ..."); a BA is counted once, in its own row.
REGIONS = frozenset({"US48", "CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW", "NE", "NW", "NY", "SE", "SW", "TEN", "TEX"})
DAYS = 9                 # one-day windows: 2 skipped + 24 in the window + 168 of range + the 2 h EIA runs behind
PAGE_ROWS = 5000
SKIP_NEWEST = 2
WINDOW_HOURS = 24
RANGE_HOURS = 168
MIN_RANGE_HOURS = 120
MIN_RUN = 3
MIN_FORECAST_MW = 1000
FLOOR = 0.25
LOW_PCT = 0.01
RANGE_MULT = 1.5
STALE_HOURS = 12
WORKERS = 4
DEADLINE_S = 180
LIVE_DAYS = 4            # the newest four windows (96 h) are read live every run
SETTLED_HOURS = 72       # EIA's correction period: only hours at least this old when read are carried to the next run
CARRY_FORMAT = "blackout-eia-hours/1"
CARRY_NAME = "eia-hours.json"  # published as site/data/eia-hours.json, beside the snapshot, never inside it
CARRY_KEY = "carry_out"  # fetch_eia's output holds the next run's carry here; collect takes it out before the snapshot
H = timedelta(hours=1)

# Hand-placed points in each BA's service area, with the area in words (Blackout's own, from the BAs' published
# territories; schematic, not a site). A BA not listed is still measured; its item has no point.
PLACES = {
    "PJM": (40.0, -78.0, "Mid-Atlantic and Ohio Valley, 13 states and DC"),
    "MISO": (40.5, -90.0, "the Midwest and Mississippi valley, 15 states"),
    "ERCO": (31.0, -97.5, "most of Texas"),
    "SWPP": (37.5, -98.0, "the central Plains, 14 states"),
    "SOCO": (33.0, -85.5, "Georgia, Alabama and parts of Mississippi"),
    "CISO": (36.8, -119.8, "most of California"),
    "TVA": (35.8, -86.4, "Tennessee and parts of six neighbouring states"),
    "FPL": (26.9, -80.4, "eastern and southern Florida and the western Panhandle"),
    "NYIS": (42.9, -75.5, "New York State"),
    "ISNE": (42.6, -71.8, "New England"),
    "DUK": (35.2, -81.0, "western North Carolina and South Carolina"),
    "BPAT": (46.0, -120.0, "the Pacific Northwest"),
    "CPLE": (35.5, -78.5, "eastern North Carolina and northeastern South Carolina"),
    "PACE": (41.0, -111.5, "Utah, Wyoming and southeastern Idaho"),
    "FPC": (28.6, -82.0, "central and northern Florida"),
    "AZPS": (33.8, -112.0, "Arizona outside Tucson"),
    "PSCO": (39.7, -104.9, "Colorado's Front Range"),
    "NEVP": (36.2, -115.1, "southern Nevada"),
    "LGEE": (37.9, -85.0, "Kentucky"),
    "SRP": (33.4, -111.9, "the Phoenix area"),
    "SWPW": (40.8, -105.5, "Colorado, Wyoming and neighbouring states (SPP's western area)"),
    "SC": (33.4, -80.1, "South Carolina"),
    "PGE": (45.5, -122.7, "Portland and northwestern Oregon"),
    "LDWP": (34.05, -118.25, "the City of Los Angeles"),
    "PACW": (44.9, -123.0, "Oregon, southern Washington and northern California"),
    "SCEG": (33.9, -81.0, "central and southern South Carolina"),
    "TEC": (27.95, -82.45, "the Tampa Bay area"),
    "AECI": (37.2, -93.3, "Missouri and parts of Iowa and Oklahoma"),
    "FMPP": (28.3, -81.5, "Florida municipal utilities around Orlando and Lakeland"),
    "IPCO": (43.6, -116.2, "southern Idaho and eastern Oregon"),
    "BANC": (38.6, -121.4, "Sacramento and northern California"),
    "PSEI": (47.6, -122.2, "western Washington around Seattle"),
    "PNM": (35.1, -106.6, "New Mexico"),
    "JEA": (30.3, -81.66, "Jacksonville"),
    "AVA": (47.66, -117.4, "eastern Washington and northern Idaho"),
    "TEPC": (32.2, -110.95, "the Tucson area"),
    "NWMT": (46.6, -112.0, "Montana"),
    "SCL": (47.61, -122.33, "Seattle"),
    "EPE": (31.76, -106.45, "El Paso and southern New Mexico"),
    "BHBA": (44.08, -103.23, "western South Dakota and northeastern Wyoming"),
}

# What this rule found in real EIA-930 data (API v2, read 13 Nov 2026), every run replayed hour by hour, each as a run
# 3 h after the newest hour it saw would have read it. Change = worst hour against the BA's own usual ratio.
# tests/test_lanes.py replays the Milton run (fixtures/eia, recorded for a run at 16:30 UTC 10 Oct 2024).
CALIBRATION = {
    "found": [
        {"event": "Hurricane Milton (landfall about 00:30 UTC 10 Oct 2024)", "ba": "TEC", "first_low": "2024-10-09T18",
         "worst": -0.742, "worst_hour": "2024-10-10T09"},
        {"event": "Hurricane Milton", "ba": "FPC", "first_low": "2024-10-10T01", "worst": -0.518,
         "worst_hour": "2024-10-10T14"},
        {"event": "Hurricane Helene (26-27 Sep 2024)", "ba": "DUK", "first_low": "2024-09-27T12", "worst": -0.316,
         "worst_hour": "2024-09-27T17"},
        {"event": "Winter Storm Uri load shedding (15-18 Feb 2021)", "ba": "ERCO", "first_low": "2021-02-15T12",
         "worst": -0.426, "worst_hour": "2021-02-16T02"},
    ],
    "missed": [
        {"event": "Hurricane Beryl (8 Jul 2024)", "ba": "ERCO", "worst": -0.217,
         "why": "under the 25% floor; ERCOT is large and the outage was one region of it"},
        {"event": "Hurricane Milton", "ba": "FPL", "worst": -0.128, "why": "under the 25% floor"},
        {"event": "Hurricane Helene", "ba": "SOCO", "worst": None, "why": "no gap: Southern Company's forecast tracked "
         "its demand within 5%"},
        {"event": "Winter Storm Elliott rolling outages (24 Dec 2022)", "ba": "TVA, DUK", "worst": None,
         "why": "demand ran above forecast"},
        {"event": "Houston derecho (16-17 May 2024)", "ba": "ERCO", "worst": None, "why": "no run"},
    ],
    "quiet": {"span": "2026-10-01 to 2026-11-13", "bas": 38, "runs": [
        {"ba": "SRP", "start": "2026-10-18T09", "hours": 4, "worst": -0.384},
        {"ba": "SWPW", "start": "2026-10-16T18", "hours": 3, "worst": -0.256}],
        "note": "neither matched to an outage report (not checked further); with RANGE_MULT 1.0 the same span gave 10"},
}


def api_key() -> str | None:
    """The EIA key from the environment, else (Windows) the user's saved environment. Never printed."""
    k = (os.environ.get(KEY_ENV) or "").strip()
    if k or sys.platform != "win32":
        return k or None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return (str(winreg.QueryValueEx(h, KEY_ENV)[0]).strip()) or None
    except OSError:
        return None


class DeadlinePassed(TimeoutError):
    """A read stopped at the run's deadline."""


def _get(url: str, timeout: float = 90, tries: int = 3, deadline: float | None = None):
    """GET an EIA URL (given without the key) with the key added. Retries 5xx and timeouts; errors never carry the key.
    `deadline` (time.monotonic()): no try starts within a second of it, each try's timeout ends at it, and a retry
    that would start after it raises DeadlinePassed instead."""
    key = api_key()
    if not key:
        raise RuntimeError(f"{KEY_ENV} is not set")
    full = url + ("&" if "?" in url else "?") + "api_key=" + key
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last = None
    for attempt in range(tries):
        left = timeout if deadline is None else deadline - time.monotonic()
        if left < 1:
            was = f"; last: {type(last).__name__}: {last}" if last else ""
            raise DeadlinePassed(f"stopped at the deadline after {attempt} tries{was}".replace(key, "<key>"))
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, left)) as r:
                return json.loads(r.read(20_000_000).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        if attempt + 1 < tries:
            pause = 3 * (attempt + 1)
            if deadline is not None:
                pause = min(pause, max(deadline - time.monotonic(), 0))
            time.sleep(pause)
    raise RuntimeError(f"{type(last).__name__}: {last}".replace(key, "<key>"))


def window_url(start: datetime, end: datetime, offset: int = 0) -> str:
    return (f"{API}?frequency=hourly&data[0]=value&facets[type][]=D&facets[type][]=DF"
            f"&start={start:%Y-%m-%dT%H}&end={end:%Y-%m-%dT%H}&sort[0][column]=period&sort[0][direction]=desc"
            f"&sort[1][column]=respondent&sort[1][direction]=asc&offset={offset}&length={PAGE_ROWS}")


def windows(now: datetime, days: int = DAYS) -> list[tuple[datetime, datetime]]:
    """One-day windows ending at the current hour, newest first."""
    end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    out = []
    for _ in range(days):
        out.append((end - timedelta(hours=23), end))
        end -= timedelta(hours=24)
    return out


def _hk(h: datetime) -> str:
    return h.strftime("%Y-%m-%dT%H")


def _hours(start: datetime, end: datetime) -> list[datetime]:
    out, h = [], start
    while h <= end:
        out.append(h)
        h += H
    return out


def carried(doc) -> tuple[dict, dict, dict]:
    """A published carry (`carry_doc`) as (BA -> {"D": {hour: MW}, "DF": {...}}, BA -> name, hour -> when that hour
    was read live). Only the hours its `read` spans list are held; anything that isn't a live carry in this format
    holds nothing. A malformed one raises (KeyError, TypeError, ValueError)."""
    if not isinstance(doc, dict) or doc.get("format") != CARRY_FORMAT or doc.get("mode") != "live":
        return {}, {}, {}
    start = datetime.strptime(doc["start"], "%Y-%m-%dT%H")
    held = {}
    for first, last, read_at in doc["read"]:
        for h in _hours(datetime.strptime(first, "%Y-%m-%dT%H"), datetime.strptime(last, "%Y-%m-%dT%H")):
            held[h] = str(read_at)
    series, names = {}, {}
    for ba, s in doc["bas"].items():
        if ba in REGIONS:
            continue
        for typ in ("D", "DF"):
            for i, v in enumerate(s.get(typ) or []):
                h = start + i * H
                if v is not None and h in held:
                    series.setdefault(ba, {"D": {}, "DF": {}})[typ][h] = float(v)
        if ba in series:
            names[ba] = s.get("name") or ba
    return series, names, held


def carry_doc(series: dict, names: dict, held: dict, now: datetime) -> dict | None:
    """What the next run may carry: the hours this run holds that had ended at least SETTLED_HOURS before they were
    read (past EIA's correction period), each span with the time it was read live. None when there are none."""
    end = windows(now, 1)[0][1]
    keep = sorted(h for h in held if h <= end - SETTLED_HOURS * H)
    if not keep:
        return None
    start, n = keep[0], (keep[-1] - keep[0]) // H + 1
    spans, prev = [], None
    for h in keep:
        if spans and spans[-1][2] == held[h] and prev + H == h:
            spans[-1][1] = _hk(h)
        else:
            spans.append([_hk(h), _hk(h), held[h]])
        prev = h
    kept = set(keep)

    def column(values: dict) -> list:
        out = [None] * n
        for h, v in values.items():
            if h in kept:
                out[(h - start) // H] = int(v) if float(v).is_integer() else v
        return out
    bas = {}
    for ba in sorted(series):
        d, df = column(series[ba]["D"]), column(series[ba]["DF"])
        if any(v is not None for v in d + df):
            bas[ba] = {"name": names.get(ba, ba), "D": d, "DF": df}
    return {
        "format": CARRY_FORMAT, "mode": "live", "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "about": ("EIA-930 hourly demand (D) and day-ahead demand forecast (DF) in MW per balancing authority, hour "
                  f"ending UTC, as Blackout read them: only hours that had ended {SETTLED_HOURS} h or more before they "
                  "were read (EIA's correction period), kept so the next run reads only the newest days live. "
                  "`read` lists the hours held, each span with the time it was read; D and DF count hours from "
                  "`start`, null where EIA had no value."),
        "source": "Source: U.S. Energy Information Administration", "licence": LICENCE,
        "start": _hk(start), "hours": n, "read": spans, "bas": bas,
    }


def _read_window(fetch, start: datetime, end: datetime) -> list[dict]:
    rows, offset = [], 0
    while True:
        doc = fetch(window_url(start, end, offset))
        resp = doc.get("response") if isinstance(doc, dict) else None
        if not isinstance(resp, dict) or not isinstance(resp.get("data"), list):
            raise ValueError("EIA response has no data list")
        rows += resp["data"]
        offset += len(resp["data"])
        if not resp["data"] or offset >= int(resp.get("total") or 0):
            return rows


def parse_rows(rows: list[dict]) -> tuple[dict, dict]:
    """(BA -> {"D": {hour: MW}, "DF": {...}}, BA -> name), regions left out, blank values skipped."""
    series: dict[str, dict] = {}
    names: dict[str, str] = {}
    for r in rows:
        ba, typ, val = r.get("respondent"), r.get("type"), r.get("value")
        if not ba or ba in REGIONS or typ not in ("D", "DF") or val in (None, ""):
            continue
        try:
            hour = datetime.strptime(r["period"], "%Y-%m-%dT%H")
            mw = float(val)
        except (KeyError, TypeError, ValueError):
            continue
        series.setdefault(ba, {"D": {}, "DF": {}})[typ][hour] = mw
        names[ba] = r.get("respondent-name") or ba
    return series, names


def _pct(values: list[float], q: float) -> float:
    v = sorted(values)
    i = q * (len(v) - 1)
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def _iso(h: datetime) -> str:
    return h.strftime("%Y-%m-%dT%H:00:00Z")


def assess(ba: str, name: str, d: dict, df: dict, now: datetime) -> dict:
    """One BA against its own range (see the module docstring)."""
    lat, lon, area = PLACES.get(ba, (None, None, None))
    out = {"id": ba, "name": name, "area": area, "lat": lat, "lon": lon}
    hours = sorted(h for h in d if df.get(h, 0) > 0)
    forecasts = list(df.values())
    out["median_forecast"] = round(statistics.median(forecasts)) if forecasts else None
    if not hours:
        return {**out, "state": "no_data"}
    out["newest"] = _iso(max(d))
    if (out["median_forecast"] or 0) < MIN_FORECAST_MW:
        return {**out, "state": "small"}
    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)
    if naive_now - max(d) > timedelta(hours=STALE_HOURS):
        return {**out, "state": "stale"}
    newest_d = sorted(d)
    if len(newest_d) <= SKIP_NEWEST:
        return {**out, "state": "no_data"}
    last = newest_d[-1 - SKIP_NEWEST]
    first = last - (WINDOW_HOURS - 1) * H
    ratio = {h: d[h] / df[h] for h in hours}
    base = [ratio[h] for h in hours if first - RANGE_HOURS * H <= h < first]
    out.update({"window": {"start": _iso(first - H), "end": _iso(last)}, "range_hours": len(base)})
    if len(base) < MIN_RANGE_HOURS:
        return {**out, "state": "no_range"}
    med = statistics.median(base)
    low = _pct(base, LOW_PCT)
    line = max(FLOOR, RANGE_MULT * (1 - low / med))
    out.update({"median_ratio": round(med, 4), "low_ratio": round(low, 4), "line": round(line, 4)})

    def point(h):
        exp = df[h] * med
        return {"hour": _iso(h), "demand": round(d[h]), "forecast": round(df[h]), "expected": round(exp),
                "change": round(ratio[h] / med - 1, 4)}

    recent = [h for h in hours if first <= h <= last]
    out["window_hours"] = len(recent)
    if last in ratio:
        out["latest"] = point(last)
    runs, cur = [], []
    for h in recent:
        if ratio[h] / med - 1 <= -line and (not cur or cur[-1] + H == h):
            cur.append(h)
            continue
        if len(cur) >= MIN_RUN:
            runs.append(cur)
        cur = [h] if ratio[h] / med - 1 <= -line else []
    if len(cur) >= MIN_RUN:
        runs.append(cur)
    if not runs:
        return {**out, "state": "normal"}
    run = runs[-1]
    worst = min(run, key=lambda h: ratio[h])
    out["run"] = {"start": _iso(run[0] - H), "end": _iso(run[-1]), "hours": len(run), "ongoing": run[-1] == last,
                  "worst": point(worst),
                  "shortfall_mwh": round(sum(df[h] * med - d[h] for h in run))}
    return {**out, "state": "low"}


def fetch_eia(fetch=None, now: datetime | None = None, deadline_s: float = DEADLINE_S,
              carry: dict | None = None) -> tuple[dict | None, list]:
    """EIA-930 demand against forecast for every BA. The newest LIVE_DAYS windows are read live, and so is any older
    window `carry` (the last published run's settled hours, `carry_doc`) doesn't hold in full; fresh values win.
    The output's CARRY_KEY holds the carry for the next run. None when no rows could be read live."""
    now = now or datetime.now(timezone.utc)
    if fetch is None:
        if not api_key():
            return None, [{"endpoint": "eia-key", "error": f"{KEY_ENV} is not set; EIA-930 not read"}]
        fetch = _get
    try:
        c_series, c_names, c_held = carried(carry)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        print(f"[blackout:eia] carried hours not readable, every window read live: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        c_series, c_names, c_held = {}, {}, {}
    wins = windows(now)
    span = {h for w in wins for h in _hours(*w)}
    c_held = {h: r for h, r in c_held.items() if h in span}
    want = [w for i, w in enumerate(wins) if i < LIVE_DAYS or any(h not in c_held for h in _hours(*w))]
    problems: list[dict] = []
    rows: list[dict] = []
    live: set[datetime] = set()
    failed = []
    t0 = time.monotonic()
    if fetch is _get:  # the real reader stops at the deadline too, so no read outlives the run's wait by more than a try
        fetch = functools.partial(_get, deadline=t0 + deadline_s)
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        futures = [(w, pool.submit(_read_window, fetch, *w)) for w in want]
        for (start, end), fut in futures:
            left = deadline_s - (time.monotonic() - t0)
            try:
                rows += fut.result(timeout=max(left, 0.1))
                live.update(_hours(start, end))
            except Exception as exc:
                fut.cancel()
                # fut.result's own timeout is a bare TimeoutError; _get's is DeadlinePassed
                deadline = isinstance(exc, DeadlinePassed) or (isinstance(exc, TimeoutError) and not str(exc))
                failed.append((start, end, f"TimeoutError: not read within the {deadline_s:g} s deadline" if deadline
                               else f"{type(exc).__name__}: {exc}"))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # a read past the deadline is let go, never waited for
    if not rows:
        return None, [{"endpoint": "eia-region-data", "error": f"{s:%Y-%m-%dT%H}..{e:%Y-%m-%dT%H}: {err}"[:200]}
                      for s, e, err in failed] or [{"endpoint": "eia-region-data", "error": "no rows read"}]
    use = {h: r for h, r in c_held.items() if h not in live}  # carried hours fill only what wasn't read live
    for start, end, err in failed:  # a missing day shortens some BAs' range; it is listed, never filled in
        hrs = _hours(start, end)
        k = sum(1 for h in hrs if h in use)
        if k == len(hrs):
            continue  # an earlier run's reading holds every hour of it
        note = f" ({k} of {len(hrs)} hours carried from earlier runs)" if k else ""
        problems.append({"endpoint": "eia-region-data",
                         "error": f"{start:%Y-%m-%dT%H}..{end:%Y-%m-%dT%H}{note}: {err}"[:200]})
    series: dict[str, dict] = {}
    names: dict[str, str] = {}
    for ba, s in c_series.items():
        for typ in ("D", "DF"):
            for h, v in s[typ].items():
                if h in use:
                    series.setdefault(ba, {"D": {}, "DF": {}})[typ][h] = v
        if ba in series:
            names[ba] = c_names[ba]
    fresh, fresh_names = parse_rows(rows)
    for ba, s in fresh.items():
        dest = series.setdefault(ba, {"D": {}, "DF": {}})
        dest["D"].update(s["D"])
        dest["DF"].update(s["DF"])
    names.update(fresh_names)
    bas = [assess(ba, names[ba], s["D"], s["DF"], now) for ba, s in series.items()]
    order = {"low": 0, "normal": 1, "no_range": 2, "stale": 3, "no_data": 4, "small": 5}
    bas.sort(key=lambda b: (order.get(b["state"], 9), -(b.get("median_forecast") or 0), b["id"]))
    for b in bas:
        if b["state"] == "low" and b["lat"] is None:
            problems.append({"endpoint": "eia-places", "error": f"{b['id']} has no point in eia.PLACES"})
    read_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    held = {**use, **{h: read_at for h in live}}
    n_live = sum(1 for w in wins if all(h in live for h in _hours(*w)))
    n_carried = sum(1 for w in wins if all(h in use for h in _hours(*w)))
    return {
        "read_at": read_at,
        "rule": {"floor": FLOOR, "low_percentile": LOW_PCT, "min_hours": MIN_RUN, "skip_newest": SKIP_NEWEST,
                 "window_hours": WINDOW_HOURS, "range_hours": RANGE_HOURS, "min_forecast_mw": MIN_FORECAST_MW},
        "windows_read": n_live + n_carried,  # windows held in full, read this run or carried
        "windows_live": n_live,
        "windows_carried": n_carried,
        "windows": len(wins),
        "carried": {"from_run": carry.get("read_at"), "hours": len(use), "oldest_read": min(use.values())}
        if use else None,
        "respondents": len(series),
        "assessed": sum(1 for b in bas if b["state"] in ("low", "normal")),
        "bas": bas,
        "notable": [b["id"] for b in bas if b["state"] == "low"],
        CARRY_KEY: carry_doc(series, names, held, now),
    }, problems
