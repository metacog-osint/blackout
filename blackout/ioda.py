"""Georgia Tech IODA (Internet Outage Detection and Analysis) collector and normalizer.

Live collection (checked against the real API, 12 Nov 2026):
  1. /v2/outages/events lists outage events. Each has `location` ("country/IR"), `location_name`, `start` and
     `duration` (seconds), `datasource` and `score`. The score is IODA's severity magnitude (often in the thousands),
     not a percentage, so it is kept as `ioda_score` and never shown as a drop.
  2. For each country with a BGP, active-probing or darknet event, /v2/signals/raw/country/<cc> gives each signal's
     time series. The drop is the median during the event against the median of the 7 days before it, per
     datasource, and the z-score is against that same 7-day window. Nothing is estimated when a series is missing.
"""
from __future__ import annotations

import json
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from tic_doctrine import geo

API = "https://api.ioda.inetintel.cc.gatech.edu/v2"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"

# IODA datasource -> Blackout field. gtr (Google traffic), mozilla and the ping loss/latency series are not used.
SIGNALS = {"bgp": "bgp_drop_pct", "ping-slash24": "probing_drop_pct", "merit-nt": "darknet_drop_pct"}
LABELS = {"bgp": "BGP", "ping-slash24": "active probing", "merit-nt": "darknet"}

BASELINE_DAYS = 7
MIN_DROP_PCT = 20.0          # below this an event is not kept at all (BO-IND-2 needs >=20%)
IND1_DROP_PCT = 25.0         # BO-IND-1: drop_pct >= 25 and duration_minutes >= 30
IND1_MIN_MINUTES = 30
IND4_BGP_MAX = 5.0           # BO-IND-4: BGP steady (< 5%) while probing or darknet drops >= 25%


def resolve_country(cc: str, fallback_name: str | None = None) -> tuple[str, float | None, float | None]:
    """(name, lat, lon) for an ISO alpha-2 code from the kit's Natural Earth label points; lat/lon None if unknown."""
    code = (cc or "").strip().upper()
    pt = geo.point(code)
    name = fallback_name or geo.name(code) or code
    if pt and len(pt) == 2:
        return name, float(pt[0]), float(pt[1])
    return name, None, None


def _num(v) -> float | None:
    return None if v is None else float(v)


def duration_text(minutes: float) -> str:
    """A duration a reader takes in at a glance: "45 min", "1 h 15 min", "56 h", "3 d 4 h" (the page's durText)."""
    m = int(round(minutes))
    if m < 60:
        return f"{m} min"
    if m < 600:
        h, r = divmod(m, 60)
        return f"{h} h {r} min" if r else f"{h} h"
    h = int(round(m / 60))
    if h < 72:
        return f"{h} h"
    d, r = divmod(h, 24)
    return f"{d} d {r} h" if r else f"{d} d"


def _iso(ts: int | float) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_ioda_events(raw_events: list[dict]) -> list[dict]:
    """Normalize per-country outage records (live, from fetch_ioda_live, or seed) into Blackout outage records.

    Drops that were not measured stay None; nothing is filled in from another datasource. indicator_id is set only
    when the record meets BO-IND-1 or BO-IND-4; records below both (20-25% drops) are kept for BO-IND-2 corridor
    correlation but not published on their own."""
    out = []
    for ev in raw_events:
        cc = (ev.get("country") or "").upper()
        if not cc and str(ev.get("location", "")).startswith("country/"):
            cc = str(ev["location"]).split("/", 1)[1].upper()
        if len(cc) != 2 or not ev.get("start_time") or ev.get("duration_minutes") is None:
            continue
        cname, def_lat, def_lon = resolve_country(cc, ev.get("country_name"))
        lat = _num(ev.get("lat")) if ev.get("lat") is not None else def_lat
        lon = _num(ev.get("lon")) if ev.get("lon") is not None else def_lon
        if lat is None or lon is None:
            continue
        drops = {f: _num(ev.get(f)) for f in SIGNALS.values()}
        measured = [v for v in drops.values() if v is not None]
        if not measured:
            continue
        drop_pct = round(max(measured), 1)
        if drop_pct < MIN_DROP_PCT:
            continue
        bgp, probing, darknet = drops["bgp_drop_pct"], drops["probing_drop_pct"], drops["darknet_drop_pct"]
        duration_min = int(ev["duration_minutes"])
        corroborated = bgp is not None and probing is not None and bgp >= 20.0 and probing >= 20.0
        artefact = bgp is not None and bgp < IND4_BGP_MAX and max(probing or 0.0, darknet or 0.0) >= IND1_DROP_PCT
        if artefact:
            indicator_id = "BO-IND-4"
        elif drop_pct >= IND1_DROP_PCT and duration_min >= IND1_MIN_MINUTES:
            indicator_id = "BO-IND-1"
        else:
            indicator_id = None
        dropped = [ds for ds, f in SIGNALS.items() if drops[f] is not None and drops[f] >= 15.0]
        datasource = "+".join(dropped) if dropped else "none above 15%"
        parts = [f"{LABELS[ds]} -{round(drops[f], 1)}%" if drops[f] > 0 else f"{LABELS[ds]} no drop"
                 for ds, f in SIGNALS.items() if drops[f] is not None]
        zscore = ev.get("baseline_zscore")
        out.append({
            "id": ev.get("id") or f"ioda-{cc.lower()}-{ev['start_time'][:13]}",
            "country": cc,
            "country_name": cname,
            "lat": lat,
            "lon": lon,
            "asn": ev.get("asn"),
            "asn_name": ev.get("asn_name"),
            "bgp_drop_pct": None if bgp is None else round(bgp, 1),
            "probing_drop_pct": None if probing is None else round(probing, 1),
            "darknet_drop_pct": None if darknet is None else round(darknet, 1),
            "drop_pct": drop_pct,
            "baseline_zscore": None if zscore is None else round(float(zscore), 2),
            "baseline_ref": ev.get("baseline_ref"),
            "ioda_score": ev.get("ioda_score"),
            "ioda_datasources": ev.get("ioda_datasources") or [],
            "duration_minutes": duration_min,
            "datasource": datasource,
            "corroborated": corroborated,
            "indicator_id": indicator_id,
            "start_time": ev["start_time"],
            "summary": ev.get("summary") or (
                f"{cname} ({cc}): {drop_pct}% connectivity drop ({', '.join(parts)}) "
                f"over {duration_text(duration_min)}, against the 7 days before."
            ),
        })
    out.sort(key=lambda r: (-r["drop_pct"], r["country"], r["id"]))
    return out


