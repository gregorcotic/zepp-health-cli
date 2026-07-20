import json
import tempfile
import unittest
from pathlib import Path

from zepp_health import _format_offset, _write_insight_csv, normalize_insight_data


class InsightParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "items": [
                {
                    "timestamp": 1783382400000,
                    "startTime": 1783382400000,
                    "timezone": "Europe/Zagreb",
                    "deviceId": "4,app",
                    "deviceType": "4,2",
                    "samples": [
                        {
                            "insightId": 1,
                            "insight": 66,
                            "type": 2,
                            "diff": -4,
                            "slope": -0.1428571492433548,
                            "s": 1080000,
                            "e": 2700000,
                            "trackId": 1783403679,
                            "thres": 0,
                            "u": 89292730,
                            "jsonExtra": '{"minsBelowThreshold": 0, "diffBelowThreshold": 0}',
                        },
                        {"type": 3, "insight": 13, "s": 0},
                    ],
                }
            ]
        }

    def test_normalizes_valid_records_and_json_extra(self) -> None:
        records = normalize_insight_data(self.payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["date"], "2026-07-07")
        self.assertEqual(records[0]["samples"][0]["insight"], 66)
        self.assertEqual(records[0]["samples"][0]["json_extra"]["diffBelowThreshold"], 0)
        self.assertEqual(records[0]["samples"][0]["parsed_json_extra"]["minsBelowThreshold"], 0)
        self.assertEqual(records[0]["samples"][0]["raw_u"], 89292730)
        self.assertEqual(records[0]["samples"][1]["end_offset_ms"], None)

    def test_missing_fields_and_malformed_json_do_not_crash(self) -> None:
        records = normalize_insight_data({"items": [{"date": "2026-07-08", "samples": [
            {"jsonExtra": "not json", "insight": 55}
        ]}]})
        sample = records[0]["samples"][0]
        self.assertEqual(sample["json_extra"], "not json")
        self.assertIn("json_extra_error", sample)
        self.assertIsNone(sample["track_id"])

    def test_empty_and_multiple_days(self) -> None:
        self.assertEqual(normalize_insight_data({"items": []}), [])
        records = normalize_insight_data({"data": [
            {"date": "2026-07-07", "samples": [{"type": 1}]},
            {"date": "2026-07-08", "samples": [{"type": 2}, {"type": 4}]},
        ]})
        self.assertEqual([len(record["samples"]) for record in records], [1, 2])

    def test_offset_formatting(self) -> None:
        self.assertEqual(_format_offset(0), "00:00:00")
        self.assertEqual(_format_offset(3723000), "01:02:03")
        self.assertEqual(_format_offset(None), "—")

    def test_json_output_normalization_is_serializable(self) -> None:
        records = normalize_insight_data(self.payload)
        decoded = json.loads(json.dumps(records, ensure_ascii=False))
        self.assertEqual(decoded[0]["samples"][0]["type"], 2)
        self.assertEqual(decoded[0]["samples"][0]["start_offset_ms"], 1080000)

    def test_csv_has_one_row_per_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "insights.csv"
            _write_insight_csv(str(path), normalize_insight_data(self.payload))
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn("mins_below_threshold", lines[0])
            self.assertIn("2026-07-07", lines[1])


if __name__ == "__main__":
    unittest.main()
