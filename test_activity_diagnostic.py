import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from zepp_health import (
    _activity_diagnostic_window,
    diagnose_activity_payload,
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


if __name__ == "__main__":
    unittest.main()
