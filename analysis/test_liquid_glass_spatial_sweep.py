import unittest

import numpy as np

from liquid_glass_spatial_sweep import (
    SpatialSweep,
    canonicalize_phase,
    difference_metrics,
    face_replay,
    rgba8_blur_zero_patch,
    support_metrics,
)


class SpatialSweepTests(unittest.TestCase):
    def test_difference_metrics_counts_values_and_pixels(self) -> None:
        predicted = np.asarray(
            ((1, 2, 3), (4, 5, 6)),
            dtype=np.uint8,
        )
        actual = np.asarray(
            ((1, 2, 3), (4, 7, 6)),
            dtype=np.uint8,
        )
        result = difference_metrics(predicted, actual)
        self.assertEqual(result["changedValues"], 1)
        self.assertEqual(result["changedPixels"], 1)
        self.assertEqual(result["maximumAbsoluteCodes"], 2)

    def test_phase_canonicalization_is_an_involution(self) -> None:
        values = np.arange(5 * 5, dtype=np.uint8).reshape(
            1,
            5,
            5,
            1,
        )
        for phase_y in range(2):
            for phase_x in range(2):
                transformed = canonicalize_phase(
                    values,
                    phase_y=phase_y,
                    phase_x=phase_x,
                )
                restored = canonicalize_phase(
                    transformed,
                    phase_y=phase_y,
                    phase_x=phase_x,
                )
                np.testing.assert_array_equal(restored, values)

    def test_support_metrics_uses_chebyshev_radius(self) -> None:
        stream = np.full(
            (2, 1, 3, 3, 3),
            128,
            dtype=np.uint8,
        )
        stream[1, 0, 0, 2, 1] = 129
        sweep = SpatialSweep(
            manifest={
                "sourceDesign": {
                    "patchRadiusPixels": 1,
                    "patchSidePixels": 3,
                    "sites": [],
                }
            },
            control=stream,
            clear=stream,
            interventions={},
        )
        result = support_metrics(sweep, stream)
        self.assertEqual(result["unionChangedCoordinates"], 1)
        self.assertEqual(result["maximumChangedChebyshevRadius"], 1)

    def test_face_replay_recovers_uniform_gray_baseline(self) -> None:
        identity = np.full(
            (1, 1, 1, 1, 3),
            128,
            dtype=np.uint8,
        )
        clear = np.full_like(identity, 152)
        sweep = SpatialSweep(
            manifest={
                "sourceDesign": {
                    "patchRadiusPixels": 0,
                    "patchSidePixels": 1,
                    "sites": [],
                }
            },
            control=identity,
            clear=clear,
            interventions={"identity-blur-1": identity},
        )
        result = face_replay(sweep)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["changedValues"], 0)

    def test_rgba8_blur_zero_explains_code_152_tie(self) -> None:
        patch = rgba8_blur_zero_patch(152)
        np.testing.assert_array_equal(
            patch,
            np.asarray(
                (
                    (129, 132, 132, 129),
                    (132, 141, 141, 132),
                    (132, 141, 141, 132),
                    (129, 132, 132, 129),
                ),
                dtype=np.uint8,
            ),
        )


if __name__ == "__main__":
    unittest.main()
