import unittest

import liquid_glass_geometry_policy as policy


class GeometryPolicyTests(unittest.TestCase):
    def test_half_away_handles_signed_offsets(self) -> None:
        self.assertEqual(policy.round_half_away(90.5), 91)
        self.assertEqual(policy.round_half_away(-90.5), -91)

    def test_snap_candidates_apply_to_the_frame_origin(
        self,
    ) -> None:
        candidates = policy.snap_candidates(
            602.5,
            width=640,
        )
        self.assertEqual(candidates["frame-origin-nearest-away"], 283)
        self.assertEqual(candidates["frame-origin-nearest-even"], 282)
        self.assertEqual(
            candidates[
                "frame-origin-nearest-ties-positive-infinity"
            ],
            283,
        )

    def test_negative_frame_origin_distinguishes_tie_rules(self) -> None:
        candidates = policy.snap_candidates(
            512,
            width=1535,
        )
        self.assertEqual(candidates["frame-origin-nearest-away"], -256)
        self.assertEqual(candidates["frame-origin-nearest-even"], -256)
        self.assertEqual(
            candidates[
                "frame-origin-nearest-ties-positive-infinity"
            ],
            -255,
        )
        self.assertEqual(candidates["frame-origin-ceil"], -255)

    def test_source_origin_is_recovered_from_vertex_uvs(self) -> None:
        virtual_width = 1536
        virtual_height = 1280
        origin_x = -256
        origin_y = 0
        positions = (
            (112.0, 912.0),
            (912.0, 912.0),
            (912.0, 112.0),
            (912.0, 112.0),
            (112.0, 112.0),
            (112.0, 912.0),
        )
        vertices: list[policy.Vertex] = [
            (
                x,
                y,
                0.0,
                1.0,
                0.0,
                0.0,
                (x - origin_x) / virtual_width,
                (y - origin_y) / virtual_height,
            )
            for x, y in positions
        ]
        recovered = policy.recover_source_origin(
            vertices,
            virtual_width=virtual_width,
            virtual_height=virtual_height,
        )
        self.assertEqual(recovered[:2], (origin_x, origin_y))
        self.assertLess(recovered[2], 1.0e-9)

    def test_near_integral_vertices_tolerate_float_noise(self) -> None:
        rounded, residual = policy.rounded_integral(
            -1.3322676295501878e-14
        )
        self.assertEqual(rounded, 0)
        self.assertGreater(residual, 0.0)
        with self.assertRaises(ValueError):
            policy.rounded_integral(0.25)

    def test_regular_expansion_uses_unsnapped_requested_bounds(
        self,
    ) -> None:
        geometry: policy.JsonObject = {
            "centerX": 512,
            "centerY": 512,
            "width": 511,
            "height": 511,
            "windowWidth": 1024,
            "windowHeight": 1024,
        }
        expanded = policy.expanded_source_bounds(
            geometry,
            margin=178.85000610351562,
        )
        self.assertEqual(
            expanded,
            {
                "minimumX": 76,
                "minimumY": 76,
                "maximumX": 948,
                "maximumY": 948,
            },
        )

    def test_crop_padding_candidates_are_in_downsample_pixels(
        self,
    ) -> None:
        capture: policy.JsonObject = {
            "glassPath": "regular-edge-bleed",
            "downsample": {
                "sourceBounds": {
                    "minimumX": 0.0,
                    "minimumY": 0.0,
                    "maximumX": 776.0,
                    "maximumY": 776.0,
                },
            },
            "sourceCrop": {
                "originX": -256,
                "originY": -256,
                "virtualWidth": 1280,
                "virtualHeight": 1280,
            },
        }
        candidates = policy.crop_padding_candidates(
            capture,
            axis="X",
        )
        self.assertNotIn(0, candidates)
        self.assertIn(1, candidates)
        self.assertIn(62, candidates)
        self.assertNotIn(63, candidates)

    def test_viewport_extent_aligns_scissor_and_caps_at_256(
        self,
    ) -> None:
        expected = {
            1: 64,
            63: 64,
            64: 64,
            65: 128,
            128: 128,
            129: 192,
            192: 192,
            193: 256,
            256: 256,
            300: 256,
        }
        self.assertEqual(
            {
                extent: policy.viewport_extent_for_scissor(extent)
                for extent in expected
            },
            expected,
        )

    def test_geometry_crop_model_preserves_odd_axis_asymmetry(
        self,
    ) -> None:
        capture: policy.JsonObject = {
            "glassPath": "regular-edge-bleed",
            "geometry": {
                "windowWidth": 1024,
                "windowHeight": 1024,
            },
            "mainBounds": {
                "minimumX": 341.0,
                "minimumY": 340.0,
                "maximumX": 684.0,
                "maximumY": 683.0,
            },
            "downsample": {
                "regularProfile": {
                    "edgeBleedAmount": 120.05000305175781,
                },
            },
        }
        self.assertEqual(
            policy.regular_crop_from_geometry(
                capture,
                padding=220,
            ),
            {
                "originX": 0,
                "virtualWidth": 1280,
                "originY": -256,
                "virtualHeight": 1280,
            },
        )

    def test_no_bleed_crop_model_preserves_odd_axis_asymmetry(
        self,
    ) -> None:
        capture: policy.JsonObject = {
            "glassPath": "no-bleed",
            "geometry": {
                "width": 63,
                "height": 63,
            },
            "downsample": {
                "scissor": {
                    "width": 38,
                    "height": 38,
                },
            },
            "mainBounds": {
                "minimumX": 481.0,
                "minimumY": 480.0,
            },
        }
        self.assertEqual(
            policy.no_bleed_crop_from_geometry(capture),
            {
                "originX": 464,
                "virtualWidth": 256,
                "originY": 448,
                "virtualHeight": 256,
            },
        )

    def test_no_bleed_lower_regime_transfers_to_offset_axis(
        self,
    ) -> None:
        capture: policy.JsonObject = {
            "glassPath": "no-bleed",
            "geometry": {
                "width": 24,
                "height": 24,
            },
            "downsample": {
                "scissor": {
                    "width": 24,
                    "height": 24,
                },
            },
            "mainBounds": {
                "minimumX": 325.0,
                "minimumY": 593.0,
            },
        }
        self.assertEqual(
            policy.no_bleed_crop_from_geometry(capture),
            {
                "originX": 320,
                "virtualWidth": 256,
                "originY": 576,
                "virtualHeight": 256,
            },
        )

    def test_small_regular_crop_rounds_extent_after_origin(
        self,
    ) -> None:
        capture: policy.JsonObject = {
            "glassPath": "regular-edge-bleed",
            "geometry": {
                "windowWidth": 1024,
                "windowHeight": 1024,
            },
            "mainBounds": {
                "minimumX": 397.0,
                "minimumY": 464.0,
                "maximumX": 493.0,
                "maximumY": 560.0,
            },
            "downsample": {
                "regularProfile": {
                    "edgeBleedAmount": 33.599998474121094,
                },
            },
        }
        self.assertEqual(
            policy.small_regular_crop_from_geometry(
                capture,
                padding=127,
            ),
            {
                "originX": 128,
                "virtualWidth": 768,
                "originY": 256,
                "virtualHeight": 512,
            },
        )

    def test_no_bleed_selector_can_be_axis_specific(self) -> None:
        capture: policy.JsonObject = {
            "glassPath": "no-bleed",
            "geometry": {
                "width": 47,
                "height": 47,
            },
            "downsample": {
                "scissor": {
                    "width": 32,
                    "height": 33,
                },
            },
            "mainBounds": {
                "minimumX": 314.0,
                "minimumY": 581.0,
            },
        }
        self.assertEqual(
            policy.no_bleed_crop_from_geometry(capture),
            {
                "originX": 304,
                "virtualWidth": 256,
                "originY": 560,
                "virtualHeight": 256,
            },
        )

    def test_no_bleed_phase_discriminates_tier_selectors(self) -> None:
        capture: policy.JsonObject = {
            "glassPath": "no-bleed",
            "geometry": {
                "width": 47,
                "height": 47,
            },
            "downsample": {
                "scissor": {
                    "width": 33,
                    "height": 33,
                },
            },
            "mainBounds": {
                "minimumX": 490.0,
                "minimumY": 490.0,
            },
        }
        self.assertEqual(
            policy.no_bleed_crop_from_geometry(
                capture,
                tier_selector="scissor-33",
            )["originX"],
            464,
        )
        self.assertEqual(
            policy.no_bleed_crop_from_geometry(
                capture,
                tier_selector="diameter-48",
            )["originX"],
            480,
        )


if __name__ == "__main__":
    unittest.main()
