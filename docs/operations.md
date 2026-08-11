# Operations

## MacBook validation

From the project directory, with the existing private `config.json`:

```bash
python3 zepp_health.py sync-db --days 30
python3 zepp_health.py db-status
python3 zepp_health.py daily-status --days 14 --from-db
python3 zepp_health.py sync-db --days 30
python3 zepp_health.py sync-db --days 30 --json
python3 zepp_health.py db-status --json
python3 zepp_health.py daily-status --days 14 --from-db --json
python3 zepp_health.py db-check --json
python3 zepp_health.py db-backup --output backups/zepp_health_$(date +%Y-%m-%d).db --json
python3 zepp_health.py db-restore --input backups/zepp_health_YYYY-MM-DD.db --db restore-test/zepp_health.db --json
```

The second synchronization is expected to report unchanged records and no
duplicate logical rows. A failed domain is recorded while other domains remain
committed. JSON output contains provenance and counts, never credentials.

## Ubuntu deployment

The project remains the code source and the database remains runtime state:

```bash
cd /opt/zepp-health-cli
git pull origin main
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 700 /opt/zepp-health-cli/data /opt/zepp-health-cli/backups
python3 -m venv .venv  # only for initial setup, if absent
. .venv/bin/activate
python3 zepp_health.py sync-db --days 30 --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-status --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-check --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-backup --db /opt/zepp-health-cli/data/zepp_health.db --output /opt/zepp-health-cli/backups/zepp_health_$(date +%Y-%m-%d).db
python3 zepp_health.py db-restore --input /opt/zepp-health-cli/backups/zepp_health_YYYY-MM-DD.db --db /opt/zepp-health-cli/restore-test/zepp_health.db
```

### Systemd installation

The repository contract is `zepp-health-sync.service`,
`zepp-health-sync.timer`, command
`/opt/zepp-health-cli/scripts/zepp-health-sync`, and working directory
`/opt/zepp-health-cli`. The locked wrapper synchronizes both native daily
metrics (`sync-db`) and canonical activity history/detail records
(`sync-activities`) in that order. Activity synchronization uses
`ZEPP_ACTIVITY_SYNC_DAYS` when set, otherwise the same bounded
`ZEPP_SYNC_DAYS` window. The single timer remains the only scheduler; a
successful context-generation trigger therefore requires both commands to
succeed. The shipped service has no `EnvironmentFile` and uses installation
placeholders for its user/group. The exact live Ubuntu account and overrides
are not recorded here. Before C019, the repository timer used
`OnCalendar=*-*-* 00/6:00:00` (00:00, 06:00, 12:00, and 18:00 in the system
timezone). Discover the live contract before deployment:

```bash
systemctl cat zepp-health-sync.service
systemctl cat zepp-health-sync.timer
systemctl show zepp-health-sync.service \
  -p FragmentPath -p DropInPaths -p User -p Group -p WorkingDirectory \
  -p ExecStart -p EnvironmentFiles
systemctl status zepp-health-sync.timer
systemctl list-timers --all | grep -i zepp
```

C019 uses one persistent timer at exactly 02:00, 06:30, 08:30, 12:00, 18:00,
and 22:00 in `Europe/Ljubljana`. The two morning runs cover weekday wake time,
weekends, and delayed Zepp calculations; the others provide overnight
redundancy, midday catch-up, daytime exertion, and late-day coverage. This is
intentionally not hourly polling.

### Existing context generator and success chaining

C019 does not duplicate the context generator. Production is expected to have
`coach-context-generate.service` and the independent fallback
`coach-context-generate.timer`, but their definitions and output path are not
in this repository. Confirm the actual contract:

```bash
systemctl cat coach-context-generate.service
systemctl cat coach-context-generate.timer
systemctl show coach-context-generate.service \
  -p FragmentPath -p DropInPaths -p User -p Group -p WorkingDirectory \
  -p ExecStart -p EnvironmentFiles
systemctl is-enabled coach-context-generate.timer
systemctl list-timers --all coach-context-generate.timer
```

The shipped service drop-in uses
`OnSuccess=coach-context-generate.service`. Systemd starts it only after the
Zepp oneshot succeeds; failure leaves the last context available. The existing
context timer must remain enabled. Confirm syntax support before installation:

```bash
systemd --version
systemd-analyze verify \
  /opt/zepp-health-cli/deploy/systemd/zepp-health-sync.service \
  /opt/zepp-health-cli/deploy/systemd/zepp-health-sync.timer
systemd-analyze calendar '*-*-* 06:30:00 Europe/Ljubljana'
```

### C019 standalone deployment runbook

Do not replace the service template blindly: preserve the discovered live
user, group, environment, hardening, and overrides.

```bash
# After the reviewed standalone commit is pushed:
cd /opt/zepp-health-cli
git status --short
git pull --ff-only origin main

# Repeat discovery, then back up exact live unit files.
systemctl cat zepp-health-sync.service
systemctl cat zepp-health-sync.timer
systemctl cat coach-context-generate.service
systemctl cat coach-context-generate.timer
sudo install -d -m 700 /root/zepp-systemd-backup
sudo cp --archive /etc/systemd/system/zepp-health-sync.service \
  /etc/systemd/system/zepp-health-sync.timer /root/zepp-systemd-backup/

# Install the reviewed timer and additive success drop-in only.
sudo install -m 644 deploy/systemd/zepp-health-sync.timer \
  /etc/systemd/system/zepp-health-sync.timer
sudo install -d -m 755 /etc/systemd/system/zepp-health-sync.service.d
sudo install -m 644 \
  deploy/systemd/zepp-health-sync.service.d/10-context-on-success.conf \
  /etc/systemd/system/zepp-health-sync.service.d/10-context-on-success.conf
sudo systemctl daemon-reload
sudo systemctl enable --now zepp-health-sync.timer
systemctl cat zepp-health-sync.timer
systemctl cat zepp-health-sync.service
systemctl status zepp-health-sync.timer
systemctl list-timers --all | grep -i zepp
```

