# Blackout

**Blackout** (`blackout.theintelligence.company`) is The Intelligence Company's connectivity, BGP routing, censorship, and undersea cable outage monitor.

It ingests public, free-tier telemetry from:
1. **Georgia Tech IODA (Internet Outage Detection and Analysis)** — BGP visibility, Merit-NT darknet, and active-probing outage events by country and ASN (`api.ioda.inetintel.cc.gatech.edu/v2/`).
2. **OONI (Open Observatory of Network Interference)** — aggregated censorship anomaly and confirmed blocking counts for messaging, news, and circumvention tools (`api.ooni.io/api/v1/aggregation`).
3. **TeleGeography Public Submarine Cable Landing & Corridor Registry** — correlates multi-country or coastal ASN BGP drops with shared submarine cable landing points (Red Sea / Bab el-Mandeb, Baltic, Taiwan Strait, West Africa).
4. **Cloudflare Radar** — outages Cloudflare's analysts annotate in the Radar Outage Center, with the cause Cloudflare states (`BO-IND-9`), and traffic anomalies Cloudflare verified (`BO-IND-10`); reported, never assessed. Needs `CLOUDFLARE_RADAR_TOKEN` (free, Radar read). Data CC BY-NC 4.0.
5. **Provider watch** — IODA BGP and active probing on Starlink (AS14593), Amazon (AS16509), Microsoft (AS8075) and Google (AS15169, AS396982), plus the open incidents AWS, Google Cloud, Google Workspace, Azure, Microsoft 365 (status.cloud.microsoft), Apple, Cloudflare (network, CDN, DNS, WARP and data-centre incidents, or major and critical ones), Akamai, Slack, Zoom and Starlink's Statuspage list on their own status feeds (reported, never assessed for cause). Akamai and Zoom are read from their Statuspage history RSS because their robots.txt disallows the API; Apple's items carry facts only, not Apple's wording.

## Commands

```bash
python tools/blackout.py collect --live       # pull live IODA, OONI, provider watch and suite tips
python tools/blackout.py collect              # write the seed snapshot (made-up, status test)
python tools/blackout.py build                # build site/ and site/data/feed.geojson (tic-feed/2)
python tools/blackout.py check                # validate doctrine registers and lint contract-v2 feed
python -m unittest discover -s tests -v       # run offline test suite
```
