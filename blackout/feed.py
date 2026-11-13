"""Build Blackout's contract-v2 feed (tic-feed/2, GeoJSON) and site data payload."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tic_doctrine import CONTRACT, confidence, likelihood, tips as doctrine_tips, vocab

from . import bgp, blackmarble, eia, iaea, ioda, odin, status_feeds, stop

SITE = "https://blackout.theintelligence.company"
METHOD = "blackout-1.0"
LANGUAGE_KEY = "https://github.com/metacog-osint/tic-doctrine/blob/main/docs/language-key.md"
# The feed licence is Blackout's `terms` in the kit's suite registry, word for word, read from the vendored kit so the
# two can't drift. Kit 0.4.5 (13 Nov 2026) appended each new source's licence as doctrine/sources.json states it.
LICENCE = next(p["terms"] for p in vocab.load("programs")["programs"] if p["id"] == "blackout")
ATTRIBUTION = ("Blackout, The Intelligence Company. Data: OONI (Open Observatory of Network Interference), CC BY-NC-SA "
               "4.0; Georgia Tech IODA, credited as the source (IODA states no data licence); provider status feeds "
               "(AWS, Google Cloud, Google Workspace, Azure, Microsoft 365, Apple, Cloudflare, Akamai, Slack, Zoom, "
               "Starlink Statuspage) as each provider publishes them; Cloudflare Radar (outages, anomalies, BGP "
               "hijacks and route leaks), CC BY-NC 4.0; the IAEA's Ukraine updates, reused with acknowledgement (no "
               "IAEA endorsement); Access Now's STOP dataset, credited (no licence stated); Citizen Lab and Others, "
               "URL Testing Lists Intended for Discovering Website Censorship, CC BY-NC-SA 4.0; ODIN (Oak Ridge National "
               "Laboratory for the US Department of Energy) and the reporting utilities, credited (no licence stated); "
               "US Census Bureau Gazetteer (public domain); NASA Black Marble (VIIRS, NOAA-20), from NASA LANCE and "
               "LAADS DAAC; Source: U.S. Energy Information Administration (EIA-930, public domain).")

SRC_IODA = {
    "id": "ioda-gatech",
    "publisher": "Georgia Tech IODA",
    "title": "Internet Outage Detection and Analysis (BGP & Active Probing)",
    "url": "https://api.ioda.inetintel.cc.gatech.edu/v2/",
    "licence": "No data licence stated by IODA (Georgia Tech); credited as the source, not relicensed",
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
    "licence": "CC BY-NC-SA 4.0 (OONI data licence: https://github.com/ooni/license/blob/master/data/LICENSE.md)",
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

SRC_RADAR = {
    "id": "cloudflare-radar",
    "publisher": "Cloudflare Radar",
    "title": "Cloudflare Radar Outage Center (outages annotated by Cloudflare)",
    "url": "https://radar.cloudflare.com/outage-center",
    "licence": "CC BY-NC 4.0 (Cloudflare Radar data: https://developers.cloudflare.com/radar/)",
    "grade": "A2",
    "independence_group": "cloudflare-radar",
    "source_type": "telemetry",
    "descriptor": "Outages Cloudflare's analysts annotate from Cloudflare's own traffic, with the cause and type "
                  "Cloudflare states.",
}

SRC_RADAR_ANOMALY = {
    **SRC_RADAR,
    "title": "Cloudflare Radar traffic anomalies (verified by Cloudflare)",
    "grade": "B2",
    "descriptor": "Traffic anomalies Cloudflare's detection flagged in its own traffic and Cloudflare marked verified. "
                  "Radar gives no cause for them.",
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


SRC_IODA_ASN = {
    **SRC_IODA,
    "descriptor": "BGP-visible address space and responsive /24s (Trinocular active probing) on a watched provider "
                  "network, against the 7 days before.",
}

OUTAGE_BASELINE = {"id": "ioda-7d-median", "window": "the 7 days before the event, per IODA datasource",
                   "type": "rolling", "version": "1"}
ASN_BASELINE = {"id": "ioda-asn-7d-median", "window": "the 7 days before the window, per IODA datasource (BO-IND-6)",
                "type": "rolling", "version": "1"}
STATUS_BASELINE = {"id": "bo-ind-7-open-incident",
                   "window": "fixed: the provider lists the incident as open on its own status feed (BO-IND-7)",
                   "type": "fixed", "version": "1"}
CONCURRENT_BASELINE = {"id": "bo-ind-8-concurrent-onsets",
                       "window": "fixed: two or more providers begin a drop or incident within 2 h, in the last 24 h "
                                 "(BO-IND-8)",
                       "type": "fixed", "version": "1"}
PROVIDER_NCF = {"starlink": ["provide_satellite_access", "provide_internet_routing"]}
PROVIDER_NCF_DEFAULT = ["provide_internet_content_services", "provide_internet_routing", "operate_core_network"]
# Cloudflare's feed keeps only incidents on its network, CDN, DNS, WARP or data centres (status_feeds.cloudflare_core).
STATUS_NCF = {"starlink": ["provide_satellite_access"],
              "cloudflare": ["provide_internet_routing", "provide_internet_content_services"]}
STATUS_NCF_DEFAULT = ["provide_internet_content_services"]


def _status_source(feed_id: str, link: str | None, generated: str) -> dict:
    f = status_feeds.FEED_BY_ID[feed_id]
    return {
        "id": f["source_id"],
        "publisher": f["publisher"],
        "title": f["title"],
        "url": link if (link or "").startswith("https://") else f["page"],
        "licence": f.get("licence") or "No licence stated; title, times, affected areas and link only",
        "grade": f["grade"],
        "independence_group": f["source_id"],
        "source_type": "status_page",
        "descriptor": f"{f['label']}'s own status feed: what the provider says is open. "
                      + (f.get("note") or "It is not an outside measurement."),
        "accessed_at": generated,
    }


def _provider_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """Provider watch (BO-WP-3): IODA drops on watched networks (BO-IND-6), open incidents the providers report
    (BO-IND-7), and onsets at two or more providers within 2 h (BO-IND-8). All observations; no cause is assessed."""
    feats = []
    incidents = snapshot.get("provider_incidents") or []
    open_by_provider: dict[str, list[dict]] = {}
    for inc in incidents:
        open_by_provider.setdefault(inc["provider"], []).append(inc)
    common = {"contract": CONTRACT, "program": "blackout", "kind": "observation", "tier": "anomaly", "status": status,
              "msg_type": "new", "human_review": "none", "us_person_data": "none", "status_class": "reported",
              "product_category": "current", "published_at": generated, "valid_until": valid_until, "countries": [],
              "pmesii_pt": ["infrastructure", "information"], "ascope": ["structures", "capabilities"],
              "ie_aspect": ["physical", "informational"], "warning_problem_id": "BO-WP-3", "direction": "neutral",
              "indicator_state": "present", "method_version": METHOD, "precision_class": "none"}

    for w in snapshot.get("asn_watch") or []:
        if w.get("indicator_id") != "BO-IND-6":
            continue
        dropped = [lbl for lbl, f in (("BGP", "bgp_drop_pct"), ("active probing", "probing_drop_pct"))
                   if (w.get(f) or 0) >= 5.0]
        summary = (w["summary"] + " IODA alone doesn't tell a network outage at the provider (BO-H-3A) from a "
                   "collector or prober fault (BO-H-3D).")
        reported = open_by_provider.get(w["provider"]) or []
        if reported:
            summary += (f" {reported[0]['provider_label']} lists {len(reported)} open incident(s) on its status feed "
                        "(see those items).")
        both = w.get("bgp_drop_pct") is not None and w.get("probing_drop_pct") is not None
        feats.append({"type": "Feature", "geometry": None, "properties": {
            **common,
            "id": f"blackout:provider-net:{w['asn'].lower()}:{w['window_start'][:13]}",
            "title": f"{w['label']} ({w['asn']}): -{w['drop_pct']}% in IODA {' and '.join(dropped)}",
            "summary": summary,
            "data_as_of": w["window_end"],
            "baseline_ref": ASN_BASELINE,
            "ncf": PROVIDER_NCF.get(w["provider"], PROVIDER_NCF_DEFAULT),
            "requirement_ids": ["BO-PIR-4", "BO-EEI-3"],
            "indicator_id": "BO-IND-6",
            "hypothesis_id": "BO-H-3A",
            "coverage_fraction": 1.0 if both else 0.5,
            "visual": {"type": "list", "url": f"{SITE}/#{w['asn']}"},
            "sources": [{**SRC_IODA_ASN, "accessed_at": generated}],
        }})

    for inc in incidents:
        f = status_feeds.FEED_BY_ID[inc["feed"]]
        where = inc.get("locations") or inc.get("affected") or []
        when = []
        if inc.get("started_at"):
            when.append(f"since {inc['started_at']}")
        if inc.get("updated_at"):
            when.append(f"last update {inc['updated_at']}")
        details = [x for x in (inc.get("status"), inc.get("severity") and f"severity {inc['severity']}",
                               inc.get("service")) if x]
        summary = (f"{f['label']}'s status feed lists this incident as open"
                   + (f" ({', '.join(when)})" if when else "") + ". "
                   + (f"{'; '.join(details)}. " if details else "")
                   + (f"Affected, as the provider lists them: {', '.join(where[:12])}"
                      + (f" and {len(where) - 12} more" if len(where) > 12 else "") + ". " if where else "")
                   + "Reported by the provider; Blackout does not assess its cause.")
        slug = "".join(ch if ch.isalnum() else "-" for ch in str(inc["incident_id"]).lower()).strip("-")[:80]
        loc = f" ({', '.join(inc['locations'][:3])})" if inc.get("locations") else ""
        feats.append({"type": "Feature", "geometry": None, "properties": {
            **common,
            "id": f"blackout:provider-status:{inc['feed']}:{slug}",
            "title": f"{f['label']}: {inc['title']}{loc}",
            "summary": summary,
            "data_as_of": generated,  # when Blackout read the feed and found the incident listed as open
            "baseline_ref": STATUS_BASELINE,
            "ncf": STATUS_NCF.get(inc["provider"], STATUS_NCF_DEFAULT),
            "requirement_ids": ["BO-PIR-4", "BO-EEI-4"],
            "indicator_id": "BO-IND-7",
            "hypothesis_id": "BO-H-3B",
            "coverage_fraction": 1.0,
            "visual": {"type": "list", "url": f"{SITE}/#provider-{inc['feed']}"},
            "sources": [_status_source(inc["feed"], inc.get("link"), generated)],
        }})

    conc = snapshot.get("provider_concurrency")
    if conc:
        srcs, seen = [], set()
        for o in conc["onsets"]:
            key = o.get("feed") or "ioda"
            if key in seen:
                continue
            seen.add(key)
            srcs.append(_status_source(o["feed"], o.get("link"), generated) if o.get("feed")
                        else {**SRC_IODA_ASN, "accessed_at": generated})
        names = ", ".join(conc["provider_labels"])
        feats.append({"type": "Feature", "geometry": None, "properties": {
            **common,
            "id": f"blackout:provider-concurrent:{conc['window_start'][:13]}",
            "title": f"Outages at {len(conc['providers'])} providers began within 2 h: {names}",
            "summary": ("; ".join(f"{o['label']} from {o['onset']}" for o in conc["onsets"])
                        + ". Onsets this close don't by themselves show a shared cause or a cascade (BO-H-3C); "
                          "each provider's own item has the details."
                        + (f" {conc['caution']}" if conc.get("caution") else "")),
            "data_as_of": conc["window_end"],
            "baseline_ref": CONCURRENT_BASELINE,
            "ncf": PROVIDER_NCF_DEFAULT,
            "requirement_ids": ["BO-PIR-4"],
            "indicator_id": "BO-IND-8",
            "hypothesis_id": "BO-H-3C",
            "coverage_fraction": 1.0,
            "visual": {"type": "list", "url": f"{SITE}/#providers"},
            "sources": srcs,
        }})
    return feats


RADAR_OUTAGE_BASELINE = {"id": "radar-outage-center",
                         "window": "fixed: Cloudflare Radar's analysts list the outage in its Outage Center (BO-IND-9)",
                         "type": "fixed", "version": "1"}
RADAR_ANOMALY_BASELINE = {"id": "radar-traffic-anomaly",
                          "window": "Cloudflare Radar's own anomaly detection on its traffic, verified by Cloudflare "
                                    "(BO-IND-10)",
                          "type": "rolling", "version": "1"}
# Cloudflare's stated cause -> (warning problem, hypothesis it bears on, direction). Any other cause Cloudflare names
# (a technical problem, maintenance, weather, a cyber attack ...) is evidence against a shutdown (BO-H-1A, refutes);
# no cause, or UNKNOWN, is neutral.
RADAR_CAUSE = {
    "GOVERNMENT_DIRECTED": ("BO-WP-1", "BO-H-1A", "supports"),
    "POWER_OUTAGE": ("BO-WP-1", "BO-H-1B", "supports"),
    "MILITARY_ACTION": ("BO-WP-1", "BO-H-1B", "supports"),
    "CABLE_CUT": ("BO-WP-2", "BO-H-2A", "supports"),
}


def _human(s: str | None) -> str:
    return str(s).replace("_", " ").lower() if s else ""


def _radar_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """Cloudflare Radar's annotated outages (BO-IND-9) and verified traffic anomalies (BO-IND-10), published as
    observations reported by Cloudflare. Each names an IODA drop in the same country when this run has one."""
    r = snapshot.get("radar") or {}
    ioda_ccs = {o["country"] for o in snapshot.get("outages") or [] if o.get("indicator_id")}
    common = {"contract": CONTRACT, "program": "blackout", "kind": "observation", "tier": "anomaly", "status": status,
              "msg_type": "new", "human_review": "none", "us_person_data": "none", "status_class": "reported",
              "product_category": "current", "published_at": generated, "valid_until": valid_until,
              "pmesii_pt": ["infrastructure", "information"], "ascope": ["structures", "capabilities"],
              "ie_aspect": ["physical", "informational"], "ncf": ["provide_internet_routing", "operate_core_network"],
              "indicator_state": "present", "method_version": METHOD, "coverage_fraction": 1.0,
              "visual": {"type": "list", "url": f"{SITE}/#radar"}}

    def place(cc):
        if not cc:
            return None, None
        name, lat, lon = ioda.resolve_country(cc)
        return name, ({"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None)

    def also_ioda(ccs):
        hit = [cc for cc in ccs if cc in ioda_ccs]
        return f" IODA also shows a drop in {', '.join(hit)} in this run (see that item)." if hit else ""

    feats = []
    for a in r.get("annotations") or []:
        ccs = a.get("countries") or []
        name, geom = place(ccs[0] if ccs else None)
        cause_id = a.get("cause") or "UNKNOWN"
        wp, hyp, direction = RADAR_CAUSE.get(
            cause_id, ("BO-WP-1", "BO-H-1A", "neutral" if cause_id == "UNKNOWN" else "refutes"))
        where = (a.get("regions") or [None])[0] or name or ", ".join(ccs) or "Location not given"
        asns = "".join(f", AS{n}" for n in (a.get("asns") or [])[:3])
        cause = _human(a.get("cause")) or "cause not stated"
        kind = f", {_human(a.get('type'))}" if a.get("type") else ""
        span = f"from {a['start']} to {a['end']}" if a.get("end") else f"since {a['start']}, ongoing"
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            **common,
            "id": f"blackout:radar-outage:{a['radar_id']}",
            "title": f"{where}{asns}: {cause}{kind} (Cloudflare Radar)",
            "summary": (f"Cloudflare Radar's Outage Center lists an outage {span}: \"{a.get('description')}\" "
                        f"Cause as Cloudflare states it: {cause}.{also_ioda(ccs)} Reported by Cloudflare; Blackout "
                        "does not assess the cause."),
            "data_as_of": a.get("end") or generated,
            "countries": ccs,
            "precision_class": "country" if geom else "none",
            "baseline_ref": RADAR_OUTAGE_BASELINE,
            "requirement_ids": (["BO-PIR-2"] if wp == "BO-WP-2" else
                                ["BO-PIR-1", "BO-PIR-7"] if cause_id == "POWER_OUTAGE" else ["BO-PIR-1"]),
            "warning_problem_id": wp,
            "indicator_id": "BO-IND-9",
            "hypothesis_id": hyp,
            "direction": direction,
            "sources": [{**SRC_RADAR, "url": a.get("link") or SRC_RADAR["url"], "accessed_at": generated}],
        }})
    for a in r.get("anomalies") or []:
        cc = a.get("country")
        name, geom = place(cc)
        net = ""
        if a.get("asn"):
            net = f", AS{a['asn']}" + (f" ({a['asn_name']})" if a.get("asn_name") else "")
        span = f"from {a['start']} to {a['end']}" if a.get("end") else f"since {a['start']}, ongoing"
        seen = f", seen in Radar's {', '.join(a['seen_in'])} data" if a.get("seen_in") else ""
        link = (f"https://radar.cloudflare.com/as{a['asn']}" if a.get("asn")
                else f"https://radar.cloudflare.com/{cc.lower()}" if cc else SRC_RADAR_ANOMALY["url"])
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            **common,
            "id": f"blackout:radar-anomaly:{a['uuid']}",
            "title": f"{name or cc or 'Location not given'}{net}: traffic anomaly verified by Cloudflare Radar",
            "summary": (f"Cloudflare Radar's anomaly detection flagged this traffic {span}{seen}; Cloudflare marked it "
                        f"verified ({a.get('status')}). Radar gives no cause, so it doesn't tell a shutdown (BO-H-1A) "
                        f"from a fault.{also_ioda([cc] if cc else [])} Reported by Cloudflare; Blackout does not "
                        "assess the cause."),
            "data_as_of": a.get("end") or generated,
            "countries": [cc] if cc else [],
            "precision_class": "country" if geom else "none",
            "baseline_ref": RADAR_ANOMALY_BASELINE,
            "requirement_ids": ["BO-PIR-1"],
            "warning_problem_id": "BO-WP-1",
            "indicator_id": "BO-IND-10",
            "hypothesis_id": "BO-H-1A",
            "direction": "neutral",
            "sources": [{**SRC_RADAR_ANOMALY, "url": link, "accessed_at": generated}],
        }})
    return feats


SRC_RADAR_BGP = {
    **SRC_RADAR,
    "id": "cloudflare-radar-bgp",
    "title": "Cloudflare Radar BGP hijack and route-leak detection",
    "url": bgp.ROUTING_PAGE,
    "grade": "A3",
    "descriptor": "Cloudflare's automated detection on RouteViews, RIPE RIS and its own route collectors: origin "
                  "hijacks with Cloudflare's confidence score and evidence, and provider-customer-provider leaks. "
                  "It says what the collectors saw, not who meant it.",
}
BGP_HIJACK_BASELINE = {"id": "radar-bgp-hijack", "window": "fixed: Cloudflare confidence >= 8 and seen by >= 25 "
                       "route-collector peers, last seen within 72 h (BO-IND-11)", "type": "fixed", "version": "1"}
BGP_LEAK_BASELINE = {"id": "radar-bgp-leak", "window": "fixed: >= 100 prefixes leaked, seen by >= 25 route-collector "
                     "peers, ongoing or finished within 72 h (BO-IND-12)", "type": "fixed", "version": "1"}


def _asn(n, name) -> str:
    return f"AS{n}" + (f" ({name})" if name else "")


def _point(cc):
    name, lat, lon = ioda.resolve_country(cc) if cc else (None, None, None)
    return name, ({"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None)


def _bgp_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """Routing integrity (BO-WP-4): widely seen high-confidence hijacks (BO-IND-11), one item each, and large leaks
    (BO-IND-12), one item per leaking network. Reported by Cloudflare; intent is never assessed (BO-H-4A neutral)."""
    b = snapshot.get("bgp") or {}
    common = {"contract": CONTRACT, "program": "blackout", "kind": "observation", "tier": "anomaly", "status": status,
              "msg_type": "new", "human_review": "none", "us_person_data": "none", "status_class": "reported",
              "product_category": "current", "published_at": generated, "valid_until": valid_until,
              "pmesii_pt": ["infrastructure", "information"], "ascope": ["capabilities"],
              "ie_aspect": ["physical", "informational"], "ncf": ["provide_internet_routing"],
              "requirement_ids": ["BO-PIR-5", "BO-EEI-5"], "warning_problem_id": "BO-WP-4",
              "hypothesis_id": "BO-H-4A", "direction": "neutral", "indicator_state": "present",
              "method_version": METHOD, "coverage_fraction": 1.0}
    monitors = f" of Radar's {b['monitors']}" if b.get("monitors") else ""
    feats = []
    for h in b.get("notable_hijacks") or []:
        _, geom = _point((h.get("victim_countries") or [None])[0])
        victims = ", ".join(_asn(v, n) for v, n in zip(h["victim_asns"][:3], h["victim_names"][:3]))
        more = f" and {len(h['victim_asns']) - 3} more" if len(h["victim_asns"]) > 3 else ""
        pfx = h.get("prefixes") or []
        plural = "es" if len(pfx) != 1 else ""
        evidence = ", ".join(e.replace("_", " ") for e in h.get("evidence") or []) or "none listed"
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            **common,
            "id": f"blackout:bgp-hijack:{h['radar_id']}",
            "title": (f"BGP hijack: {_asn(h['hijacker_asn'], h.get('hijacker_name'))} originated "
                      f"{len(pfx)} prefix{plural} of {victims}{more} (Cloudflare Radar)"),
            "summary": (f"Cloudflare Radar's hijack detection lists event {h['radar_id']} at confidence "
                        f"{h['confidence']} (Cloudflare's high band is 8 and above), first seen {h['start']}, last "
                        f"seen {h['last_seen']}, by {h.get('peers')}{monitors} route-collector peers. Prefixes: "
                        f"{', '.join(pfx[:6])}{' and more' if len(pfx) > 6 else ''}. Cloudflare's evidence: "
                        f"{evidence}. Routing data alone doesn't tell a deliberate hijack (BO-H-4A) from an operator "
                        "error (BO-H-4B) or a legitimate change the detector misreads (BO-H-4C). Reported by "
                        "Cloudflare; Blackout does not assess intent."),
            "data_as_of": h.get("last_seen") or generated,
            "countries": h.get("victim_countries") or [],
            "precision_class": "country" if geom else "none",
            "baseline_ref": BGP_HIJACK_BASELINE,
            "indicator_id": "BO-IND-11",
            "visual": {"type": "list", "url": f"{SITE}/#routing/hijack-{h['radar_id']}"},
            "sources": [{**SRC_RADAR_BGP, "accessed_at": generated}],
        }})
    groups: dict = {}
    for lk in b.get("notable_leaks") or []:
        groups.setdefault(lk.get("leak_asn"), []).append(lk)
    for asn, leaks in groups.items():
        leaks = sorted(leaks, key=lambda x: x["start"])
        first, last = leaks[0], leaks[-1]
        _, geom = _point(next((x.get("leak_country") for x in leaks if x.get("leak_country")), None))
        still_open = [x for x in leaks if not x["finished"]]
        paths = sorted({" > ".join(_asn(a, n) for a, n in zip(x["segment"], x["segment_names"])) for x in leaks})
        ccs = sorted({c for x in leaks for c in x.get("countries") or []})
        top = max(leaks, key=lambda x: x.get("prefixes") or 0)
        many = len(leaks) > 1
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            **common,
            "id": f"blackout:bgp-leak:as{asn}:{first['start'][:10]}",
            "title": (f"Route leak: {_asn(asn, first.get('leak_name'))} leaked up to {top['prefixes']} prefixes"
                      + (f" in {len(leaks)} events" if many else "") + " (Cloudflare Radar)"),
            "summary": (f"Cloudflare Radar's route-leak detection lists {len(leaks)} provider-customer-provider "
                        f"leak{'s' if many else ''} by this network since {first['start']}"
                        + (f", {len(still_open)} still open" if still_open else ", all finished")
                        + f"; the largest carried {top['prefixes']} prefixes from {top.get('origins')} origin "
                        f"networks and was seen by {top.get('peers')}{monitors} route-collector peers. "
                        f"Path{'s' if len(paths) > 1 else ''} (upstream > leaker > customer): {'; '.join(paths[:3])}. "
                        f"Event ID{'s' if many else ''}: {', '.join(x['radar_id'] for x in leaks[:8])}. A leak can "
                        "send traffic the long way round or drop it; most are export mistakes (BO-H-4B). Reported "
                        "by Cloudflare; Blackout does not assess intent."),
            "data_as_of": last.get("last_seen") or generated,
            "countries": ccs,
            "precision_class": "country" if geom else "none",
            "baseline_ref": BGP_LEAK_BASELINE,
            "indicator_id": "BO-IND-12",
            "visual": {"type": "list", "url": f"{SITE}/#routing/leak-as{asn}"},
            "sources": [{**SRC_RADAR_BGP, "accessed_at": generated}],
        }})
    return feats


SRC_IAEA = {
    "id": "iaea-ukraine-updates",
    "publisher": "International Atomic Energy Agency (IAEA)",
    "title": "Update N – IAEA Director General Statement on Situation in Ukraine",
    "url": iaea.RSS_URL,
    "licence": iaea.TERMS,
    "grade": "A2",
    "independence_group": "iaea",
    "source_type": "official_statement",
    "descriptor": "The IAEA's numbered statements on Ukraine, from its team at the plant. Numbers from the IAEA's Top "
                  "News RSS; text from the Wayback Machine's capture, because iaea.org refuses scripts.",
}
ZNPP_BASELINE = {"id": "znpp-offsite-redundancy", "window": "fixed: two or more off-site power lines (redundancy); the "
                 "ZNPP had ten before the war, by the IAEA's count (BO-IND-13)", "type": "fixed", "version": "1"}
ZNPP_STATE = {"none": "without off-site power", "single": "on a single off-site power line",
              "not_stated": "off-site power not stated"}


def _day(iso_date: str | None) -> str:
    if not iso_date:
        return "date not read"
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{d.day} {d:%b %Y}"


def _znpp_feature(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """Zaporizhzhya NPP off-site power (BO-IND-13), as the IAEA's newest numbered update states it, quoted."""
    z = snapshot.get("znpp")
    if not z:
        return []
    stated = z.get("stated_in") or (z if z["state"] != "not_stated" else None)
    state = stated["state"] if stated else "not_stated"
    present = state in ("none", "single")
    parts = []
    if stated and stated is not z:
        parts.append(f"Update {z['number']} ({_day(z.get('date'))}) says nothing current about the plant's off-site "
                     f"power; Update {stated['number']} ({_day(stated.get('date'))}) is the newest that does.")
    if stated:
        parts.append(f"The IAEA, Update {stated['number']}: " + " ".join(f"\u201c{q}\u201d" for q in stated["quotes"][:2]))
    else:
        parts.append(f"None of the updates read, the newest being Update {z['number']} ({_day(z.get('date'))}), "
                     "states the plant's current off-site power, so the state is not known here; that is not read as "
                     "calm.")
    if z.get("losses"):
        parts.append("Loss of power it reports: " + " ".join(f"\u201c{q}\u201d" for q in z["losses"][:1]))
    if z.get("loop_count"):
        parts.append(f"The IAEA counts {z['loop_count']} complete losses of off-site power since the war began.")
    if z.get("newer_unread"):
        parts.append(f"Update {z['newest_number']} is listed by the IAEA but its text isn't archived yet, so it isn't "
                     "read here.")
    if z.get("overdue"):
        parts.append(f"No newer numbered update was found, and this one is {z['age_days']} days old; the IAEA has "
                     "issued them about weekly, so one may be missing.")
    parts.append("Reported by the IAEA; Blackout quotes it and does not assess it.")
    which = stated or z
    title = (f"Zaporizhzhya NPP: {ZNPP_STATE[state]} (IAEA Update {which['number']}, {_day(which.get('date'))})")
    props = {
        "contract": CONTRACT, "program": "blackout",
        "id": f"blackout:znpp-power:update-{z['number']}",
        "title": title,
        "summary": " ".join(parts),
        "kind": "observation", "tier": "anomaly" if present else "routine", "status": status, "msg_type": "new",
        "human_review": "none", "us_person_data": "none", "status_class": "reported", "product_category": "current",
        "data_as_of": f"{which['date']}T00:00:00Z" if which.get("date") else generated,
        "published_at": generated, "valid_until": valid_until, "countries": ["UA"],
        "pmesii_pt": ["infrastructure", "military"], "ascope": ["structures"], "ie_aspect": ["physical"],
        "ncf": ["transmit_electricity", "manage_hazardous_materials"],
        "requirement_ids": ["BO-PIR-6"], "warning_problem_id": "BO-WP-5", "indicator_id": "BO-IND-13",
        "hypothesis_id": "BO-H-5A", "direction": "neutral",
        "indicator_state": "present" if present else "no_data",
        "method_version": METHOD, "coverage_fraction": 1.0 if stated else 0.0, "precision_class": "point",
        "baseline_ref": ZNPP_BASELINE,
        "visual": {"type": "map", "url": f"{SITE}/#power/znpp"},
        "sources": [{**SRC_IAEA, "title": f"Update {which['number']} – IAEA Director General Statement on Situation in "
                     "Ukraine", "url": which.get("link") or SRC_IAEA["url"], "accessed_at": generated}],
    }
    geom = {"type": "Point", "coordinates": [iaea.ZNPP["lon"], iaea.ZNPP["lat"]]}
    return [{"type": "Feature", "geometry": geom, "properties": props}]


