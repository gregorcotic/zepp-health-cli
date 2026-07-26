# Zepp activity/workout forensics

Status: Z001.2 investigation. No activity source, coach contract, database, or
production behavior changed.

## Evidence boundary

This repository proves one reverse-engineered cloud request:

```text
GET /v1/sport/{sport}/history.json
```

`ZeppClient.sport_history` sends `userid`, `startTrackId`, `stopTrackId`,
`need_sub_data`, and an empty `type`; the shared GET helper adds the opaque
request UUID `r`. Authentication uses the configured regional host and
`apptoken` header plus the existing app/device-identification headers. Never
print those headers or the configured user ID.

The current `run-history` command passes the current UTC-midnight epoch seconds
as both track bounds. It defaults the URL segment to `run`. There is no
pagination loop, response parser, normalized activity model, activity table,
UPSERT, deletion handling, or generic all-sports endpoint. The names
`startTrackId` and `stopTrackId` do not by themselves prove date-filter or
cursor semantics.

Evidence grades used below:

- **PROVEN:** directly present in current source, repository history, or an
  observed response.
- **LIKELY:** suggestive evidence exists, but no production cloud payload
  confirms the interpretation.
- **UNKNOWN:** no adequate evidence.

## Provenance and sport identifiers

The sport-history method and command originate in upstream commit `a466dfa`;
later local history did not add a workout schema. Repository and upstream
issue searches yielded no captured activity examples or additional contract.

| URL segment/type | Evidence | Status |
|---|---|---|
| `run` | client docstring and CLI default | PROVEN as an implemented segment; response unverified |
| `walking` | client docstring and README example | LIKELY endpoint candidate |
| `ride` | client docstring | LIKELY endpoint candidate |
| `swimming` | client docstring | LIKELY endpoint candidate |
| `hiking` | README 404 example only | UNKNOWN; not a mapping |
| trail run, indoor ride, mountain bike, gravel, strength, CrossFit, skiing, open-water/pool swimming | none | UNKNOWN |
| numeric sport ID/enum/subtype | no captured response | UNKNOWN |

No generic endpoint returning all sports was found in source, history,
documentation, configured upstream, or repository discussions. This means
"not found", not proof that Zepp has no such private endpoint.

## First production response: 2026-07-22

A narrow production probe established the actual success wrapper:

```text
code
message
data.next
data.summary[]
```

The `run` segment returned HTTP success and one object in `data.summary`.
The initial Z001.1 diagnostic described that object's field names but reported
zero records because `summary` was not yet a recognized record wrapper. This
was a diagnostic-parser limitation, not an empty Zepp response. The parser now
supports the observed wrapper.

The same request bounds returned HTTP errors for `walking`, `ride`,
`swimming`, `training`, `strength`, `cross_training`, `indoor`, and `fitness`.
Because the first diagnostic emitted only the exception class, those results
do not reveal whether the status was 400, 404, or another error. Current
diagnostics include the numeric HTTP status without response bodies, URLs, or
headers.

The successful field-name schema proves that the cloud summary contract has
slots for substantially more than basic running totals. Observed exact names
include:

- identity/type/time: `trackid`, `parent_trackid`, `thirdPartyWorkoutId`,
  `sport_mode`, `type`, `sport_title`, `displayType`, `source`, `start_time`,
  `end_time`, `createTime`, `updateTime`, `syncedTimezone`;
- duration/distance/elevation: `run_time`, `pause_time`, `dis`,
  `totalTimeWithMillis`, `exerciseTimeWithMillis`, `elevationGain`,
  `elevationLoss`, `altitude_ascend`, `altitude_descend`, `distance_ascend`,
  `max_altitude`, `min_altitude`, `avg_altitude`;
- heart/effort/training: `avg_heart_rate`, `max_heart_rate`,
  `min_heart_rate`, `heart_range`, `exercise_load`, `te`, `anaerobic_te`,
  `VO2_max`, `rpe`, `totalExertion`, `totalCardiacExertion`,
  `totalMuscularExertion`, `coachInsight`;
- pace/motion/environment: `avg_pace`, `max_pace`, `min_pace`,
  `avg_cadence`, `max_cadence`, `avg_stride_length`, `average_power`,
  `max_power`, `avg_slope`, `max_slope`, `avg_temperature`,
  `max_temperature`, `min_temperature`, `averageAirTemp`,
  `highestAirTemp`, `lowestAirTemp`, `weatherInfo`;
- running/hiking/downhill: `lap_distance`, `runningType`, `runningProgram`,
  `climb_dis_ascend_time`, `climb_dis_descend_time`,
  `climb_dis_descend`, `downhill_num`,
  `downhill_max_altitude_desend`, `durationOfDownhillWithMillis`;
