import unittest

import numpy as np

from liquid_glass_pinned_sdf_scale import _curve_difference
from liquid_glass_sdf_scale import STATE_COUNT


class CurveDifferenceTests(unittest.TestCase):
    def test_reports_statewise_profile_intervention(self) -> None:
        shape = (1, STATE_COUNT, 1, 1, 1, 3)
        all_opacity = np.zeros(shape, dtype=np.uint8)
        pinned = all_opacity.copy()
        pinned[0, 17, 0, 0, 0, 1] = 3

        report = _curve_difference(pinned, all_opacity)

        self.assertEqual(report["changedStateCount"], 1)
        self.assertEqual(report["changedValues"], 1)
        self.assertEqual(report["maximumAbsoluteCodes"], 3)
        self.assertFalse(report["states"][17]["exact"])
        self.assertTrue(report["states"][-1]["exact"])

    def test_identical_curves_are_exact(self) -> None:
        values = np.zeros(
            (1, STATE_COUNT, 1, 1, 1, 3),
            dtype=np.uint8,
        )

        report = _curve_difference(values, values)

        self.assertTrue(report["exact"])
        self.assertEqual(
            report["exactStateIndices"],
            list(range(STATE_COUNT)),
        )


if __name__ == "__main__":
    unittest.main()
