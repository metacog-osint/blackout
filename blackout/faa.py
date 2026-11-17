"""US aviation system stops (BO-WP-6, BO-IND-17): ground stops as the FAA's Command Center issues them.

Added 17 Nov 2026, after the FAA's nationwide ground stop of 13 Nov 2026 (a communications failure; departures stopped
at every US airport from 20:53 UTC for about four and a half hours) reached no source Blackout read. Tom: "It is
critical Blackout captures these events."

Source: the ATCSCC advisories database (www.fly.faa.gov/adv/advADB), the FAA Air Traffic Control System Command
Center's own record of every advisory it issues, back to the 1990s. A US Government work, public domain. Two pages,
both plain HTML, both read without a key (robots.txt on www.fly.faa.gov is a redirect page, no rules):

  adv_list?whichAdvisories=ATCSCC&advisoryCategory=NotAll&date=YYYY-MM-DD&gStop=true&_gStop=on
      one UTC day's ground-stop advisories: rows "111 DCC 11/13/26 NATIONAL GROUND STOP 11/13/26 20:54", each with
      a link adv_otherdis?adv_date=MMDDYYYY&advn=N. Older days (19 Jul 2024) list every advisory whatever the
      category, so the title is filtered here too.
  adv_otherdis?adv_date=MMDDYYYY&advn=N
      one advisory's text: "ATCSCC ADVZY 120 DCC 11/13/2026 NATIONWIDE GROUND STOP", then fields such as
      "IMPACTING CONDITION: EQUIPMENT / OUTAGE" (or "REASON: COMMUNICATIONS FAILURE"), "COMMENTS: ...", and
      "EFFECTIVE TIME: 132246-140430" (ddhhmm-ddhhmm, UTC).

Rule (BO-IND-17). A ground stop counts when it is one of:
  national       issued by DCC with NATIONAL or NATIONWIDE GROUND STOP in its title (13 Nov 2026: advisories 111 at
                 20:54 UTC, "ALL AIRPORTS ARE GROUND STOPPED", and 120 at 22:46, "REASON: COMMUNICATIONS FAILURE").
  airline        the title names an airline before GROUND STOP ("UAL AIRLINES GROUND STOP", "UAL AND UAL SUBS GROUND
                 STOP", "DAL AIRLINES GROUND STOP MAINLINE AND SUBS."): 19 Jul 2024, the CrowdStrike update, gave
                 United, American, Delta, Allegiant, Spirit, Frontier and PSA stops, "IMPACTING CONDITION:
                 COMMUNICATION ISSUE".
  carrier        two or more airports stopped within 2 h whose comments say CARRIER REQUEST (6 Aug 2025, United's
                 own outage: SFO, IAH, ORD, DEN and EWR at 23:42-23:47 UTC, "OTHER / OTHER", "UAL AND SUBS ONLY.
                 CARRIER REQUEST."). One stop alone is left out: an airline asks for those for many reasons.
  equipment      an airport stop whose impacting condition or reason names EQUIPMENT or COMMUNICATION (13 Nov 2026:
                 ORD and MDW, "EQUIPMENT / OUTAGE", "COMMS ISSUES", 27 and 24 minutes before the national stop).
                 Routine when it is one airport (16 Nov 2026: LAS, runway approach lights), anomaly when two or more
                 airports' equipment stops begin within 2 h.
Weather, volume, staffing, runway and construction stops are left out (on 16 Nov 2026, 14 of 15).

Advisories for the same thing are joined into one event: an extension re-issues the stop with a later end. A
cancellation ("CDM GS CNX", "LGA GROUND STOP CANCELLATION", "UAL AND UAL SUBS GROUND STOP CANCELLATION") ends the event
at the time it was issued. Otherwise the event ends at its last effective time, which is the FAA's expected end, not a
confirmed one; the item says so. An event is kept while in effect and for 24 h after it ended. Two UTC days are read,
so a stop that began late the day before is still seen.

Coverage: a ground stop is the FAA's tool, so an airline outage that only delays flights at the gate, with no stop
requested (United, 16 Nov 2026), never appears here. Its absence here means nothing for an airline.
"""
from __future__ import annotations

import html
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://www.fly.faa.gov/adv/"
PAGE = BASE + "advADB"
LIST_URL = BASE + "adv_list?whichAdvisories=ATCSCC&advisoryCategory=NotAll&date={date}&gStop=true&_gStop=on"
DETAIL_URL = BASE + "adv_otherdis?adv_date={mmddyyyy}&advn={advn}"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"