- strength/CrossFit: `crossfitContent`, `strengthScores`,
  `strength_training_group`, `total_group`, `workoutBalance`,
  `difficultySystem`, `highestDifficulty`, `hyroxRace`;
- swimming: `swim_pool_length`, `swim_style`, `swolf`, `strokes`,
  `total_strokes`, `totalStrokes`, `avg_distance_per_stroke`,
  `avg_stroke_speed`, `max_stroke_speed`, and per-stroke-style length fields;
- other structures/candidates: `child_list`, `add_info`, `originSummary`,
  `location`, `feature`, `pb`, `marathon`, `totalInsight`, and
  `thirdPartyDataSource`.

Field presence in the schema does not prove that a value is populated for the
observed activity, establish its units, or prove that `child_list`,
`add_info`, or `location` contains laps/streams/GPS. The first output did not
extract scalar values. A rerun is required to identify the activity represented
by `sport_mode`, `type`, `trackid`, and `sport_title`, and to determine whether
the `/run/` segment is sport-specific or a practical aggregate entry point.

`data.next` is now proven to exist. Its cursor meaning and termination rules
remain UNKNOWN; the client still performs one request and does not paginate.

## Cloud capability matrix

The table describes the reverse-engineered cloud interface, not data shown on
the watch/app and not Zepp OS device APIs.

| Metric/capability | Zepp support | Source | Confidence | Strava currently provides |
|---|---|---|---|---|
| Sport-specific activity listing | PARTIAL | `/v1/sport/{sport}/history.json` | medium: request exists, payload unverified | YES |
| Generic all-sports history | UNKNOWN | no endpoint found | low | YES |
| Stable activity ID | PARTIAL | `trackid` and `parent_trackid` schema fields | low: values/repeat stability pending | YES |
| Sport type/subtype mapping | PARTIAL | four URL-segment candidates | low | YES |
| Name/custom title | PARTIAL | `sport_title` and `app_name` fields | low: values/rename pending | YES |
| Description/notes | UNKNOWN | no response fixture | low | YES |
| Start/local time/timezone | UNKNOWN | no response fixture | low | YES |
| Duration/moving/elapsed time | UNKNOWN | no response fixture | low | YES |
| Distance | UNKNOWN | no response fixture | low | YES |
| Ascent/descent/min/max altitude | PARTIAL | explicit summary field names | low: population/units pending | ascent |
| Calories | UNKNOWN | no response fixture | low | YES |
| Average/max/min HR | PARTIAL | explicit summary field names | low: values/units pending | average/max |
| Workout HR stream | UNKNOWN | no response fixture | low | YES where available |
| Speed/pace/cadence/power | PARTIAL | pace/cadence/power schema fields | low: population/units pending | PARTIAL/YES by activity |
| Training load/aerobic TE/anaerobic TE/recovery/VO2max/EPOC | PARTIAL | load/TE/VO2 fields; recovery/EPOC unproven | low | PARTIAL |
| GPS/altitude track | UNKNOWN | no response fixture | low | YES where available |
| Laps/splits/intervals | UNKNOWN | no response fixture | low | PARTIAL |
| Strength exercises/sets/reps/weight/rest/muscles/corrections | PARTIAL | CrossFit/strength container fields only | low: shapes/values pending | descriptions only in current architecture |
| Swim pool length/laps/strokes/SWOLF/intervals | PARTIAL | pool/stroke/SWOLF fields; laps/intervals unproven | low | PARTIAL |
| Full-history pagination/retention | PARTIAL | `data.next` exists; no loop or semantics | low | YES within authorized history |
| Update and deletion semantics | UNKNOWN | no persistence or repeated captures | low | source IDs and updates available |

Official Zepp OS documentation is useful only as a discovery aid. Its
device-side Workout API exposes limited history/status values, and
`getSportData` lists live-workout values such as distance, duration, calories,
speed, pace, cadence, altitude, ascent, vertical speed, and downhill metrics.
Those APIs do **not** establish that the private cloud history response exposes
the same data. Likewise, watch/app UI fields do not prove cloud availability.

Consequently:

- Hike/Trail support is UNKNOWN for distance, ascent/descent, profile, GPS, HR,
  moving time, calories, training effect, and route.
- Cycling support is UNKNOWN for speed, cadence, power, HR, elevation, GPS, and
  training effect.
- Swimming support is UNKNOWN for pool length, laps, strokes, stroke type,
  SWOLF, pace, HR, and intervals.
