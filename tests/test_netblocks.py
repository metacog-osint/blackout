"""NetBlocks lane (blackout/netblocks.py), replayed from the real Bluesky responses in tests/fixtures/netblocks
(MANIFEST.json; each post's text cut to the excerpt length there). Parser cases use short sentences written for these
tests in NetBlocks' style, not NetBlocks' own posts."""
from __future__ import annotations

import gzip
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))

from blackout import netblocks  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "netblocks"
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
BY_URL = {url: name for name, url in MANIFEST["files"].items()}
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)


def replay_get(url):
    return json.loads(gzip.decompress((FIX / BY_URL[url]).read_bytes()))


def nb_posts():
    posts, problems = netblocks.fetch(getter=replay_get)
    assert not problems, problems
    return posts


def entry(text, tags=(), reply=False, handle="netblocks.org", rkey="3abc", created="2026-11-19T10:00:00.000Z"):
    rec = {"text": text, "createdAt": created,
           "facets": [{"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": t}]} for t in tags]}
    if reply:
        rec["reply"] = {"root": {"uri": "at://x/app.bsky.feed.post/root"}, "parent": {"uri": "at://x/p"}}
    return {"post": {"uri": f"at://did:plc:x/app.bsky.feed.post/{rkey}", "author": {"handle": handle},
                     "record": rec, "indexedAt": created}}


class TestParse(unittest.TestCase):
    def test_level_country_region_and_cause(self):
        p = netblocks.parse(entry("Confirmed: Metrics show connectivity in Sampletown, #Morocco, falling to 61% of "
                                  "ordinary levels after a storm cut power", tags=["Morocco"]))
        self.assertEqual((p["type"], p["countries"], p["level_pct"], p["level_basis"]),
                         ("Confirmed", ["MA"], 61.0, "% of ordinary levels"))
        self.assertIn("Sampletown", p["regions"])
        self.assertEqual(set(p["causes"]), {"power", "weather"})
        self.assertEqual(p["scope"], "connectivity")
        self.assertEqual(p["url"], "https://bsky.app/profile/netblocks.org/post/3abc")
        self.assertEqual(p["posted_at"], "2026-11-19T10:00:00Z")

    def test_hashtag_country_wins_over_named_actor(self):
        # The country attacked is hashtagged; the actor named in the text is not the place of the outage.
        p = netblocks.parse(entry("Update: Network data show a drop in Testa Oblast, #Ukraine, as Russia's drones "
                                  "hit energy infrastructure", tags=["Ukraine"]))
        self.assertEqual(p["countries"], ["UA"])
        self.assertEqual(p["country_basis"], {"UA": "hashtag"})
        self.assertIn("Testa Oblast", p["regions"])
        self.assertIn("strike", p["causes"])

    def test_names_count_when_no_hashtag_and_adjectives_never(self):
        p = netblocks.parse(entry("Confirmed: Metrics show a decline in connectivity in occupied Crimea"))
        self.assertEqual(p["countries"], ["UA"])
        p = netblocks.parse(entry("Confirmed: Metrics show a collapse in the networks of a Russian tech firm"))
        self.assertEqual(p["countries"], [])

    def test_possessive_hashtag_and_joined_names(self):
        p = netblocks.parse(entry("Update: #Iran's blackout continues; #ElSalvador and #SriLanka unaffected",
                                  tags=["Iran's", "ElSalvador", "SriLanka"]))
        self.assertEqual(p["countries"], ["IR", "SV", "LK"])

    def test_service_note_is_a_platform_post_with_no_cause_from_the_disclaimer(self):
        p = netblocks.parse(entry("Note: Testapp and Telegram are having international outages; incident not related "
                                  "to country-level internet restrictions or filtering"))
        self.assertEqual(p["type"], "Note")
        self.assertEqual(p["scope"], "platform")
        self.assertIn("Telegram", p["platforms"])
        self.assertNotIn("restriction", p["causes"])

    def test_network_numbers_and_threads(self):
        p = netblocks.parse(entry("Update: service restored on a satellite platform (AS14593) after an outage",
                                  reply=True))
        self.assertEqual(p["asns"], [14593])
        self.assertTrue(p["in_thread"])
        self.assertEqual(p["thread_root"], "at://x/app.bsky.feed.post/root")

    def test_reposts_and_other_authors_are_not_netblocks_posts(self):
        e = entry("Confirmed: text")
        self.assertIsNone(netblocks.parse({**e, "reason": {"$type": "app.bsky.feed.defs#reasonRepost"}}))
        self.assertIsNone(netblocks.parse(entry("Confirmed: text", handle="someone.else")))

    def test_excerpt_is_short_and_marked(self):
        p = netblocks.parse(entry("Confirmed: " + "word " * 60))
        self.assertLessEqual(len(p["excerpt"]), netblocks.EXCERPT_CHARS + 2)
        self.assertTrue(p["excerpt"].endswith("…"))
        self.assertFalse(p["excerpt"].startswith("Confirmed"))


class TestFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.posts = nb_posts()

    def test_whole_account_read(self):
        self.assertEqual(len(MANIFEST["files"]), 11)
        self.assertEqual(len(self.posts), 1027)       # 1,035 entries less 8 reposts of other accounts
        self.assertEqual(min(p["posted_at"] for p in self.posts), "2023-04-21T15:42:29Z")
        self.assertTrue(all(p["url"].startswith(netblocks.PROFILE + "/post/") for p in self.posts))

    def test_the_outages_iodas_country_signal_missed_are_there(self):
        by_id = {p["id"]: p for p in self.posts}
        pk = by_id["3n2kdo6hpbk2z"] if "3n2kdo6hpbk2z" in by_id else next(
            p for p in self.posts if p["posted_at"].startswith("2026-11-18T17:20"))
        self.assertEqual((pk["type"], pk["countries"]), ("Confirmed", ["PK"]))
        ua = next(p for p in self.posts if p["posted_at"].startswith("2026-11-20T11:36"))
        self.assertEqual((ua["type"], ua["countries"]), ("Confirmed", ["UA"]))

    def test_no_full_text_is_kept(self):
        for p in self.posts:
            self.assertNotIn("text", p)
            self.assertLessEqual(len(p["excerpt"]), netblocks.EXCERPT_CHARS + 2)

    def test_a_failed_page_is_a_problem_and_keeps_what_was_read(self):
        calls = []

        def flaky(url):
            calls.append(url)
            if len(calls) == 3:
                raise OSError("boom")
            return replay_get(url)
        posts, problems = netblocks.fetch(getter=flaky)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["endpoint"], "author-feed")
        self.assertTrue(0 < len(posts) < 1027)

    def test_document_keeps_posts_the_account_no_longer_shows(self):
        gone = {**self.posts[0], "id": "deleted1", "posted_at": "2024-01-01T00:00:00Z"}
        doc = netblocks.document(self.posts, [], NOW, kept=[gone])
        self.assertEqual(doc["posts_count"], 1028)
        self.assertFalse(next(p for p in doc["posts"] if p["id"] == "deleted1")["in_account"])
        self.assertTrue(doc["complete"])
        self.assertIn("all rights reserved", doc["terms"])
        self.assertGreater(doc["monthly_connectivity_posts"]["IR"]["2024-01"] if "2024-01" in
                           doc["monthly_connectivity_posts"]["IR"] else 1, 0)

    def test_recent_posts_become_items_and_daily_counts(self):
        rec = netblocks.recent(self.posts, NOW)
        self.assertTrue(all(p["posted_at"] >= "2026-11-17T15:00:00Z" for p in rec))
        days = netblocks.daily(self.posts, "2026-11-18", "2026-11-20")
        self.assertEqual(days["PK"]["2026-11-18"]["netblocks_posts"], 1)
        self.assertEqual(days["UA"]["2026-11-20"]["netblocks_by_type"], {"Confirmed": 1})



