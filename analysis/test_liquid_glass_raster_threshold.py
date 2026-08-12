import unittest
from fractions import Fraction

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_threshold as threshold


class RasterThresholdTests(unittest.TestCase):
    def test_product_lattice_preserves_exact_fraction(self) -> None:
        lattice = threshold.product_lattice(
            Fraction(21_184, 65_536),
            81,
        )

        self.assertEqual(lattice["fraction"], Fraction(3, 8))
        self.assertEqual(
            lattice["ceilIndex"],
            lattice["floorIndex"] + 1,
        )

    def test_classification_distinguishes_visible_rounding(self) -> None:
        lattice = threshold.product_lattice(
            Fraction(21_184, 65_536),
            81,
        )

        self.assertEqual(
            threshold.threshold_classification(
                lattice["floorBits"],
                lattice,
            ),
            "floor",
        )
        self.assertEqual(
            threshold.threshold_classification(
                lattice["ceilBits"],
                lattice,
            ),
            "ceil",
        )

    def test_classification_reports_masked_adjacent_values(self) -> None:
        step = raster.power_of_two(-30)
        floor_index = 1 << 27
        lattice = {
            "floorBits": raster.round_fraction_to_float32_bits(floor_index * step),
            "ceilBits": raster.round_fraction_to_float32_bits((floor_index + 1) * step),
        }
        self.assertEqual(
            threshold.threshold_classification(
                lattice["floorBits"],
                lattice,
            ),
            "masked",
        )


if __name__ == "__main__":
    unittest.main()
