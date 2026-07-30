# S002 — Sport-specific elevation semantics audit

Date: 2026-07-30
Scope: investigation only; no production behavior, canonical model, scoring,
TRC, coach-data-bridge, or activity projection changes.

## Conclusion

The 2023-12-16 activity represented in historical output as “Kronplatz day 3”
must not be reported as approximately 9,973 m of ascent.

The exact native Zepp activity is:

```text
date              2023-12-16
trackid           1702711638
type/sport_mode   105/0
mapped sport      Ski
altitude_ascend   0 m
altitude_descend  9852 m
downhill_num      14
climb_dis_descend 40955 m
```

Zepp's skiing summary semantics classify `altitude_descend` as total ski
vertical descent. Under the requested classification this is **E — Total ski
vertical** (measured as cumulative descent), not true athlete-powered ascent,
combined ascent/descent, or lift vertical.

The repository's newer 2026 app-verified fixture independently establishes
the same field meaning: Zepp API `altitude_descend=5921` corresponded to the
Zepp app's approximately 5913 m vertical display. The API/app difference was
retained rather than silently corrected.

## Evidence acquisition

The following bounded, read-only calls were made against the configured Zepp
account:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date 2023-12-16 --to-date 2023-12-16 \
  --timezone Europe/Ljubljana --sport run \
  --limit 20 --need-sub-data 1 --include-text --json

python3 zepp_health.py diagnose-activity-detail \
  --from-date 2023-12-16 --to-date 2023-12-16 \
  --timezone Europe/Ljubljana --track-id 1702711638 --json
