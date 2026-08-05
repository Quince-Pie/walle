#!/usr/bin/env python3
"""Specialize the exact Apple pass after its diagnostic gates succeed.

The generic diagnostic shader deliberately keeps every recovered branch and
implements half conversion in portable integer GLSL.  That makes it useful as
an executable specification, but unnecessarily expensive for Walle's known
circle geometry and on the AMD/Mesa renderer used by the application.

Nothing in this module is allowed to be approximate.  Every transformation is
paired with a byte-for-byte image gate, and every source rewrite fails closed
if the executable specification drifts.
"""

from pathlib import Path
from typing import Literal


EXACT_FINAL_CONSTANTS: dict[str, tuple[str, str]] = {
    "SamplerSpatialQuantization": ("int", "0"),
    "SamplerModel": ("int", "0"),
    "InnerSamplerCoordinateModel": ("int", "3"),
    "OuterSamplerCoordinateModel": ("int", "1"),
    "EdgeSamplerCoordinateModel": ("int", "1"),
    "ShadowSamplerCoordinateModel": ("int", "2"),
    "RefractionMixModel": ("int", "0"),
    "UseAppleRefractionTrace": ("int", "0"),
    "UseAppleInterpolantTrace": ("int", "0"),
    "UseAppleSdfTrace": ("int", "0"),
    "UseAppleSqrtTrace": ("int", "0"),
    "UseAppleRsqrtTrace": ("int", "0"),
    "UseAppleIntrinsicTable": ("int", "1"),
    "RecordAppleIntrinsicUsage": ("int", "0"),
    "NumericTrace": ("int", "0"),
    "CoordinateMode": ("int", "5"),
    "AnalyticCoordinateUlpBias": ("int", "0"),
    "AppleFastSqrtBias": ("uint", "0u"),
    "AppleFastReciprocalBias": ("uint", "1u"),
    "ProfileMode4Path": ("int", "0"),
    "EmulateAppleBlend": ("int", "1"),
}

type GlassMaterial = Literal["clear", "regular"]


# These controls were constant in every state of the recovered transition
# matrix.  Clear has four additional zero-valued controls whose complete
# subgraphs are dead.  Appearance-dependent values, including regular edge
# bleed, intentionally remain uniforms.
MATERIAL_CONSTANTS: dict[GlassMaterial, dict[str, str]] = {
    "clear": {
        "ComplexRefraction": "1.0",
        "RefractionOpacity": "0.0",
        "HoldingToneOpacity": "1.0",
        "PreserveHue": "0.0",
        "FloatMixWorkaround": "0.0",
        "ShadowOpacity": "0.0",
        "ShadowContribution": "0.0",
        "EdgeBleedOpacity": "0.0",
    },
    "regular": {
        "ComplexRefraction": "1.0",
        "HoldingToneOpacity": "1.0",
        "PreserveHue": "0.0",
        "FloatMixWorkaround": "0.0",
    },
}


AMD_EXACT_FLOAT_TO_HALF = """uint next_half_up_bits(uint bits);
uint next_half_down_bits(uint bits);

uint float_to_half_bits(float value)
{
    // radeonsi lowers packHalf2x16 to a fast native conversion.  Its result
    // is used only as a candidate: the midpoint comparison below restores
    // IEEE round-to-nearest, ties-to-even exactly.
    uint bits = packHalf2x16(vec2(value, 0.0)) & 0xffffu;
    float rounded = unpackHalf2x16(bits).x;
    if (value == rounded || isnan(value)) {
        return bits;
    }

    bool value_is_higher = value > rounded;
    uint adjacent_bits = value_is_higher
        ? next_half_up_bits(bits)
        : next_half_down_bits(bits);
    float adjacent = unpackHalf2x16(adjacent_bits).x;
    float midpoint = (isinf(rounded) || isinf(adjacent))
        ? ((rounded < 0.0 || adjacent < 0.0) ? -65520.0 : 65520.0)
        : (rounded + adjacent) * 0.5;
    bool choose_adjacent = value_is_higher
        ? value > midpoint
        : value < midpoint;
    if (value == midpoint) {
        choose_adjacent = (adjacent_bits & 1u) == 0u;
    }
    return choose_adjacent ? adjacent_bits : bits;
}"""


AMD_EXACT_FLOAT_TO_HALF_RTZ = """uint float_to_half_bits_rtz(float value)
{
    // This specialization is admitted only after the device pixel gate has
    // proven that radeonsi's native conversion has Apple's RTZ behavior.
    return packHalf2x16(vec2(value, 0.0)) & 0xffffu;
}"""


CIRCLE_SDF_DISPATCH = """vec4 replay_compute_sdf(vec2 point, int mode)
{
    // Walle invokes only the circular mode: +4 for the face and -4 for the
    // shadow-only draw.  The sign controls draw composition, not SDF shape.
    return replay_compute_mode4_sdf(point);
}"""