- Strength/CrossFit exercise detail and user notes are UNKNOWN. Current BioCharge
  and Wake BioCharge are unrelated and must never be treated as workouts.

## Safe diagnostic

Use a small range and only exact candidate segments:

```bash
cd /opt/zepp-health-cli
source .venv/bin/activate

python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-20 \
  --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --sport run \
  --sport walking \
  --sport ride \
  --sport swimming \
  --limit 20 \
  --need-sub-data 1 \
  --json
```

This is one request per supplied sport. The date bounds are inclusive local
calendar seconds. `--limit` limits the reported records only; it is not sent to
Zepp. No pagination follows. The diagnostic emits safe scalar values, complete
field names, nested field names/counts, and GPS presence/counts without
coordinate values. It omits credentials, user/device identifiers, URLs, and
text values.

Compare `need_sub_data` shapes on one known activity:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date YYYY-MM-DD --to-date YYYY-MM-DD \
  --timezone Europe/Ljubljana \
  --sport EXACT_CAPTURED_SEGMENT \
  --limit 5 --need-sub-data 0 --json

python3 zepp_health.py diagnose-activities \
  --from-date YYYY-MM-DD --to-date YYYY-MM-DD \
  --timezone Europe/Ljubljana \
  --sport EXACT_CAPTURED_SEGMENT \
  --limit 5 --need-sub-data 1 --json
```

For Hike, strength/CrossFit, cycling, and swimming, select a separate narrow
window containing one known app activity. Obtain the exact URL segment from a
proxy capture when it is not one of the repository candidates. Do not probe a
large list of guessed segments.

Title/description values are intentionally opt-in. Only for a one-day,
one-sport private comparison:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date YYYY-MM-DD --to-date YYYY-MM-DD \
  --timezone Europe/Ljubljana \
  --sport EXACT_CAPTURED_SEGMENT \
  --limit 2 --need-sub-data 1 --include-text --json
```

Keep that output private: names and notes can reveal locations or health and
training details. The diagnostic never prints GPS coordinate values, even with
`--include-text`.

An empty/error result is evidence only for that host, token, segment, window,
and observation time. Record HTTP error class separately; do not infer that an
activity type is unsupported from a guessed 404.

## Evidence needed for Z001.2

For one Hike, one strength/CrossFit workout, one ride, and one swim if
available, collect:

1. observation time and Zepp app sport/title/date;
2. both sanitized sub-data diagnostics above;
3. the exact captured request path/query with credentials and user ID removed;
4. explicit private `--include-text` result if title/notes comparison is
   authorized;
5. whether renaming one test activity changes a repeated response;
6. the matching Strava activity's source ID, sport, name, description, local
   start, elapsed/moving duration, distance, elevation, HR, calories, speed,
   power, streams, laps, and device/source metadata;
7. narrow probes around one 2023 activity, one 2020 activity, and the earliest
   known activity—one window at a time, without bulk download.

Preserve raw captures privately. A sanitized response needs exact raw field
names, types, counts, units inferred from paired app values, and representative
non-GPS scalar values. This is necessary to determine IDs, units, timestamps,
pagination, retention, and whether sub-data contains tracks or laps.

## Matching design (not implemented)

Represent one physical activity as a canonical record with independent Zepp
and Strava source representations. Prefer a stable source ID within each
source. Generate cross-source candidates by normalized sport and local/UTC
start-time tolerance, then score duration, distance, average HR, and device
metadata. Require one-to-one matching and leave ambiguous candidates
unmatched. Never use title alone; renamed and automatically generated titles
are unstable. Preserve both raw source values and matching evidence so a link
can be reviewed or undone.

Repeated captures before and after a controlled rename/correction are required
to establish update behavior. A controlled deletion can establish whether
Zepp omits or tombstones a workout, but Z001.1 does not request deletion and
must not implement deletion synchronization.

## Architecture assessment

1. **Option A — Zepp health + Strava activities.** Best supported today; retain.
2. **Option D — canonical multi-source peer activities.** Best investigation
   target if Z001.2 proves stable Zepp IDs and sufficient payloads.
3. **Option B — Zepp authoritative + Strava text enrichment.** Plausible only
   if history, update semantics, core workout values, and matching are proven.
4. **Option C — Zepp-only coach.** Unsupported while names/notes, CrossFit
   details, tracks/elevation, identifiers, pagination, and retention remain
   unknown.

Zepp does not yet have enough proven cloud capability to replace Strava. The
first production schema is promising, but populated values, units, sub-data
shapes, sport routing, pagination, retention, titles/notes, and update
stability still require evidence.

## Z001.2 Cross-training production fixture

