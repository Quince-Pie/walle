import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from liquid_glass_pixel_gate import (
    CaptureArtifact,
    PROTECTED_ERROR_METRICS,
    compare_baseline,
    frame_metrics,
)


class LiquidGlassPixelGateTests(unittest.TestCase):
    def test_extracted_capture_directory_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.png"
            pixels = np.zeros((3, 4, 4), dtype=np.uint8)
            pixels[..., 3] = 255
            Image.fromarray(pixels, mode="RGBA").save(reference)
            manifest = {
                "references": [
                    {
                        "background": "source",
                        "file": reference.name,
                    },
                ],
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            artifact = CaptureArtifact.open(root)
            try:
                self.assertIsNone(artifact.archive)
                self.assertIsNone(artifact.sha256)
                np.testing.assert_array_equal(
                    artifact.reference("source"),
                    pixels,
                )
            finally:
                artifact.close()

    def test_identical_frame_has_zero_error(self) -> None:
        image = np.zeros((24, 32, 4), dtype=np.uint8)
        image[..., 3] = 255
        measured = frame_metrics(
            apple=image,
            rendered=image,
            outgoing=image,
            exclusions=[],
        )
        self.assertEqual(measured["full"]["maximumAbsoluteCodes"], 0)
        self.assertEqual(measured["perceptual"]["oneMinusSSIM"], 0)
        self.assertEqual(measured["perceptual"]["deltaE2000Maximum"], 0)
        self.assertEqual(measured["edgeWeightedMeanAbsoluteCodes"], 0)
        self.assertEqual(measured["perceptual"]["oneMinusSSIM"], 0)
        self.assertEqual(measured["perceptual"]["deltaE2000Maximum"], 0)

    def test_one_code_difference_is_detected(self) -> None:
        apple = np.zeros((24, 32, 4), dtype=np.uint8)
        apple[..., 3] = 255
        rendered = apple.copy()
        rendered[12, 16, 1] = 1
        measured = frame_metrics(
            apple=apple,
            rendered=rendered,
            outgoing=apple,
            exclusions=[],
        )
        self.assertEqual(measured["full"]["maximumAbsoluteCodes"], 1)
        self.assertGreater(measured["full"]["meanAbsoluteCodes"], 0)

    def test_excluded_difference_is_ignored(self) -> None:
        apple = np.zeros((24, 32, 4), dtype=np.uint8)
        apple[..., 3] = 255
        rendered = apple.copy()
        rendered[:4, :, :3] = 255
        measured = frame_metrics(
            apple=apple,
            rendered=rendered,
            outgoing=apple,
            exclusions=[{"x": 0, "y": 0, "width": 32, "height": 4}],
        )
        self.assertEqual(measured["full"]["maximumAbsoluteCodes"], 0)

    def test_baseline_rejects_a_single_metric_increase(self) -> None:
        protected = {metric: 1.0 for metric in PROTECTED_ERROR_METRICS}
        baseline = {
            "cases": {
                "case": {
                    "protectedMetrics": protected,
                }
            }
        }
        candidate_metrics = protected | {"full.meanAbsoluteCodes": 1.1}
        candidate = {
            "cases": {
                "case": {
                    "protectedMetrics": candidate_metrics,
                }
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "baseline.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            result = compare_baseline(candidate, path)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["regressions"]), 1)

    def test_baseline_rejects_a_different_metric_implementation(self) -> None:
        protected = {metric: 1.0 for metric in PROTECTED_ERROR_METRICS}
        baseline = {
            "implementation": {"sha256": "trusted"},
            "cases": {"case": {"protectedMetrics": protected}},
        }
        candidate = {
            "implementation": {"sha256": "changed"},
            "cases": {"case": {"protectedMetrics": protected}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "baseline.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            result = compare_baseline(candidate, path)
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["incompatibilities"]["implementation.sha256"],
            {"baseline": "trusted", "candidate": "changed"},
        )


if __name__ == "__main__":
    unittest.main()
