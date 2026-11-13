# Blackout (`D:\Projects\blackout`)

Connectivity, BGP routing, internet shutdown, and submarine cable outage monitor for The Intelligence Company (`tic-feed/2`, program id: `blackout`, prefix: `BO`).

## Architecture
- **Standard library only** (Python 3.11+).
- **`doctrine/`**: Full `tic-doctrine 0.4.4` registers (`BO-REQ-*`, `BO-PIR-*`, `BO-EEI-*`, `BO-WP-*`, `BO-H-*`, `BO-IND-*`, `BO-ASM-*`).
- **`blackout/ioda.py`**: Reads IODA v2 outage events, then each event country's raw signals (BGP, active probing
  `ping-slash24`, darknet `merit-nt`): drop = median during the event against the median of the 7 days before, per
  signal; z-score against the same 7 days. IODA's event `score` is a magnitude, never a percentage. A signal that
  can't be read stays `None`; nothing is filled in from another signal.
- **`blackout/asn_watch.py`** (added 12 Nov 2026, after a Signal group's "cascading outages" claim that only a
  Microsoft 365 outage bore out): IODA on a fixed watch list (`WATCHLIST`: AS14593 Starlink, AS16509 Amazon, AS8075
  Microsoft, AS15169 Google, AS396982 Google Cloud; add networks there). Window = IODA's own ASN outage events in the
  last 24 h, else the longest run of 30-minute points >=5% under the 7-day median, else the last 3 h; drop = median
  in the window against the 7 days before the window (`ioda.signal_drop`). BO-IND-6 at >=5% over >=30 min; the
  week to 12 Nov 2026 had no 3-hour dip above 1.6% on any of the five.
- **`blackout/radar.py`** (added 13 Nov 2026): Cloudflare Radar's Outage Center annotations (BO-IND-9; the
  cause and type Cloudflare states are quoted, never assessed; cause maps to a hypothesis: government-directed
  BO-H-1A supports, power or military BO-H-1B supports, cable cut BO-H-2A supports, any other named cause
  BO-H-1A refutes, none neutral) and verified traffic anomalies (BO-IND-10: status TP or VERIFIED, not covered
  by an annotation; no cause, BO-H-1A neutral). Ongoing or ended within 72 h; country centroid points. Token in
  env `CLOUDFLARE_RADAR_TOKEN` (repo secret, set by Tom 13 Nov 2026; also in Tom's local env), passed only to
  the collect step. Data CC BY-NC 4.0 (developers.cloudflare.com/radar, checked again 13 Nov 2026); kit 0.4.4
  put that clause in the registry's `terms`, and the feed `licence` is those terms word for word (nothing
  appended). radar.cloudflare.com pages answer 403 to scripts, so the AS and country links on anomaly items were
  not checked by script.