SRC_STOP = {
    "id": "accessnow-stop",
    "publisher": "Access Now (#KeepItOn)",
    "title": "STOP: Shutdown Tracker Optimization Project dataset",
    "url": stop.PAGE,
    "licence": stop.LICENCE,
    "grade": "B2",
    "independence_group": "accessnow",
    "source_type": "dataset",
    "descriptor": "Shutdowns Access Now documented in the country in the last full year, as history. Context only: it "
                  "does not bear on the cause of this outage.",
}


SRC_ODIN = {
    "id": "odin-ornl",
    "publisher": "ODIN, Outage Data Initiative Nationwide (Oak Ridge National Laboratory for the US Department of "
                 "Energy), from the reporting utilities",
    "title": "ODIN county outage view",
    "url": odin.PAGE,
    "licence": odin.LICENCE,
    "grade": "B2",
    "independence_group": "odin",
    "source_type": "dataset",
    "descriptor": "Meters without power by county, as the utilities that report to ODIN state them (over 155 "
                  "utilities, about 47 million customers, Feb 2026). Utilities that don't report to ODIN aren't "
                  "counted.",
}
ODIN_BASELINE = {"id": "doe-417-50k", "window": "fixed: more than 50,000 customers out for 1 hour or more (form "
                 "DOE-417, criterion 12), read here as meters out at ODIN's reporting utilities in a state, in one "
                 "reading (BO-IND-14)", "type": "fixed", "version": "1"}