The July 22 production record is conclusively the known Cross-training workout:

| Evidence | Zepp API | Zepp app |
|---|---:|---:|
| local start | `trackid=1784739852`, approximately 19:04 Europe/Ljubljana | approximately 19:04 |
| duration | `run_time=2623`; `totalTimeWithMillis=2623680` | 43:43 |
| calories | `calorie=259` | 259 kcal |
| average HR | `avg_heart_rate=89` | 89 bpm |
| maximum HR | `max_heart_rate=121` | 121 bpm |
| minimum HR | `min_heart_rate=71` | not supplied |
| training load | `exercise_load=4` | 4 |
| API type | `type=130` | Cross-training |
| timezone | `syncedTimezone=Europe/Ljubljana` | Europe/Ljubljana |

This proves that `/v1/sport/run/history.json` is **not restricted to literal
running activities**. One non-running record is insufficient to prove that it
is a complete generic history route. A Hike, ride, and swim returned by the
same route would materially strengthen that hypothesis.

For this fixture, `type=130` empirically identifies Cross-training. No
authoritative enum mapping was found in this repository, upstream commit
history, its issues, or the searched open-source Zepp/Huami code. Therefore
`130 → Cross-training` is a fixture-backed mapping, not a guessed global enum.

The app additionally displays RPE 5/Hard, aerobic Training Effect 0.3,
anaerobic Training Effect 0, and Workout Balance 0/100. The supplied diagnostic
did not expose the corresponding raw values, so `rpe`, `te`, and
`workoutBalance` remain pending production confirmation. The diagnostic now
places these exact fields in `coaching_fields` with native scalar types.

The following fields are also explicitly reported in `coaching_fields`:

```text
rpe
te
anaerobic_te
exercise_load
workoutBalance
strengthScores
strength_training_group
totalCardiacExertion
totalMuscularExertion
totalExertion
totalInsight
crossfitContent
coachInsight
```

Nested values in `child_list`, `add_info`, `originSummary`, `strengthScores`,
and `workoutBalance` are bounded to three sample items and two nested levels.
The report includes native numeric/boolean/null scalar values, field names,
counts, and explicitly authorized text. Coordinate values and user/device
identifiers remain suppressed.

`crossfitContent=""` and `sport_title=""` in the first extracted record do not
prove that Zepp Workout Notes are unavailable. The known note contains
`Evening Crossfit`, `DEADLIFT`, `162.5`, `PRESS`, and `65`; the next narrow
`--include-text` capture must check `crossfitContent`, `coachInsight`,
`add_info`, `originSummary`, `child_list`, and any newly exposed text fields.
No repository, upstream, issue, or public code evidence identified a separate
note endpoint or one of the searched note/remark/memo field names.

### Correct GPS semantics

`location` is treated as location metadata only. It no longer sets
`gps_present`. A GPS track is reported only when a non-location nested list
contains records with both latitude and longitude keys:

```text
gps_track_present
gps_point_count
track_field_names
```

`gps_present` remains as a compatibility alias for `gps_track_present`. An
indoor Cross-training activity with a nonempty `location` can therefore
correctly report location metadata while `gps_track_present=false`.

### Sub-data and detail endpoint status

The client sends `need_sub_data` exactly as 0 or 1. No local/upstream method,
historical commit, repository issue, or searched public Zepp/Huami source
identified a verified separate detail endpoint for `trackid=1784739852`.
Endpoint-shaped guesses were not implemented.

The existing summary contains possible embedded detail containers:
`child_list`, `add_info`, `originSummary`, `strengthScores`, and `location`.
Only a paired response comparison for the same date and track with
`need_sub_data=0` and `1` can establish what the flag adds.

### Exact read-only Cross-training probes

Run both commands against the same fixture and compare only the record whose
`trackid` is `1784739852`:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-22 --to-date 2026-07-22 \
  --timezone Europe/Ljubljana \
  --sport run --limit 20 --need-sub-data 0 --include-text --json

python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-22 --to-date 2026-07-22 \
  --timezone Europe/Ljubljana \
  --sport run --limit 20 --need-sub-data 1 --include-text --json
```

The output is read-only but contains the explicitly requested private workout
text. Keep it private. Search the single record for the five known note tokens;
do not dump unrelated records or coordinates.

### Exact read-only Hike probe template

Set the date to the local calendar day of one known recent Hike; keep the
window to that single day:

```bash
HIKE_DATE=YYYY-MM-DD
python3 zepp_health.py diagnose-activities \
  --from-date "$HIKE_DATE" --to-date "$HIKE_DATE" \
  --timezone Europe/Ljubljana \
  --sport run --limit 20 --need-sub-data 1 --include-text --json
