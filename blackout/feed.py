"""Build Blackout's contract-v2 feed (tic-feed/2, GeoJSON) and site data payload."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from tic_doctrine import CONTRACT, confidence, likelihood  # noqa: E402

SITE = "https://blackout.theintelligence.company"
METHOD = "blackout-1.0"
LANGUAGE_KEY = "https://github.com/metacog-osint/tic-doctrine/blob/main/docs/language-key.md"

SRC_IODA = {
    "id": "ioda-gatech",
    "publisher": "Georgia Tech IODA",
    "title": "Internet Outage Detection and Analysis (BGP & Active Probing)",
    "url": "https://api.ioda.inetintel.cc.gatech.edu/v2/",
    "licence": "CC BY 4.0",
    "grade": "A2",
    "independence_group": "ioda-gatech",
    "source_type": "telemetry",
    "descriptor": "BGP prefix visibility and Trinocular /24 active probing relative to 7-day rolling baseline.",
}

SRC_OONI = {
    "id": "ooni-api",
    "publisher": "OONI (Open Observatory of Network Interference)",
    "title": "OONI Probe Aggregation API",
    "url": "https://api.ooni.io/api/v1/aggregation",
    "licence": "CC BY 4.0",
    "grade": "A2",
    "independence_group": "ooni",
    "source_type": "telemetry",
    "descriptor": "Daily confirmed blocking and anomaly counts across Web Connectivity, Telegram, Signal, and Tor tests.",
}

SRC_CABLES = {
    "id": "telegeography-cables",
    "publisher": "TeleGeography",
    "title": "Submarine Cable Map Landing & Corridor Registry",
    "url": "https://www.submarinecablemap.com/",
    "licence": "CC BY-SA 4.0",
    "grade": "A2",
    "independence_group": "telegeography",
    "source_type": "registry",
    "descriptor": "Public submarine cable systems, coastal landing stations, and shared chokepoint corridors.",
}


def _plus_hours(iso_utc: str, hours: int) -> str:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conf(ind_lines: int, cov: float, alt_rej: int) -> dict:
    return confidence.fields(confidence.Evidence(
        independent_lines=max(1, ind_lines),
        contradicted=False,
        coverage_fraction=cov,
        grades=["A2"],
        alternatives_rejected=alt_rej,
        method_scored=False,
        assumption_status="holds",
        ease_of_faking="low",
        horizon_days=0.0,
    ))


def build_feed(snapshot: dict) -> dict:
    """Build a contract-v2 GeoJSON FeatureCollection from Blackout's normalized snapshot."""
    generated = snapshot["generated_at"]
    valid_until = _plus_hours(generated, 12)
    outages = snapshot.get("outages", [])
    censorship = snapshot.get("censorship", [])
    corridors = snapshot.get("corridor_alerts", [])
    ooni_by_cc = {c["country"]: c for c in censorship}

    features = []

    # 1. Submarine cable corridor multi-country assessments (BO-WP-2)
    for ca in corridors:
        conf = _conf(2, 0.85, 1)
        prob = 0.75
        props = {
            "contract": CONTRACT,
            "id": f"blackout:corridor:{ca['corridor_id']}:{generated[:10]}",
            "program": "blackout",
            "title": f"{ca['name']}: concurrent connectivity drop (-{ca['mean_drop_pct']}%) in {', '.join(ca['countries'])}",
            "summary": ca["summary"],
            "kind": "assessment",
            "tier": "warning",
            "status": "actual",
            "msg_type": "new",
            "human_review": "sampled",
            "us_person_data": "none",
            "status_class": "assessed",
            "product_category": "warning",
            "data_as_of": ca["start_time"],
            "published_at": generated,
            "valid_until": valid_until,
            "change_from_prior": "new",
            "countries": ca["countries"],
            "pmesii_pt": ["infrastructure", "information"],
            "ascope": ["structures", "areas"],
            "ie_aspect": ["physical", "informational"],
            "ncf": ["provide_cable_access_network", "provide_internet_routing", "operate_core_network"],
            "requirement_ids": ["BO-PIR-2", "BO-EEI-2"],
            "warning_problem_id": "BO-WP-2",
            "indicator_id": "BO-IND-2",
            "hypothesis_id": "BO-H-2A",
            "direction": "supports",
            "indicator_state": "present",
            "method_version": METHOD,
            "coverage_fraction": 0.85,
            "probability": prob,
            "likelihood_term": likelihood.term_for(prob)["id"],
            **conf,
            "alternatives": [
                {
                    "hypothesis": "Physical submarine cable fault or landing-station cut along the shared corridor",
                    "tell_apart": "Simultaneous step-function BGP drop across multiple coastal ASNs served by the same cable system.",
                },
                {
                    "hypothesis": "Independent domestic policy shutdowns in neighbouring states",
                    "tell_apart": "Preceding OONI application-layer blocking surge or official curfew decrees.",
                },
            ],
            "precision_class": "point",
            "visual": {"type": "map", "url": f"{SITE}/#{ca['corridor_id']}"},
            "sources": [
                {**SRC_IODA, "accessed_at": generated},
                {**SRC_CABLES, "accessed_at": generated},
            ],
        }
        geom = {"type": "Point", "coordinates": [ca["lon"], ca["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    # 2. Country/ASN connectivity outages (BO-WP-1)
    for o in outages:
        cc = o["country"]
        has_ooni = cc in ooni_by_cc and ooni_by_cc[cc]["anomaly_rate"] >= 0.15
        ind_lines = 2 if (o["corroborated"] or has_ooni) else 1
        cov = 0.8 if o["corroborated"] else 0.55
        conf = _conf(ind_lines, cov, 1 if o["corroborated"] else 0)
        srcs = [{**SRC_IODA, "accessed_at": generated}]
        if has_ooni:
            srcs.append({**SRC_OONI, "accessed_at": generated})

        prob = 0.80 if has_ooni else 0.70
        props = {
            "contract": CONTRACT,
            "id": f"blackout:outage:{cc.lower()}:{o['start_time'][:13]}",
            "program": "blackout",
            "title": f"{o['country_name']}: {o['drop_pct']}% connectivity drop ({o['datasource']})",
            "summary": o["summary"],
            "kind": "assessment",
            "tier": "warning",
            "status": "actual",
            "msg_type": "new",
            "human_review": "sampled",
            "us_person_data": "none",
            "status_class": "assessed",
            "product_category": "warning",
            "data_as_of": o["start_time"],
            "published_at": generated,
            "valid_until": valid_until,
            "change_from_prior": "new",
            "countries": [cc],
            "pmesii_pt": ["infrastructure", "information"],
            "ascope": ["structures", "capabilities"],
            "ie_aspect": ["physical", "informational"],
            "ncf": ["provide_internet_routing", "operate_core_network"],
            "requirement_ids": ["BO-PIR-1", "BO-EEI-1"],
            "warning_problem_id": "BO-WP-1",
            "indicator_id": "BO-IND-1",
            "hypothesis_id": "BO-H-1A",
            "direction": "supports",
            "indicator_state": "present",
            "method_version": METHOD,
            "coverage_fraction": cov,
            "probability": prob,
            "likelihood_term": likelihood.term_for(prob)["id"],
            **conf,
            "alternatives": [
                {
                    "hypothesis": "Deliberate state-ordered connectivity curfew or ASN shutdown",
                    "tell_apart": "Co-occurring OONI application blocking or mobile-carrier-specific BGP withdrawal.",
                },
                {
                    "hypothesis": "Power-grid failure or physical telecom exchange damage",
                    "tell_apart": "Broad regional loss across both fixed and mobile ASNs without prior DPI throttling.",
                },
            ],
            "precision_class": "country",
            "visual": {"type": "map", "url": f"{SITE}/#{cc}"},
            "sources": srcs,
        }
        geom = {"type": "Point", "coordinates": [o["lon"], o["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    # 3. Standalone OONI censorship surges not already covered by a BGP outage
    outage_ccs = {o["country"] for o in outages}
    for c in censorship:
        if c["country"] in outage_ccs or c["anomaly_rate"] < 0.15:
            continue
        props = {
            "contract": CONTRACT,
            "id": f"blackout:ooni:{c['country'].lower()}:{c['day']}",
            "program": "blackout",
            "title": f"{c['country_name']}: {round(c['anomaly_rate'] * 100, 1)}% OONI censorship anomaly rate",
            "summary": c["summary"],
            "kind": "observation",
            "tier": "anomaly",
            "baseline_ref": "ooni-7d-rolling-country-baseline",
            "status": "actual",
            "msg_type": "new",
            "human_review": "none",
            "us_person_data": "none",
            "status_class": "reported",
            "product_category": "current",
            "data_as_of": f"{c['day']}T00:00:00Z",
            "published_at": generated,
            "valid_until": valid_until,
            "countries": [c["country"]],
            "pmesii_pt": ["information", "political"],
            "ascope": ["capabilities"],
            "ie_aspect": ["informational"],
            "ncf": ["provide_internet_content_services", "provide_internet_routing"],
            "requirement_ids": ["BO-PIR-3"],
            "indicator_id": "BO-IND-3",
            "hypothesis_id": "BO-H-1A",
            "direction": "supports",
            "indicator_state": "present",
            "method_version": METHOD,
            "coverage_fraction": 0.65,
            "precision_class": "country",
            "visual": {"type": "map", "url": f"{SITE}/#{c['country']}"},
            "sources": [{**SRC_OONI, "accessed_at": generated}],
        }
        geom = {"type": "Point", "coordinates": [c["lon"], c["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    return {
        "type": "FeatureCollection",
        "contract": CONTRACT,
        "program": "blackout",
        "generated_at": generated,
        "licence": "CC BY 4.0 (IODA, OONI); CC BY-SA 4.0 (TeleGeography); Blackout code MIT",
        "attribution": "Blackout, The Intelligence Company (IODA, OONI, TeleGeography)",
        "language_key": LANGUAGE_KEY,
        "features": features,
    }
