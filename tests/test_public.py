"""Offline tests for the public-report lanes (BO-WP-7), added 17 Nov 2026: Bluesky post counts (BO-IND-18) and GDELT
news headlines (BO-IND-19).

Bluesky: real searchPosts responses recorded by tests/make_bluesky_fixtures.py, listed under their URLs in
tests/fixtures/bluesky/MANIFEST.json, each reduced to hitsTotal (the post and the cursor, which encodes that post's
author and record key, are dropped before writing). GDELT: real GKG 15-minute files recorded by
tests/make_gdelt_fixtures.py, cut to the rows that matter and the five columns Blackout reads (MANIFEST.json says how),
plus the per-file matches of the 7 days before each window as the lane carries them. The days are the outages the
lanes were built for: Chase, Bank of America, Wells Fargo and United Airlines on 16 Nov 2026, the FAA's ground stop
and the National Weather Service on 13 Nov 2026."""
from __future__ import annotations

import io
import json
import re
import sys
import unittest
import urllib.error
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackout import bluesky, feed, gdelt  # noqa: E402
from test_blackout import blackout_cli, lint_errors  # noqa: E402

BSKY = ROOT / "tests" / "fixtures" / "bluesky"
BSKY_MANIFEST = json.loads((BSKY / "MANIFEST.json").read_text(encoding="utf-8"))
GDELT = ROOT / "tests" / "fixtures" / "gdelt"
GDELT_MANIFEST = json.loads((GDELT / "MANIFEST.json").read_text(encoding="utf-8"))


