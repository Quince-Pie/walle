#!/usr/bin/env python3

import unittest

import liquid_glass_raster_arithmetic as arithmetic


class RasterArithmeticTests(unittest.TestCase):
    def test_component_path_extracts_axis(self) -> None:
        self.assertEqual(
            arithmetic.component_path("fastDivideX"),
            ("fastDivide", "x"),
        )
        self.assertEqual(
            arithmetic.component_path("preciseAreaDivideY"),
            ("preciseAreaDivide", "y"),
        )

    def test_component_path_requires_axis(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "no axis suffix",
        ):
            arithmetic.component_path("fastDivide")


if __name__ == "__main__":
    unittest.main()
