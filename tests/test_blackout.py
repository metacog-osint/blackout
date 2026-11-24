"""Offline tests for Blackout (IODA, OONI, submarine cable corridors, doctrine registers, contract v2 feed).

The live-collector tests replay real API responses (tests/fixtures/ioda_live_2026-11-12.json, recorded from IODA v2
on 12 Nov 2026; the OONI counts below are the live aggregation for Iran, 10-11 Nov 2026), so a change in how the
code reads the APIs shows up here rather than on the site. The provider watch replays IODA ASN responses
(ioda_asn_*.json) and provider status feeds (fixtures/status/, listed with their URLs in MANIFEST.json)."""
from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackout import asn_watch, bgp, blackmarble, bluesky, cables, citizenlab, eia, faa, feed, gdelt, iaea, ioda, odin, ooni, radar, status_feeds, stop  # noqa: E402
from tic_doctrine import contract, registers  # noqa: E402

_spec = importlib.util.spec_from_file_location("blackout_cli", ROOT / "tools" / "blackout.py")
blackout_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blackout_cli)

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "ioda_live_2026-11-12.json").read_text(encoding="utf-8"))


def fixture_fetch(url: str) -> dict:
    """Serve the recorded IODA responses by endpoint (events, or signals per country and datasource)."""
    u = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(u.query))
    if u.path.endswith("/outages/events"):
        return FIXTURE["responses"]["events"]
    return FIXTURE["responses"][f"signals/{u.path.rsplit('/', 1)[-1]}/{q['datasource']}"]


def lint_errors(fc: dict) -> dict:
    regs = registers.ids(ROOT / "doctrine")
    out = {}
    for feat in fc["features"]:
        errs = [m for _, sev, m in contract.check(feat, regs) if sev == "error"]
        if errs:
            out[feat["properties"].get("id")] = errs
    return out


def outage(cc: str, start: str, bgp=None, probing=None, darknet=None, minutes=120, **extra) -> dict:
    return {"country": cc, "start_time": start, "duration_minutes": minutes, "bgp_drop_pct": bgp,
            "probing_drop_pct": probing, "darknet_drop_pct": darknet, **extra}


