# Blackout

**Blackout** (`blackout.theintelligence.company`) is The Intelligence Company's connectivity, BGP routing, censorship, and undersea cable outage monitor.

It ingests public, free-tier telemetry from:
1. **Georgia Tech IODA (Internet Outage Detection and Analysis)** — BGP visibility, Merit-NT darknet, and active-probing outage events by country and ASN (`api.ioda.inetintel.cc.gatech.edu/v2/`).
2. **OONI (Open Observatory of Network Interference)** — aggregated censorship anomaly and confirmed blocking counts for messaging, news, and circumvention tools (`api.ooni.io/api/v1/aggregation`).
3. **TeleGeography Public Submarine Cable Landing & Corridor Registry** — correlates multi-country or coastal ASN BGP drops with shared submarine cable landing points (Red Sea / Bab el-Mandeb, Baltic, Taiwan Strait, West Africa).

## Commands

```bash
python tools/blackout.py collect              # pull live IODA + OONI data (or write seed snapshot)
python tools/blackout.py build                # build site/ and site/data/feed.geojson (tic-feed/2)
python tools/blackout.py check                # validate doctrine registers and lint contract-v2 feed
python -m unittest discover -s tests -v       # run offline test suite
```
