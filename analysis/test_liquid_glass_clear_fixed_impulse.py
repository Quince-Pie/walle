import json
import unittest

import numpy as np

from liquid_glass_clear_state_fit import SampleGrid
from liquid_glass_clear_fixed_impulse import (
    bilinear_gaussian_core_candidate,
    fit_impulse_kernel,
    fixed_impulse_origins,
    kernel_prediction_report,
    minimum_nearest_affine_slack,
    predict_dense_samples,
    quantized_affine_report,
    reconstruction_bases,
    spatial_color_separability,
)


class ClearFixedImpulseTests(unittest.TestCase):
    def test_fixed_origins_are_aligned_and_phase_complete(self) -> None:
        origins = fixed_impulse_origins((900, 1200))
        self.assertTrue(np.all(origins % 2 == 0))
        self.assertEqual(origins[0].tolist(), [32, 32])
        reduced_x = origins[origins[:, 0] == 32, 1] // 2
        self.assertEqual(set((reduced_x[:8] % 8).tolist()), set(range(8)))

    def test_nearest_affine_slack_accepts_exact_quantized_line(self) -> None:
        x = np.arange(128, dtype=np.float64)
        trace = np.rint(151.73 + 0.319 * x).astype(np.int64)
        report = minimum_nearest_affine_slack(trace)
        self.assertTrue(report["closedNearestIntervalFeasible"])
        self.assertLessEqual(
            report["minimumAdditionalHalfWidthCodes"],
            1e-9,
        )

    def test_nearest_affine_slack_rejects_discrete_outlier(self) -> None:
        x = np.arange(128, dtype=np.float64)
        trace = np.rint(151.73 + 0.319 * x).astype(np.int64)
        trace[64] += 3
        report = minimum_nearest_affine_slack(trace)
        self.assertFalse(report["closedNearestIntervalFeasible"])
        self.assertGreater(
            report["minimumAdditionalHalfWidthCodes"],
            0.5,
        )

    def test_quantized_affine_report_separates_active_values(self) -> None:
        x = np.arange(128, dtype=np.float64)
        active = np.rint(151.73 + 0.319 * x).astype(np.int64)
        constant = np.full(128, 152, dtype=np.int64)
        traces = np.column_stack((active, constant))
        report = quantized_affine_report(traces, feasibility_samples=4)
        json.dumps(report)
        self.assertEqual(report["maximumAbsoluteErrorCodes"], 0)
        self.assertEqual(report["exactValueFraction"], 1.0)
        self.assertEqual(report["activeTraceCount"], 1)
        self.assertEqual(report["activeTraceExactFraction"], 1.0)
        self.assertEqual(
            report["nonzeroActualResponseValueExactFraction"],
            1.0,
        )
        residues = report["exactByActualOutputCodeModulo8"]
        self.assertEqual(
            sum(record["observations"] for record in residues.values()),
            traces.size,
        )
        self.assertEqual(
            sum(
                record["nonzeroActualResponseValueCount"]
                for record in residues.values()
            ),
            int(np.count_nonzero(traces != traces[0:1])),
        )

    def test_held_site_kernel_fit_recovers_cross_channel_response(
        self,
    ) -> None:
        vectors = np.array(
            [
                [1, 0, 0],
                [0, 1, 0],
                [0, 0, 1],
                [-1, 1, 0],
                [0, -1, 1],
                [1, 0, -1],
                [1, 1, 1],
                [-1, -1, 1],
                [1, -1, -1],
                [-1, 1, -1],
                [1, 1, 0],
                [0, 1, 1],
            ],
            dtype=np.int64,
        )
        coefficients = np.array(
            [
                [0.31, 0.003, -0.002],
                [0.002, 0.32, 0.001],
                [-0.001, 0.004, 0.30],
            ],
            dtype=np.float64,
        )
        amplitudes = np.arange(128, dtype=np.float64)
        response = np.einsum(
            "a,si,io->aso",
            amplitudes,
            vectors,
            coefficients,
        )
        codes = np.rint(152.0 + response).astype(np.uint8)[:, :, None]
        selected = np.ones(vectors.shape[0], dtype=np.bool_)
        fitted = fit_impulse_kernel(codes, vectors, selected)
        np.testing.assert_allclose(
            fitted.reshape(3, 3),
            coefficients,
            atol=1e-3,
        )
        report = kernel_prediction_report(
            codes,
            vectors,
            np.arange(vectors.shape[0], dtype=np.int64) % 4,
        )
        json.dumps(report)
        self.assertLessEqual(
            report["quantized"]["half-even"][
                "maximumAbsoluteErrorCodes"
            ],
            1,
        )
        self.assertGreater(
            report["quantized"]["half-even"]["exactValueFraction"],
            0.95,
        )

    def test_core_candidate_recovers_bilinear_gaussian_mixture(
        self,
    ) -> None:
        values = np.arange(-4, 5, dtype=np.int64)
        y, x = np.meshgrid(values, values, indexing="ij")
        offsets = np.column_stack((y.ravel(), x.ravel()))
        sharp, blur = reconstruction_bases(
            offsets,
            gaussian_sigma_half_grid=2.05,
        )
        kernels = {}
        for state in range(13):
            fraction = state / 12
            response = (
                (0.54 + 0.20 * fraction) * sharp
                + (0.40 - 0.24 * fraction) * blur
            )
            kernel = np.zeros((3, offsets.shape[0], 3))
            for channel in range(3):
                kernel[channel, :, channel] = response
            kernels[state] = kernel
        report = bilinear_gaussian_core_candidate(kernels, offsets)
        json.dumps(report)
        self.assertAlmostEqual(
            report["gaussianSigmaHalfGridCells"],
            2.05,
            places=2,
        )
        self.assertLess(
            report["coefficientErrorCodesPerSourceCode"][
                "maximumAbsolute"
            ],
            1e-12,
        )
        separability = spatial_color_separability(kernels)
        json.dumps(separability)
        for record in separability["records"].values():
            self.assertAlmostEqual(
                record["spatialTimesColorRankOneEnergyFraction"],
                1.0,
            )
            np.testing.assert_allclose(
                record["normalizedColorMatrixInputByOutput"],
                np.eye(3),
                atol=1e-12,
            )

    def test_dense_prediction_obeys_output_phase(self) -> None:
        half_grid = np.arange(36, dtype=np.float64).reshape(3, 4, 3) + 128
        baseline = np.full((6, 8, 3), 152, dtype=np.uint8)
        y, x = np.meshgrid(
            np.arange(1, 5, dtype=np.int64),
            np.arange(1, 7, dtype=np.int64),
            indexing="ij",
        )
        grid_y = y.ravel()
        grid_x = x.ravel()
        grid = SampleGrid(y=grid_y, x=grid_x)
        offsets = np.array(
            [[0, 0], [0, 1], [1, 0], [1, 1]],
            dtype=np.int64,
        )
        kernel = np.zeros((3, 4, 3), dtype=np.float64)
        for offset_index in range(4):
            kernel[:, offset_index, :] = np.eye(3)
        predicted = predict_dense_samples(
            half_grid,
            baseline,
            grid,
            np.zeros(grid_y.size, dtype=np.int64),
            {0: kernel},
            offsets,
        )
        expected = (
            baseline[grid_y, grid_x].astype(np.float64)
            + half_grid[grid_y // 2, grid_x // 2]
            - 128
        )
        np.testing.assert_array_equal(predicted, expected)


if __name__ == "__main__":
    unittest.main()
