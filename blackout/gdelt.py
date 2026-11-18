"""Outages the press reports (BO-WP-7, BO-IND-19): headlines naming a watched US service with an outage word, counted
in GDELT's raw Global Knowledge Graph files against that service's own 7-day usual.

Added 17 Nov 2026, beside the Bluesky lane (`bluesky.py`, BO-IND-18), for the same gap: severe US outages at companies
with no status feed (Chase, Bank of America and Wells Fargo and United Airlines on 16 Nov 2026, the National Weather
Service on 13 Nov) reached no source Blackout read. The press covers them within an hour or two; GDELT reads the
press every 15 minutes.

Source: GDELT 2.0's Global Knowledge Graph (GKG 2.1), one zipped tab-separated file every 15 minutes at
https://data.gdeltproject.org/gdeltv2/<YYYYMMDDHHMMSS>.gkg.csv.zip (about 5-7 MB zipped, about 1,400 articles and
18 MB of text; `lastupdate.txt` names the newest). Not the DOC 2.0 API, which answered HTTP 429 to every request from
here and from WebFetch on 17 Nov 2026 and `{}` for real events. 27 columns, no header; Blackout reads five: 0 record
id, 1 date (YYYYMMDDHHMMSS, UTC), 3 the outlet's domain (SourceCommonName), 4 the article URL (DocumentIdentifier),
26 extras, whose `<PAGE_TITLE>...</PAGE_TITLE>` holds the headline (HTML-escaped, so unescaped first). A file GDELT
skipped answers 404 (it happens); a stamp is listed as missing, not read as zero.
Terms (gdeltproject.org/about.html#termsofuse, read 17 Nov 2026): the data may be used, copied and redistributed
without restriction, citing the GDELT Project. Blackout publishes counts, outlet domains and article links only, never
a headline's text (the outlets' own words), and the headlines it reads stay in memory for the run.

Rule (BO-IND-19). A headline counts for a watched name (`WATCH`, the Bluesky lane's keys with a headline pattern each)
when its title matches that name's pattern and an outage word (`OUTAGE`); each article URL counts once. For each name:
the headlines in every GKG file of the `WINDOW_H` hours to GDELT's newest file, against the same name's headlines per
file over the `BASELINE_DAYS` days before the window, scaled to the window's files. A surge is at least `MIN_HEADLINES`
headlines from at least `MIN_OUTLETS` different outlets and at least `RATIO` times the usual, the usual floored at
`FLOOR` (so a name the press never pairs with an outage word needs `MIN_HEADLINES`, not a ratio over zero).

The usual needs 672 files a run (7 days of 15-minute files, about 4 GB), so each file's matches (outlet and link per
name, no titles) are carried from run to run in their own published file, `data/gdelt-counts.json` (format
`CARRY_FORMAT`), the way EIA-930's hours are: a run reads every window file it doesn't hold, then baseline files it
doesn't hold, newest first, `MAX_FILES` in all, so a first run (no carry) has a partial usual and fills the rest over
the next runs. The snapshot says how many baseline files the usual rests on (`baseline_files`, of `baseline_expected`).

Calibration (17 Nov 2026, `tools/gdelt_calibrate.py`: every GKG file from 00:00 UTC 7 Nov to 20:30 UTC 17 Nov, the
rule replayed at the lane's run times from 9 Nov; `CALIBRATION` holds every name's numbers). Only verified outages
crossed the line: Outlook (12-13 Nov, 372 headlines in a window against a usual of 0.6), the FAA (6 headlines within
40 minutes of the 13 Nov ground stop, 1,487 by morning), the Weather Service (91), the four banks of 16 Nov (Bank of
America 137) and United (565). Every other name's busiest window held 2 headlines or fewer. Missed: Zelle (4), Schwab,
Gmail and Marriott of 17 Nov (1 or none). GDELT's headlines follow Bluesky's posts: the banks crossed in the same run,
United and the Weather Service one run later.

What it is and isn't. A count of what the press published, not confirmed by the company, which may deny it (Google
said on 17 Nov 2026 it saw no Gmail disruption), and Blackout does not assess a cause. The same wire story run by many
outlets counts once per outlet's article; a story about an outage elsewhere that names the company (a bank's app "back
online", a rival "not affected") counts too; that is BO-H-7B. GDELT reads news sites worldwide but not every one, and
not paywalled text; silence here means nothing.
"""
from __future__ import annotations

