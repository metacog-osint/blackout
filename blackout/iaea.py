"""Zaporizhzhya NPP off-site power (BO-IND-13) from the IAEA's numbered Ukraine updates ("Update N – IAEA Director
General Statement on Situation in Ukraine"), reported by the IAEA and quoted, never assessed.

Where it is read (checked 13 Nov 2026):
  - The IAEA's Top News RSS (www.iaea.org/feeds/topnews) lists the updates as they come out, with their numbers.
  - iaea.org answers 403 to scripts on the update pages themselves (its robots.txt doesn't bar them; the refusal is
    the site's bot screen, and Blackout doesn't try to get round it). The Wayback Machine captures each update within
    hours (Update 374 of 6 Nov 2026 was captured at 16:54 UTC the same day), so the text is read from the Wayback
    copy: the CDX index (captures of www.iaea.org/newscenter/pressreleases/update-* in the last 60 days) gives the
    newest numbers and their first capture, and the raw capture (`id_`) gives the text, gzip as archived.
  - Terms: "IAEA content ... may be adapted, translated, copied, printed and downloaded ... for use in commercial and
    non-commercial products or services, provided that appropriate acknowledgement of the IAEA as the source is given
    and that the IAEA's endorsement of users' views, products or services is not stated or implied in any way"
    (iaea.org/about/terms-of-use, Wayback copy of 17 Oct 2026, read 13 Nov 2026).

The updates are prose, so the state is read conservatively from sentences that name the ZNPP or Zaporizhzhya, and
the sentences it rests on are quoted: `none` (it "remains without off-site power", "has been without off-site power
since"), `single` ("single", "sole" or "only" external, off-site or back-up line; "only one line available"),
`not_stated` otherwise. A sentence saying the plant "lost" its line is a loss report, quoted separately, never a
statement of the current state. Checked against Updates 350-374 (14 May - 6 Nov 2026) as archived (23 of the 25; 368 and
369 would not download from the Wayback Machine that night): 363, 364 and 365 (20 Aug - 5 Sep, during the loss of off-site power that lasted from 20 Aug to 7 Sep) read `none`; 350, 353,
354, 356, 358, 362, 367 and 371-374 read `single`; the other nine say nothing current about it and read
`not_stated`, which is never taken as calm (the newest update that does state it is read too). The numbering lets a missing update be noticed: the IAEA
issued them about weekly through 2026, so a newest update older than STALE_DAYS is said to be overdue.
"""
from __future__ import annotations

import gzip
import html
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

RSS_URL = "https://www.iaea.org/feeds/topnews"
CDX = ("https://web.archive.org/cdx/search/cdx?url=www.iaea.org/newscenter/pressreleases/update-&matchType=prefix"
       "&output=json&fl=timestamp,original,statuscode&filter=statuscode:200&collapse=urlkey&from={since}")
WAYBACK = "https://web.archive.org/web/{timestamp}id_/{url}"
TERMS = ("IAEA content: reuse allowed with acknowledgement of the IAEA as the source and no IAEA endorsement stated or "
         "implied (iaea.org/about/terms-of-use, read 13 Nov 2026)")
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"
ZNPP = {"name": "Zaporizhzhya Nuclear Power Plant", "lat": 47.5117, "lon": 34.5858}  # Enerhodar, Ukraine
STALE_DAYS = 10
MAX_READS = 4
LOOKBACK_DAYS = 60
TITLE_RE = re.compile(r"Update (\d+)\s*[–-]\s*IAEA Director General Statement on (?:the )?(?:Situation in )?Ukraine",
                      re.I)
URL_RE = re.compile(r"/newscenter/pressreleases/update-(\d+)-iaea-director-general-statement-on-[a-z-]*ukraine/?$")
DATELINE_RE = re.compile(r"^\s*(?:\d{1,2} [A-Z][a-z]+ \d{4}\s+)?(?:[A-Z][A-Za-z ,]{2,40}?\s+)?\d{1,3}/\d{4}\s+")
DATE_RE = re.compile(r"\b(\d{1,2}) (January|February|March|April|May|June|July|August|September|October|November|"
                     r"December) (20\d\d)\b")
