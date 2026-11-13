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

from blackout import bgp, feed, iaea, radar, stop  # noqa: E402
from test_blackout import blackout_cli, lint_errors  # noqa: E402

RADAR = ROOT / "tests" / "fixtures" / "radar"
IAEA = ROOT / "tests" / "fixtures" / "iaea"
IAEA_NOW = "2026-11-13T06:00:00Z"
BGP_NOW = "2026-11-13T05:20:00Z"


def manifest_fetch(folder: Path, fail: tuple = (), raw: bool = False):
    """Serve recorded responses by their exact URL from the folder's MANIFEST.json (bytes when raw)."""
    by_url = {u: name for name, u in json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))["files"].items()}

    def fetch(url: str):
        if any(f in url for f in fail):
            raise OSError(f"down: {url}")
        name = by_url[url]
        if raw:
            return (folder / name).read_bytes()
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



def update_bytes(n: int) -> bytes:
    return next(IAEA.glob(f"update_{n}_*.html.gz")).read_bytes()


class TestZnpp(unittest.TestCase):
    def test_newest_update_reads_single_line(self):
        z, problems = iaea.fetch_znpp(fetch=manifest_fetch(IAEA, raw=True), now=radar._t(IAEA_NOW))
        self.assertEqual(problems, [])
        self.assertEqual((z["number"], z["date"], z["state"], z["newest_number"], z["overdue"]),
                         (374, "2026-11-06", "single", 374, False))
        self.assertIn("continues to rely on a single external power line", z["quotes"][0])
        self.assertEqual(z["link"], "https://www.iaea.org/newscenter/pressreleases/"
                                    "update-374-iaea-director-general-statement-on-situation-in-ukraine")

    def test_states_across_the_august_loss_of_offsite_power(self):
        # 20 Aug - 7 Sep 2026: the plant lost its only line on 20 Aug; 364 and 365 were issued during the loss.
        self.assertEqual(iaea.parse_update(update_bytes(364), 364)["state"], "none")
        self.assertEqual(iaea.parse_update(update_bytes(365), 365)["state"], "none")
        u366 = iaea.parse_update(update_bytes(366), 366)  # reports the reconnection; says nothing current itself
        self.assertEqual((u366["state"], u366["loop_count"]), ("not_stated", 26))
        self.assertIn("had lost connection", u366["losses"][0])
        u361 = iaea.parse_update(update_bytes(361), 361)  # two brief losses: a loss report, not a current state
        self.assertEqual(u361["state"], "not_stated")
        self.assertIn("lost connection to its sole remaining", u361["losses"][0])

    def test_failed_capture_falls_back_and_newer_is_named(self):
        z, problems = iaea.fetch_znpp(fetch=manifest_fetch(IAEA, fail=("update-374",), raw=True),
                                      now=radar._t(IAEA_NOW))
        self.assertEqual(z["number"], 373)
        self.assertEqual(z["newer_unread"], [374])
        self.assertEqual([p["endpoint"] for p in problems], ["wayback-update-374"])

    def test_overdue_when_nothing_newer(self):
        recorded = manifest_fetch(IAEA, raw=True)

        def fetch(url):  # a week later the CDX query starts a week later too; serve the recorded index
            return recorded(url.replace("from=20260921", "from=20260914"))
        z, _ = iaea.fetch_znpp(fetch=fetch, now=radar._t("2026-11-20T06:00:00Z"))
        self.assertEqual((z["number"], z["age_days"], z["overdue"]), (374, 14, True))

    def test_feed_item_quotes_the_iaea_and_lints(self):
        z, _ = iaea.fetch_znpp(fetch=manifest_fetch(IAEA, raw=True), now=radar._t(IAEA_NOW))
        fc = live_feed(znpp=z)
        self.assertEqual(lint_errors(fc), {})
        p = next(f["properties"] for f in fc["features"] if f["properties"]["indicator_id"] == "BO-IND-13")
        self.assertEqual(p["title"], "Zaporizhzhya NPP: on a single off-site power line (IAEA Update 374, 6 Nov 2026)")
        self.assertEqual((p["kind"], p["status_class"], p["indicator_state"], p["direction"]),
                         ("observation", "reported", "present", "neutral"))
        self.assertIn("“Multiple lines remain available", p["summary"])
        self.assertIn("acknowledgement of the IAEA", p["sources"][0]["licence"])
        # An update that states nothing current hands over to the newest one that does.
        u366 = iaea.parse_update(update_bytes(366), 366)
        u366["stated_in"] = {**iaea.parse_update(update_bytes(365), 365), "link": "x", "archived": "y"}
        p = next(f["properties"] for f in live_feed(znpp=u366)["features"]
                 if f["properties"]["indicator_id"] == "BO-IND-13")
        self.assertEqual(p["title"], "Zaporizhzhya NPP: without off-site power (IAEA Update 365, 5 Sep 2026)")
        self.assertIn("Update 366 (11 Sep 2026) says nothing current", p["summary"])



STOP_CSV = ROOT / "tests" / "fixtures" / "stop" / "stop_sheet_2026-11-13.csv"


class TestStop(unittest.TestCase):
    def setUp(self):
        self.s, self.problems = stop.fetch_stop(fetch=lambda url: STOP_CSV.read_text(encoding="utf-8-sig")
                                                if url == stop.CSV_URL else None)

    def test_every_country_matched_and_counted(self):
        self.assertEqual(self.problems, [])
        self.assertEqual((self.s["year"], self.s["rows"], len(self.s["countries"])), ("2025", 313, 52))
        mm = self.s["countries"][0]
        self.assertEqual((mm["country"], mm["shutdowns"], mm["ongoing_at_year_end"]), ("MM", 95, 14))
        ir = next(c for c in self.s["countries"] if c["country"] == "IR")  # "Iran (Islamic Republic of)" in the sheet
        self.assertEqual((ir["shutdowns"], ir["latest_start"]), (11, "2025-12-29"))
        # One row names two countries ("Cameroon; Central African Republic"); both are counted.
        self.assertTrue({"CM", "CF"} <= {c["country"] for c in self.s["countries"]})

    def test_history_is_context_and_never_a_judgment(self):
        from test_blackout import outage
        o = outage("IR", "2026-11-13T02:00:00Z", bgp=40.0, probing=38.0, minutes=180)
        fc = live_feed(raw_outages=[o], stop=self.s)
        self.assertEqual(lint_errors(fc), {})
        p = next(f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:outage:ir"))
        self.assertIn("STOP sheet lists 11 shutdowns here in 2025", p["summary"])
        self.assertEqual((p["kind"], p["direction"]), ("observation", "neutral"))  # still no OONI, still no judgment
        self.assertIn("accessnow-stop", [s["id"] for s in p["sources"]])
        self.assertIn("No licence stated", next(s for s in p["sources"] if s["id"] == "accessnow-stop")["licence"])

    def test_unreadable_sheet_publishes_nothing(self):
        s, problems = stop.fetch_stop(fetch=lambda url: "not,a,stop,sheet\n1,2,3,4\n")
        self.assertIsNone(s)
        self.assertEqual(problems[0]["endpoint"], "stop-sheet")


if __name__ == "__main__":
    unittest.main()
