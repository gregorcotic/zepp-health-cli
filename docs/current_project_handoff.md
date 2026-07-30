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
- Sport-specific canonical `ski_vertical_m` semantics
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

## Legacy audit — final status

Status: BROAD LEGACY AUDIT CLOSED

Production-validated / implementation-ready domains:

- Exertion core metrics
- PHN / Zepp Coach
- Charge / HybridCharge
- Stress via `all_day_stress`
- Food / Nutrition
- native activity detail
- canonical activity model
- activity persistence / incremental sync
- LifeLoad semantics
- activity-calorie semantics
- Zepp UI total-consumption relationship

Deferred non-blocking semantics:

- Respiratory Rate binary decoder/readback
- exact `recoveryFactor` labels
- exact `recoveryFactorID` dictionary
- exact `insightState` dictionary
- exact PHN 51/61/62 meaning
- exact Resting-energy source/algorithm
- exact Charge cumulative-energy internal algorithms

These unresolved items are not blockers for TRC or for integrating the
production-validated factual domains.

Standing rule:
do not restart broad Legacy -> current gap research.

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



## Stress — factual contract solved

Status: IMPLEMENTED / LIVE-VALIDATED THROUGH INCREMENTAL SYNC

Do NOT restart broad Stress reverse engineering.

### Preferred factual contract

Production captures expose:

`eventType = all_day_stress`

The payload contains the final factual Stress layer directly.

Known fields:

- `data`
- `minStress`
- `maxStress`
- `avgStress`
- `relaxProportion`
- `normalProportion`
- `mediumProportion`
- `highProportion`

`data` is a JSON 5-minute Stress timeline containing:

- `time` = epoch milliseconds
- `value` = Stress score

The timeline is sparse. Missing 5-minute intervals mean no valid Stress
measurement; they must not be converted to zero.

### Production-validated score semantics

Stress score range:

- 0–39 = Relaxed
- 40–59 = Normal
- 60–79 = Medium
- 80–100 = High

These boundaries were validated both from the Zepp UI and mathematically
against native all_day_stress category proportions.

### Mathematical validation

Production fixtures reproduced native:

- minStress
- maxStress
- category proportions

directly from `data[].value`.

Known examples:

28-May-2026:
- 73 samples
- min 10 = native 10
- max 53 = native 53
- mean 28.1233
- native avgStress 28
- calculated proportions 73/27/0/0 = native 73/27/0/0

Another 28-May snapshot:
- 72 samples
- mean 27.8333
- native avgStress 27

2-Jun-2026:
- 112 samples
- min 10 = native 10
- max 55 = native 55
- mean 34.0357
- native avgStress 34
- calculated proportions 54/46/0/0 = native 54/46/0/0

Native `avgStress` remains authoritative. Evidence suggests Zepp may truncate
or otherwise integer-convert the mean in some snapshots rather than applying
ordinary rounding.

### stressInfo relationship

`Charge / stress_data / stressInfo` remains an encoded protobuf-like internal
feature/model package.

Manual reverse engineering established that its large arrays and repeated
feature vectors are NOT required to obtain final factual Stress.

Do NOT decode stressInfo for ordinary TRC/coach Stress support unless a future
requirement proves it contains a needed metric unavailable from
`all_day_stress`.

### Current implementation

I001.1–I001.5 are implemented:

- production readback through `all_day_stress/all_day_stress`
- canonical integer normalization
- SQLite schema v7 daily and sparse-sample persistence
- dedicated incremental synchronization
- SQLite-only factual `stress` CLI output
- local-date factual freshness (`current`, `stale`, `missing`)

Native daily aggregates remain authoritative. The CLI never fills missing
five-minute intervals and does not expose stored raw payload text.


## Food / Nutrition — production-validated core contract

Status: FOOD LOG IMPLEMENTED / LIVE READ PATH RETURNED EMPTY

Do NOT restart broad Food/Nutrition reverse engineering.

### Native Food Log contract

Production-captured endpoint:

POST `/v2/users/me/events`

with:

- `eventType = Food`
- `subType = real_data`

A controlled Banana fixture was production-captured and matched the Zepp UI.

Known fields include:

- `foodLogId`
- `mealType`
- `mealName`
- `foodName`
- `measureWeight`
- `weightUnit`
- `energy`
- `carbohydrates`
- `protein`
- `fatTotal`
- `fiber`
- `servings`
- `labels`
- `emoji`
- `recognizeType`
- `recognizeSourceType`

### Banana production fixture

Known Zepp UI state:

- meal: Afternoon Snack
- meal time: 13:36
- Banana x2
- original weight: 240 g
- original energy: 210 kcal
- UI macros approximately:
  - carbs 54 g
  - protein 3 g
  - fat 1 g

Controlled edit:

240 g -> 250 g

Native payload then contained approximately:

- `measureWeight = 250`
- `energy = 218.75`
- `carbohydrates = 56.25`
- `protein = 2.708333...`
- `fatTotal = 0.833333...`
- `fiber = 3.1`

The same `foodLogId` remained associated with the edited entry.

This is strong production evidence for edit/upsert identity semantics.

### mealType dictionary

Production-validated by moving the same Banana entry through all six Zepp meal
categories while keeping food identity and other fields stable.

Mapping:

- `1` = Breakfast
- `2` = Morning Snack
- `3` = Lunch
- `4` = Afternoon Snack
- `5` = Dinner
- `6` = Evening Snack

Do NOT treat these as guessed labels; this mapping is production-validated.

### Food Goals contract

Known contract:

POST `/users/{user_id}/properties`

Property:

`huami.mifit.user.settings.food.goal`

Known fields:

- `calorie`
- `carb_percent`
- `protein_percent`
- `fat_percent`

Known UI fixture:

