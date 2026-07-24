"""SQLite persistence for native Zepp data.

This module stores Zepp values and provenance only. It does not calculate
health, readiness, recovery, or training recommendations.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 3
DEFAULT_DB_PATH = Path("data") / "zepp_health.db"
_REMOVED_KEYS = {
    "app_token", "apptoken", "authorization", "cookie", "cookies",
    "user_id", "userid", "uid", "token", "access_token", "refresh_token",
}
FRESHNESS_TIMEZONE = "Europe/Ljubljana"
MORNING_EXPECTED_AFTER = time(6, 30)
FRESHNESS_DOMAINS = {
    "sleep": None,
    "hrv": "hrv_samples",
    "readiness": "readiness_records",
    "sleep_related_readiness": "sleep_related_readiness",
    "wake_energy": "wake_energy",
    "exertion": "exertion_records",
}
MORNING_RECOVERY_DOMAINS = (
    "hrv",
    "readiness",
    "sleep_related_readiness",
    "wake_energy",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_payload(value: Any) -> Any:
    """Remove credential and user-id keys before anything reaches SQLite."""
    if isinstance(value, dict):
        return {
            key: sanitize_payload(item)
            for key, item in value.items()
            if str(key).lower() not in _REMOVED_KEYS
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def json_text(value: Any) -> str:
    return json.dumps(sanitize_payload(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def db_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json_text(value)
    return value


def logical_key(*values: Any) -> str:
    raw = "\x1f".join("" if value is None else str(value) for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_db_path(cli_path: str | Path | None = None, config: dict[str, Any] | None = None) -> Path:
    """Resolve DB path: CLI, config.json db_path, ZEPP_DB_PATH, default."""
    if cli_path:
        return Path(cli_path).expanduser()
    cfg = config or {}
    if cfg.get("db_path"):
        return Path(str(cfg["db_path"])).expanduser()
    import os

    if os.environ.get("ZEPP_DB_PATH", "").strip():
        return Path(os.environ["ZEPP_DB_PATH"]).expanduser()
    return DEFAULT_DB_PATH


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_payloads (
    payload_hash TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sub_type TEXT NOT NULL,
    requested_from_ms INTEGER,
    requested_to_ms INTEGER,
    retrieved_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    requested_days INTEGER NOT NULL,
    requested_from_ms INTEGER NOT NULL,
    requested_to_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_run_domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id),
    domain TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sub_type TEXT NOT NULL,
    status TEXT NOT NULL,
    records_retrieved INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS hrv_samples (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    start_time_ms INTEGER,
    offset_ms INTEGER,
    hrv REAL,
    raw_u TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hrv_daily (
    record_key TEXT PRIMARY KEY,
    event_date TEXT NOT NULL,
    value_json TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wake_energy (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    start_time_ms INTEGER,
    sample_timestamp_ms INTEGER,
    offset_ms INTEGER,
    bio_charge_wake REAL,
    wake_charge REAL,
    physical_wake REAL,
    mental_wake REAL,
    daily_fitness_score REAL,
    stress_fitness_score REAL,
    exertion_score REAL,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exertion_records (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    recovery_factor TEXT,
    total_score TEXT,
    activity_score TEXT,
    exercise_score TEXT,
    atl TEXT,
    ctl TEXT,
    tsb TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS readiness_records (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    timestamp_update_ms INTEGER,
    status TEXT,
    hrv_score TEXT,
    sleep_hrv TEXT,
    rhr_score TEXT,
    sleep_rhr TEXT,
    phy_score TEXT,
    ment_score TEXT,
    skin_temp_score TEXT,
    ahi_score TEXT,
    rdns_score TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sleep_related_readiness (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    timestamp_update_ms INTEGER,
    sleep_hrv TEXT,
    sleep_rhr TEXT,
    ahi_score TEXT,
    ahi_baseline TEXT,
    rdns_score TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS charge_records (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    start_time_ms INTEGER,
    sample_timestamp_ms INTEGER,
    offset_ms INTEGER,
    end_offset_ms INTEGER,
    total TEXT,
    physical TEXT,
    mental TEXT,
    raw_u TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS insight_records (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    start_time_ms INTEGER,
    insight_id TEXT,
    insight TEXT,
    type TEXT,
    diff TEXT,
    slope TEXT,
    start_offset_ms INTEGER,
    end_offset_ms INTEGER,
    track_id TEXT,
    threshold TEXT,
    raw_u TEXT,
    parsed_json_extra TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lifeload_records (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    start_time_ms INTEGER,
    life_load TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hrv_date ON hrv_samples(event_date);
CREATE INDEX IF NOT EXISTS idx_wake_date ON wake_energy(event_date);
CREATE INDEX IF NOT EXISTS idx_exertion_date ON exertion_records(event_date);
CREATE INDEX IF NOT EXISTS idx_readiness_date ON readiness_records(event_date);
CREATE INDEX IF NOT EXISTS idx_sleep_readiness_date ON sleep_related_readiness(event_date);
CREATE INDEX IF NOT EXISTS idx_charge_date ON charge_records(event_date);
CREATE INDEX IF NOT EXISTS idx_insight_date ON insight_records(event_date);
CREATE INDEX IF NOT EXISTS idx_lifeload_date ON lifeload_records(event_date);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS lifeload_records (
    record_key TEXT PRIMARY KEY,
    event_date TEXT,
    timestamp_ms INTEGER,
    start_time_ms INTEGER,
    life_load TEXT,
    source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lifeload_date ON lifeload_records(event_date);
"""

