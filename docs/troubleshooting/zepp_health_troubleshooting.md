# Zepp health troubleshooting

## Synchronization

Run `db-status --json` and `db-check --json` first. `sync-db` isolates domains;
an unavailable LifeLoad or sleep source does not erase prior rows. Repeated
synchronization should produce `unchanged` counts rather than duplicates.

For scheduled operation, inspect `systemctl status zepp-health-sync.service`,
`systemctl status zepp-health-sync.timer`, and
`journalctl -u zepp-health-sync.service`. Run `sync-health --json` for an
automation-friendly result. Exit code 1 means warning, 2 means failed health
state, and 3 means configuration/database error.

For C019 scheduling/context issues, also inspect:

```bash
systemctl cat zepp-health-sync.service
systemctl cat zepp-health-sync.timer
systemctl show zepp-health-sync.service -p OnSuccess -p ExecMainStatus
journalctl -u coach-context-generate.service
systemctl list-timers --all coach-context-generate.timer
```

A recent successful sync with `morning_data_status=pending` is valid: sync
freshness is current while every stored recovery domain still ends before
today. `partial` means at least one, but not every, supported recovery domain
contains today. Zepp domains arriving at different times is expected. Before
06:30 Europe/Ljubljana, `morning_expectation=before_first_morning_sync` avoids
claiming a stale/error state.

On Zepp failure, `OnSuccess` must not invoke the generator. Do not touch
`general-context.json` to make it look fresh. Preserve the last valid file,
leave `coach-context-generate.timer` enabled, and diagnose Zepp independently
from the gateway and Strava.

If a run is skipped, the journal says that the `flock` lock is held. The lock
is kernel-owned and is released when the owning process exits; do not delete a
lock file while synchronization may still be running. Check the process and
journal first. The scheduled wrapper exits 75 for this no-op so systemd does
not treat it as a successful sync or invoke the context generator.

## SQLite files

With WAL mode, keep `.db`, `.db-wal`, and `.db-shm` together while diagnosing.
Use `db-backup` instead of copying only the main database file. Validate a
backup with `db-restore` into a new path and then `db-check`.

## Credentials and privacy

Never paste `config.json`, authorization headers, cookies, tokens or complete
raw API payloads into an issue. The persistence layer removes known credential
and user-id keys before storing raw JSON, but the database still contains
private health data and must remain local.

## API limitations

Native Zepp fields are preserved as returned. Unknown readiness statuses,
sentinel-like values such as 255, and insight codes are not interpreted.
Missing domains are documented as unavailable rather than replaced with local
health calculations.

## Wake BioCharge forensic procedure

Status: closed in C018.3. The production symptom was a current Wake BioCharge
visible in Zepp while SQLite and factual freshness remained on the prior day.
Production proved that the parent UTC timestamp was an envelope date, while
`value.startTime + sample.s` with the observed prefixed timezone identified the
local wake day.

The current path is:

`GET /v2/users/me/events` with `eventType=Charge`,
`subType=wake_data`, millisecond `from`/`to`, `limit`, and `reverse=1`
→ `_event_records`
→ `_record_payload`
→ `_record_date` / `_normalize_sample_records`
→ `normalize_wake_data`
→ `Database.store_domain_with_raw`
→ `wake_energy`
→ `Database.factual_freshness`
→ `domain_data_freshness.wake_energy` and `morning_data_status`.

The request is a single GET; there is no pagination/cursor loop. The client adds
an opaque `r` request UUID. Timezone is sent as the normal Zepp request header,
not as an events query parameter. The diagnostic converts inclusive local
calendar bounds to a half-open UTC epoch-millisecond range.

Generic event date precedence remains:

1. The merged payload checks `date`, then `day`, then `dayId`. Top-level record
   fields overwrite same-named fields inside `value`.
2. Otherwise it checks `timestamp`, `time`, `ts`, `startTime`, then
   `start_time`.
3. The timestamp is always divided by 1000, so epoch seconds are currently
   interpreted as milliseconds.
4. `timezone`, `timeZone`, then `tz` selects an IANA zone. Missing or invalid
   timezone means UTC. `utcOffset` is not used.
5. Generic sample domains inherit the parent date. `sample_timestamp` is
   `parent startTime + sample s` when both are numeric, otherwise the parent
   timestamp.

Wake data now has a narrow, evidence-based override:

1. explicit sample `date`, `day`, or `dayId`;
2. explicit sample `timestamp`, `time`, or `startTime` in an explicit timezone;
3. `value.startTime + sample s` in an explicit timezone;
4. the existing resolved parent date.

The production timezone was `1,Europe/Ljubljana`; the numeric prefix is Zepp
metadata and the suffix is the IANA timezone. This parsing applies only to wake
data. Production timestamps were milliseconds and no `utcOffset` was present,
so epoch-unit and offset semantics were not changed.

