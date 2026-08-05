import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from liquid_glass_transition_decomposition import analyze


class TransitionDecompositionTests(unittest.TestCase):
    def test_recovers_scalar_blend_and_holds_out_reverse_direction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "dynamic").mkdir()

            def save(name: str, value: int) -> str:
                relative = f"dynamic/{name}.png"
                pixels = np.full((8, 8, 3), value, dtype=np.uint8)
                Image.fromarray(pixels, "RGB").save(root / relative)
                return relative

            def sequence(mode: str, incoming: int, glass: int) -> dict[str, object]:
                identifier = f"{mode}__clear__light"
                frames = []
                for index, (progress, alpha) in enumerate(
                    ((0.62, 1.0), (0.75, 0.5), (1.0, 0.0))
                ):
                    value = round(incoming + alpha * (glass - incoming))
                    frames.append({
                        "file": save(f"{identifier}-{index}", value),
                        "index": index,
                        "captureBackend":
                            "ScreenCaptureKit-SCStream-BGRA",
                        "actualSeconds": progress,
                        "presentationProgress": progress,
                    })
                return {
                    "id": identifier,
                    "mode": mode,
                    "overlay": "clear",
                    "appearance": "light",
                    "durationSeconds": 1,
                    "analysisExclusionPixels": [],
                    "frames": frames,
                    "tailFrames": [{
                        "file": save(f"{identifier}-tail", incoming),
                        "sample": 0,
                        "captureBackend": "CGWindowListCreateImage",
                        "actualSeconds": 1.125,
                        "presentationProgress": 1,
                        "tailProgress": 0.25,
                        "secondsAfterNominalEndpoint": 0.125,
                    }],
                    "postSettleFrame": {
                        "file": save(f"{identifier}-incoming", incoming)
                    },
                }

            manifest = {
                "ciCommit": "test",
                "osBuild": "test",
                "windowPoints": [8, 8],
                "backingScaleFactor": 1,
                "transitionOriginNormalized": [0.25, 0.30],
                "dynamicSequences": [
                    sequence("wallpaper-transition", 20, 220),
                    sequence("wallpaper-transition-reverse", 40, 200),
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            report = analyze(root)

        self.assertEqual(len(report["sequences"]), 2)
        training = next(
            sequence
            for sequence in report["sequences"]
            if sequence["partition"] == "training"
        )
        middle = next(
            sample for sample in training["samples"] if sample["index"] == 1
        )
        self.assertAlmostEqual(
            middle["projection"]["codeValue"]["alpha"],
            0.5,
            places=7,
        )
        self.assertEqual(
            middle["projection"]["codeValue"]["quantizedMeanAbsoluteCodes"],
            0,
        )
        self.assertAlmostEqual(
            report["holdout"]["aggregateMeanAbsoluteAlphaError"],
            0,
            places=7,
        )
        self.assertEqual(report["schemaVersion"], 2)
        convergence = training["endpointConvergence"]
        self.assertTrue(convergence["stableBitExactEndpointObserved"])
        self.assertEqual(convergence["stableBitExactSuffixSamples"], 2)
        self.assertEqual(
            convergence["captureBackendSampleCounts"],
            {
                "CGWindowListCreateImage": 1,
                "ScreenCaptureKit-SCStream-BGRA": 3,
            },
        )
        self.assertEqual(
            convergence["stableBitExactSuffixStart"]["actualSeconds"],
            1,
        )
        tail = next(
            sample
            for sample in training["samples"]
            if sample["phase"] == "tail"
        )
        self.assertEqual(tail["tailProgress"], 0.25)
        self.assertEqual(
            tail["captureBackend"],
            "CGWindowListCreateImage",
        )
        self.assertTrue(
            tail["endpointComparison"]["analysisRegionBitExact"]
        )


if __name__ == "__main__":
    unittest.main()
