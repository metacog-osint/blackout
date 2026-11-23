"""Build Blackout's contract-v2 feed (tic-feed/2, GeoJSON) and site data payload."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import tic_doctrine
from tic_doctrine import CONTRACT, confidence, geo, likelihood, tips as doctrine_tips, vocab
from tic_doctrine.contract import UTC_RE

from . import bgp, blackmarble, bluesky, eaglei, eia, faa, gdelt, iaea, ioda, itu, netblocks, odin, satcom, status_feeds, stop

SITE = "https://blackout.theintelligence.company"
CONTRACT_REV = "2.1"  # feed contract revision 2.1 (kit 0.6.0, 18 Nov 2026): fields added beside the old keys


def _rev21(props: dict, subject: str, start: str | None = None, end: str | None = None, precision: str | None = None,
           signal: dict | None = None, basis: str | None = None) -> dict:
    """Add the revision 2.1 item fields to props (in place) and return it. subject is the item's subject_id: the id
    itself where the id is stable, otherwise blackout:<kind>:<key> with no date, so COP's ledger and the kit history
    log keep one subject across readings. start/end are the event's own times from the source's fields (never from
    the prose); end only when the source closes or schedules it. basis is place_basis, set only when countries are
    named. last_change comes from the history log (--fill-last-change) unless the builder states it."""
    props["subject_id"] = subject
    start, end = _z(start), _z(end)
    if start:
        props["event_start"] = start
        if end and end >= start:
            props["event_end"] = end
        if precision:
            props["event_time_precision"] = precision
    if signal and isinstance(signal.get("value"), (int, float)) and not isinstance(signal["value"], bool):
        props["signal"] = {k: v for k, v in signal.items() if v is not None}
    if basis and props.get("countries"):
        props["place_basis"] = basis
    return props


def _z(t) -> str | None:
    """t when it is UTC ending in Z (the contract's time form), else None, so a provider's odd time is left out."""
    return t if isinstance(t, str) and UTC_RE.match(t) else None


def _days_before(day: str, n: int) -> str:
    """'2026-11-18', 6 -> '2026-11-12T00:00:00Z'."""
    d = datetime.strptime(day, "%Y-%m-%d") - timedelta(days=n)
    return f"{d:%Y-%m-%d}T00:00:00Z"
METHOD = "blackout-1.1"
LANGUAGE_KEY = "https://github.com/metacog-osint/tic-doctrine/blob/main/docs/language-key.md"
# The feed licence is Blackout's `terms` in the kit's suite registry, word for word, read from the vendored kit so the
# two can't drift. Kit 0.4.5 (13 Nov 2026) appended each new source's licence as doctrine/sources.json states it.
LICENCE = next(p["terms"] for p in vocab.load("programs")["programs"] if p["id"] == "blackout")
ATTRIBUTION = ("Blackout, The Intelligence Company. Data: OONI (Open Observatory of Network Interference), CC BY-NC-SA "
               "4.0; Georgia Tech IODA, credited as the source (IODA states no data licence); provider status feeds "
               "(AWS, Google Cloud, Google Workspace, Azure, Microsoft 365, Apple, Cloudflare, Akamai, Slack, Zoom, GitHub, "
               "Starlink Statuspage) as each provider publishes them; Cloudflare Radar (outages, anomalies, BGP "
               "hijacks and route leaks), CC BY-NC 4.0; the IAEA's Ukraine updates, reused with acknowledgement (no "
               "IAEA endorsement); Access Now's STOP dataset, credited (no licence stated); Citizen Lab and Others, "
               "URL Testing Lists Intended for Discovering Website Censorship, CC BY-NC-SA 4.0; ODIN (Oak Ridge National "
               "Laboratory for the US Department of Energy) and the reporting utilities, credited (no licence stated); "
               "US Census Bureau Gazetteer (public domain); NASA Black Marble (VIIRS, NOAA-20), from NASA LANCE and "
               "LAADS DAAC; Source: U.S. Energy Information Administration (EIA-930, public domain); base rates from ORNL "
               "EAGLE-I's outage history 2018-2025 (Brelsford et al., figshare doi:10.6084/m9.figshare.24237376), "
               "Creative Commons Attribution 4.0, reduced by Blackout to state episodes; ground stops from the FAA Air Traffic "
               "Control System Command Center's advisories (US Government work, public domain); counts of public "
               "Bluesky posts (no post, text or author kept); news headlines counted from the GDELT Project's Global "
               "Knowledge Graph (https://www.gdeltproject.org/), outlets and links only; NetBlocks' reports, from its "
               "public Bluesky posts: times, links, fields parsed from them and short excerpts, credited to NetBlocks "
               "(copyright NetBlocks, all rights reserved); internet users as an upper bound on exposure, from "
               f"{itu.SOURCE} ({itu.LICENCE}).")

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

OUTAGE_BASELINE = {"id": "ioda-7d-median", "window": "the same hours of the 7 days before the event, per IODA "
                   "datasource; a signal with a baseline median under 20 is left out", "type": "rolling", "version": "2"}
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
STATUS_NCF = {"starlink": ["provide_satellite_access"], "viasat": ["provide_satellite_access"],
              "cloudflare": ["provide_internet_routing", "provide_internet_content_services"]}
STATUS_NCF_DEFAULT = ["provide_internet_content_services"]


def _ooni_window_text(o: dict, win: dict | None) -> str:
    """What OONI shows in an outage's own hours, said so that "not read" never reads as "nothing there"."""
    name = o.get("country_name") or o["country"]
    if not win:
        return (" OONI was not read for these hours, so the drop alone doesn't tell a shutdown (BO-H-1A) from a power "
                "or physical fault (BO-H-1B).")
    n, exp = win["measurements"], win["expected_measurements"]
    hrs = f"{win['hours']} hour{'s' if win['hours'] != 1 else ''} from {win['from'][11:16]} UTC"
    if n < 50:
        return (f" OONI has {n} measurements from {name} in the {hrs} (about {round(exp)} on a usual day at those hours), "
                "too few to read blocking; late uploads can still add some. The drop alone doesn't tell a shutdown "
                "(BO-H-1A) from a power or physical fault (BO-H-1B).")
    rate = round(win["rate"] * 100, 1)
    base = "no measurements" if win["baseline_rate"] is None else f"{round(win['baseline_rate'] * 100, 1)}%"
    head = (f" In the {hrs}, {rate}% of OONI's {n} measurements from {name} were confirmed blocks or anomalies "
            f"({win['confirmed']} confirmed), against {base} in the same hours of the {win['baseline_days']} days "
            "before.")
    if win.get("rising_blocking"):
        return head + (" Blocking rose with the outage, which supports a shutdown (BO-H-1A). Blackout puts no "
                       "probability on it until Verdict has scored this reading.")
    return head + (" Blocking did not rise with the outage (BO-IND-3's line and twice the usual rate), so the drop alone doesn't tell a shutdown (BO-H-1A) from "
                   "a power or physical fault (BO-H-1B).")


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
        feats.append({"type": "Feature", "geometry": None, "properties": _rev21({
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
        }, f"blackout:provider-net:{w['asn'].lower()}", start=w.get("window_start"),
            signal={"name": "IODA signal on the network against its 7-day median", "value": round(100 - w["drop_pct"], 1),
                    "unit": "%", "expected": 100, "tail": "low"} if w.get("drop_pct") is not None else None)})

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
                   + (f"{f['label']} has posted no update in {inc['quiet_days']} days, so Blackout lists it as "
                      f"long-running (no update in {status_feeds.QUIET_DAYS} days or more), not as a current incident. "
                      if inc.get("long_running") else "")
                   + "Reported by the provider; Blackout does not assess its cause.")
        slug = "".join(ch if ch.isalnum() else "-" for ch in str(inc["incident_id"]).lower()).strip("-")[:80]
        loc = f" ({', '.join(inc['locations'][:3])})" if inc.get("locations") else ""
        quiet = f" (open; no update in {inc['quiet_days']} days)" if inc.get("long_running") else ""
        # The provider dates its own changes, so last_change is stated here, not left to the history log: an update
        # after the start is "changed" at that update, otherwise the incident is "new" at its start.
        began, updated = _z(inc.get("started_at")), _z(inc.get("updated_at"))
        told = ({"last_change": "changed", "last_change_at": updated} if updated and began and updated > began
                and updated <= generated else
                {"last_change": "new", "last_change_at": began} if began and began <= generated else {})
        feats.append({"type": "Feature", "geometry": None, "properties": _rev21({
            **common,
            **told,
            "tier": "routine" if inc.get("long_running") else "anomaly",
            "id": f"blackout:provider-status:{inc['feed']}:{slug}",
            "title": f"{f['label']}: {inc['title']}{loc}{quiet}",
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
        }, f"blackout:provider-status:{inc['feed']}:{slug}", start=began)})

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
        feats.append({"type": "Feature", "geometry": None, "properties": _rev21({
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
        }, "blackout:provider-concurrent:watched", start=conc.get("window_start"))})
    return feats


