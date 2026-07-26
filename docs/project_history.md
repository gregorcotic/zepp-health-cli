# Project history

## B004 — SQLite persistence

Added schema-versioned SQLite storage for native Zepp domains, sanitized raw
payloads, per-domain synchronization metadata, deterministic logical keys and
idempotent UPSERT behavior. Schema version 2 includes LifeLoad storage.

## B005 — Operational validation and recovery

Added SQLite integrity checks and SQLite-backup-API backup/restore commands.
Backups are validated before publication; restore requires a separate target by
default and compares schema version and table counts. Runtime databases,
sidecars and backup directories are ignored by Git. Live validation should be
performed on the MacBook account and must not expose credentials.

## Known historical commits

- B004: SQLite persistence for native Zepp health data (`677181b`).
- B003: daily-status/readiness and native metric consolidation (see Git history).

## B006 — Production automation

Problem: validated SQLite synchronization still required manual operation on
Ubuntu.

Root cause: no unattended schedule, process lock, or machine-readable health
status existed.

Solution: added a six-hour systemd timer and oneshot service template, a
`flock`-based wrapper, `sync-health` with documented exit codes, and journal-
focused operational procedures. Credentials remain outside units and logs.

Lessons learned: systemd service templates must name the runtime account
explicitly; this repository therefore requires installation-time replacement
of safe user/group placeholders. Kernel advisory locks are preferable to
timestamp-only lock files because terminated processes release them.

## C019 — Fixed Zepp schedule and morning factual freshness

Problem: the old repository timer ran at 00:00, 06:00, 12:00, and 18:00 local
time, while a successful sync timestamp could mask that same-day
HRV/readiness data had not arrived for morning TRC.

Solution: changed the single persistent timer to 02:00, 06:30, 08:30, 12:00,
18:00, and 22:00 in `Europe/Ljubljana`; added an additive declarative trigger
for the existing context generator after successful sync; and extended
`sync-health` with separate sync and domain factual freshness. The context
timer remains a fallback. No readiness/TRC calculation, AI, destructive
migration, or invented sleep data was added.

Ownership: this app owns Zepp synchronization and database coverage facts.
`coach-data-bridge` must project the freshness object into its generated
`general-context.json`; bridge logic is intentionally not placed here. Deploy
and observe standalone first, then mirror into `coach-platform`, run its
`test-all`, and resume C017.3. At the C019 close, C018 remained deferred; it is
completed in the entries below.

## C018.1 — Wake Energy forensics

Investigated the `Charge/wake_data` path without changing its normalization,
freshness, or coaching semantics. Added a narrow sanitized
`diagnose-wake-energy` command and characterization coverage for explicit dates,
the Europe/Ljubljana midnight boundary, epoch seconds, sleep crossing midnight,
missing/empty/multiple samples, unsupported wrappers, and same-day SQLite
revision behavior.

The investigation establishes current behavior, not the production root cause:
wake samples inherit the parent event date; missing timezone defaults to UTC;
timestamps are assumed to be epoch milliseconds; and the ordinary sync summary
reports both an actually empty response and a nonempty unrecognized payload as
`empty` with zero normalized records. Production watch/app/API evidence is still
required before selecting a C018.2 fix.

Repository provenance: `Charge/wake_data` and the observed wake field mapping
first appeared in fork commit `beedc7a` after live validation. The configured
upstream base commit `a466dfa` contains generic `Charge/real_data` access but no
wake parser or BioCharge contract. Repository issue/code searches found no
additional upstream explanation.

## C018.2 — Proven local wake-day correction

Production evidence for July 23–26 classified the stale Wake BioCharge as
Case C, not a cloud delay. Three raw records each contained one extracted wake
sample. For the latest record, the parent timestamp resolved to July 25 UTC,
but `value.startTime=1785016800000` with
`timeZone=1,Europe/Ljubljana` represented July 26 00:00 local. The record was
retrieved by the July 26 morning sync, yet the old generic resolver stored it
as July 25.

The operator's July 24 09:03 app comparison independently confirmed the
semantics: Zepp displayed Wake BioCharge 65 and distinct Current BioCharge 55.
The raw record with parent July 23, local `startTime` July 24, and
`wakeCharge=65` was therefore the July 24 wake value, not a July 23 value.

The fix is wake-specific: it recognizes the observed Zepp timezone prefix and
uses an explicit sample date first, then an explicit sample timestamp or
`value.startTime + s` in that timezone, then the existing parent-date fallback.
No generic event resolver, freshness rule, morning requirement, endpoint,
schema, current Charge mapping, epoch-unit handling, or UTC-offset handling was
changed. The last two were not present in the production evidence.

The three affected derived `wake_energy` rows require a guarded in-place
record-key/date repair before resynchronization. Their sanitized raw payloads
and sync history remain intact; no schema migration or historical rebuild is
required.

