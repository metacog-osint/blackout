"""The collection's time budget: a slow or hung source cannot use the time of the sources after it, and a file the
run did not rebuild keeps its last good copy, marked stale.

Measured case (update.yml runs of 26 Nov 2026): IODA did not answer, the IODA ASN watch waited its whole 300 s, EIA-930
ran to its 180 s deadline, and FAA, Bluesky, GDELT, Citizen Lab, tips, NetBlocks and trends were not started or were
cut short; data/trends.json was then deleted, so the page's Trends tab had nothing to load.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackout import (archive, asn_watch, bgp, blackmarble, bluesky, citizenlab, eia, faa, gdelt, iaea,  # noqa: E402
                      ioda, netblocks, odin, ooni, radar, satcom, status_feeds, stop, trends)

_spec = importlib.util.spec_from_file_location("blackout_cli_budget", ROOT / "tools" / "blackout.py")
blackout_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blackout_cli)

NOW = datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc)

# Every lane quick and down, as the live-collect tests in test_blackout.py mock them.
DOWN = [(ioda, "fetch_ioda_live"), (ooni, "fetch_ooni_live"), (status_feeds, "fetch_status_feeds"),
        (radar, "fetch_radar"), (bgp, "fetch_bgp"), (iaea, "fetch_znpp"), (stop, "fetch_stop"),
        (odin, "fetch_odin"), (blackmarble, "fetch_blackmarble"), (citizenlab, "fetch_testlists"),
        (satcom, "fetch_traffic")]
NO_PREVIOUS = ("previous_health", "previous_nights", "previous_eia_hours", "previous_gdelt_counts", "previous_trends",
               "previous_netblocks")


def _no_previous(stack, **keep):
    for name in NO_PREVIOUS:
        value = keep.get(name, {} if name == "previous_health" else None)
        stack.enter_context(mock.patch.object(blackout_cli, name, return_value=value, create=True))


def _budgets(stack, total, per_source, reserve, trends_reserve):
    stack.enter_context(mock.patch.object(blackout_cli, "COLLECT_BUDGET_S", total))
    stack.enter_context(mock.patch.object(blackout_cli, "SOURCE_BUDGET_S", per_source))
    stack.enter_context(mock.patch.object(blackout_cli, "SOURCE_BUDGETS_S", {}))
    stack.enter_context(mock.patch.object(blackout_cli, "RESERVE_S", reserve, create=True))
    stack.enter_context(mock.patch.object(blackout_cli, "RESERVES_S", {"trends": trends_reserve}, create=True))


def _empty_trends(now=None, netblocks_posts=None, satcom_daily=None):
    return trends.build([], [], [], NOW, [], netblocks_posts=netblocks_posts, satcom_daily=satcom_daily), []


class HungSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        blackout_cli.LET_GO.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _collect(self, stack, mocks):
        stack.enter_context(mock.patch.object(blackout_cli, "ROOT", self.tmp))
        for mod, name in DOWN:
            if (mod, name) not in mocks:
                stack.enter_context(mock.patch.object(mod, name, side_effect=OSError(f"{name} down")))
        got = {}
        for (mod, name), m in mocks.items():
            got[name] = stack.enter_context(mock.patch.object(mod, name, m))
        return blackout_cli.collect(live=True), got

    def test_a_hung_source_leaves_the_later_sources_their_time(self):
        """The ASN watch hangs and EIA takes its time: FAA, Bluesky, GDELT, NetBlocks and trends still run and
        publish, and EIA is still read."""
        def slow_eia(carry=None):
            time.sleep(2.0)
            raise OSError("eia answered late and failed")  # read, not starved: its own error is listed
        mocks = {(asn_watch, "fetch_asn_watch"): mock.Mock(side_effect=lambda: self.release.wait(30)),
                 (eia, "fetch_eia"): mock.Mock(side_effect=slow_eia),
                 (faa, "fetch_faa"): mock.Mock(side_effect=OSError("faa down")),
                 (bluesky, "fetch_bluesky"): mock.Mock(side_effect=OSError("bluesky down")),
                 (gdelt, "fetch_gdelt"): mock.Mock(side_effect=OSError("gdelt down")),
                 (netblocks, "fetch"): mock.Mock(return_value=([], [])),
                 (trends, "fetch"): mock.Mock(side_effect=_empty_trends)}
        with contextlib.ExitStack() as stack:
            _budgets(stack, total=6.0, per_source=4.5, reserve=0.15, trends_reserve=0.5)
            _no_previous(stack)
            snap, got = self._collect(stack, mocks)
        problems = snap["collection"]["problems"]
        by_source = {p["source"]: p["error"] for p in problems}
        self.assertIn("SourceTimeout", by_source["ioda-asn"])  # the hung source is let go and listed
        for name in ("fetch_faa", "fetch_bluesky", "fetch_gdelt", "fetch", "fetch_eia"):
            self.assertTrue(got[name].called, f"{name} was never started")
        self.assertFalse([p for p in problems if "not started" in p["error"]], problems)
        self.assertIn("eia answered late", by_source["eia"])
        self.assertNotIn("trends", by_source)
        self.assertNotIn("netblocks", by_source)
        doc = json.loads((self.tmp / "data" / trends.NAME).read_text(encoding="utf-8"))
        self.assertEqual(doc["format"], trends.FORMAT)
        self.assertNotIn("stale", doc)  # rebuilt this run
        self.assertTrue((self.tmp / "data" / netblocks.NAME).exists())
        self.assertIsNotNone(snap["netblocks"])

    def test_the_asn_watch_is_skipped_when_iodas_country_read_timed_out(self):
        asn = mock.Mock(side_effect=AssertionError("should not be called"))
        mocks = {(ioda, "fetch_ioda_live"): mock.Mock(side_effect=TimeoutError("The read operation timed out")),
                 (asn_watch, "fetch_asn_watch"): asn,
                 (eia, "fetch_eia"): mock.Mock(side_effect=OSError("eia down")),
                 (faa, "fetch_faa"): mock.Mock(side_effect=OSError("faa down")),
                 (bluesky, "fetch_bluesky"): mock.Mock(side_effect=OSError("bluesky down")),
                 (gdelt, "fetch_gdelt"): mock.Mock(side_effect=OSError("gdelt down")),
                 (netblocks, "fetch"): mock.Mock(return_value=([], [])),
                 (trends, "fetch"): mock.Mock(side_effect=_empty_trends)}
        with contextlib.ExitStack() as stack:
            _no_previous(stack)
            snap, _ = self._collect(stack, mocks)
        asn.assert_not_called()
        err = next(p["error"] for p in snap["collection"]["problems"] if p["source"] == "ioda-asn")
        self.assertTrue(err.startswith("SourceSkipped: skipped: IODA did not answer"), err)
        self.assertEqual(snap["collection"]["seconds"]["ioda-asn"], 0.0)

    def test_a_series_not_rebuilt_keeps_its_last_good_copy_marked_stale(self):
        last = trends.build([], [], [], NOW, [])
        last_nb = netblocks.document([], [], NOW)
        mocks = {(asn_watch, "fetch_asn_watch"): mock.Mock(side_effect=OSError("asn down")),
                 (eia, "fetch_eia"): mock.Mock(side_effect=OSError("eia down")),
                 (faa, "fetch_faa"): mock.Mock(side_effect=OSError("faa down")),
                 (bluesky, "fetch_bluesky"): mock.Mock(side_effect=OSError("bluesky down")),
                 (gdelt, "fetch_gdelt"): mock.Mock(side_effect=OSError("gdelt down")),
                 (netblocks, "fetch"): mock.Mock(side_effect=OSError("bluesky appview down")),
                 (trends, "fetch"): mock.Mock(side_effect=OSError("trends down"))}
        with contextlib.ExitStack() as stack:
            _no_previous(stack, previous_trends=last, previous_netblocks=last_nb)
            snap, _ = self._collect(stack, mocks)
        doc = json.loads((self.tmp / "data" / trends.NAME).read_text(encoding="utf-8"))
        self.assertEqual(doc["generated_at"], last["generated_at"])  # the last good copy, its own time kept
        self.assertIn("trends down", doc["stale"]["reason"])
        nb = json.loads((self.tmp / "data" / netblocks.NAME).read_text(encoding="utf-8"))
        self.assertIn("bluesky appview down", nb["stale"]["reason"])
        self.assertFalse((self.tmp / "data" / netblocks.FULL_NAME).exists())  # no posts read, none for the archive
        self.assertIsNone(snap["netblocks"])  # the page's NetBlocks items still come from this run only
        # A kept copy is never archived a second time (build publishes data/trends.json on any live run).
        self.assertEqual(archive.archive_trends(self.tmp / "history", doc, now=NOW), 0)


class BudgetUnitTests(unittest.TestCase):
    def tearDown(self):
        blackout_cli.LET_GO.clear()

    def test_time_is_kept_for_the_sources_still_to_come(self):
        hold = threading.Event()
        b = blackout_cli._Budget(total_s=1.0, per_source_s=10, overrides={}, order=("a", "b", "c"), reserve_s=0.3)
        self.assertAlmostEqual(b.kept_after("a"), 0.6)
        t0 = time.monotonic()
        with self.assertRaises(blackout_cli.SourceTimeout):
            b.run("a", hold.wait, 5)  # let go at about 1.0 - 0.6 s, not at the collection's whole second
        self.assertLess(time.monotonic() - t0, 0.8)
        self.assertEqual(b.run("b", lambda: "b read"), "b read")
        self.assertEqual(b.run("c", lambda: "c read"), "c read")
        hold.set()

    def test_a_source_read_beside_the_others_is_let_go_at_its_own_budget(self):
        hold = threading.Event()
        b = blackout_cli._Budget(total_s=30, per_source_s=0.3, overrides={})
        b.start("eia", hold.wait, 5)
        b.start("iaea", lambda: "read")
        self.assertEqual(b.wait("iaea"), "read")
        with self.assertRaises(blackout_cli.SourceTimeout):
            b.wait("eia")
        self.assertEqual(blackout_cli.LET_GO, ["eia"])
        hold.set()

    def test_previous_netblocks_cuts_old_excerpts_again(self):
        doc = {"format": netblocks.FORMAT, "mode": "live", "generated_at": "2026-11-20T00:00:00Z",
               "posts": [{"id": "old", "posted_at": "2026-11-20T00:00:00Z", "excerpt": "x"},
                         {"id": "new", "posted_at": "2026-11-27T03:00:00Z", "excerpt": "y"}]}
        with mock.patch.object(blackout_cli, "_published_json", return_value=doc):
            kept = blackout_cli.previous_netblocks(NOW)
        posts = {p["id"]: p for p in kept["posts"]}
        self.assertNotIn("excerpt", posts["old"])
        self.assertEqual(posts["new"]["excerpt"], "y")

    def test_the_trends_tab_marks_a_kept_copy_stale(self):
        app = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        self.assertIn('doc.stale ? h("strong", {}, "Stale: ', app)


if __name__ == "__main__":
    unittest.main()
