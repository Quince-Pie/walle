import unittest

import numpy as np

from liquid_glass_clear_amplitude_affinity import (
    amplitude_subspace_report,
    fixed_intercept_intervals,
    free_intercept_intervals,
)


class ClearAmplitudeAffinityTests(unittest.TestCase):
    def test_affine_nearest_integer_traces_are_feasible(self) -> None:
        amplitudes = np.arange(17, dtype=np.int64)
        slopes = np.asarray((-0.37, 0.0, 0.291), dtype=np.float64)
        intercepts = np.asarray((0.13, -0.41, 0.33), dtype=np.float64)
        traces = np.floor(
            intercepts[np.newaxis]
            + amplitudes[:, np.newaxis] * slopes[np.newaxis]
            + 0.5
        ).astype(np.int64)

        free = free_intercept_intervals(traces, amplitudes)

        np.testing.assert_array_equal(free.feasible, True)

    def test_fixed_intercept_rejects_a_required_subcode_offset(self) -> None:
        amplitudes = np.arange(17, dtype=np.int64)
        trace = np.floor(
            -0.499 + amplitudes * -1.875 + 0.5
        ).astype(np.int64)
        traces = trace[:, np.newaxis]

        fixed = fixed_intercept_intervals(traces, amplitudes)
        free = free_intercept_intervals(traces, amplitudes)

        self.assertFalse(bool(fixed.feasible[0]))
        self.assertTrue(bool(free.feasible[0]))

    def test_nonaffine_trace_is_rejected(self) -> None:
        amplitudes = np.arange(9, dtype=np.int64)
        traces = np.asarray(
            (0, 0, 1, 1, 2, 2, 3, 8, 4),
            dtype=np.int64,
        )[:, np.newaxis]

        self.assertFalse(
            bool(free_intercept_intervals(traces, amplitudes).feasible[0])
        )

    def test_validation_rejects_missing_zero_baseline(self) -> None:
        amplitudes = np.arange(4, dtype=np.int64)
        traces = np.ones((4, 1), dtype=np.int64)

        with self.assertRaises(ValueError):
            free_intercept_intervals(traces, amplitudes)

    def test_rank_two_subspace_reconstructs_integer_traces(self) -> None:
        amplitudes = np.arange(9, dtype=np.int64)
        basis = np.column_stack(
            (
                amplitudes,
                (amplitudes % 2) * 3,
            )
        )
        coefficients = np.asarray(
            (
                (1, 2, -1, 3, 0, 2),
                (2, -1, 3, 1, 4, -2),
            )
        )
        traces = basis @ coefficients

        report = amplitude_subspace_report(
            traces,
            eligible=np.asarray((True, True)),
            channels=3,
            maximum_rank=3,
        )

        self.assertEqual(
            report["roundedReconstructionByRank"][1][
                "exactChannelFraction"
            ],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