PACKED_INTRINSIC_DECLARATIONS = """uniform highp usampler2D AppleSqrtIntrinsicTable;
uniform highp usampler2D AppleRsqrtIntrinsicTable;
uniform highp uint AppleCircleScaleReciprocalBits;"""


UNUSED_INTRINSIC_CODE = """uint apple_intrinsic_code(
    float value,
    uint operation
)
{
    // The packed specialization performs operation-specific lookups.
    return 0u;
}"""


PACKED_APPLE_FAST_SQRT = """float apple_fast_sqrt(float value)
{
    float root = ieee_sqrt(value);
    uint source_bits = floatBitsToUint(value);
    uint mantissa = source_bits & 0x007fffffu;
    uint word_index = mantissa >> 3u;
    uint word = texelFetch(
        AppleSqrtIntrinsicTable,
        ivec2(int(word_index & 2047u), int(word_index >> 11u)),
        0
    ).r;
    uint code = (word >> ((mantissa & 7u) * 4u)) & 15u;
    uint encoded_delta = ((source_bits >> 23u) & 1u) == 0u
        ? code & 3u
        : (code >> 2u) & 3u;
    return uintBitsToFloat(uint(
        int(floatBitsToUint(root)) + int(encoded_delta) - 1
    ));
}"""


PACKED_APPLE_FAST_RSQRT = """float apple_fast_rsqrt(float value)
{
    float reciprocal_root = ieee_rsqrt(value);
    uint source_bits = floatBitsToUint(value);
    uint mantissa = source_bits & 0x007fffffu;
    uint word_index = mantissa >> 4u;
    uint word = texelFetch(
        AppleRsqrtIntrinsicTable,
        ivec2(int(word_index & 2047u), int(word_index >> 11u)),
        0
    ).r;
    uint code = (word >> ((mantissa & 15u) * 2u)) & 3u;
    int delta = int(
        (code >> ((source_bits >> 23u) & 1u)) & 1u
    );
    if (mantissa == 651320u || mantissa == 8380416u) {
        delta = -1;
    }
    return uintBitsToFloat(uint(
        int(floatBitsToUint(reciprocal_root)) + delta
    ));
}"""


UNIFORM_APPLE_FAST_RECIPROCAL = """float apple_fast_reciprocal(float value)
{
    // Circle scale is uniform across the draw.  Its exhaustively corrected
    // reciprocal is calculated once on the CPU instead of once per pixel.
    return uintBitsToFloat(AppleCircleScaleReciprocalBits);
}"""


def _circle_sdf_function(material: GlassMaterial) -> str:
    radial_mix = "0.0" if material == "clear" else "0.5"
    return f"""vec4 replay_compute_mode4_sdf(vec2 point)
{{
    vec3 shape = replay_profile_circle_sdf(abs(point));
    vec2 signs = vec2(
        point.x >= 0.0 ? 1.0 : -1.0,
        point.y >= 0.0 ? 1.0 : -1.0
    );
    vec2 shape_normal = half_value(shape.yz * signs);

    // Walle's SDF has equal axes, so the radial-normal input is point.
    float radial_inverse_length = inversesqrt(dot(point, point));
    vec2 radial_normal = half_value(point * radial_inverse_length);
    vec2 normal = half_value(mix(
        shape_normal,
        radial_normal,
        {radial_mix}
    ));
    normal = half_value(normal * half_rsqrt(half_dot(normal, normal)));

    // The captured circular profiles use the identity normal transform.
    return half_value(vec4(shape.x, normal, 1.0));
}}"""


def _replace_exactly_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(
            f"expected exactly one shader occurrence, found {count}: {old!r}"
        )
    return source.replace(old, new)