```

The `/run/` route is intentional because it already returned Cross-training.
For the matching Hike record inspect `type`, `sport_mode`, `trackid`,
`sport_title`, times, duration, distance, ascent/descent, altitude, HR,
calories, load/effect/RPE, nested structures, and the three GPS evidence
fields. Do not infer track availability from `location`.

### Strategic comparison after the fixture

- **Potential Zepp-native advantage:** RPE, training load, aerobic/anaerobic
  effect, workout balance, cardiac/muscular exertion, and structured strength
  data. Only training load is value-matched so far; the rest remain pending.
- **Equivalent and verified:** duration, calories, average/max HR, local
  timezone, and a source activity ID candidate.
- **Current Strava advantage:** proven activity name, description/notes,
  established activity IDs, historical synchronization, update semantics, and
  outdoor streams/elevation in the existing coach architecture.
- **Unresolved:** Zepp Workout Notes, populated strength structures, GPS,
  laps/streams, generic-route coverage, pagination, retention, and update
  stability.

The recommendation remains Option A while evidence is incomplete. Option B
becomes credible if the Hike and sub-data probes succeed. Option C requires
notes or an adequate structured replacement, outdoor GPS/elevation, reliable
history/pagination, stable IDs, and update behavior.

C017 remains paused because choosing a Zepp-only or multi-source activity
architecture changes the intended monorepo/service boundaries. No repository
restructure should precede the Z001 decision. A future Garmin coach should
likewise prefer independently proven Garmin-native health and activity data
over a mandatory Strava dependency; Garmin parity is not assumed or part of
this implementation.

## Z001.3 production probes and source-trust gate

Z001.3 requires operator-provided production output. No `need_sub_data=0/1`
JSON or Ojstrica JSON was supplied with the task, so RPE, aerobic TE, Workout
Balance, strength/exertion values, Workout Notes, and outdoor stream capability
remain evidence-gated. App values are comparison targets, not substituted API
facts.

The diagnostic now accepts an exact local filter:

```text
--track-id TRACKID
```

The filter is not sent to Zepp; the API request contract is unchanged. It is
applied before text rendering, so `--include-text` cannot expose unrelated
activities returned in the same response. Output distinguishes the total API
count from `matched_record_count`.

For actual coordinate-bearing sample lists, the report now adds:

```text
gps_track_present
gps_point_count
track_field_names
track_time_coverage.timestamp_field_names
track_time_coverage.sample_count_with_timestamp
track_time_coverage.raw_start
track_time_coverage.raw_end
altitude_sample_count
workout_hr_sample_count
```

Time boundaries remain in the raw vendor representation because the unit and
timezone of an unobserved track timestamp must not be guessed. Coordinate
values remain suppressed. `location_metadata` is reported independently and
never creates track evidence.

### Cross-training comparison commands

Run exactly the same date and track in both modes:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-22 --to-date 2026-07-22 \
  --timezone Europe/Ljubljana \
  --sport run --track-id 1784739852 \
  --limit 1 --need-sub-data 0 --include-text --json

python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-22 --to-date 2026-07-22 \
  --timezone Europe/Ljubljana \
  --sport run --track-id 1784739852 \
  --limit 1 --need-sub-data 1 --include-text --json
```

Compare `field_names`, `coaching_fields`, `nested_structures`, and all stream
evidence fields. Search the single matched output for `Evening Crossfit`,
`DEADLIFT`, `162.5`, `PRESS`, and `65`. If none occur in either mode, the
observed history/sub-data path does not expose the known Workout Notes. That
does not prove that no undiscovered Zepp endpoint can expose them.

### Ojstrica discovery and exact follow-up

The exact Zepp date/track ID is not in repository evidence. Do not guess it.
Use a read-only two-week discovery window without private text:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-13 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --sport run --limit 50 --need-sub-data 1 --json
```

If Ojstrica is outside that period, shift the same 14-day window rather than
expanding into a broad history pull. Match using local start, duration,
distance, HR, calories, and type—not title alone. Then rerun exactly one day
and one discovered ID:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date OJSTRICA_DATE --to-date OJSTRICA_DATE \
  --timezone Europe/Ljubljana \
  --sport run --track-id OJSTRICA_TRACKID \
  --limit 1 --need-sub-data 1 --include-text --json
```

Capture all native elevation summary fields unchanged:
`elevationGain`, `elevationLoss`, `altitude_ascend`, `altitude_descend`,
`max_altitude`, `min_altitude`, and `avg_altitude`. A positive
`altitude_sample_count` makes later track-derived validation technically
possible; Z001.3 does not calculate or overwrite elevation.

