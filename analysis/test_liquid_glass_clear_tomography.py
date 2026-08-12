import unittest

import numpy as np

from liquid_glass_clear_tomography import (
    affine_slope_intervals,
    contributing_odd_half_cells,
    endpoint_scaling_error,
    merge_error_records,
    training_background,
)


class ClearTomographyTests(unittest.TestCase):
    def test_half_grid_categories_count_all_four_bilinear_cells(self) -> None:
        half_odd = np.asarray(
            (
                (0, 1, 0),
                (1, 1, 0),
                (0, 0, 0),
            ),
            dtype=np.uint8,
        )
        source_signs = np.zeros((6, 6, 3), dtype=np.bool_)
        for y in range(3):
            for x in range(3):
                if half_odd[y, x]:
                    source_signs[2 * y, 2 * x] = True

        categories = contributing_odd_half_cells(
            source_signs,
            y_coordinates=np.asarray((2,), dtype=np.int64),
            x_coordinates=np.asarray((2,), dtype=np.int64),
        )

        self.assertEqual(categories.shape, (1, 1, 3))
        self.assertEqual(categories[0, 0].tolist(), [3, 3, 3])

    def test_training_backgrounds_cannot_name_holdouts(self) -> None:
        self.assertEqual(
            training_background(0, 17),
            "noise-rgb-a017-tomography-train-00",
        )
        self.assertEqual(
            training_background(3, 64),
            "noise-rgb-a064-kernel-train-03",
        )
        with self.assertRaises(ValueError):
            training_background(4, 17)
        with self.assertRaises(ValueError):
            training_background(0, 16)

    def test_affine_intervals_accept_a_rounded_linear_sequence(self) -> None:
        amplitudes = np.asarray((0, 17, 31, 47, 64), dtype=np.float64)
        slopes = np.asarray((0.125, -0.3125, 0.421875))
        outputs = np.rint(
            152.125 + amplitudes[:, np.newaxis] * slopes[np.newaxis, :]
        )

        lower, upper, feasible = affine_slope_intervals(outputs)

        self.assertTrue(np.all(feasible))
        self.assertTrue(np.all(lower <= slopes))
        self.assertTrue(np.all(slopes <= upper))

    def test_affine_intervals_reject_an_inconsistent_sequence(self) -> None:
        outputs = np.asarray(
            (
                (152.0,),
                (150.0,),
                (160.0,),
                (145.0,),
                (170.0,),
            )
        )

        _, _, feasible = affine_slope_intervals(outputs)

        self.assertFalse(feasible[0])

    def test_endpoint_error_and_merge_preserve_exact_counts(self) -> None:
        endpoint = np.asarray((160.0, 144.0, 152.0))
        actual = np.asarray((154.0, 150.0, 152.0))

        record = endpoint_scaling_error(
            actual,
            endpoint,
            amplitude=17,
        )
        merged = merge_error_records([record, record])

        self.assertEqual(merged["channels"], 6)
        self.assertEqual(merged["exactChannelFraction"], 1.0)
        self.assertEqual(merged["meanAbsoluteCodes"], 0.0)
        self.assertEqual(merged["maximumAbsoluteCodes"], 0.0)


if __name__ == "__main__":
    unittest.main()
