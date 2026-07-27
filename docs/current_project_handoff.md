# ZEPP-HEALTH-CLI — Current project handoff / do-not-repeat context

Updated: 2026-07-27

This file describes CURRENT project status.

When status text in older audit material conflicts with this document, use:

1. current production code and tests
2. newer production evidence
3. this handoff
4. historical audit notes

## Architecture

`zepp-health-cli` is the primary Zepp-native factual data layer for future
TRC / AI Coach use.

Zepp is the primary native activity/health source.
Strava is optional validation/enrichment.

Do not restart the general Legacy ZeppAiAgent → current-project gap audit.

## Already solved

Do not repeat broad research into:

- Exertion Batch 1A
- ATL / CTL / TSB
- Daily Suggestion / exercisePlan
- `(type, sport_mode)` activity identity
- `sport_mode=5` Zepp Coach activity marker
- native activity detail endpoint
- Workout Notes (`memo`)
- canonical native activity model
- native activity persistence/incremental sync
- Ski descent semantics
- optional-sensor absence semantics
- known altitude sentinel handling
- LifeLoad vs BioCharge distinction
- broad Wake BioCharge discovery
- general morning freshness handling
- PHN training-day bitmask
- PHN record ↔ workout completion matching

## PHN / Zepp Coach

Batch 1B: DONE / LIVE VALIDATED.

Native GET access is production-proven for:

- `phn / record`
- `phn / training_plan`

`phn/record` is historical daily Coach state.

`phn/training_plan` is a persistent mutable plan object.

Production-proven identity/freshness contract:

`phn/record.phn_plan_id`
=
`phn/training_plan.event.timestamp`

Current plan freshness:
`last_update_time`

Do not use the old training-plan event date as freshness.

SQLite schema v6 stores:

- `phn_daily_records`
- `phn_training_plans`

### PHN flag semantics

Keep raw native flags authoritative.

Still strong:

- 31 = normal configured rest/non-training state
- 41 = adaptive/dynamic Coach rest override
- 71 = zero counted plan progress state
- 72 = partial counted progress state

SUPERSEDED:

- 51 / 61 / 62 as simple completion-percentage buckets

Broader GET history disproved that model.

Exact meanings of 51 / 61 / 62 remain open.

## Persistence rule

Raw payload equality is NOT canonical equality.

A record is `unchanged` only when:

- raw native `source_json` is unchanged
- all persisted normalized columns are equivalent

This keeps historical raw evidence safely re-normalizable when parsers improve.

## Still open legacy-specific work

Prioritize only concrete unresolved items:

1. remaining Charge/BioCharge semantics
2. Stress
3. Food/Nutrition integration
4. Respiratory Rate current-project validation only
5. recoveryFactor semantics
6. recoveryFactorID semantics
7. insightState semantics
8. resting calories / TDEE
9. exact PHN 51/61/62 semantics only if useful evidence appears

Separately, native activity quality/elevation validation remains new current
project work and must not be conflated with Legacy migration.


## Charge / HybridCharge — core semantics solved

Status: CORE SEMANTICS PRODUCTION-VALIDATED

Do NOT restart broad Charge / BioCharge reverse engineering.

### Charge / real_data

Native fields:

- `total`
- `physical`
- `mental`

Production validation against the Zepp UI confirms:

`total`
= native Zepp HybridCharge display score

`physical`
= Physical Charge component

`mental`
= Mental Charge component

Across a 30-day validation:

- valid real_data samples: 42,512
- rounded physical/mental mean matched native `total`: 42,497
- match rate: 99.9647%

This proves a very strong relationship between the physical/mental components
and native HybridCharge, but native `total` remains authoritative.

Do NOT reconstruct `total` when a valid native value exists.

### Sentinel 255

`total = 255`

is an invalid/unavailable HybridCharge sentinel.

It was observed both:
- at the current tail
- inside historical real_data sequences

Therefore it is NOT merely an unfinished final sample.

Never expose 255 as a physiological HybridCharge value.

### Charge / wake_data

Production UI comparison confirms:

`wakeCharge`
= Zepp UI Wake HybridCharge

Also available:

- `physicalWake`
- `mentalWake`
- `bioChargeWake`

`bioChargeWake` is a closely related precise internal hybrid metric.

Do NOT assume:

`wakeCharge == round(bioChargeWake)`

30-day evidence disproved that simple formula.

### Charge / summary

Native daily summary fields include:

- `minCharge`
- `minChargeTimestamp`
- `maxCharge`
- `maxChargeTimestamp`
- `cumulativeChargingEnergy`
- `cumulativeConsumptionEnergy`

`maxCharge` is strongly validated as the native daily maximum HybridCharge.

Do NOT derive `minCharge` by simply taking the minimum integer `real_data.total`.
30-day comparison showed exact agreement on only 13/29 comparable days.

Do NOT derive cumulative charging/consumption from integer total deltas.
The native summary values remain authoritative.

Interpretation:

`cumulativeChargingEnergy`
= native accumulated charging/regeneration metric

`cumulativeConsumptionEnergy`
= native accumulated depletion/consumption metric

Exact internal accumulation algorithms remain open but are NOT blockers for TRC.

### 22-Jul-2026 production fixture

Zepp UI:

- Wake HybridCharge = 74
- Cross-training impact = -2
- Nap impact = +4
- Sleep impact = +32

Native Charge summary:

- minCharge = 35
- maxCharge = 74
- cumulativeChargingEnergy = 40
- cumulativeConsumptionEnergy = 42
- wakeCharge = 74

Native real_data:

Cross-training:
45 -> 43
= -2

Nap:
35 -> 39
= +4

These are direct UI-to-native confirmations that activity/rest events can
produce attributable HybridCharge changes.

The full Sleep +32 value cannot be reconstructed from one real_data UTC bucket
because the sleep interval crossed the UTC bucket boundary.


## Charge time semantics — production validated

The Charge domains use different calendar semantics.

### Charge / real_data

`real_data` is UTC-calendar-day bucketed.

22-Jul-2026 production evidence:

`value.startTime`

= 2026-07-22 00:00 UTC
= 2026-07-22 02:00 Europe/Ljubljana during CEST

The sample range is:

00:00-23:59 UTC

which appears locally as:

02:00 22-Jul -> 01:59 23-Jul

IMPORTANT:

02:00 local time is NOT a physiological or Zepp-defined start of day.

It is simply UTC midnight represented in Europe/Ljubljana during UTC+2.

### Charge / summary and Charge / wake_data

These use local-calendar-day semantics through `value.startTime`.

For local 22-Jul-2026:

`value.startTime`

= 2026-07-21 22:00 UTC
= 2026-07-22 00:00 Europe/Ljubljana

Therefore local-day attribution for summary/wake_data must use the local date
derived from `value.startTime`.

Do NOT use `event.timestamp` as the local calendar-day key for these domains.

### Standing parser rule

Keep separate:

- API transport/bucket time
- local calendar date
- physiological/event meaning

Never infer local-day semantics from UTC bucket boundaries.

