import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import requests

from zepp_db import Database, SCHEMA_VERSION
from zepp_health import (
    _activity_sync_dates,
    canonicalize_activity,
    main,
    sync_native_activities,
)


def history_record(
    track_id=1700000000,
    *,
    native_type=22,
    sport_mode=0,
    duration=600,
    **fields,
):
    return {
        "trackid": track_id,
        "source": "private-native-source",
        "type": native_type,
        "sport_mode": sport_mode,
        "run_time": duration,
        "highPrecisionDistance": 1000,
        "avg_heart_rate": 120,
        "min_heart_rate": 90,
        "max_heart_rate": 150,
        "exercise_load": 20,
        "te": 3,
        "anaerobic_te": 1,
        "rpe": 5,
        **fields,
    }


def detail_payload(track_id=1700000000, **fields):
    return {
        "data": {
            "trackid": track_id,
            "longitude_latitude": "1420000000,4610000000;100,100;",
            "time": "0;1;",
            "altitude": "70000;70100;",
            "heart_rate": "0,90;1,10;",
            **fields,
        }
    }


class FakeActivityClient:
    def __init__(self, records, details, *, next_cursor=-1, history_failure=None):
        self.records = records
        self.details = details
        self.next_cursor = next_cursor
        self.history_failure = history_failure
        self.history_calls = []
        self.detail_calls = []

    def sport_history(
        self, sport, start_track_id, stop_track_id, *, need_sub_data=1
    ):
        self.history_calls.append(
            (sport, start_track_id, stop_track_id, need_sub_data)
        )
        if self.history_failure:
            raise self.history_failure
        return {
            "data": {
                "next": self.next_cursor,
                "summary": copy.deepcopy(self.records),
            }
        }

    def sport_detail(self, track_id, source):
        self.detail_calls.append((str(track_id), source))
        response = self.details[str(track_id)]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class ActivityStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "zepp.db")

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def _store(self, history=None, detail=None):
        history = history or history_record()
        detail = detail or detail_payload(history["trackid"])
        canonical = canonicalize_activity(
            history, detail, timezone_name="Europe/Ljubljana"
        )
        return self.db.store_canonical_activity(canonical, history, detail)

    def _parse_activity_sync_args(self, *arguments):
        captured = {}

        def capture(args):
            captured["args"] = args

        with (
            patch("zepp_health.cmd_sync_activities", side_effect=capture),
            patch.object(sys, "argv", ["zepp_health.py", "sync-activities", *arguments]),
        ):
            main()
        return captured["args"]

    def test_activity_sync_cli_date_modes_distinguish_default_days(self):
        default_args = self._parse_activity_sync_args()
        self.assertIsNone(default_args.days)
        default_from, default_to = _activity_sync_dates(default_args)
        self.assertEqual(
            (date.fromisoformat(default_to) - date.fromisoformat(default_from)).days,
            6,
        )

        rolling_args = self._parse_activity_sync_args("--days", "14")
        self.assertEqual(rolling_args.days, 14)
        rolling_from, rolling_to = _activity_sync_dates(rolling_args)
        self.assertEqual(
            (date.fromisoformat(rolling_to) - date.fromisoformat(rolling_from)).days,
            13,
        )

        explicit_args = self._parse_activity_sync_args(
            "--from-date", "2026-07-26", "--to-date", "2026-08-11"
        )
        self.assertIsNone(explicit_args.days)
        self.assertEqual(
            _activity_sync_dates(explicit_args),
            ("2026-07-26", "2026-08-11"),
        )

    def test_activity_sync_cli_rejects_conflicting_or_incomplete_ranges(self):
        mixed_args = self._parse_activity_sync_args(
            "--days", "7", "--from-date", "2026-07-26", "--to-date", "2026-08-11"
        )
        with self.assertRaisesRegex(ValueError, "--days cannot be combined"):
            _activity_sync_dates(mixed_args)

        for arguments in (
            ("--from-date", "2026-07-26"),
            ("--to-date", "2026-08-11"),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ValueError, "provided together"):
                    _activity_sync_dates(self._parse_activity_sync_args(*arguments))

        invalid_range_args = self._parse_activity_sync_args(
            "--from-date", "2026-08-11", "--to-date", "2026-07-26"
        )
        with self.assertRaisesRegex(ValueError, "must not be before"):
            _activity_sync_dates(invalid_range_args)

    def test_insert_repeated_unchanged_and_relational_children(self):
        self.assertEqual(self._store(), "inserted")
        self.assertEqual(self._store(), "unchanged")
        counts = self.db.status()["record_counts"]
        self.assertEqual(counts["activities"], 1)
        self.assertGreater(counts["activity_summary_metrics"], 10)
        self.assertEqual(counts["activity_streams"], 7)
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM activity_samples"
            ).fetchone()[0],
            6,
        )
        coordinates = self.db.connection.execute(
            "SELECT latitude, longitude FROM activity_samples "
            "WHERE latitude IS NOT NULL"
        ).fetchall()
        self.assertEqual(len(coordinates), 2)
        self.assertEqual(
            self.db.connection.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )
        self.assertEqual(
            self.db.connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )

    def test_changed_summary_and_notes_upsert_without_duplicate(self):
        first_history = history_record()
        first_detail = detail_payload(memo="First private note")
        self.assertEqual(self._store(first_history, first_detail), "inserted")
        changed_history = history_record(duration=700, exercise_load=25)
        changed_detail = detail_payload(memo="Revised private note")
        self.assertEqual(
            self._store(changed_history, changed_detail), "updated"
        )
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM activities"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.connection.execute(
                "SELECT duration_s FROM activities"
            ).fetchone()[0],
            700,
        )
        note = self.db.connection.execute(
            "SELECT note_text, note_length FROM activity_notes"
        ).fetchone()
        self.assertEqual(tuple(note), ("Revised private note", 20))

    def test_raw_detail_only_change_updates_forensic_reference(self):
        first = detail_payload(unmodeled_field="first")
        self.assertEqual(self._store(history_record(), first), "inserted")
        first_hash = self.db.connection.execute(
            "SELECT detail_payload_hash FROM activities"
        ).fetchone()[0]
        revised = detail_payload(unmodeled_field="second")
        self.assertEqual(self._store(history_record(), revised), "updated")
        second_hash = self.db.connection.execute(
            "SELECT detail_payload_hash FROM activities"
        ).fetchone()[0]
        self.assertNotEqual(first_hash, second_hash)

    def test_stream_refresh_replaces_samples_atomically(self):
        self._store()
        stream_id = self.db.connection.execute(
            "SELECT id FROM activity_streams WHERE stream_type='gps'"
        ).fetchone()[0]
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM activity_samples WHERE stream_id=?",
                (stream_id,),
            ).fetchone()[0],
            2,
        )
        changed = detail_payload(
            longitude_latitude="1420000000,4610000000;",
            time="0;",
            altitude="70000;",
            heart_rate="0,90;",
        )
        self.assertEqual(self._store(history_record(), changed), "updated")
        self.assertEqual(
            self.db.connection.execute(
                """SELECT COUNT(*) FROM activity_samples s
                JOIN activity_streams st ON st.id=s.stream_id
                WHERE st.stream_type='gps'"""
            ).fetchone()[0],
            1,
        )

    def test_failed_stream_replacement_rolls_back_old_activity(self):
        self._store()
        self.db.connection.execute(
            """CREATE TRIGGER fail_stream_insert
            BEFORE INSERT ON activity_streams
            BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"""
        )
        self.db.connection.commit()
        changed = detail_payload(altitude="70000;70100;70200;")
        with self.assertRaises(sqlite3.IntegrityError):
            self._store(history_record(duration=700), changed)
        self.assertEqual(
            self.db.connection.execute(
                "SELECT duration_s FROM activities"
            ).fetchone()[0],
            600,
        )
        self.assertEqual(
            self.db.connection.execute(
                """SELECT COUNT(*) FROM activity_samples s
                JOIN activity_streams st ON st.id=s.stream_id
                WHERE st.stream_type='altitude'"""
            ).fetchone()[0],
            2,
        )

    def test_open_water_sentinel_and_gravel_optional_power_persist_factually(self):
        swim_history = history_record(
            1700000100, native_type=15, sport_mode=0
        )
        swim_detail = detail_payload(
            1700000100, altitude="-2000000;-2000000;"
        )
        self._store(swim_history, swim_detail)
        altitude = self.db.connection.execute(
            "SELECT status FROM activity_streams WHERE activity_track_id=? "
            "AND stream_type='altitude'",
            ("1700000100",),
        ).fetchone()[0]
        self.assertEqual(altitude, "SENTINEL_UNAVAILABLE")
        self.assertEqual(
            self.db.connection.execute(
                """SELECT COUNT(*) FROM activity_samples s
                JOIN activity_streams st ON st.id=s.stream_id
                WHERE st.activity_track_id=? AND st.stream_type='altitude'
                AND s.value_real IS NOT NULL""",
                ("1700000100",),
            ).fetchone()[0],
            0,
        )

        gravel_history = history_record(
            1700000200, native_type=208, sport_mode=0
        )
        gravel_detail = detail_payload(
            1700000200, cadence="0,80;1,1;", power_meter=""
        )
        self._store(gravel_history, gravel_detail)
        statuses = dict(self.db.connection.execute(
            "SELECT stream_type, status FROM activity_streams "
            "WHERE activity_track_id=?",
            ("1700000200",),
        ).fetchall())
        self.assertEqual(statuses["cadence"], "AVAILABLE")
        self.assertEqual(
            statuses["power"], "SUPPORTED_BUT_NOT_RECORDED"
        )

    def test_ski_descent_and_pool_laps_preserve_sport_semantics(self):
        ski_history = history_record(
            1700000300,
            native_type=105,
            altitude_ascend=0,
            altitude_descend=5921,
            climb_dis_descend=28133,
        )
        self._store(ski_history, detail_payload(1700000300))
        metrics = {
            row["metric_name"]: (row["value_real"], row["status"])
            for row in self.db.connection.execute(
                "SELECT metric_name, value_real, status "
                "FROM activity_summary_metrics WHERE activity_track_id=?",
                ("1700000300",),
            )
        }
        self.assertEqual(metrics["vertical_descent_m"], (5921, "AVAILABLE"))
        self.assertEqual(
            metrics["ski_vertical_m"], (5921, "AVAILABLE")
        )
        self.assertEqual(
            metrics["reported_elevation_gain_m"], (0, "AVAILABLE")
        )

        pool_history = history_record(
            1700000400, native_type=14, sport_mode=0
        )
        pool_detail = detail_payload(
            1700000400,
            longitude_latitude="",
            time="",
            altitude="",
            lap="1,2;3,4;",
            pool_swim_pace="5,6;",
        )
        self._store(pool_history, pool_detail)
        gps = self.db.connection.execute(
            "SELECT status FROM activity_streams WHERE activity_track_id=? "
            "AND stream_type='gps'",
            ("1700000400",),
        ).fetchone()[0]
        self.assertEqual(gps, "NOT_APPLICABLE")
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM activity_laps WHERE activity_track_id=?",
                ("1700000400",),
            ).fetchone()[0],
            3,
        )

    def test_free_diving_depth_and_laps_persist_without_elevation(self):
        diving_history = history_record(
            1786105959, native_type=196, sport_mode=0, duration=477,
            maximumDepth=6.0493865, numberOfDives=3,
            maximumDiveSpeed=1.7465142, avg_temperature=28.942812,
            totalDiveTimeWithMillis=78370, avgDiveTimeWithMillis=15674,
            maxDiveTimeWithMillis=19879, totalSurfaceTimeWithMillis=340560,
            avgSurfaceTimeWithMillis=68112, altitude_ascend=-1,
            altitude_descend=-1, elevationGain=-100, elevationLoss=-100,
        )
        diving_detail = detail_payload(
            1786105959,
            altitude="-2000000;-2000000;-2000000;",
            divingDepth="0,0.0,0;1,2.5,0;1,7.5,0;",
            lap="1,2,3;4,5,6;7,8,9;",
            temperature="0,28.85;1,0.02;1,-0.01;",
        )
        self.assertEqual(self._store(diving_history, diving_detail), "inserted")
        metrics = {
            row["metric_name"]: (row["value_real"], row["status"])
            for row in self.db.connection.execute(
                "SELECT metric_name, value_real, status "
                "FROM activity_summary_metrics WHERE activity_track_id=?",
                ("1786105959",),
            )
        }
        self.assertEqual(metrics["max_depth_m"], (6.0493865, "AVAILABLE"))
        for name in ("elevation_gain_m", "elevation_loss_m", "ski_vertical_m"):
            self.assertEqual(metrics[name], (None, "NOT_APPLICABLE"))
        depth_values = [row[0] for row in self.db.connection.execute(
            "SELECT s.value_real FROM activity_samples s "
            "JOIN activity_streams st ON st.id=s.stream_id "
            "WHERE st.activity_track_id=? AND st.stream_type='depth' "
            "ORDER BY s.sample_index", ("1786105959",)
        )]
        self.assertEqual(depth_values, [0.0, 2.5, 10.0])
        self.assertEqual(self.db.connection.execute(
            "SELECT COUNT(*) FROM activity_laps WHERE activity_track_id=?",
            ("1786105959",),
        ).fetchone()[0], 3)

    def test_sync_is_incremental_zero_new_is_ok_and_changed_refreshes(self):
        record = history_record()
        stale_run = self.db.start_activity_sync(
            "2023-11-13", "2023-11-13", "Europe/Ljubljana"
        )
        client = FakeActivityClient(
            [record], {str(record["trackid"]): detail_payload()}
        )
        first = sync_native_activities(
            client, self.db, "2023-11-14", "2023-11-14"
        )
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(len(client.detail_calls), 1)
        self.assertEqual(
            self.db.connection.execute(
                "SELECT status FROM activity_sync_runs WHERE id=?",
                (stale_run,),
            ).fetchone()[0],
            "interrupted",
        )

        second = sync_native_activities(
            client, self.db, "2023-11-14", "2023-11-14"
        )
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["detail_fetch_skipped"], 1)
        self.assertEqual(len(client.detail_calls), 1)

        client.records[0]["exercise_load"] = 30
        third = sync_native_activities(
            client, self.db, "2023-11-14", "2023-11-14"
        )
        self.assertEqual(third["updated"], 1)
        self.assertEqual(len(client.detail_calls), 2)

        empty = FakeActivityClient([], {})
        zero = sync_native_activities(
            empty, self.db, "2023-11-15", "2023-11-15"
        )
        self.assertEqual(zero["status"], "ok")
        self.assertEqual(zero["activities_seen"], 0)
        self.assertIsNotNone(
            self.db.activity_status()["latest_successful_sync_at"]
        )
        start_bound = client.history_calls[0][1]
        stop_bound = client.history_calls[0][2]
        self.assertLess(start_bound, stop_bound)

    def test_forced_detail_refresh_updates_notes(self):
        record = history_record()
        client = FakeActivityClient(
            [record], {str(record["trackid"]): detail_payload(memo="First")}
        )
        sync_native_activities(client, self.db, "2023-11-14", "2023-11-14")
        client.details[str(record["trackid"])] = detail_payload(memo="Revised")
        refreshed = sync_native_activities(
            client,
            self.db,
            "2023-11-14",
            "2023-11-14",
            refresh_details=True,
        )
        self.assertEqual(refreshed["updated"], 1)
        self.assertEqual(
            self.db.connection.execute(
                "SELECT note_text FROM activity_notes"
            ).fetchone()[0],
            "Revised",
        )

    def test_refresh_repairs_legacy_serialized_memo_without_duplicates(self):
        record = history_record(native_type=130)
        note = (
            "BACK SQUAT\n5x5\n\nBulgarian Split Squat\n4x8 / leg\n\n"
            "BENCH PRESS\n5x5"
        )
        legacy_memo = json.dumps({
            "summary": note,
            "groupMemos": [],
            "onlyUpdateSummary": False,
        })
        detail = detail_payload(memo=legacy_memo)
        legacy = canonicalize_activity(record, detail, timezone_name="Europe/Ljubljana")
        legacy["notes"] = {
            "present": True,
            "text": legacy_memo,
            "length": len(legacy_memo),
            "source_path": "detail.memo",
            "evidence": legacy["notes"]["evidence"],
        }
        self.assertEqual(self.db.store_canonical_activity(legacy, record, detail), "inserted")
        before_counts = {
            table: self.db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("activities", "activity_notes", "activity_summary_metrics", "activity_streams")
        }
        client = FakeActivityClient([record], {str(record["trackid"]): detail})

        repaired = sync_native_activities(
            client,
            self.db,
            "2023-11-14",
            "2023-11-14",
            refresh_details=True,
        )
        self.assertEqual(repaired["updated"], 1)
        repaired_note = self.db.connection.execute(
            "SELECT present, note_text, note_length, source_path FROM activity_notes"
        ).fetchone()
        self.assertEqual(tuple(repaired_note), (1, note, len(note), "detail.memo.summary"))
        after_counts = {
            table: self.db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before_counts
        }
        self.assertEqual(after_counts, before_counts)

        replay = sync_native_activities(
            client,
            self.db,
            "2023-11-14",
            "2023-11-14",
            refresh_details=True,
        )
        self.assertEqual(replay["unchanged"], 1)
        self.assertEqual(replay["updated"], 0)

    def test_partial_detail_failure_and_identity_mismatch_preserve_valid_rows(self):
        good = history_record()
        failed = history_record(1700000500)
        client = FakeActivityClient(
            [good, failed],
            {
                str(good["trackid"]): detail_payload(),
                str(failed["trackid"]): requests.ConnectionError("private"),
            },
        )
        result = sync_native_activities(
            client, self.db, "2023-11-14", "2023-11-14"
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["detail_fetch_failed"], 1)
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM activities"
            ).fetchone()[0],
            1,
        )
        self.assertNotIn("private", json.dumps(result))

        mismatch_record = history_record(1700000600)
        mismatch = FakeActivityClient(
            [mismatch_record],
            {str(mismatch_record["trackid"]): detail_payload(1700000601)},
        )
        mismatch_result = sync_native_activities(
            mismatch, self.db, "2023-11-14", "2023-11-14"
        )
        self.assertEqual(mismatch_result["status"], "partial")
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM activities WHERE track_id=?",
                ("1700000600",),
            ).fetchone()[0],
            0,
        )

    def test_status_inspect_privacy_and_query_filters(self):
        detail = detail_payload(
            memo="Private note body",
            deviceSN="private-device",
            secretUrl="https://secret.example/private",
        )
        self._store(history_record(deviceId="private-watch"), detail)
        status = self.db.activity_status()
        rendered_status = json.dumps(status)
        self.assertNotIn("Private note", rendered_status)
        self.assertNotIn("46.1", rendered_status)
        safe = self.db.inspect_activity(1700000000)
        rendered_safe = json.dumps(safe)
        self.assertNotIn("Private note", rendered_safe)
        self.assertNotIn("private-native-source", rendered_safe)
        self.assertNotIn("46.1", rendered_safe)
        raw_payloads = " ".join(
            row[0] for row in self.db.connection.execute(
                "SELECT payload_json FROM raw_payloads WHERE domain='activities'"
            )
        )
        self.assertNotIn("private-watch", raw_payloads)
        self.assertNotIn("private-device", raw_payloads)
        self.assertNotIn("secret.example", raw_payloads)
        private = self.db.inspect_activity(1700000000, include_notes=True)
        self.assertEqual(private["notes"]["note_text"], "Private note body")
        rows = self.db.query_activities(
            "2023-11-01", "2023-11-30", sport_family="Hiking"
        )
        self.assertEqual([row["track_id"] for row in rows], ["1700000000"])
        self.assertEqual(
            self.db.query_activities(
                "2023-11-01", "2023-11-30", sport_family="Swimming"
            ),
            [],
        )

    def test_v3_migration_is_idempotent_and_health_data_is_unchanged(self):
        self.db.store_domain_rows(
            "hrv",
            [{"date": "2026-01-01", "start_time": 1, "s": 0, "hrv": 42}],
        )
        path = self.db.path
        self.db.close()
        connection = sqlite3.connect(path)
        for table in (
            "activity_samples", "activity_streams", "activity_laps",
            "activity_notes", "activity_quality_flags", "activity_provenance",
            "activity_summary_metrics", "activities", "activity_sync_runs",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("PRAGMA user_version = 3")
        connection.execute(
            "UPDATE schema_meta SET value='3' WHERE key='schema_version'"
        )
        connection.commit()
        connection.close()
        self.db = Database(path)
        self.assertEqual(
            self.db.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM hrv_samples"
            ).fetchone()[0],
            1,
        )
        self.db.migrate()
        self.assertEqual(
            self.db.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(
            self.db.connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )


if __name__ == "__main__":
    unittest.main()
