import unittest

import numpy as np

from liquid_glass_filter_interventions import (
    CUBE_CODES,
    GRID_SIDE,
    INTERVENTIONS,
    numeric_value_matches,
    pattern_colors,
    unique_mapping,
)


class FilterInterventionTests(unittest.TestCase):
    def test_patterns_cover_gray_and_complete_cube(self) -> None:
        gray = pattern_colors("gray-256").reshape(-1, 3)
        np.testing.assert_array_equal(
            gray[:, 0],
            np.arange(256),
        )
        np.testing.assert_array_equal(gray[:, 0], gray[:, 1])
        np.testing.assert_array_equal(gray[:, 0], gray[:, 2])

        cube = np.concatenate((
            pattern_colors("cube-8-p0").reshape(-1, 3),
            pattern_colors("cube-8-p1").reshape(-1, 3),
        ))
        self.assertEqual(cube.shape, (512, 3))
        self.assertEqual(np.unique(cube, axis=0).shape, (512, 3))
        for channel in range(3):
            np.testing.assert_array_equal(
                np.unique(cube[:, channel]),
                CUBE_CODES,
            )

    def test_all_patterns_have_declared_grid_shape(self) -> None:
        for name in ("gray-256", "cube-8-p0", "cube-8-p1"):
            self.assertEqual(
                pattern_colors(name).shape,
                (GRID_SIDE, GRID_SIDE, 3),
            )

    def test_intervention_names_are_unique(self) -> None:
        names = [name for name, _ in INTERVENTIONS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names[0], "baseline")

    def test_numeric_match_preserves_bool_and_float32_bits(self) -> None:
        self.assertTrue(numeric_value_matches(True, True))
        self.assertFalse(numeric_value_matches(1, True))
        expected = np.float32(0.97)
        self.assertTrue(
            numeric_value_matches(float(expected), expected)
        )
        self.assertFalse(
            numeric_value_matches(
                float(np.nextafter(expected, np.float32(1))),
                expected,
            )
        )

    def test_unique_mapping_reports_conflicts(self) -> None:
        inputs = np.asarray(((1, 2, 3), (1, 2, 3), (4, 5, 6)))
        outputs = np.asarray(((7, 8, 9), (7, 8, 10), (1, 1, 1)))
        _, report = unique_mapping(inputs, outputs)
        self.assertEqual(report["distinctInputColors"], 2)
        self.assertEqual(report["conflictingInputColors"], 1)
        self.assertEqual(report["maximumOutputsPerInput"], 2)


if __name__ == "__main__":
    unittest.main()
