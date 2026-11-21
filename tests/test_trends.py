"""Daily outage series (blackout/trends.py), replayed from the real responses in tests/fixtures/trends (MANIFEST.json)."""
from __future__ import annotations

import gzip
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackout import archive, feed, trends  # noqa: E402
from test_blackout import blackout_cli  # noqa: E402
from test_netblocks import nb_posts  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "trends"
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
NOW = datetime.strptime(MANIFEST["now"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _load(name):
    return json.loads(gzip.decompress((FIX / name).read_bytes()))


def ioda_fetch(url):
    assert "/outages/events?" in url and "entityType=country" in url, url
    return _load("ioda_events.json.gz")


def radar_fetch(url):
    return _load("radar_outages.json.gz" if "annotations/outages" in url else "radar_anomalies.json.gz")


def replay(now=NOW, ioda=ioda_fetch, radar=radar_fetch, nb="replay"):
    return trends.fetch(now, ioda_fetch=ioda, radar_fetch=radar, netblocks_posts=nb_posts() if nb == "replay" else nb)


class TestTrends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.problems = replay()

    def test_shape_and_windows(self):
        d = self.doc
        self.assertEqual(self.problems, [])
        self.assertEqual((d["format"], d["mode"]), (trends.FORMAT, "live"))
        self.assertEqual((d["first_day"], d["through"], d["today"]), ("2026-09-21", "2026-11-19", "2026-11-20"))
        self.assertEqual(sorted(d["totals"], key=int), ["7", "14", "30"])
        self.assertEqual(d["read"], {"ioda": True, "radar_outages": True, "radar_anomalies": True,
                                     "netblocks": True, "satcom": False})
        for w in ("7", "14", "30"):
            for part in ("current", "previous"):
                self.assertEqual(set(d["totals"][w][part]), {"episodes", "hours", "standing_hours", "radar_outages",
                                                             "radar_anomalies", "countries_with_outage",
                                                             "countries_standing", "netblocks_posts",
                                                             "netblocks_connectivity", "netblocks_lowest_level_pct"})

    def test_measurement_wide_bursts_are_left_out(self):
        bursts = self.doc["notes"]["ioda_bursts_left_out"]
        self.assertEqual([(b["start"], b["datasource"]) for b in bursts][:2],
                         [("2026-10-15T22:00:00Z", "ping-slash24"), ("2026-10-18T00:00:00Z", "ping-slash24")])
        self.assertEqual(len(bursts), 7)
        self.assertTrue(all(b["countries"] >= trends.BURST_COUNTRIES for b in bursts))
        # On 18 Oct no country is left with a 00:00 active-probing episode from the burst.
        oct18 = sum((c["days"].get("2026-10-18") or {}).get("episodes", 0) for c in self.doc["countries"].values())
        self.assertLess(oct18, 20)

    def test_radar_outage_named_by_network_goes_to_its_country(self):
        # Radar 1752 names AS15796 (Salt, Switzerland) and no location; 1751 is Pakistan, nationwide, cause unknown.
        ch = self.doc["countries"]["CH"]["days"]["2026-11-18"]["radar_outages"]
        self.assertIn("1752", [o["id"] for o in ch])
        pk = self.doc["countries"]["PK"]["days"]["2026-11-18"]["radar_outages"]
        self.assertEqual(pk, [{"id": "1751", "cause": "UNKNOWN", "type": "NATIONWIDE"}])

    def test_each_country_carries_exposure_an_upper_bound_with_its_year(self):
        from blackout import itu
        pk = self.doc["countries"]["PK"]["exposure"]
        row = itu.load()["countries"]["PK"]
        self.assertEqual((pk["share_pct"], pk["year"], pk["population"]), tuple(row))
        self.assertEqual(pk["internet_users_upper"], round(row[0] / 100 * row[2]))
        self.assertIn("never how many it did", self.doc["exposure_basis"])
        self.assertIn("World Development Indicators", self.doc["exposure_source"])
        rows = [r for r in trends.settled_rows(self.doc, NOW) if r["kind"] == "country" and r["country"] == "PK"]
        self.assertTrue(rows)
        self.assertEqual({(r["internet_users_upper"], r["itu_year"]) for r in rows}, {(pk["internet_users_upper"], row[1])})

    def test_window_sums_match_the_days(self):
        c = self.doc["countries"]["NG"]
        days = sorted(d for d in c["days"] if d <= self.doc["through"])
        last7 = [d for d in days if d > "2026-11-12"]
        self.assertEqual(c["windows"]["7"]["current"]["episodes"], sum(c["days"][d].get("episodes", 0) for d in last7))
        self.assertAlmostEqual(c["windows"]["7"]["current"]["hours"],
                               round(sum(c["days"][d].get("hours", 0) for d in last7), 1), places=1)

    def test_standing_bgp_flags_are_marked_and_counted_apart(self):
        # A BGP-only flag set 18 h+ a day for 7+ days in a row is a routing change that never clears.
        d = self.doc
        self.assertEqual(set(d["notes"]["standing"]["countries"]) & {"BM", "PM", "AM", "TJ", "RS", "GI", "GQ", "SC", "CK"},
                         {"BM", "PM", "AM", "TJ", "RS", "GI", "GQ", "SC", "CK"})
        bm = d["countries"]["BM"]
        self.assertEqual(bm["standing"], [{"first": "2026-09-21", "last": "2026-11-20", "days": 61, "hours": 1455.0}])
        self.assertTrue(all(r.get("standing") for r in bm["days"].values()))
        w = bm["windows"]["30"]["current"]
        self.assertEqual((w["hours"], w["standing_hours"], w["days_with_outage"]), (w["hours"], w["hours"], 0))
        # A country with outages that are not standing keeps them counted.
        ng = d["countries"]["NG"]["windows"]["7"]["current"]
        self.assertEqual(ng["standing_hours"], 0.0)
        self.assertGreater(ng["days_with_outage"], 0)
        # World: the standing part summed over countries; a country whose only hours were standing is not counted.
        for w in ("7", "14", "30"):
            for part in ("current", "previous"):
                t = d["totals"][w][part]
                n = int(w)
                dates = d["countries"]["BM"]["days"]
                days = sorted(x for x in dates if x <= d["through"])
                sl = days[-n:] if part == "current" else days[-2 * n:-n]
                exp = round(sum(r.get("hours", 0) for c in d["countries"].values() for x, r in c["days"].items()
                                if x in sl and r.get("standing")), 1)
                self.assertAlmostEqual(t["standing_hours"], exp, places=0)
                self.assertLessEqual(t["standing_hours"], t["hours"])
        self.assertGreaterEqual(d["totals"]["7"]["current"]["countries_standing"], 9)

    def test_standing_runs_rule(self):
        def row(h, sig=("bgp",)):
            return {"hours": h, "hours_by_signal": {s: h for s in sig}}
        dates = [f"2026-11-{i:02d}" for i in range(1, 13)]
        # 6.5 h partial start, 7 full days, a 3 h partial end, then a probing day that is a real outage.
        days = {dates[0]: row(6.5), **{dates[i]: row(24.0) for i in range(1, 8)}, dates[8]: row(3.0),
                dates[10]: row(5.0, ("bgp", "ping-slash24"))}
        marked, runs = trends.standing_runs(days, dates)
        self.assertEqual(marked, set(dates[0:9]))
        self.assertEqual(runs, [{"first": dates[0], "last": dates[8], "days": 9, "hours": 177.5}])
        # Six full days in a row is not standing; nor is a full day seen by active probing too.
        days = {**{dates[i]: row(24.0) for i in range(6)}, dates[6]: row(24.0, ("bgp", "ping-slash24"))}
        self.assertEqual(trends.standing_runs(days, dates), (set(), []))

    def test_trends_tab_is_the_tenth_hash_and_reads_the_series(self):
        page = (ROOT / "site_src" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="tab-trends" aria-controls="panel-trends"', page)
        self.assertIn('id="panel-trends" aria-labelledby="tab-trends"', page)
        self.assertLess(page.index('id="tab-satcom"'), page.index('id="tab-trends"'))
        self.assertLess(page.index('id="tab-trends"'), page.index('id="tab-method"'))
        self.assertIn('getJSON("data/trends.json", true)', app)
        self.assertIn(f'"{trends.FORMAT}"', app)
        for v in trends.WINDOWS:
            self.assertIn(f'name="trend-w" value="{v}"', page)
        # Standing flags are named apart and taken out of the counted hours; exposure is never users affected.
        self.assertIn("p.hours - (p.standing_hours || 0)", app)
        self.assertIn("an upper bound", page)
        self.assertNotIn("users affected", app.replace("never a count of users affected", ""))

    def test_long_tables_open_in_a_ten_row_window(self):
        # Suite rule (20 Nov 2026): a table over 10 rows scrolls in a window about 10 rows tall, with "Show all".
        app = (ROOT / "site_src" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "site_src" / "style.css").read_text(encoding="utf-8")
        # Since 20 Nov 2026 the hub's tic-fold.js 2.0 draws the window (it was Blackout's own, blackout#24).
        page = (ROOT / "site_src" / "index.html").read_text(encoding="utf-8")
        fold = (ROOT / "site_src" / "tic-fold.js").read_text(encoding="utf-8")
        fold_css = (ROOT / "site_src" / "tic-fold.css").read_text(encoding="utf-8")
        self.assertIn('h("table", { class: "tbl", "data-tic-fold": "" }', app)
        self.assertIn('<script src="tic-fold.js" defer></script>', page)
        self.assertIn('<link rel="stylesheet" href="tic-fold.css">', page)
        self.assertTrue(fold.startswith("/*! tic-fold.js v2.0"))
        self.assertIn('return [10, 10];', fold)
        self.assertIn("overflow-y: auto", fold_css)
        self.assertIn("thead th, .tic-fold-win thead td { position: sticky; top: 0;", fold_css)
        self.assertNotIn("tbl-window", app + css)
        # Card lists (Routing's BGP events and the rest) open in the same window once they pass ten cards.
        lists = re.findall(r'<div id="[^"]+" class="rows"[^>]*>', page)
        self.assertEqual(len(lists), 18)
        self.assertTrue(all("data-tic-fold" in d for d in lists), lists)

    def test_hours_never_exceed_a_day(self):
        for c in self.doc["countries"].values():
            for r in c["days"].values():
                self.assertLessEqual(r.get("hours", 0), 24.0)
                for h in (r.get("hours_by_signal") or {}).values():
                    self.assertLessEqual(h, 24.0)

    def test_overlapping_signals_are_one_episode_and_split_at_midnight(self):
        # Two signals of one outage (shapes as IODA gives them), from 23:00 to 01:30 the next day.
        t = datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc).timestamp()
        evs = [{"location": "country/TD", "start": t, "duration": 9000, "datasource": "bgp", "score": 100.0},
               {"location": "country/TD", "start": t + 1800, "duration": 3600, "datasource": "ping-slash24",
                "score": 50.0},
               {"location": "country/TD", "start": t, "duration": 9000, "datasource": "gtr", "score": 999.0}]
        first = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
        out = trends.ioda_daily(evs, first, first + 30 * 86400)["TD"]
        self.assertEqual(out["2026-11-10"]["episodes"], 1)
        self.assertEqual(out["2026-11-10"]["hours"], 1.0)
        self.assertEqual(out["2026-11-11"]["hours"], 1.5)
        self.assertEqual(out["2026-11-11"]["episodes"], 0)
        self.assertEqual(out["2026-11-10"]["max_score"], 100.0)  # Google traffic (gtr) is not read

    def test_a_failed_source_is_null_never_zero(self):
        def down(url):
            raise OSError("ioda down")
        doc, problems = replay(ioda=down)
        self.assertEqual([p["source"] for p in problems], ["trends:ioda"])
        self.assertFalse(doc["read"]["ioda"])
        self.assertIsNone(doc["totals"]["7"]["current"]["episodes"])
        self.assertIsNotNone(doc["totals"]["7"]["current"]["radar_outages"])
        self.assertEqual(feed.problem_lanes(problems[0]), set())  # never fails the live IODA lane
        self.assertEqual(trends.settled_rows(doc, NOW), [])     # nor writes a false zero to the archive

    def test_a_full_page_is_a_problem(self):
        def full(url):
            body = ioda_fetch(url)
            return {**body, "data": body["data"] * (trends.IODA_LIMIT // len(body["data"]) + 1)}
        doc, problems = replay(ioda=full)
        self.assertIn("full page", problems[0]["error"])
        self.assertFalse(doc["read"]["ioda"])

    def test_archive_writes_each_settled_day_once(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            # The clock is pinned: which days count as settled moves with the date (the test began failing at
            # 00:00 UTC on 21 Nov 2026, when 2026-11-17 settled).
            now = datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)
            n = archive.archive_trends(tmp, self.doc, now)
            rows = archive.read_rows(tmp / archive.TRENDS_FILE)
            self.assertEqual(n, len(rows))
            days = [r["day"] for r in rows if r["kind"] == "day"]
            self.assertEqual((days[0], days[-1]), ("2026-09-21", "2026-11-16"))  # 2026-11-17 is not settled yet
            self.assertEqual(archive.archive_trends(tmp, self.doc, now), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_published_from_live_runs_only(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / "doctrine", tmp / "doctrine")
            shutil.copytree(ROOT / "site_src", tmp / "site_src")
            (tmp / "data").mkdir()
            (tmp / "data" / trends.NAME).write_text(trends.dumps(self.doc), encoding="utf-8")
            seed = blackout_cli.build_snapshot()
            (tmp / "data" / "snapshot.json").write_text(json.dumps(seed), encoding="utf-8")
            self.assertNotIn(f"data/{trends.NAME}", blackout_cli.build_site(tmp))
            (tmp / "data" / "snapshot.json").write_text(json.dumps({**seed, "mode": "live"}), encoding="utf-8")
            files = blackout_cli.build_site(tmp)
            self.assertEqual(json.loads(files[f"data/{trends.NAME}"])["through"], "2026-11-19")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
