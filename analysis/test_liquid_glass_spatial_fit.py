import unittest

import numpy as np

from liquid_glass_spatial_fit import (
    SpatialModel,
    fit_polynomial_transfer,
    from_working_space,
    isotonic_increasing,
    polynomial_exponents,
    predict_polynomial_transfer,
    srgb_to_linear,
    to_working_space,
)


class LiquidGlassSpatialFitTests(unittest.TestCase):
    def test_srgb_transfer_round_trips_every_code(self) -> None:
        codes = np.arange(256, dtype=np.float64)
        working = to_working_space(codes, "linear-srgb")
        reconstructed = from_working_space(working, "linear-srgb")
        np.testing.assert_allclose(reconstructed, codes, atol=1e-12)
        np.testing.assert_allclose(
            working,
            srgb_to_linear(codes / 255.0),
            atol=0,
        )

    def test_isotonic_projection_is_monotone(self) -> None:
        fitted = isotonic_increasing(
            np.asarray([0.0, 3.0, 2.0, 5.0, 4.0]),
        )
        np.testing.assert_allclose(fitted, [0.0, 2.5, 2.5, 4.5, 4.5])
        self.assertTrue(np.all(np.diff(fitted) >= 0))

    def test_gaussian_step_and_three_pixel_line_are_consistent(self) -> None:
        model = SpatialModel(
            pipeline="tone-after-spatial-filter",
            source_space="srgb-code",
            sigmas=np.asarray([3.0]),
            weights=np.asarray([1.0]),
            shift_pixels=0.0,
        )
        offsets = np.arange(-30, 31, dtype=np.float64)
        step = model.step_response(offsets)
        line = model.line_response(offsets)
        np.testing.assert_allclose(
            line[2:-1],
            step[3:] - step[:-3],
            atol=1e-15,
        )
        self.assertAlmostEqual(float(line.sum()), 3.0, places=12)

    def test_cubic_polynomial_recovers_known_rgb_mapping(self) -> None:
        generator = np.random.default_rng(7)
        inputs = generator.uniform(0, 255, size=(256, 3))
        normalized = inputs / 127.5 - 1.0
        outputs = np.column_stack(
            (
                80 + 20 * normalized[:, 0] + 7 * normalized[:, 1] ** 2,
                90 - 11 * normalized[:, 1] + 3 * normalized[:, 2] ** 3,
                100 + 5 * normalized[:, 0] * normalized[:, 2],
            )
        )
        exponents, coefficients = fit_polynomial_transfer(
            inputs,
            outputs,
            degree=3,
        )
        predicted = predict_polynomial_transfer(
            inputs,
            exponents,
            coefficients,
        )
        np.testing.assert_allclose(predicted, outputs, atol=1e-10)

    def test_polynomial_term_count_matches_three_variable_simplex(self) -> None:
        self.assertEqual(len(polynomial_exponents(1)), 4)
        self.assertEqual(len(polynomial_exponents(3)), 20)
        self.assertEqual(len(polynomial_exponents(7)), 120)


if __name__ == "__main__":
    unittest.main()
