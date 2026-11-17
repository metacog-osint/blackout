# Blackout (`[local path]`)

Connectivity, BGP routing, internet shutdown, and submarine cable outage monitor for The Intelligence Company (`tic-feed/2`, program id: `blackout`, prefix: `BO`).

## Architecture
- **Standard library only** (Python 3.11+), except `h5py` for Black Marble's HDF5 (Tom, 13 Nov 2026).
- **`doctrine/`**: Full `tic-doctrine 0.4.7` registers (`BO-REQ-*`, `BO-PIR-*`, `BO-EEI-*`, `BO-WP-*`, `BO-H-*`, `BO-IND-*`, `BO-ASM-*`).
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
  Rate limits (14 Nov 2026, after the 13:46 UTC run lost anomalies and route leaks to HTTP 429 and the next run
  read both): `radar._get`, which `bgp.py` uses too, retries a 429 after Radar's Retry-After in seconds, else 10,
  30 and 60 s (`RETRY_PAUSES_S`), no wait over 90 s; any other error is not retried.
- **Lanes added 13 Nov 2026** (the plan's step 10; built by a side session of the Blackout review, from
  `_ideas/reports/Non DoD sources and COP visuals.md`). Each module's docstring has the shapes, checks and numbers;
  tests in `tests/test_lanes.py` replay real responses listed in each fixtures folder's `MANIFEST.json`.
  - **Routing, `blackout/bgp.py`** (BO-WP-4, BO-IND-11/12): Radar `bgp/hijacks/events` (3 days, confidence 8+) and
    `bgp/leaks/events`, every page, same token. Everything counted per country; feed items only for hijacks seen by
    25+ route-collector peers and leaks of 100+ prefixes seen by 25+ peers, one item per leaking AS (2 of 197 and
    12 of 1,012 on 13 Nov). Radar event pages 403 scripts, so items link radar.cloudflare.com/routing.
  - **Zaporizhzhia power, `blackout/iaea.py`** (BO-IND-13): update numbers from the IAEA's Top News RSS, text from
    the Wayback Machine's first capture (iaea.org 403s scripts; Blackout doesn't work around it). State none, single
    or not_stated from sentences naming the plant; a not_stated update hands over to the newest that states it;
    more than 10 days old is overdue. Checked on 23 updates (350-374). IAEA terms: reuse with acknowledgement.
  - **Shutdown history, `blackout/stop.py`** (BO-EEI-6): Access Now's STOP sheet (first tab as CSV = the 2025
    dataset, 313 rows, status as of 31 Dec 2025). Context only, appended to a current outage's summary; never
    changes a judgment. No licence stated for the data, none claimed.
  - **US grid, `blackout/odin.py`** (BO-IND-14, BO-PIR-8): ODIN's anonymous `/odi?view=county` and `/odi/status`
    (no key). EAGLE-I's live data is closed (FEMA's layer answers 499 Token Required; the platform serves government
    responders), so Tom chose ODIN on 13 Nov 2026. Counties placed with the Census 2025 Gazetteer
    (`blackout/us_counties.json`, public domain). A feed item per state above 50,000 meters out (DOE-417 criterion
    12). Each state carries its base rate from EAGLE-I's history (next bullet). Since 14 Nov 2026 (branch
    `power/county-fips`) each item also lists `us_counties`, every county in the state with meters out in that
    reading (`{fips, name, meters}`, largest first), so COP can match NWS alerts by county (their SAME codes are these
    FIPS with a leading 0) instead of by state. COP does not read it yet; a county-level match is a triage rule
    change and needs Tom's yes.
  - **US outage base rates, `blackout/eaglei.py`** (added 13 Nov 2026; Tom, about 11:45 AM CST: "use EAGLE-I, but
    keep looking for the OE-417 summaries"): ORNL's EAGLE-I outage history on figshare (doi:10.6084/m9.figshare.
    24237376, version 4 of 25 Feb 2026, 2014-2025; CC BY 4.0 as the record states, read through the figshare API 13
    Nov 2026; data descriptor Brelsford et al., Scientific Data 11:271, 2024). County customers out every 15 minutes,
    UTC, zero rows left out. **Offline only:** `python tools/eaglei_history.py --cache <folder>` reads 2018-2025
    (about 9.7 GB, every file checked against figshare's MD5; 2014-2017 had far lower coverage) and writes
    `blackout/eaglei_rates.json` (committed); live runs and the page read only that file. Rerun it when figshare adds
    a year (2024 came on 10 Apr 2025, 2025 on 24 Feb 2026), with `--years 2018-2026` and `BASE_YEARS` moved on.
    Rule (the docstring has it in full): a state's total = its counties summed per run; runs missing from the file
    neither join nor split; an episode = readings above the line with no more than 3 known readings under it between
    them (an hour) and no more than 6 hours from one to the next, 4 or more of them not suspect; peak = the highest
    total held over 4 clean readings. A run is **suspect** for a state when one of its counties shows more than
    100,000 customers out beyond 1.1 times EAGLE-I's modelled customers for it (MCC.csv): kept, but it can't make an
    episode or set a peak. Why: from 21:00 to 22:30 UTC on 8 Jan 2025 Southern California Edison's counties read far
    too high at once (Ventura 1,077,426 out of 317,658 customers, California 3.49 million against 400,000-510,000
    either side). Why not drop suspect runs (tried first, 13 Nov 2026): the test also fires where EAGLE-I puts a
    utility region's real outage on one county (Fiona, Sept 2022: Caguas, Arecibo, Mayaguez, Ponce; Uri, Feb 2021:
    Brazoria County, TX), and dropping lost Fiona and cut Uri; without the 100,000 margin it fires constantly (MCC
    puts Henderson County, NC at 24 customers). Checked against known events after the fix: Uri TX 3.37 million,
    Milton FL 3.29 million, Beryl TX 2.75 million, Ian FL 2.48 million, Isaias NJ 1.39 million, Ida LA 1.04 million.
    The file's shapes differ by year (2023 names the count "sum"; 2024 adds total_customers and sorts by county; 2020
    has empty counts), so columns are read by position. Lines 50,000 (DOE-417 criterion 12), 100,000, 250,000,
    500,000 and 1,000,000 per state: episodes a year (mean and each year), by month, hours (median, 90th
    percentile, longest) and peak. A state's rate counts only the years EAGLE-I has any row for it (Puerto Rico had
    0% coverage in 2018-2020 by EAGLE-I's own coverage file, so its rate is over 2021-2025; `each_year` holds None
    for a year left out). `attach()` puts the 50,000 line in the month of ODIN's reading beside every ODIN
    state (`snapshot.odin.states[].base_rate`, set in `build_snapshot`); a BO-IND-14 item appends the sentence and
    its caveat to the summary, carries the numbers in `baseline_ref.base_rate`, and cites `ornl-eaglei-history` as
    a second source. The caveat: EAGLE-I reads more utilities than ODIN (about 92% of customers by 2022), so a state
    over the line at ODIN's utilities alone is rarer still; coverage grew over the years. ODIN's data also feeds
    EAGLE-I. The page shows the line on each notable state and a column in ODIN's state table.
    **Verdict ground truth:** `eaglei.verdict_rule(state, start, end, line)` writes the resolution in the exact form
    Verdict's `tools/auto_resolve.py` reads (`EAGLEI_RE`, Verdict `f4e6a38` and `e7989ae`): Verdict streams that year's file from
    figshare itself (MD5 checked), applies the same rule, skips a year figshare hasn't published (so the forecast
    stays due), and holds a No for review when an hour or more of the window is missing or a suspect run is over
    the line (a real outage EAGLE-I put on one county, or a broken report: a person decides). A forecast it settles
    resolves months after its window (known_at is set to 30 April of the next year). Fixtures: real California rows,
    12:00 UTC 8 Jan to 12:00 UTC 9 Jan 2025, with SCE's broken report, in `tests/fixtures/eaglei/` (same copy in
    Verdict's tests).
    **OE-417 (DOE's Electric Emergency Incident and Disturbance Report) summaries, checked 13 Nov 2026:** the old
    host (www.oe.netl.doe.gov) doesn't answer; the Wayback Machine has its annual-summary page (last good capture 9
    Dec 2024, every capture since 20 Feb 2025 a 503) and the per-year files for 2002-2023 (old Excel .xls, which the
    standard library can't read; also PDF), the newest 2023 (`download.aspx?type=OE417XLS&ID=84`, captured 9 Jun
    2024; a ranged GET returns a real OLE2 header). No terms are stated on the archived page (a US Government work).
    EIA's Electric Power Monthly Table B.1 republished OE-417 events only to 30 Mar 2024. doe417.pnnl.gov is the
    filing app only (no public summaries; `files/DOE-417_Form.pdf` is the blank form). PNNL's "Event-correlated
    Outage Dataset in America" (OpenEI 6458, CC BY 4.0) is derived (EAGLE-I to 2023, DOE-417 from EIA's archive).
    No 2024-or-later summary was found anywhere, so OE-417 is not a second ground truth; at most a historical
    cross-check of EAGLE-I's 50,000 episodes for 2018-2023.
  - **Night lights, `blackout/blackmarble.py`** (BO-IND-15, BO-PIR-9): NASA Black Marble VJ146A2 (NOAA-20), near
    real time from LANCE (`VJ146A2_NRT`, a night out by about 04:00 UTC the next day, kept about a week), the
    standard product from LAADS for older nights (about 16 days late on 13 Nov 2026: newest 28 Oct). A box about 44 km square around each city in
    `CITIES`; clear high-quality pixels only; a night counts when 60% of the box is clear; the newest counted night
    against the median of the counted nights in the 14 before. See its docstring for the calibration behind the
    line. Reads `EARTHDATA_TOKEN` (the token Noisefloor uses; 60-day life, warned 14 days ahead from its own
    expiry claim, never printed). LAADS and LANCE redirect downloads to `/profiles/licenses/` until the Earthdata
    account has accepted LAADS's data licence once (Tom's step). Nights already read are carried from the last
    published snapshot (`previous_nights` in `tools/blackout.py`), so a run reads only new nights, at most
    `NIGHTS_PER_RUN`. Uses `h5py` 3.16.0, the one exception to the standard-library rule (Tom, 13 Nov 2026); the
    workflow installs it, and its tests skip without it. The licence gate is per account, so the first refused tile
    names it once and the run stops reading nights (a live run on 13 Nov 2026 had listed 60 problems, 15 tiles by 4
    nights). LANCE's catalogue keeps listing a night after deleting its files (13 Nov 2026: 14 tiles of 5 Nov listed,
    every download HTTP 404, 15 problems a run). When every listed tile of a product is 404, the next product is
    tried; a night no product holds goes in `nights_gone` (a line under the city table), not in the problems, unless
    it is `FRESH_DAYS` old or newer, when it is one problem.
  - **US grid demand, `blackout/eia.py`** (BO-IND-16, BO-PIR-10; added 13 Nov 2026 by the second lanes session):
    EIA API v2 `electricity/rto/region-data` hourly demand (D) and day-ahead forecast (DF), every respondent, EIA's 14
    regional sums left out. Nine one-day windows, four at a time, retried, under a 180 s deadline (a week in one query
    answered 504; a two-day window needs a second 5,000-row page, a day fits one; a live read took about 40 s).
    **Carried hours** (13 Nov 2026, after the 22:43 UTC run lost its two oldest days to the deadline and left SWPP,
    SWPW, BANC and PNM with 117 range hours, under 120; never-asked queries took 7-90 s or answered 503/504, EIA caches
    a repeat only for minutes, nine at once all 504): a run reads live only the newest 4 windows (96 h) and any older
    window the carry doesn't hold in full. The carry is hours that had ended 72 h or more before they were read (EIA
    corrects within 3 days), each span with its read time, in its own file `site/data/eia-hours.json` (about 80 KB,
    never in `snapshot.json`): collect writes `data/eia-hours.json`, build publishes it from live snapshots only, and
    the next run reads it back from the live site (`previous_eia_hours` in `tools/blackout.py`). Fresh values win; a
    failed window keeps its carried hours and is a problem only for hours neither read nor carried; no carry (first
    run, gap over a day, broken file) reads what's missing, all nine at most; a run that reads nothing live republishes
    the last carry. `snapshot.eia` says `windows_live`, `windows_carried` and `carried` (from which run, how many
    hours). The 180 s deadline bounds the run (14 Nov 2026): `_get` starts no try after it and ends each try's timeout at
    it, and `fetch_eia` lets the pool go without waiting (queued windows cancelled). Retries wait 20, 40 and 60 s
    (`RETRY_PAUSE_S`, 4 tries; 14 Nov 2026): EIA's 504s come in bursts and a retry a minute later often gets the
    query from EIA's cache in 1-2 s (probe in eia.py's docstring). Per
    balancing authority with a median forecast of 1,000 MW or more: skip its two newest hours (often partial), take
    the 24 before, and compare each hour's D/DF with the BA's own median over the 168 hours before; low = 25% under,
    or 1.5 times as far under as its 1st percentile where its forecasts often miss (EIA lets a BA report a forecast
    not comparable to demand: Duke Energy Florida's demand ran 24% or more above its forecast in every hour of 5-13
    Nov 2026); 3 low hours in a row is BO-IND-16. Stamps are hour ending, UTC (EIA-930 instructions, OMB 1905-0129,
    read 13 Nov 2026).
    Calibration, replayed hour by hour on real data (`CALIBRATION`): finds Milton at Tampa Electric (-74%) and Duke
    Energy Florida (-52%), Helene at Duke Energy Carolinas (-32%), Uri at ERCOT (-43%); misses Beryl at ERCOT
    (-22%). Over 1 Oct-13 Nov 2026 (38 BAs) it gave 2 runs (Salt River Project, SPP West), neither matched to an
    outage; with the 1st percentile alone (x1.0) it gave 10. Items are reported observations (BO-WP-5, BO-H-5B
    neutral), with EIA's acknowledgement and the read date; points are hand-placed in each BA's area (`PLACES`,
    schematic, precision admin1). Key: env `EIA_API_KEY` (repo secret), else `HKCU\Environment` on Windows; never
    printed, scrubbed from error text. Fixtures (`tests/fixtures/eia/`, gzipped real bodies, keyless URLs) are a run
    during Milton (16:30 UTC 10 Oct 2024) and one at 16:20 UTC 13 Nov 2026; `tests/make_eia_fixtures.py` records
    more. EIA terms: public domain, acknowledge "Source: U.S. Energy Information Administration (<date>)".
  - Radar outages whose stated cause is a power outage also carry BO-PIR-7 (BO-REQ-2, "where have people or
    critical sites lost grid power").
- **`blackout/status_feeds.py`**: open incidents from the providers' own status feeds (AWS RSS, Google Cloud and
  Google Workspace incidents.json, Azure RSS, Microsoft 365's status.cloud.microsoft, Apple System Status, Cloudflare
  Status, Akamai and Zoom history RSS, Slack Status, Starlink's Statuspage), published as `kind: observation`, `status_class: reported`,
  direction `neutral`, the provider as source with its link (BO-IND-7). Never assess a cause from them. BO-IND-8 =
  drops or incidents at two or more providers starting within 2 h, inside the last 24 h; also an observation.
  Long-running (14 Nov 2026): an open incident the provider hasn't updated in `QUIET_DAYS` (14) is still listed but
  never counted as current (`mark_quiet`, in `build_snapshot`: `quiet_days`, `long_running`); its feed item is tier
  `routine` with "(open; no update in N days)" in the title, and the page puts it under "Long-running" in Providers,
  out of the tile, tab count and Now list. A feed that gives no time counts as current (never guessed).
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
  - Provider items have no map point (`geometry: null`, `precision_class: none`); the page lists them on its
    Providers tab. Seed builds hold no provider data (a made-up incident at a real provider is too easy to misread).
  - GitHub (`www.githubstatus.com/history.rss`, added 17 Nov 2026; robots.txt disallows `/api/`), read like Akamai's
    and Zoom's; provider `github`. The 17 Nov source scan's other candidates were left out: Okta 401 and Fastly 403 to
    scripts; Oracle Cloud and IBM Cloud feeds weeks stale; Atlassian's and Shopify's feeds empty or stale; Twilio's RSS
    mostly maintenance; Salesforce's API noisy (per-instance); PayPal's atom feed not yet checked against an incident.
    No official public feed exists for the banks, airlines, hotels, US carriers, Visa, Mastercard, Sabre or Amadeus
    (checked 17 Nov 2026); Downdetector's robots.txt disallows everything; fcc.gov answers 403 to scripts and DIRS
    reports are documents, not a feed; NWS has no machine-readable outage feed (its 13 Nov 2026 KWNO message, ADA at
    23:02 UTC, said "There are currently no widespread NWS IT or network outages"), and api.weather.gov's robots.txt
    disallows everything; the Federal Reserve's service-status page is HTML behind a robots.txt `Disallow: /`.
- **US flight ground stops, `blackout/faa.py`** (BO-WP-6, BO-IND-17, BO-PIR-11 under BO-REQ-3; added 17 Nov 2026, after
  the FAA's nationwide ground stop of 13 Nov reached no source Blackout read; Tom: "It is critical Blackout captures
  these events"). The FAA ATCSCC advisories database (`www.fly.faa.gov/adv/`), today's and yesterday's ground-stop
  lists, then each stop's own page (cancellations from the list row alone), one page at a time with 0.2 s between.
  Counts: national (DCC, NATIONAL or NATIONWIDE GROUND STOP), a whole airline (the title names it), carrier requests
  at 2+ airports within 2 h, and EQUIPMENT or COMMUNICATION conditions (routine for one airport, anomaly for 2+
  within 2 h); weather, volume, staffing, runway and construction left out. Advisories for the same key join into one
  event; a cancellation within an hour of its end ends it; kept 24 h after it ends. Calibrated on real days, all in
  `tests/fixtures/faa/` (220 gzipped pages, about 1 MB, `tests/make_faa_fixtures.py`): 13 Nov 2026 (national 20:54
  UTC, cancelled 01:19 UTC 14 Nov; ORD and MDW "EQUIPMENT / OUTAGE", "COMMS ISSUES", 27 and 24 min before), 6 Aug
  2025 (United's outage: carrier requests at SFO, IAH, ORD, DEN, EWR), 19 Jul 2024 (CrowdStrike: AAL, DAL, UAL, AAY,
  NKS, PSA; Frontier's of 18 Jul 22:57 UTC with that evening's Azure outage), 16 Nov 2026 (14 of 15 stops left out).
  Known gap: an airline outage that holds flights at the gate with no ground stop (United, 16 Nov 2026) never shows.
  **Flapping pages** (fixed 17 Nov 2026, after the first live run, 18:29 UTC, failed the lane on 3 pages of 16 Nov
  that answered "NO Advisory."; two read fine minutes later): a page without its advisory is asked for again,
  `faa.TRIES` (3) times, `RETRY_PAUSE_S` (3 s) apart. One that still says "NO Advisory" is a data note
  (`faa:advisory-missing`, in `feed.DATA_NOTES`), not a failed lane; its list title alone still counts a national
  or whole-airline stop, but an airport stop can't be classified without its page. Any other empty page or error
  fails the lane. The two live-collect tests mock `fetch_faa` (the first version let them read the FAA live).
  Items: observation, reported, direction neutral, `transport_by_air`, countries US; national and airline items carry
  the US label point. Source `faa-atcscc`, grade A1, US Government work, public domain. The page lists them in a card
  at the top of Providers (`#providers/faa`, rows `faa-<event id>`), with a tile and Now entries. Lane `faa-atcscc`;
  problems `{"source": "faa"}`. **Not yet done:** the kit registry's Blackout `owns` and `terms` don't name the FAA
  or GitHub (a tic-doctrine release), and COP's `config/sources.json` doesn't register `faa-atcscc` or
  `github-status`.

## Page (`site_src/`, rebuilt 12 Nov 2026; Power and Routing tabs 13 Nov 2026)
- **Headline** (13 Nov 2026, Tom's line, confirmed about 11:20 PM CST 12 Nov): "Where the lights go out and the
  internet goes dark.", with a matching `<title>` and meta description. The longer "...Blackout sees/shows where"
  lines are only for a share card if Tom asks.
- **Power tab** (`#power`): the ZNPP as the IAEA's newest update states it (quoted; loss count; update and archive
  links); EIA-930 BAs under forecast, then every BA read in a table; ODIN's notable states, coverage line, and every
  state and county in tables; Black Marble's darker cities and a city table, with the licence-gate problem named;
  Radar outages whose stated cause is a power outage. **Routing tab** (`#routing`): Radar's widely seen hijacks and
  leaks (one row per leaking AS), totals and lines, counts by country. **Censorship** adds Access Now STOP history
  and Citizen Lab test-list counts beside each OONI and outage row, and a context card.
- Feed links route: `#power/znpp`, `#power/eia-<ba>`, `#power/us-<st>`, `#power/night-<city>`,
  `#routing/hijack-<id>`, `#routing/leak-as<asn>`; a row inside a closed table opens it. County rows are keyed
  `county-<fips>`, so a state link highlights one row. Map keys (`#znpp`, `#eia-tec`, `#us-ny`, ...) open the panel.
- **Map**: a Power layer (yellow, Okabe-Ito #F0E442 with a dark ring: ZNPP, BAs, states, darker cities; ODIN counties
  as small dots sized by meters out) and a Routing layer (black in light mode, near-white in dark). The Power tile is
  marked only for a new loss (a BA, state or city, or the ZNPP without off-site power), not the ZNPP's standing
  single line.
- **Checked 13 Nov 2026** on a live build (collected 16:2x UTC) under `tools/serve.py`: light and dark at 390 px and
  desktop, no console errors or CSP violations, no sideways page scroll with every table open, lowest text contrast
  5.31:1 (light) and 7.27:1 (dark). CSP unchanged. The house bar is the hub's v1.7 (Blackout's new wording, waiting
  on Tom's OK and the rollout to other sites).
- **Design pass, 14 Nov 2026** (items 6-11 of the 13 Nov review): at 390 px the map started 1,519 px down (now
  836 with the collection banner showing), with 9 tiles filling the first screen and the tabs on 3 lines. Now: one
  row of tabs that scrolls sideways under 641 px (`showTab` keeps the selected tab in view); tiles three across on a
  phone, notes hidden there; a tile that reads 0 folds into one "Quiet now" line (a lane not read says "not read"
  there); the headline and lede show on the Overview only (screen readers keep them on every tab), and "Data as of"
  is its own line on every tab; the map's layer list folds under "Layers" on a phone (`#layers-fold`, opened again
  when the window widens past 640 px); the map key paragraph folds under "How to read the map", leaving one line;
  IODA and watched-network durations read "1 h 15 min", "56 h", "3 d 1 h" (`ioda.duration_text`, the page's
  `durText`); the map refits to one world on resize until the reader moves it or picks something. Checked at 390 px
  and 1024-1500 px, light and dark, under `tools/serve.py` with the CSP on, on a copy of the 13:46 UTC 14 Nov live
  data: no console errors, no sideways page scroll; colours are the existing tokens.
Tom, 12 Nov 2026: the page lacked the standard header, scrolled far too long (about 7,000 px), had filter chips for
empty sections, and its map was hand-drawn polygons. The rebuild:
- **House bar:** `tic-nav.js` v1.7 and `tic-nav.css` v1.2, copied byte for byte from the hub's `site/shared/` (hub
  commit `cf879a3`, 13 Nov 2026, local and unpushed: Blackout's power and routing wording, waiting on Tom's OK; re-copy
  if the hub changes it). Loaded with
  `defer data-current="blackout"`, mounted first in `<body>`. Blackout's masthead sits under it: Feed, Snapshot,
  Method, Time (no "All programs"; the bar has it).
