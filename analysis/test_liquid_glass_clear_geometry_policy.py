import unittest

import liquid_glass_clear_geometry_policy as clear_policy


class ClearGeometryPolicyTests(unittest.TestCase):
    def test_virtual_extent_is_recovered_from_position_uv_slope(
        self,
    ) -> None:
        vertices: list[clear_policy.Vertex] = [
            (
                112.0,
                912.0,
                0.0,
                1.0,
                -400.0,
                -400.0,
                8.0 / 896.0,
                808.0 / 896.0,
            ),
            (
                912.0,
                912.0,
                0.0,
                1.0,
                400.0,
                -400.0,
                808.0 / 896.0,
                808.0 / 896.0,
            ),
        ]
        extent, residual = clear_policy.inferred_virtual_axis(
            vertices,
            position_offset=0,
            uv_offset=6,
        )
        self.assertEqual(extent, 896)
        self.assertLess(residual, 1.0e-9)

    def test_alignment_reports_largest_supported_power_of_two(
        self,
    ) -> None:
        self.assertEqual(clear_policy.source_alignment(104), 8)
        self.assertEqual(clear_policy.source_alignment(-256), 256)
        self.assertEqual(clear_policy.source_alignment(896), 128)

    def test_clear_crop_law_transfers_to_offset_geometry(
        self,
    ) -> None:
        capture: clear_policy.JsonObject = {
            "geometry": {
                "width": 512,
                "height": 512,
                "windowWidth": 1024,
                "windowHeight": 1024,
            },
            "mainBounds": {
                "minimumX": 81.0,
                "minimumY": 349.0,
            },
        }
        self.assertEqual(
            clear_policy.clear_crop_from_geometry(capture),
            {
                "originX": 72,
                "virtualWidth": 640,
                "originY": 344,
                "virtualHeight": 640,
            },
        )

    def test_clear_crop_law_clamps_oversized_origin(
        self,
    ) -> None:
        capture: clear_policy.JsonObject = {
            "geometry": {
                "width": 1536,
                "height": 1536,
                "windowWidth": 1024,
                "windowHeight": 1024,
            },
            "mainBounds": {
                "minimumX": -256.0,
                "minimumY": -256.0,
            },
        }
        self.assertEqual(
            clear_policy.clear_crop_from_geometry(capture),
            {
                "originX": -8,
                "virtualWidth": 1152,
                "originY": -8,
                "virtualHeight": 1152,
            },
        )


if __name__ == "__main__":
    unittest.main()