Ojstrica is a mandatory future Z001.4 regression fixture. The validator should
compare summary elevation with a separately derived track result, attach a
quality flag when they differ materially, and preserve both raw values.

### History/pagination probe

`data.next` exists and `-1` has been observed as a terminal candidate. The
current client makes one request and does not paginate. A terminal `-1`
response cannot prove how a nonterminal cursor should be applied.

Use this small multi-record window:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-20 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --sport run --limit 50 --need-sub-data 0 --json
```

If `response_metadata.next` is not `-1`, preserve the sanitized output for a
separate cursor-characterization task. Do not feed it into `startTrackId` or
`stopTrackId` speculatively and do not implement a history loop until the
direction, inclusivity, duplication, and termination behavior are proven.

### Draft factual source-trust model

| Metric | Native source | Validation source | Initial confidence | Known quality issue |
|---|---|---|---|---|
| identity | Zepp `trackid` | repeated Zepp capture; optional Strava match | medium | long-term/update stability pending |
| sport | Zepp `type`/`sport_mode` | Zepp app; optional Strava | medium per fixture | global enum incomplete |
| duration | Zepp summary | app; optional Strava | high for Cross-training | moving/elapsed meanings pending |
| distance | Zepp summary | Zepp GPS track; optional Strava | pending outdoor | units and GPS validation pending |
| HR summary | Zepp summary | Zepp workout HR stream; optional Strava | high for Cross-training | stream pending |
| calories | Zepp summary | app; optional Strava | high for Cross-training | vendor algorithm |
| training load/effect/RPE | Zepp summary | Zepp app | load high; others pending | raw values pending |
| strength/exertion | Zepp summary/sub-data | Zepp app/notes | pending | structures pending |
| title/notes | Zepp summary/sub-data | Zepp app; optional Strava enrichment | pending | known summary title/content empty |
| GPS track | Zepp sub-data/detail | optional Strava track | pending | no production sample yet |
| elevation | Zepp summary | Zepp altitude samples; optional Strava | pending | Ojstrica anomaly |

A future canonical Zepp activity should contain:

```text
source_activity_id
sport
start_time
duration
distance
elevation
heart_rate
calories
training_load
training_effect
rpe
strength_metrics
gps_availability
quality_flags
source_raw_metrics
```

Strava can attach as optional validation/enrichment; it must not overwrite raw
Zepp metrics silently. This is a design draft, not an implementation.

The intended public/open-source project must keep credentials, user IDs,
private notes, raw personal activities, and GPS coordinates out of commits and
examples. Documentation must label API contracts as proven, production
behavior as observed, mappings as fixture-backed or candidate, and unknowns as
unsupported rather than filling them with assumptions.

C017 remains paused through the activity-source decision. Garmin remains a
separate deferred native-data audit after the Zepp architecture stabilizes.

## Z001.4 activity-quality design

The factual quality and source-trust contract is documented in
`docs/activity_data_quality.md`. No production Ojstrica payload accompanied
Z001.4, so no correction threshold, ascent algorithm, sentinel mapping, or
`diagnose-activity-quality` command was implemented.

Ojstrica remains the required anomaly fixture, paired with one normal Hike.
The operator must capture both `need_sub_data` modes by exact track ID.
Summary elevation fields remain separate and raw; coordinate-bearing stream
counts establish validation feasibility but do not by themselves produce a
canonical ascent.

The existing activity diagnostic is the evidence collector. The future
quality layer will preserve raw values, attach independent evidence, select a
value only under a proven rule, and expose factual status/confidence/reason.
Strava is optional validation/enrichment and its absence cannot block Zepp
ingestion. C017 remains paused.

## Z001.5 Ojstrica track/elevation forensics

Production identifies the July 25 Ojstrica Hike as `trackid=1784948221`,
fixture-backed `type=22`. The `/run/history.json` response contains rich
summary distance, duration, HR, training, and paired elevation fields, but
`need_sub_data=1` returned no detected GPS, altitude, or workout-HR samples.

The paired elevation fields strongly support `/100` scaling for long-form
values on this fixture, yielding ascent 1915.44 m, descent 1880.21 m, minimum
786.81 m, maximum 2329.29 m, and average 1651.95 m. This is not yet declared a
global conversion.

Ojstrica is now a multi-source processing fixture, not an example of invalid
Zepp elevation. Zepp and an independent Garmin recording agree within about
1.9 m at maximum altitude and 4.0 m at minimum altitude, while ascent totals
vary across Zepp summary, Strava summary, two exported GPX analyses, devices,
and sample densities. Naive positive-delta GPX ascent is not authoritative.

No verified Zepp cloud track/detail endpoint was found in current/upstream
source, history, branches, issues, or public code. Do not probe guessed URLs.
Capture sanitized app network traffic while opening detail/map/chart/share
screens. The new `--compare-sub-data` mode safely automates the two proven
history requests and structural diff for one exact track.

Source references: `ZeppClient.sport_history` is the only activity-history
method and originates in upstream commit `a466dfa`; `cmd_diagnose_activities`
calls it with date-derived `startTrackId`/`stopTrackId`. No other `ZeppClient`
method accepts an activity `trackid` or `parent_trackid`. The second-HR file
manifest, generic events, band data, and insight `trackId` fields belong to
different contracts and are not activity-detail evidence.

Exact read-only Ubuntu commands:

```bash
cd /opt/zepp-health-cli
source .venv/bin/activate

