#!/usr/bin/env python3

import unittest

import numpy as np

from liquid_glass_clear_compact_fit import (
    BlockCase,
    bilinear_half_grid,
    error_counts,
    feature_matrix,
    fit_model,
    point_output_terms,
    predict_case,
    within_state_coordinate,
)


class CompactFitTests(unittest.TestCase):
    def test_bilinear_half_grid_uses_texture_center_phase(self) -> None:
        image = np.arange(4 * 4 * 3, dtype=np.float64).reshape(4, 4, 3)
        sampled = bilinear_half_grid(
            image,
            np.asarray((1,), dtype=np.int64),
            np.asarray((1,), dtype=np.int64),
        )
        expected = (
            image[0, 0] * 0.75 * 0.75
            + image[0, 1] * 0.75 * 0.25
            + image[1, 0] * 0.25 * 0.75
            + image[1, 1] * 0.25 * 0.25
        )
        np.testing.assert_allclose(sampled[0], expected)

    def test_within_state_coordinate_centers_each_interval(self) -> None:
        coordinate = np.asarray((0.04, 0.9), dtype=np.float64)
        state = np.asarray((0, 12), dtype=np.int64)
        residual = within_state_coordinate(coordinate, state)
        self.assertAlmostEqual(float(residual[0]), 0.0)
        self.assertLess(abs(float(residual[1])), 0.5)

    def test_point_output_terms_reconstruct_mixed_input(self) -> None:
        blurred = np.asarray(((128.0, 128.0, 128.0),))
        sharp = np.asarray(((160.0, 96.0, 128.0),))
        base, slope = point_output_terms(blurred, sharp)
        mixed = base + 0.25 * slope
        direct, _ = point_output_terms(
            blurred + 0.25 * (sharp - blurred),
            blurred + 0.25 * (sharp - blurred),
        )
        np.testing.assert_allclose(mixed, direct)

    def test_state_linear_fit_recovers_synthetic_coefficients(self) -> None:
        state = np.asarray((0, 1, 2, 3), dtype=np.int64)
        coordinate = np.asarray((0.04, 0.12, 0.19, 0.26))
        slope = np.tile(
            np.asarray(((8.0, 10.0, 12.0),)),
            (state.size, 1),
        )
        base = np.full((state.size, 3), 100.25)
        expected = np.asarray((0.5, 0.125))
        alpha = expected[0] + expected[1] * state
        # fit_model targets the center of each integer output interval.
        actual = np.floor(base + slope * alpha[:, np.newaxis]).astype(
            np.int64
        )
        case = BlockCase(
            block_size=2,
            amplitude=16,
            coordinate=coordinate,
            state=state,
            within_state=np.zeros(state.size),
            base_output=base,
            sharp_slope=slope,
            actual=actual,
            baseline=np.full_like(actual, 100),
        )
        fitted = fit_model([case], "state-linear")
        predicted = predict_case(case, "state-linear", fitted)
        np.testing.assert_array_equal(predicted, actual)

    def test_feature_and_error_shapes(self) -> None:
        state = np.asarray((0, 12), dtype=np.int64)
        actual = np.asarray(((1, 2, 3), (4, 5, 6)), dtype=np.int64)
        case = BlockCase(
            block_size=2,
            amplitude=2,
            coordinate=np.asarray((0.04, 0.9)),
            state=state,
            within_state=np.asarray((0.0, 0.1)),
            base_output=actual.astype(np.float64),
            sharp_slope=np.zeros_like(actual, dtype=np.float64),
            actual=actual,
            baseline=np.zeros_like(actual),
        )
        self.assertEqual(feature_matrix(case, "state-lookup").shape, (2, 13))
        counts = error_counts(case, actual)
        self.assertEqual(counts.values, 6)
        self.assertEqual(counts.exact, 6)
        self.assertEqual(counts.maximum, 0)


if __name__ == "__main__":
    unittest.main()
