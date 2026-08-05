from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from liquid_glass_dynamic_sampler import measure


def manifest() -> dict[str, object]:
    return {
        "ciCommit": "abc",
        "osBuild": "test",
        "dynamicSequences": [{
            "id": "wallpaper-transition__clear__dark",
            "captureAttempts": 10,
            "decodedSamples": 10,
            "frames": [
                {
                    "actualSeconds": 0,
                    "captureDurationSeconds": 0.001,
                },
                {
                    "actualSeconds": 0.5,
                    "captureDurationSeconds": 0.002,
                },
                {
                    "actualSeconds": 1.0,
                    "captureDurationSeconds": 0.004,
                },
            ],
        }],
    }


class DynamicSamplerTests(unittest.TestCase):
    def test_directory_measurement_separates_capture_and_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text(json.dumps(manifest()))
            report = measure([root])

        aggregate = report["aggregate"]
        self.assertEqual(aggregate["captureAttempts"], 10)
        self.assertEqual(aggregate["medianRawCaptureMilliseconds"], 3)
        self.assertEqual(
            aggregate["medianObservedMillisecondsPerAttempt"],
            100,
        )
        self.assertEqual(
            aggregate["medianEstimatedUnmeasuredMillisecondsPerAttempt"],
            96,
        )

    def test_zip_artifact_is_supported_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "capture.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                entry = zipfile.ZipInfo(
                    "nested/manifest.json",
                    date_time=(2026, 1, 1, 0, 0, 0),
                )
                archive.writestr(
                    entry,
                    json.dumps(manifest()),
                )
            report = measure([archive_path])

        self.assertEqual(report["aggregate"]["sequences"], 1)
        self.assertIn("!/nested/manifest.json", report["artifacts"][0]["manifest"])


if __name__ == "__main__":
    unittest.main()
