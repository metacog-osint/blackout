"""Satellite networks register (SATCOM lane, ideas#104 item 5): every network found for a satellite operator, and the
lookalikes that share a name, each with a class so the page and the trend series can filter rather than drop them.

The register (`satcom_networks.json`, committed) is compiled by hand from registry records: the RIRs (RIPE DB, ARIN,
APNIC, LACNIC, AFRINIC), PeeringDB, and the operators' own sites for what a network does. Nothing is excluded: a
network that is not satellite internet (AS34602, a Moscow home ISP called Starlink; AS29242, Viasat Ukraine's
satellite-TV company) stays in, classed as such, because a reader searching the name should find it and see why it is
not counted. `confidence` says how far the class was checked.

Classes (closed list, CLASSES): satellite_internet (consumer or enterprise access over satellite),
satellite_maritime_aviation (connectivity sold to ships and aircraft), satellite_operator_other (teleport, capacity,
corporate networks of an operator), satellite_tv (broadcast pay-TV), not_satellite (a lookalike name).

The traffic lane (`fetch_traffic`, built 20 Nov 2026; shapes checked against real responses in
tests/fixtures/satcom/MANIFEST.json):

  Cloudflare Radar (CC BY-NC 4.0)  for each network in a COUNTED class, `http/top/locations?asn=` over 7 days gives
                    the countries its requests come from (share of the network's own requests). A network with no
                    rows is `seen: false` (Radar sees no traffic from it) and nothing more is read for it. For the
                    rest, `http/timeseries?asn=` hourly over the 29 days to the last full hour, worldwide and in each
                    of its top countries (TOP_COUNTRIES, Starlink TOP_COUNTRIES_BY_ASN; share >= MIN_SHARE_PCT), gives
                    the level now: the mean of the last CURRENT_HOURS hours against the median, over the NORMAL_DAYS
                    days before, of the same clock hours (`percent_of_normal`). Each series is its own request:
                    a batch's answer names did not match its series (20 Nov 2026). A series Radar rounds to a few
                    levels with many zero hours is named too thin (`sparse`), not measured. Daily points over the
                    trend days plus NORMAL_DAYS give each day's percent of its own 28-day normal for trends.json
                    (`daily_percent`), worldwide, and by country for DAILY_COUNTRIES_ASNS (Starlink). Radar's `estimatedUsers` is never read: its Starlink figures (47.4M
                    in all, Yemen 4.3M, 20 Nov 2026) are implausible and unchecked.
  IODA (credited)   for each counted network the register marks confirmed, `/v2/signals/raw/asn/<asn>` for 8 days:
                    BGP and active probing over the last asn_watch.CURRENT_HOURS (3) against the same hours of the 7
                    days before (ioda.signal_drop), as a percent of normal; a signal whose baseline is under
                    ioda.MIN_BASELINE is named low_count and not measured, as for countries.

First replay (tests): Starlink (AS14593) located in Ukraine fell from 0.79 to 0.40 of its window peak between 16:00 and
18:00 UTC on 4 Feb 2026, the day after Ukraine's terminal-registration deadline; Starlink worldwide and all Ukrainian
traffic stayed flat. The lane publishes measurements only: no feed item, tier or threshold comes from them (a SATCOM
drop rule is a method decision not yet taken), so a failed read is a problem on no feed lane (feed.NOT_A_LANE).

Registry terms (read 20 Nov 2026; doctrine/sources.json): PeeringDB's AUP and the RIPE Database Terms and Conditions
both bar reproducing compiled data without permission (RIPE art. 4.5 excepts an undefined "insubstantial part"). So
the page part (`snapshot_part`) carries the ASN, Blackout's own operator label, class and service, and a link to
Cloudflare Radar's page for the network, never a registry holder name, country, date or evidence text.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).with_name("satcom_networks.json")
CLASSES = ("satellite_internet", "satellite_maritime_aviation", "satellite_operator_other", "satellite_tv",
           "not_satellite")
COUNTED = ("satellite_internet", "satellite_maritime_aviation")  # what a SATCOM total adds up, by default
CONFIDENCE = ("confirmed", "registry only", "unverified")


def load(path: Path = FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def search(networks: list[dict], text: str = "", classes: tuple | list | None = None) -> list[dict]:
    """Networks whose ASN, operator, registry holder or parent contains `text` (case-insensitive; "AS14593" or
    "14593" both match), limited to `classes` when given. Nothing is dropped from the register itself."""
    t = text.strip().lower().removeprefix("as")
    out = []
    for n in networks:
        if classes and n.get("class") not in classes:
            continue
        hay = " ".join(str(n.get(k) or "") for k in ("asn", "operator", "registry_holder", "parent")).lower()
        if not t or t in hay:
            out.append(n)
    return out


# What the page may show of each register row: Blackout's own compilation (from operators' sites) and the ASN. Registry
# holder names, countries, dates and the evidence text quoting registry objects stay in the repository's register only
# (PeeringDB AUP; RIPE Database Terms art. 4.5 and 9.2; read 20 Nov 2026).
PAGE_FIELDS = ("asn", "operator", "class", "service", "constellation", "parent", "confidence", "registry")
RADAR_AS_PAGE = "https://radar.cloudflare.com/as{asn}"
PAGE_NOTE = ("Operator, class and service are Blackout's own reading of the operators' sites and registry records, "
             "checked on the date shown. Registry holder names are not shown: PeeringDB's and the RIPE Database's "
             "terms bar republishing their data. Each network links to Cloudflare Radar's page for it.")


def snapshot_part(doc: dict | None = None, traffic: dict | None = None) -> dict | None:
    """What the page reads: every network in the register (nothing excluded) with the fields it may show, the
    operators with no network found, and this run's traffic readings when the lane ran (live runs only)."""
    try:
        doc = doc if doc is not None else load()
    except (OSError, ValueError):
        return None
    nets = [{k: n.get(k) for k in PAGE_FIELDS} | {"link": RADAR_AS_PAGE.format(asn=n["asn"])}
            for n in doc.get("networks") or [] if isinstance(n.get("asn"), int)]
    return {"checked_at": doc.get("checked_at"), "note": PAGE_NOTE, "networks": nets,
            "not_found": [x.get("operator") for x in doc.get("not_found") or [] if x.get("operator")],
            "name_matches_not_satellite": [{"asn": x.get("asn"), "reason": x.get("reason")}
                                           for x in doc.get("name_matches_not_satellite") or []],
            "classes": list(CLASSES), "counted": list(COUNTED), "traffic": traffic}


