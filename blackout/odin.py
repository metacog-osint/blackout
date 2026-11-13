"""US grid (BO-WP-5): meters without power by county, from ODIN, the Outage Data Initiative Nationwide.

ODIN (odin.ornl.gov) is the standard US utilities use to share their own outage data, run by Oak Ridge National
Laboratory for the Department of Energy. Its anonymous read endpoints need no key (its OpenAPI, /v3/api-docs, gives
the /odi paths no security; the /odin paths need a subscriber token). Shapes checked against real responses
(tests/fixtures/odin/MANIFEST.json, 14:40 UTC 13 Nov 2026):

  /odi?view=county   {"outage": [...]}: one row per county with an outage now. communityDescriptor = county FIPS,
                     metersAffected = meters out in the county summed over the reporting utilities, names = pairs of
                     UtilityName and UtilityId (the EIA utility id) for each utility out there. Every other field
                     was null. 214 counties, 39,663 meters.
  /odi/status        one row per reporting utility: eiaId, name, dataResolution (COUNTY, POINT, ZIP, POLYGON, H3),
                     receivedDate (its last report), hasError, totalOutages (incidents, not meters), vendor. 159
                     utilities (133 county-level), 10 with an error, 2 last heard from more than 6 h before.

Coverage is only the utilities that report to ODIN: over 155 utilities and 16 vendors in 47 states, over 47 million
customers (ODIN's webinar, Feb 2026). A county with no ODIN row may still be dark, because its utility may not report
to ODIN. A few utilities that report by polygon or ZIP don't appear in the county view (4 of the 72 with outages on
13 Nov 2026); they are counted from /odi/status as utilities with outages, not placed.

Why not EAGLE-I: DOE's EAGLE-I covers far more (about 146 million customers, 92%, in 2022, read from utilities' public
outage maps every 15 minutes; Brelsford et al., Scientific Data 11:271, 2024), but its live platform serves
government emergency responders, and FEMA's public layer of it (gis.fema.gov, PowerOutages_EAGLE_I) answered 499
"Token Required" on 13 Nov 2026. ODIN's data also feeds EAGLE-I (ODIN webinar, Feb 2026).

Rule (BO-IND-14): a state where ODIN's reporting utilities show more than 50,000 meters out. The line is DOE's: form
DOE-417 makes "Loss of electric service to more than 50,000 customers for 1 hour or more" reportable (criterion 12,
doe417.pnnl.gov/files/DOE-417_Form.pdf, read 13 Nov 2026). ODIN's figure is one reading rather than an hour, summed
over a state rather than one utility's event, and a meter is close to but not the same as a customer; the item says
so. Every county with an outage is listed on the page and the map at any size.

Licence: ODIN states none for its public data (API docs, site and Feb 2026 webinar, read 13 Nov 2026); none is
claimed, and ODIN and the reporting utilities are credited. County points: the Census Bureau's 2025 Gazetteer internal
points (public domain), in us_counties.json.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

COUNTY_URL = "https://odin.ornl.gov/odi?view=county"
STATUS_URL = "https://odin.ornl.gov/odi/status"
PAGE = "https://odin.ornl.gov"
LICENCE = ("No licence stated by ODIN (Oak Ridge National Laboratory for the US Department of Energy) for its public "
           "outage data; credited to ODIN and the reporting utilities")
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"
STATE_THRESHOLD = 50_000   # DOE-417 criterion 12: more than 50,000 customers out for 1 hour or more
STALE_HOURS = 6
COUNTIES_FILE = Path(__file__).with_name("us_counties.json")
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico",
}


def _get(url: str, timeout: float = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(30_000_000).decode("utf-8"))


@lru_cache(maxsize=1)
def places() -> dict[str, list]:
    """County FIPS -> [state, name, lat, lon] (Census 2025 Gazetteer)."""
    return json.loads(COUNTIES_FILE.read_text(encoding="utf-8"))["counties"]


def _utilities(names: list | None) -> list[dict]:
    """Pair ODIN's flat names list (UtilityName, UtilityId, UtilityName, UtilityId, ...) into utilities."""
    out, current = [], None
    for n in names or []:
        if n.get("nameType") == "UtilityName":
            current = {"name": n.get("name"), "eia_id": None}
            out.append(current)
        elif n.get("nameType") == "UtilityId" and current is not None and current["eia_id"] is None:
            current["eia_id"] = n.get("name")
    return out


