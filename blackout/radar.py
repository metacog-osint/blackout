"""Cloudflare Radar (BO-IND-9, BO-IND-10): outages Cloudflare's analysts annotate in the Radar Outage Center, with the
cause and type Cloudflare states, and traffic anomalies Cloudflare's own detection flagged and Cloudflare verified.

Blackout republishes both as reported observations: Cloudflare's cause is quoted as Cloudflare's and never assessed.
Cloudflare's view comes from its own traffic, so it is a line of evidence independent of IODA. The API needs a token
(env CLOUDFLARE_RADAR_TOKEN: a free Cloudflare API token with Radar read permission; repo secret of the same name).
Data licence CC BY-NC 4.0 ("Data available via Radar API endpoints is made available under the CC BY-NC 4.0
license", developers.cloudflare.com/radar, read 13 Nov 2026). Shapes checked against real responses
(tests/fixtures/radar/MANIFEST.json, 13 Nov 2026):

  annotations/outages  result.annotations[]: id, description, startDate, endDate (null while ongoing), locations
                       (ISO alpha-2), asns, entities (GEOLOCATION names a region), outage.outageCause and
                       outage.outageType, linkedUrl (Cloudflare's own post about it).
  traffic_anomalies    result.trafficAnomalies[]: uuid, type (AS or LOCATION), status (UNVERIFIED, TP, VERIFIED),
                       startDate, endDate, asnDetails {asn, name, location {code}}, locationDetails {code, name},
                       visibleInDataSources, outageId (set when an annotation covers it). Only TP and VERIFIED are
                       kept (status=VERIFIED returns both), and not those an annotation already covers.

Published while ongoing or for RECENT_HOURS after they end. An endpoint that can't be read publishes nothing and is
listed in problems; nothing is carried over or filled in.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone

API = "https://api.cloudflare.com/client/v4/radar"
OUTAGES_URL = f"{API}/annotations/outages?dateRange=7d&format=json&limit=100"
ANOMALIES_URL = f"{API}/traffic_anomalies?dateRange=7d&format=json&limit=100&status=VERIFIED"
OUTAGE_CENTER = "https://radar.cloudflare.com/outage-center"
TOKEN_ENV = "CLOUDFLARE_RADAR_TOKEN"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"
RECENT_HOURS = 72
VERIFIED = {"TP", "VERIFIED"}


def _get(url: str, timeout: float = 30) -> dict:
    """GET a Radar endpoint with the token from the environment. Errors never include the token."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} is not set")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(20_000_000))


def _t(s) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _recent(start: datetime | None, end: datetime | None, now: datetime) -> bool:
    """Ongoing (no end) or ended within RECENT_HOURS; never one with no start or a start in the future."""
    if start is None or start > now:
        return False
    return end is None or (now - end).total_seconds() <= RECENT_HOURS * 3600


def _result(data, key: str) -> list:
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ValueError("Radar did not answer success")
    rows = (data.get("result") or {}).get(key)
    if not isinstance(rows, list):
        raise ValueError(f"no result.{key} list")
    return rows


def parse_outages(data: dict, now: datetime) -> list[dict]:
    """Annotated outages, ongoing or recently ended, as Cloudflare describes them."""
    out = []
    for a in _result(data, "annotations"):
        if not isinstance(a, dict) or a.get("id") is None:
            continue
        start, end = _t(a.get("startDate")), _t(a.get("endDate"))
        if not _recent(start, end, now):
            continue
        o = a.get("outage") or {}
        regions = [e["entityName"] for e in a.get("entities") or []
                   if isinstance(e, dict) and e.get("entityType") == "GEOLOCATION" and e.get("entityName")]
        link = a.get("linkedUrl") if str(a.get("linkedUrl") or "").startswith("https://") else OUTAGE_CENTER
        out.append({
            "radar_id": str(a["id"]),
            "description": str(a.get("description") or "").strip()[:500],
            "countries": [c for c in a.get("locations") or [] if isinstance(c, str) and len(c) == 2],
            "asns": [int(x) for x in a.get("asns") or [] if str(x).isdigit()],
            "regions": regions,
            "cause": o.get("outageCause") or None,
            "type": o.get("outageType") or None,
            "start": _iso(start), "end": _iso(end),
            "link": link,
        })
    out.sort(key=lambda x: x["start"], reverse=True)
    return out


def parse_anomalies(data: dict, now: datetime) -> list[dict]:
    """Verified traffic anomalies, ongoing or recently ended, that no annotation covers."""
    out = []
    for a in _result(data, "trafficAnomalies"):
        if not isinstance(a, dict) or not a.get("uuid") or a.get("status") not in VERIFIED or a.get("outageId"):
            continue
        start, end = _t(a.get("startDate")), _t(a.get("endDate"))
        if not _recent(start, end, now):
            continue
        asn = a.get("asnDetails") or {}
        loc = asn.get("location") or a.get("locationDetails") or {}
        code = loc.get("code")
        num = int(asn["asn"]) if str(asn.get("asn") or "").isdigit() else None
        name = re.sub(rf"\s*\(AS{num}\)$", "", str(asn.get("name") or "")).strip() if num else asn.get("name")
        out.append({
            "uuid": str(a["uuid"]),
            "type": a.get("type"),
            "status": a.get("status"),
            "asn": num,
            "asn_name": name or None,
            "country": code if isinstance(code, str) and len(code) == 2 else None,
            "seen_in": [s for s in a.get("visibleInDataSources") or [] if isinstance(s, str) and s],
            "start": _iso(start), "end": _iso(end),
        })
    out.sort(key=lambda x: x["start"], reverse=True)
    return out


def fetch_radar(fetch=_get, now: datetime | None = None) -> tuple[dict, list[dict]]:
    """({annotations, anomalies, read}, problems). An endpoint that fails is read=False with nothing from it."""
    now = now or datetime.now(timezone.utc)
    out: dict = {"annotations": [], "anomalies": [], "read": {}, "recent_hours": RECENT_HOURS}
    problems = []
    for key, url, parse in (("annotations", OUTAGES_URL, parse_outages),
                            ("anomalies", ANOMALIES_URL, parse_anomalies)):
        try:
            out[key] = parse(fetch(url), now)
            out["read"][key] = True
        except Exception as exc:
            problems.append({"endpoint": key, "error": f"{type(exc).__name__}: {exc}"[:200]})
            out["read"][key] = False
    return out, problems
