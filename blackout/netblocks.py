"""NetBlocks' own reports of internet disruptions (BO-IND-20), read from its public Bluesky account.

Why (20 Nov 2026): IODA's country signal missed both outages a daily brief led with that week, Pakistan's SMW5 cable
fault (NetBlocks 18 Nov 17:20 UTC) and Ukraine's power-cut declines (NetBlocks 20 Nov 11:36 UTC). Partial and regional
disruptions are what NetBlocks reports and what country-wide signals miss, so its reports are a lane of their own and a
column of the daily series (trends.py).

Source: Bluesky's public AppView, `app.bsky.feed.getAuthorFeed` for `netblocks.org` on public.api.bsky.app, no account
(checked 20 Nov 2026: 1,035 entries with filter posts_and_author_threads, 8 of them reposts of others, 240 replies in
NetBlocks' own threads; the first post 21 Apr 2023; 11 pages of 100 in about 4 s). The whole account is read every run:
it is small, posts are never missed between runs, and every field below is derived fresh from NetBlocks' own words.
Not read: X (no script access; not worked around); netblocks.org/feed (RSS, newest report 26 Aug 2023); Mastodon
@netblocks@mastodon.social, which mirrors the same posts seconds apart and adds only Nov 2022 to Apr 2023.

Terms (netblocks.org, read 20 Nov 2026): the site states "Copyright, all rights reserved"; its report pages say key
findings, facts, figures and original reporting are NetBlocks' and that citation and source attribution are required
at the point of use; it publishes no reuse licence. So Blackout does not republish NetBlocks' posts: it keeps each
post's time and link, the fields it parses from the text (type, countries, regions, causes, platforms, networks, the
level NetBlocks states), and a short excerpt of the post's opening words, quoted and credited to NetBlocks with the link
to the post. The full text is read in memory and never stored or published.

Parsed fields (what NetBlocks writes; the parse is mechanical and can miss a name):
  type        the post's own label: Confirmed, Update, Note, ICYMI (None when the post has none, as in thread posts)
  countries   ISO codes from hashtags (#Ukraine, #Iran's) and country names in the text, never from adjectives
              ("Russian drone attacks" names no country); `country_basis` says which. Crimea is placed in UA (ISO 3166).
  regions     subnational places named with Oblast, Province, Region, Governorate, Krai, State or District, places
              written "in <Place>, #Country", and a short list NetBlocks names often (REGION_WORDS)
  causes      CAUSE_WORDS categories found in the text (power, cable, shutdown, restriction, strike, weather, ...)
  platforms   services named (Telegram, TikTok, X, Yandex, Starlink ...); asns: AS numbers written in the text
  level_pct   the level NetBlocks states ("falling to 67% of ordinary levels" -> 67.0) with `level_basis`
  scope       connectivity (a place's internet), platform (a service restricted or down), or other
Items: posts of the last ITEM_HOURS naming a country, a platform or a network, status_class reported, direction neutral:
NetBlocks' words quoted as NetBlocks', never assessed by Blackout.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
ACTOR = "netblocks.org"
PROFILE = "https://bsky.app/profile/netblocks.org"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"
FILTER = "posts_and_author_threads"   # NetBlocks' "Update" posts are often replies in its own threads
PAGE = 100
MAX_PAGES = 60          # 6,000 posts; a feed that long is listed as a problem rather than read for ever
TRIES = 3
RETRY_PAUSES_S = (5, 15, 30)
ITEM_HOURS = 72         # posts this recent become feed items (Radar's outages use the same 72 h)
CORROBORATION_H = 24    # a NetBlocks post and an IODA or Radar event in one country within this many hours
EXCERPT_CHARS = 110     # the opening words quoted with the link; never the whole post (terms above)
NAME = "netblocks.json"  # data/netblocks.json: every post's parsed fields, link and excerpt
FORMAT = "blackout-netblocks/1"
PARSER = "1"
TERMS = ("NetBlocks' posts: copyright NetBlocks, all rights reserved, no reuse licence published (netblocks.org, read "
         "20 Nov 2026); Blackout keeps the time, link and fields it parses, quotes a short excerpt credited to "
         "NetBlocks with the link, and never republishes a post's full text")

TYPES = ("Confirmed", "Update", "Note", "ICYMI")
_TYPE_RE = re.compile(r"^[^A-Za-z]*([A-Za-z][A-Za-z ]{1,24}?):")
_LEVEL_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%\s+of\s+(ordinary|normal|usual|previous|pre-war)\s+levels", re.I)
_ASN_RE = re.compile(r"\bAS ?(\d{2,7})\b")
_TAG_RE = re.compile(r"#([\wÀ-ɏ']+)")

# Names NetBlocks uses that the kit's Natural Earth names don't hold, or hold differently. Lower case.
ALIASES = {
    "gaza": "PS", "gaza strip": "PS", "west bank": "PS", "palestine": "PS", "crimea": "UA", "occupied crimea": "UA",
    "dr congo": "CD", "drc": "CD", "democratic republic of congo": "CD", "congo-brazzaville": "CG",
    "ivory coast": "CI", "cote d'ivoire": "CI", "côte d'ivoire": "CI", "us": "US", "usa": "US", "united states": "US",
    "uk": "GB", "britain": "GB", "great britain": "GB", "burma": "MM", "türkiye": "TR", "turkiye": "TR",
    "tchad": "TD", "guinée": "GN", "algérie": "DZ", "elsalvador": "SV", "lka": "LK", "eswatini": "SZ",
    "east timor": "TL", "cape verde": "CV", "czech republic": "CZ", "the gambia": "GM", "somaliland": "SO",
    "kashmir": None, "congo": None, "georgia": "GE", "mayotte": "YT", "guadeloupe": "GP", "martinique": "MQ",
    "réunion": "RE", "reunion": "RE", "french guiana": "GF", "canary islands": "ES", "tigray": "ET",
    "abkhazia": "GE", "transnistria": "MD", "nagorno-karabakh": "AZ", "hong kong": "HK", "macau": "MO",
    "u.s. virgin islands": "VI", "st. croix": "VI", "st. thomas": "VI", "puerto rico": "PR",
}
# NetBlocks' standing disclaimer on service outages; its words "restrictions or filtering" are not a cause.
_BOILERPLATE = re.compile(r"(incident|the incident is)?\s*not related to country-level internet[^.;#]*", re.I)
# US states and territories NetBlocks names (hashtags #Florida, #Texas ...). Georgia is left to the country.
US_STATES = ("Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida",
             "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
             "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
             "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
             "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee",
             "Texas", "Utah", "Vermont", "Virginia", "Washington State", "West Virginia", "Wisconsin", "Wyoming")
REGION_WORDS = ("Crimea", "Sevastopol", "Donetsk", "Luhansk", "Zaporizhzhia", "Kherson", "Kharkiv", "Kyiv", "Odesa",
                "Tigray", "Amhara", "Kashmir", "Balochistan", "Khyber Pakhtunkhwa", "Gaza Strip", "West Bank",
                "Rakhine", "Darfur", "Khuzestan", "Sistan and Baluchestan", "Kurdistan", "Abkhazia", "Transnistria",
                "Nagorno-Karabakh", "Canary Islands", "Manipur", "Punjab", "Sindh", "Tehran", "Caracas", "Havana",
                "Casablanca", "La Palma", "Nuuk", "Mayotte")
_REGION_RE = re.compile(r"\b((?:[A-Z][\w'’-]+ ){0,2}[A-Z][\w'’-]+) (Oblasts?|Province|Region|Governorate|Krai|State|"
                        r"District|Territory|Prefecture|Department|Republic)\b")
_IN_PLACE_RE = re.compile(r"\bin ((?:[A-Z][\w'’-]+ ){0,2}[A-Z][\w'’-]+), #?[A-Z]")
CAUSE_WORDS = {
    "power": ("power outage", "power cut", "power failure", "electricity", "energy infrastructure", "energy system",
              "grid", "substation", "power shutdown", "blackout of power", "load shedding", "load-shedding",
              "without power", "power supply", "power plant", "cut power", "loss of power", "power loss",
              "knocked out power"),
    "cable": ("cable", "subsea", "submarine", "fibre cut", "fiber cut", "landing station"),
    "shutdown": ("shutdown", "shut down", "internet cut", "cut off", "switched off", "blackout", "curfew"),
    "restriction": ("restrict", "block", "filtering", "throttl", "censorship"),
    "strike": ("drone", "missile", "airstrike", "air strike", "shelling", "bombard", "attack on", "attacks on",
               "strikes on", "strike on", "military"),
    "weather": ("storm", "hurricane", "typhoon", "cyclone", "flood", "wind", "lightning", "snow", "heatwave",
                "heat wave", "wildfire"),
    "earthquake": ("earthquake", "tsunami", "volcan"),
    "cyberattack": ("cyberattack", "cyber attack", "cyber-attack", "ddos", "hack"),
    "fire": ("fire",),
    "protest": ("protest", "unrest", "demonstration"),
    "election": ("election", "vote", "polling"),
    "exams": ("exam",),
    "technical": ("technical", "maintenance", "software", "configuration"),
}
PLATFORMS = ("Telegram", "TikTok", "Instagram", "Facebook", "WhatsApp", "Twitter", "X", "YouTube", "Signal", "Threads",
             "Messenger", "Snapchat", "Discord", "Reddit", "Wikipedia", "Viber", "Skype", "Zoom", "Google", "Gmail",
             "Apple", "iCloud", "App Store", "Microsoft", "Outlook", "Azure", "AWS", "Amazon", "Cloudflare",
             "Starlink", "Yandex", "VK", "VKontakte", "Kyivstar", "Optus", "Spectrum", "Namecheap", "Meta",
             "ChatGPT", "OpenAI", "Bluesky", "Steam", "PlayStation", "Xbox", "Roblox", "Netflix", "Spotify")
_PLATFORM_RES = [(p, re.compile(r"(?<![\w#])" + re.escape(p) + r"(?![\w])")) for p in PLATFORMS]


def _names() -> dict[str, str | None]:
    """lower-case name -> ISO code, from the kit's country table plus ALIASES and the US states."""
    from tic_doctrine import geo
    out: dict[str, str | None] = {}
    for c in json.loads(Path(geo.FILE).read_text(encoding="utf-8"))["countries"]:
        if c.get("name") and c.get("cc"):
            out.setdefault(c["name"].lower(), c["cc"].upper())
    out["united states of america"] = "US"
    for s in US_STATES:
        out[s.lower()] = "US"
    out.update(ALIASES)
    return out


