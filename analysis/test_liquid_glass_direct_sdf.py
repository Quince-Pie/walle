#!/usr/bin/env python3
"""Tests for the direct Apple SDF AIR replay."""

import unittest

import numpy as np

from liquid_glass_direct_sdf import (
    EXPECTED_INPUT_SIDE,
    brim_seeds,
    encode_unorm8_via_half,
    generate_field,
    jump_flood,
    jump_schedule,
    native_half_fma_blur,
    padded_alpha,
)


class DirectSdfReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        rgba = np.zeros(
            (EXPECTED_INPUT_SIDE, EXPECTED_INPUT_SIDE, 4),
            dtype=np.uint8,
        )
        rgba[48:208, 64:192] = 255
        self.alpha = padded_alpha(rgba, 64)

    def test_jump_schedule_is_power_of_two_descent(self) -> None:
        self.assertEqual(
            jump_schedule(64),
            (64, 32, 16, 8, 4, 2, 1),
        )
        self.assertEqual(jump_schedule(5), (4, 2, 1))

    def test_brim_is_the_inner_axial_rectangle_boundary(self) -> None:
        winner_x, winner_y = brim_seeds(self.alpha)
        boundary = winner_x != 0
        self.assertEqual(int(np.count_nonzero(boundary)), 572)
        self.assertEqual(
            (int(winner_x[112, 128]), int(winner_y[112, 128])),
            (128, 112),
        )
        self.assertFalse(boundary[113, 129])

    def test_jump_flood_preserves_the_x_zero_sentinel(self) -> None:
        winner_x, _ = jump_flood(
            self.alpha,
            64,
            cost_dtype=np.float16,
        )
        self.assertEqual(int(np.count_nonzero(winner_x == 0)), 768)
        self.assertTrue(np.all(winner_x[:, 0] == 0))
        self.assertTrue(np.all(winner_x[:, -1] == 0))
        self.assertTrue(np.all(winner_x[:, 1:-1] != 0))

    def test_field_mapping_uses_half_before_unorm8(self) -> None:
        winner_x, winner_y = jump_flood(
            self.alpha,
            64,
            cost_dtype=np.float32,
        )
        field = generate_field(
            self.alpha,
            winner_x,
            winner_y,
            zero_distance=-64,
            one_distance=16,
        )
        encoded = encode_unorm8_via_half(field)
        self.assertEqual(int(encoded[112, 192]), 202)
        self.assertEqual(int(encoded[111, 192]), 206)
        self.assertEqual(int(encoded[192, 192]), 2)
        self.assertEqual(int(encoded[0, 0]), 206)
        self.assertEqual(int(encoded[15, 0]), 253)
        self.assertEqual(int(encoded[16, 0]), 255)

    def test_binary16_best_cost_changes_the_winner_map(self) -> None:
        float_x, float_y = jump_flood(
            self.alpha,
            64,
            cost_dtype=np.float32,
        )
        half_x, half_y = jump_flood(
            self.alpha,
            64,
            cost_dtype=np.float16,
        )
        exact_pixels = (float_x == half_x) & (float_y == half_y)
        exact_components = np.stack(
            (float_x == half_x, float_y == half_y),
            axis=-1,
        )
        self.assertEqual(int(np.count_nonzero(exact_pixels)), 89_663)
        self.assertEqual(
            int(np.count_nonzero(exact_components)),
            237_038,
        )

    def test_native_blur_uses_the_recovered_half_fma_order(
        self,
    ) -> None:
        samples = np.array(
            [[
                0x3800,
                0x3800,
                0x3800,
                0x5944,
                0x3800,
                0x5939,
                0x3800,
                0x592D,
                0x3800,
                0x5921,
            ]],
            dtype=np.uint16,
        )
        native = native_half_fma_blur(samples)
        sequential = native_half_fma_blur(
            samples,
            order=(0, 1, 2, 3, 4),
        )
        self.assertEqual(int(native[0]), 0x527C)
        self.assertEqual(int(sequential[0]), 0x527B)


if __name__ == "__main__":
    unittest.main()
