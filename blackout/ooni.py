"""OONI (Open Observatory of Network Interference) aggregation collector and normalizer."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .ioda import COUNTRY_CENTROIDS


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
        rate = round((confirmed + anomaly) / m_count, 4)
        confirmed_rate = round(confirmed / m_count, 4)
        cname, lat, lon = COUNTRY_CENTROIDS.get(cc, (cc, float(r.get("lat", 0.0)), float(r.get("lon", 0.0))))
        test_name = r.get("test_name") or "web_connectivity,telegram,signal"
        day = r.get("measurement_start_day") or r.get("day") or "2026-11-01"
        out.append({
            "id": r.get("id") or f"ooni-{cc.lower()}-{day}",
            "country": cc,
            "country_name": cname,
            "lat": lat,
            "lon": lon,
            "day": day,
            "test_name": test_name,
            "measurement_count": m_count,
            "confirmed_count": confirmed,
            "anomaly_count": anomaly,
            "anomaly_rate": rate,
            "confirmed_rate": confirmed_rate,
            "summary": (
                f"{cname} ({cc}): OONI anomaly/blocking rate {round(rate * 100, 1)}% "
                f"({confirmed} confirmed blocks, {anomaly} anomalies across {m_count} probes on {test_name})."
            ),
        })
    out.sort(key=lambda x: (-x["anomaly_rate"], x["country"]))
    return out


def fetch_ooni_live(countries: tuple[str, ...] = ("IR", "RU", "MM", "SD", "PK", "VE")) -> list[dict]:
    """Fetch recent 7-day OONI aggregation stats for monitored countries."""
    until = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = []
    for cc in countries:
        params = urllib.parse.urlencode({"probe_cc": cc, "since": since, "until": until})
        url = f"https://api.ooni.io/api/v1/aggregation?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Blackout/0.1 (+https://theintelligence.company)"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read(2_000_000))
        res = payload.get("result") or {}
        if isinstance(res, dict) and res.get("measurement_count"):
            rows.append({
                "probe_cc": cc,
                "day": until,
                "measurement_count": res.get("measurement_count", 0),
                "confirmed_count": res.get("confirmed_count", 0),
                "anomaly_count": res.get("anomaly_count", 0),
            })
    return normalize_ooni_rows(rows)
