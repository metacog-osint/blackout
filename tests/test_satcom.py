"""The satellite networks register and traffic lane (blackout/satcom.py): complete, closed classes, nothing excluded,
searchable; traffic against each network's own 28-day normal, replayed from recorded Radar and IODA answers."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackout import feed, satcom, trends  # noqa: E402

FIELDS = ("asn", "operator", "registry_holder", "registry_country", "registry", "class", "service", "evidence",
          "confidence")
FIX = ROOT / "tests" / "fixtures" / "satcom"
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
BY_URL = {u: n for n, u in MANIFEST["files"].items()}


def replay(url, *a, **k):
    """The recorded answer for a URL the lane asks for (tests/make_satcom_fixtures.py); a URL not recorded fails."""
    return json.loads((FIX / BY_URL[url]).read_text(encoding="utf-8"))


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestRegister(unittest.TestCase):
    def setUp(self):
        self.doc = satcom.load()
        self.nets = self.doc["networks"]

    def test_every_network_has_its_fields_and_a_closed_class(self):
        for n in self.nets:
            for k in FIELDS:
                self.assertIn(k, n, (n.get("asn"), k))
            self.assertIsInstance(n["asn"], int)
            self.assertIn(n["class"], satcom.CLASSES, n["asn"])
            self.assertIn(n["confidence"], satcom.CONFIDENCE, n["asn"])
            self.assertTrue(n["evidence"].strip(), n["asn"])

    def test_one_row_per_asn(self):
        asns = [n["asn"] for n in self.nets]
        self.assertEqual(len(asns), len(set(asns)))

    def test_lookalikes_stay_in_and_say_what_they_are(self):
        by = {n["asn"]: n for n in self.nets}
        self.assertEqual(by[14593]["class"], "satellite_internet")
        self.assertEqual(by[34602]["class"], "not_satellite")   # Moscow home ISP named Starlink, not Rassvet
        self.assertEqual(by[29242]["class"], "satellite_tv")    # Viasat Ukraine's pay-TV company
        self.assertNotIn(16509, by)                             # Amazon's AWS network is not Kuiper

    def test_search_matches_name_or_number_and_filters_by_class(self):
        self.assertIn(34602, [n["asn"] for n in satcom.search(self.nets, "starlink")])
        self.assertNotIn(34602, [n["asn"] for n in satcom.search(self.nets, "starlink", satcom.COUNTED)])
        self.assertEqual([n["asn"] for n in satcom.search(self.nets, "AS14593")], [14593])
        self.assertEqual(len(satcom.search(self.nets)), len(self.nets))

    def test_snapshot_carries_the_whole_register(self):
        part = satcom.snapshot_part(self.doc)
        self.assertEqual(len(part["networks"]), len(self.nets))
        self.assertEqual(part["classes"], list(satcom.CLASSES))
        self.assertEqual(len(satcom.counted(self.doc)), sum(1 for n in self.nets if n["class"] in satcom.COUNTED))

    def test_page_part_leaves_out_registry_data(self):
        """PeeringDB's AUP and the RIPE Database terms bar republishing their data: no holder, country, date or
        evidence on the page, only the ASN, Blackout's own labels and a link to Radar's page for the network."""
        part = satcom.snapshot_part(self.doc)
        for n in part["networks"]:
            self.assertEqual(set(n), set(satcom.PAGE_FIELDS) | {"link"})
            self.assertEqual(n["link"], f"https://radar.cloudflare.com/as{n['asn']}")
        self.assertTrue(all(isinstance(x, str) for x in part["not_found"]))
        self.assertEqual(set(part["name_matches_not_satellite"][0]), {"asn", "reason"})
        self.assertNotIn("Hughes Hubbard", json.dumps(part))   # a registry holder name from name_matches


class TestPercentOfNormal(unittest.TestCase):
    def _series(self, name):
        return satcom._one(replay(MANIFEST["replay_urls"][name]))

    def test_starlink_ukraine_4_feb_2026_step(self):
        """The first replay: Starlink located in Ukraine stepped down on 4 Feb 2026, the day after Ukraine's 3 Feb
        23:59 terminal-registration deadline; Starlink worldwide and all Ukrainian traffic stayed flat."""
        ua = self._series("UAstarlink")
        hours = dict(ua)
        self.assertEqual(round(hours["2026-02-04T16:00:00Z"], 2), 0.79)
        self.assertEqual(round(hours["2026-02-04T18:00:00Z"], 2), 0.40)
        m = satcom.percent_of_normal(ua)
        self.assertEqual((m["current_from"], m["current_to"]), ("2026-02-04T19:00:00Z", "2026-02-05T01:00:00Z"))
        self.assertEqual(m["normal_days"], 28)
        self.assertEqual(m["pct"], 55.1)
        self.assertEqual(satcom.percent_of_normal(self._series("starlink"))["pct"], 100.4)
        self.assertEqual(satcom.percent_of_normal(self._series("UAall"))["pct"], 102.1)

    def test_thin_series_is_named_not_measured(self):
        pts = [(f"2026-11-{1 + i // 24:02d}T{i % 24:02d}:00:00Z", (i % 3) / 17) for i in range(29 * 24)]
        m = satcom.percent_of_normal(pts)
        self.assertIsNone(m["pct"])
        self.assertIn("too thin", m["why"])
        self.assertEqual(satcom.daily_percent(pts), {})

    def test_too_few_normal_days_states_no_normal(self):
        pts = [(f"2026-11-{1 + i // 24:02d}T{i % 24:02d}:00:00Z", 1.0 + (i % 97) / 1000) for i in range(10 * 24)]
        m = satcom.percent_of_normal(pts)
        self.assertIsNone(m["pct"])
        self.assertEqual(m["normal_days"], 9)

    def test_daily_percent_needs_its_own_21_of_28_days(self):
        pts = [(f"2026-{9 + i // 30:02d}-{1 + i % 30:02d}T00:00:00Z", 1.0 + i / 1000) for i in range(40)]
        out = satcom.daily_percent(pts)
        self.assertNotIn("2026-09-21", out)        # 20 days before it
        self.assertIn("2026-09-22", out)           # the first with 21
        self.assertTrue(all(90 < v < 110 for v in out.values()))


class TestLaneReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nets = [n for n in satcom.counted() if n["asn"] in MANIFEST["asns"]]
        cls.traffic, cls.daily, cls.problems = satcom.fetch_traffic(
            _t(MANIFEST["now"]), replay, replay, nets, _t(MANIFEST["trend_first"]), workers=1)
        cls.by = {r["asn"]: r for r in cls.traffic["networks"]}

    def test_reads_every_network_with_no_problem(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(sorted(self.by), sorted(MANIFEST["asns"]))
        self.assertEqual(self.traffic["read"], {"radar": True, "ioda": True})

    def test_starlink_worldwide_and_by_country(self):
        r = self.by[14593]["radar"]
        self.assertTrue(r["seen"])
        self.assertEqual(r["top_locations"][0]["cc"], "US")
        self.assertEqual(r["world"]["pct"], 102.7)
        self.assertEqual(len(r["countries"]), 20)
        self.assertEqual(r["countries"]["UA"]["pct"], 94.2)
        self.assertEqual(r["countries"]["YE"]["pct"], 102.4)
        io = self.by[14593]["ioda"]
        self.assertEqual((io["bgp_pct"], io["probing_pct"]), (99.9, 100.2))

    def test_unseen_thin_and_low_count_networks_say_so(self):
        self.assertEqual(self.by[40305]["radar"], {"seen": False, "top_locations": []})   # Viasat: no traffic
        self.assertIsNone(self.by[40305]["ioda"])                                          # registry only: not read
        ow = self.by[800]["radar"]
        self.assertTrue(ow["seen"])
        self.assertIsNone(ow["world"]["pct"])
        self.assertIn("too thin", ow["world"]["why"])
        self.assertEqual(self.by[31515]["ioda"]["low_count"], {"ping-slash24": 9.0})      # Inmarsat probing
        self.assertIsNone(self.by[31515]["ioda"]["probing_pct"])

    def test_daily_series_for_trends(self):
        first = MANIFEST["trend_first"][:10]
        sl = self.daily["14593"]
        self.assertEqual(len(sl["countries"]), 20)
        self.assertTrue(sl["world"])
        self.assertTrue(all(d >= first for d in sl["world"]))
        self.assertIsNone(self.by[800]["radar"]["world"]["pct"])  # OneWeb: hourly too thin, daily measurable
        self.assertTrue(self.daily["800"]["world"])
        self.assertEqual(self.daily["31515"]["countries"], {})    # by country for Starlink only
        self.assertNotIn("40305", self.daily)
        doc = trends.build([], [], [], _t(MANIFEST["now"]), satcom_daily=self.daily)
        self.assertTrue(doc["read"]["satcom"])
        w = doc["satcom"]["networks"]["14593"]["countries"]["UA"]["windows"]["7"]
        self.assertEqual(w["current"]["days"], 7)
        self.assertIsNotNone(w["current"]["mean_pct"])
        doc["read"].update(ioda=True, radar_outages=True, radar_anomalies=True, netblocks=True)
        rows = [r for r in trends.settled_rows(doc, _t(MANIFEST["now"])) if r["kind"] == "satcom"]
        self.assertTrue(any(r["asn"] == 14593 and r["country"] == "UA" for r in rows))
        self.assertTrue(any(r["asn"] == 14593 and r["country"] is None for r in rows))

    def test_a_failed_read_is_listed_and_fails_the_satcom_lane(self):
        def broken(url, *a, **k):
            raise TimeoutError("timed out")
        nets = [n for n in satcom.counted() if n["asn"] == 14593]
        traffic, daily, probs = satcom.fetch_traffic(_t(MANIFEST["now"]), broken, broken, nets,
                                                     _t(MANIFEST["trend_first"]), workers=1)
        self.assertEqual(traffic["networks"][0]["radar"], {"seen": None})
        self.assertEqual(traffic["read"], {"radar": False, "ioda": False})
        self.assertEqual({p["source"] for p in probs}, {"satcom:radar", "satcom:ioda"})
        for p in probs:
            self.assertEqual(feed.problem_lanes(p), {"cloudflare-radar-satcom"})  # a failed read drops the items

    def test_never_reads_estimated_users(self):
        self.assertFalse(any("users" in u.lower() for u in MANIFEST["files"].values()))


def hourly(url):
    return satcom._one(replay(url))


def recorded(prefix):
    """The recorded hourly series whose URL has the Radar timeseries query prefix (name=...&asn=...)."""
    url = next(u for u in MANIFEST["files"].values() if f"timeseries?{prefix}" in u and "aggInterval=1h" in u)
    return hourly(url)


def at(points, end):
    """The series as the lane would have read it when the last full hour ended at `end` (UTC ISO, exclusive)."""
    return [(t, v) for t, v in points if _t(t) < _t(end)]


class TestDropRule(unittest.TestCase):
    """BO-IND-21 (ideas#109): under 70% of the weekday-matched normal on two runs in a row, replayed from recorded Radar
    series. Nothing here is a shape made up for the test."""

    def test_inmarsat_in_britain_fires_on_17_nov_and_says_since_when(self):
        pts = at(recorded("name=GB&asn=31515&location=GB"), "2026-11-17T08:00:00Z")
        r = satcom.drop_rule(pts)
        self.assertEqual(r["state"], "fired")
        self.assertLess(r["weekday_pct"], satcom.DROP_BELOW_PCT)
        self.assertLess(r["prior_pct"], satcom.DROP_BELOW_PCT)
        self.assertEqual(r["since"], "2026-11-16T22:00:00Z")
        self.assertFalse(r["standing"])

    def test_one_run_under_the_line_is_not_enough(self):
        # Starlink in Ukraine on the evening of 4 Feb 2026: 54% now, 79% four hours earlier, so not yet two runs
        r = satcom.drop_rule(hourly(MANIFEST["replay_urls"]["UAstarlink"]))
        self.assertEqual((r["state"], r["weekday_pct"], r["prior_pct"]), ("ok", 54.1, 79.1))

    def test_a_weekend_replay_of_viasat_as16491_never_fires(self):
        """Viasat AS16491 falls to 16-35% of its 28-day normal every weekend; against the same weekday it is steady, but
        its weekend normal is about 7 counts an hour, so a 30% dip is counting noise and the series is named coarse."""
        pts = hourly(MANIFEST["weekend_urls"]["viasat"])
        states = {satcom.drop_rule(pts[:i])["state"] for i in range(24 * 21, len(pts) + 1)}
        self.assertNotIn("fired", states)
        self.assertIn("coarse", states)
        # Saturday 14 Nov 19:00 UTC fired (67% and 60%, two runs running) before the counts guard
        late = satcom.drop_rule(at(pts, "2026-11-14T19:00:00Z"))
        self.assertEqual(late["state"], "coarse")
        self.assertIn("counts an hour", late["why"])

    def test_oneweb_mid_november_is_too_thin_never_flagged(self):
        pts = hourly(MANIFEST["weekend_urls"]["oneweb"])  # ends 16 Nov, so it holds 11 and 13 and 15 Nov
        for i in range(24 * 21, len(pts) + 1):
            r = satcom.drop_rule(pts[:i])
            self.assertEqual(r["state"], "sparse")
            self.assertIn("too thin to measure", r["why"])

    def test_steady_networks_do_not_fire_over_their_last_week(self):
        for prefix in ("name=world&asn=14593", "name=UA&asn=14593&location=UA", "name=world&asn=31515"):
            pts = recorded(prefix)
            states = {satcom.drop_rule(pts[:i])["state"] for i in range(24 * 22, len(pts) + 1)}
            self.assertEqual(states, {"ok"}, prefix)

    def test_too_few_weeks_states_no_normal(self):
        pts = recorded("name=world&asn=14593")[-24 * 15:]      # 15 days: only two same weekdays back
        r = satcom.drop_rule(pts)
        self.assertEqual(r["state"], "no_normal")
        self.assertIn("weeks with those hours", r["why"])

    def test_the_readings_carry_the_rule_beside_the_all_days_normal(self):
        nets = [n for n in satcom.counted() if n["asn"] == 14593]
        traffic, _d, _p = satcom.fetch_traffic(_t(MANIFEST["now"]), replay, replay, nets, None, workers=1)
        world = traffic["networks"][0]["radar"]["world"]
        self.assertIn("pct", world)                              # the published percent_of_normal is unchanged
        self.assertEqual(world["weekday"]["state"], "ok")
        self.assertEqual(set(world["weekday"]), {"state", "weekday_pct", "weeks", "prior_pct"})


class TestDropFeed(unittest.TestCase):
    """The BO-IND-21 item: an observation of the network's own traffic, with its subject, lane and wording."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_blackout import blackout_cli, lint_errors  # noqa: E402
        cls.lint_errors = staticmethod(lint_errors)
        cls.gb = satcom.drop_rule(at(recorded("name=GB&asn=31515&location=GB"), "2026-11-17T08:00:00Z"))
        cls.snap = blackout_cli.build_snapshot()
        row = {"asn": 31515, "operator": "Inmarsat", "class": "satellite_maritime_aviation",
               "radar": {"seen": True, "world": {"pct": 95.0, "weekday": {"state": "ok"}},
                         "countries": {"GB": {"pct": 60.0, "current_to": "2026-11-17T08:00:00Z", "weekday": cls.gb}}}}
        cls.snap["satcom"] = {**cls.snap["satcom"], "traffic": {"checked_at": "2026-11-17T08:05:00Z", "networks": [row]}}
        cls.fc = feed.build_feed(cls.snap)

    def items(self, fc=None):
        return [f["properties"] for f in (fc or self.fc)["features"] if f["properties"]["id"].startswith("blackout:satcom:")]

    def test_a_fired_country_series_is_one_neutral_observation(self):
        (p,) = self.items()
        self.assertEqual(p["id"], "blackout:satcom:31515:GB")
        self.assertEqual(p["subject_id"], p["id"])
        self.assertEqual((p["kind"], p["tier"], p["direction"], p["status_class"]),
                         ("observation", "anomaly", "neutral", "reported"))  # the kit lists no "measured"
        self.assertEqual((p["indicator_id"], p["warning_problem_id"], p["countries"], p["place_basis"]),
                         ("BO-IND-21", "BO-WP-3", ["GB"], "location"))
        self.assertEqual(p["event_start"], self.gb["since"])
        self.assertEqual(p["signal"]["value"], self.gb["weekday_pct"])
        self.assertEqual(p["sources"][0]["id"], "cloudflare-radar-satcom")
        self.assertIn("not an outage", p["summary"])
        self.assertIn("not its users", p["summary"])
        self.assertIn("CC BY-NC 4.0", p["summary"])
        self.assertEqual(self.lint_errors(self.fc), {})

    def test_a_series_that_is_not_fired_makes_no_item(self):
        snap = {**self.snap, "satcom": {**self.snap["satcom"], "traffic": {"networks": [{"asn": 800, "operator": "OneWeb", "radar": {
            "seen": True, "world": {"weekday": {"state": "sparse", "why": "too thin to measure"}}, "countries": {}}}]}}}
        self.assertEqual(self.items(feed.build_feed(snap)), [])

    def test_a_worldwide_series_has_no_place_and_a_drop_of_three_days_is_standing(self):
        reading = {**self.gb, "hours_below": 80, "standing": True}
        snap = {**self.snap, "satcom": {**self.snap["satcom"], "traffic": {"checked_at": "2026-11-17T08:05:00Z", "networks": [
            {"asn": 16491, "operator": "Viasat", "radar": {"seen": True, "world": {"weekday": reading}, "countries": {}}}]}}}
        fc = feed.build_feed(snap)
        (p,) = self.items(fc)
        self.assertEqual((p["id"], p["tier"], p["countries"]), ("blackout:satcom:16491", "routine", []))
        self.assertNotIn("place_basis", p)
        self.assertIn("standing condition", p["summary"])
        self.assertEqual(self.lint_errors(fc), {})

    def test_the_lane_is_in_the_health_header_and_fails_on_a_satcom_problem(self):
        self.assertEqual(self.fc["health"]["cloudflare-radar-satcom"]["state"], "fresh")
        self.assertEqual(feed.problem_lanes({"source": "satcom:radar", "error": "x"}), {"cloudflare-radar-satcom"})
        self.assertEqual(feed.problem_lanes({"source": "satcom", "error": "x"}), {"cloudflare-radar-satcom"})
        self.assertIn("cloudflare-radar-satcom", feed.LANES)
        snap = {**self.snap, "collection": {"problems": [{"source": "satcom:radar", "error": "boom"}]}}
        self.assertEqual(feed.build_feed(snap)["health"]["cloudflare-radar-satcom"]["state"], "failing")

    def test_the_indicator_and_source_are_registered(self):
        inds = {i["id"]: i for i in json.loads((ROOT / "doctrine" / "indicators.json").read_text(encoding="utf-8"))["indicators"]}
        self.assertIn("weekday", inds["BO-IND-21"]["text"] + inds["BO-IND-21"]["rule"])
        srcs = {x["id"]: x for x in json.loads((ROOT / "doctrine" / "sources.json").read_text(encoding="utf-8"))["sources"]}
        self.assertEqual(srcs["cloudflare-radar-satcom"]["licence"], feed.SRC_SATCOM["licence"])


class TestControlAndShort(unittest.TestCase):
    """ideas#168, replayed from real recorded Radar answers: Cloudflare's outage of 18 Nov 2025 and Starlink's worldwide
    outage of 24 Jul 2025."""

    @classmethod
    def setUpClass(cls):
        u = MANIFEST["control_urls"]
        cls.cf_control = satcom.control_hours(hourly(u["cf_control"]))
        cls.cf_us = hourly(u["cf_starlink_us"])
        cls.jul_control = satcom.control_hours(hourly(u["jul_control"]))
        cls.jul = hourly(u["jul_starlink"])

    def test_cloudflare_outage_is_held_not_flagged(self):
        raw = satcom.drop_rule(self.cf_us)
        self.assertEqual(raw["state"], "fired")                 # the rule alone fires on Cloudflare's own outage
        r = satcom.hold(raw, self.cf_control, satcom.series_end(self.cf_us))
        self.assertEqual(r["state"], "radar_wide")
        self.assertLess(r["control_low_pct"], satcom.CONTROL_BELOW_PCT)
        self.assertEqual(r["weekday_pct"], raw["weekday_pct"])  # the figures stay, named on the page

    def test_a_failed_or_gappy_control_is_no_control_and_not_flagged(self):
        raw = satcom.drop_rule(self.cf_us)
        end = satcom.series_end(self.cf_us)
        self.assertEqual(satcom.hold(raw, None, end)["state"], "no_control")
        gappy = {t: v for t, v in self.cf_control.items() if t != end - timedelta(hours=1)}
        self.assertEqual(satcom.hold(raw, gappy, end)["state"], "no_control")

    def test_a_reading_that_did_not_fire_passes_through(self):
        ok = {"state": "ok", "weekday_pct": 99.0}
        self.assertIs(satcom.hold(ok, None, None), ok)

    def test_24_jul_2025_fires_the_short_reading_on_one_hour(self):
        r = satcom.short_reading(self.jul, self.jul_control)
        self.assertEqual(r["state"], "fired")
        self.assertLess(r["lowest_pct"], satcom.SHORT_BELOW_PCT)
        self.assertTrue(r["since"].startswith("2025-07-24T"))

    def test_the_short_reading_is_held_on_a_radar_wide_hour_and_without_a_control(self):
        low = {t: 50.0 for t in self.jul_control}
        self.assertEqual(satcom.short_reading(self.jul, low)["state"], "radar_wide")
        self.assertEqual(satcom.short_reading(self.jul, None)["state"], "no_control")

    def test_a_steady_week_does_not_fire_the_short_reading(self):
        pts = recorded("name=world&asn=14593")
        control = satcom.control_hours(hourly(MANIFEST["control_urls"]["control_now"]))
        states = {satcom.short_reading(pts[:i], control)["state"] for i in range(len(pts) - 24 * 6, len(pts) + 1)}
        self.assertEqual(states, {"ok"})

    def test_the_lane_reads_the_control_once_and_a_failed_control_is_a_lane_problem(self):
        nets = [n for n in satcom.counted() if n["asn"] == 14593]
        calls = []
        def counting(url, *a, **k):
            calls.append(url)
            return replay(url)
        traffic, _d, probs = satcom.fetch_traffic(_t(MANIFEST["now"]), counting, replay, nets, None, workers=1)
        self.assertEqual(probs, [])
        self.assertTrue(traffic["control_read"])
        self.assertEqual(calls.count(MANIFEST["control_urls"]["control_now"]), 1)
        self.assertIn("short", traffic["networks"][0]["radar"]["world"])
        self.assertNotIn("short", traffic["networks"][0]["radar"]["countries"]["UA"])
        def no_control(url, *a, **k):
            if url == MANIFEST["control_urls"]["control_now"]:
                raise TimeoutError("timed out")
            return replay(url)
        traffic, _d, probs = satcom.fetch_traffic(_t(MANIFEST["now"]), no_control, replay, nets, None, workers=1)
        self.assertFalse(traffic["control_read"])
        self.assertEqual([p["asn"] for p in probs], ["all traffic"])
        self.assertEqual(feed.problem_lanes(probs[0]), {"cloudflare-radar-satcom"})

    def test_country_line_reads_real_all_traffic(self):
        pts = hourly(MANIFEST["control_urls"]["gb_all"])
        c = satcom.country_line(pts, satcom.series_end(pts))
        self.assertIsNotNone(c["pct"])
        self.assertFalse(c["fell"])


class TestShortAndCountryFeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_blackout import blackout_cli, lint_errors  # noqa: E402
        cls.lint_errors = staticmethod(lint_errors)
        cls.snap = blackout_cli.build_snapshot()
        u = MANIFEST["control_urls"]
        cls.short = satcom.short_reading(hourly(u["jul_starlink"]), satcom.control_hours(hourly(u["jul_control"])))
        cls.gb = satcom.drop_rule(at(recorded("name=GB&asn=31515&location=GB"), "2026-11-17T08:00:00Z"))

    def build(self, row):
        snap = {**self.snap, "satcom": {**self.snap["satcom"], "traffic": {"checked_at": "2026-11-17T08:05:00Z", "networks": [row]}}}
        fc = feed.build_feed(snap)
        return fc, [f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:satcom")]

    def test_the_short_item_is_a_neutral_observation_with_a_stable_subject(self):
        fc, items = self.build({"asn": 14593, "operator": "Starlink", "radar": {"seen": True, "world": {
            "pct": 80.0, "weekday": {"state": "ok"}, "short": self.short}, "countries": {}}})
        (p,) = items
        self.assertEqual((p["id"], p["subject_id"]), ("blackout:satcom-short:14593", "blackout:satcom-short:14593"))
        self.assertEqual((p["kind"], p["tier"], p["direction"], p["indicator_id"]),
                         ("observation", "anomaly", "neutral", "BO-IND-22"))
        self.assertEqual(p["signal"]["value"], self.short["lowest_pct"])
        self.assertIn("not called an outage", p["summary"])
        self.assertNotIn("probability", p)
        self.assertEqual(self.lint_errors(fc), {})

    def test_held_readings_make_no_item(self):
        for state in ("radar_wide", "no_control"):
            _fc, items = self.build({"asn": 14593, "operator": "Starlink", "radar": {"seen": True, "world": {
                "weekday": {**self.gb, "state": state}, "short": {**self.short, "state": state}}, "countries": {}}})
            self.assertEqual(items, [])

    def test_the_country_line_is_added_and_never_suppresses(self):
        row = lambda c: {"asn": 31515, "operator": "Inmarsat", "radar": {"seen": True, "world": {"weekday": {"state": "ok"}},
            "countries": {"GB": {"current_to": "2026-11-17T08:00:00Z", "weekday": {**self.gb, "country_all": c}}}}}
        _f, (fell,) = self.build(row({"pct": 62.5, "fell": True}))
        self.assertIn("All traffic from", fell["summary"])
        self.assertIn("62.5%", fell["summary"])
        self.assertEqual(fell["direction"], "neutral")
        _f, (flat,) = self.build(row({"pct": 99.0, "fell": False}))
        self.assertNotIn("All traffic from", flat["summary"])
        _f, (unread,) = self.build(row({"pct": None, "why": "not read: TimeoutError"}))
        self.assertNotIn("All traffic from", unread["summary"])

    def test_bo_ind_22_is_registered(self):
        inds = {i["id"]: i for i in json.loads((ROOT / "doctrine" / "indicators.json").read_text(encoding="utf-8"))["indicators"]}
        self.assertIn("60%", inds["BO-IND-22"]["rule"])
        self.assertIn("radar_wide", inds["BO-IND-21"]["rule"])


class TestPage(unittest.TestCase):
    def test_satellite_tab_is_the_ninth_hash(self):
        page = (ROOT / "site_src" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="tab-satcom" aria-controls="panel-satcom"', page)
        self.assertIn('id="panel-satcom" aria-labelledby="tab-satcom"', page)
        self.assertIn('const TABS = ["overview", "outages", "censorship", "power", "routing", "cables", "providers", '
                      '"satcom", "trends", "method"];', app)
        self.assertIn('id="sat-q"', page)


if __name__ == "__main__":
    unittest.main()
