"""Record real Bluesky search counts for tests/test_public.py: python tests/make_bluesky_fixtures.py

Each scenario below is replayed through bluesky.fetch_bluesky with a recording fetch (and the rule's lines set to
zero, so every name's hourly counts are recorded too, whatever the rule later says). Every response is kept in
tests/fixtures/bluesky/MANIFEST.json under its URL, reduced before it is written: the one post searchPosts returns is
dropped, and so is `cursor`, which encodes that post's time, its author's DID and its record key (checked 17 Nov 2026:
base64 of {"s": [<ms>, "did:plc:...", "<rkey>"]}), so no stranger's post or account is committed. `hitsTotal`, the
only field the lane reads, is kept as received."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackout import bluesky  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "bluesky"
# (run time, watched keys): the bank and United outages of 16 Nov 2026, with two quiet names; the FAA's nationwide
# ground stop and the National Weather Service of 13 Nov 2026, with Outlook a day into Microsoft's outage; and the
# runs 4 h later, when United's outage and the Weather Service's had begun.
SCENARIOS = [
    ("2026-11-16T21:45:00Z", ["chase", "bank-of-america", "wells-fargo", "united-airlines", "marriott", "delta"]),
    ("2026-11-13T21:45:00Z", ["faa", "nws", "outlook"]),
    ("2026-11-17T01:45:00Z", ["united-airlines", "chase"]),  # the next run: United's outage began after 21:45
    ("2026-11-14T01:45:00Z", ["nws", "faa"]),  # the next run: the Weather Service's surge came after 21:45
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "MANIFEST.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {"responses": {}}

    def rec(url):
        if url in manifest["responses"]:
            return json.dumps(manifest["responses"][url]["body"]).encode()
        d = json.loads(bluesky._get(url))
        body = {"posts": [], "hitsTotal": d["hitsTotal"]}
        manifest["responses"][url] = {"body": body, "posts_dropped": len(d.get("posts") or []),
                                      "cursor_dropped": "cursor" in d,
                                      "read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return json.dumps(body).encode()

    for when, keys in SCENARIOS:
        now = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        watch = [w for w in bluesky.WATCH if w[0] in keys]
        with mock.patch.object(bluesky, "MIN_MATCHES", 0), mock.patch.object(bluesky, "RATIO", 0.0):
            snap, problems = bluesky.fetch_bluesky(now, fetch=rec, watch=watch)
        print(when, [(r["key"], r["matches"], r["usual"]) for r in snap["rows"]], problems)
    manifest["note"] = (
        "Real responses from Bluesky's public search (https://api.bsky.app/xrpc/app.bsky.feed.searchPosts, no account), "
        "recorded by tests/make_bluesky_fixtures.py. Each body is reduced before it is written: the one post returned "
        "(limit=1) is dropped, and so is `cursor`, which encodes that post's time, its author's DID and its record key; "
        "`hitsTotal`, the only field Blackout reads, is as received. No post, text, handle or author is committed.")
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