def _get_json(url: str, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(8_000_000))


def _series(payload: dict) -> list[dict]:
    """The list of signal series in a /signals/raw response (data is a list holding one list of series)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], list):
        return [s for s in data[0] if isinstance(s, dict)]
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)]
    return []


def signal_drop(series: dict, start: int, end: int, baseline_days: int = BASELINE_DAYS) -> dict | None:
    """Drop of one IODA series during [start, end) against the median of the baseline_days before start.

    Returns {drop_pct, zscore, baseline, current, points} or None when either window has no numeric values."""
    t0, step = int(series.get("from", 0)), int(series.get("step") or 0)
    vals = series.get("values") or []
    if not step or not vals:
        return None
    base_from = start - baseline_days * 86400
    base, cur = [], []
    for i, v in enumerate(vals):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        t = t0 + i * step
        if base_from <= t < start:
            base.append(float(v))
        elif start <= t < end:
            cur.append(float(v))
    if len(base) < 3 or not cur:
        return None
    b_med = statistics.median(base)
    if b_med <= 0:
        return None
    c_med = statistics.median(cur)
    sd = statistics.pstdev(base)
    return {
        "drop_pct": round(max(0.0, (b_med - c_med) / b_med * 100.0), 1),
        "zscore": round((c_med - statistics.fmean(base)) / sd, 2) if sd > 0 else None,
        "baseline": round(b_med, 2),
        "current": round(c_med, 2),
        "points": len(cur),
    }


def fetch_ioda_live(hours: int = 24, max_countries: int = 40, fetch=_get_json, now: int | None = None
                    ) -> tuple[list[dict], dict]:
    """Live IODA outages for the last `hours`: (normalized outages, run stats).

    One record per country: the span of its BGP, active-probing and darknet events in the window, measured on the
    raw signals. Countries whose only events are Google traffic (gtr) are not checked. A country whose signals cannot
    be read is listed in stats["problems"] and left out; nothing is estimated in its place."""
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    frm = now - hours * 3600
    q = urllib.parse.urlencode({"from": frm, "until": now, "entityType": "country", "limit": 500})
    payload = fetch(f"{API}/outages/events?{q}")
    events = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("IODA outages/events: no data list in the response")

    by_cc: dict[str, dict] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("datasource") not in SIGNALS:
            continue
        loc = str(ev.get("location") or "")
        if not loc.startswith("country/"):
            continue
        cc = loc.split("/", 1)[1].upper()
        try:
            start = int(ev["start"])
            end = start + int(ev.get("duration") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        g = by_cc.setdefault(cc, {"start": start, "end": end, "score": 0.0, "datasources": set(),
                                  "name": ev.get("location_name")})
        g["start"], g["end"] = min(g["start"], start), max(g["end"], end)
        g["score"] = max(g["score"], float(ev.get("score") or 0.0))
        g["datasources"].add(ev["datasource"])

    stats = {"events": len(events), "countries_with_events": len(by_cc), "countries_checked": 0, "problems": []}
    ranked = sorted(by_cc.items(), key=lambda kv: -kv[1]["score"])[:max_countries]
    raw = []
    for cc, g in ranked:
        end = min(g["end"], now)
        if end - g["start"] < 600:
            continue
        rec = {"country": cc, "country_name": None, "start_time": _iso(g["start"]),
               "duration_minutes": (end - g["start"]) // 60, "ioda_score": round(g["score"], 1),
               "ioda_datasources": sorted(g["datasources"]),
               "baseline_ref": f"median of the {BASELINE_DAYS} days before the event, per IODA datasource"}
        best = None
        for ds, field in SIGNALS.items():
            sq = urllib.parse.urlencode({"from": g["start"] - BASELINE_DAYS * 86400, "until": end,
                                         "datasource": ds, "maxPoints": 600})
            try:
                series = _series(fetch(f"{API}/signals/raw/country/{cc}?{sq}"))
            except Exception as exc:  # network, HTTP or JSON: record it and measure nothing for this signal
                stats["problems"].append({"country": cc, "datasource": ds, "error": f"{type(exc).__name__}: {exc}"[:200]})
                continue
            s = next((x for x in series if x.get("datasource") == ds), None)
            if s and not rec["country_name"]:
                rec["country_name"] = s.get("entityName")
            m = signal_drop(s, g["start"], end) if s else None
            if m is None:
                continue
            rec[field] = m["drop_pct"]
            if best is None or m["drop_pct"] > best["drop_pct"]:
                best = m
        stats["countries_checked"] += 1
        if best is None:
            continue
        rec["baseline_zscore"] = best["zscore"]
        rec["id"] = f"ioda-{cc.lower()}-{rec['start_time'][:13]}"
        raw.append(rec)
    for r in raw:  # IODA's long names ("Iran (Islamic Republic Of)"); prefer the kit's plain ones
        r["country_name"] = geo.name(r["country"]) or r["country_name"]
    return normalize_ioda_events(raw), stats
