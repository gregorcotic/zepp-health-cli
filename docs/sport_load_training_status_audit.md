# S001.1 — SPORT_LOAD / Training Status evidence inventory

Status: IMPLEMENTED AND LIVE-VALIDATED
Investigation date: 2026-07-29
Scope: evidence inventory and implementation planning only

## Scope and rules

This document inventories the already-discovered SPORT_LOAD evidence without
reopening the closed legacy audit. It does not define a persistence schema,
normalizer, sync contract, or new CLI.

Confidence labels:

- **PROVEN**: directly present in transport evidence or established by exact
  repeated numeric comparison.
- **STRONG EVIDENCE**: repeated evidence strongly supports the interpretation,
  but one part of the semantic mapping remains indirect.
- **PLAUSIBLE**: consistent with names and observations but not
  cross-validated.
- **UNKNOWN**: no adequate evidence.

Raw transport facts and semantic interpretations are kept separate below.

## Evidence inventory

The legacy capture paths below are relative to the sibling `ZeppAiAgent`
repository. Account identifiers are intentionally omitted here.

| Artifact | Location | Raw fact / example | UI candidate | Confidence | Reason |
|---|---|---|---|---|---|
| Current client transport | `zepp_health.py:180-188`; `README.md:230-237` | GET `/v2/watch/users/{id}/WatchSportStatistics/SPORT_LOAD`; `startDay`, `endDay`, `limit=900`, `isReverse=true` | Training Status data source | PROVEN transport; PLAUSIBLE UI mapping | Endpoint is implemented as a raw read probe, but the code applies no canonical semantics. |
| Current raw probe | `zepp_health.py:581-586`, parser registration near `zepp_health.py:6542` | `sport-load --days N` prints the raw response | None by itself | PROVEN | Existing code reads and dumps the endpoint; it does not normalize, persist, or interpret it. |
| Current summary display | `zepp_health.py:6434-6464` | Prints `wtlSum` as `load` and `currnetDayTrainLoad` as `day_train_load` | Training load | PLAUSIBLE labels | These are developer-facing labels derived from native names, not UI-validated canonical semantics. |
| May 28 request, captured three times | `research/zepp_captured_requests/Raw_05-28-2026-07-52-27.folder/[282]`, `[598]`, `[939]` SPORT_LOAD request/response files | GET for `startDay=2026-05-27`, `endDay=2026-05-28`; all three responses were identical | Two-day Training Status history | PROVEN | Repeated HTTP 200 response and identical native values. |
| May 28 daily row | Same response files, response lines 13-22 | `dayId=2026-05-28`, `generatedTime=1779926400`, `currnetDayTrainLoad=0`, `wtlSum=359`, `updateTime=1779919200000`, thresholds `391/911/1088`, `device_source` present | Current daily load; rolling/weekly load; optimal range; overreaching boundary | PROVEN fields; PLAUSIBLE UI meanings | Values and types are direct transport facts. UI labels are not preserved with the capture. |
| May 27 daily row | Same response files, response lines 24-33 | `currnetDayTrainLoad=156`, `wtlSum=359`, thresholds `391/911/1088` | Daily workout load and rolling/weekly load | PROVEN fields; STRONG EVIDENCE semantics | The daily value exactly matches the same-day workout `exercise_load` in the captured activity corpus. |
| June 2 long-range first page | `research/zepp_captured_requests/biocharge_m59a/Raw_06-02-2026-17-11-33.folder/[688]` request/response | GET `2020-05-28..2026-06-02`, 900 items, `next=1699228800`; first row: 2026-06-02, daily `0`, `wtlSum=435`, thresholds `391/911/1088` | Historical Training Status series | PROVEN | Direct request, response, and pagination cursor. |
| June 1/May 31 rows | `[688]` response lines 22-40 | 2026-06-01 daily `83`, `wtlSum=435`; 2026-05-31 daily `43`, `wtlSum=449` | Daily workout load and rolling/weekly load | PROVEN fields; STRONG EVIDENCE semantics | Daily values exactly match same-date workout `exercise_load`. |
| June 2 pagination page | `[714]` request/response | Same request plus `next=1699228800`; 508 items from 2023-11-06 through 2022-04-04; no further `next` | Historical Training Status series | PROVEN | Demonstrates multi-page historical behavior. |
| Historical threshold sets | `[688]` and `[714]` responses | Examples include `391/911/1088`, `503/1088/1312`, and `113/261/304` for min/max/overreaching | Personalized optimal and overreaching boundaries | PROVEN values; STRONG EVIDENCE that thresholds vary over history | Thresholds are not global constants. The exact calculation and change timing are unknown. |
| Current handoff fixture | `docs/current_project_handoff.md:692-736` | Later fixture: daily `0`, `wtlSum=437`, min `330`, max `735`, overreaching `864`; same Training Status UI showed `Optimal` | Status category and optimal range | STRONG EVIDENCE | `330 <= 437 <= 735` is consistent with the UI category, but the original screenshot/raw response and date are not stored in the repositories. |
| Training Status UI labels | `docs/current_project_handoff.md:726-734` | ATL / Fatigue Level, CTL / Fitness Level, TSB / Form; `TSB = CTL - ATL` | Fatigue, Fitness, Form | PROVEN as recorded UI/Exertion meanings; backend is Exertion, not shown to be SPORT_LOAD | Existing Exertion captures directly contain ATL/CTL/TSB. No SPORT_LOAD field with these names exists. |
| Workout history corpus | `../ZeppAiAgent/data/debug/zepp_run_history_live.json` | Per-activity `exercise_load`; 764 dates overlap SPORT_LOAD and have nonnegative logged workout load | `currnetDayTrainLoad` | STRONG EVIDENCE, nearly proven | 692/764 daily comparisons match exactly after summing same-local-date activity loads. Mismatches, especially multi-activity days, prevent declaring a universal sum rule. |
| Exertion event corpus | Legacy captured `/v2/users/me/events`, `exertion/algo_result` records | 2026-05-28: ATL/CTL/TSB `30/34/4`, SPORT_LOAD daily/WTL `0/359`; 2026-05-27: `33/38/5` versus `156/359` | Fatigue/Fitness/Form versus Training Load | PROVEN distinct values | Same-date numeric pairs disprove simple identity between `wtlSum` and ATL or CTL. |
| Legacy application code | Sibling repository excluding capture/research data | No SPORT_LOAD parser, model, persistence, or semantic consumer found | None | PROVEN absence in searched material | Legacy material captured the endpoint but did not operationalize or interpret it in application code. |

