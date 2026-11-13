"""Offline tests for the lanes added 13 Nov 2026: routing integrity (Cloudflare Radar BGP), power (Radar's power
causes, the IAEA's Zaporizhzhia updates, the US grid from ODIN and EIA-930, NASA night lights), shutdowns (Access Now
STOP) and censorship test lists (Citizen Lab). Each replays real responses recorded on 13 Nov 2026, listed with their URLs in the MANIFEST.json of
its fixtures folder; nothing in them is edited."""
from __future__ import annotations

import base64
import gzip
import json
from datetime import datetime
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackout import bgp, blackmarble, citizenlab, eaglei, eia, feed, iaea, odin, radar, stop  # noqa: E402
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


EIA = ROOT / "tests" / "fixtures" / "eia"
MILTON_NOW = "2024-10-10T16:30:00Z"
QUIET_NOW = "2026-11-13T16:20:00Z"


def eia_fetch(fail: tuple = ()):
    """Serve the recorded EIA bodies (gzipped, as returned) by their URL without the key."""
    by_url = {u: n for n, u in json.loads((EIA / "MANIFEST.json").read_text(encoding="utf-8"))["files"].items()}

    def fetch(url: str):
        assert "api_key" not in url
        if any(f in url for f in fail):
            raise OSError(f"HTTP Error 504: Gateway Timeout ({url[-60:]})")
        return json.loads(gzip.decompress((EIA / by_url[url]).read_bytes()))
    return fetch


