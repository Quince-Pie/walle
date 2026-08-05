import unittest

import numpy as np

from liquid_glass_clear_grid_fit import (
    FEATURE_MODELS,
    ProbeFeatures,
    cross_validate_candidate,
    feature_name,
    probe_backgrounds,
    sampled_radius_fraction,
    sample_region,
    symmetric_ring_masks,
)


class LiquidGlassClearGridFitTests(unittest.TestCase):
    def test_probe_roles_are_balanced_and_disjoint(self) -> None:
        training = probe_backgrounds("train")
        holdout = probe_backgrounds("holdout")

        self.assertEqual(len(training), 10)
        self.assertEqual(set(training), set(holdout))
        self.assertTrue(all(name.endswith("-train") for name in training.values()))
        self.assertTrue(all(name.endswith("-holdout") for name in holdout.values()))
        self.assertTrue(set(training.values()).isdisjoint(holdout.values()))

    def test_sample_region_excludes_the_giant_circle_boundary(self) -> None:
        self.assertEqual(
            sample_region((2000, 3200), stride=13),
            (slice(512, 1488, 13), slice(512, 2688, 13)),
        )
        with self.assertRaises(ValueError):
            sample_region((1024, 3200), stride=13)

    def test_fixed_feature_models_reference_known_grid_features(self) -> None:
        known = {
            feature_name(grid, sigma)
            for grid, sigmas in (
                ("half", (0.0, 0.5, 1.0, 2.0)),
                ("quarter", (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 8.0)),
                ("eighth", (0.0, 0.5, 1.0, 2.0)),
            )
            for sigma in sigmas
        }

        self.assertTrue(FEATURE_MODELS)
        self.assertTrue(
            all(set(features) <= known for features in FEATURE_MODELS.values())
        )

    def test_radius_fraction_uses_the_declared_circle_geometry(self) -> None:
        radius = sampled_radius_fraction(
            (11, 16),
            center_x=8.0,
            center_y=5.0,
            radius=10.0,
            stride=1,
            margin=5,
        )

        np.testing.assert_allclose(
            radius,
            np.asarray(
                [
                    np.hypot(5.0 - 8.0, 5.0 - 5.0) / 10.0,
                    np.hypot(6.0 - 8.0, 5.0 - 5.0) / 10.0,
                    np.hypot(7.0 - 8.0, 5.0 - 5.0) / 10.0,
                    np.hypot(8.0 - 8.0, 5.0 - 5.0) / 10.0,
                    np.hypot(9.0 - 8.0, 5.0 - 5.0) / 10.0,
                    np.hypot(10.0 - 8.0, 5.0 - 5.0) / 10.0,
                ]
            ),
        )

    def test_symmetric_ring_masks_are_normalized(self) -> None:
        masks = symmetric_ring_masks(3)

        self.assertEqual(masks[0][3, 3], 1.0)
        self.assertTrue(all(np.isclose(mask.sum(), 1.0) for mask in masks.values()))
        self.assertTrue(all(np.array_equal(mask, mask[::-1]) for mask in masks.values()))
        self.assertTrue(
            all(np.array_equal(mask, mask[:, ::-1]) for mask in masks.values())
        )

    def test_leave_one_probe_out_recovers_shared_linear_mapping(self) -> None:
        feature = feature_name("half", 0.0)
        groups = {}
        matrix = np.asarray(
            [
                [0.8, 0.1, 0.0],
                [0.0, 0.9, 0.1],
                [0.1, 0.0, 0.8],
            ]
        )
        for index, offset in enumerate((-0.3, 0.2, 0.7)):
            values = np.asarray(
                [
                    [32.0, 64.0, 96.0],
                    [96.0, 128.0, 160.0],
                    [160.0, 192.0, 224.0],
                    [224.0, 32.0, 128.0],
                ]
            ) + offset
            groups[str(index)] = ProbeFeatures(
                name=str(index),
                background=str(index),
                features={feature: values},
                output=4.0 + values @ matrix,
                radius_fraction=np.linspace(0.0, 1.0, values.shape[0]),
            )

        report = cross_validate_candidate(
            groups,
            feature_names=(feature,),
            degree=1,
            penalty=1e-12,
        )

        self.assertLess(
            report["pooled"]["continuous"]["maximumAbsoluteCodes"],
            1e-8,
        )


if __name__ == "__main__":
    unittest.main()
