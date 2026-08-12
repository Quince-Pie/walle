import unittest
from fractions import Fraction

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_refinement as refinement


class RasterRefinementTests(unittest.TestCase):
    def test_single_round_model_has_no_27_bit_double_round(self) -> None:
        delta = Fraction(57_152, 65_536)
        dimension = 58
        reciprocal = raster.quantize_binary_significand(
            Fraction(1, dimension),
            25,
        )
        expected = raster.round_fraction_to_float32_bits(delta * reciprocal)
        self.assertEqual(
            refinement.single_round_reciprocal_25_product(
                delta,
                dimension,
            ),
            expected,
        )

    def test_expected_case_records_preserve_residual_order(self) -> None:
        mismatches = [
            {
                "baseCase": "tomography-discovery-factor-h064-w047",
                "dimension": 47,
                "numeratorIndex": 74,
                "deltaNumerator": 42_304,
            },
        ]
        self.assertEqual(
            refinement.expected_case_records(mismatches),
            [
                {
                    "name": (
                        "numerator-refinement-discovery-factor-h064-w047-anchor-074"
                    ),
                    "baseCase": "tomography-discovery-factor-h064-w047",
                    "anchorNumeratorIndex": 74,
                    "deltaNumerators": list(range(42_301, 42_309)),
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