PLANT_RE = re.compile(r"\bZNPP\b|Zaporizh", re.I)
NONE_RE = re.compile(r"\b(?:remains|is|is still|currently) without (?:any )?off-?site power"
                     r"|\bhas (?:now )?been without (?:any )?off-?site power (?:since|for)"
                     r"|\bdiesel generators (?:are|remain) currently (?:in operation|operating|running|supplying)", re.I)
LOST_RE = re.compile(r"\blost (?:all off-?site power|(?:the )?connection to (?:its|the) (?:only|sole|single)\b)"
                     r"|\b(?:experienced|suffered) (?:its|the|a) \d{1,3}(?:st|nd|rd|th) (?:complete )?loss of off-?site power",
                     re.I)
LOST_VERB_RE = re.compile(r"\blost\b|\blos(?:e|es|ing)\b", re.I)
SINGLE_RE = re.compile(r"\b(?:single|sole|only)\b[^.;]{0,40}?\b(?:off-?site|external|back-?up|backup)\b[^.;]{0,30}?"
                       r"\bline\b|\bonly one line available\b", re.I)
LOOP_RE = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th) (?:complete )?(?:loss of off-?site power|LOOP\b|off-?site power "
                     r"outage)", re.I)


def _get(url: str, timeout: float = 60, retries: int = 2, wait: float = 10) -> bytes:
    """GET with a short wait and retry on 429 or 503: the Wayback Machine sheds load that way (it answered 503 to
    three CDX queries in a row on 13 Nov 2026, then answered normally)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(5_000_000)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == retries:
                raise
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _text(raw: bytes) -> str:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    t = raw.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", t)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))).strip()


def parse_rss(raw: bytes) -> list[dict]:
    """Numbered Ukraine updates in the IAEA Top News RSS: [{number, title, link}] (no DTDs or entities expanded)."""
    text = raw.decode("utf-8", "replace")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("feed carries a DTD; refused")
    out = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", text):
        title = html.unescape(re.sub(r"\s+", " ", (re.search(r"(?s)<title>(.*?)</title>", item) or [None, ""])[1]))
        link = (re.search(r"(?s)<link>(.*?)</link>", item) or [None, ""])[1].strip().replace("http://", "https://")
        m = TITLE_RE.search(title)
        if m:
            out.append({"number": int(m.group(1)), "title": title.strip(), "link": link})
    return out


def parse_cdx(rows) -> dict[int, dict]:
    """{number: {timestamp, url}} from the CDX index, first capture per update."""
    out: dict[int, dict] = {}
    if not isinstance(rows, list):
        raise ValueError("CDX did not answer a list")
    for r in rows[1:]:
        if not isinstance(r, list) or len(r) < 2:
            continue
        ts, url = str(r[0]), str(r[1]).split("?", 1)[0]
        m = URL_RE.search(url)
        if m and re.fullmatch(r"\d{14}", ts):
            n = int(m.group(1))
            if n not in out or ts < out[n]["timestamp"]:
                out[n] = {"timestamp": ts, "url": url.replace("http://", "https://")}
    return out


def _date(body: str, number: int) -> str | None:
    """The update's own date: the first date after its title in the page text."""
    starts = [m.end() for m in TITLE_RE.finditer(body) if int(m.group(1)) == number]
    for pos in starts:
        m = DATE_RE.search(body, pos, pos + 120)
        if m:
            return datetime.strptime(" ".join(m.groups()), "%d %B %Y").strftime("%Y-%m-%d")
    return None


