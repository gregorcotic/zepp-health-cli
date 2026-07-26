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

Current date precedence is exact and intentionally unchanged:

1. The merged payload checks `date`, then `day`, then `dayId`. Top-level record
   fields overwrite same-named fields inside `value`.
2. Otherwise it checks `timestamp`, `time`, `ts`, `startTime`, then
   `start_time`.
3. The timestamp is always divided by 1000, so epoch seconds are currently
   interpreted as milliseconds.
4. `timezone`, `timeZone`, then `tz` selects an IANA zone. Missing or invalid
   timezone means UTC. `utcOffset` is not used.
5. A sample's own date or timestamp never selects `event_date`. Samples inherit
   the parent date. `sample_timestamp` is `parent startTime + sample s` when
   both are numeric, otherwise the parent timestamp.

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
- otherwise Case G. Local characterization alone is currently Case G.
