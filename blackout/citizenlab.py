"""Censorship test lists (context for OONI): what Citizen Lab's lists ask OONI probes to test in each country.

OONI's Web Connectivity test checks the URLs in Citizen Lab's test lists (github.com/citizenlab/test-lists): a global
list and one per country, each row a URL with a category code (31 codes in 00-LEGEND-new_category_codes.csv). An
OONI anomaly rate means more beside what was tested, so Blackout shows, for each country it reads OONI for (and each
country with a current outage), how many URLs its list holds and in which categories, with the date the newest was
added. Counts only: Blackout never republishes the URLs or the notes.

Shapes (raw.githubusercontent.com, master; recorded 13 Nov 2026 at commit 3305ad9584 of 4 Nov 2026):
  lists/<cc>.csv and lists/global.csv   url, category_code, category_description, date_added, source, notes
  lists/00-LEGEND-new_category_codes.csv   Category Description, New Code, Replaces old code(s), Description

Licence: "All data is provided under Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International"
(the repository's README, read 13 Nov 2026), with the citation it asks for: Citizen Lab and Others. 2014. URL
Testing Lists Intended for Discovering Website Censorship.
"""
from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from collections import Counter

RAW = "https://raw.githubusercontent.com/citizenlab/test-lists/master/lists/{name}.csv"
LEGEND_URL = RAW.format(name="00-LEGEND-new_category_codes")
PAGE = "https://github.com/citizenlab/test-lists"
LICENCE = "CC BY-NC-SA 4.0 (Citizen Lab and Others, URL Testing Lists Intended for Discovering Website Censorship)"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"


def _get(url: str, timeout: float = 60) -> str | None:
    """The file's text, or None when the list doesn't exist (404: Citizen Lab has no list for that country)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(20_000_000).decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def parse_legend(text: str) -> dict[str, dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "New Code" not in rows[0]:
        raise ValueError("test-list legend lacks its columns")
    return {r["New Code"].strip(): {"name": r["Category Description"].strip(), "description": r["Description"].strip()}
            for r in rows if (r.get("New Code") or "").strip()}


def parse_list(text: str) -> dict:
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows and "category_code" not in rows[0]:
        raise ValueError("test list lacks its columns")
    cats = Counter((r.get("category_code") or "").strip() or "none" for r in rows)
    dates = [(r.get("date_added") or "").strip() for r in rows if (r.get("date_added") or "").strip()]
    return {"urls": len(rows), "categories": dict(cats.most_common()), "latest_added": max(dates) if dates else None}


def fetch_testlists(countries, fetch=_get) -> tuple[dict | None, list[dict]]:
    """Legend, global list and each country's list, as counts. None when the legend or global list can't be read."""
    try:
        legend = parse_legend(fetch(LEGEND_URL))
        glob = parse_list(fetch(RAW.format(name="global")))
    except Exception as exc:
        return None, [{"endpoint": "citizenlab", "error": f"{type(exc).__name__}: {exc}"[:200]}]
    problems, per = [], {}
    for cc in sorted({c.upper() for c in countries if c}):
        try:
            text = fetch(RAW.format(name=cc.lower()))
            per[cc] = parse_list(text) if text is not None else None  # None: Citizen Lab keeps no list there
        except Exception as exc:
            problems.append({"endpoint": "citizenlab", "error": f"{cc}: {type(exc).__name__}: {exc}"[:200]})
    unknown = sorted({k for c in [glob, *[v for v in per.values() if v]] for k in c["categories"]} - set(legend) - {"none"})
    return {"legend": legend, "global": glob, "countries": per, "codes_not_in_legend": unknown}, problems