## C018.3 — Production validation and closeout

The guarded repair and targeted seven-day resynchronization completed
successfully in production. The resync retrieved six wake records and reported
all six unchanged, confirming stable corrected logical identities with no
duplicates or revision regression. The repaired range retained Wake Charge
values 72, 65, 41, and 65 on July 23–26 respectively. July 24–26 raw parent
dates remained one day earlier while the wake-specific resolver and SQLite rows
used the correct local wake dates.

`sync-health` was healthy: synchronization occurred today, Wake Energy covered
July 26 as today, HRV/readiness/sleep-related readiness also covered today, and
`morning_data_status` became `complete` without weakening freshness rules.
SQLite integrity and foreign-key checks passed under schema version 3; raw
payloads, timestamps, sync history, and all other Zepp domain counts remained
intact.

C018 is complete. C018.2 changed only the value of the existing
`wake_energy.event_date` contract, so no new bridge field, gateway route, GPT
Action, OpenAPI schema, token, URL, or Custom GPT import is required. Existing
C017 monorepo mirroring remains separate planned work and may resume after this
standalone closeout.

## Z001.1 — Zepp activity-source forensics

Audited the inherited reverse-engineered workout interface without changing
health synchronization, persistence, coaching, or Strava. The only implemented
cloud workout path is a sport-specific
`GET /v1/sport/{sport}/history.json` request. It has no pagination loop,
generic all-sports mode, normalizer, database table, or captured response
fixture. `run`, `walking`, `ride`, and `swimming` occur as URL-segment
candidates in repository code/documentation; they are not a complete or
production-validated sport mapping.

Added `diagnose-activities`, a narrow read-only structural probe. It suppresses
credentials, user/device identifiers, URL values, GPS coordinates, and
title/note text by default while reporting field names, safe summary scalars,
nested counts, and sample shapes. Synthetic tests characterize privacy and
shape handling but do not assert an unobserved Zepp schema.

Current conclusion: Zepp cannot yet be designated the authoritative activity
source. Critical cloud capabilities—including stable activity IDs, titles,
notes, strength details, tracks, elevation, workout HR, laps, update/deletion
semantics, pagination, and historical retention—remain unknown until a small
production probe captures representative Hike, strength/CrossFit, cycling, and
swimming responses. The existing Zepp-health plus Strava-activities
architecture remains unchanged.

The first production probe subsequently returned one real record under
`data.summary[]` from the `run` segment. Its field-name schema includes stable-ID
candidates, sport/title, distance/elevation, HR, pace/cadence/power,
training-effect, CrossFit/strength, swimming, and `data.next` pagination
candidates. The initial diagnostic exposed the structure but failed to count
the record because `summary` was not a recognized wrapper; this was corrected
with a production-shaped privacy regression test. Field population, units,
sport routing, cursor semantics, and historical stability remain unproven
until the sanitized command is rerun.

## Z001.2 — Cross-training activity deep dive

Production matched `trackid=1784739852`, `type=130`, local start around 19:04,
2623 seconds, 259 kcal, average/max/min HR 89/121/71, and training load 4 to
the user's July 22 Cross-training workout. Because this non-running workout
came from `/v1/sport/run/history.json`, that route is proven broader than
literal runs but is not yet proven to be complete generic history. Type 130 is
recorded only as a fixture-backed Cross-training mapping.

Expanded the sanitized diagnostic for native RPE, training effects, workout
balance, strength scores/groups, cardiac/muscular/total exertion, CrossFit
content, and coach insight. Known nested containers now expose bounded safe
scalar values and authorized text. Corrected the prior GPS overstatement:
`location` is metadata, while GPS requires actual coordinate-bearing track
samples. No verified detail or Workout Notes endpoint was found locally,
upstream, in repository issues, or in searched public code.

Paired `need_sub_data=0/1` production captures are still required to determine
embedded details and verify app RPE 5, aerobic TE 0.3, Workout Balance 0/100,
exertion, and the known manual notes. A one-day Hike capture through the
already proven `/run/` route is the next outdoor capability gate. C017 remains
paused because the outcome can materially change future repository and service
boundaries; no downstream or Strava behavior changed.

## Z001.3 — Track-specific production-probe preparation

Added a local-only `--track-id` diagnostic filter so explicitly authorized text
inspection is limited to one activity even when Zepp returns several records.
Added factual outdoor stream metadata: coordinate-bearing GPS point count,
field names, raw timestamp coverage, altitude-sample count, and workout-HR
sample count. Coordinates remain suppressed and `location` remains metadata,
not GPS evidence.

Prepared exact paired Cross-training probes, a bounded Ojstrica discovery and
one-track follow-up, and a small pagination probe. `data.next=-1` is recorded
only as an observed terminal candidate; cursor direction and reuse remain
unproven and no pagination loop was added. Ojstrica is designated as a future
Z001.4 quality fixture for comparing raw summary elevation with track-derived
validation without overwriting either.

