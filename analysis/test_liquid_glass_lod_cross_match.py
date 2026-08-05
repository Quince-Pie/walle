import unittest

import numpy as np

from liquid_glass_lod_cross_match import (
    SIGNATURE_BYTES,
    exact_catalog_candidates,
    exact_signature_words,
)


class ExactSignatureWordsTests(unittest.TestCase):
    def test_packing_is_lossless_across_both_words(self) -> None:
        signatures = np.zeros((4, SIGNATURE_BYTES), dtype=np.uint8)
        signatures[1, 0] = 1
        signatures[2, 8] = 1
        signatures[3, 14] = 1

        words = exact_signature_words(signatures)

        self.assertEqual(words.shape, (4, 2))
        self.assertEqual(len(np.unique(words, axis=0)), 4)

    def test_packing_rejects_truncated_signature(self) -> None:
        with self.assertRaisesRegex(ValueError, "layout"):
            exact_signature_words(
                np.zeros((2, SIGNATURE_BYTES - 1), dtype=np.uint8)
            )


class ExactOracleCandidateTests(unittest.TestCase):
    def test_reports_unique_ambiguous_noncontiguous_and_missing(self) -> None:
        oracle = np.asarray([
            [[10, 20], [1, 2]],
            [[11, 21], [3, 4]],
            [[12, 22], [1, 2]],
            [[13, 23], [5, 6]],
        ], dtype=np.uint64)
        default = np.asarray([
            [[11, 21], [1, 2]],
            [[99, 99], [5, 6]],
        ], dtype=np.uint64)

        bounds = exact_catalog_candidates(default, oracle)

        np.testing.assert_array_equal(
            bounds.count,
            np.asarray([[1, 2], [0, 1]], dtype=np.uint16),
        )
        np.testing.assert_array_equal(
            bounds.lower,
            np.asarray([[1, 0], [4, 3]], dtype=np.uint16),
        )
        np.testing.assert_array_equal(
            bounds.upper,
            np.asarray([[1, 2], [4, 3]], dtype=np.uint16),
        )
        np.testing.assert_array_equal(
            bounds.contiguous,
            np.asarray([[True, False], [False, True]]),
        )

    def test_rejects_spatial_catalog_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "shapes"):
            exact_catalog_candidates(
                np.zeros((2, 3, 2), dtype=np.uint64),
                np.zeros((4, 2, 2), dtype=np.uint64),
            )


if __name__ == "__main__":
    unittest.main()
