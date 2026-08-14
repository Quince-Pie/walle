#!/usr/bin/env python3
"""Tests for the release-Walle eight-state dynamic process gate."""

import json
import unittest

import run_walle_process_dynamic_full_frame_gate as gate


class WalleProcessDynamicFullFrameGateTests(unittest.TestCase):
    def test_scope_is_dynamic_process_without_live_overclaim(self) -> None:
        self.assertTrue(gate.SCOPE["prospectiveEightStateDynamicInputs"])
        self.assertTrue(gate.SCOPE["releaseWalleExecutableRendered"])
        self.assertTrue(gate.SCOPE["walleLayerShellEglSurfaceRendered"])
        self.assertTrue(gate.SCOPE["bothFinalHighlightTopologiesRendered"])
        self.assertFalse(gate.SCOPE["capturedRenderInputs"])
        self.assertFalse(gate.SCOPE["ordinaryWallpaperTransitionModeRendered"])
        self.assertFalse(gate.SCOPE["continuousLiveTransitionStateRendered"])
        self.assertFalse(gate.SCOPE["formalLiquidGlassParity"])

    def test_recorded_release_process_matrix_is_exact(self) -> None:
        result = json.loads(
            (
                gate.ROOT
                / "analysis/walle_process_dynamic_full_frame_gate_result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(result["scope"], gate.SCOPE)
        self.assertEqual(len(result["runs"]), 8)
        self.assertEqual(
            {run["sampleIndex"] for run in result["runs"]},
            set(gate.SAMPLES),
        )
        self.assertEqual(
            {
                (run["highlightVertexCount"], run["highlightIndexCount"])
                for run in result["runs"]
            },
            {(4, 6), (16, 24)},
        )
        self.assertEqual(result["totals"]["offscreenCheckedBytes"], 33_554_432)
        self.assertEqual(result["totals"]["offscreenMismatchedBytes"], 0)
        self.assertEqual(result["totals"]["layerShellCheckedBytes"], 33_554_432)
        self.assertEqual(result["totals"]["layerShellMismatchedBytes"], 0)
        self.assertTrue(
            result["gate"]["walleReleaseProcessEightStateDynamicLayerShellExact"]
        )
        self.assertEqual(result["gate"]["remainingAppleAlgorithmUnknowns"], 0)
        self.assertFalse(result["gate"]["ordinaryWallpaperTransitionParityEstablished"])
        self.assertFalse(result["gate"]["formalLiquidGlassParityEstablished"])


if __name__ == "__main__":
    unittest.main()
