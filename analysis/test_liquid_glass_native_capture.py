import unittest

import numpy as np

from liquid_glass_native_capture import (
    RECOVERED_MATRIX_BITS,
    decode_rgb8_records,
    recovered_half_face,
)


class NativeCaptureTest(unittest.TestCase):
    def test_decode_rgb8_records_preserves_record_order(self) -> None:
        result = decode_rgb8_records(
            bytes((1, 2, 3, 254, 253, 252)),
            expected_records=2,
        )
        np.testing.assert_array_equal(
            result,
            np.asarray(((1, 2, 3), (254, 253, 252))),
        )

    def test_decode_rgb8_records_rejects_wrong_length(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected 6"):
            decode_rgb8_records(
                bytes((1, 2, 3)),
                expected_records=2,
            )

    def test_recovered_half_face_is_deterministic(self) -> None:
        inputs = np.asarray(
            ((0, 0, 0), (128, 128, 128), (255, 255, 255)),
            dtype=np.int64,
        )
        first = recovered_half_face(inputs)
        second = recovered_half_face(inputs)
        np.testing.assert_array_equal(first, second)
        np.testing.assert_array_equal(
            first,
            np.asarray(((19, 19, 19), (152, 152, 152), (255, 255, 255))),
        )

    def test_recovered_matrix_binary16_bits(self) -> None:
        np.testing.assert_array_equal(
            RECOVERED_MATRIX_BITS,
            np.asarray(
                (
                    (15425, 8564, 5314),
                    (6795, 15432, 5207),
                    (6763, 8581, 15423),
                ),
                dtype=np.uint16,
            ),
        )


if __name__ == "__main__":
    unittest.main()