# --- traffic lane (Cloudflare Radar and IODA per network) -------------------------------------------------------------
NORMAL_DAYS = 28          # each network's own normal: the 28 days before
MIN_NORMAL_DAYS = 21      # fewer days with a value than this: no normal is stated
CURRENT_HOURS = 6         # "now" is the mean of the last 6 full hours Radar gives
HOURLY_DAYS = NORMAL_DAYS + 1
# One series per request. Radar takes several series in one request (name=...&asn=...), but on 20 Nov 2026 the names
# on the answer did not match the series asked for (Starlink worldwide and Starlink in Ukraine came back swapped), and
# series in one request share one MIN0_MAX scale. So each series is its own request.
# A series too thin to measure: Radar rounds a small network's traffic to a few levels (OneWeb AS800 in steps of 1/17,
# 20 Nov 2026) with many zero hours, so a percent of normal from it would be noise. Such a series is named, not measured.
SPARSE_ZERO_SHARE = 0.05  # more than 5% of points at zero
SPARSE_MIN_LEVELS = 50    # or fewer than 50 distinct values
TOP_COUNTRIES = 3
TOP_COUNTRIES_BY_ASN = {14593: 20}   # Starlink: its top 20 countries reach Ukraine (11th) and Yemen (12th), 20 Nov 2026
DAILY_COUNTRIES_ASNS = (14593,)       # daily series by country for trends.json; every other network worldwide only
MIN_SHARE_PCT = 0.5       # a country under 0.5% of the network's requests is listed, not measured
LOCATIONS_DAYS = 7
IODA_DAYS = 8
WORKERS = 4