```

The complete native history and detail responses were inspected in memory.
They were not retained in the repository because they contain private source,
device, and route data. The diagnostic output is the durable, sanitized JSON
reference: it records every field name, exact relevant scalar values, stream
counts/ranges, and suppresses coordinates and identifiers.

No supplied or locally available screenshot showed the target 2023 activity
inside the Zepp UI. Therefore the target's exact current UI number cannot be
claimed. The field-to-UI mapping is supported by the separate manually
matched 2026 Ski fixture documented in `docs/zepp_sport_types.md`.

## Complete elevation-related native fields

### Kronplatz day 3 — 2023-12-16

History payload (`trackid=1702711638`):

| Field | Raw value | Interpretation |
|---|---:|---|
| `altitude_ascend` | `0` | no skiing ascent metric |
| `altitude_descend` | `9852` | cumulative ski vertical descent, metres |
| `max_altitude` | `2070` | summary maximum altitude, metres |
| `min_altitude` | `673` | summary minimum altitude, metres |
| `avg_altitude` | `1599.0` | summary average altitude, metres |
| `distance_ascend` | `0` | no ascent distance |
| `climb_dis_descend` | `40955` | downhill/ski distance, metres |
| `climb_dis_ascend_time` | `-1` | unavailable sentinel |
| `climb_dis_descend_time` | `-1` | unavailable sentinel |
| `downhill_num` | `14` | downhill run count |
| `downhill_max_altitude_desend` | `1359` | largest recorded downhill altitude loss, metres |
| `maximumClimbingDistance` | `0` | not populated for climbing |
| `cumulativeClimbingAscent` | `0` | no climbing ascent |
| `maximumClimbingAscent` | `0` | no climbing ascent |
| `floor_number` | `-1` | unavailable/not applicable |
| `upstairs_height` | `-1.0` | unavailable/not applicable |
| `min_upstairs_floors` | `-1.0` | unavailable/not applicable |
| `downstairs_floors` | `-1` | unavailable/not applicable |
| `downstairs_height` | `-1.0` | unavailable/not applicable |

The older 2023 schema does not contain `elevationGain`, `elevationLoss`,
`highestAltitude`, `lowestAltitude`, `averageAltitude`, or
`totalClimbDistance`; absence is distinct from zero.

Detail payload:

| Field | Raw state |
|---|---|
| `altitude` | populated encoded string, length 119,511; 17,607 samples |
| `time_delta_altitude` | populated encoded string, length 185,092 |
| `air_pressure_altitude` | empty |
| `correct_altitude` | empty |
| `DEMAltitude` | empty |
| `descendSpeed` | empty |
| `strydVerticalRatio` | empty |
| `verticalOscillationBalance` | empty |

The production-supported `/100` scaling gives an altitude range of
673.46–2069.77 m, agreeing with the rounded history minimum/maximum. A raw,
unfiltered adjacent-sample calculation gives 10,398.40 m positive change and
10,414.95 m negative change. These noisy stream sums are not the authoritative
summary and, importantly, show that the full track contains lift-assisted
upward travel. Zepp nevertheless reports `altitude_ascend=0` and the skiing
metric in `altitude_descend`.

There are no raw fields named `ascent`, `descent`, `elevation_gain`,
`elevation_loss`, `total_climb`, `vertical`, `ski_vertical`, `uphill`, or
`lift` in this record beyond the exact fields listed above.

## Additional alpine ski days

All three activities are native `type=105`, `sport_mode=0` and show the same
semantic pattern.

| Date | Track ID | Descent distance | `altitude_ascend` | `altitude_descend` | Runs | Altitude range | Raw stream + / - |
|---|---|---:|---:|---:|---:|---:|---:|
| 2023-12-14 | 1702541656 | 37,075 m | 0 m | 8,058 m | 14 | 765.96–2283.14 m | +6,898.29 / -8,405.87 m |
| 2023-12-15 | 1702626315 | 24,889 m | 0 m | 6,183 m | 15 | 759.07–2148.62 m | +6,515.56 / -6,590.58 m |
| 2023-12-16 | 1702711638 | 40,955 m | 0 m | 9,852 m | 14 | 673.46–2069.77 m | +10,398.40 / -10,414.95 m |

The consistency is decisive: skiing stores lift-inclusive altitude samples,
but its summary sets ascent to zero and publishes ski vertical through
`altitude_descend`.

## Hiking comparison — Ojstrica

Ojstrica (`trackid=1784948221`) is native `type=22`, `sport_mode=0`, mapped
to Hiking.

| Field | Raw value |
|---|---:|
| `altitude_ascend` | `1915` |
| `altitude_descend` | `1880` |
| `elevationGain` | `191544` (= 1915.44 m) |
| `elevationLoss` | `188021` (= 1880.21 m) |
| `highestAltitude` | `232929` (= 2329.29 m) |
| `lowestAltitude` | `78681` (= 786.81 m) |
| `averageAltitude` | `165195` (= 1651.95 m) |
| `totalClimbDistance` | `709142` (= 7091.42 m) |
| `distance_ascend` | `7091` |
| `climb_dis_descend` | `7203` |
| `climb_dis_ascend_time` | `17138` s |
| `climb_dis_descend_time` | `19123` s |

Its altitude stream has 36,494 samples and range 786.81–2329.29 m. Raw
adjacent-sample totals are +1913.49/-1878.20 m, closely matching the native
summary. Thus the exact same `altitude_ascend` field is a real,
athlete-powered elevation gain for Hiking but is deliberately zero/not the
primary vertical metric for Ski. `altitude_descend` is cumulative descent in
both sports, but its product meaning differs: hiking elevation loss versus
the primary total ski vertical.

## Zepp UI and Strava comparison

### Zepp UI

The available app-verified Ski evidence maps:

```text
Zepp app vertical  ≈ 5913 m
native API         altitude_descend=5921 m
native API         altitude_ascend=0 m
```

For the target record, Zepp native fields support 9,852 m ski vertical,
40.955 km downhill distance, 14 downhill runs, maximum altitude 2,070 m,
minimum altitude 673 m, and maximum single downhill altitude loss 1,359 m.
The target's current app screen was not available, so no exact UI value or
lift count is asserted.

### Strava

The local `strava-sync/data/strava.db` is a zero-byte placeholder and has no
activity table. A read-only attempt to reach the documented production
database was unavailable from this environment. Consequently the Strava
record cannot be independently queried in this audit.

The supplied historical symptom (`~9,973 m ascent`) differs from Zepp's
`altitude_descend=9852` by 121 m. The bridge history implementation projects
Strava's `total_elevation_gain` directly into `elevation_gain_m`, so 9,973 m
is consistent with a Strava-imported/reprocessed elevation-gain value, but
this is an inference rather than a verified target-row comparison. Distance,
moving time, elapsed time, ascent, and descent comparisons to Strava therefore
remain unavailable.

## Historical summary impact

S002.1 implementation status: the Zepp canonical activity output now exposes
`elevation_gain_m`, `elevation_loss_m`, and `ski_vertical_m` with the
sport-specific semantics above. The separate `coach-data-bridge` historical
projection was intentionally not modified because this task explicitly
prohibits changes there.

`getCoachHistorySummary` serves the derived Strava history database.
`coach-data-bridge/history.py` aliases:

```text
Strava total_elevation_gain -> historical_activities.elevation_gain_m
```

The gateway returns that field without sport-specific reinterpretation.
Therefore a Ski/AlpineSki record is currently liable to be labelled as
elevation gain/ascent even though the value represents ski vertical after
Strava import or processing. The reported “~9,973 m ascent” label is
semantically wrong.

The required bridge-side fix remains a sport-aware projection:

1. preserve the source field and source semantics;
2. for Alpine Ski/Ski, expose the skiing value as `ski_vertical_m` (or
   `vertical_descent_m`), not `elevation_gain_m`/ascent;
3. keep `elevation_gain_m` null unless a distinct true athlete-powered ascent
   is actually recorded;
4. do not infer `lift_vertical_m` from total positive altitude change without
   an evidence-backed lift segmentation rule;
5. update aggregate/record labels so Ski cannot contribute to “highest
   elevation gain” or ascent totals.

This is a recommendation only; no bridge or gateway code was changed.

## Canonical/T005 recommendation

A sport-safe future canonical contract should distinguish:

```text
elevation_gain_m       athlete-powered/general ascent where applicable
elevation_loss_m       general cumulative descent
ski_vertical_m         total cumulative ski descent
lift_vertical_m        lift-assisted vertical only when explicitly proven
```

Retaining `vertical_descent_m` as a generic factual alias may also be useful,
but it must not erase `ski_vertical_m`'s sport meaning. T005 must consume the
corrected sport-specific semantics and must not project generic
`elevation_gain_m` as “ascent” for Ski.

## Other sports

- Snowboard, cross-country skiing, and trail running have no
  production-proven mapping/fixture in the current catalog; no semantics can
  be assigned from this evidence.
- Cycling is currently classified as athlete-powered ascent relevant, but its
  sport semantics are `INFERRED`, not proven across all cycling modes.
- Hiking `22/0` is fixture-proven as described above.
- No additional conclusion should be generalized from Ski based only on
  similar field names.

## Files and data inspected

- `zepp_health.py`
- `test_activity_diagnostic.py`
- `test_canonical_activity.py`
- `test_activity_storage.py`
- `docs/current_project_handoff.md`
- `docs/canonical_activity_model.md`
- `docs/activity_storage.md`
- `docs/activity_data_quality.md`
- `docs/zepp_activity_detail.md`
- `docs/zepp_sport_capabilities.md`
- `docs/zepp_sport_types.md`
- `docs/troubleshooting/zepp_activity_forensics.md`
- `docs/project_history.md`
- local `data/zepp_health.db` (no stored activities)
- local `../strava-sync/data/strava.db` (zero-byte placeholder)
- read-only `../coach-data-bridge/history.py`
- read-only `../coach-context-gateway/app/history/repository.py`
- bounded native Zepp history/detail responses for the four activities above

No commit was created. No push was performed.