class TestEia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.milton, cls.milton_problems = eia.fetch_eia(fetch=eia_fetch(), now=radar._t(MILTON_NOW))
        cls.quiet, cls.quiet_problems = eia.fetch_eia(fetch=eia_fetch(), now=radar._t(QUIET_NOW))

    def by_id(self, e):
        return {b["id"]: b for b in e["bas"]}

    def test_nine_one_day_windows_and_no_regions(self):
        self.assertEqual(self.quiet_problems, [])
        self.assertEqual((self.quiet["windows_read"], self.quiet["windows"]), (9, 9))
        ids = set(self.by_id(self.quiet))
        self.assertFalse(ids & eia.REGIONS)  # US48, FLA, TEX ... are sums of BAs, never counted
        self.assertIn("ERCO", ids)
        first = eia.window_url(*eia.windows(radar._t(QUIET_NOW))[0])
        self.assertIn("&start=2026-11-12T17&end=2026-11-13T16&", first)
        self.assertTrue(first.endswith("&offset=0&length=5000"))

    def test_quiet_week_has_nothing_low(self):
        e = self.quiet
        self.assertEqual(e["notable"], [])
        self.assertEqual(e["assessed"], 38)  # every BA of 1,000 MW or more that reported on 13 Nov 2026
        pjm = self.by_id(e)["PJM"]
        self.assertEqual(pjm["state"], "normal")
        self.assertEqual(pjm["range_hours"], 168)
        # The two newest hours are skipped: the window ends two hours before PJM's newest demand hour.
        self.assertEqual((radar._t(pjm["newest"]) - radar._t(pjm["window"]["end"])).total_seconds(), 7200)
        self.assertGreater(self.by_id(e)["FPC"]["median_ratio"], 1.2)  # Duke Energy Florida: demand well above forecast
        self.assertEqual(self.by_id(e)["SEC"]["state"], "small")
        fc = live_feed(eia=e)
        self.assertFalse([f for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-16"])

    def test_milton_finds_tampa_electric_and_duke_energy_florida(self):
        e = self.milton
        self.assertEqual(self.milton_problems, [])
        self.assertEqual(e["notable"], ["FPC", "TEC"])
        tec, fpc, fpl = (self.by_id(e)[k] for k in ("TEC", "FPC", "FPL"))
        w = tec["run"]["worst"]
        self.assertEqual((w["hour"], w["demand"], w["forecast"]), ("2024-10-10T09:00:00Z", 460, 1841))
        self.assertAlmostEqual(w["change"], -0.743, places=3)  # against the range a run at 16:30 UTC had
        self.assertTrue(tec["run"]["ongoing"])
        self.assertAlmostEqual(fpc["run"]["worst"]["change"], -0.520, places=3)
        self.assertEqual(fpc["line"], eia.FLOOR)
        self.assertEqual(fpl["state"], "normal")  # 13% under at worst: below the floor
        # Hour-ending stamps: the run's start is the beginning of its first low hour.
        self.assertTrue(tec["run"]["start"] < tec["run"]["worst"]["hour"])

    def test_the_line_widens_where_forecasts_often_miss(self):
        # Before Milton, Tampa Electric's own range was wide enough to lift its line above the 25% floor.
        tec = self.by_id(self.milton)["TEC"]
        self.assertGreater(tec["line"], eia.FLOOR)
        self.assertAlmostEqual(tec["line"], eia.RANGE_MULT * (1 - tec["low_ratio"] / tec["median_ratio"]), places=3)

    def test_low_balancing_authority_is_a_reported_observation_that_lints(self):
        fc = live_feed(eia=self.milton)
        self.assertEqual(lint_errors(fc), {})
        items = {f["properties"]["id"]: f for f in fc["features"] if f["properties"]["indicator_id"] == "BO-IND-16"}
        self.assertEqual(len(items), 2)
        f = next(v for k, v in items.items() if k.startswith("blackout:us-demand:tec:"))
        p = f["properties"]
        self.assertRegex(p["title"], r"^Tampa Electric Company: demand 74% below what its forecast implies, \d+ h \(EIA-930\)$")
        self.assertEqual((p["kind"], p["tier"], p["status_class"], p["direction"], p["us_person_data"], p["precision_class"]),
                         ("observation", "anomaly", "reported", "neutral", "organizations", "admin1"))
        self.assertEqual((p["warning_problem_id"], p["hypothesis_id"], p["requirement_ids"]), ("BO-WP-5", "BO-H-5B", ["BO-PIR-10"]))
        self.assertIn("460 MW of demand to EIA for the hour to 09:00 UTC 10 Oct 2024", p["summary"])
        self.assertIn("Source: U.S. Energy Information Administration (10 Oct 2024).", p["summary"])
        self.assertIn("public domain", p["sources"][0]["licence"])
        self.assertEqual(p["visual"]["url"], "https://blackout.theintelligence.company/#power/eia-tec")
        lon, lat = f["geometry"]["coordinates"]
        self.assertTrue(27 < lat < 29 and -83 < lon < -82)  # the Tampa Bay area

    def test_a_missing_day_is_listed_never_filled(self):
        e, problems = eia.fetch_eia(fetch=eia_fetch(fail=("start=2026-11-08T17",)), now=radar._t(QUIET_NOW))
        self.assertEqual(e["windows_read"], 8)
        self.assertIn("2026-11-08T17..2026-11-09T16", problems[0]["error"])
        self.assertLess(self.by_id(e)["PJM"]["range_hours"], 168)

    def test_no_key_reads_nothing_and_errors_never_carry_it(self):
        with unittest.mock.patch.object(eia, "api_key", return_value=None):
            e, problems = eia.fetch_eia(now=radar._t(QUIET_NOW))
        self.assertIsNone(e)
        self.assertIn("EIA_API_KEY is not set", problems[0]["error"])
        secret = "NOT-A-REAL-KEY-0123456789"

        def refuse(req, timeout=None):
            raise eia.urllib.error.URLError(f"could not reach {req.full_url}")
        with unittest.mock.patch.object(eia, "api_key", return_value=secret), \
                unittest.mock.patch.object(eia.urllib.request, "urlopen", refuse), \
                unittest.mock.patch.object(eia.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                eia._get(eia.window_url(*eia.windows(radar._t(QUIET_NOW))[0]))
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn("api_key=<key>", str(ctx.exception))


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


ODIN = ROOT / "tests" / "fixtures" / "odin"
ODIN_NOW = "2026-11-13T14:40:05Z"


class TestOdin(unittest.TestCase):
    def setUp(self):
        self.o, self.problems = odin.fetch_odin(fetch=manifest_fetch(ODIN), now=radar._t(ODIN_NOW))

    def test_every_county_placed_and_summed_by_state(self):
        self.assertEqual(self.problems, [])
        self.assertEqual((self.o["meters"], len(self.o["counties"]), len(self.o["states"])), (39663, 214, 34))
        la = self.o["counties"][0]
        self.assertEqual((la["fips"], la["name"], la["state"], la["meters"]), ("06037", "Los Angeles County", "CA", 11231))
        self.assertEqual(la["utilities"], [{"name": "SOUTHERN CALIFORNIA EDISON CO", "eia_id": "17609"}])
        ca = self.o["states"][0]
        self.assertEqual((ca["state"], ca["meters"], ca["counties"]), ("CA", 13723, 10))
        # A county out at two or more utilities lists each (13 such rows on 13 Nov 2026).
        self.assertTrue(any(len(c["utilities"]) > 1 for c in self.o["counties"]))

    def test_coverage_is_stated_with_the_reading(self):
        cov = self.o["coverage"]
        self.assertEqual((cov["utilities"], cov["by_resolution"]["COUNTY"], cov["with_error"], cov["stale"]),
                         (159, 133, 10, 2))
        self.assertIn("Puget Sound Energy Inc", [u["name"] for u in cov["outages_not_in_county_view"]])

    def test_nothing_above_the_doe_line_publishes_nothing(self):
        self.assertEqual(self.o["notable_states"], [])  # 13,723 in California is the most; the line is 50,000
        fc = live_feed(odin=self.o)
        self.assertFalse([f for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-14"])

    def test_state_above_the_line_is_a_reported_observation_that_lints(self):
        with unittest.mock.patch.object(odin, "STATE_THRESHOLD", 10_000):  # real reading, lower line
            o, _ = odin.fetch_odin(fetch=manifest_fetch(ODIN), now=radar._t(ODIN_NOW))
        self.assertEqual([s["state"] for s in o["notable_states"]], ["CA", "NY"])
        fc = live_feed(odin=o)
        self.assertEqual(lint_errors(fc), {})
        items = [f for f in fc["features"] if f["properties"]["indicator_id"] == "BO-IND-14"]
        p = items[0]["properties"]
        self.assertEqual(p["title"], "California: 13,723 meters without power (ODIN's reporting utilities)")
        self.assertEqual((p["kind"], p["status_class"], p["direction"], p["us_person_data"], p["precision_class"]),
                         ("observation", "reported", "neutral", "organizations", "admin1"))
        self.assertIn("Los Angeles County (11,231)", p["summary"])
        self.assertIn("No licence stated by ODIN", p["sources"][0]["licence"])
        lon, lat = items[0]["geometry"]["coordinates"]
        self.assertTrue(32 < lat < 42 and -125 < lon < -114)  # inside California, weighted toward the outage

    def test_failed_county_view_publishes_nothing(self):
        o, problems = odin.fetch_odin(fetch=manifest_fetch(ODIN, fail=("view=county",)), now=radar._t(ODIN_NOW))
        self.assertIsNone(o)
        self.assertEqual(problems[0]["endpoint"], "odin-county")


EAGLEI = ROOT / "tests" / "fixtures" / "eaglei"
EAGLEI_SLICE = EAGLEI / "eaglei_outages_2025_CA_2025-01-08T12Z_24h.csv.gz"


def eaglei_slice(caps: bool = True) -> dict:
    """The real California rows of 12:00 UTC 8 Jan to 12:00 UTC 9 Jan 2025 (MANIFEST.json), reduced."""
    cap = eaglei.load_caps((EAGLEI / "MCC_CA.csv").read_text(encoding="utf-8")) if caps else None
    with gzip.open(EAGLEI_SLICE, "rt", encoding="utf-8-sig", newline="") as f:
        return eaglei.state_totals(f, 2025, cap)


class TestEaglei(unittest.TestCase):
    """EAGLE-I's history as base rates (Tom, 13 Nov 2026: "use EAGLE-I"). The slice holds the 7 runs (21:00-22:30 UTC
    8 Jan 2025) in which Southern California Edison's counties read far above their customers."""

    def test_suspect_runs_are_kept_but_make_no_episode_or_peak(self):
        red = eaglei_slice()
        self.assertEqual(red["rows"], 3212)
        self.assertEqual(max(red["totals"]["CA"].values()), 3494570)  # kept as EAGLE-I published it
        suspect = [eaglei.stamp(2025, i) for i in red["suspect"]["CA"]]
        self.assertEqual(suspect[:7], [f"2025-01-08T{h}Z" for h in ("21:00", "21:15", "21:30", "21:45", "22:00",
                                                                     "22:15", "22:30")])
        self.assertEqual(suspect[7:], ["2025-01-09T02:45Z", "2025-01-09T03:00Z"])  # Ventura at 507,301 of 317,658
        eps = eaglei.year_episodes(red)
        self.assertEqual(eps["50000"], [{"state": "CA", "start": "2025-01-08T12:00Z", "end": "2025-01-09T12:00Z",
                                         "hours": 24.0, "peak": 510705, "month": 1}])
        self.assertEqual(eps["1000000"], [])  # only the 7 suspect runs were over a million

    def test_without_the_county_check_the_broken_report_makes_a_million(self):
        red = eaglei_slice(caps=False)
        self.assertEqual([(e["start"], e["end"], e["peak"]) for e in eaglei.year_episodes(red)["1000000"]],
                         [("2025-01-08T21:00Z", "2025-01-08T22:45Z", 3486286)])

    def test_an_episode_needs_an_hour_of_readings(self):
        self.assertEqual(eaglei.episodes({0: 60_000, 3: 60_000}, 50_000), [])  # two spikes an hour apart
        four = eaglei.episodes({0: 60_000, 1: 60_000, 2: 40_000, 3: 60_000, 4: 70_000}, 50_000)
        self.assertEqual([(e["start"], e["end"], e["readings"]) for e in four], [(0, 4, 4)])
        # Runs missing from the file neither join nor split: 8 missing runs between readings join; 4 known runs
        # under the line split.
        self.assertEqual(len(eaglei.episodes({0: 6e4, 1: 6e4, 10: 6e4, 11: 6e4}, 50_000, list(range(2, 10)))), 1)
        self.assertEqual(eaglei.episodes({0: 6e4, 1: 6e4, 6: 6e4, 7: 6e4}, 50_000), [])

    def test_committed_tables_come_from_figshare_files_that_match_their_md5(self):
        doc = eaglei.rates()
        self.assertEqual(doc["base_years"], list(range(2018, 2026)))
        self.assertEqual(doc["licence"]["name"], "CC BY 4.0")
        self.assertEqual(sorted(doc["files"]), [str(y) for y in range(2018, 2026)])
        self.assertTrue(all(f["md5_matches_figshare"] for f in doc["files"].values()))
        total = 0
        for st, lines in doc["states"].items():
            row = lines["50000"]
            self.assertEqual(len(row["each_year"]), 8, st)
            counted = [n for n in row["each_year"] if n is not None]
            self.assertEqual(len(counted), len(row["years"]), st)
            self.assertAlmostEqual(row["per_year"], round(sum(counted) / len(counted), 2), places=2, msg=st)
            self.assertAlmostEqual(sum(row["by_month"]), row["per_year"], delta=0.07, msg=st)
            total += sum(counted)
        self.assertEqual(total, len(doc["episodes_over_50000"]))
        # EAGLE-I's coverage file gives Puerto Rico 0% for 2018-2020 and its files have no row there, so those years
        # are left out of its rate rather than counted as quiet.
        self.assertEqual(doc["states"]["PR"]["50000"]["years"], [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(doc["states"]["PR"]["50000"]["each_year"][:3], [None, None, None])
        self.assertIn("Puerto Rico's record for 2021-2025", eaglei.base_rate("PR", 4)["text"].replace(
            "In EAGLE-I's record for 2021-2025, Puerto Rico", "Puerto Rico's record for 2021-2025"))

    def test_base_rate_line(self):
        b = eaglei.base_rate("CA", 1)
        self.assertTrue(b["text"].startswith("In EAGLE-I's record for 2018-2025, California had more than 50,000 "
                                             "customers out for an hour or more "), b["text"])
        self.assertIn(" in January", b["text"])
        self.assertIn("ODIN", b["caveat"])
        self.assertIsNone(eaglei.base_rate("ZZ", 1))
        self.assertIsNone(eaglei.base_rate("CA", 1, doc={}))

    def test_odin_states_carry_their_base_rate(self):
        o, _ = odin.fetch_odin(fetch=manifest_fetch(ODIN), now=radar._t(ODIN_NOW))
        snap = blackout_cli.build_snapshot({"mode": "live", "generated_at": BGP_NOW, "odin": o})
        rates = [s["base_rate"] for s in snap["odin"]["states"]]
        self.assertTrue(all(r and r["month"] == 11 for r in rates))
        self.assertNotIn("base_rate", o["states"][0])  # the reading itself is left as ODIN gave it

    def test_odin_item_cites_eaglei_for_the_base_rate_and_lints(self):
        with unittest.mock.patch.object(odin, "STATE_THRESHOLD", 10_000):  # real reading, lower line
            o, _ = odin.fetch_odin(fetch=manifest_fetch(ODIN), now=radar._t(ODIN_NOW))
        fc = live_feed(odin=o)
        self.assertEqual(lint_errors(fc), {})
        p = next(f["properties"] for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-14")
        self.assertIn("Base rate: In EAGLE-I's record for 2018-2025, California had more than 50,000", p["summary"])
        self.assertEqual([s["id"] for s in p["sources"]], ["odin-ornl", "ornl-eaglei-history"])
        self.assertIn("CC BY 4.0", p["sources"][1]["licence"])
        br = p["baseline_ref"]["base_rate"]
        self.assertEqual((br["years"], br["month"], br["per_year"]),
                         ([2018, 2025], 11, eaglei.rates()["states"]["CA"]["50000"]["per_year"]))

    def test_verdict_rule_is_the_text_verdict_reads(self):
        # Verdict's auto-resolve (tools/auto_resolve.py, EAGLEI_RE) parses exactly this; its test holds the same text.
        r = eaglei.verdict_rule("CA", radar._t("2025-01-08T12:00:00Z"), radar._t("2025-01-09T12:00:00Z"), 500_000)
        self.assertEqual(r["rule"], (
            "Yes if EAGLE-I's outage history (ORNL, figshare doi:10.6084/m9.figshare.24237376) shows California (CA) "
            "with more than 500,000 customers out, its counties summed in each 15-minute run, in 4 or more runs with "
            "no more than 3 runs under the line between them, between 2025-01-08 12:00 and 2025-01-09 12:00 UTC. No "
            "otherwise."))
        self.assertEqual(r["known_at"], "2026-04-30T00:00:00Z")


CL = ROOT / "tests" / "fixtures" / "citizenlab"


class TestCitizenLab(unittest.TestCase):
    def setUp(self):
        by_url = {u: n for n, u in json.loads((CL / "MANIFEST.json").read_text(encoding="utf-8"))["files"].items()}
        self.fetch = lambda url: (CL / by_url[url]).read_text(encoding="utf-8") if url in by_url else None  # zz: 404

    def test_counts_per_country_and_category(self):
        out, problems = citizenlab.fetch_testlists(["ir", "RU", "ZZ"], fetch=self.fetch)
        self.assertEqual(problems, [])
        self.assertEqual((len(out["legend"]), out["global"]["urls"], out["codes_not_in_legend"]), (31, 1727, []))
        self.assertEqual(out["legend"]["NEWS"]["name"], "News Media")
        ir = out["countries"]["IR"]
        self.assertEqual((ir["urls"], next(iter(ir["categories"])), ir["latest_added"]), (902, "NEWS", "2025-03-11"))
        self.assertEqual(out["countries"]["RU"]["urls"], 1093)
        self.assertIsNone(out["countries"]["ZZ"])  # no list there: said so, not counted as zero
        self.assertNotIn("http", json.dumps(out))  # counts only, never the URLs

    def test_unreadable_legend_publishes_nothing(self):
        out, problems = citizenlab.fetch_testlists(["IR"], fetch=lambda url: "not,a,legend\n")
        self.assertIsNone(out)
        self.assertEqual(problems[0]["endpoint"], "citizenlab")


BM = ROOT / "tests" / "fixtures" / "blackmarble"
try:
    import h5py  # noqa: F401  # Black Marble's one dependency (installed by the workflow)
    HAVE_H5PY = True
except ImportError:
    HAVE_H5PY = False


def bm_fixtures():
    by_url = {u: n for n, u in json.loads((BM / "MANIFEST.json").read_text(encoding="utf-8"))["files"].items()}
    return (lambda url: json.loads((BM / by_url[url]).read_text(encoding="utf-8")),
            lambda url: (BM / by_url[url]).read_bytes())


def jwt(exp: datetime) -> str:  # a made-up token with only an expiry claim, never a real one
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp.timestamp())})}.x"


KYIV = [c for c in blackmarble.CITIES if c["id"] == "kyiv"]


@unittest.skipUnless(HAVE_H5PY, "h5py not installed")
class TestBlackMarble(unittest.TestCase):
    def setUp(self):
        self.search, self.fetch = bm_fixtures()

    def test_clear_night_is_measured(self):
        got, problems = blackmarble.read_night("2026-10-21", KYIV, search=self.search, fetch_tile=self.fetch)
        # LANCE no longer held the night (empty search, recorded), so the standard product was read.
        self.assertEqual(problems, [])
        self.assertEqual(got["kyiv"], {"date": "2026-10-21", "mean": 6.421, "clear": 0.694, "pixels": 14400,
                                       "product": "VJ146A2"})

    def test_cloudy_night_says_nothing(self):
        got, _ = blackmarble.read_night("2026-10-17", KYIV, search=self.search, fetch_tile=self.fetch,
                                        products=(blackmarble.STANDARD,))
        self.assertEqual((got["kyiv"]["mean"], got["kyiv"]["clear"]), (None, 0.0))
        a = blackmarble.assess([got["kyiv"]], radar._t("2026-10-18T06:00:00Z"))
        self.assertEqual(a["state"], "no_clear_night")  # never "normal"

    def test_partly_clear_night_is_not_counted(self):
        got, _ = blackmarble.read_night("2026-10-25", KYIV, search=self.search, fetch_tile=self.fetch,
                                        products=(blackmarble.STANDARD,))
        self.assertEqual((got["kyiv"]["mean"], got["kyiv"]["clear"]), (27.243, 0.201))  # the bright centre alone
        a = blackmarble.assess([got["kyiv"]], radar._t("2026-10-26T06:00:00Z"))
        self.assertEqual(a["state"], "no_clear_night")

    def test_failed_tile_reads_no_city(self):
        def fetch(url):
            raise PermissionError("LAADS licence not accepted")
        got, problems = blackmarble.read_night("2026-10-21", KYIV, search=self.search, fetch_tile=fetch)
        self.assertEqual(got, {})
        self.assertIn("PermissionError", problems[0]["error"])

    def test_licence_page_is_named(self):
        class Page:
            status = 200
            def geturl(self):
                return "https://nrt3.modaps.eosdis.nasa.gov/profiles/licenses/api/v2/content/archives/x.h5"
            def read(self, n=-1):
                return b"<html></html>"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        opener = unittest.mock.Mock(open=unittest.mock.Mock(return_value=Page()))
        with unittest.mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(PermissionError):
                blackmarble.download("https://nrt3.modaps.eosdis.nasa.gov/x.h5", tok="t")
        # and when the gate is a redirect (as LAADS and LANCE send it), the redirect itself is caught
        with self.assertRaisesRegex(PermissionError, "licence not accepted"):
            blackmarble._LicenceCheck().redirect_request(None, None, 303, "See Other", {},
                                                         "https://nrt3.modaps.eosdis.nasa.gov/profiles/licenses/x.h5")

    def test_licence_gate_is_one_problem_and_stops_the_run(self):
        # 13 Nov 2026: before the account accepted LAADS's licence, every tile of every night was refused, which
        # listed 60 problems (15 tiles, 4 nights) in one run. The gate is per account, so one is named and the run stops.
        calls = []

        def fetch(url):
            calls.append(url)
            raise PermissionError(blackmarble.LICENCE_GATE)
        with unittest.mock.patch.object(blackmarble, "CITIES", KYIV):  # one tile, h21v03, as recorded
            out, problems = blackmarble.fetch_blackmarble(history={}, search=self.search, fetch_tile=fetch,
                                                          now=radar._t("2026-10-22T06:00:00Z"), tok="t", max_new=4)
        gate = [p for p in problems if blackmarble.LICENCE_GATE in p["error"]]
        self.assertEqual(len(gate), 1)
        self.assertIn("2026-10-21, 1 tiles", gate[0]["error"])
        self.assertEqual(len(calls), 1)  # one night's one tile tried, then no more
        self.assertEqual({c["assessment"]["state"] for c in out["cities"]}, {"not_read"})

    def test_token_expiry_warns_two_weeks_ahead_and_no_token_reads_nothing(self):
        now = radar._t("2026-12-10T00:00:00Z")
        self.assertEqual(blackmarble.token_expiry(jwt(radar._t("2026-12-20T22:31:27Z"))).isoformat(),
                         "2026-12-20T22:31:27+00:00")
        out, problems = blackmarble.fetch_blackmarble(history={}, search=lambda u: {"feed": {"entry": []}},
                                                      now=now, tok=jwt(radar._t("2026-12-20T22:31:27Z")), max_new=0)
        self.assertIn("expires 2026-12-20 22:31", problems[0]["error"])
        out, problems = blackmarble.fetch_blackmarble(now=now, tok="")
        self.assertIsNone(out)
        self.assertIn("EARTHDATA_TOKEN is not set", problems[0]["error"])


NIGHTS = BM / "nights_VJ146A2_2026-09-29_to_2026-10-28.json"
KYIV_CHANGE = -0.424


class TestNightLights(unittest.TestCase):
    """The rule on 30 real nights per city (read_night on real VJ146A2 tiles; see the fixture's note)."""
    def setUp(self):
        self.n = json.loads(NIGHTS.read_text(encoding="utf-8"))["cities"]

    def upto(self, cid, date):
        return [x for x in self.n[cid] if x["date"] <= date]

    def test_kyiv_the_night_after_the_8_october_strike_is_darker(self):
        # Russia struck Ukraine's grid on 8 Oct 2026 and Ukrenergo cut power in Kyiv (Kyiv Independent, 8 Oct);
        # NOAA-20 passes about 01:30 local, so the "9 Oct" night is the night after.
        a = blackmarble.assess(self.upto("kyiv", "2026-10-09"), radar._t("2026-10-10T06:00:00Z"))
        self.assertEqual((a["state"], a["latest"]["date"], a["change"]), ("darker", "2026-10-09", KYIV_CHANGE))
        self.assertGreaterEqual(a["baseline_nights"], blackmarble.MIN_BASELINE)

    def test_khartoum_after_the_27_october_drone_strike_is_darker(self):
        # A drone strike took the Al-Markhiyat transformer station in Omdurman out of service early on 27 Oct 2026;
        # large parts of Khartoum were without power for more than 12 hours (Sudan Tribune; AP, 27-28 Oct).
        for night, change in (("2026-10-27", -0.743), ("2026-10-28", -0.779)):
            a = blackmarble.assess(self.upto("khartoum", night), radar._t(night + "T12:00:00Z"))
            self.assertEqual((a["state"], a["change"]), ("darker", change))

    def test_the_rule_on_all_30_nights(self):
        # Every night the rule flags in the calibration set: 6 of the 229 assessed nights with a baseline of 1.0
        # nW/cm2/sr or more. Kyiv and Khartoum match reported grid strikes; Havana a reported grid shortfall;
        # Dnipro's two were not found in reporting.
        hits = []
        for cid, nights in self.n.items():
            for x in nights:
                a = blackmarble.assess(self.upto(cid, x["date"]), radar._t(x["date"] + "T12:00:00Z"))
                if a["state"] == "darker" and a["latest"]["date"] == x["date"]:
                    hits.append((cid, x["date"]))
        self.assertEqual(sorted(hits), [("dnipro", "2026-10-10"), ("dnipro", "2026-10-13"), ("havana", "2026-10-13"),
                                        ("khartoum", "2026-10-27"), ("khartoum", "2026-10-28"), ("kyiv", "2026-10-09")])

    def test_dark_city_is_too_dark_not_normal(self):
        # Kharkiv's nights are about 0.1-0.3 nW/cm2/sr (Kyiv's about 6-7): a percentage of that means nothing.
        a = blackmarble.assess(self.upto("kharkiv", "2026-10-19"), radar._t("2026-10-19T12:00:00Z"))
        self.assertEqual(a["state"], "too_dark")
        a = blackmarble.assess(self.upto("zaporizhzhia", "2026-10-22"), radar._t("2026-10-22T12:00:00Z"))
        self.assertEqual((a["state"], a["change"]), ("too_dark", -0.581))  # a 58% drop, not published

    def test_old_night_is_stale_not_normal(self):
        a = blackmarble.assess(self.upto("kyiv", "2026-10-09"), radar._t("2026-10-20T06:00:00Z"))
        self.assertEqual(a["state"], "stale")

    def test_darker_city_is_a_reported_observation_that_lints(self):
        hist = {cid: self.upto(cid, "2026-10-09") for cid in self.n}
        far = jwt(radar._t("2027-01-01T00:00:00Z"))
        nl, problems = blackmarble.fetch_blackmarble(history=hist, now=radar._t("2026-10-10T06:00:00Z"), tok=far,
                                                     max_new=0)
        self.assertEqual(problems, [])
        self.assertIn("kyiv", nl["darker"])
        fc = live_feed(nightlights=nl)
        self.assertEqual(lint_errors(fc), {})
        p = next(f["properties"] for f in fc["features"] if f["properties"]["id"] == "blackout:night-lights:kyiv:2026-10-09")
        self.assertEqual(p["title"], f"Kyiv: night lights {round(-KYIV_CHANGE * 100)}% below its recent nights "
                                     "(NASA Black Marble, 9 Oct 2026)")
        self.assertEqual((p["kind"], p["status_class"], p["direction"], p["indicator_id"], p["precision_class"]),
                         ("observation", "reported", "neutral", "BO-IND-15", "area"))
        self.assertIn("not why", p["summary"])
        self.assertIn("no restrictions on use", p["sources"][0]["licence"])
        self.assertEqual(p["sources"][0]["url"], "https://doi.org/10.5067/VIIRS/VJ146A2.002")


if __name__ == "__main__":
    unittest.main()
