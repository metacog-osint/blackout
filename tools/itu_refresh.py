"""Offline, about once a year: rewrite blackout/itu_internet_use.json from the World Bank's copy of ITU's series.

    python tools/itu_refresh.py [--save-raw DIR]

Reads IT.NET.USER.ZS and SP.POP.TOTL (2019 on) and the World Bank's country list (to leave out its aggregates), then
keeps each country's latest year (`blackout/itu.py` says why the World Bank and not ITU DataHub). The API can take
30 s or more for a series, so each read waits up to 4 minutes. --save-raw keeps the three responses, as recorded for
tests/fixtures/itu/.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackout import itu  # noqa: E402

USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read(50_000_000).decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--save-raw", type=Path, help="also write the three API responses here, gzipped")
    args = ap.parse_args()
    urls = {"share": itu.series_url(itu.SHARE), "population": itu.series_url(itu.POPULATION),
            "countries": itu.COUNTRIES_URL}
    docs = {k: _get(u) for k, u in urls.items()}
    read_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = itu.reduce(docs["share"], docs["population"], docs["countries"], read_at)
    itu.FILE.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8",
                        newline="\n")
    years = {}
    for _, year, _ in doc["countries"].values():
        years[year] = years.get(year, 0) + 1
    print(f"Wrote {itu.FILE.name}: {len(doc['countries'])} countries, years {dict(sorted(years.items()))}, "
          f"World Bank updated {doc['world_bank_updated']}.")
    if args.save_raw:
        args.save_raw.mkdir(parents=True, exist_ok=True)
        stamp = read_at[:10]
        for k, d in docs.items():
            (args.save_raw / f"wb_{k}_{stamp}.json.gz").write_bytes(
                gzip.compress(json.dumps(d, ensure_ascii=False).encode("utf-8"), mtime=0))
        manifest = {"note": f"Real World Bank API responses, recorded {read_at} by tools/itu_refresh.py --save-raw; "
                            "nothing edited.", "files": {f"wb_{k}_{stamp}.json.gz": u for k, u in urls.items()}}
        (args.save_raw / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Saved the raw responses in {args.save_raw}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
