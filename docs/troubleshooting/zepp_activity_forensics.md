# Zepp activity/workout forensics

Status: Z001.1 investigation. No activity source, coach contract, database, or
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

## Cloud capability matrix

The table describes the reverse-engineered cloud interface, not data shown on
the watch/app and not Zepp OS device APIs.

| Metric/capability | Zepp support | Source | Confidence | Strava currently provides |
|---|---|---|---|---|
| Sport-specific activity listing | PARTIAL | `/v1/sport/{sport}/history.json` | medium: request exists, payload unverified | YES |
| Generic all-sports history | UNKNOWN | no endpoint found | low | YES |
| Stable activity ID | UNKNOWN | query names mention TrackId only | low | YES |
| Sport type/subtype mapping | PARTIAL | four URL-segment candidates | low | YES |
| Name/custom title | UNKNOWN | no response fixture | low | YES |
| Description/notes | UNKNOWN | no response fixture | low | YES |
| Start/local time/timezone | UNKNOWN | no response fixture | low | YES |
| Duration/moving/elapsed time | UNKNOWN | no response fixture | low | YES |
| Distance | UNKNOWN | no response fixture | low | YES |
| Ascent/descent/min/max altitude | UNKNOWN | no response fixture | low | ascent |
| Calories | UNKNOWN | no response fixture | low | YES |
| Average/max/min HR | UNKNOWN | no response fixture | low | average/max |
| Workout HR stream | UNKNOWN | no response fixture | low | YES where available |
| Speed/pace/cadence/power | UNKNOWN | no response fixture | low | PARTIAL/YES by activity |
| Training load/aerobic TE/anaerobic TE/recovery/VO2max/EPOC | UNKNOWN | no response fixture | low | PARTIAL |
| GPS/altitude track | UNKNOWN | no response fixture | low | YES where available |
| Laps/splits/intervals | UNKNOWN | no response fixture | low | PARTIAL |
| Strength exercises/sets/reps/weight/rest/muscles/corrections | UNKNOWN | no response fixture | low | descriptions only in current architecture |
| Swim pool length/laps/strokes/SWOLF/intervals | UNKNOWN | no response fixture | low | PARTIAL |
| Full-history pagination/retention | UNKNOWN | no pagination implementation | low | YES within authorized history |
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

Zepp does not currently have enough proven cloud capability to replace Strava.
The largest unknown is the response itself: no production activity payload has
yet been captured in this project.
