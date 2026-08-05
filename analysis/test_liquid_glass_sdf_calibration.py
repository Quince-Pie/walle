#!/usr/bin/env python3
"""Tests for the Liquid Glass SDF-distance calibration analyzer."""

import unittest

import numpy as np

from liquid_glass_sdf_calibration import (
    CALIBRATION_STATES,
    STATE_COUNT,
    _expected_states,
    _float16_bits,
    _same_profile_analysis,
    _state_comparisons,
    _state_values,
)


class CalibrationDesignTests(unittest.TestCase):
    def test_catalog_has_controls_and_range_falsifiers(self) -> None:
        self.assertEqual(STATE_COUNT, 11)
        self.assertEqual(
            [state[0] for state in CALIBRATION_STATES],
            [
                "pinned-radius-zero",
                "pinned-radius-four",
                "collapsed-sentinel-threshold",
                "sentinel-lower-bracket",
                "sentinel-upper-bracket",
                "far-positive-threshold",
                "live-range-threshold",
                "raw-lower-threshold",
                "raw-upper-threshold",
                "normalized-full-threshold",
                "normalized-interior-threshold",
            ],
        )

    def test_filter_values_preserve_each_float32_input(self) -> None:
        for _, opacities, distances, _ in CALIBRATION_STATES:
            values = _state_values(opacities, distances)
            self.assertEqual(
                [values[f"inputBlurOpacity{index}"] for index in range(5)],
                list(opacities),
            )
            self.assertEqual(
                [values[f"inputBlurDistance{index}"] for index in range(5)],
                list(distances),
            )
            self.assertEqual(values["inputBlurRadius"], 4.0)

    def test_expected_state_names_and_indices_are_stable(self) -> None:
        states = _expected_states()
        self.assertEqual(
            [state["index"] for state in states],
            list(range(STATE_COUNT)),
        )
        self.assertEqual(
            states[0]["name"],
            "sdf-calibration-pinned-radius-zero",
        )
        self.assertEqual(
            states[-1]["name"],
            "sdf-calibration-normalized-interior-threshold",
        )

    def test_sentinel_breakpoints_are_adjacent_half_values(self) -> None:
        self.assertEqual(_float16_bits(-10_008.0), "f0e3")
        self.assertEqual(_float16_bits(-10_000.0), "f0e2")
        self.assertEqual(_float16_bits(-9_992.0), "f0e1")
        self.assertEqual(
            _float16_bits(-9_999.0),
            _float16_bits(-10_000.0),
        )


class CalibrationComparisonTests(unittest.TestCase):
    def test_states_are_compared_to_both_exact_endpoints(self) -> None:
        identity = np.zeros((STATE_COUNT, 1, 1, 1, 3), dtype=np.uint8)
        identity[1:] = 200
        identity[4] = 0
        identity[5] = 0
        comparisons = _state_comparisons(identity)
        self.assertTrue(comparisons[0]["vsPinnedRadiusZero"]["exact"])
        self.assertFalse(comparisons[0]["vsPinnedRadiusFour"]["exact"])
        self.assertTrue(comparisons[1]["vsPinnedRadiusFour"]["exact"])
        self.assertTrue(comparisons[2]["vsPinnedRadiusFour"]["exact"])
        self.assertTrue(comparisons[3]["vsPinnedRadiusFour"]["exact"])
        self.assertTrue(comparisons[4]["vsPinnedRadiusZero"]["exact"])
        self.assertTrue(comparisons[5]["vsPinnedRadiusZero"]["exact"])

    def test_same_profile_classes_use_lossless_array_equality(self) -> None:
        identity = np.zeros((STATE_COUNT, 2, 1, 1, 3), dtype=np.uint8)
        identity[3:5] = 200
        analysis = _same_profile_analysis(identity)
        self.assertEqual(
            analysis["exactResponseClasses"],
            [
                {
                    "representativeIndex": 2,
                    "representativeName":
                        "sdf-calibration-collapsed-sentinel-threshold",
                    "memberIndices": [2, 5, 6, 7, 8, 9, 10],
                    "memberNames": [
                        "sdf-calibration-collapsed-sentinel-threshold",
                        "sdf-calibration-far-positive-threshold",
                        "sdf-calibration-live-range-threshold",
                        "sdf-calibration-raw-lower-threshold",
                        "sdf-calibration-raw-upper-threshold",
                        "sdf-calibration-normalized-full-threshold",
                        "sdf-calibration-normalized-interior-threshold",
                    ],
                },
                {
                    "representativeIndex": 3,
                    "representativeName":
                        "sdf-calibration-sentinel-lower-bracket",
                    "memberIndices": [3, 4],
                    "memberNames": [
                        "sdf-calibration-sentinel-lower-bracket",
                        "sdf-calibration-sentinel-upper-bracket",
                    ],
                },
            ],
        )
        difference = analysis["twoClassDifference"]
        self.assertIsNotNone(difference)
        self.assertEqual(difference["changedPixels"], 2)
        self.assertEqual(difference["pixels"], 2)


if __name__ == "__main__":
    unittest.main()
