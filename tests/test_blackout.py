"""Offline tests for Blackout (IODA, OONI, submarine cable corridors, doctrine registers, contract v2 feed).

The live-collector tests replay real API responses (tests/fixtures/ioda_live_2026-11-12.json, recorded from IODA v2
on 12 Nov 2026; the OONI counts below are the live aggregation for Iran, 10-11 Nov 2026), so a change in how the
code reads the APIs shows up here rather than on the site."""
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

from blackout import cables, feed, ioda, ooni  # noqa: E402
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
        status = {p["id"]: p["status"] for p in reg["programs"]}
        self.assertEqual(status["wake"], "planned")
        self.assertEqual(status["blackout"], "planned")

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


class TestCollect(unittest.TestCase):
    def test_live_collect_never_falls_back_to_seed(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(blackout_cli, "ROOT", tmp), \
                    mock.patch.object(ioda, "fetch_ioda_live", side_effect=OSError("ioda down")), \
                    mock.patch.object(ooni, "fetch_ooni_live", side_effect=OSError("ooni down")):
                snap = blackout_cli.collect(live=True)
            self.assertEqual(snap["mode"], "live")
            self.assertEqual((snap["outages"], snap["censorship"], snap["corridor_alerts"]), ([], [], []))
            self.assertEqual({p["source"] for p in snap["collection"]["problems"]}, {"ioda", "ooni", "tips"})
            self.assertTrue((tmp / "data" / "snapshot.json").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