- **`blackout/status_feeds.py`**: open incidents from the providers' own status feeds (AWS RSS, Google Cloud and
  Google Workspace incidents.json, Azure RSS, Microsoft 365's status.cloud.microsoft, Apple System Status, Cloudflare
  Status, Akamai and Zoom history RSS, Slack Status, Starlink's Statuspage), published as `kind: observation`, `status_class: reported`,
  direction `neutral`, the provider as source with its link (BO-IND-7). Never assess a cause from them. BO-IND-8 =
  drops or incidents at two or more providers starting within 2 h, inside the last 24 h; also an observation.
  - AWS: open = updates since the last "Service is operating normally" one, per `<service>-<region>` key. On
    12 Nov 2026 AWS listed me-central-1 (UAE) open since 1 Mar 2026 and me-south-1 (Bahrain) since 2 Mar 2026, last
    updated 15 Sep 2026; AWS's own current-events JSON agreed.
  - Microsoft 365 (added 12 Nov 2026, after that day's Exchange Online and Teams outage, MO1550821, reached no
    feed Blackout read): `status.cloud.microsoft/api/posts/mac`, the JSON behind Microsoft's public status page
    (`api/feed/mac` is the same as RSS; `api/posts/m365Consumer`, the endpoint first tried, answers 501). One
    current post, open unless Status is Available or a closing status (Status alone: an open update on 13 Nov
    had an empty Title, so the title falls back to its "User impact:" line); no start time; a new Id each update. Microsoft uses
    the page for issues that stop admins reaching their tenant's Service health (learn.microsoft.com,
    view-service-health), so smaller incidents, like that morning's Teams one (TM1550488), never appear.
    Azure's feed doesn't cover Microsoft 365. BO-IND-8 names providers by company (`PROVIDER_LABELS`).
  - starlink.statuspage.io: operator unverified, one incident in its whole history (April 2022), nothing for the
    24 Jul 2025 global outage. Graded F6; its silence means nothing. AS14593 on IODA is the Starlink check.
  - Added 13 Nov 2026 (after Apple's iCloud outage of 10 Nov and the Microsoft 365 outage of 12 Nov reached no feed
    Blackout read; from the source scan in the side-session notes): Cloudflare, Google Workspace, Apple, Akamai,
    Slack, Zoom. Each provider's own page, grade B2. Details in the `status_feeds.py` docstring.
  - Cloudflare (`incidents/unresolved.json`): kept only when rated major or critical, or on a core network
    component (`CLOUDFLARE_CORE`: Network, CDN/Cache, Authoritative DNS, Recursive DNS = 1.1.1.1 by Cloudflare's own
    description, DNS Root Servers, WARP, Magic Transit, China Network) or a data centre ("City - (IATA)"). Both,
    because Cloudflare rates nearly everything minor (47 of its last 50 on 13 Nov 2026, the rest none), network
    incidents included ("Network Performance Issues in APJC", 3 Nov 2026); impact alone would drop those, and a
    component rule alone would drop a global outage filed under developer products. Developer-platform incidents
    are left out (all 3 open on 13 Nov 2026). NCF: routing plus content services.
  - Google Workspace: Google Cloud's incidents.json shape, same rule, links on `www.google.com/appsstatus/dashboard/`;
    provider `google`. No open Workspace incident in 53 Wayback captures, so its open path is Google Cloud's.
  - Apple (`system_status_en_US.js`, plain JSON): open = eventStatus not resolved/completed/upcoming and statusType
    not Maintenance (the values in 115 Wayback captures, 2024-2026); grouped by `messageId` (one outage is posted on every
    service it hit); type = worst of Outage, Issue, Performance; start from `epochStartDate`, else "MM/DD/YYYY HH:MM
    PST|PDT" as US Pacific (they agree on all 34 recorded events; Apple's `endDate` string is the group's end while
    `epochEndDate` is each service's own). Apple's website Terms of Use bar republishing site content without
    consent, so Apple items carry services, type and times only, never Apple's message text, and the source
    `licence` says so (per-feed `licence` in `FEEDS`). The 10 Nov 2026 iCloud outage is in the fixtures twice:
    ongoing (Wayback, 00:10 UTC 11 Nov) and resolved (live, 13 Nov).
  - Akamai and Zoom: robots.txt on both disallows `/api/`, so their Statuspage `history.rss` is read
    (`parse_statuspage_rss`): open = newest update not Resolved, Postmortem or Completed; maintenances (any
    Scheduled, In progress, Verifying or Completed update) and Akamai's "Customer Security Advisory" items left
    out; times read in UTC, PST or PDT only. The feeds hold 25 items, so an older open incident is missed (Zoom's
    maintenance notices fill most of its 25, reaching back only a week). On 13 Nov 2026 the RSS rule gave the same
    open incidents as Akamai's own status page and Zoom's summary.json (both recorded as fixtures). Zoom's
    summary.json was read that once; starlink.statuspage.io's robots.txt also disallows `/api/` and Blackout
    still reads its summary.json (not changed).
  - Slack (`slack-status.com/api/v2.0.0/current`): open = status not resolved, scheduled, completed or cancelled
    (docs.slack.dev/reference/slack-status-api). No capture with an active incident exists, so the test puts the
    real 11 Nov 2026 incident from Slack's history into the real current envelope. Provider `salesforce`.
  - 12 Nov 2026 gave a real BO-IND-8: Zoom's Microsoft-integrations degradation (19:24 UTC) and Akamai's email
    incident (20:53 UTC), both tied by those providers to the Microsoft outage, began 89 min apart.
  - Provider items have no map point (`geometry: null`, `precision_class: none`); the page lists them under
    Provider Watch. Seed builds hold no provider data (a made-up incident at a real provider is too easy to misread).

## Data modes (fixed 12 Nov 2026 after a review of Gemini's changes)
- `collect` (no flag) builds from `SEED_SNAPSHOT`, which is made up. Everything built from it has feed
  `status: "test"` and a "Test data" banner on the page. Never publish seed output as `actual`.
- `collect --live` starts empty and never falls back to seed: a source that fails publishes nothing and is listed in
  `snapshot.collection.problems` (the page shows an "Incomplete collection" banner).
