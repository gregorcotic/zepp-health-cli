# Legacy ZeppAiAgent Audit – Verified Findings and Integration Status

Last updated: 2026-07-27

Purpose:
Preserve findings from the Legacy `ZeppAiAgent` reverse-engineering audit so
that verified behavior is not researched or implemented repeatedly.

This document distinguishes:
- VERIFIED = supported by raw API / UI / repeatable production evidence
- HIGH CONFIDENCE = strong repeated evidence, exact internal Zepp meaning may be undocumented
- OPEN = still requires investigation

---

# 1. Repository roles

## Legacy ZeppAiAgent
Role:
- research
- reverse engineering
- captured Proxyman evidence
- historical experiments

Do not treat old normalized labels as authoritative without validation.

Known Legacy problem:
The old workout mapper sometimes normalized only the first sport `type` field
and therefore produced incorrect labels.

## zepp-health-cli
Role:
- current production Zepp collector
- normalized Zepp-native data
- SQLite persistence
- current source for future TRC / AI coach integration

## strava-sync
Role:
- current Strava activity ingestion

## coach-data-bridge
Role:
- integration/context layer between Zepp + Strava and future TRC/AI coach

Architecture rule:

RAW ZEPP DATA
    ↓
VERIFIED / NORMALIZED ZEPP SIGNALS
    ↓
TRC / AI COACH

Never discard raw native values merely because their semantics are still unknown.

---

# 2. Batch 1A – Exertion

Status: IMPLEMENTED AND LIVE-VALIDATED

Local commit:
`2517ae8 Extend native Zepp exertion metrics`

SQLite schema:
v5

Current normalized Exertion fields:

- recoveryFactor
- recoveryFactorID
- totalScore
- activityScore
- exerciseScore
- targetScore
- completionPercent
- atl
- ctl
- tsb
- insightState
- exercise_plan_intensity
- exercise_plan_duration
- exercise_plan_hr_lower
- exercise_plan_hr_upper

Original raw `exercisePlan` remains preserved.

## Verified Exertion relationships

VERIFIED / VERY HIGH CONFIDENCE:

`totalScore = activityScore + exerciseScore`

Observed repeatedly in live production data.

`completionPercent ≈ totalScore / targetScore × 100`

Observed live examples include:
- 23 / 13 ≈ 176 %
- 716 / 83 ≈ 862 %
- 49 / 71 ≈ 69 %

`TSB = CTL - ATL`

Exercise Plan represents Zepp's native Daily Suggestion.

Observed:

`exercisePlan.intensity = 1`
→ UI meaning: LOW INTENSITY

exercisePlan also carries:
- duration
- heartRateLower
- heartRateUpper

## Still unresolved

Do NOT invent semantics for:

- recoveryFactor exact scale
- recoveryFactorID 1/2/3
- insightState exact state dictionary

Observed insightState values include at least:
- 2
- 3
- 6

Keep these fields raw until separately decoded.

---

# 3. Correct Zepp activity identity

Activity identity must use:

`(type, sport_mode)`

NOT only `type`.

This corrects an important Legacy mapping error.

## Current mapping

105 / 0 = Ski
130 / 0 = Cross-training

14 / 0 = Pool Swim

15 / 0 = Open Water Swim
15 / 5 = Open Water Swim - Zepp Coach

207 / 0 = E-MTB
208 / 0 = Gravel Cycling

22 / 0 = Hiking
22 / 5 = Hiking - Zepp Coach

224 / 0 = Mountain Hiking

6 / 0 = Walking
6 / 5 = Walking - Zepp Coach

9 / 0 = Outdoor Cycling
9 / 5 = Outdoor Cycling - Zepp Coach

## Zepp Coach marker

VERIFIED:

`sport_mode = 5`
→ Zepp Coach-associated activity

Direct API + Zepp UI ground-truth examples:

### 2026-02-25
UI:
Outdoor cycling · Zepp Coach

Raw API:
`type = 9`
`sport_mode = 5`

Additional exact match:
- UI start about 14:18
- API 14:18:30
- UI 25.75 km
- API 25,755 m
- UI duration 1:09:27
- API 4,167 s

Therefore:

`9 / 5 = Outdoor Cycling - Zepp Coach` VERIFIED

### 2026-06-06
UI:
Open water · Zepp Coach

Expected/observed mapping:
`15 / 5`

### 2026-06-13
UI:
Hiking · Zepp Coach

Expected/observed mapping:
`22 / 5`

Important:

`22 / 5 != Mountain Hiking`

Mountain Hiking is:

`224 / 0`

---

# 4. Zepp Coach / PHN architecture

Current `zepp-health-cli` status:
PHN SUPPORT IS NOW IMPLEMENTED AND LIVE-VALIDATED IN `zepp-health-cli` (Batch 1B, 2026-07-27).

