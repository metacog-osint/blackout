"""Blackout CLI: collect, build, and check.

    python tools/blackout.py collect [--live]
    python tools/blackout.py build
    python tools/blackout.py check
    python tools/blackout.py archive     # live data to history/ (retention archives)
    python tools/blackout.py archive-full --dir D   # NetBlocks posts with excerpts into D (the private archive's copy)
    python tools/blackout.py retire-legacy --dir D  # remove the old public history/netblocks-posts.jsonl once D holds it
"""
from __future__ import annotations

import argparse
import json
import urllib.request
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))

from blackout import archive, asn_watch, bgp, blackmarble, bluesky, cables, citizenlab, eaglei, eia, faa, feed, gdelt, iaea, ioda, itu, netblocks, odin, ooni, radar, satcom, status_feeds, stop, trends  # noqa: E402
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


# Black Marble nights already read are carried from the last published snapshot (Blackout's own earlier
# measurements), so a run reads only the nights it doesn't hold, at most NIGHTS_PER_RUN of them; a first run fills
# the 14-night baseline over a few runs rather than all at once.
NIGHTS_PER_RUN = 4


def _published_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Blackout/0.2 (+https://theintelligence.company)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read(50_000_000).decode("utf-8"))


def previous_health(url: str = f"{feed.SITE}/data/feed.geojson") -> dict:
    """{lane: health entry} from the last published live feed, so a lane that fails now keeps its last success and
    counts its failed runs in a row."""
    try:
        doc = _published_json(url)
    except Exception as exc:
        print(f"[blackout:collect] last published feed not read; a failed source's last success is unknown: {exc}",
              file=sys.stderr)
        return {}
    if doc.get("mode") != "live":
        return {}
    return {k: v for k, v in (doc.get("health") or {}).items() if isinstance(v, dict)}


def previous_last_ok(url: str = f"{feed.SITE}/data/feed.geojson") -> dict:
    """{lane: last_ok} from the last published feed's health block."""
    return {k: v["last_ok"] for k, v in previous_health(url).items() if v.get("last_ok")}


def previous_nights(url: str = f"{feed.SITE}/data/snapshot.json") -> dict | None:
    try:
        snap = _published_json(url)
    except Exception as exc:
        print(f"[blackout:collect] last published snapshot not read; Black Marble starts afresh: {exc}",
              file=sys.stderr)
        return None
    if snap.get("mode") != "live":
        return None
    return {c["id"]: c.get("nights") or [] for c in (snap.get("nightlights") or {}).get("cities") or []}


# EIA-930 hours past EIA's correction period are carried the same way, from their own published file (kept out of
# snapshot.json, which the page loads), so a run reads live only the newest days and any older day the file lacks.
def previous_eia_hours(url: str = f"{feed.SITE}/data/{eia.CARRY_NAME}") -> dict | None:
    try:
        doc = _published_json(url)
    except Exception as exc:
        print(f"[blackout:collect] last published EIA-930 hours not read; every day is read live: {exc}",
              file=sys.stderr)
        return None
    if not isinstance(doc, dict) or doc.get("format") != eia.CARRY_FORMAT or doc.get("mode") != "live":
        return None
    return doc


# GDELT's per-file matches are carried the same way (data/gdelt-counts.json), so a run reads only the GKG files it
# doesn't hold instead of 7 days of them.
def previous_gdelt_counts(url: str = f"{feed.SITE}/data/{gdelt.CARRY_NAME}") -> dict | None:
    try:
        doc = _published_json(url)
    except Exception as exc:
        print(f"[blackout:collect] last published GDELT counts not read; the usual starts afresh: {exc}",
              file=sys.stderr)
        return None
    if not isinstance(doc, dict) or doc.get("format") != gdelt.CARRY_FORMAT or doc.get("mode") != "live":
        return None
    return doc


NEW_GDELT_NAME = "gdelt-new.json"  # data/, git-ignored, not published: the files read this run, for `archive`


def archive_all(root: Path = ROOT) -> list[str]:
    """Run the retention archives (blackout/archive.py) from data/ into history/. Returns the errors; every archive is
    tried even if one fails. Nothing is written for a seed build."""
    data, hist = root / "data", root / "history"
    snap_path = data / "snapshot.json"
    if not snap_path.exists():
        return [f"{snap_path} not found: nothing to archive"]
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    if snap.get("mode") != "live":
        print("[blackout:archive] seed build: nothing archived")
        return []

    def load(name):
        p = data / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    errs = []
    steps = [("Black Marble nights", lambda: archive.archive_nights(hist, snap)),
             ("ODIN counties", lambda: archive.archive_odin(hist, snap)),
             ("Bluesky counts", lambda: archive.archive_bluesky(hist, snap)),
             ("GDELT files", lambda: archive.archive_gdelt(
                 hist, (load(NEW_GDELT_NAME) or {}).get("files"), (load(gdelt.CARRY_NAME) or {}).get("files"),
                 sorted(set((load(NEW_GDELT_NAME) or {}).get("missing") or [])
                        | set((load(gdelt.CARRY_NAME) or {}).get("missing") or [])))),
             ("trend days", lambda: archive.archive_trends(hist, load(trends.NAME))),
             ("NetBlocks posts", lambda: archive.archive_netblocks(hist, load(netblocks.NAME)))]
    for label, fn in steps:
        try:
            print(f"[blackout:archive] {label}: {fn()} lines written")
        except Exception as exc:  # noqa: BLE001 - reported, and the other archives still run
            errs.append(f"{label}: {type(exc).__name__}: {exc}")
    return errs


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
    generated = raw.get("generated_at", "2026-11-01T12:00:00Z")
    incidents = status_feeds.mark_quiet(list(raw.get("provider_incidents") or []), generated)
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
        "stop": raw.get("stop"),
        "internet_use": itu.snapshot_part(),  # ITU, annual, from the committed table: context only
        # Satellite networks: the whole register (page-safe fields) and, on live runs, each one's traffic now.
        "satcom": satcom.snapshot_part(traffic=raw.get("satcom_traffic")),
        # Each ODIN state gets EAGLE-I's base rate for its month (blackout/eaglei_rates.json; None if absent).
        "odin": eaglei.attach(json.loads(json.dumps(raw["odin"]))) if raw.get("odin") else None,
        "nightlights": raw.get("nightlights"),
        # The carried EIA-930 hours go to their own file (collect), never into the snapshot.
        "eia": {k: v for k, v in raw["eia"].items() if k != eia.CARRY_KEY} if raw.get("eia") else None,
        "testlists": raw.get("testlists"),
        "faa": raw.get("faa"),  # FAA ATCSCC ground stops (BO-IND-17); live runs only
        "bluesky": raw.get("bluesky"),  # public Bluesky post counts (BO-IND-18); live runs only
        # GDELT outage headlines (BO-IND-19); the carried per-file matches go to their own file, never the snapshot.
        "gdelt": {k: v for k, v in raw["gdelt"].items() if k not in (gdelt.CARRY_KEY, gdelt.NEW_KEY)} if raw.get("gdelt") else None,
        "netblocks": raw.get("netblocks"),  # NetBlocks' recent posts, parsed (BO-IND-20); live runs only
        "collection": raw.get("collection", {}),
    }