_NAMES: dict[str, str | None] | None = None
_NAME_RE: re.Pattern | None = None


def _name_table() -> tuple[dict[str, str | None], re.Pattern]:
    global _NAMES, _NAME_RE
    if _NAMES is None:
        _NAMES = _names()
        alts = sorted((n for n in _NAMES if len(n) > 2 or n in ("uk", "us")), key=len, reverse=True)
        # Longest first, so "Papua New Guinea" wins over "Guinea"; a name followed by 'n'/'ian' (an adjective) is not one.
        _NAME_RE = re.compile(r"(?<![\w#])(" + "|".join(re.escape(n) for n in alts) + r")(?!\w)(?!-administered)",
                              re.I)
    return _NAMES, _NAME_RE


def _countries(text: str, tags: list[str]) -> tuple[list[str], dict[str, str]]:
    names, name_re = _name_table()
    found: dict[str, str] = {}
    # NetBlocks hashtags the place affected; when it does, other names in the text are the actor or context ("Russia's
    # strategy of drone attacks" in a #Ukraine post), so text names count only in a post with no country hashtag.
    for t in tags:
        key = re.sub(r"['’]s$", "", t).lower()
        cc = names.get(key)
        if cc is None:  # #ElSalvador, #SriLanka: try with spaces before capitals
            key2 = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"['’]s$", "", t)).lower()
            cc = names.get(key2)
        if cc:
            found.setdefault(cc, "hashtag")
    for m in ([] if found else name_re.finditer(text)):
        word = m.group(1)
        if word.isupper() and len(word) <= 3 and word.lower() not in ("uk", "us", "usa", "drc"):
            continue
        if word in ("US", "us") and not re.search(r"\b(US|U\.S\.)\b", text):
            continue
        cc = names.get(word.lower())
        if cc:
            found.setdefault(cc, "name")
    return list(found), found