Drafted the future source-trust and canonical-activity direction: Zepp native
facts remain authoritative candidates, while Strava may be optional validation
or enrichment. The long-term public repository must separate proven contracts,
observed behavior, candidate mappings, and unknowns while excluding secrets,
private notes, personal activities, user IDs, and GPS coordinates. No
production output accompanied Z001.3, so RPE/TE/notes/sub-data and outdoor
capabilities remain pending rather than inferred. C017 stays paused.

## Z001.4 — Activity data quality and source-trust design

Defined a source-traceable quality architecture before implementing activity
corrections. Each metric retains its raw vendor value and field, independent
validation evidence, any selected factual value, qualitative confidence,
quality status, flags, and reason. Strava is optional evidence and never an
automatic authority or ingestion dependency.

No Ojstrica production payload was provided, and the repository contains no
proven activity meaning for candidate negative values `-1`, `-100`, `-20000`,
or `-274`. Consequently no sentinel conversion, elevation threshold, naive
altitude summation, correction algorithm, or quality CLI was added. The
documented production plan pairs Ojstrica with a normal Hike and captures both
sub-data modes by exact track ID before calibration.

Documented sport-aware checks, raw-versus-validated separation, qualitative
statuses/confidence, optional Zepp–Strava matching, the future coach contract,
and the intended public Zepp-native platform. Ojstrica remains the mandatory
Z001.4/Z001.5 anomaly fixture. C017 remains paused; no downstream, production,
Strava, Garmin, or repository-structure change was made.

## Z001.5 — Ojstrica native-track and elevation forensics

Reclassified the July 25 Ojstrica Hike (`trackid=1784948221`, fixture-backed
`type=22`) as a multi-source elevation-processing fixture. Zepp's paired
summary fields support `/100` scaling on this record: 1915.44 m ascent,
1880.21 m descent, 786.81–2329.29 m range. An independent Garmin recording
agrees within roughly 1.9 m at the maximum and 4.0 m at the minimum, while
different summary/export/device algorithms yield materially different ascent
totals. No source is declared ground truth and naive GPX positive-delta ascent
is explicitly rejected as an automatic replacement.

Deep repository, upstream, history, branch, issue, and public-code searches
found no verified Zepp cloud track/detail endpoint. The proven
`/run/history.json` response with `need_sub_data=1` still exposed no detected
GPS, altitude, or workout-HR samples. Added a sanitized deterministic
`--compare-sub-data` mode that performs the two proven history requests for one
track and reports structural/type/safe-value differences without text,
coordinates, identifiers, or unrelated records.

Expanded provenance classes to distinguish native summaries/tracks,
third-party summaries/exports, independent-device tracks, and route
references. Ojstrica's current flags are source disagreement plus strong
absolute-altitude agreement and pending native-track validation—not invalid
Zepp elevation. C017 remains paused and no downstream, Garmin, production, or
repository restructuring occurred.

## Z001.6 — bounded multi-sport coverage discovery

Shifted the activity audit from deeper Ojstrica track work to breadth across
the user's main sports. Added `diagnose-sport-coverage`, a read-only,
single-request inventory over the production-proven
`GET /v1/sport/run/history.json` route. It groups records by exact `type` and
`sport_mode`, reports one representative identifier, classifies allow-listed
fields as populated, empty, absent, or unknown-semantics, and summarizes actual
GPS/altitude/workout-HR sample evidence without coordinates or activity text.

The command deliberately does not follow `data.next`. A `next=-1` response is
reported only as a terminal single-page observation; any other cursor makes
the requested-window counts explicitly incomplete. Cursor direction,
continuation bounds, and loop termination remain unproven.

Only two mappings are currently production-fixture-backed: type 22 is the July
25 Ojstrica Hike and type 130 is the July 22 Cross-training workout. The
`/run/` route is proven broader than literal running, but it is not yet proven
to contain every activity type. Ride/Gravel/MTB, Pool Swim, Open Water Swim,
Run/Trail Run, Ski, Walk, and a distinct Strength type remain
production-pending. C017 stays paused; no production, downstream, storage, or
repository-structure changes were made.

After the bounded 2026 production probe returned 135 records in 14
`type`/`sport_mode` groups, the coverage diagnostic gained safe representative
lookup metadata for manual app matching. It reports the raw representative
`end_time`, its date and clock time in the requested timezone, normalized
duration seconds, distance metres, calories, and the source field used for
each normalized metric. It does not relabel `end_time` as a start timestamp.

An opt-in `--mapping-list` renders one compact line per group without exposing
titles, notes, coordinates, device/user identifiers, URLs, or credentials.
Unknown types remain unknown until the operator matches the representative
record in the Zepp app; no new sport mappings were inferred or hard-coded.
