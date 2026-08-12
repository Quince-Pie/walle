import unittest

import numpy as np

from liquid_glass_clear_grid_basis import (
    CELL_AMPLITUDES,
    CELL_EFFECTIVE_AMPLITUDES,
    cell_background,
    difference_report,
    grid_background,
    relation_report,
    source_control_backgrounds,
)


class ClearGridBasisTests(unittest.TestCase):
    def test_catalog_names_and_control_count(self) -> None:
        self.assertEqual(
            grid_background(32, 1, 0),
            "noise-rgb-a032-grid2-shift-10-train",
        )
        self.assertEqual(
            cell_background(63, 0, 1),
            "noise-rgb-a063-cell2-basis-01-train",
        )
        self.assertEqual(CELL_AMPLITUDES, (1, 17, 32, 63, 64))
        self.assertEqual(
            CELL_EFFECTIVE_AMPLITUDES,
            {1: 0, 17: 4, 32: 8, 63: 16, 64: 16},
        )
        self.assertEqual(len(source_control_backgrounds()), 11)
        self.assertEqual(len(set(source_control_backgrounds())), 11)

    def test_catalog_rejects_uncaptured_shift_amplitude(self) -> None:
        with self.assertRaises(ValueError):
            grid_background(4, 1, 0)
        with self.assertRaises(ValueError):
            cell_background(16, 0, 0)

    def test_difference_report_counts_pixels_and_channels(self) -> None:
        left = np.asarray(
            [[10, 20, 30], [40, 50, 60], [70, 80, 90]],
            dtype=np.int64,
        )
        right = np.asarray(
            [[10, 21, 30], [40, 50, 60], [68, 80, 93]],
            dtype=np.int64,
        )

        report = difference_report(
            left,
            right,
            mask=np.asarray([True, False, True]),
        )

        self.assertEqual(report["pixels"], 2)
        self.assertEqual(report["channels"], 6)
        self.assertEqual(report["exactChannelFraction"], 0.5)
        self.assertEqual(report["changedPixelFraction"], 1.0)
        self.assertEqual(report["maximumAbsoluteCodes"], 3)
        self.assertEqual(
            report["signedDeltaCounts"],
            {"-3": 1, "-1": 1, "0": 3, "2": 1},
        )

    def test_relation_report_is_difference_from_zero(self) -> None:
        relation = np.asarray(
            [[0, 1, -1], [0, 0, 0]],
            dtype=np.int64,
        )

        report = relation_report(relation)

        self.assertEqual(report["exactChannelFraction"], 4 / 6)
        self.assertEqual(report["changedPixelFraction"], 0.5)
        self.assertEqual(report["maximumAbsoluteCodes"], 1)


if __name__ == "__main__":
    unittest.main()