DAYS_READ = 2          # today and yesterday, UTC
KEEP_HOURS = 24        # an event is listed while in effect and for this long after it ended
GROUP_HOURS = 2        # carrier-request and equipment stops at different airports join when they begin this close
PAUSE_S = 0.2          # between detail pages: one at a time, never in parallel
TRIES = 3              # a detail page that comes back without its advisory is asked for again ...
RETRY_PAUSE_S = 3      # ... after this long (17 Nov 2026: 3 pages of 16 Nov said "NO Advisory." on the first
                       # live run; two of them read fine minutes later)
NO_ADVISORY = "NO Advisory"

# ICAO designators of US airlines, for titles; an unknown one is shown as its code.
AIRLINES = {
    "AAL": "American Airlines", "UAL": "United Airlines", "DAL": "Delta Air Lines", "SWA": "Southwest Airlines",
    "JBU": "JetBlue", "ASA": "Alaska Airlines", "NKS": "Spirit Airlines", "FFT": "Frontier Airlines",
    "AAY": "Allegiant Air", "HAL": "Hawaiian Airlines", "SKW": "SkyWest Airlines", "PSA": "PSA Airlines",
    "RPA": "Republic Airways", "ENY": "Envoy Air", "EDV": "Endeavor Air", "ASH": "Mesa Airlines",
    "QXE": "Horizon Air", "SCX": "Sun Country Airlines", "MXY": "Breeze Airways", "FDX": "FedEx", "UPS": "UPS",
    "GJS": "GoJet Airlines", "JIA": "PSA Airlines", "AWI": "Air Wisconsin", "CPZ": "Compass Airlines",
}
# Words that start a title but are not an airline.
NOT_AIRLINE = {"CDM", "ALL", "DCC", "THE", "AND", "GS", "NAT"}

ROW_RE = re.compile(r"adv_otherdis\?adv_date=(\d{8})&(?:amp;)?advn=(\d+)")
HEAD_RE = re.compile(r"ATCSCC ADVZY\s+(\d+)\s+(\S+)\s+(\d{2}/\d{2}/\d{4})\s+(.*)")
EFFECTIVE_RE = re.compile(r"EFFECTIVE TIME:\s*(\d{6})-(\d{6})")
SIGNATURE_RE = re.compile(r"SIGNATURE:\s*(\d{2})/(\d{2})/(\d{2}) (\d{2}):(\d{2})")
FIELD_RE = {
    "condition": re.compile(r"(?:IMPACTING CONDITION|REASON):\s*([^\n]+)"),
    "comments": re.compile(r"COMMENTS?:\s*([^\n]+(?:\n(?![A-Z][A-Z /]+:)[^\n]+)*)"),
    "ctl_element": re.compile(r"CTL ELEMENT:\s*([A-Z0-9]+)"),
    "destination": re.compile(r"DESTINATION AIRPORTS?:\s*([^\n]+)"),
    "facilities": re.compile(r"(?:DEP )?FACILITIES INCLUDED:\s*([^\n]+)"),
}