- **"Elsewhere today"** (16 Nov 2026, from the suite design review; `tic-related.js` and `tic-related.css` v1.0, the
  hub's shared module, built here first): a card after "Now" on the Overview showing what the other programs report
  in the countries of Blackout's current findings (any tier but routine, judgments first), read from COP's agent
  index (`cop.theintelligence.company/data/agent/index.json`, about 100 KB compressed) once the card nears the
  screen. Last 48 hours, unusual readings first, three at most, one per program, then "N more on COP". Hidden until
  the read succeeds; a failed read leaves it hidden. `renderRelated()` in `app.js`; the module's header has the rules.
  COP is already in `connect-src` (house bar v2.0). Checked 16 Nov 2026 on a copy of the live data under the real
  CSP: 1280 px light (lowest contrast 6.09:1) and 390 px dark (7.27:1), no sideways scroll, no console errors.
- **Six tabs, eight since 13 Nov 2026** (ARIA tabs pattern: arrows, Home, End, roving tabindex), each with its own hash: `#overview` (tiles,
  map, a "Now" list linking to items), `#outages` (IODA, then Cloudflare Radar), `#censorship` (OONI at the line;
  rows below it in a disclosure), `#cables` (corridor alerts, the register, suite tips), `#providers` (BO-IND-8,
  open incidents, watched networks and status feeds as compact cards, a full row only when BO-IND-6 fires),
  `#method` (how Blackout judges, indicators and warning problems from `data/method.json`, every registered source
  and licence, the feeds). Each section has one line, with the full checked text under "How this is measured"; an
  empty section says what would appear there and that Blackout collects about every 4 hours. The chips and search
  are gone (no tab is long enough to need search).
- **Doctrine detail on each item** (`details.doctrine` in `app.js`): "Judgment: likely (80%), moderate confidence",
  "Measurement, no probability" or "Reported by the source", then kind, basis, warning problem, hypothesis and the
  direction of the evidence, indicator text, NCF, requirements, competing hypotheses with `tell_apart`, sources
  (grade, licence, link), item id. Rows find their feed item by id prefix (`feat()`; provider incidents by
  `feed.py`'s slug). The old separate doctrine list is gone.
- **Hash routing** keeps every link the feed and COP use: a tab name; `#<tab>/<key>` highlights rows in that tab;
  `#radar` opens the Radar card; a map key (country code with a current item, corridor id, tip) opens the Overview,
  flies the map there and lists every item for that place in the detail panel, each with "Open in <tab>"; other keys
  (`#AS14593`, `#provider-aws`) open their tab with the rows highlighted; a key with nothing current says so. Map
  clicks write `#<key>` with replaceState, so a selection is a shareable link.
- **Map** (`map.js`, an ES module loaded when the Overview first shows; same stack as COP's `cop.js`): vendored
  MapLibre GL JS 6.14.0 in `vendor/maplibre-gl/` (BSD-3, `LICENSE.txt`; copied from COP). Natural Earth admin-0
  (public domain, naturalearthdata.com/about/terms-of-use, read 12 Nov 2026: "Crediting the authors is
  unnecessary"; credited anyway) from the kit's `geo/countries.json`: the build writes `data/world.geojson` (every
  country snapped to 0.1 degrees, about 970 KB, the land below zoom 3.5) and `data/countries.geojson` (kit
  resolution, only the countries named by outages, OONI rows and Radar items). OpenFreeMap positron or dark above
  zoom 3.5; if it can't be reached, Natural Earth at every zoom and a note. Marks: IODA outage = country fill plus a
  dot (small countries vanish otherwise; darker when judged), OONI at the line = dashed outline, Radar = blue dots at
  the country point, suite tips = green dots, cable corridors = schematic lines from the corridor's reference point
  to each landing station (dashed; solid and thick when alerted), named on the map from zoom 2.2 and called
  schematic in the key, the note, the panel and the Cables tab. No TeleGeography data. Colours: Okabe-Ito, light and
  dark sets in `MARK` (`map.js`) and `--m-*` (`style.css`), edges at least 3:1 on the base maps. Opens one world
  wide; cooperative gestures (Ctrl and scroll to zoom). Without WebGL it says so and the tabs still list everything.
  Credits on the map, in the footer and under Method: OpenFreeMap, (c) OpenMapTiles, (c) OpenStreetMap
  contributors, Natural Earth, MapLibre.
- **CSP** is COP's: self, plus `tiles.openfreemap.org` for images and connect, `worker-src 'self' blob:`,
  `style-src 'self'` (the old `'unsafe-inline'` is gone: no inline styles, and the DOM is built as text, never HTML
  strings). `test_page_keeps_the_csp_and_credits` keeps it so.
- **Checking locally:** `python tools/serve.py [port] [--dir FOLDER]` serves a built site with its `_headers`, CSP
  enforced (adapted from COP's). "blackout-site" in `[local path]` serves `site/` as last built here on
  8745 without headers (run `collect` and `build` first: `site/` and `data/snapshot.json` are build output, not
  tracked since 14 Nov 2026, when the committed copies were found a day behind `site_src/`). To see live data without touching the repo, copy `blackout/ tools/ doctrine/ data/
  site_src/` to a temp folder, run `collect --live` and `build` there, and serve that `site/`.
- **Checked 12 Nov 2026** on live data (collected 01:48 UTC 13 Nov) and the seed build, light, dark and 390 px phone:
  text contrast at least 4.5:1 on every tab with every disclosure open (lowest 5.31:1; an in-page check using the
  hub axe helper's rule, since axe-core isn't installed locally), every control named, tabs wired, no console
  errors or CSP violations under the real headers (OpenFreeMap's dark style logs a missing "circle-11" sprite, its
  own). Heights at 1280 px: Overview about 1,560 px, Outages 1,490, Censorship 990, Cables 1,280, Providers 2,190,
  Method 1,810.

## Per-source health in the feed (14 Nov 2026, Tom's yes)
- `feed.geojson` carries a top-level `health` block, `{lane: {state, last_ok, checked_at, problems}}`, the shape
  Cordon's feed uses. A lane is an item's first source id (`feed.LANES`: IODA, OONI, Radar, Radar BGP, IAEA, STOP,
  ODIN, Black Marble, EIA-930, TeleGeography, the twelve status feeds and the FAA; 23 in all since 17 Nov 2026). Why: Blackout drops a failed
  source's items instead of keeping its last ones, so the kit's history log (`tic-history/1`) and COP's suite ledger
  need to know which lanes were read before they call a vanished item ended.
- `feed.problem_lanes` places each `collection.problems` entry on a lane. Any problem marks its lane `failed`, even a
  partial one; a problem it cannot place marks every lane failed; tips, Citizen Lab and the two data notes (ODIN county
  not in the Gazetteer, EIA place missing) fail nothing. So a mistake here can only delay an end, never invent one.
  A new collector or problem source needs a line there (a test lists the known ones).
- A failed lane keeps the last success from the previous published feed (`collect --live` reads it into
  `collection.last_ok`); null when none is known. The kit's `lanes_ok` counts a lane as read when `last_ok` equals
  `checked_at` and the state is fresh. Not yet used by COP: its ledger still ends items per program.
- Tests: `TestSourceHealth` in `tests/test_blackout.py` (7; one skips until the vendored kit has `history`, 0.5.0+).

## Data modes (fixed 12 Nov 2026 after a review of Gemini's changes)
- `collect` (no flag) builds from `SEED_SNAPSHOT`, which is made up. Everything built from it has feed
  `status: "test"` and a "Test data" banner on the page. Never publish seed output as `actual`.
- `collect --live` starts empty and never falls back to seed: a source that fails publishes nothing and is listed in
  `snapshot.collection.problems` (the page shows an "Incomplete collection" banner).
- Tips: a tip counts toward BO-IND-5 only when its own coordinates (taken from the producer's item geometry) fall
  within 50 km of a corridor point. Tips naming an outage country are context only and never raise BO-H-1A.
- Outages in a flagged corridor are published as observations pointing at the corridor item (BO-H-2A), not as
  shutdown assessments.
- **Country outages carry no judgment (blackout-1.1, Tom's yes 16 Nov 2026, from the 15 Nov suite review).** The
  0.80 "likely shutdown" is gone: its OONI line was a standing 7-day rate (Iran, Russia and Myanmar met it every day)
  and counted unconfirmed anomalies. Now `collect` reads OONI hourly for each BO-IND-1 outage's own country and hours
  (`ooni.fetch_outage_window`, one request) against the same hours of the 5 days before; when the outage hours meet
  BO-IND-3 (>=50 measurements, >=15%) and at least double the baseline rate, the item is an observation, tier anomaly,
  direction `supports` BO-H-1A, with no probability until Verdict has scored the rule. Otherwise `neutral`, and the
  summary says what OONI showed: not read, too few measurements (with the usual count), or no rise.
- **IODA (blackout-1.1):** the baseline is the same hours of the 7 days before (an event of 24 h or more uses every
  point; `signal_drop(same_hours=True)`, also used by the ASN watch); a signal whose baseline median is under
  `ioda.MIN_BASELINE` (20) is left out, listed under `low_count` and named in the summary (DR Congo's darknet 1.92 ->
  0.31 had been published as an 83.8% drop); BO-IND-1 needs BGP or active probing, as the register defines it, never
  darknet alone. BO-IND-4 items are titled "single-signal dip", tier routine.
- **Tiers (blackout-1.1):** routine for standing conditions and fixed publishing lines: OONI 7-day rates, BGP hijacks
  and leaks, the ZNPP's stated state (anomaly only when the update reports a loss of power). COP's MCP instructions
  still describe Blackout's "likely shutdown" assessment; that text is COP's to change.
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
- **Kit 0.4.7** (tic-doctrine `62875dd`, 13 Nov 2026, on GitHub): `owns` adds the EAGLE-I base rates and `terms` appends
  EAGLE-I's licence word for word (`eaglei.LICENCE`, the same string in `doctrine/sources.json`). EAGLE-I is really
  CC BY 4.0, so `test_licence_claims_match_the_sources` allows those words only inside that licence text. Synced to
  COP (`29a226b`, rebased over Headwind's kit 0.4.6 commit; it registers `ornl-eaglei-history` in `config/sources.json`).
- **Kit 0.4.5** (tic-doctrine `a206cf0`, 13 Nov 2026, local): Blackout's registry `owns` lists every lane (nothing
  planned), and `terms` appends each new source's licence word for word from `doctrine/sources.json` (IAEA, Access
  Now STOP, Citizen Lab, ODIN, Census Gazetteer, NASA Black Marble, EIA-930). `feed.LICENCE` reads those terms from the
  vendored kit (`vocab.load("programs")`), so they can't drift; `feed.ATTRIBUTION` names every source.
  `test_licence_claims_match_the_sources` checks each new licence is in the terms. Synced to COP too (COP `16f33c5`,
  which also registers the eight new source ids in `config/sources.json`).
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
- Headers (CSP, HSTS, CORS on `/data/*`) in `site_src/_headers` (CSP is COP's since the page rebuild; see Page).
- **`blackout/ooni.py`**: Normalizes OONI aggregation measurements (`anomaly_count`, `confirmed_count`, `measurement_count`, `ok_count`) into censorship anomaly rates.
- **`blackout/itu.py`** (14 Nov 2026): internet use by country, context only, beside IODA and OONI rows ("Internet
  use: 85.3% of people in 2024 (ITU), about 78 million of 92 million"); never an indicator, a judgment or a feed
  item (adding it to the feed would need the kit's licence terms extended first). ITU DataHub answers scripts with
  403, so the series comes from the World Bank's WDI copy (IT.NET.USER.ZS, source ITU; SP.POP.TOTL), CC BY 4.0;
  ITU's terms allow non-commercial use with acknowledgement. Each country's latest year from 2019 (190 of 217;
  none for Sudan, Syria, Turkmenistan, North Korea, Kosovo, and the page says so). Annual: `python
  tools/itu_refresh.py --save-raw tests/fixtures/itu` rewrites the committed `blackout/itu_internet_use.json` and
  the fixtures together (the test checks the table is the fixtures reduced).
- **`blackout/cables.py`**: Correlates country/ASN connectivity drops with shared submarine cable corridors and landing stations, plus cross-program tips from `Elements` (seismic/volcanic) and `Wake` (maritime AIS loitering).
- **`blackout/feed.py`**: Emits `site/data/feed.geojson` (`tic-feed/2`) with `tic_doctrine.confidence` chips and `ncf` mappings (`provide_internet_routing`, `operate_core_network`, `provide_cable_access_network`, `provide_internet_content_services`).