The May 28 `[282]`, `[598]`, and `[939]` responses are repeated captures of
the same two factual rows, not three independent semantic fixtures.

## Proven transport contract

- HTTP method: GET.
- Path:
  `/v2/watch/users/{id}/WatchSportStatistics/SPORT_LOAD`.
- Query parameters observed:
  - `startDay=YYYY-MM-DD`
  - `endDay=YYYY-MM-DD`
  - `limit=900`
  - `isReverse=true`
  - optional `next=<generatedTime-like cursor>` for pagination
- Request timezone header in the production captures:
  `Europe/Ljubljana`.
- Response envelope:
  - `items`: array of daily objects
  - optional `next`: numeric pagination cursor
- One request can return multiple dates. A long request returned 900 rows and
  required a second page of 508 rows.
- Captured historical coverage spans 2022-04-04 through 2026-06-02, with some
  absent calendar dates.
- No eventType, subType, or property key is involved. SPORT_LOAD belongs to
  the watch statistics subsystem.

Every observed daily item contains:

| Field | Observed type | Proven meaning |
|---|---|---|
| `dayId` | `YYYY-MM-DD` string | Native day key |
| `generatedTime` | integer epoch seconds | Native generation/day timestamp; exact semantic role unresolved |
| `updateTime` | integer epoch milliseconds | Native row update timestamp |
| `currnetDayTrainLoad` | integer | Native daily training-load value; spelling is native |
| `wtlSum` | integer | Native WTL aggregate; exact expansion/algorithm unresolved |
| `wtlSumOptimalMin` | integer | Native lower threshold |
| `wtlSumOptimalMax` | integer | Native upper threshold |
| `wtlSumOverreaching` | integer | Native overreaching threshold |
| `device_source` | integer, absent in older rows | Native device provenance |

