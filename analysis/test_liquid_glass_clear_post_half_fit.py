import unittest

import numpy as np

from liquid_glass_clear_post_half_fit import (
    INITIAL_REDUCTION_MODE,
    aligned_background,
    candidates,
    post_half_feature_names,
    reduce_half_grid,
    shifted_background,
    union_features,
    union_feature_names,
)
from liquid_glass_clear_state_fit import (
    SampleGrid,
    bilinear_ring_features,
    half_grid_reduction,
    square_ring_offsets,
)


class ClearPostHalfFitTests(unittest.TestCase):
    def test_identified_first_reduction_uses_measured_half_even_rule(
        self,
    ) -> None:
        self.assertEqual(INITIAL_REDUCTION_MODE, "half-even")
        source = np.full((128, 128, 3), 128.0)
        source[64:66, 64:66] = np.asarray(
            (
                ((128.0, 128.0, 128.0), (128.0, 128.0, 128.0)),
                ((128.0, 128.0, 128.0), (130.0, 130.0, 130.0)),
            )
        )
        grid = SampleGrid(
            y=np.asarray((64,), dtype=np.int64),
            x=np.asarray((64,), dtype=np.int64),
        )
        rings = square_ring_offsets(0)
        actual = union_features(source, grid=grid, rings=rings)[:, :1]
        half_even = half_grid_reduction(source, "half-even")
        expected = bilinear_ring_features(
            half_even - 128.0,
            grid,
            rings,
        )
        np.testing.assert_array_equal(actual, expected)
        half_up = half_grid_reduction(source, "half-up")
        rejected = bilinear_ring_features(
            half_up - 128.0,
            grid,
            rings,
        )
        self.assertFalse(np.array_equal(actual, rejected))

    def test_probe_names_are_exact(self) -> None:
        self.assertEqual(
            aligned_background(7),
            "noise-rgb-a007-grid2-shift-00-train",
        )
        self.assertEqual(
            shifted_background(31, "10"),
            "noise-rgb-a031-grid2-shift-10-train",
        )
        with self.assertRaises(ValueError):
            shifted_background(4, "10")

    def test_candidate_inventory_covers_each_post_half_quantizer(self) -> None:
        model_candidates = candidates(27)
        by_name = {candidate.name: candidate for candidate in model_candidates}

        self.assertEqual(len(model_candidates), 29)
        self.assertEqual(
            by_name["post-half-continuous"].feature_indices.size,
            35,
        )
        self.assertEqual(
            by_name[
                "quarter-and-eighth-and-eighth-spatial-floor"
            ].feature_indices.size,
            70,
        )
        self.assertEqual(
            by_name[
                "quarter-and-eighth-and-eighth-spatial-half-up"
            ].feature_indices.size,
            70,
        )

    def test_feature_inventory_matches_candidate_extent(self) -> None:
        names = union_feature_names(tuple(range(27)))
        model_candidates = candidates(27)

        self.assertEqual(len(post_half_feature_names()), 8)
        self.assertEqual(len(names), 175)
        self.assertEqual(
            max(
                int(candidate.feature_indices.max())
                for candidate in model_candidates
            ),
            len(names) - 1,
        )

    def test_post_half_reduction_preserves_a_constant(self) -> None:
        half_grid = np.full((16, 24, 3), 17.25)

        continuous = reduce_half_grid(
            half_grid,
            factor=4,
            mode="continuous",
        )
        rounded = reduce_half_grid(
            half_grid,
            factor=4,
            mode="half-up",
        )

        np.testing.assert_allclose(continuous, 17.25)
        np.testing.assert_array_equal(rounded, 17.0)


if __name__ == "__main__":
    unittest.main()
