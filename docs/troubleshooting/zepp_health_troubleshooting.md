# Zepp health troubleshooting

## Synchronization

Run `db-status --json` and `db-check --json` first. `sync-db` isolates domains;
an unavailable LifeLoad or sleep source does not erase prior rows. Repeated
synchronization should produce `unchanged` counts rather than duplicates.

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
