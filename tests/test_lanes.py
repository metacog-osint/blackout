"""Offline tests for the lanes added 13 Nov 2026: routing integrity (Cloudflare Radar BGP), power (Radar's power
causes, the IAEA's Zaporizhzhia updates, the US grid), shutdowns (Access Now STOP) and censorship test lists
(Citizen Lab). Each replays real responses recorded on 13 Nov 2026, listed with their URLs in the MANIFEST.json of
its fixtures folder; nothing in them is edited."""
from __future__ import annotations

import base64
import json
from datetime import datetime
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackout import bgp, blackmarble, citizenlab, feed, iaea, odin, radar, stop  # noqa: E402
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
