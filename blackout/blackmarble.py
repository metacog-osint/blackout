"""Night lights (BO-WP-5): NASA Black Marble, the moon- and surface-corrected night radiance over watched cities.

Black Marble's daily product (VJ146A2 from NOAA-20; the same algorithm as Suomi NPP's VNP46A2) gives, per 15
arc-second pixel (about 500 m), the night radiance with moonlight and surface effects removed, a quality flag and a
cloud mask, in 10 by 10 degree tiles (h00-h35, v00-v17) as HDF5. Freshness, measured 13 Nov 2026 (the plan said to
measure it before promising any):

  VJ146A2_NRT   near real time (NASA LANCE, nrt3.modaps.eosdis.nasa.gov): the night of 12 Nov was out by 04:14 UTC
                13 Nov. LANCE keeps about a week: on 13 Nov it served 6-12 Nov, and 5 Nov was still listed in CMR
                but its files were gone (HTTP 404).
  VJ146A2       standard (LAADS, Earthdata Cloud): newest night 28 Oct on 13 Nov for all 15 watched tiles (none
                listed for 29 Oct - 6 Nov), about 16 days late; an earlier note here said 5 Nov, which CMR did not
                bear out. Suomi NPP's VNP46A2 was 11 days late (newest 2 Nov). Used only for nights LANCE no longer
                holds, so nights between LANCE's week and the standard product's lag are read by neither.

NASA posts a night's tiles one by one over about two hours the next morning (17 Nov 2026: 04:49 to 06:31 UTC, the
Americas' tiles first), and the 05:45 UTC run reads whatever is posted by then. So a night is held per city: a city
whose tile wasn't posted yet is read again on a later run (only its own tiles), and a tile not yet listed for the
newest night (POSTING_DAYS) is not a problem. Until 18 Nov 2026 a night read for any city was held for all of them,
which left 19 of the 23 cities with no night after 12 Nov.

LANCE's catalogue keeps listing a night for a while after its files are deleted: on 13 Nov 2026 CMR still listed 14
tiles of 5 Nov and every download came back HTTP 404 (15 problems a run, and the "Incomplete collection" banner). So
when every listed tile of a product is 404, that product no longer holds the night: the next product is tried, and if
none has it the night is a gap (nights_gone), not a problem. A night FRESH_DAYS old or newer that is gone everywhere
is still one problem, since LANCE should hold it.

Both need an Earthdata Login token (env EARTHDATA_TOKEN, the one Noisefloor uses; Earthdata tokens last 60 days, so
the collector warns two weeks before it expires, reading only the token's own expiry claim). LAADS and LANCE also
need the account to have accepted LAADS's data licence once: until then they redirect every download to
/profiles/licenses/ (both did on 13 Nov 2026), while the Earthdata Cloud copy of the standard product already served
data. Files are read with h5py (the one exception to Blackout's standard-library rule, since 13 Nov 2026): 15 tiles
cover the 23 cities, downloaded whole, WORKERS at a time (see the note on downloads).

Per city and night: the pixels in a box of +-BOX_DEG latitude around the city (longitude widened by 1/cos(lat), so
the box is about 44 km square) that are high quality (Mandatory_Quality_Flag 0) and clear (QF_Cloud_Mask bits 6-7
confident or probably clear). A night counts when at least MIN_CLEAR of the box is clear; its value is the mean
radiance of the clear pixels (nW/cm2/sr). A cloudy night says nothing, and is never read as lights on.

Rule (BO-IND-15): the newest counted night, no older than FRESH_DAYS, is DROP or more below the median of the counted
nights in the BASELINE_NIGHTS before it (at least MIN_BASELINE of them). A measurement, reported as one: a drop in
light fits a grid failure, strikes on the grid, rationing or load shedding, and also smoke, dust or snow the cloud
mask missed. Blackout says how much darker it was, not why.

Licence: NASA states "there are no restrictions on the use of these data" and strongly urges citing them
(earthdata.nasa.gov/learn/find-data/near-real-time/citation, read 13 Nov 2026); near-real-time data come "as is"
under the LANCE disclaimer, which advises against using them for tactical decisions or local-scale conditions
(earthdata.nasa.gov/data/projects/lance/disclaimer, read 13 Nov 2026). Cited with each product's DOI.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import median

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
NRT, STANDARD = "VJ146A2_NRT", "VJ146A2"
DOI = {NRT: "10.5067/VIIRS/VJ146A2_NRT.002", STANDARD: "10.5067/VIIRS/VJ146A2.002"}
PAGE = "https://www.earthdata.nasa.gov/data/projects/black-marble"
LICENCE = ("NASA Earth science data: no restrictions on use, citation requested (Black Marble VJ146A2, DOI "
           "10.5067/VIIRS/VJ146A2.002, and its near-real-time version from NASA LANCE); near-real-time data are "
           "provided as is under the LANCE disclaimer")
FIELDS = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields"
USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"
BOX_DEG = 0.2
MIN_CLEAR = 0.6
BASELINE_NIGHTS = 14
MIN_BASELINE = 5
DROP = 0.4            # see CALIBRATION
MIN_BASE_RADIANCE = 1.0   # nW/cm2/sr: below this a city is already too dark for a drop to mean anything
FRESH_DAYS = 3
POSTING_DAYS = 1      # a night this new may still be being posted: a tile not listed yet is read next run
KEEP_NIGHTS = 30
TOKEN_WARN_DAYS = 14
CALIBRATION = """
Calibrated 13 Nov 2026 on 30 real nights (VJ146A2, 29 Sep - 28 Oct 2026) for all 23 cities, through read_night
(tests/fixtures/blackmarble/nights_VJ146A2_2026-09-29_to_2026-10-28.json). 254 nights could be set against a
baseline; their change against it: 1st percentile -60%, 5th -34%, median 0, 95th +69%. At DROP 0.4 and
MIN_BASE_RADIANCE 1.0, six nights flag (of 229 with a bright enough baseline):
  Kyiv 9 Oct -42%       the night after Russia's 8 Oct strike on the grid; Ukrenergo cut power in Kyiv
                        (Kyiv Independent; Euromaidan Press, 8 Oct 2026)
  Khartoum 27 Oct -74%, 28 Oct -78%   a drone strike put the Al-Markhiyat transformer station in Omdurman out of
                        service early on 27 Oct; large parts of the capital lost power for over 12 hours
                        (Sudan Tribune; AP; Radio Tamazuj, 27-28 Oct 2026)
  Havana 13 Oct -51%    Cuba's eastern grid split on 10 Oct with a 1,680 MW shortfall; planned cuts neared 75% of
                        demand by 16 Oct (Rio Times; Eastern Herald)
  Dnipro 10 Oct -60%, 13 Oct -48%   not found in reporting (unverified)