- calorie goal = 2700 kcal
- carbs = 30%
- protein = 45%
- fats = 25%

Zepp requires:

`carb_percent + protein_percent + fat_percent = 100`

A later controlled UI state used:

- carbs = 35%
- protein = 40%
- fats = 25%

### Food Insight

Production-captured endpoint:

`/aura/insight/food/daily`

Important correction:

the `calorie` query parameter was matched against Zepp UI Exercise/Activity
calories and is NOT consumed-food calories.

Examples:

- 26-May-2026: UI Exercise 338 == endpoint calorie 338
- 27-May-2026: UI Exercise 930 == endpoint calorie 930

### Standing architecture rule

Keep separate:

- Food Log = factual consumed-food records
- Food Goals = user-configured targets
- Food Insight = advisory/AI narrative layer

Future TRC/coach logic should consume factual Food Log + Goals data first.
Food Insight may be preserved as optional advisory evidence but must not replace
the factual nutrition layer.

### Current Food Log implementation

I002 implements the audited `Food / real_data` event source through:

- canonical normalization with numeric-string handling
- exact mealType labels with safe unknown-value fallback
- SQLite schema v8 `food_entries`, keyed by native `foodLogId`
- incremental `sync-db` persistence
- raw-free database reads and `food --days N` CLI output

No native daily nutrition summary was proven, so the CLI does not calculate or
present entry sums as native totals. It explicitly reports native daily totals
as unavailable.

Food Goals remain separate. The property and fields are production-known, but
the current client has no established safe property read contract. I002 does
not add goal writes or speculative goal read transport.

A read-only 30-day and 90-day production GET validation on 2026-07-29
successfully returned an empty Food domain. This validates empty-state and
sync handling but is not a non-empty live normalization fixture.



## Respiratory Rate — deferred for later decoder work

Status: PARTIALLY REVERSE-ENGINEERED / DEFERRED

Production evidence proves:

`eventType = RespiratoryRate`
`subType = real_data`

The native payload contains encoded `value.measurements`.

Legacy discovery concluded that this measurements payload is binary/base64 and
not yet import-ready.

Legacy code also contains a prepared parser/model for:

`RespiratoryRate / record`

with expected fields such as:

- `respiratoryRate`
- `recoveryScore`
- `minuteOfDay`

However, provenance review found no production raw capture proving that the
`record` contract actually exists on the current account.

Those fields are currently fixture-derived, not production-proven.

Do NOT port the fixture-based `record` model into current code as factual truth
without production evidence.

Recommended future work:
use a focused Codex-assisted corpus/binary investigation to establish either a
real readback contract or a decoder for real_data.measurements.

Respiratory Rate is not currently a blocker for TRC.



## Resting calories / total consumption — validated UI relationship

Status: DISPLAY RELATIONSHIP SOLVED / RESTING SOURCE UNRESOLVED

Production UI fixtures:

26-May-2026:
- Resting = 2307 kcal
- Activity = 338 kcal
- Total consumption = 2645 kcal

27-May-2026:
- Resting = 2307 kcal
- Activity = 930 kcal
- Total consumption = 3237 kcal

28-May-2026:
- Resting = 2307 kcal
- Activity = 406 kcal
- Total consumption = 2713 kcal

All three satisfy:

`Total consumption = Resting + Activity`

No direct production field named restingCalories, restingEnergy, BMR, TDEE or
equivalent was found in the capture corpus.

Therefore:

- Zepp UI Total consumption semantics are validated.
- Activity/Exercise calories are production-validated.
- The source or algorithm behind Zepp's Resting value remains unresolved.
- Do NOT label Resting as BMR without evidence.
- Do NOT invent a Resting formula in current code.



## SPORT_LOAD — newly discovered Training Status contract

Status: PRODUCTION DISCOVERED / NEXT DEDICATED BATCH

Live capture exposed:

`WatchSportStatistics / SPORT_LOAD`

Observed fields include:

- `currnetDayTrainLoad`
- `wtlSum`
- `wtlSumOptimalMin`
- `wtlSumOptimalMax`
- `wtlSumOverreaching`

Known production fixture:

- current day train load = 0
- wtlSum = 437
- optimal minimum = 330
- optimal maximum = 735
- overreaching threshold = 864

The same Zepp Training Status UI showed `Optimal`.

Because:

`330 <= 437 <= 735`

the contract is strongly consistent with the UI Optimal classification.

Do NOT assume `wtlSum` is ATL or CTL.

Known UI values from the same Training Status system include:

- ATL / Fatigue Level
- CTL / Fitness Level
- TSB / Form

with:

`TSB = CTL - ATL`

SPORT_LOAD should be investigated and implemented as a separate next batch.

## SPORT_LOAD factual implementation — S001.3

Status: IMPLEMENTED AND LIVE-VALIDATED

SQLite schema v9 adds `sport_load_records`, one native daily row per `dayId`.
Canonical fields preserve `generated_time_s`, `updated_time_ms`,
`current_day_training_load`, conservative `wtl_sum`, native optimal bounds,
the overreaching threshold, and device provenance. No status-category formula
is implemented.

`sync-db` now retrieves SPORT_LOAD through its dedicated
`WatchSportStatistics / SPORT_LOAD` endpoint, follows native cursor
pagination, and records `sport_load` in `sync_run_domains`. The
SQLite-backed command is:

```bash
python3 zepp_health.py sport-load --days 30
python3 zepp_health.py sport-load --days 30 --json
```

Production validation on 2026-07-29 inserted 7 recent rows. An identical
second sync produced `inserted=0`, `updated=0`, `unchanged=7`. A bounded
two-page historical validation stored 961 unique dates from 2023-11-01
through 2026-07-29. SPORT_LOAD remains separate from Exertion ATL/CTL/TSB and
is not integrated into TRC.