Repository search on 2026-07-27 found no current implementation for:

- phn
- training_plan
- daily_smart_plan
- degree_of_completion
- phn_plan_id
- exercise_day
- weekly_high_intensity_day

This is Batch 1B.

## PHN event model

Observed endpoint:

`POST /v2/users/me/events`

PHN is represented through multiple subTypes.

Two important ones:

### phn / training_plan

Represents the current/mutable Zepp Coach plan state.

Observed fields include:

- phn_plan_id
- exercise_day
- training_days
- weekly_high_intensity_day
- current_weekday
- flag_recommended_exercise
- trimp_daily_recommended
- daily_recommend_intensity
- duration_zone1
- duration_zone2
- duration_zone3
- yesterday_recommend_flag
- this_week_achieved_daily_completed_percent
- ATL/CTL/TSB arrays and training history state
- other native Coach state fields

Important finding:

`training_plan` behaves like a mutable/current Coach engine state,
not like one independent historical record per day.

The plan event timestamp / phn_plan_id may remain stable while
`last_update_time` and internal state change.

### phn / record

Represents daily historical Coach state.

Important native fields:

- flag
- degree_of_completion
- degree_of_completion_week
- phn_plan_id

These records were found in batch POST request bodies to
`/v2/users/me/events`.

SUPERSEDED: production validation on 2026-07-27 proved native GET access for both `phn/record` and `phn/training_plan` through `/v2/users/me/events`.

---

# 5. Training Calendar

VERIFIED configuration example:

`exercise_day = 53`

Observed:

`training_days = [0, 2, 4, 5]`

Mapping:

0 = Monday
2 = Wednesday
4 = Friday
5 = Saturday

Bitmask check:

1 + 4 + 16 + 32 = 53

Therefore configured training days were:

MON / WED / FRI / SAT

Observed weekly intensity configuration:

`weekly_high_intensity_day = [2,0,1,0,3,1,0]`

MON = 2
TUE = 0
WED = 1
THU = 0
FRI = 3
SAT = 1
SUN = 0

IMPORTANT:
Testing showed weekly_high_intensity_day class does NOT directly determine
PHN flag family 5x/6x/7x.

Do not implement that earlier hypothesis.

---

# 6. PHN daily completion

Very strong evidence shows:

`run/history.degreeOfCompletion`
and
`PHN record.degree_of_completion`

represent the same daily Coach-plan completion metric.

Observed same-day matches:

13.05 = 45 / 45
15.05 = 121 / 121
18.05 = 153 / 153
20.05 = 127 / 127
23.05 = 1 / 1
25.05 = 81 / 81
27.05 = 121 / 121
29.05 = 12 / 12
30.05 = 428 / 428
01.06 = 51 / 51

10/10 tested same-day comparisons matched.

## dailyPlanFinished

Observed:

completion < 100 %
→ dailyPlanFinished = false

completion >= 100 %
→ dailyPlanFinished = true

This relationship matched all tested records.

---

# 7. PHN flag reverse engineering

Do NOT interpret the numeric code as two independent digits unless future
evidence proves that structure.

Earlier hypothesis that `x1/x2` corresponded to sport_mode 0/5 was disproven.

All flag families 51/61/62/71/72 occurred with normal `sport_mode=0`
activities.

## Flag 31

VERY HIGH CONFIDENCE:

Normal configured rest / non-training day.

Observed:
- degree_of_completion = 0
- Coach daily target = 0 in training_plan example
- activities may still be performed on this day
- such activity does not necessarily count toward Coach completion

Example:
Mountain Hiking on flag 31 day with meaningful exercise load still showed
Coach completion = 0.

## Flag 41

VERY HIGH CONFIDENCE:

Adaptive/dynamic Coach rest override on what would otherwise be a training day.

Observed:
- completion = 0
- separate state from normal rest flag 31

Strong sequence observed:

very large over-completion
→ scheduled rest day
→ next configured training day converted by Coach to flag 41 rest

Example sequence:

09.05 flag 51, completion 2657 %
10.05 flag 31
11.05 flag 41

## Flag 71

VERY HIGH CONFIDENCE:

Training-plan day with zero counted progress.

Observed values:
0 % only.

## Flag 72

VERY HIGH CONFIDENCE:

Training-plan day with partial counted progress below target.

Observed values in dataset:
1–81 %

All observed values:
> 0 and < 100

## Flag 61

HIGH CONFIDENCE:

Target achieved / moderate over-completion state.

Observed values:
121–134 %

Exact threshold is NOT proven.

## Flag 62

HIGH CONFIDENCE:

Stronger over-completion state.

Observed values:
153–208 %

