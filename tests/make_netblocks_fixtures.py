"""Record the real Bluesky responses one netblocks.fetch reads, for tests/test_netblocks.py:

    python tests/make_netblocks_fixtures.py

    python tests/make_netblocks_fixtures.py --from-fixtures   # re-sample the pages already saved, no network

Reads NetBlocks' whole public Bluesky account once (app.bsky.feed.getAuthorFeed, no account) and saves a small sample of
it: three pages, gzipped as tests/fixtures/netblocks/page-NN.json.gz, with MANIFEST.json listing each page's URL and
the run's time. NetBlocks' posts are copyright NetBlocks, all rights reserved, and this repository is public, so the
recorder keeps the responses' shape and fields but only a few posts (see sample()), and of those only the first
EXCERPT_CHARS characters of each post's text (what Blackout itself quotes); image alt text and link cards are dropped.
Parser tests use short sentences written for the tests in NetBlocks' style."""
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from blackout import netblocks  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "netblocks"


def _reduce(body: dict) -> dict:
    """The response with NetBlocks' words cut to the excerpt length and alt text and link cards dropped."""
    for e in body.get("feed") or []:
        for post in [e.get("post") or {}, *[(e.get("reply") or {}).get(k) or {} for k in ("root", "parent")]]:
            rec = post.get("record") or {}
            if isinstance(rec.get("text"), str):
                rec["text"] = rec["text"][:netblocks.EXCERPT_CHARS]
            n = len(rec.get("text", "").encode("utf-8"))
            rec["facets"] = [f for f in rec.get("facets") or [] if (f.get("index") or {}).get("byteEnd", 0) <= n]
            if "embed" in rec:
                rec["embed"] = {"$type": rec["embed"].get("$type")}
            if "embed" in post:
                post["embed"] = {"$type": post["embed"].get("$type")}
    return body


def _created(e: dict) -> str:
    return ((e.get("post") or {}).get("record") or {}).get("createdAt") or ""


def sample(pages: list[tuple[str, dict]], recorded: datetime) -> list[tuple[str, dict]]:
    """Three of the recorded pages, cut to a few entries each and linked by their cursors so fetch reads them in turn:
    the first page's entries of the 72 h before the recording (the feed's window); from the first later page holding a
    repost, that repost, its first post in a thread and its first post naming a country; and the last page's last two
    entries (the account's first post). The middle page's cursor is rewritten to the last page's, so the account still
    reads to its first post in three pages."""
    first_url, first = pages[0]
    last_url, last = pages[-1]
    mid = next(b for _, b in pages[1:-1] if any(e.get("reason") for e in b.get("feed") or []))
    cut = (recorded - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%S")
    feed = mid["feed"]
    picks = [next(e for e in feed if e.get("reason")),
             next(e for e in feed if not e.get("reason") and (e.get("post") or {}).get("record", {}).get("reply")),
             next(e for e in feed if not e.get("reason") and netblocks.parse(e) and netblocks.parse(e)["countries"])]
    mid_entries = [e for e in feed if any(e is p for p in picks)]
    last_cursor = parse_qs(urlsplit(last_url).query).get("cursor", [None])[0]
    return [
        (first_url, {**first, "feed": [e for e in first["feed"] if _created(e) >= cut]}),
        (netblocks.page_url(first["cursor"]), {**mid, "feed": mid_entries, "cursor": last_cursor}),
        (last_url, {**last, "feed": last["feed"][-2:]}),
    ]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pages: list[tuple[str, dict]] = []
    if "--from-fixtures" in sys.argv:
        manifest = json.loads((OUT / "MANIFEST.json").read_text(encoding="utf-8"))
        recorded = datetime.strptime(manifest["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        for name, url in manifest["files"].items():
            pages.append((url, json.loads(gzip.decompress((OUT / name).read_bytes()))))
    else:
        recorded = datetime.now(timezone.utc).replace(microsecond=0)

        def fetch(url):
            body = _reduce(netblocks._get(url))
            pages.append((url, body))
            return body
        _, problems = netblocks.fetch(getter=fetch)
        if problems:
            raise SystemExit(f"read cut short, nothing saved: {problems}")
    for old in OUT.glob("page-*.json.gz"):
        old.unlink()
    files = {}
    for url, body in sample(pages, recorded):
        name = f"page-{len(files):02d}.json.gz"
        (OUT / name).write_bytes(gzip.compress(json.dumps(body).encode("utf-8"), mtime=0))
        files[name] = url
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "note": ("A sample of the real responses read by blackout.netblocks.fetch, recorded by "
                 "tests/make_netblocks_fixtures.py: three pages of the account, a few entries each, the middle page's "
                 "cursor rewritten to the last page's. NetBlocks' posts are copyright NetBlocks (all rights reserved): "
                 f"each post's text is cut to its first {netblocks.EXCERPT_CHARS} characters, facets past the cut, "
                 "image alt text and link cards are dropped; every other field is as Bluesky sent it."),
        "recorded_at": recorded.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    replay = {u: json.loads(gzip.decompress((OUT / n).read_bytes())) for n, u in files.items()}
    posts, problems = netblocks.fetch(getter=replay.__getitem__)
    print(len(files), "pages;", len(posts), "posts;", problems)


if __name__ == "__main__":
    main()
