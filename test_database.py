import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import configparser
import subprocess
import sys

import requests

from zepp_db import (
    Database,
    SCHEMA_VERSION,
    backup_database,
    inspect_database_file,
    resolve_db_path,
    restore_database,
)
from zepp_health import backfill_native_metrics, sync_native_metrics
from zepp_ops import SyncLock, lock_is_held


class FakeClient:
    def __init__(self, responses, failures=None):
        self.responses = responses
        self.failures = set(failures or ())

    def events(self, event_type, sub_type, from_ms, to_ms, *, limit=2000, reverse=True):
        key = (event_type, sub_type)
        if key in self.failures:
            raise requests.ConnectionError("fixture network failure")
        return copy.deepcopy(self.responses[key])


def fixture_responses():
    return {
        ("HRVRMSSD", "real_data"): {"items": [{
            "userId": "fixture-user",
            "eventType": "HRVRMSSD", "subType": "real_data",
            "date": "2026-07-08", "value": {"startTime": 1000000, "samples": [{"s": 1000, "hrv": 42, "u": 255}]},
        }]},
        ("Charge", "wake_data"): {"items": [{
            "userId": "fixture-user", "date": "2026-07-08",
            "value": {"startTime": 1000000, "samples": [{"s": 1000, "bioChargeWake": 72, "wakeCharge": 80, "physicalWake": 60, "mentalWake": 70, "dailyFitnessScore": 0.7, "stressFitnessScore": 0.6, "exertionScore": 10}]},
        }]},
        ("exertion", "algo_result"): {"items": [{
            "date": "2026-07-08", "timestamp": 1000000,
            "value": {
                "recoveryFactor": 2,
                "recoveryFactorID": 3,
                "totalScore": 20,
                "activityScore": 8,
                "exerciseScore": 12,
                "targetScore": 15,
                "completionPercent": 133,
                "atl": 4,
                "ctl": 5,
                "tsb": 1,
                "insightState": 6,
                "exercisePlan": {
                    "intensity": 1,
                    "duration": 10,
                    "heartRateLower": 120,
                    "heartRateUpper": 150,
                },
            },
        }]},
        ("readiness", "watch_score"): {"items": [{
            "userId": "fixture-user", "date": "2026-07-08", "timestamp": 1000000, "timestampUpdate": 1000001,
            "value": {"status": 200, "phyScore": 255, "sleepHRV": 42, "sleepRHR": 52, "ahiScore": 90, "ahiBaseline": 1, "rdnsScore": 80},
        }]},
        ("Charge", "real_data"): {"items": [{
            "date": "2026-07-08", "value": {"startTime": 1000000, "samples": [{"s": 1000, "e": 2000, "total": 50, "physical": 25.5, "mental": 24.5, "u": 9}]},
        }]},
        ("Charge", "insight_data"): {"items": [{
            "date": "2026-07-08", "timestamp": 1000000, "startTime": 1000000,
            "samples": [{"insightId": 1, "insight": 66, "type": 2, "diff": -4, "slope": -0.1, "s": 1000, "e": 2000, "trackId": 3, "thres": 0, "u": 255, "jsonExtra": '{"x":1}'}],
        }]},
        ("LifeLoad", "summary"): {"items": []},
    }


