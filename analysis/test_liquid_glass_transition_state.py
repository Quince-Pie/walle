import math
import unittest

from liquid_glass_transition_state import (
    expected_background_inputs,
    expected_boolean_inputs,
    expected_clamp,
    expected_color_inputs,
    expected_foreground_inputs,
    expected_geometry,
    extended_srgb_to_linear,
    parse_rect,
)


class TransitionStateTests(unittest.TestCase):
    def test_rect_parser_preserves_fractional_geometry(self) -> None:
        self.assertEqual(
            parse_rect(
                "{{-204.00896453857419, -204.00896453857419}, "
                "{808.01792907714844, 808.01792907714844}}"
            ),
            (
                -204.00896453857419,
                -204.00896453857419,
                808.0179290771484,
                808.0179290771484,
            ),
        )

    def test_geometry_uses_material_scalar_and_pixel_snapped_inset(
        self,
    ) -> None:
        remaining = 0.49887943267822266
        progress = 1 - remaining
        expected = expected_geometry(
            diameter=800,
            center_x=512,
            center_y=512,
            remaining=remaining,
        )
        self.assertEqual(
            expected["outerWidth"],
            800 * remaining,
        )
        self.assertEqual(
            expected["outerOriginX"],
            512 - 400 * remaining,
        )
        self.assertEqual(
            expected["effectWidth"],
            800 + 16 * progress,
        )
        self.assertEqual(
            expected["effectOriginX"],
            -round(400 * progress) - 8 * progress,
        )

    def test_offset_geometry_uses_window_pivot_and_snapped_center(
        self,
    ) -> None:
        remaining = 0.5
        expected = expected_geometry(
            diameter=640,
            center_x=602.25,
            center_y=377.75,
            remaining=remaining,
            window_center_x=512,
            window_center_y=512,
        )
        self.assertEqual(expected["outerOriginX"], 352)
        self.assertEqual(expected["outerOriginY"], 352)
        self.assertEqual(
            expected["effectOriginX"],
            602 - 512 - 160 - 4,
        )
        self.assertEqual(
            expected["effectOriginY"],
            378 - 512 - 160 - 4,
        )

    def test_foreground_is_exactly_linear_in_removed_fraction(
        self,
    ) -> None:
        progress = 0.37
        expected = expected_foreground_inputs(progress)
        self.assertEqual(
            expected["inputEdgeOpacityEnd"],
            progress,
        )
        self.assertEqual(
            expected["inputAberrationAmount"],
            -5 * progress,
        )
        self.assertEqual(
            expected["inputAberrationAngle"],
            math.pi * progress / 2,
        )
        self.assertEqual(
            expected["inputRefractionHeight"],
            16 * progress,
        )

    def test_regular_light_distances_use_expanded_effect_extent(
        self,
    ) -> None:
        remaining = 0.25
        progress = 0.75
        extent = 800 + 16 * progress
        expected = expected_background_inputs(
            material="regular",
            appearance="light",
            diameter=800,
            remaining=remaining,
        )
        self.assertEqual(
            expected["inputBlurDistance0"],
            -0.5 * extent * remaining,
        )
        self.assertEqual(
            expected["inputOuterRefractionAmount"],
            0.2 * extent * remaining,
        )
        self.assertEqual(
            expected["inputBleedAmount"],
            0.35 * extent * remaining,
        )
        self.assertEqual(
            expected["inputBlurOpacity3"],
            0.4 * remaining + 0.6 * remaining**2,
        )

    def test_dark_and_light_regular_profiles_are_not_conflated(
        self,
    ) -> None:
        light = expected_background_inputs(
            material="regular",
            appearance="light",
            diameter=800,
            remaining=1,
        )
        dark = expected_background_inputs(
            material="regular",
            appearance="dark",
            diameter=800,
            remaining=1,
        )
        self.assertEqual(
            light["inputFaceColorMatrixBlack"],
            0.5,
        )
        self.assertEqual(
            dark["inputFaceColorMatrixBlack"],
            0.2,
        )
        self.assertEqual(
            light["inputFaceColorMatrixWhite"],
            1.03,
        )
        self.assertEqual(
            dark["inputFaceColorMatrixWhite"],
            0.6,
        )

    def test_clamp_is_extended_srgb_face_white_transfer(self) -> None:
        clear = expected_background_inputs(
            material="clear",
            appearance="light",
            diameter=800,
            remaining=1,
        )
        self.assertAlmostEqual(
            clear["inputClamp"],
            1.375824,
            places=6,
        )
        self.assertEqual(expected_clamp(0.6), 1)
        self.assertEqual(
            expected_clamp(1.15),
            extended_srgb_to_linear(1.15),
        )

    def test_exact_color_laws_preserve_profile_semantics(
        self,
    ) -> None:
        clear = expected_color_inputs(
            material="clear",
            appearance="light",
            remaining=0.5,
        )
        self.assertIsNone(
            clear["inputFaceColorMatrixFillColor"]
        )
        self.assertEqual(
            clear["inputShadowColorMatrixFillColor"],
            (0, 0, 0, 0.05),
        )
        regular_light = expected_color_inputs(
            material="regular",
            appearance="light",
            remaining=0.5,
        )
        self.assertEqual(
            regular_light["inputFaceColorMatrixFillColor"],
            (1, 1, 1, 0.2),
        )
        self.assertEqual(
            regular_light["inputShadowColorMatrixFillColor"],
            (0, 0, 0, 0.06),
        )
        regular_dark = expected_color_inputs(
            material="regular",
            appearance="dark",
            remaining=0.5,
        )
        self.assertEqual(
            regular_dark["inputFaceColorMatrixFillColor"],
            (0, 0, 0, 0.2),
        )
        self.assertIsNone(
            regular_dark["inputShadowColorMatrixFillColor"]
        )

    def test_clear_dark_boolean_switches_at_half_material(
        self,
    ) -> None:
        below = expected_boolean_inputs(
            material="clear",
            appearance="dark",
            remaining=0.499999,
        )
        at_half = expected_boolean_inputs(
            material="clear",
            appearance="dark",
            remaining=0.5,
        )
        self.assertFalse(below["inputBleedDarkenBlend"])
        self.assertTrue(at_half["inputBleedDarkenBlend"])
        self.assertFalse(at_half["inputClampPreserveHue"])
        self.assertTrue(at_half["inputSDRHoldingToneEnabled"])


if __name__ == "__main__":
    unittest.main()
