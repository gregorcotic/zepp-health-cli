# zepp-health

A tiny, single-file Python wrapper around the **Zepp (Huami) mobile API** so you can read your own health records from the command line: skin temperature, heart rate, HRV, body battery, daily activity, training load, and more.

> **Unofficial.** Not affiliated with or endorsed by Zepp Health. The endpoints used here are reverse-engineered from network traffic of the official Zepp iOS app and may change or break without notice.

## Requirements

- Python 3.9+
- `requests`
- A Zepp account and an **HTTPS proxy capture** of the official Zepp app's network traffic, exported as **HAR** or as a JSON session export (needed to obtain `apptoken` and regional `host`; password-based API login is not supported—see below).

## Install

```bash
git clone <your fork url> zepp-health
cd zepp-health
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

There are two ways to provide credentials. The CLI reads them in this priority order:

1. `--config <path>`
2. `$ZEPP_CONFIG`
3. `./config.json`
4. `~/.config/zepp/config.json`
5. Individual env vars (`ZEPP_APP_TOKEN`, `ZEPP_USER_ID`, `ZEPP_HOST`, …) — these override values from the file.

### Option A — From an HTTPS proxy capture (easiest)

1. Use any HTTPS-decrypting proxy on your computer (mitmproxy, Proxyman, or similar). Install its CA on the phone and trust it so that traffic to `*.zepp.com` can be decrypted.
2. Open the Zepp app, scroll around, open the Health tab so the app makes some API calls.
3. Export the recorded session as **HAR** (universal format, supported by every major proxy) or as your tool's native **JSON session export**. Save it as e.g. `capture.har` (or `.json`) in the project folder.
4. Initialize the config:

   ```bash
   python3 zepp_health.py init capture.har
   ```

   This writes `./config.json` (chmod 600) with `app_token`, `user_id`, and the regional `host` extracted from the capture. Both HAR and JSON-array session exports are auto-detected.

### Option B — Manually create `config.json`

Copy `config.example.json` to `config.json` and fill in the values. The minimum fields are:

```json
{
  "app_token": "MQVBQE…",
  "user_id": "1234567890",
  "host": "api-mifit-us3.zepp.com"
}
```

You can find these in any captured request to `api-mifit*.zepp.com` (the `apptoken` header, the `/users/<id>/…` path segment, and the host).

**Why no email/password login:** Zepp’s current apps use encrypted or otherwise unsupported flows for `/v2/registrations/tokens`, and the older plaintext Huami login path is unreliable, often rate-limited (HTTP 429), and effectively deprecated for this tool. Use a proxy capture and `init` instead.

## Usage

Add **`--json`** to any data subcommand for **compact, single-line JSON** (easy to pipe to `jq`). Without it, JSON responses are **pretty-printed** (indented). `summary` defaults to plain text; use `summary --json` for a structured JSON document.

```bash
# Quick text snapshot
python3 zepp_health.py summary

# Skin temperature (delta from baseline)
python3 zepp_health.py temperature --days 14

# Heart rate samples
python3 zepp_health.py heart-rate --days 7

# Daily training load
python3 zepp_health.py sport-load --days 30

# Weight, VO2 max, workouts
python3 zepp_health.py weight --days 90
python3 zepp_health.py vo2 --days 30
python3 zepp_health.py run-history
python3 zepp_health.py run-history --sport walking   # if your app uses that segment
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-20 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana \
  --sport run --sport walking --limit 20 --json

# Safe structural comparison for one activity with/without sub-data
python3 zepp_health.py diagnose-activities \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana --sport run \
  --track-id TRACKID --compare-sub-data --json

# Bounded activity-type inventory (one page; no guessed cursor traversal)
python3 zepp_health.py diagnose-sport-coverage \
  --from-date 2026-04-28 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana --need-sub-data 1 --json

# One representative line per type/sport_mode for manual Zepp-app mapping
python3 zepp_health.py diagnose-sport-coverage \
  --from-date 2026-01-01 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana --need-sub-data 1 --mapping-list

# Audit the 14 approved representative sports without coordinates or text
python3 zepp_health.py diagnose-sport-capabilities \
  --from-date 2026-01-01 --to-date 2026-07-26 \
  --timezone Europe/Ljubljana --need-sub-data 1 --json

