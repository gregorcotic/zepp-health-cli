import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from zepp_health import (
    _wake_diagnostic_window,
    consolidate_daily_status,
    diagnose_wake_energy_payload,
    discover_event_domains,
    latest_readiness_per_day,
    normalize_charge_data,
    normalize_hrv_data,
    normalize_readiness_data,
    normalize_wake_data,
    _normalize_value_records,
)


class NativeMetricsTests(unittest.TestCase):
    def test_hrv_real_data_preserves_samples_and_timestamp_inputs(self) -> None:
        payload = {"items": [{
            "date": "2026-07-08",
            "value": {
                "startTime": 1783468800000,
                "samples": [{"s": 60000, "hrv": 42.5, "u": 123}, {"s": 120000, "hrv": 44, "u": 124}],
            },
        }]}
        rows = normalize_hrv_data(payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-07-08")
        self.assertEqual(rows[0]["offset"], 60000)
        self.assertEqual(rows[0]["raw_u"], 123)
        self.assertEqual(rows[0]["sample_timestamp"], 1783468860000)
        self.assertEqual(rows[0]["mapping_confidence"], "confirmed")

    def test_wake_energy_preserves_zepp_fields(self) -> None:
        rows = _normalize_value_records(
            {"items": [{"date": "2026-07-08", "value": {
                "physicalWake": 65, "mentalWake": 66, "dailyFitnessScore": 80,
                "stressFitnessScore": 70, "exertionScore": 55, "futureField": "kept",
            }}]},
            "Charge", "wake_data",
            ("physicalWake", "mentalWake", "dailyFitnessScore", "stressFitnessScore", "exertionScore"),
        )
        self.assertEqual(rows[0]["physicalWake"], 65)
        self.assertEqual(rows[0]["raw_value"]["futureField"], "kept")

    def test_wake_data_extracts_fields_from_nested_samples(self) -> None:
        rows = normalize_wake_data({"items": [{
            "timestamp": 1783468800000,
            "value": {"startTime": 1783468800000, "samples": [{
                "s": 60000, "bioChargeWake": 72.5, "wakeCharge": 80,
                "physicalWake": 69.9, "mentalWake": 83.0,
                "dailyFitnessScore": 0.72, "stressFitnessScore": 0.67,
                "exertionScore": 382, "snapshot": {"physicalMinutes": 10},
            }]},
        }]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bioChargeWake"], 72.5)
        self.assertEqual(rows[0]["wakeCharge"], 80)
        self.assertEqual(rows[0]["exertionScore"], 382)
        self.assertEqual(rows[0]["raw_sample"]["snapshot"]["physicalMinutes"], 10)

    def test_wake_data_explicit_today_date_is_preserved(self) -> None:
        rows = normalize_wake_data({"items": [{
            "date": "2026-07-24",
            "value": {"samples": [{"bioChargeWake": 72}]},
        }]})
        self.assertEqual(rows[0]["date"], "2026-07-24")

    def test_wake_data_midnight_boundary_characterizes_timezone_behavior(self) -> None:
        instant = datetime(
            2026, 7, 24, 0, 30, tzinfo=ZoneInfo("Europe/Ljubljana")
        )
        timestamp_ms = int(instant.timestamp() * 1000)
        without_timezone = normalize_wake_data({"items": [{
            "timestamp": timestamp_ms,
            "value": {"samples": [{"bioChargeWake": 70}]},
        }]})
        with_timezone = normalize_wake_data({"items": [{
            "timestamp": timestamp_ms,
            "timezone": "Europe/Ljubljana",
            "value": {"samples": [{"bioChargeWake": 70}]},
        }]})
        self.assertEqual(without_timezone[0]["date"], "2026-07-23")
        self.assertEqual(with_timezone[0]["date"], "2026-07-24")

    def test_production_prefixed_timezone_uses_local_start_time_wake_day(self) -> None:
        rows = normalize_wake_data({"items": [{
            "eventType": "Charge",
            "subType": "wake_data",
            "timestamp": 1784764800000,
            "value": {
                "startTime": 1784844000000,
                "timeZone": "1,Europe/Ljubljana",
                "samples": [{
                    "s": 0,
                    "bioChargeWake": 64.12416,
                    "wakeCharge": 65,
                }],
            },
        }]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], 1784764800000)
        self.assertEqual(rows[0]["sample_timestamp"], 1784844000000)
        self.assertEqual(rows[0]["wakeCharge"], 65)
        self.assertEqual(rows[0]["date"], "2026-07-24")

    def test_production_prefixed_timezone_diagnostic_compares_old_and_wake_dates(self) -> None:
        report = diagnose_wake_energy_payload({"items": [{
            "timestamp": 1784937600000,
            "value": {
                "startTime": 1785016800000,
                "timeZone": "1,Europe/Ljubljana",
                "samples": [{"s": 0, "bioChargeWake": 63.938446}],
            },
        }]})
        record = report["records"][0]
        self.assertEqual(record["raw_timezone"], "1,Europe/Ljubljana")
        self.assertEqual(record["effective_timezone"], "Europe/Ljubljana")
        self.assertEqual(record["generic_parent_date"], "2026-07-25")
        self.assertEqual(record["resolved_event_date"], "2026-07-26")
        self.assertEqual(
            record["samples"][0]["normalized_wake_energy_event_date"],
            "2026-07-26",
        )

    def test_wake_data_epoch_seconds_are_treated_as_milliseconds(self) -> None:
        timestamp_seconds = int(datetime(
            2026, 7, 24, 0, 30, tzinfo=ZoneInfo("Europe/Ljubljana")
        ).timestamp())
        rows = normalize_wake_data({"items": [{
            "timestamp": timestamp_seconds,
            "timezone": "Europe/Ljubljana",
            "value": {"samples": [{"bioChargeWake": 70}]},
        }]})
        self.assertEqual(rows[0]["date"], "1970-01-21")

    def test_wake_data_sleep_crossing_midnight_inherits_parent_date(self) -> None:
        wake = datetime(
            2026, 7, 24, 6, 30, tzinfo=ZoneInfo("Europe/Ljubljana")
        )
        rows = normalize_wake_data({"items": [{
            "date": "2026-07-23",
            "value": {"samples": [{
                "timestamp": int(wake.timestamp() * 1000),
                "bioChargeWake": 70,
            }]},
        }]})
        self.assertEqual(rows[0]["date"], "2026-07-23")
        self.assertEqual(rows[0]["sample_timestamp"], rows[0]["timestamp"])

    def test_wake_data_parent_fields_without_samples_are_normalized(self) -> None:
        rows = normalize_wake_data({"items": [{
            "date": "2026-07-24",
            "value": {"bioChargeWake": 70},
        }]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bioChargeWake"], 70)

    def test_wake_data_empty_samples_falls_back_to_parent_fields(self) -> None:
        rows = normalize_wake_data({"items": [{
            "date": "2026-07-24",
            "value": {"samples": [], "bioChargeWake": 70},
        }]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bioChargeWake"], 70)

    def test_wake_data_multiple_samples_and_unsupported_wrapper(self) -> None:
        record = {
            "date": "2026-07-24",
            "value": {"samples": [
                {"s": 0, "bioChargeWake": 70},
                {"s": 1000, "bioChargeWake": 74},
            ]},
        }
        self.assertEqual(len(normalize_wake_data({"items": [record]})), 2)
        self.assertEqual(normalize_wake_data({"unexpected": {"items": [record]}}), [])

    def test_wake_diagnostic_is_sanitized_and_compares_dates(self) -> None:
        payload = {"items": [{
            "userId": "secret-user",
            "date": "2026-07-23",
            "authorization": "secret-token",
            "value": {"samples": [{
                "timestamp": 1784867400000,
                "bioChargeWake": 70,
                "snapshot": {"unrelated": "private"},
                "newWakeMetric": 12,
            }]},
        }]}
        report = diagnose_wake_energy_payload(payload)
        rendered = json.dumps(report)
        self.assertNotIn("secret-user", rendered)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("private", rendered)
        self.assertIn("newWakeMetric", rendered)
        sample = report["records"][0]["samples"][0]
        self.assertEqual(sample["normalized_wake_energy_event_date"], "2026-07-23")

    def test_wake_diagnostic_window_uses_local_inclusive_dates(self) -> None:
        start, end = _wake_diagnostic_window(
            "2026-07-24", "2026-07-24", "Europe/Ljubljana"
        )
        self.assertEqual(end - start, 24 * 60 * 60 * 1000)

    def test_readiness_watch_score_and_embedded_sleep_fields(self) -> None:
        rows = normalize_readiness_data({"items": [{"date": "2026-07-08", "value": {
            "status": 1, "hrvScore": 80, "sleepHRV": 42, "sleepRHR": 52,
            "phyScore": 75, "mentScore": 70, "skinTempScore": 90,
            "ahiScore": 99, "rdnsScore": 88, "unknownNative": "preserved",
        }}]})
        self.assertEqual(rows[0]["hrvScore"], 80)
        self.assertEqual(rows[0]["raw_value"]["unknownNative"], "preserved")

    def test_readiness_latest_per_day_prefers_timestamp_update(self) -> None:
        rows = [
            {"date": "2026-07-08", "timestamp": 100, "timestampUpdate": 200, "status": 1},
            {"date": "2026-07-08", "timestamp": 300, "timestampUpdate": 150, "status": 2},
            {"date": "2026-07-09", "timestamp": 500, "status": 3},
            {"date": "2026-07-09", "timestamp": 600, "status": 4},
            {"date": "2026-07-10", "status": "first"},
            {"date": "2026-07-10", "status": "second"},
        ]
        selected = latest_readiness_per_day(rows)
        self.assertEqual([row["status"] for row in selected], [1, 4, "first"])

    def test_status_200_and_255_are_preserved_raw(self) -> None:
        rows = normalize_readiness_data({"items": [{"date": "2026-07-08", "value": {
            "status": 200, "phyScore": 255, "phyInsight": 255,
            "mentScore": 255, "hrvInsight": 255, "afibScore": 255,
        }}]})
        self.assertEqual(rows[0]["status"], 200)
        self.assertEqual(rows[0]["phyScore"], 255)
        self.assertEqual(rows[0]["afibScore"], 255)
        self.assertEqual(rows[0]["status_mapping_confidence"], "unknown")
        self.assertEqual(rows[0]["sentinel_255_semantics"], "unknown")

    def test_event_domain_discovery_reports_nonempty_and_empty(self) -> None:
        def fake_fetch(event_type, sub_type, start, end):
            if (event_type, sub_type) == ("Charge", "wake_data"):
                return {"items": [{"value": {}}]}
            return {"items": []}

        result = discover_event_domains(
            fake_fetch, 0, 1,
            (("Charge", "wake_data"), ("LifeLoad", "summary")),
        )
        self.assertEqual(result[0]["status"], "nonempty")
        self.assertEqual(result[0]["item_count"], 1)
        self.assertEqual(result[1]["status"], "empty")

    def test_exertion_and_lifeload_field_names_are_not_recalculated(self) -> None:
        payload = {"items": [{"date": "2026-07-08", "value": {
            "recoveryFactor": 0.8, "totalScore": 90, "activityScore": 30,
            "exerciseScore": 40, "atl": 12, "ctl": 20, "tsb": 8,
            "exercisePlan": {"id": 1},
        }}]}
        rows = _normalize_value_records(
            payload, "exertion", "algo_result",
            ("recoveryFactor", "totalScore", "activityScore", "exerciseScore", "atl", "ctl", "tsb"),
        )
        self.assertEqual(rows[0]["atl"], 12)
        self.assertEqual(rows[0]["raw_value"]["exercisePlan"], {"id": 1})
        life = _normalize_value_records(
            {"items": [{"date": "2026-07-08", "value": {"lifeLoad": 65, "other": 1}}]},
            "LifeLoad", "summary", ("lifeLoad",), confidence="candidate",
        )
        self.assertEqual(life[0]["lifeLoad"], 65)
        self.assertEqual(life[0]["mapping_confidence"], "candidate")

    def test_charge_real_data_preserves_sample_resolution(self) -> None:
        rows = normalize_charge_data({"items": [{
            "date": "2026-07-08",
            "value": {"startTime": 1000000, "samples": [
                {"s": 0, "e": 60000, "total": 10, "physical": 6, "mental": 4},
                {"s": 60000, "total": 11, "physical": 7, "mental": 4},
            ]},
        }]})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["total"], 10)
        self.assertEqual(rows[1]["start_offset_ms"], 60000)
        self.assertIsNone(rows[1]["end_offset_ms"])

    def test_daily_status_uses_latest_hrv_and_count_only(self) -> None:
        hrv = [
            {"date": "2026-07-08", "hrv": 40, "sample_timestamp": 100, "source": "zepp", "calculation_source": "zepp", "mapping_confidence": "confirmed"},
            {"date": "2026-07-08", "hrv": 44, "sample_timestamp": 200, "source": "zepp", "calculation_source": "zepp", "mapping_confidence": "confirmed"},
        ]
        wake = [{"date": "2026-07-08", "physicalWake": 65, "source": "zepp", "calculation_source": "zepp", "mapping_confidence": "confirmed"}]
        status = consolidate_daily_status(hrv, wake, [], [])
        self.assertEqual(status[0]["hrv"]["latest"], 44)
        self.assertEqual(status[0]["hrv_sample_count"], 2)
        self.assertEqual(status[0]["wake_energy"]["physicalWake"], 65)
        self.assertNotIn("readiness", status[0])

    def test_daily_status_deduplicates_readiness_and_names_sleep_section(self) -> None:
        readiness = [
            {"date": "2026-07-08", "timestamp": 100, "timestampUpdate": 100, "status": 1, "sleepHRV": 40, "source": "zepp", "calculation_source": "zepp", "mapping_confidence": "confirmed"},
            {"date": "2026-07-08", "timestamp": 200, "timestampUpdate": 200, "status": 2, "sleepHRV": 42, "source": "zepp", "calculation_source": "zepp", "mapping_confidence": "confirmed"},
        ]
        status = consolidate_daily_status([], [], [], [], readiness, readiness)
        self.assertEqual(status[0]["readiness"]["status"], 2)
        self.assertEqual(status[0]["sleep_related_readiness"]["sleepHRV"], 42)
        self.assertNotIn("sleep", status[0])

    def test_empty_response_and_json_serialization(self) -> None:
        self.assertEqual(normalize_hrv_data({"items": []}), [])
        self.assertEqual(normalize_charge_data({}), [])
        status = consolidate_daily_status([], [], [], [])
        self.assertEqual(status, [])
        json.dumps(status)


if __name__ == "__main__":
    unittest.main()