Exact threshold is NOT proven.

## Flag 51

HIGH CONFIDENCE:

Extreme over-completion state.

Observed examples:
428 %
2657 %

Exact threshold is NOT proven.

## Important conclusion

Current evidence fits approximately:

rest:
31 = normal rest
41 = adaptive Coach rest

training:
71 = 0 %
72 = partial <100 %
61 = moderate over target
62 = larger over target
51 = extreme over target

BUT exact boundaries between 61 / 62 / 51 remain OPEN.

Do not hardcode guessed thresholds such as 150 or 250.

Always preserve native `flag`.

---

# 8. Zepp Coach versus Coach-started activity

These are two separate concepts.

PHN Coach plan state:
- training_plan
- record
- daily completion
- PHN flag

Activity launch/context:
- sport_mode

Therefore:

`sport_mode = 5`

does NOT explain PHN flag 51/61/62/71/72.

Do not join these concepts into one field.

---

# 9. Calories

VERIFIED:

`DailyHealth.totalCalories`
represents active/activity calories in the investigated context.

It must NOT automatically be interpreted as TDEE.

Observed:

`calorieGoal = 500`

corresponds to the Zepp Daily Calorie Burn Goal visible in the app.

OPEN:

- resting calories source
- total daily energy expenditure
- whether Zepp sends resting calories or calculates them locally

Do not derive TDEE without evidence.

---

# 10. Food / Nutrition

Legacy audit found native Food events containing useful structured nutrition.

Observed capabilities include:

- AI food recognition
- calories
- protein
- carbohydrates
- fat
- fiber
- mass/portion
- mealType
- foodLogId
- edit/update/upsert behavior
- nutrition goals / macro targets

Food is suitable for future integration.

OPEN:

numeric `mealType` mapping to:
- Breakfast
- Lunch
- Dinner
- Snack

This should be solved with a small controlled capture rather than guessed.

---

# 11. LifeLoad

IMPORTANT CORRECTION:

LifeLoad is NOT BioCharge.

LifeLoad comes from subjective/self-report questionnaire input.

Classification:

`LifeLoad = subjective user-entered signal`

Do not use it as:
- automatic recovery
- objective body energy
- BioCharge

Current `zepp-health-cli` documentation/CLI should eventually reflect this.

---

# 12. Charge / BioCharge

Still under audit.

Known native domains include:

### Charge / real_data

Observed fields:
- total
- physical
- mental

### Charge / summary

Observed candidates include:
- minCharge
- maxCharge
- cumulativeConsumptionEnergy
- cumulativeChargingEnergy

Likely related to BioCharge, but exact semantics must remain evidence-driven.

Wake BioCharge and Current BioCharge are separate concepts.

Do not map LifeLoad to BioCharge.

OPEN:
- exact current BioCharge contract
- physical / mental meaning
- daily charge curve semantics
- summary field meaning

---

# 13. Wake / physical / mental fields

Observed Legacy/current fields include:

- physicalWake
- mentalWake
- dailyFitnessScore
- stressFitnessScore
- exertionScore

Do not automatically equate these with:
- Charge.physical
- Charge.mental

Relationship remains OPEN.

---

# 14. Respiratory rate

Native source known:

RespiratoryRate / record

Legacy had parser support.
Current project also exposes respiratory-related event access.

Still needs current-project gap validation before any new port.

Do not duplicate existing implementation.

---

# 15. Stress

Native `stress_data` exists.

Legacy parser was not fully completed.

Status:
OPEN reverse-engineering task.

---

# 16. Remaining audit backlog

High priority:

1. Batch 1B – PHN / Zepp Coach implementation
2. BioCharge / Charge validation
3. Physical/Mental Wake relationship
4. recoveryFactor exact semantics
5. recoveryFactorID exact semantics
6. insightState exact semantics
7. PHN exact 61/62/51 thresholds
8. Stress
9. Resting calories / total expenditure
10. Food mealType mapping
11. Legacy vs current final gap matrix

Quick-win candidate:
Respiratory Rate current-project validation.

---

# 17. Integration strategy

Use incremental audit integration.

Do NOT wait for the entire Legacy audit to finish.

Workflow:

1. reverse engineer / verify
2. record evidence in this document
3. port only verified or useful raw fields
4. preserve unknown native fields
5. test
6. live validate
7. commit as small independent batch
8. continue audit

Never make a large one-shot Legacy port.

---

# 18. Completed integration batches

## Batch 1A – Exertion
DONE

- parser extended
- nested exercisePlan exposed
- raw payload retained
- SQLite schema v5
- migration made defensive/idempotent
- DB readback
- daily-status support
- tests: 40/40
- live API validation
- live SQLite sync validation

Commit:
`2517ae8 Extend native Zepp exertion metrics`

