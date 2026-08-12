import unittest

import numpy as np

from liquid_glass_quarter_grid_fit import (
    LOW_RESOLUTION_SIGMAS,
    ProbeFeatures,
    cross_validate_candidate,
    probe_backgrounds,
    sample_region,
)


class LiquidGlassQuarterGridFitTests(unittest.TestCase):
    def test_probe_roles_are_paired_without_name_overlap(self) -> None:
        training = probe_backgrounds("train")
        holdout = probe_backgrounds("holdout")

        self.assertEqual(len(training), 4)
        self.assertEqual(set(training), set(holdout))
        self.assertTrue(all(name.endswith("-train") for name in training.values()))
        self.assertTrue(all(name.endswith("-holdout") for name in holdout.values()))
        self.assertTrue(set(training.values()).isdisjoint(holdout.values()))

    def test_sample_region_rejects_boundary_overlap(self) -> None:
        self.assertEqual(
            sample_region((2000, 3200), stride=13),
            (slice(512, 1488, 13), slice(512, 2688, 13)),
        )
        with self.assertRaises(ValueError):
            sample_region((1024, 3200), stride=13)

    def test_leave_one_probe_out_recovers_shared_quarter_mapping(self) -> None:
        groups = {}
        for index, offset in enumerate((-0.3, 0.2, 0.7)):
            raw = np.asarray(
                [
                    [32.0, 64.0, 96.0],
                    [96.0, 128.0, 160.0],
                    [160.0, 192.0, 224.0],
                    [224.0, 32.0, 128.0],
                ]
            )
            scale = raw + offset
            output = (
                4.0
                + scale
                @ np.asarray(
                    [
                        [0.8, 0.1, 0.0],
                        [0.0, 0.9, 0.1],
                        [0.1, 0.0, 0.8],
                    ]
                )
            )
            groups[str(index)] = ProbeFeatures(
                name=str(index),
                background=str(index),
                raw=raw,
                quarter_scales=[
                    scale.copy() for _ in LOW_RESOLUTION_SIGMAS
                ],
                outputs={"dark": output, "light": output},
            )

        report = cross_validate_candidate(
            groups,
            appearance="dark",
            scale_count=1,
            include_raw=False,
            degree=1,
            penalty=1e-12,
        )

        self.assertLess(report["pooledError"]["maximumAbsoluteCodes"], 1e-8)


if __name__ == "__main__":
    unittest.main()
