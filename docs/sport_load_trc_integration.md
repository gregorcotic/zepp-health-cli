# S001.4 — SPORT_LOAD to TRC integration design

Status: READY FOR CONTEXT-ONLY INTEGRATION (bridge projection implemented)

Design date: 2026-07-29

## Scope

This document defines how native SPORT_LOAD facts should reach a future
Training Readiness Check (TRC) consumer. It does not change SPORT_LOAD
persistence, calculate a readiness score, implement a Zepp status classifier,
or integrate SPORT_LOAD into TRC code.

The authoritative factual and semantic evidence remains in
`docs/sport_load_training_status_audit.md`.

## Actual current architecture

There is no deterministic TRC scoring engine in `zepp-health-cli`.
The repository owns Zepp retrieval, canonical factual persistence, factual
freshness, and SQLite reads. It explicitly does not calculate readiness,
recovery, coaching recommendations, or proprietary health metrics.

The adjacent `coach-data-bridge` is also factual and read-only. Its documented
architecture is:

```text
Zepp Cloud
  -> zepp-health-cli
  -> zepp_health.db
  -> coach-data-bridge
  -> factual context package
  -> ChatGPT reasoning
```

The bridge does not calculate a TRC score. It exports facts and delegates
readiness assessment and recommendations to ChatGPT. Consequently, there are
no implemented TRC component scores, weights, numeric penalties, bonuses, or
fixed reason-generation rules to extend.

The current bridge factual package can expose:

- native readiness records;
- HRV samples summarized by day;
- wake energy;
- Exertion, including ATL, CTL, TSB, and unresolved recovery factors;
- readiness-derived sleep fields;
- Charge and insights;
- LifeLoad in extended detail;
- Strava activity history, including duration, distance, elevation, heart
  rate, power, perceived exertion, descriptions, and workout text where
  available.

Missing data remains missing, and source/domain freshness is reported
separately from measurement values. Morning completeness currently concerns
HRV, native readiness, readiness-derived sleep, and wake energy; it is a
mechanical coverage state, not a readiness calculation.

At the time of this design the bridge was the intended implementation
location. The active standalone `coach-data-bridge` now consumes the source
owned schema-v9 table read-only, projects `sport_load_context`, advertises
`zepp_sport_load` when rows are available, and keeps score effect at zero.
The bridge boundary is documented separately in its
`docs/sport_load_trc_integration.md`; this document remains the semantic and
integration design authority. A scorer must not be invented inside either
factual project.

## Proven SPORT_LOAD facts

SPORT_LOAD is a separate WatchSportStatistics daily resource with:

- `event_date`;
- `generated_time_s`;
- `updated_time_ms`;
- `current_day_training_load`;
- conservative `wtl_sum`;
- `optimal_min`;
- `optimal_max`;
- `overreaching_threshold`;
- `device_source`.

The WTL expansion, unit, proprietary formula, and exact rolling window are
unknown. The complete Zepp status algorithm is unknown.

The 2026-07-29 observation was:

```text
current_day_training_load = 0
wtl_sum                   = 432
native range              = 261..607
overreaching threshold    = 735
```

Zepp displayed `Optimal`, and `261 <= 432 <= 607`. This is
`CONSISTENT RANGE` evidence only. Same-day ATL/CTL/TSB were `89/64/-25` and
belong to Exertion/Training Form.

## Current workload inputs and duplication risk

Because no fixed TRC exists, the classifications below apply to the factual
context that a downstream readiness assessment can currently use.

| Existing input | Relationship to SPORT_LOAD | Classification | Design consequence |
|---|---|---|---|
| Exertion ATL | Both change with recent training burden, but values and algorithms differ | PARTIALLY OVERLAPPING | Never give WTL and ATL independent full readiness weights. |
| Exertion CTL | Both retain accumulated training history, but are not numerically identical | PARTIALLY OVERLAPPING | Treat as related context, not two independent recovery votes. |
| Exertion TSB | Derived Training Form (`CTL - ATL`); SPORT_LOAD is distinct | PARTIALLY OVERLAPPING | A negative TSB and high WTL may describe the same training block; avoid additive penalties. |
| Exertion daily/activity/exercise scores | May respond to the same workouts | PARTIALLY OVERLAPPING | Prefer direct recovery signals for readiness; use SPORT_LOAD as a corroborating workload envelope. |
| Per-activity `exercise_load` | `current_day_training_load` matched same-day sums on 692/764 audited dates | LIKELY DUPLICATE for same-day workload | Prefer the native SPORT_LOAD daily fact; do not add both as independent load. |
| Recent activity duration/volume/elevation | Describes performed work directly; WTL is a native aggregate with an unknown formula | PARTIALLY OVERLAPPING | Keep activity specificity, but do not turn both into uncapped generic-load penalties. |
| Hiking/CrossFit workload | Activities add sport and session context that SPORT_LOAD lacks | PARTIALLY OVERLAPPING | SPORT_LOAD cannot replace sport-specific volume, terrain, movement, or workout-text analysis. |
| HRV, resting HR, sleep, native readiness, wake energy | Direct recovery/readiness observations rather than workload accumulation | INDEPENDENT in role, with possible physiological association | These retain precedence for readiness. SPORT_LOAD must not override them. |
| Daily Stress | A native psychophysiological observation, not training workload | INDEPENDENT in role | Preserve as a separate readiness/recovery context. |
| Local muscular fatigue, soreness, pain | Not represented by SPORT_LOAD | INDEPENDENT and missing from SPORT_LOAD | Subjective/local input remains necessary. |

