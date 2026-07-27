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
