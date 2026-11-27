"""Offline tests for the FAA ground-stop lane (BO-WP-6, BO-IND-17), added 17 Nov 2026, and the GitHub status feed.

Each test replays real pages from the FAA's ATCSCC advisories database, recorded by tests/make_faa_fixtures.py and
listed with their URLs in tests/fixtures/faa/MANIFEST.json; nothing in them is edited. The days are the ones the rule
was built on: the nationwide ground stop of 13 Nov 2026, United's own outage of 6 Aug 2025, the CrowdStrike update of
19 Jul 2024, and an ordinary day (16 Nov 2026)."""
from __future__ import annotations

import gzip
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackout import faa, feed, status_feeds  # noqa: E402
from test_blackout import blackout_cli, lint_errors  # noqa: E402

FAA = ROOT / "tests" / "fixtures" / "faa"
PAGES = json.loads((FAA / "MANIFEST.json").read_text(encoding="utf-8"))["pages"]


def faa_fetch(fail: tuple = ()):
    def fetch(url: str) -> bytes:
        if any(f in url for f in fail):
            raise OSError(f"down: {url}")
        return gzip.decompress((FAA / PAGES[url]["file"]).read_bytes())
    return fetch


def at(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read(ts: str, fail: tuple = ()):
    return faa.fetch_faa(at(ts), fetch=faa_fetch(fail), pause=0)


def live_feed(ts: str, faa_snap: dict, problems: list | None = None) -> dict:
    raw = {"mode": "live", "generated_at": ts, "raw_outages": [], "raw_ooni": [], "raw_tips": [], "faa": faa_snap,
           "collection": {"problems": problems or []}}
    return feed.build_feed(blackout_cli.build_snapshot(raw))


def by_kind(snap: dict) -> dict:
    out: dict = {}
    for e in snap["events"]:
        out.setdefault(e["kind"], []).append(e)
    return out


class TestFaaGroundStops(unittest.TestCase):
    def test_nationwide_ground_stop_in_effect(self):
        # 23:00 UTC 13 Nov 2026: ATCSCC 111 (20:54, "ALL AIRPORTS ARE GROUND STOPPED") and 120 (22:46, "REASON:
        # COMMUNICATIONS FAILURE") are one event, in effect; ORD and MDW equipment stops came 27 and 24 min before.
        snap, problems = read("2026-11-13T23:00:00Z")
        self.assertEqual(problems, [])
        k = by_kind(snap)
        self.assertEqual(sorted(k), ["equipment", "national"])
        nat = k["national"][0]
        self.assertEqual((nat["started_at"], nat["in_effect"], nat["condition"]),
                         ("2026-11-13T20:54:00Z", True, "COMMUNICATIONS FAILURE"))
        self.assertEqual([a["advn"] for a in nat["advisories"]], [111, 120])
        self.assertEqual(sorted(e["key"] for e in k["equipment"]), ["MDW", "ORD"])
        self.assertTrue(all(e["cluster"] == ["MDW", "ORD"] for e in k["equipment"]))
        ord_ = next(e for e in k["equipment"] if e["key"] == "ORD")
        self.assertEqual((ord_["condition"], ord_["comments"]), ("EQUIPMENT / OUTAGE", "COMMS ISSUES"))

    def test_nationwide_ground_stop_cancelled(self):
        # Read the next morning: the FAA's cancellation at 01:19 UTC 14 Nov ends it, and it is still listed (24 h).
        snap, _ = read("2026-11-14T12:00:00Z")
        nat = by_kind(snap)["national"][0]
        self.assertEqual((nat["cancelled_at"], nat["in_effect"]), ("2026-11-14T01:19:00Z", False))
        # A later ORD cancellation (03:28 UTC) belongs to a weather stop, not to the equipment stop that ended at 22:30.
        ord_ = next(e for e in by_kind(snap)["equipment"] if e["key"] == "ORD")
        self.assertIsNone(ord_["cancelled_at"])
        self.assertEqual(snap["events"], [e for e in snap["events"] if e["started_at"] >= "2026-11-13T00:00:00Z"])

    def test_dropped_after_a_day(self):
        snap, _ = faa.fetch_faa(at("2026-11-16T23:59:00Z"), fetch=faa_fetch(), pause=0)
        self.assertFalse([e for e in snap["events"] if e["kind"] == "national"])

    def test_carrier_requests_at_several_airports(self):
        # 6 Aug 2025, United's own outage: SFO, IAH, ORD, DEN and EWR stopped 23:42-23:47 UTC "CARRIER REQUEST".
        snap, problems = read("2025-08-07T00:30:00Z")
        self.assertEqual(problems, [])
        self.assertEqual([e["kind"] for e in snap["events"]], ["carrier"])
        e = snap["events"][0]
        self.assertEqual((e["airports"], e["carriers"], e["in_effect"]), (["DEN", "EWR", "IAH", "ORD", "SFO"], ["UAL"], True))
        # The weather (DEN, MCO, TPA, SEA) and volume (DCA) stops of that day are left out.
        self.assertGreater(snap["ground_stops"], 10)

    def test_airline_ground_stops_on_the_crowdstrike_day(self):
        # 19 Jul 2024: whole-airline stops, "COMMUNICATION ISSUE" or "IT ISSUE"; Frontier's of 18 Jul 22:57 UTC came
        # with the Azure Central US outage that evening.
        snap, problems = read("2024-07-19T12:00:00Z")
        airlines = {e["key"]: e for e in snap["events"] if e["kind"] == "airline"}
        self.assertEqual(set(airlines), {"AAL", "AAY", "DAL", "FFT", "NKS", "PSA", "UAL"})
        self.assertEqual(airlines["DAL"]["cancelled_at"], "2024-07-19T11:51:00Z")
        self.assertEqual(airlines["UAL"]["airline"], "United Airlines")
        self.assertIn(airlines["AAL"]["condition"], ("IT ISSUE", "COMMUNICATION ISSUE"))
        # One archived page of 18 Jul holds no advisory text; it is a problem, never filled in.
        self.assertEqual([p["endpoint"] for p in problems], ["faa:advisory"])

    def test_ordinary_day_keeps_only_the_equipment_stop(self):
        # 16 Nov 2026: 15 ground stops, 14 of them weather, volume, runway or construction; LAS's approach lights
        # stop is equipment, one airport, so routine.
        snap, _ = read("2026-11-16T23:59:00Z")
        self.assertEqual([(e["kind"], e["key"]) for e in snap["events"]], [("equipment", "LAS")])
        fc = live_feed("2026-11-16T23:59:00Z", snap)
        items = [f["properties"] for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-17"]
        self.assertEqual([p["tier"] for p in items], ["routine"])

    def test_feed_items_are_reported_observations_that_lint(self):
        snap, _ = read("2026-11-13T23:00:00Z")
        fc = live_feed("2026-11-13T23:00:00Z", snap)
        self.assertEqual(lint_errors(fc), {})
        items = {f["properties"]["id"]: f for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-17"}
        nat = items["blackout:faa:national:national:2026-11-13T20"]
        p = nat["properties"]
        self.assertEqual((p["kind"], p["tier"], p["status_class"], p["direction"]), ("observation", "anomaly", "reported", "neutral"))
        self.assertEqual((p["warning_problem_id"], p["hypothesis_id"], p["requirement_ids"], p["ncf"]),
                         ("BO-WP-6", "BO-H-6A", ["BO-PIR-11"], ["transport_by_air"]))
        self.assertEqual(p["title"], "FAA ground stop at every US airport (in effect)")
        self.assertIn("Condition the FAA names: COMMUNICATIONS FAILURE.", p["summary"])
        self.assertIn("does not assess why", p["summary"])
        self.assertEqual(p["sources"][0]["id"], "faa-atcscc")
        self.assertTrue(p["sources"][0]["url"].startswith("https://www.fly.faa.gov/adv/adv_otherdis?"))
        self.assertEqual(p["countries"], ["US"])
        self.assertEqual(nat["geometry"]["type"], "Point")
        self.assertEqual({items[i]["properties"]["tier"] for i in items if ":equipment:" in i}, {"anomaly"})
        self.assertEqual(fc["health"]["faa-atcscc"]["state"], "fresh")

    def test_failed_read_fails_only_its_lane(self):
        snap, problems = read("2026-11-13T23:00:00Z", fail=("adv_list",))
        self.assertEqual(snap["events"], [])
        self.assertEqual({p["endpoint"] for p in problems}, {"faa:list"})
        fc = live_feed("2026-11-13T23:00:00Z", snap,
                       [{"source": "faa", "error": f"{p['endpoint']}: {p['error']}"} for p in problems])
        failed = {k for k, v in fc["health"].items() if v["state"] == "failing"}
        self.assertEqual(failed, {"faa-atcscc"})

    def _no_advisory_fetch(self, advn: int, times: int):
        """The real fixtures, except that advisory `advn` of 16 Nov answers the FAA's real "NO Advisory." page the
        first `times` times it is asked for (17 Nov 2026: such pages came and went within minutes)."""
        empty = gzip.decompress((FAA / "no_advisory_11162026_71.html.gz").read_bytes())
        real, asked = faa_fetch(), {"n": 0}

        def fetch(url: str) -> bytes:
            if url.endswith(f"adv_date=11162026&advn={advn}"):
                asked["n"] += 1
                if asked["n"] <= times:
                    return empty
            return real(url)
        return fetch, asked

    def test_a_page_that_says_no_advisory_is_asked_again(self):
        fetch, asked = self._no_advisory_fetch(10, times=2)  # LAS's equipment stop
        snap, problems = faa.fetch_faa(at("2026-11-16T23:59:00Z"), fetch=fetch, pause=0)
        self.assertEqual((asked["n"], problems), (3, []))
        self.assertEqual([(e["kind"], e["key"]) for e in snap["events"]], [("equipment", "LAS")])

    def test_a_page_that_keeps_saying_no_advisory_is_a_note_not_a_failed_lane(self):
        fetch, asked = self._no_advisory_fetch(10, times=99)
        snap, problems = faa.fetch_faa(at("2026-11-16T23:59:00Z"), fetch=fetch, pause=0)
        self.assertEqual(asked["n"], faa.TRIES)
        self.assertEqual([p["endpoint"] for p in problems], ["faa:advisory-missing"])
        self.assertEqual(snap["events"], [])  # an airport stop can't be classified without its page
        fc = live_feed("2026-11-16T23:59:00Z", snap,
                       [{"source": "faa", "error": f"{p['endpoint']}: {p['error']}"} for p in problems])
        self.assertEqual(fc["health"]["faa-atcscc"]["state"], "fresh")

    def test_seed_build_has_no_faa_items(self):
        fc = feed.build_feed(blackout_cli.build_snapshot())
        self.assertFalse([f for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-17"])

    def test_registered_in_doctrine(self):
        srcs = {s["id"]: s for s in json.loads((ROOT / "doctrine" / "sources.json").read_text(encoding="utf-8"))["sources"]}
        self.assertEqual(srcs["faa-atcscc"]["licence"], feed.SRC_FAA["licence"])
        self.assertEqual(srcs["faa-atcscc"]["grade"], feed.SRC_FAA["grade"])
        ind = json.loads((ROOT / "doctrine" / "indicators.json").read_text(encoding="utf-8"))
        self.assertIn("BO-IND-17", {i["id"] for i in ind["indicators"]})
        self.assertIn("BO-WP-6", {w["id"] for w in ind["warning_problems"]})
        self.assertIn("faa-atcscc", feed.LANES)
        page = (ROOT / "site_src" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="faa-stops"', page)


class TestGithubStatus(unittest.TestCase):
    def test_history_rss_parses_with_nothing_open(self):
        # 17 Nov 2026: 25 incidents in the feed (16 Nov "Incident with Pull Requests" among them), all resolved.
        body = (ROOT / "tests" / "fixtures" / "status" / "github_history_2026-11-17.rss").read_bytes()
        self.assertEqual(status_feeds.parse_statuspage_rss(body), [])
        f = status_feeds.FEED_BY_ID["github"]
        self.assertEqual((f["provider"], f["source_id"], f["format"]), ("github", "github-status", "statuspage_rss"))


class TestViasatMssStatus(unittest.TestCase):
    """Viasat MSS (20 Nov 2026): the one satellite operator beyond Starlink found with a readable
    status feed."""
    FIX = ROOT / "tests" / "fixtures" / "status" / "viasat_mss_history_2026-11-20.rss"

    def test_history_rss_parses_with_nothing_open(self):
        # 20 Nov 2026: 25 incidents in the feed (13 Nov EMEA outage the newest), all resolved.
        body = self.FIX.read_bytes()
        self.assertEqual(body.decode("utf-8").count("<item>"), 25)
        self.assertEqual(status_feeds.parse_statuspage_rss(body), [])
        f = status_feeds.FEED_BY_ID["viasat-mss"]
        self.assertEqual((f["provider"], f["source_id"], f["format"]), ("viasat", "viasat-mss-status", "statuspage_rss"))
        self.assertIn("viasat-mss-status", feed.LANES)
        self.assertEqual(feed.problem_lanes({"source": "status:viasat-mss", "error": "timed out"}),
                         {"viasat-mss-status"})
        self.assertEqual(feed.STATUS_NCF["viasat"], ["provide_satellite_access"])

    def test_reopened_outage_reads_as_open(self):
        # The newest item (13 Nov EMEA outage) with its Resolved update set back to Identified: that one status word
        # changed; the feed is otherwise Viasat's own.
        text = self.FIX.read_text(encoding="utf-8")
        head, sep, tail = text.partition("</item>")
        first = head.replace("&lt;strong&gt;Resolved&lt;/strong&gt;", "&lt;strong&gt;Identified&lt;/strong&gt;", 1)
        incs = status_feeds.parse_statuspage_rss((first + sep + tail).encode("utf-8"))
        self.assertEqual(len(incs), 1)
        i = incs[0]
        self.assertEqual((i["title"], i["status"], i["started_at"], i["updated_at"], i["updates"]),
                         ("Viasat MSS EMEA Region Service Outage, 2026-11-13", "identified", "2026-11-13T11:52:00Z",
                          "2026-11-13T14:05:00Z", 2))
        self.assertEqual(i["link"], "https://status.mss.viasat.com/incidents/6f06p6j7d767")


if __name__ == "__main__":
    unittest.main()
