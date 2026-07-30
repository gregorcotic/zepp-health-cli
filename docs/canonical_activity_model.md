# Canonical Zepp activity model

Status: Z001.10 normalization foundation, persisted relationally by Z001.11.
It is not yet a coach contract.

## Layering

The model deliberately preserves five boundaries:

```text
Zepp raw history/detail
  -> normalized values and independently sampled streams
  -> sport-specific meaning
  -> factual quality flags
  -> future coaching facts
```

History remains authoritative for established summary facts. Native detail
enriches it with streams, laps, notes, and Coach structures. A detail value
never silently replaces a history summary. Derived elevation has a separate,
currently empty slot.

## Top-level structure

`canonicalize_activity(history_record, detail_response, timezone_name=...)`
returns:

```text
schema_version
identity
time
sport_capabilities
summary
streams
laps
strength
notes
coach
quality
provenance
```

`track_id` remains Zepp's native identifier. Identity retains native
`type`, exact `sport_mode`, mapped sport/family, Coach-mode flag, mapping
confidence, and the private history `source` needed for traceability.

Time contains local start/end representations, duration, timezone, and local
activity date. The start conversion from Unix-like `trackid` is explicitly
`INFERRED`; duration uses the established history precedence and records its
source field. Stream offsets are independent. Different GPS, HR, altitude,
and cadence counts are never aligned by array index and no global 1 Hz rate is
assumed.

## Metric statuses

Canonical facts use:

| Status | Meaning |
|---|---|
| `AVAILABLE` | Valid factual data exists. |
| `SUPPORTED_BUT_NOT_RECORDED` | This sport/API can carry the optional sensor metric, but this activity did not record it. |
| `NOT_APPLICABLE` | The metric has no meaningful application to this sport. |
| `UNSUPPORTED` | Credible evidence proves the source does not support it. |
| `SENTINEL_UNAVAILABLE` | A proven unavailable sentinel is present. |
| `INVALID` | Data exists but parsing or factual validation fails. |
| `UNKNOWN` | Evidence is insufficient. |

Capabilities and observations remain separate. Cycling power is
`SUPPORTED_OPTIONAL_SENSOR`; absent `power_meter` on the production Gravel
fixture becomes `SUPPORTED_BUT_NOT_RECORDED`, not an error. Hiking power and
Pool Swim GPS are `NOT_APPLICABLE`.

## Streams

- GPS: compact delta coordinate pairs are accumulated and divided by `1e8`.
  Internal samples retain coordinates and offsets for future route analysis.
- Altitude: raw values are retained and normalized with production-supported
  `/100` metre scaling.
- HR: delta-time/delta-value records produce independent offset/BPM samples.
- Cadence and power: raw structural records and provenance are retained where
  semantics/units are not yet sufficiently proven.
- Speed and pace: structural evidence is retained with `UNKNOWN` semantics;
  units are not guessed.

Open Water Swim's production sentinel `detail.altitude=-2000000` is checked
before scaling. A stream containing only that value becomes
`SENTINEL_UNAVAILABLE`; it cannot produce `-20000 m`, altitude aggregates, or
elevation load. Field-specific sentinels live in one registry and arbitrary
negative values are not globally discarded.

## Sport structures

- Hiking retains reported ascent/descent and native altitude separately. The
  future derived-ascent slot remains empty for Ojstrica validation.
- Alpine Ski is identified only by `(type, sport_mode)=(105,0)`. It retains
  `altitude_descend` as `elevation_loss_m` and `ski_vertical_m`, sets
  `elevation_gain_m` to native `altitude_ascend` (zero in the audited
  fixtures), and never maps lift gain to athlete-powered climbing load.
- Pool Swim preserves `lap`, `pool_swim_pace`, `pool_stroke_speed`, and
  `currentDistance` records structurally without guessing component meanings.
- Open Water Swim supports GPS, HR, speed, pace, and stroke evidence while
  rejecting sentinel altitude.
- Cross-training supports native HR and `memo` Workout Notes. Potential
  `strengthAssess`, `strengthSets`, and HYROX fields are
  `SCHEMA_DISCOVERED`, not production-populated.
- Exact mode 5 remains available for future Coach plans, targets, and
  segments.

## Notes and privacy

The internal model may retain `notes.text` because future intentional coach
ingestion may need it. `safe_canonical_activity()` always removes:

- GPS coordinates and all sample values;
- Workout Notes text;
- the private history `source`;
- credentials, user/device identifiers, URLs, and raw payloads.

It exposes only safe counts, statuses, ranges for altitude/HR, note presence
and length, field-name provenance, and quality flags.

## Merge and quality rules

History/detail `trackid` mismatch produces
`HISTORY_DETAIL_TRACK_ID_MISMATCH`, and the foreign detail is not merged.
Other factual flags include:

```text
GPS_STREAM_AVAILABLE
GPS_STREAM_MISSING
ALTITUDE_SENTINEL
POWER_SENSOR_NOT_RECORDED
WORKOUT_NOTES_AVAILABLE
DETAIL_WRAPPER_UNRECOGNIZED
DETAIL_TRACK_ID_MISSING
```

Optional-sensor absence is a factual flag, not a quality penalty. Missing GPS
does not warn for Pool Swim or Cross-training. Current checks are deliberately
limited to wrapper/identity, numeric parsing, known sentinels, coordinate
plausibility, and monotonic non-negative offsets.

## CLI

```bash
python3 zepp_health.py diagnose-canonical-activity \
  --from-date YYYY-MM-DD --to-date YYYY-MM-DD \
  --timezone Europe/Ljubljana \
  --track-id TRACKID --json
```

The command makes one bounded history lookup and one exact detail request. It
prints only `safe_canonical_activity()`.

## Relational storage contract

Do not store one huge duplicated JSON document as the only representation.
Z001.11 implements the proposed separation:

```text
activities
activity_summary_metrics
activity_streams
activity_samples
activity_laps
activity_notes
activity_quality_flags
activity_provenance
raw_payload references
```

Samples retain their stream-local offset/timestamp and provenance. Notes use
separate privacy/access handling. Raw payloads should be retained once and
referenced, not copied into each metric. This structure supports incremental
upsert by `track_id` and later optional validation evidence without requiring
Strava.

See `docs/activity_storage.md` for schema, transactions, incremental refresh,
growth estimates, and production migration procedure.

The model intentionally leaves future body-region fatigue and elevation
algorithms outside normalization. They can later consume notes, structured
strength fields, swim volume, hiking ascent/descent, cycling cadence/duration,
and sport family without being baked into the raw parser.