## Batch 1B – PHN / Zepp Coach
NEXT

Current repo search confirms PHN support is missing.

Planned first-class domains:

`phn_record`

and

`phn_training_plan`

Native raw codes and values must remain preserved even when interpretation
confidence is incomplete.

---

# 19. Do-not-repeat list

Before researching any Zepp behavior, check this document first.

Already solved or materially investigated:

- Exertion extended native fields
- totalScore relationship
- completionPercent relationship
- ATL / CTL / TSB
- exercisePlan Daily Suggestion
- type + sport_mode activity identity
- sport_mode=5 Zepp Coach marker
- Mountain Hiking vs Hiking Coach distinction
- PHN training_plan vs record roles
- Training Days bitmask 53
- training_days [0,2,4,5]
- PHN completion ↔ workout degreeOfCompletion
- dailyPlanFinished >=100 observation
- PHN flags 31/41/71/72 and over-completion families
- disproven PHN x1/x2 ↔ sport_mode hypothesis
- disproven weekly intensity class ↔ flag-family hypothesis
- LifeLoad is subjective, not BioCharge
- activity calories vs calorieGoal
- Food native nutrition availability


---

## Batch 1B production closeout — 2026-07-27

Status: DONE / LIVE VALIDATED

First-class PHN / Zepp Coach support is now implemented in the current
`zepp-health-cli`.

Production-proven contracts:

- `phn/record` is readable as native historical GET data.
- `phn/training_plan` is also readable by GET.
- `training_plan.event.timestamp` is the stable native plan identity.
- daily `phn/record.phn_plan_id` matches that plan identity.
- mutable current-plan freshness comes from `last_update_time`, not the
  historical event timestamp.

SQLite schema v6 persists:

- `phn_daily_records`
- `phn_training_plans`

Current normalized PHN record fields include:

- `phn_plan_id`
- `flag`
- `degree_of_completion`
- `degree_of_completion_week`

Current normalized training-plan state includes the known native Coach
configuration and recommendation fields while preserving the raw payload.

### Superseded flag interpretation

The earlier theory that flags 51 / 61 / 62 are simple ordered completion
percentage buckets is disproven by the broader native GET history.

Production history contains:

- flag 51 with `degree_of_completion = 0`
- flag 51 with very large completion values
- flag 62 with completion values close to 400%

Therefore exact meanings of 51 / 61 / 62 remain unresolved and raw native
codes are authoritative.

Do not derive their semantics from completion percentage alone.

### Persistence lesson

A native raw payload being unchanged does NOT guarantee that the normalized
stored representation is unchanged.

Parser or normalizer improvements may derive new/corrected canonical fields
from identical historical native evidence.

The generic domain UPSERT therefore compares both:

1. sanitized `source_json`
2. all normalized persisted columns

before returning `unchanged`.

This allows historical raw evidence to be safely re-normalized without
requiring deletion or redownload.


---

## Charge / HybridCharge production closeout — 2026-07-27

Status: CORE SEMANTICS SOLVED

Broad Charge / BioCharge investigation is closed.

Production UI + native API validation established:

- `Charge/real_data.total` = Zepp HybridCharge score
- `physical` = Physical Charge component
- `mental` = Mental Charge component
- `total=255` = invalid/unavailable sentinel
- `wakeCharge` = UI Wake HybridCharge
- `maxCharge` = native daily maximum HybridCharge
- summary cumulative fields represent native accumulated charging/depletion
  metrics and must not be reconstructed from integer real_data deltas

30-day validation:

- 42,512 valid real_data samples
- 42,497 matched the rounded physical/mental mean
- 99.9647% agreement

Native total remains authoritative.

The simple formula:

`wakeCharge = round(bioChargeWake)`

was disproved over 30 days.

The simple formula:

`cumulativeChargingEnergy = maxCharge - minCharge`

was also disproved.

### Time-semantics correction

`Charge/real_data` is UTC-day bucketed.

Example:

2026-07-22 00:00 UTC
=
2026-07-22 02:00 Europe/Ljubljana

The 02:00 local boundary is NOT physiological.

`Charge/summary` and `Charge/wake_data` instead use local-day semantics via
their `value.startTime`.

For those domains, do not assign the local day from `event.timestamp`.

### 22-Jul fixture

UI / native exact matches:

Cross-training:
45 -> 43
= UI -2

Nap:
35 -> 39
= UI +4

Wake:
wakeCharge = 74
= UI Wake HybridCharge 74

This fixture is retained as production evidence for future regression work.

Remaining optional research:

- exact internal calculation behind cumulative charging/depletion
- exact mathematical role of bioChargeWake
- exact native activity-impact contract if needed later

These are NOT blockers for TRC.

