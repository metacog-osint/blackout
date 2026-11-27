"""OONI (Open Observatory of Network Interference) aggregation collector and normalizer."""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .ioda import resolve_country


def normalize_ooni_rows(rows: list[dict]) -> list[dict]:
    """Normalize OONI aggregation rows into censorship anomaly records."""
    out = []
    for r in rows:
        cc = (r.get("probe_cc") or r.get("country") or "").upper()
        if len(cc) != 2:
            continue
        m_count = int(r.get("measurement_count", 0))
        if m_count <= 0:
            continue
        confirmed = int(r.get("confirmed_count", 0))
        anomaly = int(r.get("anomaly_count", 0))
        ok_count = int(r["ok_count"]) if r.get("ok_count") is not None else None
        rate = round((confirmed + anomaly) / m_count, 4)
        confirmed_rate = round(confirmed / m_count, 4)
        cname, def_lat, def_lon = resolve_country(cc, r.get("country_name"))
        lat = float(r["lat"]) if r.get("lat") is not None else def_lat
        lon = float(r["lon"]) if r.get("lon") is not None else def_lon
        day = r.get("measurement_start_day") or r.get("day")
        if lat is None or lon is None or not day:
            continue
        test_name = r.get("test_name") or "all tests"
        window_days = int(r.get("window_days") or 1)
        span = f"on {day}" if window_days == 1 else f"over the {window_days} days to {day}"
        out.append({
            "id": r.get("id") or f"ooni-{cc.lower()}-{day}",
            "country": cc,
            "country_name": cname,
            "lat": lat,
            "lon": lon,
            "day": day,
            "test_name": test_name,
            "window_days": window_days,
            "measurement_count": m_count,
            "confirmed_count": confirmed,
            "anomaly_count": anomaly,
            "ok_count": ok_count,
            "anomaly_rate": rate,
            "confirmed_rate": confirmed_rate,
            "summary": (
                f"{cname} ({cc}): OONI anomaly/blocking rate {round(rate * 100, 1)}% "
                f"({confirmed} confirmed blocks, {anomaly} anomalies in {m_count} measurements, {test_name}, {span})."
            ),
        })
    out.sort(key=lambda x: (-x["anomaly_rate"], x["country"]))
    return out


# The second line for a shutdown reading (15 Nov 2026 suite review; in use since 16 Nov): OONI in the outage's own
# country and hours against the same hours of the 5 days before, so the evidence moves with the event instead of
# being a standing fact about a country. OONI's aggregation gives hourly counts only for a range of 7 days or less
# (checked 17 Nov 2026: 8 days and more answer 400 "Choose time_grain between day, week, auto"), so one request covers
# the 5 days before and the outage's own two days.
WINDOW_BASE_DAYS = 5
WINDOW_MIN_MEASUREMENTS = 50      # BO-IND-3's floor, applied to the outage hours
WINDOW_MIN_RATE = 0.15            # BO-IND-3's rate, applied to the outage hours
WINDOW_RISE = 2.0                 # and at least twice the same hours' rate over the 5 days before


