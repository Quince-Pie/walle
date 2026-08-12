import unittest

from liquid_glass_transition_uniforms import (
    expected_geometry_field_bits,
    expected_field_bits,
    float32_bits,
    half_bits,
)


class TransitionUniformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.values = {
            "inputInnerRefractionAmount": -30.0,
            "inputInnerRefractionHeight": 10.0,
            "inputOuterRefractionAmount": 80.0,
            "inputOuterRefractionHeight": 50.0,
            "inputRefractionDistance0": -0.5,
            "inputRefractionDistance1": 0.0,
            "inputRefractionOpacity": 0.0,
            "inputBlurRadius": 0.5,
            "inputBlurOpacity0": 0.5,
            "inputBlurOpacity1": 0.2,
            "inputBlurOpacity2": 0.2,
            "inputBlurOpacity3": 0.4,
            "inputBlurDistance0": -200.0,
            "inputBlurDistance1": -0.5,
            "inputBlurDistance2": 0.0,
            "inputBlurDistance3": 0.0,
            "inputBleedBlurRadius": 0.0,
            "inputBleedAmount": 0.0,
            "inputBleedHeight": 0.0,
            "inputBleedDistance0": 0.5,
            "inputBleedDistance1": 0.0,
            "inputBleedOpacity": 0.0,
            "inputBleedDarkenBlend": True,
            "inputShadowAmount": 37.5,
            "inputShadowHeight": 160.0,
            "inputShadowOffset": {
                "description": "NSSize: {0, 8}",
            },
            "inputShadowBlurRadius": 0.0,
            "inputShadowRadius": 0.0,
            "inputShadowDistanceOffset": 0.0,
            "inputShadowOpacity": 0.0,
            "inputShadowVibrancyContribution": 0.0,
            "inputShadowColorMatrixFillColor": None,
            "inputSDRShadowOpacity": 0.125,
            "inputFaceColorMatrixWhite": 1.075,
            "inputFaceOpacity": 0.5,
            "inputSDRHoldingToneEnabled": True,
            "inputSDRHoldingToneWhite": 0.985,
            "inputSDRGradientDistance0": -1.0,
            "inputSDRGradientDistance1": -0.5,
            "inputClampPreserveHue": False,
            "inputClamp": 123.0,
        }

    def test_clamp_interpolates_compiled_endpoint_not_input_clamp(
        self,
    ) -> None:
        expected = expected_field_bits(
            "clear",
            "light",
            self.values,
        )
        self.assertEqual(
            expected["clamp_limit"],
            [half_bits(1.078125)],
        )
        self.assertNotEqual(
            expected["clamp_limit"],
            [half_bits(1.075)],
        )
        self.assertNotEqual(
            expected["clamp_limit"],
            [half_bits(123.0)],
        )

    def test_clear_blur_and_inverse_rules(self) -> None:
        expected = expected_field_bits(
            "clear",
            "dark",
            self.values,
        )
        self.assertEqual(
            expected["blur_radius"],
            [float32_bits(0.4)],
        )
        self.assertEqual(
            expected["inner_refraction_inverse_height"],
            [float32_bits(0.1)],
        )
        self.assertEqual(
            expected["edge_bleed_inverse_height"],
            [float32_bits(float("inf"))],
        )

    def test_regular_blur_scaling_and_blur_differences(self) -> None:
        self.values["inputBlurRadius"] = 2.0
        expected = expected_field_bits(
            "regular",
            "light",
            self.values,
        )
        self.assertEqual(
            expected["blur_radius"],
            [float32_bits(0.8)],
        )
        self.assertEqual(
            expected["blur_alpha"],
            [
                half_bits(0.5),
                half_bits(0.3),
                half_bits(0.0),
                half_bits(-0.2),
            ],
        )

    def test_boolean_vectors_and_y_inversion_are_explicit(self) -> None:
        expected = expected_field_bits(
            "clear",
            "light",
            self.values,
        )
        self.assertEqual(
            expected["bleed_darken"],
            [half_bits(1.0), half_bits(0.0)],
        )
        self.assertEqual(
            expected["shadow_offset"],
            [float32_bits(0.0), float32_bits(-8.0)],
        )
        self.assertEqual(
            expected["shadow_face_opacity"],
            [float32_bits(0.125)],
        )
        self.values["inputShadowColorMatrixFillColor"] = {
            "alpha": 0.25,
        }
        expected = expected_field_bits(
            "clear",
            "light",
            self.values,
        )
        self.assertEqual(
            expected["shadow_face_opacity"],
            [float32_bits(0.375)],
        )
        self.values["inputBleedDarkenBlend"] = False
        expected = expected_field_bits(
            "clear",
            "light",
            self.values,
        )
        self.assertEqual(
            expected["bleed_darken"],
            [half_bits(-1.0), half_bits(1.0)],
        )

    def test_sdf_and_displacement_follow_measured_extents(
        self,
    ) -> None:
        render = {
            "metalCommandProvenance": {
                "records": [{
                    "kind": "texture",
                    "stage": "compute",
                    "index": 1,
                    "pipeline": {
                        "label": (
                            "com.apple.coreanimation."
                            "variable_blur_copy_base_mip_compute"
                        ),
                    },
                    "texture": {
                        "width": 320,
                        "height": 384,
                    },
                }],
            },
        }
        expected = expected_geometry_field_bits(
            "regular",
            {"width": 800, "height": 640},
            render,
        )
        self.assertEqual(expected["sdf_arg"], [
            float32_bits(400.0),
            float32_bits(320.0),
            float32_bits(4.0),
            float32_bits(0.5),
        ])
        self.assertEqual(expected["sdf_transform"], [
            float32_bits(1.0),
            float32_bits(0.0),
            float32_bits(0.0),
            float32_bits(1.0),
        ])
        self.assertEqual(expected["sdf_arg2"], [
            float32_bits(1.0),
            float32_bits(1.0),
            float32_bits(320.0),
            float32_bits(0.0),
        ])
        self.assertEqual(expected["displacement_matrix"], [
            float32_bits(1.0 / 1280),
            float32_bits(0.0),
            float32_bits(0.0),
            float32_bits(-1.0 / 1536),
        ])
        clear = expected_geometry_field_bits(
            "clear",
            {"width": 800, "height": 640},
            render,
        )
        self.assertEqual(clear["displacement_matrix"], [
            float32_bits(1.0 / 640),
            float32_bits(0.0),
            float32_bits(0.0),
            float32_bits(-1.0 / 768),
        ])


if __name__ == "__main__":
    unittest.main()