- Tips: a tip counts toward BO-IND-5 only when its own coordinates (taken from the producer's item geometry) fall
  within 50 km of a corridor point. Tips naming an outage country are context only and never raise BO-H-1A.
- Outages in a flagged corridor are published as observations pointing at the corridor item (BO-H-2A), not as
  shutdown assessments.
- A BO-IND-1 outage is judged (assessment, tier warning, BO-H-1A at 0.80) only with OONI blocking that meets BO-IND-3
  (>=15% over >=50 measurements) in the same country. Without it, it's an observation, direction `neutral` (Tom,
  12 Nov 2026): an IODA drop alone fits a power or physical fault (BO-H-1B) as well as a shutdown.
- Tests replay real API responses (`tests/fixtures/ioda_live_2026-11-12.json`, `ioda_asn_*.json`, and
  `tests/fixtures/status/` with its `MANIFEST.json` of source URLs; Azure, Google Cloud, Cloudflare and Apple
  open-incident shapes are Wayback Machine captures because none had one open when recorded); don't replace them
  with invented shapes.
- Times on the page go through `tic-time.js` (copy of `theintelligence-company/site/shared/`).
- TeleGeography: no TeleGeography data is ingested; the corridor register is hand-compiled and only checked against
  its public map, so no TeleGeography licence is claimed (fixed 12 Nov 2026; the old "CC BY-SA 4.0" was unverified).

## Deploy (12 Nov 2026)
- Live at https://blackout-dbn.pages.dev (blackout.pages.dev was taken). Deploys so far by hand from live builds
  (last: provider watch `1e343ef`, 22:2x UTC 12 Nov).
- Custom domain: https://blackout.theintelligence.company, active with HTTPS and HSTS since about 23:00 UTC 12 Nov
  2026 (Pages API domain + Squarespace `CNAME blackout → blackout-dbn.pages.dev`, 4 hrs; Squarespace asks for a
  Google "Verify to continue" before adding a record, and the Browser pane blocks that popup unless Tom clicks it).
- COP: kit 0.4.3 (12 Nov 2026) lists Blackout as `live` (the test `test_registry_keeps_unbuilt_programs_planned` now
  expects that), and COP's `config/programs.json` has its drawing entry, committed in COP and waiting on Tom's push
  (a push to COP's main deploys COP). The registry's `terms` name OONI's data licence, CC BY-NC-SA 4.0
  (github.com/ooni/license, `data/LICENSE.md`, read 12 Nov 2026), and claim none for IODA, which states none on its
  API page or site. Blackout's own output now matches (fixed 12 Nov 2026; it used to claim "CC BY 4.0" for both):
  the feed's `licence` is the registry's `terms` text word for word (`feed.LICENCE`), the OONI source says
  CC BY-NC-SA 4.0, the IODA source says no data licence is stated and it is credited, and `doctrine/sources.json` and
  the page footer say the same. `test_licence_claims_match_the_sources` keeps it that way.
- Cloudflare Pages project `blackout`, deployed by `.github/workflows/update.yml` (tests, `collect --live`, build,
  check, refuses a seed feed, `wrangler pages deploy site`). Started every 4 h by the hub's tic-dispatch Worker (:45
  past 01, 05, 09, 13, 17, 21 UTC), on push to main, by hand, and by its own 6-hourly cron as a fallback. Account ID is
  repo variable `CLOUDFLARE_ACCOUNT_ID`; repo secret `CLOUDFLARE_PAGES_TOKEN` set by Tom 23:08 UTC 12 Nov (without it
  the deploy step is skipped). Tom added `blackout` (and `vis`) to the `tic-dispatch` token's repository access the
  same evening. First deploy from Actions: manual run 23:09 UTC 12 Nov. Live data is not committed; the repo's `site/` stays
  a seed build.
- Headers (CSP, HSTS, CORS on `/data/*`) in `site_src/_headers`.
- **`blackout/ooni.py`**: Normalizes OONI aggregation measurements (`anomaly_count`, `confirmed_count`, `measurement_count`, `ok_count`) into censorship anomaly rates.
- **`blackout/cables.py`**: Correlates country/ASN connectivity drops with shared submarine cable corridors and landing stations, plus cross-program tips from `Elements` (seismic/volcanic) and `Wake` (maritime AIS loitering).
- **`blackout/feed.py`**: Emits `site/data/feed.geojson` (`tic-feed/2`) with `tic_doctrine.confidence` chips and `ncf` mappings (`provide_internet_routing`, `operate_core_network`, `provide_cable_access_network`, `provide_internet_content_services`).