def _hour(ts: str) -> datetime:
    return datetime.strptime(ts[:13], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def outage_window(rows: list[dict], start: str, minutes: int, now: datetime | None = None) -> dict:
    """OONI in an outage's hours against the same hours of the 5 days before, from hourly aggregation rows.

    An hour OONI does not list had no measurements (0). Returns counts, rates and `rising_blocking`: the outage hours
    meet BO-IND-3 (>= 50 measurements, >= 15% confirmed or anomalous) and the rate is at least twice the baseline's."""
    t0 = _hour(start)
    end = t0 + timedelta(minutes=max(int(minutes), 60))
    if now is not None:
        end = min(end, now)
    hours = []
    h = t0
    while h < end and len(hours) < 24:
        hours.append(h)
        h += timedelta(hours=1)
    by_hour = {}
    for r in rows:
        ts = r.get("measurement_start_day") or ""
        if len(ts) >= 13:
            by_hour[ts[:13]] = r

    def tally(hs):
        m = c = a = 0
        for x in hs:
            r = by_hour.get(x.strftime("%Y-%m-%dT%H"))
            if r:
                m += int(r.get("measurement_count") or 0)
                c += int(r.get("confirmed_count") or 0)
                a += int(r.get("anomaly_count") or 0)
        return m, c, a

    m, c, a = tally(hours)
    base_hours = [x - timedelta(days=d) for d in range(1, WINDOW_BASE_DAYS + 1) for x in hours]
    bm, bc, ba = tally(base_hours)
    rate = round((c + a) / m, 4) if m else None
    base_rate = round((bc + ba) / bm, 4) if bm else None
    expected = round(bm / WINDOW_BASE_DAYS, 1)
    rising = (m >= WINDOW_MIN_MEASUREMENTS and rate is not None and rate >= WINDOW_MIN_RATE
              and (base_rate is None or base_rate == 0 or rate >= WINDOW_RISE * base_rate))
    return {"hours": len(hours), "from": hours[0].strftime("%Y-%m-%dT%H:00:00Z") if hours else start,
            "measurements": m, "confirmed": c, "anomalies": a, "rate": rate,
            "expected_measurements": expected, "baseline_rate": base_rate, "baseline_days": WINDOW_BASE_DAYS,
            "rising_blocking": bool(rising)}


def fetch_outage_window(cc: str, start: str, minutes: int, now: datetime | None = None, fetch=None) -> dict:
    """Hourly OONI aggregation for one country, the 5 days before an outage's day through the next day (one request,
    7 days, OONI's limit for hourly counts). The outage hours are capped at 24, so they end within those two days."""
    now = now or datetime.now(timezone.utc)
    t0 = _hour(start)
    day0 = t0.replace(hour=0)
    since = (day0 - timedelta(days=WINDOW_BASE_DAYS)).strftime("%Y-%m-%d")
    until = (day0 + timedelta(days=2)).strftime("%Y-%m-%d")  # exclusive: the outage's day and the next
    params = urllib.parse.urlencode({"probe_cc": cc, "since": since, "until": until,
                                     "axis_x": "measurement_start_day", "time_grain": "hour"})
    url = f"https://api.ooni.io/api/v1/aggregation?{params}"
    if fetch is None:
        req = urllib.request.Request(url, headers={"User-Agent": "Blackout/0.1 (+https://theintelligence.company)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read(8_000_000))
    else:
        payload = fetch(url)
    res = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(res, list):
        raise ValueError("OONI aggregation: no hourly result list")
    return outage_window(res, start, minutes, now=now)


def fetch_ooni_live(countries: tuple[str, ...] = ("IR", "RU", "MM", "SD", "PK", "VE")) -> tuple[list[dict], list[dict]]:
    """OONI aggregation over the last 7 days for each country, all tests together: (rows, problems).

    The API's anomaly, confirmed, failure and ok counts are disjoint and sum to measurement_count (checked against
    the live API, 12 Nov 2026). A country that fails is listed in problems and left out."""
    problems = []
    until = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = []
    for cc in countries:
        params = urllib.parse.urlencode({"probe_cc": cc, "since": since, "until": until})
        url = f"https://api.ooni.io/api/v1/aggregation?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Blackout/0.1 (+https://theintelligence.company)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read(2_000_000))
        except Exception as exc:
            print(f"[blackout:ooni] warning: failed fetching {cc}: {exc}", file=sys.stderr)
            problems.append({"country": cc, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        res = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(res, list) and res:
            m_tot = sum(int(item.get("measurement_count", 0)) for item in res if isinstance(item, dict))
            c_tot = sum(int(item.get("confirmed_count", 0)) for item in res if isinstance(item, dict))
            a_tot = sum(int(item.get("anomaly_count", 0)) for item in res if isinstance(item, dict))
            o_tot = sum(int(item.get("ok_count", 0)) for item in res if isinstance(item, dict))
            res = {"measurement_count": m_tot, "confirmed_count": c_tot, "anomaly_count": a_tot, "ok_count": o_tot}
        if isinstance(res, dict) and res.get("measurement_count"):
            rows.append({
                "probe_cc": cc,
                "day": until,
                "window_days": 7,
                "test_name": "all tests",
                "measurement_count": res.get("measurement_count", 0),
                "confirmed_count": res.get("confirmed_count", 0),
                "anomaly_count": res.get("anomaly_count", 0),
                "ok_count": res.get("ok_count"),
            })
    return normalize_ooni_rows(rows), problems
