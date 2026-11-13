"""US grid base rates (BO-IND-14): how often a state has more than 50,000 customers out, from EAGLE-I's history.

EAGLE-I (Environment for Analysis of Geo-Located Energy Information, Oak Ridge National Laboratory for the US
Department of Energy) reads utilities' public outage maps every 15 minutes. Its yearly history is published on
figshare: Brelsford et al., "The Environment for Analysis of Geo-Located Energy Information's Recorded Electricity
Outages 2014-2025", doi:10.6084/m9.figshare.24237376 (version 4, 25 Feb 2026), CC BY 4.0 as the record states (read
through the figshare API, 13 Nov 2026). The data descriptor is Brelsford et al., Scientific Data 11:271 (2024), also
CC BY 4.0. Facts from the descriptor (Europe PMC full text PMC10915145, read 13 Nov 2026):
  - one row per county and 15-minute collection run: fips_code, county, state, customers_out, run_start_time;
  - "All data in EAGLE-I is presented in Coordinated Universal Time (UTC)"; run_start_time is the start of the run;
  - rows with no customers out are left out, and "Missing entries are either a customer outage value of zero or a
    gap in data collection; we do not distinguish between these cases";
  - "customers" are what each utility counts, most often a meter; coverage rose through the years, to 92% of
    customers in the 50 states, DC and Puerto Rico by 2022; the state coverage file covers 2018-2022.
Shapes seen in the files themselves (13 Nov 2026): the 2023 file names the count column "sum"; the 2024 file adds
total_customers and is sorted by county, then time (the others by time, then county); the 2020 file has rows with an
empty count. So columns are read by position, an empty count is zero, and nothing assumes the order.

Each year file is 1.0-1.4 GB, so live runs never read it. `tools/eaglei_history.py` reads the files once, offline,
and writes the compact tables in eaglei_rates.json (committed); Blackout reads only that file.

Reduction (the same rule Verdict's EAGLE-I ground truth uses):
  - a state's total for a run = the sum of its counties' customers_out in that run (state from the FIPS prefix);
  - a suspect run: one in which any county of the state shows more than 100,000 customers out beyond 1.1 times
    EAGLE-I's own modelled customers for it (MCC.csv, 2022). Suspect runs are kept as EAGLE-I published them, but
    they can't make an episode by themselves and don't set its peak. Why: from 21:00 to 22:30 UTC on 8 Jan 2025 (7
    runs) Southern California Edison's counties all read far too high at once (Ventura 1,077,426 out of 317,658
    modelled customers, Los Angeles 1,717,886, California 3,488,043 in all) while the runs before and after hold
    400,000-510,000 (the Santa Ana winds of the Los Angeles fires); capping the county alone still left 2.7 million.
    Why not drop them: the same test fires on real outages that EAGLE-I assigns to one county of a utility's region,
    where the state's total is right (Hurricane Fiona, Sept 2022: Caguas at 243,802 against 58,033 modelled, and
    Arecibo, Mayaguez and Ponce alike; Winter Storm Uri, Feb 2021: Brazoria County, Texas at 318,242 against
    144,164); dropping them lost Fiona and cut Uri. Without the 100,000 margin it fires all the time, because MCC is
    no guide for small counties (Henderson County, North Carolina is modelled at 24 customers);
  - a run missing from the file (no rows anywhere) is unknown: it neither joins nor splits an episode;
  - an episode at a line T = readings with the state's total above T, joined when no more than 3 known readings
    under the line (an hour) come between them and they are no more than 6 hours apart, with 4 or more of them
    (an hour of 15-minute runs) not suspect. Four readings, not just an hour from first to last, so that two
    one-run spikes can't make one. The line and the hour are DOE's: form DOE-417 makes "Loss of electric service to
    more than 50,000 customers for 1 hour or more" reportable (criterion 12, doe417.pnnl.gov/files/DOE-417_Form.pdf,
    read 13 Nov 2026);
  - an episode's peak is the highest total held over 4 readings in a row that aren't suspect; its hours run from
    its first reading to the end of its last;
  - an episode belongs to the month and year it starts in (UTC); one crossing New Year's Eve is counted in each
    year's file separately (each year is read alone).
Base years are 2018-2025: the years the coverage file covers and after (2014-2017 had far lower coverage; the 2014
file is 78 MB against 1.0-1.4 GB from 2018). A state's rate counts only the years in which EAGLE-I has any row for
it: EAGLE-I's own coverage file gives Puerto Rico 0% for 2018-2020, and its files have no Puerto Rico row in those
years, so its rate is over 2021-2025.

Caveat carried into every line: ODIN's reporting utilities cover fewer customers than EAGLE-I (about 47 million
against about 146 million in 2022), so a state above 50,000 at ODIN's utilities is at least as rare as these rates.
ODIN's data also feeds EAGLE-I, so the two are not independent.
"""
from __future__ import annotations

