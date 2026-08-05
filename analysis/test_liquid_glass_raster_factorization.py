#!/usr/bin/env python3

import unittest

import liquid_glass_raster_factorization as factorization


class RasterFactorizationTests(unittest.TestCase):
    def test_scale_positive_normal_bits_changes_only_exponent(self) -> None:
        self.assertEqual(
            factorization.scale_positive_normal_bits(
                0x3F123456,
                1,
            ),
            0x3F923456,
        )
        self.assertEqual(
            factorization.scale_positive_normal_bits(
                0x3F123456,
                -2,
            ),
            0x3E123456,
        )

    def test_scale_positive_normal_bits_rejects_invalid_values(
        self,
    ) -> None:
        for bits in (0, 0x80000000, 0x7F800000):
            with self.subTest(bits=bits):
                with self.assertRaises(ValueError):
                    factorization.scale_positive_normal_bits(bits, 1)

    def test_compare_scaled_maps_reports_exact_and_mismatch(
        self,
    ) -> None:
        right = {
            (0, "x"): 0x3F000000,
            (0, "y"): 0x3E800000,
        }
        exact = factorization.compare_scaled_maps(
            {
                (0, "x"): 0x3F800000,
                (0, "y"): 0x3E000000,
            },
            right,
            {"x": 1, "y": -1},
        )
        self.assertTrue(exact["exact"])
        self.assertEqual(exact["exactCount"], 2)

        changed = factorization.compare_scaled_maps(
            {
                (0, "x"): 0x3F800001,
                (0, "y"): 0x3E000000,
            },
            right,
            {"x": 1, "y": -1},
        )
        self.assertFalse(changed["exact"])
        self.assertEqual(changed["mismatchCount"], 1)


if __name__ == "__main__":
    unittest.main()
