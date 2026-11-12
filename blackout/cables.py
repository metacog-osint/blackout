"""Submarine cable corridor registry, multi-country fault correlation, and cross-program tip matching."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from tic_doctrine import geo

CABLE_CORRIDORS = [
    {
        "id": "red-sea-bab-el-mandeb",
        "area_id": "BO-AREA-REDSEA",
        "name": "Red Sea / Bab el-Mandeb Submarine Corridor",
        "cables": ["AAE-1", "Seacom", "EIG", "TGN-Eurasia", "PEACE"],
        "countries": ["YE", "DJ", "ET", "SO", "SD", "SA", "EG"],
        "landing_stations": ["Djibouti City (DJ)", "Aden / Al Hudaydah (YE)", "Port Sudan (SD)", "Zafarana (EG)"],
        "landing_coords": [(12.65, 43.42), (11.58, 43.15), (14.80, 42.95), (19.61, 37.22), (29.11, 32.65)],
        "lat": 12.65,
        "lon": 43.42,
    },
    {
        "id": "baltic-finnish-gulf",
        "area_id": "BO-AREA-BALTIC",
        "name": "Baltic Sea / Gulf of Finland Subsea Corridor",
        "cables": ["C-Lion1", "BCS East-West Interlink", "Balticconnector Comms", "EE-FI2"],
        "countries": ["FI", "EE", "SE", "LV", "LT"],
        "landing_stations": ["Hanko / Porkkala (FI)", "Tallinn / Hiiumaa (EE)", "Gotland (SE)"],
        "landing_coords": [(59.65, 24.10), (59.83, 22.96), (59.44, 24.75), (57.50, 18.50)],
        "lat": 59.65,
        "lon": 24.10,
    },
    {
        "id": "taiwan-strait-luzon",
        "area_id": "BO-AREA-TAIWAN",
        "name": "Taiwan Strait / Luzon Strait Cable Corridor",
        "cables": ["APCN-2", "SJC", "FNAL", "TSE-1", "Matsu-Taiwan"],
        "countries": ["TW", "PH", "JP"],
        "landing_stations": ["Toucheng / Fangshan (TW)", "Matsu Islands (TW)", "Ballesteros (PH)"],
        "landing_coords": [(24.45, 120.15), (24.86, 121.82), (26.15, 119.93), (18.41, 121.51)],
        "lat": 24.45,
        "lon": 120.15,
    },
    {
        "id": "eastern-med-levant",
        "area_id": "BO-AREA-EASTMED",
        "name": "Eastern Mediterranean / Levant Cable Corridor",
        "cables": ["CADMOS", "BERYTAR", "IMEWE", "MedNautilus"],
        "countries": ["LB", "SY", "CY", "PS"],
        "landing_stations": ["Tripoli / Beirut (LB)", "Tartous (SY)", "Pentaskhinos (CY)"],
        "landing_coords": [(34.20, 35.10), (33.89, 35.50), (34.89, 35.89), (34.83, 33.41)],
        "lat": 34.20,
        "lon": 35.10,
    },
    {
        "id": "west-africa-wacs-ace",
        "area_id": "BO-AREA-WESTAFRICA",
        "name": "West Africa / WACS-ACE-2Africa Submarine Corridor",
        "cables": ["WACS", "ACE", "2Africa", "SAT-3/WASC", "MainOne"],
        "countries": ["NG", "GH", "CI", "SN", "CM", "AO", "ZA"],
        "landing_stations": ["Lagos (NG)", "Accra (GH)", "Abidjan (CI)", "Dakar (SN)"],
        "landing_coords": [(5.25, -3.95), (6.45, 3.39), (5.55, -0.20), (14.69, -17.44)],
        "lat": 5.25,
        "lon": -3.95,
    },
]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two WGS84 points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return round(2.0 * r * math.asin(min(1.0, math.sqrt(a))), 2)


def tip_points(tip: dict) -> list[tuple[float, float]]:
    """[(lat, lon), ...] for a tip: its lat/lon, or every vertex of its GeoJSON geometry. Empty when it has neither."""
    if tip.get("lat") is not None and tip.get("lon") is not None:
        return [(float(tip["lat"]), float(tip["lon"]))]
    return geo.geometry_points(tip.get("_geometry") or tip.get("geometry"))


def correlate_tips_to_corridor(corr: dict, tips: list[dict] | None, max_distance_km: float = 50.0) -> list[dict]:
    """Elements or Wake tips with a location within max_distance_km of one of the corridor's points (BO-IND-5).

    The distance is measured from the tip's own coordinates; a tip without coordinates cannot meet BO-IND-5 and is
    not matched. The corridor's points are its landing stations and chokepoint, so a fault far out along a cable can
    fall outside the radius."""
    if not tips:
        return []
    ref_points = list(corr.get("landing_coords") or [])
    if (corr["lat"], corr["lon"]) not in ref_points:
        ref_points.insert(0, (corr["lat"], corr["lon"]))
    matched = []
    for t in tips:
        if t.get("stale"):
            continue
        prog = (t.get("program") or t.get("_program") or "").lower()
        if prog not in ("elements", "wake"):
            continue
        pts = tip_points(t)
        if not pts:
            continue
        dist_km = min(haversine_km(p[0], p[1], rp[0], rp[1]) for p in pts for rp in ref_points)
        if dist_km > max_distance_km:
            continue
        enriched = {k: v for k, v in t.items() if k not in ("_geometry", "geometry")}
        enriched["program"] = prog
        enriched["distance_km"] = round(dist_km, 1)
        enriched["indicator_id"] = "BO-IND-5"
        matched.append(enriched)
    return matched


def related_tips(outage: dict, tips: list[dict] | None) -> list[dict]:
    """Suite tips naming the outage's country, kept as context for the reader.

    They do not raise the probability of the outage item's hypothesis: an earthquake or anchor drag points to a
    cable fault (BO-H-2A, carried by the corridor item), and a cyber attack on a carrier is another explanation
    again; neither is evidence for a state-ordered shutdown (BO-H-1A)."""
    if not tips:
        return []
    cc = outage.get("country")
    out = []
    for t in tips:
        if t.get("stale") or not cc or cc not in (t.get("countries") or []):
            continue
        enriched = {k: v for k, v in t.items() if k not in ("_geometry", "geometry")}
        enriched["program"] = (t.get("program") or t.get("_program") or "").lower()
        enriched["relation"] = "context"
        out.append(enriched)
    return out


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _concurrent(outages: list[dict], window_hours: float = 2.0) -> list[dict]:
    """The largest group of outages whose start times fall within window_hours of one another (BO-IND-2)."""
    best: list[dict] = []
    for anchor in outages:
        t0 = _parse(anchor["start_time"])
        group = [o for o in outages if timedelta(0) <= _parse(o["start_time"]) - t0 <= timedelta(hours=window_hours)]
        if len(group) > len(best):
            best = group
    return best


def correlate_cable_corridors(
    outages: list[dict],
    min_drop_pct: float = 20.0,
    tips: list[dict] | None = None,
    max_tip_distance_km: float = 50.0,
) -> list[dict]:
    """Corridors where >=2 served countries drop >=min_drop_pct within 2 hours of each other (BO-IND-2), or where
    one degraded country co-occurs with an Elements or Wake tip within max_tip_distance_km (BO-IND-5).

    Single-source artefacts (BO-IND-4) are left out: a darknet-only or probing-only dip with steady BGP is not
    evidence of a cut."""
    degraded = [o for o in outages if o.get("drop_pct", 0.0) >= min_drop_pct and o.get("indicator_id") != "BO-IND-4"]
    corridor_alerts = []
    for corr in CABLE_CORRIDORS:
        served = [o for o in degraded if o["country"] in corr["countries"]]
        if not served:
            continue
        concurrent = _concurrent(served)
        matched_tips = correlate_tips_to_corridor(corr, tips, max_distance_km=max_tip_distance_km)
        if len(concurrent) >= 2:
            affected = concurrent
        elif matched_tips:
            affected = [max(served, key=lambda o: o["drop_pct"])]
        else:
            continue
        avg_drop = round(sum(a["drop_pct"] for a in affected) / len(affected), 1)
        ccs = [a["country"] for a in affected]
        ind_ids = (["BO-IND-2"] if len(affected) >= 2 else []) + (["BO-IND-5"] if matched_tips else [])
        summary = (
            f"{corr['name']}: connectivity drops in {', '.join(ccs)} (mean -{avg_drop}%) "
            f"in countries served by shared cables ({', '.join(corr['cables'][:3])})"
        )
        if matched_tips:
            tip_desc = ", ".join(f"{t['program']}: {t.get('title') or t.get('item_id')} ({t['distance_km']} km)"
                                 for t in matched_tips)
            summary += f"; suite tips within {max_tip_distance_km:g} km of the corridor: {tip_desc}"
        corridor_alerts.append({
            "id": f"cable-{corr['id']}",
            "corridor_id": corr["id"],
            "name": corr["name"],
            "cables": corr["cables"],
            "landing_stations": corr["landing_stations"],
            "countries": ccs,
            "affected_count": len(affected),
            "mean_drop_pct": avg_drop,
            "lat": corr["lat"],
            "lon": corr["lon"],
            "start_time": min(a["start_time"] for a in affected),
            "indicator_ids": ind_ids,
            "tips": matched_tips,
            "summary": summary + ".",
        })
    return corridor_alerts