def _regions(text: str) -> list[str]:
    out: list[str] = []
    for m in _REGION_RE.finditer(text):
        out.append(f"{m.group(1)} {m.group(2)}")
    names, _ = _name_table()
    for m in _IN_PLACE_RE.finditer(text):
        place = m.group(1)
        if place.lower() not in names and not place.startswith(("Network", "Metrics", "Live")):
            out.append(place)
    for w in REGION_WORDS:
        if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text):
            out.append(w)
    seen, uniq = set(), []
    for r in out:  # "Zhytomyr Oblast" covers "Zhytomyr"; keep the first, longest spelling
        if r not in seen and not any(r != o and r in o for o in out):
            seen.add(r)
            uniq.append(r)
    return uniq


def _causes(text: str) -> list[str]:
    low = _BOILERPLATE.sub(" ", text).lower()
    out = [c for c, words in CAUSE_WORDS.items() if any(w in low for w in words)]
    if "power" in out and "shutdown" in out and not re.search(r"internet (shutdown|cut|blackout)|shut(ting)? down the "
                                                              r"internet|cut off from", low):
        out.remove("shutdown")   # "emergency power shutdowns" is the grid, not the internet
    return out


def _platforms(text: str) -> list[str]:
    out = []
    for p, rx in _PLATFORM_RES:
        if p == "X" and not re.search(r"Twitter/X|\bX \(|, X,|, X and|and X\b|X/Twitter", text):
            continue  # a bare X is too often a letter
        if rx.search(text) and p not in out:
            out.append(p)
    return out