def parse_update(raw: bytes, number: int) -> dict:
    """Read one update: its date, the ZNPP off-site power state and the sentences it rests on, and any count of
    complete losses of off-site power the IAEA gives."""
    body = _text(raw)
    if not any(int(m.group(1)) == number for m in TITLE_RE.finditer(body)):
        raise ValueError(f"page is not Update {number}")
    starts = [m.end() for m in TITLE_RE.finditer(body) if int(m.group(1)) == number]
    article = body[starts[-1]:] if starts else body
    article = DATELINE_RE.sub("", article.strip(), count=1)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?”\"])\s+", article) if 20 <= len(s.strip()) <= 700]
    near = set()  # sentences naming the plant, and the two after each (they often go on with "the plant")
    for i, s in enumerate(sentences):
        if PLANT_RE.search(s):
            near.update(range(i, min(i + 3, len(sentences))))
    plant = [s for s in sentences if PLANT_RE.search(s)]
    context = [sentences[i] for i in sorted(near)]
    none = [s for s in context if NONE_RE.search(s)]
    single = [s for s in plant if SINGLE_RE.search(s) and not LOST_VERB_RE.search(s)]
    state, basis = ("none", none) if none else ("single", single) if single else ("not_stated", [])
    losses = [s for s in context if LOST_RE.search(s)]
    loops = [int(m.group(1)) for s in context for m in LOOP_RE.finditer(s)]
    return {"number": number, "date": _date(body, number), "state": state, "quotes": basis[:3],
            "losses": losses[:2], "loop_count": max(loops) if loops else None}


def fetch_znpp(fetch=_get, now: datetime | None = None) -> tuple[dict | None, list[dict]]:
    """The newest readable update and what it says about the ZNPP's off-site power, plus what is known of newer
    ones. (None, problems) when nothing could be read."""
    now = now or datetime.now(timezone.utc)
    problems: list[dict] = []
    rss: list[dict] = []
    try:
        rss = parse_rss(fetch(RSS_URL))
    except Exception as exc:
        problems.append({"endpoint": "iaea-rss", "error": f"{type(exc).__name__}: {exc}"[:200]})
    since = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    try:
        caps = parse_cdx(json.loads(fetch(CDX.format(since=since))))
    except Exception as exc:
        problems.append({"endpoint": "wayback-cdx", "error": f"{type(exc).__name__}: {exc}"[:200]})
        caps = {}
    newest = max([u["number"] for u in rss] + list(caps), default=None)
    if newest is None:
        return None, problems
    reading = None
    # Newest archived first. A capture that won't read falls back to the one before; an update that says nothing
    # current about the plant's power is kept, and the newest one that does is read as well (up to MAX_READS).
    for n in sorted(caps, reverse=True)[:MAX_READS]:
        try:
            got = parse_update(fetch(WAYBACK.format(**caps[n])), n)
        except Exception as exc:
            problems.append({"endpoint": f"wayback-update-{n}", "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        got.update({"link": caps[n]["url"], "archived": WAYBACK.format(**caps[n]).replace("id_/", "/"),
                    "captured": datetime.strptime(caps[n]["timestamp"], "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")})
        if reading is None:
            reading = got
        if got["state"] != "not_stated":
            if got is not reading:
                reading["stated_in"] = {k: got[k] for k in ("number", "date", "state", "quotes", "link", "archived")}
            break
    if reading is None:
        return None, problems
    rss_link = {u["number"]: u["link"] for u in rss}
    reading["link"] = rss_link.get(reading["number"], reading["link"])
    reading["newest_number"] = newest
    reading["newer_unread"] = [n for n in range(reading["number"] + 1, newest + 1)]
    if reading.get("date"):
        age = (now.date() - datetime.strptime(reading["date"], "%Y-%m-%d").date()).days
        reading["age_days"] = age
        reading["overdue"] = age > STALE_DAYS and not reading["newer_unread"]
    else:
        reading["age_days"], reading["overdue"] = None, False
    reading["rss_read"] = bool(rss) or not any(p["endpoint"] == "iaea-rss" for p in problems)
    return reading, problems
