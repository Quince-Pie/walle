import unittest

import numpy as np

from liquid_glass_backdrop_pyramid import (
    CHANNELS,
    SOURCE_SIDE,
    WEIGHTS,
    _copy_base_half_phase_sample,
    _half_phase_sample,
    _split_mask,
    half_round,
    replay_agx2_software,
    replay_base_producer_software,
    replay_copy_base_mip_software,
    replay_live_copy_base_software,
    replay_regular_base_producer_software,
    unorm8,
)


class BackdropPyramidTests(unittest.TestCase):
    def test_embedded_kernel_is_normalized(self) -> None:
        normalization = float(
            WEIGHTS[3].astype(np.float64)
            + 4 * np.sum(WEIGHTS[:3].astype(np.float64))
        )
        self.assertEqual(normalization, 1.0)

    def test_constant_source_is_invariant(self) -> None:
        codes = np.asarray((17, 91, 203, 255), dtype=np.uint8)
        source = np.broadcast_to(
            codes,
            (SOURCE_SIDE, SOURCE_SIDE, CHANNELS),
        ).copy()
        predicted = unorm8(replay_agx2_software(source))
        expected = np.broadcast_to(codes, predicted.shape)
        np.testing.assert_array_equal(predicted, expected)

    def test_copy_base_constant_source_is_invariant(self) -> None:
        codes = np.asarray((17, 91, 203, 255), dtype=np.uint8)
        source = np.broadcast_to(
            codes,
            (SOURCE_SIDE, SOURCE_SIDE, CHANNELS),
        ).copy()
        predicted = unorm8(
            replay_copy_base_mip_software(source)
        )
        expected = np.broadcast_to(codes, predicted.shape)
        np.testing.assert_array_equal(predicted, expected)

    def test_downsample_replays_accept_every_even_mip_size(
        self,
    ) -> None:
        codes = np.asarray((17, 91, 203, 255), dtype=np.uint8)
        source = np.broadcast_to(
            codes,
            (24, 32, CHANNELS),
        ).copy()
        for replay in (
            replay_agx2_software,
            replay_copy_base_mip_software,
        ):
            predicted = unorm8(replay(source))
            self.assertEqual(predicted.shape, (12, 16, CHANNELS))
            np.testing.assert_array_equal(
                predicted,
                np.broadcast_to(codes, predicted.shape),
            )

    def test_half_phase_sampler_averages_four_codes(self) -> None:
        source = np.zeros(
            (SOURCE_SIDE, SOURCE_SIDE, CHANNELS),
            dtype=np.uint8,
        )
        source[0, 0, 0] = 0
        source[0, 1, 0] = 1
        source[1, 0, 0] = 2
        source[1, 1, 0] = 3
        sampled = _half_phase_sample(
            source,
            offset_x=0,
            offset_y=0,
        )
        expected = np.float16(1.5 / 255)
        self.assertEqual(sampled[0, 0, 0], expected)

    def test_half_phase_sampler_clamps_each_endpoint(
        self,
    ) -> None:
        source = np.zeros(
            (24, 24, CHANNELS),
            dtype=np.uint8,
        )
        source[:, 0, 0] = 20
        source[:, 1, 0] = 220
        sampled = _half_phase_sample(
            source,
            offset_x=-4,
            offset_y=0,
        )
        self.assertEqual(
            sampled[0, 0, 0],
            np.float16(20 / 255),
        )

    def test_copy_base_prefilter_uses_explicit_half_adds(self) -> None:
        source = np.zeros(
            (SOURCE_SIDE, SOURCE_SIDE, CHANNELS),
            dtype=np.uint8,
        )
        source[1, 0, 0] = 1
        source[1, 1, 0] = 8
        source_half = half_round(
            source.astype(np.float64) / 255
        )
        explicit = _copy_base_half_phase_sample(
            source_half,
            offset_x=0,
            offset_y=0,
        )
        filtered = _half_phase_sample(
            source,
            offset_x=0,
            offset_y=0,
        )
        self.assertEqual(
            int(explicit[0, 0, 0].view(np.uint16)),
            0x2084,
        )
        self.assertEqual(
            int(filtered[0, 0, 0].view(np.uint16)),
            0x2085,
        )

    def test_base_producer_rounds_sampler_result_to_half(self) -> None:
        source = np.zeros((2, 2, CHANNELS), dtype=np.uint8)
        source[..., 3] = 255
        source[0, 0, 0] = 0
        source[0, 1, 0] = 1
        source[1, 0, 0] = 2
        source[1, 1, 0] = 3
        predicted = replay_base_producer_software(
            source,
            destination_width=1,
            destination_height=1,
            source_x=0,
            source_y=0,
            active_width=1,
            active_height=1,
        )
        # The RGBA red source channel is BGRA channel two in the target.
        # Exact code averaging would round 1.5 to 2; Apple's intervening
        # binary16 sample rounds the eventual UNORM8 store to code 1.
        np.testing.assert_array_equal(
            predicted[0, 0],
            np.asarray((0, 0, 1, 255), dtype=np.uint8),
        )

    def test_regular_base_producer_preserves_air_fma_order(
        self,
    ) -> None:
        texture = np.zeros((4, 4, CHANNELS), dtype=np.uint8)
        texture[..., 3] = 255
        texture[0:2, 0:2, 0] = 203
        texture[0:2, 2:4, 0] = 59
        texture[2:4, 0:2, 0] = 62
        texture[2:4, 2:4, 0] = 42
        source_rgba = texture[::-1, :, :][..., (2, 1, 0, 3)]
        predicted = replay_regular_base_producer_software(
            source_rgba
        )
        # Reversing the two AIR y-pairs produces code 92 here.
        np.testing.assert_array_equal(
            predicted[0, 0],
            np.asarray((91, 0, 0, 255), dtype=np.uint8),
        )

    def test_live_copy_base_applies_offset_then_clamp(self) -> None:
        source = np.arange(
            4 * 5 * CHANNELS,
            dtype=np.uint8,
        ).reshape(4, 5, CHANNELS)
        predicted = replay_live_copy_base_software(
            source,
            destination_width=5,
            destination_height=4,
            base_x=-1,
            base_y=-2,
            clamp=(0, 0, 3, 2),
        )
        expected_x = np.asarray((0, 0, 1, 2, 3))
        expected_y = np.asarray((0, 0, 0, 1))
        expected = source[
            expected_y[:, None],
            expected_x[None, :],
        ]
        np.testing.assert_array_equal(predicted, expected)

    def test_train_and_holdout_are_disjoint(self) -> None:
        train = _split_mask(holdout=False)
        holdout = _split_mask(holdout=True)
        self.assertFalse(bool(np.any(train & holdout)))
        self.assertEqual(
            int(np.count_nonzero(train)),
            int(np.count_nonzero(holdout)),
        )


if __name__ == "__main__":
    unittest.main()