import csv
import io
import json
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median

ARTICLE = 24237376
API = f"https://api.figshare.com/v2/articles/{ARTICLE}"
DOI = "10.6084/m9.figshare.24237376"
PAGE = f"https://doi.org/{DOI}"
LICENCE = ("CC BY 4.0 (creativecommons.org/licenses/by/4.0), as the figshare record states (doi:10.6084/m9.figshare."
           "24237376, read 13 Nov 2026); Brelsford et al., Oak Ridge National Laboratory EAGLE-I; reduced by Blackout to "
           "state totals and episodes")
CITATION = ("Brelsford, C., Tennille, S., Myers, A., Chinthavali, S., Tansakul, V., Denman, M., et al. The Environment "
            "for Analysis of Geo-Located Energy Information's Recorded Electricity Outages 2014-2025. figshare, dataset "
            "(2023, version 4 of 25 Feb 2026). https://doi.org/10.6084/m9.figshare.24237376")
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"
RATES_FILE = Path(__file__).with_name("eaglei_rates.json")
BASE_YEARS = tuple(range(2018, 2026))
LINE = 50_000                 # DOE-417 criterion 12
LINES = (50_000, 100_000, 250_000, 500_000, 1_000_000)
STEP_MIN = 15
JOIN_BELOW = 3                # at most 3 known readings under the line (an hour) inside one episode
JOIN_MAX_RUNS = 24            # and readings above it no more than 6 hours apart
MIN_READINGS = 4              # an hour of 15-minute readings above the line
CAP = 1.1                     # a county more than SUSPECT_EXCESS over 1.1 x its modelled customers (MCC.csv)
SUSPECT_EXCESS = 100_000      # makes the run suspect for its state
FIPS_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
    "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
    "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI",
}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


# ---- Reading a year file -------------------------------------------------------------------------------------

