import unittest

import numpy as np

from liquid_glass_pair_sweep import (
    FEATURE_COUNT,
    GRID_SIDE,
    PAIR_PAGE_COUNT,
    categorical_additive_design,
    expected_pattern_names,
    exhaustive_pair,
    minimum_interval_fit,
    pattern_colors,
    quantized_prediction,
    unique_mapping,
)


class PairSweepTests(unittest.TestCase):
    def test_pattern_catalog_has_every_declared_page(self) -> None:
        names = expected_pattern_names()
        self.assertEqual(len(names), 7 * PAIR_PAGE_COUNT)
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(names[0], "pair-rg-b000-p00")
        self.assertEqual(names[-1], "latin-rgb-b-p63")

    def test_exhaustive_pair_pages_cover_every_pair(self) -> None:
        pairs = np.concatenate([
            np.column_stack([
                channel.reshape(-1)
                for channel in exhaustive_pair(page)
            ])
            for page in range(PAIR_PAGE_COUNT)
        ])
        self.assertEqual(pairs.shape, (65536, 2))
        self.assertEqual(np.unique(pairs, axis=0).shape, (65536, 2))

    def test_pattern_generators_have_declared_shapes(self) -> None:
        for name in (
            "pair-rg-b000-p00",
            "pair-rg-b128-p63",
            "pair-rb-g128-p03",
            "pair-gb-r128-p42",
            "latin-rgb-a-p37",
            "latin-rgb-b-p59",
        ):
            colors = pattern_colors(name)
            self.assertEqual(
                colors.shape,
                (GRID_SIDE, GRID_SIDE, 3),
            )
            self.assertTrue(np.all((colors >= 0) & (colors <= 255)))

    def test_categorical_design_has_at_most_four_nonzeros(self) -> None:
        inputs = np.asarray(
            (
                (128, 128, 128),
                (0, 128, 255),
                (1, 2, 3),
            ),
            dtype=np.int64,
        )
        design = categorical_additive_design(inputs)
        self.assertEqual(design.shape, (3, FEATURE_COUNT))
        self.assertEqual(design.getnnz(axis=1).tolist(), [1, 3, 4])

    def test_interval_fit_accepts_exact_floor_additive_data(self) -> None:
        inputs = np.asarray(
            (
                (0, 128, 128),
                (64, 128, 128),
                (128, 128, 128),
                (192, 128, 128),
                (255, 128, 128),
                (0, 0, 128),
                (255, 255, 128),
            ),
            dtype=np.int64,
        )
        features = categorical_additive_design(inputs)
        outputs = np.asarray((10, 70, 130, 190, 250, 5, 255))
        fit = minimum_interval_fit(features, outputs, "floor")
        self.assertLessEqual(fit.minimum_extra_half_width, 1e-8)
        predicted = quantized_prediction(
            np.asarray(features @ fit.coefficients),
            "floor",
        )
        np.testing.assert_array_equal(predicted, outputs)

    def test_mapping_keeps_generated_codes_before_control_aliases(
        self,
    ) -> None:
        generated = np.asarray(
            ((10, 20, 30), (11, 20, 30)),
            dtype=np.int64,
        )
        outputs = np.asarray(
            ((40, 50, 60), (41, 50, 60)),
            dtype=np.int64,
        )
        _, _, generated_report = unique_mapping(
            generated,
            outputs,
        )
        self.assertTrue(
            generated_report["maximumOutputsPerInputAtMostOne"]
        )

        captured_alias = np.asarray(
            ((10, 20, 30), (10, 20, 30)),
            dtype=np.int64,
        )
        _, _, alias_report = unique_mapping(
            captured_alias,
            outputs,
        )
        self.assertFalse(
            alias_report["maximumOutputsPerInputAtMostOne"]
        )


if __name__ == "__main__":
    unittest.main()
