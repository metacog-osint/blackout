"""Private archive lanes: every file a run of update.yml leaves outside the tracked code (data/, history/, site/)
lands in the private archive (tools/tic_archive.py, metacog-osint/tic-archive) and restores byte for byte. A file
that a later run removes or replaces stays restorable from the snapshot that held it.

The test makes a live collection offline in a temporary folder: the lanes that write data/ files of their own
(EIA-930, Bluesky, GDELT, NetBlocks, the daily series) replay their recorded responses, every other lane is down,
and nothing is read from the live site. It then builds the site, runs the kit's history log as the workflow calls
it, the retention archives and NetBlocks' private copy. Then it runs the workflow's own tic_archive.py commands,
read from update.yml, against a local DirStore. Nothing reaches GitHub: GhStore is replaced by the DirStore and any
subprocess call during an archive command fails the test."""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.append(str(ROOT / "tools"))   # after ROOT: tools/blackout.py must not shadow the package
import tic_archive as ta  # noqa: E402
from blackout import (asn_watch, bgp, blackmarble, citizenlab, eia, faa, iaea, ioda, odin, ooni, radar,  # noqa: E402
                      satcom, status_feeds, stop)
from test_blackout import blackout_cli  # noqa: E402
from test_lanes import QUIET_NOW, eia_fetch  # noqa: E402
from test_netblocks import NOW as NB_NOW, nb_posts  # noqa: E402
from test_public import bsky, gdelt_run  # noqa: E402
import test_trends  # noqa: E402

WF = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
PROGRAM = "blackout"
RUN_OUTPUTS = ("data", "history", "site")          # what a run writes beside the tracked code
RUN_INPUTS = ("doctrine", "site_src")              # tracked code the offline run is given
LANES = {"netblocks", "state", "site", "history"}
EARLY = "Private archive (run snapshot and site, to tic-archive)"
LAST = "Private archive (history, to tic-archive)"
FIRST, SECOND = "20261127T060000Z", "20261127T100000Z"


def steps() -> dict[str, str]:
    """The workflow's steps by name (unnamed `uses:` steps by their action)."""
    out = {}
    for chunk in re.split(r"(?m)^      - ", WF.split("\n    steps:\n", 1)[1])[1:]:
        m = re.match(r"name: (.+)|uses: (\S+)", chunk)
        out[(m.group(1) or m.group(2)).strip()] = chunk
    return out


STEPS = steps()


def step(prefix: str) -> str:
    (name,) = [n for n in STEPS if n.startswith(prefix)]
    return STEPS[name]


def archive_calls(text: str, cmd: str) -> list[list[str]]:
    """Every `python tools/tic_archive.py <cmd> ...` in a step, as argv after the script name."""
    flat = text.replace("\\\n", " ")
    return [[m.group(1), *shlex.split(m.group(2))]
            for m in re.finditer(r"python tools/tic_archive\.py (" + cmd + r")\b([^;|&\n]*)", flat)]


def arg(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, t in enumerate(argv) if t == flag]


def put_calls() -> list[list[str]]:
    """Every put the workflow makes, in any step."""
    return [c for text in STEPS.values() for c in archive_calls(text, "put")]