Supported shapes are `value.samples` as a list or object, plus direct wake
fields in the merged `value`/record when samples are absent or empty. Direct
record fields work because the record is merged over `value`. Multiple samples
produce multiple rows. Unsupported outer wrappers (anything other than the
recursive `items`, `data`, `records`, or `result` shapes), non-dictionary
samples, and records with neither supported samples nor direct known wake fields
produce zero rows.

At the July DST boundary, `2026-07-24 00:30 Europe/Ljubljana` is
`2026-07-23 22:30 UTC`. Characterization tests show:

- explicit `date=2026-07-24` → `event_date=2026-07-24`;
- the corresponding epoch milliseconds plus `timezone=Europe/Ljubljana` →
  `2026-07-24`;
- the same timestamp without timezone → `2026-07-23`;
- a July 23 parent with a July 24 wake sample → `2026-07-23`;
- the equivalent epoch-seconds value is treated as milliseconds and resolves
  near January 1970.

### C018.2 production evidence and result

The July 23–26 capture contained three raw records and three normalized rows;
the API was not empty and the parser extracted every sample. SQLite retained
exactly those three normalized rows. This excludes Cases A, D, and E.

Each record showed the same one-day pattern. The latest example was:

- parent `timestamp=1784937600000` → July 25 UTC;
- `value.startTime=1785016800000`;
- `timeZone=1,Europe/Ljubljana`;
- sample `s=0`;
- local wake clock → July 26 00:00 Europe/Ljubljana;
- old normalized/stored date → July 25;
- stored at `2026-07-26T06:30:05Z`.

At 09:03 Europe/Ljubljana on July 24, the Zepp app showed Wake BioCharge 65
and Current BioCharge 55. The matching API wake sample had `wakeCharge=65`,
parent timestamp July 23, and local `startTime` July 24. This proves both the
wake-day date and that current Charge is a separate value.

The root cause is therefore Case C: the generic parent-date resolver ignored
the wake clock and could not parse Zepp's prefixed timezone. After the fix the
same raw record normalizes to July 26. The value remains Wake BioCharge at
waking; current/intraday BioCharge is not substituted.

### Completed targeted production repair

Stop the timer so corrected rows cannot be inserted before the key move. Back
up first, verify the three exact source rows, then move their derived identities
in one transaction. This preserves the rows, values, `updated_at`, raw payloads,
and sync history:

```bash
cd /opt/zepp-health-cli
sudo systemctl stop zepp-health-sync.timer

.venv/bin/python zepp_health.py db-backup \
  --db /opt/zepp-health-cli/data/zepp_health.db \
  --output /opt/zepp-health-cli/backups/pre-c0182-wake-energy.db --json

sqlite3 -readonly -json /opt/zepp-health-cli/data/zepp_health.db \
  "SELECT record_key,event_date,start_time_ms,offset_ms,bio_charge_wake,updated_at
     FROM wake_energy
    WHERE record_key IN (
      'aff7e57568369f17443ea4073fefaf710f6701e02b681e65ff145f21cb32fa34',
      '7f2a6540a4e80714cfbaceaae54ee9ad11949ed1ad30a04938674f3ec5946da5',
      'eca0e86c28463b809ea1339953185f1686a11402332856f6f1fc4e667b7cb395'
    )
    ORDER BY event_date;"

sqlite3 /opt/zepp-health-cli/data/zepp_health.db <<'SQL'
.bail on
BEGIN IMMEDIATE;
CREATE TEMP TABLE repair_guard(n INTEGER CHECK(n=1));
UPDATE wake_energy
   SET record_key='1e49c413d1e661abd6c97ae8ccccdb64b0f443c6ecf34ed62a675241f108bb19',
       event_date='2026-07-24'
 WHERE record_key='aff7e57568369f17443ea4073fefaf710f6701e02b681e65ff145f21cb32fa34'
   AND event_date='2026-07-23' AND start_time_ms=1784844000000 AND offset_ms=0;
INSERT INTO repair_guard VALUES(changes());
UPDATE wake_energy
   SET record_key='08ad258b2d1bcd34a010035f93a7f9240ad0bc763c9f663fb83cc26f8220a1aa',
       event_date='2026-07-25'
 WHERE record_key='7f2a6540a4e80714cfbaceaae54ee9ad11949ed1ad30a04938674f3ec5946da5'
   AND event_date='2026-07-24' AND start_time_ms=1784930400000 AND offset_ms=0;
INSERT INTO repair_guard VALUES(changes());
UPDATE wake_energy
   SET record_key='48facbdc1c79c7cd2d2b5afd59dcbb5b78ff130c23b76a04d70a9c6567342e65',
       event_date='2026-07-26'
 WHERE record_key='eca0e86c28463b809ea1339953185f1686a11402332856f6f1fc4e667b7cb395'
   AND event_date='2026-07-25' AND start_time_ms=1785016800000 AND offset_ms=0;
INSERT INTO repair_guard VALUES(changes());
COMMIT;
SQL

.venv/bin/python zepp_health.py sync-db \
  --days 7 --db /opt/zepp-health-cli/data/zepp_health.db --json
sudo systemctl start zepp-health-sync.timer
```

