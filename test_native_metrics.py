import json
import unittest

from zepp_health import (
    consolidate_daily_status,
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