SCHEMA_V3_SQL = """
CREATE TABLE IF NOT EXISTS historical_sync_progress (
    job_key TEXT NOT NULL,
    domain TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sub_type TEXT NOT NULL,
    target_from_date TEXT NOT NULL,
    cursor_to_date TEXT NOT NULL,
    status TEXT NOT NULL,
    chunks_completed INTEGER NOT NULL DEFAULT 0,
    records_retrieved INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_key, domain)
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    def migrate(self) -> None:
        with self.connection:
            current = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported database schema version: {current}")
            if current < 1:
                self.connection.executescript(SCHEMA_SQL)
                self.connection.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '1')"
                )
                self.connection.execute("PRAGMA user_version = 1")
                current = 1
            if current < 2:
                self.connection.executescript(SCHEMA_V2_SQL)
                self.connection.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '2')"
                )
                self.connection.execute("PRAGMA user_version = 2")
                current = 2
            if current < 3:
                self.connection.executescript(SCHEMA_V3_SQL)
                self.connection.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '3')"
                )
                self.connection.execute("PRAGMA user_version = 3")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def start_sync(self, days: int, from_ms: int, to_ms: int) -> int:
        cur = self.connection.execute(
            "INSERT INTO sync_runs(started_at, requested_days, requested_from_ms, requested_to_ms, status) VALUES (?, ?, ?, ?, ?)",
            (utc_now(), days, from_ms, to_ms, "running"),
        )
        self.connection.commit()
        return int(cur.lastrowid)

    def mark_running_syncs_interrupted(self) -> int:
        """Close stale runs left behind by a terminated process on next startup."""
        cur = self.connection.execute(
            "UPDATE sync_runs SET finished_at=?, status='interrupted', summary_json=? WHERE status='running'",
            (utc_now(), json_text({"error": "process_interrupted"})),
        )
        self.connection.commit()
        return int(cur.rowcount)

    def finish_sync(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        self.connection.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, summary_json=? WHERE id=?",
            (utc_now(), status, json_text(summary), run_id),
        )
        self.connection.commit()

    def record_sync_domain(self, run_id: int, result: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO sync_run_domains
            (sync_run_id, domain, event_type, sub_type, status, records_retrieved,
             inserted_count, updated_count, unchanged_count, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, result["domain"], result["event_type"], result["sub_type"],
                result["status"], result.get("records_retrieved", 0),
                result.get("inserted", 0), result.get("updated", 0),
                result.get("unchanged", 0), result.get("error"),
            ),
        )
        self.connection.commit()

    def get_historical_progress(self, job_key: str, domain: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM historical_sync_progress WHERE job_key=? AND domain=?",
            (job_key, domain),
        ).fetchone()

    def save_historical_progress(self, job_key: str, result: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO historical_sync_progress
            (job_key, domain, event_type, sub_type, target_from_date, cursor_to_date,
             status, chunks_completed, records_retrieved, inserted_count,
             updated_count, unchanged_count, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_key, domain) DO UPDATE SET
              cursor_to_date=excluded.cursor_to_date, status=excluded.status,
              chunks_completed=excluded.chunks_completed,
              records_retrieved=excluded.records_retrieved,
              inserted_count=excluded.inserted_count,
              updated_count=excluded.updated_count,
              unchanged_count=excluded.unchanged_count,
              last_error=excluded.last_error, updated_at=excluded.updated_at""",
            (job_key, result["domain"], result["event_type"], result["sub_type"],
             result["target_from_date"], result["cursor_to_date"], result["status"],
             result.get("chunks_completed", 0), result.get("records_retrieved", 0),
             result.get("inserted", 0), result.get("updated", 0),
             result.get("unchanged", 0), result.get("error"), utc_now()),
        )
        self.connection.commit()

    def store_raw_payload(
        self,
        domain: str,
        event_type: str,
        sub_type: str,
        payload: Any,
        from_ms: int,
        to_ms: int,
    ) -> str:
        payload_json = json_text(payload)
        digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        self.connection.execute(
            """INSERT OR IGNORE INTO raw_payloads
            (payload_hash, domain, event_type, sub_type, requested_from_ms,
             requested_to_ms, retrieved_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (digest, domain, event_type, sub_type, from_ms, to_ms, utc_now(), payload_json),
        )
        return digest

    def _upsert(
        self,
        table: str,
        row: dict[str, Any],
        columns: tuple[str, ...],
    ) -> str:
        key = row["record_key"]
        existing = self.connection.execute(
            f"SELECT source_json FROM {table} WHERE record_key=?", (key,)
        ).fetchone()
        raw = json_text(row.get("source_json", row))
        if existing is None:
            values = [key] + [db_value(row.get(column)) for column in columns] + [raw, utc_now()]
            names = ["record_key", *columns, "source_json", "updated_at"]
            placeholders = ",".join("?" for _ in names)
            self.connection.execute(
                f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders})", values
            )
            return "inserted"
        if existing["source_json"] == raw:
            return "unchanged"
        assignments = ",".join(f"{column}=?" for column in (*columns, "source_json", "updated_at"))
        values = [db_value(row.get(column)) for column in columns] + [raw, utc_now(), key]
        self.connection.execute(
            f"UPDATE {table} SET {assignments} WHERE record_key=?", values
        )
        return "updated"

    def store_domain_rows(self, domain: str, rows: list[dict[str, Any]]) -> dict[str, int]:
        with self.transaction():
            return self._store_domain_rows_in_transaction(domain, rows)

    def _store_domain_rows_in_transaction(self, domain: str, rows: list[dict[str, Any]]) -> dict[str, int]:
        mapping = _domain_rows(domain, rows)
        if domain == "readiness":
            mapping += _domain_rows("sleep_related_readiness", rows)
        totals = {"inserted": 0, "updated": 0, "unchanged": 0}
        for table, table_rows, columns in mapping:
            for row in table_rows:
                result = self._upsert(table, row, columns)
                if table == domain_table(domain):
                    totals[result] += 1
        return totals

    def store_domain_with_raw(
        self,
        domain: str,
        event_type: str,
        sub_type: str,
        payload: Any,
        from_ms: int,
        to_ms: int,
        rows: list[dict[str, Any]],
    ) -> dict[str, int]:
        with self.transaction():
            self.store_raw_payload(domain, event_type, sub_type, payload, from_ms, to_ms)
            return self._store_domain_rows_in_transaction(domain, rows)

    def status(self) -> dict[str, Any]:
        tables = (
            "hrv_samples", "hrv_daily", "wake_energy", "exertion_records",
            "readiness_records", "sleep_related_readiness", "charge_records",
            "insight_records", "lifeload_records", "raw_payloads", "sync_runs",
        )
        counts = {
            table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
        dates: list[str] = []
        for table in tables[:-2]:
            row = self.connection.execute(
                f"SELECT MIN(event_date), MAX(event_date) FROM {table} WHERE event_date IS NOT NULL"
            ).fetchone()
            dates.extend([value for value in row if value])
        latest = self.connection.execute(
            "SELECT MAX(finished_at) FROM sync_runs WHERE finished_at IS NOT NULL"
        ).fetchone()[0]
        return {
            "database_path": str(self.path),
            "schema_version": SCHEMA_VERSION,
            "date_range": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
            "record_counts": counts,
            "latest_sync_at": latest,
        }

    def factual_freshness(
        self,
        now: datetime | None = None,
        timezone_name: str = FRESHNESS_TIMEZONE,
    ) -> dict[str, Any]:
        """Report synchronization and domain dates without interpreting health."""
        local_zone = ZoneInfo(timezone_name)
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        local_now = instant.astimezone(local_zone)
        today = local_now.date().isoformat()
        yesterday = (local_now.date() - timedelta(days=1)).isoformat()

        latest_success = self.connection.execute(
            "SELECT finished_at FROM sync_runs "
            "WHERE status='ok' AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        latest_success_at = latest_success[0] if latest_success else None
        latest_success_local_date = None
        if latest_success_at:
            parsed = datetime.fromisoformat(str(latest_success_at))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            latest_success_local_date = parsed.astimezone(local_zone).date().isoformat()

        domains: dict[str, dict[str, Any]] = {}
        for name, table_name in FRESHNESS_DOMAINS.items():
            if table_name is None:
                domains[name] = {
                    "supported": False,
                    "latest_date": None,
                    "coverage": "unavailable",
                }
                continue
            latest_date = self.connection.execute(
                f"SELECT MAX(event_date) FROM {table_name} WHERE event_date IS NOT NULL"
            ).fetchone()[0]
            if latest_date == today:
                coverage = "today"
            elif latest_date == yesterday:
                coverage = "yesterday"
            elif latest_date is None:
                coverage = "unavailable"
            else:
                coverage = "other"
            domains[name] = {
                "supported": True,
                "latest_date": latest_date,
                "coverage": coverage,
            }

        recovery = [domains[name] for name in MORNING_RECOVERY_DOMAINS]
        available = [item for item in recovery if item["latest_date"] is not None]
        today_count = sum(item["latest_date"] == today for item in recovery)
        if not available:
            morning_status = "unavailable"
        elif today_count == len(recovery):
            morning_status = "complete"
        elif today_count:
            morning_status = "partial"
        else:
            morning_status = "pending"

        return {
            "timezone": timezone_name,
            "as_of": local_now.isoformat(),
            "today": today,
            "yesterday": yesterday,
            "morning_expectation": (
                "before_first_morning_sync"
                if local_now.timetz().replace(tzinfo=None) < MORNING_EXPECTED_AFTER
                else "morning_sync_expected"
            ),
            "sync_freshness": {
                "latest_successful_sync": latest_success_at,
                "latest_successful_sync_local_date": latest_success_local_date,
                "synchronized_today": latest_success_local_date == today,
            },
            "domain_data_freshness": domains,
            "morning_recovery_domains": list(MORNING_RECOVERY_DOMAINS),
            "morning_data_status": morning_status,
        }

    def read_daily_status(self, from_date: str, to_date: str) -> list[dict[str, Any]]:
        """Read a factual daily status view from stored rows only."""
        rows: dict[str, dict[str, Any]] = {}

        def bucket(day: str | None) -> dict[str, Any] | None:
            if not day or not (from_date <= day <= to_date):
                return None
            return rows.setdefault(day, {"date": day})

        hrv = self.connection.execute(
            "SELECT * FROM hrv_samples WHERE event_date BETWEEN ? AND ? ORDER BY event_date, COALESCE(timestamp_ms, start_time_ms + COALESCE(offset_ms, 0))",
            (from_date, to_date),
        ).fetchall()
        for row in hrv:
            target = bucket(row["event_date"])
            if target is None:
                continue
            target["hrv"] = {"latest": row["hrv"], "sample_timestamp": row["timestamp_ms"], "source": "zepp", "calculation_source": "zepp", "mapping_confidence": "confirmed"}
            target["hrv_sample_count"] = target.get("hrv_sample_count", 0) + 1

        specs = (
            ("wake_energy", "wake_energy", "COALESCE(sample_timestamp_ms, timestamp_ms)"),
            ("exertion", "exertion_records", "timestamp_ms"),
            ("readiness", "readiness_records", "COALESCE(timestamp_update_ms, timestamp_ms)"),
            ("sleep_related_readiness", "sleep_related_readiness", "COALESCE(timestamp_update_ms, timestamp_ms)"),
            ("charge", "charge_records", "COALESCE(sample_timestamp_ms, timestamp_ms)"),
            ("insights", "insight_records", "COALESCE(timestamp_ms, start_time_ms)"),
            ("lifeload", "lifeload_records", "timestamp_ms"),
        )
        selected_fields = {
            "wake_energy": ("bio_charge_wake", "wake_charge", "physical_wake", "mental_wake", "daily_fitness_score", "stress_fitness_score", "exertion_score"),
            "exertion": ("recovery_factor", "total_score", "activity_score", "exercise_score", "atl", "ctl", "tsb"),
            "readiness": ("status", "hrv_score", "sleep_hrv", "rhr_score", "sleep_rhr", "phy_score", "ment_score", "skin_temp_score", "ahi_score", "rdns_score"),
            "sleep_related_readiness": ("sleep_hrv", "sleep_rhr", "ahi_score", "ahi_baseline", "rdns_score"),
            "lifeload": ("life_load",),
        }
        output_names = {
            "bio_charge_wake": "bioChargeWake", "wake_charge": "wakeCharge", "physical_wake": "physicalWake", "mental_wake": "mentalWake", "daily_fitness_score": "dailyFitnessScore", "stress_fitness_score": "stressFitnessScore", "exertion_score": "exertionScore",
            "recovery_factor": "recoveryFactor", "total_score": "totalScore", "activity_score": "activityScore", "exercise_score": "exerciseScore", "atl": "atl", "ctl": "ctl", "tsb": "tsb", "status": "status", "hrv_score": "hrvScore", "sleep_hrv": "sleepHRV", "rhr_score": "rhrScore", "sleep_rhr": "sleepRHR", "phy_score": "phyScore", "ment_score": "mentScore", "skin_temp_score": "skinTempScore", "ahi_score": "ahiScore", "ahi_baseline": "ahiBaseline", "rdns_score": "rdnsScore", "life_load": "lifeLoad",
        }
        for name, table, order_by in specs:
            query = f"SELECT * FROM {table} WHERE event_date BETWEEN ? AND ? ORDER BY event_date, {order_by}"
            for row in self.connection.execute(query, (from_date, to_date)).fetchall():
                target = bucket(row["event_date"])
                if target is None:
                    continue
                values = {output_names[field]: row[field] for field in selected_fields.get(name, ()) if field in row.keys()}
                values.update({"source": "zepp", "calculation_source": "zepp", "mapping_confidence": "confirmed"})
                target[name] = values
        return [rows[key] for key in sorted(rows)]


def inspect_database_file(path: str | Path) -> dict[str, Any]:
    """Inspect an existing SQLite file without migrating or changing it."""
    database_path = Path(path).expanduser()
    if not database_path.is_file():
        raise FileNotFoundError(str(database_path))
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = [
            "hrv_samples", "hrv_daily", "wake_energy", "exertion_records",
            "readiness_records", "sleep_related_readiness", "charge_records",
            "insight_records", "lifeload_records", "raw_payloads", "sync_runs",
            "sync_run_domains",
        ]
        counts: dict[str, int] = {}
        for table in tables:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if exists else 0
        latest_sync = connection.execute(
            "SELECT finished_at, status FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
        ).fetchone() if counts["sync_runs"] else None
        sidecars = {}
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database_path) + suffix)
            sidecars[suffix[1:]] = {"exists": sidecar.exists(), "size_bytes": sidecar.stat().st_size if sidecar.exists() else 0}
        return {
            "database_path": str(database_path),
            "integrity_check": integrity,
            "foreign_key_check": foreign_keys,
            "journal_mode": journal_mode,
            "schema_version": schema_version,
            "database_size_bytes": database_path.stat().st_size,
            "sidecars": sidecars,
            "record_counts": counts,
            "latest_sync": dict(latest_sync) if latest_sync else None,
        }
    finally:
        connection.close()


def _backup_sqlite(source: Path, target: Path, overwrite: bool) -> None:
    if not source.is_file():
        raise FileNotFoundError(str(source))
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30.0)
        target_connection = sqlite3.connect(temporary, timeout=30.0)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
            source_connection.close()
        checked = inspect_database_file(temporary)
        if checked["integrity_check"] != "ok" or checked["foreign_key_check"]:
            raise sqlite3.DatabaseError("backup integrity check failed")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def backup_database(source: str | Path, target: str | Path, overwrite: bool = False) -> dict[str, Any]:
    source_path = Path(source).expanduser()
    target_path = Path(target).expanduser()
    source_info = inspect_database_file(source_path)
    if source_info["integrity_check"] != "ok" or source_info["foreign_key_check"]:
        raise sqlite3.DatabaseError("source database integrity check failed")
    _backup_sqlite(source_path, target_path, overwrite)
    result = inspect_database_file(target_path)
    result.update({"source_path": str(source_path), "output_path": str(target_path)})
    return result


def restore_database(source: str | Path, target: str | Path, overwrite: bool = False) -> dict[str, Any]:
    source_path = Path(source).expanduser()
    target_path = Path(target).expanduser()
    source_info = inspect_database_file(source_path)
    if source_info["integrity_check"] != "ok" or source_info["foreign_key_check"]:
        raise sqlite3.DatabaseError("source backup integrity check failed")
    _backup_sqlite(source_path, target_path, overwrite)
    result = inspect_database_file(target_path)
    if result["schema_version"] != source_info["schema_version"] or result["record_counts"] != source_info["record_counts"]:
        raise sqlite3.DatabaseError("restored database does not match source")
    result.update({"source_path": str(source_path), "output_path": str(target_path), "counts_match": True})
    return result


def _domain_rows(domain: str, rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]], tuple[str, ...]]]:
    now_rows: list[dict[str, Any]] = []
    for row in rows:
        row = dict(row)
        if "raw_sample" in row:
            row["source_json"] = row["raw_sample"]
        elif "raw_value" in row:
            row["source_json"] = row["raw_value"]
        else:
            row["source_json"] = {key: value for key, value in row.items() if key != "source_json"}
        if domain == "hrv":
            row["record_key"] = logical_key("hrv", row.get("date"), row.get("start_time"), row.get("s"), row.get("hrv"), row.get("u"))
            now_rows.append(row)
        elif domain == "wake_energy":
            row["record_key"] = logical_key("wake", row.get("date"), row.get("start_time"), row.get("s"))
            now_rows.append(row)
        elif domain == "exertion":
            row["record_key"] = logical_key("exertion", row.get("date"), row.get("timestamp"))
            now_rows.append(row)
        elif domain == "readiness":
            row["record_key"] = logical_key("readiness", row.get("date"), row.get("timestamp"), row.get("timestampUpdate"))
            now_rows.append(row)
        elif domain == "charge":
            row["record_key"] = logical_key("charge", row.get("date"), row.get("start_time"), row.get("s"))
            now_rows.append(row)
        elif domain == "insights":
            row["record_key"] = logical_key("insight", row.get("date"), row.get("start_time"), row.get("start_offset_ms"), row.get("insight_id"), row.get("type"))
            now_rows.append(row)
        elif domain == "lifeload":
            row["record_key"] = logical_key("lifeload", row.get("date"), row.get("timestamp"), row.get("start_time"))
            now_rows.append(row)
        elif domain == "sleep_related_readiness":
            row["record_key"] = logical_key("sleep_readiness", row.get("date"), row.get("timestamp"), row.get("timestampUpdate"))
            now_rows.append(row)
    columns = {
        "hrv": ("event_date", "timestamp_ms", "start_time_ms", "offset_ms", "hrv", "raw_u"),
        "wake_energy": ("event_date", "timestamp_ms", "start_time_ms", "sample_timestamp_ms", "offset_ms", "bio_charge_wake", "wake_charge", "physical_wake", "mental_wake", "daily_fitness_score", "stress_fitness_score", "exertion_score"),
        "exertion": ("event_date", "timestamp_ms", "recovery_factor", "total_score", "activity_score", "exercise_score", "atl", "ctl", "tsb"),
        "readiness": ("event_date", "timestamp_ms", "timestamp_update_ms", "status", "hrv_score", "sleep_hrv", "rhr_score", "sleep_rhr", "phy_score", "ment_score", "skin_temp_score", "ahi_score", "rdns_score"),
        "charge": ("event_date", "timestamp_ms", "start_time_ms", "sample_timestamp_ms", "offset_ms", "end_offset_ms", "total", "physical", "mental", "raw_u"),
        "insights": ("event_date", "timestamp_ms", "start_time_ms", "insight_id", "insight", "type", "diff", "slope", "start_offset_ms", "end_offset_ms", "track_id", "threshold", "raw_u", "parsed_json_extra"),
        "sleep_related_readiness": ("event_date", "timestamp_ms", "timestamp_update_ms", "sleep_hrv", "sleep_rhr", "ahi_score", "ahi_baseline", "rdns_score"),
        "lifeload": ("event_date", "timestamp_ms", "start_time_ms", "life_load"),
    }[domain]
    converted: list[dict[str, Any]] = []
    for row in now_rows:
        converted_row = dict(row)
        aliases = {
            "event_date": "date", "timestamp_ms": "timestamp", "start_time_ms": "start_time", "offset_ms": "s", "sample_timestamp_ms": "sample_timestamp", "end_offset_ms": "e", "bio_charge_wake": "bioChargeWake", "wake_charge": "wakeCharge", "physical_wake": "physicalWake", "mental_wake": "mentalWake", "daily_fitness_score": "dailyFitnessScore", "stress_fitness_score": "stressFitnessScore", "exertion_score": "exertionScore", "recovery_factor": "recoveryFactor", "total_score": "totalScore", "activity_score": "activityScore", "exercise_score": "exerciseScore", "timestamp_update_ms": "timestampUpdate", "hrv_score": "hrvScore", "sleep_hrv": "sleepHRV", "rhr_score": "rhrScore", "sleep_rhr": "sleepRHR", "phy_score": "phyScore", "ment_score": "mentScore", "skin_temp_score": "skinTempScore", "ahi_score": "ahiScore", "ahi_baseline": "ahiBaseline", "rdns_score": "rdnsScore", "life_load": "lifeLoad", "insight_id": "insight_id", "track_id": "track_id", "threshold": "threshold", "parsed_json_extra": "parsed_json_extra", "raw_u": "raw_u",
        }
        for target, source in aliases.items():
            converted_row[target] = row.get(source)
        converted.append(converted_row)
    return [(domain_table(domain), converted, columns)]


def domain_table(domain: str) -> str:
    return {"hrv": "hrv_samples", "wake_energy": "wake_energy", "exertion": "exertion_records", "readiness": "readiness_records", "charge": "charge_records", "insights": "insight_records", "sleep_related_readiness": "sleep_related_readiness", "lifeload": "lifeload_records"}[domain]
