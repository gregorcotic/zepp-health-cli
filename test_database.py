import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
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
from zepp_health import (
    backfill_native_metrics,
    fetch_sport_load_pages,
    read_sport_load_report,
    sync_native_metrics,
)
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

    def sport_load(self, start_day, end_day, *, limit=900, next_cursor=None):
        key = ("WatchSportStatistics", "SPORT_LOAD")
        if key in self.failures:
            raise requests.ConnectionError("fixture network failure")
        return copy.deepcopy(self.responses.get(key, {"items": []}))


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
        ("all_day_stress", "all_day_stress"): {
        "items": []
    },
        ("Food", "real_data"): {"items": []},
        ("WatchSportStatistics", "SPORT_LOAD"): {"items": []},
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

    def test_phn_record_is_exposed_in_factual_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")

            db.store_domain_rows(
                "phn_record",
                [{
                    "date": "2026-07-27",
                    "timestamp": 1785170000000,
                    "phn_plan_id": "1769457413483",
                    "flag": 41,
                    "degree_of_completion": 0,
                    "degree_of_completion_week": 0,
                    "raw_value": {
                        "record": "native"
                    },
                }],
            )

            fresh = db.factual_freshness(
                datetime(
                    2026,
                    7,
                    27,
                    7,
                    0,
                    tzinfo=timezone.utc,
                )
            )

            state = fresh[
                "domain_data_freshness"
            ]["phn_record"]

            self.assertTrue(state["supported"])
            self.assertEqual(
                state["latest_date"],
                "2026-07-27",
            )
            self.assertEqual(
                state["coverage"],
                "today",
            )

            db.close()


    def test_phn_training_plan_freshness_uses_last_update_time(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")

            updated = datetime(
                2026,
                7,
                27,
                6,
                0,
                tzinfo=timezone.utc,
            )

            db.store_domain_rows(
                "phn_training_plan",
                [{
                    "date": "2026-01-26",
                    "timestamp": 1769457413483,
                    "phn_plan_id": "1769457413483",
                    "last_update_time": int(
                        updated.timestamp()
                    ),
                    "exercise_day": 53,
                    "raw_value": {
                        "result": "native"
                    },
                }],
            )

            fresh = db.factual_freshness(
                datetime(
                    2026,
                    7,
                    27,
                    7,
                    0,
                    tzinfo=timezone.utc,
                )
            )

            state = fresh[
                "phn_training_plan_state"
            ]

            self.assertEqual(
                state["event_timestamp_ms"],
                1769457413483,
            )
            self.assertEqual(
                state["last_update_local_date"],
                "2026-07-27",
            )
            self.assertEqual(
                state["coverage"],
                "today",
            )

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
            phn_tables = {
                row[0]
                for row in db.connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table'"
                ).fetchall()
            }
            self.assertIn(
                "phn_daily_records", phn_tables
            )
            self.assertIn(
                "phn_training_plans", phn_tables
            )
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

    def test_phn_domains_roundtrip_and_daily_status(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")

            record = {
                "date": "2026-05-18",
                "timestamp": 1779055200000,
                "phn_plan_id": "plan-1",
                "flag": 62,
                "degree_of_completion": 153,
                "degree_of_completion_week": 30,
                "raw_value": {"record": "native"},
            }

            plan = {
                "date": "2026-05-28",
                "timestamp": 1779946620000,
                "phn_plan_id": "plan-1",
                "last_update_time": 1779946620,
                "exercise_day": 53,
                "training_days": [0, 2, 4, 5],
                "weekly_high_intensity_day":
                    [2, 0, 1, 0, 3, 1, 0],
                "current_weekday": 3,
                "flag_recommended_exercise": 31,
                "trimp_daily_recommended": 0,
                "daily_recommend_intensity": 0,
                "duration_zone1": 0,
                "duration_zone2": 0,
                "duration_zone3": 0,
                "yesterday_recommend_flag": 61,
                "this_week_achieved_daily_completed_percent":
                    [81, 0, 121, 0, 0, 0, 0],
                "raw_value": {"result": "native"},
            }

            self.assertEqual(
                db.store_domain_rows("phn_record", [record]),
                {
                    "inserted": 1,
                    "updated": 0,
                    "unchanged": 0,
                },
            )

            self.assertEqual(
                db.store_domain_rows(
                    "phn_training_plan", [plan]
                ),
                {
                    "inserted": 1,
                    "updated": 0,
                    "unchanged": 0,
                },
            )

            stored = db.connection.execute(
                """SELECT flag, degree_of_completion,
                          degree_of_completion_week
                   FROM phn_daily_records"""
            ).fetchone()

            self.assertEqual(
                tuple(stored),
                ("62", "153", "30"),
            )

            stored_plan = db.connection.execute(
                """SELECT exercise_day,
                          training_days_json,
                          weekly_high_intensity_day_json
                   FROM phn_training_plans"""
            ).fetchone()

            self.assertEqual(stored_plan[0], "53")
            self.assertEqual(
                json.loads(stored_plan[1]),
                [0, 2, 4, 5],
            )
            self.assertEqual(
                json.loads(stored_plan[2]),
                [2, 0, 1, 0, 3, 1, 0],
            )

            daily = db.read_daily_status(
                "2026-05-18",
                "2026-05-18",
            )[0]

            self.assertEqual(
                daily["phn_record"]["flag"], "62"
            )
            self.assertEqual(
                daily["phn_record"][
                    "degree_of_completion"
                ],
                "153",
            )

            db.close()


    def test_same_raw_payload_updates_when_normalized_state_changes(
        self,
    ):
        """Parser improvements must refresh derived columns even for same raw."""
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "zepp.db")

            plan_id = 1769457413483

            # Simulates the first Batch 1B normalization before the factual
            # phn_plan_id fallback was added.
            first = {
                "date": "2026-01-26",
                "timestamp": plan_id,
                "phn_plan_id": None,
                "last_update_time": 1785179517,
                "exercise_day": 53,
                "raw_value": {
                    "result": "identical-native-payload"
                },
            }

            # Same raw Zepp evidence, improved normalizer now derives
            # phn_plan_id from the production-proven event identity.
            renormalized = {
                **first,
                "phn_plan_id": plan_id,
                "raw_value": {
                    "result": "identical-native-payload"
                },
            }

            self.assertEqual(
                db.store_domain_rows(
                    "phn_training_plan",
                    [first],
                ),
                {
                    "inserted": 1,
                    "updated": 0,
                    "unchanged": 0,
                },
            )

            self.assertEqual(
                db.store_domain_rows(
                    "phn_training_plan",
                    [renormalized],
                ),
                {
                    "inserted": 0,
                    "updated": 1,
                    "unchanged": 0,
                },
            )

            stored = db.connection.execute(
                """SELECT phn_plan_id
                   FROM phn_training_plans"""
            ).fetchone()

            self.assertEqual(
                stored["phn_plan_id"],
                str(plan_id),
            )

            # A third identical normalization must now be genuinely unchanged.
            self.assertEqual(
                db.store_domain_rows(
                    "phn_training_plan",
                    [renormalized],
                ),
                {
                    "inserted": 0,
                    "updated": 0,
                    "unchanged": 1,
                },
            )

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



    def test_stress_persistence_v7(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)

        db = Database(Path(directory.name) / "zepp.db")
        self.addCleanup(db.close)

        schema_version = db.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        self.assertEqual(schema_version, SCHEMA_VERSION)

        rows = [
            {
                "event_type": "all_day_stress",
                "event_timestamp_ms": 1779926400001,
                "date": "2026-05-28",
                "min_stress": 10,
                "max_stress": 55,
                "avg_stress": 34,
                "relax_proportion": 54,
                "normal_proportion": 46,
                "medium_proportion": 0,
                "high_proportion": 0,
                "sample_count": 3,
                "provenance": {
                    "eventType": "all_day_stress",
                    "subType": "all_day_stress",
                },
                "samples": [
                    {
                        "timestamp_ms": 1779919500000,
                        "stress": 43,
                        "category": "normal",
                    },
                    {
                        "timestamp_ms": 1779919800000,
                        "stress": 49,
                        "category": "normal",
                    },

                    # Deliberate 10-minute jump.
                    {
                        "timestamp_ms": 1779920400000,
                        "stress": 22,
                        "category": "relaxed",
                    },
                ],
                "raw": {
                    "eventType": "all_day_stress",
                    "timestamp": 1779926400001,
                },
            }
        ]

        first = db.store_stress_rows(rows)

        self.assertEqual(first["daily_inserted"], 1)
        self.assertEqual(first["daily_updated"], 0)
        self.assertEqual(first["daily_unchanged"], 0)

        self.assertEqual(first["samples_inserted"], 3)
        self.assertEqual(first["samples_updated"], 0)
        self.assertEqual(first["samples_unchanged"], 0)

        daily_count = db.connection.execute(
            "SELECT COUNT(*) FROM stress_daily_records"
        ).fetchone()[0]

        sample_count = db.connection.execute(
            "SELECT COUNT(*) FROM stress_samples"
        ).fetchone()[0]

        self.assertEqual(daily_count, 1)
        self.assertEqual(sample_count, 3)

        # Writing the exact same normalized/native state is unchanged.
        second = db.store_stress_rows(rows)

        self.assertEqual(second["daily_unchanged"], 1)
        self.assertEqual(second["samples_unchanged"], 3)

        self.assertEqual(
            db.connection.execute(
                "SELECT COUNT(*) FROM stress_daily_records"
            ).fetchone()[0],
            1,
        )

        self.assertEqual(
            db.connection.execute(
                "SELECT COUNT(*) FROM stress_samples"
            ).fetchone()[0],
            3,
        )

        # Later native daily snapshot updates the same logical day.
        rows[0]["avg_stress"] = 35
        rows[0]["normal_proportion"] = 47

        # Also correct one native sample in place.
        rows[0]["samples"][1]["stress"] = 50

        third = db.store_stress_rows(rows)

        self.assertEqual(third["daily_updated"], 1)
        self.assertEqual(third["samples_updated"], 1)
        self.assertEqual(third["samples_unchanged"], 2)

        daily = db.connection.execute(
            """
            SELECT avg_stress, normal_proportion
            FROM stress_daily_records
            WHERE event_date='2026-05-28'
            """
        ).fetchone()

        self.assertEqual(daily["avg_stress"], 35)
        self.assertEqual(daily["normal_proportion"], 47)

        sample = db.connection.execute(
            """
            SELECT stress, category
            FROM stress_samples
            WHERE timestamp_ms=1779919800000
            """
        ).fetchone()

        self.assertEqual(sample["stress"], 50)
        self.assertEqual(sample["category"], "normal")

        # Sparse timeline must remain sparse:
        # no synthetic 5-minute sample is inserted at 1779920100000.
        missing = db.connection.execute(
            """
            SELECT COUNT(*)
            FROM stress_samples
            WHERE timestamp_ms=1779920100000
            """
        ).fetchone()[0]

        self.assertEqual(missing, 0)



    def test_sync_native_metrics_stress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "stress-sync.db")

            responses = fixture_responses()

            responses[
                ("all_day_stress", "all_day_stress")
            ] = {
                "items": [
                    {
                        "eventType": "all_day_stress",
                        "timestamp": 1779926400001,
                        "deviceId": "stress-device",
                        "value": {
                            "minStress": 10,
                            "maxStress": 55,
                            "avgStress": 34,
                            "relaxProportion": 54,
                            "normalProportion": 46,
                            "mediumProportion": 0,
                            "highProportion": 0,
                            "data": [
                                {
                                    "time": 1779919500000,
                                    "value": 43,
                                },
                                {
                                    "time": 1779919800000,
                                    "value": 49,
                                },

                                # Native 10-minute gap.
                                {
                                    "time": 1779920400000,
                                    "value": 22,
                                },
                            ],
                        },
                    }
                ]
            }

            result = sync_native_metrics(
                FakeClient(responses),
                db,
                7,
            )

            stress = [
                item
                for item in result["domains"]
                if item["domain"] == "stress"
            ]

            self.assertEqual(len(stress), 1)

            stress = stress[0]

            self.assertEqual(stress["status"], "ok")
            self.assertEqual(
                stress["event_type"],
                "all_day_stress",
            )
            self.assertEqual(
                stress["sub_type"],
                "all_day_stress",
            )
            self.assertEqual(
                stress["records_retrieved"],
                1,
            )

            self.assertEqual(
                stress["daily_inserted"],
                1,
            )
            self.assertEqual(
                stress["samples_inserted"],
                3,
            )

            daily = db.connection.execute(
                """
                SELECT
                    min_stress,
                    max_stress,
                    avg_stress,
                    sample_count
                FROM stress_daily_records
                WHERE event_date='2026-05-28'
                """
            ).fetchone()

            self.assertIsNotNone(daily)
            self.assertEqual(daily["min_stress"], 10)
            self.assertEqual(daily["max_stress"], 55)
            self.assertEqual(daily["avg_stress"], 34)
            self.assertEqual(daily["sample_count"], 3)

            samples = db.connection.execute(
                """
                SELECT timestamp_ms, stress
                FROM stress_samples
                ORDER BY timestamp_ms
                """
            ).fetchall()

            self.assertEqual(len(samples), 3)

            # Sparse timeline remains sparse.
            synthetic = db.connection.execute(
                """
                SELECT COUNT(*)
                FROM stress_samples
                WHERE timestamp_ms=1779920100000
                """
            ).fetchone()[0]

            self.assertEqual(synthetic, 0)

            sync_domain = db.connection.execute(
                """
                SELECT domain, status
                FROM sync_run_domains
                WHERE domain='stress'
                ORDER BY rowid DESC
                LIMIT 1
                """
            ).fetchone()

            self.assertIsNotNone(sync_domain)
            self.assertEqual(
                sync_domain["domain"],
                "stress",
            )
            self.assertEqual(
                sync_domain["status"],
                "ok",
            )

            db.close()

    def test_stress_database_reads_preserve_sparse_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "stress-read.db")
            rows = []
            for day, timestamp, average in (
                ("2026-07-27", 1785110400000, 28),
                ("2026-07-28", 1785196800000, 34),
            ):
                rows.append({
                    "event_timestamp_ms": timestamp,
                    "date": day,
                    "min_stress": 10,
                    "max_stress": 55,
                    "avg_stress": average,
                    "relax_proportion": 54,
                    "normal_proportion": 46,
                    "medium_proportion": 0,
                    "high_proportion": 0,
                    "sample_count": 2,
                    "samples": [
                        {
                            "timestamp_ms": timestamp + 300000,
                            "stress": 22,
                            "category": "relaxed",
                        },
                        {
                            # Deliberate missing five-minute measurement.
                            "timestamp_ms": timestamp + 900000,
                            "stress": 43,
                            "category": "normal",
                        },
                    ],
                    "raw": {"eventType": "all_day_stress"},
                })
            db.store_stress_rows(rows)

            daily = db.fetch_stress_daily(
                "2026-07-27",
                "2026-07-28",
            )
            self.assertEqual(
                [row["date"] for row in daily],
                ["2026-07-28", "2026-07-27"],
            )
            self.assertEqual(
                [row["avg_stress"] for row in daily],
                [34, 28],
            )
            self.assertEqual(
                db.fetch_latest_stress_daily()["date"],
                "2026-07-28",
            )

            samples = db.fetch_stress_samples(
                "2026-07-28",
                "2026-07-28",
            )
            self.assertEqual(len(samples), 2)
            self.assertEqual(
                [row["timestamp_ms"] for row in samples],
                [1785197100000, 1785197700000],
            )
            self.assertEqual(
                [row["value"] for row in samples],
                [22, 43],
            )
            db.close()

    def test_stress_freshness_current_stale_and_missing(self) -> None:
        now = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "current.db")
            db.store_stress_rows([{
                "date": "2026-07-29",
                "sample_count": 0,
                "samples": [],
                "raw": {},
            }])
            state = db.factual_freshness(now)[
                "domain_data_freshness"
            ]["stress"]
            self.assertEqual(state["freshness"], "current")
            db.close()

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "stale.db")
            db.store_stress_rows([{
                "date": "2026-07-28",
                "sample_count": 0,
                "samples": [],
                "raw": {},
            }])
            state = db.factual_freshness(now)[
                "domain_data_freshness"
            ]["stress"]
            self.assertEqual(state["freshness"], "stale")
            db.close()

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "missing.db")
            state = db.factual_freshness(now)[
                "domain_data_freshness"
            ]["stress"]
            self.assertEqual(state["freshness"], "missing")
            db.close()

    def test_stress_cli_json_shape_and_optional_sparse_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stress-cli.db"
            today = datetime.now(
                ZoneInfo("Europe/Ljubljana")
            ).date().isoformat()
            timestamp = int(
                datetime.now(timezone.utc).timestamp() * 1000
            )
            db = Database(path)
            db.store_stress_rows([{
                "event_timestamp_ms": timestamp,
                "date": today,
                "min_stress": 10,
                "max_stress": 81,
                "avg_stress": 34,
                "relax_proportion": 54,
                "normal_proportion": 45,
                "medium_proportion": 0,
                "high_proportion": 1,
                "sample_count": 2,
                "samples": [
                    {
                        "timestamp_ms": timestamp - 900000,
                        "stress": 22,
                        "category": "relaxed",
                    },
                    {
                        "timestamp_ms": timestamp - 300000,
                        "stress": 81,
                        "category": "high",
                    },
                ],
                "raw": {"private": "not exposed"},
            }])
            db.close()

            base = [
                sys.executable,
                "zepp_health.py",
                "stress",
                "--days",
                "1",
                "--db",
                str(path),
                "--json",
            ]
            without_samples = subprocess.run(
                base,
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(without_samples.stdout)
            self.assertEqual(result["latest"]["date"], today)
            self.assertEqual(
                result["latest"]["distribution"],
                {
                    "relaxed": 54,
                    "normal": 45,
                    "medium": 0,
                    "high": 1,
                },
            )
            self.assertNotIn("samples", result)
            self.assertNotIn("private", without_samples.stdout)

            with_samples = subprocess.run(
                base[:-1] + ["--samples", "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(with_samples.stdout)
            self.assertEqual(len(result["samples"]), 2)
            self.assertEqual(
                [row["timestamp_ms"] for row in result["samples"]],
                [timestamp - 900000, timestamp - 300000],
            )
            self.assertEqual(
                [row["value"] for row in result["samples"]],
                [22, 81],
            )

    def test_food_schema_v8_migration_and_idempotent_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "food.db"
            db = Database(path)
            self.assertEqual(db.connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0], 9)
            indexes = {
                row["name"] for row in db.connection.execute(
                    "PRAGMA index_list(food_entries)"
                ).fetchall()
            }
            self.assertIn("idx_food_entries_food_log_id", indexes)
            row = {
                "food_log_id": "banana-id",
                "date": "2026-07-28",
                "timestamp_ms": 1785238560000,
                "meal_type": 4,
                "meal_label": "Afternoon Snack",
                "meal_name": "Afternoon Snack",
                "food_name": "Banana",
                "measure_weight": 250,
                "weight_unit": "g",
                "energy": 218.75,
                "carbohydrates": 56.25,
                "protein": 2.7083333333,
                "fat_total": 0.8333333333,
                "fiber": 3.1,
                "servings": 2,
                "labels": ["fruit"],
                "emoji": "🍌",
                "recognize_type": 1,
                "recognize_source_type": 2,
                "raw": {"privateMetadata": "stored-only"},
            }
            self.assertEqual(
                db.store_food_rows([row]),
                {"inserted": 1, "updated": 0, "unchanged": 0},
            )
            self.assertEqual(
                db.store_food_rows([row]),
                {"inserted": 0, "updated": 0, "unchanged": 1},
            )
            row["measure_weight"] = 260
            row["energy"] = 227.5
            self.assertEqual(
                db.store_food_rows([row]),
                {"inserted": 0, "updated": 1, "unchanged": 0},
            )
            self.assertEqual(db.connection.execute(
                "SELECT COUNT(*) FROM food_entries"
            ).fetchone()[0], 1)
            db.connection.execute("DROP TABLE food_entries")
            db.connection.execute("PRAGMA user_version = 7")
            db.connection.commit()
            db.close()

            migrated = Database(path)
            self.assertEqual(migrated.connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0], 9)
            self.assertIsNotNone(migrated.connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='food_entries'"
            ).fetchone())
            migrated.close()

    def test_food_database_reads_and_cli_json_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "food-cli.db"
            today = datetime.now(
                ZoneInfo("Europe/Ljubljana")
            ).date()
            yesterday = today - timedelta(days=1)
            db = Database(path)
            db.store_food_rows([
                {
                    "food_log_id": "breakfast-id",
                    "date": today.isoformat(),
                    "timestamp_ms": 2000,
                    "meal_type": 1,
                    "meal_label": "Breakfast",
                    "food_name": "Banana",
                    "energy": 210,
                    "protein": None,
                    "raw": {"secretFreeText": "must-not-leak"},
                },
                {
                    "food_log_id": "dinner-id",
                    "date": yesterday.isoformat(),
                    "timestamp_ms": 1000,
                    "meal_type": 5,
                    "meal_label": "Dinner",
                    "food_name": "Recorded dinner",
                    "raw": {},
                },
            ])
            entries = db.fetch_food_entries(
                yesterday.isoformat(),
                today.isoformat(),
            )
            self.assertEqual(
                [entry["food_log_id"] for entry in entries],
                ["breakfast-id", "dinner-id"],
            )
            self.assertEqual(entries[0]["meal_type"], 1)
            self.assertIsNone(entries[0]["protein"])
            self.assertEqual(
                len(db.fetch_food_entries(
                    yesterday.isoformat(),
                    today.isoformat(),
                    meal_type=5,
                )),
                1,
            )
            db.close()

            completed = subprocess.run(
                [
                    sys.executable,
                    "zepp_health.py",
                    "food",
                    "--days",
                    "2",
                    "--db",
                    str(path),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(len(result["entries"]), 2)
            self.assertEqual(result["entries"][0]["food_name"], "Banana")
            self.assertEqual(result["today_log_status"], "food_logged")
            self.assertFalse(result["daily_totals"]["available"])
            self.assertNotIn("secretFreeText", completed.stdout)
            self.assertNotIn("source_json", completed.stdout)

    def test_sync_native_metrics_food_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "food-sync.db")
            responses = fixture_responses()
            responses[("Food", "real_data")] = {"items": [{
                "eventType": "Food",
                "subType": "real_data",
                "timestamp": 1785238560000,
                "timezone": "Europe/Ljubljana",
                "value": {
                    "foodLogId": "banana-sync-id",
                    "mealType": 4,
                    "foodName": "Banana",
                    "measureWeight": "250",
                    "weightUnit": "g",
                    "energy": "218.75",
                    "carbohydrates": "56.25",
                    "protein": "2.7083333333",
                    "fatTotal": "0.8333333333",
                },
            }]}
            first = sync_native_metrics(FakeClient(responses), db, 7)
            food = next(
                row for row in first["domains"]
                if row["domain"] == "food"
            )
            self.assertEqual(food["inserted"], 1)
            second = sync_native_metrics(FakeClient(responses), db, 7)
            food = next(
                row for row in second["domains"]
                if row["domain"] == "food"
            )
            self.assertEqual(food["inserted"], 0)
            self.assertEqual(food["updated"], 0)
            self.assertEqual(food["unchanged"], 1)
            db.close()

    def test_sport_load_schema_v9_persistence_reads_and_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sport-load.db"
            db = Database(path)
            self.assertEqual(db.connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0], 9)
            self.assertIsNotNone(db.connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='sport_load_records'"
            ).fetchone())
            indexes = {
                row["name"] for row in db.connection.execute(
                    "PRAGMA index_list(sport_load_records)"
                ).fetchall()
            }
            self.assertIn("idx_sport_load_event_date", indexes)
            base = {
                "event_date": "2026-07-29",
                "generated_time_s": 1785283200,
                "updated_time_ms": 1785276000000,
                "current_day_training_load": 0,
                "wtl_sum": 432,
                "optimal_min": 261,
                "optimal_max": 607,
                "overreaching_threshold": 735,
                "device_source": 9568513,
                "raw": {"dayId": "2026-07-29", "transport": "a"},
            }
            older = {**base, "event_date": "2026-07-28", "wtl_sum": 420}
            self.assertEqual(
                db.store_sport_load_rows([older, base]),
                {"inserted": 2, "updated": 0, "unchanged": 0},
            )
            changed_raw = copy.deepcopy(base)
            changed_raw["raw"]["transport"] = "b"
            self.assertEqual(
                db.store_sport_load_rows([changed_raw]),
                {"inserted": 0, "updated": 0, "unchanged": 1},
            )
            corrected = copy.deepcopy(changed_raw)
            corrected["current_day_training_load"] = 12
            corrected["wtl_sum"] = 444
            self.assertEqual(
                db.store_sport_load_rows([corrected]),
                {"inserted": 0, "updated": 1, "unchanged": 0},
            )
            self.assertEqual(db.connection.execute(
                "SELECT COUNT(*) FROM sport_load_records"
            ).fetchone()[0], 2)
            rows = db.fetch_sport_load("2026-07-28", "2026-07-29")
            self.assertEqual([row["date"] for row in rows], [
                "2026-07-29", "2026-07-28",
            ])
            self.assertEqual(db.fetch_latest_sport_load()["wtl_sum"], 444)
            current = db.factual_freshness(
                datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
            )["domain_data_freshness"]["sport_load"]
            self.assertEqual(current["latest_date"], "2026-07-29")
            self.assertEqual(current["freshness"], "current")
            self.assertEqual(read_sport_load_report(
                db, 7, now=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
            )["freshness"], "current")
            self.assertEqual(read_sport_load_report(
                db, 7, now=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
            )["freshness"], "stale")
            db.connection.execute("DROP TABLE sport_load_records")
            db.connection.execute("PRAGMA user_version = 8")
            db.connection.commit()
            db.close()
            migrated = Database(path)
            self.assertEqual(migrated.connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0], 9)
            self.assertEqual(
                read_sport_load_report(
                    migrated,
                    7,
                    now=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                )["freshness"],
                "missing",
            )
            migrated.close()

    def test_sport_load_pagination_deduplicates_and_terminates(self) -> None:
        class PageClient:
            def __init__(self):
                self.cursors = []

            def sport_load(self, start_day, end_day, *, limit=900, next_cursor=None):
                self.cursors.append(next_cursor)
                if next_cursor is None:
                    return {
                        "items": [
                            {"dayId": "2026-07-29", "wtlSum": 432},
                            {"dayId": "2026-07-28", "wtlSum": 420},
                        ],
                        "next": "123",
                    }
                return {"items": [
                    {"dayId": "2026-07-28", "wtlSum": 999},
                    {"dayId": "2026-07-27", "wtlSum": 410},
                ]}

        client = PageClient()
        rows, pages = fetch_sport_load_pages(
            client, date(2026, 7, 27), date(2026, 7, 29)
        )
        self.assertEqual(pages, 2)
        self.assertEqual(client.cursors, [None, 123])
        self.assertEqual([row["event_date"] for row in rows], [
            "2026-07-27", "2026-07-28", "2026-07-29",
        ])
        self.assertEqual(rows[1]["wtl_sum"], 420)
        empty_rows, empty_pages = fetch_sport_load_pages(
            FakeClient(fixture_responses()),
            date(2026, 7, 29),
            date(2026, 7, 29),
        )
        self.assertEqual((empty_rows, empty_pages), ([], 1))

    def test_sport_load_empty_and_failure_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "isolated.db")
            empty = sync_native_metrics(FakeClient(fixture_responses()), db, 1)
            sport = next(row for row in empty["domains"]
                         if row["domain"] == "sport_load")
            self.assertEqual(sport["status"], "empty")
            responses = fixture_responses()
            failed = sync_native_metrics(
                FakeClient(
                    responses,
                    {("WatchSportStatistics", "SPORT_LOAD")},
                ),
                db,
                1,
            )
            sport = next(row for row in failed["domains"]
                         if row["domain"] == "sport_load")
            self.assertEqual(sport["status"], "error")
            self.assertGreater(
                db.connection.execute(
                    "SELECT COUNT(*) FROM hrv_samples"
                ).fetchone()[0],
                0,
            )
            db.close()

    def test_sport_load_sync_idempotency_correction_and_cli_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sport-load-sync.db"
            db = Database(path)
            responses = fixture_responses()
            fixture = {
                "dayId": datetime.now(
                    ZoneInfo("Europe/Ljubljana")
                ).date().isoformat(),
                "generatedTime": 1785283200,
                "updateTime": 1785276000000,
                "currnetDayTrainLoad": 0,
                "wtlSum": 432,
                "wtlSumOptimalMin": 261,
                "wtlSumOptimalMax": 607,
                "wtlSumOverreaching": 735,
                "device_source": 9568513,
                "privateTransport": "must-not-leak",
            }
            responses[("WatchSportStatistics", "SPORT_LOAD")] = {
                "items": [fixture]
            }
            first = sync_native_metrics(FakeClient(responses), db, 7)
            sport = next(row for row in first["domains"]
                         if row["domain"] == "sport_load")
            self.assertEqual(sport["inserted"], 1)
            second = sync_native_metrics(FakeClient(responses), db, 7)
            sport = next(row for row in second["domains"]
                         if row["domain"] == "sport_load")
            self.assertEqual(
                (sport["inserted"], sport["updated"], sport["unchanged"]),
                (0, 0, 1),
            )
            fixture["wtlSum"] = 440
            third = sync_native_metrics(FakeClient(responses), db, 7)
            sport = next(row for row in third["domains"]
                         if row["domain"] == "sport_load")
            self.assertEqual(sport["updated"], 1)
            accounting = db.connection.execute(
                "SELECT status FROM sync_run_domains "
                "WHERE domain='sport_load' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(accounting["status"], "ok")
            db.close()
            completed = subprocess.run(
                [
                    sys.executable, "zepp_health.py", "sport-load",
                    "--days", "7", "--db", str(path), "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["latest"]["wtl_sum"], 440)
            self.assertEqual(result["freshness"], "current")
            self.assertNotIn("source_json", completed.stdout)
            self.assertNotIn("privateTransport", completed.stdout)
            human = subprocess.run(
                [
                    sys.executable, "zepp_health.py", "sport-load",
                    "--days", "7", "--db", str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("WTL sum: 440", human.stdout)


if __name__ == "__main__":
    unittest.main()
