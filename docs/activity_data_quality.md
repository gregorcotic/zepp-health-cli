# Activity data quality and source trust

Status: Z001.4 design gate. This document defines factual quality semantics; it
does not implement correction algorithms or a coach contract.

## Non-negotiable principles

1. Preserve every vendor value and its exact source field.
2. Validation is additive. It never mutates the raw value.
3. A selected factual value must cite its source, confidence, quality status,
   and reason.
4. Strava is optional validation/enrichment. Its absence must not block a Zepp
   activity.
5. Quality is sport-aware. Missing outdoor metrics are not automatically
   failures for indoor activities.
6. ChatGPT consumes the quality result; it does not silently repair vendor
   data itself.

## Proposed metric envelope

Use one envelope per factual metric rather than one opaque activity-level
score:

```json
{
  "raw": {
    "value": 2410,
    "unit": "m",
    "source": "zepp_summary",
    "source_field": "elevationGain"
  },
  "evidence": [
    {
      "value": 1315,
      "unit": "m",
      "source": "zepp_altitude_track",
      "method": "future_validated_ascent_algorithm"
    }
  ],
  "selected": {
    "value": null,
    "unit": "m",
    "source": null
  },
  "quality_status": "unvalidated",
  "confidence": "unknown",
  "flags": [],
  "reason": "track_algorithm_not_calibrated"
}
```

The numbers above illustrate structure only and are not Ojstrica facts. Until
the track algorithm and production evidence are validated, `selected.value`
must not claim a corrected ascent. Consumers can still inspect `raw`.

An activity-level quality object should aggregate facts without hiding them:

```json
{
  "overall_status": "unvalidated",
  "flags": ["elevation_validation_pending"],
  "metrics_checked": 6,
  "metrics_inconsistent": 0,
  "metrics_unvalidated": 2
}
```

`overall_status` is a compact summary, never a replacement for per-metric
evidence.

## Status and confidence vocabulary

Quality status:

- `ok`: available evidence is internally consistent under a proven check.
- `inconsistent`: two or more comparable facts conflict.
- `suspect`: a factual plausibility check fails, but no trusted replacement is
  available.
- `missing`: expected source field is absent for this record and sport context.
- `unsupported`: the source/contract does not provide the metric.
- `unvalidated`: a raw value exists but no calibrated validation has run.

Confidence:

- `high`: direct native fact confirmed by independent same-concept evidence or
  a calibrated native-track validator.
- `medium`: coherent native fact with partial validation.
- `low`: present but affected by known ambiguity or anomaly.
- `unknown`: semantics, units, or validation are not established.

Confidence describes factual reliability, not athlete readiness. Do not assign
percentages before calibration.

## Source preference draft

| Metric | Native source candidate | Optional validation | Current default |
|---|---|---|---|
| identity | Zepp `trackid` | repeat capture / Strava match | Zepp, medium |
| sport | Zepp `type` and `sport_mode` | Zepp app / Strava | fixture-specific |
| start/timezone | Zepp summary | app / Strava | Zepp when coherent |
| duration | Zepp summary | start/end / Strava | Zepp |
| distance | Zepp summary | Zepp GPS track / Strava | unvalidated outdoors |
| HR summary | Zepp summary | Zepp workout HR stream / Strava | Zepp |
| calories | Zepp summary | Zepp app | Zepp |
| training load | Zepp summary | Zepp app | Zepp |
| TE/RPE/exertion | Zepp summary/sub-data | Zepp app | pending values |
| GPS | Zepp native track | Strava route | pending track evidence |
| elevation | Zepp summary fields | calibrated Zepp altitude track / Strava | pending |
| title/notes | Zepp summary/sub-data | Strava enrichment | unresolved |

Strava disagreement creates evidence and possibly a flag; it does not make
Strava automatically correct.

## Generic factual checks

### Time

- `end >= start`.
- elapsed duration derived from start/end must not be shorter than a claimed
  exercise/moving duration after units are proven.
- `totalTimeWithMillis` should be compared to `run_time` only after confirming
  the former is milliseconds and the latter seconds for more than one fixture.
- pause time must be nonnegative if the field semantics are proven, and
  exercise plus pause should be checked against elapsed time with a documented
  rounding allowance.

The Cross-training fixture supports the likely relationship
`2623680 ms ≈ 2623 s`, but one record is insufficient for a universal mapping.

### Heart rate

- For present, nonsentinel values: `min_hr <= avg_hr <= max_hr`.
- Values must be positive before ordering is evaluated.
- Compare summary min/average/max with a workout-specific HR stream when
  available.
- Do not attach medical meaning or universal athlete-specific range limits.

### Distance and speed

- Compare summary distance with a future track-derived distance using a
  calibrated relative-and-absolute tolerance.
- Compare distance/duration with native speed or pace only after units are
  proven.
- Do not choose final tolerances from one hike.

### Elevation

Keep these fields separate until production values establish semantics:

```text
elevationGain
elevationLoss
altitude_ascend
altitude_descend
accumulated_gap
distance_ascend
min_altitude
max_altitude
avg_altitude
```

No evidence currently proves aliases, units, algorithms, or sentinel meanings.
Candidate checks, after units are established:

- compare native ascent fields with each other;
- check `min_altitude <= avg_altitude <= max_altitude`;
- compare gain/loss with start/end altitude;
- detect physically impossible spikes;
- detect repeated noise/oscillation;
- compare with a validated altitude-track calculation;
- compare with optional Strava and known route characteristics.

Do not sum raw altitude differences. A future algorithm needs smoothing,
minimum vertical-change thresholds, spike rejection, gap handling, and
documented preference for barometric altitude when sensor/source metadata
supports it. It must be calibrated on Ojstrica plus normal hikes before
producing a selected canonical ascent.