Cities already darker than 1.0 nW/cm2/sr (Kharkiv and Zaporizhzhia about 0.1-0.3, Odesa about 0.7, Port-au-Prince
about 0.4) read too_dark: Zaporizhzhia's -58% on 22 Oct is not published. Partial cloud biases the mean toward the
lit centre (Kyiv 25 Oct, 20% clear: 27.2 against 6.4 on a 69%-clear night), hence MIN_CLEAR 0.6.
"""

# Cities where losing the grid matters to Blackout's questions: war-damaged or rationed grids, and grids that have
# failed nationwide. Add a city here; a tile it needs is fetched with the rest.
CITIES = [
    {"id": "kyiv", "name": "Kyiv", "country": "UA", "lat": 50.450, "lon": 30.523},
    {"id": "kharkiv", "name": "Kharkiv", "country": "UA", "lat": 49.994, "lon": 36.231},
    {"id": "odesa", "name": "Odesa", "country": "UA", "lat": 46.482, "lon": 30.723},
    {"id": "dnipro", "name": "Dnipro", "country": "UA", "lat": 48.465, "lon": 35.046},
    {"id": "zaporizhzhia", "name": "Zaporizhzhia", "country": "UA", "lat": 47.838, "lon": 35.140},
    {"id": "lviv", "name": "Lviv", "country": "UA", "lat": 49.839, "lon": 24.030},
    {"id": "belgorod", "name": "Belgorod", "country": "RU", "lat": 50.596, "lon": 36.588},
    {"id": "gaza", "name": "Gaza City", "country": "PS", "lat": 31.502, "lon": 34.467},
    {"id": "beirut", "name": "Beirut", "country": "LB", "lat": 33.894, "lon": 35.502},
    {"id": "damascus", "name": "Damascus", "country": "SY", "lat": 33.513, "lon": 36.292},
    {"id": "baghdad", "name": "Baghdad", "country": "IQ", "lat": 33.315, "lon": 44.366},
    {"id": "tehran", "name": "Tehran", "country": "IR", "lat": 35.689, "lon": 51.389},
    {"id": "sanaa", "name": "Sanaa", "country": "YE", "lat": 15.369, "lon": 44.191},
    {"id": "aden", "name": "Aden", "country": "YE", "lat": 12.785, "lon": 45.019},
    {"id": "khartoum", "name": "Khartoum", "country": "SD", "lat": 15.501, "lon": 32.560},
    {"id": "port-sudan", "name": "Port Sudan", "country": "SD", "lat": 19.616, "lon": 37.216},
    {"id": "tripoli", "name": "Tripoli", "country": "LY", "lat": 32.887, "lon": 13.191},
    {"id": "kabul", "name": "Kabul", "country": "AF", "lat": 34.528, "lon": 69.172},
    {"id": "yangon", "name": "Yangon", "country": "MM", "lat": 16.840, "lon": 96.173},
    {"id": "caracas", "name": "Caracas", "country": "VE", "lat": 10.491, "lon": -66.902},
    {"id": "havana", "name": "Havana", "country": "CU", "lat": 23.113, "lon": -82.366},
    {"id": "san-juan", "name": "San Juan", "country": "PR", "lat": 18.466, "lon": -66.106},
    {"id": "port-au-prince", "name": "Port-au-Prince", "country": "HT", "lat": 18.543, "lon": -72.339},
]


# --- the token: only its own expiry claim is read; the value is never printed or stored -------------------------

def token() -> str | None:
    return (os.environ.get("EARTHDATA_TOKEN") or "").strip() or None


def token_expiry(tok: str | None) -> datetime | None:
    try:
        part = tok.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
    except Exception:
        return None


# --- downloads: whole tiles, a few at a time ----------------------------------------------------------------------
# Measured 13 Nov 2026 on three VJ146A2 tiles: whole files (11-33 MB) took 4-5 s each, and HTTP range reads of only
# the rows around Kyiv (6-7 requests, about 1.4 MB) took 5-7 s, bound by latency. h5py serialises reads from a Python
# file object, so range reads can't overlap; whole downloads can. WORKERS tiles download at once.
WORKERS = 4
LICENCE_GATE = ("LAADS data licence not accepted for this Earthdata account (download redirected to "
                "/profiles/licenses/); sign in at urs.earthdata.nasa.gov and accept it once")


class _LicenceCheck(urllib.request.HTTPRedirectHandler):
    """Name the licence gate: LAADS and LANCE answer 303 to /profiles/licenses/, then Earthdata Login answers 401."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if "/profiles/licenses/" in newurl:
            raise PermissionError(LICENCE_GATE)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, tok: str | None = None, timeout: float = 180) -> bytes:
    """A whole tile. The bearer token goes only to the host the URL names (urllib re-sends it on a redirect to the
    same host only; LAADS hands downloads to a signed CloudFront URL, which ignores it)."""
    tok = tok if tok is not None else token()
    headers = {"User-Agent": USER_AGENT}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    opener = urllib.request.build_opener(_LicenceCheck)
    with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        if "/profiles/licenses/" in r.geturl():
            raise PermissionError(LICENCE_GATE)
        data = r.read(200_000_000)
    if data[:8] != b"\x89HDF\r\n\x1a\n":
        raise ValueError("not an HDF5 file (an HTML page in its place: login or licence)")
    return data


