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

## B006 — Production automation

Problem: validated SQLite synchronization still required manual operation on
Ubuntu.

Root cause: no unattended schedule, process lock, or machine-readable health
status existed.

Solution: added a six-hour systemd timer and oneshot service template, a
`flock`-based wrapper, `sync-health` with documented exit codes, and journal-
focused operational procedures. Credentials remain outside units and logs.

Lessons learned: systemd service templates must name the runtime account
explicitly; this repository therefore requires installation-time replacement
of safe user/group placeholders. Kernel advisory locks are preferable to
timestamp-only lock files because terminated processes release them.