## Sport-aware expectations

### Cross-training/indoor

Missing distance, elevation, GPS, cadence, or power can be `unsupported` or
not applicable, not `missing`/bad. Validate duration, HR ordering, calories,
load, TE, RPE, Workout Balance, strength, and exertion when present.

The July 22 fixture (`trackid=1784739852`, `type=130`) is the indoor regression
reference. Its absence of a GPS track must not reduce overall quality.

### Hike/trail

Distance, duration, elevation, and HR are useful. GPS/altitude streams increase
confidence when recorded, but missing GPS does not invalidate a manually
recorded or device-limited hike. Elevation remains `unvalidated` until summary
field semantics and a track method are calibrated.

### Cycling

Check available distance, time, GPS, elevation, HR, cadence, and power.
Cadence and power are optional because sensor hardware may be absent.

### Swimming

For pool swimming, GPS is not expected. Where semantics are proven, compare
distance with pool length and lap count. For open-water swimming, GPS can
provide validation, but its absence is not a universal failure.

## Sentinel policy

The activity-schema field list and prompts mention candidate values `-1`,
`-100`, `-20000`, and `-274`, but no captured activity record in this
repository proves their meanings. Do not globally convert them to null.

Sentinel handling must be keyed by:

```text
endpoint + activity type + field + observed value + production evidence
```

Negative values can be valid for some measurements, so sign alone is
insufficient. Preserve the raw number, mark semantics `unknown`, and add a
field-specific mapping only after paired app/API evidence proves
“unavailable.” Existing readiness `255` and temperature display behavior are
different domain contracts and must not be copied into activities.

## Optional Zepp–Strava matching

Generate candidates within the same local/UTC date and a bounded start-time
window. Score:

1. normalized sport family;
2. start-time difference;
3. duration agreement;
4. distance agreement where applicable;
5. HR agreement where present.

Require a unique best candidate and retain the match evidence. Ambiguous
records remain unmatched. Never merge destructively or require a manual ID
map. A missing Strava candidate leaves the Zepp activity fully usable.

## Future coach contract

Conceptually, the bridge can later receive:

```json
{
  "source_activity_id": "zepp:...",
  "sport": {"value": "hike", "source": "zepp_type", "confidence": "medium"},
  "metrics": {
    "distance": {"raw": {}, "evidence": [], "selected": {}, "quality_status": "unvalidated", "confidence": "unknown", "flags": [], "reason": null},
    "elevation_gain": {"raw": {}, "evidence": [], "selected": {}, "quality_status": "unvalidated", "confidence": "unknown", "flags": [], "reason": null}
  },
  "quality": {"overall_status": "unvalidated", "flags": []},
  "source_raw_metrics": {}
}
```

The exact bridge schema is intentionally deferred. ChatGPT should receive the
selected fact and the reason for any source choice; it should not rediscover
quality anomalies from raw payloads on every request.

## Ojstrica production evidence plan

No exact Ojstrica Zepp date or `trackid` exists in repository evidence. Start
with the bounded read-only discovery:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-13 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --sport run --limit 50 --need-sub-data 1 --json
```

If absent, shift the same 14-day window. Identify the activity using local
start, duration, distance, HR, calories, and sport indicators—not title alone.

Then run both exact modes for the discovered date and ID:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date OJSTRICA_DATE --to-date OJSTRICA_DATE \
  --timezone Europe/Ljubljana \
  --sport run --track-id OJSTRICA_TRACKID \
  --limit 1 --need-sub-data 0 --json

python3 zepp_health.py diagnose-activities \
  --from-date OJSTRICA_DATE --to-date OJSTRICA_DATE \
  --timezone Europe/Ljubljana \
  --sport run --track-id OJSTRICA_TRACKID \
  --limit 1 --need-sub-data 1 --json
```

Private-title/note inspection, only if needed:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date OJSTRICA_DATE --to-date OJSTRICA_DATE \
  --timezone Europe/Ljubljana \
  --sport run --track-id OJSTRICA_TRACKID \
  --limit 1 --need-sub-data 1 --include-text --json
```

Record summary fields, stream counts/fields/time coverage, and every negative
candidate value without interpreting it.

For optional Strava validation, export only the already matched activity's
summary fields: source ID, sport, name, local/UTC start, elapsed/moving time,
distance, elevation gain, min/max altitude if available, average/max HR, and
calories. This repository has no Strava client or documented Strava database
schema, so it intentionally does not invent an executable Strava command.

Repeat the same one-day, one-track pair on one normal recent Hike. Quality
tolerances and elevation algorithms must be calibrated on both the anomaly and
normal fixtures, not Ojstrica alone.

## Implementation gate

`diagnose-activity-quality` is not implemented in Z001.4. Production Ojstrica
and normal-Hike values, actual stream structures, units, and field semantics
are prerequisites. Implementing it now would encode speculation as policy.

The next implementation may safely begin with unit-proven structural checks
(HR ordering and duration relationships), raw/evidence envelopes, and
sport-aware applicability. Elevation selection must remain unvalidated until
the track algorithm is separately calibrated.

## Open-source architecture

The recommended future public project centers on a Zepp-native platform:

```text
Zepp API client
→ native health and activity ingestion
→ SQLite/raw preservation
→ freshness
→ activity quality validation
→ canonical factual context
→ read-only coach API
```

Optional adapters such as a Strava validator attach after native ingestion.
Public tests use synthetic payloads only. Never publish credentials, user IDs,
private notes, personal timestamps/routes, or GPS coordinates.

C017 remains paused until production evidence establishes the activity
contract and quality layer. Repository restructuring and Garmin work remain
out of scope.
