import unittest

import numpy as np

from liquid_glass_clear_filter_stage import (
    error_report,
    impulse_cross_validation,
    impulse_offsets,
    impulse_origins,
    quantize,
    slope_pair_report,
    ImpulseChart,
)


class ClearFilterStageTests(unittest.TestCase):
    def test_impulse_geometry_is_aligned_and_complete(self) -> None:
        origins = impulse_origins(
            (700, 900),
            offset_x=64,
            offset_y=96,
        )
        self.assertTrue(np.all(origins[:, 0] % 2 == 0))
        self.assertTrue(np.all(origins[:, 1] % 2 == 0))
        self.assertEqual(origins[0].tolist(), [96, 64])
        self.assertEqual(origins[-1].tolist(), [608, 832])
        offsets = impulse_offsets(2)
        self.assertEqual(offsets.shape, (25, 2))
        self.assertEqual(offsets[12].tolist(), [0, 0])

    def test_quantizers_handle_signed_half_codes(self) -> None:
        values = np.array([-1.5, -0.5, 0.5, 1.5])
        np.testing.assert_array_equal(
            quantize(values, "half-up"),
            np.array([-1.0, 0.0, 1.0, 2.0]),
        )
        np.testing.assert_array_equal(
            quantize(values, "half-even"),
            np.array([-2.0, -0.0, 0.0, 2.0]),
        )

    def test_slope_pair_detects_affine_complement(self) -> None:
        x = np.arange(8, dtype=np.int64)
        forward = np.zeros((4, 8, 3), dtype=np.int64)
        forward[:, :, 0] = 64 + x // 2
        reverse = 256 - forward
        output_forward = 2 * forward + 7
        output_reverse = 2 * reverse - 7
        report = slope_pair_report(
            forward,
            reverse,
            output_forward,
            output_reverse,
            channel=0,
            axis=1,
        )
        self.assertEqual(report["eligibleSteps"], 24)
        self.assertEqual(
            report["complementaryStepSum"]["maximumAbsoluteCodes"],
            0,
        )

    def test_impulse_cross_validation_recovers_linear_response(self) -> None:
        offsets = impulse_offsets(0)
        charts = []
        sources = (
            np.array(
                [[1, 2, 3], [3, -2, 1], [-2, 1, 3], [4, 2, -3], [2, -4, 1], [-3, -1, 2]],
                dtype=np.float64,
            ),
            np.array(
                [[2, 3, -1], [-1, 4, 2], [3, 1, 2], [-4, 2, 1], [1, -3, 4], [2, -1, -3]],
                dtype=np.float64,
            ),
            np.array(
                [[4, -1, 2], [2, 4, 1], [-3, 2, 4], [1, 3, -2], [-2, -4, 3], [3, -2, -1]],
                dtype=np.float64,
            ),
        )
        transform = np.array(
            [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, -1.0]]
        )
        for index, source in enumerate(sources):
            response = (source @ transform)[:, np.newaxis, :]
            charts.append(
                ImpulseChart(
                    name=f"{index:02d}",
                    source=source,
                    response=response,
                    states=np.zeros(source.shape[0], dtype=np.int64),
                    eligible=np.ones(source.shape[0], dtype=np.bool_),
                    offsets=offsets,
                )
            )
        report = impulse_cross_validation(tuple(charts))
        self.assertLess(
            report["continuous"]["maximumAbsoluteCodes"],
            1e-12,
        )
        self.assertEqual(
            report["quantized"]["half-up"]["exactFraction"],
            1.0,
        )

    def test_error_report_is_exact(self) -> None:
        report = error_report(
            np.array([0.0, 1.0, 3.0]),
            np.array([0.0, 2.0, 1.0]),
        )
        self.assertEqual(report["exactFraction"], 1 / 3)
        self.assertEqual(report["maximumAbsoluteCodes"], 2.0)


if __name__ == "__main__":
    unittest.main()