# A: summary request without sub-data
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana \
  --sport run --track-id 1784948221 \
  --limit 1 --need-sub-data 0 --json

# B/C: exact Ojstrica request with sub-data
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana \
  --sport run --track-id 1784948221 \
  --limit 1 --need-sub-data 1 --json

# Deterministic field/type/safe-value comparison
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana \
  --sport run --track-id 1784948221 \
  --limit 1 --compare-sub-data --json
```

There is no command D because no track-detail endpoint is verified. The safe
next step is an HTTPS proxy capture on the operator's own account while the app
opens the Ojstrica map, elevation/HR charts, and export/share screens; sanitize
path/query/response structure before reporting it.

For one normal Hike control, first use a narrow known date and discover its ID:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date NORMAL_HIKE_DATE --to-date NORMAL_HIKE_DATE \
  --timezone Europe/Ljubljana \
  --sport run --limit 20 --need-sub-data 1 --json

python3 zepp_health.py diagnose-activities \
  --from-date NORMAL_HIKE_DATE --to-date NORMAL_HIKE_DATE \
  --timezone Europe/Ljubljana \
  --sport run --track-id NORMAL_HIKE_TRACKID \
  --limit 1 --compare-sub-data --json
```

## Z001.6 multi-sport inventory

Use the bounded coverage command before treating a quiet sport-specific URL
segment as evidence that a sport is unavailable:

```bash
python3 zepp_health.py diagnose-sport-coverage \
  --from-date 2026-04-28 \
  --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --need-sub-data 1 \
  --json
```

The result is grouped by `(type, sport_mode)`. `PRESENT_WITH_VALUE` means only
that a nonempty value exists; its semantics or units may remain unknown.
`PRESENT_EMPTY` and `ABSENT` are distinct. Candidate negatives `-1`, `-100`,
`-20000`, and `-274` are `UNKNOWN_SEMANTICS`, not proven unavailable
sentinels. GPS requires an actual coordinate-bearing sample collection;
location metadata alone does not qualify.

Pagination remains an evidence boundary. `SINGLE_PAGE_TERMINAL_OBSERVED`
means that response contained `data.next=-1`; it does not prove general cursor
semantics. `INCOMPLETE_PAGINATION_UNRESOLVED` means counts must not be used as
complete coverage for the window.

The current mappings are deliberately narrow: type 22 is proven for the
Ojstrica Hike fixture and type 130 for the July 22 Cross-training fixture. See
`docs/zepp_sport_types.md` and `docs/zepp_sport_capabilities.md`.

For manual mapping against the Zepp app, use:

```bash
python3 zepp_health.py diagnose-sport-coverage \
  --from-date 2026-01-01 \
  --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --need-sub-data 1 \
  --mapping-list
```

Each line identifies one representative using `end_time`, because production
history does not populate `start_time`. The output calls it `end`; it must not
be interpreted as the workout's start. JSON adds
`representative_end_time`, `representative_local_date`,
`representative_local_time`, duration/distance/calorie values, units, and
source-field names to every existing group without removing prior keys.

Use the displayed local end date/time, duration, distance, calories, and
track ID to locate the exact app activity. Record the app's sport label
separately; never infer it solely from the metric pattern.

### Staged production probes (read-only)

Run phase A first. Phase B is useful only if a required sport is absent and
the phase-A pagination status is terminal. A nonterminal `next` makes absence
inconclusive.

