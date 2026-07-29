# SQLite persistence

`zepp-health-cli` stores retrieved native Zepp data locally for later use by
Coach AI Agent. The database is not a web service and is never exposed by this
project.

## Path precedence

The database path is resolved in this order:

1. CLI `--db PATH` (before or after the relevant subcommand)
2. `db_path` in the selected `config.json`
3. `ZEPP_DB_PATH`
4. `data/zepp_health.db`

Relative paths are relative to the current working directory. On Ubuntu, a
recommended local path is `/opt/zepp-health-cli/data/zepp_health.db`; the
runtime user must own or be able to write the `data/` directory.

## Commands

```bash
python3 zepp_health.py sync-db --days 30
python3 zepp_health.py sync-db --days 30 --db /opt/zepp-health-cli/data/zepp_health.db --json
python3 zepp_health.py db-status
python3 zepp_health.py daily-status --days 14 --from-db
python3 zepp_health.py stress --days 7 --db data/zepp_health.db
python3 zepp_health.py stress --days 1 --samples --db data/zepp_health.db --json
python3 zepp_health.py food --days 7 --db data/zepp_health.db --json
python3 zepp_health.py sync-activities --days 7 --db data/zepp_health.db
python3 zepp_health.py activity-status --db data/zepp_health.db
```

`sync-db` initializes the database, fetches each native domain independently,
and records inserted, updated, unchanged, empty, and failed domains. A failed
domain does not roll back successful domains from the same run. Each domain's
normalized records and raw payload insertion use a SQLite transaction.

## Schema

- `schema_meta`: schema metadata; SQLite `PRAGMA user_version` is the migration version.
- `hrv_samples`: Zepp HRV/RMSSD-like samples. `hrv_daily` is reserved for explicit Zepp daily HRV values; no local daily aggregate is inserted.
- `wake_energy`: `Charge/wake_data` samples.
- `exertion_records`: `exertion/algo_result` values, including raw `recoveryFactor`, ATL, CTL, and TSB.
- `readiness_records`: `readiness/watch_score` records, including raw status and sentinel values.
- `sleep_related_readiness`: readiness-derived sleep fields; this is not a complete sleep summary.
- `charge_records`: `Charge/real_data` samples.
- `insight_records`: normalized `Charge/insight_data` samples and unknown codes.
- `raw_payloads`: deduplicated sanitized JSON responses for future reverse engineering.
- `sync_runs` and `sync_run_domains`: synchronization provenance and outcomes.
- `stress_daily_records`: one factual native Stress aggregate per local
  `event_date`, including native min/max/avg, category proportions, and count.
- `stress_samples`: only native sparse Stress timeline measurements, keyed by
  their epoch-millisecond timestamp; missing intervals are not synthesized.
- `food_entries`: canonical native `Food / real_data` entries keyed by stable
  `foodLogId`, with factual meal, amount, energy, nutrient, and recognition
  fields plus sanitized internal source evidence.
- `activities` and `activity_summary_metrics`: canonical native activity
  identity/time and factual metric envelopes.
- `activity_streams` and `activity_samples`: independently sampled native GPS,
  altitude, HR, cadence, and unresolved structural streams.
- `activity_laps`, `activity_notes`, `activity_quality_flags`, and
  `activity_provenance`: ordered sport detail, private Notes, factual flags,
  and audit evidence.
- `activity_sync_runs`: activity-only attempted/successful freshness and
  bounded synchronization results.

Normalized tables use deterministic `record_key` values and UPSERT behavior.
Repeating a sync does not create duplicate logical records. Raw payloads are
deduplicated by SHA-256 of sanitized JSON.

Source timestamps are stored as epoch millisecond columns where available;
original values remain in `source_json`. No health or training metrics are
calculated locally.

## Raw payload and security policy

Raw responses are JSON text, but credential-like keys including app tokens,
authorization/cookie values, and user-id keys are removed before storage. The
database still contains personal health data and must be treated as private.
Back it up as a private file, restrict filesystem permissions, and never serve
it through a public HTTP endpoint. Git ignores `data/`, SQLite files, and WAL
sidecar files.

## Migrations and deployment

The current database schema version is 8. Future compatible schema
changes must increment the migration version and apply transactional migrations
before normal reads/writes. The database is runtime state, not source code:

```bash
cd /opt/zepp-health-cli
git pull origin main
python3 zepp_health.py sync-db --days 30 --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-status --db /opt/zepp-health-cli/data/zepp_health.db
```

`git pull` does not overwrite ignored local database files.

## Stress persistence and reads

Schema v7 stores native `all_day_stress` facts in `stress_daily_records` and
`stress_samples`. `avg_stress` is the native Zepp aggregate and is not
recomputed from samples. Numeric aggregate fields are normalized to integers
before persistence so JSON strings such as `"31"` compare idempotently.

The `stress` command reads SQLite only. Its requested daily window is based on
the `Europe/Ljubljana` local calendar. Freshness is based on the newest factual
daily `event_date`: today is `current`, an earlier date is `stale`, and no
daily row is `missing`. Sparse sample recency does not determine freshness.

