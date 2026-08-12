import unittest

import numpy as np

from liquid_glass_point_sweep import (
    center_patch_value,
    collect_samples,
    difference_report,
)


class LiquidGlassPointSweepTest(unittest.TestCase):
    def test_center_patch_requires_uniform_value(self) -> None:
        image = np.zeros((64, 64, 4), dtype=np.uint8)
        image[..., :3] = (12, 34, 56)
        self.assertEqual(
            center_patch_value(image, 0, 0, 64),
            (12, 34, 56),
        )
        image[32, 32, 0] = 13
        with self.assertRaisesRegex(ValueError, "nonuniform"):
            center_patch_value(image, 0, 0, 64)

    def test_sample_collection_detects_conflicts(self) -> None:
        samples = collect_samples(
            [
                ((1, 2, 3), (4, 5, 6)),
                ((1, 2, 3), (4, 5, 7)),
                ((8, 9, 10), (11, 12, 13)),
            ]
        )
        self.assertEqual(samples.observations, 3)
        self.assertEqual(samples.inputs.shape, (2, 3))
        self.assertEqual(samples.conflicting_inputs, 1)
        self.assertEqual(samples.maximum_outputs_per_input, 2)

    def test_difference_report_ignores_alpha(self) -> None:
        left = np.zeros((2, 2, 4), dtype=np.int64)
        right = left.copy()
        right[0, 0, 3] = 255
        self.assertEqual(
            difference_report(left, right)["changedPixels"],
            0,
        )
        right[1, 1, 2] = 1
        report = difference_report(left, right)
        self.assertEqual(report["changedPixels"], 1)
        self.assertEqual(report["maximumChannelDelta"], 1)


if __name__ == "__main__":
    unittest.main()
