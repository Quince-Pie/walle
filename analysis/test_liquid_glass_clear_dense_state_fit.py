import unittest

import numpy as np

from liquid_glass_clear_dense_state_fit import (
    Candidate,
    amplitude_fold,
    bilinear_ring_features_at_factor,
    candidates,
    grid_reduction,
    select_statistics,
    union_feature_names,
)
from liquid_glass_clear_state_fit import (
    NormalStatistics,
    SampleGrid,
    square_ring_offsets,
)


class ClearDenseStateFitTests(unittest.TestCase):
    def test_amplitude_folds_are_contiguous_and_complete(self) -> None:
        groups = {
            fold: [
                amplitude
                for amplitude in range(1, 65)
                if amplitude_fold(amplitude) == fold
            ]
            for fold in range(8)
        }

        self.assertEqual(groups[0], list(range(1, 8)))
        self.assertEqual(groups[1], list(range(8, 16)))
        self.assertEqual(groups[6], list(range(48, 56)))
        self.assertEqual(groups[7], list(range(56, 65)))
        with self.assertRaises(ValueError):
            amplitude_fold(0)
        with self.assertRaises(ValueError):
            amplitude_fold(65)

    def test_candidate_indices_are_unique_and_share_the_base(self) -> None:
        model_candidates = candidates(27)
        by_name = {candidate.name: candidate for candidate in model_candidates}

        self.assertEqual(len(model_candidates), 61)
        self.assertEqual(by_name["continuous"].feature_indices.size, 35)
        self.assertEqual(by_name["half-floor"].feature_indices.size, 62)
        self.assertEqual(by_name["quarter-floor"].feature_indices.size, 39)
        self.assertEqual(by_name["eighth-floor"].feature_indices.size, 39)
        self.assertEqual(by_name["half-half-up"].feature_indices.size, 62)
        self.assertEqual(by_name["half-half-even"].feature_indices.size, 62)
        self.assertEqual(
            by_name["quarter-and-eighth-floor"].feature_indices.size,
            43,
        )
        self.assertEqual(
            by_name[
                "half-and-quarter-and-eighth-floor"
            ].feature_indices.size,
            70,
        )
        self.assertEqual(
            by_name[
                "half-and-quarter-and-eighth-and-eighth-spatial-floor"
            ].feature_indices.size,
            97,
        )
        base = set(by_name["continuous"].feature_indices.tolist())
        for candidate in model_candidates:
            indices = candidate.feature_indices.tolist()
            self.assertEqual(len(indices), len(set(indices)))
            self.assertTrue(base <= set(indices))

    def test_union_feature_names_match_maximum_candidate_extent(self) -> None:
        names = union_feature_names(tuple(range(27)))
        model_candidates = candidates(27)

        self.assertEqual(len(names), 283)
        self.assertEqual(
            max(
                int(candidate.feature_indices.max())
                for candidate in model_candidates
            ),
            len(names) - 1,
        )

    def test_select_statistics_extracts_exact_normal_subsystem(self) -> None:
        design = np.asarray(
            (
                (1.0, 2.0, 3.0),
                (4.0, 5.0, 6.0),
                (7.0, 8.0, 9.0),
            )
        )
        target = np.asarray((2.0, 3.0, 5.0))
        source = NormalStatistics.empty(3)
        source.add(design, target)
        candidate = Candidate(
            "subset",
            np.asarray((0, 2), dtype=np.int64),
        )

        selected = select_statistics(source, candidate.feature_indices)

        np.testing.assert_array_equal(
            selected.xtx,
            design[:, (0, 2)].T @ design[:, (0, 2)],
        )
        np.testing.assert_array_equal(
            selected.xty,
            design[:, (0, 2)].T @ target,
        )
        self.assertEqual(selected.yty, source.yty)
        self.assertEqual(selected.observations, source.observations)

    def test_grid_reduction_and_reconstruction_preserve_constant(self) -> None:
        source = np.full((32, 32, 3), 17.75, dtype=np.float64)
        grid = SampleGrid(
            y=np.asarray((12, 19), dtype=np.int64),
            x=np.asarray((13, 18), dtype=np.int64),
        )
        rings = square_ring_offsets(0)

        continuous = grid_reduction(
            source,
            factor=8,
            mode="continuous",
        )
        floored = grid_reduction(source, factor=8, mode="floor")
        features = bilinear_ring_features_at_factor(
            continuous,
            grid,
            rings,
            factor=8,
        )

        np.testing.assert_allclose(continuous, 17.75)
        np.testing.assert_array_equal(floored, 17.0)
        np.testing.assert_allclose(features, 17.75)

    def test_reduced_grid_rejects_invalid_geometry(self) -> None:
        source = np.zeros((31, 32, 3), dtype=np.float64)
        with self.assertRaises(ValueError):
            grid_reduction(source, factor=8, mode="continuous")


if __name__ == "__main__":
    unittest.main()
