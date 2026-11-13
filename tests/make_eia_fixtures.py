"""Run as: python -P tests/make_eia_fixtures.py NAME NOW   (NOW like 2026-11-13T16:30Z)

Record the real EIA API v2 responses blackout.eia.fetch_eia reads for a run at NOW: each response body exactly as
returned, gzipped (a day of all respondents is about 700 KB of JSON), under tests/fixtures/eia/, listed in its
MANIFEST.json by the URL without the key. EIA does not echo the key; this checks it is not in any body all the same.
The key comes from EIA_API_KEY (or HKCU\\Environment) and is never printed."""
import gzip
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"D:\Projects\blackout")
from blackout import eia  # noqa: E402

NAME, NOW = sys.argv[1], datetime.strptime(sys.argv[2], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
OUT = Path(r"D:\Projects\blackout\tests\fixtures\eia")
OUT.mkdir(parents=True, exist_ok=True)
man_path = OUT / "MANIFEST.json"
manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {"note": "", "files": {}}
key = eia.api_key()
assert key, "EIA_API_KEY not found"


def record(url: str):
    assert "api_key" not in url
    req = urllib.request.Request(url + "&api_key=" + key, headers={"User-Agent": eia.USER_AGENT})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            break
        except Exception as exc:  # 504s happen; the error text never holds the key (urllib omits the URL)
            print("retry", attempt, type(exc).__name__)
    else:
        raise RuntimeError("no response")
    assert key.encode() not in body
    start = url.split("&start=")[1][:13]
    off = url.split("&offset=")[1].split("&")[0]
    name = f"{NAME}_{start}_{off}.json.gz"
    (OUT / name).write_bytes(gzip.compress(body, mtime=0))
    manifest["files"][name] = url
    print(name, len(body))
    return json.loads(body)


got, problems = eia.fetch_eia(fetch=record, now=NOW, deadline_s=1800)
man_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
print("problems:", problems)
print("low:", got and got["notable"])
