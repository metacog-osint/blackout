# Blackout Method (`blackout-1.0`)

## Purpose
Blackout monitors global internet connectivity, BGP routing stability, application-layer censorship, and submarine cable corridor resilience.

## Signal Families & Independence Groups
1. **Control-Plane Routing (`ioda-gatech` BGP)**: Fraction of announced IPv4/IPv6 prefixes visible across RouteViews and RIPE RIS collectors compared against a 7-day rolling baseline.
2. **Data-Plane Active Probing (`ioda-gatech` Ping-Slash24)**: Responsive `/24` networks from Trinocular active probing.
3. **Application-Layer Censorship (`ooni`)**: Daily ratio of `confirmed_count + anomaly_count` to `measurement_count` across Web Connectivity, Telegram, Signal, WhatsApp, and Tor/Psiphon tests.
4. **Physical Submarine Cable Corridors (`telegeography`)**: Maps coastal landing stations and shared multi-country chokepoints (Red Sea / Bab el-Mandeb, Baltic Sea, Taiwan Strait, West Africa / WACS-ACE).

## Confidence & Alternative Hypotheses
Every warning item (`tier: "warning"` or `"alert"`) evaluates three competing hypotheses:
- **State-ordered shutdown / curfew** (`BO-H-1A`): BGP or active-probing drop accompanied by OONI blocking surge or mobile ASN targeting.
- **Physical cable or power-grid fault** (`BO-H-2A` / `BO-H-1B`): Multi-country shared cable corridor drop or co-occurrence with `Elements` seismic / `Wake` maritime tips.
- **Collector artefact or upstream transit reroute** (`BO-H-1C` / `BO-H-2C`): Single-collector or darknet-only fluctuation without corroborating BGP/probing loss.