def _replace_function(source: str, signature: str, replacement: str) -> str:
    """Replace one GLSL function, validating its balanced-brace boundary."""

    if source.count(signature) != 1:
        raise ValueError(
            "expected exactly one shader function signature for "
            f"{signature!r}"
        )
    start = source.index(signature)
    opening_brace = source.find("{", start + len(signature))
    if opening_brace < 0:
        raise ValueError(f"shader function has no body: {signature!r}")
    depth = 0
    for index in range(opening_brace, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[:start] + replacement + source[index + 1 :]
    raise ValueError(f"shader function has an unbalanced body: {signature!r}")


def specialize_exact_final_shader(
    source: str,
    *,
    coordinate_mode: int = 5,
    use_apple_interpolant_trace: int = 0,
    dynamic_uniforms: frozenset[str] = frozenset(),
) -> str:
    """Freeze only gate-proven final-render controls.

    ``ArithmeticBarrier`` deliberately remains a uniform. Its opaque zero
    value prevents the GLSL optimizer from contracting materialized float32
    operations whose exact rounding is part of the pixel gate.
    """

    specialized = source
    constants = {
        **EXACT_FINAL_CONSTANTS,
        "CoordinateMode": ("int", str(coordinate_mode)),
        "UseAppleInterpolantTrace": (
            "int",
            str(use_apple_interpolant_trace),
        ),
    }
    unknown_dynamic_uniforms = dynamic_uniforms - constants.keys()
    if unknown_dynamic_uniforms:
        raise ValueError(
            "unsupported dynamic specialization uniforms: "
            f"{sorted(unknown_dynamic_uniforms)}"
        )
    for name, (data_type, value) in constants.items():
        if name in dynamic_uniforms:
            continue
        declaration = f"uniform {data_type} {name};"
        if specialized.count(declaration) != 1:
            raise ValueError(
                f"expected exactly one shader declaration for {name}"
            )
        specialized = specialized.replace(
            declaration,
            f"const {data_type} {name} = {value};",
        )
    return specialized


def specialize_amd_exact_circle_shader(
    source: str,
    *,
    material: GlassMaterial,
    coordinate_mode: int = 5,
) -> str:
    """Build the byte-exact AMD/Mesa shader for Walle's circle geometry.

    This is a device specialization, not a portability claim.  A caller must
    admit it only after rendering the reference fixtures on the target driver
    with zero mismatched bytes.
    """

    try:
        material_constants = MATERIAL_CONSTANTS[material]
    except KeyError as error:
        raise ValueError(f"unsupported glass material: {material!r}") from error

    specialized = specialize_exact_final_shader(
        source,
        coordinate_mode=coordinate_mode,
    )
    specialized = _replace_exactly_once(
        specialized,
        "const int ProfileMode4Path = 0;",
        "const int ProfileMode4Path = 1;",
    )
    for name, value in material_constants.items():
        specialized = _replace_exactly_once(
            specialized,
            f"uniform float {name};",
            f"const float {name} = {value};",
        )
    specialized = _replace_function(
        specialized,
        "uint float_to_half_bits(float value)",
        AMD_EXACT_FLOAT_TO_HALF,
    )
    specialized = _replace_function(
        specialized,
        "uint float_to_half_bits_rtz(float value)",
        AMD_EXACT_FLOAT_TO_HALF_RTZ,
    )
    specialized = _replace_function(
        specialized,
        "vec4 replay_compute_mode4_sdf(vec2 point)",
        _circle_sdf_function(material),
    )
    return _replace_function(
        specialized,
        "vec4 replay_compute_sdf(vec2 point, int mode)",
        CIRCLE_SDF_DISPATCH,
    )


def specialize_amd_packed_exact_circle_shader(
    source: str,
    *,
    material: GlassMaterial,
    coordinate_mode: int = 5,
) -> str:
    """Use losslessly packed operation tables and a uniform reciprocal.

    The sqrt table retains both two-bit exponent-parity corrections for all
    2^23 mantissas (4 MiB).  The rsqrt table retains both one-bit corrections
    for all mantissas (2 MiB).  The two known negative rsqrt exceptions stay
    explicit.  No learned, sparse, or approximate replacement is involved.
    """

    specialized = specialize_amd_exact_circle_shader(
        source,
        material=material,
        coordinate_mode=coordinate_mode,
    )
    specialized = _replace_exactly_once(
        specialized,
        "uniform highp usampler2D AppleFloatIntrinsicTable;",
        PACKED_INTRINSIC_DECLARATIONS,
    )
    specialized = _replace_function(
        specialized,
        "uint apple_intrinsic_code(float value, uint operation)",
        UNUSED_INTRINSIC_CODE,
    )
    specialized = _replace_function(
        specialized,
        "float apple_fast_sqrt(float value)",
        PACKED_APPLE_FAST_SQRT,
    )
    specialized = _replace_function(
        specialized,
        "float apple_fast_rsqrt(float value)",
        PACKED_APPLE_FAST_RSQRT,
    )
    return _replace_function(
        specialized,
        "float apple_fast_reciprocal(float value)",
        UNIFORM_APPLE_FAST_RECIPROCAL,
    )


def load_specialized_exact_final_shader(
    path: Path = Path("analysis/apple_glass_reference.frag.glsl"),
    *,
    coordinate_mode: int = 5,
    use_apple_interpolant_trace: int = 0,
    dynamic_uniforms: frozenset[str] = frozenset(),
) -> str:
    return specialize_exact_final_shader(
        path.read_text(encoding="utf-8"),
        coordinate_mode=coordinate_mode,
        use_apple_interpolant_trace=use_apple_interpolant_trace,
        dynamic_uniforms=dynamic_uniforms,
    )


def load_amd_exact_circle_shader(
    material: GlassMaterial,
    path: Path = Path("analysis/apple_glass_reference.frag.glsl"),
    *,
    coordinate_mode: int = 5,
) -> str:
    return specialize_amd_exact_circle_shader(
        path.read_text(encoding="utf-8"),
        material=material,
        coordinate_mode=coordinate_mode,
    )


def load_amd_packed_exact_circle_shader(
    material: GlassMaterial,
    path: Path = Path("analysis/apple_glass_reference.frag.glsl"),
    *,
    coordinate_mode: int = 5,
) -> str:
    return specialize_amd_packed_exact_circle_shader(
        path.read_text(encoding="utf-8"),
        material=material,
        coordinate_mode=coordinate_mode,
    )