def tree(root: Path) -> dict[str, str]:
    """{posix path relative to root: sha256} for every file."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def run_archive(argv: list[str], cwd: Path, store: Path, stamp: str = FIRST) -> int:
    """One tic_archive.py command as the workflow gives it, in cwd, against a DirStore at store."""
    def no_subprocess(*a, **k):
        raise AssertionError("an archive command tried to run a subprocess (gh): nothing may leave the test")

    old = os.getcwd()
    os.chdir(cwd)
    try:
        env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(ta, "GhStore", lambda *a, **k: ta.DirStore(store)), \
                mock.patch.object(ta, "utc_stamp", lambda now=None: stamp), \
                mock.patch.object(subprocess, "run", no_subprocess):
            return ta.main(argv)
    finally:
        os.chdir(old)


def offline_collect(tmp: Path) -> None:
    """`collect --live` with the recorded lanes replayed and every other lane down; nothing read from the site."""
    down = [(ioda, "fetch_ioda_live"), (ooni, "fetch_ooni_live"), (asn_watch, "fetch_asn_watch"),
            (status_feeds, "fetch_status_feeds"), (radar, "fetch_radar"), (bgp, "fetch_bgp"), (iaea, "fetch_znpp"),
            (stop, "fetch_stop"), (odin, "fetch_odin"), (blackmarble, "fetch_blackmarble"),
            (citizenlab, "fetch_testlists"), (faa, "fetch_faa"), (satcom, "fetch_traffic"),
            (blackout_cli.doctrine_tips, "gather")]
    real_eia = eia.fetch_eia
    posts = nb_posts()                                   # each replayed before the lanes are patched below
    trends_doc = test_trends.replay()
    bluesky_part = bsky("2026-11-16T21:45:00Z", ["chase"])
    gdelt_part = gdelt_run("banks-united")
    class AtFixtureTime(datetime):        # the run at the NetBlocks recording's time, so its recent posts are items
        @classmethod
        def now(cls, tz=None):
            return NB_NOW

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(blackout_cli, "ROOT", tmp))
        stack.enter_context(mock.patch.object(blackout_cli, "datetime", AtFixtureTime))
        for mod, name in down:
            stack.enter_context(mock.patch.object(mod, name, side_effect=OSError(f"{name} down in tests")))
        stack.enter_context(mock.patch.object(blackout_cli, "previous_health", return_value={}))
        for name in ("previous_nights", "previous_eia_hours", "previous_gdelt_counts", "previous_trends",
                     "previous_netblocks"):
            stack.enter_context(mock.patch.object(blackout_cli, name, return_value=None))
        stack.enter_context(mock.patch.object(
            eia, "fetch_eia", lambda carry=None: real_eia(fetch=eia_fetch(), now=radar._t(QUIET_NOW), carry=carry)))
        stack.enter_context(mock.patch.object(blackout_cli.bluesky, "fetch_bluesky",
                                              lambda now: bluesky_part))
        stack.enter_context(mock.patch.object(blackout_cli.gdelt, "fetch_gdelt", lambda now, carry=None: gdelt_part))
        stack.enter_context(mock.patch.object(blackout_cli.netblocks, "fetch", lambda: (posts, [])))
        stack.enter_context(mock.patch.object(blackout_cli.trends, "fetch", lambda now, **kw: trends_doc))
        stack.enter_context(mock.patch("urllib.request.urlopen",
                                       side_effect=AssertionError("the offline run read the network")))
        stack.enter_context(mock.patch("sys.stderr"))
        stack.enter_context(mock.patch("sys.stdout"))
        snap = blackout_cli.collect(live=True)
    read = [p for p in snap["collection"]["problems"] if "read the network" in str(p)]
    if read:
        raise AssertionError(f"the offline run read the network: {read}")


class RunTree:
    """A live run made offline, once: collect, build, the kit's history log, the retention archives and NetBlocks'
    private copy, each as update.yml calls it."""
    tmp: Path | None = None

    @classmethod
    def make(cls) -> Path:
        if cls.tmp is not None:
            return cls.tmp
        tmp = Path(tempfile.mkdtemp(prefix="blackout-lanes-"))
        for d in RUN_INPUTS:
            shutil.copytree(ROOT / d, tmp / d)
        offline_collect(tmp)
        blackout_cli.build_site(tmp)
        (kit,) = re.findall(r"python (doctrine/kit/tic\.py history [^|;\n]*)", step("History").replace("\\\n", " "))
        r = subprocess.run([sys.executable, *shlex.split(kit)], cwd=tmp, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"kit history failed: {r.stderr[-400:]}")
        with mock.patch("sys.stdout"):
            errs = blackout_cli.archive_all(tmp)                                          # blackout.py archive
            if errs:
                raise RuntimeError(f"retention archives failed: {errs}")
            (call,) = [c for c in archive_calls(step("Archive what the live view drops"), "put")]
            private = tmp / arg(call, "--src")[0]
            private.mkdir(parents=True)
            if blackout_cli.private_netblocks("archive-full", private, tmp) != 0:        # blackout.py archive-full
                raise RuntimeError("archive-full failed")
        cls.tmp = tmp
        return tmp


def tearDownModule():
    if RunTree.tmp is not None:
        shutil.rmtree(RunTree.tmp, ignore_errors=True)


class TestWorkflowLanes(unittest.TestCase):
    def test_lanes_and_their_folders(self):
        lanes = {arg(c, "--lane")[0]: c for c in put_calls()}
        self.assertEqual(set(lanes), LANES)
        self.assertEqual(len(put_calls()), len(LANES))
        for c in lanes.values():
            self.assertEqual(arg(c, "--program"), [PROGRAM])
            self.assertEqual(arg(c, "--include"), [])         # whole folders: a new file is never left out
        self.assertEqual(arg(lanes["netblocks"], "--src"), ["data/private/netblocks"])
        self.assertEqual(arg(lanes["state"], "--src"), ["data"])
        self.assertEqual(arg(lanes["state"], "--exclude"), ["private/*"])   # lane netblocks holds it
        self.assertEqual(arg(lanes["site"], "--src"), ["site"])
        self.assertEqual(arg(lanes["history"], "--src"), ["history"])
        for lane in ("state", "site", "history"):              # archived as they are: they match by hash
            self.assertFalse([t for t in lanes[lane] if t.startswith("--redact")], lane)
            self.assertFalse([t for t in lanes[lane] if t == "--min-interval-hours"], lane)

    def test_lane_names_never_extend_one_another(self):
        # tic_archive.py finds a lane's releases by the prefix blackout-<lane>-, so "state" and "state-x" would mix.
        for a in LANES:
            for b in LANES - {a}:
                self.assertFalse(b.startswith(a + "-"), (a, b))

    def test_where_the_new_steps_run(self):
        names = list(STEPS)
        deploy, early, last = (names.index("Deploy site/ to Cloudflare Pages"), names.index(EARLY),
                               names.index(LAST))
        self.assertEqual(early, deploy + 1)                     # before any later gh or git call can hang
        self.assertEqual(last, len(names) - 1)                  # after the retention archives and the commit
        self.assertGreater(last, names.index("Commit history/ (after the deploy, so the log never runs ahead of "
                                             "the published feed)"))
        self.assertGreater(last, names.index("Archive what the live view drops (history/blackmarble-nights, odin, "
                                             "public-counts, gdelt-matches)"))

    def test_loud_on_failure_and_only_the_archive_token(self):
        for name, exits, tries in ((EARLY, 2, 2), (LAST, 1, 0)):
            text = STEPS[name]
            self.assertIn("if: ${{ !cancelled() }}", text)
            self.assertIn("GH_TOKEN: ${{ secrets.TIC_ARCHIVE_TOKEN }}", text)
            self.assertEqual(re.findall(r"secrets\.(\w+)", text), ["TIC_ARCHIVE_TOKEN"])
            self.assertNotIn("github.token", text)
            self.assertNotIn("|| true", text)
            self.assertNotIn("continue-on-error", text)
            self.assertIn('[ -z "$GH_TOKEN" ]', text)
            self.assertEqual(text.count("exit 1"), exits)
            self.assertEqual(text.count("|| failed=1"), tries)   # each lane tried even if the other failed

    def test_each_lane_only_after_the_step_that_writes_it(self):
        early, last = STEPS[EARLY], STEPS[LAST]
        self.assertIn("COLLECTED: ${{ steps.collect.outcome }}", early)
        # The deploy step also succeeds when it skips itself, so the site lane waits for its output, not its outcome.
        self.assertIn("DEPLOYED: ${{ steps.deploy.outputs.deployed }}", early)
        self.assertIn("HISTORY: ${{ steps.history.outcome }}", last)
        self.assertIn("id: collect", step("Live collection"))
        self.assertIn("id: history", step("History"))
        self.assertIn("id: deploy", step("Deploy site/"))
        for text, var, value, lane in ((early, "COLLECTED", "success", "state"), (early, "DEPLOYED", "true", "site"),
                                       (last, "HISTORY", "success", "history")):
            self.assertRegex(text.replace("\\\n", " "), r'if \[ "\$' + var + r'" = "' + value + r'" \]; then\s+'
                             r'python tools/tic_archive\.py put --program blackout --lane ' + lane + ' ')

    def test_steps_after_the_deploy_are_bounded_in_time(self):
        # A hung gh call fails its own step, so the later archive steps still run, and one hang fits in the job.
        names = list(STEPS)
        after = names[names.index("Deploy site/ to Cloudflare Pages") + 1:]
        limits = {}
        for name in after:
            m = re.search(r"(?m)^        timeout-minutes: (\d+)", STEPS[name])
            if name != "Commit history/ (after the deploy, so the log never runs ahead of the published feed)":
                self.assertTrue(m, name)
            if m:
                limits[name] = int(m.group(1))
        job = int(re.search(r"(?m)^    timeout-minutes: (\d+)", WF).group(1))
        # The longest job of the 40 runs to 27 Nov 2026 07:00Z took 846 s, before these steps; one hang adds at most
        # its step's limit.
        self.assertLessEqual(846 + 60 * max(limits.values()) + 120, 60 * job)

    def test_the_netblocks_lane_is_unchanged(self):
        text = step("Archive what the live view drops")
        self.assertIn("--lane netblocks --src data/private/netblocks", text.replace("\\\n", " "))
        self.assertNotIn("--lane netblocks --src history", WF)


class TestEveryRunFileIsArchived(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_root = RunTree.make()
        cls.work = Path(tempfile.mkdtemp(prefix="blackout-store-"))
        cls.store = cls.work / "store"
        cls.before = {top: tree(cls.run_root / top) for top in RUN_OUTPUTS}
        cls.results = {arg(argv, "--lane")[0]: run_archive(argv, cls.run_root, cls.store, FIRST)
                       for argv in put_calls()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def restore_all(self, dest: Path, snapshots: dict[str, str] | None = None) -> dict[str, str]:
        """Every lane restored, mapped back to run-tree paths: {path: sha256}."""
        got = {}
        for argv in put_calls():
            lane, (src,) = arg(argv, "--lane")[0], arg(argv, "--src")
            cmd = ["restore", "--program", PROGRAM, "--lane", lane, "--dest", str(dest / lane)]
            if snapshots and lane in snapshots:
                cmd += ["--snapshot", snapshots[lane]]
            self.assertEqual(run_archive(cmd, self.run_root, self.store), 0, lane)
            for rel, sha in tree(dest / lane).items():
                path = f"{src}/{rel}"
                self.assertNotIn(path, got, "two lanes hold the same file")
                got[path] = sha
        return got

    def snapshot_names(self, stamp: str) -> dict[str, str]:
        out = {}
        for tag in self.store.iterdir():
            for p in tag.iterdir():
                if p.name.startswith(stamp) and p.name.endswith(".files.json"):
                    out[tag.name[len(PROGRAM) + 1:].rsplit("-", 2)[0]] = p.name
        return out

    def test_the_offline_run_writes_only_the_known_folders(self):
        self.assertEqual({p.name for p in self.run_root.iterdir()}, set(RUN_INPUTS) | set(RUN_OUTPUTS))
        self.assertTrue(self.before["data"] and self.before["history"] and self.before["site"])

    def test_every_put_succeeded(self):
        self.assertEqual(self.results, {lane: 0 for lane in LANES})

    def test_every_file_of_the_run_restores_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            got = self.restore_all(Path(td), self.snapshot_names(FIRST))
        want = {f"{top}/{rel}": sha for top, files in self.before.items() for rel, sha in files.items()}
        self.assertEqual(sorted(set(want) - set(got)), [], "files of the run missing from the archive")
        self.assertEqual(sorted(set(got) - set(want)), [], "the archive holds files the run did not write")
        self.assertEqual({p: s for p, s in got.items() if want[p] != s}, {}, "restored bytes differ")
        # Every kind of file a run writes is in this run, so the check above covers it.
        for p in ("data/snapshot.json", "data/eia-hours.json", "data/gdelt-counts.json", "data/gdelt-new.json",
                  "data/trends.json", "data/netblocks.json", "data/netblocks-full.json",
                  "data/private/netblocks/netblocks-posts.jsonl",
                  "site/data/feed.geojson", "site/data/ended.geojson", "site/data/snapshot.json",
                  "site/data/eia-hours.json", "site/data/trends.json", "site/data/netblocks.json", "site/index.html",
                  "history/state.json", "history/public-counts.jsonl", "history/netblocks-index.jsonl",
                  "history/trends-days.jsonl"):
            self.assertIn(p, got)
        self.assertTrue([p for p in got if p.startswith("history/log/")])
        self.assertTrue([p for p in got if p.startswith("history/gdelt-matches/")])

    def test_removed_or_replaced_files_stay_restorable(self):
        root = self.run_root
        log_day = sorted(p for p in self.before["history"] if p.startswith("log/"))[0]
        old = {p: (root / p).read_bytes() for p in (f"history/{log_day}", "history/state.json",
                                                     "data/snapshot.json", "data/netblocks-full.json",
                                                     "site/data/feed.geojson")}
        try:
            # The next run: a log day no longer in the public tree (a cap), a rewritten state, a new snapshot, a
            # run with no NetBlocks posts read (collect removes the file), a new deploy.
            (root / f"history/{log_day}").unlink()
            (root / "data/netblocks-full.json").unlink()
            for p in ("history/state.json", "site/data/feed.geojson"):
                (root / p).write_bytes(old[p] + b"\n")
            (root / "data/snapshot.json").write_bytes(old["data/snapshot.json"].replace(b"{", b'{"run": 2, ', 1))
            for argv in put_calls():
                if arg(argv, "--lane")[0] in ("history", "state", "site"):
                    self.assertEqual(run_archive(argv, root, self.store, SECOND), 0)
            first, second = self.snapshot_names(FIRST), self.snapshot_names(SECOND)
            self.assertEqual(set(first), LANES)
            self.assertEqual(set(second), {"history", "state", "site"})
            with tempfile.TemporaryDirectory() as td:
                now = self.restore_all(Path(td) / "now")
                then = self.restore_all(Path(td) / "then", first)
            for p, data in old.items():
                self.assertEqual(then[p], hashlib.sha256(data).hexdigest(), p)
                if (root / p).exists():
                    self.assertEqual(now[p], hashlib.sha256((root / p).read_bytes()).hexdigest(), p)
                    self.assertNotEqual(now[p], then[p], p)
                else:
                    self.assertNotIn(p, now)
        finally:
            for p, data in old.items():
                (root / p).parent.mkdir(parents=True, exist_ok=True)
                (root / p).write_bytes(data)


def find_bash() -> str | None:
    """Git's bash on Windows (System32's bash.exe is WSL's), else the bash on PATH, as the runner has."""
    for p in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if p and os.path.exists(p) and "system32" not in p.lower():
            return p
    return None


