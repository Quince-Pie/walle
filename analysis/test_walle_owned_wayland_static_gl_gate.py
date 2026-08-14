import json
import unittest

import run_walle_owned_wayland_static_gl_gate as gate


class WalleOwnedWaylandStaticGlGateTests(unittest.TestCase):
    def test_scope_records_real_wayland_without_overclaiming_walle(self) -> None:
        self.assertTrue(
            gate.SCOPE["walleOwnedWaylandEglWindowSurfaceRendered"]
        )
        self.assertFalse(gate.SCOPE["productionWalleProcessRendered"])
        self.assertFalse(gate.SCOPE["productionWalleWaylandSurfaceRendered"])
        self.assertFalse(gate.SCOPE["physicalRetinaOutput"])
        self.assertFalse(gate.SCOPE["formalLiquidGlassParity"])

    def test_recorded_wayland_matrix_is_exact(self) -> None:
        result = json.loads(
            (
                gate.ROOT
                / "analysis/walle_owned_wayland_static_gl_gate_result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["scope"], gate.SCOPE)
        self.assertEqual(
            result["totals"]["offscreenCheckedBytes"], 16_777_216
        )
        self.assertEqual(
            result["totals"]["waylandCheckedBytes"], 16_777_216
        )
        self.assertEqual(result["totals"]["waylandMismatchedBytes"], 0)
        self.assertEqual(result["totals"]["waylandMismatchedPixels"], 0)
        self.assertTrue(
            result["gate"]["walleOwnedWaylandStaticGlExact"]
        )
        self.assertFalse(
            result["gate"]["productionWalleParityEstablished"]
        )
        self.assertEqual(len(result["runs"]), 4)
        self.assertEqual(
            {run["fixture"] for run in result["runs"]},
            set(gate.FIXTURES),
        )


if __name__ == "__main__":
    unittest.main()
