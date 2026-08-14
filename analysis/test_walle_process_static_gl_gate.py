import json
import unittest

import run_walle_process_static_gl_gate as gate


class WalleProcessStaticGlGateTests(unittest.TestCase):
    def test_scope_closes_static_process_without_overclaiming_live(self) -> None:
        self.assertTrue(gate.SCOPE["releaseWalleExecutableRendered"])
        self.assertTrue(gate.SCOPE["productionWalleProcessRendered"])
        self.assertTrue(gate.SCOPE["walleLayerShellEglSurfaceRendered"])
        self.assertTrue(gate.SCOPE["exactStaticDiagnosticMode"])
        self.assertFalse(
            gate.SCOPE["ordinaryWallpaperTransitionModeRendered"]
        )
        self.assertFalse(gate.SCOPE["liveTransitionStateRendered"])
        self.assertFalse(gate.SCOPE["physicalRetinaOutput"])
        self.assertFalse(gate.SCOPE["formalLiquidGlassParity"])

    def test_recorded_release_process_matrix_is_exact(self) -> None:
        result = json.loads(
            (
                gate.ROOT
                / "analysis/walle_process_static_gl_gate_result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["scope"], gate.SCOPE)
        self.assertEqual(
            result["totals"]["offscreenCheckedBytes"], 16_777_216
        )
        self.assertEqual(
            result["totals"]["layerShellCheckedBytes"], 16_777_216
        )
        self.assertEqual(result["totals"]["layerShellMismatchedBytes"], 0)
        self.assertEqual(result["totals"]["layerShellMismatchedPixels"], 0)
        self.assertTrue(
            result["gate"]["walleReleaseProcessStaticLayerShellExact"]
        )
        self.assertFalse(
            result["gate"]["ordinaryWallpaperTransitionParityEstablished"]
        )
        self.assertFalse(
            result["gate"]["formalLiquidGlassParityEstablished"]
        )
        self.assertEqual(len(result["runs"]), 4)
        self.assertEqual(
            {run["fixture"] for run in result["runs"]},
            set(gate.FIXTURES),
        )


if __name__ == "__main__":
    unittest.main()