import html
import io
import re
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BASE = "https://data.gdeltproject.org/gdeltv2/"
LASTUPDATE = BASE + "lastupdate.txt"
PAGE = "https://www.gdeltproject.org/data.html#rawdatafiles"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"

STEP = timedelta(minutes=15)
WINDOW_H = 6
BASELINE_DAYS = 7
MIN_HEADLINES = 5      # a surge needs at least this many headlines in the window ...
MIN_OUTLETS = 3        # ... from at least this many outlets ...
RATIO = 5.0            # ... and this many times the usual ...
FLOOR = 1.0            # ... the usual taken as at least this (per window)
MAX_FILES = 96         # files read in one run: the window's 24, then the baseline, newest first (about 550 MB)
WORKERS = 4
TRIES = 3
RETRY_PAUSES_S = (5, 15, 30)
DEADLINE_S = 240       # the whole read; files not read by then: the window's are a problem, the baseline's wait
MAX_LINKS = 40         # per name per file, carried; more than this in one file is kept as a count only

CARRY_FORMAT = "blackout-gdelt-counts/1"
CARRY_NAME = "gdelt-counts.json"  # published as site/data/gdelt-counts.json, never inside the snapshot
CARRY_KEY = "carry_out"

# Outage words in a headline (case-insensitive). Kept narrow: "down" only in "is/are/was/went ... down" forms (a bare
# "down" is every falling stock), no "crash" (a plane crash names an airline), and "issues"/"problems" only after a word
# that makes them a system's ("technical issues", "system problems").
OUTAGE = re.compile(
    r"\boutages?\b|\b(?:is|are|was|were|goes|go|going|went|been)\s+(?:back\s+)?down\b"
    r"(?!\s*(?:\d|by\b|from\b|to\b|after\b|on\s+the\b|in\s+(?:early|late|pre|after)|so\s+far|this\s+year))|"
    r"\bdown\s+for\s+(?:some|many|"
    r"thousands|hundreds|users|customers)\b|\bnot\s+working\b|\bglitch(?:es|y)?\b|\boffline\b|\bback\s+online\b|"
    r"\b(?:system|systems|systemwide|system-wide|technical|technology|tech|it|computer|service|network|app|website|"
    r"online|login|log-in|communications?|internet|connectivity)\s+(?:issues?|problems?|failures?|disruptions?|"
    r"meltdown|glitch(?:es)?|errors?|trouble)\b|\bdisruptions?\b|\bdisrupted\b|\bground\s+stop\b|"
    r"\b(?:can't|cannot|can not|unable\s+to)\s+(?:log|sign|access)\b|\bservice\s+(?:restored|interruptions?)\b",
    re.I)