def _excerpt(text: str) -> str:
    body = re.sub(r"\s+", " ", _TYPE_RE.sub("", text, count=1) if _TYPE_RE.match(text) else text).strip()
    body = body.lstrip("⚠️ℹ️📉📈🛜🔌🌀 ").strip()
    if len(body) <= EXCERPT_CHARS:
        cut = body
    else:
        cut = body[:EXCERPT_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + " …"
    return cut


def _z(ts: str | None) -> str | None:
    """A Bluesky timestamp ('2026-11-20T11:36:14.086Z') as the contract's '2026-11-20T11:36:14Z'."""
    if not isinstance(ts, str):
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(entry: dict) -> dict | None:
    """One getAuthorFeed entry -> the post's parsed fields, or None for a repost of someone else's post."""
    if not isinstance(entry, dict) or entry.get("reason"):
        return None
    post = entry.get("post") or {}
    if ((post.get("author") or {}).get("handle")) != ACTOR:
        return None
    rec = post.get("record") or {}
    text = rec.get("text") or ""
    uri = post.get("uri") or ""
    rkey = uri.rsplit("/", 1)[-1]
    if not rkey:
        return None
    tags = [f["features"][0]["tag"] for f in rec.get("facets") or []
            if (f.get("features") or [{}])[0].get("tag")]
    tags += [t for t in _TAG_RE.findall(text) if t not in tags]
    m = _TYPE_RE.match(text)
    label = m.group(1).strip() if m else None
    ccs, basis = _countries(text, tags)
    lv = _LEVEL_RE.search(text)
    platforms = _platforms(text)
    low = text.lower()
    causes = _causes(text)
    if re.search(r"connectivity|internet (service|access|disruption|outage)|telecom|mobile (data|internet)|"
                 r"offline|network data show|blackout", low) and not re.search(r"not related to country-level", low):
        scope = "platform" if platforms and "restriction" in causes and "connectivity" not in low else "connectivity"
    elif platforms:
        scope = "platform"
    else:
        scope = "other"
    reply = rec.get("reply") or {}
    return {
        "id": rkey,
        "uri": uri,
        "url": f"{PROFILE}/post/{rkey}",
        "posted_at": _z(rec.get("createdAt")) or _z(post.get("indexedAt")),
        "type": label if label in TYPES else None,
        "label": label,
        "countries": ccs,
        "country_basis": basis,
        "regions": _regions(text),
        "causes": causes,
        "platforms": platforms,
        "asns": sorted({int(a) for a in _ASN_RE.findall(text)}),
        "level_pct": float(lv.group(1)) if lv else None,
        "level_basis": f"% of {lv.group(2).lower()} levels" if lv else None,
        "scope": scope,
        "in_thread": bool(reply),
        "thread_root": ((reply.get("root") or {}).get("uri") or None) if reply else None,
        "excerpt": _excerpt(text),
        "parser": PARSER,
    }


def _get(url: str, timeout: float = 30, opener=urllib.request.urlopen, sleep=time.sleep) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for i in range(TRIES):
        try:
            with opener(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if (exc.code == 429 or exc.code >= 500) and i + 1 < TRIES:
                sleep(RETRY_PAUSES_S[i])
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i + 1 < TRIES:
                sleep(RETRY_PAUSES_S[i])
                continue
            raise
    raise RuntimeError("unreachable")


def page_url(cursor: str | None = None) -> str:
    q = {"actor": ACTOR, "limit": PAGE, "filter": FILTER}
    if cursor:
        q["cursor"] = cursor
    return f"{API}?{urllib.parse.urlencode(q)}"


def fetch(getter=_get) -> tuple[list[dict], list[dict]]:
    """(every NetBlocks post parsed, newest first; problems). A failed page ends the read and is a problem: the posts
    already read are returned, and `complete` in the document says the account was not read to its first post."""
    posts: list[dict] = []
    problems: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        try:
            body = getter(page_url(cursor))
        except Exception as exc:  # noqa: BLE001 - listed; the lane says it was cut short
            problems.append({"endpoint": "author-feed", "error": f"{type(exc).__name__}: {exc}"[:200]})
            return posts, problems
        feed = body.get("feed") if isinstance(body, dict) else None
        if not isinstance(feed, list):
            problems.append({"endpoint": "author-feed", "error": "no feed list in the response"})
            return posts, problems
        posts += [p for p in (parse(e) for e in feed) if p]
        cursor = body.get("cursor")
        if not cursor or not feed:
            return posts, problems
    problems.append({"endpoint": "author-feed", "error": f"more than {MAX_PAGES * PAGE} entries: read cut short"})
    return posts, problems


def document(posts: list[dict], problems: list[dict], now: datetime, kept: list[dict] | None = None) -> dict:
    """data/netblocks.json: every post parsed (newest first), with posts the archive holds that the account no longer
    shows (deleted by NetBlocks) kept and marked, and monthly counts per country for a baseline since the first post."""
    now = now.astimezone(timezone.utc)
    have = {p["id"] for p in posts}
    gone = [{**r, "in_account": False} for r in kept or [] if r.get("id") not in have]
    allp = sorted([{**p, "in_account": True} for p in posts] + gone, key=lambda p: p.get("posted_at") or "",
                  reverse=True)
    complete = not problems
    monthly: dict[str, dict[str, int]] = {}
    for p in allp:
        if p.get("posted_at") and p.get("scope") == "connectivity":
            for cc in p.get("countries") or []:
                m = monthly.setdefault(cc, {})
                m[p["posted_at"][:7]] = m.get(p["posted_at"][:7], 0) + 1
    first = min((p["posted_at"] for p in allp if p.get("posted_at")), default=None)
    return {
        "format": FORMAT, "mode": "live", "parser": PARSER,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": complete, "problems": list(problems),
        "first_post": first, "posts_count": len(allp),
        "source": f"NetBlocks on Bluesky ({PROFILE}), read with app.bsky.feed.getAuthorFeed, no account",
        "terms": TERMS,
        "method": ("Fields parsed mechanically from NetBlocks' own words: the post's label, countries from hashtags and "
                   "country names (never adjectives), regions named, cause words, services and networks named, and the "
                   "level NetBlocks states. Monthly counts are connectivity posts per country. NetBlocks reports what "
                   "it chooses to post; a month with no post is not a month with no disruption."),
        "monthly_connectivity_posts": {cc: dict(sorted(m.items())) for cc, m in sorted(monthly.items())},
        "posts": allp,
    }


def recent(posts: list[dict], now: datetime, hours: int = ITEM_HOURS) -> list[dict]:
    """Posts of the last `hours` naming a country, a platform or a network: the feed's items."""
    cut = (now.astimezone(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [p for p in posts if (p.get("posted_at") or "") >= cut and p.get("in_account", True)
            and (p.get("countries") or p.get("platforms") or p.get("asns"))]


def daily(posts: list[dict], first: str, last: str) -> dict[str, dict[str, dict]]:
    """{cc: {day: {netblocks_posts, netblocks_connectivity, netblocks_by_type, netblocks_levels, netblocks_ids,
    netblocks_conn_ids}}} for
    posts from day `first` to day `last` inclusive (UTC)."""
    out: dict[str, dict[str, dict]] = {}
    for p in posts:
        day = (p.get("posted_at") or "")[:10]
        if not day or not (first <= day <= last):
            continue
        for cc in p.get("countries") or []:
            r = out.setdefault(cc, {}).setdefault(day, {"netblocks_posts": 0, "netblocks_connectivity": 0,
                                                         "netblocks_by_type": {}, "netblocks_levels": [],
                                                         "netblocks_ids": [], "netblocks_conn_ids": []})
            r["netblocks_posts"] += 1
            if p.get("scope") == "connectivity":
                r["netblocks_connectivity"] += 1
                r["netblocks_conn_ids"].append(p["id"])
            t = p.get("type") or "other"
            r["netblocks_by_type"][t] = r["netblocks_by_type"].get(t, 0) + 1
            if p.get("level_pct") is not None:
                r["netblocks_levels"].append(p["level_pct"])
            r["netblocks_ids"].append(p["id"])
    return out


def snapshot_part(doc: dict | None, now: datetime) -> dict | None:
    """What the snapshot (and so the feed and page) carries: the read's state and the recent posts only."""
    if not doc:
        return None
    return {"read_at": doc["generated_at"], "complete": doc["complete"], "posts_count": doc["posts_count"],
            "first_post": doc["first_post"], "recent": recent(doc["posts"], now),
            "last_30d": [p for p in doc["posts"] if (p.get("posted_at") or "") >=
                         (now.astimezone(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")]}
