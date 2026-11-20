"""Record the real IODA and Cloudflare Radar responses one trends.fetch reads, for tests/test_trends.py:

    python tests/make_trends_fixtures.py        (needs CLOUDFLARE_RADAR_TOKEN)

Runs trends.fetch once at the current hour with recording fetchers; each response is saved gzipped, unedited, as
tests/fixtures/trends/<name>.json.gz, and MANIFEST.json lists its URL (token never written) and the run's `now`."""
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from blackout import ioda, radar, trends  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "trends"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    files = {}

    def recorder(name_of, get):
        def fetch(url):
            body = get(url)
            name = name_of(url)
            (OUT / name).write_bytes(gzip.compress(json.dumps(body).encode("utf-8"), mtime=0))
            files[name] = url
            return body
        return fetch

    def radar_name(url):
        return "radar_outages.json.gz" if "annotations/outages" in url else "radar_anomalies.json.gz"

    doc, problems = trends.fetch(now, ioda_fetch=recorder(lambda u: "ioda_events.json.gz", ioda._get_json),
                                 radar_fetch=recorder(radar_name, radar._get))
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "note": ("Real responses read by blackout.trends.fetch, recorded by tests/make_trends_fixtures.py with a Radar "
                 "read token; nothing edited. IODA: Georgia Tech, credited (no data licence stated). Radar: CC BY-NC "
                 "4.0."),
        "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(now, len(doc["countries"]), "countries;", len(problems), "problems;", doc["notes"])


if __name__ == "__main__":
    main()