def counted(doc: dict | None = None) -> list[dict]:
    """The register's networks in a COUNTED class, in register order."""
    doc = doc if doc is not None else load()
    return [n for n in doc.get("networks") or [] if n.get("class") in COUNTED and isinstance(n.get("asn"), int)]


def _ts(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def locations_url(asn: int, limit: int) -> str:
    from . import radar
    return f"{radar.API}/http/top/locations?asn={asn}&dateRange={LOCATIONS_DAYS}d&limit={limit}&format=json"


def timeseries_url(series: list[tuple[str, int, str | None]], start, end, agg: str) -> str:
    """One Radar request for several series, each (name, asn or 0, location or None), over [start, end)."""
    from . import radar
    parts = []
    for name, asn, cc in series:
        parts.append(f"name={name}")
        if asn:
            parts.append(f"asn={asn}")
        if cc:
            parts.append(f"location={cc}")
        parts.append(f"dateStart={_ts(start)}&dateEnd={_ts(end)}")
    return f"{radar.API}/http/timeseries?{'&'.join(parts)}&aggInterval={agg}&format=json"


def parse_locations(data: dict) -> list[dict]:
    """[{cc, name, share_pct}] from http/top/locations (top_0 rows; value is a percent of the network's requests)."""
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ValueError("Radar did not answer success")
    rows = (data.get("result") or {}).get("top_0")
    if not isinstance(rows, list):
        raise ValueError("no result.top_0 list")
    out = []
    for r in rows:
        cc = r.get("clientCountryAlpha2") if isinstance(r, dict) else None
        try:
            share = round(float(r.get("value")), 2)
        except (TypeError, ValueError):
            continue
        if isinstance(cc, str) and len(cc) == 2:
            out.append({"cc": cc.upper(), "name": r.get("clientCountryName") or cc, "share_pct": share})
    return out


def parse_timeseries(data: dict) -> dict[str, list[tuple[str, float]]]:
    """{series name: [(time, value)]} from http/timeseries; a value that is not a number is left out."""
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ValueError("Radar did not answer success")
    out = {}
    for name, s in (data.get("result") or {}).items():
        if name == "meta" or not isinstance(s, dict):
            continue
        ts, vals = s.get("timestamps") or [], s.get("values") or []
        pts = []
        for t, v in zip(ts, vals):
            try:
                pts.append((str(t), float(v)))
            except (TypeError, ValueError):
                continue
        out[name] = pts
    if not out:
        raise ValueError("no series in the response")
    return out


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def sparse(points: list[tuple[str, float]]) -> str | None:
    """Why a series is too thin to measure (see SPARSE_ZERO_SHARE), or None."""
    vals = [v for _, v in points]
    if not vals:
        return "no points"
    zeros = sum(1 for v in vals if v == 0)
    if zeros > SPARSE_ZERO_SHARE * len(vals):
        return f"too thin to measure: {zeros} of {len(vals)} points at zero"
    if len(set(vals)) < min(SPARSE_MIN_LEVELS, len(vals)):
        return f"too thin to measure: {len(set(vals))} distinct levels in {len(vals)} points"
    return None


def percent_of_normal(points: list[tuple[str, float]], hours: int = CURRENT_HOURS, days: int = NORMAL_DAYS) -> dict:
    """The mean of the last `hours` hourly points against the median, over the `days` days before, of the mean of the
    same clock hours on each day. {pct, current_from, current_to, normal_days} with pct None when there is no
    normal (fewer than MIN_NORMAL_DAYS days with every hour present, or a normal of zero)."""
    from datetime import datetime, timedelta
    thin = sparse(points)
    if thin:
        return {"pct": None, "why": thin, "normal_days": 0}
    by = {datetime.fromisoformat(t.replace("Z", "+00:00")): v for t, v in points}
    if len(by) < hours:
        return {"pct": None, "why": "too few hours", "normal_days": 0}
    cur_t = sorted(by)[-hours:]
    current = sum(by[t] for t in cur_t) / hours
    means = []
    for k in range(1, days + 1):
        vals = [by.get(t - timedelta(days=k)) for t in cur_t]
        if all(v is not None for v in vals):
            means.append(sum(vals) / hours)
    out = {"current_from": _ts(cur_t[0]), "current_to": _ts(cur_t[-1] + timedelta(hours=1)),
           "normal_days": len(means), "pct": None}
    if len(means) < MIN_NORMAL_DAYS:
        out["why"] = f"{len(means)} of {days} days with those hours"
        return out
    normal = _median(means)
    if normal <= 0:
        out["why"] = "no traffic in the normal"
        return out
    out["pct"] = round(100.0 * current / normal, 1)
    return out


def daily_percent(points: list[tuple[str, float]], days: int = NORMAL_DAYS) -> dict[str, float]:
    """{UTC day: that day's value as a percent of the median of the `days` days before}, for each day that has at
    least MIN_NORMAL_DAYS of them and a normal above zero."""
    if sparse(points):
        return {}
    vals = {t[:10]: v for t, v in points}
    order = sorted(vals)
    out = {}
    for i, d in enumerate(order):
        prior = [vals[x] for x in order[max(0, i - days):i]]
        if len(prior) < MIN_NORMAL_DAYS:
            continue
        normal = _median(prior)
        if normal > 0:
            out[d] = round(100.0 * vals[d] / normal, 1)
    return out


def _one(data: dict) -> list[tuple[str, float]]:
    """The points of a single-series answer."""
    got = parse_timeseries(data)
    if len(got) != 1:
        raise ValueError(f"{len(got)} series in a one-series answer")
    return next(iter(got.values()))


def _radar_network(n: dict, now, radar_fetch, trend_first) -> tuple[dict, dict | None, list[dict]]:
    """(reading, daily, problems) for one network."""
    from datetime import timedelta
    asn = n["asn"]
    limit = max(TOP_COUNTRIES, TOP_COUNTRIES_BY_ASN.get(asn, 0))
    problems = []
    try:
        locs = parse_locations(radar_fetch(locations_url(asn, limit)))
    except Exception as exc:  # noqa: BLE001 - listed; nothing else is read for this network
        return ({"seen": None}, None,
                [{"source": "satcom:radar", "asn": f"AS{asn}", "error": f"top locations: {type(exc).__name__}: {exc}"[:200]}])
    if not locs:
        return {"seen": False, "top_locations": []}, None, []
    measured = [x["cc"] for x in locs if x["share_pct"] >= MIN_SHARE_PCT][:limit]
    series = [("world", asn, None)] + [(cc, asn, cc) for cc in measured]
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=HOURLY_DAYS)
    reading = {"seen": True, "top_locations": locs, "world": None, "countries": {}}
    for name, a, cc in series:
        try:
            m = percent_of_normal(_one(radar_fetch(timeseries_url([(name, a, cc)], start, end, "1h"))))
        except Exception as exc:  # noqa: BLE001
            problems.append({"source": "satcom:radar", "asn": f"AS{asn}",
                             "error": f"hourly {name}: {type(exc).__name__}: {exc}"[:200]})
            continue
        if cc:
            reading["countries"][cc] = m
        else:
            reading["world"] = m
    daily = None
    if trend_first is not None:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        dstart = trend_first - timedelta(days=NORMAL_DAYS)
        first = trend_first.strftime("%Y-%m-%d")
        daily = {"world": None, "countries": {}}
        for name, a, cc in (series if asn in DAILY_COUNTRIES_ASNS else series[:1]):
            try:
                pts = _one(radar_fetch(timeseries_url([(name, a, cc)], dstart, today, "1d")))
            except Exception as exc:  # noqa: BLE001
                problems.append({"source": "satcom:radar", "asn": f"AS{asn}",
                                 "error": f"daily {name}: {type(exc).__name__}: {exc}"[:200]})
                continue
            days = {d: v for d, v in daily_percent(pts).items() if d >= first}
            if cc:
                daily["countries"][cc] = days
            else:
                daily["world"] = days
    return reading, daily, problems