The exact overlap between WTL and each Exertion calculation remains unknown,
so `PARTIALLY OVERLAPPING` is safer than claiming duplication or
independence.

## Historical descriptive observations

The existing temporary historical validation database contains 961 unique
SPORT_LOAD dates from 2023-11-01 through 2026-07-29. Applying only direct
comparisons with each row's native thresholds gives:

| Derived native-range position | Days | Share |
|---|---:|---:|
| Below native optimal range | 178 | 18.5% |
| Within native optimal range | 535 | 55.7% |
| Above native optimal maximum but below overreaching | 115 | 12.0% |
| At or above native overreaching threshold | 133 | 13.8% |
| Unavailable because a required value was missing | 0 | 0.0% |

Across this period, `wtl_sum` ranged from 0 to 2398 and averaged 845.34.
`current_day_training_load` ranged from 0 to 998 and averaged 116.63.
These values have no claimed unit and are not calibration targets.

### Seven-day cross-domain overlap

The available temporary recent database contains only seven overlapping
SPORT_LOAD and Exertion dates, so it is insufficient for score fitting or
causal conclusions.

One useful transition is factual:

```text
2026-07-24:
  current_day_training_load = 0
  wtl_sum = 31
  ATL / CTL / TSB = 14 / 31 / 17

2026-07-25:
  current_day_training_load = 421
  wtl_sum = 452
  ATL / CTL / TSB = 146 / 98 / -48

2026-07-26:
  current_day_training_load = 0
  wtl_sum = 452
  ATL / CTL / TSB = 122 / 88 / -34
```

In this small, serially correlated sample, Pearson correlations between WTL
and ATL/CTL/TSB were respectively `0.946`, `0.921`, and `-0.964`. These are
descriptive duplication warnings, not evidence of identity or stable
population relationships.

For the same seven dates, correlations of WTL with daily mean HRV and native
sleep HRV were `0.151` and `0.063`; with native average Stress the six-date
correlation was `0.257`. Readiness physical and mental scores were the
unresolved constant sentinel-like value `255`, so they could not be compared.
The sample is too small and semantically incomplete to justify any readiness
coefficient. It supports treating WTL as workload context, not as a direct
recovery measurement.

## Evaluation of integration roles

### Option A — direct readiness score input

Not recommended.

There is no current score to modify, no calibrated relationship to recovery,
and substantial overlap risk with ATL/CTL/TSB and activity load. Below-range
WTL is not necessarily poor readiness, within-range WTL is not evidence of
good recovery, and a direct weight would falsely imply known units and
directionality.

### Option B — workload context or modifier

Useful later, but only as a bounded interpretation layer. SPORT_LOAD can
describe the accumulated native workload envelope around direct recovery
signals. It must not override HRV, sleep, resting HR, Stress, native
readiness, pain, or local fatigue.

### Option C — explanation and guardrail

Recommended initial role.

Expose factual values, freshness, and a clearly derived native-range
position. Use them for explanation and cautious workload guardrails while
leaving the numeric readiness score unchanged.

## Safe derived range position

A consumer may derive a mechanical range position if all four native numeric
values are available and threshold ordering is coherent:

```text
if wtl_sum >= overreaching_threshold:
    at_or_above_native_overreaching_threshold
else if wtl_sum > optimal_max:
    above_native_optimal_range
else if optimal_min <= wtl_sum <= optimal_max:
    within_native_optimal_range
else:
    below_native_optimal_range
```

If required values are missing, or if native thresholds are not ordered
coherently, the result is `unavailable`. This field is a local arithmetic
diagnostic, not Zepp's authoritative Training Status.

No readiness meaning follows automatically:

- below range: no readiness penalty;
- within range: no readiness bonus;
- above range: factual caution only;
- at/above overreaching: prominent workload guardrail, but initially no
  numeric score penalty.

## Recommended TRC behavior

### Score effect

Initial maximum numeric score impact: **0 points**.

SPORT_LOAD should not change a TRC score until a real scorer, scale, precedence
rules, and calibrated non-duplicative behavior exist. Direct recovery inputs
remain authoritative for readiness.

If a later deterministic TRC scorer is introduced, an overreaching effect
must be:

- independently calibrated against outcomes;
- capped and small relative to direct recovery signals;
- mutually exclusive with an equivalent ATL/TSB or aggregate-load penalty;
- absent for stale or missing SPORT_LOAD;
- documented as a project guardrail rather than Zepp's score.

No numeric cap other than zero is justified by current evidence.