class _FakeResp:
    def __init__(self, body: dict):
        self._buf = io.BytesIO(json.dumps(body).encode("utf-8"))

    def read(self, _n: int = -1) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestDoctrineAndBuild(unittest.TestCase):
    def test_doctrine_registers_pass(self):
        self.assertEqual(registers.check(ROOT / "doctrine"), [])

    def test_registry_keeps_unbuilt_programs_planned(self):
        reg = json.loads((ROOT / "doctrine" / "kit" / "vocab" / "programs.json").read_text(encoding="utf-8"))
        progs = {p["id"]: p for p in reg["programs"]}
        # Wake went live in kit 0.5.x; Fieldcast is still unbuilt (kit 0.5.12, 18 Nov 2026).
        self.assertEqual(progs["wake"]["status"], "live")
        self.assertEqual(progs["fieldcast"]["status"], "planned")
        # Live since 12 Nov 2026 (kit 0.4.3), publishing real data at its own address.
        bo = progs["blackout"]
        self.assertEqual(bo["status"], "live")
        self.assertNotIn("status_note", bo)
        self.assertEqual(bo["feed"], "https://blackout.theintelligence.company/data/feed.geojson")
        self.assertNotIn("TeleGeography)", bo["terms"])  # no TeleGeography data is ingested, so no licence is claimed
        # Kit 0.4.4 (13 Nov 2026): Radar is owned, not planned, and all eleven status feeds are named. Kit 0.4.5
        # (13 Nov 2026): the routing, power and context lanes are owned, and nothing is left planned.
        owns = " | ".join(bo["owns"])
        planned = [o for o in bo["owns"] if o.startswith("planned:")]
        self.assertEqual(planned, [])
        self.assertIn("Cloudflare Radar", owns)
        for lane in ("BGP origin hijacks and route leaks", "Zaporizhzhya NPP", "ODIN", "EIA-930", "NASA Black Marble",
                     "STOP", "Citizen Lab"):
            self.assertIn(lane, owns)
        for name in ("AWS", "Google Cloud", "Google Workspace", "Azure", "Microsoft 365", "Apple",
                     "Cloudflare", "Akamai", "Slack", "Zoom", "Starlink", "GitHub"):
            self.assertIn(name, owns)
        # Kit 0.5.12 (18 Nov 2026): the FAA's ground stops and the public-report lanes are owned.
        for lane in ("Air Traffic Control System Command Center", "Bluesky post counts", "GDELT headline counts",
                     "NetBlocks' reports", "daily outage series",  # kit 0.6.5
                     "Viasat MSS"):  # kit 0.6.7
            self.assertIn(lane, owns)

    def test_licence_claims_match_the_sources(self):
        # OONI data is CC BY-NC-SA 4.0 (github.com/ooni/license, data/LICENSE.md, read 12 Nov 2026); IODA states
        # no data licence. Nothing Blackout publishes may claim CC BY 4.0 for either.
        reg = json.loads((ROOT / "doctrine" / "kit" / "vocab" / "programs.json").read_text(encoding="utf-8"))
        terms = {p["id"]: p for p in reg["programs"]}["blackout"]["terms"]
        fc = feed.build_feed(blackout_cli.build_snapshot())
        # The registry's terms word for word; kit 0.4.4 (13 Nov 2026) put the Radar clause there, so Blackout
        # appends nothing and the clause appears once. Kit 0.4.5 appended each newer source's licence exactly as
        # doctrine/sources.json states it.
        self.assertEqual(fc["licence"], terms)
        self.assertIn("; Cloudflare Radar data CC BY-NC 4.0;", terms)
        self.assertEqual(fc["licence"].count("Cloudflare Radar"), 1)
        sources = {s["id"]: s for s in json.loads((ROOT / "doctrine" / "sources.json").read_text(encoding="utf-8"))["sources"]}
        for sid in ("iaea-ukraine-updates", "accessnow-stop", "citizenlab-test-lists", "odin-ornl",
                    "census-gazetteer-counties", "nasa-black-marble", "eia-930", "ornl-eaglei-history",
                    "faa-atcscc", "github-status", "bluesky-search", "gdelt-gkg",  # kit 0.5.12
                    "netblocks-bluesky",  # kit 0.6.5
                    "viasat-mss-status"):  # kit 0.6.7
            self.assertIn(sources[sid]["licence"], terms, sid)
        for name in ("IAEA", "Access Now", "Citizen Lab", "ODIN", "NASA Black Marble",
                     "Source: U.S. Energy Information Administration", "EAGLE-I"):
            self.assertIn(name, fc["attribution"])
        self.assertIn("CC BY-NC-SA 4.0", fc["attribution"])
        self.assertIn("CC BY-NC-SA 4.0", feed.SRC_OONI["licence"])
        self.assertIn("no data licence", feed.SRC_IODA["licence"].lower())
        texts = {
            "feed": json.dumps(fc),
            "doctrine/sources.json": (ROOT / "doctrine" / "sources.json").read_text(encoding="utf-8"),
            "site_src/index.html": (ROOT / "site_src" / "index.html").read_text(encoding="utf-8"),
        }
        # EAGLE-I's history really is CC BY 4.0 (its figshare record, read 13 Nov 2026; kit 0.4.7), and so is the World
        # Bank's copy of ITU's internet-use series (14 Nov 2026). Their own licence texts are the only places the words
        # may appear.
        from blackout import eaglei, itu
        for name, text in texts.items():
            self.assertNotIn("CC BY 4.0", text.replace(eaglei.LICENCE, "").replace(itu.LICENCE, ""), name)

    def test_seed_corridor_needs_drops_within_two_hours(self):
        snap = blackout_cli.build_snapshot()
        self.assertEqual(snap["mode"], "seed")
        self.assertEqual(snap["tips"], [])
        self.assertEqual(len(snap["corridor_alerts"]), 1)
        ca = snap["corridor_alerts"][0]
        self.assertEqual(ca["corridor_id"], "red-sea-bab-el-mandeb")
        # Sudan's seed drop starts three hours before Yemen's and Djibouti's, so it is not concurrent (BO-IND-2).
        self.assertEqual(set(ca["countries"]), {"YE", "DJ"})

    def test_seed_feed_is_marked_test_and_lints(self):
        fc = feed.build_feed(blackout_cli.build_snapshot())
        self.assertEqual(fc["mode"], "seed")
        self.assertGreaterEqual(len(fc["features"]), 5)
        self.assertEqual({f["properties"]["status"] for f in fc["features"]}, {"test"})
        self.assertEqual(lint_errors(fc), {})
        for f in fc["features"]:
            self.assertFalse(any(s.get("source_type") == "suite_program" for s in f["properties"]["sources"]))

    def test_site_build_and_check_all(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / "doctrine", tmp / "doctrine")
            shutil.copytree(ROOT / "site_src", tmp / "site_src")
            files = blackout_cli.build_site(tmp)
            self.assertIn("data/feed.geojson", files)
            self.assertIn("index.html", files)
            self.assertIn("tic-time.js", files)
            self.assertEqual(blackout_cli.check_all(tmp), [])
            # The map's own files: Natural Earth land for every country, shapes only for the countries drawn, the
            # method register, the house bar and vendored MapLibre with its licence (page rebuilt 12 Nov 2026).
            for name in ("data/world.geojson", "data/countries.geojson", "data/method.json", "tic-nav.js", "tic-nav.css",
                         "tic-related.js", "tic-related.css", "tic-fold.js", "tic-fold.css",
                         "map.js", "vendor/maplibre-gl/maplibre-gl.mjs", "vendor/maplibre-gl/maplibre-gl-worker.mjs",
                         "vendor/maplibre-gl/maplibre-gl.css", "vendor/maplibre-gl/LICENSE.txt"):
                self.assertIn(name, files)
            world = json.loads(files["data/world.geojson"])
            self.assertGreater(len(world["features"]), 200)
            self.assertIn("public domain", world["source"])
            snap = json.loads(files["data/snapshot.json"])
            drawn = {f["properties"]["cc"] for f in json.loads(files["data/countries.geojson"])["features"]}
            self.assertEqual(drawn, blackout_cli.map_countries(snap) & {c["cc"] for c in json.loads(
                (ROOT / "doctrine" / "kit" / "geo" / "countries.json").read_text(encoding="utf-8"))["countries"]})
            method = json.loads(files["data/method.json"])
            n_ind = len(json.loads((ROOT / "doctrine" / "indicators.json").read_text(encoding="utf-8"))["indicators"])
            self.assertEqual([i["id"] for i in method["indicators"]], [f"BO-IND-{n}" for n in range(1, n_ind + 1)])
            self.assertIn("Redistribution and use in source and binary forms", files["vendor/maplibre-gl/LICENSE.txt"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_page_keeps_the_csp_and_credits(self):
        """No inline styles or scripts, no CDN, CSP no wider than COP's (OpenFreeMap tiles and MapLibre's worker),
        the six tabs wired to their panels, and the map's credits on the page."""
        src = ROOT / "site_src"
        page = (src / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(page, r"\sstyle=")
        self.assertNotRegex(page, r"<script(?![^>]*\ssrc=)[^>]*>")
        self.assertNotRegex(page, r"<(script|link)[^>]+(src|href)=\"https?://")
        for js in ("app.js", "map.js"):
            text = (src / js).read_text(encoding="utf-8")
            self.assertNotIn("innerHTML", text, js)
            self.assertNotRegex(text, r"import [^;]+ from \"https?://", js)
        csp = next(line for line in (src / "_headers").read_text(encoding="utf-8").splitlines()
                   if "Content-Security-Policy" in line)
        self.assertNotIn("unsafe-inline", csp)
        # OpenFreeMap tiles for the map; COP's health check for the house bar's freshness dots (tic-nav v2.0)
        self.assertEqual(sorted(set(re.findall(r"https://[^\s;]+", csp))),
                         ["https://cop.theintelligence.company", "https://tiles.openfreemap.org"])
        self.assertIn('data-health="on"', page)
        # "Elsewhere today" (tic-related.js from the hub): a hidden card the module fills from COP's index
        self.assertIn('<section class="card" id="tic-related" hidden></section>', page)
        self.assertIn('<script src="tic-related.js" defer></script>', page)
        self.assertIn("worker-src 'self' blob:", csp)
        for tab in ("overview", "outages", "censorship", "power", "routing", "cables", "providers", "satcom", "trends",
                    "method"):
            self.assertIn(f'id="tab-{tab}" aria-controls="panel-{tab}"', page)
            self.assertIn(f'id="panel-{tab}" aria-labelledby="tab-{tab}"', page)
        for credit in ("OpenFreeMap", "&copy; OpenMapTiles", "&copy; OpenStreetMap contributors", "Natural Earth"):
            self.assertIn(credit, page)
        self.assertTrue((src / "vendor" / "maplibre-gl" / "LICENSE.txt").exists())


class TestIoda(unittest.TestCase):
    def test_live_ioda_replay_measures_real_drops(self):
        out, stats = ioda.fetch_ioda_live(fetch=fixture_fetch, now=FIXTURE["now"])
        self.assertEqual(stats["countries_checked"], 3)
        self.assertEqual(stats["problems"], [])
        by = {o["country"]: o for o in out}
        # Seychelles' darknet "48% drop" was 0.29 -> 0.15 sources a bin: under the floor, so SC is not kept at all.
        self.assertEqual(set(by), {"TN", "VE"})
        self.assertEqual(len({o["id"] for o in out}), len(out))

        tn = by["TN"]  # active probing fell 38% for 65 min while BGP moved 8.6%
        self.assertEqual(tn["country_name"], "Tunisia")
        self.assertEqual((tn["bgp_drop_pct"], tn["probing_drop_pct"], tn["darknet_drop_pct"]), (8.6, 38.0, 7.3))
        self.assertEqual(tn["start_time"], "2026-11-12T00:25:00Z")
        self.assertEqual(tn["duration_minutes"], 65)
        self.assertEqual(tn["indicator_id"], "BO-IND-1")
        self.assertEqual(tn["ioda_score"], 558.4)


        ve = by["VE"]  # 20.4% BGP drop: kept for corridor correlation, below BO-IND-1
        self.assertIsNone(ve["indicator_id"])

        # IODA's score is a magnitude, never shown as a percentage drop.
        self.assertTrue(all(o["drop_pct"] < 60 for o in out))

    def test_signal_drop_uses_seven_day_median_and_skips_gaps(self):
        start = 1_000_000_000
        step = 3600
        t0 = start - 7 * 86400
        values = [100.0] * (7 * 24) + [None, 50.0, 50.0, 50.0]
        m = ioda.signal_drop({"from": t0, "step": step, "values": values}, start, start + 4 * step)
        self.assertEqual(m["drop_pct"], 50.0)
        self.assertEqual(m["points"], 3)
        self.assertIsNone(m["zscore"])  # flat baseline: no spread, so no z-score rather than an invented one
        self.assertIsNone(ioda.signal_drop({"from": start, "step": step, "values": [50.0] * 4}, start, start + 4 * step))

    def test_low_count_signal_never_sets_the_headline(self):
        # DR Congo, 14 Nov 2026: darknet 1.92 -> 0.31 published as an 83.8% drop while BGP moved 1.1%.
        start, step = 1_000_000_000, 1800
        t0 = start - 7 * 86400
        dark = {"from": t0, "step": step, "values": [1.92] * (7 * 48) + [0.31] * 6, "datasource": "merit-nt"}
        m = ioda.signal_drop(dark, start, start + 6 * step)
        self.assertLess(m["baseline"], ioda.MIN_BASELINE["merit-nt"])
        recs = ioda.normalize_ioda_events([{**outage("CD", "2026-11-14T03:00:00Z", bgp=1.1, probing=1.6, minutes=180),
                                             "low_count": {"merit-nt": 1.92}}])
        self.assertEqual(recs, [])  # nothing left above 20%
        recs = ioda.normalize_ioda_events([{**outage("CD", "2026-11-14T03:00:00Z", bgp=30.0, probing=28.0),
                                             "low_count": {"merit-nt": 1.92}}])
        self.assertIn("darknet left out: its baseline median, 1.92, is under the floor of 20.", recs[0]["summary"])

    def test_darknet_alone_never_meets_bo_ind_1(self):
        recs = ioda.normalize_ioda_events([outage("AR", "2026-11-01T11:00:00Z", bgp=9.0, probing=10.0, darknet=60.0)])
        self.assertIsNone(recs[0]["indicator_id"])  # register: BO-IND-1 is BGP or active probing

    def test_baseline_uses_the_same_hours(self):
        # Traffic is 100 at night and 300 by day; a night-time event at night levels is no drop.
        start, step = 1_000_000_000 - (1_000_000_000 % 86400), 3600   # midnight UTC
        t0 = start - 7 * 86400
        day = [100.0] * 6 + [300.0] * 18
        s = {"from": t0, "step": step, "values": day * 7 + [100.0] * 6}
        self.assertEqual(ioda.signal_drop(s, start, start + 6 * step)["drop_pct"], 0.0)
        self.assertGreater(ioda.signal_drop(s, start, start + 6 * step, same_hours=False)["drop_pct"], 60.0)

    def test_normalize_never_fills_in_unmeasured_signals(self):
        recs = ioda.normalize_ioda_events([outage("NG", "2026-11-01T10:00:00Z", bgp=42.0, minutes=90)])
        self.assertEqual(len(recs), 1)
        ng = recs[0]
        self.assertIsNone(ng["probing_drop_pct"])
        self.assertIsNone(ng["baseline_zscore"])
        self.assertFalse(ng["corroborated"])
        self.assertEqual(ng["country_name"], "Nigeria")
        self.assertEqual(ng["indicator_id"], "BO-IND-1")

    def test_normalize_indicator_rules(self):
        recs = {r["country"]: r for r in ioda.normalize_ioda_events([
            outage("KZ", "2026-11-01T11:00:00Z", bgp=2.0, probing=0.0, darknet=34.0, minutes=45),
            outage("PE", "2026-11-01T11:00:00Z", bgp=30.0, probing=28.0, minutes=20),
            outage("CL", "2026-11-01T11:00:00Z", bgp=12.0, probing=10.0, minutes=90),
        ])}
        self.assertEqual(recs["KZ"]["indicator_id"], "BO-IND-4")
        self.assertIsNone(recs["PE"]["indicator_id"])  # 30% but only 20 minutes: below BO-IND-1
        self.assertNotIn("CL", recs)  # below 20%: not kept


class TestOoni(unittest.TestCase):
    IR_LIVE = {"result": {"anomaly_count": 4105, "confirmed_count": 3461, "failure_count": 1938, "ok_count": 13140,
                          "measurement_count": 22644}}

    def test_live_ooni_shape(self):
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(self.IR_LIVE)):
            rows, problems = ooni.fetch_ooni_live(countries=("IR",))
        self.assertEqual(problems, [])
        ir = rows[0]
        self.assertEqual(ir["anomaly_rate"], round((4105 + 3461) / 22644, 4))
        self.assertEqual(ir["ok_count"], 13140)
        self.assertEqual(ir["test_name"], "all tests")
        self.assertEqual(ir["window_days"], 7)
        self.assertIn("over the 7 days to", ir["summary"])

    def test_ooni_failure_is_reported_not_filled(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            rows, problems = ooni.fetch_ooni_live(countries=("IR",))
        self.assertEqual(rows, [])
        self.assertEqual(problems[0]["country"], "IR")

    def test_ok_count_not_invented(self):
        rows = ooni.normalize_ooni_rows([{"probe_cc": "BY", "day": "2026-11-01", "measurement_count": 1000,
                                          "confirmed_count": 120, "anomaly_count": 80}])
        self.assertIsNone(rows[0]["ok_count"])
        self.assertEqual(rows[0]["anomaly_rate"], 0.2)


class TestTipsAndJudgments(unittest.TestCase):
    TW_QUAKE = {"program": "elements", "item_id": "elements:usgs:tw1", "title": "M6.1 earthquake, off Toucheng",
                "tier": "anomaly", "kind": "observation", "status_class": "reported", "countries": ["TW"],
                "_geometry": {"type": "Point", "coordinates": [121.85, 24.80]}}

    def test_tip_without_location_cannot_meet_bo_ind_5(self):
        tip = {k: v for k, v in self.TW_QUAKE.items() if k != "_geometry"}
        corr = next(c for c in cables.CABLE_CORRIDORS if c["id"] == "taiwan-strait-luzon")
        self.assertEqual(cables.correlate_tips_to_corridor(corr, [tip]), [])

    def test_tip_distance_measured_from_its_geometry(self):
        outs = ioda.normalize_ioda_events([outage("TW", "2026-11-01T09:30:00Z", bgp=30.0, probing=27.0)])
        far = {**self.TW_QUAKE, "item_id": "far", "_geometry": {"type": "Point", "coordinates": [90.0, 0.0]}}
        alerts = cables.correlate_cable_corridors(outs, tips=[self.TW_QUAKE, far])
        self.assertEqual(len(alerts), 1)
        tw = alerts[0]
        self.assertEqual(tw["indicator_ids"], ["BO-IND-5"])
        self.assertEqual([t["item_id"] for t in tw["tips"]], ["elements:usgs:tw1"])
        self.assertLess(tw["tips"][0]["distance_km"], 10.0)
        self.assertNotIn("_geometry", tw["tips"][0])

    def test_artefacts_do_not_make_corridor_alerts(self):
        outs = ioda.normalize_ioda_events([
            outage("NG", "2026-11-01T10:00:00Z", bgp=1.0, probing=0.0, darknet=40.0),
            outage("GH", "2026-11-01T10:15:00Z", bgp=2.0, probing=30.0, darknet=0.0),
        ])
        self.assertEqual(cables.correlate_cable_corridors(outs), [])

    RISING = {"hours": 3, "from": "2026-11-01T09:00:00Z", "measurements": 600, "confirmed": 90, "anomalies": 60,
              "rate": 0.25, "expected_measurements": 900.0, "baseline_rate": 0.08, "baseline_days": 5,
              "rising_blocking": True}

    def test_related_tips_do_not_raise_shutdown_likelihood(self):
        raw = {
            "mode": "live",
            "generated_at": "2026-11-01T12:00:00Z",
            "raw_outages": [outage("IR", "2026-11-01T09:00:00Z", bgp=31.2, probing=29.0, ooni_window=self.RISING)],
            "raw_ooni": [{"probe_cc": "IR", "day": "2026-11-01", "measurement_count": 4820, "confirmed_count": 940,
                          "anomaly_count": 610}],
            "raw_tips": [{"program": "vigil", "item_id": "vigil:claim:1", "title": "X claims Iran Telecom (IR)",
                          "tier": "anomaly", "kind": "observation", "status_class": "claimed", "countries": ["IR"]}],
        }
        snap = blackout_cli.build_snapshot(raw)
        self.assertEqual(len(snap["outages"][0]["related_tips"]), 1)
        fc = feed.build_feed(snap)
        self.assertEqual(lint_errors(fc), {})
        ir = next(f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:outage:ir"))
        self.assertEqual(ir["status"], "actual")
        self.assertEqual(ir["hypothesis_id"], "BO-H-1A")
        # Blocking rose in the outage's own hours: supports a shutdown, published with no probability until Verdict
        # has scored the rule (Tom, 16 Nov 2026).
        self.assertEqual((ir["kind"], ir["tier"], ir["direction"]), ("observation", "anomaly", "supports"))
        self.assertNotIn("probability", ir)
        self.assertIn("no probability", ir["summary"])
        self.assertFalse(any(s.get("source_type") == "suite_program" for s in ir["sources"]))

    def test_standing_censorship_is_not_evidence_about_an_outage(self):
        # Iran's 7-day rate meets BO-IND-3 every day; without a rise in the outage's own hours it supports nothing.
        flat = {**self.RISING, "rate": 0.31, "baseline_rate": 0.30, "rising_blocking": False}
        raw = {"mode": "live", "generated_at": "2026-11-01T12:00:00Z",
               "raw_outages": [outage("IR", "2026-11-01T09:00:00Z", bgp=31.2, probing=29.0, ooni_window=flat)],
               "raw_ooni": [{"probe_cc": "IR", "day": "2026-11-01", "measurement_count": 4820, "confirmed_count": 940,
                             "anomaly_count": 610}], "raw_tips": []}
        fc = feed.build_feed(blackout_cli.build_snapshot(raw))
        self.assertEqual(lint_errors(fc), {})
        ir = next(f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:outage:ir"))
        self.assertEqual(ir["direction"], "neutral")
        self.assertIn("against 30.0% in the same hours of the 5 days before", ir["summary"])
        rate = next(f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:ooni:ir"))
        self.assertEqual(rate["tier"], "routine")  # a standing condition against a fixed line

    def test_outage_window_compares_the_same_hours(self):
        def row(ts, m, c=0, a=0):
            return {"measurement_start_day": ts, "measurement_count": m, "confirmed_count": c, "anomaly_count": a}
        rows = [row(f"2026-10-{d:02d}T09:00:00Z", 30, a=1) for d in range(27, 32)]
        rows += [row("2026-11-01T09:00:00Z", 80, c=10, a=20)]
        w = ooni.outage_window(rows, "2026-11-01T09:10:00Z", 50)
        self.assertEqual((w["hours"], w["measurements"], w["expected_measurements"]), (1, 80, 30.0))
        self.assertTrue(w["rising_blocking"])  # 37.5% against 3.3%
        quiet = ooni.outage_window(rows[:-1] + [row("2026-11-01T09:00:00Z", 12, c=10)], "2026-11-01T09:10:00Z", 50)
        self.assertFalse(quiet["rising_blocking"])  # 12 measurements: too few to read
        self.assertIn("too few to read blocking", feed._ooni_window_text({"country": "XX", "country_name": "X"}, quiet))
        self.assertIn("was not read", feed._ooni_window_text({"country": "XX"}, None))

    def test_outage_without_ooni_is_an_observation(self):
        # IODA alone can't tell a shutdown from a power or physical fault: no judgment without BO-IND-3.
        # OONI under 50 measurements doesn't meet BO-IND-3 either.
        raw = {
            "mode": "live",
            "generated_at": "2026-11-01T12:00:00Z",
            "raw_outages": [outage("TN", "2026-11-01T09:00:00Z", bgp=8.6, probing=38.1),
                            outage("IR", "2026-11-01T09:00:00Z", bgp=31.2, probing=29.0)],
            "raw_ooni": [{"probe_cc": "IR", "day": "2026-11-01", "measurement_count": 40, "confirmed_count": 30,
                          "anomaly_count": 5}],
            "raw_tips": [],
        }
        fc = feed.build_feed(blackout_cli.build_snapshot(raw))
        self.assertEqual(lint_errors(fc), {})
        for cc in ("tn", "ir"):
            p = next(f["properties"] for f in fc["features"] if f["properties"]["id"].startswith(f"blackout:outage:{cc}"))
            self.assertEqual((p["kind"], p["tier"], p["direction"]), ("observation", "anomaly", "neutral"))
            self.assertEqual(p["indicator_id"], "BO-IND-1")
            self.assertNotIn("probability", p)
            self.assertIn("baseline_ref", p)
            self.assertIn("BO-H-1B", p["summary"])
        self.assertFalse(any(f["properties"]["kind"] == "assessment" for f in fc["features"]))

    def test_corridor_countries_are_not_called_shutdowns(self):
        raw = {
            "mode": "live",
            "generated_at": "2026-11-01T12:00:00Z",
            "raw_outages": [outage("YE", "2026-11-01T08:00:00Z", bgp=58.4, probing=54.1, minutes=210),
                            outage("DJ", "2026-11-01T08:15:00Z", bgp=28.5, probing=26.2, minutes=180),
                            outage("KZ", "2026-11-01T11:00:00Z", bgp=1.5, probing=0.0, darknet=33.0, minutes=45)],
        }
        fc = feed.build_feed(blackout_cli.build_snapshot(raw))
        self.assertEqual(lint_errors(fc), {})
        by = {f["properties"]["id"]: f["properties"] for f in fc["features"]}
        corr = by["blackout:corridor:red-sea-bab-el-mandeb:2026-11-01"]
        self.assertEqual((corr["kind"], corr["hypothesis_id"], corr["indicator_id"]), ("assessment", "BO-H-2A", "BO-IND-2"))
        ye = by["blackout:outage:ye:2026-11-01T08"]
        self.assertEqual((ye["kind"], ye["tier"], ye["hypothesis_id"]), ("observation", "anomaly", "BO-H-2A"))
        self.assertNotIn("probability", ye)
        kz = by["blackout:outage:kz:2026-11-01T11"]
        self.assertEqual((kz["kind"], kz["indicator_id"], kz["hypothesis_id"]), ("observation", "BO-IND-4", "BO-H-1C"))
        self.assertNotIn("probability", kz)

ASN_FIX = json.loads((ROOT / "tests" / "fixtures" / "ioda_asn_live_2026-11-12.json").read_text(encoding="utf-8"))
ASN_EVENTS = json.loads((ROOT / "tests" / "fixtures" / "ioda_asn_events_2026-11-12.json").read_text(encoding="utf-8"))
STATUS = ROOT / "tests" / "fixtures" / "status"


def asn_fetch(fix: dict):
    """Serve recorded IODA ASN responses by endpoint (events per ASN, signals per ASN and datasource)."""
    def fetch(url: str) -> dict:
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.endswith("/outages/events"):
            return fix["responses"][f"events/{q['entityCode']}"]
        return fix["responses"][f"signals/{u.path.rsplit('/', 1)[-1]}/{q['datasource']}"]
    return fetch


def status_fetch(files: dict, fail: tuple = ()):
    """Serve recorded status feed bodies by URL; feeds named in fail raise like a network error."""
    by_url = {f["url"]: f["id"] for f in status_feeds.FEEDS}

    def fetch(url: str) -> bytes:
        fid = by_url[url]
        if fid in fail:
            raise OSError(f"{fid} down")
        return (STATUS / files[fid]).read_bytes()
    return fetch


LIVE_FEEDS = {"aws": "aws_all_2026-11-12.rss", "gcp": "gcp_incidents_2026-11-12.json",
              "azure": "azure_feed_2026-11-12.rss", "starlink": "starlink_summary_2026-11-12.json",
              "m365": "mscloud_posts_mac_2026-11-12.json",
              # Recorded 00:56-00:58 UTC 13 Nov 2026 (MANIFEST.json).
              "cloudflare": "cloudflare_unresolved_2026-11-13.json", "gworkspace": "gworkspace_incidents_2026-11-13.json",
              "apple": "apple_system_status_2026-11-13.json", "akamai": "akamai_history_2026-11-13.rss",
              "slack": "slack_current_2026-11-13.json", "zoom": "zoom_history_2026-11-13.rss",
              # Recorded 18:34 UTC 17 Nov 2026 (MANIFEST.json).
              "github": "github_history_2026-11-17.rss",
              # Recorded 21:56 UTC 20 Nov 2026 (MANIFEST.json).
              "viasat-mss": "viasat_mss_history_2026-11-20.rss"}
KPUNET = [{"asn": 32204, "provider": "kpunet", "label": "KPUNET"}]


class TestAsnWatch(unittest.TestCase):
    def test_watchlist_replay_is_flat(self):
        # 12 Nov 2026: all five watched networks flat, no IODA events (the expected baseline).
        recs, problems = asn_watch.fetch_asn_watch(fetch=asn_fetch(ASN_FIX), now=ASN_FIX["now"])
        self.assertEqual(problems, [])
        self.assertEqual([r["asn"] for r in recs], ["AS14593", "AS16509", "AS8075", "AS15169", "AS396982"])
        by = {r["asn"]: r for r in recs}
        self.assertEqual(by["AS14593"]["asn_name"], "AS14593 (SPACEX-STARLINK)")
        for r in recs:
            self.assertIsNone(r["indicator_id"])
            self.assertEqual((r["window_basis"], r["window_minutes"], r["ioda_events"]), ("last 3 h", 180, 0))
            self.assertLess(r["drop_pct"], 1.0)
        self.assertIsNone(by["AS15169"]["bgp_zscore"])  # flat BGP: no spread, so no z-score rather than an invented one

    def test_ioda_event_sets_the_window(self):
        wl = KPUNET + [{"asn": 141883, "provider": "bgpnet", "label": "BGPNET"}]
        recs, problems = asn_watch.fetch_asn_watch(wl, fetch=asn_fetch(ASN_EVENTS), now=ASN_EVENTS["now"])
        self.assertEqual(problems, [])
        kp, bn = recs
        self.assertEqual((kp["bgp_drop_pct"], kp["probing_drop_pct"]), (95.0, 95.8))
        self.assertEqual((kp["window_start"], kp["window_minutes"]), ("2026-11-11T23:40:00Z", 255))
        self.assertEqual(kp["window_basis"], "IODA outage events on this network")
        self.assertEqual(kp["indicator_id"], "BO-IND-6")
        # Began 2 Nov, before the 24 h lookback: its baseline is the 7 days before 2 Nov, not before the lookback.
        self.assertEqual(bn["window_start"], "2026-11-02T08:50:00Z")
        self.assertEqual(bn["bgp_drop_pct"], 100.0)
        self.assertIsNone(bn["probing_drop_pct"])  # no usable probing baseline: left unmeasured, not filled in

    def test_low_run_sets_the_window_without_an_event(self):
        # The recorded Starlink series with its last four 30-minute probing values cut by 20% (values only; the
        # response shape is IODA's own). IODA has no event, so the run of low points sets the window.
        fix = json.loads(json.dumps(ASN_FIX))
        s = fix["responses"]["signals/14593/ping-slash24"]["data"][0][0]
        s["values"][-4:] = [v * 0.8 for v in s["values"][-4:]]
        recs, problems = asn_watch.fetch_asn_watch([asn_watch.WATCHLIST[0]], fetch=asn_fetch(fix), now=fix["now"])
        r = recs[0]
        self.assertEqual(r["window_basis"], "points >= 5% under the 7-day median")
        first_low = s["from"] + (len(s["values"]) - 4) * s["step"]
        self.assertEqual(r["window_start"], ioda._iso(first_low))
        # The last 30-minute point runs past now, so the window stops at now: 111 min rather than 120.
        self.assertEqual(r["window_minutes"], (fix["now"] - first_low) // 60)
        self.assertEqual(r["window_minutes"], 111)
        self.assertEqual(r["indicator_id"], "BO-IND-6")
        self.assertGreaterEqual(r["probing_drop_pct"], 19.0)
        self.assertEqual(r["bgp_drop_pct"], 0.0)

    def test_failed_reads_go_to_problems_not_filled(self):
        base = asn_fetch(ASN_FIX)

        def fetch(url):
            if "/signals/raw/asn/8075" in url or "entityCode=16509" in url:
                raise OSError("timed out")
            return base(url)
        recs, problems = asn_watch.fetch_asn_watch(fetch=fetch, now=ASN_FIX["now"])
        self.assertNotIn("AS8075", [r["asn"] for r in recs])  # no signal read: no record at all
        self.assertIn("AS16509", [r["asn"] for r in recs])    # events failed, signals still measured
        self.assertEqual(sorted((p["asn"], p["datasource"]) for p in problems),
                         [("AS16509", "events"), ("AS8075", "bgp"), ("AS8075", "ping-slash24")])


class TestStatusFeeds(unittest.TestCase):
    def test_an_incident_quiet_for_two_weeks_is_long_running_not_current(self):
        # Recorded feeds: AWS me-south-1 and me-central-1 last updated 15 Sep 2026, Akamai's edge issue 29 Oct.
        incs, feeds, problems = status_feeds.fetch_status_feeds(fetch=status_fetch(LIVE_FEEDS))
        raw = {"mode": "live", "generated_at": "2026-11-13T01:00:00Z", "raw_outages": [], "raw_ooni": [],
               "raw_tips": [], "provider_incidents": incs, "provider_feeds": feeds}
        snap = blackout_cli.build_snapshot(raw)
        quiet = {(i["feed"], i["quiet_days"]) for i in snap["provider_incidents"] if i["long_running"]}
        self.assertEqual(quiet, {("aws", 58), ("akamai", 14)})
        self.assertEqual(sum(1 for i in snap["provider_incidents"] if i["feed"] == "aws" and i["long_running"]), 2)
        current = [i for i in snap["provider_incidents"] if not i["long_running"]]
        self.assertTrue(all(i["quiet_days"] is None or i["quiet_days"] < status_feeds.QUIET_DAYS for i in current))
        fc = feed.build_feed(snap)
        self.assertEqual(lint_errors(fc), {})
        items = [f["properties"] for f in fc["features"] if f["properties"]["indicator_id"] == "BO-IND-7"]
        uae = next(p for p in items if "me-central-1" in p["title"])
        self.assertEqual(uae["tier"], "routine")
        self.assertTrue(uae["title"].endswith("(me-central-1) (open; no update in 58 days)"), uae["title"])
        self.assertIn("not as a current incident", uae["summary"])
        self.assertEqual({p["tier"] for p in items if "no update in" not in p["title"]} - {"anomaly"}, set())
        # A time the feed doesn't give is never guessed: that incident counts as current.
        blind = status_feeds.mark_quiet([{"feed": "x", "updated_at": None, "started_at": None}], "2026-11-13T01:00:00Z")
        self.assertEqual((blind[0]["quiet_days"], blind[0]["long_running"]), (None, False))
        page = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f"const QUIET_DAYS = {status_feeds.QUIET_DAYS};", page)

    def test_aws_open_events_from_real_feed(self):
        incs = status_feeds.parse_aws_rss((STATUS / "aws_all_2026-11-12.rss").read_bytes())
        by = {i["locations"][0]: i for i in incs}
        # The Tokyo EC2 event ended with "Service is operating normally", so it is not open.
        self.assertEqual(set(by), {"me-central-1", "me-south-1"})
        uae = by["me-central-1"]
        self.assertEqual((uae["started_at"], uae["updated_at"]), ("2026-03-01T12:51:25Z", "2026-09-15T10:27:10Z"))
        self.assertEqual((uae["status"], uae["title"], uae["service"]),
                         ("Service disruption", "Increased Error Rates", "Multiple services"))
        self.assertEqual(uae["updates"], 24)
        self.assertEqual(by["me-south-1"]["started_at"], "2026-03-02T05:56:27Z")

    def test_gcp_open_only_without_end(self):
        self.assertEqual(status_feeds.parse_gcp_json((STATUS / "gcp_incidents_2026-11-12.json").read_bytes()), [])
        incs = status_feeds.parse_gcp_json((STATUS / "gcp_incidents_wayback_20250612200641.json").read_bytes())
        self.assertEqual(len(incs), 1)
        g = incs[0]
        self.assertEqual(g["started_at"], "2025-06-12T17:51:00Z")
        self.assertEqual(g["link"], "https://status.cloud.google.com/incidents/ow5i3PPK96RduMcb1SsW")
        self.assertEqual((g["status"], g["severity"]), ("SERVICE_OUTAGE", "high"))
        self.assertIn("africa-south1", g["locations"])

    def test_azure_lists_current_items_only(self):
        self.assertEqual(status_feeds.parse_azure_rss((STATUS / "azure_feed_2026-11-12.rss").read_bytes()), [])
        a = status_feeds.parse_azure_rss((STATUS / "azure_feed_wayback_20240719153048.rss").read_bytes())
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["title"], a[0]["started_at"]), ("Awareness - Virtual Machines", "2024-07-18T04:09:00Z"))
        self.assertIn("UAE North", a[0]["affected"])
        b = status_feeds.parse_azure_rss((STATUS / "azure_feed_wayback_20241227041223.rss").read_bytes())
        self.assertEqual(b[0]["started_at"], "2024-12-26T18:44:00Z")
        self.assertIn("South Central US", b[0]["affected"])

    def test_m365_public_post(self):
        # 12 Nov 2026, 23:28 UTC: the Microsoft 365 outage (MO1550821) as status.cloud.microsoft posted it.
        incs = status_feeds.parse_mscloud_json((STATUS / "mscloud_posts_mac_2026-11-12.json").read_bytes())
        self.assertEqual(len(incs), 1)
        m = incs[0]
        self.assertEqual((m["title"], m["status"]),
                         ("Users may be unable to connect to multiple Microsoft 365 services", "Service degradation"))
        self.assertIsNone(m["started_at"])  # the post gives no start; never guessed
        self.assertEqual(m["updated_at"], "2026-11-12T23:06:31Z")
        self.assertEqual(m["affected"][:3], ["Exchange Online", "Microsoft Teams", "SharePoint Online"])
        self.assertIn("Microsoft Purview", m["affected"])
        self.assertIn("Universal Print", m["affected"])
        # "Available" posts from the same API (its Azure and Power Platform posts, same minute) list nothing.
        for name in ("mscloud_posts_azure_2026-11-12.json", "mscloud_posts_ppac_2026-11-12.json"):
            self.assertEqual(status_feeds.parse_mscloud_json((STATUS / name).read_bytes()), [], name)
        # 00:45 UTC 13 Nov 2026: the same incident, next update, with an empty Title and a new Id.
        later = status_feeds.parse_mscloud_json((STATUS / "mscloud_posts_mac_2026-11-13.json").read_bytes())
        self.assertEqual([(i["title"], i["status"]) for i in later],
                         [("Users may be unable to connect to multiple Microsoft 365 services",
                           "Service degradation")])
        self.assertNotEqual(later[0]["incident_id"], m["incident_id"])
        self.assertIn("Exchange Online", later[0]["affected"])
        with self.assertRaises(ValueError):  # no Status: a changed shape, not "nothing open"
            status_feeds.parse_mscloud_json(b'{"Id": "x", "Title": "t"}')

    def test_statuspage_starlink_empty_and_unmaintained(self):
        self.assertEqual(status_feeds.parse_statuspage((STATUS / "starlink_summary_2026-11-12.json").read_bytes()), [])
        hist = json.loads((STATUS / "starlink_incidents_2026-11-12.json").read_text(encoding="utf-8"))["incidents"]
        self.assertEqual([i["created_at"][:10] for i in hist], ["2022-04-21"])  # its whole history
        self.assertEqual(status_feeds.FEED_BY_ID["starlink"]["grade"], "F6")
        gh = status_feeds.parse_statuspage((STATUS / "statuspage_github_summary_2026-11-12.json").read_bytes())
        self.assertEqual([(i["title"], i["status"]) for i in gh], [("Disruption with Email Delivery", "monitoring")])
        self.assertEqual(gh[0]["started_at"], "2026-11-12T19:36:45Z")

    def test_cloudflare_keeps_its_network_not_developer_products(self):
        live = (STATUS / "cloudflare_unresolved_2026-11-13.json").read_bytes()
        # 13 Nov 2026: three open, all minor and on developer products; none is kept.
        self.assertEqual([(i["severity"], i["products"]) for i in status_feeds.parse_statuspage(live)],
                         [("minor", ["Durable Objects"]), ("minor", ["Replicate"]), ("minor", ["Workers", "Queues"])])
        self.assertEqual(status_feeds.parse_cloudflare_json(live), [])
        # Cloudflare rates network incidents minor too, so the component decides (Wayback captures).
        nov = status_feeds.parse_cloudflare_json(
            (STATUS / "cloudflare_unresolved_wayback_20261103045807.json").read_bytes())
        self.assertEqual([(i["title"], i["products"], i["started_at"]) for i in nov],
                         [("Network Performance Issues in APJC", ["Network"], "2026-11-03T04:44:12Z")])
        sep = status_feeds.parse_cloudflare_json(
            (STATUS / "cloudflare_unresolved_wayback_20260928184514.json").read_bytes())
        self.assertEqual([i["products"] for i in sep], [["Network"], ["WARP"]])  # Cloudflare One Client left out
        self.assertEqual(sep[1]["link"], "https://www.cloudflarestatus.com/incidents/9g65dxfbcjln")
        # Rated major (the live response with that one value changed; the shape is Cloudflare's own): kept whatever
        # the component.
        data = json.loads(live)
        data["incidents"][0]["impact"] = "major"
        self.assertEqual([i["title"] for i in status_feeds.parse_cloudflare_json(json.dumps(data).encode())],
                         ["Durable Objects increased error rates in North America"])
        # The names are Cloudflare's own: every core component exists on its page, and the data-centre pattern
        # matches exactly the components in its regional groups.
        summ = json.loads((STATUS / "cloudflare_summary_2026-11-13.json").read_text(encoding="utf-8"))
        comps = {c["id"]: c for c in summ["components"]}
        groups = {c["id"]: c["name"] for c in summ["components"] if c.get("group")}
        leaves = [c for c in summ["components"] if not c.get("group")]
        self.assertLessEqual(status_feeds.CLOUDFLARE_CORE, {c["name"] for c in leaves})
        self.assertIn("1.1.1.1", comps[next(c["id"] for c in leaves if c["name"] == "Recursive DNS")]["description"])
        regional = {c["name"] for c in leaves if groups.get(c["group_id"]) != "Cloudflare Sites and Services"}
        self.assertGreater(len(regional), 300)
        self.assertEqual({c["name"] for c in leaves if status_feeds.CLOUDFLARE_DC_RE.search(c["name"])}, regional)

    def test_gworkspace_reads_like_google_cloud(self):
        body = (STATUS / "gworkspace_incidents_2026-11-13.json").read_bytes()
        self.assertEqual(status_feeds.parse_gworkspace_json(body), [])  # all 32 closed on 13 Nov 2026
        data = json.loads(body)
        self.assertEqual(len(data), 32)
        for inc in data:  # the fields parse_gcp_json reads are all there
            self.assertLessEqual({"id", "begin", "end", "external_desc", "most_recent_update", "affected_products",
                                  "uri", "status_impact", "severity", "service_name"}, set(inc))
        # The incident page this link names answered 200 on 13 Nov 2026.
        self.assertEqual(status_feeds.WORKSPACE_LINK_BASE + data[0]["uri"],
                         "https://www.google.com/appsstatus/dashboard/incidents/BoKRw5LNb3Vb2NadUJRU")

    def test_apple_groups_an_outage_and_reads_pacific_time(self):
        live = (STATUS / "apple_system_status_2026-11-13.json").read_bytes()
        self.assertEqual(status_feeds.parse_apple_json(live), [])  # the 10 Nov 2026 outage, resolved
        resolved = {(s["serviceName"], e["messageId"]) for s in json.loads(live)["services"] for e in s["events"]}
        self.assertEqual(len(resolved), 8)
        # 00:10 UTC 11 Nov 2026 (Wayback), during that outage: one Outage posted on seven services, and Find My.
        raw = (STATUS / "apple_system_status_wayback_20261111001007.json").read_bytes()
        incs = {i["incident_id"]: i for i in status_feeds.parse_apple_json(raw)}
        self.assertEqual(set(incs), {"1000005965", "2000005780"})  # Apple Music and Podcasts (9 Nov) resolved
        ic = incs["1000005965"]
        self.assertEqual(ic["title"], "Outage on Game Center, Photos, iCloud Account & Sign In and 4 more")
        self.assertEqual((ic["status"], ic["severity"]), ("ongoing", "Outage"))
        self.assertEqual(ic["affected"], ["Game Center", "Photos", "iCloud Account & Sign In", "iCloud Calendar",
                                          "iCloud Contacts", "iCloud Mail", "iWork for iCloud"])
        self.assertEqual((ic["started_at"], ic["updated_at"]), ("2026-11-10T22:49:00Z", "2026-11-11T00:07:00Z"))
        self.assertEqual((incs["2000005780"]["title"], incs["2000005780"]["started_at"]),
                         ("Issue on Find My", "2026-11-10T22:50:00Z"))
        for e in (e for s in json.loads(raw)["services"] for e in s["events"]):  # Apple's wording is never kept
            self.assertNotIn(e["message"], json.dumps(list(incs.values())))
        # 24 Jun 2025 (PDT): one messageId listed as Issue on some services and Outage on others is an Outage;
        # FaceTime and iMessage are resolved though they have no endDate.
        jun = {i["incident_id"]: i for i in status_feeds.parse_apple_json(
            (STATUS / "apple_system_status_wayback_20250624202139.json").read_bytes())}
        self.assertEqual(set(jun), {"2000004700", "1000004965"})
        self.assertEqual((jun["2000004700"]["severity"], jun["2000004700"]["started_at"]),
                         ("Outage", "2025-06-24T18:36:00Z"))
        self.assertIn("iCloud Web Apps (iCloud.com)", jun["2000004700"]["affected"])
        # Upcoming maintenance is not an incident.
        self.assertEqual(status_feeds.parse_apple_json(
            (STATUS / "apple_system_status_wayback_20240720130517.json").read_bytes()), [])
        # Apple's local times read as US Pacific agree with its epoch times on every event recorded here.
        self.assertEqual(status_feeds.apple_time("11/10/2026 14:49 PST"), status_feeds._parse_time("2026-11-10T22:49:00Z"))
        self.assertEqual(status_feeds.apple_time("06/24/2025 11:36 PDT"), status_feeds._parse_time("2025-06-24T18:36:00Z"))
        self.assertIsNone(status_feeds.apple_time("11/10/2026 14:49 EST"))  # another zone: not guessed
        n = 0
        for name in ("apple_system_status_2026-11-13.json", "apple_system_status_wayback_20261111001007.json",
                     "apple_system_status_wayback_20250624202139.json",
                     "apple_system_status_wayback_20240720130517.json"):
            for e in (e for s in json.loads((STATUS / name).read_bytes())["services"] for e in s["events"]):
                self.assertEqual(status_feeds.apple_time(e["startDate"]).timestamp() * 1000, e["epochStartDate"])
                n += 1
        self.assertEqual(n, 34)

    def test_akamai_rss_open_matches_its_status_page(self):
        rss = (STATUS / "akamai_history_2026-11-13.rss").read_bytes()
        incs = {i["incident_id"]: i for i in status_feeds.parse_akamai_rss(rss)}
        # Akamai's own page, read a minute later, lists the same two as unresolved.
        page = (STATUS / "akamai_status_page_2026-11-13.html").read_text(encoding="utf-8")
        unresolved = re.findall(r'class="unresolved-incident [^"]*"[\s\S]*?href="/incidents/([a-z0-9]+)"', page)
        self.assertEqual(sorted(unresolved), ["j6wjy0jkxgfq", "m5cqzkvd7ldg"])
        self.assertEqual(set(incs), set(unresolved))
        mail, edge = incs["j6wjy0jkxgfq"], incs["m5cqzkvd7ldg"]
        self.assertEqual((mail["title"], mail["status"], mail["started_at"], mail["updated_at"], mail["updates"]),
                         ("Email Systems Widespread Outage", "investigating", "2026-11-12T20:53:00Z",
                          "2026-11-12T23:37:00Z", 3))
        # Its newest update is a plain "Update"; the status shown is the last one Akamai set.
        self.assertEqual((edge["status"], edge["started_at"], edge["updates"]),
                         ("monitoring", "2026-10-25T13:44:00Z", 4))
        self.assertEqual(mail["link"], "https://www.akamaistatus.com/incidents/j6wjy0jkxgfq")
        # Customer Security Advisories: four in the feed, all resolved. One set back to open (that one status word
        # changed; the feed is otherwise Akamai's own) is still left out.
        text = rss.decode("utf-8")
        self.assertEqual(text.count("<title>Customer Security Advisory"), 4)
        head, sep, tail = text.partition("<title>Customer Security Advisory")
        reopened = (head + sep + tail.replace("&lt;strong&gt;Resolved&lt;/strong&gt;",
                                              "&lt;strong&gt;Update&lt;/strong&gt;", 1)).encode("utf-8")
        self.assertEqual(len(status_feeds.parse_statuspage_rss(reopened)), 3)
        self.assertEqual(set(i["incident_id"] for i in status_feeds.parse_akamai_rss(reopened)), set(unresolved))
        with self.assertRaises(ValueError):  # updates that can't be read: a changed shape, not "nothing open"
            status_feeds.parse_statuspage_rss(b"<rss><channel><item><title>x</title><description>&lt;p&gt;y"
                                              b"&lt;/p&gt;</description></item></channel></rss>")

    def test_zoom_rss_skips_maintenance_and_matches_summary(self):
        rss = (STATUS / "zoom_history_2026-11-13.rss").read_bytes()
        incs = status_feeds.parse_statuspage_rss(rss)
        # Zoom's summary.json, read a minute earlier, listed the same one open incident.
        summ = status_feeds.parse_statuspage((STATUS / "zoom_summary_2026-11-13.json").read_bytes())
        self.assertEqual([i["incident_id"] for i in incs], [i["incident_id"] for i in summ])
        self.assertEqual([i["incident_id"] for i in incs], ["8n4n9nsqcl33"])
        z = incs[0]
        self.assertEqual((z["status"], z["started_at"]), ("identified", "2026-11-12T19:24:00Z"))  # 11:24 PST
        self.assertEqual(summ[0]["started_at"], "2026-11-12T19:24:50Z")  # the same minute
        # 20 of the 25 items are maintenance notices (Scheduled, In progress, Completed), none listed; of the 5
        # incidents, 4 are resolved.
        items = re.findall(r"<item>[\s\S]*?</item>", rss.decode("utf-8"))
        self.assertEqual(len(items), 25)
        self.assertEqual(sum("THIS IS A SCHEDULED EVENT" in it or "&lt;strong&gt;Completed" in it for it in items), 20)
        self.assertEqual(sum("&lt;strong&gt;Resolved" in it for it in items), 4)

    def test_slack_open_is_active(self):
        cur = (STATUS / "slack_current_2026-11-13.json").read_bytes()
        self.assertEqual(json.loads(cur)["status"], "ok")
        self.assertEqual(status_feeds.parse_slack_json(cur), [])
        hist = json.loads((STATUS / "slack_history_2026-11-13.json").read_text(encoding="utf-8"))
        self.assertEqual({(i["type"], i["status"]) for i in hist}, {("incident", "resolved")})
        # No capture with an open Slack incident exists, so the newest real one (11 Nov 2026) goes into the real
        # current envelope; resolved it lists nothing, and as "active" (Slack's documented open status) it is open.
        env = json.loads(cur)
        env["active_incidents"] = [hist[0]]
        self.assertEqual(status_feeds.parse_slack_json(json.dumps(env).encode()), [])
        env["active_incidents"] = [{**hist[0], "status": "active"}]
        (s,) = status_feeds.parse_slack_json(json.dumps(env).encode())
        self.assertEqual((s["title"], s["severity"], s["started_at"], s["updated_at"]),
                         ("Trouble loading Slack and saving changes.", "incident", "2026-11-11T15:24:01Z",
                          "2026-11-11T18:58:05Z"))
        self.assertEqual(s["link"], "https://slack-status.com/2026-11/b8bb993128ac94f5")
        self.assertIn("Messaging", s["affected"])
        with self.assertRaises(ValueError):
            status_feeds.parse_slack_json(b'{"status": "ok"}')

    def test_every_feed_is_registered(self):
        srcs = {x["id"]: x for x in json.loads((ROOT / "doctrine" / "sources.json").read_text(encoding="utf-8"))["sources"]}
        for f in status_feeds.FEEDS:
            self.assertIn(f["provider"], status_feeds.PROVIDER_LABELS, f["id"])
            self.assertIn(f["format"], status_feeds.PARSERS, f["id"])
            self.assertEqual((srcs[f["source_id"]]["url"], srcs[f["source_id"]]["grade"]), (f["url"], f["grade"]))
        page = (ROOT / "site_src" / "index.html").read_text(encoding="utf-8")
        for lbl in set(status_feeds.PROVIDER_LABELS.values()) - {"Salesforce"}:
            self.assertIn(lbl, page)

    def test_feed_with_a_dtd_is_refused(self):
        with self.assertRaises(ValueError):
            status_feeds.parse_azure_rss(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss>&a;</rss>')

    def test_failed_feed_is_listed_not_filled(self):
        incs, feeds_read, problems = status_feeds.fetch_status_feeds(fetch=status_fetch(LIVE_FEEDS, fail=("azure",)))
        st = {f["id"]: f for f in feeds_read}
        self.assertEqual((st["azure"]["read"], st["azure"]["open"]), (False, None))
        self.assertEqual((st["aws"]["read"], st["aws"]["open"]), (True, 2))
        self.assertEqual((st["gcp"]["open"], st["starlink"]["open"]), (0, 0))
        self.assertEqual([p["feed"] for p in problems], ["azure"])
        self.assertEqual(st["m365"]["open"], 1)
        self.assertEqual({k: st[k]["open"] for k in ("cloudflare", "gworkspace", "apple", "akamai", "slack", "zoom", "github",
                                                     "viasat-mss")},
                         {"cloudflare": 0, "gworkspace": 0, "apple": 0, "akamai": 2, "slack": 0, "zoom": 1, "github": 0,
                          "viasat-mss": 0})
        self.assertEqual({i["feed"] for i in incs}, {"aws", "m365", "akamai", "zoom"})


class TestProviderFeed(unittest.TestCase):
    def _snap(self, feeds=None):
        asn_recs, _ = asn_watch.fetch_asn_watch(KPUNET, fetch=asn_fetch(ASN_EVENTS), now=ASN_EVENTS["now"])
        incs, feeds_read, _ = status_feeds.fetch_status_feeds(fetch=status_fetch(feeds or LIVE_FEEDS))
        raw = {"mode": "live", "generated_at": "2026-11-12T21:30:00Z", "raw_outages": [], "raw_ooni": [],
               "raw_tips": [], "asn_watch": asn_recs, "provider_incidents": incs, "provider_feeds": feeds_read}
        return blackout_cli.build_snapshot(raw)

    def test_provider_items_are_observations_and_lint(self):
        snap = self._snap()
        self.assertEqual(len(snap["asn_watchlist"]), 5)
        fc = feed.build_feed(snap)
        self.assertEqual(lint_errors(fc), {})
        prov = [f["properties"] for f in fc["features"] if f["properties"].get("warning_problem_id") == "BO-WP-3"]
        self.assertEqual(sorted(p["indicator_id"] for p in prov), ["BO-IND-6"] + ["BO-IND-7"] * 6 + ["BO-IND-8"])
        for p in prov:  # an incident with no update in 14 days is long-running: routine, not an anomaly (14 Nov 2026)
            quiet = "(open; no update in" in p["title"]
            self.assertEqual((p["kind"], p["tier"], p["direction"], p["status_class"]),
                             ("observation", "routine" if quiet else "anomaly", "neutral", "reported"))
            self.assertNotIn("probability", p)
            self.assertIn("baseline_ref", p)
        aws = [p for p in prov if p["indicator_id"] == "BO-IND-7" and p["sources"][0]["id"] == "aws-health"]
        self.assertEqual({p["title"] for p in aws},
                         {"AWS: Increased Error Rates (me-central-1) (open; no update in 58 days)",
                          "AWS: Increased Error Rates (me-south-1) (open; no update in 58 days)"})
        for p in aws:
            self.assertIn("does not assess its cause", p["summary"])
            self.assertEqual(p["sources"][0]["publisher"], "Amazon Web Services")
            self.assertEqual(p["sources"][0]["id"], "aws-health")
            self.assertTrue(p["sources"][0]["url"].startswith("https://"))
        net = next(p for p in prov if p["indicator_id"] == "BO-IND-6")
        self.assertEqual(net["id"], "blackout:provider-net:as32204:2026-11-11T23")
        self.assertIn("BO-H-3D", net["summary"])
        ms = next(p for p in prov if p["indicator_id"] == "BO-IND-7" and p["sources"][0]["id"] == "m365-status")
        self.assertEqual(ms["title"],
                         "Microsoft 365: Users may be unable to connect to multiple Microsoft 365 services")
        self.assertIn("Exchange Online", ms["summary"])
        self.assertEqual(ms["sources"][0]["url"], "https://status.cloud.microsoft/")
        # 12 Nov 2026: Akamai's email incident (20:53 UTC, which Akamai tied to the Microsoft Outlook outage) and
        # Zoom's Microsoft-integrations degradation (19:24 UTC) began 89 min apart: BO-IND-8, an observation.
        conc = snap["provider_concurrency"]
        self.assertEqual((conc["providers"], conc["provider_labels"]), (["akamai", "zoom"], ["Akamai", "Zoom"]))
        self.assertEqual((conc["window_start"], conc["window_end"]), ("2026-11-12T19:24:00Z", "2026-11-12T20:53:00Z"))
        self.assertIsNone(conc["caution"])
        ak = next(p for p in prov if p["indicator_id"] == "BO-IND-7" and p["sources"][0]["id"] == "akamai-status")
        self.assertEqual(ak["sources"][0]["url"], "https://www.akamaistatus.com/incidents/j6wjy0jkxgfq")
        self.assertEqual(ak["ncf"], ["provide_internet_content_services"])

    def test_archived_open_incidents_lint(self):
        feeds = {**LIVE_FEEDS, "gcp": "gcp_incidents_wayback_20250612200641.json",
                 "azure": "azure_feed_wayback_20240730171154.rss"}
        fc = feed.build_feed(self._snap(feeds))
        self.assertEqual(lint_errors(fc), {})
        ids = [f["properties"]["id"] for f in fc["features"]]
        self.assertIn("blackout:provider-status:gcp:ow5i3ppk96rdumcb1ssw", ids)
        self.assertIn("blackout:provider-status:azure:"
                      "network-infrastructure-issues-accessing-a-subset-of-microsoft-services", ids)

    def test_archived_apple_and_cloudflare_incidents_lint(self):
        feeds = {**LIVE_FEEDS, "apple": "apple_system_status_wayback_20261111001007.json",
                 "cloudflare": "cloudflare_unresolved_wayback_20261103045807.json"}
        fc = feed.build_feed(self._snap(feeds))
        self.assertEqual(lint_errors(fc), {})
        by = {f["properties"]["id"]: f["properties"] for f in fc["features"]}
        ap = by["blackout:provider-status:apple:1000005965"]
        self.assertEqual(ap["title"], "Apple: Outage on Game Center, Photos, iCloud Account & Sign In and 4 more")
        self.assertIn("iCloud Mail", ap["summary"])
        self.assertIn("Terms of Use", ap["sources"][0]["licence"])  # Apple's terms, not "no licence stated"
        cf = by["blackout:provider-status:cloudflare:j25w3rpv45gs"]
        self.assertEqual(cf["title"], "Cloudflare: Network Performance Issues in APJC")
        self.assertEqual(cf["ncf"], ["provide_internet_routing", "provide_internet_content_services"])
        self.assertEqual(cf["sources"][0]["licence"], "No licence stated; title, times, affected areas and link only")

    def test_concurrency_needs_two_providers_within_two_hours(self):
        # Internal logic on records already parsed; the onset times are set by hand.
        def drop(provider, start):
            return {"provider": provider, "label": provider.title(), "asn": "AS1", "indicator_id": "BO-IND-6",
                    "window_start": start}

        def inc(provider, start, feed_id):
            return {"provider": provider, "provider_label": provider.title(), "title": "x", "feed": feed_id,
                    "started_at": start, "link": "https://example.invalid/"}
        gen = "2026-11-12T22:00:00Z"
        old_aws = inc("amazon", "2026-03-01T12:51:25Z", "aws")
        self.assertIsNone(status_feeds.concurrency([drop("google", "2026-11-12T10:00:00Z")], [old_aws], gen))
        self.assertIsNone(status_feeds.concurrency(
            [drop("google", "2026-11-12T10:00:00Z"), drop("google", "2026-11-12T10:30:00Z")], [], gen))
        c = status_feeds.concurrency([drop("amazon", "2026-11-12T10:00:00Z"), drop("google", "2026-11-12T20:00:00Z")],
                                     [inc("microsoft", "2026-11-12T11:30:00Z", "azure"), old_aws], gen)
        self.assertEqual(c["providers"], ["amazon", "microsoft"])
        self.assertEqual(c["provider_labels"], ["Amazon", "Microsoft"])
        self.assertEqual((c["window_start"], c["window_end"]), ("2026-11-12T10:00:00Z", "2026-11-12T11:30:00Z"))
        self.assertIsNone(c["caution"])  # Azure reported an incident, so not IODA alone
        ioda_only = status_feeds.concurrency(
            [drop("amazon", "2026-11-12T10:00:00Z"), drop("microsoft", "2026-11-12T10:30:00Z")], [], gen)
        self.assertIn("BO-H-3D", ioda_only["caution"])
        snap = self._snap()
        snap["provider_concurrency"] = c
        fc = feed.build_feed(snap)
        self.assertEqual(lint_errors(fc), {})
        p = next(f["properties"] for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-8")
        self.assertEqual((p["kind"], p["hypothesis_id"], p["direction"]), ("observation", "BO-H-3C", "neutral"))
        self.assertIn("don't by themselves show a shared cause", p["summary"])


RADAR = ROOT / "tests" / "fixtures" / "radar"
RADAR_NOW = "2026-11-13T00:50:00Z"     # just after the responses were recorded
RADAR_EARLY = "2026-11-08T12:00:00Z"   # while the Ukraine annotation was open


def radar_fetch(fail: tuple = (), anomalies: str = "traffic_anomalies_verified_7d_2026-11-13.json",
                annotations: str = "annotations_outages_7d_2026-11-13.json"):
    """Serve recorded Radar responses by endpoint; endpoints named in fail raise like a network error."""
    files = {radar.OUTAGES_URL: ("annotations", annotations),
             radar.ANOMALIES_URL: ("anomalies", anomalies)}

    def fetch(url: str) -> dict:
        key, name = files[url]
        if key in fail:
            raise OSError(f"radar {key} down")
        return json.loads((RADAR / name).read_text(encoding="utf-8"))
    return fetch


def _utc(iso: str):
    return radar._t(iso)


class TestRadar(unittest.TestCase):
    def _rate_limited(self, answers):
        """Run radar._get against a fake opener that answers 429 (with an optional Retry-After) before a body."""
        import email.message
        import os
        import urllib.error
        from unittest import mock
        calls, waits = [], []

        class Body:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1): return self.data

        def opener(req, timeout=None):
            calls.append(req.full_url)
            a = answers[len(calls) - 1]
            if isinstance(a, int):
                raise urllib.error.HTTPError(req.full_url, a, "Too Many Requests", email.message.Message(), None)
            if isinstance(a, str):
                hdrs = email.message.Message()
                hdrs["Retry-After"] = a
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", hdrs, None)
            return Body(json.dumps(a).encode())

        with mock.patch.dict(os.environ, {radar.TOKEN_ENV: "test-token"}):
            try:
                return radar._get("https://example.test/radar", opener=opener, sleep=waits.append), calls, waits
            except urllib.error.HTTPError as exc:
                return exc, calls, waits

    def test_rate_limit_is_retried(self):
        # 14 Nov 2026, 13:46 UTC: anomalies and route leaks answered 429 and the run listed two problems.
        got, calls, waits = self._rate_limited([429, "7", {"success": True}])
        self.assertEqual(got, {"success": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(waits, [radar.RETRY_PAUSES_S[0], 7.0])  # Radar's own Retry-After wins when it gives seconds
        got, calls, waits = self._rate_limited(["600", {"success": True}])
        self.assertEqual(waits, [radar.RETRY_MAX_WAIT_S])

    def test_rate_limit_gives_up_and_other_errors_are_not_retried(self):
        got, calls, waits = self._rate_limited([429, 429, 429, 429])
        self.assertEqual((getattr(got, "code", None), len(calls), waits), (429, 4, list(radar.RETRY_PAUSES_S)))
        got, calls, waits = self._rate_limited([403])
        self.assertEqual((getattr(got, "code", None), len(calls), waits), (403, 1, []))

    def test_recent_window_and_verified_only(self):
        r, problems = radar.fetch_radar(fetch=radar_fetch(), now=_utc(RADAR_NOW))
        self.assertEqual(problems, [])
        self.assertEqual(r["read"], {"annotations": True, "anomalies": True})
        self.assertEqual(r["annotations"], [])  # Ukraine (8 Nov) and Claro Dominicana (6 Nov) ended over 72 h before
        got = [(a["uuid"][:8], a["asn"], a["country"], a["end"]) for a in r["anomalies"]]
        self.assertEqual(got, [("5e240406", 15378, "RU", None),
                               ("7a4b565a", 15378, "RU", "2026-11-10T04:00:00Z"),
                               ("8ddd8f4a", 48492, "IQ", None)])
        # The unfiltered response holds UNVERIFIED ones too (Algeria AS327712, a second Tele2 record); never kept.
        r_all, _ = radar.fetch_radar(fetch=radar_fetch(anomalies="traffic_anomalies_all_7d_2026-11-13.json"),
                                     now=_utc(RADAR_NOW))
        self.assertEqual([a["uuid"] for a in r_all["anomalies"]], [a["uuid"] for a in r["anomalies"]])

    def test_annotations_cause_and_hypothesis(self):
        r, _ = radar.fetch_radar(fetch=radar_fetch(), now=_utc(RADAR_EARLY))
        self.assertEqual([(a["radar_id"], a["cause"], a["type"]) for a in r["annotations"]],
                         [("1748", "POWER_OUTAGE", "REGIONAL"), ("1747", "TECHNICAL_PROBLEM", "NETWORK")])
        # Claro Dominicana's anomaly carries outageId 1747, so the annotation stands for it.
        self.assertNotIn(6400, [a["asn"] for a in r["anomalies"]])
        mx = next(a for a in r["anomalies"] if a["asn"] == 28548)
        self.assertEqual(mx["asn_name"], "izzi telecom")  # Radar's "izzi telecom (AS28548)", AS number not repeated
        raw = {"mode": "live", "generated_at": RADAR_EARLY, "raw_outages": [], "raw_ooni": [], "raw_tips": [],
               "radar": r}
        fc = feed.build_feed(blackout_cli.build_snapshot(raw))
        self.assertEqual(lint_errors(fc), {})
        items = {f["properties"]["id"]: f for f in fc["features"]}
        ua = items["blackout:radar-outage:1748"]["properties"]
        self.assertEqual(ua["title"], "Dnipropetrovsk, Ukraine: power outage, regional (Cloudflare Radar)")
        self.assertEqual((ua["kind"], ua["status_class"], ua["indicator_id"], ua["hypothesis_id"], ua["direction"]),
                         ("observation", "reported", "BO-IND-9", "BO-H-1B", "supports"))
        self.assertIn("Russian strikes on energy infrastructure", ua["summary"])
        self.assertEqual(ua["requirement_ids"], ["BO-PIR-1", "BO-PIR-7"])  # a power cause also answers the power PIR
        self.assertEqual(ua["sources"][0]["url"], "https://x.com/CloudflareRadar/status/2119759535682535615")
        self.assertIn("CC BY-NC 4.0", ua["sources"][0]["licence"])
        do = items["blackout:radar-outage:1747"]["properties"]
        self.assertEqual(do["title"], "Dominican Republic, AS6400 Claro Dominicana: technical problem, network (Cloudflare Radar)")
        self.assertEqual((do["hypothesis_id"], do["direction"]), ("BO-H-1A", "refutes"))  # a fault, not an order
        ga_uuid = next(a["uuid"] for a in r["anomalies"] if a["country"] == "GA")
        ga = items[f"blackout:radar-anomaly:{ga_uuid}"]["properties"]
        self.assertEqual((ga["indicator_id"], ga["hypothesis_id"], ga["direction"]), ("BO-IND-10", "BO-H-1A", "neutral"))
        self.assertEqual(ga["sources"][0]["url"], "https://radar.cloudflare.com/ga")

    def test_a_platform_outage_names_no_place_and_says_so(self):
        # Recorded 02:42 UTC 14 Nov 2026: Microsoft 365 (1749, PLATFORM, no locations) open; Ukraine (1748) ended 8 Nov.
        r, problems = radar.fetch_radar(fetch=radar_fetch(annotations="annotations_outages_7d_2026-11-14.json"),
                                        now=_utc("2026-11-14T02:42:00Z"))
        self.assertEqual(problems, [])
        self.assertEqual([(a["radar_id"], a["type"], a["countries"], a["end"]) for a in r["annotations"]],
                         [("1749", "PLATFORM", [], None)])
        raw = {"mode": "live", "generated_at": "2026-11-14T02:42:00Z", "raw_outages": [], "raw_ooni": [],
               "raw_tips": [], "radar": r}
        fc = feed.build_feed(blackout_cli.build_snapshot(raw))
        self.assertEqual(lint_errors(fc), {})
        ms = next(f for f in fc["features"] if f["properties"]["id"] == "blackout:radar-outage:1749")
        self.assertEqual(ms["properties"]["title"],
                         "Platform outage, no place named: technical problem (Cloudflare Radar)")
        self.assertIsNone(ms["geometry"])
        self.assertIn("Microsoft 365", ms["properties"]["summary"])
        page = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f'"{feed.PLATFORM_WHERE}"', page)  # the page says it the same way
        self.assertNotIn("(a.countries || []).join(\", \")}: ", page)  # no title that can start with ':'

    def test_an_outage_named_by_network_only_takes_the_networks_country_and_says_so(self):
        # Recorded 20 Nov 2026 (tests/fixtures/trends): 1752 names no location, only AS15796 (SALT-CH), whose
        # asnsDetails location is CH. It was published as "Location not given, AS15796" before item 3 of ideas#104.
        import gzip
        data = json.loads(gzip.open(ROOT / "tests" / "fixtures" / "trends" / "radar_outages.json.gz", "rt",
                                    encoding="utf-8").read())
        anns = radar.parse_outages(data, _utc("2026-11-19T00:00:00Z"))
        salt = next(a for a in anns if a["radar_id"] == "1752")
        self.assertEqual((salt["countries"], salt["country_basis"], salt["asn_names"]),
                         (["CH"], "network", {"15796": "SALT-CH"}))
        own = [a for a in anns if a["country_basis"] == "location"]
        self.assertTrue(own)
        ms = next((a for a in anns if a["radar_id"] == "1749"), None)  # platform outage, no network: still no place
        if ms:
            self.assertEqual((ms["countries"], ms["country_basis"]), ([], None))
        r = {"annotations": [salt], "anomalies": [], "read": {"annotations": True, "anomalies": True},
             "recent_hours": radar.RECENT_HOURS}
        raw = {"mode": "live", "generated_at": "2026-11-19T00:00:00Z", "raw_outages": [], "raw_ooni": [],
               "raw_tips": [], "radar": r}
        fc = feed.build_feed(blackout_cli.build_snapshot(raw))
        self.assertEqual(lint_errors(fc), {})
        p = next(f for f in fc["features"] if f["properties"]["id"] == "blackout:radar-outage:1752")["properties"]
        self.assertEqual(p["subject_id"], "blackout:radar-outage:1752")  # COP's ledger key is unchanged
        self.assertEqual((p["countries"], p["place_basis"]), (["CH"], "named"))
        self.assertTrue(p["title"].startswith("Switzerland (network's country), AS15796 SALT-CH:"), p["title"])
        self.assertIn("not a measured place", p["summary"])
        # Trends place it the same way.
        from blackout import trends
        self.assertEqual(trends._radar_countries(next(a for a in data["result"]["annotations"] if str(a["id"]) == "1752")),
                         ["CH"])

    def test_unread_endpoint_and_missing_token(self):
        r, problems = radar.fetch_radar(fetch=radar_fetch(fail=("anomalies",)), now=_utc(RADAR_NOW))
        self.assertEqual((r["read"]["anomalies"], r["anomalies"]), (False, []))
        self.assertEqual([p["endpoint"] for p in problems], ["anomalies"])
        with mock.patch.dict("os.environ", {radar.TOKEN_ENV: ""}):
            r, problems = radar.fetch_radar(now=_utc(RADAR_NOW))  # no token: nothing is fetched at all
        self.assertEqual(r["read"], {"annotations": False, "anomalies": False})
        self.assertTrue(all("CLOUDFLARE_RADAR_TOKEN is not set" in p["error"] for p in problems))


class TestCollect(unittest.TestCase):
    @mock.patch.object(blackout_cli.satcom, "fetch_traffic", side_effect=OSError("satcom down"))
    @mock.patch.object(blackout_cli.netblocks, "fetch", side_effect=OSError("bluesky appview down"))
    def test_live_collect_never_falls_back_to_seed(self, _netblocks, _satcom):
        tmp = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(blackout_cli, "ROOT", tmp), \
                    mock.patch.object(ioda, "fetch_ioda_live", side_effect=OSError("ioda down")), \
                    mock.patch.object(ooni, "fetch_ooni_live", side_effect=OSError("ooni down")), \
                    mock.patch.object(asn_watch, "fetch_asn_watch", side_effect=OSError("ioda asn down")), \
                    mock.patch.object(status_feeds, "fetch_status_feeds", side_effect=OSError("feeds down")), \
                    mock.patch.object(radar, "fetch_radar", side_effect=OSError("radar down")),                     mock.patch.object(bgp, "fetch_bgp", side_effect=OSError("radar bgp down")),                     mock.patch.object(iaea, "fetch_znpp", side_effect=OSError("iaea down")),                     mock.patch.object(stop, "fetch_stop", side_effect=OSError("sheet down")), mock.patch.object(odin, "fetch_odin", side_effect=OSError("odin down")), mock.patch.object(blackmarble, "fetch_blackmarble", side_effect=OSError("nasa down")), mock.patch.object(blackout_cli, "previous_nights", return_value=None), mock.patch.object(citizenlab, "fetch_testlists", side_effect=OSError("github down")), mock.patch.object(eia, "fetch_eia", side_effect=OSError("eia down")), \
                    mock.patch.object(blackout_cli, "previous_eia_hours", return_value=None), \
                    mock.patch.object(faa, "fetch_faa", side_effect=OSError("faa down")),                     mock.patch.object(bluesky, "fetch_bluesky", side_effect=OSError("bluesky down")),                     mock.patch.object(blackout_cli, "previous_gdelt_counts", return_value=None),                     mock.patch.object(gdelt, "fetch_gdelt", side_effect=OSError("gdelt down")),                     mock.patch.object(blackout_cli.trends, "fetch", side_effect=OSError("trends down")):
                snap = blackout_cli.collect(live=True)
            self.assertEqual(snap["mode"], "live")
            self.assertEqual((snap["outages"], snap["censorship"], snap["corridor_alerts"]), ([], [], []))
            self.assertEqual((snap["asn_watch"], snap["provider_incidents"], snap["provider_feeds"]), ([], [], []))
            self.assertIsNone(snap["provider_concurrency"])
            self.assertEqual({p["source"] for p in snap["collection"]["problems"]},
                             {"ioda", "ooni", "tips", "ioda-asn", "status", "radar", "radar:bgp", "iaea", "stop", "odin", "blackmarble", "eia", "citizenlab",
                              "faa", "bluesky", "gdelt", "trends", "netblocks", "satcom"})
            self.assertIsNone(snap["satcom"]["traffic"])            # the register is shown; no traffic read
            self.assertEqual(len(snap["satcom"]["networks"]), 117)
            self.assertIsNone(snap["radar"])
            self.assertIsNone(snap["bgp"])
            self.assertIsNone(snap["znpp"])
            self.assertIsNone(snap["stop"])
            self.assertIsNone(snap["odin"])
            self.assertIsNone(snap["nightlights"])
            self.assertIsNone(snap["testlists"])
            self.assertIsNone(snap.get("eia"))
            self.assertIsNone(snap["faa"])
            self.assertIsNone(snap["bluesky"])
            self.assertIsNone(snap["gdelt"])
            self.assertIsNone(snap["netblocks"])
            self.assertFalse((tmp / "data" / "netblocks.json").exists())
            self.assertFalse((tmp / "data" / gdelt.CARRY_NAME).exists())  # nothing read, nothing carried
            self.assertTrue((tmp / "data" / "snapshot.json").exists())
            self.assertFalse((tmp / "data" / eia.CARRY_NAME).exists())  # no hours read, none carried
        finally:
            shutil.rmtree(tmp, ignore_errors=True)



class DurationTextTests(unittest.TestCase):
    """Durations read at a glance (design review of 13 Nov 2026: Seychelles showed "for 3376 min")."""

    def test_minutes_hours_days(self):
        from blackout.ioda import duration_text
        cases = {45: "45 min", 60: "1 h", 75: "1 h 15 min", 599: "9 h 59 min", 600: "10 h", 3376: "56 h",
                 4320: "3 d", 4380: "3 d 1 h"}
        for minutes, text in cases.items():
            self.assertEqual(duration_text(minutes), text)

    def test_page_formats_durations_the_same_way(self):
        js = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function durText(minutes)", js)
        self.assertNotIn("duration_minutes} min", js)


class TestOutageReading(unittest.TestCase):
    """Today's reading separates drops that met BO-IND-1 from single-source dips (ideas#261, 24 Nov 2026)."""

    def reading(self, total, ind1):
        import json
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        js = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        start = js.index("function outageReading(")
        end = js.index("function renderReading(")
        code = ("const fmt = (v) => String(v);" + chr(10) + js[start:end]
                + chr(10) + f"console.log(JSON.stringify(outageReading({total}, {ind1})));")
        out = subprocess.run([node, "-e", code], capture_output=True, text=True, check=True, encoding="utf-8").stdout
        parts = json.loads(out)
        return None if parts is None else "".join(p if isinstance(p, str) else p["b"] for p in parts)

    def test_none_at_bo_ind_1_says_so_plainly(self):
        self.assertEqual(self.reading(15, 0),
                         "No outage met Blackout’s outage standard (BO-IND-1); 15 single-source dips measured by IODA")
        self.assertEqual(self.reading(1, 0),
                         "No outage met Blackout’s outage standard (BO-IND-1); 1 single-source dip measured by IODA")

    def test_some_at_bo_ind_1_come_first_then_the_rest_are_single_source(self):
        self.assertEqual(self.reading(15, 2), "2 outages at BO-IND-1 and 13 single-source dips, measured by IODA")
        self.assertEqual(self.reading(2, 1), "1 outage at BO-IND-1 and 1 single-source dip, measured by IODA")

    def test_all_at_bo_ind_1_have_no_single_source_part(self):
        self.assertEqual(self.reading(3, 3), "3 outages at BO-IND-1, measured by IODA")

    def test_no_drops_leave_the_part_out_and_no_shutdown_or_likelihood_is_claimed(self):
        self.assertIsNone(self.reading(0, 0))
        text = " ".join([self.reading(15, 0), self.reading(15, 2), self.reading(3, 3)]).lower()
        for word in ("shutdown", "likely", "probab"):
            self.assertNotIn(word, text)


class TestSourceHealth(unittest.TestCase):
    """The feed's health block: which source lanes were read this run (14 Nov 2026)."""

    def snap(self, *problems, last_ok=None):
        s = blackout_cli.build_snapshot()
        s["collection"] = {"problems": list(problems), "last_ok": last_ok or {}}
        return s

    def test_every_item_lane_is_in_the_health_block(self):
        fc = feed.build_feed(self.snap())
        lanes = {(f["properties"].get("sources") or [{}])[0].get("id") for f in fc["features"]}
        self.assertTrue(lanes)
        self.assertLessEqual(lanes, set(fc["health"]))
        self.assertTrue(all(v["state"] == "fresh" and v["last_ok"] == fc["generated_at"] for v in fc["health"].values()))
        self.assertEqual(lint_errors(fc), {})

    def test_each_problem_lands_on_its_own_lane(self):
        cases = {"ioda": "ioda-gatech", "ioda-asn": "ioda-gatech", "ooni": "ooni-api",
                 "radar:annotations": "cloudflare-radar", "radar:anomalies": "cloudflare-radar",
                 "radar:bgp-hijacks": "cloudflare-radar-bgp", "radar:bgp": "cloudflare-radar-bgp",
                 "status:aws": "aws-health", "iaea:iaea-rss": "iaea-ukraine-updates", "stop": "accessnow-stop",
                 "odin:odin-status": "odin-ornl", "blackmarble:tile-VNP46A2": "nasa-black-marble",
                 "eia:eia-region-data": "eia-930", "faa": "faa-atcscc", "bluesky": "bluesky-search",
                 "bluesky:chase": "bluesky-search", "gdelt": "gdelt-gkg", "gdelt:gkg-window": "gdelt-gkg",
                 "netblocks": "netblocks-bluesky", "netblocks:author-feed": "netblocks-bluesky"}
        for source, lane in cases.items():
            h = feed.source_health(self.snap({"source": source, "error": "HTTPError: HTTP Error 503"}))
            self.assertEqual({k for k, v in h.items() if v["state"] == "failing"}, {lane}, source)

    def test_the_whole_status_collector_failing_fails_every_status_lane(self):
        h = feed.source_health(self.snap({"source": "status", "error": "boom"}))
        self.assertEqual({k for k, v in h.items() if v["state"] == "failing"}, set(feed.STATUS_LANES.values()))

    def test_an_unplaced_problem_fails_every_lane(self):
        h = feed.source_health(self.snap({"source": "something-new", "error": "boom"}))
        self.assertTrue(all(v["state"] == "failing" for v in h.values()))

    def test_tips_citizen_lab_and_data_notes_fail_nothing(self):
        h = feed.source_health(self.snap(
            {"source": "tips:cop", "error": "timeout"}, {"source": "citizenlab", "error": "timeout"},
            {"source": "odin:odin-county", "error": "county FIPS not in the Gazetteer: 99999"},
            {"source": "eia:eia-places", "error": "XYZ has no point in eia.PLACES"}))
        self.assertTrue(all(v["state"] == "fresh" for v in h.values()))

    def test_a_failed_lane_keeps_its_last_success(self):
        h = feed.source_health(self.snap({"source": "ooni", "error": "boom"},
                                         last_ok={"ooni-api": "2026-11-14T16:20:00Z"}))
        self.assertEqual(h["ooni-api"]["last_ok"], "2026-11-14T16:20:00Z")
        self.assertNotEqual(h["ooni-api"]["last_ok"], h["ooni-api"]["checked_at"])
        self.assertEqual(h["ooni-api"]["problems"], 1)
        h = feed.source_health(self.snap({"source": "ooni", "error": "boom"}))
        self.assertIsNone(h["ooni-api"]["last_ok"])

    def test_the_kit_history_log_reads_the_block(self):
        sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
        try:
            from tic_doctrine import history
        except ImportError:
            self.skipTest("this kit has no history module yet")
        fc = feed.build_feed(self.snap({"source": "ooni", "error": "boom"}))
        ok = history.lanes_ok(fc["health"])
        self.assertNotIn("ooni-api", ok)
        self.assertIn("ioda-gatech", ok)



class TestRevision21(unittest.TestCase):
    """Feed contract revision 2.1 (kit 0.6.0, step 4.1 of the feed-contract migration, 18 Nov 2026)."""

    def snap(self, *problems, last_ok=None, failures=None):
        s = blackout_cli.build_snapshot()
        s["collection"] = {"problems": list(problems), "last_ok": last_ok or {}, "failures": failures or {}}
        return s

    def test_header_states_2_1_and_its_health_is_clean_at_2_1(self):
        fc = feed.build_feed(self.snap({"source": "ooni", "error": "HTTPError: HTTP Error 503"},
                                       {"source": "something-new", "error": "boom"}))
        self.assertEqual(fc["contract_rev"], "2.1")
        self.assertRegex(fc["kit"], r"^\d+\.\d+\.\d+$")
        header = {k: v for k, v in fc.items() if k != "features"}
        self.assertEqual([m for _, sev, m in contract.check_header(header) if sev == "error"], [])

    def test_a_failing_lane_counts_its_runs_in_a_row_and_names_its_error(self):
        h = feed.source_health(self.snap({"source": "ooni", "error": "HTTPError: HTTP Error 503"},
                                         failures={"ooni-api": 2, "ioda-gatech": 4}))
        self.assertEqual(h["ooni-api"]["state"], "failing")
        self.assertEqual(h["ooni-api"]["failures"], 3)
        self.assertIn("HTTP Error 503", h["ooni-api"]["last_error"])
        self.assertEqual(h["ioda-gatech"]["failures"], 0)       # read well again: the count resets
        self.assertIsNone(h["ioda-gatech"]["last_error"])
        self.assertEqual(h["ooni-api"]["problems"], 1)           # the older key stays beside the new ones

    def test_every_item_has_an_undated_subject_and_a_named_lane(self):
        fc = feed.build_feed(self.snap())
        self.assertTrue(fc["features"])
        for f in fc["features"]:
            p = f["properties"]
            self.assertTrue(p["subject_id"] == p["id"] or contract.SUBJECT_RE.match(p["subject_id"]), p["id"])
            self.assertIsNone(contract.DATED_RE.search(p["subject_id"]), p["subject_id"])
            self.assertIn(p["sources"][0]["lane"], fc["health"])
            if f.get("geometry"):
                self.assertIsInstance(p["sea_areas"], list)
        self.assertEqual(lint_errors(fc), {})

    def test_an_ooni_day_rollover_is_an_update_to_one_subject_not_new_and_ended(self):
        from datetime import datetime, timezone
        from tic_doctrine import history
        s1 = self.snap()
        cc = next(c for c in s1["censorship"] if c["anomaly_rate"] >= 0.15 and c["measurement_count"] >= 50)
        s1["censorship"] = [cc]
        fc1 = feed.build_feed(s1)
        sub = next(f["properties"]["subject_id"] for f in fc1["features"]
                   if f["properties"]["id"].startswith("blackout:ooni:"))
        state, _ = history.update({}, fc1, fc1["health"], datetime(2026, 11, 18, 13, tzinfo=timezone.utc))
        s2 = json.loads(json.dumps(s1))
        s2["censorship"][0]["day"] = "2099-01-02"
        s2["generated_at"] = s1["generated_at"].replace(s1["generated_at"][:4], str(int(s1["generated_at"][:4]) + 1))
        fc2 = feed.build_feed(s2)
        _, lines = history.update(state, fc2, fc2["health"], datetime(2026, 11, 19, 13, tzinfo=timezone.utc))
        ooni = [ln for ln in lines if ln.get("subject_id") == sub or ln["id"].startswith("blackout:ooni:")]
        self.assertEqual([ln["change"] for ln in ooni if ln["change"] in ("new", "ended")], [])


if __name__ == "__main__":
    unittest.main()
