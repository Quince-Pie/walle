import unittest
from fractions import Fraction

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_residue as residue
import liquid_glass_raster_threshold as threshold


class RasterResidueTests(unittest.TestCase):
    def test_integer_product_lattice_matches_fraction_model(self) -> None:
        for dimension, numerator in (
            (47, 24_576),
            (81, 21_184),
            (84, 49_152),
            (127, 63_360),
        ):
            floor_index, product_fraction = (
                residue.integer_product_lattice(
                    numerator,
                    dimension,
                )
            )
            expected = threshold.product_lattice(
                Fraction(numerator, 65_536),
                dimension,
            )
            self.assertEqual(floor_index, expected["floorIndex"])
            self.assertEqual(
                product_fraction,
                expected["fraction"],
            )

    def test_fast_binary32_rounding_matches_fraction_model(self) -> None:
        for product_exponent in range(-8, -4):
            step = raster.power_of_two(product_exponent - 26)
            for index in (
                (1 << 26) - 2,
                (1 << 26) - 1,
                (1 << 26),
                (1 << 26) + 3,
                (1 << 26) + 4,
                (1 << 26) + 5,
                (1 << 27) - 5,
                (1 << 27) - 1,
            ):
                self.assertEqual(
                    residue.round_product_index_to_float32_bits(
                        index,
                        product_exponent,
                    ),
                    raster.round_fraction_to_float32_bits(
                        index * step
                    ),
                )

    def test_offset_threshold_fit_retains_synthetic_rule(self) -> None:
        dimension = 47
        quotient_exponent = (
            residue.reciprocal_exponent(dimension) - 1
        )
        true_offset = -1
        true_threshold = Fraction(3, 8)
        samples = []
        for numerator in range(24_576, 49_152, 257):
            if not residue.ratio_has_binary_exponent(
                numerator,
                dimension,
                quotient_exponent,
            ):
                continue
            floor_index, product_fraction = (
                residue.integer_product_lattice(
                    numerator,
                    dimension,
                )
            )
            observed_bits = (
                residue.round_product_index_to_float32_bits(
                    floor_index
                    + true_offset
                    + (product_fraction >= true_threshold),
                    quotient_exponent,
                )
            )
            samples.append(
                {
                    "floorIndex": floor_index,
                    "productExponent": quotient_exponent,
                    "productFraction": residue.fraction_record(
                        product_fraction
                    ),
                    "observedBits": f"0x{observed_bits:08x}",
                }
            )
        candidates = residue.fit_offset_threshold(samples)
        matching = [
            candidate
            for candidate in candidates
            if candidate["latticeOffset"] == true_offset
        ]
        self.assertEqual(len(matching), 1)
        lower, upper = residue.candidate_interval(matching[0])
        self.assertLess(lower, true_threshold)
        self.assertGreaterEqual(upper, true_threshold)


if __name__ == "__main__":
    unittest.main()