def _run_index(stamp: str, year: int) -> int | None:
    """'2025-01-01 00:15:00' (or the descriptor's 'MM/DD/YY HH:MM') -> quarter-hour of that year, None if not."""
    s = stamp.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            t = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    if t.year != year:
        return None
    return int((t - datetime(year, 1, 1)).total_seconds() // (STEP_MIN * 60))


def runs_in(year: int) -> int:
    return int((datetime(year + 1, 1, 1) - datetime(year, 1, 1)).total_seconds() // (STEP_MIN * 60))


def load_caps(text: str) -> dict[str, int]:
    """MCC.csv (County_FIPS without the leading zero, Customers) -> {5-digit FIPS: 1.1 x modelled customers}."""
    out = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        try:
            out[r["County_FIPS"].strip().zfill(5)] = int(float(r["Customers"]) * CAP)
        except (KeyError, ValueError):
            continue
    return out


def state_totals(lines, year: int, caps: dict[str, int] | None = None) -> dict:
    """Stream one year's CSV (an iterable of text lines) into each state's total per 15-minute run.

    Returns {"year", "totals": {state: {run index: customers out}}, "runs_present": sorted run indexes with any row,
    "rows", "skipped": rows whose time, FIPS or count couldn't be read, "other_year": rows stamped outside the year,
    "suspect": {state: suspect runs}, "suspect_rows": county rows that made a run suspect}.
    caps: load_caps(MCC.csv); a county not in it is never over.
    """
    caps = caps or {}
    totals: dict[str, dict[int, int]] = {}
    present: set[int] = set()
    cache: dict[str, int | None] = {}
    suspect: dict[str, set[int]] = {}
    rows = skipped = other = suspect_rows = 0
    reader = csv.reader(lines)
    header = next(reader, None)
    if not header or len(header) < 5 or header[0].strip().lstrip("﻿") != "fips_code":
        raise ValueError(f"not an EAGLE-I outage file: header {header!r}")
    for r in reader:
        rows += 1
        if len(r) < 5:
            skipped += 1
            continue
        stamp = r[4]
        i = cache.get(stamp, -1)
        if i == -1:
            i = cache[stamp] = _run_index(stamp, year)
        if i is None:
            other += 1
            continue
        present.add(i)
        n = r[3].strip()
        if not n:
            continue          # an empty count (the 2020 file has some) is no customers out
        try:
            out = int(float(n))
        except ValueError:
            skipped += 1
            continue
        fips = r[0].strip().zfill(5)
        st = FIPS_STATE.get(fips[:2])
        if st is None:
            skipped += 1
            continue
        cap = caps.get(fips)
        if cap is not None and out - cap > SUSPECT_EXCESS:
            suspect.setdefault(st, set()).add(i)
            suspect_rows += 1
        if out > 0:
            s = totals.setdefault(st, {})
            s[i] = s.get(i, 0) + out
    return {"year": year, "totals": totals, "runs_present": sorted(present), "rows": rows, "skipped": skipped,
            "other_year": other, "suspect": {st: sorted(r) for st, r in sorted(suspect.items())},
            "suspect_rows": suspect_rows}


def missing_runs(reduced: dict) -> list[int]:
    """Runs of the year with no rows anywhere in the file, sorted."""
    present = set(reduced["runs_present"])
    return [i for i in range(runs_in(reduced["year"])) if i not in present]


def held_peak(series: dict[int, int], start: int, end: int, suspect: set[int] | frozenset = frozenset()) -> int:
    """The highest total held over 4 readings in a row between two runs, suspect runs left out."""
    vals = [series[i] for i in range(start, end + 1) if i in series and i not in suspect]
    if len(vals) < MIN_READINGS:
        return min(vals) if vals else 0
    return max(min(vals[k:k + MIN_READINGS]) for k in range(len(vals) - MIN_READINGS + 1))


def episodes(series: dict[int, int], line: int, unknown: list[int] | None = None,
             suspect: set[int] | frozenset = frozenset()) -> list[dict]:
    """Episodes above `line` in one state's run totals: start and end run, readings above, peak held an hour.

    unknown: sorted runs missing from the file (neither above nor under the line). suspect: runs kept as published
    that can't make an episode by themselves or set its peak."""
    unknown = unknown or []
    above = sorted(i for i, v in series.items() if v > line)
    out: list[dict] = []
    cur = None
    for i in above:
        gap_unknown = bisect_left(unknown, i) - bisect_right(unknown, cur["end"]) if cur else 0
        if cur and i - cur["end"] <= JOIN_MAX_RUNS and (i - cur["end"] - 1) - gap_unknown <= JOIN_BELOW:
            cur["end"] = i
            cur["readings"] += 1
            continue
        if cur:
            out.append(cur)
        cur = {"start": i, "end": i, "readings": 1}
    if cur:
        out.append(cur)
    kept = []
    for e in out:
        clean = sum(1 for i in range(e["start"], e["end"] + 1) if i in series and series[i] > line and i not in suspect)
        if clean < MIN_READINGS:
            continue
        e["suspect"] = e["readings"] - clean
        e["peak"] = held_peak(series, e["start"], e["end"], suspect)
        kept.append(e)
    return kept


def stamp(year: int, run: int) -> str:
    t = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() + run * STEP_MIN * 60
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def year_episodes(reduced: dict, lines=LINES) -> dict:
    """{"<line>": [{state, start, end, hours, peak, month}], "seen": [states with any row]} for one reduced year."""
    year = reduced["year"]
    out: dict[str, list[dict]] = {}
    unknown = missing_runs(reduced)
    suspect = {st: set(r) for st, r in reduced.get("suspect", {}).items()}
    for line in lines:
        rows = []
        for st, series in sorted(reduced["totals"].items()):
            for e in episodes(series, line, unknown, suspect.get(st, frozenset())):
                start = stamp(year, e["start"])
                rows.append({"state": st, "start": start, "end": stamp(year, e["end"] + 1),
                             "hours": round((e["end"] - e["start"] + 1) * STEP_MIN / 60, 2), "peak": e["peak"],
                             "month": int(start[5:7])})
        out[str(line)] = rows
    out["seen"] = sorted(reduced["totals"])
    return out


# ---- Base-rate tables ----------------------------------------------------------------------------------------

def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def rate_table(by_year: dict[int, dict], states: list[str], years=BASE_YEARS) -> dict:
    """Per state and line: episodes a year (mean and each year), by month (mean a year), duration and peak.

    by_year: {year: {"<line>": [episodes], "seen": [states]}} from year_episodes. A year in which EAGLE-I has no row
    for the state is left out of its rate (each_year holds None for it)."""
    table: dict[str, dict] = {}
    for st in states:
        seen = [y for y in years if st in by_year[y].get("seen", [st])]
        n = len(seen) or 1
        per_line = {}
        for line in LINES:
            each_eps = [[e for e in by_year[y][str(line)] if e["state"] == st] if y in seen else None for y in years]
            eps = [e for y_eps in each_eps if y_eps for e in y_eps]
            each = [len(y_eps) if y_eps is not None else None for y_eps in each_eps]
            months = [0] * 12
            for e in eps:
                months[e["month"] - 1] += 1
            hours = [e["hours"] for e in eps]
            per_line[str(line)] = {
                "years": seen,
                "per_year": round(len(eps) / n, 2),
                "each_year": each,
                "by_month": [round(m / n, 2) for m in months],
                "hours_median": round(median(hours), 1) if hours else None,
                "hours_p90": round(_pct(hours, 0.9), 1) if hours else None,
                "hours_max": max(hours) if hours else None,
                "peak_median": int(median([e["peak"] for e in eps])) if eps else None,
                "peak_max": max(e["peak"] for e in eps) if eps else None,
            }
        table[st] = per_line
    return table


@lru_cache(maxsize=1)
def rates() -> dict | None:
    """The committed tables, or None when the file is missing (base rates are then left out, never guessed)."""
    try:
        return json.loads(RATES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _times(x: float) -> str:
    if x == 0:
        return "never"
    if x < 0.95:
        every = round(1 / x)
        return "about once a year" if every <= 1 else f"about once every {every} years"
    if x < 1.5:
        return "about once a year"
    return f"about {round(x):,} times a year" if x < 20 else f"about {round(x / 5) * 5:,} times a year"


def _hours(h: float) -> str:
    if h < 1.5:
        return "about an hour"
    if h < 36:
        return f"about {round(h):,} hours"
    return f"about {round(h / 24):,} days"


def base_rate(state: str, month: int | None = None, doc: dict | None = None, line: int = LINE) -> dict | None:
    """What EAGLE-I's history says about `state` going over `line` for an hour or more; None if not in the tables."""
    doc = rates() if doc is None else doc
    if not doc:
        return None
    row = (doc.get("states") or {}).get(state, {}).get(str(line))
    if row is None:
        return None
    years = row.get("years") or doc["base_years"]
    if not years:
        return None
    span = f"{years[0]}-{years[-1]}"
    per_year = row["per_year"]
    n_years = len(years)
    total = sum(n for n in row["each_year"] if n)
    if total == 0:
        text = (f"EAGLE-I's record shows no time from {span} when {state_name(state)} had more than {line:,} customers "
                f"out for an hour or more.")
    else:
        text = (f"In EAGLE-I's record for {span}, {state_name(state)} had more than {line:,} customers out for an hour "
                f"or more {_times(per_year)} ({total:,} times in {n_years} years)")
        if month:
            m = row["by_month"][month - 1]
            text += f", {_times(m)} in {MONTHS[month - 1]}"
        text += f"; half lasted {_hours(row['hours_median'])} or less"
        if row["hours_p90"] is not None and total >= 10:
            text += f", 1 in 10 more than {_hours(row['hours_p90'])}"
        text += "."
    cov = (doc.get("coverage_2022") or {}).get(state)
    return {
        "state": state, "line": line, "years": [years[0], years[-1]], "per_year": per_year,
        "each_year": row["each_year"], "in_month": row["by_month"][month - 1] if month else None, "month": month,
        "hours_median": row["hours_median"], "hours_p90": row["hours_p90"], "peak_max": row["peak_max"],
        "coverage_2022": cov, "text": text,
        "caveat": ("EAGLE-I reads more utilities than ODIN (about 92% of US customers by 2022), so a state over the "
                   "line at ODIN's reporting utilities alone is rarer still. Coverage grew over these years, so earlier "
                   "years may count fewer."),
        "source": {"doi": doc.get("doi"), "version": doc.get("version"), "built_at": doc.get("built_at")},
    }


TERRITORIES = {"AS": "American Samoa", "GU": "Guam", "MP": "Northern Mariana Islands", "VI": "US Virgin Islands"}


def state_name(st: str) -> str:
    from .odin import STATE_NAMES
    return STATE_NAMES.get(st) or TERRITORIES.get(st, st)


def attach(odin_doc: dict | None, doc: dict | None = None) -> dict | None:
    """Put the base rate beside each ODIN state (in the month of ODIN's reading). Leaves ODIN unchanged otherwise."""
    if not odin_doc:
        return odin_doc
    at = odin_doc.get("read_at") or ""
    month = int(at[5:7]) if len(at) >= 7 and at[5:7].isdigit() else None
    for key in ("states", "notable_states"):
        for s in odin_doc.get(key) or []:
            s["base_rate"] = base_rate(s["state"], month, doc=doc)
    return odin_doc


def verdict_rule(state: str, start: datetime, end: datetime, line: int = LINE) -> dict:
    """The resolution for a Blackout forecast that EAGLE-I settles, in the exact form Verdict's auto-resolve reads
    (verdict/tools/auto_resolve.py, EAGLEI_RE). Figshare adds a year in the next year's first months (2024 on
    10 Apr 2025, 2025 on 24 Feb 2026), so known_at is a guess Verdict only uses to list it as due; it resolves when
    figshare lists the year, not before."""
    f = "%Y-%m-%d %H:%M"
    rule = (f"Yes if EAGLE-I's outage history (ORNL, figshare doi:{DOI}) shows {state_name(state)} ({state}) with "
            f"more than {line:,} customers out, its counties summed in each 15-minute run, in 4 or more runs with no "
            f"more than 3 runs under the line between them, between {start.strftime(f)} and {end.strftime(f)} UTC. "
            f"No otherwise.")
    year = (end - timedelta(minutes=1)).year
    return {"rule": rule, "source": f"ORNL EAGLE-I outage history, figshare {PAGE} (CC BY 4.0)",
            "known_at": f"{year + 1}-04-30T00:00:00Z", "resolve_by": f"{year + 1}-06-30T00:00:00Z"}


def text_lines(raw: io.BufferedIOBase):
    """Decode a byte stream of the CSV as text lines (UTF-8, BOM allowed)."""
    return io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