No units are present in the response and no unit is proven.

## Numeric cross-checks

### Backend to recorded UI

One same-context fixture is preserved in the current handoff:

- `wtlSum=437`
- native lower/upper thresholds `330/735`
- overreaching threshold `864`
- Zepp UI category `Optimal`

The inequality `330 <= 437 <= 735` is exact and strongly supports the
threshold/category relationship. It is not an exact numeric backend-to-UI
match because the repository does not retain the screenshot values or dated
raw response. No scaling or rounding claim is justified from this fixture.

### Daily SPORT_LOAD to activity `exercise_load`

The existing historical corpora were joined by Europe/Ljubljana activity date:

- 764 overlapping dates had at least one nonnegative activity
  `exercise_load`.
- 692 dates exactly matched:
  `currnetDayTrainLoad == sum(activity.exercise_load for that date)`.
- Recent exact examples:
  - 2026-06-01: `83 == 83`
  - 2026-05-31: `43 == 43`
  - 2026-05-30: `136 == 136`
  - 2026-05-29: `16 == 16`
  - 2026-05-27: `156 == 156`
  - 2026-05-20: `200 == 200`

This is strong evidence that `currnetDayTrainLoad` is the daily aggregate of
the same native workout-load system represented by per-activity
`exercise_load`. It is not yet safe to state that it is always a simple sum:
72 overlapping dates differed, and multi-activity days can differ (for
example 2026-04-30: SPORT_LOAD `2`, summed stored activities `3`). Possible
causes include rounding, activity revisions, excluded activities, incomplete
history, or a non-additive rule; none is proven.

### `wtlSum` rolling-window hypothesis

`wtlSum` is close to a seven-calendar-day sum of the integer
`currnetDayTrainLoad` values on much of the corpus:

- 1,036 dates had seven consecutive SPORT_LOAD rows available.
- 94 were exact integer sums.
- 862 were within 5 points.
- Material outliers also exist.

This supports a rolling/weekly-load hypothesis, and the native name `wtlSum`
is consistent with it, but the precise window and use of unrounded internal
values are not proven. No reconstruction formula should be implemented.

### SPORT_LOAD versus Exertion

Same-date examples:

| Date | daily SPORT_LOAD | `wtlSum` | ATL | CTL | TSB |
|---|---:|---:|---:|---:|---:|
| 2026-05-28 | 0 | 359 | 30 | 34 | 4 |
| 2026-05-27 | 156 | 359 | 33 | 38 | 5 |

Current evidence therefore indicates related but distinct systems:

- `currnetDayTrainLoad` aligns strongly with per-workout `exercise_load`.
- `wtlSum` is a higher-magnitude rolling/weekly candidate.
- ATL/CTL/TSB are separate Exertion values.
- No conversion between SPORT_LOAD and ATL/CTL/TSB is demonstrated.

## Training Status UI model

| UI metric recorded in evidence | Classification | Backend status |
|---|---|---|
| Status category `Optimal` | B — candidate mapping exists | Strongly consistent with `wtlSum` lying between `wtlSumOptimalMin` and `wtlSumOptimalMax`; needs one simultaneous screenshot/response cross-check. |
| Training-load aggregate / trend | B | `wtlSum` is the candidate; exact UI label, rolling window, and graph mapping remain unproven. |
| Optimal range | B | `wtlSumOptimalMin` and `wtlSumOptimalMax` are direct backend candidates; exact UI equality is not retained. |
| Overreaching boundary | B | `wtlSumOverreaching` is the direct candidate; UI equality is not retained. |
| Current/day training load | B | `currnetDayTrainLoad` is strongly linked to activity `exercise_load`; exact Training Status UI field equality is not retained. |
| Fatigue Level | A | Existing Exertion `atl` is the recorded backend source. No evidence maps it to a SPORT_LOAD field. |
| Fitness Level | A | Existing Exertion `ctl` is the recorded backend source. No evidence maps it to a SPORT_LOAD field. |
| Form | A | Existing Exertion `tsb`, with `TSB = CTL - ATL`, is the recorded backend source. No evidence maps it to SPORT_LOAD. |
| Recovery | C | No SPORT_LOAD field or proven Training Status backend mapping found. |

