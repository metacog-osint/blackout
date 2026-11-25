"""Outages the public reports (BO-WP-7, BO-IND-18): surges in public Bluesky posts naming a watched US service.

Added 17 Nov 2026. The week of 12-17 Nov 2026 had a run of severe US outages that Blackout caught almost none of,
because most were at companies that publish no status feed: JPMorgan Chase's online banking and transactions and
United Airlines' systems on 16 Nov, the National Weather Service's communications on 13 Nov, Gmail, Marriott, Schwab
and iCloud on 17 Nov. Tom: "It is critical Blackout captures these events." The public says so first, and in public:
this lane counts it.

Source: Bluesky's public AppView search, `app.bsky.feed.searchPosts` on api.bsky.app, read without an account (checked
17 Nov 2026: api.bsky.app answers; public.api.bsky.app answers 403 to search; a second page by `cursor` answers 403
without an account, so only `hitsTotal` and the first page are open). bsky.app's robots.txt allows everything but
/intent/compose and says a handful of concurrent requests should be ok, 429 meaning back off. Blackout reads only the
count, `hitsTotal`, with `limit=1` (the smallest allowed; 0 is refused); it keeps, shows and republishes no post, no
author and no text, so a post its author deletes leaves nothing behind here. Items link Bluesky's own search page for
the same words.

Request (one per term and window):
  GET https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q="Wells Fargo" outage&limit=1&since=<ISO>&until=<ISO>
  -> {"posts": [<one post>], "cursor": "<base64>", "hitsTotal": 192}
The cursor encodes the returned post's time, its author's DID and its record key (base64 of {"s": [<ms>, "did:plc:...",
"<rkey>"]}, checked 17 Nov 2026), so it is read no more than the post: the test fixtures drop both
(tests/make_bluesky_fixtures.py).
`hitsTotal` counts every post matching in the window, not only the page (16 Nov 2026, 19:00-23:00 UTC: 98 posts on the
page, hitsTotal 192; same hours on 9 Nov: 0). Quoted words must appear together; unquoted words anywhere in the post.
OR is not supported ('"wells fargo" (down OR outage)' gave 10 where each term alone gave 192-195), so each term is its
own query.

Rule (BO-IND-18). For each watched name (`WATCH`): the matches for '"<name>" down' plus '"<name>" outage' (a post
with both words counts twice, here and in the baseline alike) in the `WINDOW_H` hours to the run, against the same
two terms over the `BASELINE_DAYS` days before the window, scaled to the window's length. A surge is at least
`MIN_MATCHES` matches and at least `RATIO` times the usual, the usual floored at `FLOOR` (so a name nobody mentions
needs `MIN_MATCHES`, not a ratio over zero). For a surge, the window's hours are each counted too (the same two
terms), to give the hour the posts began and peaked.

Calibration (17 Nov 2026, `tools/bluesky_calibrate.py`: the rule replayed at every scheduled run from 3 to 17 Nov, all
44 names; `CALIBRATION` holds the numbers and the reasoning). No name crossed the line in the quiet week 3-9 Nov. Every
verified outage the public posted about did, with room: the banks and Zelle on 16 Nov (Bank of America 471 posts
against a usual of 1.1), United (1,371 against 0.8), Outlook (3,773 against 3.7), the FAA (289 within an hour of the
ground stop) and the Weather Service (427), iCloud on 10 and 17 Nov, Schwab on 17 Nov. Missed: Marriott on 17 Nov (22
at most). A long outage enters its own usual: Outlook a day on was 1,300 against 381, under the line. A few names
crossed with no outage found (Starlink 12 and 14 Nov, AWS 12-13 Nov, 911 on 14 Nov, Gmail 12-13 Nov); those are the
BO-H-7B cases the items name.

What it is and isn't. An item is a count of what members of the public posted. It is not confirmed by the company,
which may deny it (Bank of America told CBS on 16 Nov 2026 that its systems ran normally; Google said on 17 Nov 2026
it saw no Gmail disruption), and Blackout does not read the posts or assess a cause. A surge can also come from news
about an outage elsewhere, a joke, or the same word in another sense (a sports team's "outage", a stock "down").
Coverage: Bluesky's users, not the US public; a service its users don't use or talk about stays quiet here, so silence
here means nothing.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

API = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
SEARCH_PAGE = "https://bsky.app/search?q={q}"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"

WINDOW_H = 6           # the run looks back this far: a 4-hourly run with some slack, so a late run misses nothing
BASELINE_DAYS = 7      # the usual: the same terms over the 7 days before the window, scaled to the window
MIN_MATCHES = 30       # a surge needs at least this many matches in the window ...
RATIO = 5.0            # ... and this many times the usual ...
FLOOR = 4.0            # ... the usual taken as at least this (per window), so a name nobody mentions isn't a ratio of 0
TERMS = ("down", "outage")
WORKERS = 4            # concurrent requests ("a handful of concurrent requests should be ok", bsky.app/robots.txt)
TRIES = 3
RETRY_PAUSES_S = (5, 15, 30)  # after a 429 or a 5xx; a 4xx other than 429 is not retried
DEADLINE_S = 240       # the whole read; queries not started by then are a problem, not a wait

# Watched names: (key, name searched, label, sector, NCF). Chosen 17 Nov 2026 for the services US daily life runs on
# and that publish no machine-readable status of their own (banks, brokers, payments, airlines, hotels, carriers,
# email, government), plus a few that do, as an independent check on their own pages. The name is searched as a
# quoted phrase, so a short or common word (Delta, Spectrum, Chase) carries a higher usual, which the ratio absorbs.
WATCH = [
    ("chase", "Chase", "JPMorgan Chase", "bank", ["provide_consumer_banking"]),
    ("bank-of-america", "Bank of America", "Bank of America", "bank", ["provide_consumer_banking"]),
    ("wells-fargo", "Wells Fargo", "Wells Fargo", "bank", ["provide_consumer_banking"]),
    ("citi", "Citibank", "Citibank", "bank", ["provide_consumer_banking"]),
    ("capital-one", "Capital One", "Capital One", "bank", ["provide_consumer_banking"]),
    ("us-bank", "US Bank", "U.S. Bank", "bank", ["provide_consumer_banking"]),
    ("pnc", "PNC", "PNC", "bank", ["provide_consumer_banking"]),
    ("truist", "Truist", "Truist", "bank", ["provide_consumer_banking"]),
    ("navy-federal", "Navy Federal", "Navy Federal Credit Union", "bank", ["provide_consumer_banking"]),
    ("schwab", "Schwab", "Charles Schwab", "broker", ["provide_capital_markets"]),
    ("fidelity", "Fidelity", "Fidelity", "broker", ["provide_capital_markets"]),
    ("vanguard", "Vanguard", "Vanguard", "broker", ["provide_capital_markets"]),
    ("robinhood", "Robinhood", "Robinhood", "broker", ["provide_capital_markets"]),
    ("paypal", "PayPal", "PayPal", "payments", ["provide_payment_clearing_settlement"]),
    ("venmo", "Venmo", "Venmo", "payments", ["provide_payment_clearing_settlement"]),
    ("zelle", "Zelle", "Zelle", "payments", ["provide_payment_clearing_settlement"]),
    ("cash-app", "Cash App", "Cash App", "payments", ["provide_payment_clearing_settlement"]),
    ("visa", "Visa", "Visa", "payments", ["provide_payment_clearing_settlement"]),
    ("mastercard", "Mastercard", "Mastercard", "payments", ["provide_payment_clearing_settlement"]),
    ("united-airlines", "United Airlines", "United Airlines", "airline", ["transport_by_air"]),
    ("american-airlines", "American Airlines", "American Airlines", "airline", ["transport_by_air"]),
    ("delta", "Delta", "Delta Air Lines", "airline", ["transport_by_air"]),
    ("southwest", "Southwest", "Southwest Airlines", "airline", ["transport_by_air"]),
    ("jetblue", "JetBlue", "JetBlue", "airline", ["transport_by_air"]),
    ("alaska-airlines", "Alaska Airlines", "Alaska Airlines", "airline", ["transport_by_air"]),
    ("marriott", "Marriott", "Marriott", "hotel", ["provide_internet_content_services"]),
    ("hilton", "Hilton", "Hilton", "hotel", ["provide_internet_content_services"]),
    ("verizon", "Verizon", "Verizon", "carrier", ["provide_wireless_access", "provide_wireline_access"]),
    ("att", "AT&T", "AT&T", "carrier", ["provide_wireless_access", "provide_wireline_access"]),
    ("t-mobile", "T-Mobile", "T-Mobile", "carrier", ["provide_wireless_access"]),
    ("comcast", "Comcast", "Comcast", "carrier", ["provide_cable_access_network"]),
    ("xfinity", "Xfinity", "Xfinity (Comcast)", "carrier", ["provide_cable_access_network"]),
    ("spectrum", "Spectrum", "Spectrum (Charter)", "carrier", ["provide_cable_access_network"]),
    ("starlink", "Starlink", "Starlink", "carrier", ["provide_satellite_access"]),
    ("gmail", "Gmail", "Gmail (Google)", "email", ["provide_internet_content_services"]),
    ("outlook", "Outlook", "Outlook (Microsoft)", "email", ["provide_internet_content_services"]),
    ("icloud", "iCloud", "iCloud (Apple)", "email", ["provide_internet_content_services", "provide_identity_management"]),
    ("aws", "AWS", "Amazon Web Services", "cloud", ["provide_internet_content_services"]),
    ("cloudflare", "Cloudflare", "Cloudflare", "cloud", ["provide_internet_content_services", "provide_internet_routing"]),
    ("faa", "FAA", "Federal Aviation Administration", "government", ["transport_by_air"]),
    ("nws", "Weather Service", "National Weather Service", "government", ["prepare_for_manage_emergencies"]),
    ("irs", "IRS", "Internal Revenue Service", "government", ["operate_government"]),
    ("social-security", "Social Security", "Social Security Administration", "government", ["operate_government"]),
    ("911", "911", "911 emergency calls", "emergency", ["provide_public_safety"]),
]
# Names with common other senses: how many of a one-day sample of '"<name>" down' posts were about the service (a
# post naming the service's own business: banking or the app, a website or login, e-mail). Read once by hand in the
# 23 Nov 2026 database review (ideas#225, finding F7), on 22 Nov, a day with no outage at any of them; none of the
# posts is kept. An outage day runs higher. The items say so rather than change the searches the 17 Nov calibration
# was run on (a query change resets every usual).
PRECISION = {
    "chase": {"day": "2026-11-22", "read": 49, "about": 8, "meaning": "banking or the Chase app"},
    "social-security": {"day": "2026-11-22", "read": 37, "about": 3, "meaning": "the agency's website or login"},
    "outlook": {"day": "2026-11-22", "read": 22, "about": 9, "meaning": "e-mail"},
}


def precision_text(key: str, name: str) -> str:
    """The sentence an item carries for a name in PRECISION, else ''."""
    s = PRECISION.get(key)
    if not s:
        return ""
    return (f" Many posts use \"{name}\" in another sense: of {s['read']} '\"{name}\" down' posts read on {s['day']}, "
            f"a day with no outage, {s['about']} were about {s['meaning']}. That share fills the usual, so only a "
            f"large outage crosses the line here.")


SECTORS = {"bank": "bank", "broker": "brokerage", "payments": "payments", "airline": "airline", "hotel": "hotel",
           "carrier": "phone or internet carrier", "email": "email", "cloud": "cloud platform",
           "government": "government agency", "emergency": "emergency service"}

# From the 17 Nov 2026 calibration run (tools/bluesky_calibrate.py): the rule replayed at every scheduled run (:45 past
# 01, 05, 09, 13, 17 and 21 UTC) from 3 to 17 Nov 2026, all 44 names, 3,960 windows, both terms. Per name: the largest
# 6-hour window in the quiet week 3-9 Nov and after it, each as (matches, run, usual), and the runs the rule flags.
# The quiet week's usual is its own mean (in-sample); later runs use the 7 days before, as the lane does. The run of
# 21:45 UTC 17 Nov was read at about 20:30 UTC, so its window is short of its last 75 minutes.
# Result: nothing flagged in the quiet week. Flagged after it: the banks of 16 Nov (Chase, Bank of America, Wells Fargo,
# U.S. Bank, Truist, Zelle, Capital One; CNN, Fortune), United (16 Nov), Microsoft's Outlook outage (12-13 Nov), the
# FAA's ground stop and the Weather Service (13 Nov), iCloud (10 Nov, the outage Apple's own page showed, and 17 Nov),
# Schwab (17 Nov, which Schwab said it resolved), Gmail (17 Nov: Downdetector over 2,800 by noon ET, Google said no
# disruption; and 12-13 Nov, during Microsoft's outage, cause not found), and, cause not found, Starlink (12 and 14 Nov),
# AWS (12-13 Nov, during Microsoft's outage) and 911 (14 Nov 01:45, the evening of the FAA and NWS outages).
# MIN_MATCHES 40 would have dropped iCloud of 10 Nov and Capital One, and caught Schwab 4 hours later; 25 adds Visa (27,
# 17 Nov) and nothing verified.
CALIBRATION: dict = {
    "chase": {"quiet_max": (30, "2026-11-05T21:45Z", 16.02), "after_max": (703, "2026-11-16T21:45Z", 15.95), "surges": ["2026-11-16T21:45Z", "2026-11-17T01:45Z"]},
    "bank-of-america": {"quiet_max": (6, "2026-11-06T09:45Z", 0.98), "after_max": (471, "2026-11-16T21:45Z", 1.07), "surges": ["2026-11-16T21:45Z", "2026-11-17T01:45Z"]},
    "wells-fargo": {"quiet_max": (3, "2026-11-08T05:45Z", 0.45), "after_max": (378, "2026-11-16T21:45Z", 0.57), "surges": ["2026-11-16T21:45Z", "2026-11-17T01:45Z"]},
    "citi": {"quiet_max": (8, "2026-11-06T01:45Z", 0.71), "after_max": (6, "2026-11-16T21:45Z", 0.02), "surges": []},
    "capital-one": {"quiet_max": (4, "2026-11-03T01:45Z", 0.81), "after_max": (34, "2026-11-17T01:45Z", 0.79), "surges": ["2026-11-17T01:45Z"]},
    "us-bank": {"quiet_max": (1, "2026-11-04T01:45Z", 0.07), "after_max": (117, "2026-11-16T21:45Z", 0.12), "surges": ["2026-11-16T21:45Z", "2026-11-17T01:45Z"]},
    "pnc": {"quiet_max": (4, "2026-11-03T01:45Z", 0.4), "after_max": (12, "2026-11-16T21:45Z", 0.21), "surges": []},
    "truist": {"quiet_max": (1, "2026-11-03T17:45Z", 0.1), "after_max": (49, "2026-11-16T21:45Z", 0.14), "surges": ["2026-11-16T21:45Z", "2026-11-17T01:45Z"]},
    "navy-federal": {"quiet_max": (2, "2026-11-06T05:45Z", 0.19), "after_max": (10, "2026-11-17T09:45Z", 0.45), "surges": []},
    "schwab": {"quiet_max": (3, "2026-11-06T17:45Z", 0.4), "after_max": (63, "2026-11-17T21:45Z", 1.33), "surges": ["2026-11-17T17:45Z", "2026-11-17T21:45Z"]},
    "fidelity": {"quiet_max": (4, "2026-11-04T17:45Z", 0.62), "after_max": (4, "2026-11-12T17:45Z", 0.71), "surges": []},
    "vanguard": {"quiet_max": (5, "2026-11-05T21:45Z", 0.62), "after_max": (3, "2026-11-10T01:45Z", 0.62), "surges": []},
    "robinhood": {"quiet_max": (3, "2026-11-05T21:45Z", 0.6), "after_max": (8, "2026-11-16T17:45Z", 0.5), "surges": []},
    "paypal": {"quiet_max": (5, "2026-11-06T17:45Z", 1.33), "after_max": (8, "2026-11-17T21:45Z", 2.31), "surges": []},
    "venmo": {"quiet_max": (3, "2026-11-06T01:45Z", 0.43), "after_max": (6, "2026-11-17T01:45Z", 0.69), "surges": []},
    "zelle": {"quiet_max": (2, "2026-11-06T09:45Z", 0.21), "after_max": (169, "2026-11-16T21:45Z", 0.29), "surges": ["2026-11-16T21:45Z", "2026-11-17T01:45Z"]},
    "cash-app": {"quiet_max": (2, "2026-11-03T05:45Z", 0.31), "after_max": (2, "2026-11-17T01:45Z", 0.21), "surges": []},
    "visa": {"quiet_max": (7, "2026-11-06T21:45Z", 2.76), "after_max": (27, "2026-11-17T01:45Z", 2.62), "surges": []},
    "mastercard": {"quiet_max": (4, "2026-11-03T21:45Z", 0.98), "after_max": (3, "2026-11-16T05:45Z", 0.26), "surges": []},
    "united-airlines": {"quiet_max": (2, "2026-11-04T21:45Z", 0.24), "after_max": (1371, "2026-11-17T01:45Z", 0.81), "surges": ["2026-11-17T01:45Z", "2026-11-17T05:45Z"]},
    "american-airlines": {"quiet_max": (1, "2026-11-03T01:45Z", 0.05), "after_max": (18, "2026-11-17T01:45Z", 0.45), "surges": []},
    "delta": {"quiet_max": (12, "2026-11-04T17:45Z", 4.48), "after_max": (18, "2026-11-14T01:45Z", 4.83), "surges": []},
    "southwest": {"quiet_max": (14, "2026-11-08T09:45Z", 3.86), "after_max": (16, "2026-11-17T01:45Z", 4.64), "surges": []},
    "jetblue": {"quiet_max": (2, "2026-11-05T17:45Z", 0.26), "after_max": (2, "2026-11-13T01:45Z", 0.19), "surges": []},
    "alaska-airlines": {"quiet_max": (1, "2026-11-03T17:45Z", 0.1), "after_max": (1, "2026-11-10T21:45Z", 0.07), "surges": []},
    "marriott": {"quiet_max": (2, "2026-11-03T21:45Z", 0.29), "after_max": (22, "2026-11-17T21:45Z", 0.55), "surges": []},
    "hilton": {"quiet_max": (5, "2026-11-05T21:45Z", 0.86), "after_max": (3, "2026-11-16T17:45Z", 0.55), "surges": []},
    "verizon": {"quiet_max": (4, "2026-11-03T01:45Z", 0.67), "after_max": (7, "2026-11-14T01:45Z", 0.88), "surges": []},
    "att": {"quiet_max": (8, "2026-11-03T01:45Z", 1.69), "after_max": (9, "2026-11-10T09:45Z", 1.6), "surges": []},
    "t-mobile": {"quiet_max": (5, "2026-11-06T21:45Z", 1.05), "after_max": (4, "2026-11-13T05:45Z", 0.83), "surges": []},
    "comcast": {"quiet_max": (3, "2026-11-06T21:45Z", 0.4), "after_max": (3, "2026-11-11T13:45Z", 0.5), "surges": []},
    "xfinity": {"quiet_max": (4, "2026-11-05T21:45Z", 0.83), "after_max": (5, "2026-11-17T01:45Z", 0.57), "surges": []},
    "spectrum": {"quiet_max": (13, "2026-11-06T21:45Z", 4.93), "after_max": (14, "2026-11-17T21:45Z", 4.95), "surges": []},
    "starlink": {"quiet_max": (26, "2026-11-04T09:45Z", 7.64), "after_max": (68, "2026-11-14T01:45Z", 8.98), "surges": ["2026-11-12T17:45Z", "2026-11-12T21:45Z", "2026-11-14T01:45Z"]},
    "gmail": {"quiet_max": (4, "2026-11-03T17:45Z", 0.64), "after_max": (72, "2026-11-13T13:45Z", 4.69), "surges": ["2026-11-12T21:45Z", "2026-11-13T01:45Z", "2026-11-13T05:45Z", "2026-11-13T09:45Z", "2026-11-13T13:45Z", "2026-11-13T17:45Z", "2026-11-17T21:45Z"]},
    "outlook": {"quiet_max": (12, "2026-11-03T13:45Z", 4.12), "after_max": (3773, "2026-11-12T21:45Z", 3.67), "surges": ["2026-11-12T21:45Z", "2026-11-13T01:45Z", "2026-11-13T05:45Z", "2026-11-13T09:45Z", "2026-11-13T13:45Z", "2026-11-13T17:45Z"]},
    "icloud": {"quiet_max": (2, "2026-11-06T05:45Z", 0.29), "after_max": (527, "2026-11-17T21:45Z", 2.48), "surges": ["2026-11-11T01:45Z", "2026-11-17T21:45Z"]},
    "aws": {"quiet_max": (16, "2026-11-07T01:45Z", 3.17), "after_max": (48, "2026-11-12T21:45Z", 4.1), "surges": ["2026-11-12T21:45Z", "2026-11-13T01:45Z", "2026-11-13T13:45Z"]},
    "cloudflare": {"quiet_max": (14, "2026-11-09T17:45Z", 3.98), "after_max": (19, "2026-11-17T01:45Z", 5.26), "surges": []},
    "faa": {"quiet_max": (9, "2026-11-07T01:45Z", 0.95), "after_max": (2333, "2026-11-14T01:45Z", 7.5), "surges": ["2026-11-13T21:45Z", "2026-11-14T01:45Z", "2026-11-14T05:45Z"]},
    "nws": {"quiet_max": (7, "2026-11-03T01:45Z", 0.95), "after_max": (427, "2026-11-14T01:45Z", 1.31), "surges": ["2026-11-14T01:45Z", "2026-11-14T05:45Z"]},
    "irs": {"quiet_max": (3, "2026-11-04T09:45Z", 1.14), "after_max": (7, "2026-11-17T17:45Z", 1.02), "surges": []},
    "social-security": {"quiet_max": (9, "2026-11-04T17:45Z", 3.43), "after_max": (10, "2026-11-10T21:45Z", 3.45), "surges": []},
    "911": {"quiet_max": (23, "2026-11-05T21:45Z", 4.45), "after_max": (52, "2026-11-14T01:45Z", 3.21), "surges": ["2026-11-14T01:45Z", "2026-11-14T05:45Z"]},
}


def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def query(name: str, term: str) -> str:
    return f'"{name}" {term}'


def url_for(q: str, since: datetime, until: datetime) -> str:
    return API + "?" + urllib.parse.urlencode({"q": q, "limit": 1, "since": _utc(since), "until": _utc(until)})


def search_link(name: str) -> str:
    """Bluesky's own search page for the outage words, for a reader to see the posts themselves."""
    return SEARCH_PAGE.format(q=urllib.parse.quote(query(name, "outage")))


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def hits(body: bytes | str) -> int:
    """hitsTotal from a searchPosts response. A response without it is an error, never read as 0."""
    d = json.loads(body)
    n = d.get("hitsTotal")
    if not isinstance(n, int) or n < 0:
        raise ValueError("no hitsTotal in the response")
    return n


