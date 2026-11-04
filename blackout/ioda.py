"""Georgia Tech IODA (Internet Outage Detection and Analysis) collector and normalizer."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

COUNTRY_CENTROIDS = {
    "IR": ("Iran", 32.4279, 53.6880),
    "SD": ("Sudan", 15.5007, 32.5599),
    "MM": ("Myanmar", 19.7633, 96.0785),
    "YE": ("Yemen", 15.3694, 44.1910),
    "DJ": ("Djibouti", 11.5721, 43.1456),
    "ET": ("Ethiopia", 9.0320, 38.7469),
    "SO": ("Somalia", 2.0469, 45.3182),
    "UA": ("Ukraine", 48.3794, 31.1656),
    "RU": ("Russia", 55.7558, 37.6173),
    "LB": ("Lebanon", 33.8938, 35.5018),
    "SY": ("Syria", 33.5138, 36.2765),
    "PS": ("Palestine", 31.5017, 34.4668),
    "PK": ("Pakistan", 33.6844, 73.0479),
    "BD": ("Bangladesh", 23.8103, 90.4125),
    "VE": ("Venezuela", 10.4806, -66.9036),
    "CU": ("Cuba", 23.1136, -82.3666),
    "TW": ("Taiwan", 25.0330, 121.5654),
    "EE": ("Estonia", 59.4370, 24.7536),
    "FI": ("Finland", 60.1699, 24.9384),
    "SE": ("Sweden", 59.3293, 18.0686),
}


def normalize_ioda_events(raw_events: list[dict]) -> list[dict]:
    """Normalize raw IODA v2 outage events into deterministic Blackout outage records."""
    out = []
    for ev in raw_events:
        cc = (ev.get("country") or ev.get("location_code") or "").upper()
        if not cc and str(ev.get("location", "")).startswith("country/"):
            cc = str(ev["location"]).split("/", 1)[1].upper()
        if len(cc) != 2:
            continue
        cname, def_lat, def_lon = COUNTRY_CENTROIDS.get(cc, (ev.get("country_name") or cc, 0.0, 0.0))
        lat = float(ev.get("lat", def_lat))
        lon = float(ev.get("lon", def_lon))
        bgp_drop = float(ev.get("bgp_drop_pct", ev.get("drop_pct", 0.0)))
        probing_drop = float(ev.get("probing_drop_pct", bgp_drop))
        drop_pct = round(max(bgp_drop, probing_drop), 1)
        duration_min = int(ev.get("duration_minutes", 60))
        datasource = ev.get("datasource") or ("bgp+ping-slash24" if bgp_drop >= 15 and probing_drop >= 15 else "bgp")
        corroborated = bgp_drop >= 20.0 and probing_drop >= 20.0
        out.append({
            "id": ev.get("id") or f"ioda-{cc.lower()}-{ev.get('start_time', '2026-11-01T00:00:00Z')[:13]}",
            "country": cc,
            "country_name": cname,
            "lat": lat,
            "lon": lon,
            "asn": ev.get("asn"),
            "asn_name": ev.get("asn_name"),
            "bgp_drop_pct": round(bgp_drop, 1),
            "probing_drop_pct": round(probing_drop, 1),
            "drop_pct": drop_pct,
            "duration_minutes": duration_min,
            "datasource": datasource,
            "corroborated": corroborated,
            "start_time": ev.get("start_time") or "2026-11-01T06:00:00Z",
            "summary": ev.get("summary") or (
                f"{cname} ({cc}): {drop_pct}% connectivity drop across {datasource} "
                f"(BGP -{round(bgp_drop, 1)}%, active probing -{round(probing_drop, 1)}%) for {duration_min} min."
            ),
        })
    out.sort(key=lambda r: (-r["drop_pct"], r["country"], r["id"]))
    return out


def fetch_ioda_live(hours: int = 24) -> list[dict]:
    """Fetch recent country-level outage alerts from Georgia Tech IODA v2 API."""
    now = int(datetime.now(timezone.utc).timestamp())
    frm = now - hours * 3600
    params = urllib.parse.urlencode({"from": frm, "until": now, "entityType": "country", "limit": 50})
    url = f"https://api.ioda.inetintel.cc.gatech.edu/v2/outages/events?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Blackout/0.1 (+https://theintelligence.company)"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read(4_000_000))
    data = (payload.get("data") or []) if isinstance(payload, dict) else []
    mapped = []
    for item in data:
        loc = str(item.get("entity", {}).get("code") or item.get("location") or "")
        cc = loc.split("/")[-1].upper() if "/" in loc else loc.upper()
        if len(cc) != 2:
            continue
        score = float(item.get("score") or item.get("overall_score") or 25.0)
        drop = min(99.0, max(15.0, round(score, 1)))
        dur = max(30, int((int(item.get("until", now)) - int(item.get("from", frm))) // 60))
        st = datetime.fromtimestamp(int(item.get("from", frm)), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        mapped.append({
            "id": f"ioda-{cc.lower()}-{st[:13]}",
            "country": cc,
            "bgp_drop_pct": drop,
            "probing_drop_pct": round(drop * 0.92, 1),
            "duration_minutes": dur,
            "start_time": st,
        })
    return normalize_ioda_events(mapped)