The repository has no retained screenshot artifact, so UI layout, displayed
rounding, graph period, and exact wording cannot be reconstructed further.

## Recovery relationship

SPORT_LOAD responses do not contain `recovery`, `recoveryFactor`, or
`recoveryFactorID`.

Those fields occur in the separate `exertion/algo_result` event payload. The
same-date examples above show coexistence but do not demonstrate a formula or
causal mapping. S001.1 provides no new evidence about the unresolved
recoveryFactor or recoveryFactorID dictionaries; they remain deferred.

## Activity relationship

Per-activity `exercise_load` is the only strong activity-level linkage found.
The 692 exact daily matches establish that SPORT_LOAD is related to workout
load. They do not prove:

- that every activity is included;
- that multiple activities are always summed directly;
- whether deleted/edited workouts revise historical SPORT_LOAD;
- when after a workout the daily row updates;
- whether non-workout sources can contribute.

No before/after workout SPORT_LOAD capture exists in the repository.

## Date and time semantics

Proven:

- `dayId` is a date string and matches the requested date range.
- Requests carried `timezone: Europe/Ljubljana`.
- `generatedTime` is epoch seconds; for 2026-05-28 it is
  2026-05-28 00:00 UTC / 02:00 Europe/Ljubljana.
- `updateTime` is epoch milliseconds.
- The 2026-05-28 zero-load row used `updateTime=2026-05-28 00:00`
  Europe/Ljubljana, while the 2026-05-27 workout row updated at 19:54:11
  local time.
- Three May 28 responses over approximately nine minutes were identical.

Interpretation:

- `dayId` is the safest factual calendar key.
- `updateTime` can represent an actual daytime update on workout days.
- Midnight `updateTime` on zero-load days may be initialization/default state.
- `generatedTime` appears UTC-midnight anchored and must not be treated as
  proof of a UTC physiological day.

Unknown:

- precise local-day rollover behavior;
- whether today's values are partial/in-progress;
- update delay after workout sync;
- when historical rows become immutable;
- whether thresholds can revise historical dates.

## Proven facts

1. SPORT_LOAD is a readable GET watch-statistics resource, separate from
   events, properties, activities, and Exertion.
2. The response is a paginated array of dated daily objects.
3. All listed native fields and their integer/time types are production
   captured.
4. Threshold values change historically and must be preserved per row.
5. `currnetDayTrainLoad` has a strong, repeated, often exact relationship to
   per-activity `exercise_load`.
6. SPORT_LOAD values are not aliases for Exertion ATL or CTL.
7. SPORT_LOAD has no direct recoveryFactor/recoveryFactorID fields.
8. The current project has only a raw read probe; it has no first-class
   SPORT_LOAD normalization, persistence, incremental sync, or factual CLI.

## Remaining hypotheses

1. `wtlSum` is the Training Status load aggregate shown by the UI.
2. `wtlSumOptimalMin/Max` are the displayed optimal-range boundaries.
3. `wtlSumOverreaching` is the displayed overreaching boundary.
4. Zepp selects `Optimal` when `wtlSum` is inclusively inside the native
   min/max range.
5. `wtlSum` is based on a rolling seven-day load using more precise internal
   values than the displayed integer daily loads.
6. `currnetDayTrainLoad` is a daily aggregation of eligible workout
   `exercise_load`, with an unresolved rule for mismatched/multi-activity
   days.

