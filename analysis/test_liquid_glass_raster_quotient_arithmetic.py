import unittest

import numpy as np

import liquid_glass_raster_quotient_arithmetic as arithmetic


class RasterQuotientArithmeticTests(unittest.TestCase):
    def test_comparison_counts_matches_and_signed_ulp_errors(self):
        reference = np.array([10, 20, 30, 40], dtype="<u4")
        candidate = np.array([10, 19, 31, 38], dtype="<u4")

        self.assertEqual(
            arithmetic.comparison(reference, candidate),
            {
                "sampleCount": 4,
                "matchCount": 1,
                "mismatchCount": 3,
                "matchRate": 0.25,
                "referenceMinusCandidateFloatUlpDistribution": {
                    "-1": 1,
                    "0": 1,
                    "1": 1,
                    "2": 1,
                },
                "exact": False,
            },
        )

    def test_equivalence_classes_are_exact_and_stable(self):
        values = np.array(
            [
                [[1, 1, 2, 3], [4, 4, 5, 6]],
                [[7, 7, 8, 9], [10, 10, 11, 12]],
            ],
            dtype="<u4",
        )

        self.assertEqual(
            arithmetic.equivalence_classes(
                values,
                component_names=("a", "b", "c", "d"),
            ),
            [["a", "b"], ["c"], ["d"]],
        )

    def test_equivalence_classes_reject_wrong_component_count(self):
        values = np.zeros((2, 3), dtype="<u4")
        with self.assertRaisesRegex(ValueError, "component names"):
            arithmetic.equivalence_classes(
                values,
                component_names=("a", "b"),
            )

    def test_expected_arithmetic_size_is_120_mib(self):
        self.assertEqual(arithmetic.expected_arithmetic_bytes(), 120 * 1024**2)


if __name__ == "__main__":
    unittest.main()
