#!/usr/bin/env python3
"""Tests for exact clear-glass point-stage interval fitting."""

import unittest

import numpy as np

from liquid_glass_clear_point_stage import (
    categorical_additive_design,
    feature_matrix,
    minimum_interval_fit,
)


class PointStageTests(unittest.TestCase):
    def test_feature_families_have_declared_rank_width(self) -> None:
        inputs = np.asarray(
            ((0, 128, 255), (64, 192, 128)),
            dtype=np.int64,
        )
        self.assertEqual(feature_matrix(inputs, "linear").shape, (2, 4))
        self.assertEqual(
            feature_matrix(inputs, "diagonal-quadratic").shape,
            (2, 7),
        )
        self.assertEqual(
            feature_matrix(inputs, "full-quadratic").shape,
            (2, 10),
        )
        self.assertEqual(
            feature_matrix(inputs, "full-cubic").shape,
            (2, 20),
        )

    def test_categorical_design_uses_one_baseline_intercept(self) -> None:
        inputs = np.asarray(
            (
                (127, 128, 127),
                (128, 127, 129),
                (129, 129, 128),
            ),
            dtype=np.int64,
        )
        design = categorical_additive_design(inputs)
        self.assertEqual(design.features.shape, (3, 7))
        self.assertEqual(len(design.labels), 6)
        np.testing.assert_array_equal(
            design.features[:, -1],
            np.ones(3),
        )

    def test_interval_fit_accepts_exact_affine_codes(self) -> None:
        coordinates = np.arange(8, dtype=np.float64)
        features = np.column_stack(
            (coordinates, np.ones_like(coordinates))
        )
        outputs = np.floor(1.75 * coordinates + 12.25).astype(np.int64)
        for quantizer in ("floor", "nearest"):
            fit = minimum_interval_fit(
                features,
                outputs,
                quantizer,
            )
            self.assertLessEqual(
                fit.minimum_extra_half_width,
                1e-9,
            )

    def test_interval_fit_measures_non_affine_slack(self) -> None:
        coordinates = np.asarray(
            ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)),
        )
        features = np.column_stack(
            (coordinates, np.ones(coordinates.shape[0]))
        )
        outputs = np.asarray((0, 2, 2, 0), dtype=np.int64)
        fit = minimum_interval_fit(
            features,
            outputs,
            "nearest",
        )
        self.assertGreater(
            fit.minimum_extra_half_width,
            0.1,
        )


if __name__ == "__main__":
    unittest.main()