SRC_FAA = {
    "id": "faa-atcscc",
    "publisher": "Federal Aviation Administration, Air Traffic Control System Command Center (ATCSCC)",
    "title": "ATCSCC advisories database: ground stops",
    "url": faa.PAGE,
    "licence": "US Government work (Federal Aviation Administration), public domain; credited to the FAA's Air Traffic "
               "Control System Command Center",
    "grade": "A1",
    "independence_group": "faa",
    "source_type": "official_notice",
    "descriptor": "The FAA's own advisory ordering the ground stop: what it stopped, the condition it names and its "
                  "effective times. It says what the FAA did, not why a system failed.",
}
FAA_BASELINE = {"id": "bo-ind-17-ground-stop",
                "window": "fixed: the FAA issues a ground stop for every airport, a whole airline, an airline's request "
                          "at 2 or more airports within 2 h, or an airport's equipment or communications outage "
                          "(BO-IND-17); kept 24 h after it ends",
                "type": "fixed", "version": "1"}
FAA_COVERAGE = ("A ground stop is the FAA's tool: an airline outage that only holds flights at the gate, with no stop "
                "requested, does not appear here.")


def _hm(ts: str | None) -> str:
    """'20:54 UTC 13 Nov 2026' for a feed summary (the page shows each reader's own zone)."""
    if not ts:
        return "an unstated time"
    d = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    return f"{d:%H:%M} UTC {d.day} {d:%b %Y}"


def faa_state(e: dict) -> str:
    if e.get("in_effect"):
        return "in effect"
    if e.get("cancelled_at"):
        return "cancelled"
    return "ended"


