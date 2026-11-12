"""Build Blackout's contract-v2 feed (tic-feed/2, GeoJSON) and site data payload."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tic_doctrine import CONTRACT, confidence, likelihood, tips as doctrine_tips

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
    "title": "Blackout corridor register (hand-compiled; checked against TeleGeography's Submarine Cable Map)",
    "url": "https://www.submarinecablemap.com/",
    "licence": "No TeleGeography data ingested; cable names and landing countries hand-compiled, checked against its public map",
    "grade": "B2",
    "independence_group": "telegeography",
    "source_type": "registry",
    "descriptor": "Hand-compiled list of submarine cable systems, landing countries and shared chokepoint corridors.",
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


OUTAGE_BASELINE = {"id": "ioda-7d-median", "window": "the 7 days before the event, per IODA datasource",
                   "type": "rolling", "version": "1"}
OONI_BASELINE = {"id": "bo-ind-3-threshold", "window": "fixed: anomaly_rate >= 0.15 over >= 50 measurements (BO-IND-3)",
                 "type": "fixed", "version": "1"}


def _cite_tip(t: dict, generated: str) -> dict:
    """A source entry for a tip, carrying only what the producing program said (no filled-in tier or kind)."""
    dt = datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone(timezone.utc)
    return doctrine_tips.cite({
        "program": (t.get("program") or t.get("_program") or "").lower(),
        "item_id": t.get("item_id") or t.get("id"),
        "title": t.get("title"),
        "tier": t.get("tier"),
        "kind": t.get("kind"),
        "status_class": t.get("status_class"),
        "link": t.get("link"),
        "published_at": t.get("published_at"),
        "upstream": t.get("upstream") or [],
    }, accessed_at=dt)


def build_feed(snapshot: dict) -> dict:
    """Build a contract-v2 GeoJSON FeatureCollection from Blackout's normalized snapshot."""
    generated = snapshot["generated_at"]
    valid_until = _plus_hours(generated, 12)
    outages = snapshot.get("outages", [])
    censorship = snapshot.get("censorship", [])
    corridors = snapshot.get("corridor_alerts", [])
    # Seed or replayed data is a pipeline test (CAP status "test": never shown as live); only a live run is actual.
    status = "actual" if snapshot.get("mode") == "live" else "test"
    ooni_by_cc = {c["country"]: c for c in censorship}
    corridor_by_cc = {cc: ca for ca in corridors for cc in ca.get("countries", [])}

    features = []

    # 1. Submarine cable corridor multi-country assessments (BO-WP-2)
    for ca in corridors:
        corr_tips = ca.get("tips") or []
        tip_progs = {t.get("program") for t in corr_tips if t.get("program")}
        ind_lines = (2 if ca.get("affected_count", 0) >= 2 else 1) + len(tip_progs)
        cov = 0.90 if corr_tips else 0.85
        alt_rej = 2 if corr_tips else 1
        conf = _conf(ind_lines, cov, alt_rej)
        prob = 0.85 if corr_tips else 0.75
        ind_id = "BO-IND-2" if ca.get("affected_count", len(ca.get("countries", []))) >= 2 else "BO-IND-5"
        srcs = [
            {**SRC_IODA, "accessed_at": generated},
            {**SRC_CABLES, "accessed_at": generated},
        ]
        for t in corr_tips:
            srcs.append(_cite_tip(t, generated))
        props = {
            "contract": CONTRACT,
            "id": f"blackout:corridor:{ca['corridor_id']}:{generated[:10]}",
            "program": "blackout",
            "title": f"{ca['name']}: concurrent connectivity drop (-{ca['mean_drop_pct']}%) in {', '.join(ca['countries'])}",
            "summary": ca["summary"],
            "kind": "assessment",
            "tier": "warning",
            "status": status,
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
            "indicator_id": ind_id,
            "hypothesis_id": "BO-H-2A",
            "direction": "supports",
            "indicator_state": "present",
            "method_version": METHOD,
            "coverage_fraction": cov,
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
            "sources": srcs,
        }
        geom = {"type": "Point", "coordinates": [ca["lon"], ca["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    # 2. Country connectivity outages (BO-WP-1)
    judged_ccs = set()
    for o in outages:
        cc = o["country"]
        ind_id = o.get("indicator_id")
        if not ind_id:  # 20-25% drops: used for corridor correlation (BO-IND-2), not published on their own
            continue
        ooni = ooni_by_cc.get(cc)
        has_ooni = bool(ooni) and ooni["anomaly_rate"] >= 0.15 and (ooni.get("measurement_count") or 0) >= 50
        corridor = corridor_by_cc.get(cc)
        srcs = [{**SRC_IODA, "accessed_at": generated}]
        # A shutdown call needs IODA and OONI together (BO-IND-1 with BO-IND-3). An IODA drop alone fits a power or
        # physical fault (BO-H-1B) as well as a shutdown, so it is published as a measurement, not a judgment.
        judged = ind_id == "BO-IND-1" and not corridor and has_ooni
        if judged:
            srcs.append({**SRC_OONI, "accessed_at": generated})
            judged_ccs.add(cc)
        direction = "supports"
        summary = o["summary"]
        if o.get("baseline_zscore") is not None:
            summary += f" z-score {o['baseline_zscore']} against the same 7 days."
        if ind_id == "BO-IND-4":
            # One signal dropped while BGP held: recorded as a measurement that supports the artefact hypothesis.
            hyp_id, cov = "BO-H-1C", 0.45
            summary += " BGP held steady, so this reads as a single-source dip (BO-IND-4), not a confirmed outage."
        elif corridor:
            # Neighbours on the same cables dropped too, or a suite tip sits near the corridor: the corridor item
            # weighs a cable fault (BO-H-2A); this country's drop is recorded as a measurement, not a shutdown call.
            hyp_id, cov = "BO-H-2A", 0.8 if o["corroborated"] else 0.55
            summary += (f" {corridor['name']} is also flagged ({', '.join(corridor['indicator_ids'])}); see that item, "
                        "which weighs a cable fault.")
        else:
            hyp_id = "BO-H-1A"
            cov = 0.8 if o["corroborated"] else 0.55
            if judged:
                conf = _conf(2, cov, 1 if o["corroborated"] else 0)
                prob = 0.80
            else:
                direction = "neutral"
                summary += (" OONI shows no blocking at or above BO-IND-3 in this country, so the drop alone doesn't "
                            "tell a shutdown (BO-H-1A) from a power or physical fault (BO-H-1B).")

        props = {
            "contract": CONTRACT,
            "id": f"blackout:outage:{cc.lower()}:{o['start_time'][:13]}",
            "program": "blackout",
            "title": f"{o['country_name']}: {o['drop_pct']}% connectivity drop ({o['datasource']})",
            "summary": summary,
            "kind": "assessment" if judged else "observation",
            "tier": "warning" if judged else "anomaly",
            "status": status,
            "msg_type": "new",
            "human_review": "sampled" if judged else "none",
            "us_person_data": "none",
            "status_class": "assessed" if judged else "reported",
            "product_category": "warning" if judged else "current",
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
            "warning_problem_id": "BO-WP-2" if (corridor and ind_id == "BO-IND-1") else "BO-WP-1",
            "indicator_id": ind_id,
            "hypothesis_id": hyp_id,
            "direction": direction,
            "indicator_state": "present",
            "method_version": METHOD,
            "coverage_fraction": cov,
            "precision_class": "country",
            "visual": {"type": "map", "url": f"{SITE}/#{cc}"},
            "sources": srcs,
        }
        if judged:
            props.update({
                "probability": prob,
                "likelihood_term": likelihood.term_for(prob)["id"],
                **conf,
                "alternatives": [
                    {"hypothesis": "Power-grid failure or physical damage to telecom exchanges",
                     "tell_apart": "Broad loss across fixed and mobile networks with no OONI application blocking."},
                    {"hypothesis": "Submarine cable or upstream transit fault",
                     "tell_apart": "Concurrent drops in other countries on the same cables (BO-IND-2) or an "
                                   "earthquake or anchor-drag tip near a landing (BO-IND-5)."},
                    {"hypothesis": "Cyber attack on the national carrier",
                     "tell_apart": "Claims or advisories naming the carrier (Vigil); loss limited to that carrier's "
                                   "networks."},
                ],
            })
        else:
            props["baseline_ref"] = OUTAGE_BASELINE
        geom = {"type": "Point", "coordinates": [o["lon"], o["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    # 3. OONI anomaly rates at or above BO-IND-3 not already folded into an outage assessment
    for c in censorship:
        if c["country"] in judged_ccs or c["anomaly_rate"] < 0.15 or c["measurement_count"] < 50:
            continue
        props = {
            "contract": CONTRACT,
            "id": f"blackout:ooni:{c['country'].lower()}:{c['day']}",
            "program": "blackout",
            "title": f"{c['country_name']}: {round(c['anomaly_rate'] * 100, 1)}% OONI censorship anomaly rate",
            "summary": c["summary"],
            "kind": "observation",
            "tier": "anomaly",
            "baseline_ref": OONI_BASELINE,
            "status": status,
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
        "mode": snapshot.get("mode", "seed"),
        "licence": "CC BY 4.0 (IODA, OONI); Blackout code MIT",
        "attribution": "Blackout, The Intelligence Company. Data: Georgia Tech IODA, OONI (CC BY 4.0).",
        "language_key": LANGUAGE_KEY,
        "features": features,
    }
