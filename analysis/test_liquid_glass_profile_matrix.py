import struct
import unittest

from liquid_glass_profile_matrix import (
    GLASS_END,
    _changed_fields,
    _glass_uniform_snapshots,
    decode_profile,
)


class LiquidGlassProfileMatrixTests(unittest.TestCase):
    def test_decode_preserves_float_and_half_bits(self) -> None:
        payload = bytearray(GLASS_END)
        struct.pack_into("<f", payload, 64, -60.0)
        struct.pack_into("<H", payload, 230, 0x3C00)
        decoded = decode_profile(bytes(payload))
        self.assertEqual(
            decoded["fields"]["inner_refraction_amount"],
            {
                "values": [-60.0],
                "bits": ["0xc2700000"],
            },
        )
        self.assertEqual(
            decoded["fields"]["face_opacity"],
            {
                "values": [1.0],
                "bits": ["0x3c00"],
            },
        )

    def test_decode_serializes_infinity_without_nonstandard_json(
        self,
    ) -> None:
        payload = bytearray(GLASS_END)
        struct.pack_into("<I", payload, 100, 0x7F800000)
        decoded = decode_profile(bytes(payload))
        self.assertEqual(
            decoded["fields"]["edge_bleed_inverse_height"][
                "values"
            ],
            ["Infinity"],
        )

    def test_changed_fields_uses_exact_encodings(self) -> None:
        baseline_payload = bytearray(GLASS_END)
        candidate_payload = bytearray(GLASS_END)
        struct.pack_into("<H", baseline_payload, 230, 0x0000)
        struct.pack_into("<H", candidate_payload, 230, 0x8000)
        baseline = decode_profile(bytes(baseline_payload))
        candidate = decode_profile(bytes(candidate_payload))
        self.assertEqual(
            _changed_fields(baseline, candidate),
            ["face_opacity"],
        )

    def test_snapshot_selector_uses_material_fragment(self) -> None:
        runtime = {
            "materialProfileEvidence": {"material": "regular"},
            "carendererEvidence": {
                "metalBufferSnapshots": {
                    "snapshots": [
                        {
                            "stage": "fragment",
                            "index": 1,
                            "pipeline": {
                                "creationDescriptor": {
                                    "fragmentFunction":
                                        "glass_background_sdf_lph",
                                },
                            },
                        },
                        {
                            "stage": "fragment",
                            "index": 1,
                            "pipeline": {
                                "creationDescriptor": {
                                    "fragmentFunction":
                                        "glass_background_sdf_no_bleed_lph",
                                },
                            },
                        },
                    ],
                },
            },
        }
        fragment, snapshots = _glass_uniform_snapshots(runtime)
        self.assertEqual(fragment, "glass_background_sdf_lph")
        self.assertEqual(len(snapshots), 1)


if __name__ == "__main__":
    unittest.main()
