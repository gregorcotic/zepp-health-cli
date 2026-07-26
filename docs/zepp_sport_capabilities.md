# Zepp native sport capabilities

Status here means production evidence, not field-name availability. A raw
track is not required for basic coach ingestion when the native summary is
sufficient; it is primarily an advanced validation and route-analysis input.

## Current basic-ingestion assessment

| Sport | Found | Type | Assessment | Basis / blocker |
|---|---:|---:|---|---|
| Hike | yes | 22 | READY_FOR_BASIC_INGESTION | distance, time, pause, HR, calories, load, TE, RPE, elevation summary |
| Cross-training | yes | 130 | READY_FOR_BASIC_INGESTION | time, HR, calories, load; strength detail/notes unresolved |
| Strength | no distinct fixture | — | INSUFFICIENT_EVIDENCE | distinct type and structured content unknown |
| Ride | no | — | NOT_FOUND | production inventory required |
| Gravel | no | — | NOT_FOUND | type/sport_mode distinction unknown |
| MTB | no | — | NOT_FOUND | type/sport_mode distinction unknown |
| Pool Swim | no | — | NOT_FOUND | pool/lap/stroke population untested |
| Open Water Swim | no | — | NOT_FOUND | swim metrics and GPS population untested |
| Run | no matched fixture | — | INSUFFICIENT_EVIDENCE | route name is not a sport mapping |
| Trail Run | no | — | NOT_FOUND | type/sport_mode distinction unknown |
| Alpine Ski | no | — | NOT_FOUND | downhill/elevation fields untested |
| Walk | no | — | NOT_FOUND | structural distinction from Hike untested |

## Capability matrix

`YES` means populated production evidence. `PARTIAL` means useful summary
coverage with an unresolved dimension. `UNKNOWN` means no suitable fixture.

| Sport | Distance | Time/pause | HR | Load/TE/RPE | GPS | Elevation | Cadence/power | Swim detail | Strength detail | Title/notes |
|---|---|---|---|---|---|---|---|---|---|---|
| Hike | YES | YES | YES | YES | UNKNOWN | YES summary | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN |
| Cross-training | NOT_APPLICABLE | YES | YES | PARTIAL | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN | UNKNOWN |
| Strength | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN | UNKNOWN |
| Ride/Gravel/MTB | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN |
| Pool Swim | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN | NOT_APPLICABLE | UNKNOWN |
| Open Water Swim | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN | NOT_APPLICABLE | UNKNOWN |
| Run/Trail Run | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN |
| Alpine Ski | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN |
| Walk | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_APPLICABLE | NOT_APPLICABLE | UNKNOWN |

## Field-state and unit rules

The coverage diagnostic distinguishes populated, empty, absent, and
unknown-semantics fields. Candidate negatives `-1`, `-100`, `-20000`, and
`-274` are not converted to missing without fixture evidence.

Production evidence currently supports seconds for `run_time`, milliseconds
for `totalTimeWithMillis`/`exerciseTimeWithMillis`/`pauseTimeWithMillis`,
metres for Ojstrica `highPrecisionDistance`, bpm for matched HR fields, and
kcal for matched Cross-training `calorie`. Ojstrica strongly supports `/100`
scaling for its paired centi-metre elevation/altitude fields, but this is not
yet declared universal. Other units remain field- or sport-specific.

## Optional Strava value

Strava remains useful for custom titles/descriptions, exported route access,
historical cross-checks, and secondary distance/elevation comparison. These
are optional-enrichment candidates, not automatic ground truth. Production
breadth must be proven for the remaining sports before Zepp can be declared
primary across roughly 95% of the user's activities.

The first current-year inventory returned 14 type/mode groups. Capability
classification remains unchanged until the representative rows are manually
matched to their Zepp app sport labels; field patterns alone are not mapping
evidence.
