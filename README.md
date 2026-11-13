# Blackout

**Blackout** (`blackout.theintelligence.company`) is The Intelligence Company's monitor of where the lights go out
and the internet goes dark: internet outages and shutdowns, censorship, routing integrity, submarine cable corridors,
cloud and satellite providers, and the power grid. It publishes a `tic-feed/2` feed (program `blackout`, prefix
`BO`) and a page with Overview, Outages, Censorship, Power, Routing, Cables, Providers and Method tabs.

Nearly everything it publishes is a measurement or a source's own report (`kind: observation`). It makes two kinds of
judgment, each needing two independent lines: a likely shutdown (an IODA outage with OONI-confirmed blocking in the
same country) and a cable-corridor outage (drops that coincide along a corridor).

## Sources

**Internet**
1. **IODA** (Georgia Tech): outage events by country, then each country's BGP, active-probing and darknet signals
   against the 7 days before. No data licence stated; credited.
2. **OONI**: censorship anomaly and confirmed-blocking rates per country. CC BY-NC-SA 4.0.
3. **Cloudflare Radar**: outages Cloudflare's analysts annotate, with the cause Cloudflare states, and traffic
   anomalies Cloudflare verified; reported, never assessed. Needs `CLOUDFLARE_RADAR_TOKEN`. CC BY-NC 4.0.
4. **Provider watch**: IODA on Starlink, Amazon, Microsoft and Google networks, and the open incidents on AWS, Google
   Cloud, Google Workspace, Azure, Microsoft 365, Apple, Cloudflare, Akamai, Slack, Zoom and Starlink status feeds.
5. **Submarine cable corridors**: a hand-compiled register of corridors and landing stations (no TeleGeography data),
   checked against coinciding outages and suite tips from Elements and Wake.

**Routing integrity**
6. **Cloudflare Radar BGP**: high-confidence origin hijacks and route leaks over 3 days, counted per country; feed
   items only for events seen widely (25 or more route-collector peers). Same token. CC BY-NC 4.0.

**Shutdown history**
7. **Access Now STOP**: the last full year of documented shutdowns per country, shown as context with a current
   outage, never as evidence about it. No licence stated for the data; credited, counts only.

**Power**
8. **Cloudflare Radar's stated cause**: outages Cloudflare attributes to a power outage.
9. **IAEA Ukraine updates**: the Zaporizhzhia nuclear plant's off-site power, quoted from the IAEA's numbered
   statements (via its RSS and the Wayback Machine, because iaea.org refuses scripts). Reuse with acknowledgement.
10. **ODIN** (Oak Ridge National Laboratory for DOE): US meters out by county from the utilities that report to ODIN;
    a feed item for a state above 50,000 (DOE-417's line). No key. No licence stated; credited.
11. **NASA Black Marble**: moon- and surface-corrected night radiance over watched cities (VJ146A2, NOAA-20, near
    real time from NASA LANCE), against each city's own recent clear nights. Needs `EARTHDATA_TOKEN` and the LAADS
    data licence accepted on that Earthdata account. No restrictions on use; citation requested.
12. **EIA-930** (U.S. Energy Information Administration): each US balancing authority's hourly demand against its
    own day-ahead forecast; a feed item when demand runs 25% or more under what that forecast usually implies (further
    where an authority's forecasts often miss) for 3 hours or more. Calibrated on real data: finds Hurricanes Milton
    and Helene and Winter Storm Uri, misses Beryl. Needs `EIA_API_KEY`. Public domain; acknowledged as "Source: U.S.
    Energy Information Administration" with the date read.

Every source, with its grade and licence, is in `doctrine/sources.json` and on the page's Method tab.

## Commands

```bash
python tools/blackout.py collect --live       # every source live; a failed source publishes nothing and is listed
python tools/blackout.py collect              # write the seed snapshot (made-up, status test)
python tools/blackout.py build                # build site/ and site/data/feed.geojson (tic-feed/2)
python tools/blackout.py check                # validate doctrine registers and lint contract-v2 feed
python -m unittest discover -s tests -v       # run offline test suite (Black Marble's tests need h5py)
python -P tests/make_eia_fixtures.py NAME NOW # record real EIA responses for a run at NOW (needs EIA_API_KEY)
python tools/serve.py 8746                    # serve site/ locally with its _headers (CSP enforced)
```

Python 3.11 or later, standard library only, except `h5py` (3.16.0) for Black Marble's HDF5 files.