def _faa_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """US aviation system stops (BO-WP-6, BO-IND-17): ground stops as the FAA issues them, reported, never assessed."""
    snap = snapshot.get("faa") or {}
    feats = []
    _, lat, lon = ioda.resolve_country("US")
    us_geom = {"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None
    for e in snap.get("events") or []:
        state = faa_state(e)
        cond = e.get("condition")
        if e["kind"] == "national":
            title = f"FAA ground stop at every US airport ({state})"
            what = "a ground stop for departures at every US airport"
            tier = "anomaly"
        elif e["kind"] == "airline":
            name = e.get("airline") or e["key"]
            title = f"FAA ground stop for all {name} ({e['key']}) flights ({state})"
            what = f"a ground stop for every {name} ({e['key']}) flight"
            tier = "anomaly"
        elif e["kind"] == "carrier":
            who = ", ".join(e.get("carriers") or []) or "an airline the advisories do not name"
            title = f"Ground stops at {len(e['airports'])} US airports at a carrier's request ({', '.join(e['airports'])})"
            what = (f"ground stops at {len(e['airports'])} airports ({', '.join(e['airports'])}) within 2 h, each "
                    f"at a carrier's request (named: {who})")
            tier = "anomaly"
        else:
            title = f"FAA ground stop at {e['key']}: {cond or 'equipment outage'} ({state})"
            what = f"a ground stop at {e['key']} for {cond or 'an equipment or communications outage'}"
            tier = "anomaly" if len(e.get("cluster") or []) >= 2 else "routine"
        when = f"from {_hm(e['started_at'])}"
        if e.get("cancelled_at"):
            when += f", cancelled at {_hm(e['cancelled_at'])}"
        elif e.get("in_effect"):
            when += f", effective to {_hm(e['ends_at'])} unless extended or cancelled"
        else:
            when += f", to {_hm(e['ends_at'])} (the last effective time the FAA gave; no cancellation was read)"
        summary = (f"The FAA's Command Center issued {what}, {when}. "
                   + (f"Condition the FAA names: {cond}. " if cond else "")
                   + (f"FAA comments: {e['comments']}. " if e.get("comments") else "")
                   + (f"Equipment ground stops at {', '.join(e['cluster'])} began within 2 h of each other. "
                      if e["kind"] == "equipment" and len(e.get("cluster") or []) >= 2 else "")
                   + f"Advisories: {', '.join(str(a['advn']) for a in e['advisories'])}. "
                   + "Reported by the FAA; Blackout does not assess why the system failed. " + FAA_COVERAGE)
        on_map = us_geom is not None and e["kind"] in ("national", "airline")
        feats.append({"type": "Feature", "geometry": us_geom if on_map else None, "properties": _rev21({
            "contract": CONTRACT, "program": "blackout",
            "id": f"blackout:faa:{e['id']}",
            "title": title,
            "summary": summary,
            "kind": "observation", "tier": tier, "status": status, "msg_type": "new", "human_review": "none",
            "us_person_data": "organizations" if e["kind"] in ("airline", "carrier") else "none",
            "status_class": "reported", "product_category": "current",
            "data_as_of": max(a["issued"] for a in e["advisories"] if a.get("issued")),
            "published_at": generated, "valid_until": valid_until,
            "countries": ["US"], "pmesii_pt": ["infrastructure", "economic"], "ascope": ["structures", "capabilities"],
            "ie_aspect": ["physical"], "ncf": ["transport_by_air"],
            "requirement_ids": ["BO-PIR-11"], "warning_problem_id": "BO-WP-6", "indicator_id": "BO-IND-17",
            "hypothesis_id": "BO-H-6A", "direction": "neutral", "indicator_state": "present",
            "method_version": METHOD, "coverage_fraction": 1.0,
            "precision_class": "country" if on_map else "none",
            "baseline_ref": FAA_BASELINE,
            "visual": {"type": "list", "url": f"{SITE}/#providers/faa-{faa_key(e)}"},
            "sources": [{**SRC_FAA, "url": e.get("link") or faa.PAGE, "accessed_at": generated}],
        }, f"blackout:faa:{e['kind']}:{e['key'].lower()}", start=e.get("started_at"),
            # The FAA's cancellation, or else the last effective time it gave (scheduled while in effect).
            end=e.get("cancelled_at") or e.get("ends_at"), basis="location")})
    return feats


def faa_key(e: dict) -> str:
    """The page's row key for an FAA event (#providers/faa-<key>)."""
    return "".join(ch if ch.isalnum() else "-" for ch in e["id"].lower()).strip("-")


# Outages the public and the press report (BO-WP-7): Bluesky post counts (BO-IND-18) and GDELT headlines (BO-IND-19).
SRC_BLUESKY = {
    "id": "bluesky-search",
    "publisher": "Bluesky (public post search, app.bsky.feed.searchPosts)",
    "title": "Posts naming the service with \"down\" or \"outage\": counts only",
    "url": "https://bsky.app/search",
    "licence": "No data licence stated; Blackout keeps and republishes only post counts and a link to Bluesky's own "
               "search, never a post, its text or its author (Bluesky's developer guidelines ask apps to honour "
               "deletions)",
    "grade": "F3",
    "independence_group": "bluesky",
    "source_type": "social_media",
    "descriptor": "How many public Bluesky posts named the service with an outage word: what members of the public "
                  "said, unverified, never what the company said.",
}
SRC_GDELT = {
    "id": "gdelt-gkg",
    "publisher": "The GDELT Project (Global Knowledge Graph 2.1)",
    "title": "News headlines naming the service with an outage word: counts, outlets and links",
    "url": "https://www.gdeltproject.org/",
    "licence": "GDELT Project datasets: unlimited and unrestricted use and redistribution, citing the GDELT Project "
               "with a link to https://www.gdeltproject.org/; Blackout republishes counts, outlet domains and article "
               "links, never headline text",
    "grade": "C3",
    "independence_group": "gdelt",
    "source_type": "press",
    "descriptor": "Which news outlets ran headlines saying the service was down, as GDELT read them: the press's "
                  "report, not the company's.",
}
BLUESKY_BASELINE = {"id": "bo-ind-18-bluesky-usual",
                    "window": "rolling: the same two searches over the 7 days before the 6-hour window, scaled to 6 "
                              "hours (the usual taken as at least the floor)",
                    "type": "rolling", "version": "1"}
GDELT_BASELINE = {"id": "bo-ind-19-gdelt-usual",
                  "window": "rolling: the same name's outage headlines per GKG file over the 7 days of files before "
                            "the 6-hour window, scaled to the window's 24 files (the usual taken as at least the floor)",
                  "type": "rolling", "version": "1"}
PUBLIC_CAVEAT = ("Blackout does not assess its cause. A surge can also be news of an outage elsewhere that names the "
                 "company, a joke, the words in another sense, or the company's own denial (BO-H-7B).")


def _n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else f"{x:g}"


def public_key(lane: str, r: dict, ref: str) -> str:
    """Item id tail and page row key for a public-report surge: lane, watched name and the UTC day it rose."""
    return f"{lane}-{r['key']}-{(ref or '')[:10]}"


def _public_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """Outages the public (Bluesky) and the press (GDELT) report at watched US services: counts, never assessed."""
    feats = []
    bs = snapshot.get("bluesky") or {}
    gd = snapshot.get("gdelt") or {}
    b_hot = {r["key"]: r for r in bs.get("surges") or []}
    g_hot = {r["key"]: r for r in gd.get("surges") or []}
    ncf = {k: n for k, _, _, _, n in bluesky.WATCH}
    common = {"contract": CONTRACT, "program": "blackout", "kind": "observation", "tier": "anomaly", "status": status,
              "msg_type": "new", "human_review": "none", "us_person_data": "organizations", "status_class": "reported",
              "product_category": "current", "published_at": generated, "valid_until": valid_until,
              "countries": ["US"], "pmesii_pt": ["infrastructure", "economic", "information"],
              "ascope": ["organizations", "capabilities"], "ie_aspect": ["physical", "informational"],
              "warning_problem_id": "BO-WP-7", "hypothesis_id": "BO-H-7A", "direction": "neutral",
              "indicator_state": "present", "method_version": METHOD, "precision_class": "none",
              "requirement_ids": ["BO-PIR-12"]}
    for r in bs.get("surges") or []:
        ref = r.get("first_hour") or bs.get("window_end")
        if r.get("first_hour") and r["first_hour"] == bs.get("window_start"):
            rose = f" Posts were already over the line in the window's first hour (from {_hm(r['first_hour'])})"
        elif r.get("first_hour"):
            rose = f" Posts rose past the line in the hour from {_hm(r['first_hour'])}"
        else:
            rose = ""
        rose += (f" and peaked in the hour from {_hm(r['peak_hour'])}." if rose and r.get("peak_hour")
                 else "." if rose else "")
        other = (f" News headlines also surged ({g_hot[r['key']]['headlines']} in GDELT; see that item)."
                 if r["key"] in g_hot else "")
        feats.append({"type": "Feature", "geometry": None, "properties": _rev21({
            **common,
            "id": f"blackout:public:{public_key('bluesky', r, ref)}",
            "title": f"{r['label']}: {_n(r['matches'])} public Bluesky posts report it down or out in "
                     f"{bs.get('window_h', bluesky.WINDOW_H)} h (usual {_n(r['usual'])})",
            "summary": (f"Members of the public posted {_n(r['matches'])} times on Bluesky naming \"{r['name']}\" with "
                        f"\"down\" ({_n(r['terms'].get('down', 0))}) or \"outage\" ({_n(r['terms'].get('outage', 0))}) "
                        f"in the {bs.get('window_h', bluesky.WINDOW_H)} hours to {_hm(bs.get('window_end'))}, "
                        f"against a usual of {_n(r['usual'])} for that many hours over the 7 days before."
                        f"{rose}{other} Reported by the public on Bluesky and not confirmed by {r['label']}; "
                        f"Blackout counts the posts and does not read or keep them. " + PUBLIC_CAVEAT),
            "data_as_of": bs.get("window_end") or generated,
            "baseline_ref": BLUESKY_BASELINE,
            "ncf": ncf.get(r["key"]) or ["provide_internet_content_services"],
            "indicator_id": "BO-IND-18",
            "coverage_fraction": 1.0,
            "visual": {"type": "list", "url": f"{SITE}/#providers/{public_key('bluesky', r, ref)}"},
            "sources": [{**SRC_BLUESKY, "url": r["link"], "accessed_at": generated}],
        }, f"blackout:public:bluesky-{r['key']}", start=r.get("first_hour"),  # hours count back from the window's end, not clock hours
            signal={"name": f"public Bluesky posts naming it with down or outage in {bs.get('window_h', bluesky.WINDOW_H)} h",
                    "value": r.get("matches"), "unit": "posts", "expected": r.get("usual"), "tail": "high"},
            basis="named")})
    for r in gd.get("surges") or []:
        ref = r.get("first_file") or gd.get("window_end")
        domains = r.get("outlet_domains") or []
        other = (f" Public Bluesky posts also surged ({_n(b_hot[r['key']]['matches'])}; see that item)."
                 if r["key"] in b_hot else "")
        partial = (f" The usual rests on {gd.get('baseline_files')} of the {gd.get('baseline_expected')} files of "
                   f"the 7 days before, the rest not yet read." if gd.get("baseline_files", 0)
                   < gd.get("baseline_expected", 0) else "")
        links = [{"publisher": x["domain"], "title": "News article (headline not reproduced)", "url": x["url"],
                  "licence": "Linked, not reproduced", "source_type": "press",
                  "descriptor": "An article GDELT read, linked so a reader can see the report itself.",
                  "accessed_at": generated} for x in (r.get("links") or [])[:5]]
        feats.append({"type": "Feature", "geometry": None, "properties": _rev21({
            **common,
            "id": f"blackout:public:{public_key('gdelt', r, ref)}",
            "title": f"{r['label']}: {_n(r['headlines'])} news headlines from {_n(r['outlets'])} outlets report an "
                     f"outage in {gd.get('window_h', gdelt.WINDOW_H)} h (usual {_n(r['usual'])})",
            "summary": (f"GDELT read {_n(r['headlines'])} news articles whose headlines name {r['label']} with an "
                        f"outage word, from {_n(r['outlets'])} outlets ({', '.join(domains[:8])}"
                        + (f" and {len(domains) - 8} more" if len(domains) > 8 else "") +
                        f"), in the {gd.get('window_h', gdelt.WINDOW_H)} hours to {_hm(gd.get('window_end'))}, "
                        f"against a usual of {_n(r['usual'])} for that many hours over the 7 days before."
                        + (f" The first came in GDELT's file of {_hm(r['first_file'])}." if r.get("first_file") else "")
                        + f"{partial}{other} Reported by the press and not confirmed by {r['label']}; Blackout links "
                        "the articles and does not reproduce their headlines. " + PUBLIC_CAVEAT),
            "data_as_of": gd.get("window_end") or generated,
            "baseline_ref": GDELT_BASELINE,
            "ncf": ncf.get(r["key"]) or ["provide_internet_content_services"],
            "indicator_id": "BO-IND-19",
            "coverage_fraction": round(gd.get("window_read", 0) / gd["window_files"], 2) if gd.get("window_files")
            else 1.0,
            "visual": {"type": "list", "url": f"{SITE}/#providers/{public_key('gdelt', r, ref)}"},
            "sources": [{**SRC_GDELT, "accessed_at": generated}, *links],
        }, f"blackout:public:gdelt-{r['key']}", start=r.get("first_file"),
            signal={"name": f"news headlines naming it with an outage word in {gd.get('window_h', gdelt.WINDOW_H)} h",
                    "value": r.get("headlines"), "unit": "headlines", "expected": r.get("usual"), "tail": "high"},
            basis="named")})
    return feats


SRC_NETBLOCKS = {
    "id": "netblocks-bluesky",
    "publisher": "NetBlocks",
    "title": "NetBlocks' reports of internet disruptions, from its public Bluesky posts",
    "url": netblocks.PROFILE,
    "licence": netblocks.TERMS,
    "grade": "B2",
    "independence_group": "netblocks",
    "source_type": "monitor_report",
    "descriptor": "What NetBlocks, an internet observatory, reports from its own measurements: the places, causes and "
                  "levels it names. NetBlocks chooses what to post, so a place with no post is not a place with no "
                  "disruption.",
}
NETBLOCKS_BASELINE = {"id": "netblocks-report",
                      "window": "fixed: NetBlocks posts a report naming the place, platform or network (BO-IND-20); "
                                f"kept {netblocks.ITEM_HOURS} h after it is posted",
                      "type": "fixed", "version": "1"}
# NetBlocks' cause words -> (warning problem, hypothesis it bears on). Always direction neutral: NetBlocks' reading,
# quoted, never Blackout's.
NETBLOCKS_CAUSE = (("cable", "BO-WP-2", "BO-H-2A"), ("power", "BO-WP-1", "BO-H-1B"), ("strike", "BO-WP-1", "BO-H-1B"),
                   ("shutdown", "BO-WP-1", "BO-H-1A"), ("restriction", "BO-WP-1", "BO-H-1A"))


def _iso_ts(t: str | None) -> float | None:
    try:
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() if t else None
    except (TypeError, ValueError):
        return None


def corroboration(snapshot: dict, cc: str, at: str | None, hours: int = netblocks.CORROBORATION_H) -> dict:
    """Which independent measurers saw something in country `cc` within `hours` of `at`: IODA outages in this run,
    Cloudflare Radar outages and verified anomalies, and NetBlocks posts, each counted ({source: n} plus window_h)."""
    t0 = _iso_ts(at)
    win = hours * 3600

    def near(t):
        t1 = _iso_ts(t)
        return t0 is not None and t1 is not None and abs(t1 - t0) <= win
    r = snapshot.get("radar") or {}
    nb = snapshot.get("netblocks") or {}
    return {
        "window_h": hours,
        "ioda": sum(1 for o in snapshot.get("outages") or [] if o.get("indicator_id") and o.get("country") == cc
                    and near(o.get("start_time"))),
        "radar_outages": sum(1 for a in r.get("annotations") or [] if cc in (a.get("countries") or [])
                             and near(a.get("start"))) if snapshot.get("radar") is not None else None,
        "radar_anomalies": sum(1 for a in r.get("anomalies") or [] if a.get("country") == cc and near(a.get("start")))
        if snapshot.get("radar") is not None else None,
        "netblocks": sum(1 for p in nb.get("last_30d") or [] if cc in (p.get("countries") or [])
                         and near(p.get("posted_at"))) if snapshot.get("netblocks") is not None else None,
    }


def percent_of_normal(snapshot: dict, o: dict, hours: int = netblocks.CORROBORATION_H) -> dict:
    """The impact measure for a country outage: IODA's active-probing level in the event against the same hours of
    the 7 days before (100 - probing_drop_pct; None when probing was not read), and the lowest level NetBlocks states
    for the country in a post within `hours` of the start, with that post's link."""
    out: dict = {"ioda_active_probing_pct": (round(100 - o["probing_drop_pct"], 1)
                                             if isinstance(o.get("probing_drop_pct"), (int, float)) else None),
                 "netblocks_level_pct": None, "netblocks_url": None}
    t0 = _iso_ts(o.get("start_time"))
    best = None
    for p in ((snapshot.get("netblocks") or {}).get("last_30d") or []):
        t1 = _iso_ts(p.get("posted_at"))
        if (p.get("level_pct") is not None and o.get("country") in (p.get("countries") or []) and t0 is not None
                and t1 is not None and abs(t1 - t0) <= hours * 3600 and (best is None or p["level_pct"] < best["level_pct"])):
            best = p
    if best:
        out.update(netblocks_level_pct=best["level_pct"], netblocks_url=best.get("url"))
    return out


def impact_text(pon: dict, exp: dict | None, name: str) -> str:
    """' Percent of normal: active probing 62% of its usual level; NetBlocks states 67% (its post). Exposure, an upper
    bound and not users affected: about 110 million people online in Pakistan (ITU 2024: 45.7% of 241 million).'"""
    parts = []
    if pon.get("ioda_active_probing_pct") is not None:
        parts.append(f"IODA active probing at {pon['ioda_active_probing_pct']:g}% of its usual level for these hours")
    if pon.get("netblocks_level_pct") is not None:
        parts.append(f"NetBlocks states {pon['netblocks_level_pct']:g}% of ordinary levels ({pon['netblocks_url']})")
    text = (" Percent of normal: " + "; ".join(parts) + ".") if parts else ""
    if exp:
        text += (f" Exposure, an upper bound and not users affected: about {_people(exp['internet_users_upper'])} people "
                 f"online in {name} (ITU {exp['year']}: {exp['share_pct']:g}% of {_people(exp['population'])}).")
    return text


def _people(n: int) -> str:
    if n >= 10_000_000:
        return f"{round(n / 1e6)} million"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f} million"
    return f"{round(n, -3 if n >= 10_000 else -2):,}"