# --- geometry ---------------------------------------------------------------------------------------------------

def box(city: dict) -> tuple[float, float, float, float]:
    """(south, north, west, east) around the city, about 44 km square."""
    dlon = BOX_DEG / max(math.cos(math.radians(city["lat"])), 0.2)
    return city["lat"] - BOX_DEG, city["lat"] + BOX_DEG, city["lon"] - dlon, city["lon"] + dlon


def tiles(b: tuple[float, float, float, float]) -> list[str]:
    """Black Marble tiles (hHHvVV) the box touches."""
    s, n, w, e = b
    hs = range(int((w + 180) // 10), int((e + 180 - 1e-9) // 10) + 1)
    vs = range(int((90 - n) // 10), int((90 - s - 1e-9) // 10) + 1)
    return [f"h{h:02d}v{v:02d}" for v in vs for h in hs]


def box_sums(h5, b: tuple[float, float, float, float]) -> tuple[float, int, int]:
    """(radiance summed over clear high-quality pixels, clear pixels, all pixels) for the part of the box in this
    tile."""
    import numpy as np
    d = h5[FIELDS]
    lat, lon = d["lat"][:], d["lon"][:]
    s, n, w, e = b
    rows = np.nonzero((lat >= s) & (lat < n))[0]
    cols = np.nonzero((lon >= w) & (lon < e))[0]
    if not len(rows) or not len(cols):
        return 0.0, 0, 0
    sl = (slice(int(rows[0]), int(rows[-1]) + 1), slice(int(cols[0]), int(cols[-1]) + 1))
    ntl = d["DNB_BRDF-Corrected_NTL"][sl]
    mqf = d["Mandatory_Quality_Flag"][sl]
    cloud = (d["QF_Cloud_Mask"][sl] >> 6) & 3
    ok = (mqf == 0) & (cloud <= 1) & (ntl >= 0)
    return float(ntl[ok].sum()), int(ok.sum()), int(ok.size)


# --- finding and reading a night's tiles ------------------------------------------------------------------------

def _get_json(url: str, timeout: float = 60):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as r:
        return json.loads(r.read())


def search_url(product: str, night: str, tile_ids: list[str]) -> str:
    doy = datetime.strptime(night, "%Y-%m-%d").strftime("%Y%j")
    q = [("short_name", product), ("page_size", "100"), ("options[readable_granule_name][pattern]", "true")]
    q += [("readable_granule_name[]", f"{product}.A{doy}.{t}.*") for t in sorted(tile_ids)]
    return CMR + "?" + urllib.parse.urlencode(q)


def granule_links(doc: dict, product: str) -> dict[str, str]:
    """tile id -> download URL, from a CMR granules.json response (the Earthdata Cloud link when there is one)."""
    out = {}
    for g in (doc.get("feed") or {}).get("entry") or []:
        name = g.get("producer_granule_id") or ""
        parts = name.split(".")
        if len(parts) < 3 or parts[0] != product:
            continue
        links = [l["href"] for l in g.get("links") or [] if (l.get("rel") or "").endswith("/data#")
                 and l.get("href", "").endswith(".h5")]
        links.sort(key=lambda u: "earthdatacloud" not in u)
        if links:
            out[parts[2]] = links[0]
    return out


def _deleted(exc) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 404


def read_night(night: str, cities: list[dict], search=_get_json, fetch_tile=download,
               products=(NRT, STANDARD)) -> tuple[dict, list[dict]]:
    """{city id: {date, mean, clear, pixels, product}} for one night, from the first product that holds its tiles.

    A product that lists tiles it has deleted (every listed tile HTTP 404) doesn't hold the night, so the next one is
    tried. When none holds it and one had deleted it, the one problem returned carries "gone" (fetch_blackmarble
    decides whether that is a gap or a fault)."""
    need = {c["id"]: box(c) for c in cities}
    tile_ids = sorted({t for b in need.values() for t in tiles(b)})
    problems, gone = [], []
    for p in products:
        try:
            links = granule_links(search(search_url(p, night, tile_ids)), p)
        except Exception as exc:
            problems.append({"endpoint": f"cmr-{p}", "error": f"{night}: {type(exc).__name__}: {exc}"[:200]})
            continue
        if not links:
            continue
        got = _read_tiles(night, p, links, need, tile_ids, fetch_tile)
        if got is None:
            gone.append(f"{p} lists {len(links)} tiles but every download was HTTP 404 (deleted)")
            continue
        return got[0], problems + got[1]
    if gone:
        problems.append({"endpoint": "night-gone", "error": f"{night}: " + "; ".join(gone), "gone": True})
    return {}, problems


def _read_tiles(night: str, product: str, links: dict, need: dict, tile_ids: list[str], fetch_tile):
    """(per-city readings, problems) from one product's tiles, or None when every listed tile was HTTP 404."""
    import h5py
    from concurrent.futures import ThreadPoolExecutor
    sums = {cid: [0.0, 0, 0] for cid in need}

    def get(t):
        try:
            return t, fetch_tile(links[t]), None
        except Exception as exc:
            return t, None, exc
    listed = [t for t in tile_ids if t in links]
    problems = [{"endpoint": f"tile-{product}", "error": f"{night} {t}: not listed"} for t in tile_ids if t not in links]
    deleted = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for t, data, exc in pool.map(get, listed):  # streamed: a tile is about 33 MB, so they aren't all held at once
            deleted += _deleted(exc)
            try:
                if exc:
                    raise exc
                with h5py.File(io.BytesIO(data), "r") as h5:
                    for cid, b in need.items():
                        if sums[cid] is not None and t in tiles(b):
                            s, c, n = box_sums(h5, b)
                            sums[cid][0] += s
                            sums[cid][1] += c
                            sums[cid][2] += n
            except Exception as e:
                problems.append({"endpoint": f"tile-{product}", "error": f"{night} {t}: {type(e).__name__}: {e}"[:200]})
                for cid, b in need.items():  # a city half-read is not read
                    if t in tiles(b):
                        sums[cid] = None
    if listed and deleted == len(listed):
        return None
    for cid, b in need.items():  # nor is one whose tile wasn't listed
        if sums[cid] is not None and any(t not in links for t in tiles(b)):
            sums[cid] = None
    out = {}
    for cid, v in sums.items():
        if v is None or not v[2]:
            continue
        out[cid] = {"date": night, "mean": round(v[0] / v[1], 3) if v[1] else None, "clear": round(v[1] / v[2], 3),
                    "pixels": v[2], "product": product}
    return out, problems


# --- the judgment-free part: how much darker than its own recent nights ------------------------------------------

def assess(nights: list[dict], now: datetime) -> dict:
    """Latest counted night against the median of the counted nights in the BASELINE_NIGHTS before it."""
    counted = sorted((n for n in nights if n.get("mean") is not None and n["clear"] >= MIN_CLEAR),
                     key=lambda n: n["date"])
    newest = max((n["date"] for n in nights), default=None)
    if not nights:
        return {"state": "not_read", "newest_night": None}
    if not counted:
        return {"state": "no_clear_night", "newest_night": newest}
    last = counted[-1]
    start = (datetime.strptime(last["date"], "%Y-%m-%d") - timedelta(days=BASELINE_NIGHTS)).strftime("%Y-%m-%d")
    base = [n["mean"] for n in counted[:-1] if n["date"] >= start]
    age = (now.date() - datetime.strptime(last["date"], "%Y-%m-%d").date()).days
    out = {"newest_night": newest, "latest": last, "age_days": age, "baseline_nights": len(base)}
    if len(base) < MIN_BASELINE:
        return {**out, "state": "no_baseline"}
    med = median(base)
    change = (last["mean"] - med) / med if med > 0 else None
    out.update({"baseline_median": round(med, 3), "change": round(change, 3) if change is not None else None})
    if age > FRESH_DAYS:
        return {**out, "state": "stale"}
    if med < MIN_BASE_RADIANCE:
        return {**out, "state": "too_dark"}
    return {**out, "state": "darker" if change is not None and change <= -DROP else "normal"}


def merge(history: dict | None, new: dict[str, dict[str, dict]]) -> dict[str, list[dict]]:
    """Per city, the nights kept from before plus the new ones (a re-read night replaces the old copy)."""
    out = {}
    for c in CITIES:
        by_date = {n["date"]: n for n in ((history or {}).get(c["id"]) or [])}
        for night, per_city in new.items():
            if c["id"] in per_city:
                by_date[night] = per_city[c["id"]]
        out[c["id"]] = [by_date[d] for d in sorted(by_date)][-KEEP_NIGHTS:]
    return out


def fetch_blackmarble(history: dict | None = None, search=_get_json, fetch_tile=download, now: datetime | None = None,
                      tok: str | None = None, max_new: int = BASELINE_NIGHTS + 2) -> tuple[dict | None, list[dict]]:
    """Read the nights not yet held for every city (newest first, at most max_new; for each, only the cities that lack
    it), merge with the held ones, assess each city."""
    now = now or datetime.now(timezone.utc)
    tok = tok if tok is not None else token()
    if not tok:
        return None, [{"endpoint": "earthdata-token", "error": "EARTHDATA_TOKEN is not set; no night lights read"}]
    problems = []
    exp = token_expiry(tok)
    if exp and exp - now < timedelta(days=TOKEN_WARN_DAYS):
        problems.append({"endpoint": "earthdata-token", "error": f"the Earthdata token expires {exp:%Y-%m-%d %H:%M} "
                         "UTC; make a new one at urs.earthdata.nasa.gov and set it as EARTHDATA_TOKEN"})
    held = {c["id"]: {n["date"] for n in (history or {}).get(c["id"]) or []} for c in CITIES}
    wanted = [(now - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(1, BASELINE_NIGHTS + 3)]
    new, read, gone = {}, 0, []
    for night in wanted:
        todo = [c for c in CITIES if night not in held[c["id"]]]
        if not todo or read >= max_new:
            continue
        age = (now.date() - datetime.strptime(night, "%Y-%m-%d").date()).days
        got, p = read_night(night, todo, search=search, fetch_tile=fetch_tile)
        gated = [x for x in p if LICENCE_GATE in x["error"]]
        if gated:  # the gate is per account, so every tile and night would hit it: one problem, and stop reading
            problems += [x for x in p if LICENCE_GATE not in x["error"]]
            problems.append({"endpoint": gated[0]["endpoint"], "error": f"{night}, {len(gated)} tiles: {LICENCE_GATE}"})
            break
        for x in p:
            if not x.pop("gone", False):
                if age <= POSTING_DAYS and x["endpoint"].startswith("tile-") and x["error"].endswith(": not listed"):
                    continue  # not posted yet: that city is read on a later run
                problems.append(x)
            elif age > FRESH_DAYS:
                gone.append(night)  # deleted from LANCE and not yet in the standard archive: a gap, not a fault
            else:
                problems.append(x)  # LANCE should still hold a night this new
        read += 1
        if got:
            new[night] = got
    nights = merge(history, new)
    cities = []
    for c in CITIES:
        cities.append({**c, "box": [round(x, 3) for x in box(c)], "assessment": assess(nights[c["id"]], now),
                       "nights": nights[c["id"]]})
    return {
        "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule": {"drop": DROP, "min_base_radiance": MIN_BASE_RADIANCE, "min_clear": MIN_CLEAR,
                 "baseline_nights": BASELINE_NIGHTS,
                 "min_baseline": MIN_BASELINE, "fresh_days": FRESH_DAYS, "box_deg": BOX_DEG},
        "token_expires": exp.strftime("%Y-%m-%dT%H:%M:%SZ") if exp else None,
        "nights_read": sorted(new),
        "nights_gone": sorted(gone),
        "cities": cities,
        "darker": [c["id"] for c in cities if c["assessment"]["state"] == "darker"],
    }, problems