def _odin_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """US states where ODIN's reporting utilities show more than 50,000 meters out (BO-IND-14), reported."""
    o = snapshot.get("odin")
    if not o:
        return []
    out = []
    at = o.get("read_at") or generated
    for s in o.get("notable_states") or []:
        top = ", ".join(f"{c['name']} ({c['meters']:,})" for c in s["top"])
        utilities = s["utilities"]
        named = ", ".join(utilities[:3]) + (f" and {len(utilities) - 3} more" if len(utilities) > 3 else "")
        summary = (f"ODIN's reporting utilities showed {s['meters']:,} meters without power in {s['name']} at "
                   f"{at[11:16]} UTC, in {s['counties']} count{'ies' if s['counties'] != 1 else 'y'}; most in "
                   f"{top}. Utilities: {named}. More than 50,000 is the size of loss DOE's form DOE-417 makes "
                   "reportable (more than 50,000 customers for an hour or more); this is one reading, summed over "
                   "the state, and a meter is close to but not the same as a customer. Only utilities that report "
                   "to ODIN are counted, so the real number may be higher. Reported by the utilities through ODIN; "
                   "Blackout does not assess the cause.")
        props = {
            "contract": CONTRACT, "program": "blackout",
            "id": f"blackout:us-power:{s['state'].lower()}:{at[:10]}",
            "title": f"{s['name']}: {s['meters']:,} meters without power (ODIN's reporting utilities)",
            "summary": summary,
            "kind": "observation", "tier": "anomaly", "status": status, "msg_type": "new",
            "human_review": "none", "us_person_data": "organizations", "status_class": "reported",
            "product_category": "current", "data_as_of": at, "published_at": generated, "valid_until": valid_until,
            "countries": ["US"], "pmesii_pt": ["infrastructure"], "ascope": ["structures"],
            "ie_aspect": ["physical"], "ncf": ["distribute_electricity"],
            "requirement_ids": ["BO-PIR-8"], "warning_problem_id": "BO-WP-5", "indicator_id": "BO-IND-14",
            "hypothesis_id": "BO-H-5B", "direction": "neutral", "indicator_state": "present",
            "method_version": METHOD, "coverage_fraction": 1.0, "precision_class": "admin1",
            "baseline_ref": ODIN_BASELINE,
            "visual": {"type": "map", "url": f"{SITE}/#power/us-{s['state'].lower()}"},
            "sources": [{**SRC_ODIN, "accessed_at": generated}],
        }
        out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                     "properties": props})
    return out


