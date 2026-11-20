"""Record the real Bluesky responses one netblocks.fetch reads, for tests/test_netblocks.py:

    python tests/make_netblocks_fixtures.py

Reads NetBlocks' whole public Bluesky account once (app.bsky.feed.getAuthorFeed, no account); each page is saved
gzipped as tests/fixtures/netblocks/page-NN.json.gz, and MANIFEST.json lists its URL and the run's time.
NetBlocks' posts are copyright NetBlocks, all rights reserved, and this repository is public, so the recorder keeps the
response's shape and every field except NetBlocks' words: each post's text is cut to its first EXCERPT_CHARS characters
(what Blackout itself quotes) and image alt text and link cards are dropped. Parser tests use short sentences written
for the tests in NetBlocks' style."""
import gzip
import json
import sys
from datetime import datetime, timezone
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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}

    def fetch(url):
        body = _reduce(netblocks._get(url))
        name = f"page-{len(files):02d}.json.gz"
        (OUT / name).write_bytes(gzip.compress(json.dumps(body).encode("utf-8"), mtime=0))
        files[name] = url
        return body
    posts, problems = netblocks.fetch(getter=fetch)
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "note": ("Real responses read by blackout.netblocks.fetch, recorded by tests/make_netblocks_fixtures.py. NetBlocks' "
                 "posts are copyright NetBlocks (all rights reserved): each post's text is cut to its first "
                 f"{netblocks.EXCERPT_CHARS} characters, facets past the cut, image alt text and link cards are "
                 "dropped; every other field is as Bluesky sent it."),
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(len(files), "pages;", len(posts), "posts;", problems)


if __name__ == "__main__":
    main()