# (key, label, headline pattern, case-sensitive). Keys match bluesky.WATCH so the page can show both lanes per name.
# Common words carry their company context (a police "chase", a river "delta", a travel "visa", an economic "outlook").
WATCH = [
    ("chase", "JPMorgan Chase", r"\bJ\.?P\.? ?Morgan Chase\b|\bChase(?:'s)?\s+(?:bank|app|customers|online|banking|"
     r"cards?|mobile|outage|website|accounts?|systems?)\b", False),
    ("bank-of-america", "Bank of America", r"\bBank of America\b|\bBofA\b", False),
    ("wells-fargo", "Wells Fargo", r"\bWells Fargo\b", False),
    ("citi", "Citibank", r"\bCiti(?:bank|group)?\b", True),
    ("capital-one", "Capital One", r"\bCapital One\b", False),
    ("us-bank", "U.S. Bank", r"\bU\.?S\.? Bank\b(?!s)", True),
    ("pnc", "PNC", r"\bPNC\b", True),
    ("truist", "Truist", r"\bTruist\b", False),
    ("navy-federal", "Navy Federal Credit Union", r"\bNavy Federal\b", False),
    ("schwab", "Charles Schwab", r"\bSchwab\b", False),
    ("fidelity", "Fidelity", r"\bFidelity\s+(?:Investments|customers|website|app|outage|accounts?)\b", False),
    ("vanguard", "Vanguard", r"\bVanguard\s+(?:Group|customers|website|app|outage|accounts?)\b", False),
    ("robinhood", "Robinhood", r"\bRobinhood\b", False),
    ("paypal", "PayPal", r"\bPayPal\b", False),
    ("venmo", "Venmo", r"\bVenmo\b", False),
    ("zelle", "Zelle", r"\bZelle\b", False),
    ("cash-app", "Cash App", r"\bCash App\b", False),
    ("visa", "Visa", r"\bVisa(?:'s)?\s+(?:cards?|payments?|network|outage|users|customers|systems?)\b|"
     r"\bVisa and Mastercard\b|\bMastercard and Visa\b", False),
    ("mastercard", "Mastercard", r"\bMastercard\b", False),
    ("united-airlines", "United Airlines", r"\bUnited Airlines\b|\bUnited(?:'s)?\s+(?:flights?|passengers|outage|"
     r"systems?)\b", False),
    ("american-airlines", "American Airlines", r"\bAmerican Airlines\b", False),
    ("delta", "Delta Air Lines", r"\bDelta\s+(?:Air\s*Lines|Airlines|flights?|customers|passengers|outage|systems?)\b",
     False),
    ("southwest", "Southwest Airlines", r"\bSouthwest\s+(?:Airlines|flights?|customers|passengers|outage|systems?)\b",
     False),
    ("jetblue", "JetBlue", r"\bJetBlue\b", False),
    ("alaska-airlines", "Alaska Airlines", r"\bAlaska Airlines\b", False),
    ("marriott", "Marriott", r"\bMarriott\b", False),
    ("hilton", "Hilton", r"\bHilton\s+(?:Hotels?|Honors|app|website|outage|systems?|guests|customers)\b", False),
    ("verizon", "Verizon", r"\bVerizon\b", False),
    ("att", "AT&T", r"\bAT&T\b|\bAT\s*and\s*T\b", False),
    ("t-mobile", "T-Mobile", r"\bT-Mobile\b", False),
    ("comcast", "Comcast", r"\bComcast\b", False),
    ("xfinity", "Xfinity (Comcast)", r"\bXfinity\b", False),
    ("spectrum", "Spectrum (Charter)", r"\bSpectrum\s+(?:internet|outage|customers|cable|TV|service|mobile)\b|"
     r"\bCharter Spectrum\b", False),
    ("starlink", "Starlink", r"\bStarlink\b", False),
    ("gmail", "Gmail (Google)", r"\bGmail\b", False),
    ("outlook", "Outlook (Microsoft)", r"\bMicrosoft Outlook\b|\bOutlook(?:\.com)?\s+(?:email|e-mail|mail|outage|"
     r"users|down|app)\b|\bOutlook and Teams\b|\bTeams and Outlook\b", False),
    ("icloud", "iCloud (Apple)", r"\biCloud\b", False),
    ("aws", "Amazon Web Services", r"\bAWS\b|\bAmazon Web Services\b", True),
    ("cloudflare", "Cloudflare", r"\bCloudflare\b", False),
    ("faa", "Federal Aviation Administration", r"\bFAA\b|\bFederal Aviation Administration\b", True),
    ("nws", "National Weather Service", r"\bNational Weather Service\b|\bWeather Service\b|\bNWS\b", True),
    ("irs", "Internal Revenue Service", r"\bIRS\b|\bInternal Revenue Service\b", True),
    ("social-security", "Social Security Administration", r"\bSocial Security\s+(?:Administration|website|online|"
     r"offices?|systems?|phones?|phone lines)\b|\bSSA\b", True),
    ("911", "911 emergency calls", r"\b911\b", False),
]
_PATTERNS = [(k, lbl, re.compile(p, 0 if cs else re.I)) for k, lbl, p, cs in WATCH]
TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)

