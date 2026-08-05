import unittest

import numpy as np

from liquid_glass_half_blend import (
    combined_prediction,
    half_fma,
    half_round,
    hash32,
    unorm8,
)


class HalfBlendTests(unittest.TestCase):
    def test_hash_matches_scalar_reference(self) -> None:
        inputs = np.asarray(
            (0, 1, 0x12345678, 0xFFFFFFFF),
            dtype=np.uint32,
        )
        expected: list[int] = []
        for source in inputs:
            value = int(source)
            value ^= value >> 16
            value = (value * 0x7FEB352D) & 0xFFFFFFFF
            value ^= value >> 15
            value = (value * 0x846CA68B) & 0xFFFFFFFF
            value ^= value >> 16
            expected.append(value)
        np.testing.assert_array_equal(
            hash32(inputs),
            np.asarray(expected, dtype=np.uint32),
        )

    def test_blend_rounds_each_binary16_boundary(self) -> None:
        source = np.asarray((0.25,), dtype=np.float16)
        alpha = np.asarray((0.125,), dtype=np.float16)
        destination = half_round(
            np.asarray((173 / 255,), dtype=np.float64)
        )
        factor = half_round(
            np.float64(1) - alpha.astype(np.float64)
        )
        blended = half_fma(destination, factor, source)
        expected = (
            destination.astype(np.float64)
            * factor.astype(np.float64)
            + source.astype(np.float64)
        ).astype(np.float16)
        np.testing.assert_array_equal(blended, expected)

    def test_unorm8_is_round_to_nearest(self) -> None:
        values = np.asarray(
            (0, 1 / 255, 127 / 255, 1, 2),
            dtype=np.float64,
        )
        np.testing.assert_array_equal(
            unorm8(values),
            np.asarray((0, 1, 127, 255, 255), dtype=np.uint8),
        )

    def test_combined_prediction_shape(self) -> None:
        prediction = combined_prediction(total_records=17)
        self.assertEqual(prediction.shape, (17, 4))
        np.testing.assert_array_equal(
            prediction[:, 0],
            prediction[:, 1],
        )
        np.testing.assert_array_equal(
            prediction[:, 0],
            prediction[:, 2],
        )
        np.testing.assert_array_equal(
            prediction[:, 3],
            np.full(17, 255, dtype=np.uint8),
        )


if __name__ == "__main__":
    unittest.main()
