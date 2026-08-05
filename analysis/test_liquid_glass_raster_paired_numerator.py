#!/usr/bin/env python3

import unittest
from collections import Counter

import liquid_glass_raster_paired_numerator as paired


class RasterPairedNumeratorTests(unittest.TestCase):
    def test_counter_report_sorts_signed_keys(self) -> None:
        self.assertEqual(
            paired.counter_report(Counter({1: 2, -1: 3, 0: 5})),
            {"-1": 3, "0": 5, "1": 2},
        )

    def test_keyed_samples_selects_one_complete_axis(self) -> None:
        records = [
            {
                "numeratorIndex": index,
                "axis": axis,
            }
            for index in range(256)
            for axis in ("x", "y")
        ]
        selected = paired.keyed_samples(records, axis="x")
        self.assertEqual(len(selected), 256)
        self.assertTrue(
            all(record["axis"] == "x" for record in selected.values())
        )

    def test_keyed_samples_rejects_an_incomplete_axis(self) -> None:
        with self.assertRaises(ValueError):
            paired.keyed_samples(
                [{"numeratorIndex": 0, "axis": "x"}],
                axis="x",
            )

    def test_aggregate_axis_reports_sums_distributions(self) -> None:
        report = paired.aggregate_axis_reports([
            {
                "pairCount": 1,
                "sampleCount": 3,
                "dividerFloatUlpErrorDistribution": {
                    "-1": 1,
                    "0": 2,
                },
                "factorizationFloatUlpShiftDistribution": {
                    "0": 2,
                    "1": 1,
                },
                "finalFloatUlpErrorDistribution": {
                    "0": 3,
                },
                "cancelledDividerErrors": 1,
                "introducedErrors": 0,
            },
            {
                "pairCount": 1,
                "sampleCount": 2,
                "dividerFloatUlpErrorDistribution": {
                    "0": 1,
                    "1": 1,
                },
                "factorizationFloatUlpShiftDistribution": {
                    "-1": 1,
                    "0": 1,
                },
                "finalFloatUlpErrorDistribution": {
                    "0": 1,
                    "1": 1,
                },
                "cancelledDividerErrors": 1,
                "introducedErrors": 1,
            },
        ])
        self.assertEqual(report["sampleCount"], 5)
        self.assertEqual(report["dividerExactCount"], 3)
        self.assertEqual(report["finalExactCount"], 4)
        self.assertEqual(report["cancelledDividerErrors"], 2)


if __name__ == "__main__":
    unittest.main()
