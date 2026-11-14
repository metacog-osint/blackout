"""Internet use by country (context only): ITU's share of people using the internet, with the population that year.

What it answers: how many people an outage in a country can reach (not how many it did). It sits beside IODA and OONI
rows on the page; it never changes an indicator, a judgment or a feed item.

Source (checked 14 Nov 2026): ITU's per-country series "Individuals using the Internet (% of population)" is on ITU
DataHub (datahub.itu.int), which answers scripts with 403, site and API alike. The World Bank's World Development
Indicators republish it as IT.NET.USER.ZS, source "World Telecommunication/ICT Indicators Database, International
Telecommunication Union (ITU)", and list it as CC BY 4.0; SP.POP.TOTL (population, from the UN's World Population
Prospects and national statistics offices) is CC BY 4.0 too (series metadata, api.worldbank.org, read 14 Nov 2026).
ITU's own terms allow "personal, educational, or non-commercial purposes" with the source acknowledged
(itu.int/en/about/Pages/terms-of-use.aspx, read 14 Nov 2026); Blackout is non-commercial and credits both.

Reading: each country's latest year with a value from 2019 on (172 of 190 countries had 2024 in the World Bank's
11 Nov 2026 update; Myanmar and Eritrea stop at 2020), and the population for that same year. Sudan, Syria,
Turkmenistan, North Korea and Kosovo, among 27 territories, have no value, and the page says so rather than guessing.
ITU counts a person who used the internet from any place in the last three months. Annual data: refresh with
`python tools/itu_refresh.py`, which rewrites itu_internet_use.json (committed); collection never reads the API.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).with_name("itu_internet_use.json")
SHARE = "IT.NET.USER.ZS"
POPULATION = "SP.POP.TOTL"
FIRST_YEAR = 2019
API = "https://api.worldbank.org/v2"
SOURCE = ("ITU World Telecommunication/ICT Indicators Database (individuals using the internet), with population from "
          "the UN World Population Prospects, both via the World Bank's World Development Indicators")
LICENCE = ("World Bank WDI, CC BY 4.0 (IT.NET.USER.ZS and SP.POP.TOTL); ITU's terms allow non-commercial use with the "
           "source acknowledged")


def series_url(indicator: str) -> str:
    return f"{API}/country/all/indicator/{indicator}?format=json&per_page=2000&date={FIRST_YEAR}:2030"


COUNTRIES_URL = f"{API}/country?format=json&per_page=400"


def _rows(doc) -> list[dict]:
    if not (isinstance(doc, list) and len(doc) == 2 and isinstance(doc[1], list)):
        raise ValueError("World Bank response is not [meta, rows]")
    meta = doc[0]
    if int(meta.get("pages") or 1) > 1:
        raise ValueError(f"World Bank response has {meta.get('pages')} pages; raise per_page")
    return doc[1]


def reduce(share_doc, population_doc, countries_doc, read_at: str) -> dict:
    """ISO2 -> [percent online, year, population that year], for countries only (the World Bank's regional and income
    aggregates, region id "NA", are left out), each at its latest year with a value."""
    countries = {c["id"]: c["iso2Code"] for c in _rows(countries_doc)
                 if (c.get("region") or {}).get("id") != "NA" and c.get("iso2Code")}
    latest: dict[str, dict] = {}
    for r in _rows(share_doc):
        k = r.get("countryiso3code")
        if k in countries and r.get("value") is not None and (k not in latest or r["date"] > latest[k]["date"]):
            latest[k] = r
    pop = {(r.get("countryiso3code"), r.get("date")): r["value"] for r in _rows(population_doc)
           if r.get("value") is not None}
    out = {}
    for k, r in sorted(latest.items(), key=lambda kv: countries[kv[0]]):
        p = pop.get((k, r["date"]))
        out[countries[k]] = [round(float(r["value"]), 1), int(r["date"]), int(p) if p is not None else None]
    return {"about": __doc__.strip().split("\n")[0], "source": SOURCE, "licence": LICENCE, "read_at": read_at,
            "share": SHARE, "population": POPULATION, "world_bank_updated": share_doc[0].get("lastupdated"),
            "countries": out}


def load(path: Path = FILE) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot_part(doc: dict | None = None) -> dict | None:
    """What the page reads: the table, its source and licence. None if the committed file is missing."""
    doc = doc if doc is not None else load()
    if not doc:
        return None
    return {k: doc.get(k) for k in ("source", "licence", "read_at", "world_bank_updated", "countries")}
