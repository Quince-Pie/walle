import unittest

import numpy as np

import liquid_glass_raster_quotient_corpus as corpus


class RasterQuotientSelectorTests(unittest.TestCase):
    def test_truncated_partial_products_match_measured_non_nearest_cases(self):
        cases = (
            (33, 32_537_631, 33_798, 0x3C8005D1),
            (45, 23_860_930, 32_808, 0x3C364445),
            (100, 21_474_837, 38_209, 0x3BBF0B86),
            (101, 21_262_215, 32_775, 0x3BA240A3),
            (125, 17_179_869, 33_324, 0x3B854BC7),
        )
        for width, reciprocal, numerator, expected in cases:
            predicted, _discarded, _indices = corpus.truncated_radix2_product27_bits(
                width,
                reciprocal,
            )
            self.assertEqual(
                int(predicted[numerator - corpus.NUMERATOR_LOWER]),
                expected,
            )

    def test_partial_product_model_uses_exact_hardware_constants(self):
        self.assertEqual(corpus.PARTIAL_PRODUCT_TRUNCATION_BITS, 8)
        self.assertEqual(corpus.PARTIAL_PRODUCT_ROUNDING_BIAS, 0x1400)

    def test_full_mantissa_extrapolation_reduces_to_measured_model(self):
        width = 100
        reciprocal = 21_474_837
        numerators = np.arange(
            corpus.NUMERATOR_LOWER,
            corpus.NUMERATOR_UPPER + 1,
            dtype=np.uint64,
        )
        reduced = corpus.truncated_radix2_product27_bits(
            width,
            reciprocal,
        )[0]
        physical = corpus.truncated_radix2_product_bits(
            width,
            reciprocal,
            numerators << np.uint64(8),
            operand_precision_bits=24,
            partial_product_truncation_bits=16,
            rounding_bias=0x140000,
        )[0]
        np.testing.assert_array_equal(physical, reduced)


if __name__ == "__main__":
    unittest.main()
