# Zepp sport type catalog

This is an evidence catalog, not a complete Zepp enum. The URL segment and the
activity's numeric `type` are separate concepts. In particular,
`/v1/sport/run/history.json` returned non-running production activities.

## Evidence grades

- `PROVEN_FOR_FIXTURE`: app identity and several payload metrics match a known
  production workout.
- `OBSERVED`: an ID occurred in sanitized history but its app sport was not
  independently matched.
- `LIKELY`: external evidence suggests a mapping, but this project has not
  production-matched it.
- `UNKNOWN`: no defensible mapping.

## Current catalog

| Type | Sport family | Evidence | Sport mode |
|---:|---|---|---|
| 22 | Hike | PROVEN_FOR_FIXTURE — 2026-07-25 Ojstrica | semantics unproven |
| 130 | Cross-training | PROVEN_FOR_FIXTURE — 2026-07-22 | semantics unproven |

No distinct Strength, Ride, Gravel, MTB, Pool Swim, Open Water Swim, Run,
Trail Run, Alpine Ski, or Walk type has yet been supplied as production
evidence. That means “not yet observed in this audit,” not “unsupported by
Zepp.”

## Route and privacy status

`GET /v1/sport/run/history.json` is proven broader than literal runs because it
returned types 22 and 130. It is not yet proven to be a complete all-workout
endpoint. `data.next` exists, but safe continuation semantics are unresolved.

New mappings require a sanitized response plus a matching Zepp app workout.
Never commit personal payloads, coordinates, device/user identifiers, or
workout notes.

The 2026 bounded inventory has 135 records in 14 `(type, sport_mode)` groups.
Their unknown IDs are intentionally not cataloged as sports until app matching
is returned. `diagnose-sport-coverage --mapping-list` supplies one safe
representative end time and summary per group for that process.