SRC_BLACK_MARBLE = {
    "id": "nasa-black-marble",
    "publisher": "NASA (Black Marble, VIIRS on NOAA-20; near real time from NASA LANCE, standard from LAADS DAAC)",
    "title": "VJ146A2 Gap-Filled Lunar BRDF-Adjusted Nighttime Lights Daily",
    "url": blackmarble.PAGE,
    "licence": blackmarble.LICENCE,
    "grade": "A2",
    "independence_group": "nasa",
    "source_type": "dataset",
    "descriptor": "Night radiance with moonlight and surface effects removed, about 500 m pixels, one pass a night. "
                  "Blackout averages the clear pixels in a box about 44 km across each watched city.",
}
NIGHT_BASELINE = {"id": "black-marble-14-night-median", "window": "median of the counted nights (at least 60% of the "
                  "box clear) in the 14 nights before, at least 5 of them (BO-IND-15)", "type": "rolling",
                  "version": "1"}


def _night_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """Watched cities much darker than their own recent nights (BO-IND-15): Blackout's measurement from NASA data."""
    nl = snapshot.get("nightlights")
    if not nl:
        return []
    out = []
    for c in nl.get("cities") or []:
        a = c.get("assessment") or {}
        if a.get("state") != "darker":
            continue
        last = a["latest"]
        pct = round(-a["change"] * 100)
        product = "near real time" if last.get("product") == blackmarble.NRT else "standard"
        summary = (f"NASA's Black Marble ({product}, NOAA-20) gives {last['mean']:.2f} nW/cm\u00b2/sr over a box about "
                   f"44 km across {c['name']} on the night of {_day(last['date'])}, {pct}% below the median of the "
                   f"{a['baseline_nights']} clear nights in the 14 before it ({a['baseline_median']:.2f}). "
                   f"{round(last['clear'] * 100)}% of the box was clear. A drop in light fits a grid failure, strikes "
                   "on the grid, rationing or load shedding, and also smoke, dust or snow the cloud mask missed; "
                   "Blackout measures how much darker it was, not why. NASA advises against using near-real-time data "
                   "for tactical decisions or local-scale conditions.")
        props = {
            "contract": CONTRACT, "program": "blackout",
            "id": f"blackout:night-lights:{c['id']}:{last['date']}",
            "title": f"{c['name']}: night lights {pct}% below its recent nights (NASA Black Marble, "
                     f"{_day(last['date'])})",
            "summary": summary,
            "kind": "observation", "tier": "anomaly", "status": status, "msg_type": "new",
            "human_review": "none", "us_person_data": "none", "status_class": "reported",
            "product_category": "current", "data_as_of": f"{last['date']}T00:00:00Z", "published_at": generated,
            "valid_until": valid_until, "countries": [c["country"]], "pmesii_pt": ["infrastructure"],
            "ascope": ["structures"], "ie_aspect": ["physical"], "ncf": ["distribute_electricity"],
            "requirement_ids": ["BO-PIR-9"], "warning_problem_id": "BO-WP-5", "indicator_id": "BO-IND-15",
            "hypothesis_id": "BO-H-5A", "direction": "neutral", "indicator_state": "present",
            "method_version": METHOD, "coverage_fraction": last["clear"], "precision_class": "area",
            "baseline_ref": NIGHT_BASELINE,
            "visual": {"type": "map", "url": f"{SITE}/#power/night-{c['id']}"},
            "sources": [{**SRC_BLACK_MARBLE, "url": f"https://doi.org/{blackmarble.DOI[last['product']]}",
                         "accessed_at": generated}],
        }
        out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
                    "properties": props})
    return out


