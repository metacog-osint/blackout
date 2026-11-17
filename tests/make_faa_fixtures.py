"""Record real FAA ATCSCC pages for tests/test_lanes.py (TestFaa): python tests/make_faa_fixtures.py

Each `now` below is replayed through faa.fetch_faa with a recording fetch; every page it asks for is saved gzipped as
tests/fixtures/faa/<sha1 of url>.html.gz and listed in MANIFEST.json with its URL and the time it was read."""
import gzip, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackout import faa  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "faa"
NOWS = ["2026-11-13T23:00:00Z", "2026-11-14T12:00:00Z", "2025-08-07T00:30:00Z", "2024-07-19T12:00:00Z",
        "2026-11-16T23:59:00Z"]


def name(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16] + ".html.gz"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "MANIFEST.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {"pages": {}}
    def rec(url):
        f = OUT / name(url)
        if f.exists():
            return gzip.decompress(f.read_bytes())
        body = faa._get(url)
        f.write_bytes(gzip.compress(body, mtime=0))
        manifest["pages"][url] = {"file": f.name,
                                  "read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return body
    for n in NOWS:
        now = datetime.strptime(n, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        snap, problems = faa.fetch_faa(now, fetch=rec)
        print(n, len(snap["events"]), "events,", snap["ground_stops"], "stops,", len(problems), "problems")
    manifest["note"] = ("Real pages from www.fly.faa.gov (FAA ATCSCC advisories database), US Government work, public "
                        "domain. Recorded by tests/make_faa_fixtures.py.")
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
