"""Submarine cable corridor registry and multi-country fault correlation."""
from __future__ import annotations

CABLE_CORRIDORS = [
    {
        "id": "red-sea-bab-el-mandeb",
        "name": "Red Sea / Bab el-Mandeb Submarine Corridor",
        "cables": ["AAE-1", "Seacom", "EIG", "TGN-Eurasia", "PEACE"],
        "countries": ["YE", "DJ", "ET", "SO", "SD", "SA", "EG"],
        "landing_stations": ["Djibouti City (DJ)", "Aden / Al Hudaydah (YE)", "Port Sudan (SD)", "Zafarana (EG)"],
        "lat": 12.65,
        "lon": 43.42,
    },
    {
        "id": "baltic-finnish-gulf",
        "name": "Baltic Sea / Gulf of Finland Subsea Corridor",
        "cables": ["C-Lion1", "BCS East-West Interlink", "Balticconnector Comms", "EE-FI2"],
        "countries": ["FI", "EE", "SE", "LV", "LT"],
        "landing_stations": ["Hanko / Porkkala (FI)", "Tallinn / Hiiumaa (EE)", "Gotland (SE)"],
        "lat": 59.65,
        "lon": 24.10,
    },
    {
        "id": "taiwan-strait-luzon",
        "name": "Taiwan Strait / Luzon Strait Cable Corridor",
        "cables": ["APCN-2", "SJC", "FNAL", "TSE-1", "Matsu-Taiwan"],
        "countries": ["TW", "PH", "JP"],
        "landing_stations": ["Toucheng / Fangshan (TW)", "Matsu Islands (TW)", "Ballesteros (PH)"],
        "lat": 24.45,
        "lon": 120.15,
    },
    {
        "id": "eastern-med-levant",
        "name": "Eastern Mediterranean / Levant Cable Corridor",
        "cables": ["CADMOS", "BERYTAR", "IMEWE", "MedNautilus"],
        "countries": ["LB", "SY", "CY", "PS"],
        "landing_stations": ["Tripoli / Beirut (LB)", "Tartous (SY)", "Pentaskhinos (CY)"],
        "lat": 34.20,
        "lon": 35.10,
    },
]


def correlate_cable_corridors(outages: list[dict], min_drop_pct: float = 20.0) -> list[dict]:
    """Identify submarine cable corridors where >=2 served countries have concurrent BGP/probing drops."""
    degraded_by_cc = {o["country"]: o for o in outages if o.get("drop_pct", 0.0) >= min_drop_pct}
    corridor_alerts = []
    for corr in CABLE_CORRIDORS:
        affected = [degraded_by_cc[cc] for cc in corr["countries"] if cc in degraded_by_cc]
        if len(affected) >= 2:
            avg_drop = round(sum(a["drop_pct"] for a in affected) / len(affected), 1)
            ccs = [a["country"] for a in affected]
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
                "summary": (
                    f"{corr['name']}: concurrent connectivity drops in {', '.join(ccs)} "
                    f"(mean -{avg_drop}%) across shared cables ({', '.join(corr['cables'][:3])})."
                ),
            })
    return corridor_alerts
