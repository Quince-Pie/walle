import unittest

import numpy as np

from liquid_glass_clear_state_fit import (
    ColorNormalStatistics,
    NormalStatistics,
    SampleGrid,
    bilinear_ring_features,
    half_grid_reduction,
    color_residual_sum_squares,
    pyramid_feature_names,
    pyramid_features,
    residual_sum_squares,
    solve_color_coefficients,
    solve_coefficients,
    square_ring_offsets,
)


class ClearStateFitTests(unittest.TestCase):
    def test_half_grid_reduction_distinguishes_tie_modes(self) -> None:
        source = np.asarray(
            (
                ((119, 136, 120), (120, 137, 121)),
                ((119, 136, 120), (120, 137, 121)),
            ),
            dtype=np.float64,
        )

        self.assertEqual(
            half_grid_reduction(source, "continuous")[0, 0].tolist(),
            [119.5, 136.5, 120.5],
        )
        self.assertEqual(
            half_grid_reduction(source, "floor")[0, 0].tolist(),
            [119.0, 136.0, 120.0],
        )
        self.assertEqual(
            half_grid_reduction(source, "half-up")[0, 0].tolist(),
            [120.0, 137.0, 121.0],
        )
        self.assertEqual(
            half_grid_reduction(source, "half-even")[0, 0].tolist(),
            [120.0, 136.0, 120.0],
        )
        self.assertEqual(
            half_grid_reduction(source, "ceil")[0, 0].tolist(),
            [120.0, 137.0, 121.0],
        )

    def test_radius_zero_feature_uses_half_pixel_bilinear_weights(self) -> None:
        half = np.zeros((4, 4, 3), dtype=np.float64)
        half[:, :, 0] = np.arange(16, dtype=np.float64).reshape(4, 4)
        grid = SampleGrid(
            y=np.asarray((2,), dtype=np.int64),
            x=np.asarray((2,), dtype=np.int64),
        )

        features = bilinear_ring_features(
            half,
            grid,
            square_ring_offsets(0),
        )

        expected = (
            0.25 * 0.25 * half[0, 0]
            + 0.25 * 0.75 * half[0, 1]
            + 0.75 * 0.25 * half[1, 0]
            + 0.75 * 0.75 * half[1, 1]
        )
        np.testing.assert_allclose(features[0, 0], expected)

    def test_uniform_pyramid_features_are_centered(self) -> None:
        source = np.full((16, 16, 3), 128.0)
        grid = SampleGrid(
            y=np.asarray((4, 8), dtype=np.int64),
            x=np.asarray((4, 8), dtype=np.int64),
        )

        features = pyramid_features(source, grid, mode="continuous")

        self.assertEqual(
            features.shape,
            (2, len(pyramid_feature_names()), 3),
        )
        np.testing.assert_array_equal(features, 0.0)

    def test_pyramid_reduction_applies_the_selected_tie_rule(self) -> None:
        source = np.empty((16, 16, 3), dtype=np.float64)
        source[:, 0::2] = 119.0
        source[:, 1::2] = 120.0
        grid = SampleGrid(
            y=np.asarray((4,), dtype=np.int64),
            x=np.asarray((4,), dtype=np.int64),
        )

        continuous = pyramid_features(source, grid, mode="continuous")
        half_up = pyramid_features(source, grid, mode="half-up")

        np.testing.assert_allclose(continuous, -8.5, atol=2e-5)
        np.testing.assert_allclose(half_up, -8.0, atol=2e-5)

    def test_normal_statistics_recover_a_synthetic_filter(self) -> None:
        design = np.asarray(
            (
                (1.0, 0.0),
                (0.0, 1.0),
                (1.0, 1.0),
                (2.0, -1.0),
            )
        )
        expected = np.asarray((2.0, -0.5))
        target = design @ expected
        statistics = NormalStatistics.empty(2)
        statistics.add(design, target)

        actual = solve_coefficients(statistics, penalty=0.0)

        np.testing.assert_allclose(actual, expected)
        self.assertAlmostEqual(
            residual_sum_squares(statistics, actual),
            0.0,
            places=12,
        )

    def test_color_statistics_recover_a_synthetic_matrix(self) -> None:
        design = np.asarray(
            (
                (1.0, 0.0),
                (0.0, 1.0),
                (1.0, 1.0),
                (2.0, -1.0),
            )
        )
        expected = np.asarray(
            (
                (2.0, -0.5, 0.25),
                (0.5, 1.5, -1.0),
            )
        )
        target = design @ expected
        statistics = ColorNormalStatistics.empty(2)
        statistics.add(design, target)

        actual = solve_color_coefficients(statistics, penalty=0.0)

        np.testing.assert_allclose(actual, expected)
        self.assertAlmostEqual(
            color_residual_sum_squares(statistics, actual),
            0.0,
            places=12,
        )


if __name__ == "__main__":
    unittest.main()
