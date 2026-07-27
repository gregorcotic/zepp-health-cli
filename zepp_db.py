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


SCHEMA_VERSION = 5
DEFAULT_DB_PATH = Path("data") / "zepp_health.db"
_REMOVED_KEYS = {
    "app_token", "apptoken", "authorization", "cookie", "cookies",
    "user_id", "userid", "uid", "token", "access_token", "refresh_token",
    "device_id", "deviceid", "device_sn", "devicesn", "account_id",
    "accountid", "owner_id", "ownerid", "download_url", "downloadurl",
    "file_url", "fileurl", "url", "secret_url", "secreturl",
    "accesstoken", "refreshtoken", "authorizationheader",
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


def payload_fingerprint(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def _safe_activity_sync_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    allowed = (
        "id", "started_at", "finished_at", "requested_from_date",
        "requested_to_date", "timezone", "status", "activities_seen",
        "inserted_count", "updated_count", "unchanged_count",
        "detail_fetch_success", "detail_fetch_failed", "history_next",
        "error_category", "error_type",
    )
    return {key: row[key] for key in allowed}


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

SCHEMA_V4_SQL = """
CREATE TABLE IF NOT EXISTS activities (
    track_id TEXT PRIMARY KEY,
    source TEXT,
    native_type INTEGER,
    sport_mode INTEGER,
    sport_name TEXT,
    sport_family TEXT,
    zepp_coach_mode INTEGER,
    mapping_confidence TEXT,
    local_activity_date TEXT,
    start_time TEXT,
    end_time TEXT,
    timezone TEXT,
    duration_s REAL,
    history_fingerprint TEXT NOT NULL,
    detail_fingerprint TEXT,
    canonical_fingerprint TEXT NOT NULL,
    history_payload_hash TEXT REFERENCES raw_payloads(payload_hash),
    detail_payload_hash TEXT REFERENCES raw_payloads(payload_hash),
    detail_complete INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_summary_metrics (
    activity_track_id TEXT NOT NULL REFERENCES activities(track_id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    value_real REAL,
    value_text TEXT,
    unit TEXT,
    status TEXT NOT NULL,
    raw_value_json TEXT,
    source_path TEXT,
    semantic_confidence TEXT,
    provenance_json TEXT,
    reason TEXT,
    PRIMARY KEY (activity_track_id, metric_name)
);

CREATE TABLE IF NOT EXISTS activity_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_track_id TEXT NOT NULL REFERENCES activities(track_id) ON DELETE CASCADE,
    stream_type TEXT NOT NULL,
    status TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    unit TEXT,
    source_path TEXT,
    semantic_confidence TEXT,
    provenance_json TEXT,
    metadata_json TEXT,
    UNIQUE (activity_track_id, stream_type)
);

CREATE TABLE IF NOT EXISTS activity_samples (
    stream_id INTEGER NOT NULL REFERENCES activity_streams(id) ON DELETE CASCADE,
    sample_index INTEGER NOT NULL,
    offset_s REAL,
    timestamp TEXT,
    value_real REAL,
    value_text TEXT,
    latitude REAL,
    longitude REAL,
    status TEXT,
    raw_value_json TEXT,
    PRIMARY KEY (stream_id, sample_index)
);

CREATE TABLE IF NOT EXISTS activity_laps (
    activity_track_id TEXT NOT NULL REFERENCES activities(track_id) ON DELETE CASCADE,
    lap_type TEXT NOT NULL,
    record_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    raw_components_json TEXT,
    source_path TEXT,
    provenance_json TEXT,
    PRIMARY KEY (activity_track_id, lap_type, record_index)
);

CREATE TABLE IF NOT EXISTS activity_notes (
    activity_track_id TEXT PRIMARY KEY REFERENCES activities(track_id) ON DELETE CASCADE,
    present INTEGER NOT NULL,
    note_text TEXT,
    note_length INTEGER NOT NULL,
    source_path TEXT,
    evidence TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_quality_flags (
    activity_track_id TEXT NOT NULL REFERENCES activities(track_id) ON DELETE CASCADE,
    flag TEXT NOT NULL,
    PRIMARY KEY (activity_track_id, flag)
);

CREATE TABLE IF NOT EXISTS activity_provenance (
    activity_track_id TEXT NOT NULL REFERENCES activities(track_id) ON DELETE CASCADE,
    provenance_key TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    PRIMARY KEY (activity_track_id, provenance_key)
);

CREATE TABLE IF NOT EXISTS activity_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    requested_from_date TEXT NOT NULL,
    requested_to_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL,
    activities_seen INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    detail_fetch_success INTEGER NOT NULL DEFAULT 0,
    detail_fetch_failed INTEGER NOT NULL DEFAULT 0,
    history_next TEXT,
    error_category TEXT,
    error_type TEXT,
    summary_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_activities_date
    ON activities(local_activity_date);
CREATE INDEX IF NOT EXISTS idx_activities_sport_date
    ON activities(sport_family, local_activity_date);
CREATE INDEX IF NOT EXISTS idx_activities_type_mode_date
    ON activities(native_type, sport_mode, local_activity_date);
CREATE INDEX IF NOT EXISTS idx_activity_streams_activity
    ON activity_streams(activity_track_id);
CREATE INDEX IF NOT EXISTS idx_activity_quality_flag
    ON activity_quality_flags(flag, activity_track_id);
CREATE INDEX IF NOT EXISTS idx_activity_sync_finished
    ON activity_sync_runs(finished_at, status);
"""

SCHEMA_V5_SQL = """
ALTER TABLE exertion_records ADD COLUMN recovery_factor_id TEXT;
ALTER TABLE exertion_records ADD COLUMN target_score TEXT;
ALTER TABLE exertion_records ADD COLUMN completion_percent TEXT;
ALTER TABLE exertion_records ADD COLUMN insight_state TEXT;
ALTER TABLE exertion_records ADD COLUMN exercise_plan_intensity TEXT;
ALTER TABLE exertion_records ADD COLUMN exercise_plan_duration TEXT;
ALTER TABLE exertion_records ADD COLUMN exercise_plan_hr_lower TEXT;
ALTER TABLE exertion_records ADD COLUMN exercise_plan_hr_upper TEXT;
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
                current = 3
            if current < 4:
                self.connection.executescript(SCHEMA_V4_SQL)
                self.connection.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '4')"
                )
                self.connection.execute("PRAGMA user_version = 4")
                current = 4
            if current < 5:
                existing_columns = {
                    row[1]
                    for row in self.connection.execute(
                        "PRAGMA table_info(exertion_records)"
                    ).fetchall()
                }
                exertion_v5_columns = {
                    "recovery_factor_id": "TEXT",
                    "target_score": "TEXT",
                    "completion_percent": "TEXT",
                    "insight_state": "TEXT",
                    "exercise_plan_intensity": "TEXT",
                    "exercise_plan_duration": "TEXT",
                    "exercise_plan_hr_lower": "TEXT",
                    "exercise_plan_hr_upper": "TEXT",
                }
                for column, column_type in exertion_v5_columns.items():
                    if column not in existing_columns:
                        self.connection.execute(
                            f"ALTER TABLE exertion_records "
                            f"ADD COLUMN {column} {column_type}"
                        )
                self.connection.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '5')"
                )
                self.connection.execute("PRAGMA user_version = 5")

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

    def activity_sync_state(
        self, track_id: str | int, history_record: dict[str, Any]
    ) -> str:
        row = self.connection.execute(
            "SELECT history_fingerprint, detail_complete FROM activities "
            "WHERE track_id=?",
            (str(track_id),),
        ).fetchone()
        if row is None:
            return "new"
        if row["history_fingerprint"] != payload_fingerprint(history_record):
            return "changed"
        if not row["detail_complete"]:
            return "detail_incomplete"
        return "unchanged"

    def touch_activity(self, track_id: str | int) -> None:
        self.connection.execute(
            "UPDATE activities SET last_synced_at=? WHERE track_id=?",
            (utc_now(), str(track_id)),
        )
        self.connection.commit()

    def start_activity_sync(
        self, from_date: str, to_date: str, timezone_name: str
    ) -> int:
        cursor = self.connection.execute(
            """INSERT INTO activity_sync_runs
            (started_at, requested_from_date, requested_to_date, timezone, status)
            VALUES (?, ?, ?, ?, 'running')""",
            (utc_now(), from_date, to_date, timezone_name),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def mark_running_activity_syncs_interrupted(self) -> int:
        cursor = self.connection.execute(
            """UPDATE activity_sync_runs SET finished_at=?, status='interrupted',
            error_category='process', error_type='ProcessInterrupted'
            WHERE status='running'""",
            (utc_now(),),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def finish_activity_sync(self, run_id: int, result: dict[str, Any]) -> None:
        self.connection.execute(
            """UPDATE activity_sync_runs SET
            finished_at=?, status=?, activities_seen=?, inserted_count=?,
            updated_count=?, unchanged_count=?, detail_fetch_success=?,
            detail_fetch_failed=?, history_next=?, error_category=?,
            error_type=?, summary_json=? WHERE id=?""",
            (
                utc_now(),
                result["status"],
                result.get("activities_seen", 0),
                result.get("inserted", 0),
                result.get("updated", 0),
                result.get("unchanged", 0),
                result.get("detail_fetch_success", 0),
                result.get("detail_fetch_failed", 0),
                None if result.get("history_next") is None
                else str(result.get("history_next")),
                result.get("error_category"),
                result.get("error_type"),
                json_text(result),
                run_id,
            ),
        )
        self.connection.commit()

    def store_canonical_activity(
        self,
        canonical: dict[str, Any],
        history_record: dict[str, Any],
        detail_payload: Any,
    ) -> str:
        """Atomically replace one activity and every dependent detail row."""
        identity = canonical["identity"]
        track_id = str(identity["track_id"])
        history_fingerprint = payload_fingerprint(history_record)
        detail_fingerprint = payload_fingerprint(detail_payload)
        canonical_fingerprint = payload_fingerprint(canonical)
        now = utc_now()
        with self.transaction():
            history_hash = self.store_raw_payload(
                "activities",
                "sport",
                "history",
                history_record,
                int(_number_or_none(identity["track_id"]) or 0),
                int(_number_or_none(identity["track_id"]) or 0),
            )
            detail_hash = self.store_raw_payload(
                "activities",
                "sport",
                "detail",
                detail_payload,
                int(_number_or_none(identity["track_id"]) or 0),
                int(_number_or_none(identity["track_id"]) or 0),
            )
            existing = self.connection.execute(
                "SELECT canonical_fingerprint, history_fingerprint, "
                "detail_fingerprint, created_at FROM activities "
                "WHERE track_id=?",
                (track_id,),
            ).fetchone()
            if (
                existing
                and existing["canonical_fingerprint"] == canonical_fingerprint
                and existing["history_fingerprint"] == history_fingerprint
                and existing["detail_fingerprint"] == detail_fingerprint
            ):
                self.connection.execute(
                    "UPDATE activities SET last_synced_at=? WHERE track_id=?",
                    (now, track_id),
                )
                return "unchanged"

            time_model = canonical.get("time", {})
            duration = time_model.get("duration_s", {}).get("value")
            values = (
                identity.get("source"),
                identity.get("native_type"),
                identity.get("sport_mode"),
                identity.get("sport_name"),
                identity.get("sport_family"),
                int(bool(identity.get("zepp_coach_mode")))
                if identity.get("zepp_coach_mode") is not None else None,
                identity.get("mapping_confidence"),
                time_model.get("local_activity_date"),
                time_model.get("start_time"),
                time_model.get("end_time"),
                time_model.get("timezone"),
                duration,
                history_fingerprint,
                detail_fingerprint,
                canonical_fingerprint,
                history_hash,
                detail_hash,
                1,
                existing["created_at"] if existing else now,
                now,
                now,
                track_id,
            )
            self.connection.execute(
                """INSERT INTO activities
                (source, native_type, sport_mode, sport_name, sport_family,
                 zepp_coach_mode, mapping_confidence, local_activity_date,
                 start_time, end_time, timezone, duration_s,
                 history_fingerprint, detail_fingerprint, canonical_fingerprint,
                 history_payload_hash, detail_payload_hash, detail_complete,
                 created_at, updated_at, last_synced_at, track_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                 source=excluded.source, native_type=excluded.native_type,
                 sport_mode=excluded.sport_mode, sport_name=excluded.sport_name,
                 sport_family=excluded.sport_family,
                 zepp_coach_mode=excluded.zepp_coach_mode,
                 mapping_confidence=excluded.mapping_confidence,
                 local_activity_date=excluded.local_activity_date,
                 start_time=excluded.start_time, end_time=excluded.end_time,
                 timezone=excluded.timezone, duration_s=excluded.duration_s,
                 history_fingerprint=excluded.history_fingerprint,
                 detail_fingerprint=excluded.detail_fingerprint,
                 canonical_fingerprint=excluded.canonical_fingerprint,
                 history_payload_hash=excluded.history_payload_hash,
                 detail_payload_hash=excluded.detail_payload_hash,
                 detail_complete=excluded.detail_complete,
                 updated_at=excluded.updated_at, last_synced_at=excluded.last_synced_at""",
                values,
            )
            for table in (
                "activity_summary_metrics",
                "activity_streams",
                "activity_laps",
                "activity_notes",
                "activity_quality_flags",
                "activity_provenance",
            ):
                self.connection.execute(
                    f"DELETE FROM {table} WHERE activity_track_id=?", (track_id,)
                )

            summary_rows = []
            for name, metric in canonical.get("summary", {}).items():
                value = metric.get("value")
                value_real = (
                    float(value)
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                    else None
                )
                value_text = (
                    str(value) if value is not None and value_real is None else None
                )
                provenance = metric.get("provenance") or {}
                summary_rows.append((
                    track_id, name, value_real, value_text, metric.get("unit"),
                    metric.get("status", "UNKNOWN"),
                    json_text(metric.get("raw_value")),
                    provenance.get("source_path"),
                    metric.get("semantic_confidence"),
                    json_text(provenance), metric.get("reason"),
                ))
            self.connection.executemany(
                """INSERT INTO activity_summary_metrics
                (activity_track_id, metric_name, value_real, value_text, unit,
                 status, raw_value_json, source_path, semantic_confidence,
                 provenance_json, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                summary_rows,
            )

            for stream_type, stream in canonical.get("streams", {}).items():
                metadata = {
                    key: value for key, value in stream.items()
                    if key not in {
                        "samples", "records", "raw_values", "provenance"
                    }
                }
                cursor = self.connection.execute(
                    """INSERT INTO activity_streams
                    (activity_track_id, stream_type, status, sample_count, unit,
                     source_path, semantic_confidence, provenance_json,
                     metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        track_id, stream_type, stream.get("status", "UNKNOWN"),
                        stream.get("sample_count", 0), stream.get("unit"),
                        stream.get("source_path"),
                        stream.get("semantic_confidence"),
                        json_text(stream.get("provenance") or {}),
                        json_text(metadata),
                    ),
                )
                stream_id = int(cursor.lastrowid)
                records = stream.get("samples", stream.get("records", []))
                sample_rows = []
                for index, sample in enumerate(records):
                    value = sample.get("value", sample.get("value_m"))
                    value_real = (
                        float(value)
                        if isinstance(value, (int, float))
                        and not isinstance(value, bool) else None
                    )
                    value_text = (
                        str(value)
                        if value is not None and value_real is None else None
                    )
                    sample_rows.append((
                        stream_id, index, sample.get("offset_s"),
                        sample.get("timestamp"), value_real, value_text,
                        sample.get("latitude"), sample.get("longitude"),
                        sample.get("status", stream.get("status")),
                        json_text(
                            sample.get(
                                "raw",
                                sample.get(
                                    "raw_value",
                                    sample.get("raw_components", sample),
                                ),
                            )
                        ),
                    ))
                self.connection.executemany(
                    """INSERT INTO activity_samples
                    (stream_id, sample_index, offset_s, timestamp, value_real,
                     value_text, latitude, longitude, status, raw_value_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    sample_rows,
                )

            lap_rows = []
            for lap_type, structure in canonical.get("laps", {}).items():
                for index, record in enumerate(structure.get("records", [])):
                    lap_rows.append((
                        track_id, lap_type, index,
                        structure.get("status", "UNKNOWN"),
                        json_text(record), structure.get("source_path"),
                        json_text(structure.get("provenance") or {}),
                    ))
            self.connection.executemany(
                """INSERT INTO activity_laps
                (activity_track_id, lap_type, record_index, status,
                 raw_components_json, source_path, provenance_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                lap_rows,
            )

            notes = canonical.get("notes", {})
            self.connection.execute(
                """INSERT INTO activity_notes
                (activity_track_id, present, note_text, note_length, source_path,
                 evidence, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    track_id, int(bool(notes.get("present"))), notes.get("text"),
                    notes.get("length", 0), notes.get("source_path"),
                    notes.get("evidence"), now,
                ),
            )
            self.connection.executemany(
                "INSERT INTO activity_quality_flags(activity_track_id, flag) "
                "VALUES (?, ?)",
                [
                    (track_id, flag)
                    for flag in canonical.get("quality", {}).get("flags", [])
                ],
            )
            provenance_rows = [
                (track_id, key, json_text(value))
                for key, value in canonical.get("provenance", {}).items()
            ]
            self.connection.executemany(
                """INSERT INTO activity_provenance
                (activity_track_id, provenance_key, provenance_json)
                VALUES (?, ?, ?)""",
                provenance_rows,
            )
            return "updated" if existing else "inserted"

    def activity_status(self) -> dict[str, Any]:
        count = int(self.connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0])
        date_row = self.connection.execute(
            "SELECT MIN(local_activity_date), MAX(local_activity_date) "
            "FROM activities"
        ).fetchone()
        sports = [
            dict(row) for row in self.connection.execute(
                "SELECT sport_family, sport_name, COUNT(*) AS count "
                "FROM activities GROUP BY sport_family, sport_name "
                "ORDER BY sport_family, sport_name"
            ).fetchall()
        ]
        stream_coverage = [
            dict(row) for row in self.connection.execute(
                "SELECT stream_type, status, COUNT(*) AS activity_count, "
                "SUM(sample_count) AS sample_count FROM activity_streams "
                "GROUP BY stream_type, status ORDER BY stream_type, status"
            ).fetchall()
        ]
        latest = self.connection.execute(
            "SELECT track_id, sport_name, local_activity_date, start_time "
            "FROM activities ORDER BY COALESCE(start_time, local_activity_date) "
            "DESC LIMIT 1"
        ).fetchone()
        sync = self.connection.execute(
            "SELECT * FROM activity_sync_runs WHERE finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        latest_success = self.connection.execute(
            "SELECT finished_at FROM activity_sync_runs WHERE status='ok' "
            "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "database_path": str(self.path),
            "schema_version": SCHEMA_VERSION,
            "activity_count": count,
            "date_range": {"from": date_row[0], "to": date_row[1]},
            "sport_counts": sports,
            "latest_activity": dict(latest) if latest else None,
            "latest_sync": _safe_activity_sync_row(sync),
            "latest_successful_sync_at": latest_success[0] if latest_success else None,
            "detail_complete_count": int(self.connection.execute(
                "SELECT COUNT(*) FROM activities WHERE detail_complete=1"
            ).fetchone()[0]),
            "stream_coverage": stream_coverage,
            "notes": {
                "activities_with_notes": int(self.connection.execute(
                    "SELECT COUNT(*) FROM activity_notes WHERE present=1"
                ).fetchone()[0])
            },
            "quality_flag_count": int(self.connection.execute(
                "SELECT COUNT(*) FROM activity_quality_flags"
            ).fetchone()[0]),
        }

    def inspect_activity(
        self, track_id: str | int, *, include_notes: bool = False
    ) -> dict[str, Any] | None:
        activity = self.connection.execute(
            """SELECT track_id, native_type, sport_mode, sport_name, sport_family,
            zepp_coach_mode, mapping_confidence, local_activity_date, start_time,
            end_time, timezone, duration_s, detail_complete, created_at,
            updated_at, last_synced_at FROM activities WHERE track_id=?""",
            (str(track_id),),
        ).fetchone()
        if activity is None:
            return None
        metrics = [
            dict(row) for row in self.connection.execute(
                """SELECT metric_name, value_real, value_text, unit, status,
                source_path, semantic_confidence, reason
                FROM activity_summary_metrics WHERE activity_track_id=?
                ORDER BY metric_name""",
                (str(track_id),),
            ).fetchall()
        ]
        streams = [
            dict(row) for row in self.connection.execute(
                """SELECT stream_type, status, sample_count, unit, source_path,
                semantic_confidence FROM activity_streams
                WHERE activity_track_id=? ORDER BY stream_type""",
                (str(track_id),),
            ).fetchall()
        ]
        laps = [
            dict(row) for row in self.connection.execute(
                "SELECT lap_type, COUNT(*) AS record_count FROM activity_laps "
                "WHERE activity_track_id=? GROUP BY lap_type ORDER BY lap_type",
                (str(track_id),),
            ).fetchall()
        ]
        note = self.connection.execute(
            "SELECT present, note_length, note_text, source_path, evidence "
            "FROM activity_notes WHERE activity_track_id=?",
            (str(track_id),),
        ).fetchone()
        notes = dict(note) if note else {
            "present": 0, "note_length": 0, "source_path": None, "evidence": None
        }
        if not include_notes:
            notes.pop("note_text", None)
            notes["text_suppressed"] = True
        flags = [
            row[0] for row in self.connection.execute(
                "SELECT flag FROM activity_quality_flags "
                "WHERE activity_track_id=? ORDER BY flag",
                (str(track_id),),
            ).fetchall()
        ]
        return {
            "activity": dict(activity),
            "summary_metrics": metrics,
            "streams": streams,
            "laps": laps,
            "notes": notes,
            "quality_flags": flags,
            "privacy": (
                "Coordinates, sample/raw values, source identifiers, and raw "
                "payloads omitted; notes text requires explicit opt-in."
            ),
        }

    def query_activities(
        self,
        from_date: str,
        to_date: str,
        *,
        sport_family: str | None = None,
        native_type: int | None = None,
        sport_mode: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["local_activity_date BETWEEN ? AND ?"]
        parameters: list[Any] = [from_date, to_date]
        for column, value in (
            ("sport_family", sport_family),
            ("native_type", native_type),
            ("sport_mode", sport_mode),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                parameters.append(value)
        return [
            dict(row) for row in self.connection.execute(
                "SELECT track_id, native_type, sport_mode, sport_name, "
                "sport_family, local_activity_date, start_time, duration_s "
                "FROM activities WHERE " + " AND ".join(clauses)
                + " ORDER BY local_activity_date, start_time, track_id",
                parameters,
            ).fetchall()
        ]

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
        for table in (
            "activities", "activity_summary_metrics", "activity_streams",
            "activity_samples", "activity_laps", "activity_notes",
            "activity_quality_flags", "activity_provenance",
            "activity_sync_runs",
        ):
            counts[table] = int(
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
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
            "exertion": (
                "recovery_factor",
                "recovery_factor_id",
                "total_score",
                "activity_score",
                "exercise_score",
                "target_score",
                "completion_percent",
                "atl",
                "ctl",
                "tsb",
                "insight_state",
                "exercise_plan_intensity",
                "exercise_plan_duration",
                "exercise_plan_hr_lower",
                "exercise_plan_hr_upper",
            ),
            "readiness": ("status", "hrv_score", "sleep_hrv", "rhr_score", "sleep_rhr", "phy_score", "ment_score", "skin_temp_score", "ahi_score", "rdns_score"),
            "sleep_related_readiness": ("sleep_hrv", "sleep_rhr", "ahi_score", "ahi_baseline", "rdns_score"),
            "lifeload": ("life_load",),
        }
        output_names = {
            "bio_charge_wake": "bioChargeWake", "wake_charge": "wakeCharge", "physical_wake": "physicalWake", "mental_wake": "mentalWake", "daily_fitness_score": "dailyFitnessScore", "stress_fitness_score": "stressFitnessScore", "exertion_score": "exertionScore",
            "recovery_factor": "recoveryFactor", "recovery_factor_id": "recoveryFactorID", "total_score": "totalScore", "activity_score": "activityScore", "exercise_score": "exerciseScore", "target_score": "targetScore", "completion_percent": "completionPercent", "atl": "atl", "ctl": "ctl", "tsb": "tsb", "insight_state": "insightState", "exercise_plan_intensity": "exercise_plan_intensity", "exercise_plan_duration": "exercise_plan_duration", "exercise_plan_hr_lower": "exercise_plan_hr_lower", "exercise_plan_hr_upper": "exercise_plan_hr_upper", "status": "status", "hrv_score": "hrvScore", "sleep_hrv": "sleepHRV", "rhr_score": "rhrScore", "sleep_rhr": "sleepRHR", "phy_score": "phyScore", "ment_score": "mentScore", "skin_temp_score": "skinTempScore", "ahi_score": "ahiScore", "ahi_baseline": "ahiBaseline", "rdns_score": "rdnsScore", "life_load": "lifeLoad",
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
            "activities", "activity_summary_metrics", "activity_streams",
            "activity_samples", "activity_laps", "activity_notes",
            "activity_quality_flags", "activity_provenance",
            "activity_sync_runs",
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
        "exertion": (
            "event_date",
            "timestamp_ms",
            "recovery_factor",
            "recovery_factor_id",
            "total_score",
            "activity_score",
            "exercise_score",
            "target_score",
            "completion_percent",
            "atl",
            "ctl",
            "tsb",
            "insight_state",
            "exercise_plan_intensity",
            "exercise_plan_duration",
            "exercise_plan_hr_lower",
            "exercise_plan_hr_upper",
        ),
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
            "event_date": "date", "timestamp_ms": "timestamp", "start_time_ms": "start_time", "offset_ms": "s", "sample_timestamp_ms": "sample_timestamp", "end_offset_ms": "e", "bio_charge_wake": "bioChargeWake", "wake_charge": "wakeCharge", "physical_wake": "physicalWake", "mental_wake": "mentalWake", "daily_fitness_score": "dailyFitnessScore", "stress_fitness_score": "stressFitnessScore", "exertion_score": "exertionScore", "recovery_factor": "recoveryFactor", "recovery_factor_id": "recoveryFactorID", "total_score": "totalScore", "activity_score": "activityScore", "exercise_score": "exerciseScore", "target_score": "targetScore", "completion_percent": "completionPercent", "insight_state": "insightState", "exercise_plan_intensity": "exercise_plan_intensity", "exercise_plan_duration": "exercise_plan_duration", "exercise_plan_hr_lower": "exercise_plan_hr_lower", "exercise_plan_hr_upper": "exercise_plan_hr_upper", "timestamp_update_ms": "timestampUpdate", "hrv_score": "hrvScore", "sleep_hrv": "sleepHRV", "rhr_score": "rhrScore", "sleep_rhr": "sleepRHR", "phy_score": "phyScore", "ment_score": "mentScore", "skin_temp_score": "skinTempScore", "ahi_score": "ahiScore", "ahi_baseline": "ahiBaseline", "rdns_score": "rdnsScore", "life_load": "lifeLoad", "insight_id": "insight_id", "track_id": "track_id", "threshold": "threshold", "parsed_json_extra": "parsed_json_extra", "raw_u": "raw_u",
        }
        for target, source in aliases.items():
            converted_row[target] = row.get(source)
        converted.append(converted_row)
    return [(domain_table(domain), converted, columns)]


def domain_table(domain: str) -> str:
    return {"hrv": "hrv_samples", "wake_energy": "wake_energy", "exertion": "exertion_records", "readiness": "readiness_records", "charge": "charge_records", "insights": "insight_records", "sleep_related_readiness": "sleep_related_readiness", "lifeload": "lifeload_records"}[domain]