None of these should be promoted to canonical semantics beyond the confidence
stated here until the UI cross-check below is completed.

## Minimum live capture required

One coordinated capture session is sufficient for implementation readiness.
A before/after workout experiment is not required for the initial factual
implementation.

1. In Zepp, open the exact **Training Status** screen.
2. Screenshot the screen(s) that visibly contain:
   - the current status label (for example `Optimal`);
   - the current/aggregate training-load number;
   - the displayed optimal minimum and maximum, if shown;
   - the overreaching boundary, if shown;
   - Fatigue Level / ATL, Fitness Level / CTL, and Form / TSB values if they
     are on the same screen or one immediately linked detail screen.
3. Record the local date and time shown by the phone.
4. With Proxyman recording, pull to refresh or close/reopen only that Training
   Status screen.
5. Export the matching GET request and JSON response whose path ends in:
   `/WatchSportStatistics/SPORT_LOAD`.
6. Preserve the query parameters, response `items` row for the screenshot
   date, and response time. Credentials/cookies may be removed.

The decisive comparison is whether the visible load/range/boundary values
equal `wtlSum`, `wtlSumOptimalMin`, `wtlSumOptimalMax`, and
`wtlSumOverreaching` on the same dated row, and whether the visible category
matches the row's position relative to its thresholds.

Only if the screen does not display numeric load/range values would a second
capture after a known workout be useful. That optional experiment would
record the same screen and SPORT_LOAD response immediately before the workout
and again after the workout has synced, alongside the workout's native
`exercise_load`.

## Implementation readiness decision

**READY AFTER ONE CROSS-CHECK**

Transport, payload shape, pagination, native identity/date fields, historical
behavior, and the activity-load relationship are sufficiently established for
a factual model. One simultaneous Training Status screenshot and SPORT_LOAD
response is still needed to safely name `wtlSum` and its thresholds as the
exact UI load/range/status inputs.

If that equality is confirmed, S001.2 can implement the native fields without
reconstructing WTL, thresholds, ATL/CTL/TSB, status categories, or workout
attribution formulas.

## S001.2 — Same-Day Production Cross-Check

### Capture context

- UI date: 2026-07-29.
- UI capture time: approximately 10:17 Europe/Ljubljana.
- UI source label: `From Workouts`.
- Training Form / TSB: `-25.0`.
- Fatigue Level / ATL: `89.0`.
- Fitness Level / CTL: `64.0`.
- Training Status category: `Optimal`.
- The arithmetic is exact: `CTL - ATL = 64 - 89 = -25`.
- The supplied Proxyman HAR did not contain a SPORT_LOAD request or any of
  the SPORT_LOAD native fields. This is consistent with app caching or
  request timing, but that cause is not proven.

ATL, CTL, and TSB remain Exertion-backed Training Form metrics. They are not
mapped to SPORT_LOAD.

### Direct production request

The existing authenticated client method was used without adding probe or
implementation code:

```text
GET /v2/watch/users/{id}/WatchSportStatistics/SPORT_LOAD
startDay=2026-07-29
endDay=2026-07-29
limit=900
isReverse=true
```

The narrow one-day request succeeded; no wider fallback range was required.
Sanitized response item:

```json
{
  "dayId": "2026-07-29",
  "generatedTime": 1785283200,
  "updateTime": 1785276000000,
  "currnetDayTrainLoad": 0,
  "wtlSum": 432,
  "wtlSumOptimalMin": 261,
  "wtlSumOptimalMax": 607,
  "wtlSumOverreaching": 735,
  "device_source": 9568513
}
```

### UI to backend comparison

