import unittest
from pathlib import Path

import numpy as np

import liquid_glass_raster_reciprocal_table as table


class RasterReciprocalTableTests(unittest.TestCase):
    def test_normalized_denominator_is_power_of_two_invariant(self):
        for denominator in (8_192, 8_193, 10_000, 16_383):
            self.assertEqual(
                table.normalized_denominator(denominator),
                denominator,
            )
            self.assertEqual(
                table.normalized_denominator(denominator * 2),
                denominator,
            )

    def test_combiner_requires_disjoint_complete_partitions(self):
        expected = set(
            range(
                table.NORMALIZED_DENOMINATOR_LOWER,
                table.NORMALIZED_DENOMINATOR_UPPER + 1,
            )
        )
        discovery_classes = {
            denominator: 20_000_000 + denominator
            for denominator in expected
            if denominator & 1
        }
        holdout_classes = {
            denominator: 20_000_000 + denominator
            for denominator in expected
            if not denominator & 1
        }
        discovery = table.Partition(
            role="discovery",
            report_path=Path("discovery.json"),
            table_path=Path("discovery.raw"),
            report_sha256="",
            table_sha256="",
            widths=(),
            selected_by_class=discovery_classes,
            coefficient_count=0,
            scale_equivalence_comparisons=0,
        )
        holdout = table.Partition(
            role="holdout",
            report_path=Path("holdout.json"),
            table_path=Path("holdout.raw"),
            report_sha256="",
            table_sha256="",
            widths=(),
            selected_by_class=holdout_classes,
            coefficient_count=0,
            scale_equivalence_comparisons=0,
        )

        combined = table.combine_partitions(discovery, holdout)

        self.assertEqual(combined.shape, (table.CANONICAL_CLASS_COUNT,))
        self.assertEqual(combined.dtype, np.dtype("<u4"))
        self.assertEqual(int(combined[0]), 20_008_192)
        self.assertEqual(int(combined[-1]), 20_016_383)


if __name__ == "__main__":
    unittest.main()