def at(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def bsky_fetch(fail: dict | None = None):
    """Replay the recorded bodies; `fail` maps a substring of a URL to an exception raised for it."""
    def fetch(url: str) -> bytes:
        for part, exc in (fail or {}).items():
            if part in url:
                raise exc
        return json.dumps(BSKY_MANIFEST["responses"][url]["body"]).encode()
    return fetch


def bsky(ts: str, keys: list[str], fail: dict | None = None):
    watch = [w for w in bluesky.WATCH if w[0] in keys]
    return bluesky.fetch_bluesky(at(ts), fetch=bsky_fetch(fail), pause=lambda s: None, watch=watch)


def gdelt_fetch(scenario: str, fail: dict | None = None):
    sc = GDELT_MANIFEST["scenarios"][scenario]

    def fetch(url: str) -> bytes:
        for part, exc in (fail or {}).items():
            if part in url:
                raise exc
        name = sc["files"].get(url)
        if name is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return (GDELT / name).read_bytes()
    return fetch


def gdelt_run(scenario: str, fail: dict | None = None, carry: bool = True):
    sc = GDELT_MANIFEST["scenarios"][scenario]
    doc = json.loads((GDELT / sc["carry"]).read_text(encoding="utf-8")) if carry else None
    return gdelt.fetch_gdelt(at(sc["run"]), fetch=gdelt_fetch(scenario, fail), pause=lambda s: None, carry=doc,
                             newest=gdelt.stamp(at(sc["newest"])))


def live_feed(ts: str, problems: list | None = None, **parts) -> dict:
    raw = {"mode": "live", "generated_at": ts, "raw_outages": [], "raw_ooni": [], "raw_tips": [],
           "collection": {"problems": problems or []}, **parts}
    return feed.build_feed(blackout_cli.build_snapshot(raw))


def http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.bsky.app/x", code, "x", {}, None)


class TestBlueskyCounts(unittest.TestCase):
    def test_bank_outages_surge_and_quiet_names_stay_under(self):
        # 21:45 UTC 16 Nov 2026: Chase blamed a software bug that day; Bank of America and Wells Fargo customers
        # reported the same (CNN, Fortune). United's outage had not begun; Marriott and Delta were quiet.
        snap, problems = bsky("2026-11-16T21:45:00Z", ["chase", "bank-of-america", "wells-fargo", "united-airlines",
                                                     "marriott", "delta"])
        self.assertEqual(problems, [])
        self.assertEqual({r["key"] for r in snap["surges"]}, {"chase", "bank-of-america", "wells-fargo"})
        rows = {r["key"]: r for r in snap["rows"]}
        self.assertEqual((rows["bank-of-america"]["matches"], rows["wells-fargo"]["matches"], rows["chase"]["matches"]),
                         (471, 378, 703))
        self.assertGreater(rows["chase"]["usual"], 10)  # "Chase" is a common word: a higher usual, the ratio absorbs it
        for k in ("united-airlines", "marriott", "delta"):
            self.assertFalse(rows[k]["surge"], k)
        boa = rows["bank-of-america"]
        self.assertEqual(len(boa["hours"]), bluesky.WINDOW_H)
        self.assertEqual(boa["hours"][0]["hour"], "2026-11-16T15:45:00Z")
        self.assertIsNotNone(boa["first_hour"])
        self.assertIn(boa["peak_hour"], [h["hour"] for h in boa["hours"]])
        self.assertTrue(boa["link"].startswith("https://bsky.app/search?q="))

    def test_united_surges_in_the_next_run(self):
        snap, _ = bsky("2026-11-17T01:45:00Z", ["united-airlines", "chase"])
        united = next(r for r in snap["rows"] if r["key"] == "united-airlines")
        self.assertTrue(united["surge"])
        self.assertEqual(united["matches"], 1371)

    def test_faa_and_weather_service(self):
        snap, _ = bsky("2026-11-13T21:45:00Z", ["faa", "nws", "outlook"])
        rows = {r["key"]: r for r in snap["rows"]}
        self.assertTrue(rows["faa"]["surge"])  # the ground stop began 20:54 UTC
        self.assertFalse(rows["nws"]["surge"])  # the Weather Service's posts came after this run
        # Outlook a day into Microsoft's outage: 1,300 posts, but the outage's first day is now in its own usual.
        self.assertEqual(rows["outlook"]["matches"], 1300)
        self.assertFalse(rows["outlook"]["surge"])
        snap, _ = bsky("2026-11-14T01:45:00Z", ["nws", "faa"])
        self.assertEqual({r["key"] for r in snap["surges"]}, {"nws", "faa"})

    def test_a_failed_name_is_a_problem_never_a_zero(self):
        snap, problems = bsky("2026-11-16T21:45:00Z", ["chase", "marriott"],
                              fail={"Marriott": http(400)})
        self.assertEqual([r["key"] for r in snap["rows"]], ["chase"])
        self.assertEqual(problems[0]["endpoint"], "marriott")
        self.assertEqual(feed.problem_lanes({"source": f"bluesky:{problems[0]['endpoint']}", "error": "x"}),
                         {"bluesky-search"})

    def test_rate_limits_are_retried(self):
        calls = {"n": 0}
        real = bsky_fetch()

        def flaky(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http(429)
            return real(url)
        pauses = []
        watch = [w for w in bluesky.WATCH if w[0] == "marriott"]
        snap, problems = bluesky.fetch_bluesky(at("2026-11-16T21:45:00Z"), fetch=flaky, pause=pauses.append,
                                               watch=watch)
        self.assertEqual((problems, pauses), ([], [bluesky.RETRY_PAUSES_S[0]]))

    def test_a_response_without_hits_total_is_an_error(self):
        with self.assertRaises(ValueError):
            bluesky.hits(b'{"posts": []}')

    def test_the_fixtures_hold_no_post_or_author(self):
        text = (BSKY / "MANIFEST.json").read_text(encoding="utf-8")
        self.assertNotIn("did:", text)
        self.assertNotIn('"cursor":', text)
        for url, r in BSKY_MANIFEST["responses"].items():
            self.assertEqual(r["body"]["posts"], [], url)
            self.assertEqual(set(r["body"]), {"posts", "hitsTotal"}, url)

    def test_feed_items_are_reported_never_assessed(self):
        snap, _ = bsky("2026-11-16T21:45:00Z", ["chase", "bank-of-america", "wells-fargo", "marriott"])
        fc = live_feed("2026-11-16T21:50:00Z", bluesky=snap)
        items = [f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:public:")]
        self.assertEqual(len(items), 3)
        for p in items:
            self.assertEqual((p["kind"], p["status_class"], p["direction"], p["us_person_data"], p["tier"]),
                             ("observation", "reported", "neutral", "organizations", "anomaly"))
            self.assertEqual((p["indicator_id"], p["warning_problem_id"], p["requirement_ids"]),
                             ("BO-IND-18", "BO-WP-7", ["BO-PIR-12"]))
            self.assertIn("Reported by the public on Bluesky and not confirmed by", p["summary"])
            self.assertEqual((p["sources"][0]["id"], p["sources"][0]["grade"]), ("bluesky-search", "F3"))
            self.assertTrue(p["sources"][0]["url"].startswith("https://bsky.app/search?q="))
        boa = next(p for p in items if "Bank of America" in p["title"])
        self.assertTrue(boa["id"].startswith("blackout:public:bluesky-bank-of-america-2026-11-16"))
        chase = next(p for p in items if p["id"].startswith("blackout:public:bluesky-chase-"))
        self.assertIn("of 49 '\"Chase\" down' posts read on 2026-11-22", chase["summary"])  # precision
        self.assertNotIn("Many posts use", boa["summary"])
        self.assertEqual(lint_errors(fc), {})
        self.assertEqual(fc["health"]["bluesky-search"]["state"], "fresh")


class TestGdeltHeadlines(unittest.TestCase):
    def test_newest_file_from_lastupdate(self):
        body = (GDELT / GDELT_MANIFEST["lastupdate_sample"]).read_bytes()
        self.assertRegex(gdelt.newest_stamp(body), r"^\d{14}$")
        with self.assertRaises(ValueError):
            gdelt.newest_stamp(b"nothing here\n")

    def test_real_headlines_match_their_service(self):
        for key, title in GDELT_MANIFEST["match_cases"]:
            self.assertIn(key, gdelt.match(title), title)

    def test_words_in_another_sense_do_not_count(self):
        for title in ("Wells Fargo shares are down 3% after earnings", "Police chase ends downtown",
                      "Stocks down as Delta earnings miss", "Student visa disruption ahead of term",
                      "Economic outlook darkens as trade disruption grows", "Plane crash: United flight lands safely"):
            self.assertEqual(gdelt.match(title), [], title)

    def test_watch_lists_share_keys(self):
        self.assertEqual([k for k, *_ in gdelt.WATCH], [k for k, *_ in bluesky.WATCH])

    def test_bank_and_united_outages(self):
        sc = GDELT_MANIFEST["scenarios"]["banks-united"]
        snap, problems = gdelt_run("banks-united")
        self.assertEqual(problems, [])
        self.assertEqual(snap["window_files"], 24)
        self.assertEqual(snap["window_read"] + len(snap["window_missing"]), 24)
        self.assertEqual({r["key"] for r in snap["surges"]}, set(sc["expect_surges"]))
        for r in snap["surges"]:
            self.assertGreaterEqual(r["outlets"], gdelt.MIN_OUTLETS)
            self.assertTrue(all(set(x) == {"domain", "url"} for x in r["links"]))
        self.assertEqual(snap["files_read_now"], snap["window_read"])  # the baseline came from the carry

    def test_weather_service(self):
        sc = GDELT_MANIFEST["scenarios"]["nws"]
        snap, problems = gdelt_run("nws")
        self.assertEqual(problems, [])
        self.assertEqual({r["key"] for r in snap["surges"]}, set(sc["expect_surges"]))

    def test_a_window_file_not_read_fails_the_lane_and_a_404_does_not(self):
        sc = GDELT_MANIFEST["scenarios"]["nws"]
        stamps = sorted(gdelt.stamps(at(sc["newest"]) - gdelt.STEP * 23, at(sc["newest"])))
        snap, problems = gdelt_run("nws", fail={stamps[3]: OSError("reset"),
                                                stamps[5]: urllib.error.HTTPError("x", 404, "Not Found", {}, None)})
        self.assertEqual([p["endpoint"] for p in problems], ["gkg-window"])
        self.assertIn("1 of 24", problems[0]["error"])
        self.assertEqual(snap["window_missing"], [stamps[5]])
        self.assertEqual(feed.problem_lanes({"source": "gdelt:gkg-window", "error": "x"}), {"gdelt-gkg"})

    def test_a_replay_over_files_carried_as_counts_only(self):
        # A carry keeps links only for files the next run's window can hold; replaying a past window over a later
        # carry meets its files as counts alone. The count still stands and no first or peak file is named.
        sc = GDELT_MANIFEST["scenarios"]["banks-united"]
        doc = json.loads((GDELT / sc["carry"]).read_text(encoding="utf-8"))
        snap, _ = gdelt_run("banks-united")
        held = snap[gdelt.CARRY_KEY]["files"]
        doc["files"] = {s: {k: gdelt._count(v) for k, v in found.items()} for s, found in held.items()}
        again, problems = gdelt.fetch_gdelt(at(sc["run"]), fetch=gdelt_fetch("banks-united"), pause=lambda s: None,
                                            carry=doc, newest=gdelt.stamp(at(sc["newest"])))
        self.assertEqual(problems, [])
        self.assertEqual(again["files_read_now"], 0)
        before = {r["key"]: r["headlines"] for r in snap["rows"]}
        rows = [r for r in again["rows"] if r["headlines"]]
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["headlines"], before[r["key"]])
            self.assertNotIn("first_file", r)

    def test_without_a_carry_the_usual_is_partial_and_says_so(self):
        snap, problems = gdelt_run("nws", carry=False)
        self.assertEqual(problems, [])
        self.assertEqual(snap["baseline_files"], 0)  # the recorded files are the window's only
        fc = live_feed("2026-11-14T01:50:00Z", gdelt=snap)
        for f in fc["features"]:
            if f["properties"]["id"].startswith("blackout:public:gdelt-"):
                self.assertIn("files of the 7 days before, the rest not yet read", f["properties"]["summary"])

    def test_the_carry_keeps_no_headline_and_only_the_span(self):
        snap, _ = gdelt_run("banks-united")
        carry = snap[gdelt.CARRY_KEY]
        self.assertEqual(carry["format"], gdelt.CARRY_FORMAT)
        newest = gdelt.parse_stamp(max(carry["files"]))
        self.assertLessEqual(len(carry["files"]) + len(carry["missing"]), 24 + 7 * 96)
        self.assertTrue(all(newest - gdelt.parse_stamp(s) < gdelt.STEP * (24 + 7 * 96) for s in carry["files"]))
        window = set(gdelt.stamps(newest - gdelt.STEP * 23, newest))
        for s, entries in carry["files"].items():
            for v in entries.values():
                if s in window:  # links (outlet, URL; never a headline) only where the next window can reach
                    self.assertTrue(all(len(link) == 2 for link in gdelt._links(v)))
                else:
                    self.assertIsInstance(v, int)

    def test_feed_items_carry_links_never_headlines(self):
        snap, _ = gdelt_run("banks-united")
        fc = live_feed("2026-11-17T01:50:00Z", gdelt={k: v for k, v in snap.items() if k != gdelt.CARRY_KEY})
        items = [f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:public:gdelt-")]
        self.assertTrue(items)
        text = json.dumps(fc)
        for _, title in GDELT_MANIFEST["match_cases"]:
            self.assertNotIn(title, text)
        for p in items:
            self.assertEqual((p["kind"], p["status_class"], p["direction"], p["us_person_data"]),
                             ("observation", "reported", "neutral", "organizations"))
            self.assertEqual((p["indicator_id"], p["sources"][0]["id"], p["sources"][0]["grade"]),
                             ("BO-IND-19", "gdelt-gkg", "C3"))
            self.assertIn("Reported by the press and not confirmed by", p["summary"])
            self.assertTrue(all(s["url"].startswith("http") for s in p["sources"]))
        self.assertEqual(lint_errors(fc), {})

    def test_the_fixtures_are_excerpts(self):
        for sc in GDELT_MANIFEST["scenarios"].values():
            for name in sc["files"].values():
                z = zipfile.ZipFile(io.BytesIO((GDELT / name).read_bytes()))
                for row in z.read(z.namelist()[0]).decode("utf-8").split("\n"):
                    if not row:
                        continue
                    cols = row.split("\t")
                    self.assertEqual(len(cols), 27)
                    self.assertTrue(all(c == "" for i, c in enumerate(cols) if i not in (0, 1, 2, 3, 4, 26)))
                    self.assertRegex(cols[26], r"^(<PAGE_TITLE>.*</PAGE_TITLE>)?$")


class TestPublicLanes(unittest.TestCase):
    def test_both_lanes_on_one_name_point_at_each_other(self):
        b, _ = bsky("2026-11-17T01:45:00Z", ["united-airlines", "chase"])
        g, _ = gdelt_run("banks-united")
        g = {k: v for k, v in g.items() if k != gdelt.CARRY_KEY}
        both = {r["key"] for r in b["surges"]} & {r["key"] for r in g["surges"]}
        self.assertTrue(both)
        fc = live_feed("2026-11-17T01:50:00Z", bluesky=b, gdelt=g)
        k = sorted(both)[0]
        props = [f["properties"] for f in fc["features"] if f["properties"]["id"].startswith("blackout:public:")
                 and f"-{k}-" in f["properties"]["id"]]
        self.assertEqual(len(props), 2)
        self.assertTrue(all("see that item" in p["summary"] for p in props))

    def test_lanes_are_in_the_health_block(self):
        fc = live_feed("2026-11-17T01:50:00Z", problems=[{"source": "gdelt", "error": "boom"}])
        self.assertEqual(fc["health"]["gdelt-gkg"]["state"], "failing")
        self.assertEqual(fc["health"]["bluesky-search"]["state"], "fresh")

    def test_doctrine_registers_the_lanes(self):
        ind = json.loads((ROOT / "doctrine" / "indicators.json").read_text(encoding="utf-8"))
        ids = {i["id"] for i in ind["indicators"]} | {w["id"] for w in ind["warning_problems"]}
        self.assertLessEqual({"BO-IND-18", "BO-IND-19", "BO-WP-7"}, ids)
        src = {s["id"]: s for s in json.loads((ROOT / "doctrine" / "sources.json").read_text(encoding="utf-8"))["sources"]}
        self.assertEqual((src["bluesky-search"]["grade"], src["gdelt-gkg"]["grade"]), ("F3", "C3"))
        self.assertEqual(src["bluesky-search"]["licence"], feed.SRC_BLUESKY["licence"])
        self.assertEqual(src["gdelt-gkg"]["licence"], feed.SRC_GDELT["licence"])
        # The page and doctrine state the rule's numbers; they must be the module's.
        text = ind["indicators"][[i["id"] for i in ind["indicators"]].index("BO-IND-18")]["text"]
        self.assertIn(f"at least {bluesky.MIN_MATCHES} in the {bluesky.WINDOW_H} hours", text)
        self.assertIn(f"at least {bluesky.RATIO:g} times", text)
        text = ind["indicators"][[i["id"] for i in ind["indicators"]].index("BO-IND-19")]["text"]
        self.assertIn(f"at least {gdelt.MIN_HEADLINES} headlines from at least {gdelt.MIN_OUTLETS} outlets", text)
        page = (ROOT / "site_src" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"A surge is at least {bluesky.MIN_MATCHES} posts and at least {bluesky.RATIO:g} times", page)
        self.assertIn(f"A surge is at least {gdelt.MIN_HEADLINES} headlines from at least {gdelt.MIN_OUTLETS} outlets",
                      page)

    def test_calibration_is_recorded(self):
        self.assertTrue(bluesky.CALIBRATION)
        self.assertTrue(gdelt.CALIBRATION)


if __name__ == "__main__":
    unittest.main()
