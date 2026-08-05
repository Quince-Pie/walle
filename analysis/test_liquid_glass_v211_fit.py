import unittest

import numpy as np

from liquid_glass_v211_fit import (
    GAUSSIAN_SIGMAS,
    ProbeSamples,
    adaptive_probe_pairs,
    cross_validate_candidate,
    sample_region,
)


class LiquidGlassV211FitTests(unittest.TestCase):
    def test_probe_pairs_keep_ten_training_holdout_interventions(self) -> None:
        pairs = adaptive_probe_pairs()

        self.assertEqual(len(pairs), 10)
        self.assertEqual(len({training for training, _ in pairs.values()}), 10)
        self.assertEqual(len({holdout for _, holdout in pairs.values()}), 10)
        self.assertTrue(
            all(training.endswith("-train") for training, _ in pairs.values())
        )
        self.assertTrue(
            all(holdout.endswith("-holdout") for _, holdout in pairs.values())
        )

    def test_sampling_strides_reject_invalid_or_empty_geometry(self) -> None:
        self.assertEqual(
            sample_region((2000, 3200), stride=13),
            (slice(512, 1488, 13), slice(512, 2688, 13)),
        )
        with self.assertRaises(ValueError):
            sample_region((1024, 3200), stride=13)
        with self.assertRaises(ValueError):
            sample_region((2000, 3200), stride=0)

    def test_leave_one_probe_out_recovers_shared_linear_mapping(self) -> None:
        groups = {}
        for index, offset in enumerate((-0.4, 0.2, 0.7)):
            raw = np.asarray(
                [
                    [32.0, 64.0, 96.0],
                    [96.0, 128.0, 160.0],
                    [160.0, 192.0, 224.0],
                ]
            )
            raw += offset
            output = np.clip(
                raw
                @ np.asarray(
                    [
                        [0.8, 0.1, 0.0],
                        [0.0, 0.9, 0.1],
                        [0.1, 0.0, 0.8],
                    ]
                )
                + 7.0,
                0.0,
                255.0,
            )
            groups[str(index)] = ProbeSamples(
                group=str(index),
                background=str(index),
                scale_inputs=[raw.copy() for _ in GAUSSIAN_SIGMAS],
                outputs={
                    "dark/clear": output,
                    "dark/regular": output,
                    "light/regular": output,
                },
            )

        report = cross_validate_candidate(
            groups,
            variant="dark/regular",
            degree=1,
            scale_count=1,
            penalty=1e-9,
        )

        self.assertLess(report["pooledError"]["maximumAbsoluteCodes"], 1e-6)


if __name__ == "__main__":
    unittest.main()
