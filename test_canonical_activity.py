import json
import unittest

from zepp_health import canonicalize_activity, safe_canonical_activity


def detail(track_id: int, **fields):
    return {"data": {"trackid": track_id, **fields}}


class CanonicalActivityTests(unittest.TestCase):
    def test_hiking_normalizes_independent_gps_altitude_and_hr_streams(self):
        history = {
            "trackid": 1784948221,
            "source": "private-source",
            "type": 22,
            "sport_mode": 0,
            "run_time": 100,
            "highPrecisionDistance": 1234,
            "altitude_ascend": 321,
            "altitude_descend": 300,
            "avg_heart_rate": 130,
            "min_heart_rate": 90,
            "max_heart_rate": 170,
        }
        activity = canonicalize_activity(
            history,
            detail(
                1784948221,
                longitude_latitude="1420000000,4610000000;100,100;",
                time="0;2;",
                altitude="78681;80000;",
                heart_rate="0,90;1,10;2,-5;",
            ),
            timezone_name="Europe/Ljubljana",
        )
        self.assertEqual(activity["streams"]["gps"]["status"], "AVAILABLE")
        self.assertEqual(activity["streams"]["gps"]["sample_count"], 2)
        self.assertEqual(activity["streams"]["altitude"]["status"], "AVAILABLE")
        self.assertEqual(
            activity["streams"]["altitude"]["samples"][0]["value_m"], 786.81
        )
        self.assertEqual(activity["streams"]["heart_rate"]["sample_count"], 3)
        self.assertEqual(
            activity["quality"]["stream_alignment"],
            "INDEPENDENT_OFFSETS_NO_INDEX_ALIGNMENT",
        )
        self.assertEqual(
            activity["summary"]["reported_elevation_gain_m"]["value"], 321
        )
        self.assertEqual(activity["streams"]["power"]["status"], "NOT_APPLICABLE")

    def test_cross_training_notes_are_internal_but_safe_output_hides_text(self):
        history = {
            "trackid": 100,
            "source": "private-source",
            "type": 130,
            "sport_mode": 0,
            "run_time": 600,
        }
        activity = canonicalize_activity(
            history,
            detail(100, heart_rate="0,80;1,5;", memo="Private Deadlift note"),
        )
        self.assertEqual(activity["streams"]["gps"]["status"], "NOT_APPLICABLE")
        self.assertEqual(
            activity["streams"]["altitude"]["status"], "NOT_APPLICABLE"
        )
        self.assertTrue(activity["notes"]["present"])
        self.assertEqual(activity["notes"]["text"], "Private Deadlift note")
        safe = safe_canonical_activity(activity)
        rendered = json.dumps(safe)
        self.assertNotIn("Private Deadlift", rendered)
        self.assertNotIn("private-source", rendered)
        self.assertTrue(safe["notes"]["present"])
        self.assertEqual(safe["notes"]["length"], 21)

    def test_pool_swim_preserves_structural_laps_without_gps_warning(self):
        history = {
            "trackid": 200,
            "type": 14,
            "sport_mode": 0,
            "run_time": 1200,
            "dis": 1000,
        }
        activity = canonicalize_activity(
            history,
            detail(
                200,
                lap="1,2,3;4,5,6;",
                pool_swim_pace="10,20;",
                pool_stroke_speed="30,40;",
                currentDistance="500;1000;",
                heart_rate="0,90;1,5;",
            ),
        )
        self.assertEqual(activity["streams"]["gps"]["status"], "NOT_APPLICABLE")
        self.assertEqual(activity["laps"]["lap"]["status"], "AVAILABLE")
        self.assertEqual(activity["laps"]["lap"]["sample_count"], 2)
        self.assertEqual(
            activity["laps"]["lap"]["records"][0]["raw_components"],
            ["1", "2", "3"],
        )
        self.assertNotIn("GPS_STREAM_MISSING", activity["quality"]["flags"])

    def test_open_water_altitude_sentinel_is_never_scaled_as_real_altitude(self):
        history = {
            "trackid": 300,
            "type": 15,
            "sport_mode": 0,
            "run_time": 1000,
        }
        activity = canonicalize_activity(
            history,
            detail(
                300,
                longitude_latitude="1420000000,4610000000;",
                time="0;",
                altitude="-2000000;-2000000;",
                heart_rate="0,100;",
            ),
        )
        altitude = activity["streams"]["altitude"]
        self.assertEqual(altitude["status"], "SENTINEL_UNAVAILABLE")
        self.assertTrue(
            all(sample["value_m"] is None for sample in altitude["samples"])
        )
        self.assertNotIn("-20000", json.dumps(safe_canonical_activity(activity)))
        self.assertIn("ALTITUDE_SENTINEL", activity["quality"]["flags"])

    def test_gravel_optional_power_absence_is_not_a_quality_error(self):
        history = {
            "trackid": 400,
            "type": 208,
            "sport_mode": 0,
            "run_time": 1000,
        }
        activity = canonicalize_activity(
            history,
            detail(400, cadence="0,80;1,1;", heart_rate="0,100;"),
        )
        self.assertEqual(activity["streams"]["cadence"]["status"], "AVAILABLE")
        self.assertEqual(
            activity["streams"]["power"]["status"],
            "SUPPORTED_BUT_NOT_RECORDED",
        )
        self.assertIn("POWER_SENSOR_NOT_RECORDED", activity["quality"]["flags"])
        self.assertNotIn("INVALID", activity["quality"]["flags"])

    def test_ski_descent_never_becomes_ascent(self):
        history = {
            "trackid": 500,
            "type": 105,
            "sport_mode": 0,
            "run_time": 1000,
            "altitude_ascend": 0,
            "altitude_descend": 5921,
            "climb_dis_descend": 28133,
        }
        activity = canonicalize_activity(history, detail(500, altitude="96583;191362;"))
        self.assertEqual(activity["summary"]["vertical_descent_m"]["value"], 5921)
        self.assertEqual(
            activity["summary"]["reported_elevation_gain_m"]["status"],
            "NOT_APPLICABLE",
        )
        self.assertIsNone(
            activity["summary"]["reported_elevation_gain_m"]["value"]
        )

    def test_safe_serializer_hides_coordinates_samples_notes_and_source(self):
        history = {
            "trackid": 600,
            "source": "secret-source",
            "type": 22,
            "sport_mode": 0,
            "run_time": 100,
        }
        activity = canonicalize_activity(
            history,
            detail(
                600,
                longitude_latitude="1423456789,4612345678;",
                time="0;",
                altitude="90000;",
                memo="Secret workout note",
            ),
        )
        rendered = json.dumps(safe_canonical_activity(activity))
        self.assertNotIn("14.23456789", rendered)
        self.assertNotIn("46.12345678", rendered)
        self.assertNotIn("1423456789", rendered)
        self.assertNotIn("Secret workout", rendered)
        self.assertNotIn("secret-source", rendered)
        self.assertIn('"sample_count": 1', rendered)

    def test_detail_identity_mismatch_is_flagged_and_not_merged(self):
        history = {
            "trackid": 700,
            "type": 22,
            "sport_mode": 0,
            "run_time": 100,
        }
        activity = canonicalize_activity(
            history,
            detail(
                701,
                longitude_latitude="1420000000,4610000000;",
                altitude="90000;",
                memo="Wrong activity",
            ),
        )
        self.assertIn(
            "HISTORY_DETAIL_TRACK_ID_MISMATCH", activity["quality"]["flags"]
        )
        self.assertEqual(activity["streams"]["gps"]["sample_count"], 0)
        self.assertEqual(activity["streams"]["power"]["status"], "UNKNOWN")
        self.assertFalse(activity["notes"]["present"])

    def test_unknown_sport_keeps_conservative_unknown_semantics(self):
        history = {
            "trackid": 800,
            "type": 999,
            "sport_mode": 9,
            "run_time": 100,
        }
        activity = canonicalize_activity(history, detail(800))
        self.assertIsNone(activity["identity"]["sport_name"])
        self.assertEqual(activity["identity"]["mapping_confidence"], "UNKNOWN")
        self.assertTrue(
            all(
                value == "UNKNOWN"
                for value in activity["sport_capabilities"].values()
            )
        )
        self.assertEqual(
            activity["summary"]["reported_elevation_gain_m"]["status"],
            "UNKNOWN",
        )


if __name__ == "__main__":
    unittest.main()