def _ioda_network(asn: int, now_ts: int, ioda_fetch) -> tuple[dict | None, list[dict]]:
    """IODA's BGP and active probing for one network over the last ioda_hours against the 7 days before."""
    from . import asn_watch, ioda
    import urllib.parse
    q = urllib.parse.urlencode({"from": now_ts - IODA_DAYS * 86400, "until": now_ts, "maxPoints": 600})
    try:
        series = {s.get("datasource"): s for s in ioda._series(ioda_fetch(f"{ioda.API}/signals/raw/asn/{asn}?{q}"))}
    except Exception as exc:  # noqa: BLE001
        return None, [{"source": "satcom:ioda", "asn": f"AS{asn}", "error": f"{type(exc).__name__}: {exc}"[:200]}]
    start = now_ts - asn_watch.CURRENT_HOURS * 3600
    out = {"window_start": _ts_epoch(start), "window_end": _ts_epoch(now_ts), "tracked": bool(series)}
    for ds, key in (("bgp", "bgp_pct"), ("ping-slash24", "probing_pct")):
        s = series.get(ds)
        m = ioda.signal_drop(s, start, now_ts) if s else None
        out[key] = None
        if m is None:
            continue
        if m["baseline"] < ioda.MIN_BASELINE[ds]:
            out.setdefault("low_count", {})[ds] = round(m["baseline"], 1)
            continue
        if m["baseline"] > 0:
            out[key] = round(100.0 * m["current"] / m["baseline"], 1)
    return out, []


