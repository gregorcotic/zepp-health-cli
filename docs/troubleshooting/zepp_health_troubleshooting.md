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