def _utc(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _get(url: str, timeout: float = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(5_000_000)


def page_text(body: bytes | str) -> str:
    """The page as plain text: scripts and styles dropped, <br> as new lines, entities and &nbsp; decoded."""
    t = body.decode("utf-8", "ignore") if isinstance(body, bytes) else body
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S | re.I)
    t = re.sub(r"<br[^>]*>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t)


def parse_list(body: bytes | str) -> list[dict]:
    """[{advn, adv_date, element, title, issued}] for every advisory row of a list page. `issued` is the time in the
    row (UTC); the title is the row's text between the date and that time."""
    t = body.decode("utf-8", "ignore") if isinstance(body, bytes) else body
    out, seen = [], set()
    for row in re.findall(r"<tr.*?</tr>", t, flags=re.S | re.I):
        m = ROW_RE.search(row)
        if not m:
            continue
        cells = [page_text(c).strip() for c in re.findall(r"<td.*?</td>", row, flags=re.S | re.I)]
        text = " ".join(c for c in cells if c)
        rm = re.match(r"^(\d+)\s+(.+?)\s+\d{2}/\d{2}/\d{2,4}\s+(.*?)\s+(\d{2})/(\d{2})/(\d{2,4})\s+(\d{2}):(\d{2})$",
                      re.sub(r"\s+", " ", text))
        if not rm:
            continue
        mo, dd, yy, hh, mi = (int(rm.group(i)) for i in (4, 5, 6, 7, 8))
        year = yy + 2000 if yy < 100 else yy
        key = (m.group(1), int(m.group(2)))
        if key in seen:
            continue
        seen.add(key)
        out.append({"advn": int(m.group(2)), "adv_date": m.group(1), "element": rm.group(2),
                    "title": rm.group(3).strip(), "issued": _utc(datetime(year, mo, dd, hh, mi, tzinfo=timezone.utc))})
    return out


def _ddhhmm(s: str, ref: datetime) -> datetime:
    """A ddhhmm time near `ref` (the advisory's issue time): the day decides the month, rolling over either way."""
    d, h, m = int(s[:2]), int(s[2:4]), int(s[4:6])
    best = None
    for months in (-1, 0, 1):
        y, mo = ref.year, ref.month + months
        y, mo = (y - 1, 12) if mo < 1 else ((y + 1, 1) if mo > 12 else (y, mo))
        try:
            cand = datetime(y, mo, d, h, m, tzinfo=timezone.utc)
        except ValueError:
            continue
        if best is None or abs(cand - ref) < abs(best - ref):
            best = cand
    return best


def parse_detail(body: bytes | str) -> dict | None:
    """One advisory's fields, or None when the page holds no advisory text."""
    t = page_text(body)
    i = t.find("ATCSCC ADVZY")
    if i < 0:
        return None
    t = t[i:]
    j = t.find("FAA.gov Home")
    t = t[:j] if j > 0 else t
    head = HEAD_RE.search(t)
    if not head:
        return None
    sig = SIGNATURE_RE.search(t)
    issued = (datetime(2000 + int(sig.group(1)), int(sig.group(2)), int(sig.group(3)), int(sig.group(4)),
                       int(sig.group(5)), tzinfo=timezone.utc) if sig else None)
    out = {"advn": int(head.group(1)), "element": head.group(2), "title": head.group(4).strip(),
           "issued": _utc(issued), "start": None, "end": None}
    eff = EFFECTIVE_RE.search(t)
    if eff and issued:
        out["start"] = _utc(_ddhhmm(eff.group(1), issued))
        out["end"] = _utc(_ddhhmm(eff.group(2), issued))
    for k, rx in FIELD_RE.items():
        m = rx.search(t)
        v = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
        out[k] = re.split(r"\s*(?:EFFECTIVE TIME|SIGNATURE):", v)[0].strip() or None if v else None
    return out


def _is_cancel(title: str) -> bool:
    return bool(re.search(r"\bCNX\b|CANCEL", title))


def _is_stop(title: str) -> bool:
    return ("GROUND STOP" in title or re.search(r"\bGS\b", title) is not None) and "PROGRAM" not in title


def airline_code(title: str, element: str) -> str | None:
    """The airline a ground stop names in its title, or None for an airport stop."""
    if title.startswith("CDM ") or "NATIONAL" in title or "NATIONWIDE" in title:
        return None
    first = re.match(r"^([A-Z]{3,5})\b", title)
    code = first.group(1) if first else None
    if code in ("DELTA",):
        return "DAL"
    if not code or code in NOT_AIRLINE:
        return None
    airline_words = re.search(r"\bAIRLINES?\b|\bSUBS\b|\bMAINLINE\b", title)
    el_first, _, el_second = element.partition("/")
    by_element = el_second == "DCC" and el_first == code
    if airline_words or by_element or code in AIRLINES and el_first != code:
        return code
    return None


def classify(adv: dict) -> tuple[str, str] | None:
    """(kind, key) for an advisory that counts toward BO-IND-17, else None. Cancellations return their key too, with
    kind "cancel:<kind>", so they can end an event."""
    title, element = adv["title"], adv["element"]
    cancel = _is_cancel(title)
    el_first = element.split("/")[0]
    if el_first == "DCC" and ("NATIONAL" in title or "NATIONWIDE" in title):
        return ("cancel:national" if cancel else "national", "national")
    if (adv.get("destination") or "").strip().upper() == "ALL" and "ALL" in (adv.get("facilities") or "").upper() \
            and airline_code(title, element) is None and not title.startswith("CDM "):
        return ("cancel:national" if cancel else "national", "national")
    code = airline_code(title, element)
    if code:
        return ("cancel:airline" if cancel else "airline", code)
    airport = adv.get("element_ctl") or el_first
    if cancel:
        return ("cancel:airport", airport)
    cond = (adv.get("condition") or "").upper()
    comments = (adv.get("comments") or "").upper()
    if "CARRIER REQUEST" in comments:
        return ("carrier", airport)
    if "EQUIPMENT" in cond or "COMMUNICATION" in cond:
        return ("equipment", airport)
    return None


def _carrier_named(comments: str) -> str | None:
    m = re.search(r"\b([A-Z]{3}) (?:AND SUBS|MAINLINE|ONLY)\b", comments or "")
    return m.group(1) if m and m.group(1) not in NOT_AIRLINE else None


def _parse_time(s: str | None) -> datetime | None:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


def build_events(advisories: list[dict], now: datetime) -> list[dict]:
    """Join classified advisories into events (see the module docstring) and keep those in effect or ended within
    KEEP_HOURS of `now`."""
    stops, cancels = [], []
    for a in advisories:
        if (_parse_time(a.get("issued")) or now) > now:  # not yet issued at `now` (replays)
            continue
        c = classify(a)
        if not c:
            continue
        kind, key = c
        a = {**a, "kind": kind, "key": key}
        (cancels if kind.startswith("cancel:") else stops).append(a)
    # 1. one event per (kind, key) run of overlapping advisories
    events: list[dict] = []
    for a in sorted(stops, key=lambda x: (x["issued"] or "", x["advn"])):
        start = _parse_time(a["start"]) or _parse_time(a["issued"])
        end = _parse_time(a["end"]) or start
        match = None
        for e in events:
            if e["kind"] == a["kind"] and e["key"] == a["key"] and start <= e["_end"] + timedelta(hours=1):
                match = e
        if match:
            match["_end"] = max(match["_end"], end)
            match["advisories"].append(a)
        else:
            events.append({"kind": a["kind"], "key": a["key"], "_start": min(start, _parse_time(a["issued"]) or start),
                           "_end": end, "advisories": [a], "cancelled_at": None})
    # 2. a cancellation ends the latest event for its key that it follows
    for c in sorted(cancels, key=lambda x: x["issued"] or ""):
        ct = _parse_time(c["issued"])
        kind = c["kind"].split(":", 1)[1]
        cands = [e for e in events if e["key"] == c["key"] and e["_start"] <= ct
                 and (kind == e["kind"] or kind == "airport" and e["kind"] in ("carrier", "equipment"))
                 and e["cancelled_at"] is None and ct <= e["_end"] + timedelta(hours=1)]
        if cands:
            e = max(cands, key=lambda x: x["_start"])
            e["cancelled_at"] = ct
            e["_end"] = min(e["_end"], ct)
            e["advisories"].append(c)
    # 3. carrier-request stops count only when 2+ airports begin within GROUP_HOURS; they join into one event.
    out = [e for e in events if e["kind"] in ("national", "airline", "equipment")]
    carrier = sorted((e for e in events if e["kind"] == "carrier"), key=lambda e: e["_start"])
    used = set()
    for i, e in enumerate(carrier):
        if i in used:
            continue
        group = [j for j in range(i, len(carrier))
                 if j not in used and carrier[j]["_start"] - e["_start"] <= timedelta(hours=GROUP_HOURS)]
        if len({carrier[j]["key"] for j in group}) >= 2:
            used.update(group)
            members = [carrier[j] for j in group]
            named = sorted({n for m in members for a in m["advisories"]
                            if (n := _carrier_named(a.get("comments") or ""))})
            out.append({"kind": "carrier", "key": "+".join(sorted({m["key"] for m in members})),
                        "_start": min(m["_start"] for m in members), "_end": max(m["_end"] for m in members),
                        "advisories": [a for m in members for a in m["advisories"]],
                        "cancelled_at": None, "airports": sorted({m["key"] for m in members}), "carriers": named,
                        "members": [{"airport": m["key"], "start": _utc(m["_start"]), "end": _utc(m["_end"]),
                                     "cancelled": m["cancelled_at"] is not None} for m in members]})
    # 4. equipment stops at 2+ airports beginning within GROUP_HOURS of each other are marked as a cluster
    equip = [e for e in out if e["kind"] == "equipment"]
    for e in equip:
        e["cluster"] = sorted({x["key"] for x in equip
                               if abs(x["_start"] - e["_start"]) <= timedelta(hours=GROUP_HOURS)})
    kept = []
    for e in out:
        if now - e["_end"] > timedelta(hours=KEEP_HOURS) or e["_start"] > now + timedelta(hours=1):
            continue
        first = e["advisories"][0]
        texts = [a for a in e["advisories"] if not a["kind"].startswith("cancel:")]
        last = texts[-1]
        kept.append({
            "kind": e["kind"],
            "key": e["key"],
            "id": f"{e['kind']}:{e['key'].lower()}:{_utc(e['_start'])[:13]}",
            "airline": AIRLINES.get(e["key"]) if e["kind"] == "airline" else None,
            "airports": e.get("airports") or ([e["key"]] if e["kind"] == "equipment" else []),
            "carriers": e.get("carriers") or [],
            "members": e.get("members") or [],
            "cluster": e.get("cluster") or [],
            "started_at": _utc(e["_start"]),
            "ends_at": _utc(e["_end"]),
            "cancelled_at": _utc(e["cancelled_at"]),
            "in_effect": e["cancelled_at"] is None and now < e["_end"],
            "condition": last.get("condition"),
            "comments": (last.get("comments") or "")[:300] or None,
            "advisories": [{"advn": a["advn"], "issued": a["issued"], "title": a["title"],
                            "link": DETAIL_URL.format(mmddyyyy=a["adv_date"], advn=a["advn"])}
                           for a in e["advisories"]],
            "link": DETAIL_URL.format(mmddyyyy=first["adv_date"], advn=first["advn"]),
        })
    kept.sort(key=lambda x: x["started_at"], reverse=True)
    return kept


def fetch_faa(now: datetime | None = None, fetch=_get, pause: float = PAUSE_S) -> tuple[dict, list[dict]]:
    """({read_at, days, advisories_read, ground_stops, events}, problems). Lists DAYS_READ UTC days, then reads each
    ground stop's own page (cancellations are read from the list row alone). A day or page that fails is a problem;
    events are built from what was read."""
    now = now or datetime.now(timezone.utc)
    problems, advisories, stops_seen = [], [], 0
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS_READ - 1, -1, -1)]
    for day in days:
        try:
            rows = parse_list(fetch(LIST_URL.format(date=day)))
        except Exception as exc:
            problems.append({"endpoint": "faa:list", "error": f"{day}: {type(exc).__name__}: {exc}"[:200]})
            continue
        for row in rows:
            if not _is_stop(row["title"]):
                continue
            stops_seen += 1
            if _is_cancel(row["title"]):
                advisories.append({**row, "start": row["issued"], "end": row["issued"], "condition": None,
                                   "comments": None})
                continue
            d, body, failed = None, b"", None
            for attempt in range(TRIES):
                if attempt and pause:
                    time.sleep(RETRY_PAUSE_S)
                try:
                    body = fetch(DETAIL_URL.format(mmddyyyy=row["adv_date"], advn=row["advn"]))
                    failed = None
                except Exception as exc:
                    failed = f"{type(exc).__name__}: {exc}"
                    continue
                d = parse_detail(body)
                if d is not None:
                    break
            if failed and d is None:
                problems.append({"endpoint": "faa:advisory", "error": f"{row['adv_date']} {row['advn']}: {failed}"[:200]})
                continue
            if d is None:
                text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body)
                if NO_ADVISORY in text:
                    # The list links it but the FAA's page says it holds no advisory, every time asked. A data note,
                    # not a failed read: the title alone still counts a national or whole-airline stop.
                    problems.append({"endpoint": "faa:advisory-missing",
                                     "error": f"{row['adv_date']} {row['advn']}: the FAA's page says NO Advisory "
                                              f"({row['title']})"[:200]})
                    if classify({**row, "condition": None, "comments": None}) and \
                            classify({**row, "condition": None, "comments": None})[0] in ("national", "airline"):
                        advisories.append({**row, "start": row["issued"], "end": row["issued"], "condition": None,
                                           "comments": None})
                else:
                    problems.append({"endpoint": "faa:advisory", "error": f"{row['adv_date']} {row['advn']}: no text"})
                continue
            advisories.append({**row, **{k: v for k, v in d.items() if k not in ("advn", "title", "element")},
                               "element_ctl": d.get("ctl_element"), "issued": d.get("issued") or row["issued"]})
            if pause:
                time.sleep(pause)
    events = build_events(advisories, now)
    return {"read_at": _utc(now), "days": days, "advisories_read": len(advisories), "ground_stops": stops_seen,
            "events": events, "page": PAGE}, problems