| UI concept | UI value | Backend candidate | Backend value | Match type | Interpretation |
|---|---:|---|---:|---|---|
| Training Status | `Optimal` | `wtlSum` relative to native optimal bounds | `261 <= 432 <= 607` | CONSISTENT RANGE | The current observation is consistent with the native Optimal range. It does not prove the complete category algorithm. |
| Displayed aggregate Training Load | Not displayed in supplied evidence | `wtlSum` | `432` | NOT DISPLAYED | No exact UI numeric equality can be claimed. Preserve conservatively as WTL sum. |
| Optimal lower bound | Not displayed | `wtlSumOptimalMin` | `261` | NOT DISPLAYED | Native threshold is factual; exact UI-label mapping remains strongly supported rather than directly displayed. |
| Optimal upper bound | Not displayed | `wtlSumOptimalMax` | `607` | NOT DISPLAYED | Native threshold is factual; exact UI-label mapping remains strongly supported rather than directly displayed. |
| Overreaching boundary | Not displayed | `wtlSumOverreaching` | `735` | NOT DISPLAYED | `735 > 607 > 432`; the native threshold is preserved without inferring other status categories. |
| Current-day training load | No separate number displayed | `currnetDayTrainLoad` | `0` | NOT DISPLAYED | The UI source label `From Workouts` is semantically consistent but is not a numeric match. |
| Fatigue Level / ATL | `89.0` | Exertion `atl` | Not queried in this cross-check | Separate domain | It must not be mapped to SPORT_LOAD. |
| Fitness Level / CTL | `64.0` | Exertion `ctl` | Not queried in this cross-check | Separate domain | It must not be mapped to SPORT_LOAD. |
| Training Form / TSB | `-25.0` | Exertion `tsb` | `64 - 89 = -25` from UI | EXACT UI arithmetic | It must not be mapped to SPORT_LOAD. |

The status/range result independently repeats the earlier recorded fixture:
an `Optimal` UI state coincides with `wtlSum` inside the per-row native
minimum and maximum. This is strong evidence for the range/category
relationship, but no claim is made about every other category.

### Same-day activity comparison

The existing read-only workout-history source was queried for 2026-07-29.
It returned zero activity summaries:

- SPORT_LOAD `currnetDayTrainLoad`: `0`
- same-day activity count: `0`
- activities with a native `exercise_load`: `0`
- factual `exercise_load` sum: unavailable
- difference: unavailable

The absence of activities does not provide an independent numeric
`exercise_load` cross-check. A zero sum was not synthesized from an empty
result.

### Time semantics

- `dayId=2026-07-29` matches the local UI date.
- `generatedTime=1785283200` is
  2026-07-29 00:00:00 UTC / 02:00:00 Europe/Ljubljana.
- `updateTime=1785276000000` is
  2026-07-28 22:00:00 UTC / 2026-07-29 00:00:00 Europe/Ljubljana.
- At approximately 10:17 local time, the current-day record therefore
  already existed, but its update timestamp remained local midnight.
- With no same-day recorded workout and daily load `0`, this observation is
  consistent with a generated current-day baseline that had not received an
  intraday workout update. One observation does not establish the general
  update-delay or rollover algorithm.

### Confidence and readiness

Proven:

- authenticated same-day SPORT_LOAD readback works with an exact one-day
  range;
- `dayId` is the factual native date key;
- all native scalar and timestamp fields can be preserved without deriving
  formulas;
- ATL/CTL/TSB are separate from SPORT_LOAD;
- the 2026-07-29 WTL value lies within its native min/max while the UI reports
  `Optimal`.

Strong but not fully proven:

- `wtlSum` is the aggregate load used by the Training Status range;
- `wtlSumOptimalMin/Max` are the UI Optimal boundaries;
- `wtlSumOverreaching` is the UI overreaching boundary.

Unknown:

- the expansion and proprietary calculation of WTL;
- WTL units and exact rolling window;
- the complete status-category algorithm;
- UI rounding or direct numeric display mapping;
- exact intraday update and historical stabilization behavior.

Readiness is **READY FOR IMPLEMENTATION**. The source can be implemented as a
factual native daily record without encoding the unresolved UI hypotheses or
reconstructing proprietary calculations. The conservative canonical name
`wtl_sum` avoids overstating the meaning of `wtlSum`.

