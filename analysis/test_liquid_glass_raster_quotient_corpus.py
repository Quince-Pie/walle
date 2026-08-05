import unittest
from fractions import Fraction

import numpy as np

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_quotient_corpus as corpus
import liquid_glass_raster_setup_model as setup


class RasterQuotientCorpusTests(unittest.TestCase):
    def test_position_map_is_exact(self):
        self.assertEqual(
            corpus.expected_positions(100),
            [
                {"primitive": 0, "tile": 0, "x": 31, "y": 82},
                {"primitive": 0, "tile": 1, "x": 63, "y": 82},
                {"primitive": 0, "tile": 2, "x": 95, "y": 82},
                {"primitive": 0, "tile": 3, "x": 116, "y": 82},
                {"primitive": 1, "tile": 0, "x": 17, "y": 19},
                {"primitive": 1, "tile": 1, "x": 32, "y": 19},
                {"primitive": 1, "tile": 2, "x": 64, "y": 19},
                {"primitive": 1, "tile": 3, "x": 96, "y": 19},
            ],
        )

    def test_vector_staged_model_matches_exact_model(self):
        for width in (32, 33, 47, 83, 100, 126):
            vector = corpus.reciprocal25_product27_bits(width)
            for numerator in (32_768, 32_769, 41_943, 65_534, 65_535):
                expected = setup.reciprocal_25_product_27(
                    Fraction(numerator, 65_536),
                    width,
                )
                self.assertEqual(
                    int(vector[numerator - 32_768]),
                    expected,
                )

    def test_product_endpoints_enclose_nearest_even_product(self):
        exponent, index = corpus.reciprocal25_index(100)
        self.assertEqual(exponent, -7)
        floor, ceil, _shifts, _products = corpus.product27_endpoint_bits(100, index)
        rounded = corpus.reciprocal25_product27_bits(100)
        self.assertTrue(np.all((rounded == floor) | (rounded == ceil)))

    def test_reciprocal_envelope_recovers_nonstandard_index(self):
        observed = corpus.reciprocal25_product27_bits(
            100,
            reciprocal_offset=1,
        )
        recovered = corpus.recover_reciprocal_envelope(
            100,
            observed,
        )
        self.assertEqual(recovered["nearestEvenOffset"], 1)
        self.assertTrue(recovered["unique"])

    def test_scaled_normalized_lookup_preserves_significand(self):
        table = np.array(
            [[raster.float32_bits(0.25)]],
            dtype="<u4",
        )
        self.assertEqual(
            corpus.scaled_normalized_bits(table, 0, 16_384),
            raster.float32_bits(0.125),
        )


if __name__ == "__main__":
    unittest.main()
