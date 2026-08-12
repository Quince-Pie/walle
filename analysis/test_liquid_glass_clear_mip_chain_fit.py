import unittest

import numpy as np

from liquid_glass_clear_mip_chain_fit import (
    INITIAL_REDUCTION_MODE,
    candidates,
    reduce_mip_level,
    union_features,
    union_feature_names,
)
from liquid_glass_clear_state_fit import (
    SampleGrid,
    bilinear_ring_features,
    half_grid_reduction,
    square_ring_offsets,
)


class ClearMipChainFitTests(unittest.TestCase):
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

    def test_recursive_half_up_reduction_uses_top_left_even_extent(self) -> None:
        values = np.zeros((5, 7, 3), dtype=np.float64)
        values[:4, :6] = np.arange(24).reshape(4, 6, 1)

        reduced = reduce_mip_level(values, "half-up")

        self.assertEqual(reduced.shape, (2, 3, 3))
        np.testing.assert_array_equal(
            reduced[:, :, 0],
            np.asarray(
                (
                    (4.0, 6.0, 8.0),
                    (16.0, 18.0, 20.0),
                )
            ),
        )

    def test_candidate_inventory_covers_each_mode_and_depth(self) -> None:
        model_candidates = candidates(27)
        by_name = {candidate.name: candidate for candidate in model_candidates}

        self.assertEqual(len(model_candidates), 26)
        self.assertEqual(
            by_name["identified-half-only"].feature_indices.size,
            27,
        )
        self.assertEqual(
            by_name[
                "sequential-half-up-through-1x64"
            ].feature_indices.size,
            32,
        )

    def test_feature_inventory_matches_candidate_extent(self) -> None:
        names = union_feature_names(tuple(range(27)))
        model_candidates = candidates(27)

        self.assertEqual(len(names), 52)
        self.assertEqual(
            max(
                int(candidate.feature_indices.max())
                for candidate in model_candidates
            ),
            len(names) - 1,
        )


if __name__ == "__main__":
    unittest.main()