If the read-only precheck does not return exactly the three documented rows,
stop and restore nothing; investigate before opening a write transaction. The
`.bail on` plus the temporary `CHECK(n=1)` guard aborts the sqlite3 process if
an UPDATE does not affect exactly one row, leaving the transaction uncommitted.
Do not run this repair on another database or capture.

The production repair completed successfully. A subsequent seven-day sync
retrieved six wake rows and reported `inserted=0`, `updated=0`, and
`unchanged=6`; this is the expected idempotent result and confirms that no
duplicate corrected identities were introduced.

Validate afterward:

```bash
.venv/bin/python zepp_health.py diagnose-wake-energy \
  --from-date 2026-07-23 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --db /opt/zepp-health-cli/data/zepp_health.db --json \
  | jq '{raw_record_count,normalized_row_count,
         dates:[.records[] |
           {generic_parent_date,effective_timezone,resolved_event_date,
            normalized:[.samples[].normalized_wake_energy_event_date]}],
         sqlite:.sqlite.wake_energy_rows}'

.venv/bin/python zepp_health.py sync-health \
  --db /opt/zepp-health-cli/data/zepp_health.db --json \
  | jq '.factual_freshness |
        {sync_freshness,
         wake_energy:.domain_data_freshness.wake_energy,
         morning_data_status}'

.venv/bin/python zepp_health.py db-check \
  --db /opt/zepp-health-cli/data/zepp_health.db --json
```

SQLite uses logical key SHA-256 of
`("wake", event_date, start_time, s)`. A changed value with that same identity
updates the existing row and `updated_at`; distinct offsets/start times remain
separate rows. Thus a 70→74 revision is supported only when date/start/offset
stay stable. `source_json` stores the sanitized sample, but there is no separate
creation timestamp or raw source-arrival timestamp beyond `updated_at` and the
matching `raw_payloads.retrieved_at`. `daily-status --from-db` iterates ordered
rows and the last row for a date wins in its daily projection.

Ordinary sync status distinguishes request failure (`error`) from successful
normalization with rows (`ok`) and zero rows (`empty`). It does not expose raw
record count or returned dates, so these two cases are indistinguishable there:
an actually empty response, and a nonempty raw response whose shape normalizes
to zero. A successful response containing only yesterday's normalized row is
`ok`; factual freshness independently keeps yesterday as `latest_date`.

Morning freshness requires `hrv`, `readiness`,
`sleep_related_readiness`, and `wake_energy` to all have today's stored date.
If HRV/readiness/sleep-related readiness have today while wake energy ends
yesterday, at least one but not all required domains contain today, so the
mechanical result is `partial`. Do not substitute current Charge, readiness,
HRV, or yesterday's Wake Energy.

### Safe Ubuntu commands (prepare only; run manually)

Run these from `/opt/zepp-health-cli` as the normal runtime user. They contact
only the wake endpoint or read the local database and do not dump raw payloads:

```bash
cd /opt/zepp-health-cli

.venv/bin/python zepp_health.py diagnose-wake-energy \
  --from-date 2026-07-23 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana \
  --db /opt/zepp-health-cli/data/zepp_health.db --json \
  | jq '{event_contract,request,raw_record_count,normalized_row_count,records,sqlite}'

.venv/bin/python zepp_health.py wake-energy --days 7 --json \
  | jq '[.[] | select(.date >= "2026-07-23" and .date <= "2026-07-25") |
    {date,timestamp,start_time,sample_timestamp,s,bioChargeWake,wakeCharge,
     physicalWake,mentalWake,dailyFitnessScore,stressFitnessScore,exertionScore}]'

sqlite3 -readonly -json /opt/zepp-health-cli/data/zepp_health.db \
  "SELECT event_date,timestamp_ms,start_time_ms,sample_timestamp_ms,offset_ms,
          bio_charge_wake,wake_charge,physical_wake,mental_wake,
          daily_fitness_score,stress_fitness_score,exertion_score,updated_at
     FROM wake_energy
    WHERE event_date BETWEEN '2026-07-23' AND '2026-07-25'
    ORDER BY event_date,COALESCE(sample_timestamp_ms,timestamp_ms),record_key;"

sqlite3 -readonly -json /opt/zepp-health-cli/data/zepp_health.db \
  "SELECT r.started_at,r.finished_at,r.status AS run_status,d.status AS wake_status,
          d.records_retrieved,d.inserted_count,d.updated_count,d.unchanged_count,d.error
     FROM sync_runs AS r
     JOIN sync_run_domains AS d ON d.sync_run_id=r.id
    WHERE d.domain='wake_energy'
    ORDER BY r.id DESC LIMIT 12;"

.venv/bin/python zepp_health.py sync-health \
  --db /opt/zepp-health-cli/data/zepp_health.db --json \
  | jq '{factual_freshness:
    {sync_freshness:.factual_freshness.sync_freshness,
     wake_energy:.factual_freshness.domain_data_freshness.wake_energy,
     morning_data_status:.factual_freshness.morning_data_status}}'
```

