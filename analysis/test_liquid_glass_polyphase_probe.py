import unittest

import cv2
import numpy as np

from liquid_glass_polyphase_probe import (
    Coordinates,
    disk_coordinates,
    fit_phase_models,
    linear_interpolation_matrix,
    predict_phase_models,
    sampled_rectangle_coordinates,
)


class LiquidGlassPolyphaseProbeTests(unittest.TestCase):
    def test_interpolation_matrix_matches_half_pixel_linear_resize(self) -> None:
        generator = np.random.default_rng(91)
        source = generator.normal(size=(5, 7))
        vertical = linear_interpolation_matrix(5, 20)
        horizontal = linear_interpolation_matrix(7, 28)
        matrix_result = vertical @ source @ horizontal.T
        opencv_result = cv2.resize(
            source,
            (28, 20),
            interpolation=cv2.INTER_LINEAR,
        )

        np.testing.assert_allclose(matrix_result, opencv_result, atol=1e-12)

    def test_rectangle_sampling_is_unique_and_inside_the_margin(self) -> None:
        coordinates = sampled_rectangle_coordinates(
            (40, 60),
            margin=5,
            sample_count=300,
            seed=17,
        )

        self.assertEqual(coordinates.y.size, 300)
        self.assertEqual(
            np.unique(np.column_stack((coordinates.y, coordinates.x)), axis=0).shape[
                0
            ],
            300,
        )
        self.assertTrue(np.all((5 <= coordinates.y) & (coordinates.y < 35)))
        self.assertTrue(np.all((5 <= coordinates.x) & (coordinates.x < 55)))
        with self.assertRaises(ValueError):
            sampled_rectangle_coordinates(
                (10, 10),
                margin=5,
                sample_count=1,
                seed=17,
            )

    def test_disk_coordinates_are_centered_and_phase_partitioned(self) -> None:
        coordinates = disk_coordinates(center_x=11, center_y=13, radius=3)

        self.assertEqual(coordinates.y.size, 29)
        self.assertEqual(
            sum(
                coordinates.select_phase(2, phase_y, phase_x).y.size
                for phase_y in range(2)
                for phase_x in range(2)
            ),
            coordinates.y.size,
        )
        self.assertTrue(
            np.all(
                np.square(coordinates.x - 11)
                + np.square(coordinates.y - 13)
                <= 9
            )
        )

    def test_period_two_fit_generalizes_to_an_independent_field(self) -> None:
        generator = np.random.default_rng(0xC0FFEE)
        train_source = generator.normal(size=(32, 32, 3))
        holdout_source = generator.normal(size=(32, 32, 3))
        matrices = {
            (phase_y, phase_x): generator.normal(size=(3, 3)) * 2
            for phase_y in range(2)
            for phase_x in range(2)
        }
        intercepts = {
            phase: 128 + generator.normal(size=3)
            for phase in matrices
        }

        def render(source: np.ndarray) -> np.ndarray:
            output = np.empty_like(source)
            y, x = np.indices(source.shape[:2])
            for phase, matrix in matrices.items():
                selected = (y % 2 == phase[0]) & (x % 2 == phase[1])
                output[selected] = (
                    intercepts[phase] + source[selected] @ matrix
                )
            return output

        coordinates = Coordinates(
            y=np.indices((24, 24), dtype=np.int64)[0].reshape(-1) + 4,
            x=np.indices((24, 24), dtype=np.int64)[1].reshape(-1) + 4,
        )
        coefficients = fit_phase_models(
            train_source,
            render(train_source),
            coordinates,
            radius=0,
            phase_period=2,
            penalty=1e-12,
        )
        prediction = predict_phase_models(
            holdout_source,
            coordinates,
            coefficients,
            radius=0,
            phase_period=2,
        )
        expected = render(holdout_source)[coordinates.y, coordinates.x]

        np.testing.assert_allclose(prediction, expected, atol=1e-10)


if __name__ == "__main__":
    unittest.main()
