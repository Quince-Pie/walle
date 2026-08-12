import struct
import unittest

from liquid_glass_geometry_transfer import (
    _expected_main_vertices,
    _expected_profile_bits,
    _expected_shadow_vertices,
    _vertices,
    _vertices_exact,
)


class LiquidGlassGeometryTransferTests(unittest.TestCase):
    def test_fractional_center_is_snapped_before_y_inversion(self) -> None:
        geometry = {
            "centerX": 602.25,
            "centerY": 377.75,
            "width": 640,
            "height": 640,
            "windowWidth": 1024,
            "windowHeight": 1024,
        }
        vertices, snapping = _expected_main_vertices(
            geometry,
            source_origin_x=-256,
            source_origin_y=-256,
            virtual_width=1536,
            virtual_height=1536,
        )
        self.assertEqual(snapping["snappedSwiftUICenter"], [602, 378])
        self.assertEqual(snapping["metalCenter"], [602, 646])
        self.assertEqual(vertices[0][0:2], (282.0, 966.0))
        self.assertEqual(vertices[2][0:2], (922.0, 326.0))

    def test_shadow_expansion_is_asymmetric_in_y(self) -> None:
        geometry = {
            "centerX": 512,
            "centerY": 512,
            "width": 256,
            "height": 256,
            "windowWidth": 1024,
            "windowHeight": 1024,
        }
        main, _ = _expected_main_vertices(
            geometry,
            source_origin_x=0,
            source_origin_y=0,
            virtual_width=1024,
            virtual_height=1024,
        )
        shadow = _expected_shadow_vertices(
            main,
            source_origin_x=0,
            source_origin_y=0,
            virtual_width=1024,
            virtual_height=1024,
        )
        self.assertEqual(shadow[0][0:2], (336.0, 680.0))
        self.assertEqual(shadow[-1][0:2], (688.0, 328.0))

    def test_profile_rules_preserve_exact_float_bits(self) -> None:
        expected = _expected_profile_bits(
            half_width=448,
            half_height=448,
            virtual_width=1536,
            virtual_height=1536,
        )
        self.assertEqual(
            expected["edge_bleed_inverse_height"],
            ["0x3b50fac7"],
        )
        self.assertEqual(
            expected["outer_refraction_amount"],
            ["0x43333333"],
        )
        self.assertEqual(
            expected["blur_distance"][0],
            "0xdf00",
        )

    def test_vertex_decoder_obeys_captured_stride(self) -> None:
        first = (1.0, 2.0, 0.0, 1.0, -3.0, -4.0, 0.25, 0.5)
        second = (5.0, 6.0, 0.0, 1.0, 7.0, 8.0, 0.75, 1.0)
        payload = (
            struct.pack("<8f", *first)
            + bytes(16)
            + struct.pack("<8f", *second)
            + bytes(16)
        )
        snapshot = {"payload": {"hex": payload.hex()}}
        decoded = _vertices(snapshot, 2)
        self.assertTrue(_vertices_exact(decoded, [first, second]))


if __name__ == "__main__":
    unittest.main()
