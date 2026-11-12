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
