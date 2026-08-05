#!/usr/bin/env python3
"""Tests for the fixed-production-resource Liquid Glass LOD oracle."""

import unittest

import numpy as np

from liquid_glass_production_kernel import (
    GRID_37_STATE,
    PATTERN_COUNT,
    SOURCE_DEFINITIONS,
    STATE_COUNT,
    TRAILING_PRODUCTION_STATE,
    _curve_diagnostics,
    _expected_states,
    _source_manifest,
    source_code,
)


class ProductionKernelDesignTests(unittest.TestCase):
    def test_sources_have_frozen_train_and_holdout_roles(self) -> None:
        self.assertEqual(PATTERN_COUNT, 6)
        self.assertEqual(
            [source["role"] for source in _source_manifest()],
            [
                "calibration",
                "train",
                "train",
                "train",
                "holdout",
                "holdout",
            ],
        )
        self.assertEqual(
            [source["seedHex"] for source in _source_manifest()],
            [
                None,
                "243f6a88",
                "85a308d3",
                "13198a2e",
                "03707344",
                "a4093822",
            ],
        )

    def test_state_catalog_brackets_the_complete_lod_grid(self) -> None:
        states = _expected_states()
        self.assertEqual(len(states), STATE_COUNT)
        self.assertEqual(
            states[0]["name"],
            "production-opacity-one-leading",
        )
        self.assertEqual(
            states[1]["name"],
            "production-resource-lod-bin-000",
        )
        self.assertEqual(
            states[GRID_37_STATE]["name"],
            "production-resource-lod-bin-037",
        )
        self.assertEqual(
            states[TRAILING_PRODUCTION_STATE]["name"],
            "production-opacity-one-trailing",
        )
        self.assertEqual(
            [state["targetLodNumerator"] for state in states[1:39]],
            list(range(38)),
        )
        self.assertEqual(
            states[0]["activeBlurOpacity0Float32Bits"],
            "3f800000",
        )
        self.assertEqual(
            states[-1]["activeBlurOpacity0Float32Bits"],
            "3f800000",
        )

    def test_hash_generator_has_stable_known_vectors(self) -> None:
        seed = SOURCE_DEFINITIONS[1][2]
        assert seed is not None
        values = source_code(
            seed=seed,
            x=np.asarray((0, 1, 63, 64), dtype=np.int64),
            y=np.asarray((0, 2, 63, 64), dtype=np.int64),
            channel=2,
        )
        self.assertEqual(values.tolist(), [147, 165, 116, 147])


class ProductionKernelCurveTests(unittest.TestCase):
    def test_curve_diagnostics_detect_direction_and_violation(self) -> None:
        stream = np.zeros(
            (PATTERN_COUNT, STATE_COUNT, 1, 1, 1, 1),
            dtype=np.uint8,
        )
        ascending = np.arange(38, dtype=np.uint8)
        stream[:, 1:39, 0, 0, 0, 0] = ascending
        diagnostics = _curve_diagnostics(stream)
        self.assertTrue(diagnostics["allMonotonic"])
        self.assertEqual(
            diagnostics["monotonicCurves"],
            PATTERN_COUNT,
        )

        stream[0, 20, 0, 0, 0, 0] = 255
        diagnostics = _curve_diagnostics(stream)
        self.assertFalse(diagnostics["allMonotonic"])
        self.assertEqual(
            diagnostics["monotonicCurves"],
            PATTERN_COUNT - 1,
        )


if __name__ == "__main__":
    unittest.main()
