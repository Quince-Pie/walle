import unittest

import numpy as np

from liquid_glass_transfer_fit import (
    color_cross_validation,
    color_cube,
    context_repeat_validation,
    fit_report,
    knot_indexes,
)


class LiquidGlassTransferFitTests(unittest.TestCase):
    def test_knot_indexes_always_include_last_sample(self) -> None:
        self.assertEqual(knot_indexes(256, 32).tolist()[-2:], [224, 255])
        self.assertEqual(knot_indexes(256, 1).size, 256)

    def test_color_cube_uses_rgb_axis_order(self) -> None:
        levels = np.array([0.0, 255.0])
        inputs = np.array(
            [
                [red, green, blue]
                for blue in levels
                for green in levels
                for red in levels
            ]
        )
        outputs = inputs.copy()
        cube = color_cube(levels=levels, inputs=inputs, outputs=outputs)
        self.assertEqual(cube[1, 0, 1].tolist(), [255.0, 0.0, 255.0])

    def test_identity_grid_has_zero_trilinear_holdout_error(self) -> None:
        levels = np.arange(9, dtype=np.float64) * 32
        levels[-1] = 255
        inputs = np.array(
            [
                [red, green, blue]
                for blue in levels
                for green in levels
                for red in levels
            ]
        )
        result = color_cross_validation(
            levels=levels,
            inputs=inputs,
            outputs=inputs.copy(),
            tone_curve=np.arange(256, dtype=np.float64),
        )
        self.assertEqual(result["withheldSamples"], 604)
        self.assertAlmostEqual(
            result["tonePlusTrilinearResidual"]["withheldError"][
                "maximumAbsoluteCodes"
            ],
            0,
        )

    def test_context_repeat_matches_colors_after_permutation(self) -> None:
        fitting_inputs = np.asarray(
            [[0, 0, 0], [255, 0, 0], [0, 255, 0]],
            dtype=np.float64,
        )
        fitting_outputs = fitting_inputs + 7
        permutation = [2, 0, 1]
        result = context_repeat_validation(
            fitting_inputs=fitting_inputs,
            fitting_outputs=fitting_outputs,
            repeat_inputs=fitting_inputs[permutation],
            repeat_outputs=fitting_outputs[permutation],
        )
        self.assertEqual(
            result["sameInputDifferentPositionAndNeighborhoodError"][
                "maximumAbsoluteCodes"
            ],
            0,
        )

    def test_complete_spatial_capture_still_requires_a_fitted_model(self) -> None:
        levels = np.asarray(
            [0, 32, 64, 96, 128, 160, 192, 224, 255],
            dtype=np.float64,
        )
        inputs = np.asarray(
            [
                [red, green, blue]
                for blue in levels
                for green in levels
                for red in levels
            ],
            dtype=np.float64,
        )
        midpoint_levels = np.arange(16, 241, 32, dtype=np.float64)
        midpoint_inputs = np.asarray(
            [
                [red, green, blue]
                for blue in midpoint_levels
                for green in midpoint_levels
                for red in midpoint_levels
            ],
            dtype=np.float64,
        )
        combinations = (
            "dark/clear",
            "light/clear",
            "dark/regular",
            "light/regular",
        )

        def chart(
            chart_inputs: np.ndarray,
            order: np.ndarray | None = None,
        ) -> dict[str, object]:
            selected = chart_inputs if order is None else chart_inputs[order]
            result: dict[str, object] = {
                "available": True,
                "sampleCount": len(selected),
                "inputCodes": selected.tolist(),
            }
            for combination in combinations:
                result[combination] = {"outputCodes": selected.tolist()}
            return result

        tone = {
            combination: {
                "inputCodes": list(range(256)),
                "outputCodes": list(range(256)),
                "orientationOutputCodes": {
                    "x": list(range(256)),
                    "y": list(range(256)),
                },
            }
            for combination in combinations
        }
        dense = chart(inputs)
        dense["gridLevels"] = levels.tolist()
        midpoint = chart(midpoint_inputs)
        midpoint["gridLevels"] = midpoint_levels.tolist()
        fitting_permutation = np.roll(np.arange(len(inputs)), 113)
        midpoint_permutation = np.roll(np.arange(len(midpoint_inputs)), 97)
        periods = {str(period): {} for period in (32, 64, 128, 256, 512, 1024)}
        measurements = {
            "denseToneTransfer": {"available": True, **tone},
            "denseColorTransfer": dense,
            "denseColorHoldout": midpoint,
            "denseColorContextRepeat": chart(
                inputs,
                fitting_permutation,
            ),
            "denseColorContextHoldout": chart(
                inputs,
                fitting_permutation[::-1],
            ),
            "denseColorHoldoutContextRepeat": chart(
                midpoint_inputs,
                midpoint_permutation,
            ),
            "phaseResponse": {
                "scenes": {
                    "circle-4000-center": {
                        combination: {axis: periods for axis in ("x", "y")}
                        for combination in (
                            "dark/regular",
                            "light/regular",
                        )
                    }
                }
            },
        }
        report = fit_report(measurements)

        sufficiency = report["captureSufficiency"]
        self.assertTrue(sufficiency["spatialCaptureCoverageComplete"])
        self.assertFalse(sufficiency["modelIdentificationCoverageComplete"])
        self.assertEqual(
            sufficiency["randomizedOnGridTrainingContexts"],
            0,
        )
        self.assertEqual(sufficiency["smallSignalStochasticProbes"], 0)
        self.assertFalse(sufficiency["pointwiseColorLutRejected"])
        self.assertFalse(sufficiency["colorTransferCertificationReady"])

        measurements["denseColorContextTraining"] = {
            "available": True,
            "requiredChartCount": 4,
            "availableChartCount": 4,
        }
        measurements["denseColorHoldoutContextTraining"] = {
            "available": True,
            "requiredChartCount": 4,
            "availableChartCount": 4,
        }
        measurements["stochasticProbeStatistics"] = {
            "available": True,
            "requiredProbeCount": 8,
            "availableProbeCount": 8,
        }
        sufficiency = fit_report(measurements)["captureSufficiency"]
        self.assertTrue(sufficiency["modelIdentificationCoverageComplete"])
        self.assertFalse(sufficiency["colorTransferCertificationReady"])


if __name__ == "__main__":
    unittest.main()
