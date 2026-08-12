#!/usr/bin/env python3
"""Tests for the production-profile distance-only Liquid Glass oracle."""

import unittest

import numpy as np

from liquid_glass_production_distance import (
    FIRST_LOWER_HALF_BITS,
    LAST_LOWER_HALF_BITS,
    PATTERN_COUNT,
    STATE_COUNT,
    THRESHOLD_COUNT,
    _coarse_lower_half_bits,
    _expected_states,
    _threshold_diagnostics,
)


class ProductionDistanceDesignTests(unittest.TestCase):
    def test_coarse_half_catalog_is_ordered_and_frozen(self) -> None:
        bits = _coarse_lower_half_bits()
        self.assertEqual(len(bits), THRESHOLD_COUNT)
        self.assertEqual(bits[0], FIRST_LOWER_HALF_BITS)
        self.assertEqual(bits[-1], LAST_LOWER_HALF_BITS)
        self.assertEqual(len(set(bits)), THRESHOLD_COUNT)
        self.assertTrue(all(
            left > right
            for left, right in zip(bits, bits[1:])
        ))
        self.assertEqual(bits[:4], [0xF0E3, 0xF099, 0xF04E, 0xF004])
        self.assertEqual(bits[-4:], [0xDF21, 0xDED7, 0xDE8C, 0xDE41])

    def test_state_catalog_preserves_opacity_and_radius(self) -> None:
        states = _expected_states()
        self.assertEqual(len(states), STATE_COUNT)
        self.assertEqual(
            states[0]["name"],
            "production-live-leading",
        )
        self.assertEqual(
            states[5]["name"],
            "production-distance-threshold-lower-f0e3",
        )
        self.assertEqual(
            states[69]["name"],
            "production-distance-threshold-lower-de41",
        )
        self.assertEqual(
            states[70]["name"],
            "production-live-trailing",
        )
        self.assertTrue(all(
            state["blurOpacities"] == [1, 0.5, 0.5, 1, 1]
            and state["resourceBlurRadius"] == 1
            for state in states
        ))


class ProductionDistanceThresholdTests(unittest.TestCase):
    def test_exact_single_transition_is_accepted(self) -> None:
        stream = np.zeros(
            (PATTERN_COUNT, STATE_COUNT, 1, 1, 1, 1),
            dtype=np.uint8,
        )
        stream[:, 0] = 200
        stream[:, 1] = 200
        stream[:, 2] = 200
        stream[:, 3] = 100
        stream[:, 4] = 100
        stream[:, 5:35] = 100
        stream[:, 35:70] = 200
        stream[:, 70] = 200
        result = _threshold_diagnostics(stream)
        self.assertTrue(result["allThresholdValuesAreExactEndpoints"])
        self.assertTrue(result["allSpatialClassesConsistent"])
        self.assertTrue(result["allSpatialCurvesMonotonic"])
        self.assertTrue(
            result["allSpatialCurvesTransitionExactlyOnce"]
        )
        self.assertEqual(
            result["coarseTransitionIndexHistogram"],
            {"30": 1},
        )

    def test_intermediate_and_reverse_values_are_rejected(self) -> None:
        stream = np.zeros(
            (PATTERN_COUNT, STATE_COUNT, 1, 1, 1, 1),
            dtype=np.uint8,
        )
        stream[:, 0:3] = 200
        stream[:, 3:5] = 100
        stream[:, 5:20] = 100
        stream[:, 20:70] = 200
        stream[:, 70] = 200
        stream[1, 25] = 150
        stream[2, 30] = 100
        result = _threshold_diagnostics(stream)
        self.assertGreater(
            result["intermediateDiscriminatingValues"],
            0,
        )
        self.assertGreater(result["reverseValueTransitions"], 0)
        self.assertFalse(result["allSpatialClassesConsistent"])


if __name__ == "__main__":
    unittest.main()
