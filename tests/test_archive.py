"""Retention archives (18 Nov 2026): what the live view drops lands in history/ and is never rewritten or lost.

Real recorded responses (ODIN county view, Bluesky counts, GDELT GKG files) go through the lanes, then through
blackout/archive.py into a temporary history/ folder."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blackout import archive, blackmarble, gdelt, odin, radar  # noqa: E402
from test_blackout import blackout_cli  # noqa: E402
from test_lanes import ODIN, ODIN_NOW, manifest_fetch  # noqa: E402
from test_public import bsky, gdelt_run  # noqa: E402


def night(date, mean=5.0, clear=0.9):
    return {"date": date, "mean": mean, "clear": clear, "pixels": 100, "product": "VJ146A2"}


def dates(n, start=1):
    return [f"2026-10-{d:02d}" for d in range(start, start + n)]


class Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.hist = Path(self._t.name) / "history"


class TestNights(Tmp):
    def snapshot(self, per_city):
        return {"nightlights": {"cities": [{"id": cid, "nights": ns} for cid, ns in per_city.items()]}}

    def test_nights_the_30_night_window_drops_are_kept(self):
        all_nights = [night(d) for d in dates(31)]
        self.assertEqual(blackmarble.KEEP_NIGHTS, 30)  # the live window is unchanged
        # Day 1: the snapshot holds nights 1-30; day 2: night 31 arrives and night 1 leaves the snapshot.
        first = {"kyiv": blackmarble.merge(None, {n["date"]: {"kyiv": n} for n in all_nights[:30]})["kyiv"]}
        archive.archive_nights(self.hist, self.snapshot(first))
        later = blackmarble.merge({"kyiv": first["kyiv"]}, {all_nights[30]["date"]: {"kyiv": all_nights[30]}})["kyiv"]
        self.assertEqual(len(later), 30)
        self.assertNotIn(all_nights[0]["date"], [n["date"] for n in later])
        archive.archive_nights(self.hist, self.snapshot({"kyiv": later}))
        rows = archive.read_rows(self.hist / archive.NIGHTS_FILE)
        self.assertEqual(sorted(r["date"] for r in rows), dates(31))
        self.assertTrue(all(r["city"] == "kyiv" and r["mean"] == 5.0 for r in rows))

    def test_a_second_run_adds_nothing_and_never_rewrites_lines(self):
        snap = self.snapshot({"kyiv": [night(d) for d in dates(3)], "lviv": [night(d) for d in dates(3)]})
        self.assertEqual(archive.archive_nights(self.hist, snap), 6)
        before = (self.hist / archive.NIGHTS_FILE).read_bytes()
        # a re-read night with a different value: the first reading stays, once per city and night
        snap["nightlights"]["cities"][0]["nights"][0] = night("2026-10-01", mean=99.0)
        self.assertEqual(archive.archive_nights(self.hist, snap), 0)
        self.assertEqual((self.hist / archive.NIGHTS_FILE).read_bytes(), before)


class TestOdinArchive(Tmp):
    def reading(self, now=ODIN_NOW):
        o, _ = odin.fetch_odin(fetch=manifest_fetch(ODIN), now=radar._t(now))
        o["read_at"] = now  # the run's own time, so successive readings differ
        return {"generated_at": now, "odin": o}

    def test_every_county_row_is_written_once_per_run(self):
        snap = self.reading()
        n = archive.archive_odin(self.hist, snap)
        self.assertEqual(n, 1 + 214)
        rows = archive.read_rows(self.hist / "odin" / "2026-11-13.jsonl")
        self.assertEqual([r["kind"] for r in rows].count("county"), 214)
        run = rows[0]
        self.assertEqual((run["kind"], run["meters"], run["counties"], run["coverage"]["utilities"]),
                         ("run", 39663, 214, 159))
        la = next(r for r in rows if r.get("fips") == "06037")
        self.assertEqual((la["meters"], la["state"], la["utilities"]),
                         (11231, "CA", [["SOUTHERN CALIFORNIA EDISON CO", "17609"]]))
        self.assertEqual(archive.archive_odin(self.hist, snap), 0)  # the same reading again: nothing
        self.assertEqual(len(archive.read_rows(self.hist / "odin" / "2026-11-13.jsonl")), 215)

    def test_closed_days_are_gzipped_and_not_rewritten(self):
        archive.archive_odin(self.hist, self.reading())
        archive.archive_odin(self.hist, self.reading("2026-11-13T20:40:05Z"))
        self.assertEqual(len(archive.read_rows(self.hist / "odin" / "2026-11-13.jsonl")), 430)  # two runs, open day
        archive.archive_odin(self.hist, self.reading("2026-11-14T00:40:05Z"))
        gz = self.hist / "odin" / "2026-11-13.jsonl.gz"
        self.assertTrue(gz.exists())
        self.assertFalse((self.hist / "odin" / "2026-11-13.jsonl").exists())
        self.assertEqual(len(archive.read_rows(gz)), 430)
        closed = gz.read_bytes()
        archive.archive_odin(self.hist, self.reading("2026-11-14T04:40:05Z"))
        self.assertEqual(gz.read_bytes(), closed)  # a closed day is never touched by later runs
        self.assertEqual(len(archive.read_rows(self.hist / "odin" / "2026-11-14.jsonl")), 430)

    def test_a_run_with_no_counties_is_still_recorded(self):
        o, _ = odin.fetch_odin(fetch=lambda url: {"outage": []} if "county" in url else [], now=radar._t(ODIN_NOW))
        archive.archive_odin(self.hist, {"generated_at": ODIN_NOW, "odin": o})
        rows = archive.read_rows(self.hist / "odin" / "2026-11-13.jsonl")
        self.assertEqual([(r["kind"], r["counties"]) for r in rows], [("run", 0)])

    def test_size_per_day(self):
        archive.archive_odin(self.hist, self.reading())
        archive.archive_odin(self.hist, self.reading("2026-11-14T00:40:05Z"))
        size = (self.hist / "odin" / "2026-11-13.jsonl.gz").stat().st_size
        print(f"\n[size] ODIN, one run of 214 counties: {size} bytes gzipped")
        self.assertLess(size, 20_000)  # the workflow runs up to 6 times a day


class TestBlueskyArchive(Tmp):
    def test_every_name_every_run_surge_or_not(self):
        snap, _ = bsky("2026-11-16T21:45:00Z", ["chase", "delta", "marriott"])
        n = archive.archive_bluesky(self.hist, {"bluesky": snap})
        rows = archive.read_rows(self.hist / archive.COUNTS_FILE)
        self.assertEqual(n, 3)
        self.assertEqual({r["key"] for r in rows}, {"chase", "delta", "marriott"})
        self.assertEqual({r["run_at"] for r in rows}, {"2026-11-16T21:45:00Z"})
        chase = next(r for r in rows if r["key"] == "chase")
        self.assertEqual(chase["window_n"], 703)
        self.assertEqual(chase["baseline_n"], next(x for x in snap["rows"] if x["key"] == "chase")["baseline_matches"])
        self.assertEqual(set(chase), {"run_at", "name", "key", "window_n", "baseline_n"})  # counts only
        self.assertEqual(archive.archive_bluesky(self.hist, {"bluesky": snap}), 0)  # same run again
        snap2, _ = bsky("2026-11-17T01:45:00Z", ["chase"])
        archive.archive_bluesky(self.hist, {"bluesky": snap2})
        self.assertEqual(len(archive.read_rows(self.hist / archive.COUNTS_FILE)), 4)  # appended, earlier lines kept


class TestGdeltArchive(Tmp):
    def test_the_files_read_are_archived_and_the_snapshot_is_unchanged(self):
        snap, _ = gdelt_run("banks-united")
        new = snap[gdelt.NEW_KEY]
        self.assertEqual(len(new["files"]), snap["files_read_now"])
        # the page's snapshot carries neither the carry nor the new files
        pub = blackout_cli.build_snapshot({"mode": "live", "generated_at": "2026-11-17T01:45:00Z", "raw_outages": [],
                                           "raw_ooni": [], "raw_tips": [], "gdelt": snap})["gdelt"]
        self.assertNotIn(gdelt.NEW_KEY, pub)
        self.assertNotIn(gdelt.CARRY_KEY, pub)
        carry = snap[gdelt.CARRY_KEY]
        wrote = archive.archive_gdelt(self.hist, new["files"], carry["files"], carry["missing"])
        self.assertGreater(wrote, 0)
        rows = [r for p in sorted((self.hist / "gdelt-matches").glob("*.jsonl.gz")) for r in archive.read_rows(p)]
        stamps = [r["file"] for r in rows]
        self.assertEqual(len(stamps), len(set(stamps)))
        for s, v in new["files"].items():
            self.assertEqual(next(r for r in rows if r["file"] == s)["matches"], v)  # full reduction, links included
        # a second archive of the same data adds nothing and leaves every byte alone
        before = {p.name: p.read_bytes() for p in (self.hist / "gdelt-matches").glob("*.gz")}
        self.assertEqual(archive.archive_gdelt(self.hist, new["files"], carry["files"], carry["missing"]), 0)
        self.assertEqual({p.name: p.read_bytes() for p in (self.hist / "gdelt-matches").glob("*.gz")}, before)

    def test_a_later_run_appends_a_member_and_keeps_the_earlier_lines(self):
        archive.archive_gdelt(self.hist, {"20261113194500": {"chase": [["a.com", "https://a.com/1"]]}}, None)
        p = self.hist / "gdelt-matches" / "2026-11.jsonl.gz"
        first = p.read_bytes()
        archive.archive_gdelt(self.hist, {"20261113200000": {}}, None, ["20261113201500"])
        self.assertTrue(p.read_bytes().startswith(first))
        self.assertEqual([r["file"] for r in archive.read_rows(p)],
                         ["20261113194500", "20261113200000", "20261113201500"])
        self.assertEqual(archive.read_rows(p)[2], {"file": "20261113201500", "missing": True})
        archive.archive_gdelt(self.hist, {"20261201000000": {}}, None)  # a new month is its own file
        self.assertTrue((self.hist / "gdelt-matches" / "2026-12.jsonl.gz").exists())
        self.assertEqual(len(archive.read_rows(p)), 3)

    def test_size_of_real_reductions(self):
        snap, _ = gdelt_run("banks-united")
        files = snap[gdelt.CARRY_KEY]["files"]
        archive.archive_gdelt(self.hist, files, None)
        p = next((self.hist / "gdelt-matches").glob("*.jsonl.gz"))
        per_file = p.stat().st_size / max(1, len(files))
        print(f"\n[size] GDELT, {len(files)} files, {p.stat().st_size} bytes gzipped, {per_file:.0f} bytes a file")
        self.assertLess(per_file * 96, 2_000_000)  # a day (96 files)


class TestArchiveCommand(Tmp):
    def write_snapshot(self, root, doc):
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / "data" / "snapshot.json").write_text(json.dumps(doc), encoding="utf-8")

    def test_archive_all_from_data_files_and_a_seed_build_writes_nothing(self):
        root = Path(self._t.name) / "repo"
        snap, _ = bsky("2026-11-16T21:45:00Z", ["chase"])
        self.write_snapshot(root, {"mode": "seed", "bluesky": snap})
        self.assertEqual(blackout_cli.archive_all(root), [])
        self.assertFalse((root / "history").exists())
        self.write_snapshot(root, {"mode": "live", "generated_at": "2026-11-16T21:50:00Z", "bluesky": snap})
        g, _ = gdelt_run("banks-united")
        (root / "data" / blackout_cli.NEW_GDELT_NAME).write_text(json.dumps(g[gdelt.NEW_KEY]), encoding="utf-8")
        (root / "data" / gdelt.CARRY_NAME).write_text(json.dumps(g[gdelt.CARRY_KEY]), encoding="utf-8")
        self.assertEqual(blackout_cli.archive_all(root), [])
        self.assertEqual(len(archive.read_rows(root / "history" / archive.COUNTS_FILE)), 1)
        self.assertTrue(list((root / "history" / "gdelt-matches").glob("*.jsonl.gz")))

    def test_one_failing_archive_is_reported_and_the_others_still_run(self):
        root = Path(self._t.name) / "repo"
        snap, _ = bsky("2026-11-16T21:45:00Z", ["chase"])
        self.write_snapshot(root, {"mode": "live", "generated_at": "2026-11-16T21:50:00Z", "bluesky": snap,
                                   "nightlights": {"cities": [{"id": "kyiv", "nights": [night("2026-10-01")]}]}})
        (root / "history").mkdir()
        (root / "history" / archive.NIGHTS_FILE).write_text("not json\n", encoding="utf-8")
        errs = blackout_cli.archive_all(root)
        self.assertEqual(len(errs), 1)
        self.assertIn("Black Marble nights", errs[0])
        self.assertTrue((root / "history" / archive.COUNTS_FILE).exists())


class TestNetblocksPrivate(Tmp):
    """NetBlocks' excerpts (24 Nov 2026): recent posts only in public; every post with its excerpt in the private
    archive's file only. Posts come from the recorded account (tests/fixtures/netblocks)."""

    @classmethod
    def setUpClass(cls):
        from test_netblocks import NOW, nb_posts
        from blackout import netblocks
        cls.nb, cls.now = netblocks, NOW
        cls.posts = sorted(nb_posts(), key=lambda p: p["posted_at"])
        cls.doc = netblocks.document(cls.posts, [], NOW)

    def test_public_document_quotes_recent_posts_only(self):
        cut = "2026-11-17T15:00:00Z"   # NOW less 72 h
        with_x = [p for p in self.doc["posts"] if "excerpt" in p]
        self.assertTrue(with_x)
        self.assertTrue(all(p["posted_at"] >= cut for p in with_x))
        self.assertTrue(all("excerpt" in p for p in self.doc["posts"] if p["posted_at"] >= cut))
        self.assertLess(len(with_x), 40)
        self.assertEqual(self.doc["posts_count"], len(self.posts))
        # the feed's items and the page read `recent`, which still carries the words quoted
        part = self.nb.snapshot_part(self.doc, self.now)
        self.assertTrue(part["recent"] and all(p["excerpt"] for p in part["recent"]))

    def test_public_index_has_every_post_and_no_excerpt(self):
        self.assertEqual(archive.archive_netblocks(self.hist, self.doc), len(self.posts))
        rows = archive.read_rows(self.hist / archive.NETBLOCKS_INDEX)
        self.assertEqual(sorted(r["id"] for r in rows), sorted(p["id"] for p in self.posts))
        self.assertEqual([r["posted_at"] for r in rows], sorted(r["posted_at"] for r in rows))
        self.assertTrue(all("excerpt" not in r and "in_account" not in r and r["url"] for r in rows))
        self.assertNotIn("excerpt", (self.hist / archive.NETBLOCKS_INDEX).read_text(encoding="utf-8"))
        self.assertEqual(archive.archive_netblocks(self.hist, self.doc), 0)
        self.assertFalse((self.hist / archive.NETBLOCKS_FILE).exists())

    def test_deleted_posts_stay_counted_from_the_index(self):
        archive.archive_netblocks(self.hist, self.doc)
        kept = archive.netblocks_kept(self.hist)
        doc = self.nb.document(self.posts[1:], [], self.now, kept=kept)
        gone = next(p for p in doc["posts"] if p["id"] == self.posts[0]["id"])
        self.assertFalse(gone["in_account"])
        self.assertEqual(gone["countries"], self.posts[0]["countries"])
        self.assertEqual(doc["monthly_connectivity_posts"], self.doc["monthly_connectivity_posts"])

    def test_private_file_keeps_the_old_format_and_is_append_only(self):
        private = Path(self._t.name) / "private"
        n = archive.netblocks_full(private, self.posts[:-3])
        self.assertEqual(n, {"legacy": 0, "posts": len(self.posts) - 3})
        before = (private / archive.NETBLOCKS_FILE).read_bytes()
        n = archive.netblocks_full(private, self.posts)
        self.assertEqual(n, {"legacy": 0, "posts": 3})
        after = (private / archive.NETBLOCKS_FILE).read_bytes()
        self.assertTrue(after.startswith(before))
        rows = archive.read_rows(private / archive.NETBLOCKS_FILE)
        self.assertEqual(rows, self.posts)
        self.assertTrue(all(r["excerpt"] for r in rows))
        self.assertEqual(archive.netblocks_full(private, None), {"legacy": 0, "posts": 0})

    def test_old_public_file_folded_in_then_removed_only_when_all_of_it_is_held(self):
        legacy = self.hist / archive.NETBLOCKS_FILE
        gone = {**self.posts[0], "id": "deleted1"}       # a post NetBlocks deleted: only the old file has its excerpt
        archive.append_jsonl(legacy, [gone] + self.posts[:50])
        private = Path(self._t.name) / "private"
        archive.append_jsonl(private / archive.NETBLOCKS_FILE, self.posts[:40])    # the copy seeded earlier
        with self.assertRaises(RuntimeError):
            archive.retire_netblocks_legacy(private, legacy)
        self.assertTrue(legacy.exists())
        n = archive.netblocks_full(private, self.posts, legacy)
        self.assertEqual(n, {"legacy": 11, "posts": len(self.posts) - 50})
        self.assertIn(gone, archive.read_rows(private / archive.NETBLOCKS_FILE))
        # the public index and the next read's kept list take the old file's posts too, without the excerpt
        self.assertIn("deleted1", {r["id"] for r in archive.netblocks_kept(self.hist)})
        archive.archive_netblocks(self.hist, self.doc)
        idx = archive.read_rows(self.hist / archive.NETBLOCKS_INDEX)
        self.assertEqual(len(idx), len(self.posts) + 1)
        self.assertTrue(all("excerpt" not in r for r in idx))
        self.assertEqual(archive.retire_netblocks_legacy(private, legacy), 51)
        self.assertFalse(legacy.exists())
        self.assertEqual(archive.retire_netblocks_legacy(private, legacy), 0)

    def test_private_folder_is_never_a_published_one(self):
        root = Path(self._t.name)
        for bad in ("history/x", "site/data", "site_src"):
            self.assertEqual(blackout_cli.private_netblocks("retire-legacy", root / bad, root), 1)
        self.assertEqual(blackout_cli.private_netblocks("retire-legacy", root / "data" / "private" / "netblocks", root), 0)

    def test_committed_history_has_no_excerpt(self):
        rows = archive.read_rows(ROOT / "history" / archive.NETBLOCKS_INDEX)
        self.assertGreater(len(rows), 1000)
        self.assertTrue(all("excerpt" not in r for r in rows))

    def test_build_never_publishes_the_private_copy(self):
        src = (ROOT / "tools" / "blackout.py").read_text(encoding="utf-8")
        build = src[src.index("def build_site("):src.index("def private_netblocks(")]
        self.assertNotIn("FULL_NAME", build)
        self.assertNotIn("netblocks-full", build)
        self.assertIn("/data/netblocks-full.json", (ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_workflow_uploads_before_it_removes_and_fails_loudly(self):
        wf = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        step = wf[wf.index("- name: Archive what the live view drops"):wf.index("- name: Commit history/")]
        order = ["blackout.py archive ", "tic_archive.py restore --program blackout --lane netblocks",
                 "blackout.py archive-full", "tic_archive.py put --program blackout --lane netblocks",
                 "blackout.py retire-legacy", "exit $rc"]
        at = [step.index(x) for x in order]
        self.assertEqual(at, sorted(at))
        self.assertIn("GH_TOKEN: ${{ secrets.TIC_ARCHIVE_TOKEN }}", step)
        self.assertNotIn("github.token", step)
        self.assertNotIn("|| true", step)
        self.assertLess(wf.index("- name: Deploy site/ to Cloudflare Pages"), wf.index("- name: Archive what the live view drops"))
        # The excerpts go up only from the private folder; history/ has a lane of its own (public files only).
        self.assertIn("--lane netblocks --src data/private/netblocks", step)
        self.assertNotIn("--lane netblocks --src history", wf)


if __name__ == "__main__":
    unittest.main()
