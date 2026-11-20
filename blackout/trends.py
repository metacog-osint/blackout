"""Daily outage series per country (data/trends.json), rebuilt every live run from the sources' own archives.

Why (20 Nov 2026): 7, 14 and 30-day trends need more days than Blackout's own history log holds (it starts 18 Nov
2026), and IODA and Cloudflare Radar both answer for any past range. So each live run reads DAYS days back from the
sources themselves and counts, per country and UTC day:

  IODA (/v2/outages/events, entityType country; checked 20 Nov 2026: 1,719 events over 61 days in one call)
    episodes        IODA events on BGP, active probing (ping-slash24) or darknet (merit-nt), merged where they overlap
                    in one country, counted on the day the episode starts. Google traffic (gtr) is left out, as in the
                    live lane. Event counts per datasource would count one outage up to three times.
    hours           hours of that day inside a BGP or active-probing event (the BO-IND-1 signals), overlaps counted
                    once; `hours_by_signal` gives each datasource's own hours, darknet included.
    max_score       IODA's largest event score touching the day: a magnitude, never a percentage.
  Cloudflare Radar (annotations/outages, traffic_anomalies; CC BY-NC 4.0)
    radar_outages   outages Cloudflare's analysts annotated, on their start day, with the cause Cloudflare states.
                    An outage named by network only is placed in that network's country (asnsDetails).
    radar_anomalies traffic anomalies Cloudflare verified (TP or VERIFIED) on their start day, not counting those an
                    annotation already covers (they are in radar_outages).
  NetBlocks (its public Bluesky posts, blackout/netblocks.py; added 21 Nov 2026 for partial and regional outages that
  IODA's country signal misses)
    netblocks_posts         NetBlocks posts naming the country, on the UTC day posted
    netblocks_connectivity  of those, posts about the country's internet connectivity (not a platform or service)
    netblocks_by_type       by NetBlocks' own label (Confirmed, Update, Note, ICYMI, other)
    netblocks_levels        levels NetBlocks states ("67% of ordinary levels" -> 67.0), the impact measure
  Each country also gets `netblocks_usual_30d`: its connectivity posts per 30 days from NetBlocks' first post
  (21 Apr 2023) to the first day of the series, a baseline older than the window.

Windows: each of WINDOWS ends on the last complete UTC day (`through`) and is set against the same number of days
just before it. The series is a count of what the sources report: it says how often and how long, not why, and no
trend arrow or judgment is made here. Raw counts are uneven by design of the sources (BGP sees small networks drop
out), so a country is compared with itself, never with another country.

A source that cannot be read leaves its fields null (never 0) and is listed in `problems`; nothing is carried over or
filled in. Days settled (older than SETTLE_DAYS) are appended once to history/trends-days.jsonl by `archive`, so no
day is lost when it leaves the window.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta, timezone

from blackout import ioda, itu, netblocks, radar

FORMAT = "blackout-trends/1"
NAME = "trends.json"
DAYS = 61          # 60 complete UTC days (two 30-day windows) and today, still open
WINDOWS = (7, 14, 30)
SETTLE_DAYS = 3          # IODA and Radar can still add or close an event for a day or two
IODA_LIMIT = 5000        # one call; a full page means the answer may be cut short, and is listed as a problem
EPISODE_SIGNALS = ("bgp", "ping-slash24", "merit-nt")
HOURS_SIGNALS = ("bgp", "ping-slash24")   # BO-IND-1's signals; darknet alone never makes an outage hour
RADAR_LIMIT = 1000
# IODA measurement-wide bursts (found 20 Nov 2026 in the 61 days to that date): one datasource's events starting in the
# same half hour in 52 to 188 countries at once (ping-slash24 15 and 18 Oct; BGP 9 Nov and three times on 18 Nov, 60 each,
# each lasting 30 or 60 min). Every other half hour held at most 6 countries. Those are IODA's own collection, not the
# world's internet, and would swamp any trend, so a burst of BURST_COUNTRIES or more is left out and listed in notes.
BURST_BIN_S = 1800
BURST_COUNTRIES = 20


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _midnight(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _hours_by_day(spans: list[tuple[float, float]], first: float, last: float) -> dict[str, float]:
    """Hours of each UTC day (between first and last, epoch seconds) covered by the merged spans."""
    out: dict[str, float] = {}
    for s, e in _merge(spans):
        s, e = max(s, first), min(e, last)
        while s < e:
            day_end = (_midnight(datetime.fromtimestamp(s, tz=timezone.utc)) + timedelta(days=1)).timestamp()
            cut = min(e, day_end)
            out[_day(s)] = out.get(_day(s), 0.0) + (cut - s) / 3600.0
            s = cut
    return out


def ioda_bursts(events: list[dict]) -> dict[tuple[int, str], int]:
    """{(half-hour start, datasource): countries} for measurement-wide bursts (BURST_COUNTRIES or more)."""
    bins: dict[tuple[int, str], set] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("datasource") not in EPISODE_SIGNALS:
            continue
        try:
            key = (int(float(ev["start"])) // BURST_BIN_S * BURST_BIN_S, ev["datasource"])
        except (KeyError, TypeError, ValueError):
            continue
        bins.setdefault(key, set()).add(str(ev.get("location")))
    return {k: len(v) for k, v in bins.items() if len(v) >= BURST_COUNTRIES}


def ioda_daily(events: list[dict], first: float, last: float, bursts: dict | None = None
               ) -> dict[str, dict[str, dict]]:
    """{cc: {day: {episodes, hours, hours_by_signal, max_score}}} from IODA country events, leaving out the events in
    a measurement-wide burst (ioda_bursts)."""
    bursts = ioda_bursts(events) if bursts is None else bursts
    per: dict[str, dict[str, list]] = {}
    scores: dict[tuple[str, str], float] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("datasource") not in EPISODE_SIGNALS:
            continue
        loc = str(ev.get("location") or "")
        if not loc.startswith("country/"):
            continue
        cc = loc.split("/", 1)[1].upper()
        try:
            s = float(ev["start"])
            e = min(s + float(ev.get("duration") or 0), last)
        except (KeyError, TypeError, ValueError):
            continue
        if (int(s) // BURST_BIN_S * BURST_BIN_S, ev["datasource"]) in bursts:
            continue
        if e <= first or s >= last or e <= s:
            continue
        per.setdefault(cc, {}).setdefault(ev["datasource"], []).append((s, e))
        sc = float(ev.get("score") or 0.0)
        d = max(s, first)
        while d < e:  # the score touches every day the event spans
            key = (cc, _day(d))
            scores[key] = max(scores.get(key, 0.0), sc)
            d = (_midnight(datetime.fromtimestamp(d, tz=timezone.utc)) + timedelta(days=1)).timestamp()
    out: dict[str, dict[str, dict]] = {}
    for cc, by_ds in per.items():
        days: dict[str, dict] = {}

        def row(day):
            return days.setdefault(day, {"episodes": 0, "hours": 0.0, "hours_by_signal": {}, "max_score": None})
        for s, _e in _merge([sp for ds in EPISODE_SIGNALS for sp in by_ds.get(ds, [])]):
            if s >= first:  # an episode already running when the window opens is not a new one
                row(_day(s))["episodes"] += 1
        for day, h in _hours_by_day([sp for ds in HOURS_SIGNALS for sp in by_ds.get(ds, [])], first, last).items():
            row(day)["hours"] = round(h, 2)
        for ds in EPISODE_SIGNALS:
            for day, h in _hours_by_day(by_ds.get(ds, []), first, last).items():
                row(day)["hours_by_signal"][ds] = round(h, 2)
        for day, r in days.items():
            r["max_score"] = round(scores[(cc, day)], 1) if (cc, day) in scores else None
        out[cc] = days
    return out


def _radar_countries(a: dict) -> list[str]:
    """Countries a Radar annotation names, or failing that the countries of the networks it names."""
    return radar.network_countries(a)[0]


def radar_daily(outages: list[dict] | None, anomalies: list[dict] | None, first: float, last: float
                ) -> tuple[dict[str, dict[str, dict]], dict]:
    """({cc: {day: {radar_outages: [...], radar_anomalies: n}}}, {unplaced_outages}) on each event's start day."""
    out: dict[str, dict[str, dict]] = {}
    unplaced = 0

    def row(cc, day):
        return out.setdefault(cc, {}).setdefault(day, {"radar_outages": [], "radar_anomalies": 0})
    for a in outages or []:
        start = radar._t(a.get("startDate"))
        if start is None or not (first <= start.timestamp() < last):
            continue
        ccs = _radar_countries(a)
        if not ccs:
            unplaced += 1
            continue
        o = a.get("outage") or {}
        for cc in ccs:
            row(cc, _day(start.timestamp()))["radar_outages"].append(
                {"id": str(a.get("id")), "cause": o.get("outageCause") or None, "type": o.get("outageType") or None})
    for a in anomalies or []:
        if a.get("status") not in radar.VERIFIED or a.get("outageId"):
            continue
        start = radar._t(a.get("startDate"))
        if start is None or not (first <= start.timestamp() < last):
            continue
        loc = (a.get("asnDetails") or {}).get("location") or a.get("locationDetails") or {}
        code = loc.get("code")
        if not (isinstance(code, str) and len(code) == 2):
            continue
        row(code.upper(), _day(start.timestamp()))["radar_anomalies"] += 1
    return out, {"unplaced_outages": unplaced}


