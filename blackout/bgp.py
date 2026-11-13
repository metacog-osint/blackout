"""Routing integrity (BO-WP-4): BGP origin hijacks and route leaks from Cloudflare Radar's BGP endpoints.

Cloudflare's own detection, reported, never assessed: Radar says what it saw from its route collectors (RouteViews,
RIPE RIS and its own peers) and gives each hijack a confidence score; it does not say who meant what. Same token as
radar.py (env CLOUDFLARE_RADAR_TOKEN, Radar read); data CC BY-NC 4.0 (developers.cloudflare.com/radar).

Shapes checked against real responses (tests/fixtures/radar/MANIFEST.json, recorded 05:2x UTC 13 Nov 2026):

  bgp/hijacks/events   result.events[]: id, confidence_score, hijacker_asn, hijacker_country, victim_asns,
                       victim_countries, prefixes, peer_ip_count (route-collector peers that saw it), min_hijack_ts,
                       max_hijack_ts, duration (s), tags [{name, score}] (Cloudflare's evidence: RPKI and IRR origin
                       validity, sibling origins), is_stale, on_going_count; result.total_monitors (280 on 13 Nov
                       2026); result.asn_info [{asn, org_name, country_code}]. Confidence: 1-3 low, 4-7 medium, 8 and
                       above high (developers.cloudflare.com/radar/investigate/bgp-anomalies, read 13 Nov 2026).
                       is_stale and on_going_count are not defined there, so Blackout doesn't use them.
  bgp/leaks/events     result.events[]: id, leak_asn, leak_seg (the AS path segment: upstream, leaker, customer),
                       countries (one per leak_seg AS), prefix_count, origin_count, peer_count, min_ts, max_ts,
                       finished, leak_type (1: provider-customer-provider, the only type Radar detects).

Scale on the 3 days to 05:00 UTC 13 Nov 2026: 197 high-confidence hijacks, 1,012 leaks, nearly all a prefix or two
seen by a few peers. Each hijack and leak is counted per country for the page; only events seen widely become feed
items (BO-IND-11, BO-IND-12): a high-confidence hijack seen by at least VISIBLE_PEERS peers (2 of the 197), and a
leak of at least LEAK_PREFIXES prefixes seen by at least VISIBLE_PEERS peers (6 of the 1,012).
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import radar

API = radar.API
HIJACKS_URL = f"{API}/bgp/hijacks/events?dateRange=3d&minConfidence=8&format=json&per_page=100"
LEAKS_URL = f"{API}/bgp/leaks/events?dateRange=3d&format=json&per_page=100"
ROUTING_PAGE = "https://radar.cloudflare.com/routing"
HIGH_CONFIDENCE = 8      # Cloudflare's own "high" band
VISIBLE_PEERS = 25       # route-collector peers that saw the event (of about 280 Radar monitors)
LEAK_PREFIXES = 100
RECENT_HOURS = 72
MAX_PAGES = {"hijacks": 5, "leaks": 15}


def _pages(fetch, base: str, key: str) -> tuple[list[dict], dict, list[dict]]:
    """Every page of one endpoint: (events, result of page 1 without events, problems)."""
    events, first, problems = [], {}, []
    total = None
    for page in range(1, MAX_PAGES[key] + 1):
        data = fetch(f"{base}&page={page}")
        if not isinstance(data, dict) or data.get("success") is not True:
            raise ValueError("Radar did not answer success")
        res = data.get("result") or {}
        rows = res.get("events")
        if not isinstance(rows, list):
            raise ValueError("no result.events list")
        if page == 1:
            first = {k: v for k, v in res.items() if k != "events"}
            total = (data.get("result_info") or {}).get("total_count")
        events += [e for e in rows if isinstance(e, dict) and e.get("id") is not None]
        if len(rows) < 100:
            break
    else:
        if isinstance(total, int) and total > len(events):
            problems.append({"endpoint": f"bgp-{key}",
                             "error": f"read {len(events)} of {total} events ({MAX_PAGES[key]} pages)"})
    return events, first, problems


def _names(info) -> dict[int, dict]:
    out = {}
    for a in info if isinstance(info, list) else []:
        if isinstance(a, dict) and str(a.get("asn") or "").isdigit():
            out[int(a["asn"])] = {"name": str(a.get("org_name") or "").strip() or None,
                                  "country": a.get("country_code") if len(str(a.get("country_code") or "")) == 2
                                  else None}
    return out


def _cc(x) -> str | None:
    return x if isinstance(x, str) and len(x) == 2 and x.isalpha() and x.isupper() else None


def parse_hijacks(events: list[dict], names: dict, now: datetime) -> list[dict]:
    """High-confidence hijacks that are ongoing or ended within RECENT_HOURS, newest first."""
    out = []
    for e in events:
        conf = e.get("confidence_score")
        if not isinstance(conf, (int, float)) or conf < HIGH_CONFIDENCE:
            continue
        start, end = radar._t(e.get("min_hijack_ts")), radar._t(e.get("max_hijack_ts"))
        if start is None or start > now or (end and (now - end).total_seconds() > RECENT_HOURS * 3600):
            continue
        hj = e.get("hijacker_asn")
        victims = [int(v) for v in e.get("victim_asns") or [] if str(v).isdigit()]
        out.append({
            "radar_id": str(e["id"]),
            "confidence": conf,
            "hijacker_asn": int(hj) if str(hj).isdigit() else None,
            "hijacker_name": (names.get(int(hj)) or {}).get("name") if str(hj).isdigit() else None,
            "hijacker_country": _cc(e.get("hijacker_country")),
            "victim_asns": victims,
            "victim_names": [(names.get(v) or {}).get("name") for v in victims],
            "victim_countries": sorted({c for c in (_cc(x) for x in e.get("victim_countries") or []) if c}),
            "prefixes": [p for p in e.get("prefixes") or [] if isinstance(p, str)],
            "peers": e.get("peer_ip_count") if isinstance(e.get("peer_ip_count"), int) else None,
            "evidence": [t["name"] for t in e.get("tags") or []
                         if isinstance(t, dict) and t.get("name") and (t.get("score") or 0) != 0],
            "start": radar._iso(start), "last_seen": radar._iso(end),
            "duration_s": e.get("duration") if isinstance(e.get("duration"), int) else None,
        })
    out.sort(key=lambda x: x["start"], reverse=True)
    return out


def parse_leaks(events: list[dict], names: dict, now: datetime) -> list[dict]:
    """Route leaks that are ongoing (not finished) or ended within RECENT_HOURS, newest first."""
    out = []
    for e in events:
        start, end = radar._t(e.get("min_ts")), radar._t(e.get("max_ts"))
        finished = e.get("finished") is True
        if start is None or start > now:
            continue
        if finished and (end is None or (now - end).total_seconds() > RECENT_HOURS * 3600):
            continue
        seg = [int(a) for a in e.get("leak_seg") or [] if str(a).isdigit()]
        ccs = list(e.get("countries") or [])
        leaker = int(e["leak_asn"]) if str(e.get("leak_asn") or "").isdigit() else None
        where = None
        if leaker in seg and len(ccs) == len(seg):
            where = _cc(ccs[seg.index(leaker)])
        out.append({
            "radar_id": str(e["id"]),
            "leak_asn": leaker,
            "leak_name": (names.get(leaker) or {}).get("name") if leaker else None,
            "leak_country": (where or (names.get(leaker) or {}).get("country")) if leaker else where,
            "segment": seg,
            "segment_names": [(names.get(a) or {}).get("name") for a in seg],
            "countries": sorted({c for c in (_cc(x) for x in ccs) if c}),
            "prefixes": e.get("prefix_count") if isinstance(e.get("prefix_count"), int) else None,
            "origins": e.get("origin_count") if isinstance(e.get("origin_count"), int) else None,
            "peers": e.get("peer_count") if isinstance(e.get("peer_count"), int) else None,
            "finished": finished,
            "start": radar._iso(start), "last_seen": radar._iso(end),
        })
    out.sort(key=lambda x: x["start"], reverse=True)
    return out


def notable_hijack(h: dict) -> bool:
    """BO-IND-11: Cloudflare's high confidence, seen by at least VISIBLE_PEERS route-collector peers."""
    return h["confidence"] >= HIGH_CONFIDENCE and (h.get("peers") or 0) >= VISIBLE_PEERS


