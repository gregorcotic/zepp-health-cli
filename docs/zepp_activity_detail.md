# Zepp native activity detail discovery

## Evidence boundary

The current repository and its configured upstream contain only:

```text
GET /v1/sport/run/history.json
```

No current or removed local method requests workout detail by `trackid`.
However, two public reverse-engineering implementations independently document
and use:

```text
GET /v1/sport/run/detail.json
query: trackid, source
header: apptoken
```

The strongest code evidence is
[Mi-Fit-and-Zepp-workout-exporter](https://github.com/rolandsz/Mi-Fit-and-Zepp-workout-exporter/blob/master/src/api.py),
whose `get_workout_detail` passes the history summary's `trackid` and `source`.
Its parser decodes the returned compact strings into route, altitude, HR, and
cadence points. An independent
[Amazfit cloud backup walkthrough](https://christianott.ch/post/2022-10-26_amazfit_cloud/)
reports the same endpoint and parameters.

Evidence level is therefore `OBSERVED_IN_PUBLIC_IMPLEMENTATION`, not yet
`PROVEN_IN_CURRENT_ACCOUNT`.

## Observed response contract

The public model includes:

```text
longitude_latitude
time
altitude
accuracy
gait
pace
pause
spo2
flag
kilo_pace
mile_pace
heart_rate
speed
bearing
distance
lap
air_pressure_altitude
course
correct_altitude
stroke_speed
cadence
daily_performance_info
rope_skipping_frequency
weather_info
coaching_segment
golf_swing_rt_data
power_meter
```

Most are semicolon-delimited records with comma-delimited components. Public
parser evidence treats `time`, coordinates, and HR as delta-encoded series,
and altitude as `/100` metres. Those transformations remain candidates until
the current account is probed and checked against matching summaries.

No public model field establishes Workout Notes, structured strength
exercises, muscle regions, or a native FIT/TCX/GPX download endpoint.
`coaching_segment`, `lap`, `stroke_speed`, and `power_meter` are promising
containers whose actual current structures remain unknown.

## Track identifiers

Production `trackid` values align with Unix epoch seconds and the public
exporter uses them as workout start timestamps and stable detail lookup keys.
This supports both meanings for the observed contract:

- Unix-like workout timestamp;
- per-activity identifier reused by history and detail.

`parent_trackid` appears in history schemas, but neither the local client nor
the public detail implementation uses it. Its relationship to child workouts,
intervals, multisport, or Coach stages remains unknown.

## Privacy-safe diagnostic

```bash
python3 zepp_health.py diagnose-activity-detail \
  --from-date YYYY-MM-DD \
  --to-date YYYY-MM-DD \
  --timezone Europe/Ljubljana \
  --track-id TRACKID \
  --json
```

The command first finds the exact record through bounded history, obtains its
`source` internally, and then performs one detail request. It does not print
the source value. Output contains:

- endpoint/evidence classification;
- sport mapping and track-ID match;
- response field/shape information;
- stream presence, counts, and component widths;
- GPS point count and time-offset coverage;
- altitude raw range plus explicitly candidate `/100` conversion;
- HR count/range decoded under the public delta model;
- note-like field paths and text lengths only;
- unknown field names.

Coordinates, routes, text values, source values, credentials, headers,
cookies, URLs, and user/device identifiers are suppressed.

## History, detail, and stream distinction

| Data class | History summary | Detail contract | Raw sample stream |
|---|---|---|---|
| identity/sport/duration/distance | production-proven | track ID returned | N/A |
| GPS | location metadata only | `longitude_latitude` observed publicly | production probe pending |
| altitude | summary fields | `altitude`, `air_pressure_altitude`, `correct_altitude` | production probe pending |
| HR | summary min/avg/max | `heart_rate` | production probe pending |
| speed/pace | summary varies | `speed`, `pace`, kilometre/mile pace | production probe pending |
| cadence/power | summary sensor-dependent | `gait`, `cadence`, `power_meter` | production probe pending |
| laps/intervals | limited summary | `lap`, `coaching_segment` | semantics pending |
| swim detail | pool/stroke summaries | `stroke_speed`, `lap` candidates | semantics pending |
| strength detail | summary containers mostly empty | no proven exercise/muscle field | not discovered |
| Workout Notes | known in app, absent from history | diagnostic searches unknown fields | not discovered |
| native file export | none | no file/download contract found | not discovered |

Basic coach ingestion remains supported by history alone. Detail data is an
advanced-analysis enhancement.

## Required production probes

Use exact one-day records and never dump raw responses:

```bash
cd /opt/zepp-health-cli
source .venv/bin/activate

# Hiking / Ojstrica
python3 zepp_health.py diagnose-activity-detail \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana --track-id 1784948221 --json

# Cross-training
python3 zepp_health.py diagnose-activity-detail \
  --from-date 2026-07-22 --to-date 2026-07-22 \
  --timezone Europe/Ljubljana --track-id 1784739852 --json

# Pool Swim
python3 zepp_health.py diagnose-activity-detail \
  --from-date 2026-05-31 --to-date 2026-05-31 \
  --timezone Europe/Ljubljana --track-id 1780212041 --json

# Open Water Swim
python3 zepp_health.py diagnose-activity-detail \
  --from-date 2026-07-07 --to-date 2026-07-07 \
  --timezone Europe/Ljubljana --track-id 1783403679 --json

# Gravel Cycling
python3 zepp_health.py diagnose-activity-detail \
  --from-date 2026-07-11 --to-date 2026-07-11 \
  --timezone Europe/Ljubljana --track-id 1783747838 --json

# Ski
python3 zepp_health.py diagnose-activity-detail \
  --from-date 2026-01-02 --to-date 2026-01-02 \
  --timezone Europe/Ljubljana --track-id 1767339463 --json
```

If the detail endpoint returns HTTP failure or an unrecognized wrapper for the
current Zepp app/account, do not guess another URL. Capture official-app
traffic while opening the activity map, charts, laps/sets, notes, Coach stages,
and export screen. Retain only path, method, query-key names, response field
names/counts, selected track-ID match, and sanitized error/status evidence.