### Proposed S001.3 canonical model

| Native field | Proposed canonical field | Treatment |
|---|---|---|
| `dayId` | `event_date` | Native factual date |
| `generatedTime` | `generated_time_s` | Preserve epoch seconds; do not silently convert the native unit |
| `updateTime` | `updated_time_ms` | Preserve epoch milliseconds |
| `currnetDayTrainLoad` | `current_day_training_load` | Native daily load; do not reconstruct from activities |
| `wtlSum` | `wtl_sum` | Conservative native aggregate name |
| `wtlSumOptimalMin` | `optimal_min` | Native lower threshold |
| `wtlSumOptimalMax` | `optimal_max` | Native upper threshold |
| `wtlSumOverreaching` | `overreaching_threshold` | Native threshold without deriving categories |
| `device_source` | `device_source` | Optional native provenance |

The raw provenance should retain the exact native key
`currnetDayTrainLoad`. ATL, CTL, and TSB must remain in the Exertion model.

### Proposed S001.3 implementation plan

1. **S001.3A — canonical normalizer:** normalize the dated native rows,
   canonicalize numeric strings where necessary, preserve missing values,
   timestamps, device provenance, and raw sanitized source data.
2. **S001.3B — SQLite persistence:** migrate the current schema v8 to the
   next verified version, expected v9, with one factual daily SPORT_LOAD row
   per `event_date` and deterministic idempotency.
3. **S001.3C — historical backfill and pagination:** follow `next` through
   the proven 900-row pages while retaining per-day thresholds.
4. **S001.3D — incremental sync:** fetch a bounded overlap window so
   current-day or recently revised rows update without duplicates.
5. **S001.3E — factual CLI:** read from SQLite and expose native daily load,
   WTL sum, thresholds, dates, timestamps, and factual freshness without
   coaching or derived status formulas.
6. **S001.3F — tests and live idempotency:** cover normalization,
   numeric-string handling, migration, DB reads, pagination, first/second
   writes, factual updates, CLI privacy, and a temporary-database live
   readback.
7. **S001.3G — documentation:** update the handoff, database contract, and
   project history after validation.
8. **S001.3H — TRC integration:** remain a separate later step after the
   factual implementation and live idempotency are validated.

## S001.3 — Factual implementation results

Status: IMPLEMENTED AND LIVE-VALIDATED

The implementation preserves the proven endpoint and native payload without
adding a WTL formula or status classifier:

- canonical `normalize_sport_load_data()`;
- schema v9 `sport_load_records`, unique by native `dayId`/`event_date`;
- factual UPSERT comparison with raw-only changes remaining unchanged;
- native cursor pagination with repeated-cursor protection and date
  deduplication;
- dedicated bounded SPORT_LOAD sync and `sync_run_domains` accounting;
- database reads for a date window and latest row;
- SQLite-only `sport-load --days N [--json]` CLI;
- Europe/Ljubljana date-aware `current`, `stale`, and `missing` freshness.

Deterministic validation covered the audited 2026-07-29 fixture,
numeric-string typing, missing fields, unknown native fields, v8-to-v9
migration, persistence corrections, raw-only idempotency, pagination,
deduplication, empty and failed domain isolation, CLI privacy, and freshness.

Production validation used a fresh temporary database:

- first 7-day sync: 7 SPORT_LOAD rows inserted;
- identical second sync: 0 inserted, 0 updated, 7 unchanged;
- latest row: the audited 2026-07-29 fixture (`wtl_sum=432`,
  native range `261..607`);
- freshness: `current`;
- database integrity and foreign keys: clean.

A bounded historical request from 2023-11-01 through 2026-07-29 followed two
native pages and persisted 961 unique dated rows with no duplicates.

The remaining semantic boundaries are unchanged: `wtl_sum` has no proven
expansion, unit, proprietary formula, exact rolling window, complete status
algorithm, or authoritative direct numeric UI mapping. ATL, CTL, and TSB
remain Exertion fields. TRC integration is explicitly deferred.