def _ts_epoch(t: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_traffic(now=None, radar_fetch=None, ioda_fetch=None, networks: list[dict] | None = None,
                  trend_first=None, workers: int = WORKERS) -> tuple[dict, dict | None, list[dict]]:
    """(traffic, daily, problems). traffic is what the page shows; daily ({asn: {world, countries}}, the per-day
    percent of normal from trend_first, a UTC midnight) goes to trends.json only. A read that fails is listed in
    problems and leaves that network's field None; nothing is carried over or filled in."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from . import ioda, radar
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    radar_fetch = radar_fetch or radar._get
    ioda_fetch = ioda_fetch or ioda._get_json
    nets = networks if networks is not None else counted()
    problems: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        r_jobs = [pool.submit(_radar_network, n, now, radar_fetch, trend_first) for n in nets]
        i_jobs = {n["asn"]: pool.submit(_ioda_network, n["asn"], int(now.timestamp()), ioda_fetch)
                  for n in nets if n.get("confidence") == "confirmed"}
        rows, daily = [], {}
        for n, job in zip(nets, r_jobs):
            reading, d, probs = job.result()
            problems += probs
            row = {"asn": n["asn"], "operator": n.get("operator"), "class": n.get("class"), "radar": reading,
                   "ioda": None}
            if n["asn"] in i_jobs:
                row["ioda"], iprobs = i_jobs[n["asn"]].result()
                problems += iprobs
            rows.append(row)
            if d is not None:
                daily[str(n["asn"])] = {"operator": n.get("operator"), "class": n.get("class"), **d}
    radar_ok = not any(p["source"] == "satcom:radar" for p in problems)
    traffic = {"checked_at": _ts(now), "normal_days": NORMAL_DAYS, "current_hours": CURRENT_HOURS,
               "ioda_hours": _ioda_hours(), "min_share_pct": MIN_SHARE_PCT,
               "read": {"radar": radar_ok, "ioda": not any(p["source"] == "satcom:ioda" for p in problems)},
               "networks": rows}
    return traffic, (daily if trend_first is not None else None), problems


def _ioda_hours() -> int:
    from . import asn_watch
    return asn_watch.CURRENT_HOURS
