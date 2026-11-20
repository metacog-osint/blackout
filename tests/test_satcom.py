"""The satellite networks register and traffic lane (blackout/satcom.py): complete, closed classes, nothing excluded,
searchable; traffic against each network's own 28-day normal, replayed from recorded Radar and IODA answers."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
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

    def test_a_failed_read_is_listed_and_on_no_feed_lane(self):
        def broken(url, *a, **k):
            raise TimeoutError("timed out")
        nets = [n for n in satcom.counted() if n["asn"] == 14593]
        traffic, daily, probs = satcom.fetch_traffic(_t(MANIFEST["now"]), broken, broken, nets,
                                                     _t(MANIFEST["trend_first"]), workers=1)
        self.assertEqual(traffic["networks"][0]["radar"], {"seen": None})
        self.assertEqual(traffic["read"], {"radar": False, "ioda": False})
        self.assertEqual({p["source"] for p in probs}, {"satcom:radar", "satcom:ioda"})
        for p in probs:
            self.assertEqual(feed.problem_lanes(p), set())

    def test_never_reads_estimated_users(self):
        self.assertFalse(any("users" in u.lower() for u in MANIFEST["files"].values()))


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
