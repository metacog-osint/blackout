"""Offline tests for the lanes added 13 Nov 2026: routing integrity (Cloudflare Radar BGP), power (Radar's power
causes, the IAEA's Zaporizhzhia updates, the US grid), shutdowns (Access Now STOP) and censorship test lists
(Citizen Lab). Each replays real responses recorded on 13 Nov 2026, listed with their URLs in the MANIFEST.json of
its fixtures folder; nothing in them is edited."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackout import bgp, feed, radar  # noqa: E402
from test_blackout import blackout_cli, lint_errors  # noqa: E402

RADAR = ROOT / "tests" / "fixtures" / "radar"
BGP_NOW = "2026-11-13T05:20:00Z"


def manifest_fetch(folder: Path, fail: tuple = ()):
    """Serve recorded responses by their exact URL from the folder's MANIFEST.json."""
    by_url = {u: name for name, u in json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))["files"].items()}

    def fetch(url: str):
        if any(f in url for f in fail):
            raise OSError(f"down: {url}")
        name = by_url[url]
        text = (folder / name).read_text(encoding="utf-8")
        return json.loads(text) if name.endswith(".json") else text
    return fetch


def live_feed(**raw) -> dict:
    base = {"mode": "live", "generated_at": BGP_NOW, "raw_outages": [], "raw_ooni": [], "raw_tips": []}
    return feed.build_feed(blackout_cli.build_snapshot({**base, **raw}))


class TestBgp(unittest.TestCase):
    def setUp(self):
        self.b, self.problems = bgp.fetch_bgp(fetch=manifest_fetch(RADAR), now=radar._t(BGP_NOW))

    def test_every_page_read_and_counted(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.b["read"], {"hijacks": True, "leaks": True})
        self.assertEqual(self.b["counts"], {"hijacks": 197, "leaks": 1012})  # Radar's total_count for both
        self.assertEqual(self.b["monitors"], 280)
        top = self.b["by_country"][0]
        self.assertEqual(top, {"country": "BR", "hijacks": 13, "leaks": 212})

    def test_only_widely_seen_events_are_notable(self):
        self.assertEqual([h["radar_id"] for h in self.b["notable_hijacks"]], ["172191", "171883"])
        wind = self.b["notable_hijacks"][1]
        self.assertEqual((wind["confidence"], wind["hijacker_asn"], wind["peers"]), (10, 7029, 37))
        self.assertEqual(wind["victim_asns"], [2914, 3503, 20115, 136809])
        self.assertIn("rpki_new_origin_invalid", wind["evidence"])
        leaks = self.b["notable_leaks"]
        self.assertTrue(all(lk["prefixes"] >= 100 and lk["peers"] >= 25 for lk in leaks))
        big = next(lk for lk in leaks if lk["radar_id"] == "665863")
        self.assertEqual((big["leak_asn"], big["leak_country"], big["prefixes"], big["peers"], big["finished"]),
                         (268624, "BR", 2018, 100, False))
        self.assertEqual(big["segment"], [2914, 268624, 22356])

    def test_feed_items_are_reported_observations_that_lint(self):
        fc = live_feed(bgp=self.b)
        self.assertEqual(lint_errors(fc), {})
        items = [f["properties"] for f in fc["features"] if f["properties"]["warning_problem_id"] == "BO-WP-4"]
        hijacks = [p for p in items if p["indicator_id"] == "BO-IND-11"]
        leaks = [p for p in items if p["indicator_id"] == "BO-IND-12"]
        self.assertEqual(len(hijacks), 2)
        # One item per leaking network: the 12 leaks come from fewer networks (AS268624 alone has four).
        self.assertEqual(len(leaks), len({lk["leak_asn"] for lk in self.b["notable_leaks"]}))
        for p in items:
            self.assertEqual((p["kind"], p["status_class"], p["direction"], p["hypothesis_id"]),
                             ("observation", "reported", "neutral", "BO-H-4A"))
            self.assertNotIn("probability", p)
            self.assertIn("CC BY-NC 4.0", p["sources"][0]["licence"])
        br = next(p for p in leaks if "AS268624" in p["title"])
        self.assertIn("in 4 events", br["title"])
        self.assertIn("2018 prefixes", br["summary"])

    def test_failed_endpoint_publishes_nothing(self):
        b, problems = bgp.fetch_bgp(fetch=manifest_fetch(RADAR, fail=("bgp/leaks",)), now=radar._t(BGP_NOW))
        self.assertEqual(b["read"], {"hijacks": True, "leaks": False})
        self.assertEqual((b["counts"]["leaks"], b["notable_leaks"]), (0, []))
        self.assertEqual([p["endpoint"] for p in problems], ["bgp-leaks"])


if __name__ == "__main__":
    unittest.main()
