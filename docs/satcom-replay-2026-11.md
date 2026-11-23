# What the satellite drop rule catches: a replay (BO-IND-21, 23 Nov 2026)

**Result:** the rule works as a detector of sustained drops, but it misses short outages, and it fires when Cloudflare
itself has trouble. It caught 2 of 5 known Starlink events. At the scheduled run times it would have missed the largest
one worldwide. This report changes nothing: the rule, its thresholds and the feed are as they were.

Tool: `tools/satcom_replay.py` (`run`, `report`, `compare`). Data: Cloudflare Radar (CC BY-NC 4.0), hourly, 20 Jun 2025
to 23 Nov 2026, for the 33 series the rule could judge on 23 Nov 2026: 13 networks worldwide and Starlink in 20
countries. That is 378,471 series-hours judged and 11,259 not judged (thin or coarse).

**Check on the method.** Radar limits an hourly request to about a month, so each series is stitched from overlapping
parts. On 67 sampled windows the stitched series and Radar's own 29-day answer gave the same rule state every time, with
percentages within 1 point.

## Known events

| Event | Lowest hour, % of normal | Hours under 70% | Run timings that fire (of 4) |
|---|---|---|---|
| Starlink worldwide, 13 Sep 2023 | 52.9 | 2 | 0 |
| Starlink worldwide, 24 Jul 2025 (Radar: 19:13-21:28 UTC) | 7.1 | 3 | 1, not the scheduled one |
| Starlink worldwide, 18 Aug 2025 | 97.2 | 0 | 0 |
| Starlink in Ukraine, 15 Sep 2025 | 66.6 | 1 | 0 |
| Starlink in Ukraine, 4 Feb 2026 (registration deadline) | 51.0 | 17 | 4 |

Sources: NetBlocks' archived posts for the first four, Blackout's first replay for the last. The 18 Aug and 15 Sep 2025
events were shorter than an hour's average can show; Radar's 15-minute data does show dips on both.

On 24 Jul 2025 the rule fired on 13 Starlink series (worldwide and 12 countries) at one timing or another. Only 4
countries (DE, YE, CO, NZ) fired at the scheduled timing. The worldwide series reached 69.3%, against a line of 70%, at
one timing only.

## Everything the rule would have flagged

180 episodes in 17 months; 123 at the scheduled run timing, about 7 a month.

| What else was true | All timings | Scheduled timing |
|---|---|---|
| All of Radar's traffic fell too, not a holiday | 21 | 2 |
| Near a major holiday | 46 | 36 |
| The whole country's traffic fell too | 43 | 38 |
| An independent report of this network or country | 14 | 5 |
| None of these | 56 | 42 |

- **Radar-wide:** 20 of the 21 are 18 Nov 2025, when all of Radar's traffic fell to 13% of normal during
  [Cloudflare's own outage](https://blog.cloudflare.com/18-november-2025-outage/). Radar counts requests reaching
  Cloudflare, so its trouble lowers every network at once. The other is a Radar data gap on 16 Jul 2026.
- **Whole country:** almost all Myanmar (20) and Yemen (21). The satellite network fell with the rest of the country, so
  the drop is not specific to satellite service.
- **None of these does not mean false.** Nobody has checked those 56. They concentrate in Myanmar (17), Spain (10) and
  three small networks, and may be real local events no public source reported.
- **Growth barely moves the line.** Starlink's median hour reads 103.4% of its weekday normal worldwide, and 102-104% in
  the countries checked.

## Options tested (none is live)

Every candidate drops hours when all of Radar's traffic is under 85% of normal. "Short reading": any hour under the line
against the same hour 1-4 weeks earlier, with at least 100 Radar counts in the normal.

| Short reading, one hour under | Known events caught | Starlink worldwide | Starlink countries | Other networks |
|---|---|---|---|---|
| 40% | 1 | 1 | 30 | 2 |
| 50% | 1 | 1 | 67 | 9 |
| 60% | 3 | 1 | 133 | 40 |

Cells after the first are episodes in 17 months. On Starlink worldwide the 60% reading's only episode is 24 Jul 2025, and
it also catches 13 Sep 2023 in that event's own replay. It is too noisy for countries and other networks.

## After the change (ideas#168)

`python tools/satcom_replay.py after`, the same 33 series and 17 months, with the Radar-wide hold and the short reading:

| | Before | After |
|---|---|---|
| BO-IND-21 episodes, all timings | 180 | 144 |
| BO-IND-21 episodes, scheduled timing | 123 | 110 |
| Episodes on Cloudflare's outage of 18 Nov 2025 | 20 | 0 |
| BO-IND-22 episodes, Starlink worldwide | none | 1 (24 Jul 2025, 19:00 UTC) |

37 episodes are held whole. The earlier estimate of 41 (17 at the scheduled timing) counted an episode as held if the
control dipped anywhere in its span. The live hold judges each run from the drop's start to that run, so a run before
the control dipped still fires. A drop that spans a held hour stays held until it ends.

## Limits
- Five known events is a case list, not a hit rate, and the 60% line was chosen on those same events. It needs a
  forward check.
- The series are those the rule could judge on 23 Nov 2026, so networks that were thin then are not represented.
- "Scheduled timing" is the 4-hourly dispatch (readings through 01, 05, ... 21 UTC). Pushes and the fallback cron add
  runs at other hours.
- Holidays are a short fixed list in the tool. Being near one does not show that the holiday caused a drop.
