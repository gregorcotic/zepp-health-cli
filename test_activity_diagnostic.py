import json
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from zepp_health import (
    ZeppClient,
    _activity_diagnostic_window,
    compare_activity_sub_data_payloads,
    diagnose_activity_payload,
    inventory_activity_payload,
)


class ActivityDiagnosticTests(unittest.TestCase):
    def test_activity_diagnostic_reports_safe_fields_and_nested_shapes(self) -> None:
        payload = {
            "data": [{
                "trackId": 123456,
                "sportType": 6,
                "startTime": 1784847600,
                "duration": 3600,
                "distance": 10500,
                "maxHr": 171,
                "calories": 700,
                "name": "Krofička",
                "description": "private workout notes",
                "deviceId": "private-device",
                "accountId": 987654,
                "downloadUrl": "https://secret.example/file.fit",
                "accountEmail": "private@example.com",
                "route": [
                    {"timestamp": 1, "lat": 46.1, "lon": 14.2, "altitude": 900},
                    {"timestamp": 2, "lat": 46.2, "lon": 14.3, "altitude": 910},
                ],
                "laps": [{"distance": 1000, "duration": 300}],
            }],
        }
        report = diagnose_activity_payload(
            payload, sport_segment="run", limit=20
        )
        self.assertEqual(report["raw_record_count"], 1)
        record = report["records"][0]
        self.assertEqual(record["scalar_fields"]["trackId"], 123456)
        self.assertEqual(record["scalar_fields"]["distance"], 10500)
        self.assertEqual(record["text_fields"]["name"]["length"], 8)
        self.assertEqual(record["text_fields"]["description"]["present"], True)
        self.assertTrue(record["gps_present"])
        self.assertTrue(record["gps_track_present"])
        self.assertEqual(record["gps_point_count"], 2)
        self.assertEqual(
            record["track_field_names"],
            ["altitude", "lat", "lon", "timestamp"],
        )
        self.assertEqual(record["nested_structures"]["route"]["count"], 2)
        self.assertEqual(record["nested_structures"]["laps"]["count"], 1)
        self.assertIn("deviceId", record["omitted_sensitive_field_names"])
        self.assertIn("accountId", record["omitted_sensitive_field_names"])
        self.assertIn("downloadUrl", record["omitted_sensitive_field_names"])
        self.assertIn("accountEmail", record["unknown_scalar_field_names"])
        rendered = json.dumps(report)
        self.assertNotIn("Krofička", rendered)
        self.assertNotIn("private workout notes", rendered)
        self.assertNotIn("private-device", rendered)
        self.assertNotIn("987654", rendered)
        self.assertNotIn("secret.example", rendered)
        self.assertNotIn("private@example.com", rendered)
        self.assertNotIn("46.1", rendered)

    def test_activity_text_requires_explicit_opt_in(self) -> None:
        payload = {"items": [{"name": "Krofička", "notes": "Deadlift 5x5"}]}
        hidden = diagnose_activity_payload(payload, sport_segment="run")
        shown = diagnose_activity_payload(
            payload, sport_segment="run", include_text=True
        )
        self.assertNotIn("Krofička", json.dumps(hidden))
        self.assertEqual(shown["records"][0]["text_fields"]["name"], "Krofička")
        self.assertEqual(
            shown["records"][0]["text_fields"]["notes"], "Deadlift 5x5"
        )

    def test_activity_diagnostic_limits_output_not_raw_count(self) -> None:
        payload = [{"trackId": number} for number in range(5)]
        report = diagnose_activity_payload(
            payload, sport_segment="walking", limit=2
        )
        self.assertEqual(report["raw_record_count"], 5)
        self.assertEqual(report["matched_record_count"], 5)
        self.assertEqual(report["reported_record_count"], 2)
        self.assertEqual(len(report["records"]), 2)
        with self.assertRaises(ValueError):
            diagnose_activity_payload(payload, sport_segment="walking", limit=0)

    def test_activity_window_uses_inclusive_local_seconds(self) -> None:
        start, stop = _activity_diagnostic_window(
            "2026-07-24", "2026-07-24", "Europe/Ljubljana"
        )
        zone = ZoneInfo("Europe/Ljubljana")
        self.assertEqual(
            datetime.fromtimestamp(start, zone).isoformat(),
            "2026-07-24T00:00:00+02:00",
        )
        self.assertEqual(
            datetime.fromtimestamp(stop, zone).isoformat(),
            "2026-07-24T23:59:59+02:00",
        )

    def test_unknown_wrapper_produces_no_assumed_records_but_keeps_shape(self) -> None:
        report = diagnose_activity_payload(
            {"unexpected": [{"trackId": 1}]}, sport_segment="strength"
        )
        self.assertEqual(report["raw_record_count"], 0)
        self.assertEqual(report["response_structure"]["type"], "object")
        self.assertIn(
            "unexpected", report["response_structure"]["field_names"]
        )

    def test_production_data_summary_wrapper_is_extracted_and_sanitized(self) -> None:
        payload = {
            "code": 1,
            "message": "success",
            "data": {
                "next": 1784671200,
                "summary": [{
                    "trackid": 112233,
                    "parent_trackid": 0,
                    "sport_mode": 37,
                    "type": 9,
                    "sport_title": "Private title",
                    "crossfitContent": "Deadlift 5x5",
                    "start_time": 1784700000,
                    "end_time": 1784703600,
                    "dis": 2500,
                    "elevationGain": 120,
                    "avg_heart_rate": 141,
                    "max_heart_rate": 171,
                    "rpe": 5,
                    "te": 0.3,
                    "anaerobic_te": 0,
                    "exercise_load": 4,
                    "workoutBalance": {"cardiac": 0, "muscular": 100},
                    "strengthScores": [{"exercise": "Deadlift", "score": 88}],
                    "strength_training_group": 2,
                    "totalCardiacExertion": 4,
                    "totalMuscularExertion": 9,
                    "totalExertion": 13,
                    "totalInsight": 1,
                    "coachInsight": "Private coaching text",
                    "child_list": [{
                        "reps": 5,
                        "weight": 162.5,
                        "exercise": "Deadlift",
                    }],
                    "add_info": {"rest_seconds": 90, "note": "Private note"},
                    "originSummary": {"source_type": 130},
                    "deviceid": "private-device",
                    "location": "private-location",
                }],
            },
        }
        hidden = diagnose_activity_payload(payload, sport_segment="run")
        self.assertEqual(hidden["raw_record_count"], 1)
        self.assertEqual(hidden["response_metadata"]["record_wrapper"], "data.summary")
        self.assertEqual(hidden["response_metadata"]["next"], 1784671200)
        record = hidden["records"][0]
        self.assertEqual(record["scalar_fields"]["trackid"], 112233)
        self.assertEqual(record["scalar_fields"]["sport_mode"], 37)
        self.assertEqual(record["scalar_fields"]["elevationGain"], 120)
        self.assertEqual(record["coaching_fields"]["rpe"], 5)
        self.assertEqual(record["coaching_fields"]["te"], 0.3)
        self.assertEqual(record["coaching_fields"]["exercise_load"], 4)
        self.assertNotIn("rpe", record["unknown_scalar_field_names"])
        self.assertNotIn("totalExertion", record["unknown_scalar_field_names"])
        self.assertEqual(
            record["coaching_fields"]["workoutBalance"]["scalar_values"],
            {"cardiac": 0, "muscular": 100},
        )
        self.assertEqual(
            record["nested_structures"]["child_list"]["samples"][0][
                "scalar_values"
            ],
            {"reps": 5, "weight": 162.5},
        )
        self.assertEqual(record["text_fields"]["sport_title"]["present"], True)
        self.assertFalse(record["gps_present"])
        self.assertFalse(record["gps_track_present"])
        self.assertEqual(record["gps_point_count"], 0)
        self.assertTrue(record["location_metadata"]["present"])
        self.assertIn("deviceid", record["omitted_sensitive_field_names"])
        rendered = json.dumps(hidden)
        self.assertNotIn("Private title", rendered)
        self.assertNotIn("Deadlift 5x5", rendered)
        self.assertNotIn("Private coaching text", rendered)
        self.assertNotIn("Private note", rendered)
        self.assertNotIn("Deadlift", rendered)
        self.assertNotIn("private-device", rendered)
        self.assertNotIn("private-location", rendered)

        shown = diagnose_activity_payload(
            payload, sport_segment="run", include_text=True
        )
        self.assertEqual(
            shown["records"][0]["text_fields"]["sport_title"], "Private title"
        )
        self.assertEqual(
            shown["records"][0]["text_fields"]["crossfitContent"],
            "Deadlift 5x5",
        )
        self.assertEqual(
            shown["records"][0]["coaching_fields"]["coachInsight"],
            "Private coaching text",
        )
        self.assertEqual(
            shown["records"][0]["nested_structures"]["child_list"]["samples"][0][
                "text_values"
            ]["exercise"],
            "Deadlift",
        )

    def test_location_coordinates_are_metadata_not_a_gps_track(self) -> None:
        payload = {
            "data": {
                "summary": [{
                    "trackid": 1,
                    "location": {"latitude": 46.1, "longitude": 14.2},
                }]
            }
        }
        record = diagnose_activity_payload(payload, sport_segment="run")["records"][0]
        self.assertTrue(record["location_metadata"]["present"])
        self.assertFalse(record["gps_track_present"])
        self.assertEqual(record["gps_point_count"], 0)
        self.assertNotIn("46.1", json.dumps(record))

    def test_track_filter_prevents_unrelated_text_and_reports_outdoor_streams(self) -> None:
        payload = {
            "data": {
                "summary": [
                    {"trackid": 1, "sport_title": "Unrelated private title"},
                    {
                        "trackid": 1784739852,
                        "sport_title": "Ojstrica",
                        "route": [
                            {
                                "timestamp": 1000,
                                "latitude": 46.1,
                                "longitude": 14.2,
                                "altitude": 700,
                                "heartRate": 120,
                                "speed": 2.1,
                            },
                            {
                                "timestamp": 2000,
                                "latitude": 46.2,
                                "longitude": 14.3,
                                "altitude": 900,
                                "heartRate": 135,
                                "speed": 1.8,
                            },
                        ],
                    },
                ]
            }
        }
        report = diagnose_activity_payload(
            payload,
            sport_segment="run",
            track_id="1784739852",
            include_text=True,
        )
        self.assertEqual(report["raw_record_count"], 2)
        self.assertEqual(report["matched_record_count"], 1)
        self.assertEqual(report["track_id_filter"], "1784739852")
        self.assertEqual(len(report["records"]), 1)
        record = report["records"][0]
        self.assertEqual(record["gps_point_count"], 2)
        self.assertTrue(record["altitude_stream_present"])
        self.assertEqual(record["altitude_sample_count"], 2)
        self.assertTrue(record["workout_hr_stream_present"])
        self.assertEqual(record["workout_hr_sample_count"], 2)
        self.assertEqual(
            record["track_time_coverage"],
            {
                "timestamp_field_names": ["timestamp"],
                "sample_count_with_timestamp": 2,
                "raw_start": 1000,
                "raw_end": 2000,
            },
        )
        rendered = json.dumps(report)
        self.assertIn("Ojstrica", rendered)
        self.assertNotIn("Unrelated private title", rendered)
        self.assertNotIn("46.1", rendered)
        self.assertNotIn("14.2", rendered)

    def test_sport_history_passes_need_sub_data_without_changing_contract(self) -> None:
        client = ZeppClient("private-token", "private-user", "example.invalid")
        with patch.object(client, "get_json", return_value={"data": {}}) as get_json:
            client.sport_history(
                "run", 1784671200, 1784757599, need_sub_data=0
            )
        get_json.assert_called_once_with(
            "/v1/sport/run/history.json",
            {
                "userid": "private-user",
                "startTrackId": 1784671200,
                "stopTrackId": 1784757599,
                "need_sub_data": 0,
                "type": "",
            },
        )

    def test_sub_data_diff_is_structural_and_coordinate_safe(self) -> None:
        without_sub_data = {
            "data": {
                "summary": [{
                    "trackid": 42,
                    "exercise_load": 10,
                    "sport_title": "Private hike",
                }]
            }
        }
        with_sub_data = {
            "data": {
                "summary": [{
                    "trackid": 42,
                    "exercise_load": 11,
                    "sport_title": "Private hike",
                    "child_list": [{"lap": 1, "note": "Private detail"}],
                    "route": [{
                        "timestamp": 1000,
                        "latitude": 46.1,
                        "longitude": 14.2,
                        "altitude": 900,
                        "heartRate": 120,
                    }],
                }]
            }
        }
        report = compare_activity_sub_data_payloads(
            without_sub_data,
            with_sub_data,
            sport_segment="run",
            track_id=42,
        )
        self.assertIn("$.child_list", report["diff"]["structure_paths_added"])
        self.assertIn("$.route", report["diff"]["structure_paths_added"])
        self.assertTrue(report["diff"]["safe_values_changed"])
        rendered = json.dumps(report)
        self.assertNotIn("Private hike", rendered)
        self.assertNotIn("Private detail", rendered)
        self.assertNotIn("46.1", rendered)
        self.assertNotIn("14.2", rendered)

    def test_coverage_inventory_groups_types_and_labels_fixture_mappings(self) -> None:
        payload = {
            "data": {
                "next": -1,
                "summary": [
                    {"trackid": 30, "type": 130, "sport_mode": 1, "run_time": 600},
                    {"trackid": 31, "type": 130, "sport_mode": 1, "run_time": 700},
                    {"trackid": 40, "type": 22, "sport_mode": 2, "dis": 10000},
                    {"trackid": 50, "type": 999, "sport_mode": 3},
                ],
            }
        }
        report = inventory_activity_payload(payload)
        self.assertEqual(report["raw_record_count"], 4)
        self.assertEqual(report["type_group_count"], 3)
        self.assertTrue(
            report["pagination"]["counts_are_complete_for_requested_window"]
        )
        mappings = {
            group["type"]: group["known_mapping"] for group in report["type_groups"]
        }
        self.assertEqual(mappings[22]["sport_family"], "Hike")
        self.assertEqual(mappings[130]["sport_family"], "Cross-training")
        self.assertIsNone(mappings[999])

    def test_coverage_inventory_distinguishes_empty_absent_and_unknown_negative(self) -> None:
        payload = {
            "data": {
                "next": 123,
                "summary": [{
                    "trackid": 1,
                    "type": 9,
                    "sport_mode": 0,
                    "average_power": -1,
                    "sport_title": "",
                    "avg_cadence": 81,
                }],
            }
        }
        report = inventory_activity_payload(payload)
        group = report["type_groups"][0]
        states = group["field_status_counts"]
        self.assertEqual(states["average_power"], {"UNKNOWN_SEMANTICS": 1})
        self.assertEqual(states["sport_title"], {"PRESENT_EMPTY": 1})
        self.assertEqual(states["avg_cadence"], {"PRESENT_WITH_VALUE": 1})
        self.assertEqual(states["swolf"], {"ABSENT": 1})
        self.assertEqual(
            report["pagination"]["status"], "INCOMPLETE_PAGINATION_UNRESOLVED"
        )
        self.assertFalse(
            report["pagination"]["counts_are_complete_for_requested_window"]
        )

    def test_coverage_inventory_is_sport_aware_and_coordinate_safe(self) -> None:
        payload = {
            "data": {
                "next": -1,
                "summary": [
                    {
                        "trackid": 10,
                        "type": 80,
                        "sport_mode": 1,
                        "swim_pool_length": 25,
                        "swolf": 41,
                        "location": {"latitude": 46.1, "longitude": 14.2},
                        "sport_title": "Private pool session",
                    },
                    {
                        "trackid": 11,
                        "type": 81,
                        "sport_mode": 2,
                        "route": [{
                            "timestamp": 1,
                            "lat": 46.2,
                            "lon": 14.3,
                            "heart_rate": 120,
                        }],
                    },
                ],
            }
        }
        report = inventory_activity_payload(payload)
        groups = {group["type"]: group for group in report["type_groups"]}
        self.assertEqual(groups[80]["location_metadata_present_count"], 1)
        self.assertEqual(groups[80]["gps_track_present_count"], 0)
        self.assertEqual(groups[81]["gps_track_present_count"], 1)
        rendered = json.dumps(report)
        self.assertNotIn("Private pool session", rendered)
        self.assertNotIn("46.1", rendered)
        self.assertNotIn("14.3", rendered)


if __name__ == "__main__":
    unittest.main()
