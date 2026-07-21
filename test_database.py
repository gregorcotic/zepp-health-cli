import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from zepp_db import (
    Database,
    SCHEMA_VERSION,
    backup_database,
    inspect_database_file,
    resolve_db_path,
    restore_database,
)
from zepp_health import sync_native_metrics


class FakeClient:
    def __init__(self, responses, failures=None):
        self.responses = responses
        self.failures = set(failures or ())

    def events(self, event_type, sub_type, from_ms, to_ms, *, limit=2000):
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
            "value": {"recoveryFactor": 2, "totalScore": 20, "activityScore": 8, "exerciseScore": 12, "atl": 4, "ctl": 5, "tsb": 1},
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


if __name__ == "__main__":
    unittest.main()