If discovery reports a different `FragmentPath`, back up and follow the live
installation policy rather than creating a parallel unit. If required systemd
syntax is unsupported, stop for a version-compatible declarative review; do
not add shell callbacks.

### Production validation

Record the confirmed `general-context.json` path and its mtime, then at a
low-risk time run:

```bash
systemctl show zepp-health-sync.service -p OnSuccess
systemctl is-enabled coach-context-generate.timer
sudo systemctl start zepp-health-sync.service
systemctl status zepp-health-sync.service
journalctl -u zepp-health-sync.service --since '15 minutes ago'
journalctl -u coach-context-generate.service --since '15 minutes ago'
cd /opt/zepp-health-cli
.venv/bin/python zepp_health.py sync-health \
  --db /opt/zepp-health-cli/data/zepp_health.db \
  --lock-path /opt/zepp-health-cli/run/zepp-health-sync.lock --json
stat /CONFIRMED/PATH/general-context.json
```

Verify sync success precedes the generator run and context mtime. Then use the
existing gateway/Custom GPT path to confirm the new package and factual domain
dates. Do not infer complete morning data from a recent sync alone. Validate
failure isolation in staging/test: a non-zero Zepp result must not invoke the
success trigger; the last context, gateway, Strava, and fallback context timer
must remain available.

The wrapper uses `flock` on
`/opt/zepp-health-cli/run/zepp-health-sync.lock`; the kernel releases the lock
if a process dies. Concurrent scheduled runs log a skip and exit 75 so the
post-success context trigger does not run without a synchronization.

Systemd sends wrapper and CLI output to the journal:

```bash
systemctl status zepp-health-sync.service
systemctl status zepp-health-sync.timer
journalctl -u zepp-health-sync.service
python3 zepp_health.py sync-health --db /opt/zepp-health-cli/data/zepp_health.db --lock-path /opt/zepp-health-cli/run/zepp-health-sync.lock
python3 zepp_health.py db-check --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-status --db /opt/zepp-health-cli/data/zepp_health.db
```

The service has no HTTP listener, public API, or remote database access.
Normal synchronization does not require sudo.

### Coach-platform mirroring after standalone validation

Do not migrate production while applying C019. After the standalone commit is
deployed and its real schedules are observed:

1. Copy the logical changes from `zepp_db.py`, `zepp_health.py`,
   `scripts/zepp-health-sync`, `deploy/systemd/zepp-health-sync.timer`, the
   success drop-in, tests, and documentation to
   `coach-platform/apps/zepp-health-cli`, adapting paths only where the
   monorepo layout requires it.
2. In `coach-platform/apps/coach-data-bridge`, extend the existing context
   generator (do not create another) to read the Zepp factual freshness and
   place it in `general-context.json` without renaming actual domains. Preserve
   `sync_freshness`, `domain_data_freshness`, `morning_data_status`, timezone,
   as-of time, and morning expectation as factual metadata.
3. Add bridge fixtures for complete, partial, pending, unavailable, and
   pre-06:30/DST-local dates. Missing today's data must not fail context
   generation.
4. Run each owning app's tests and the monorepo `test-all`. Record the
   standalone and monorepo commit IDs together to prevent divergence.

This bridge follow-up is required because the Custom GPT consumes
`general-context.json`; changing only the Zepp database cannot expose the new
facts through the gateway. Resume C017.3 after this mirror passes. C018 remains
planned and out of C019 scope.

## Historical backfill

Use the resumable historical mode when older context is needed:

```bash
python3 zepp_health.py backfill --days 180 --json
python3 zepp_health.py backfill --days 365 --chunk-days 30 --json
```

Backfill requests each native event domain in backwards calendar chunks. The
cursor is committed after every successful chunk in
`historical_sync_progress`; rerunning the same target resumes from that cursor.
Existing logical keys keep overlapping requests idempotent. `sync-db` remains
the incremental mode used by the systemd timer.

To measure account-specific retention and API window behavior before a large
run:

```bash
python3 zepp_health.py probe-history --probe-days 7 30 90 180 365 730 --json
```

The probe reports returned record dates, not a presumed Zepp limit. An empty
domain is recorded as empty, while request failures remain distinguishable.

The `sudo install` command is one-time directory setup; normal synchronization
and checks run as the runtime user. Do not expose the database through a web
server or network port. Remove a test restore only after checking its exact
path, for example `rm -f /opt/zepp-health-cli/restore-test/zepp_health.db`.

## Backup policy and troubleshooting

Keep daily backups for 7 days, weekly backups for 4 weeks, and monthly backups
for 6 months. This project does not schedule or delete backups automatically.
Use timestamped output and keep the files private. If `db-check` reports a
failure, stop synchronization, preserve the database and WAL sidecars, and
copy the concise error plus `db-status --json` (with sensitive values removed)
for diagnosis. A missing LifeLoad or sleep domain is an API availability result,
not a locally calculated substitute.

For scheduled backups, use the existing `db-backup` command from a separately
reviewed administrator procedure. B006 does not install a backup scheduler.
