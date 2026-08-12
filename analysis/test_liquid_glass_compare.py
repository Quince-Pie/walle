import math
import tempfile
import unittest
from pathlib import Path

from liquid_glass_compare import (
    compare_baseline,
    current_body_code,
    current_refraction,
    parse_constants,
    smoothstep,
    GLSL_FLOAT,
)


class LiquidGlassComparatorTests(unittest.TestCase):
    def test_smoothstep_clamps_and_interpolates(self) -> None:
        self.assertEqual(smoothstep(0, 1, -1), 0)
        self.assertEqual(smoothstep(0, 1, 2), 1)
        self.assertEqual(smoothstep(0, 1, 0.5), 0.5)

    def test_clear_uniform_body_matches_current_shader_equations(self) -> None:
        constants = {
            "CLEAR_GAIN": 0.93,
            "CLEAR_LIFT": 0.012,
            "DIM_MAX": 0.35,
        }
        self.assertTrue(
            math.isclose(
                current_body_code(128, regular=False, constants=constants),
                120.80355110807609,
                rel_tol=0,
                abs_tol=1e-12,
            )
        )

    def test_refraction_is_zero_beyond_current_band(self) -> None:
        constants = {
            "LENS_WIDTH_FRAC": 0.10,
            "LENS_WIDTH_MIN": 22,
            "LENS_WIDTH_MAXDIAG": 0.035,
            "LENS_BEND_CLEAR": 0.90,
            "LENS_BEND_REGULAR": 0.55,
        }
        self.assertEqual(
            current_refraction(
                25,
                regular=False,
                radius=250,
                diagonal=math.hypot(3200, 2000),
                constants=constants,
            ),
            0,
        )

    def test_constant_parser_does_not_accept_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "probe.glsl"
            source.write_text(
                "const float A = 1.25;\n"
                "const float B = 1.0 / 2.0;\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_constants(source, GLSL_FLOAT), {"A": 1.25})

    def test_analytical_baseline_rejects_any_increase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "baseline.json"
            baseline.write_text(
                '{"protectedAnalyticalMetrics":{"a":1.0,"b":2.0}}',
                encoding="utf-8",
            )
            result = compare_baseline({"a": 1.0, "b": 2.1}, baseline)
            self.assertFalse(result["passed"])
            self.assertEqual(set(result["regressions"]), {"b"})


if __name__ == "__main__":
    unittest.main()
