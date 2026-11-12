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

from blackout import cables, feed, ioda, ooni  # noqa: E402
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
    return {
        "mode": raw.get("mode", "seed"),
        "generated_at": raw.get("generated_at", "2026-11-01T12:00:00Z"),
        "outages": outages,
        "censorship": censorship,
        "corridor_alerts": corridor_alerts,
        "corridors": cables.CABLE_CORRIDORS,
        "tips": tips_out,
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


def build_site(root: Path = ROOT) -> dict[str, str]:
    snap_path = root / "data" / "snapshot.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else build_snapshot()
    feed_doc = feed.build_feed(snap)

    files = {
        "data/snapshot.json": json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
        "data/feed.geojson": json.dumps(feed_doc, indent=2, ensure_ascii=False) + "\n",
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
        print(f"Collected ({snap['mode']}) {len(snap['outages'])} outages, {len(snap['censorship'])} OONI rows, "
              f"{len(snap['corridor_alerts'])} cable alerts, {len(snap['tips'])} suite tips; {len(probs)} problems.")
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
