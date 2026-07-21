# Project history

## B004 — SQLite persistence

Added schema-versioned SQLite storage for native Zepp domains, sanitized raw
payloads, per-domain synchronization metadata, deterministic logical keys and
idempotent UPSERT behavior. Schema version 2 includes LifeLoad storage.

## B005 — Operational validation and recovery

Added SQLite integrity checks and SQLite-backup-API backup/restore commands.
Backups are validated before publication; restore requires a separate target by
default and compares schema version and table counts. Runtime databases,
sidecars and backup directories are ignored by Git. Live validation should be
performed on the MacBook account and must not expose credentials.

## Known historical commits

- B004: SQLite persistence for native Zepp health data (`677181b`).
- B003: daily-status/readiness and native metric consolidation (see Git history).
