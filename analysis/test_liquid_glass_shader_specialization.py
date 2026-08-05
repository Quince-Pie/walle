import unittest

import liquid_glass_shader_specialization as specialization


class ShaderSpecializationTests(unittest.TestCase):
    def test_exact_controls_are_compile_time_constants(self) -> None:
        source = "\n".join(
            f"uniform {data_type} {name};"
            for name, (data_type, _) in (
                specialization.EXACT_FINAL_CONSTANTS.items()
            )
        )

        result = specialization.specialize_exact_final_shader(source)

        self.assertNotIn("uniform", result)
        for name, (data_type, value) in (
            specialization.EXACT_FINAL_CONSTANTS.items()
        ):
            self.assertIn(
                f"const {data_type} {name} = {value};",
                result,
            )

    def test_arithmetic_barrier_is_not_specialized(self) -> None:
        self.assertNotIn(
            "ArithmeticBarrier",
            specialization.EXACT_FINAL_CONSTANTS,
        )

    def test_declaration_drift_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "expected exactly one shader declaration",
        ):
            specialization.specialize_exact_final_shader("")

    def test_amd_circle_specialization_freezes_clear_invariants(self) -> None:
        source = self._minimal_amd_source()

        result = specialization.specialize_amd_exact_circle_shader(
            source,
            material="clear",
        )

        for name, value in specialization.MATERIAL_CONSTANTS["clear"].items():
            self.assertIn(f"const float {name} = {value};", result)
        self.assertIn("vec3 shape = replay_profile_circle_sdf", result)
        self.assertIn("radial_inverse_length = inversesqrt(dot(point, point))", result)
        self.assertIn("shape.x, normal, 1.0", result)
        self.assertIn("packHalf2x16", result)
        self.assertIn("0.0\n    ));", result)
        self.assertIn("return replay_compute_mode4_sdf(point);", result)

    def test_regular_keeps_appearance_dependent_edge_bleed(self) -> None:
        result = specialization.specialize_amd_exact_circle_shader(
            self._minimal_amd_source(),
            material="regular",
        )

        self.assertIn("uniform float EdgeBleedOpacity;", result)
        self.assertIn("0.5\n    ));", result)

    def test_amd_specialization_rejects_unknown_material(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported glass material"):
            specialization.specialize_amd_exact_circle_shader(
                self._minimal_amd_source(),
                material="unknown",  # type: ignore[arg-type]
            )

    def test_packed_intrinsics_retain_exhaustive_lookup_domains(self) -> None:
        result = specialization.specialize_amd_packed_exact_circle_shader(
            self._minimal_amd_source(),
            material="regular",
        )

        self.assertNotIn("AppleFloatIntrinsicTable;", result)
        self.assertIn("AppleSqrtIntrinsicTable;", result)
        self.assertIn("AppleRsqrtIntrinsicTable;", result)
        self.assertIn("mantissa >> 3u", result)
        self.assertIn("mantissa >> 4u", result)
        self.assertIn("mantissa == 651320u", result)
        self.assertIn("AppleCircleScaleReciprocalBits", result)

    @staticmethod
    def _minimal_amd_source() -> str:
        controls = "\n".join(
            f"uniform {data_type} {name};"
            for name, (data_type, _) in (
                specialization.EXACT_FINAL_CONSTANTS.items()
            )
        )
        material_controls = "\n".join(
            f"uniform float {name};"
            for name in specialization.MATERIAL_CONSTANTS["clear"]
        )
        return f"""uniform highp usampler2D AppleFloatIntrinsicTable;
{controls}
{material_controls}
uint apple_intrinsic_code(float value, uint operation)
{{
    return 0u;
}}
uint float_to_half_bits(float value)
{{
    return 0u;
}}
uint float_to_half_bits_rtz(float value)
{{
    return 0u;
}}
vec4 replay_compute_mode4_sdf(vec2 point)
{{
    return vec4(point, 0.0, 1.0);
}}
vec4 replay_compute_sdf(vec2 point, int mode)
{{
    return mode == 4 ? vec4(point, 0.0, 1.0) : vec4(0.0);
}}
float apple_fast_sqrt(float value)
{{
    return value;
}}
float apple_fast_rsqrt(float value)
{{
    return value;
}}
float apple_fast_reciprocal(float value)
{{
    return value;
}}
"""


if __name__ == "__main__":
    unittest.main()
