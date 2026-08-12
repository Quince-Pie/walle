import json
import unittest

import numpy as np

from liquid_glass_clear_fixed_block import (
    AMPLITUDES,
    BLOCK_SIZES,
    SCALE_CALIBRATION_AMPLITUDES,
    background_name,
    center_scale_tomography,
    center_trace_reports_by_state,
    clamped_quantize_output,
    clipped_affine_continuous_fit,
    effective_source_amplitudes,
    fixed_block_origins,
    gaussian_square_center_response,
    metric_offsets,
    nearest_line_slack,
    nonnegative_fit,
    one_gaussian_design,
    quantized_counts,
    quantized_line_report,
    response_record,
    selected_scale_curves,
    shared_scale_fit,
    singular_energy_report,
    source_convolution_eligible,
    state_balanced_origins,
)
from liquid_glass_clear_state_fit import SampleGrid


class ClearFixedBlockTests(unittest.TestCase):
    def test_background_names_are_exact(self) -> None:
        self.assertEqual(
            background_name(64, 127),
            "clear-fixed-block-b0064-a127-train",
        )
        with self.assertRaises(ValueError):
            background_name(3, 127)
        with self.assertRaises(ValueError):
            background_name(64, 126)

    def test_common_origins_are_aligned_isolated_and_phase_cycling(
        self,
    ) -> None:
        origins = fixed_block_origins((2000, 3200))
        self.assertEqual(origins.shape, (228, 2))
        self.assertTrue(np.all(origins % 2 == 0))
        self.assertEqual(origins[0].tolist(), [32, 32])
        self.assertTrue(np.all(origins[:, 0] + 64 + 32 <= 2000))
        self.assertTrue(np.all(origins[:, 1] + 64 + 32 <= 3200))
        reduced_x = origins[origins[:, 0] == 32, 1] // 2
        self.assertEqual(set((reduced_x[:8] % 8).tolist()), set(range(8)))

    def test_metric_offsets_stay_inside_isolated_patch(self) -> None:
        for block_size in BLOCK_SIZES:
            offsets = metric_offsets(block_size)
            self.assertEqual(len(offsets), len(np.unique(offsets, axis=0)))
            self.assertGreaterEqual(int(offsets.min()), -32)
            self.assertLessEqual(int(offsets.max()), block_size + 31)
            self.assertIn([0, 0], offsets.tolist())
            self.assertIn(
                [block_size - 1, block_size - 1],
                offsets.tolist(),
            )

    def test_effective_source_amplitude_uses_observed_codes(self) -> None:
        source = np.full((4, 6, 3), 128, dtype=np.uint8)
        origins = np.asarray(((0, 0), (2, 4)), dtype=np.int64)
        vectors = np.asarray(((1, -1, 0), (0, 1, 1)), dtype=np.int64)
        source[0, 0] = (255, 0, 128)
        source[2, 4] = (128, 192, 192)
        np.testing.assert_array_equal(
            effective_source_amplitudes(source, origins, vectors),
            np.asarray((127.5, 64.0)),
        )

    def test_nearest_line_slack_accepts_irregular_amplitudes(self) -> None:
        amplitudes = np.asarray((0, *AMPLITUDES), dtype=np.float64)
        trace = np.rint(151.73 + 0.319 * amplitudes).astype(np.int64)
        report = nearest_line_slack(amplitudes, trace)
        json.dumps(report)
        self.assertTrue(report["closedNearestIntervalFeasible"])
        self.assertLessEqual(
            report["minimumAdditionalHalfWidthCodes"],
            1e-9,
        )

    def test_nearest_line_slack_accepts_clipped_affine_trace(self) -> None:
        amplitudes = np.asarray((0, *AMPLITUDES), dtype=np.float64)
        trace = np.clip(
            np.rint(152.1 + 1.2 * amplitudes),
            0,
            255,
        ).astype(np.int64)
        report = nearest_line_slack(amplitudes, trace)
        self.assertTrue(report["closedNearestIntervalFeasible"])
        self.assertLessEqual(
            report["minimumAdditionalHalfWidthCodes"],
            1e-9,
        )

    def test_clamped_quantizer_bounds_both_endpoints(self) -> None:
        values = np.asarray((-2.0, -0.6, 0.4, 254.6, 256.0))
        np.testing.assert_array_equal(
            clamped_quantize_output(values, "half-even"),
            np.asarray((0, 0, 0, 255, 255)),
        )

    def test_clipped_affine_fit_ignores_saturated_plateau(self) -> None:
        amplitudes = np.asarray((0, *AMPLITUDES), dtype=np.float64)
        traces = np.column_stack(
            (
                np.clip(np.rint(152 + amplitudes), 0, 255),
                np.clip(np.rint(100 - amplitudes), 0, 255),
            )
        ).astype(np.int64)
        continuous = clipped_affine_continuous_fit(
            amplitudes,
            traces,
        )
        predicted = clamped_quantize_output(
            continuous,
            "half-even",
        )
        np.testing.assert_array_equal(predicted, traces)

    def test_quantized_line_report_rejects_discrete_outlier(self) -> None:
        amplitudes = np.asarray((0, *AMPLITUDES), dtype=np.int64)
        exact = np.rint(151.73 + 0.319 * amplitudes).astype(np.int64)
        outlier = exact.copy()
        outlier[10] += 3
        traces = np.column_stack((exact, outlier))
        report = quantized_line_report(
            amplitudes,
            traces,
            feasibility_samples=2,
        )
        json.dumps(report)
        self.assertEqual(
            report["nearestIntervalFeasibility"]["sampled"],
            2,
        )
        self.assertEqual(
            report["nearestIntervalFeasibility"]["infeasible"],
            1,
        )
        self.assertEqual(
            report["modes"]["half-even"]["maximumAbsoluteErrorCodes"],
            3,
        )
        residues = report["modes"]["half-even"][
            "exactByActualOutputCodeModulo8"
        ]
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

    def test_quantized_counts_tracks_nonzero_errors(self) -> None:
        baseline = np.array([[10, 10, 10], [20, 20, 20]])
        actual = np.array([[10, 11, 10], [20, 22, 20]])
        predicted = np.array([[10, 10, 10], [20, 21, 23]])
        counts = quantized_counts(actual, predicted, baseline)
        report = counts.as_json()
        json.dumps(report)
        self.assertEqual(counts.values, 6)
        self.assertEqual(counts.exact, 3)
        self.assertEqual(counts.nonzero, 2)
        self.assertEqual(counts.exact_nonzero, 0)
        self.assertEqual(counts.maximum, 3)
        self.assertEqual(counts.absolute_sum, 5)
        self.assertEqual(counts.squared_sum, 11)

    def test_state_balanced_origins_caps_each_state(self) -> None:
        origins = np.column_stack(
            (
                np.arange(12, dtype=np.int64),
                np.arange(12, dtype=np.int64) * 2,
            )
        )
        states = np.asarray((0, 0, 0, 0, 1, 1, 2, 2, 2, 2, 2, 3))
        selected = state_balanced_origins(
            origins,
            states,
            sites_per_state=2,
        )
        np.testing.assert_array_equal(
            selected,
            origins[[0, 3, 4, 5, 6, 10, 11]],
        )

    def test_source_convolution_excludes_image_edge_footprints(self) -> None:
        grid = SampleGrid(
            y=np.asarray((11, 12, 1986, 1987, 1000)),
            x=np.asarray((12, 11, 3186, 3187, 1600)),
        )
        axis = np.arange(-12, 13, dtype=np.int64)
        offset_y, offset_x = np.meshgrid(axis, axis, indexing="ij")
        offsets = np.column_stack(
            (offset_y.reshape(-1), offset_x.reshape(-1))
        )
        np.testing.assert_array_equal(
            source_convolution_eligible(
                grid,
                (1000, 1600),
                offsets,
            ),
            np.asarray((False, False, True, False, True)),
        )

    def test_response_record_separates_inside_and_outside_support(self) -> None:
        block_size = 4
        side = block_size + 64
        delta = np.zeros((3, side, side, 3), dtype=np.int64)
        delta[:, 32 : 32 + block_size, 32 : 32 + block_size] = 2
        projected = np.zeros((3, side, side), dtype=np.float64)
        projected[:, 32 : 32 + block_size, 32 : 32 + block_size] = 2
        report = response_record(
            delta,
            projected,
            np.asarray((0, 1, 1), dtype=np.int64),
            block_size,
            2,
        )
        json.dumps(report)
        self.assertEqual(
            report["insideMeanSignedGainPerSourceCode"]["mean"],
            1.0,
        )
        self.assertEqual(
            report["outsideAbsoluteGainPerInputPixel"]["maximum"],
            0.0,
        )
        self.assertEqual(
            report["supportByOutsideChebyshevDistancePixels"]["1"][
                "maximumAbsoluteCodes"
            ],
            0,
        )
        self.assertEqual(report["byState"]["1"]["sites"], 2)

    def test_square_gaussian_response_is_monotone_in_block_size(self) -> None:
        sizes = np.asarray(BLOCK_SIZES, dtype=np.float64)
        response = gaussian_square_center_response(sizes, 4.0)
        self.assertTrue(np.all(np.diff(response) > 0))
        self.assertGreater(response[-1], 0.999)

    def test_singular_energy_report_distinguishes_two_scale_curves(
        self,
    ) -> None:
        first = np.asarray((1.0, 2.0, 3.0, 4.0))
        second = np.asarray((4.0, 3.0, 2.0, 1.0))
        curves = np.vstack((first, 2 * first, second, 3 * second))
        report = singular_energy_report(curves)
        json.dumps(report)
        self.assertAlmostEqual(report["rankTwoEnergyFraction"], 1.0)
        self.assertLess(report["rankOneEnergyFraction"], 1.0)
        self.assertEqual(report["curveCount"], 4)

    def test_selected_scale_curves_preserves_scale_as_last_axis(
        self,
    ) -> None:
        responses = np.arange(3 * 2 * 4, dtype=np.float64).reshape(
            3,
            2,
            4,
        )
        selected = np.asarray(
            (
                (True, False, False, True),
                (False, True, False, False),
            )
        )
        curves = selected_scale_curves(responses, selected)
        self.assertEqual(curves.shape, (3, 3))
        np.testing.assert_array_equal(
            curves,
            np.asarray(
                (
                    (0, 8, 16),
                    (3, 11, 19),
                    (5, 13, 21),
                ),
                dtype=np.float64,
            ),
        )

    def test_nonnegative_fit_recovers_active_component_subset(self) -> None:
        design = np.column_stack(
            (
                np.ones(6),
                np.linspace(0.1, 0.9, 6),
                np.linspace(0.2, 0.8, 6) ** 2,
            )
        )
        expected = np.asarray(
            (
                (0.5, 0.3, 0.0),
                (0.1, 0.0, 0.7),
            )
        )
        curves = expected @ design.T
        coefficients, predicted = nonnegative_fit(design, curves)
        np.testing.assert_allclose(coefficients, expected, atol=1e-12)
        np.testing.assert_allclose(predicted, curves, atol=1e-12)

    def test_one_gaussian_scale_is_optimized_beyond_search_grid(
        self,
    ) -> None:
        block_sizes = np.asarray(BLOCK_SIZES, dtype=np.float64)
        sigma = 4.123
        design = one_gaussian_design(block_sizes, sigma)
        curves = np.asarray(((0.61, 0.39), (0.72, 0.28))) @ design.T
        report = shared_scale_fit(
            curves,
            block_sizes,
            gaussian_components=1,
        )
        self.assertAlmostEqual(
            report["gaussianSigmaOutputPixels"][0],
            sigma,
            places=5,
        )

    def test_center_scale_tomography_recovers_two_synthetic_scales(
        self,
    ) -> None:
        block_sizes = np.asarray(BLOCK_SIZES, dtype=np.float64)
        sharp = np.ones(len(BLOCK_SIZES), dtype=np.float64)
        sharp[0] = 0.5625
        curve = (
            0.55 * sharp
            + 0.30
            * gaussian_square_center_response(block_sizes, 4.1)
            + 0.15
            * gaussian_square_center_response(block_sizes, 14.0)
        )
        sizes = {
            str(block_size): {
                "responses": {
                    str(amplitude): {
                        "byState": {
                            "0": {
                                "centerSignedGainPerSourceCode": {
                                    "mean": float(curve[size_index])
                                }
                            }
                        }
                    }
                    for amplitude in SCALE_CALIBRATION_AMPLITUDES
                }
            }
            for size_index, block_size in enumerate(BLOCK_SIZES)
        }
        report = center_scale_tomography(sizes)
        json.dumps(report)
        self.assertTrue(report["available"])
        self.assertEqual(
            report["curveCount"],
            len(SCALE_CALIBRATION_AMPLITUDES),
        )
        self.assertEqual(
            len(
                report["sharpPlusTwoGaussians"][
                    "componentWeightsByCurve"
                ]
            ),
            len(SCALE_CALIBRATION_AMPLITUDES),
        )
        recovered = report["sharpPlusTwoGaussians"][
            "gaussianSigmaOutputPixels"
        ]
        self.assertAlmostEqual(recovered[0], 4.1, delta=0.25)
        self.assertAlmostEqual(recovered[1], 14.0, delta=0.75)
        self.assertLess(
            report["sharpPlusTwoGaussians"]["training"][
                "rootMeanSquareCodesPerSourceCode"
            ],
            report["sharpPlusOneGaussian"]["training"][
                "rootMeanSquareCodesPerSourceCode"
            ],
        )

    def test_center_traces_are_partitioned_without_mixing_states(self) -> None:
        amplitudes = np.asarray((0, *AMPLITUDES), dtype=np.int64)
        offsets = metric_offsets(4)
        traces = np.full(
            (amplitudes.size, 3, offsets.shape[0], 3),
            152,
            dtype=np.uint8,
        )
        center = np.isin(offsets[:, 0], (1, 2)) & np.isin(
            offsets[:, 1],
            (1, 2),
        )
        for index, amplitude in enumerate(amplitudes):
            traces[index, 0, center] = np.clip(
                152 + amplitude // 16,
                0,
                255,
            )
            traces[index, 1:, center] = np.clip(
                152 + amplitude // 8,
                0,
                255,
            )
        report = center_trace_reports_by_state(
            amplitudes,
            traces,
            offsets,
            np.asarray((0, 1, 1), dtype=np.int64),
            4,
        )
        json.dumps(report)
        self.assertEqual(report["0"]["sites"], 1)
        self.assertEqual(report["1"]["sites"], 2)
        self.assertEqual(
            report["0"]["traces"]["modes"]["half-even"]["traceCount"],
            12,
        )
        self.assertEqual(
            report["1"]["traces"]["modes"]["half-even"]["traceCount"],
            24,
        )


if __name__ == "__main__":
    unittest.main()