def notable_leak(lk: dict) -> bool:
    """BO-IND-12: at least LEAK_PREFIXES prefixes leaked, seen by at least VISIBLE_PEERS peers."""
    return (lk.get("prefixes") or 0) >= LEAK_PREFIXES and (lk.get("peers") or 0) >= VISIBLE_PEERS


def by_country(hijacks: list[dict], leaks: list[dict]) -> list[dict]:
    """Per country: hijacks naming a victim network there, leaks by a network there (the leaker's country)."""
    rows: dict[str, dict] = {}
    for h in hijacks:
        for cc in h["victim_countries"]:
            rows.setdefault(cc, {"country": cc, "hijacks": 0, "leaks": 0})["hijacks"] += 1
    for lk in leaks:
        if lk.get("leak_country"):
            rows.setdefault(lk["leak_country"], {"country": lk["leak_country"], "hijacks": 0, "leaks": 0})["leaks"] += 1
    return sorted(rows.values(), key=lambda r: (-(r["hijacks"] + r["leaks"]), r["country"]))


def fetch_bgp(fetch=radar._get, now: datetime | None = None) -> tuple[dict, list[dict]]:
    """({counts, notable_hijacks, notable_leaks, by_country, monitors, read}, problems). Only the notable events are
    kept in full; the rest are counted. An endpoint that fails publishes nothing and counts nothing."""
    now = now or datetime.now(timezone.utc)
    found: dict = {"hijacks": [], "leaks": []}
    out: dict = {"read": {}, "recent_hours": RECENT_HOURS, "monitors": None,
                 "thresholds": {"high_confidence": HIGH_CONFIDENCE, "visible_peers": VISIBLE_PEERS,
                                "leak_prefixes": LEAK_PREFIXES}}
    problems: list[dict] = []
    for key, url, parse in (("hijacks", HIJACKS_URL, parse_hijacks), ("leaks", LEAKS_URL, parse_leaks)):
        try:
            events, first, probs = _pages(fetch, url, key)
            problems += probs
            found[key] = parse(events, _names(first.get("asn_info")), now)
            if key == "hijacks" and isinstance(first.get("total_monitors"), int):
                out["monitors"] = first["total_monitors"]
            out["read"][key] = True
        except Exception as exc:
            problems.append({"endpoint": f"bgp-{key}", "error": f"{type(exc).__name__}: {exc}"[:200]})
            out["read"][key] = False
    out["counts"] = {"hijacks": len(found["hijacks"]), "leaks": len(found["leaks"])}
    out["by_country"] = by_country(found["hijacks"], found["leaks"])
    out["notable_hijacks"] = [h for h in found["hijacks"] if notable_hijack(h)]
    out["notable_leaks"] = [lk for lk in found["leaks"] if notable_leak(lk)]
    return out, problems