class TestFeed(unittest.TestCase):
    """NetBlocks' recent posts as feed items, and the corroboration line on country outages."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_blackout import blackout_cli, lint_errors  # noqa: E402
        from blackout import feed
        cls.feed, cls.lint_errors = feed, staticmethod(lint_errors)
        doc = netblocks.document(nb_posts(), [], NOW)
        snap = blackout_cli.build_snapshot()
        snap["netblocks"] = netblocks.snapshot_part(doc, NOW)
        cls.snap = snap
        cls.fc = feed.build_feed(snap)

    def items(self):
        return [f["properties"] for f in self.fc["features"] if f["properties"]["id"].startswith("blackout:netblocks:")]

    def test_recent_posts_are_reported_items_quoting_netblocks(self):
        items = self.items()
        self.assertEqual(len(items), len(self.snap["netblocks"]["recent"]))
        self.assertTrue(items)
        for p in items:
            self.assertEqual((p["status_class"], p["direction"], p["indicator_id"]), ("reported", "neutral", "BO-IND-20"))
            self.assertIn("Reported by NetBlocks; Blackout quotes it and does not assess it.", p["summary"])
            self.assertEqual(p["sources"][0]["id"], "netblocks-bluesky")
            self.assertTrue(p["sources"][0]["url"].startswith(netblocks.PROFILE + "/post/"))
            self.assertEqual(p["subject_id"], p["id"])
        pk = next(p for p in items if p["countries"] == ["PK"])
        # The fixture's text is cut before NetBlocks names the cable, so the cause mapping is checked on its own.
        post = netblocks.parse(entry("Confirmed: Network data show slow service in #Testland consistent with a fault on "
                                     "a subsea cable system", tags=["Pakistan"], created="2026-11-20T12:00:00.000Z"))
        snap = {**self.snap, "netblocks": {"recent": [post], "last_30d": [post]}}
        item = self.feed._netblocks_features(snap, "2026-11-20T15:00:00Z", "2026-11-21T03:00:00Z", "actual")[0]["properties"]
        self.assertEqual((item["warning_problem_id"], item["hypothesis_id"], item["requirement_ids"]),
                         ("BO-WP-2", "BO-H-2A", ["BO-PIR-2"]))
        self.assertIn("Other measurers in this country within 24 h", pk["summary"])
        self.assertEqual(self.lint_errors(self.fc), {})

    def test_health_lists_the_lane(self):
        self.assertEqual(self.fc["health"]["netblocks-bluesky"]["state"], "fresh")
        self.assertIn("NetBlocks", self.fc["attribution"])

    def test_country_outages_name_the_other_measurers(self):
        outages = [f["properties"] for f in self.fc["features"] if f["properties"]["id"].startswith("blackout:outage:")]
        self.assertTrue(outages)
        for p in outages:
            self.assertIn("Other measurers in this country within 24 h:", p["summary"])
            self.assertIn("NetBlocks", p["summary"])

    def test_corroboration_counts_within_the_window(self):
        snap = {"outages": [{"indicator_id": "BO-IND-1", "country": "PK", "start_time": "2026-11-18T16:00:00Z"}],
                "radar": {"annotations": [{"countries": ["PK"], "start": "2026-11-18T15:00:00Z"},
                                          {"countries": ["PK"], "start": "2026-11-10T15:00:00Z"}], "anomalies": []},
                "netblocks": {"last_30d": [{"countries": ["PK"], "posted_at": "2026-11-18T17:20:47Z"}]}}
        c = self.feed.corroboration(snap, "PK", "2026-11-18T17:00:00Z")
        self.assertEqual((c["ioda"], c["radar_outages"], c["radar_anomalies"], c["netblocks"]), (1, 1, 0, 1))
        c = self.feed.corroboration({"outages": []}, "PK", "2026-11-18T17:00:00Z")
        self.assertIsNone(c["radar_outages"])
        self.assertIn("Cloudflare Radar not read", self.feed.corroboration_text(c, "ioda"))


if __name__ == "__main__":
    unittest.main()
