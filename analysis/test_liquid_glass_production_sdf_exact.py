#!/usr/bin/env python3
"""Tests for exact production-profile Liquid Glass SDF recovery."""

import unittest

import numpy as np

from liquid_glass_production_sdf_exact import (
    FIRST_LOWER_HALF_BITS,
    LAST_LOWER_HALF_BITS,
    LOWER_HALF_BITS,
    SOURCE_COUNT,
    STATE_COUNT,
    THRESHOLD_COUNT,
    _expected_states,
    decode_sdf_half_words,
)


class ExactSdfDesignTests(unittest.TestCase):
    def test_complete_occupied_half_range_is_enumerated(self) -> None:
        self.assertEqual(THRESHOLD_COUNT, 672)
        self.assertEqual(LOWER_HALF_BITS[0], FIRST_LOWER_HALF_BITS)
        self.assertEqual(LOWER_HALF_BITS[-1], LAST_LOWER_HALF_BITS)
        self.assertEqual(
            list(LOWER_HALF_BITS),
            list(range(0xE7DD, 0xE53D, -1)),
        )

    def test_state_indices_and_names_are_stable(self) -> None:
        states = _expected_states()
        self.assertEqual(len(states), STATE_COUNT)
        self.assertEqual(states[0]["name"], "production-exact-live-leading")
        self.assertEqual(
            states[4]["name"],
            "production-exact-threshold-lower-e7dd",
        )
        self.assertEqual(
            states[675]["name"],
            "production-exact-threshold-lower-e53e",
        )
        self.assertEqual(
            states[676]["name"],
            "production-exact-live-trailing",
        )


class ExactSdfDecodeTests(unittest.TestCase):
    def test_first_opacity_one_state_recovers_half_word(self) -> None:
        stream = np.zeros(
            (SOURCE_COUNT, STATE_COUNT, 1, 1, 1, 1),
            dtype=np.uint8,
        )
        stream[:, 0:2] = 200
        stream[:, 2:4] = 100
        transition_index = 321
        stream[:, 4:4 + transition_index] = 100
        stream[:, 4 + transition_index:676] = 200
        stream[:, 676] = 200
        diagnostics, recovered, valid = decode_sdf_half_words(stream)
        self.assertTrue(diagnostics["allSpatialSamplesRecovered"])
        self.assertTrue(valid.item())
        self.assertEqual(
            recovered.item(),
            LOWER_HALF_BITS[transition_index],
        )

    def test_conflicting_sources_fail_recovery(self) -> None:
        stream = np.zeros(
            (SOURCE_COUNT, STATE_COUNT, 1, 1, 1, 1),
            dtype=np.uint8,
        )
        stream[:, 0:2] = 200
        stream[:, 2:4] = 100
        stream[:, 4:300] = 100
        stream[:, 300:676] = 200
        stream[:, 676] = 200
        stream[1, 300] = 100
        diagnostics, _, valid = decode_sdf_half_words(stream)
        self.assertGreater(
            diagnostics[
                "spatialClassConflictsAcrossSourcesOrChannels"
            ],
            0,
        )
        self.assertFalse(valid.item())


if __name__ == "__main__":
    unittest.main()
