import unittest

import numpy as np

from liquid_glass_face_stage import (
    face_matrix_float32,
    half_fused_multiply_add,
    luminance_chroma_float32,
    luminance_chroma_half_matrix,
    luminance_chroma_half_rgb_fma,
)


class FaceStageTests(unittest.TestCase):
    def test_identity_parameters_produce_identity_matrix(self) -> None:
        matrix, bias = face_matrix_float32(
            np.float32(0),
            np.float32(1),
            np.float32(1),
            np.float32(1),
        )
        np.testing.assert_array_equal(
            matrix,
            np.eye(3, dtype=np.float32),
        )
        self.assertEqual(bias, 0)

    def test_luminance_chroma_form_preserves_gray_structure(
        self,
    ) -> None:
        codes = np.repeat(
            np.arange(256, dtype=np.int64)[:, np.newaxis],
            3,
            axis=1,
        )
        predicted = luminance_chroma_float32(
            codes,
            np.float32(0),
            np.float32(1),
            np.float32(0),
            np.float32(1),
            np.float32(1),
        )
        np.testing.assert_array_equal(
            predicted[:, 0],
            predicted[:, 1],
        )
        np.testing.assert_array_equal(
            predicted[:, 0],
            predicted[:, 2],
        )

    def test_half_holding_path_matches_known_code_examples(self) -> None:
        codes = np.asarray(
            ((0, 1, 255), (36, 73, 109), (128, 192, 240)),
            dtype=np.int64,
        )
        predicted = luminance_chroma_half_matrix(
            codes,
            np.float32(0),
            np.float32(1),
            np.float32(1),
            np.float32(0.97),
            np.float32(1),
        )
        np.testing.assert_array_equal(
            predicted,
            np.asarray(
                ((0, 1, 247), (35, 71, 106), (124, 186, 233)),
                dtype=np.int64,
            ),
        )

    def test_half_fma_fuses_before_binary16_rounding(self) -> None:
        left = np.asarray((-0.18603515625,), dtype=np.float16)
        right = np.asarray((-1.4638671875,), dtype=np.float16)
        accumulator = np.asarray((-0.387451171875,), dtype=np.float16)
        fused = half_fused_multiply_add(
            left,
            right,
            accumulator,
        )
        separate = (
            (left * right).astype(np.float16)
            + accumulator
        ).astype(np.float16)
        self.assertNotEqual(
            int(fused.view(np.uint16)[0]),
            int(separate.view(np.uint16)[0]),
        )

    def test_rgb_ordered_fma_matches_measured_baseline_example(
        self,
    ) -> None:
        predicted = luminance_chroma_half_rgb_fma(
            np.asarray(((0, 73, 146),), dtype=np.int64),
            np.float32(0.075),
            np.float32(1.15),
            np.float32(1.06),
            np.float32(0.97),
            np.float32(1),
        )
        np.testing.assert_array_equal(
            predicted,
            np.asarray(((19, 95, 170),), dtype=np.int64),
        )


if __name__ == "__main__":
    unittest.main()
