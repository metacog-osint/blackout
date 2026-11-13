"""Access Now's STOP (Shutdown Tracker Optimization Project) sheet: documented internet shutdowns, as country history.

The public Google Sheet linked from Access Now's #KeepItOn methodology page ("DOWNLOAD OUR DATASET",
accessnow.org/keepiton-data-methodology, last updated 30 Mar 2026) is read as CSV (its first tab). On 13 Nov 2026 that
tab held 313 rows: the 313 shutdowns Access Now counted in 2025, with longer-running ones that began earlier counted
again in the year they continued (Access Now counts an ongoing block "once per every year in which they occur").
`shutdown_status` is as of the end of that calendar year (Ended, Ongoing, Unknown), so "Ongoing" means ongoing on
31 Dec 2025, never now. The sheet is annual with retroactive edits; it is history, not a live feed.

Use: context only. Each country's count, causes and latest start appear on the page and in the summary of a current
outage in that country. They never raise or lower a judgment (BO-H-1A needs OONI with IODA, as before): a country's
record of shutdowns says nothing about the cause of this one.

Licence: Access Now states none for the dataset. Its site footer links CC BY 4.0 and its media page licenses
"original Media Material" (images, symbols, icons, graphics, logos) under CC BY 4.0; neither says it covers the data,
so none is claimed. Access Now is credited and only counts and fields are published, never the sheet's own text
columns (gov_ack_quote, telco_ack_quote, info_source links).
"""
from __future__ import annotations

import csv
import io
import re
import urllib.request
from collections import Counter
from datetime import datetime

from tic_doctrine import geo

SHEET = "https://docs.google.com/spreadsheets/d/1DvPAuHNLp5BXGb0nnZDGNoiIwEeu2ogdXEIDvT4Hyfk"
CSV_URL = f"{SHEET}/export?format=csv"
PAGE = "https://www.accessnow.org/keepiton-data-methodology"
LICENCE = ("No licence stated by Access Now for the STOP dataset; credited as the source, counts only (its Creative Commons "
           "Attribution notice covers original media material on its website, not data)")
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"
# The sheet's country names (UN style) that differ from the kit's Natural Earth names.
ALIASES = {
    "iran (islamic republic of)": "IR", "russian federation": "RU", "tanzania, united republic of": "TZ",
    "palestine, state of": "PS", "venezuela (bolivarian republic of)": "VE", "syrian arab republic": "SY",
    "congo (democratic republic of the)": "CD", "viet nam": "VN", "korea, democratic people's republic of": "KP",
    "korea, republic of": "KR", "bolivia (plurinational state of)": "BO", "lao people's democratic republic": "LA",
    "moldova, republic of": "MD", "türkiye": "TR", "turkey": "TR", "côte d'ivoire": "CI", "cote d'ivoire": "CI",
    "congo": "CG", "united states of america": "US", "united kingdom": "GB", "czechia": "CZ",
}
REQUIRED = ("start_date", "country", "shutdown_type", "shutdown_status")


def _get(url: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(20_000_000).decode("utf-8-sig")


def _codes(name: str, by_name: dict[str, str]) -> list[str | None]:
    """ISO alpha-2 for each country in the cell (a few rows name two, separated by ';'); None if unknown."""
    out = []
    for part in [p.strip() for p in re.split(r";", name or "") if p.strip()]:
        key = part.lower()
        out.append(ALIASES.get(key) or by_name.get(key))
    return out


def _date(s: str) -> str | None:
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse(text: str, names: dict[str, str] | None = None) -> dict:
    """Per-country STOP history from the sheet's CSV."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or any(k not in rows[0] for k in REQUIRED):
        raise ValueError("STOP sheet has no rows or lacks its columns")
    by_name = names if names is not None else {n.lower(): cc for cc, n in geo._names().items()}
    per: dict[str, dict] = {}
    unmatched: Counter = Counter()
    years: Counter = Counter()
    for r in rows:
        start = _date(r.get("start_date"))
        if start:
            years[start[:4]] += 1
        for cc, label in zip(_codes(r.get("country"), by_name), [p.strip() for p in (r.get("country") or "").split(";")]):
            if not cc:
                unmatched[label] += 1
                continue
            c = per.setdefault(cc, {"country": cc, "shutdowns": 0, "ongoing_at_year_end": 0, "latest_start": None,
                                    "causes": Counter(), "types": Counter(), "nationwide": 0})
            c["shutdowns"] += 1
            c["ongoing_at_year_end"] += (r.get("shutdown_status") or "").strip() == "Ongoing"
            c["nationwide"] += (r.get("area_name") or "").strip().lower() == "nationwide"
            if start and (c["latest_start"] is None or start > c["latest_start"]):
                c["latest_start"] = start
            if (r.get("actual_cause") or "").strip():
                c["causes"][r["actual_cause"].strip()] += 1
            for t in (r.get("shutdown_type") or "").split(","):
                if t.strip():
                    c["types"][t.strip()] += 1
    year = max(years, key=lambda y: years[y]) if years else None
    countries = []
    for c in sorted(per.values(), key=lambda c: (-c["shutdowns"], c["country"])):
        countries.append({**c, "name": geo.name(c["country"]) or c["country"],
                          "causes": [k for k, _ in c["causes"].most_common(3)],
                          "types": dict(c["types"].most_common())})
    return {"year": year, "rows": len(rows), "countries": countries,
            "unmatched": [{"name": k, "rows": v} for k, v in unmatched.most_common()]}


def context(stop: dict | None, cc: str) -> str:
    """One sentence of STOP history for a country, or '' when the sheet wasn't read or lists none there."""
    if not stop or not cc:
        return ""
    c = next((x for x in stop.get("countries") or [] if x["country"] == cc), None)
    if not c:
        return (f" Access Now's STOP sheet lists no shutdown in this country in {stop.get('year')}; context only, "
                "not evidence about this outage.")
    causes = f" (stated causes: {', '.join(c['causes']).lower()})" if c.get("causes") else ""
    return (f" Access Now's STOP sheet lists {c['shutdowns']} shutdown{'s' if c['shutdowns'] != 1 else ''} here in "
            f"{stop.get('year')}{causes}, the latest starting {c['latest_start']}; history only, not evidence about "
            "this outage.")


def fetch_stop(fetch=_get) -> tuple[dict | None, list[dict]]:
    try:
        out = parse(fetch(CSV_URL))
    except Exception as exc:
        return None, [{"endpoint": "stop-sheet", "error": f"{type(exc).__name__}: {exc}"[:200]}]
    problems = [{"endpoint": "stop-sheet", "error": f"country not matched: {u['name']} ({u['rows']} rows)"}
                for u in out["unmatched"]]
    return out, problems
