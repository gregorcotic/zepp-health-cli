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

## C019 — Fixed Zepp schedule and morning factual freshness

Problem: the old repository timer ran at 00:00, 06:00, 12:00, and 18:00 local
time, while a successful sync timestamp could mask that same-day
HRV/readiness data had not arrived for morning TRC.

Solution: changed the single persistent timer to 02:00, 06:30, 08:30, 12:00,
18:00, and 22:00 in `Europe/Ljubljana`; added an additive declarative trigger
for the existing context generator after successful sync; and extended
`sync-health` with separate sync and domain factual freshness. The context
timer remains a fallback. No readiness/TRC calculation, AI, destructive
migration, or invented sleep data was added.

Ownership: this app owns Zepp synchronization and database coverage facts.
`coach-data-bridge` must project the freshness object into its generated
`general-context.json`; bridge logic is intentionally not placed here. Deploy
and observe standalone first, then mirror into `coach-platform`, run its
`test-all`, and resume C017.3. At the C019 close, C018 remained deferred; it is
completed in the entries below.

## C018.1 — Wake Energy forensics

Investigated the `Charge/wake_data` path without changing its normalization,
freshness, or coaching semantics. Added a narrow sanitized
`diagnose-wake-energy` command and characterization coverage for explicit dates,
the Europe/Ljubljana midnight boundary, epoch seconds, sleep crossing midnight,
missing/empty/multiple samples, unsupported wrappers, and same-day SQLite
revision behavior.

The investigation establishes current behavior, not the production root cause:
wake samples inherit the parent event date; missing timezone defaults to UTC;
timestamps are assumed to be epoch milliseconds; and the ordinary sync summary
reports both an actually empty response and a nonempty unrecognized payload as
`empty` with zero normalized records. Production watch/app/API evidence is still
required before selecting a C018.2 fix.

Repository provenance: `Charge/wake_data` and the observed wake field mapping
first appeared in fork commit `beedc7a` after live validation. The configured
upstream base commit `a466dfa` contains generic `Charge/real_data` access but no
wake parser or BioCharge contract. Repository issue/code searches found no
additional upstream explanation.

## C018.2 — Proven local wake-day correction

Production evidence for July 23–26 classified the stale Wake BioCharge as
Case C, not a cloud delay. Three raw records each contained one extracted wake
sample. For the latest record, the parent timestamp resolved to July 25 UTC,
but `value.startTime=1785016800000` with
`timeZone=1,Europe/Ljubljana` represented July 26 00:00 local. The record was
retrieved by the July 26 morning sync, yet the old generic resolver stored it
as July 25.

The operator's July 24 09:03 app comparison independently confirmed the
semantics: Zepp displayed Wake BioCharge 65 and distinct Current BioCharge 55.
The raw record with parent July 23, local `startTime` July 24, and
`wakeCharge=65` was therefore the July 24 wake value, not a July 23 value.

The fix is wake-specific: it recognizes the observed Zepp timezone prefix and
uses an explicit sample date first, then an explicit sample timestamp or
`value.startTime + s` in that timezone, then the existing parent-date fallback.
No generic event resolver, freshness rule, morning requirement, endpoint,
schema, current Charge mapping, epoch-unit handling, or UTC-offset handling was
changed. The last two were not present in the production evidence.

The three affected derived `wake_energy` rows require a guarded in-place
record-key/date repair before resynchronization. Their sanitized raw payloads
and sync history remain intact; no schema migration or historical rebuild is
required.

## C018.3 — Production validation and closeout

The guarded repair and targeted seven-day resynchronization completed
successfully in production. The resync retrieved six wake records and reported
all six unchanged, confirming stable corrected logical identities with no
duplicates or revision regression. The repaired range retained Wake Charge
values 72, 65, 41, and 65 on July 23–26 respectively. July 24–26 raw parent
dates remained one day earlier while the wake-specific resolver and SQLite rows
used the correct local wake dates.

`sync-health` was healthy: synchronization occurred today, Wake Energy covered
July 26 as today, HRV/readiness/sleep-related readiness also covered today, and
`morning_data_status` became `complete` without weakening freshness rules.
SQLite integrity and foreign-key checks passed under schema version 3; raw
payloads, timestamps, sync history, and all other Zepp domain counts remained
intact.

C018 is complete. C018.2 changed only the value of the existing
`wake_energy.event_date` contract, so no new bridge field, gateway route, GPT
Action, OpenAPI schema, token, URL, or Custom GPT import is required. Existing
C017 monorepo mirroring remains separate planned work and may resume after this
standalone closeout.
