#!/usr/bin/env python3
"""Tests for the recovered GLSL end-to-end gate."""

from pathlib import Path
import unittest

from liquid_glass_glsl_end_to_end_gate import analytic_coordinate_mode


class LiquidGlassGlslEndToEndGateTests(unittest.TestCase):
    def test_coordinate_mode_uses_measured_source_slope(self) -> None:
        self.assertEqual(
            analytic_coordinate_mode(Path("ambiguous"), 0x3A92_4924),
            1,
        )
        self.assertEqual(
            analytic_coordinate_mode(Path("ambiguous"), 0x3A2A_AAAB),
            2,
        )

    def test_coordinate_mode_falls_back_to_capture_profile(self) -> None:
        self.assertEqual(
            analytic_coordinate_mode(Path("capture-clear-light"), None),
            1,
        )
        self.assertEqual(
            analytic_coordinate_mode(Path("capture-regular-dark"), None),
            2,
        )

    def test_coordinate_mode_rejects_unidentified_geometry(self) -> None:
        with self.assertRaisesRegex(ValueError, "not identifiable"):
            analytic_coordinate_mode(Path("capture-unknown"), None)


if __name__ == "__main__":
    unittest.main()