### Overreaching guardrail

When current WTL is at or above the native overreaching threshold:

- surface a high-priority workload-context caution;
- do not state that the athlete is unrecovered;
- do not automatically replace direct recovery assessment;
- ensure only one accumulated-workload guardrail is applied if Exertion or
  other workload logic already expresses the same concern;
- invite examination of sleep, HRV, resting HR, Stress, soreness/pain, and
  planned session demands.

### Current-day training load

`current_day_training_load=0` before training is neutral and must never be
penalized.

For a current row after activity:

- expose it as intraday workload context;
- use the native value rather than recomputing activity loads;
- report that same-day work is already recorded;
- do not invent a "substantial" cutoff while the unit and scale are unknown;
- combine it with actual activity timing/type and subjective local fatigue
  before cautioning against another hard session.

If the TRC question explicitly concerns morning pre-training readiness,
current-day load is normally explanation-only. If the question is an
intraday re-check, it becomes a guardrail input but not an independent
recovery score.

### Freshness and missing data

| SPORT_LOAD state | Consumer behavior |
|---|---|
| `current` | Expose values and derived range position as current workload context. |
| `stale` | Preserve the dated facts but set `usable_for_current_trc=false`; do not apply a current guardrail or score effect. |
| `missing` | Emit a missing/unavailable context object; do not fail or reduce TRC. |

Sync freshness and SPORT_LOAD event-date freshness remain separate. A current
dated row is not stale merely because its `updated_time_ms` has not changed
recently.

## Explanation contract

Acceptable:

- "Recent SPORT_LOAD is within its native threshold range (432; native range
  261–607)."
- "SPORT_LOAD is above its native optimal range."
- "SPORT_LOAD has reached or exceeded its native overreaching threshold."
- "SPORT_LOAD is stale (latest record 2026-07-28), so it was not used as
  current workload context."
- "Current-day SPORT_LOAD is 0; no same-day load has been recorded by this
  source."

Avoid:

- "You are optimally recovered."
- "SPORT_LOAD proves you are ready."
- "WTL is ATL/CTL."
- "Zepp says you are overtrained."
- any claim of a known WTL unit, formula, or exact window.

## Local fatigue and event-specific limitations

SPORT_LOAD cannot identify sore calves, damaged quads, adductor pain,
shoulder fatigue, local CrossFit muscular fatigue, injury, movement-specific
limitations, or pain. Subjective/local body-region input must remain separate
and may override a favorable workload context.

SPORT_LOAD is general-purpose and must not become Triglav-specific. It can
later contextualize accumulated load, taper, overload, and recent workload
progression, but it cannot replace hiking duration, elevation, terrain,
technical exposure, pack load, or mountain-specific preparation.

## Consumer data contract

The bridge exposes this optional additive object in the factual context,
without exposing `source_json`:

```json
{
  "sport_load_context": {
    "event_date": "2026-07-29",
    "freshness": "current",
    "usable_for_current_trc": true,
    "generated_time_s": 1785283200,
    "updated_time_ms": 1785276000000,
    "current_day_training_load": 0,
    "wtl_sum": 432,
    "optimal_min": 261,
    "optimal_max": 607,
    "overreaching_threshold": 735,
    "device_source": 9568513,
    "range_position": "within_native_optimal_range",
    "range_position_derivation": {
      "derived_from_native_thresholds": true,
      "authoritative_zepp_status": false
    },
    "score_effect": {
      "applied": false,
      "points": 0,
      "reason": "context_only"
    }
  }
}
```

For historical daily context, the same factual fields and derived range
position may appear under that day's Zepp record. For a current TRC view,
the latest object must also carry explicit freshness and usability.

The contract is additive and optional so older context packages remain
readable. `zepp_sport_load` reports whether rows are available to the bridge.

## Exact implementation location

1. `zepp-health-cli`: no scoring change. It remains owner of schema v9,
   canonical SPORT_LOAD facts, date freshness, and SQLite reads.
2. `coach-data-bridge` (active standalone bridge): schema-v9 compatibility,
   safe `sport_load_records` reading, canonical projection without
   `source_json`, `zepp_sport_load`, coverage/freshness metadata, and the
   explicitly labelled range position are implemented as a context-only
   factual boundary. The archived monorepo copy is not the active target.
3. ChatGPT/TRC reasoning policy:
   - consume SPORT_LOAD as context/guardrail;
   - apply zero numeric score impact initially;
   - enforce workload-penalty precedence so SPORT_LOAD and ATL/TSB/activity
     load are not counted independently;
   - keep direct recovery and local-fatigue inputs authoritative.

If a future deterministic TRC scorer is created, it belongs in a separate
analysis/coach layer, not in either factual data project.

## Readiness decision

**READY FOR CONTEXT-ONLY INTEGRATION**

The factual source and safe range-position diagnostic are ready for export.
Direct score modification is not justified because no current scorer or
weights exist, the WTL semantics remain deliberately conservative, and the
available overlap strongly warns against double-counting Exertion and
activity load.
