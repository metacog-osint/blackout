# Blackout (`D:\Projects\blackout`)

Connectivity, BGP routing, internet shutdown, and submarine cable outage monitor for The Intelligence Company (`tic-feed/2`, program id: `blackout`, prefix: `BO`).

## Architecture
- **Standard library only** (Python 3.11+).
- **`doctrine/`**: Full `tic-doctrine 0.4.1` registers (`BO-REQ-*`, `BO-PIR-*`, `BO-EEI-*`, `BO-WP-*`, `BO-H-*`, `BO-IND-*`, `BO-ASM-*`).
- **`blackout/ioda.py`**: Reads IODA v2 outage events, then each event country's raw signals (BGP, active probing
  `ping-slash24`, darknet `merit-nt`): drop = median during the event against the median of the 7 days before, per
  signal; z-score against the same 7 days. IODA's event `score` is a magnitude, never a percentage. A signal that
  can't be read stays `None`; nothing is filled in from another signal.

## Data modes (fixed 12 Nov 2026 after a review of Gemini's changes)
- `collect` (no flag) builds from `SEED_SNAPSHOT`, which is made up. Everything built from it has feed
  `status: "test"` and a "Test data" banner on the page. Never publish seed output as `actual`.
- `collect --live` starts empty and never falls back to seed: a source that fails publishes nothing and is listed in
  `snapshot.collection.problems` (the page shows an "Incomplete collection" banner).
- Tips: a tip counts toward BO-IND-5 only when its own coordinates (taken from the producer's item geometry) fall
  within 50 km of a corridor point. Tips naming an outage country are context only and never raise BO-H-1A.
- Outages in a flagged corridor are published as observations pointing at the corridor item (BO-H-2A), not as
  shutdown assessments.
- Tests replay real API responses (`tests/fixtures/ioda_live_2026-11-12.json`); don't replace them with invented
  shapes.
- Times on the page go through `tic-time.js` (copy of `theintelligence-company/site/shared/`).
- **`blackout/ooni.py`**: Normalizes OONI aggregation measurements (`anomaly_count`, `confirmed_count`, `measurement_count`, `ok_count`) into censorship anomaly rates.
- **`blackout/cables.py`**: Correlates country/ASN connectivity drops with shared submarine cable corridors and landing stations, plus cross-program tips from `Elements` (seismic/volcanic) and `Wake` (maritime AIS loitering).
- **`blackout/feed.py`**: Emits `site/data/feed.geojson` (`tic-feed/2`) with `tic_doctrine.confidence` chips and `ncf` mappings (`provide_internet_routing`, `operate_core_network`, `provide_cable_access_network`, `provide_internet_content_services`).
