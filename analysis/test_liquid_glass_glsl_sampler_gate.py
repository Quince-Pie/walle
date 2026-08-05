#!/usr/bin/env python3
"""Tests for the independent desktop GLSL Apple sampler gate."""

import unittest

import numpy as np

from liquid_glass_glsl_sampler_gate import (
    ACTIVE_SIZE,
    CAPTURE_SIZE,
    compare_active_half_trace,
)


class GLSLSamplerGateTests(unittest.TestCase):
    def test_exact_active_trace_passes(self) -> None:
        trace = np.zeros(
            (CAPTURE_SIZE, CAPTURE_SIZE, 4),
            dtype=np.uint16,
        )
        comparison = compare_active_half_trace(trace, trace.copy())
        self.assertTrue(comparison["exact"])
        self.assertEqual(
            comparison["observedHalfValues"],
            ACTIVE_SIZE * ACTIVE_SIZE * 4,
        )
        self.assertEqual(comparison["mismatchedHalfValues"], 0)

    def test_active_difference_is_counted(self) -> None:
        reference = np.zeros(
            (CAPTURE_SIZE, CAPTURE_SIZE, 4),
            dtype=np.uint16,
        )
        candidate = reference.copy()
        candidate[112, 112, 2] = 3
        comparison = compare_active_half_trace(reference, candidate)
        self.assertFalse(comparison["exact"])
        self.assertEqual(comparison["mismatchedHalfValues"], 1)
        self.assertEqual(comparison["mismatchedPixels"], 1)
        self.assertEqual(comparison["maximumEncodingDistance"], 3)


if __name__ == "__main__":
    unittest.main()
