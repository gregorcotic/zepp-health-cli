# Native Zepp activity storage

Status: Z001.11 relational persistence and bounded incremental synchronization.

## Database decision

Activities use the existing Zepp SQLite database. This keeps one additive
schema-version chain, WAL policy, integrity check, backup/restore procedure,
raw-payload deduplication, and operational path. Activity synchronization and
freshness remain separate from health `sync_runs`; sharing the file does not
conflate the two domains.

A dedicated activity file would duplicate migrations and backups while making
one factual Zepp snapshot harder to repair. Stream growth is isolated in
activity tables, so a future split remains possible without changing the
canonical boundary.

Schema version 4 adds:

```text
activities
activity_summary_metrics
activity_streams
activity_samples
activity_laps
activity_notes
activity_quality_flags
activity_provenance
activity_sync_runs
```

`track_id` is the native activity identity. Dependent rows use foreign keys
with cascade deletion. A history/detail/canonical fingerprint triple detects
material changes without assuming activities are immutable.

## Atomic persistence

`Database.store_canonical_activity()` uses one transaction for:

1. deduplicated sanitized history/detail raw payload references;
2. the activity identity/time row;
3. summary metric envelopes;
4. stream metadata and batch-inserted samples;
5. structural lap/swim records;
6. private Workout Notes;
7. quality flags and provenance.

Changed streams are fully replaced inside that transaction. A failure rolls
back to the previous complete version; mixed old/new detail cannot survive.
Unchanged fingerprints only advance `last_synced_at`.

Coordinates and Workout Notes are intentionally stored locally. Raw payload
sanitization removes credentials and user/device/account identifiers. Normal
status and inspection never return coordinate, raw-sample, source, or note
text values.

## Incremental policy

```bash
python3 zepp_health.py sync-activities --days 7 \
  --db data/zepp_health.db --json
```

Explicit bounds are also supported:

```bash
python3 zepp_health.py sync-activities \
  --from-date YYYY-MM-DD --to-date YYYY-MM-DD \
  --timezone Europe/Ljubljana \
  --max-activities 50 \
  --db data/zepp_health.db --json
```

Policy:

- new history identity: fetch detail and insert;
- changed sanitized history fingerprint: fetch detail and atomically update;
- incomplete stored detail: fetch detail;
- unchanged complete activity: skip detail;
- `--refresh-details`: refetch unchanged detail to discover edited Notes or
  other detail-only changes.

The request is date-bounded and one page only. `data.next=-1` is the proven
terminal cursor. Any other/missing cursor, a hard-limit truncation, or an
individual detail failure produces `partial`; no guessed pagination occurs.
A successful terminal response with zero activities is `ok` and advances
activity sync freshness.

Each run records attempted/successful time, requested dates, counts, detail
fetch outcomes, cursor, and sanitized failure categories. Activity freshness
is independent of health freshness.

## Commands and queries

```bash
python3 zepp_health.py activity-status \
  --db data/zepp_health.db --json

python3 zepp_health.py inspect-activity \
  --track-id TRACKID --db data/zepp_health.db --json
```

Local explicit Notes opt-in:

```bash
python3 zepp_health.py inspect-activity \
  --track-id TRACKID --include-notes \
  --db data/zepp_health.db --json
```

There is no coordinate-output option. The database API supports bounded
queries by date, sport family, native type, and exact sport mode. Indexes cover
activity date, sport/date, type/mode/date, stream ownership, quality flags,
and sync completion.

## Stored semantics

- Open Water altitude `-2000000` persists as
  `SENTINEL_UNAVAILABLE`; normalized sample values remain null.
- Gravel without a power meter persists
  `SUPPORTED_BUT_NOT_RECORDED`, not an error.
- Pool Swim and Cross-training GPS persist `NOT_APPLICABLE`.
- Ski vertical descent persists separately; lift ascent cannot become athlete
  climbing load.
- Pool laps and unresolved speed/pace/cadence/power records retain order and
  raw structural components without guessed field names.

Ojstrica's reported ascent, native GPS, altitude, and HR are now retrievable
offline for the future elevation validator.

## Storage growth

SQLite batches samples with `executemany`; it never commits once per sample.
The sample primary key `(stream_id, sample_index)` already supports stream
retrieval, so no additional high-volume sample indexes are added.

An outdoor activity with roughly 100,000 combined GPS/altitude/HR records can
plausibly consume roughly 10–30 MB after relational rows, indexes, and one
deduplicated forensic detail payload. Actual size depends heavily on encoded
stream length and SQLite page utilization. Hundreds of dense activities may
therefore grow into multiple gigabytes. Measure production growth before
adding compression or retention policy; no useful streams are discarded now.

## Safe production migration plan

Do not run during development. On Ubuntu:

```bash
cd /opt/zepp-health-cli
source .venv/bin/activate

python3 zepp_health.py db-backup \
  --db /opt/zepp-health-cli/data/zepp_health.db \
  --output /opt/zepp-health-cli/backups/zepp_health-pre-z00111.db \
  --json

git pull
python3 -m unittest discover -v
python3 -m compileall -q .
git diff --check

# Opening through Database performs the idempotent v3 -> v4 migration.
python3 zepp_health.py activity-status \
  --db /opt/zepp-health-cli/data/zepp_health.db --json

python3 zepp_health.py db-check \
  --db /opt/zepp-health-cli/data/zepp_health.db --json

# Small bounded first synchronization.
python3 zepp_health.py sync-activities \
  --days 7 --timezone Europe/Ljubljana --max-activities 20 \
  --db /opt/zepp-health-cli/data/zepp_health.db --json

python3 zepp_health.py activity-status \
  --db /opt/zepp-health-cli/data/zepp_health.db --json

python3 zepp_health.py inspect-activity \
  --track-id TRACKID \
  --db /opt/zepp-health-cli/data/zepp_health.db --json

python3 zepp_health.py db-check \
  --db /opt/zepp-health-cli/data/zepp_health.db --json
```

Replace the sample dates and `TRACKID` only with operator-verified values.
Do not use `--include-notes` in shared logs.

Rollback restores the entire pre-migration factual snapshot:

```bash
sudo systemctl stop zepp-health-sync.timer
sudo systemctl stop zepp-health-sync.service

python3 zepp_health.py db-restore \
  --input /opt/zepp-health-cli/backups/zepp_health-pre-z00111.db \
  --db /opt/zepp-health-cli/data/zepp_health.db \
  --overwrite --json

python3 zepp_health.py db-check \
  --db /opt/zepp-health-cli/data/zepp_health.db --json

sudo systemctl start zepp-health-sync.timer
```

The repository checkout can be returned to the previously deployed commit
through the operator's normal release procedure. No activity migration deletes
or rewrites health rows.
