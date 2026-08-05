import unittest

import numpy as np

from liquid_glass_sampler_probe import (
    _production_bilinear_code_sum,
    _production_grid_axis,
    half_linear_ties_up,
    half_round_ties_up,
    rgba8_unorm_linear_ties_up,
    rgba8_unorm_mip_ties_up,
    rgba8_unorm_exact_codes_ties_up,
)


class SamplerProbeTests(unittest.TestCase):
    def test_production_grid_recovers_all_level_zero_phases(
        self,
    ) -> None:
        coordinates_x = _production_grid_axis(origin=137)
        coordinates_y = _production_grid_axis(origin=193)
        texture = np.zeros((448, 448, 4), dtype=np.uint8)
        _, weight_x, weight_y = _production_bilinear_code_sum(
            texture,
            coordinates_x=coordinates_x,
            coordinates_y=coordinates_y,
        )
        expected = np.arange(256, dtype=np.uint64)
        np.testing.assert_array_equal(weight_x, expected)
        np.testing.assert_array_equal(weight_y, expected)

    def test_production_grid_constant_texture_is_invariant(
        self,
    ) -> None:
        coordinates_x = _production_grid_axis(origin=137)
        coordinates_y = _production_grid_axis(origin=193)
        codes = np.asarray((17, 91, 203, 255), dtype=np.uint8)
        texture = np.broadcast_to(
            codes,
            (448, 448, 4),
        ).copy()
        summed, _, _ = _production_bilinear_code_sum(
            texture,
            coordinates_x=coordinates_x,
            coordinates_y=coordinates_y,
        )
        expected = np.broadcast_to(
            codes.astype(np.uint64) * 65_536,
            summed.shape,
        )
        np.testing.assert_array_equal(summed, expected)

    def test_half_midpoint_rounds_up_instead_of_even(self) -> None:
        lower = np.asarray((0x2084,), dtype=np.uint16).view(np.float16)
        upper = np.nextafter(
            lower,
            np.float16(np.inf),
            dtype=np.float16,
        )
        midpoint = (
            lower.astype(np.float64)
            + upper.astype(np.float64)
        ) / 2
        self.assertEqual(
            int(midpoint.astype(np.float16).view(np.uint16)[0]),
            0x2084,
        )
        self.assertEqual(
            int(half_round_ties_up(midpoint).view(np.uint16)[0]),
            0x2085,
        )

    def test_measured_quarter_tie_example(self) -> None:
        inputs = (
            np.asarray((1, 6), dtype=np.float32)
            / np.float32(255)
        ).astype(np.float16)
        result = half_linear_ties_up(
            inputs[0:1],
            inputs[1:2],
            64,
        )
        self.assertEqual(
            int(result.view(np.uint16)[0]),
            0x2085,
        )

    def test_measured_radius_one_mip_example(self) -> None:
        inputs = (
            np.asarray((0, 1), dtype=np.float32)
            / np.float32(255)
        ).astype(np.float16)
        result = half_linear_ties_up(
            inputs[0:1],
            inputs[1:2],
            148,
        )
        self.assertEqual(
            int(result.view(np.uint16)[0]),
            0x18A5,
        )

    def test_rgba8_unorm_filters_in_sixteenth_codes(self) -> None:
        result = rgba8_unorm_linear_ties_up(
            np.asarray((128,), dtype=np.uint16),
            np.asarray((152,), dtype=np.uint16),
            144,
        )
        self.assertEqual(int(result.view(np.uint16)[0]), 0x3870)
        self.assertEqual(
            int(np.rint(result.astype(np.float32)[0] * 255)),
            141,
        )

    def test_rgba8_unorm_fixed_point_midpoint_rounds_up(self) -> None:
        result = rgba8_unorm_linear_ties_up(
            np.asarray((0,), dtype=np.uint16),
            np.asarray((1,), dtype=np.uint16),
            8,
        )
        self.assertEqual(int(result.view(np.uint16)[0]), 0x0C04)

    def test_rgba8_unorm_mip_lod_floors_to_one_sixty_fourth(
        self,
    ) -> None:
        level_zero = np.asarray((0,), dtype=np.uint16)
        level_one = np.asarray((255,), dtype=np.uint16)
        for numerator in range(4):
            result = rgba8_unorm_mip_ties_up(
                level_zero,
                level_one,
                numerator,
            )
            self.assertEqual(
                int(result.view(np.uint16)[0]),
                0,
            )
        at_one_sixty_fourth = rgba8_unorm_mip_ties_up(
            level_zero,
            level_one,
            4,
        )
        self.assertEqual(
            int(at_one_sixty_fourth.view(np.uint16)[0]),
            int(np.float16(4 / 255).view(np.uint16)),
        )

    def test_rgba8_unorm_mip_preserves_endpoint(self) -> None:
        endpoints = np.arange(256, dtype=np.uint16)
        result = rgba8_unorm_mip_ties_up(
            np.zeros(256, dtype=np.uint16),
            endpoints,
            256,
        )
        expected = (
            endpoints.astype(np.float32) / np.float32(255)
        ).astype(np.float16)
        np.testing.assert_array_equal(result, expected)

    def test_rgba8_unorm_trilinear_is_fused_before_rounding(
        self,
    ) -> None:
        level_zero_exact = np.asarray((254.0,))
        level_one_exact = np.asarray((23.984375,))
        fused = rgba8_unorm_exact_codes_ties_up(
            (
                108 * level_zero_exact
                + 148 * level_one_exact
            ) / 256
        )
        staged_level_one = (
            np.floor(level_one_exact * 16 + 0.5) / 16
        )
        staged = rgba8_unorm_exact_codes_ties_up(
            (
                108 * level_zero_exact
                + 148 * staged_level_one
            ) / 256
        )
        self.assertEqual(
            int(fused.view(np.uint16)[0]),
            0x3798,
        )
        self.assertEqual(
            int(staged.view(np.uint16)[0]),
            0x3799,
        )

    def test_linear_pair_direction_symmetry(self) -> None:
        inputs = (
            np.asarray((17, 203), dtype=np.float32)
            / np.float32(255)
        ).astype(np.float16)
        forward = half_linear_ties_up(
            inputs[0:1],
            inputs[1:2],
            73,
        )
        reverse = half_linear_ties_up(
            inputs[1:2],
            inputs[0:1],
            256 - 73,
        )
        np.testing.assert_array_equal(forward, reverse)


if __name__ == "__main__":
    unittest.main()