SRC_EIA = {
    "id": "eia-930",
    "publisher": "U.S. Energy Information Administration (EIA), from each balancing authority's own EIA-930 report",
    "title": "EIA-930 Hourly and Daily Balancing Authority Operations Report: demand and day-ahead demand forecast",
    "url": eia.PAGE,
    "licence": eia.LICENCE,
    "grade": "A2",
    "independence_group": "eia",
    "source_type": "dataset",
    "descriptor": "Hourly demand each US balancing authority reports to EIA, and the day-ahead forecast it reported "
                  "the day before. Preliminary: balancing authorities correct them within days.",
}
EIA_BASELINE = {"id": "eia930-own-ratio", "window": "the balancing authority's own ratio of demand to day-ahead "
                "forecast in the 168 hours before the 24-hour window: its median, with the line 25% under it or 1.5 "
                "times as far under it as the 1st percentile, whichever is further (BO-IND-16)", "type": "rolling",
                "version": "1"}


def _hour(iso: str) -> str:
    """'2024-10-10T09:00:00Z' -> '09:00 UTC 10 Oct 2024'."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"{t:%H:%M} UTC {t.day} {t:%b %Y}"


def _eia_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """US balancing authorities whose demand ran far under what their own forecast implies (BO-IND-16), reported."""
    e = snapshot.get("eia")
    if not e:
        return []
    out = []
    read = _day((e.get("read_at") or generated)[:10])
    for b in e.get("bas") or []:
        if b.get("state") != "low":
            continue
        r, w = b["run"], b["run"]["worst"]
        pct = round(-w["change"] * 100)
        where = f", which balances {b['area']}," if b.get("area") else ""
        summary = (f"{b['name']} ({b['id']}){where} reported {w['demand']:,} MW of demand to EIA for the hour to "
                   f"{_hour(w['hour'])}, against its day-ahead forecast of {w['forecast']:,} MW. Its demand usually "
                   f"runs at {round(b['median_ratio'] * 100)}% of its forecast (the median of the {b['range_hours']} "
                   f"hours before), so about {w['expected']:,} MW was expected: {pct}% below. Demand stayed "
                   f"{round(b['line'] * 100)}% or more below that for {r['hours']} hours in a row, {_hour(r['start'])} "
                   f"to {_hour(r['end'])}" + (", through the newest hour read" if r.get("ongoing") else "")
                   + f", about {r['shortfall_mwh']:,} MWh less than expected. A gap like this fits customers losing "
                   "power (a storm, strikes on the grid, a failure) and also a forecast miss, batteries charging or a "
                   "reporting error; EIA-930 figures are preliminary and balancing authorities correct them within "
                   "days. Reported by the balancing authority to EIA; Blackout measures the gap and does not assess "
                   f"the cause. Source: U.S. Energy Information Administration ({read}).")
        geom = {"type": "Point", "coordinates": [b["lon"], b["lat"]]} if b.get("lat") is not None else None
        props = {
            "contract": CONTRACT, "program": "blackout",
            "id": f"blackout:us-demand:{b['id'].lower()}:{r['start'][:10]}",
            "title": f"{b['name']}: demand {pct}% below what its forecast implies, {r['hours']} h (EIA-930)",
            "summary": summary,
            "kind": "observation", "tier": "anomaly", "status": status, "msg_type": "new",
            "human_review": "none", "us_person_data": "organizations", "status_class": "reported",
            "product_category": "current", "data_as_of": r["end"], "published_at": generated,
            "valid_until": valid_until, "countries": ["US"], "pmesii_pt": ["infrastructure"],
            "ascope": ["structures"], "ie_aspect": ["physical"], "ncf": ["distribute_electricity"],
            "requirement_ids": ["BO-PIR-10"], "warning_problem_id": "BO-WP-5", "indicator_id": "BO-IND-16",
            "hypothesis_id": "BO-H-5B", "direction": "neutral", "indicator_state": "present",
            "method_version": METHOD, "coverage_fraction": round(b.get("window_hours", 24) / 24, 2),
            "precision_class": "admin1" if geom else "none",
            "baseline_ref": EIA_BASELINE,
            "visual": {"type": "map", "url": f"{SITE}/#power/eia-{b['id'].lower()}"},
            "sources": [{**SRC_EIA, "accessed_at": generated}],
        }
        out.append({"type": "Feature", "geometry": geom, "properties": props})
    return out


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

        history = stop.context(snapshot.get("stop"), cc)
        if history:  # Access Now's record of shutdowns here: context only, never part of the judgment
            summary += history
            srcs.append({**SRC_STOP, "accessed_at": generated})
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

    # 3b. Cloudflare Radar (BO-IND-9, BO-IND-10): reported by Cloudflare, never assessed here.
    features += _radar_features(snapshot, generated, valid_until, status)

    # 3c. Routing integrity (BO-WP-4): Cloudflare Radar's BGP hijack and route-leak detection, reported.
    features += _bgp_features(snapshot, generated, valid_until, status)

    # 3d. Power (BO-WP-5): the Zaporizhzhya NPP's off-site power as the IAEA reports it.
    features += _znpp_feature(snapshot, generated, valid_until, status)

    # 3e. Power (BO-WP-5): US states with more than 50,000 meters out at ODIN's reporting utilities.
    features += _odin_features(snapshot, generated, valid_until, status)

    # 3f. Power (BO-WP-5): watched cities much darker at night than their recent nights (NASA Black Marble).
    features += _night_features(snapshot, generated, valid_until, status)

    # 3g. Power (BO-WP-5): US balancing authorities serving far less than their own forecast implies (EIA-930).
    features += _eia_features(snapshot, generated, valid_until, status)

    # 4. Provider watch (BO-WP-3): no map point (a provider network is not a place), listed on the page instead.
    features += _provider_features(snapshot, generated, valid_until, status)

    return {
        "type": "FeatureCollection",
        "contract": CONTRACT,
        "program": "blackout",
        "generated_at": generated,
        "mode": snapshot.get("mode", "seed"),
        # Blackout's `terms` in the kit registry word for word (see LICENCE). OONI: CC BY-NC-SA 4.0,
        # github.com/ooni/license data/LICENSE.md, read 12 Nov 2026. IODA states no data licence on its API page
        # or site, so none is claimed for it.
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "language_key": LANGUAGE_KEY,
        "features": features,
    }