def collect(live: bool = False) -> dict:
    """Seed (offline test) or live collection. A live run starts empty: a source that fails publishes nothing and is
    listed under collection.problems, so the page shows no data for it rather than made-up data."""
    eia_hours = None  # the EIA-930 hours to carry to the next run (data/eia-hours.json); live runs only
    gdelt_counts = None  # the GDELT per-file matches to carry (data/gdelt-counts.json); live runs only
    gdelt_new = None  # the GDELT files read this run, in full, for the retention archive (never published)
    trends_doc = None  # the 60-day daily series per country (data/trends.json); live runs only
    netblocks_doc = None  # every NetBlocks post, parsed (data/netblocks.json); live runs only
    if not live:
        snap = json.loads(json.dumps(SEED_SNAPSHOT))
    else:
        now = datetime.now(timezone.utc)
        snap = {"mode": "live", "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "raw_ooni": [], "raw_tips": [],
                "outages": [], "collection": {"problems": []}}
        prev = previous_health()
        snap["collection"]["last_ok"] = {k: v["last_ok"] for k, v in prev.items() if v.get("last_ok")}
        snap["collection"]["failures"] = {k: v["failures"] for k, v in prev.items()
                                          if isinstance(v.get("failures"), int)}
        problems = snap["collection"]["problems"]
        try:
            snap["outages"], stats = ioda.fetch_ioda_live()
            problems += [{"source": "ioda", **p} for p in stats.pop("problems")]
            snap["collection"]["ioda"] = stats
            # OONI in each BO-IND-1 outage's own country and hours: the event-tied second line (ooni.outage_window).
            for o in [o for o in snap["outages"] if o.get("indicator_id") == "BO-IND-1"][:10]:
                try:
                    o["ooni_window"] = ooni.fetch_outage_window(o["country"], o["start_time"], o["duration_minutes"],
                                                                now=now)
                except Exception as exc:  # not read: the feed says so, never "no blocking"
                    problems.append({"source": "ooni", "country": o["country"],
                                     "error": f"outage window: {type(exc).__name__}: {exc}"[:200]})
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
        satcom_daily = None  # each satellite network's daily percent of normal, for trends.json only
        try:  # satellite networks' traffic against their own 28-day normal (blackout/satcom.py); measurements only
            first_day = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=trends.DAYS - 1)
            snap["satcom_traffic"], satcom_daily, sat_problems = satcom.fetch_traffic(now, trend_first=first_day)
            problems += sat_problems
        except Exception as exc:
            print(f"[blackout:collect] SATCOM lane failed; no satellite readings: {exc}", file=sys.stderr)
            problems.append({"source": "satcom", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["znpp"], znpp_problems = iaea.fetch_znpp()
            problems += [{"source": f"iaea:{p['endpoint']}", "error": p["error"]} for p in znpp_problems]
        except Exception as exc:
            print(f"[blackout:collect] IAEA updates failed; nothing published from them: {exc}", file=sys.stderr)
            problems.append({"source": "iaea", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["stop"], stop_problems = stop.fetch_stop()
            problems += [{"source": "stop", "error": p["error"]} for p in stop_problems]
        except Exception as exc:
            print(f"[blackout:collect] STOP sheet failed; no shutdown history: {exc}", file=sys.stderr)
            problems.append({"source": "stop", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["odin"], odin_problems = odin.fetch_odin()
            problems += [{"source": f"odin:{p['endpoint']}", "error": p["error"]} for p in odin_problems]
        except Exception as exc:
            print(f"[blackout:collect] ODIN failed; no US county outages: {exc}", file=sys.stderr)
            problems.append({"source": "odin", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["nightlights"], bm_problems = blackmarble.fetch_blackmarble(history=previous_nights(),
                                                                             max_new=NIGHTS_PER_RUN)
            problems += [{"source": f"blackmarble:{p['endpoint']}", "error": p["error"]} for p in bm_problems]
        except Exception as exc:
            print(f"[blackout:collect] Black Marble failed; no night lights: {exc}", file=sys.stderr)
            problems.append({"source": "blackmarble", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            eia_hours = previous_eia_hours()  # kept as it was if this run reads nothing
            snap["eia"], eia_problems = eia.fetch_eia(carry=eia_hours)
            eia_hours = (snap["eia"] or {}).pop(eia.CARRY_KEY, None) or eia_hours
            problems += [{"source": f"eia:{p['endpoint']}", "error": p["error"]} for p in eia_problems]
        except Exception as exc:
            print(f"[blackout:collect] EIA-930 failed; no US demand gaps: {exc}", file=sys.stderr)
            problems.append({"source": "eia", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["faa"], faa_problems = faa.fetch_faa(now)
            problems += [{"source": "faa", "error": f"{p['endpoint']}: {p['error']}"} for p in faa_problems]
        except Exception as exc:
            print(f"[blackout:collect] FAA advisories failed; no ground stops: {exc}", file=sys.stderr)
            problems.append({"source": "faa", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            snap["bluesky"], bs_problems = bluesky.fetch_bluesky(now)
            problems += [{"source": f"bluesky:{p['endpoint']}", "error": p["error"]} for p in bs_problems]
        except Exception as exc:
            print(f"[blackout:collect] Bluesky failed; no public post counts: {exc}", file=sys.stderr)
            problems.append({"source": "bluesky", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            gdelt_counts = previous_gdelt_counts()  # kept as it was if this run reads nothing
            snap["gdelt"], gd_problems = gdelt.fetch_gdelt(now, carry=gdelt_counts)
            gdelt_counts = (snap["gdelt"] or {}).pop(gdelt.CARRY_KEY, None) or gdelt_counts
            gdelt_new = (snap["gdelt"] or {}).pop(gdelt.NEW_KEY, None)
            problems += [{"source": f"gdelt:{p['endpoint']}", "error": p["error"]} for p in gd_problems]
        except Exception as exc:
            print(f"[blackout:collect] GDELT failed; no news headline counts: {exc}", file=sys.stderr)
            problems.append({"source": "gdelt", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:  # what OONI tested: Citizen Lab's lists for the OONI countries and any country with an outage
            ccs = {(r.get("probe_cc") or r.get("country") or "") for r in snap.get("raw_ooni") or []}
            ccs |= {o.get("country") or "" for o in snap.get("raw_outages") or []}
            snap["testlists"], cl_problems = citizenlab.fetch_testlists(ccs)
            problems += [{"source": "citizenlab", "error": p["error"]} for p in cl_problems]
        except Exception as exc:
            print(f"[blackout:collect] Citizen Lab test lists failed; none shown: {exc}", file=sys.stderr)
            problems.append({"source": "citizenlab", "error": f"{type(exc).__name__}: {exc}"[:200]})
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
        nb_posts = None
        try:  # NetBlocks' whole public Bluesky account, parsed; the archive keeps posts the account later drops
            posts, nb_problems = netblocks.fetch()
            problems += [{"source": f"netblocks:{p['endpoint']}", "error": p["error"]} for p in nb_problems]
            kept = archive.netblocks_kept(ROOT / "history")
            netblocks_doc = netblocks.document(posts, nb_problems, now, kept=kept)
            snap["netblocks"] = netblocks.snapshot_part(netblocks_doc, now)
            nb_posts = netblocks_doc["posts"] if netblocks_doc["complete"] else None
        except Exception as exc:
            print(f"[blackout:collect] NetBlocks failed; no NetBlocks reports: {exc}", file=sys.stderr)
            problems.append({"source": "netblocks", "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:  # the daily series per country, read again from IODA's and Radar's own archives (blackout/trends.py)
            trends_doc, tr_problems = trends.fetch(now, netblocks_posts=nb_posts, satcom_daily=satcom_daily)
            problems += tr_problems
        except Exception as exc:
            print(f"[blackout:collect] trends failed; no trends.json this run: {exc}", file=sys.stderr)
            problems.append({"source": "trends", "error": f"{type(exc).__name__}: {exc}"[:200]})
    normalized = build_snapshot(snap)
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "snapshot.json").write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    hours_path = data_dir / eia.CARRY_NAME
    if eia_hours:
        hours_path.write_text(_compact(eia_hours), encoding="utf-8", newline="\n")
    elif hours_path.exists():  # never publish an older run's hours beside a new snapshot that has none
        hours_path.unlink()
    counts_path = data_dir / gdelt.CARRY_NAME
    if gdelt_counts:
        counts_path.write_text(_compact(gdelt_counts), encoding="utf-8", newline="\n")
    elif counts_path.exists():
        counts_path.unlink()
    trends_path = data_dir / trends.NAME
    if trends_doc:
        trends_path.write_text(trends.dumps(trends_doc), encoding="utf-8", newline="\n")
    elif trends_path.exists():  # never publish an older run's series beside a new snapshot
        trends_path.unlink()
    nb_path = data_dir / netblocks.NAME
    nb_full = data_dir / netblocks.FULL_NAME  # local only: read by `archive-full`, never copied to site/
    if netblocks_doc:
        nb_path.write_text(_compact(netblocks_doc), encoding="utf-8", newline="\n")
        nb_full.write_text(_compact({"generated_at": netblocks_doc["generated_at"],
                                     "posts": sorted(posts, key=lambda p: p.get("posted_at") or "")}),
                           encoding="utf-8", newline="\n")
    else:
        for p in (nb_path, nb_full):
            if p.exists():
                p.unlink()
    new_path = data_dir / NEW_GDELT_NAME  # local only: read by `archive`, never copied to site/
    if gdelt_new:
        new_path.write_text(_compact(gdelt_new), encoding="utf-8", newline="\n")
    elif new_path.exists():
        new_path.unlink()
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
    # The EIA-930 hours the next run carries (collect wrote them beside the snapshot); published from live runs only.
    hours_src = root / "data" / eia.CARRY_NAME
    if snap.get("mode") == "live" and hours_src.exists():
        files[f"data/{eia.CARRY_NAME}"] = hours_src.read_text(encoding="utf-8")
    elif (root / "site" / "data" / eia.CARRY_NAME).exists():
        (root / "site" / "data" / eia.CARRY_NAME).unlink()
    counts_src = root / "data" / gdelt.CARRY_NAME  # GDELT's per-file matches, likewise
    if snap.get("mode") == "live" and counts_src.exists():
        files[f"data/{gdelt.CARRY_NAME}"] = counts_src.read_text(encoding="utf-8")
    elif (root / "site" / "data" / gdelt.CARRY_NAME).exists():
        (root / "site" / "data" / gdelt.CARRY_NAME).unlink()
    trends_src = root / "data" / trends.NAME  # the daily series per country; published from live runs only
    if snap.get("mode") == "live" and trends_src.exists():
        files[f"data/{trends.NAME}"] = trends_src.read_text(encoding="utf-8")
    elif (root / "site" / "data" / trends.NAME).exists():
        (root / "site" / "data" / trends.NAME).unlink()
    nb_src = root / "data" / netblocks.NAME  # every NetBlocks post parsed, with its link (excerpt: recent posts only)
    if snap.get("mode") == "live" and nb_src.exists():
        files[f"data/{netblocks.NAME}"] = nb_src.read_text(encoding="utf-8")
    elif (root / "site" / "data" / netblocks.NAME).exists():
        (root / "site" / "data" / netblocks.NAME).unlink()
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
    # The header too: a feed that states contract_rev 2.1 is held to the 2.1 health shape (kit 0.6.0).
    header = {k: v for k, v in feed_doc.items() if k != "features"}
    for std, sev, msg in contract.check_header(header):
        if sev == "error":
            errs.append(f"feed header: {std}: {msg}")
    return errs


def private_netblocks(cmd: str, private: Path, root: Path = ROOT) -> int:
    """archive-full and retire-legacy: NetBlocks' posts with their excerpts, kept outside every published folder.
    Prints counts only (this repo's logs are public)."""
    legacy = root / "history" / archive.NETBLOCKS_FILE
    private = private.resolve()
    for public in (root / "history", root / "site", root / "site_src"):
        if public.resolve() in [private, *private.parents]:
            print(f"::error::--dir must not be under {public.name}/: the excerpts are never archived in public")
            return 1
    if cmd == "archive-full":
        src = root / "data" / netblocks.FULL_NAME
        posts = json.loads(src.read_text(encoding="utf-8"))["posts"] if src.exists() else None
        n = archive.netblocks_full(private, posts, legacy)
        print(f"[blackout:archive-full] NetBlocks posts: {n['posts']} new"
              + ("" if posts is not None else " (the lane was not read this run)")
              + f", {n['legacy']} taken from the old public file")
        return 0
    try:
        k = archive.retire_netblocks_legacy(private, legacy)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 1
    print(f"[blackout:retire-legacy] old public history/{archive.NETBLOCKS_FILE}: "
          + (f"removed ({k} posts, all in the private copy)" if k else "not present"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Blackout connectivity & cable outage monitor.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c_col = sub.add_parser("collect")
    c_col.add_argument("--live", action="store_true")
    sub.add_parser("build")
    sub.add_parser("archive")
    for name in ("archive-full", "retire-legacy"):
        s = sub.add_parser(name)
        s.add_argument("--dir", required=True, help="folder holding the private netblocks-posts.jsonl")
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
              f"{len((snap.get('bgp') or {}).get('notable_leaks') or [])} large route leaks, "
              f"{len((snap.get('eia') or {}).get('notable') or [])} US balancing authorities under forecast, "
              f"{len((snap.get('faa') or {}).get('events') or [])} FAA ground-stop events, "
              f"{len((snap.get('bluesky') or {}).get('surges') or [])} Bluesky and "
              f"{len((snap.get('gdelt') or {}).get('surges') or [])} GDELT outage-report surges; "
              f"{len(probs)} problems.")
        return 0
    if args.cmd == "archive":
        errs = archive_all(ROOT)
        for e in errs:
            print("  - ARCHIVE FAILED:", e, file=sys.stderr)
        return 1 if errs else 0
    if args.cmd in ("archive-full", "retire-legacy"):
        return private_netblocks(args.cmd, Path(args.dir))
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
