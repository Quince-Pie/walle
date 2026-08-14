import json
import struct
import unittest

import run_current_compositor_gl_port_gate as gate


class CurrentCompositorGlPortGateTests(unittest.TestCase):
    def test_scope_does_not_overclaim_dynamic_or_retina_parity(self) -> None:
        self.assertTrue(gate.SCOPE["freshDirectRetinaMacCapture"])
        self.assertTrue(gate.SCOPE["capturedDestinationSeeds"])
        self.assertTrue(gate.SCOPE["capturedFinalHighlightAlphaFields"])
        self.assertTrue(gate.SCOPE["walleGlslPathRendered"])
        self.assertFalse(gate.SCOPE["independentlyConstructedDynamicAlphaFields"])
        self.assertFalse(gate.SCOPE["productionWalleProcessRendered"])
        self.assertFalse(gate.SCOPE["physicalRetinaOutput"])
        self.assertFalse(gate.SCOPE["formalLiquidGlassParity"])

    def test_uniform_payload_preserves_all_binary16_words(self) -> None:
        words = [f"0x{value:04x}" for value in range(24)]
        payload = gate.current_uniform_payload(words)

        self.assertEqual(len(payload), 248)
        self.assertEqual(struct.unpack_from("<24H", payload, 0x60), tuple(range(24)))
        self.assertEqual(payload[:0x60], bytes(0x60))
        self.assertEqual(payload[0x90:], bytes(248 - 0x90))

    def test_admitted_capture_has_complete_role_and_case_matrix(self) -> None:
        records = gate.load_records(gate.DEFAULT_CAPTURE)

        self.assertEqual(tuple(record["role"] for record in records), gate.ROLES)
        for record in records:
            self.assertEqual(
                tuple(case["name"] for case in record["cases"]),
                gate.CASES,
            )

    def test_recorded_gl_port_result_is_byte_exact(self) -> None:
        result = json.loads(
            (
                gate.ROOT / "analysis/current_compositor_gl_port_gate_result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["scope"], gate.SCOPE)
        self.assertEqual(len(result["runs"]), 28)
        self.assertEqual(result["totals"]["checkedBytes"], 117_440_512)
        self.assertEqual(result["totals"]["mismatchedBytes"], 0)
        self.assertEqual(result["totals"]["mismatchedPixels"], 0)
        self.assertTrue(result["gate"]["currentCompositorGlslPortExact"])
        self.assertFalse(result["gate"]["productionWalleParityEstablished"])


if __name__ == "__main__":
    unittest.main()