# Probe the public-code-backed detail contract without coordinates or text
python3 zepp_health.py diagnose-activity-detail \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana --track-id TRACKID --json

# Merge one history/detail activity into the privacy-safe canonical model
python3 zepp_health.py diagnose-canonical-activity \
  --from-date 2026-07-25 --to-date 2026-07-25 \
  --timezone Europe/Ljubljana --track-id TRACKID --json

# Persist a bounded native activity window and inspect it safely
python3 zepp_health.py sync-activities \
  --days 7 --db data/zepp_health.db --json
python3 zepp_health.py activity-status --db data/zepp_health.db --json
python3 zepp_health.py inspect-activity \
  --track-id TRACKID --db data/zepp_health.db --json

# Sleep / steps / band payload (large JSON; often base64-encoded blobs)
python3 zepp_health.py band-data --days 14
python3 zepp_health.py band-data --from-date 2026-04-01 --to-date 2026-04-18

# Manual sleep entries, profile, BP from the app
python3 zepp_health.py manual-data --type sleep
python3 zepp_health.py user-info
python3 zepp_health.py blood-pressure --bp-days 7

# User timeline (different from `events` — phone/account stream: stress, PAI, SpO₂ taps)
python3 zepp_health.py user-events --preset all-day-stress --days 7
python3 zepp_health.py user-events --preset pai --days 30
python3 zepp_health.py user-events --preset spo2 --days 1

# SpO₂ ODI/OSA windows (ISO times + timezone)
python3 zepp_health.py user-events-day --preset spo2-odi \
  --start "2026-04-18T00:00:00" --end "2026-04-18T23:59:59" --timezone Asia/Riyadh

# Per-second HR file manifest (points at COS zip blobs)
python3 zepp_health.py second-hr --days 2

# Generic events stream — raw JSON
python3 zepp_health.py events --preset daily-health
python3 zepp_health.py events --preset body-battery
python3 zepp_health.py events --preset hrv
python3 zepp_health.py events --preset hrv-rmssd
python3 zepp_health.py events --preset respiratory
python3 zepp_health.py events --preset stress
python3 zepp_health.py events --preset blood-pressure
python3 zepp_health.py events --preset emotion
python3 zepp_health.py events --preset readiness

# Or any (eventType, subType) pair
python3 zepp_health.py events --type Charge --subtype insight_data --days 7

# Normalized Zepp Charge insight data
python3 zepp_health.py insights --days 30
python3 zepp_health.py insights --days 30 --json
python3 zepp_health.py insights --days 30 --csv insights.csv

# Zepp-native recovery, energy, HRV and exertion metrics
python3 zepp_health.py hrv --days 14
python3 zepp_health.py wake-energy --days 14
python3 zepp_health.py diagnose-wake-energy \
  --from-date 2026-07-23 --to-date 2026-07-25 --json
python3 zepp_health.py exertion --days 30
python3 zepp_health.py lifeload --days 30
python3 zepp_health.py charge-data --days 7
python3 zepp_health.py daily-status --days 14
python3 zepp_health.py readiness --days 14
python3 zepp_health.py readiness --days 14 --latest-per-day
python3 zepp_health.py sleep-status --days 14
python3 zepp_health.py event-domains --days 30

# Persist native Zepp data locally in SQLite
python3 zepp_health.py sync-db --days 30
python3 zepp_health.py sync-db --days 30 --json
python3 zepp_health.py db-status
python3 zepp_health.py daily-status --days 14 --from-db
python3 zepp_health.py db-check
python3 zepp_health.py db-backup --output backups/zepp_health_$(date +%Y-%m-%d).db
python3 zepp_health.py db-restore --input backups/zepp_health_YYYY-MM-DD.db --db restore-test/zepp_health.db
python3 zepp_health.py sync-health
python3 zepp_health.py sync-health --json

