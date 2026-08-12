import unittest

import numpy as np

from liquid_glass_sdf_threshold import (
    FIRST_LOWER_HALF_BITS,
    LAST_LOWER_HALF_BITS,
    LOWER_HALF_BITS,
    STATE_COUNT,
    _decode_transitions,
    _expected_filter_values,
    _expected_state,
    _float16_from_bits,
    _source_code,
)


class ThresholdDesignTests(unittest.TestCase):
    def test_traversal_brackets_the_protected_field(self) -> None:
        self.assertEqual(STATE_COUNT, 515)
        self.assertEqual(
            int(LOWER_HALF_BITS[0]),
            FIRST_LOWER_HALF_BITS,
        )
        self.assertEqual(
            int(LOWER_HALF_BITS[-1]),
            LAST_LOWER_HALF_BITS,
        )
        self.assertEqual(
            float(_float16_from_bits(FIRST_LOWER_HALF_BITS)),
            -400.25,
        )
        self.assertEqual(
            float(_float16_from_bits(LAST_LOWER_HALF_BITS)),
            -271.75,
        )

    def test_breakpoints_are_adjacent_binary16_values(self) -> None:
        for index in (0, 1, 257, STATE_COUNT - 1):
            lower_bits = int(LOWER_HALF_BITS[index])
            state = _expected_state(index, lower_bits)
            values = _expected_filter_values(lower_bits)

            self.assertEqual(
                state["upperDistanceFloat16Bits"],
                f"{lower_bits - 1:04x}",
            )
            self.assertLess(
                values["inputBlurDistance0"],
                values["inputBlurDistance1"],
            )
            self.assertEqual(
                [
                    values[f"inputBlurOpacity{opacity}"]
                    for opacity in range(5)
                ],
                [0, 1, 1, 1, 1],
            )

    def test_periodic_hash_source_is_bounded_and_repeats(self) -> None:
        samples = [
            _source_code(x, y, channel)
            for y in range(64)
            for x in range(64)
            for channel in range(3)
        ]

        self.assertGreaterEqual(min(samples), 16)
        self.assertLessEqual(max(samples), 239)
        self.assertEqual(_source_code(7, 11, 2), 22)
        self.assertEqual(
            _source_code(7, 11, 2),
            _source_code(71, 75, 2),
        )


class TransitionDecoderTests(unittest.TestCase):
    def test_recovers_single_binary_transitions(self) -> None:
        bits = np.asarray(
            [0xDE41, 0xDE40, 0xDE3F, 0xDE3E, 0xDE3D],
            dtype=np.uint16,
        )
        first = np.asarray(
            [[10, 20, 30], [40, 50, 60]],
            dtype=np.uint8,
        )
        last = np.asarray(
            [[11, 22, 33], [44, 55, 66]],
            dtype=np.uint8,
        )
        curve = np.empty((5, 2, 3), dtype=np.uint8)
        curve[:, 0] = np.vstack((first[0], last[0], last[0], last[0], last[0]))
        curve[:, 1] = np.vstack((first[1], first[1], first[1], last[1], last[1]))

        decoded = _decode_transitions(curve, bits)

        self.assertTrue(np.all(decoded.binary_monotonic))
        self.assertEqual(
            decoded.transition_index.tolist(),
            [1, 3],
        )
        self.assertEqual(
            decoded.field_half_bits.tolist(),
            [0xDE40, 0xDE3E],
        )

    def test_rejects_equal_endpoints_and_intermediate_values(self) -> None:
        bits = np.asarray(
            [0xDE41, 0xDE40, 0xDE3F],
            dtype=np.uint16,
        )
        curve = np.asarray(
            [
                [[1, 2, 3], [4, 5, 6]],
                [[1, 2, 3], [7, 8, 9]],
                [[1, 2, 3], [10, 11, 12]],
            ],
            dtype=np.uint8,
        )

        decoded = _decode_transitions(curve, bits)

        self.assertFalse(decoded.binary_monotonic[0])
        self.assertFalse(decoded.endpoint_discriminating[0])
        self.assertFalse(decoded.binary_monotonic[1])
        self.assertEqual(decoded.intermediate_state_count[1], 1)
        self.assertEqual(decoded.field_half_bits.tolist(), [0xFFFF, 0xFFFF])


if __name__ == "__main__":
    unittest.main()
