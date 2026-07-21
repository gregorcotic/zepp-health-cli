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

If a run is skipped, the journal says that the `flock` lock is held. The lock
is kernel-owned and is released when the owning process exits; do not delete a
lock file while synchronization may still be running. Check the process and
journal first.

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