# From the 17 Nov 2026 calibration run (tools/gdelt_calibrate.py): every GKG file from 00:00 UTC 7 Nov to 20:30 UTC 17
# Nov 2026 (1,043 files, none unreadable), the rule replayed at each run (newest file :30 past 01, 05, 09, 13, 17 and
# 21 UTC) with at least 2 days of files before it, 9 to 17 Nov: 51 runs a name, 44 names. Per name: its busiest window
# as (headlines, outlets, run, usual), and the runs the rule flags. Every other name's busiest window held 2 headlines
# or fewer. Flagged, all verified: Microsoft's Outlook outage (from the 21:30 run of 12 Nov), the FAA's ground stop
# (6 headlines from 6 outlets within 40 minutes of the 20:54 UTC stop), the National Weather Service (13-14 Nov), Chase,
# Bank of America, Wells Fargo and U.S. Bank (16 Nov, from the 21:30 run, as Bluesky), United (16-17 Nov). Not flagged:
# Zelle on 16-17 Nov (3-4 headlines), Schwab, Gmail and Marriott on 17 Nov (1 or none with these outage words), and
# Apple's iCloud on 17 Nov, whose headlines came in GDELT's files after 19:00 UTC, past the last run replayed. Rules
# of 4 headlines from 2 outlets at 4x, or 3 from 2 at 3x, add only the same events' tails and Zelle.
CALIBRATION: dict = {
    "chase": {"max": (147, 129, "2026-11-17T01:30Z", 0.39), "surges": ["2026-11-16T21:30Z", "2026-11-17T01:30Z", "2026-11-17T05:30Z", "2026-11-17T09:30Z"]},
    "bank-of-america": {"max": (137, 131, "2026-11-17T01:30Z", 0.71), "surges": ["2026-11-16T21:30Z", "2026-11-17T01:30Z"]},
    "wells-fargo": {"max": (129, 127, "2026-11-17T01:30Z", 0.68), "surges": ["2026-11-16T21:30Z", "2026-11-17T01:30Z"]},
    "citi": {"max": (1, 1, "2026-11-12T01:30Z", 0.0), "surges": []},
    "capital-one": {"max": (2, 2, "2026-11-17T01:30Z", 0.0), "surges": []},
    "us-bank": {"max": (87, 87, "2026-11-17T01:30Z", 0.61), "surges": ["2026-11-16T21:30Z", "2026-11-17T01:30Z"]},
    "pnc": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "truist": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "navy-federal": {"max": (1, 1, "2026-11-17T09:30Z", 0.0), "surges": []},
    "schwab": {"max": (1, 1, "2026-11-17T17:30Z", 0.0), "surges": []},
    "fidelity": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "vanguard": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "robinhood": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "paypal": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "venmo": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "zelle": {"max": (4, 4, "2026-11-17T09:30Z", 0.18), "surges": []},
    "cash-app": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "visa": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "mastercard": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "united-airlines": {"max": (565, 334, "2026-11-17T05:30Z", 4.68), "surges": ["2026-11-17T01:30Z", "2026-11-17T05:30Z"]},
    "american-airlines": {"max": (1, 1, "2026-11-17T05:30Z", 0.0), "surges": []},
    "delta": {"max": (1, 1, "2026-11-15T01:30Z", 0.04), "surges": []},
    "southwest": {"max": (1, 1, "2026-11-16T17:30Z", 0.07), "surges": []},
    "jetblue": {"max": (1, 1, "2026-11-14T17:30Z", 0.04), "surges": []},
    "alaska-airlines": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "marriott": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "hilton": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "verizon": {"max": (2, 2, "2026-11-16T01:30Z", 0.0), "surges": []},
    "att": {"max": (1, 1, "2026-11-11T01:30Z", 0.0), "surges": []},
    "t-mobile": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "comcast": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "xfinity": {"max": (1, 1, "2026-11-13T05:30Z", 0.0), "surges": []},
    "spectrum": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "starlink": {"max": (2, 2, "2026-11-16T09:30Z", 0.25), "surges": []},
    "gmail": {"max": (2, 2, "2026-11-13T17:30Z", 0.04), "surges": []},
    "outlook": {"max": (372, 344, "2026-11-13T01:30Z", 0.6), "surges": ["2026-11-12T21:30Z", "2026-11-13T01:30Z", "2026-11-13T05:30Z", "2026-11-13T09:30Z"]},
    "icloud": {"max": (2, 2, "2026-11-11T05:30Z", 0.0), "surges": []},
    "aws": {"max": (2, 2, "2026-11-13T01:30Z", 0.0), "surges": []},
    "cloudflare": {"max": (1, 1, "2026-11-13T13:30Z", 0.0), "surges": []},
    "faa": {"max": (1487, 807, "2026-11-14T05:30Z", 16.2), "surges": ["2026-11-13T21:30Z", "2026-11-14T01:30Z", "2026-11-14T05:30Z", "2026-11-14T09:30Z", "2026-11-14T21:30Z"]},
    "nws": {"max": (91, 88, "2026-11-14T05:30Z", 0.39), "surges": ["2026-11-14T01:30Z", "2026-11-14T05:30Z"]},
    "irs": {"max": (1, 1, "2026-11-17T17:30Z", 0.0), "surges": []},
    "social-security": {"max": (0, 0, "2026-11-17T17:30Z", 0.0), "surges": []},
    "911": {"max": (2, 2, "2026-11-16T01:30Z", 0.21), "surges": []},
}


