import json
import unittest

import run_walle_owned_static_gl_gate as gate


class WalleOwnedStaticGlGateTests(unittest.TestCase):
    def test_scope_does_not_overclaim_production_or_retina_parity(self) -> None:
        self.assertTrue(
            gate.SCOPE["independentlyGeneratedCompleteStaticRenderInputs"]
        )
        self.assertFalse(gate.SCOPE["capturedRenderInputs"])
        self.assertTrue(gate.SCOPE["walleOwnedCGlRendererRendered"])
        self.assertFalse(gate.SCOPE["productionWalleProcessRendered"])
        self.assertFalse(gate.SCOPE["productionWalleWaylandSurfaceRendered"])
        self.assertFalse(gate.SCOPE["physicalRetinaOutput"])
        self.assertFalse(gate.SCOPE["formalLiquidGlassParity"])

    def test_recorded_matrix_is_exact_on_both_amd_devices(self) -> None:
        result = json.loads(
            (
                gate.ROOT
                / "analysis/walle_owned_static_gl_gate_result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["scope"], gate.SCOPE)
        self.assertEqual(result["totals"]["checkedBytes"], 33_554_432)
        self.assertEqual(result["totals"]["mismatchedBytes"], 0)
        self.assertEqual(result["totals"]["mismatchedPixels"], 0)
        self.assertTrue(result["gate"]["walleOwnedStaticGlExact"])
        self.assertFalse(
            result["gate"]["productionWalleParityEstablished"]
        )
        self.assertEqual(len(result["runs"]), 8)
        self.assertEqual(
            {run["deviceIndex"] for run in result["runs"]},
            {0, 1},
        )


if __name__ == "__main__":
    unittest.main()
