#!/usr/bin/env python3
"""Tests for Apple float-intrinsic table packing."""

import unittest
from unittest.mock import patch

import numpy as np

import liquid_glass_float_intrinsics as intrinsics


class FloatIntrinsicPackingTests(unittest.TestCase):
    def test_lossless_packing(self) -> None:
        with patch.object(intrinsics, "MANTISSA_COUNT", 4):
            pair = np.asarray(
                [
                    [[-1, 0], [0, 1], [1, -1], [2, 0]],
                    [[0, 0], [1, 1], [-1, -1], [1, 0]],
                ],
                dtype=np.int8,
            )
            reciprocal = np.asarray([-1, 0, 1, 0], dtype=np.int8)
            packed, exceptions = intrinsics.pack_intrinsic_deltas(
                pair, reciprocal
            )
            self.assertEqual(exceptions, (2,))
            intrinsics.validate_packed_table(
                packed, pair, reciprocal, exceptions
            )

    def test_parity_selects_independent_sqrt_tables(self) -> None:
        with patch.object(intrinsics, "MANTISSA_COUNT", 4), patch.object(
            intrinsics, "MANTISSA_MASK", 3
        ):
            pair = np.zeros((2, 4, 2), dtype=np.int8)
            pair[0, :, 0] = 1
            pair[1, :, 0] = -1
            even = np.asarray([126 << 23], dtype=np.uint32)
            odd = np.asarray([127 << 23], dtype=np.uint32)
            even_baseline = np.sqrt(
                even.view(np.float32), dtype=np.float32
            ).view(np.uint32)
            odd_baseline = np.sqrt(
                odd.view(np.float32), dtype=np.float32
            ).view(np.uint32)
            self.assertEqual(
                int(intrinsics.predicted_fast_bits(even, pair, 0)[0]),
                int(even_baseline[0]) + 1,
            )
            self.assertEqual(
                int(intrinsics.predicted_fast_bits(odd, pair, 0)[0]),
                int(odd_baseline[0]) - 1,
            )


if __name__ == "__main__":
    unittest.main()
