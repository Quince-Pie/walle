import unittest

import numpy as np

from liquid_glass_kernel_sweep import (
    CHANNELS,
    EXPECTED_PATCH_RADIUS,
    EXPECTED_PATCH_SIDE,
    KernelSweep,
    _half_unorm_envelopes,
    mip_blend_envelope,
    mixed_derivative,
    source_fidelity,
)


class KernelSweepTests(unittest.TestCase):
    def test_binary16_envelopes_contain_direct_unorm_images(self) -> None:
        minimum, maximum = _half_unorm_envelopes()
        direct = (
            np.arange(256, dtype=np.float32) / np.float32(255)
        ).astype(np.float16)
        self.assertTrue(np.all(minimum <= direct))
        self.assertTrue(np.all(direct <= maximum))

    def test_mixed_derivative_recovers_separable_kernel(self) -> None:
        vertical = np.asarray((0.1, 0.3, 0.6))
        horizontal = np.asarray((0.25, 0.75))
        step = np.pad(
            np.cumsum(np.cumsum(
                vertical[:, None] * horizontal[None, :],
                axis=0,
            ), axis=1),
            ((1, 0), (1, 0)),
        )
        recovered = mixed_derivative(step[None])[0]
        np.testing.assert_allclose(
            recovered,
            vertical[:, None] * horizontal[None, :],
        )

    def test_source_fidelity_accepts_declared_square_quadrant(self) -> None:
        shape = (
            128,
            16,
            EXPECTED_PATCH_SIDE,
            EXPECTED_PATCH_SIDE,
            CHANNELS,
        )
        control = np.full(shape, 128, dtype=np.uint8)
        center = EXPECTED_PATCH_RADIUS
        for amplitude in range(128):
            control[amplitude, :, center:, center:, 0] = 128 + amplitude
            control[amplitude, :, center:, center:, 1] = 128 - amplitude
            control[amplitude, :, center:, center:, 2] = 128 + amplitude
        sweep = KernelSweep(
            manifest={"sourceDesign": {"sites": [{}] * 16}},
            control=control,
            clear=control,
            interventions={},
        )
        self.assertTrue(source_fidelity(sweep)["exact"])

    def test_mip_envelope_accepts_identical_levels(self) -> None:
        shape = (128, 1, 1, 1, 3)
        values = np.arange(128, dtype=np.uint8).reshape(
            128, 1, 1, 1, 1
        )
        stream = np.broadcast_to(values, shape)
        result = mip_blend_envelope(stream, stream, stream)
        self.assertTrue(
            result["binary16EndpointEnvelope"]["allCompatible"]
        )


if __name__ == "__main__":
    unittest.main()