# Inspect / manage config
python3 zepp_health.py config --show          # token shown masked
python3 zepp_health.py config --path          # which paths are searched
python3 zepp_health.py --config /tmp/other.json temperature
```

For Ubuntu systemd installation, locking, monitoring, journal logs, and
backup operations, see [docs/operations.md](docs/operations.md).

`sync-health --json` separates synchronization freshness from factual domain
freshness. `factual_freshness.sync_freshness` says when the last successful
Zepp synchronization completed; `domain_data_freshness` reports the latest
stored date for `hrv`, `readiness`, `sleep_related_readiness`, `wake_energy`,
and `exertion`, using `Europe/Ljubljana` for `today`. The standalone database
does not currently contain a confirmed native sleep-summary table, so `sleep`
is explicitly reported as unsupported/unavailable rather than inferred.

`morning_data_status` is mechanical, not a readiness calculation:

- `complete`: all supported morning recovery domains (HRV, readiness,
  sleep-related readiness, and wake energy) contain today.
- `partial`: at least one, but not all, of those domains contains today.
- `pending`: recovery data exists, but none of those domains contains today.
- `unavailable`: none of those recovery domains contains any stored data.

Before 06:30 local time, `morning_expectation=before_first_morning_sync` avoids
turning missing same-day data into a stale/error assertion. A successful sync
never changes `morning_data_status` by itself; stored domain dates do.

`--days N` works either before or after the subcommand (e.g. both `--days 7 heart-rate` and `heart-rate --days 7` are accepted).

Examples:

```bash
python3 zepp_health.py heart-rate --days 7 --json | jq .
python3 zepp_health.py summary --json | jq '.training_load'
python3 zepp_health.py temperature --days 14 --json
```

## Endpoints used

All data requests are **GET**s to your regional `host`, with header `apptoken: <token>`:

| Subcommand | Endpoint |
|---|---|
| `sport-load` | `GET /v2/watch/users/{id}/WatchSportStatistics/SPORT_LOAD` |
| `vo2` | `GET /v2/watch/users/{id}/WatchSportStatistics/VO2_MAX` |
| `heart-rate` | `GET /users/{id}/heartRate` |
| `weight` | `GET /users/{id}/members/-1/weightRecords` |
| `run-history` | `GET /v1/sport/{sport}/history.json` (default `sport=run`) |
| `diagnose-activities` | Same sport-specific endpoint, with sanitized field/shape reporting for a narrow local-date window |
| `diagnose-sport-coverage` | One-page bounded `/run/history` inventory grouped by `type` and `sport_mode`; unresolved pagination is explicit |
| `diagnose-sport-capabilities` | One bounded `/run/history` request audited against the 14 approved representative IDs; coordinates and activity text are omitted |
| `diagnose-activity-detail` | Bounded history lookup followed by sanitized, production-proven `GET /v1/sport/run/detail.json?trackid=…&source=…` |
| `diagnose-canonical-activity` | Deterministic history/detail merge with sport-aware statuses, provenance, stream counts, quality flags, and coordinate/note suppression |
| `sync-activities` | Bounded incremental native history/detail synchronization into relational SQLite activity tables |
| `activity-status` | Privacy-safe activity counts, coverage, date range, sport groups, quality flags, and independent sync freshness |
| `inspect-activity` | Safe stored summary/stream/lap/note metadata for one track ID; Notes text requires explicit `--include-notes` |
| `band-data` | `GET /v1/data/band_data.json` (sleep/steps sync payload; often large) |
| `manual-data` | `GET /v1/user/manualData.json` |
| `user-info` | `GET /huami.health.getUserInfo.json` |
| `blood-pressure` | `GET /users/me/bloodPressure` |
| `user-events` | `GET /users/{id}/events` (stress, PAI, SpO₂ clicks, …) |
| `user-events-day` | `GET /users/{id}/events/dateString` (e.g. SpO₂ ODI/OSA) |
| `second-hr` | `GET /users/me/fileInfo/events` (per-second HR file index) |
| `temperature`, `events` | `GET /v2/users/me/events?eventType=…&subType=…` (watch-centric stream) |
| `insights` | `GET /v2/users/me/events?eventType=Charge&subType=insight_data` |
| `hrv` | `GET /v2/users/me/events?eventType=HRVRMSSD&subType=real_data` |
| `wake-energy` | `GET /v2/users/me/events?eventType=Charge&subType=wake_data` |
| `diagnose-wake-energy` | Same wake endpoint, with a sanitized narrow-range raw/date/SQLite comparison |
| `exertion` | `GET /v2/users/me/events?eventType=exertion&subType=algo_result` |
| `lifeload` | `GET /v2/users/me/events?eventType=LifeLoad&subType=summary` |
| `charge-data` | `GET /v2/users/me/events?eventType=Charge&subType=real_data` |
| `daily-status` | Consolidates the Zepp-native endpoints above by date |
| `readiness` | `GET /v2/users/me/events?eventType=readiness&subType=watch_score` |
| `sleep-status` | Sleep-related fields from `readiness/watch_score` |
| `event-domains` | Probes known candidate `eventType/subType` pairs |

The `insights` command preserves the raw numeric `type` and `insight` values. Their
semantic mapping is not yet fully known; no labels are inferred by the CLI. Its
JSON output is normalized by day, parses `jsonExtra` when valid, and preserves the
raw string plus a parse error when it is malformed. CSV output contains one row
per sample.

## Zepp data collection scope

`zepp-health-cli` retrieves, preserves, normalizes, and displays data and
calculated metrics returned by Zepp. It is a structured Zepp data source for a
future external analysis layer that may combine Zepp data with Strava activities
and user goals. It does not calculate proprietary readiness, recovery, ATL/CTL/TSB,
or BioCharge scores, and it does not provide medical, coaching, or training
recommendations.

The `hrv` command exposes Zepp's `HRVRMSSD/real_data` samples as RMSSD-like raw
values; it does not create a daily HRV score. `daily-status` shows the latest
available HRV sample and factual sample count for each date when no explicit
daily value is present. Live validation found `readiness/watch_score` records
with fields such as `hrvScore`, `sleepHRV`, `sleepRHR`, `phyScore`, `mentScore`,
`skinTempScore`, `ahiScore`, and `rdnsScore`; the CLI preserves their original
names and does not interpret `status` or any score. Separate sleep summary
candidate domains returned no records in the validated account.

`readiness --latest-per-day` and `daily-status` deduplicate duplicate
`readiness/watch_score` records deterministically. The selected record has the
greatest `timestampUpdate`; if that field is absent, `timestamp` is used. Exact
ties keep the first record in the API response order. The plain `readiness`
command continues to expose all records, including duplicates.

`status=200` and repeated `255` values in readiness fields are preserved as raw
Zepp values. Their sentinel/status semantics are not confirmed by this project,
so the CLI does not convert them to null, unavailable, or any interpreted score.
In `daily-status`, readiness-derived sleep fields are grouped under
`sleep_related_readiness`; this is not a complete Zepp sleep summary. No native
sleep score or complete sleep summary endpoint was found for the validated
account.

## ZEPP NATIVE METRIC AVAILABILITY

The following table reflects the live account validation performed for B002.

| Metric | Source | Status |
|---|---|---|
| HRV / RMSSD-like samples | `HRVRMSSD/real_data` | Confirmed |
| Training load / ATL / CTL / TSB | `exertion/algo_result` | Confirmed |
| `recoveryFactor` | `exertion/algo_result` | Raw field; mapping unknown |
| Wake metrics | `Charge/wake_data` `value.samples[]` | Confirmed; nested sample extraction |
| Charge energy time series | `Charge/real_data` `value.samples[]` | Confirmed; raw intraday samples |
| BioCharge daily summary | `LifeLoad/summary`, BioCharge candidates | No live records |
| Readiness-related native fields | `readiness/watch_score` | Confirmed; original fields preserved |
| Sleep-related native fields | `readiness/watch_score` (`sleepHRV`, `sleepRHR`, etc.) | Confirmed; readiness-related subset only |
| Native sleep summary/score | Candidate sleep domains | Not found for validated account |
| LifeLoad | `LifeLoad/summary` | No live records |
| Insight codes | `Charge/insight_data` | Raw; numeric mappings unknown |

`Charge/wake_data` is sample-level and includes fields observed live such as
`bioChargeWake`, `wakeCharge`, `physicalWake`, `mentalWake`,
`dailyFitnessScore`, `stressFitnessScore`, and `exertionScore`, plus nested
`snapshot` and `noWearParams` objects. `Charge/real_data` is also sample-level
and includes `total`, `physical`, `mental`, `s`, and `u`; the CLI does not
aggregate it into daily averages or scores. `Charge/summary` returned a
different battery/charge series (`minCharge`, `maxCharge`, and cumulative
charging/consumption fields), not a BioCharge daily summary.

Production C018.2 evidence established that wake records use a parent
`timestamp` for the preceding UTC day while `value.startTime`, sample offset
`s`, and a Zepp-prefixed timezone such as `1,Europe/Ljubljana` identify the
actual local wake day. Wake normalization therefore resolves `event_date`
specifically from that local wake clock. Generic event date handling is
unchanged. Wake BioCharge remains distinct from current/intraday
`Charge/real_data`.

`diagnose-wake-energy` is intended for forensic use. It prints only allow-listed
wake/date values and unknown field names, never request headers, credentials,
cookies, user IDs, GPS, or unrelated event domains. Its date range is inclusive
in the configured timezone (or `Europe/Ljubljana` by default), and its SQLite
read is read-only. The normal synchronization status does not distinguish an
empty API response from a nonempty raw response that produced zero normalized
rows; this diagnostic reports both counts separately.

`event-domains` probes known candidates only. The API does not expose a
server-side exhaustive event-domain listing through the current client method.

## SQLite native data persistence

Native Zepp values can be synchronized to a private local SQLite database:

```bash
python3 zepp_health.py sync-db --days 30
python3 zepp_health.py db-status
python3 zepp_health.py daily-status --days 14 --from-db
```

The default path is `data/zepp_health.db`. Database path precedence is:
CLI `--db PATH`, `db_path` in `config.json`, `ZEPP_DB_PATH`, then the default.
Use `/opt/zepp-health-cli/data/zepp_health.db` on Ubuntu and ensure the runtime
user can write that directory. Database files and WAL sidecars are Git-ignored,
remain local, and are never exposed publicly. See [docs/database.md](docs/database.md)
for schema, idempotency, migrations, raw payload policy, backup, and deployment
details.

## A word on body temperature

Zepp/Huami expose **`skinTempCalibrated`** as a *delta from your personal baseline*, in **hundredths of a degree Celsius**, not as an absolute body temperature. Values like `+26` mean roughly **+0.26 °C** above baseline. The same payload includes `skinTempScore` (0–100) and `skinTempBaseLine`. This is consistent with how Apple Watch and most wrist sensors report skin temperature.

## Security notes

- Treat `app_token` like a password.
- `config.json` is written with permissions `600` and is gitignored.
- Proxy captures (`*.har`, `*.chlsj`, `*.saz`, `*.flow`, etc.) contain your token, user id, and sometimes your email — they are gitignored.
- If you suspect a token has leaked, log out in the Zepp app to invalidate the session, then capture a fresh session and run `init` again.

## Limitations / known issues

- **Region matters.** Use the host from your own capture (e.g. `api-mifit-us3.zepp.com`, `api-mifit-cn.zepp.com`, …). Calling the wrong regional host returns 403 or empty data.
- **Token expires.** Captured tokens have a TTL of ~30 days; after that, redo `init` with a fresh proxy capture.
- **Not every Zepp screen is covered.** New commands follow paths seen in HTTPS proxy captures. If an endpoint 404s (e.g. `run-history --sport hiking`), capture that screen in your proxy and check the real path segment.
- **Workout support is forensic, not authoritative.** The repository has a
  sport-specific history request but no production payload fixture, generic
  all-sports history implementation, pagination loop, or activity database.
  `diagnose-activities` reports structure without GPS coordinates or private
  text by default; its `--limit` limits output only, not the server response.
- **No write operations.** This wrapper only reads.

## Contributing

Issues and pull requests are welcome. For **new endpoints**, prefer a short, focused change: capture traffic from the official app (path + query), add a `ZeppClient` method if needed, wire a subcommand or preset, and update this README’s endpoint table. Do not commit secrets, `config.json`, or raw capture files.

### Contributor contract

By submitting a pull request or other contribution to this repository, you agree that your contribution is licensed under the same terms as the project: the [MIT License](LICENSE). No separate contributor agreement is required. (This is sometimes shortened informally as “contribution contract” or “CLA-lite”; it is not a formal signed contract.)

## Disclaimer

Use at your own risk. This project is not endorsed by Zepp Health, Huami, or Xiaomi. Calling private/unsupported endpoints may violate the Zepp Terms of Service in some jurisdictions; review them before running this tool. The authors accept no liability for account suspensions or data loss.

## License

[MIT](LICENSE)
