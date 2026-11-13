"""Blackout CLI: collect, build, and check.

    python tools/blackout.py collect [--live]
    python tools/blackout.py build
    python tools/blackout.py check
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))

from blackout import asn_watch, bgp, cables, feed, iaea, ioda, ooni, radar, status_feeds  # noqa: E402
from tic_doctrine import contract, registers, tips as doctrine_tips  # noqa: E402

# Made-up records for building and testing the pipeline offline. Everything built from them is published with
# status "test" and a banner on the page; `collect --live` never falls back to them.
SEED_SNAPSHOT = {
    "mode": "seed",
    "generated_at": "2026-11-01T12:00:00Z",
    "raw_outages": [
        {
            "id": "ioda-ye-2026-11-01T08",
            "country": "YE",
            "asn": "AS30873",
            "asn_name": "Public Telecommunication Corporation (YemenNet)",
            "bgp_drop_pct": 58.4,
            "probing_drop_pct": 54.1,
            "duration_minutes": 210,
            "start_time": "2026-11-01T08:00:00Z",
        },
        {
            "id": "ioda-dj-2026-11-01T08",
            "country": "DJ",
            "asn": "AS30990",
            "asn_name": "Djibouti Telecom SA",
            "bgp_drop_pct": 28.5,
            "probing_drop_pct": 26.2,
            "duration_minutes": 180,
            "start_time": "2026-11-01T08:15:00Z",
        },
        {
            "id": "ioda-sd-2026-11-01T05",
            "country": "SD",
            "asn": "AS37197",
            "asn_name": "Sudatel Telecom Group",
            "bgp_drop_pct": 46.0,
            "probing_drop_pct": 43.8,
            "duration_minutes": 360,
            "start_time": "2026-11-01T05:00:00Z",
        },
        {
            "id": "ioda-ir-2026-11-01T09",
            "country": "IR",
            "asn": "AS44244",
            "asn_name": "Iran Cell Service and Communication Company",
            "bgp_drop_pct": 31.2,
            "probing_drop_pct": 29.0,
            "duration_minutes": 120,
            "start_time": "2026-11-01T09:00:00Z",
        },
    ],
    "raw_ooni": [
        {
            "probe_cc": "IR",
            "day": "2026-11-01",
            "test_name": "web_connectivity,telegram,signal",
            "measurement_count": 4820,
            "confirmed_count": 940,
            "anomaly_count": 610,
        },
        {
            "probe_cc": "RU",
            "day": "2026-11-01",
            "test_name": "web_connectivity,tor,psiphon",
            "measurement_count": 6150,
            "confirmed_count": 820,
            "anomaly_count": 530,
        },
        {
            "probe_cc": "MM",
            "day": "2026-11-01",
            "test_name": "web_connectivity,telegram,whatsapp",
            "measurement_count": 1120,
            "confirmed_count": 210,
            "anomaly_count": 145,
        },
    ],
    # No seed tips: Wake is not deployed and Elements has nothing to replay here. Tips come only from a live run.
    "raw_tips": [],
    # No seed provider data either: a made-up incident at a real provider is too easy to mistake for a real one.
    # The provider watch fills only in a live run (or a fixture replay in the tests).
}


def build_snapshot(raw: dict | None = None) -> dict:
    """Normalize raw collector output. Outages and live IODA records arrive per country; tips keep their geometry
    only long enough to measure corridor distances."""
    raw = raw or SEED_SNAPSHOT
    raw_tips = list(raw.get("raw_tips", []))
    outages = raw["outages"] if "outages" in raw else ioda.normalize_ioda_events(raw.get("raw_outages", []))
    for o in outages:
        o["related_tips"] = cables.related_tips(o, raw_tips)
    censorship = ooni.normalize_ooni_rows(raw.get("raw_ooni", []))
    corridor_alerts = cables.correlate_cable_corridors(outages, tips=raw_tips)
    tips_out = []
    for t in raw_tips:
        pts = cables.tip_points(t)
        clean = {k: v for k, v in t.items() if k not in ("_geometry", "geometry")}
        if pts:  # one point for the map; distances were measured on the full geometry above
            clean["lat"], clean["lon"] = pts[0]
        tips_out.append(clean)
    asn_records = list(raw.get("asn_watch") or [])
    incidents = list(raw.get("provider_incidents") or [])
    generated = raw.get("generated_at", "2026-11-01T12:00:00Z")
    return {
        "mode": raw.get("mode", "seed"),
        "generated_at": generated,
        "outages": outages,
        "censorship": censorship,
        "corridor_alerts": corridor_alerts,
        "corridors": cables.CABLE_CORRIDORS,
        "tips": tips_out,
        "asn_watchlist": [{"asn": f"AS{w['asn']}", "provider": w["provider"], "label": w["label"]}
                          for w in asn_watch.WATCHLIST],
        "asn_watch": asn_records,
        "provider_feeds": list(raw.get("provider_feeds") or []),
        "provider_incidents": incidents,
        "provider_concurrency": status_feeds.concurrency(asn_records, incidents, generated),
        "radar": raw.get("radar"),
        "bgp": raw.get("bgp"),
        "znpp": raw.get("znpp"),
        "collection": raw.get("collection", {}),
    }


def collect(live: bool = False) -> dict:
    """Seed (offline test) or live collection. A live run starts empty: a source that fails publishes nothing and is
    listed under collection.problems, so the page shows no data for it rather than made-up data."""
    if not live:
        snap = json.loads(json.dumps(SEED_SNAPSHOT))
    else:
        now = datetime.now(timezone.utc)
        snap = {"mode": "live", "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "raw_ooni": [], "raw_tips": [],
                "outages": [], "collection": {"problems": []}}
        problems = snap["collection"]["problems"]
        try:
            snap["outages"], stats = ioda.fetch_ioda_live()
            problems += [{"source": "ioda", **p} for p in stats.pop("problems")]
            snap["collection"]["ioda"] = stats
        except Exception as exc:
            print(f"[blackout:collect] IODA failed; no outages published: {exc}", file=sys.stderr)
            problems.append({"source": "ioda", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["raw_ooni"], ooni_problems = ooni.fetch_ooni_live()
            problems += [{"source": "ooni", **p} for p in ooni_problems]
        except Exception as exc:
            print(f"[blackout:collect] OONI failed; no OONI rows published: {exc}", file=sys.stderr)
            problems.append({"source": "ooni", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["asn_watch"], asn_problems = asn_watch.fetch_asn_watch()
            problems += [{"source": "ioda-asn", **p} for p in asn_problems]
        except Exception as exc:
            print(f"[blackout:collect] IODA ASN watch failed; nothing published for it: {exc}", file=sys.stderr)
            problems.append({"source": "ioda-asn", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["provider_incidents"], snap["provider_feeds"], feed_problems = status_feeds.fetch_status_feeds()
            problems += [{"source": f"status:{p['feed']}", **p} for p in feed_problems]
        except Exception as exc:
            print(f"[blackout:collect] provider status feeds failed; none published: {exc}", file=sys.stderr)
            problems.append({"source": "status", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["radar"], radar_problems = radar.fetch_radar()
            problems += [{"source": f"radar:{p['endpoint']}", "error": p["error"]} for p in radar_problems]
        except Exception as exc:
            print(f"[blackout:collect] Cloudflare Radar failed; nothing published from it: {exc}", file=sys.stderr)
            problems.append({"source": "radar", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["bgp"], bgp_problems = bgp.fetch_bgp()
            problems += [{"source": f"radar:{p['endpoint']}", "error": p["error"]} for p in bgp_problems]
        except Exception as exc:
            print(f"[blackout:collect] Radar BGP failed; nothing published from it: {exc}", file=sys.stderr)
            problems.append({"source": "radar:bgp", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["znpp"], znpp_problems = iaea.fetch_znpp()
            problems += [{"source": f"iaea:{p['endpoint']}", "error": p["error"]} for p in znpp_problems]
        except Exception as exc:
            print(f"[blackout:collect] IAEA updates failed; nothing published from them: {exc}", file=sys.stderr)
            problems.append({"source": "iaea", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            interests_doc = json.loads((ROOT / "doctrine" / "interests.json").read_text(encoding="utf-8"))
            items, tip_problems = doctrine_tips.gather(interests_doc)
            problems += [{"source": f"tips:{p.get('source')}", "error": p.get("error")} for p in tip_problems]
            geom_by_id = {i.get("id"): i.get("_geometry") for i in items if i.get("id")}
            found = doctrine_tips.tips(items, interests_doc)
            for t in found:  # the kit's tips carry no location; take it from the producer's own item
                if geom_by_id.get(t.get("item_id")):
                    t["_geometry"] = geom_by_id[t["item_id"]]
            snap["raw_tips"] = found
        except Exception as exc:
            print(f"[blackout:collect] suite tips failed; none used: {exc}", file=sys.stderr)
            problems.append({"source": "tips", "error": f"{type(exc).__name__}: {exc}"[:200]})
    normalized = build_snapshot(snap)
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "snapshot.json").write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return normalized


def _compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def world_shapes(countries: dict, step: float = 0.1) -> dict:
    """Every country outline from the kit's Natural Earth admin-0 file (public domain), snapped to `step` degrees:
    the page's land at world zoom and its whole base map when OpenFreeMap can't be reached (as COP does)."""
    feats = []
    for c in countries["countries"]:
        polys = []
        for ring in c["rings"]:
            out = []
            for x, y in ring:
                pt = [round(round(x / step) * step, 2), round(round(y / step) * step, 2)]
                if not out or out[-1] != pt:
                    out.append(pt)
            if len(out) >= 4:
                if out[0] != out[-1]:
                    out.append(out[0])
                polys.append([out])
        if polys:
            feats.append({"type": "Feature", "properties": {"cc": c["cc"]},
                          "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    return {"type": "FeatureCollection", "source": countries["source"] + f"; snapped to {step} degrees for world zoom",
            "features": feats}


def map_countries(snap: dict) -> set[str]:
    """Countries the page may shade or outline: published IODA outages, every OONI row, Cloudflare Radar items."""
    ccs = {o["country"] for o in snap.get("outages") or [] if o.get("indicator_id")}
    ccs |= {c["country"] for c in snap.get("censorship") or []}
    r = snap.get("radar") or {}
    ccs |= {cc for a in r.get("annotations") or [] for cc in a.get("countries") or []}
    ccs |= {a["country"] for a in r.get("anomalies") or [] if a.get("country")}
    b = snap.get("bgp") or {}
    ccs |= {cc for h in b.get("notable_hijacks") or [] for cc in h.get("victim_countries") or []}
    ccs |= {lk["leak_country"] for lk in b.get("notable_leaks") or [] if lk.get("leak_country")}
    if snap.get("znpp"):
        ccs.add("UA")
    return ccs


def country_shapes(countries: dict, wanted: set[str]) -> dict:
    """Full-resolution outlines (the kit's own thinning) of only the countries the page draws, with their bounds."""
    out = [{"type": "Feature", "properties": {"cc": c["cc"], "name": c["name"], "bbox": c["bbox"]},
            "geometry": {"type": "MultiPolygon", "coordinates": [[ring] for ring in c["rings"]]}}
           for c in countries["countries"] if c["cc"] in wanted]
    return {"type": "FeatureCollection", "source": countries["source"], "features": out}


def method_doc(root: Path) -> dict:
    """The registers the page's Method tab lists: indicators, warning problems and sources with their licences."""
    ind = json.loads((root / "doctrine" / "indicators.json").read_text(encoding="utf-8"))
    src = json.loads((root / "doctrine" / "sources.json").read_text(encoding="utf-8"))
    return {
        "indicators": [{"id": i["id"], "text": i["text"]} for i in ind["indicators"]],
        "warning_problems": [{"id": w["id"], "title": w["title"]} for w in ind["warning_problems"]],
        "sources": [{k: s.get(k) for k in ("id", "name", "url", "licence", "grade", "use")} for s in src["sources"]],
    }


def build_site(root: Path = ROOT) -> dict[str, str]:
    snap_path = root / "data" / "snapshot.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else build_snapshot()
    feed_doc = feed.build_feed(snap)
    countries = json.loads((root / "doctrine" / "kit" / "geo" / "countries.json").read_text(encoding="utf-8"))

    files = {
        "data/snapshot.json": json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
        "data/feed.geojson": json.dumps(feed_doc, indent=2, ensure_ascii=False) + "\n",
        "data/world.geojson": _compact(world_shapes(countries)),
        "data/countries.geojson": _compact(country_shapes(countries, map_countries(snap))),
        "data/method.json": json.dumps(method_doc(root), indent=1, ensure_ascii=False) + "\n",
    }
    src_dir = root / "site_src"
    if src_dir.exists():
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                files[p.relative_to(src_dir).as_posix()] = p.read_text(encoding="utf-8")

    out_dir = root / "site"
    out_dir.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
    return files


def check_all(root: Path = ROOT) -> list[str]:
    errs = registers.check(root / "doctrine")
    regs = registers.ids(root / "doctrine")
    feed_path = root / "site" / "data" / "feed.geojson"
    if not feed_path.exists():
        build_site(root)
    feed_doc = json.loads(feed_path.read_text(encoding="utf-8"))
    for feat in feed_doc.get("features", []):
        fid = (feat.get("properties") or {}).get("id", "?")
        for std, sev, msg in contract.check(feat, regs):
            if sev == "error":
                errs.append(f"{fid}: {std}: {msg}")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Blackout connectivity & cable outage monitor.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c_col = sub.add_parser("collect")
    c_col.add_argument("--live", action="store_true")
    sub.add_parser("build")
    sub.add_parser("check")
    args = ap.parse_args(argv)

    if args.cmd == "collect":
        snap = collect(live=args.live)
        probs = snap.get("collection", {}).get("problems", [])
        drops = sum(1 for w in snap["asn_watch"] if w.get("indicator_id"))
        print(f"Collected ({snap['mode']}) {len(snap['outages'])} outages, {len(snap['censorship'])} OONI rows, "
              f"{len(snap['corridor_alerts'])} cable alerts, {len(snap['tips'])} suite tips, "
              f"{len(snap['asn_watch'])}/{len(snap['asn_watchlist'])} watched networks read ({drops} with BO-IND-6), "
              f"{len(snap['provider_incidents'])} open provider incidents, "
              f"{len((snap.get('radar') or {}).get('annotations') or [])} Radar outages and "
              f"{len((snap.get('radar') or {}).get('anomalies') or [])} verified Radar anomalies, "
              f"{len((snap.get('bgp') or {}).get('notable_hijacks') or [])} widely seen BGP hijacks and "
              f"{len((snap.get('bgp') or {}).get('notable_leaks') or [])} large route leaks; "
              f"{len(probs)} problems.")
        return 0
    if args.cmd == "build":
        files = build_site(ROOT)
        print(f"Built site/ ({len(files)} files).")
        return 0
    if args.cmd == "check":
        errs = check_all(ROOT)
        for e in errs:
            print("  -", e)
        print(f"Blackout check: {'PASS' if not errs else f'FAIL ({len(errs)} problems)'}")
        return 1 if errs else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
