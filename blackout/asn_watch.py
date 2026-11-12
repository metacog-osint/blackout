"""IODA watch on a fixed list of provider networks (ASNs), for provider-level outages (BO-WP-3, BO-IND-6).

Same method as the country signals in ioda.py, checked against the real API on 12 Nov 2026:
  1. /v2/outages/events?entityType=asn&entityCode=<asn> for the last 24 h. IODA's BGP and active-probing events on
     the network set the window to measure.
  2. /v2/signals/raw/asn/<asn> for bgp and ping-slash24, from 7 days before the 24-hour lookback to now (IODA returns
     30-minute points at maxPoints 600). With no IODA event, the window is the longest run of points in the lookback
     sitting >= 5% under the median of the 7 days before it; with neither, it is the last 3 hours.
  3. The drop is the median during the window against the median of the 7 days before the window, per signal
     (ioda.signal_drop). A signal that can't be read stays None and its failure goes into the problems list.

Week of 5-12 Nov 2026: the largest 3-hour dip on any of the five networks was 1.6% (Starlink active probing), so the
5% threshold sits about three times above that week's noise.
"""
from __future__ import annotations

import statistics
import urllib.parse
from datetime import datetime, timezone

from . import ioda

# Fixed watch list (Tom, 12 Nov 2026). Add networks here; provider is the key used for concurrency (BO-IND-8).
WATCHLIST = [
    {"asn": 14593, "provider": "starlink", "label": "Starlink (SpaceX)"},
    {"asn": 16509, "provider": "amazon", "label": "Amazon (AWS)"},
    {"asn": 8075, "provider": "microsoft", "label": "Microsoft"},
    {"asn": 15169, "provider": "google", "label": "Google"},
    {"asn": 396982, "provider": "google", "label": "Google Cloud"},
]
SIGNALS = {"bgp": "bgp_drop_pct", "ping-slash24": "probing_drop_pct"}
LABELS = {"bgp": "BGP", "ping-slash24": "active probing"}

LOOKBACK_HOURS = 24
CURRENT_HOURS = 3
IND6_DROP_PCT = 5.0      # BO-IND-6: BGP or active probing >= 5% under the 7-day median
IND6_MIN_MINUTES = 30    # ... over a window of at least 30 minutes


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _low_run(series: dict, lookback_start: int, now: int) -> tuple[int, int] | None:
    """(start, end) of the longest run of points in [lookback_start, now) sitting >= IND6_DROP_PCT under the median of
    the 7 days before lookback_start; the latest run wins a tie. None if no point is that low."""
    t0, step = int(series.get("from", 0)), int(series.get("step") or 0)
    vals = series.get("values") or []
    if not step or not vals:
        return None
    base = [float(v) for i, v in enumerate(vals) if isinstance(v, (int, float)) and not isinstance(v, bool)
            and lookback_start - ioda.BASELINE_DAYS * 86400 <= t0 + i * step < lookback_start]
    if len(base) < 3:
        return None
    floor = statistics.median(base) * (1 - IND6_DROP_PCT / 100.0)
    best, run = None, None
    for i, v in enumerate(vals):
        t = t0 + i * step
        if t < lookback_start or t >= now:
            continue
        low = isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) <= floor
        if low:
            run = (run[0], t + step) if run else (t, t + step)
            if best is None or run[1] - run[0] >= best[1] - best[0]:
                best = run
        else:
            run = None
    return best


