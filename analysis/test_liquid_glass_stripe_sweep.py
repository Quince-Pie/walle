import unittest

import numpy as np

from liquid_glass_stripe_sweep import (
    CHANNELS,
    EXPECTED_AMPLITUDES,
    EXPECTED_ORIENTATIONS,
    EXPECTED_PATCH_RADIUS,
    EXPECTED_PATCH_SIDE,
    EXPECTED_POSITIONS,
    SOURCE_CODE,
    StripeSweep,
    expected_control,
    orientation_isotropy,
    orthogonal_invariance,
    orthogonal_state_runs,
    response_measurements,
    source_fidelity,
)


def stream_shape() -> tuple[int, ...]:
    return (
        len(EXPECTED_AMPLITUDES),
        len(EXPECTED_ORIENTATIONS),
        len(EXPECTED_POSITIONS),
        EXPECTED_PATCH_SIDE,
        EXPECTED_PATCH_SIDE,
        CHANNELS,
    )


class StripeSweepTests(unittest.TestCase):
    def test_expected_control_has_half_open_alternating_edges(self) -> None:
        control = expected_control(stream_shape())
        center = EXPECTED_PATCH_RADIUS
        amplitude = 127
        self.assertEqual(control[amplitude, 0, 0, center, center - 1, 0], 128)
        self.assertEqual(control[amplitude, 0, 0, center, center, 0], 255)
        self.assertEqual(control[amplitude, 0, 1, center, center - 1, 0], 255)
        self.assertEqual(control[amplitude, 0, 1, center, center, 0], 128)
        self.assertEqual(control[amplitude, 1, 2, center, center, 1], 1)
        self.assertEqual(control[amplitude, 1, 3, center - 1, center, 2], 255)

    def test_source_fidelity_accepts_exact_control(self) -> None:
        control = expected_control(stream_shape())
        sweep = StripeSweep(
            manifest={},
            control=control,
            identity=control,
        )
        self.assertTrue(source_fidelity(sweep)["exact"])

    def test_invariance_accepts_transposed_stripes(self) -> None:
        stream = expected_control(stream_shape())
        orthogonal = orthogonal_invariance(stream)
        self.assertEqual(orthogonal["verticalRows"]["changedValues"], 0)
        self.assertEqual(
            orthogonal["horizontalColumns"]["changedValues"],
            0,
        )
        self.assertEqual(
            orientation_isotropy(stream)["changedValues"],
            0,
        )
        states = orthogonal_state_runs(
            stream,
            EXPECTED_POSITIONS,
            384,
        )
        self.assertTrue(all(record["stateCount"] == 1 for record in states))

    def test_response_recovers_unit_step_gain(self) -> None:
        stream = expected_control(stream_shape())
        response = response_measurements(stream)
        np.testing.assert_allclose(
            response["gainRangeCodesPerAmplitude"],
            (1, 1),
        )
        for record in response["phaseRecords"]:
            self.assertAlmostEqual(record["kernelSum"], 1)
            self.assertAlmostEqual(record["kernelPositiveMass"], 1)
            self.assertAlmostEqual(record["kernelNegativeMass"], 0)
            self.assertAlmostEqual(
                record["kernelCenterOfMassPixels"],
                -0.5,
            )

    def test_baseline_uses_source_code(self) -> None:
        stream = expected_control(stream_shape())
        self.assertTrue(np.all(stream[0] == SOURCE_CODE))


if __name__ == "__main__":
    unittest.main()
