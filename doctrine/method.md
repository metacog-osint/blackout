# Blackout Method (`blackout-1.0`)

## Purpose
Blackout monitors global internet connectivity, BGP routing stability, application-layer censorship, and submarine cable corridor resilience.

## Signal Families & Independence Groups
1. **Control-Plane Routing (`ioda-gatech` BGP)**: Fraction of announced IPv4/IPv6 prefixes visible across RouteViews and RIPE RIS collectors compared against a 7-day rolling baseline.
2. **Data-Plane Active Probing (`ioda-gatech` Ping-Slash24)**: Responsive `/24` networks from Trinocular active probing.
3. **Application-Layer Censorship (`ooni`)**: Daily ratio of `confirmed_count + anomaly_count` to `measurement_count` across Web Connectivity, Telegram, Signal, WhatsApp, and Tor/Psiphon tests.
4. **Physical Submarine Cable Corridors (`telegeography`)**: Maps coastal landing stations and shared multi-country chokepoints (Red Sea / Bab el-Mandeb, Baltic Sea, Taiwan Strait, West Africa / WACS-ACE).
5. **Provider networks (`ioda-gatech`, ASN level)**: IODA BGP and active probing on a fixed watch list of provider networks (Starlink AS14593, Amazon AS16509, Microsoft AS8075, Google AS15169 and AS396982). Median during the window against the median of the 7 days before it; the window is IODA's own outage events on the network, else the longest run of points >=5% low in the last 24 h, else the last 3 h (`BO-IND-6`, >=5% over >=30 min).
6. **Provider status feeds (`aws`, `google`, `microsoft`, `starlink-statuspage`)**: the incidents each provider lists as open on its own status page (`BO-IND-7`). They are what the provider reports, published as observations with the provider as source; Blackout never assesses a cause from them. An empty feed is not proof a provider is up (`BO-ASM-4`).

## Confidence & Alternative Hypotheses
Every warning item (`tier: "warning"` or `"alert"`) evaluates three competing hypotheses:
- **State-ordered shutdown / curfew** (`BO-H-1A`): BGP or active-probing drop accompanied by OONI blocking surge or mobile ASN targeting.
- **Physical cable or power-grid fault** (`BO-H-2A` / `BO-H-1B`): Multi-country shared cable corridor drop or co-occurrence with `Elements` seismic / `Wake` maritime tips.
- **Collector artefact or upstream transit reroute** (`BO-H-1C` / `BO-H-2C`): Single-collector or darknet-only fluctuation without corroborating BGP/probing loss.

## Provider watch (`BO-WP-3`)
Everything here is published as an observation (direction `neutral`, no probability). The hypotheses say what would tell the cases apart:
- **Network-level outage at the provider** (`BO-H-3A`): a `BO-IND-6` drop on its network, usually with an incident on its own feed.
- **Service or application outage without network loss** (`BO-H-3B`): a `BO-IND-7` incident with the provider's network flat on IODA (the 12 Nov 2026 Microsoft 365 / Exchange Online outage, internal cause, is this case).
- **Concurrent outages at several providers** (`BO-H-3C`): `BO-IND-8`, onsets at two or more providers within 2 h. Onsets this close don't by themselves show a shared cause or a cascade.
- **Measurement artefact or unsupported claim** (`BO-H-3D`): an IODA drop with no incident at the provider and no drop at the others.