def fetch_asn_watch(watchlist: list[dict] | None = None, fetch=ioda._get_json, now: int | None = None
                    ) -> tuple[list[dict], list[dict]]:
    """One record per watched network that could be read: (records, problems). Nothing is estimated for a signal or
    a network that couldn't be read; its failure is listed in problems instead."""
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    lookback = now - LOOKBACK_HOURS * 3600
    records, problems = [], []
    for w in watchlist or WATCHLIST:
        asn = int(w["asn"])
        rec = {"asn": f"AS{asn}", "provider": w["provider"], "label": w["label"], "asn_name": None,
               "ioda_events": 0, "ioda_score": None, "ioda_datasources": [],
               "bgp_drop_pct": None, "probing_drop_pct": None, "bgp_zscore": None, "probing_zscore": None,
               "indicator_id": None,
               "baseline_ref": f"median of the {ioda.BASELINE_DAYS} days before the window, per IODA datasource"}
        span = None
        q = urllib.parse.urlencode({"from": lookback, "until": now, "entityType": "asn", "entityCode": asn,
                                    "limit": 500})
        try:
            payload = fetch(f"{ioda.API}/outages/events?{q}")
            events = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(events, list):
                raise ValueError("no data list in the response")
            for ev in events:
                if not isinstance(ev, dict) or ev.get("datasource") not in SIGNALS:
                    continue
                try:
                    s = int(ev["start"])
                    e = s + int(ev.get("duration") or 0)
                except (KeyError, TypeError, ValueError):
                    continue
                span = (min(span[0], s), max(span[1], e)) if span else (s, e)
                rec["ioda_events"] += 1
                rec["ioda_score"] = max(rec["ioda_score"] or 0.0, round(float(ev.get("score") or 0.0), 1))
                rec["ioda_datasources"] = sorted(set(rec["ioda_datasources"]) | {ev["datasource"]})
        except Exception as exc:  # events missing: measure the signals anyway, and say so
            problems.append({"asn": rec["asn"], "datasource": "events", "error": f"{type(exc).__name__}: {exc}"[:200]})

        series = {}
        first = min(span[0], lookback) if span else lookback  # an event that began earlier needs its own 7 days
        for ds in SIGNALS:
            sq = urllib.parse.urlencode({"from": first - ioda.BASELINE_DAYS * 86400, "until": now,
                                         "datasource": ds, "maxPoints": 600})
            try:
                found = [x for x in ioda._series(fetch(f"{ioda.API}/signals/raw/asn/{asn}?{sq}"))
                         if x.get("datasource") == ds]
                if not found:
                    raise ValueError(f"no {ds} series in the response")
                series[ds] = found[0]
                rec["asn_name"] = rec["asn_name"] or found[0].get("entityName")
            except Exception as exc:
                problems.append({"asn": rec["asn"], "datasource": ds, "error": f"{type(exc).__name__}: {exc}"[:200]})
        if not series:
            continue  # nothing read for this network: listed in problems, no record

        if span:
            basis = "IODA outage events on this network"
        else:
            runs = [r for r in (_low_run(s, lookback, now) for s in series.values()) if r]
            span = max(runs, key=lambda r: (r[1] - r[0], r[1])) if runs else None
            basis = f"points >= {IND6_DROP_PCT:g}% under the 7-day median" if span else f"last {CURRENT_HOURS} h"
            span = span or (now - CURRENT_HOURS * 3600, now)
        start, end = span[0], min(span[1], now)
        rec.update({"window_start": _iso(start), "window_end": _iso(end), "window_minutes": (end - start) // 60,
                    "window_basis": basis})
        for ds, s in series.items():
            m = ioda.signal_drop(s, start, end)
            if m is None:
                continue
            key = "bgp" if ds == "bgp" else "probing"
            rec[SIGNALS[ds]] = m["drop_pct"]
            rec[f"{key}_zscore"] = m["zscore"]
        measured = [v for v in (rec["bgp_drop_pct"], rec["probing_drop_pct"]) if v is not None]
        rec["drop_pct"] = max(measured) if measured else None
        if measured and rec["drop_pct"] >= IND6_DROP_PCT and rec["window_minutes"] >= IND6_MIN_MINUTES:
            rec["indicator_id"] = "BO-IND-6"
        parts = [f"{LABELS[ds]} -{rec[f]}%" if rec[f] else f"{LABELS[ds]} no drop"
                 for ds, f in SIGNALS.items() if rec[f] is not None]
        rec["summary"] = (f"{rec['label']} ({rec['asn']}): {', '.join(parts) or 'no signal measured'} over "
                          f"{rec['window_minutes']} min to {rec['window_end']} ({basis}), against the 7 days before.")
        records.append(rec)
    return records, problems
