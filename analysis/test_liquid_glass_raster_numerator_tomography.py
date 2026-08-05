#!/usr/bin/env python3

import unittest
from fractions import Fraction

import liquid_glass_raster_numerator_tomography as numerator
import liquid_glass_raster_interpolant as raster


class RasterNumeratorTomographyTests(unittest.TestCase):
    def test_run_length_encode_is_lossless(self) -> None:
        errors = [0, 0, 1, 1, 1, 0, -1, -1]
        self.assertEqual(
            numerator.run_length_encode(errors),
            [
                {
                    "startIndex": 0,
                    "endIndexInclusive": 1,
                    "errorUlp": 0,
                },
                {
                    "startIndex": 2,
                    "endIndexInclusive": 4,
                    "errorUlp": 1,
                },
                {
                    "startIndex": 5,
                    "endIndexInclusive": 5,
                    "errorUlp": 0,
                },
                {
                    "startIndex": 6,
                    "endIndexInclusive": 7,
                    "errorUlp": -1,
                },
            ],
        )

    def test_run_length_encode_accepts_empty_input(self) -> None:
        self.assertEqual(numerator.run_length_encode([]), [])

    def test_significand_lattice_index_uses_nearest_even(self) -> None:
        self.assertEqual(
            numerator.significand_lattice_index(
                Fraction(129, 128),
                8,
            ),
            129,
        )
        self.assertEqual(
            numerator.significand_lattice_index(
                Fraction(257, 256),
                8,
            ),
            128,
        )

    def test_matching_lattice_offsets_reproduce_final_float(self) -> None:
        exact = Fraction(1, 7)
        observed_bits = raster.round_fraction_to_float32_bits(
            raster.quantize_binary_significand(
                exact,
                27,
                lattice_offset=2,
            )
        )
        offsets = numerator.matching_lattice_offsets(
            exact,
            observed_bits,
            precision_bits=27,
            radius=8,
        )
        self.assertIn(2, offsets)
        self.assertEqual(
            raster.round_fraction_to_float32_bits(
                raster.quantize_binary_significand(
                    exact,
                    27,
                    lattice_offset=offsets[0],
                )
            ),
            observed_bits,
        )

    def test_minimum_magnitude_offset_is_strict(self) -> None:
        self.assertEqual(
            numerator.minimum_magnitude_offset([-4, -3, -2, -1]),
            -1,
        )
        with self.assertRaises(ValueError):
            numerator.minimum_magnitude_offset([-1, 1])


if __name__ == "__main__":
    unittest.main()
