import json
import unittest

import run_amd_exact_circle_reference_gate as gate


class AmdExactCircleReferenceGateTests(unittest.TestCase):
    def test_scope_cannot_be_misreported_as_production_parity(self) -> None:
        self.assertTrue(gate.SCOPE["amdCircleSpecializationRendered"])
        self.assertTrue(gate.SCOPE["independentlyGeneratedBackdropPyramid"])
        self.assertTrue(
            gate.SCOPE["independentlyGeneratedStaticProfilePayloads"]
        )
        self.assertFalse(gate.SCOPE["capturedPrivateProfilePayloads"])
        self.assertTrue(
            gate.SCOPE["independentlyGeneratedStaticPassGeometry"]
        )
        self.assertFalse(gate.SCOPE["capturedPassGeometry"])
        self.assertTrue(gate.SCOPE["independentlyGeneratedStaticWallpaper"])
        self.assertTrue(
            gate.SCOPE["independentlyGeneratedDestinationPrepass"]
        )
        self.assertFalse(gate.SCOPE["capturedDestinationPrepass"])
        self.assertTrue(
            gate.SCOPE["independentlyGeneratedFinalHighlightInputs"]
        )
        self.assertFalse(gate.SCOPE["capturedFinalHighlightInputs"])
        self.assertFalse(gate.SCOPE["productionWalleProcessRendered"])
        self.assertFalse(gate.SCOPE["productionWalleDisplayContextUsed"])
        self.assertFalse(gate.SCOPE["physicalRetinaOutput"])
        self.assertFalse(gate.SCOPE["formalLiquidGlassParity"])

    def test_recorded_result_is_exact_with_retained_inputs(self) -> None:
        result = json.loads(
            (
                gate.ROOT
                / "analysis/amd_exact_circle_reference_gate_result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["scope"], gate.SCOPE)
        self.assertEqual(result["totals"]["checkedBytes"], 16_777_216)
        self.assertEqual(result["totals"]["mismatchedBytes"], 0)
        self.assertTrue(result["gate"]["amdCircleReferenceExact"])
        self.assertFalse(result["gate"]["productionWalleParityEstablished"])

    def test_discrete_walle_gpu_result_is_independently_exact(self) -> None:
        result = json.loads(
            (
                gate.ROOT
                / "analysis/amd_exact_circle_reference_gate_rx9070_result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertIn("AMD Radeon RX 9070 XT", result["device"]["renderer"])
        self.assertEqual(result["totals"]["mismatchedBytes"], 0)
        self.assertTrue(result["gate"]["observedDeviceAdmitted"])
        self.assertFalse(result["gate"]["productionWalleParityEstablished"])


if __name__ == "__main__":
    unittest.main()
