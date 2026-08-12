import hashlib
import unittest

from liquid_glass_transition_matrix import (
    APPLE_MATRIX_BASIS_BYTES,
    APPLE_MATRIX_BASIS_SHA256,
    concatenate_color_matrices,
    expected_matrix_field_bits,
    packed_matrix_half_words,
)


NEUTRAL_ROW_BITS = (
    ("0x3c00", "0x84dc", "0x04dc", "0x0000"),
    ("0x0059", "0x3c00", "0x8170", "0x0000"),
    ("0x8364", "0x0365", "0x3c00", "0x0000"),
)


def _neutral_values() -> dict[str, object]:
    values: dict[str, object] = {
        "inputSDRShadowOpacity": 0.0,
    }
    for prefix in (
        "inputFaceColorMatrix",
        "inputBleedColorMatrix",
        "inputShadowColorMatrix",
    ):
        values[f"{prefix}White"] = 1.0
        values[f"{prefix}Black"] = 0.0
        values[f"{prefix}Saturation"] = 1.0
        values[f"{prefix}FillColor"] = None
    return values


class TransitionMatrixTests(unittest.TestCase):
    def test_basis_bytes_have_the_captured_digest(self) -> None:
        self.assertEqual(len(APPLE_MATRIX_BASIS_BYTES), 160)
        self.assertEqual(
            hashlib.sha256(APPLE_MATRIX_BASIS_BYTES).hexdigest(),
            APPLE_MATRIX_BASIS_SHA256,
        )

    def test_neutral_axes_preserve_measured_residual_bits(
        self,
    ) -> None:
        expected = expected_matrix_field_bits(_neutral_values())
        for prefix in ("face", "bleed", "shadow"):
            rows = tuple(
                tuple(expected[f"{prefix}_matrix_{row}"])
                for row in range(3)
            )
            self.assertEqual(rows, NEUTRAL_ROW_BITS)

    def test_fill_is_premultiplied_source_over(self) -> None:
        values = _neutral_values()
        values["inputFaceColorMatrixFillColor"] = {
            "components": [0.25, 0.5, 0.75, 0.25],
        }
        expected = expected_matrix_field_bits(values)
        self.assertEqual(expected["face_matrix_0"][3], "0x2c00")
        self.assertEqual(expected["face_matrix_1"][3], "0x3000")
        self.assertEqual(expected["face_matrix_2"][3], "0x3200")

    def test_sdr_shadow_opacity_adds_transparent_black(
        self,
    ) -> None:
        values = _neutral_values()
        values["inputSDRShadowOpacity"] = 0.5
        expected = expected_matrix_field_bits(values)
        self.assertEqual(expected["shadow_matrix_0"][0], "0x3800")
        self.assertEqual(expected["shadow_matrix_1"][1], "0x3800")
        self.assertEqual(expected["shadow_matrix_2"][2], "0x3800")
        self.assertEqual(expected["shadow_matrix_0"][3], "0x0000")

    def test_concatenation_rejects_partial_matrices(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 20"):
            concatenate_color_matrices((1.0,), (1.0,) * 20)
        with self.assertRaisesRegex(ValueError, "exactly 20"):
            packed_matrix_half_words((1.0,))


if __name__ == "__main__":
    unittest.main()
