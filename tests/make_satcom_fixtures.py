"""Record real Cloudflare Radar and IODA responses for the SATCOM traffic lane (tests/fixtures/satcom/).

Runs satcom.fetch_traffic for a few networks with a recording fetch, so every URL the lane asks for is saved with the
answer it got, nothing edited, plus the 4 Feb 2026 Starlink-in-Ukraine replay batch. Needs CLOUDFLARE_RADAR_TOKEN.

    python tests/make_satcom_fixtures.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackout import ioda, radar, satcom  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "satcom"
# Starlink (top 20 countries), OneWeb, Inmarsat, and Viasat AS40305 (Radar sees no traffic from it, 20 Nov 2026).
ASNS = (14593, 800, 31515, 40305)
# Starlink in Ukraine around Ukraine's 3 Feb 2026 23:59 terminal-registration deadline, with its two controls.
REPLAY_SERIES = [("UAstarlink", 14593, "UA"), ("starlink", 14593, None), ("UAall", 0, "UA")]
REPLAY_END = datetime(2026, 2, 5, 0, 0, tzinfo=timezone.utc)


def name_for(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16] + ".json"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    def rec(get):
        def f(url, *a, **k):
            data = get(url)
            n = name_for(url)
            (OUT / n).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
            files[n] = url
            return data
        return f

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    trend_first = now.replace(hour=0, minute=0) - timedelta(days=60)
    nets = [n for n in satcom.counted() if n["asn"] in ASNS]
    traffic, daily, problems = satcom.fetch_traffic(now, rec(radar._get), rec(ioda._get_json), nets, trend_first,
                                                    workers=1)
    replay_urls = {name: satcom.timeseries_url([(name, asn, cc)], REPLAY_END - timedelta(days=satcom.HOURLY_DAYS),
                                               REPLAY_END, "1h") for name, asn, cc in REPLAY_SERIES}
    for url in replay_urls.values():
        rec(radar._get)(url)
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "note": ("Real Cloudflare Radar (CC BY-NC 4.0) and IODA responses recorded by tests/make_satcom_fixtures.py "
                 "with a Radar read token; nothing edited. Files are named by a hash of the URL."),
        "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trend_first": trend_first.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asns": list(ASNS),
        "replay_urls": replay_urls,
        "problems_when_recorded": problems,
        "files": files,
    }, indent=1), encoding="utf-8", newline="\n")
    print(f"{len(files)} responses; problems: {problems}")
    for r in traffic["networks"]:
        w = (r["radar"] or {}).get("world") or {}
        print(r["asn"], r["operator"], "seen", (r["radar"] or {}).get("seen"), "world", w.get("pct"),
              {cc: m.get("pct") for cc, m in ((r["radar"] or {}).get("countries") or {}).items()}, "ioda", r["ioda"])


if __name__ == "__main__":
    main()
