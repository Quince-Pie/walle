import json
import unittest
from pathlib import Path

import run_captured_input_reference_oracle as oracle


class CapturedInputReferenceOracleTests(unittest.TestCase):
    def test_scope_cannot_be_misreported_as_walle_parity(self) -> None:
        self.assertTrue(oracle.SCOPE["capturedInputReferenceRenderer"])
        self.assertFalse(oracle.SCOPE["productionWalleShaderRendered"])
        self.assertFalse(oracle.SCOPE["independentOpticalInputs"])
        self.assertFalse(oracle.SCOPE["physicalRetinaOutput"])
        self.assertFalse(oracle.SCOPE["formalLiquidGlassParity"])

    def test_reference_and_production_shaders_are_distinct(self) -> None:
        self.assertEqual(
            oracle.REFERENCE_SHADER,
            Path("analysis/apple_glass_reference.frag.glsl"),
        )
        self.assertEqual(oracle.PRODUCTION_SHADER, Path("shaders/frag.glsl"))
        self.assertNotEqual(oracle.REFERENCE_SHADER, oracle.PRODUCTION_SHADER)

    def test_protected_production_shader_is_metadata_only(self) -> None:
        provenance = oracle.validate_provenance()

        self.assertEqual(
            provenance["productionShader"]["sha256"],
            oracle.EXPECTED_PRODUCTION_SHADER_SHA256,
        )
        self.assertFalse(provenance["productionShader"]["renderedByThisGate"])

    def test_recorded_result_has_only_captured_input_authority(self) -> None:
        result = json.loads(
            (
                oracle.ROOT
                / "analysis/captured_input_reference_oracle_result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["totals"]["checkedBytes"], 16_777_216)
        self.assertEqual(result["totals"]["mismatchedBytes"], 0)
        self.assertTrue(result["gate"]["capturedInputReferenceExact"])
        self.assertFalse(result["gate"]["productionWalleParityEstablished"])
        self.assertEqual(result["scope"], oracle.SCOPE)


if __name__ == "__main__":
    unittest.main()
