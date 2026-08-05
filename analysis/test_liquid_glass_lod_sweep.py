import unittest

import numpy as np

from liquid_glass_lod_sweep import (
    FLAT_BLUR_VALUES,
    FLAT_RIG,
    _expected_filter_values,
    _rig_configuration,
    _sampler_output_preimages,
    affine_segment_feasibility,
    float32_bits,
)


class LodSweepTests(unittest.TestCase):
    def test_flat_rig_requires_exact_profile_marker(self) -> None:
        manifest = {
            "rigVersion": FLAT_RIG,
            "sweepKind":
                "flat-blur-profile-phase-controlled-lod-curve",
            "flatBlurProfileInputs": FLAT_BLUR_VALUES,
        }
        self.assertTrue(_rig_configuration(manifest))
        manifest["flatBlurProfileInputs"] = {
            **FLAT_BLUR_VALUES,
            "inputBlurOpacity2": 0.5,
        }
        with self.assertRaises(ValueError):
            _rig_configuration(manifest)

    def test_flat_filter_values_include_identity_and_radius(self) -> None:
        values = _expected_filter_values(1.25, flat_profile=True)
        self.assertEqual(values["inputBlurRadius"], 1.25)
        self.assertEqual(values["inputBlurOpacity0"], 1)
        self.assertEqual(values["inputBlurOpacity4"], 1)
        self.assertEqual(values["inputInnerRefractionAmount"], 0)
        self.assertFalse(values["inputSDRHoldingToneEnabled"])

    def test_float32_bits_use_manifest_byte_order(self) -> None:
        self.assertEqual(float32_bits(1.0), "3f800000")

    def test_sampler_output_preimages_contain_code_centers(
        self,
    ) -> None:
        minimum, maximum = _sampler_output_preimages()
        codes = np.arange(256)
        self.assertTrue(np.all(minimum <= codes))
        self.assertTrue(np.all(codes <= maximum))

    def test_affine_constant_sequence_is_feasible(self) -> None:
        sequences = np.full((3, 65), 128, dtype=np.uint8)
        result = affine_segment_feasibility(sequences)
        self.assertTrue(result["allCompatible"])
        self.assertEqual(
            result["incompatibleSequenceOccurrences"],
            0,
        )

    def test_affine_linear_sequence_is_feasible(self) -> None:
        fractions = np.arange(65) / 64
        sequence = np.rint(
            32 * (1 - fractions) + 224 * fractions
        ).astype(np.uint8)
        result = affine_segment_feasibility(sequence[None, :])
        self.assertTrue(result["allCompatible"])

    def test_nonaffine_alternation_is_incompatible(self) -> None:
        sequence = np.where(
            np.arange(65) % 2 == 0,
            0,
            255,
        ).astype(np.uint8)
        result = affine_segment_feasibility(sequence[None, :])
        self.assertFalse(result["allCompatible"])
        self.assertEqual(
            result["incompatibleSequenceOccurrences"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