Activity tables are an additive v3-to-v4 migration. They share backup and
integrity tooling with health data, but activity freshness is reported by
`activity-status`, not health `sync-health`. See
[docs/activity_storage.md](activity_storage.md).

For unattended Ubuntu synchronization, see [docs/operations.md](operations.md).
The `sync-health` command reports database integrity, synchronization age,
latest run state, lock state, and duration. It returns 0 for healthy, 1 for a
warning, 2 for a failed synchronization/database state, and 3 for a
configuration or database access error.

Its JSON also includes `factual_freshness`, computed without a schema change.
Sync freshness is the latest successful `sync_runs` timestamp. Domain
freshness is `MAX(event_date)` for each actual table, compared with the
`Europe/Ljubljana` calendar date. The schema has no confirmed native
sleep-summary table, so `sleep` is unsupported/unavailable while
`sleep_related_readiness` retains its precise existing meaning.

## Integrity, backup, and restore

Run checks without contacting Zepp:

```bash
python3 zepp_health.py db-check --db data/zepp_health.db
python3 zepp_health.py db-check --db data/zepp_health.db --json
```

The database uses WAL mode. Create backups with Python's SQLite backup API so
the main file and WAL state are captured consistently; do not copy only the
`.db` file while the database is active:

```bash
python3 zepp_health.py db-backup --output backups/zepp_health_$(date +%Y-%m-%d).db
```

Existing output is refused unless `--overwrite` is supplied explicitly.
Backups are integrity-checked before publication. Restore to a separate path:

```bash
python3 zepp_health.py db-restore --input backups/zepp_health_YYYY-MM-DD.db --db restore-test/zepp_health.db
python3 zepp_health.py db-check --db restore-test/zepp_health.db
```

Restore validates the source, refuses an existing target by default, and
compares schema version and table counts after restore. Keep backups private.
A practical manual retention policy is daily backups for 7 days, weekly
backups for 4 weeks, and monthly backups for 6 months; deletion is not
automated by this project.


## PHN / Zepp Coach persistence

SQLite schema v6 adds:

### `phn_daily_records`

Historical daily Zepp Coach state.

Important normalized fields:

- `event_date`
- `timestamp_ms`
- `phn_plan_id`
- `flag`
- `degree_of_completion`
- `degree_of_completion_week`
- sanitized `source_json`

### `phn_training_plans`

Persistent mutable Zepp Coach plan state.

The native training-plan event timestamp represents the stable plan identity.
Current-state freshness is determined from native `last_update_time`.

### UPSERT equivalence rule

A domain row is `unchanged` only when BOTH are equivalent:

- sanitized native `source_json`
- every persisted normalized column

This is intentional. Parser/normalizer improvements must be able to refresh
canonical columns from identical historical raw evidence.

Therefore:

raw unchanged + normalized unchanged
→ `unchanged`

raw unchanged + normalized changed
→ `updated`

raw changed
→ `updated`


## Charge / HybridCharge time semantics

Charge domains do not share the same calendar anchor.

### `Charge/real_data`

Stored as UTC-calendar-day buckets.

Example during CEST:

`2026-07-22 00:00 UTC`
=
`2026-07-22 02:00 Europe/Ljubljana`

The local 02:00 representation must not be treated as a physiological day
boundary.

### `Charge/summary` and `Charge/wake_data`

These use local-calendar-day semantics through `value.startTime`.

Use the local date derived from `value.startTime`.

Do not use `event.timestamp` to assign their local calendar day.

### Sentinel handling

For `Charge/real_data.total`:

`255`
= invalid/unavailable HybridCharge sentinel

Never expose 255 as a valid score.

Native `total` remains authoritative when valid; do not reconstruct it from
physical/mental components merely because the relationship is very strong.



## Food / Nutrition persistence

Schema v8 adds `food_entries`. Native `foodLogId` is the stable logical key, so
an edit updates one row and repeated identical syncs remain unchanged.

Persisted factual fields include:

- native event timestamp
- `foodLogId`
- `mealType`
- normalized meal label
- `mealName`
- `foodName`
- `measureWeight`
- `weightUnit`
- `energy`
- `carbohydrates`
- `protein`
- `fatTotal`
- `fiber`
- `servings`
- `labels`
- `emoji`
- `recognizeType`
- `recognizeSourceType`
- raw payload/provenance

mealType semantic dictionary:

1 Breakfast
2 Morning Snack
3 Lunch
4 Afternoon Snack
5 Dinner
6 Evening Snack

Food Goals should be stored separately from individual Food Log records.
I002 does not persist Goals because the current client has no proven property
read method; no write behavior was added.

The `food` CLI reads SQLite only and exposes canonical fields without
`source_json`. It reports latest known entry date and whether food is logged
today. `no_food_logged` is a factual user-entry state, not a sync failure or
device-staleness judgment.

No audited native daily nutrition summary is implemented. The CLI labels
daily totals unavailable and does not silently sum entries or invent zeros.