Do not paste the database, config, headers, or the undigested events response
into an issue. The diagnostic prints allow-listed wake/date values and unknown
field names only.

At approximately the same time as the commands, record a timestamp and:

1. Wake BioCharge on the Amazfit watch.
2. Wake BioCharge in the Zepp mobile app.
3. Current BioCharge in the Zepp app, if it is a distinct number.
4. `raw_record_count`, record dates/timestamps/timezones, wake samples, and
   `normalized_row_count` from `diagnose-wake-energy`.
5. The SQLite `wake_energy` rows and factual freshness result.

Interpretation:

- no current-day raw wake record → candidate Case A (cloud publication delay);
- app/watch current value plus consistently absent wake API data, with another
  verified source → Case B;
- raw current-day record but normalized previous day → Case C;
- raw current-day wake fields but zero/missing normalized rows → Case D;
- normalized today but missing/wrong SQLite row → Case E;
- proven different publication/semantic date → Case F;
- otherwise Case G.

### C018.3 production closeout

The repaired production rows are:

- July 23: `wakeCharge=72`;
- July 24: `wakeCharge=65`;
- July 25: `wakeCharge=41`;
- July 26: `wakeCharge=65`.

For July 24–26, the generic parent date remained the preceding day while both
the wake resolver and stored `event_date` used the correct local day. Source
values and timestamps remained intact. `db-check` returned
`integrity_check=ok`, no foreign-key errors, WAL journal mode, and schema
version 3. Raw payload and sync-history tables remained present and valid.

`sync-health` reported a successful synchronization today,
`wake_energy.latest_date=2026-07-26`, Wake Energy coverage `today`, all other
morning domains at today, and `morning_data_status=complete`. This follows from
correct data dates; the morning rule was not relaxed. If the API later lacks a
current-day wake record, Wake Energy may still be partial after a successful
sync.

The context-generation service was refreshed after repair. C018.2 did not add
or rename any factual field: consumers still read
`domain_data_freshness.wake_energy.latest_date` and
`morning_data_status`. Therefore C018 requires no coach-data-bridge,
coach-context-gateway, GPT Action, OpenAPI, token, URL, or schema-import change.
Existing C017 monorepo migration work remains the place to mirror the standalone
application and its already-defined factual contract.

Keep the pre-C018.2 backup until the normal retention period expires. If a
post-repair integrity or value check ever fails, stop synchronization and use
the documented `db-restore` workflow to a separate path first; do not overwrite
the production database without a separately reviewed recovery action.


## Same raw payload but stale normalized columns

### Symptom

A parser/normalizer is corrected, but a subsequent synchronization reports:

`unchanged`

and previously stored normalized columns remain stale.

### Root cause

Older generic UPSERT behavior classified a record as unchanged when
`source_json` matched, without checking whether normalization now produced
different persisted columns.

This appeared during PHN Batch 1B after the native relationship

`phn/record.phn_plan_id == phn/training_plan.timestamp`

was production-proven and the training-plan normalizer began deriving the
previously missing `phn_plan_id`.

The raw Zepp payload had not changed, so the old UPSERT skipped the corrected
normalized value.

### Fix

Generic domain persistence now compares both raw source JSON and every
normalized persisted column.

Identical raw evidence with corrected normalized output is classified as
`updated`.

### Lesson

Never use raw-payload equality alone as the definition of canonical database
equality. Historical native evidence must remain re-normalizable after parser
improvements.


## Charge day appears to start at 02:00

### Symptom

`Charge/real_data` appears to begin at 02:00 when timestamps are displayed in
Europe/Ljubljana during summer.

### Root cause

The real_data bucket is anchored at 00:00 UTC.

During CEST:

00:00 UTC = 02:00 Europe/Ljubljana

This is a transport/calendar bucket boundary, not a physiological start of day.

### Fix / rule

Keep the UTC real_data bucket identity separate from local-calendar semantics.

For `Charge/summary` and `Charge/wake_data`, local-day attribution instead
comes from the local date represented by `value.startTime`.

Do not use event.timestamp as a universal Charge day key.