BASH = find_bash()


def run_script(text: str) -> str:
    """The `run: |` block of a step, as the runner gets it."""
    lines = text.split("run: |\n", 1)[1].split("\n")
    body = []
    for line in lines:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


def step_env(text: str, given: dict) -> dict:
    """The step's env block, its ${{ }} expressions evaluated from given: {"steps.deploy.outputs.deployed": ...,
    "secrets.X": ..., "vars.X": ...}; a term not given is empty."""
    env = {}
    for name, expr in re.findall(r"(?m)^ {10}([A-Z_]+): \$\{\{ (.+?) \}\}", text.split("run: |\n", 1)[0]):
        env[name] = next((given[t] for t in (t.strip() for t in expr.split("||")) if given.get(t)), "")
    return env


@unittest.skipUnless(BASH, "needs bash, as the runner has")
class TestLanesInBash(unittest.TestCase):
    """The deploy step and the archive steps' lane choice run in bash as the runner runs them (bash -e), with
    wrangler and the archive tool replaced by stubs. The deploy step ends `success` when it skips itself (no
    Cloudflare secrets), so the site lane must not take that as a deploy."""

    def bash(self, script: str, env: dict, td: Path, stubs: str) -> int:
        full = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME")}
        full.update(env, CALLS=(td / "calls").as_posix(), GITHUB_OUTPUT=(td / "output").as_posix())
        r = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", stubs + script], env=full,
                           capture_output=True, text=True, timeout=60)
        return r.returncode

    def puts(self, name: str, given: dict, td: Path, python_rc: int = 0) -> tuple[int, list[str]]:
        text = STEPS[name]
        (td / "calls").unlink(missing_ok=True)
        rc = self.bash(run_script(text), step_env(text, given), td,
                       'python() { echo "$*" >> "$CALLS"; return ' + str(python_rc) + '; }\n')
        calls = (td / "calls").read_text().splitlines() if (td / "calls").exists() else []
        return rc, [arg(shlex.split(c), "--lane")[0] for c in calls if c.startswith("tools/tic_archive.py put")]

    def run_case(self, secrets: dict, npx_rc: int, collected: str = "success") -> tuple[int, list[str], list[str]]:
        """(deploy step exit code, wrangler calls, lanes the early archive step put)."""
        deploy = step("Deploy site/")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            stubs = 'npx() { echo "npx $*" >> "$CALLS"; return ' + str(npx_rc) + '; }\nsleep() { :; }\n'
            rc = self.bash(run_script(deploy), step_env(deploy, secrets), td, stubs)
            calls = (td / "calls").read_text().splitlines() if (td / "calls").exists() else []
            out = dict(line.split("=", 1) for line in (td / "output").read_text().splitlines()) \
                if (td / "output").exists() else {}
            given = {"secrets.TIC_ARCHIVE_TOKEN": "token", "steps.collect.outcome": collected}
            given.update({f"steps.deploy.outputs.{k}": v for k, v in out.items()})
            prc, lanes = self.puts(EARLY, given, td)
            self.assertEqual(prc, 0)
            return rc, calls, lanes

    def test_skipped_deploy_is_not_archived_as_deployed(self):
        rc, calls, lanes = self.run_case({}, 0)
        self.assertEqual((rc, calls), (0, []))          # the step skipped itself and still succeeded
        self.assertEqual(lanes, ["state"])

    def test_real_deploy_is_archived(self):
        rc, calls, lanes = self.run_case({"secrets.CLOUDFLARE_PAGES_TOKEN": "t", "vars.CLOUDFLARE_ACCOUNT_ID": "a"}, 0)
        self.assertEqual((rc, len(calls)), (0, 1))
        self.assertEqual(lanes, ["state", "site"])

    def test_failed_deploy_is_not_archived(self):
        rc, calls, lanes = self.run_case({"secrets.CLOUDFLARE_PAGES_TOKEN": "t", "vars.CLOUDFLARE_ACCOUNT_ID": "a"}, 1)
        self.assertNotEqual(rc, 0)
        self.assertEqual(lanes, ["state"])

    def test_no_state_when_the_collection_did_not_finish(self):
        rc, calls, lanes = self.run_case({"secrets.CLOUDFLARE_PAGES_TOKEN": "t", "vars.CLOUDFLARE_ACCOUNT_ID": "a"}, 0,
                                         collected="failure")
        self.assertEqual(lanes, ["site"])

    def test_one_failed_lane_still_tries_the_other_and_turns_the_step_red(self):
        with tempfile.TemporaryDirectory() as td:
            given = {"secrets.TIC_ARCHIVE_TOKEN": "token", "steps.collect.outcome": "success",
                     "steps.deploy.outputs.deployed": "true"}
            rc, lanes = self.puts(EARLY, given, Path(td), python_rc=1)
        self.assertEqual((rc != 0, lanes), (True, ["state", "site"]))

    def test_no_token_is_red_and_puts_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            for name, given in ((EARLY, {"steps.collect.outcome": "success", "steps.deploy.outputs.deployed": "true"}),
                                (LAST, {"steps.history.outcome": "success"})):
                rc, lanes = self.puts(name, given, Path(td))
                self.assertEqual((rc, lanes), (1, []), name)

    def test_history_lane_only_when_the_history_step_ran(self):
        with tempfile.TemporaryDirectory() as td:
            for outcome, want in (("success", ["history"]), ("skipped", []), ("failure", [])):
                rc, lanes = self.puts(LAST, {"secrets.TIC_ARCHIVE_TOKEN": "t", "steps.history.outcome": outcome},
                                      Path(td))
                self.assertEqual((rc, lanes), (0, want), outcome)
            rc, lanes = self.puts(LAST, {"secrets.TIC_ARCHIVE_TOKEN": "t", "steps.history.outcome": "success"},
                                  Path(td), python_rc=1)
            self.assertNotEqual(rc, 0)                      # a failed put turns the run red


if __name__ == "__main__":
    unittest.main()
