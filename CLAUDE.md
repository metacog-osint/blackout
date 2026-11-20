# Blackout

Internet and power outages, shutdowns, censorship, routing, cable corridors and providers' own status reports for
The Intelligence Company (feed `tic-feed/2`, program id `blackout`, prefix `BO`; public repo `metacog-osint/blackout`;
live at https://blackout.theintelligence.company, Pages project `blackout`).

**State and open items: `.claude/HANDOFF.md`**, read it first. Keep this file
under 12 KB; module docstrings hold shapes and calibration.

## Stack
- Python 3.11+ stdlib only, except `h5py` 3.16.0 for Black Marble (tests skip without it). Page in `site_src/`;
  `site/` and `data/` are build output, not tracked. Never edit `site/`.
- `doctrine/`: vendored tic-doctrine kit and registers (`BO-REQ/PIR/EEI/WP/H/IND/ASM-*`), `doctrine/sources.json`.
- `.claude/` is gitignored. Public repo: nothing private in tracked files. `GEMINI.md` (untracked): leave it.

## Commands
- `python tools/blackout.py collect` builds from the made-up `SEED_SNAPSHOT` (feed `status: "test"`, "Test data"
  banner; never publish it). `collect --live` reads every lane, never falls back to seed.
- `python tools/blackout.py build`, then `check` (feed lint against the kit).
- `python -m unittest discover -s tests` (232 tests, 20 Nov). Tests replay real recorded responses
  (`tests/fixtures/*/MANIFEST.json`, recorders `tests/make_*`; STOP's is six synthetic rows, no licence stated);
  never invent shapes. Live-collect tests mock every network lane.
- `python tools/serve.py [port] [--dir FOLDER]` serves a built site with `_headers` and CSP. Live data without
  touching the repo: copy `blackout/ tools/ doctrine/ data/ site_src/` to a temp folder, `collect --live`, `build`.
- Offline, yearly: `tools/eaglei_history.py` (new figshare year: move `BASE_YEARS`), `tools/itu_refresh.py`; `--help`.

## Deploy
- **Pushing `main` deploys.** `.github/workflows/update.yml`: tests, `collect --live`, build, check, refuses a
  non-live feed, `wrangler pages deploy site`; job timeout 15 min (Tom, 18 Nov 2026). Started every 4 h by the hub's
  tic-dispatch Worker (:45 past 01/05/09/13/17/21 UTC), on push to main, by hand, and its own 6-hourly cron.
- Secrets: `CLOUDFLARE_PAGES_TOKEN`, `CLOUDFLARE_RADAR_TOKEN`, `EARTHDATA_TOKEN` (60-day life, warned 14 days ahead),
  `EIA_API_KEY` (repo secrets; Tom sets them). Repo variable `CLOUDFLARE_ACCOUNT_ID`. Never print a key.
- Live data is never committed, except the kit history log `history/` (tic-history/1, from 18 Nov 2026): after the
  check the workflow runs `tic.py history ... --fill-last-change` (writes `site/data/ended.geojson`) and commits
  `history/` to main after the deploy. Other carries are read back from the live site: `data/eia-hours.json`,
  `data/gdelt-counts.json` (build publishes both from live runs only), Black Marble nights from the last snapshot.
- Retention archives (`blackout/archive.py`, `tools/blackout.py archive`, after the deploy; failure turns the job red,
  not the deploy): `history/blackmarble-nights.jsonl`, `odin/DAY.jsonl[.gz]`, `public-counts.jsonl`,
  `gdelt-matches/YYYY-MM.jsonl.gz`, `trends-days.jsonl`, `netblocks-posts.jsonl`. Append-only.
- Kit changes (registry `owns`/`terms`) are tic-doctrine releases, synced to Blackout and COP, with each new source
  id in COP's `config/sources.json`. Blackout is on kit 0.6.6.

## Method rules (blackout-1.1, Tom's yes 16 Nov 2026)
- **Country outages are observations with no probability.** `collect` reads OONI hourly for each BO-IND-1 outage's own
  country and hours against the same hours of the 5 days before. When the outage hours meet BO-IND-3 (>=50
  measurements, >=15% anomalies) and at least double the baseline rate: tier anomaly, direction `supports` BO-H-1A
  (OONI measured blocking rising in the outage's own hours, which supports a shutdown). Otherwise `neutral`, and the
  summary says what OONI showed (not read, too few measurements, or no rise). Never call an outage a shutdown or give
  it a likelihood until Verdict has scored the rule.
- **Cable corridors** (`cables.py`, BO-IND-5, BO-H-2A): the one judgment Blackout makes (kind `assessment`, tier
  `warning`, probability with ICD 203 term and separate confidence) when drops coincide along a submarine cable
  corridor. A tip (Elements seismic/volcanic, Wake AIS) counts only when its own coordinates fall within 50 km of a
  corridor point; tips naming an outage country are context only and never raise BO-H-1A. Outages in a flagged
  corridor are observations pointing at the corridor item. The corridor register is hand-compiled and checked only
  against TeleGeography's public map: no TeleGeography data, no TeleGeography licence claimed. Map lines are
  schematic, and say so.
- IODA baseline: the same hours of the 7 days before (24 h+ events use every point); a signal with baseline median
  under `ioda.MIN_BASELINE` (20) is left out and named (`low_count`); BO-IND-1 needs BGP or active probing, never
  darknet alone; BO-IND-4 = "single-signal dip", routine. IODA's event `score` is a magnitude, never a percentage. A
  signal not read stays `None`; nothing is filled from another signal.
- Tiers: routine for standing conditions and fixed lines (OONI 7-day rates, BGP hijacks and leaks, the ZNPP's stated
  state unless an update reports a loss of power).
- Provider, FAA and public-report items are what the source reports (`status_class: reported`, direction
  neutral); never assess a cause from them. Seed builds hold no provider data. Context lanes (STOP, ITU, Citizen Lab) never change a judgment.
- `collect --live`: a lane that fails publishes nothing and is listed in `collection.problems` ("Incomplete
  collection" banner). Times on the page go through `tic-time.js` (the hub's shared copy).

## Lanes (source, terms, staleness, what makes an anomaly)
| Lane (module, indicator) | Source and terms | Rule and gotchas |
|---|---|---|
| Country outages (`ioda.py`, BO-IND-1/4) | IODA v2 events plus BGP, `ping-slash24`, `merit-nt`. No data licence stated; credited | Drop = event median vs baseline per signal, z-score; see Method |
| Watched networks (`asn_watch.py`, BO-IND-6) | IODA, `WATCHLIST` (Starlink AS14593, Amazon, Microsoft, Google, Google Cloud) | >=5% under the 7-day median for >=30 min; AS14593 is the Starlink check |
| Censorship (`ooni.py`, BO-IND-3) | OONI aggregation API. **CC BY-NC-SA 4.0** (github.com/ooni/license) | 7-day rates routine; outage-hours rule in Method |
| Radar outages (`radar.py`, BO-IND-9/10) | Cloudflare Radar, token. CC BY-NC 4.0 | Annotations: cause quoted, mapped to a hypothesis (government BO-H-1A supports; power/military BO-H-1B; cable cut BO-H-2A); verified anomalies BO-H-1A neutral. Ongoing or ended within 72 h. 429s retried per Retry-After, max 90 s |
| Routing (`bgp.py`, BO-IND-11/12) | Radar hijacks (confidence 8+) and leaks | Items only for 25+ peers (leaks also 100+ prefixes), one per leaking AS |
| Zaporizhzhia (`iaea.py`, BO-IND-13) | IAEA Top News RSS, text from Wayback (iaea.org 403s scripts; not worked around). Reuse with acknowledgement | State none/single/not_stated; overdue after 10 days |
| Shutdown history (`stop.py`, BO-EEI-6) | Access Now STOP sheet, 2025 dataset. No licence stated, none claimed | Context only |
| US grid (`odin.py`, BO-IND-14) | ODIN anonymous API; Census Gazetteer (PD) | Item per state >50,000 meters out (DOE-417 criterion 12), with `us_counties` and EAGLE-I base rate |
| US base rates (`eaglei.py`) | ORNL EAGLE-I history, figshare, CC BY 4.0. Offline only, `blackout/eaglei_rates.json` | Suspect-run rule in docstring; `verdict_rule()` writes Verdict's resolution form |
| Night lights (`blackmarble.py`, BO-IND-15) | NASA Black Marble VJ146A2, LANCE NRT then LAADS | 60% clear box; newest vs 14-night median. Licence gate is per Earthdata account: one problem, then stop |
| US demand (`eia.py`, BO-IND-16) | EIA-930 API v2, key. Public domain, acknowledge "Source: U.S. Energy Information Administration (<date>)" | 3 low hours in a row vs the BA's own 168 h; 180 s deadline, carried hours |
| Provider status (`status_feeds.py`, BO-IND-7/8) | Providers' own feeds: AWS, Google Cloud, Workspace, Azure, Microsoft 365, Apple, Cloudflare, Akamai, Zoom, Slack, Starlink, GitHub | BO-IND-8 = 2+ providers within 2 h. Quiet 14 days = long-running, routine. Apple: services, type and times only, never its text (terms). starlink.statuspage.io graded F6 |
| Ground stops (`faa.py`, BO-IND-17) | FAA ATCSCC advisories, A1, public domain | National, whole-airline, carrier requests at 2+ airports, equipment/comms. "NO Advisory" pages retried 3x, then a data note. Gap: gate holds with no ground stop |
| Public reports (`bluesky.py`, BO-IND-18) | Bluesky `searchPosts` `hitsTotal`, no account, F3 | Surge 30+ in 6 h and 5x usual. **Counts only: no post, text, handle or author kept** |
| Press reports (`gdelt.py`, BO-IND-19) | GDELT raw GKG 2.1 files (DOC API answers 429), C3. Free use citing GDELT | Surge 5+ headlines, 3+ outlets, 5x usual. **Outlets and links only, never headline text** |
| Internet use (`itu.py`) | World Bank WDI copy of ITU, CC BY 4.0 | `exposure()`: upper bound on country outages and trends, never users affected |
| SATCOM (`satcom.py`, `#satcom`) | Radar `http/*?asn=` per counted network; IODA per confirmed one | % of own 28-day normal; one series per request; thin series named. No feed items; no registry holder names on the page (PeeringDB AUP, RIPE terms) |
| NetBlocks (`netblocks.py`, BO-IND-20) | NetBlocks' Bluesky account (getAuthorFeed, no account). All rights reserved, no reuse licence | Whole account each run; items 72 h; parsed fields, 110-char excerpt, link, **never full text** (fixtures too); `data/netblocks.json` |
| Daily series (`trends.py`) | IODA, Radar and NetBlocks; 61 days re-read each run | `data/trends.json`, 7/14/30-day windows; IODA bursts (20+ per half hour) left out; standing BGP-only flags (18 h+ a day, 7+ days in a row) marked, `standing_hours` apart; settled days to `history/trends-days.jsonl`; `#trends` tab |

Public-report items (BO-WP-7, BO-H-7A/7B/7C; names in `bluesky.WATCH`/`gdelt.WATCH`) say reported by the public or
press, not confirmed by the company. Unbuilt sources: memory `outage-public-sources`.

## Feed contract revision 2.1 (step 4.1, 18 Nov 2026)
- Header `contract_rev: "2.1"` and `kit`; `check` fails on header errors. Every builder calls `feed._rev21()`
  (`subject_id` stable and undated, event times from source fields, `signal`, `place_basis`). New kinds need it.

## Per-source health (Tom's yes, 14 Nov 2026)
- `feed.geojson` carries `health` per lane (an item's first source id, `feed.LANES`, 26 lanes): state, last_ok,
  failures in a row. A failed lane's items are dropped, so the history log and COP need it before calling an end.
- `feed.problem_lanes` places each problem on a lane (unplaced: every lane; tips, Citizen Lab, trends and data notes:
  none), so a mistake only delays an end. **A new collector or problem source needs a line there** (a test lists them).
- A failed lane keeps the previous feed's `last_ok`. Tests: `TestSourceHealth`.

## Licences in output
The feed `licence` is the kit registry's `terms` word for word (`feed.LICENCE`); `feed.ATTRIBUTION` names every
source; a new source's licence goes in `doctrine/sources.json` and the kit's terms; a test keeps them in step.

## Page (`site_src/`)
- Ten tabs with ARIA tabs pattern and hash routing (`#overview`, `#outages`, `#censorship`, `#cables`, `#power`,
  `#routing`, `#providers`, `#satcom`, `#trends`, `#method`); `#<tab>/<key>` highlights rows; map keys open the panel. Feed and COP links
  depend on these hashes: keep them.
- `tic-nav.*` and `tic-related.*` ("Elsewhere today") are the hub's `site/shared/` copies, byte for byte.
- Map: vendored MapLibre (BSD-3), Natural Earth, OpenFreeMap; credits on map, footer and Method.
- CSP is COP's (`site_src/_headers`, no `'unsafe-inline'`; DOM built as text); a test keeps it. Check light, dark,
  390 px and desktop under `tools/serve.py`.

## How COP uses Blackout
COP merges the feed, keys its ledger on `subject_id`, names the item kinds in MCP rule 2; triage reads `us_counties`.