```bash
cd /opt/zepp-health-cli
source .venv/bin/activate

# A — recent 90-day grouped inventory
python3 zepp_health.py diagnose-sport-coverage \
  --from-date 2026-04-28 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana --need-sub-data 1 --json \
  | tee /tmp/zepp-sport-coverage-90d.json

# B — current-year grouped inventory, only if phase A is insufficient
python3 zepp_health.py diagnose-sport-coverage \
  --from-date 2026-01-01 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana --need-sub-data 1 --json \
  | tee /tmp/zepp-sport-coverage-2026.json

# C — proven Hike fixture
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana --sport run \
  --track-id 1784948221 --limit 1 --need-sub-data 1 --json

# D — proven Cross-training fixture
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-22 --to-date 2026-07-22 \
  --timezone Europe/Ljubljana --sport run \
  --track-id 1784739852 --limit 1 --need-sub-data 1 --json

# E — Ride/Gravel/MTB candidates: cadence or power populated
jq '.inventory.type_groups[] | select(
  ((.field_status_counts.avg_cadence.PRESENT_WITH_VALUE // 0) > 0) or
  ((.field_status_counts.average_power.PRESENT_WITH_VALUE // 0) > 0)
)' /tmp/zepp-sport-coverage-2026.json

# F — Pool Swim candidates: pool length, SWOLF, or lap distance populated
jq '.inventory.type_groups[] | select(
  ((.field_status_counts.swim_pool_length.PRESENT_WITH_VALUE // 0) > 0) or
  ((.field_status_counts.swolf.PRESENT_WITH_VALUE // 0) > 0) or
  ((.field_status_counts.lap_distance.PRESENT_WITH_VALUE // 0) > 0)
)' /tmp/zepp-sport-coverage-2026.json

# G — Open Water Swim candidates: water type/strokes plus real GPS evidence
jq '.inventory.type_groups[] | select(
  (((.field_status_counts.waterType.PRESENT_WITH_VALUE // 0) > 0) or
   ((.field_status_counts.total_strokes.PRESENT_WITH_VALUE // 0) > 0)) and
  (.gps_track_present_count > 0)
)' /tmp/zepp-sport-coverage-2026.json

# H — Run/Trail Run candidates: pace or running subtype populated
jq '.inventory.type_groups[] | select(
  ((.field_status_counts.avg_pace.PRESENT_WITH_VALUE // 0) > 0) or
  ((.field_status_counts.runningType.PRESENT_WITH_VALUE // 0) > 0)
)' /tmp/zepp-sport-coverage-2026.json

# I — Ski candidates: downhill count/duration populated
jq '.inventory.type_groups[] | select(
  ((.field_status_counts.downhill_num.PRESENT_WITH_VALUE // 0) > 0) or
  ((.field_status_counts.durationOfDownhillWithMillis.PRESENT_WITH_VALUE // 0) > 0)
)' /tmp/zepp-sport-coverage-2026.json

# J — Walk candidates: steps populated; confirm the type in the Zepp app
jq '.inventory.type_groups[] | select(
  ((.field_status_counts.total_step.PRESENT_WITH_VALUE // 0) > 0)
)' /tmp/zepp-sport-coverage-2026.json
```

These metric filters produce candidates, not sport mappings. For every
candidate, record its representative date/type/mode and match it in the Zepp
app before adding the mapping catalog. The temporary JSON is sanitized but
still contains representative activity IDs; remove it according to the
operator's normal temporary-file policy after analysis.

## Z001.7 Ski vertical semantic correction

Historical symptom: the Ski activity's app vertical value (about 5913 m) could
be mistaken for ascent by generic elevation processing.

Production evidence from the exact sanitized record:

```text
type=105
sport_mode=0
trackid=1767339463
altitude_ascend=0
altitude_descend=5921
climb_dis_descend=28133
max_altitude=1913
min_altitude=965
```

Root cause: raw metric presence and generic field categories do not establish
sport semantics. For lift-served Ski, vertical descent is a primary exposure;
lift altitude gain is not athlete-powered climbing.

Correct behavior:

- retain every raw vertical field;
- normalize `altitude_descend` to `vertical_descent_m` and
  `elevation_loss_m`, with production evidence;
- do not normalize Ski lift gain to `elevation_gain_m`;
- set climbing-load ascent to null and eligibility false;
- keep numerical-quality validation separate from semantic interpretation.

Read-only verification:

```bash
python3 zepp_health.py diagnose-activities \
  --from-date 2026-01-02 --to-date 2026-01-02 \
  --timezone Europe/Ljubljana --sport run \
  --track-id 1767339463 --limit 1 --need-sub-data 1 --json
```

The diagnostic is allow-listed: it excludes credentials, device/user IDs,
URLs, coordinate values, and activity text. Its `semantic_interpretation`
section shows raw provenance and the normalized Ski decision.
