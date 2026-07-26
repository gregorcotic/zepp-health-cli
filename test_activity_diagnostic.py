import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from zepp_health import (
    ZeppClient,
    _activity_diagnostic_window,
    compare_activity_sub_data_payloads,
    diagnose_activity_payload,
    format_sport_coverage_mapping_list,
    inventory_activity_payload,
    interpret_activity_metrics,
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
                    {"trackid": 30, "type": 130, "sport_mode": 0, "run_time": 600},
                    {"trackid": 31, "type": 130, "sport_mode": 0, "run_time": 700},
                    {"trackid": 40, "type": 22, "sport_mode": 0, "dis": 10000},
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
        self.assertEqual(mappings[22]["sport_family"], "Hiking")
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

    def test_coverage_representative_uses_end_time_in_requested_timezone(self) -> None:
        end_time = int(
            datetime(2026, 7, 25, 18, 30, tzinfo=timezone.utc).timestamp()
        )
        payload = {
            "data": {
                "next": -1,
                "summary": [{
                    "trackid": 1784948221,
                    "type": 22,
                    "sport_mode": 0,
                    "end_time": end_time,
                    "totalTimeWithMillis": 3_697_607,
                    "highPrecisionDistance": 14_207.45,
                    "calorie": 6567,
                    "sport_title": "Private hike title",
                    "deviceid": "private-device",
                }],
            }
        }
        group = inventory_activity_payload(
            payload, timezone_name="Europe/Ljubljana"
        )["type_groups"][0]
        self.assertEqual(group["representative_end_time"], end_time)
        self.assertEqual(group["representative_local_date"], "2026-07-25")
        self.assertEqual(group["representative_local_time"], "20:30:00")
        self.assertEqual(group["representative_duration"], 3697.607)
        self.assertEqual(
            group["representative_duration_source_field"], "totalTimeWithMillis"
        )
        self.assertEqual(group["representative_distance"], 14_207.45)
        self.assertEqual(
            group["representative_distance_source_field"], "highPrecisionDistance"
        )
        self.assertEqual(group["representative_calories"], 6567)
        rendered = json.dumps(group)
        self.assertNotIn("Private hike title", rendered)
        self.assertNotIn("private-device", rendered)

    def test_coverage_mapping_list_is_human_readable_and_text_safe(self) -> None:
        payload = {
            "data": {
                "next": -1,
                "summary": [{
                    "trackid": 42,
                    "type": 14,
                    "sport_mode": 0,
                    "end_time": 1785002400,
                    "run_time": 3723,
                    "dis": 12345,
                    "calorie": 500,
                    "sport_title": "Private activity",
                }],
            }
        }
        inventory = inventory_activity_payload(
            payload, timezone_name="Europe/Ljubljana"
        )
        output = format_sport_coverage_mapping_list(inventory)
        self.assertIn("type=14 sport_mode=0", output)
        self.assertIn("duration=01:02:03", output)
        self.assertIn("distance=12.35 km", output)
        self.assertIn("calories=500 kcal", output)
        self.assertIn("trackid=42", output)
        self.assertNotIn("Private activity", output)

    def test_production_verified_catalog_preserves_type_and_mode_pairs(self) -> None:
        expected = {
            (105, 0): "Ski",
            (130, 0): "Cross-training",
            (14, 0): "Pool Swim",
            (15, 0): "Open Water Swim",
            (15, 5): "Open Water Swim - Zepp Coach",
            (207, 0): "E-MTB",
            (208, 0): "Gravel Cycling",
            (22, 0): "Hiking",
            (22, 5): "Hiking - Zepp Coach",
            (224, 0): "Mountain Hiking",
            (6, 0): "Walking",
            (6, 5): "Walking - Zepp Coach",
            (9, 0): "Outdoor Cycling",
            (9, 5): "Outdoor Cycling - Zepp Coach",
        }
        payload = {
            "data": {
                "next": -1,
                "summary": [
                    {"trackid": index, "type": type_id, "sport_mode": mode}
                    for index, (type_id, mode) in enumerate(expected, start=1)
                ],
            }
        }
        groups = inventory_activity_payload(payload)["type_groups"]
        actual = {
            (group["type"], group["sport_mode"]): group["known_mapping"]["sport_name"]
            for group in groups
        }
        self.assertEqual(actual, expected)
        self.assertNotEqual(
            actual[(15, 0)], actual[(15, 5)]
        )

    def test_ski_vertical_descent_never_becomes_climbing_ascent(self) -> None:
        fixture = {
            "trackid": "1767339463",
            "type": 105,
            "sport_mode": 0,
            "end_time": "1767354287",
            "run_time": "14824",
            "dis": "28130",
            "altitude_ascend": 0,
            "altitude_descend": 5913,
            "max_altitude": 1913,
            "min_altitude": 965,
            "downhill_max_altitude_desend": 857,
        }
        result = interpret_activity_metrics(fixture)
        self.assertEqual(result["sport_mapping"]["sport_name"], "Ski")
        self.assertEqual(result["normalized_metrics"]["duration_s"]["value"], 14824)
        self.assertEqual(result["normalized_metrics"]["distance_m"]["value"], 28130)
        self.assertEqual(
            result["normalized_metrics"]["vertical_descent_m"]["value"], 5913
        )
        self.assertEqual(
            result["normalized_metrics"]["vertical_descent_m"]["source_field"],
            "altitude_descend",
        )
        self.assertIsNone(
            result["normalized_metrics"]["elevation_gain_m"]["value"]
        )
        self.assertFalse(result["climbing_load"]["eligible"])
        self.assertIsNone(result["climbing_load"]["athlete_powered_ascent_m"])
        self.assertEqual(result["raw_metrics"]["altitude_descend"], 5913)
        self.assertEqual(result["raw_metrics"]["altitude_ascend"], 0)
        self.assertEqual(result["raw_metrics"]["dis"], "28130")
        self.assertEqual(result["metric_semantics"], "PROVEN")

    def test_semantic_duration_uses_supported_fallback_precedence(self) -> None:
        cases = (
            (
                {
                    "run_time": 120,
                    "exerciseTimeWithMillis": 110_000,
                    "totalTimeWithMillis": 130_000,
                },
                120,
                "run_time",
            ),
            (
                {"exerciseTimeWithMillis": 110_500},
                110.5,
                "exerciseTimeWithMillis",
            ),
            (
                {"totalTimeWithMillis": 130_250},
                130.25,
                "totalTimeWithMillis",
            ),
        )
        for fields, expected_value, expected_source in cases:
            with self.subTest(source=expected_source):
                result = interpret_activity_metrics({
                    "type": 130,
                    "sport_mode": 0,
                    **fields,
                })
                duration = result["normalized_metrics"]["duration_s"]
                self.assertEqual(duration["value"], expected_value)
                self.assertEqual(duration["source_field"], expected_source)

    def test_hiking_sentinel_ascent_never_enables_climbing_load(self) -> None:
        for sentinel in (-1, -100, -20000, -274):
            with self.subTest(sentinel=sentinel):
                result = interpret_activity_metrics({
                    "type": 22,
                    "sport_mode": 0,
                    "altitude_ascend": sentinel,
                })
                self.assertEqual(
                    result["raw_metrics"]["altitude_ascend"], sentinel
                )
                gain = result["normalized_metrics"]["elevation_gain_m"]
                self.assertIsNone(gain["value"])
                self.assertEqual(gain["source_field"], "altitude_ascend")
                self.assertEqual(
                    gain["reason"], "invalid_or_unavailable_ascent_metric"
                )
                self.assertFalse(result["climbing_load"]["eligible"])
                self.assertIsNone(
                    result["climbing_load"]["athlete_powered_ascent_m"]
                )
                self.assertEqual(
                    result["climbing_load"]["reason"],
                    "invalid_or_unavailable_ascent_metric",
                )

    def test_distance_sentinels_fall_back_and_preserve_raw_values(self) -> None:
        for sentinel in (-1, -100, -20000, -274):
            with self.subTest(sentinel=sentinel):
                result = interpret_activity_metrics({
                    "type": 22,
                    "sport_mode": 0,
                    "highPrecisionDistance": sentinel,
                    "dis": 10000,
                })
                distance = result["normalized_metrics"]["distance_m"]
                self.assertEqual(distance["value"], 10000)
                self.assertEqual(distance["source_field"], "dis")
                self.assertEqual(
                    result["raw_metrics"]["highPrecisionDistance"], sentinel
                )
                self.assertEqual(result["raw_metrics"]["dis"], 10000)

        unavailable = interpret_activity_metrics({
            "type": 22,
            "sport_mode": 0,
            "highPrecisionDistance": -1,
            "dis": -100,
        })
        distance = unavailable["normalized_metrics"]["distance_m"]
        self.assertIsNone(distance["value"])
        self.assertIsNone(distance["source_field"])
        self.assertEqual(
            unavailable["raw_metrics"]["highPrecisionDistance"], -1
        )
        self.assertEqual(unavailable["raw_metrics"]["dis"], -100)

    def test_missing_hiking_ascent_has_factual_unavailable_reason(self) -> None:
        cases = (
            {},
            {"altitude_ascend": ""},
            {"altitude_ascend": "not-a-number"},
        )
        for fields in cases:
            with self.subTest(fields=fields):
                result = interpret_activity_metrics({
                    "type": 22,
                    "sport_mode": 0,
                    **fields,
                })
                gain = result["normalized_metrics"]["elevation_gain_m"]
                self.assertIsNone(gain["value"])
                self.assertEqual(
                    gain["reason"], "missing_or_unavailable_ascent_metric"
                )
                self.assertFalse(result["climbing_load"]["eligible"])
                self.assertIsNone(
                    result["climbing_load"]["athlete_powered_ascent_m"]
                )
                self.assertEqual(
                    result["climbing_load"]["reason"],
                    "missing_or_unavailable_ascent_metric",
                )

    def test_unknown_sport_semantics_never_enable_climbing_load(self) -> None:
        result = interpret_activity_metrics({
            "type": 999,
            "sport_mode": 0,
            "altitude_ascend": 1500,
        })
        self.assertIsNone(result["sport_mapping"])
        self.assertEqual(result["metric_semantics"], "UNKNOWN")
        self.assertFalse(result["climbing_load"]["eligible"])
        self.assertIsNone(result["climbing_load"]["athlete_powered_ascent_m"])
        self.assertEqual(result["raw_metrics"]["altitude_ascend"], 1500)

    def test_inferred_coach_mode_ascent_is_not_yet_climbing_load(self) -> None:
        result = interpret_activity_metrics({
            "type": 22,
            "sport_mode": 5,
            "altitude_ascend": 800,
        })
        self.assertEqual(
            result["sport_mapping"]["sport_name"], "Hiking - Zepp Coach"
        )
        self.assertEqual(result["metric_semantics"], "INFERRED")
        self.assertFalse(result["climbing_load"]["eligible"])
        self.assertEqual(
            result["climbing_load"]["reason"],
            "sport_metric_semantics_not_proven",
        )


if __name__ == "__main__":
    unittest.main()
