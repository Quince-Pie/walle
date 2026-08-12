import unittest

import numpy as np

from liquid_glass_v210_fit import (
    centered_correlations,
    derived_small_amplitude,
    local_polynomial_design,
    prediction_report,
    ridge_solve,
)


class LiquidGlassV210FitTests(unittest.TestCase):
    def test_small_amplitude_is_paired_with_large_amplitude(self) -> None:
        source64 = np.asarray([[64.0, 192.0], [192.0, 64.0]])
        expected = np.asarray([[112.0, 144.0], [144.0, 112.0]])
        np.testing.assert_array_equal(
            derived_small_amplitude(source64),
            expected,
        )

    def test_local_linear_design_recovers_rgb_affine_transform(self) -> None:
        inputs = np.asarray(
            [
                [128.0, 128.0, 128.0],
                [136.0, 128.0, 128.0],
                [128.0, 136.0, 128.0],
                [128.0, 128.0, 136.0],
            ]
        )
        design = local_polynomial_design(inputs, 1)
        coefficients = np.asarray(
            [
                [50.0, 60.0, 70.0],
                [1.0, 2.0, 3.0],
                [4.0, 5.0, 6.0],
                [7.0, 8.0, 9.0],
            ]
        )
        outputs = design @ coefficients
        fitted = ridge_solve(
            design.T @ design,
            design.T @ outputs,
            penalty=0.0,
        )
        np.testing.assert_allclose(fitted, coefficients)

    def test_prediction_report_detects_exact_rounded_pixels(self) -> None:
        actual = np.asarray([[10.0, 20.0, 30.0], [40.0, 50.0, 60.0]])
        predicted = actual + np.asarray([[0.49, -0.49, 0.1], [0.0, 0.0, 0.0]])
        report = prediction_report(actual, predicted)
        self.assertEqual(report["roundedExactPixelFraction"], 1.0)
        self.assertEqual(report["roundedError"]["maximumAbsoluteCodes"], 0.0)

    def test_zero_variance_correlation_is_unavailable(self) -> None:
        actual = np.ones((4, 3))
        predicted = np.ones((4, 3))
        self.assertEqual(
            centered_correlations(actual, predicted),
            [None, None, None],
        )


if __name__ == "__main__":
    unittest.main()