def _sum_window(days: dict[str, dict], dates: list[str], read: dict) -> dict:
    """Totals over the given dates; a source not read gives null, never 0."""
    rows = [days.get(d) or {} for d in dates]
    tot: dict = {}
    if read.get("ioda"):
        tot["episodes"] = sum(r.get("episodes") or 0 for r in rows)
        tot["hours"] = round(sum(r.get("hours") or 0.0 for r in rows), 1)
        tot["days_with_outage"] = sum(1 for r in rows if (r.get("hours") or 0) > 0)
    else:
        tot.update(episodes=None, hours=None, days_with_outage=None)
    tot["radar_outages"] = sum(len(r.get("radar_outages") or []) for r in rows) if read.get("radar_outages") else None
    tot["radar_anomalies"] = sum(r.get("radar_anomalies") or 0 for r in rows) if read.get("radar_anomalies") else None
    if read.get("netblocks"):
        tot["netblocks_posts"] = sum(r.get("netblocks_posts") or 0 for r in rows)
        tot["netblocks_connectivity"] = sum(r.get("netblocks_connectivity") or 0 for r in rows)
        levels = [v for r in rows for v in r.get("netblocks_levels") or []]
        tot["netblocks_lowest_level_pct"] = min(levels) if levels else None
    else:
        tot.update(netblocks_posts=None, netblocks_connectivity=None, netblocks_lowest_level_pct=None)
    return tot