def _count(url: str, fetch, pause, deadline: float) -> int:
    last: Exception | None = None
    for i in range(TRIES):
        if time.monotonic() > deadline:
            raise TimeoutError("read deadline passed")
        try:
            return hits(fetch(url))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429 and exc.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if i + 1 < TRIES:
            pause(RETRY_PAUSES_S[i])
    raise last or RuntimeError("no response")


def surge(window: int, baseline_total: int, baseline_windows: float) -> tuple[bool, float]:
    """(is a surge, the usual per window)."""
    usual = baseline_total / baseline_windows if baseline_windows else 0.0
    return window >= MIN_MATCHES and window >= RATIO * max(usual, FLOOR), usual


def fetch_bluesky(now: datetime | None = None, fetch=_get, pause=time.sleep, watch=None) -> tuple[dict, list[dict]]:
    """Count the watch list's outage words on Bluesky. Returns (snapshot part, problems). A name whose counts could not
    all be read is left out of the surges and listed as a problem (the lane is then marked failed)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    watch = watch or WATCH
    end = now
    start = end - timedelta(hours=WINDOW_H)
    base_start = start - timedelta(days=BASELINE_DAYS)
    baseline_windows = BASELINE_DAYS * 24 / WINDOW_H
    deadline = time.monotonic() + DEADLINE_S

    jobs = {}
    for key, name, *_ in watch:
        for term in TERMS:
            q = query(name, term)
            jobs[(key, term, "window")] = url_for(q, start, end)
            jobs[(key, term, "baseline")] = url_for(q, base_start, start)
    counts: dict = {}
    errors: dict = {}

    def run(item):
        k, url = item
        try:
            counts[k] = _count(url, fetch, pause, deadline)
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none is read as zero
            errors[k] = f"{type(exc).__name__}: {exc}"[:160]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, jobs.items()))

    rows, problems = [], []
    for key, name, label, sector, ncf in watch:
        bad = [f"{t} {w}: {errors[(key, t, w)]}" for t in TERMS for w in ("window", "baseline") if (key, t, w) in errors]
        if bad:
            problems.append({"endpoint": key, "error": "; ".join(bad)[:300]})
            continue
        window = sum(counts[(key, t, "window")] for t in TERMS)
        base = sum(counts[(key, t, "baseline")] for t in TERMS)
        hot, usual = surge(window, base, baseline_windows)
        rows.append({"key": key, "name": name, "label": label, "sector": sector, "ncf": ncf,
                     "terms": {t: counts[(key, t, "window")] for t in TERMS},
                     "matches": window, "baseline_matches": base, "usual": round(usual, 1),
                     "ratio": round(window / max(usual, FLOOR), 1), "surge": hot,
                     "link": search_link(name)})

    # For each surge, the window's hours: when the posts began and peaked.
    for r in rows:
        if not r["surge"]:
            continue
        hours = []
        for h in range(WINDOW_H):
            a = start + timedelta(hours=h)
            b = a + timedelta(hours=1)
            try:
                n = sum(_count(url_for(query(r["name"], t), a, b), fetch, pause, deadline) for t in TERMS)
            except Exception as exc:  # noqa: BLE001
                problems.append({"endpoint": f"{r['key']}-hours", "error": f"{type(exc).__name__}: {exc}"[:200]})
                hours = []
                break
            hours.append({"hour": _utc(a), "matches": n})
        r["hours"] = hours
        line = max(MIN_MATCHES / WINDOW_H, RATIO * max(r["usual"], FLOOR) / WINDOW_H)
        over = [x for x in hours if x["matches"] >= line]
        r["first_hour"] = over[0]["hour"] if over else None
        r["peak_hour"] = max(hours, key=lambda x: x["matches"])["hour"] if hours else None

    rows.sort(key=lambda r: (not r["surge"], -r["ratio"], r["key"]))
    snap = {"window_start": _utc(start), "window_end": _utc(end), "window_h": WINDOW_H,
            "baseline_start": _utc(base_start), "baseline_days": BASELINE_DAYS,
            "rule": {"min_matches": MIN_MATCHES, "ratio": RATIO, "floor": FLOOR, "terms": list(TERMS)},
            "read": len(rows), "watched": len(watch), "rows": rows,
            "surges": [r for r in rows if r["surge"]]}
    return snap, problems