def parse_counties(doc: dict, where: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(counties with meters out, largest first; rows whose FIPS isn't in the Gazetteer)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("outage"), list):
        raise ValueError("ODIN county view has no outage list")
    where = places() if where is None else where
    counties, unplaced = [], []
    for row in doc["outage"]:
        fips = (row.get("communityDescriptor") or "").strip()
        meters = int(row.get("metersAffected") or 0)
        if meters <= 0:
            continue
        rec = {"fips": fips, "meters": meters, "utilities": _utilities(row.get("names"))}
        p = where.get(fips)
        if not p:
            unplaced.append(rec)
            continue
        counties.append({**rec, "state": p[0], "name": p[1], "lat": p[2], "lon": p[3]})
    counties.sort(key=lambda c: (-c["meters"], c["fips"]))
    unplaced.sort(key=lambda c: (-c["meters"], c["fips"]))
    return counties, unplaced


def by_state(counties: list[dict]) -> list[dict]:
    """Per-state totals, largest first, each with a point weighted by meters out (where the outage is)."""
    per: dict[str, dict] = {}
    for c in counties:
        s = per.setdefault(c["state"], {"state": c["state"], "name": STATE_NAMES.get(c["state"], c["state"]),
                                        "meters": 0, "counties": 0, "utilities": set(), "_lat": 0.0, "_lon": 0.0,
                                        "top": []})
        s["meters"] += c["meters"]
        s["counties"] += 1
        s["utilities"] |= {u["name"] for u in c["utilities"] if u.get("name")}
        s["_lat"] += c["lat"] * c["meters"]
        s["_lon"] += c["lon"] * c["meters"]
        if len(s["top"]) < 3:  # counties arrive largest first
            s["top"].append({"fips": c["fips"], "name": c["name"], "meters": c["meters"]})
    out = []
    for s in per.values():
        lat, lon = s.pop("_lat") / s["meters"], s.pop("_lon") / s["meters"]
        out.append({**s, "utilities": sorted(s["utilities"]), "lat": round(lat, 3), "lon": round(lon, 3)})
    return sorted(out, key=lambda s: (-s["meters"], s["state"]))


def parse_status(rows: list, now: datetime, in_county_view: set[str]) -> dict:
    """What ODIN's coverage looked like at this reading."""
    if not isinstance(rows, list):
        raise ValueError("ODIN status is not a list")
    by_res: dict[str, int] = {}
    stale, errors, outside = 0, 0, []
    for r in rows:
        by_res[r.get("dataResolution") or "unknown"] = by_res.get(r.get("dataResolution") or "unknown", 0) + 1
        errors += bool(r.get("hasError"))
        try:
            got = datetime.fromisoformat((r.get("receivedDate") or "").replace("Z", "+00:00"))
            stale += (now - got).total_seconds() > STALE_HOURS * 3600
        except ValueError:
            stale += 1
        if (r.get("totalOutages") or 0) > 0 and str(r.get("eiaId")) not in in_county_view:
            outside.append({"name": r.get("name"), "resolution": r.get("dataResolution"),
                            "outages": r.get("totalOutages")})
    return {"utilities": len(rows), "by_resolution": dict(sorted(by_res.items(), key=lambda kv: -kv[1])),
            "with_error": errors, "stale": stale, "stale_hours": STALE_HOURS,
            "with_outages": sum(1 for r in rows if (r.get("totalOutages") or 0) > 0),
            "outages_not_in_county_view": outside}


def fetch_odin(fetch=_get, now: datetime | None = None) -> tuple[dict | None, list[dict]]:
    """ODIN's county outages now, per state, with the coverage behind them. None when the county view can't be read."""
    now = now or datetime.now(timezone.utc)
    problems: list[dict] = []
    try:
        counties, unplaced = parse_counties(fetch(COUNTY_URL))
    except Exception as exc:
        return None, [{"endpoint": "odin-county", "error": f"{type(exc).__name__}: {exc}"[:200]}]
    for u in unplaced:
        problems.append({"endpoint": "odin-county", "error": f"county FIPS not in the Gazetteer: {u['fips']} "
                                                             f"({u['meters']} meters)"})
    coverage = None
    try:
        ids = {u["eia_id"] for c in counties + unplaced for u in c["utilities"] if u.get("eia_id")}
        coverage = parse_status(fetch(STATUS_URL), now, ids)
    except Exception as exc:
        problems.append({"endpoint": "odin-status", "error": f"{type(exc).__name__}: {exc}"[:200]})
    states = by_state(counties)
    return {
        "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "threshold": STATE_THRESHOLD,
        "meters": sum(c["meters"] for c in counties) + sum(u["meters"] for u in unplaced),
        "counties": counties,
        "unplaced": unplaced,
        "states": states,
        "notable_states": [s for s in states if s["meters"] > STATE_THRESHOLD],
        "coverage": coverage,
    }, problems
