import unittest

import numpy as np

from liquid_glass_half_dot import (
    half_add,
    half_fma,
    half_multiply,
    half_values,
    rgb_ordered_fma_dot,
    separate_multiply_add_dot,
)


class HalfDotTests(unittest.TestCase):
    def test_half_values_preserve_binary16_bits(self) -> None:
        bits = np.asarray((0x0000, 0x2CCD, 0x3C00), dtype=np.uint16)
        np.testing.assert_array_equal(
            half_values(bits).view(np.uint16),
            bits,
        )

    def test_fma_rounds_once(self) -> None:
        left = np.asarray((-0.18603515625,), dtype=np.float16)
        right = np.float16(-1.4638671875)
        accumulator = np.asarray((-0.387451171875,), dtype=np.float16)
        fused = half_fma(left, right, accumulator)
        separate = half_add(
            half_multiply(left, right),
            accumulator,
        )
        self.assertNotEqual(
            int(fused.view(np.uint16)[0]),
            int(separate.view(np.uint16)[0]),
        )

    def test_rgb_dot_uses_rgb_fma_order(self) -> None:
        inputs = np.asarray(
            ((0.25, 0.5, 0.75),),
            dtype=np.float16,
        )
        matrix = np.asarray(
            ((1.0634765625, 0.0107269287, 0.0010833740),),
            dtype=np.float16,
        )
        expected = np.zeros(1, dtype=np.float16)
        for channel in range(3):
            expected = half_fma(
                inputs[:, channel],
                matrix[0, channel],
                expected,
            )
        np.testing.assert_array_equal(
            rgb_ordered_fma_dot(inputs, matrix)[:, 0],
            expected,
        )

    def test_separate_multiply_add_is_observably_different(self) -> None:
        inputs = np.asarray(
            ((1 / 255, 1 / 255, 0),),
            dtype=np.float16,
        )
        matrix = np.asarray(
            ((0.0031890869, 1.0703125, 0.0010833740),),
            dtype=np.float16,
        )
        fused = rgb_ordered_fma_dot(inputs, matrix)
        separate = separate_multiply_add_dot(inputs, matrix)
        self.assertFalse(np.array_equal(fused, separate))


if __name__ == "__main__":
    unittest.main()