def corroboration_text(c: dict, own: str) -> str:
    """' Independent measurers within 24 h: IODA 1 outage; Cloudflare Radar 1 outage, 0 anomalies; NetBlocks 2
    posts.' with `own` (the item's own source) left out, and a source not read said as not read."""
    parts = []
    if own != "ioda":
        parts.append(f"IODA {c['ioda']} outage{'s' if c['ioda'] != 1 else ''} in this run")
    if own != "radar":
        parts.append("Cloudflare Radar not read" if c["radar_outages"] is None else
                     f"Cloudflare Radar {c['radar_outages']} outage{'s' if c['radar_outages'] != 1 else ''} and "
                     f"{c['radar_anomalies']} verified anomal{'ies' if c['radar_anomalies'] != 1 else 'y'}")
    if own != "netblocks":
        parts.append("NetBlocks not read" if c["netblocks"] is None else
                     f"NetBlocks {c['netblocks']} post{'s' if c['netblocks'] != 1 else ''}")
    return f" Other measurers in this country within {c['window_h']} h: " + "; ".join(parts) + "."


def _netblocks_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """NetBlocks' reports (BO-IND-20): each recent post naming a place, platform or network, as NetBlocks states it,
    quoted with its link, never assessed."""
    nb = snapshot.get("netblocks") or {}
    feats = []
    for p in nb.get("recent") or []:
        ccs = p.get("countries") or []
        name, lat, lon = ioda.resolve_country(ccs[0]) if ccs else (None, None, None)
        geom = {"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None
        causes = p.get("causes") or []
        if p.get("scope") == "platform" or not ccs:
            wp, hyp = "BO-WP-3", "BO-H-3B"
        else:
            wp, hyp = next(((w, h) for c, w, h in NETBLOCKS_CAUSE if c in causes), ("BO-WP-1", "BO-H-1A"))
        where = ", ".join(p.get("regions") or []) or ", ".join(ioda.resolve_country(c)[0] for c in ccs)
        what = ", ".join(p.get("platforms") or []) or ", ".join(f"AS{a}" for a in p.get("asns") or [])
        head = where or what or "a place not named"
        label = p.get("type") or "Report"
        level = (f" NetBlocks states the level: {p['level_pct']:g}{p['level_basis'][1:]}." if p.get("level_pct")
                 is not None else "")
        cor = "".join(corroboration_text(corroboration(snapshot, cc, p.get("posted_at")), "netblocks") for cc in ccs[:3])
        summary = (f"NetBlocks ({label}, {_hm(p.get('posted_at'))}): “{p['excerpt']}” (full post: {p['url']})."
                   + level
                   + (f" Causes NetBlocks names: {', '.join(causes)}." if causes else "")
                   + (f" Services or networks named: {what}." if what else "")
                   + cor
                   + " Reported by NetBlocks; Blackout quotes it and does not assess it.")
        feats.append({"type": "Feature", "geometry": geom, "properties": _rev21({
            "contract": CONTRACT, "program": "blackout",
            "id": f"blackout:netblocks:{p['id']}",
            "title": f"{head}: NetBlocks report ({label})",
            "summary": summary,
            "kind": "observation", "tier": "anomaly" if label in ("Confirmed", "Update") else "routine",
            "status": status, "msg_type": "new", "human_review": "none",
            "us_person_data": "organizations" if p.get("platforms") else "none",
            "status_class": "reported", "product_category": "current",
            "data_as_of": p.get("posted_at") or generated, "published_at": generated, "valid_until": valid_until,
            "countries": ccs,
            "pmesii_pt": ["infrastructure", "information"], "ascope": ["structures", "capabilities"],
            "ie_aspect": ["physical", "informational"], "ncf": ["provide_internet_routing", "operate_core_network"],
            "requirement_ids": ["BO-PIR-4"] if wp == "BO-WP-3" else (["BO-PIR-2"] if wp == "BO-WP-2" else
                                                                        ["BO-PIR-1"]),
            "warning_problem_id": wp, "indicator_id": "BO-IND-20", "hypothesis_id": hyp, "direction": "neutral",
            "indicator_state": "present", "method_version": METHOD, "coverage_fraction": 1.0,
            "precision_class": "country" if geom else "none",
            "baseline_ref": NETBLOCKS_BASELINE,
            "visual": {"type": "list", "url": f"{SITE}/#outages/netblocks-{p['id']}"},
            "sources": [{**SRC_NETBLOCKS, "url": p["url"], "accessed_at": generated}],
        }, f"blackout:netblocks:{p['id']}", start=p.get("posted_at"),
            signal={"name": "connectivity as NetBlocks states it, against ordinary levels", "value": p["level_pct"],
                    "unit": "%", "expected": 100, "tail": "low"} if p.get("level_pct") is not None else None,
            basis="named" if ccs else None)})
    return feats


SRC_SATCOM = {
    "id": "cloudflare-radar-satcom",
    "publisher": "Cloudflare Radar",
    "title": "Cloudflare Radar: HTTP request volume from a satellite network's address space, worldwide and by country",
    "url": "https://developers.cloudflare.com/radar/",
    "licence": "CC BY-NC 4.0 (developers.cloudflare.com/radar, read 13 Nov 2026)",
    "grade": "A3",
    "independence_group": "cloudflare-radar",
    "source_type": "telemetry",
    "descriptor": "Requests Cloudflare itself receives from the network's address space (its own traffic, not users, and "
                  "not other measurers'), hourly, as the share of the series' busiest hour.",
}
SATCOM_BASELINE = {"id": "bo-ind-21-weekday-normal",
                   "window": f"the median, over the {satcom.WEEKDAY_WEEKS} weeks before, of the mean of the same clock "
                             f"hours on the same weekday (BO-IND-21)",
                   "type": "rolling", "version": "1"}
SATCOM_SHORT_BASELINE = {"id": "bo-ind-22-hour-normal",
                         "window": "the median of the same hour 1 to 4 weeks earlier, at least 3 of the 4 (BO-IND-22)",
                         "type": "rolling", "version": "1"}


def satcom_key(asn: int, cc: str | None) -> str:
    return f"blackout:satcom:{asn}" + (f":{cc}" if cc else "")


def _satcom_features(snapshot: dict, generated: str, valid_until: str, status: str) -> list[dict]:
    """Satellite networks' traffic drops (BO-IND-21): a counted network's Cloudflare Radar request volume under 70% of
    its weekday-matched normal on two runs in a row, worldwide or in one country (Starlink: its top 20). Observations
    of the network's own traffic: never an outage, never users, no cause. A drop that has lasted 3 days is standing
    (routine). A series too thin, too coarse or without enough weeks is never flagged (named in the traffic reading)."""
    traffic = (snapshot.get("satcom") or {}).get("traffic") or {}
    feats = []
    for row in traffic.get("networks") or []:
        radar_r = row.get("radar") or {}
        readings = [(None, radar_r.get("world"))] + sorted((radar_r.get("countries") or {}).items())
        for cc, m in readings:
            w = (m or {}).get("weekday") or {}
            if w.get("state") != "fired":
                continue
            asn, name = row["asn"], row.get("operator") or f"AS{row['asn']}"
            cname, lat, lon = ioda.resolve_country(cc) if cc else (None, None, None)
            where = f"in {cname or cc}" if cc else "worldwide"
            standing = bool(w.get("standing"))
            geom = {"type": "Point", "coordinates": [lon, lat]} if cc and lat is not None and lon is not None else None
            days = w["hours_below"] // 24
            summary = (f"{name} (AS{asn}) {where}: Cloudflare Radar's request volume from this network is at "
                       f"{w['weekday_pct']:g}% of its normal for this weekday and hours (the median of the last "
                       f"{w['weeks']} same weekdays), after {w['prior_pct']:g}% four hours earlier; it has been under "
                       f"70% since {w['since']} ({w['hours_below']} h)."
                       + (f" It has lasted {days} days, so it is a standing condition." if standing else "")
                       + _country_all_text(w, cname or cc)
                       + " This measures the network's own traffic as Cloudflare sees it, not its users, and a level "
                         "under 100% can be a holiday, an event or a routing change: it is not an outage and no "
                         "cause is assessed. Cloudflare Radar, CC BY-NC 4.0.")
            feats.append({"type": "Feature", "geometry": geom, "properties": _rev21({
                "contract": CONTRACT, "program": "blackout",
                "id": satcom_key(asn, cc),
                "title": f"{name} (AS{asn}) {where}: traffic at {w['weekday_pct']:g}% of its weekday normal",
                "summary": summary,
                "kind": "observation", "tier": "routine" if standing else "anomaly",
                "status": status, "msg_type": "new", "human_review": "none", "us_person_data": "none",
                "status_class": "reported", "product_category": "current",  # the kit has no "measured"
                "data_as_of": m.get("current_to") or traffic.get("checked_at") or generated,
                "published_at": generated, "valid_until": valid_until,
                "countries": [cc] if cc else [],
                "pmesii_pt": ["infrastructure", "information"], "ascope": ["structures", "capabilities"],
                "ie_aspect": ["physical", "informational"],
                "ncf": ["provide_satellite_access", "provide_internet_routing"],
                "requirement_ids": ["BO-PIR-4"], "warning_problem_id": "BO-WP-3", "indicator_id": "BO-IND-21",
                "hypothesis_id": "BO-H-3A", "direction": "neutral", "indicator_state": "present",
                "method_version": METHOD, "coverage_fraction": 1.0,
                "precision_class": "country" if geom else "none",
                "baseline_ref": SATCOM_BASELINE,
                "visual": {"type": "list", "url": f"{SITE}/#satcom/as{asn}"},
                "sources": [{**SRC_SATCOM, "url": satcom.RADAR_AS_PAGE.format(asn=asn), "accessed_at": generated}],
            }, satcom_key(asn, cc), start=w.get("since"),
                signal={"name": "request volume against the same weekday's normal", "value": w["weekday_pct"],
                        "unit": "%", "expected": 100, "tail": "low"}, basis="location" if cc else None)})
        short = (radar_r.get("world") or {}).get("short") or {}
        if row.get("asn") == satcom.SHORT_ASN and short.get("state") == "fired":
            feats.append(_satcom_short_feature(row, short, traffic, generated, valid_until, status))
    return feats


def _country_all_text(w: dict, place: str | None) -> str:
    """The whole-country sentence of a fired country item, when all of that country's traffic fell too."""
    c = w.get("country_all") or {}
    if not c.get("fell"):
        return ""
    return (f" All traffic from {place} was also down, at {c['pct']:g}% of its normal for the same hours, so the drop "
            "is not specific to this network.")


def _satcom_short_feature(row: dict, s: dict, traffic: dict, generated: str, valid_until: str, status: str) -> dict:
    """BO-IND-22: an hour of Starlink's worldwide request volume under 60% of that hour's normal. The source's reading:
    never called an outage, no cause, no likelihood."""
    asn, name = row["asn"], row.get("operator") or f"AS{row['asn']}"
    key = f"blackout:satcom-short:{asn}"
    summary = (f"{name} (AS{asn}) worldwide: Cloudflare Radar's request volume from this network fell to "
               f"{s['lowest_pct']:g}% of normal in the hour from {s['lowest_at']} (normal: the median of the same hour "
               f"1 to 4 weeks earlier), with {s['hours_under']} of the last {satcom.SHORT_HOURS} hours under "
               f"{satcom.SHORT_BELOW_PCT:g}%. All of Radar's traffic was not down over those hours. This measures the "
               "network's own traffic as Cloudflare sees it, not its users: it is not called an outage and no cause "
               "is assessed. Cloudflare Radar, CC BY-NC 4.0.")
    return {"type": "Feature", "geometry": None, "properties": _rev21({
        "contract": CONTRACT, "program": "blackout", "id": key,
        "title": f"{name} (AS{asn}) worldwide: an hour at {s['lowest_pct']:g}% of normal",
        "summary": summary,
        "kind": "observation", "tier": "anomaly",
        "status": status, "msg_type": "new", "human_review": "none", "us_person_data": "none",
        "status_class": "reported", "product_category": "current",
        "data_as_of": s.get("until") or traffic.get("checked_at") or generated,
        "published_at": generated, "valid_until": valid_until, "countries": [],
        "pmesii_pt": ["infrastructure", "information"], "ascope": ["structures", "capabilities"],
        "ie_aspect": ["physical", "informational"],
        "ncf": ["provide_satellite_access", "provide_internet_routing"],
        "requirement_ids": ["BO-PIR-4"], "warning_problem_id": "BO-WP-3", "indicator_id": "BO-IND-22",
        "hypothesis_id": "BO-H-3A", "direction": "neutral", "indicator_state": "present",
        "method_version": METHOD, "coverage_fraction": 1.0, "precision_class": "none",
        "baseline_ref": SATCOM_SHORT_BASELINE,
        "visual": {"type": "list", "url": f"{SITE}/#satcom/as{asn}"},
        "sources": [{**SRC_SATCOM, "url": satcom.RADAR_AS_PAGE.format(asn=asn), "accessed_at": generated}],
    }, key, start=s.get("since"),
        signal={"name": "request volume against the same hour's normal", "value": s["lowest_pct"],
                "unit": "%", "expected": 100, "tail": "low"})}


RADAR_OUTAGE_BASELINE = {"id": "radar-outage-center",
                         "window": "fixed: Cloudflare Radar's analysts list the outage in its Outage Center (BO-IND-9)",
                         "type": "fixed", "version": "1"}
RADAR_ANOMALY_BASELINE = {"id": "radar-traffic-anomaly",
                          "window": "Cloudflare Radar's own anomaly detection on its traffic, verified by Cloudflare "
                                    "(BO-IND-10)",
                          "type": "rolling", "version": "1"}
PLATFORM_WHERE = "Platform outage, no place named"  # also in app.js (radarWhere)

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
        by_net = a.get("country_basis") == "network"
        where = (a.get("regions") or [None])[0] or name or ", ".join(ccs)
        if by_net and where:
            where = f"{where} (network's country)"
        # A PLATFORM outage belongs to a service (Microsoft 365 on 12 Nov 2026), and Cloudflare names no place for it.
        platform = not where and a.get("type") == "PLATFORM"
        where = where or (PLATFORM_WHERE if platform else "Location not given")
        names = a.get("asn_names") or {}
        asns = "".join(f", AS{n}" + (f" {names[str(n)]}" if names.get(str(n)) else "")
                       for n in (a.get("asns") or [])[:3])
        net_note = (f" Cloudflare names no location, only the network; {', '.join(ccs)} is the country Radar's network "
                    "details give for it (where it is registered), not a measured place." if by_net else "")
        cause = _human(a.get("cause")) or "cause not stated"
        kind = f", {_human(a.get('type'))}" if a.get("type") and not platform else ""
        span = f"from {a['start']} to {a['end']}" if a.get("end") else f"since {a['start']}, ongoing"
        feats.append({"type": "Feature", "geometry": geom, "properties": _rev21({
            **common,
            "id": f"blackout:radar-outage:{a['radar_id']}",
            "title": f"{where}{asns}: {cause}{kind} (Cloudflare Radar)",
            "summary": (f"Cloudflare Radar's Outage Center lists an outage {span}: \"{a.get('description')}\" "
                        f"Cause as Cloudflare states it: {cause}.{net_note}{also_ioda(ccs)} Reported by Cloudflare; "
                        "Blackout does not assess the cause."),
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
        }, f"blackout:radar-outage:{a['radar_id']}", start=a.get("start"), end=a.get("end"),
           basis="named" if by_net else "location")})
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
        feats.append({"type": "Feature", "geometry": geom, "properties": _rev21({
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
        }, f"blackout:radar-anomaly:{a['uuid']}", start=a.get("start"), end=a.get("end"), basis="location")})
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
    # Tier routine: a fixed publishing line, not a change against a baseline (15 Nov 2026 review).
    common = {"contract": CONTRACT, "program": "blackout", "kind": "observation", "tier": "routine", "status": status,
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
        feats.append({"type": "Feature", "geometry": geom, "properties": _rev21({
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
        }, f"blackout:bgp-hijack:{h['radar_id']}", start=h.get("start"),
            signal={"name": "Cloudflare hijack confidence", "value": h.get("confidence"), "tail": "high"},
            # The victims' registered countries: where the networks are homed, not where anything happened.
            basis="named")})
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
        feats.append({"type": "Feature", "geometry": geom, "properties": _rev21({
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
        }, f"blackout:bgp-leak:as{asn}", start=first.get("start"),
            # Cloudflare marks each leak finished; when all are, the last one's last sighting is the end.
            end=None if still_open else max((x.get("last_seen") or "" for x in leaks), default=None),
            signal={"name": "prefixes leaked in the largest event", "value": top.get("prefixes"), "unit": "prefixes",
                    "tail": "high"},
            basis="named")})
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
        # A standing state (one line, or none for weeks) is routine; an update that reports a loss of power is not.
        "kind": "observation", "tier": "anomaly" if (present and z.get("losses")) else "routine", "status": status,
        "msg_type": "new",
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
    _rev21(props, "blackout:znpp-power:znpp", basis="location")
    geom = {"type": "Point", "coordinates": [iaea.ZNPP["lon"], iaea.ZNPP["lat"]]}
    return [{"type": "Feature", "geometry": geom, "properties": props}]


SRC_ITU = {
    "id": "itu-internet-use",
    "publisher": "ITU, via the World Bank's World Development Indicators",
    "title": "Individuals using the internet (% of population, IT.NET.USER.ZS) and population (SP.POP.TOTL)",
    "url": "https://data.worldbank.org/indicator/IT.NET.USER.ZS",
    "licence": itu.LICENCE,
    "grade": "B2",
    "independence_group": "itu",
    "source_type": "dataset",
    "descriptor": "The share of people who used the internet in the last three months, times the population that "
                  "year: an upper bound on how many people an outage in the country could reach, never how many it did.",
}
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
SRC_EAGLEI = {
    "id": "ornl-eaglei-history",
    "publisher": "Oak Ridge National Laboratory, EAGLE-I (Brelsford et al., figshare)",
    "title": "The Environment for Analysis of Geo-Located Energy Information's Recorded Electricity Outages 2014-2025",
    "url": eaglei.PAGE,
    "licence": eaglei.LICENCE,
    "grade": "B2",
    "independence_group": "eaglei",
    "source_type": "dataset",
    "descriptor": "County customers out every 15 minutes, read by ORNL from utilities' public outage maps; Blackout "
                  "reduced 2018-2025 to how often each state went over the line for an hour or more (the base rate "
                  "only, not this reading). ODIN's data also feeds EAGLE-I.",
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
    by_state: dict[str, list[dict]] = {}
    for c in o.get("counties") or []:  # largest first, as odin.parse_counties sorts them
        by_state.setdefault(c["state"], []).append({"fips": c["fips"], "name": c["name"], "meters": c["meters"]})
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
        br = s.get("base_rate")
        if br:
            summary += f" Base rate: {br['text']} {br['caveat']}"
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
            "baseline_ref": {**ODIN_BASELINE, **({"base_rate": {
                k: br[k] for k in ("years", "per_year", "month", "in_month", "hours_median", "hours_p90", "text")}}
                if br else {})},
            "visual": {"type": "map", "url": f"{SITE}/#power/us-{s['state'].lower()}"},
            # Every county in the state with meters out in this reading (county FIPS, Census 2025 Gazetteer name),
            # largest first, so a reader can match hazards by county instead of by state (NWS alerts carry SAME
            # codes, which are these FIPS with a leading 0). Counties whose FIPS is not in the Gazetteer are left out
            # and reported as a collection note.
            "us_counties": by_state.get(s["state"], []),
            "sources": [{**SRC_ODIN, "accessed_at": generated}]
                       + ([{**SRC_EAGLEI, "accessed_at": br["source"]["built_at"] or generated}] if br else []),
        }
        _rev21(props, f"blackout:us-power:{s['state'].upper()}",
               signal={"name": "meters without power, summed over the state", "value": s.get("meters"),
                       "unit": "meters", "tail": "high"}, basis="location")
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
        _rev21(props, f"blackout:night-lights:{c['id']}", start=f"{last['date']}T00:00:00Z", precision="day",
               signal={"name": "night radiance over the city box", "value": last.get("mean"), "unit": "nW/cm2/sr",
                       "expected": a.get("baseline_median"), "tail": "low", "baseline_n": a.get("baseline_nights")},
               basis="location")
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
        _rev21(props, f"blackout:us-demand:{b['id'].upper()}", start=r.get("start"),
               end=None if r.get("ongoing") else r.get("end"), precision="hour",
               signal={"name": "demand in the worst hour against what the forecast implies", "value": w.get("demand"),
                       "unit": "MW", "expected": w.get("expected"), "tail": "low",
                       "baseline_n": b.get("range_hours")}, basis="location")
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


# Per-source health (14 Nov 2026). A lane is an item's first source id, as the doctrine kit's history log
# (tic-history/1) and COP's suite ledger name it. When a source fails, Blackout drops its items rather than keeping the
# last ones, so a reader that sees an item vanish must know whether that lane was read this run before it calls the
# item ended. Any collection problem on a lane marks it "failing", even a partial one (one watched network, one status
# feed, one tile), because a partial read can drop items too; a problem this table cannot place marks every lane failed.
# Errors here can only delay an end, never invent one. Data notes that drop nothing are not failures.
STATUS_LANES = {f["id"]: f["source_id"] for f in status_feeds.FEEDS}
LANES = [SRC_IODA["id"], SRC_OONI["id"], SRC_RADAR["id"], SRC_RADAR_BGP["id"], SRC_IAEA["id"], SRC_STOP["id"],
         SRC_ODIN["id"], SRC_BLACK_MARBLE["id"], SRC_EIA["id"], SRC_CABLES["id"], *STATUS_LANES.values(),
         SRC_FAA["id"], SRC_BLUESKY["id"], SRC_GDELT["id"], SRC_NETBLOCKS["id"], SRC_SATCOM["id"]]
# Problems that are not a source read: tips cite other programs' items, Citizen Lab only names OONI's test lists, and
# these two are notes about one record's place, not a failed read.
# trends reads IODA and Radar a second time, for the 60-day series (trends.json): its failure leaves the series' own
# fields null and says so there, and never marks the live lanes failed.
NOT_A_LANE = ("tips", "citizenlab", "trends")
DATA_NOTES = (("odin:odin-county", "not in the Gazetteer"), ("eia:eia-places", "has no point in eia.PLACES"),
              ("faa", "the FAA's page says NO Advisory"), ("iaea:iaea-rss", iaea.RSS_NOTE))


def problem_lanes(problem: dict) -> set[str] | None:
    """The lanes a collection problem means were not fully read: a set (maybe empty), or None for every lane."""
    src = str(problem.get("source") or "")
    head, _, rest = src.partition(":")
    if head in NOT_A_LANE:
        return set()
    if any(src == s and text in str(problem.get("error") or "") for s, text in DATA_NOTES):
        return set()
    if head in ("ioda", "ioda-asn"):
        return {SRC_IODA["id"]}           # IODA's country outages and its watched networks share the id
    if head == "ooni":
        return {SRC_OONI["id"]}
    if head == "radar":
        return {SRC_RADAR_BGP["id"]} if rest.startswith("bgp") else {SRC_RADAR["id"]}
    if head == "status":
        if not rest:                      # the whole status collector failed
            return set(STATUS_LANES.values())
        return {STATUS_LANES[rest]} if rest in STATUS_LANES else None
    simple = {"iaea": SRC_IAEA, "stop": SRC_STOP, "odin": SRC_ODIN, "blackmarble": SRC_BLACK_MARBLE, "eia": SRC_EIA,
              "faa": SRC_FAA, "bluesky": SRC_BLUESKY, "gdelt": SRC_GDELT, "netblocks": SRC_NETBLOCKS,
              "satcom": SRC_SATCOM}
    if head in simple:
        return {simple[head]["id"]}
    return None


def _problem_text(p: dict) -> str:
    return f"{p.get('source')}: {p.get('error')}"[:300]


def source_health(snapshot: dict) -> dict:
    """{lane: {state, checked_at, last_ok, last_error, failures, problems, detail?}} for this run: the kit's revision
    2.1 health shape (state fresh or failing; failures = failed runs in a row, carried from the previous published
    feed), with Blackout's older `problems` (problems placed on the lane this run) and `why` kept beside it. A failed
    lane keeps the last success the previous published feed recorded (collection.last_ok), or null when none is known.
    last_error is the same text the page lists under collection.problems."""
    generated = snapshot["generated_at"]
    collection = snapshot.get("collection") or {}
    previous = collection.get("last_ok") or {}
    prior_failures = collection.get("failures") or {}
    hit: dict[str, int] = {}
    errors: dict[str, list[str]] = {}
    unplaced: list[str] = []
    for p in collection.get("problems") or []:
        lanes = problem_lanes(p)
        if lanes is None:
            unplaced.append(_problem_text(p))
            continue
        for lane in lanes:
            hit[lane] = hit.get(lane, 0) + 1
            errors.setdefault(lane, []).append(_problem_text(p))
    out = {}
    for lane in LANES:
        failed = bool(unplaced) or lane in hit
        prior = prior_failures.get(lane)
        prior = prior if isinstance(prior, int) and not isinstance(prior, bool) and prior >= 0 else 0
        text = "; ".join((errors.get(lane) or unplaced)[:3]) if failed else None
        out[lane] = {"state": "failing" if failed else "fresh",
                     "checked_at": generated,
                     "last_ok": previous.get(lane) if failed else generated,
                     "last_error": text,
                     "failures": prior + 1 if failed else 0,
                     "problems": hit.get(lane, 0)}
        if unplaced and lane not in hit:
            out[lane]["detail"] = out[lane]["why"] = "a collection problem Blackout could not place on a source"
    return out


def _place_and_lane(features: list[dict]) -> None:
    """sea_areas for every placed item (the kit's gazetteer; [] on land) and sources[0].lane, the item's lane key in
    the header's health, so the history log and COP's ledger never fall back to guessing it from the source id."""
    lanes = set(LANES)
    for f in features:
        props = f["properties"]
        if f.get("geometry"):
            props["sea_areas"] = geo.sea_areas_for_geometry(f["geometry"])
        srcs = props.get("sources") or []
        if srcs and srcs[0].get("id") in lanes:
            srcs[0]["lane"] = srcs[0]["id"]


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
        _rev21(props, f"blackout:corridor:{ca['corridor_id']}", start=ca.get("start_time"),
               signal={"name": "mean IODA signal in the corridor's countries against their baselines",
                       "value": round(100 - ca["mean_drop_pct"], 1), "unit": "%", "expected": 100, "tail": "low"}
               if isinstance(ca.get("mean_drop_pct"), (int, float)) else None, basis="location")
        geom = {"type": "Point", "coordinates": [ca["lon"], ca["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    # 2. Country connectivity outages (BO-WP-1)
    judged_ccs = set()
    for o in outages:
        cc = o["country"]
        ind_id = o.get("indicator_id")
        if not ind_id:  # 20-25% drops: used for corridor correlation (BO-IND-2), not published on their own
            continue
        win = o.get("ooni_window")
        corridor = corridor_by_cc.get(cc)
        srcs = [{**SRC_IODA, "accessed_at": generated}]
        # A shutdown reading needs IODA and OONI together, and the OONI line must move with the event: blocking in the
        # outage's own hours at BO-IND-3's line and at least twice the same hours' rate over the 5 days before. A
        # country's standing censorship rate is not evidence about this outage (15 Nov 2026 review). The reading is
        # published as an observation with no probability until Verdict has scored the rule (Tom, 16 Nov 2026).
        supported = ind_id == "BO-IND-1" and not corridor and bool(win) and win.get("rising_blocking")
        if win:
            srcs.append({**SRC_OONI, "accessed_at": generated})
        if supported:
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
            summary += _ooni_window_text(o, win)
            if not supported:
                direction = "neutral"

        cor = corroboration(snapshot, cc, o.get("start_time"))
        summary += corroboration_text(cor, "ioda")
        pon = percent_of_normal(snapshot, o)
        exp = itu.exposure(cc)
        summary += impact_text(pon, exp, o["country_name"])
        if exp:
            srcs.append({**SRC_ITU, "accessed_at": generated})
        history = stop.context(snapshot.get("stop"), cc)
        if history:  # Access Now's record of shutdowns here: context only, never part of the judgment
            summary += history
            srcs.append({**SRC_STOP, "accessed_at": generated})
        props = {
            "contract": CONTRACT,
            "id": f"blackout:outage:{cc.lower()}:{o['start_time'][:13]}",
            "program": "blackout",
            "title": (f"{o['country_name']}: single-signal dip, {o['drop_pct']}% ({o['datasource']})"
                      if ind_id == "BO-IND-4" else
                      f"{o['country_name']}: {o['drop_pct']}% connectivity drop ({o['datasource']})"),
            "summary": summary,
            "kind": "observation",
            "tier": "routine" if ind_id == "BO-IND-4" else "anomaly",
            "status": status,
            "msg_type": "new",
            "human_review": "none",
            "us_person_data": "none",
            "status_class": "reported",
            "product_category": "current",
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
            # The impact measure (percent of normal) and the ceiling it applies to (exposure, an upper bound).
            "percent_of_normal": pon,
            "exposure": exp,
        }
        props["baseline_ref"] = OUTAGE_BASELINE
        # IODA gives the event's start; its duration runs to the last look, not a stated end, so no event_end.
        _rev21(props, f"blackout:outage:{cc.upper()}", start=o.get("start_time"),
               signal={"name": "IODA signal in the event against the same hours of the 7 days before (the largest "
                               "drop of BGP, active probing and darknet)",
                       "value": round(100 - o["drop_pct"], 1), "unit": "%", "expected": 100, "tail": "low"},
               basis="location")
        geom = {"type": "Point", "coordinates": [o["lon"], o["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    # 3. OONI anomaly rates at or above BO-IND-3 not already folded into an outage reading. A 7-day rate against a
    #    fixed 15% line is a standing condition (Iran, Russia and Myanmar meet it every day), so tier routine.
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
            "tier": "routine",
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
        _rev21(props, f"blackout:ooni:{c['country'].upper()}",
               start=_days_before(c["day"], max(1, int(c.get("window_days") or 1)) - 1), precision="day",
               signal={"name": f"OONI anomaly and confirmed-block rate over {c.get('window_days') or 1} day(s)",
                       "value": round(c["anomaly_rate"] * 100, 1), "unit": "%", "tail": "high"},
               basis="location")
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

    # 5. US aviation system stops (BO-WP-6): ground stops as the FAA's Command Center issues them.
    features += _faa_features(snapshot, generated, valid_until, status)

    # 6. Outages the public and the press report at watched US services (BO-WP-7): counts, never assessed.
    features += _public_features(snapshot, generated, valid_until, status)

    # 7. NetBlocks' own reports (BO-IND-20): quoted with the link, never assessed.
    features += _netblocks_features(snapshot, generated, valid_until, status)

    # 8. Satellite networks' traffic drops against their weekday-matched normal (BO-IND-21): the network's own traffic.
    features += _satcom_features(snapshot, generated, valid_until, status)
    _place_and_lane(features)

    return {
        "type": "FeatureCollection",
        "contract": CONTRACT,
        "contract_rev": CONTRACT_REV,
        "kit": tic_doctrine.__version__,
        "program": "blackout",
        "generated_at": generated,
        "mode": snapshot.get("mode", "seed"),
        # Blackout's `terms` in the kit registry word for word (see LICENCE). OONI: CC BY-NC-SA 4.0,
        # github.com/ooni/license data/LICENSE.md, read 12 Nov 2026. IODA states no data licence on its API page
        # or site, so none is claimed for it.
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "language_key": LANGUAGE_KEY,
        # Which sources were read on this run, by lane (an item's first source id): see source_health.
        "health": source_health(snapshot),
        "features": features,
    }
