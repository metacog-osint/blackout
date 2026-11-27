"""tools/satcom_replay.py: parts stitched onto one scale, the real drop rule judging each hour, episodes,
controls. Offline: the real series is the recorded 4 Feb 2026 Starlink-in-Ukraine answer."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

from blackout import satcom  # noqa: E402
import satcom_replay as R  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "satcom"
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
BY_URL = {u: n for n, u in MANIFEST["files"].items()}
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def recorded(name):
    return satcom._one(json.loads((FIX / BY_URL[MANIFEST["replay_urls"][name]]).read_text(encoding="utf-8")))


def stamp(h):
    return (T0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestStitch(unittest.TestCase):
    def test_a_later_part_is_put_on_the_first_parts_scale(self):
        a = [(stamp(h), 0.5 + h / 1000) for h in range(24)]
        b = [(stamp(h), (0.5 + h / 1000) * 2) for h in range(12, 36)]
        s = R.stitch([a, b])
        self.assertEqual(len(s.times), 36)
        self.assertEqual(s.breaks, [])
        self.assertAlmostEqual(s.values[30], 0.53)

    def test_parts_with_too_little_overlap_are_a_break_not_a_guess(self):
        a = [(stamp(h), 0.5) for h in range(24)]
        b = [(stamp(h), 0.9) for h in range(22, 40)]
        self.assertEqual(R.stitch([a, b]).breaks, [T0 + timedelta(hours=22)])

    def test_a_window_across_a_break_is_not_judged(self):
        days = R.PART_DAYS
        a = [(stamp(h), 0.5 + (h % 97) / 500) for h in range(days * 24)]
        b = [(stamp(h), 0.5 + (h % 97) / 500) for h in range(days * 24 - 1, days * 24 + 48)]
        r = R.replay(R.stitch([a, b]))
        self.assertGreater(r["states"].get("no_scale", 0), 0)


class TestReplay(unittest.TestCase):
    def test_the_replay_agrees_with_the_live_rule_on_a_real_answer(self):
        pts = recorded("UAstarlink")
        got = R.replay(R.stitch([pts]))
        live = satcom.drop_rule(pts)
        self.assertEqual(set(got["states"]), {live["state"]})  # a 697-point answer holds two full 29-day windows
        self.assertEqual(sum(got["states"].values()), 2)

    def test_the_gates_are_restored_afterwards(self):
        real = satcom.sparse, satcom.quantum
        R.replay(R.stitch([recorded("UAstarlink")]))
        self.assertEqual((satcom.sparse, satcom.quantum), real)


class TestEpisodes(unittest.TestCase):
    def fired(self, h):
        return {"at": stamp(h), "pct": 60.0 - h / 100, "prior_pct": 65.0, "since": stamp(h - 6), "hours_below": 7,
                "standing": False}

    def test_near_hours_are_one_episode_and_timings_are_counted(self):
        e = R.episodes([self.fired(h) for h in (1, 2, 8)])
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["timings"], e[0]["run_hours_mod_4"]), (3, [0, 1, 2]))

    def test_a_long_gap_starts_a_new_episode(self):
        self.assertEqual(len(R.episodes([self.fired(1), self.fired(1 + R.EPISODE_GAP_HOURS + 1)])), 2)


class TestClassify(unittest.TestCase):
    def row(self, **k):
        return {"world_all_low_pct": 100.0, "country_all_low_pct": None, "evidence": [], "holiday": None, **k}

    def test_a_radar_wide_dip_outranks_a_report_near_it(self):
        self.assertEqual(R.classify(self.row(world_all_low_pct=13.0, evidence=["x"])), "radar_wide")

    def test_a_radar_wide_dip_on_a_holiday_is_the_holiday(self):
        self.assertEqual(R.classify(self.row(world_all_low_pct=80.0, holiday="Christmas")), "near_holiday")

    def test_the_rest(self):
        self.assertEqual(R.classify(self.row(evidence=["x"])), "independent_report")
        self.assertEqual(R.classify(self.row(country_all_low_pct=70.0)), "whole_country_fell")
        self.assertEqual(R.classify(self.row()), "network_only_unexplained")


class TestShortReading(unittest.TestCase):
    def test_hours_in_a_row_and_the_count_floor(self):
        rd = [(T0 + timedelta(hours=h), p, c) for h, p, c in
              ((0, 100, 500), (1, 40, 500), (2, 100, 500), (3, 40, 500), (4, 40, 500), (5, 40, 50))]
        self.assertEqual(R.short_hits(rd, 60, 1, 100), [T0 + timedelta(hours=h) for h in (1, 3, 4)])
        self.assertEqual(R.short_hits(rd, 60, 2, 100), [T0 + timedelta(hours=h) for h in (3, 4)])


if __name__ == "__main__":
    unittest.main()