def stamp(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def parse_stamp(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_url(s: str) -> str:
    return f"{BASE}{s}.gkg.csv.zip"


def stamps(first: datetime, last: datetime) -> list[str]:
    """Every 15-minute stamp from first to last, both included, newest first."""
    out, d = [], last
    while d >= first:
        out.append(stamp(d))
        d -= STEP
    return out


def _get(url: str, timeout: float = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def newest_stamp(body: bytes | str) -> str:
    """The GKG file named in lastupdate.txt ('<size> <md5> <url>' per line)."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    for line in text.splitlines():
        m = re.search(r"/(\d{14})\.gkg\.csv\.zip\s*$", line)
        if m:
            return m.group(1)
    raise ValueError("lastupdate.txt names no GKG file")


def title_of(extras: str) -> str:
    m = TITLE.search(extras or "")
    return html.unescape(html.unescape(m.group(1))).strip() if m else ""


def match(title: str) -> list[str]:
    """The watched names a headline reports an outage at (none without an outage word)."""
    if not title or not OUTAGE.search(title):
        return []
    return [k for k, _, rx in _PATTERNS if rx.search(title)]


def scan(text: str, keep_titles: bool = False) -> dict:
    """{key: [[domain, url], ...]} for one GKG file's text; with keep_titles, [domain, url, title] (calibration only;
    never published). Rows without 27 columns are skipped."""
    out: dict[str, list] = {}
    seen: set = set()
    for row in text.split("\n"):
        if "<PAGE_TITLE>" not in row:
            continue
        cols = row.split("\t")
        if len(cols) < 27:
            continue
        title = title_of(cols[26])
        keys = match(title)
        if not keys:
            continue
        url = cols[4].strip()
        for k in keys:
            if (k, url) in seen:
                continue
            seen.add((k, url))
            out.setdefault(k, []).append([cols[3].strip().lower(), url] + ([title] if keep_titles else []))
    return out


def read_file(body: bytes, keep_titles: bool = False) -> dict:
    z = zipfile.ZipFile(io.BytesIO(body))
    names = [n for n in z.namelist() if n.endswith(".gkg.csv")]
    if not names:
        raise ValueError("no .gkg.csv in the zip")
    return scan(z.read(names[0]).decode("utf-8", "replace"), keep_titles)


class Missing(Exception):
    """GDELT has no file for this stamp (HTTP 404)."""


def fetch_file(s: str, fetch=_get, pause=time.sleep, deadline: float | None = None) -> dict:
    last: Exception | None = None
    for i in range(TRIES):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("read deadline passed")
        try:
            return read_file(fetch(file_url(s)))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise Missing(s) from exc
            last = exc
            if exc.code != 429 and exc.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, OSError, zipfile.BadZipFile) as exc:
            last = exc
        if i + 1 < TRIES:
            pause(RETRY_PAUSES_S[i])
    raise last or RuntimeError("no response")


def surge(n: int, outlets: int, usual: float) -> bool:
    return n >= MIN_HEADLINES and outlets >= MIN_OUTLETS and n >= RATIO * max(usual, FLOOR)


def carry_doc(files: dict, missing: list, now: datetime) -> dict:
    return {"format": CARRY_FORMAT, "mode": "live", "read_at": _iso(now),
            "note": ("Per GKG file: watched names with headlines reporting an outage, as [outlet domain, article link] "
                     "(no headline text) for the newest 6 hours' files, else a count. Blackout's own reduction of "
                     "GDELT's GKG; cite the GDELT Project (https://www.gdeltproject.org/)."),
            "files": files, "missing": sorted(missing)}


def _carried(carry: dict | None) -> tuple[dict, set]:
    if not isinstance(carry, dict) or carry.get("format") != CARRY_FORMAT or carry.get("mode") != "live":
        return {}, set()
    files = {s: v for s, v in (carry.get("files") or {}).items() if isinstance(v, dict)}
    return files, set(carry.get("missing") or [])


def _links(entry) -> list:
    if isinstance(entry, int):
        return []
    return entry if isinstance(entry, list) else (entry or {}).get("links") or []


def _count(entry) -> int:
    if isinstance(entry, int):
        return entry
    return len(entry) if isinstance(entry, list) else int((entry or {}).get("n") or 0)


def _counts_only(found: dict) -> dict:
    """A file past the next run's window needs only its counts: {key: n}."""
    return {k: _count(v) for k, v in found.items()}


def _reduce(found: dict) -> dict:
    """A file's matches as carried: links for up to MAX_LINKS, else {"n": count, "links": first MAX_LINKS}."""
    return {k: (v if len(v) <= MAX_LINKS else {"n": len(v), "links": v[:MAX_LINKS]}) for k, v in found.items()}


def fetch_gdelt(now: datetime | None = None, fetch=_get, pause=time.sleep, carry: dict | None = None,
                watch=None, newest: str | None = None) -> tuple[dict, list[dict]]:
    """Count outage headlines per watched name in GDELT's GKG. Returns (snapshot part with CARRY_KEY, problems).
    A window file that can't be read is a problem (the lane is then failed); a baseline file not read this run waits
    for the next run; a stamp GDELT answers 404 for is listed as missing. `newest` (a stamp) replaces reading
    lastupdate.txt, for replaying a past run."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    keys = [k for k, *_ in (watch or WATCH)]
    labels = {k: lbl for k, lbl, *_ in WATCH}
    newest = parse_stamp(newest or newest_stamp(fetch(LASTUPDATE)))
    if newest > now + STEP:
        raise ValueError(f"GDELT's newest file {stamp(newest)} is after the run time")
    n_window = WINDOW_H * 4
    window = stamps(newest - STEP * (n_window - 1), newest)
    base_last = newest - STEP * n_window
    baseline = stamps(base_last - STEP * (BASELINE_DAYS * 96 - 1), base_last)
    keep = set(window) | set(baseline)
    held, missing = _carried(carry)
    held = {s: v for s, v in held.items() if s in keep}
    missing = {s for s in missing if s in keep}
    todo = [s for s in window if s not in held and s not in missing]
    todo += [s for s in baseline if s not in held and s not in missing][:max(0, MAX_FILES - len(todo))]
    deadline = time.monotonic() + DEADLINE_S
    errors: dict[str, str] = {}
    newly_missing: set = set()

    def run(s):
        try:
            held[s] = _reduce(fetch_file(s, fetch, pause, deadline))
        except Missing:
            newly_missing.add(s)
        except Exception as exc:  # noqa: BLE001 - reported; never read as zero
            errors[s] = f"{type(exc).__name__}: {exc}"[:160]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, todo))
    missing |= newly_missing

    problems = []
    bad_window = [s for s in window if s in errors]
    if bad_window:
        problems.append({"endpoint": "gkg-window",
                         "error": f"{len(bad_window)} of {len(window)} window files not read: "
                                  + "; ".join(f"{s}: {errors[s]}" for s in bad_window[:3])})
    win_read = [s for s in window if s in held]
    base_read = [s for s in baseline if s in held]
    rows = []
    for k in keys:
        links, seen = [], set()
        for s in win_read:
            for d, u in (_links(held[s].get(k)) if k in held[s] else []):
                if u not in seen:
                    seen.add(u)
                    links.append({"domain": d, "url": u, "file": s})
        extra = sum(max(0, _count(held[s].get(k)) - len(_links(held[s].get(k)))) for s in win_read if k in held[s])
        n = len(links) + extra
        base_n = sum(_count(held[s].get(k)) for s in base_read if k in held[s])
        usual = base_n / len(base_read) * len(window) if base_read else 0.0
        outlets = sorted({x["domain"] for x in links})
        hot = surge(n, len(outlets), usual)
        row = {"key": k, "label": labels.get(k, k), "headlines": n, "outlets": len(outlets),
               "baseline_headlines": base_n, "usual": round(usual, 2), "ratio": round(n / max(usual, FLOOR), 1),
               "surge": hot}
        if n:
            per_file = {}
            for x in links:
                per_file[x["file"]] = per_file.get(x["file"], 0) + 1
            if per_file:  # a replay's window files may carry counts only (no links), so no file to name
                row["first_file"] = _iso(parse_stamp(min(per_file)))
                peak = max(sorted(per_file), key=lambda s: per_file[s])
                row["peak_file"] = _iso(parse_stamp(peak))
            row["outlet_domains"] = outlets[:30]
            row["links"] = [{"domain": x["domain"], "url": x["url"]} for x in
                            sorted(links, key=lambda x: x["file"])[:25]]
        rows.append(row)
    rows.sort(key=lambda r: (not r["surge"], -r["ratio"], r["key"]))
    # A file stamped hh:mm holds what GDELT read in the 15 minutes to hh:mm.
    snap = {"window_start": _iso(parse_stamp(window[-1]) - STEP), "window_end": _iso(newest), "window_h": WINDOW_H,
            "newest_file": _iso(newest), "window_files": len(window), "window_read": len(win_read),
            "window_missing": sorted(s for s in window if s in missing),
            "baseline_days": BASELINE_DAYS, "baseline_files": len(base_read), "baseline_expected": len(baseline),
            "files_read_now": len(todo) - len(errors) - len(newly_missing & set(todo)),
            "rule": {"min_headlines": MIN_HEADLINES, "min_outlets": MIN_OUTLETS, "ratio": RATIO, "floor": FLOOR},
            "watched": len(keys), "rows": [r for r in rows if r["headlines"] or r["surge"]],
            "quiet": sorted(r["key"] for r in rows if not r["headlines"]),
            "surges": [r for r in rows if r["surge"]],
            # Links are kept only for files the next run's window can still hold; older files carry their counts.
            CARRY_KEY: carry_doc({s: (v if s in window else _counts_only(v)) for s, v in held.items()},
                                 sorted(missing), now)}
    return snap, problems
