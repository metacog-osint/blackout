"""Offline tests for Blackout (IODA, OONI, submarine cable corridors, doctrine registers, contract v2 feed).

The live-collector tests replay real API responses (tests/fixtures/ioda_live_2026-11-12.json, recorded from IODA v2
on 12 Nov 2026; the OONI counts below are the live aggregation for Iran, 10-11 Nov 2026), so a change in how the
code reads the APIs shows up here rather than on the site. The provider watch replays IODA ASN responses
(ioda_asn_*.json) and provider status feeds (fixtures/status/, listed with their URLs in MANIFEST.json)."""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackout import asn_watch, cables, feed, ioda, ooni, status_feeds  # noqa: E402
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
        self.assertEqual(progs["wake"]["status"], "planned")
        # Live since 12 Nov 2026 (kit 0.4.3), publishing real data at its own address.
        bo = progs["blackout"]
        self.assertEqual(bo["status"], "live")
        self.assertNotIn("status_note", bo)
        self.assertEqual(bo["feed"], "https://blackout.theintelligence.company/data/feed.geojson")
        self.assertNotIn("TeleGeography)", bo["terms"])  # no TeleGeography data is ingested, so no licence is claimed

    def test_licence_claims_match_the_sources(self):
        # OONI data is CC BY-NC-SA 4.0 (github.com/ooni/license, data/LICENSE.md, read 12 Nov 2026); IODA states
        # no data licence. Nothing Blackout publishes may claim CC BY 4.0 for either.
        reg = json.loads((ROOT / "doctrine" / "kit" / "vocab" / "programs.json").read_text(encoding="utf-8"))
        terms = {p["id"]: p for p in reg["programs"]}["blackout"]["terms"]
        fc = feed.build_feed(blackout_cli.build_snapshot())
        self.assertEqual(fc["licence"], terms)
        self.assertIn("CC BY-NC-SA 4.0", fc["attribution"])
        self.assertIn("CC BY-NC-SA 4.0", feed.SRC_OONI["licence"])
        self.assertIn("no data licence", feed.SRC_IODA["licence"].lower())
        texts = {
            "feed": json.dumps(fc),
            "doctrine/sources.json": (ROOT / "doctrine" / "sources.json").read_text(encoding="utf-8"),
            "site_src/index.html": (ROOT / "site_src" / "index.html").read_text(encoding="utf-8"),
        }
        for name, text in texts.items():
            self.assertNotIn("CC BY 4.0", text, name)

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
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestIoda(unittest.TestCase):
    def test_live_ioda_replay_measures_real_drops(self):
        out, stats = ioda.fetch_ioda_live(fetch=fixture_fetch, now=FIXTURE["now"])
        self.assertEqual(stats["countries_checked"], 3)
        self.assertEqual(stats["problems"], [])
        by = {o["country"]: o for o in out}
        self.assertEqual(set(by), {"TN", "SC", "VE"})
        self.assertEqual(len({o["id"] for o in out}), len(out))

        tn = by["TN"]  # active probing fell 38% for 65 min while BGP moved 8.6%
        self.assertEqual(tn["country_name"], "Tunisia")
        self.assertEqual((tn["bgp_drop_pct"], tn["probing_drop_pct"], tn["darknet_drop_pct"]), (8.6, 38.1, 0.0))
        self.assertEqual(tn["start_time"], "2026-11-12T00:25:00Z")
        self.assertEqual(tn["duration_minutes"], 65)
        self.assertEqual(tn["indicator_id"], "BO-IND-1")
        self.assertEqual(tn["ioda_score"], 558.4)

        sc = by["SC"]  # darknet-only dip with BGP steady: a single-source artefact
        self.assertEqual(sc["indicator_id"], "BO-IND-4")
        self.assertEqual(sc["darknet_drop_pct"], 48.1)

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

    def test_related_tips_do_not_raise_shutdown_likelihood(self):
        raw = {
            "mode": "live",
            "generated_at": "2026-11-01T12:00:00Z",
            "raw_outages": [outage("IR", "2026-11-01T09:00:00Z", bgp=31.2, probing=29.0)],
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
        self.assertEqual(ir["probability"], 0.80)  # IODA + OONI, as without the tip
        self.assertFalse(any(s.get("source_type") == "suite_program" for s in ir["sources"]))

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
              "m365": "mscloud_posts_mac_2026-11-12.json"}
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
        self.assertEqual({i["feed"] for i in incs}, {"aws", "m365"})


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
        self.assertEqual(sorted(p["indicator_id"] for p in prov), ["BO-IND-6", "BO-IND-7", "BO-IND-7", "BO-IND-7"])
        for p in prov:
            self.assertEqual((p["kind"], p["tier"], p["direction"], p["status_class"]),
                             ("observation", "anomaly", "neutral", "reported"))
            self.assertNotIn("probability", p)
            self.assertIn("baseline_ref", p)
        aws = [p for p in prov if p["indicator_id"] == "BO-IND-7" and p["sources"][0]["id"] == "aws-health"]
        self.assertEqual({p["title"] for p in aws}, {"AWS: Increased Error Rates (me-central-1)",
                                                     "AWS: Increased Error Rates (me-south-1)"})
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

    def test_archived_open_incidents_lint(self):
        feeds = {**LIVE_FEEDS, "gcp": "gcp_incidents_wayback_20250612200641.json",
                 "azure": "azure_feed_wayback_20240730171154.rss"}
        fc = feed.build_feed(self._snap(feeds))
        self.assertEqual(lint_errors(fc), {})
        ids = [f["properties"]["id"] for f in fc["features"]]
        self.assertIn("blackout:provider-status:gcp:ow5i3ppk96rdumcb1ssw", ids)
        self.assertIn("blackout:provider-status:azure:"
                      "network-infrastructure-issues-accessing-a-subset-of-microsoft-services", ids)

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


class TestCollect(unittest.TestCase):
    def test_live_collect_never_falls_back_to_seed(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(blackout_cli, "ROOT", tmp), \
                    mock.patch.object(ioda, "fetch_ioda_live", side_effect=OSError("ioda down")), \
                    mock.patch.object(ooni, "fetch_ooni_live", side_effect=OSError("ooni down")), \
                    mock.patch.object(asn_watch, "fetch_asn_watch", side_effect=OSError("ioda asn down")), \
                    mock.patch.object(status_feeds, "fetch_status_feeds", side_effect=OSError("feeds down")):
                snap = blackout_cli.collect(live=True)
            self.assertEqual(snap["mode"], "live")
            self.assertEqual((snap["outages"], snap["censorship"], snap["corridor_alerts"]), ([], [], []))
            self.assertEqual((snap["asn_watch"], snap["provider_incidents"], snap["provider_feeds"]), ([], [], []))
            self.assertIsNone(snap["provider_concurrency"])
            self.assertEqual({p["source"] for p in snap["collection"]["problems"]},
                             {"ioda", "ooni", "tips", "ioda-asn", "status"})
            self.assertTrue((tmp / "data" / "snapshot.json").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