class DatabaseTests(unittest.TestCase):
    def _freshness_db(self, directory, day=None, domains=()):
        db = Database(Path(directory) / "freshness.db")
        if day and "hrv" in domains:
            db.store_domain_rows("hrv", [{"date": day, "start_time": 1, "s": 0, "hrv": 42}])
        if day and "wake_energy" in domains:
            db.store_domain_rows("wake_energy", [{"date": day, "start_time": 1, "s": 0, "bioChargeWake": 70}])
        if day and "readiness" in domains:
            db.store_domain_rows("readiness", [{"date": day, "timestamp": 1, "timestampUpdate": 2, "status": 200}])
        if day and "exertion" in domains:
            db.store_domain_rows("exertion", [{"date": day, "timestamp": 1, "totalScore": 10}])
        return db

    def test_factual_freshness_complete_partial_pending_and_unavailable(self):
        now = datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc)  # 09:00 Ljubljana
        with tempfile.TemporaryDirectory() as directory:
            db = self._freshness_db(
                directory,
                "2026-07-24",
                ("hrv", "wake_energy", "readiness", "exertion"),
            )
            complete = db.factual_freshness(now)
            self.assertEqual(complete["morning_data_status"], "complete")
            self.assertEqual(complete["domain_data_freshness"]["sleep"]["coverage"], "unavailable")
            self.assertEqual(complete["domain_data_freshness"]["exertion"]["latest_date"], "2026-07-24")
            db.close()

        with tempfile.TemporaryDirectory() as directory:
            db = self._freshness_db(directory, "2026-07-24", ("hrv",))
            self.assertEqual(db.factual_freshness(now)["morning_data_status"], "partial")
            db.close()

        with tempfile.TemporaryDirectory() as directory:
            db = self._freshness_db(
                directory,
                "2026-07-23",
                ("hrv", "wake_energy", "readiness"),
            )
            run_id = db.start_sync(7, 1, 2)
            db.finish_sync(run_id, "ok", {})
            pending = db.factual_freshness(now)
            self.assertEqual(pending["morning_data_status"], "pending")
            self.assertEqual(pending["domain_data_freshness"]["hrv"]["coverage"], "yesterday")
            db.close()

        with tempfile.TemporaryDirectory() as directory:
            db = self._freshness_db(directory)
            self.assertEqual(db.factual_freshness(now)["morning_data_status"], "unavailable")
            db.close()

    def test_factual_freshness_uses_ljubljana_dates_across_dst(self):
        with tempfile.TemporaryDirectory() as directory:
            db = self._freshness_db(directory, "2026-03-29", ("hrv",))
            before_morning = db.factual_freshness(
                datetime(2026, 3, 28, 23, 30, tzinfo=timezone.utc)
            )
            self.assertEqual(before_morning["today"], "2026-03-29")
            self.assertEqual(before_morning["morning_expectation"], "before_first_morning_sync")
            self.assertEqual(before_morning["domain_data_freshness"]["hrv"]["coverage"], "today")
            db.close()

    def test_backfill_is_chunked_resumable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            fake = FakeClient(fixture_responses(), {("HRVRMSSD", "real_data")})
            first = backfill_native_metrics(fake, db, 61, chunk_days=30)
            self.assertEqual(first["status"], "partial")
            hrv = next(row for row in first["domains"] if row["domain"] == "hrv")
            self.assertEqual(hrv["status"], "error")
            self.assertEqual(hrv["chunks_completed"], 0)
            fake.failures.clear()
            second = backfill_native_metrics(fake, db, 61, chunk_days=30)
            self.assertEqual(second["status"], "ok")
            hrv = next(row for row in second["domains"] if row["domain"] == "hrv")
            self.assertEqual(hrv["status"], "complete")
            self.assertEqual(hrv["chunks_completed"], 3)
            before = db.status()["record_counts"]
            third = backfill_native_metrics(fake, db, 61, chunk_days=30)
            self.assertEqual(third["status"], "ok")
            after = db.status()["record_counts"]
            self.assertEqual({k: v for k, v in after.items() if k != "sync_runs"},
                             {k: v for k, v in before.items() if k != "sync_runs"})
            progress = db.connection.execute(
                "SELECT status, cursor_to_date FROM historical_sync_progress WHERE domain='hrv'"
            ).fetchone()
            self.assertEqual(tuple(progress), ("complete", hrv["target_from_date"]))
            db.close()

    def test_all_domain_sync_failure_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            responses = fixture_responses()
            result = sync_native_metrics(FakeClient(responses, set(responses)), db, 7)
            self.assertEqual(result["status"], "error")
            self.assertTrue(all(row["status"] == "error" for row in result["domains"]))
            db.close()

    def test_initialization_schema_and_path_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "zepp.db"
            db = Database(path)
            self.assertEqual(db.status()["schema_version"], SCHEMA_VERSION)
            self.assertTrue(path.exists())
            db.close()
            with patch.dict(os.environ, {"ZEPP_DB_PATH": "/tmp/from-env.db"}, clear=False):
                self.assertEqual(resolve_db_path(None, {"db_path": "/tmp/from-config.db"}), Path("/tmp/from-config.db"))
                self.assertEqual(resolve_db_path(None, {}), Path("/tmp/from-env.db"))
            self.assertEqual(resolve_db_path("/tmp/from-cli.db", {"db_path": "/tmp/config.db"}), Path("/tmp/from-cli.db"))

    def test_schema_v1_migrates_to_current_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            initial = Database(path)
            initial.close()
            connection = sqlite3.connect(path)
            connection.execute("DROP TABLE lifeload_records")
            connection.execute("DELETE FROM schema_meta")
            connection.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')")
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
            connection.close()
            db = Database(path)
            self.assertEqual(db.status()["schema_version"], SCHEMA_VERSION)
            self.assertIn("lifeload_records", db.status()["record_counts"])
            exertion_columns = {
                row[1]
                for row in db.connection.execute(
                    "PRAGMA table_info(exertion_records)"
                ).fetchall()
            }
            self.assertTrue({
                "recovery_factor_id",
                "target_score",
                "completion_percent",
                "insight_state",
                "exercise_plan_intensity",
                "exercise_plan_duration",
                "exercise_plan_hr_lower",
                "exercise_plan_hr_upper",
            }.issubset(exertion_columns))
            db.close()

    def test_sync_is_idempotent_and_preserves_unknowns_and_raw_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            fake = FakeClient(fixture_responses())
            first = sync_native_metrics(fake, db, 30)
            second = sync_native_metrics(fake, db, 30)
            self.assertEqual(first["status"], "ok")
            self.assertGreater(first["domains"][0]["inserted"], 0)
            self.assertGreater(second["domains"][0]["unchanged"], 0)
            status = db.status()
            self.assertEqual(status["record_counts"]["hrv_samples"], 1)
            self.assertEqual(status["record_counts"]["readiness_records"], 1)
            self.assertEqual(status["record_counts"]["sleep_related_readiness"], 1)
            self.assertEqual(status["record_counts"]["insight_records"], 1)
            self.assertEqual(status["record_counts"]["raw_payloads"], 7)

            exertion = db.connection.execute(
                """SELECT recovery_factor_id, target_score, completion_percent,
                          insight_state, exercise_plan_intensity,
                          exercise_plan_duration, exercise_plan_hr_lower,
                          exercise_plan_hr_upper
                   FROM exertion_records"""
            ).fetchone()
            self.assertEqual(
                tuple(exertion),
                ("3", "15", "133", "6", "1", "10", "120", "150"),
            )

            daily = db.read_daily_status("2026-07-08", "2026-07-08")[0]["exertion"]
            self.assertEqual(daily["recoveryFactorID"], "3")
            self.assertEqual(daily["targetScore"], "15")
            self.assertEqual(daily["completionPercent"], "133")
            self.assertEqual(daily["insightState"], "6")
            self.assertEqual(daily["exercise_plan_duration"], "10")
            self.assertEqual(daily["exercise_plan_hr_lower"], "120")
            self.assertEqual(daily["exercise_plan_hr_upper"], "150")

            rows = db.connection.execute("SELECT payload_json FROM raw_payloads").fetchall()
            combined = " ".join(row[0] for row in rows)
            self.assertNotIn("fixture-user", combined)
            self.assertNotIn("app_token", combined)
            self.assertIn('"status":200', combined)
            db.close()

    def test_changed_native_record_is_updated_not_duplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            responses = fixture_responses()
            fake = FakeClient(responses)
            sync_native_metrics(fake, db, 30)
            responses[("readiness", "watch_score")]["items"][0]["value"]["status"] = 201
            result = sync_native_metrics(fake, db, 30)
            readiness = next(row for row in result["domains"] if row["domain"] == "readiness")
            self.assertEqual(readiness["updated"], 1)
            self.assertEqual(db.status()["record_counts"]["readiness_records"], 1)
            db.close()

    def test_revised_same_day_wake_value_updates_existing_logical_row(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            first = [{
                "date": "2026-07-24", "start_time": 1000, "s": 0,
                "sample_timestamp": 1000, "bioChargeWake": 70,
                "raw_sample": {"s": 0, "bioChargeWake": 70},
            }]
            revised = [{
                "date": "2026-07-24", "start_time": 1000, "s": 0,
                "sample_timestamp": 1000, "bioChargeWake": 74,
                "raw_sample": {"s": 0, "bioChargeWake": 74},
            }]
            self.assertEqual(
                db.store_domain_rows("wake_energy", first),
                {"inserted": 1, "updated": 0, "unchanged": 0},
            )
            self.assertEqual(
                db.store_domain_rows("wake_energy", revised),
                {"inserted": 0, "updated": 1, "unchanged": 0},
            )
            stored = db.connection.execute(
                "SELECT bio_charge_wake FROM wake_energy"
            ).fetchall()
            self.assertEqual([row[0] for row in stored], [74.0])
            db.close()

    def test_multiple_wake_samples_with_distinct_offsets_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            rows = [
                {
                    "date": "2026-07-24", "start_time": 1000, "s": offset,
                    "sample_timestamp": 1000 + offset, "bioChargeWake": value,
                    "raw_sample": {"s": offset, "bioChargeWake": value},
                }
                for offset, value in ((0, 70), (1000, 74))
            ]
            counts = db.store_domain_rows("wake_energy", rows)
            self.assertEqual(counts["inserted"], 2)
            self.assertEqual(
                db.status()["record_counts"]["wake_energy"], 2
            )
            db.close()

    def test_sync_conflates_raw_unrecognized_record_with_empty_domain_status(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            responses = fixture_responses()
            responses[("Charge", "wake_data")] = {
                "items": [{"date": "2026-07-24", "value": {"newWrapper": {}}}]
            }
            result = sync_native_metrics(FakeClient(responses), db, 7)
            wake = next(
                row for row in result["domains"] if row["domain"] == "wake_energy"
            )
            self.assertEqual(wake["status"], "empty")
            self.assertEqual(wake["records_retrieved"], 0)
            raw = db.connection.execute(
                "SELECT COUNT(*) FROM raw_payloads WHERE domain='wake_energy'"
            ).fetchone()[0]
            self.assertEqual(raw, 1)
            db.close()

    def test_domain_failure_isolated_and_summary_json_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            result = sync_native_metrics(FakeClient(fixture_responses(), {("LifeLoad", "summary")}), db, 7)
            self.assertEqual(result["status"], "partial")
            failed = next(row for row in result["domains"] if row["domain"] == "lifeload")
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["error"], "ConnectionError")
            self.assertGreater(db.status()["record_counts"]["hrv_samples"], 0)
            encoded = json.dumps(result)
            self.assertNotIn("fixture-user", encoded)
            self.assertNotIn("fixture network failure", encoded)
            db.close()

    def test_read_daily_status_from_db(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")
            sync_native_metrics(FakeClient(fixture_responses()), db, 30)
            rows = db.read_daily_status("2026-07-01", "2026-07-31")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["date"], "2026-07-08")
            self.assertEqual(rows[0]["readiness"]["status"], "200")
            self.assertEqual(rows[0]["sleep_related_readiness"]["sleepHRV"], "42")
            self.assertEqual(rows[0]["hrv_sample_count"], 1)
            db.close()

    def test_integrity_backup_restore_and_existing_target_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            backup = root / "backups" / "backup.db"
            restored = root / "restore" / "restored.db"
            db = Database(source)
            sync_native_metrics(FakeClient(fixture_responses()), db, 30)
            db.close()

            checked = inspect_database_file(source)
            self.assertEqual(checked["integrity_check"], "ok")
            self.assertEqual(checked["foreign_key_check"], [])
            result = backup_database(source, backup)
            self.assertEqual(result["integrity_check"], "ok")
            self.assertEqual(result["record_counts"], checked["record_counts"])
            with self.assertRaises(FileExistsError):
                backup_database(source, backup)
            backup_database(source, backup, overwrite=True)

            restored_result = restore_database(backup, restored)
            self.assertTrue(restored_result["counts_match"])
            self.assertEqual(restored_result["record_counts"], checked["record_counts"])
            with self.assertRaises(FileExistsError):
                restore_database(backup, restored)

    def test_corrupt_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / "corrupt.db"
            corrupt.write_text("not a sqlite database", encoding="utf-8")
            with self.assertRaises(sqlite3.DatabaseError):
                inspect_database_file(corrupt)
            with self.assertRaises(sqlite3.DatabaseError):
                backup_database(corrupt, Path(directory) / "copy.db")

    def test_lock_blocks_second_holder_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run" / "sync.lock"
            first = SyncLock(path)
            second = SyncLock(path)
            self.assertTrue(first.acquire())
            self.assertTrue(lock_is_held(path))
            self.assertFalse(second.acquire())
            first.release()
            self.assertFalse(lock_is_held(path))

    def test_sync_health_json_exit_codes_and_systemd_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.db"
            db = Database(path)
            db.close()
            command = [sys.executable, "zepp_health.py", "sync-health", "--db", str(path), "--json"]
            failed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stdout)["status"], "failed")
            db = Database(path)
            run_id = db.start_sync(1, 1, 2)
            db.finish_sync(run_id, "ok", {"duration_seconds": 0.25})
            db.close()
            healthy = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(healthy.returncode, 0)
            healthy_json = json.loads(healthy.stdout)
            self.assertEqual(healthy_json["status"], "healthy")
            self.assertIn("sync_freshness", healthy_json["factual_freshness"])
            self.assertIn("domain_data_freshness", healthy_json["factual_freshness"])

        for filename, expected in (
            ("deploy/systemd/zepp-health-sync.service", {"Type": "oneshot", "Persistent": None}),
            ("deploy/systemd/zepp-health-sync.timer", {"Persistent": "true"}),
        ):
            parser = configparser.ConfigParser(strict=False)
            parser.optionxform = str
            parser.read(filename)
            if filename.endswith(".service"):
                self.assertEqual(parser["Service"]["Type"], expected["Type"])
                self.assertIn("__ZEPP_RUNTIME_USER__", parser["Service"]["User"])
            else:
                self.assertEqual(parser["Timer"]["Persistent"], expected["Persistent"])

        timer_text = Path("deploy/systemd/zepp-health-sync.timer").read_text()
        schedules = [
            line.split("=", 1)[1]
            for line in timer_text.splitlines()
            if line.startswith("OnCalendar=")
        ]
        self.assertEqual(schedules, [
            "*-*-* 02:00:00 Europe/Ljubljana",
            "*-*-* 06:30:00 Europe/Ljubljana",
            "*-*-* 08:30:00 Europe/Ljubljana",
            "*-*-* 12:00:00 Europe/Ljubljana",
            "*-*-* 18:00:00 Europe/Ljubljana",
            "*-*-* 22:00:00 Europe/Ljubljana",
        ])
        success_dropin = Path(
            "deploy/systemd/zepp-health-sync.service.d/10-context-on-success.conf"
        ).read_text()
        self.assertIn("OnSuccess=coach-context-generate.service", success_dropin)
        self.assertNotIn("OnFailure=", success_dropin)
        wrapper_text = Path("scripts/zepp-health-sync").read_text()
        self.assertIn("zepp-health-sync skipped: lock held", wrapper_text)
        self.assertIn("exit 75", wrapper_text)


if __name__ == "__main__":
    unittest.main()