def build(ioda_events: list[dict] | None, radar_outages: list[dict] | None, radar_anomalies: list[dict] | None,
          now: datetime, problems: list[dict] | None = None, days: int = DAYS,
          netblocks_posts: list[dict] | None = None) -> dict:
    """The trends document. Inputs that are None were not read: their fields are null throughout."""
    now = now.astimezone(timezone.utc)
    today = _midnight(now)
    first_dt = today - timedelta(days=days - 1)
    first, last = first_dt.timestamp(), now.timestamp()
    dates = [(first_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    read = {"ioda": ioda_events is not None, "radar_outages": radar_outages is not None,
            "radar_anomalies": radar_anomalies is not None, "netblocks": netblocks_posts is not None}
    by_cc: dict[str, dict[str, dict]] = {}
    bursts = ioda_bursts(ioda_events) if ioda_events is not None else {}
    for cc, ds in (ioda_daily(ioda_events, first, last, bursts) if ioda_events is not None else {}).items():
        for d, r in ds.items():
            by_cc.setdefault(cc, {}).setdefault(d, {}).update(r)
    notes: dict = {"ioda_bursts_left_out": [
        {"start": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "datasource": ds,
         "countries": n} for (t, ds), n in sorted(bursts.items()) if first <= t < last]}
    if radar_outages is not None or radar_anomalies is not None:
        rd, rnotes = radar_daily(radar_outages, radar_anomalies, first, last)
        notes.update(rnotes)
        for cc, ds in rd.items():
            for d, r in ds.items():
                by_cc.setdefault(cc, {}).setdefault(d, {}).update(r)
    usual: dict[str, float] = {}
    if netblocks_posts is not None:
        for cc, ds in netblocks.daily(netblocks_posts, dates[0], dates[-1]).items():
            for d, r in ds.items():
                by_cc.setdefault(cc, {}).setdefault(d, {}).update(r)
        usual = _netblocks_usual(netblocks_posts, dates[0])
        notes["netblocks_usual_from"] = min((p["posted_at"][:10] for p in netblocks_posts if p.get("posted_at")),
                                            default=None)
    through = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    complete = dates[:-1]  # today is still open
    countries = {}
    for cc in sorted(by_cc):
        name, lat, lon = ioda.resolve_country(cc)
        windows = {}
        for w in WINDOWS:
            if 2 * w > len(complete):
                continue
            windows[str(w)] = {"current": _sum_window(by_cc[cc], complete[-w:], read),
                               "previous": _sum_window(by_cc[cc], complete[-2 * w:-w], read)}
        countries[cc] = {"name": name, "lat": lat, "lon": lon,
                         "days": {d: by_cc[cc][d] for d in sorted(by_cc[cc])}, "windows": windows,
                         "netblocks_usual_30d": (round(usual.get(cc, 0.0), 2) if netblocks_posts is not None
                                                 else None),
                         # Upper bound on exposure (ITU share x population), never users affected.
                         "exposure": _exposure(cc)}
    totals = {str(w): {"current": _world(by_cc, complete[-w:], read),
                       "previous": _world(by_cc, complete[-2 * w:-w], read)}
              for w in WINDOWS if 2 * w <= len(complete)}
    return {
        "format": FORMAT,
        "mode": "live",
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_day": dates[0], "through": through, "today": dates[-1],
        "windows": list(WINDOWS),
        "read": read,
        "problems": list(problems or []),
        "notes": notes,
        "method": ("Per country and UTC day: IODA episodes (BGP, active-probing or darknet events merged where they "
                   "overlap, on their start day), IODA outage hours (BGP or active probing), IODA's largest score "
                   "(a magnitude, not a percentage), Cloudflare Radar annotated outages with Cloudflare's stated "
                   "cause, and Radar verified traffic anomalies not covered by an annotation. IODA events of one "
                   f"datasource starting in the same half hour in {BURST_COUNTRIES} or more countries are IODA's own "
                   "collection, not the world's internet, and are left out (listed in notes). Windows end on the "
                   "last complete UTC day and are set against the same number of days before. Counts of what the "
                   "sources report, not a judgment of cause; compare a country with itself. NetBlocks posts naming "
                   "the country (from its hashtags, or names when it uses none), those about connectivity, and the "
                   "levels NetBlocks states, on the UTC day posted; its usual is connectivity posts per 30 days from "
                   "its first post to the first day of the series. NetBlocks reports what it chooses to post: a day "
                   "with no post is not a day with no disruption. Null means the source was not read."),
        "exposure_basis": itu.EXPOSURE_BASIS,
        "exposure_source": f"{itu.SOURCE}; {itu.LICENCE}",
        "sources": ["Georgia Tech IODA (credited; IODA states no data licence)",
                    "Cloudflare Radar, CC BY-NC 4.0",
                    "NetBlocks, its public Bluesky posts (counts and levels only; copyright NetBlocks, credited)"],
        "totals": totals,
        "countries": countries,
    }


def _exposure(cc: str) -> dict | None:
    e = itu.exposure(cc)
    return {k: e[k] for k in ("internet_users_upper", "share_pct", "population", "year")} if e else None


def _world(by_cc: dict[str, dict[str, dict]], dates: list[str], read: dict) -> dict:
    """World totals over the dates: the summed counts, and how many countries had any outage hour."""
    tot = _sum_window(_flat(by_cc), dates, read)
    tot.pop("days_with_outage", None)
    tot["countries_with_outage"] = (sum(1 for ds in by_cc.values() if any((ds.get(d) or {}).get("hours") for d in dates))
                                    if read.get("ioda") else None)
    return tot


def _flat(by_cc: dict[str, dict[str, dict]]) -> dict[str, dict]:
    """All countries summed into one {day: row}, for the world totals."""
    out: dict[str, dict] = {}
    for ds in by_cc.values():
        for d, r in ds.items():
            t = out.setdefault(d, {"episodes": 0, "hours": 0.0, "radar_outages": [], "radar_anomalies": 0,
                                   "nb_ids": set(), "nb_conn": set(), "netblocks_levels": []})
            t["episodes"] += r.get("episodes") or 0
            t["hours"] += r.get("hours") or 0.0
            t["radar_outages"] += r.get("radar_outages") or []
            t["radar_anomalies"] += r.get("radar_anomalies") or 0
            t["nb_ids"] |= set(r.get("netblocks_ids") or [])
            t["nb_conn"] |= set(r.get("netblocks_conn_ids") or [])
            t["netblocks_levels"] += r.get("netblocks_levels") or []
    for t in out.values():  # one annotation (or post) naming two countries is one in the world total
        t["radar_outages"] = list({o["id"]: o for o in t["radar_outages"]}.values())
        t["netblocks_posts"] = len(t.pop("nb_ids"))
        t["netblocks_connectivity"] = len(t.pop("nb_conn"))
    return out


def _netblocks_usual(posts: list[dict], before: str) -> dict[str, float]:
    """{cc: connectivity posts per 30 days} from NetBlocks' first post to the day `before` (not included)."""
    days = [p["posted_at"][:10] for p in posts if p.get("posted_at")]
    if not days or min(days) >= before:
        return {}
    span = (datetime.strptime(before, "%Y-%m-%d") - datetime.strptime(min(days), "%Y-%m-%d")).days
    counts: dict[str, int] = {}
    for p in posts:
        if p.get("scope") == "connectivity" and (p.get("posted_at") or "")[:10] < before:
            for cc in p.get("countries") or []:
                counts[cc] = counts.get(cc, 0) + 1
    return {cc: n * 30.0 / span for cc, n in counts.items()} if span > 0 else {}


def fetch(now: datetime | None = None, days: int = DAYS, ioda_fetch=ioda._get_json, radar_fetch=radar._get,
          netblocks_posts: list[dict] | None = None) -> tuple[dict, list[dict]]:
    """(trends document, problems), reading IODA and Radar for the last `days` UTC days. A source that fails is
    not read (null fields) and is listed."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    first_dt = _midnight(now) - timedelta(days=days - 1)
    problems: list[dict] = []
    ioda_events = radar_out = radar_anom = None
    try:
        q = urllib.parse.urlencode({"from": int(first_dt.timestamp()), "until": int(now.timestamp()),
                                    "entityType": "country", "limit": IODA_LIMIT})
        payload = ioda_fetch(f"{ioda.API}/outages/events?{q}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ValueError("IODA outages/events: no data list in the response")
        if len(data) >= IODA_LIMIT:
            raise ValueError(f"IODA returned a full page ({IODA_LIMIT} events): the series may be cut short")
        ioda_events = data
    except Exception as exc:  # noqa: BLE001 - listed, and the series says IODA was not read
        problems.append({"source": "trends:ioda", "error": f"{type(exc).__name__}: {exc}"[:200]})
    rng = urllib.parse.urlencode({"dateStart": first_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  "dateEnd": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "format": "json",
                                  "limit": RADAR_LIMIT})
    for key, path, field in (("radar_outages", "annotations/outages", "annotations"),
                             ("radar_anomalies", "traffic_anomalies", "trafficAnomalies")):
        try:
            extra = "&status=VERIFIED" if field == "trafficAnomalies" else ""
            rows = radar._result(radar_fetch(f"{radar.API}/{path}?{rng}{extra}"), field)
            if len(rows) >= RADAR_LIMIT:
                raise ValueError(f"Radar returned a full page ({RADAR_LIMIT}): the series may be cut short")
            if key == "radar_outages":
                radar_out = rows
            else:
                radar_anom = rows
        except Exception as exc:  # noqa: BLE001
            problems.append({"source": f"trends:{key.replace('_', '-')}",
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
    return build(ioda_events, radar_out, radar_anom, now, problems, days, netblocks_posts), problems


def settled_rows(doc: dict, now: datetime | None = None) -> list[dict]:
    """One archive row per country and settled day (older than SETTLE_DAYS), for history/trends-days.jsonl. Only
    days whose sources were all read are archived, so a failed read never writes a false zero."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("mode") != "live":
        return []
    read = doc.get("read") or {}
    if not all(read.get(k) for k in ("ioda", "radar_outages", "radar_anomalies", "netblocks")):
        return []
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = (_midnight(now) - timedelta(days=SETTLE_DAYS)).strftime("%Y-%m-%d")
    first = doc.get("first_day") or cutoff
    d0 = datetime.strptime(first, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    by_day: dict[str, list[dict]] = {}
    for cc, c in (doc.get("countries") or {}).items():
        for day, r in (c.get("days") or {}).items():
            exp = c.get("exposure") or {}
            by_day.setdefault(day, []).append({"kind": "country", "day": day, "country": cc, **r,
                                               "internet_users_upper": exp.get("internet_users_upper"),
                                               "itu_year": exp.get("year")})
    rows = []
    d = d0
    while d.strftime("%Y-%m-%d") < cutoff:
        day = d.strftime("%Y-%m-%d")
        found = sorted(by_day.get(day, []), key=lambda r: r["country"])
        # The day row says the day was read, so a country with no row that day had nothing reported, not unknown.
        rows.append({"kind": "day", "day": day, "countries": len(found), "read_at": doc.get("generated_at")})
        rows += found
        d += timedelta(days=1)
    return rows


def dumps(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
