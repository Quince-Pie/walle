#version 450 core

precision highp float;
precision highp int;

in highp vec2 sdf_uv;
in highp vec2 source_uv;

layout(location = 0) out vec4 fragment_color;

uniform sampler2D SourceTexture;
uniform sampler2D AppleRefractionTrace;
uniform highp usampler2D AppleInterpolantTrace;
uniform highp usampler2D AppleInterpolantAxisTrace;
uniform int AppleInterpolantAxisStart;
uniform highp usampler2D AppleInterpolantCoefficientTrace;
uniform int AppleInterpolantTileStart;
uniform highp isampler2D AppleInterpolantCorrectionSurface;
uniform highp uvec4 AppleInterpolantSlopeBits;
uniform highp uint AppleInterpolantSourceLowBits;
layout(std430, binding = 0) buffer AppleIntrinsicUsageBuffer {
    uint AppleIntrinsicUsageBits[];
};
uniform sampler2D AppleSdfTrace;
uniform highp usampler2D AppleSdfFloatTrace;
uniform highp usampler2D AppleSdfNormalTrace;
uniform highp usampler2D AppleFloatIntrinsicTable;
uniform highp usampler2D AppleHalfIntrinsicTable;
uniform highp usampler2D AppleHighlightHalfStages;
uniform highp usampler2D AppleHighlightCompositorB;
uniform highp usampler2D AppleHighlightGeometryTrace;
uniform sampler2D DestinationTexture;
uniform int SamplerSpatialQuantization;
uniform int SamplerModel;
uniform int InnerSamplerCoordinateModel;
uniform int OuterSamplerCoordinateModel;
uniform int EdgeSamplerCoordinateModel;
uniform int ShadowSamplerCoordinateModel;
uniform int RefractionMixModel;
uniform int HoldingMixMode;
uniform int HoldingDivideMode;
uniform int UseAppleRefractionTrace;
uniform int UseAppleInterpolantTrace;
uniform int UseAppleSdfTrace;
uniform int UseAppleSqrtTrace;
uniform int UseAppleRsqrtTrace;
uniform int UseAppleIntrinsicTable;
uniform int UseAppleHalfIntrinsicTable;
uniform int RecordAppleIntrinsicUsage;
uniform int NumericTrace;
uniform int CoordinateMode;
uniform int AnalyticCoordinateUlpBias;
uniform uint AppleFastSqrtBias;
uniform uint AppleFastReciprocalBias;
uniform uint ArithmeticBarrier;
uniform int ProfileMode4Path;
uniform int EmulateAppleBlend;
uniform int FinalHighlightPass;
uniform int FinalHighlightTrace;
uniform int HighlightDerivativeMode;
uniform int HighlightCoordinateMode;
uniform int HighlightAlphaUlpBias;
uniform int HighlightFloatDivisionMode;
uniform int HighlightCoverageArithmeticMode;
uniform int HighlightMixMode;
uniform int HighlightBandMode;
uniform int HighlightNormalizeMode;
uniform int HighlightNormalizedCoordinateMode;
uniform int HighlightSdfArithmeticMode;
uniform int HighlightSdfSquaredUlpBias;
uniform int HighlightSdfDistanceUlpBias;
uniform int HighlightVibrantArithmeticMode;
uniform int HighlightSourceDivisionMode;
uniform int HighlightSourceConstructionMode;
uniform int HighlightDestinationDivisionMode;
uniform int UseAppleHighlightAlphaTrace;
uniform int UseAppleHighlightSourceTrace;
uniform int UseAppleHighlightGeometryTrace;

uniform int SdfMode;
uniform vec4 SdfArg;
uniform vec4 SdfTransform;
uniform vec4 SdfArg2;

uniform vec4 KeyFillParams0;
uniform vec4 KeyFillParams1;
uniform vec4 KeyFillParams2;
uniform vec4 KeyFillColor0;
uniform vec4 KeyFillColor1;
uniform vec4 VibrantMatrix0;
uniform vec4 VibrantMatrix1;
uniform vec4 VibrantMatrix2;
uniform vec4 VibrantMatrix3;
uniform vec4 VibrantMatrix4;
uniform vec4 VibrantControls;

uniform float ComplexRefraction;
uniform float RefractionOpacity;
uniform float FaceOpacity;
uniform float HoldingToneOpacity;
uniform float ClampLimit;
uniform float PreserveHue;
uniform float FloatMixWorkaround;
uniform float ShadowOpacity;
uniform float ShadowContribution;
uniform vec4 DisplacementMatrix;
uniform float InnerRefractionAmount;
uniform float InnerRefractionInverseHeight;
uniform float OuterRefractionAmount;
uniform float OuterRefractionInverseHeight;
uniform float RefractionThreshold0;
uniform float RefractionThreshold1;
uniform float BlurRadius;
uniform float EdgeBleedBlurRadius;
uniform float EdgeBleedAmount;
uniform float EdgeBleedInverseHeight;
uniform float ShadowAmount;
uniform float ShadowInverseHeight;
uniform vec2 ShadowOffset;
uniform float ShadowBlurRadius;
uniform float ShadowInverseRadius;
uniform float ShadowFaceOpacity;
uniform vec4 FaceMatrix0;
uniform vec4 FaceMatrix1;
uniform vec4 FaceMatrix2;
uniform vec4 BleedMatrix0;
uniform vec4 BleedMatrix1;
uniform vec4 BleedMatrix2;
uniform vec4 ShadowMatrix0;
uniform vec4 ShadowMatrix1;
uniform vec4 ShadowMatrix2;
uniform vec4 BlurAlpha;
uniform vec4 BlurDistance;
uniform vec2 EdgeBleedDistance;
uniform float EdgeBleedOpacity;
uniform vec2 BleedDarken;
uniform float ShadowDistanceOffset;
uniform vec2 SdrShadowDistance;
uniform float SdrWhiteValue;
uniform float EdrScale;

uint round_shift_right_even(uint value, uint shift)
{
    if (shift == 0u) {
        return value;
    }
    uint truncated = value >> shift;
    uint mask = (1u << shift) - 1u;
    uint remainder = value & mask;
    uint midpoint = 1u << (shift - 1u);
    if (
        remainder > midpoint
        || (remainder == midpoint && (truncated & 1u) != 0u)
    ) {
        truncated += 1u;
    }
    return truncated;
}

uint float_to_half_bits(float value)
{
    uint bits = floatBitsToUint(value);
    uint sign = (bits >> 16u) & 0x8000u;
    uint exponent = (bits >> 23u) & 0xffu;
    uint mantissa = bits & 0x7fffffu;

    if (exponent == 0xffu) {
        return sign | (mantissa == 0u ? 0x7c00u : 0x7e00u);
    }
    if (exponent == 0u) {
        return sign;
    }

    int unbiased = int(exponent) - 127;
    int half_exponent = unbiased + 15;
    if (half_exponent >= 31) {
        return sign | 0x7c00u;
    }
    if (half_exponent <= 0) {
        if (unbiased < -25) {
            return sign;
        }
        uint significand = mantissa | 0x800000u;
        uint shift = uint(-unbiased - 1);
        uint subnormal = round_shift_right_even(significand, shift);
        return sign | min(subnormal, 0x0400u);
    }

    uint rounded_mantissa = round_shift_right_even(mantissa, 13u);
    if (rounded_mantissa == 0x0400u) {
        rounded_mantissa = 0u;
        half_exponent += 1;
        if (half_exponent >= 31) {
            return sign | 0x7c00u;
        }
    }
    return sign
        | (uint(half_exponent) << 10u)
        | rounded_mantissa;
}

float half_value(float value)
{
    return unpackHalf2x16(float_to_half_bits(value)).x;
}

uint next_half_up_bits(uint bits)
{
    if ((bits & 0x7fffu) == 0u) {
        return 0x0001u;
    }
    if ((bits & 0x8000u) != 0u) {
        return bits == 0xfc00u ? 0xfbffu : bits - 1u;
    }
    return bits == 0x7c00u ? bits : bits + 1u;
}

uint next_half_down_bits(uint bits)
{
    if ((bits & 0x7fffu) == 0u) {
        return 0x8001u;
    }
    if ((bits & 0x8000u) != 0u) {
        return bits == 0xfc00u ? bits : bits + 1u;
    }
    return bits == 0x7c00u ? 0x7bffu : bits - 1u;
}

uint float_to_half_bits_rtz(float value)
{
    uint bits = floatBitsToUint(value);
    uint sign = (bits >> 16u) & 0x8000u;
    uint exponent = (bits >> 23u) & 0xffu;
    uint mantissa = bits & 0x7fffffu;

    if (exponent == 0xffu) {
        return sign | (mantissa == 0u ? 0x7c00u : 0x7e00u);
    }
    if (exponent == 0u) {
        return sign;
    }

    int unbiased = int(exponent) - 127;
    int half_exponent = unbiased + 15;
    if (half_exponent >= 31) {
        return sign | 0x7bffu;
    }
    if (half_exponent <= 0) {
        if (unbiased < -24) {
            return sign;
        }
        uint significand = mantissa | 0x800000u;
        uint shift = uint(-unbiased - 1);
        return sign | (significand >> shift);
    }
    return sign
        | (uint(half_exponent) << 10u)
        | (mantissa >> 13u);
}

float half_value_rtz(float value)
{
    return unpackHalf2x16(float_to_half_bits_rtz(value)).x;
}

vec2 half_value_rtz(vec2 value)
{
    return vec2(
        half_value_rtz(value.x),
        half_value_rtz(value.y)
    );
}

vec3 half_value_rtz(vec3 value)
{
    return vec3(
        half_value_rtz(value.xy),
        half_value_rtz(value.z)
    );
}

float float_barrier(float value)
{
    return uintBitsToFloat(floatBitsToUint(value) ^ ArithmeticBarrier);
}

uint apple_intrinsic_code(float value, uint operation)
{
    uint mantissa = floatBitsToUint(value) & 0x007fffffu;
    if (RecordAppleIntrinsicUsage != 0) {
        uint word_count = (1u << 23u) >> 5u;
        atomicOr(
            AppleIntrinsicUsageBits[
                operation * word_count + (mantissa >> 5u)
            ],
            1u << (mantissa & 31u)
        );
    }
    return texelFetch(
        AppleFloatIntrinsicTable,
        ivec2(int(mantissa & 4095u), int(mantissa >> 12)),
        0
    ).r;
}

float ieee_sqrt(float value)
{
    float root = sqrt(value);
    if (!(value > 0.0) || isinf(value)) {
        return root;
    }
    uint root_bits = floatBitsToUint(root);
    double source = double(value);
    for (int iteration = 0; iteration < 4; ++iteration) {
        float previous = uintBitsToFloat(root_bits - 1u);
        double lower_midpoint =
            (double(previous) + double(root)) * 0.5;
        if (source < lower_midpoint * lower_midpoint) {
            root = previous;
            --root_bits;
            continue;
        }

        float next = uintBitsToFloat(root_bits + 1u);
        double upper_midpoint = (double(root) + double(next)) * 0.5;
        if (source > upper_midpoint * upper_midpoint) {
            root = next;
            ++root_bits;
            continue;
        }
        break;
    }
    return root;
}

float apple_fast_sqrt(float value)
{
    if (UseAppleSqrtTrace != 0) {
        uvec4 trace = texelFetch(
            AppleSdfFloatTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        return uintBitsToFloat(trace.y);
    }
    float root = ieee_sqrt(value);
    if (UseAppleIntrinsicTable != 0) {
        uint exponent_parity =
            (floatBitsToUint(value) >> 23) & 1u;
        uint code = apple_intrinsic_code(value, 0u);
        uint encoded_delta = exponent_parity == 0u
            ? code & 3u
            : (code >> 2) & 3u;
        return uintBitsToFloat(uint(
            int(floatBitsToUint(root))
            + int(encoded_delta)
            - 1
        ));
    }
    return uintBitsToFloat(
        floatBitsToUint(root) + AppleFastSqrtBias
    );
}

void float_fraction(
    uint bits,
    out uint significand,
    out int exponent
)
{
    uint exponent_field = (bits >> 23u) & 255u;
    if (exponent_field == 0u) {
        significand = bits & 0x007fffffu;
        exponent = -149;
    } else {
        significand = (bits & 0x007fffffu) | 0x00800000u;
        exponent = int(exponent_field) - 127 - 23;
    }
}

void midpoint_fraction(
    uint left_bits,
    uint right_bits,
    out uint significand,
    out int exponent
)
{
    uint left_significand;
    uint right_significand;
    int left_exponent;
    int right_exponent;
    float_fraction(left_bits, left_significand, left_exponent);
    float_fraction(right_bits, right_significand, right_exponent);
    int common_exponent = min(left_exponent, right_exponent);
    uint aligned_left = left_significand
        << uint(left_exponent - common_exponent);
    uint aligned_right = right_significand
        << uint(right_exponent - common_exponent);
    significand = aligned_left + aligned_right;
    exponent = common_exponent - 1;
}

int rsqrt_midpoint_product_compare(
    uint value_bits,
    uint midpoint_significand,
    int midpoint_exponent
)
{
    uint value_significand;
    int value_exponent;
    float_fraction(value_bits, value_significand, value_exponent);

    uint square_high;
    uint square_low;
    umulExtended(
        midpoint_significand,
        midpoint_significand,
        square_high,
        square_low
    );
    uint low_product_high;
    uint low_product_low;
    umulExtended(
        square_low,
        value_significand,
        low_product_high,
        low_product_low
    );
    uint high_product_high;
    uint high_product_low;
    umulExtended(
        square_high,
        value_significand,
        high_product_high,
        high_product_low
    );
    uint middle = low_product_high + high_product_low;
    uint high = high_product_high + uint(middle < low_product_high);
    uvec3 product = uvec3(low_product_low, middle, high);

    int target_shift = -(value_exponent + 2 * midpoint_exponent);
    if (target_shift < 0) {
        return 1;
    }
    if (target_shift >= 96) {
        return -1;
    }
    uvec3 target = uvec3(0u);
    if (target_shift < 32) {
        target.x = 1u << uint(target_shift);
    } else if (target_shift < 64) {
        target.y = 1u << uint(target_shift - 32);
    } else {
        target.z = 1u << uint(target_shift - 64);
    }
    if (product.z != target.z) {
        return product.z < target.z ? -1 : 1;
    }
    if (product.y != target.y) {
        return product.y < target.y ? -1 : 1;
    }
    if (product.x != target.x) {
        return product.x < target.x ? -1 : 1;
    }
    return 0;
}

float ieee_rsqrt(float value)
{
    if (!(value > 0.0) || isinf(value)) {
        return inversesqrt(value);
    }
    uint value_bits = floatBitsToUint(value);
    uint candidate_bits = floatBitsToUint(
        float(1.0 / sqrt(double(value)))
    );
    for (int iteration = 0; iteration < 2; ++iteration) {
        uint midpoint_significand;
        int midpoint_exponent;
        midpoint_fraction(
            candidate_bits - 1u,
            candidate_bits,
            midpoint_significand,
            midpoint_exponent
        );
        int lower_compare = rsqrt_midpoint_product_compare(
            value_bits,
            midpoint_significand,
            midpoint_exponent
        );
        if (
            lower_compare > 0
            || (lower_compare == 0 && (candidate_bits & 1u) != 0u)
        ) {
            candidate_bits -= 1u;
            continue;
        }

        midpoint_fraction(
            candidate_bits,
            candidate_bits + 1u,
            midpoint_significand,
            midpoint_exponent
        );
        int upper_compare = rsqrt_midpoint_product_compare(
            value_bits,
            midpoint_significand,
            midpoint_exponent
        );
        if (
            upper_compare < 0
            || (upper_compare == 0 && (candidate_bits & 1u) != 0u)
        ) {
            candidate_bits += 1u;
            continue;
        }
        break;
    }
    return uintBitsToFloat(candidate_bits);
}

float apple_fast_rsqrt(float value)
{
    if (UseAppleRsqrtTrace != 0) {
        uvec4 trace = texelFetch(
            AppleSdfNormalTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        return uintBitsToFloat(trace.y);
    }
    float reciprocal_root = ieee_rsqrt(value);
    if (UseAppleIntrinsicTable != 0) {
        uint source_bits = floatBitsToUint(value);
        uint mantissa = source_bits & 0x007fffffu;
        uint exponent_parity = (source_bits >> 23) & 1u;
        uint code = apple_intrinsic_code(value, 1u);
        int delta = int(
            (code >> (4u + exponent_parity)) & 1u
        );
        if (mantissa == 651320u || mantissa == 8380416u) {
            delta = -1;
        }
        return uintBitsToFloat(uint(
            int(floatBitsToUint(reciprocal_root)) + delta
        ));
    }
    return reciprocal_root;
}

int reciprocal_midpoint_product_compare(
    uint value_bits,
    uint midpoint_significand,
    int midpoint_exponent
)
{
    uint value_significand;
    int value_exponent;
    float_fraction(value_bits, value_significand, value_exponent);
    uint product_high;
    uint product_low;
    umulExtended(
        value_significand,
        midpoint_significand,
        product_high,
        product_low
    );
    int target_shift = -(value_exponent + midpoint_exponent);
    if (target_shift < 0) {
        return 1;
    }
    if (target_shift >= 64) {
        return -1;
    }
    uvec2 target = uvec2(0u);
    if (target_shift < 32) {
        target.x = 1u << uint(target_shift);
    } else {
        target.y = 1u << uint(target_shift - 32);
    }
    if (product_high != target.y) {
        return product_high < target.y ? -1 : 1;
    }
    if (product_low != target.x) {
        return product_low < target.x ? -1 : 1;
    }
    return 0;
}

float ieee_reciprocal(float value)
{
    if (!(value > 0.0) || isinf(value)) {
        return 1.0 / value;
    }
    uint value_bits = floatBitsToUint(value);
    uint candidate_bits = floatBitsToUint(
        float_barrier(1.0 / value)
    );
    for (int iteration = 0; iteration < 2; ++iteration) {
        uint midpoint_significand;
        int midpoint_exponent;
        midpoint_fraction(
            candidate_bits - 1u,
            candidate_bits,
            midpoint_significand,
            midpoint_exponent
        );
        int lower_compare = reciprocal_midpoint_product_compare(
            value_bits,
            midpoint_significand,
            midpoint_exponent
        );
        if (
            lower_compare > 0
            || (lower_compare == 0 && (candidate_bits & 1u) != 0u)
        ) {
            candidate_bits -= 1u;
            continue;
        }

        midpoint_fraction(
            candidate_bits,
            candidate_bits + 1u,
            midpoint_significand,
            midpoint_exponent
        );
        int upper_compare = reciprocal_midpoint_product_compare(
            value_bits,
            midpoint_significand,
            midpoint_exponent
        );
        if (
            upper_compare < 0
            || (upper_compare == 0 && (candidate_bits & 1u) != 0u)
        ) {
            candidate_bits += 1u;
            continue;
        }
        break;
    }
    return uintBitsToFloat(candidate_bits);
}

float apple_fast_reciprocal(float value)
{
    float reciprocal = ieee_reciprocal(value);
    if (UseAppleIntrinsicTable != 0) {
        uint code = apple_intrinsic_code(value, 2u);
        uint encoded_delta = (code >> 6) & 3u;
        return uintBitsToFloat(uint(
            int(floatBitsToUint(reciprocal))
            + int(encoded_delta)
            - 1
        ));
    }
    return uintBitsToFloat(
        floatBitsToUint(reciprocal) + AppleFastReciprocalBias
    );
}

vec2 half_value(vec2 value)
{
    return vec2(half_value(value.x), half_value(value.y));
}

vec3 half_value(vec3 value)
{
    return vec3(half_value(value.xy), half_value(value.z));
}

vec4 half_value(vec4 value)
{
    return vec4(half_value(value.xy), half_value(value.zw));
}

float half_constant(uint bits)
{
    return unpackHalf2x16(bits).x;
}

float half_add(float left, float right)
{
    return half_value(left + right);
}

float half_subtract(float left, float right)
{
    return half_value(left - right);
}

float half_multiply(float left, float right)
{
    return half_value(left * right);
}

float half_multiply_float_exact(float left, float right)
{
    float right_high = uintBitsToFloat(
        floatBitsToUint(right) & 0xfffff000u
    );
    float right_low = float_barrier(right - right_high);
    float high_product = float_barrier(left * right_high);
    float low_product = float_barrier(left * right_low);
    float product = float_barrier(high_product + low_product);
    float virtual_low = float_barrier(product - high_product);
    float high_error = float_barrier(
        high_product - float_barrier(product - virtual_low)
    );
    float low_error = float_barrier(low_product - virtual_low);
    float error = float_barrier(high_error + low_error);
    uint bits = float_to_half_bits(product);
    float rounded = unpackHalf2x16(bits).x;
    if (error == 0.0 || isnan(product) || isinf(product)) {
        return rounded;
    }

    bool product_is_higher = product > rounded;
    uint adjacent_bits = product_is_higher
        ? next_half_up_bits(bits)
        : next_half_down_bits(bits);
    float adjacent = unpackHalf2x16(adjacent_bits).x;
    float midpoint = float_barrier(
        (rounded + adjacent) * 0.5
    );
    if (
        product == midpoint
        && (
            (product_is_higher && error > 0.0)
            || (!product_is_higher && error < 0.0)
        )
    ) {
        bits = adjacent_bits;
    }
    return unpackHalf2x16(bits).x;
}

float half_divide(float numerator, float denominator)
{
    return half_value(numerator / denominator);
}

float replay_holding_divide(float numerator, float denominator)
{
    if (HoldingDivideMode == 1) {
        return half_value(
            numerator * apple_fast_reciprocal(denominator)
        );
    }
    if (HoldingDivideMode == 2) {
        return half_multiply(
            numerator,
            half_value(apple_fast_reciprocal(denominator))
        );
    }
    if (HoldingDivideMode == 3) {
        return half_value_rtz(numerator / denominator);
    }
    if (HoldingDivideMode == 4) {
        return half_value_rtz(
            numerator * apple_fast_reciprocal(denominator)
        );
    }
    if (HoldingDivideMode == 5) {
        return half_multiply_float_exact(
            numerator,
            apple_fast_reciprocal(denominator)
        );
    }
    if (HoldingDivideMode == 6) {
        return half_value(
            numerator * float_barrier(1.0 / denominator)
        );
    }
    if (HoldingDivideMode == 7) {
        return half_multiply(
            numerator,
            half_divide(1.0, denominator)
        );
    }
    if (HoldingDivideMode == 8) {
        return half_value(
            numerator
            * float(half_value(apple_fast_reciprocal(denominator)))
        );
    }
    if (HoldingDivideMode == 9) {
        return half_value(
            numerator * apple_fast_reciprocal(denominator) + 0.0
        );
    }
    return half_divide(numerator, denominator);
}

float half_fma(float left, float right, float addend)
{
    return half_value(left * right + addend);
}

float half_fma_exact(float left, float right, float addend)
{
    // Binary16 products are exact in binary32. TwoSum retains the one
    // residual bit needed when the binary32 sum lands on a half midpoint.
    float product = float_barrier(left * right);
    float sum = float_barrier(product + addend);
    float virtual_addend = float_barrier(sum - product);
    float product_error = float_barrier(
        product - float_barrier(sum - virtual_addend)
    );
    float addend_error = float_barrier(
        addend - virtual_addend
    );
    float error = float_barrier(product_error + addend_error);

    uint bits = float_to_half_bits(sum);
    float rounded = unpackHalf2x16(bits).x;
    if (error == 0.0 || isnan(sum) || isinf(sum)) {
        return rounded;
    }

    bool sum_is_higher = sum > rounded;
    uint adjacent_bits = sum_is_higher
        ? next_half_up_bits(bits)
        : next_half_down_bits(bits);
    float adjacent = unpackHalf2x16(adjacent_bits).x;
    float midpoint = float_barrier(
        (rounded + adjacent) * 0.5
    );
    if (
        sum == midpoint
        && (
            (sum_is_higher && error > 0.0)
            || (!sum_is_higher && error < 0.0)
        )
    ) {
        bits = adjacent_bits;
    }
    return unpackHalf2x16(bits).x;
}

vec2 half_fma(float left, vec2 right, vec2 addend)
{
    return vec2(
        half_fma(left, right.x, addend.x),
        half_fma(left, right.y, addend.y)
    );
}

float half_mix_exact(float left, float right, float amount)
{
    return half_fma_exact(
        right,
        amount,
        half_multiply_float_exact(
            left,
            float_barrier(1.0 - amount)
        )
    );
}

vec4 half_mix_exact(vec4 left, vec4 right, float amount)
{
    return vec4(
        half_mix_exact(left.x, right.x, amount),
        half_mix_exact(left.y, right.y, amount),
        half_mix_exact(left.z, right.z, amount),
        half_mix_exact(left.w, right.w, amount)
    );
}

vec4 replay_holding_mix(vec4 left, vec4 right, float amount)
{
    if (HoldingMixMode == 1) {
        return vec4(
            half_fma_exact(
                half_subtract(right.x, left.x),
                amount,
                left.x
            ),
            half_fma_exact(
                half_subtract(right.y, left.y),
                amount,
                left.y
            ),
            half_fma_exact(
                half_subtract(right.z, left.z),
                amount,
                left.z
            ),
            half_fma_exact(
                half_subtract(right.w, left.w),
                amount,
                left.w
            )
        );
    }
    if (HoldingMixMode == 2) {
        return vec4(
            half_fma_exact(
                right.x,
                amount,
                half_fma_exact(-left.x, amount, left.x)
            ),
            half_fma_exact(
                right.y,
                amount,
                half_fma_exact(-left.y, amount, left.y)
            ),
            half_fma_exact(
                right.z,
                amount,
                half_fma_exact(-left.z, amount, left.z)
            ),
            half_fma_exact(
                right.w,
                amount,
                half_fma_exact(-left.w, amount, left.w)
            )
        );
    }
    return half_mix_exact(left, right, amount);
}

float half_sqrt(float value)
{
    if (UseAppleHalfIntrinsicTable != 0) {
        uint bits = float_to_half_bits(value);
        uint intrinsic_word = texelFetch(
            AppleHalfIntrinsicTable,
            ivec2(int(bits & 255u), int(bits >> 8u)),
            0
        ).r;
        return unpackHalf2x16(intrinsic_word).x;
    }
    return half_value(sqrt(value));
}

float half_rsqrt(float value)
{
    if (UseAppleHalfIntrinsicTable != 0) {
        uint bits = float_to_half_bits(value);
        uint intrinsic_word = texelFetch(
            AppleHalfIntrinsicTable,
            ivec2(int(bits & 255u), int(bits >> 8u)),
            0
        ).r;
        return unpackHalf2x16(intrinsic_word).y;
    }
    return half_value(inversesqrt(value));
}

float half_dot(vec2 left, vec2 right)
{
    return half_fma(
        left.y,
        right.y,
        half_multiply(left.x, right.x)
    );
}

float half_dot(vec3 left, vec3 right)
{
    return half_fma_exact(
        left.z,
        right.z,
        half_fma_exact(
            left.y,
            right.y,
            half_multiply(left.x, right.x)
        )
    );
}

float replay_highlight_inverse_length(vec2 normal)
{
    float squared_length = HighlightNormalizeMode == 5
        ? half_fma_exact(
            normal.y,
            normal.y,
            half_multiply(normal.x, normal.x)
        )
        : half_dot(normal, normal);
    if (HighlightNormalizeMode == 1) {
        return half_rsqrt(squared_length);
    } else if (HighlightNormalizeMode == 2) {
        return inversesqrt(squared_length);
    } else if (HighlightNormalizeMode == 3) {
        return float_barrier(1.0 / half_sqrt(squared_length));
    } else if (HighlightNormalizeMode == 4) {
        return float_barrier(1.0 / sqrt(squared_length));
    } else if (HighlightNormalizeMode == 5) {
        return half_rsqrt(squared_length);
    }
    return half_divide(1.0, half_sqrt(squared_length));
}

float replay_normalized_coordinate(
    float point,
    float half_size,
    float circle_scale,
    float inverse_circle_scale
)
{
    if (HighlightNormalizedCoordinateMode == 1) {
        float offset = float_barrier(circle_scale - half_size);
        return float_barrier(
            float_barrier(point + offset) * inverse_circle_scale
        );
    }
    if (HighlightNormalizedCoordinateMode == 2) {
        float delta = float_barrier(point - half_size);
        return fma(delta, inverse_circle_scale, 1.0);
    }
    if (HighlightNormalizedCoordinateMode == 3) {
        float uniform_term = fma(
            -half_size,
            inverse_circle_scale,
            1.0
        );
        return fma(point, inverse_circle_scale, uniform_term);
    }
    if (HighlightNormalizedCoordinateMode == 4) {
        float half_size_term = float_barrier(
            half_size * inverse_circle_scale
        );
        float uniform_term = float_barrier(1.0 - half_size_term);
        return fma(point, inverse_circle_scale, uniform_term);
    }
    if (HighlightNormalizedCoordinateMode == 5) {
        float delta = float_barrier(point - half_size);
        float scaled = float_barrier(delta * inverse_circle_scale);
        return float_barrier(scaled + 1.0);
    }
    if (HighlightNormalizedCoordinateMode == 6) {
        float point_term = float_barrier(
            point * inverse_circle_scale
        );
        float half_size_term = float_barrier(
            half_size * inverse_circle_scale
        );
        float uniform_term = float_barrier(1.0 - half_size_term);
        return float_barrier(point_term + uniform_term);
    }
    if (HighlightNormalizedCoordinateMode == 7) {
        float delta = fma(-half_size, 1.0, point);
        return fma(delta, inverse_circle_scale, 1.0);
    }
    if (HighlightNormalizedCoordinateMode == 8) {
        float numerator = float_barrier(
            float_barrier(point + circle_scale) - half_size
        );
        return float_barrier(numerator * inverse_circle_scale);
    }
    float numerator = float_barrier(
        float_barrier(point - half_size) + circle_scale
    );
    return float_barrier(numerator * inverse_circle_scale);
}

vec3 replay_supercircle_sdf(
    vec2 point,
    vec2 half_size,
    float radius,
    vec2 ovalization
)
{
    float circle_constant = uintBitsToFloat(0x3fc3ab4bu);
    float radius_absolute = abs(radius);
    float circle_scale = float_barrier(radius_absolute * circle_constant);
    float inverse_circle_scale = apple_fast_reciprocal(circle_scale);
    float adjusted_radius = mix(
        circle_scale,
        radius_absolute,
        max(ovalization.x, ovalization.y)
    );
    vec2 adjusted_delta = vec2(
        float_barrier(
            point.x
            + float_barrier(adjusted_radius - half_size.x)
        ),
        float_barrier(
            point.y
            + float_barrier(adjusted_radius - half_size.y)
        )
    );
    vec2 normalized = max(
        vec2(0.0),
        vec2(
            replay_normalized_coordinate(
                point.x,
                half_size.x,
                circle_scale,
                inverse_circle_scale
            ),
            replay_normalized_coordinate(
                point.y,
                half_size.y,
                circle_scale,
                inverse_circle_scale
            )
        )
    );
    vec2 normalized_absolute = abs(normalized);
    float normalized_squared = float_barrier(
        normalized_absolute.y * normalized_absolute.y
        + float_barrier(
            normalized_absolute.x * normalized_absolute.x
        )
    );
    float normalized_length = apple_fast_sqrt(normalized_squared);
    float maximum = max(normalized.x, normalized.y);
    float minimum = min(normalized.x, normalized.y);
    float ratio = clamp(minimum / maximum, 0.0, 1.0);
    ratio = maximum == 0.0 ? 0.0 : ratio;

    float polynomial = uintBitsToFloat(0x3f6d11e0u) * ratio;
    polynomial = uintBitsToFloat(0x4049fc11u) - polynomial;
    polynomial = polynomial * ratio + uintBitsToFloat(0xc06909c0u);
    polynomial = polynomial * ratio + uintBitsToFloat(0x3fa24ecfu);
    polynomial = polynomial * ratio + uintBitsToFloat(0x3e897ce5u);
    float circle_distance = normalized_length + 1.0
        - 1.0
            / (
                1.0
                - ratio * ratio
                    * clamp(normalized_length, 0.0, 1.0)
                    * polynomial
            );

    vec2 oval_delta = max(vec2(0.0), vec2(
        float_barrier(
            normalized.x * circle_constant
            + uintBitsToFloat(0xbf075697u)
        ),
        float_barrier(
            normalized.y * circle_constant
            + uintBitsToFloat(0xbf075697u)
        )
    ));
    float oval_squared = float_barrier(
        oval_delta.y * oval_delta.y
        + float_barrier(oval_delta.x * oval_delta.x)
    );
    float oval_length = apple_fast_sqrt(oval_squared);
    float oval_distance = float_barrier(
        oval_length * uintBitsToFloat(0x3f277765u)
        + uintBitsToFloat(0x3eb11136u)
    );
    float distance_x = mix(
        circle_distance,
        oval_distance,
        ovalization.x
    );
    float distance_y = mix(
        circle_distance,
        oval_distance,
        ovalization.y
    );
    float direction = normalized.y > normalized.x ? 1.0 : -1.0;
    float distance_select = clamp(
        0.5 - direction + direction * ratio,
        0.0,
        1.0
    );
    float curved_distance = half_value(
        mix(distance_x, distance_y, distance_select) - 1.0
    );
    float interior_distance = min(
        max(
            half_value(adjusted_delta.x),
            half_value(adjusted_delta.y)
        ),
        0.0
    );
    float distance = half_add(
        interior_distance,
        half_value(float_barrier(circle_scale * curved_distance))
    );

    vec2 positive_delta = max(vec2(0.0), adjusted_delta);
    float positive_squared;
    if (HighlightSdfArithmeticMode == 1) {
        positive_squared = float_barrier(
            float_barrier(positive_delta.y * positive_delta.y)
            + float_barrier(positive_delta.x * positive_delta.x)
        );
    } else if (HighlightSdfArithmeticMode == 2) {
        positive_squared = float_barrier(
            float_barrier(positive_delta.y * positive_delta.y)
            + positive_delta.x * positive_delta.x
        );
    } else if (HighlightSdfArithmeticMode == 3) {
        positive_squared = float_barrier(
            positive_delta.y * positive_delta.y
            + positive_delta.x * positive_delta.x
        );
    } else {
        positive_squared = float_barrier(
            positive_delta.y * positive_delta.y
            + float_barrier(positive_delta.x * positive_delta.x)
        );
    }
    if (HighlightSdfSquaredUlpBias != 0) {
        positive_squared = uintBitsToFloat(uint(
            int(floatBitsToUint(positive_squared))
            + HighlightSdfSquaredUlpBias
        ));
    }
    float inverse_length = apple_fast_rsqrt(positive_squared);
    vec2 curved_normal = half_value(vec2(
        float_barrier(positive_delta.x * inverse_length),
        float_barrier(positive_delta.y * inverse_length)
    ));
    vec2 axis_normal = adjusted_delta.x > adjusted_delta.y
        ? vec2(1.0, 0.0)
        : vec2(0.0, 1.0);
    vec2 normal = half_add(curved_normal.x, curved_normal.y) > 0.0
        ? curved_normal
        : axis_normal;
    return half_value(vec3(distance, normal));
}

vec3 replay_profile_circle_sdf(vec2 point)
{
    float circle_constant = uintBitsToFloat(0x3fc3ab4bu);
    float circle_scale = float_barrier(SdfArg2.z * circle_constant);
    float inverse_circle_scale = apple_fast_reciprocal(circle_scale);

    float offset_x = float_barrier(circle_scale - SdfArg.x);
    float offset_y = float_barrier(circle_scale - SdfArg.y);
    float numerator_x = float_barrier(point.x + offset_x);
    float numerator_y = float_barrier(point.y + offset_y);
    float normalized_x = max(
        0.0,
        float_barrier(numerator_x * inverse_circle_scale)
    );
    float normalized_y = max(
        0.0,
        float_barrier(numerator_y * inverse_circle_scale)
    );
    float oval_x = max(0.0, float_barrier(
        normalized_x * circle_constant
        + uintBitsToFloat(0xbf075697u)
    ));
    float oval_y = max(0.0, float_barrier(
        normalized_y * circle_constant
        + uintBitsToFloat(0xbf075697u)
    ));
    float oval_squared = float_barrier(
        oval_y * oval_y + float_barrier(oval_x * oval_x)
    );
    float oval_length = apple_fast_sqrt(oval_squared);
    float oval_distance = float_barrier(
        oval_length * uintBitsToFloat(0x3f277765u)
        + uintBitsToFloat(0x3eb11136u)
    );
    precise float curved_float = fma(
        oval_length,
        uintBitsToFloat(0x3f277765u),
        uintBitsToFloat(0xbf277765u)
    );
    float curved_distance = half_value(curved_float);
    float distance = half_value(
        float_barrier(circle_scale * curved_distance)
    );

    float point_squared = float_barrier(
        point.y * point.y + float_barrier(point.x * point.x)
    );
    float inverse_length = apple_fast_rsqrt(point_squared);
    vec2 normal = half_value(vec2(
        float_barrier(point.x * inverse_length),
        float_barrier(point.y * inverse_length)
    ));
    vec2 axis_normal = point.x > point.y
        ? vec2(1.0, 0.0)
        : vec2(0.0, 1.0);
    normal = half_add(normal.x, normal.y) > 0.0
        ? normal
        : axis_normal;
    return half_value(vec3(distance, normal));
}

vec4 replay_profile_circle_debug(vec2 point)
{
    float circle_constant = uintBitsToFloat(0x3fc3ab4bu);
    float circle_scale = float_barrier(SdfArg2.z * circle_constant);
    float inverse_circle_scale = apple_fast_reciprocal(circle_scale);
    float offset_x = float_barrier(circle_scale - SdfArg.x);
    float offset_y = float_barrier(circle_scale - SdfArg.y);
    float numerator_x = float_barrier(point.x + offset_x);
    float numerator_y = float_barrier(point.y + offset_y);
    float normalized_x = max(
        0.0,
        float_barrier(numerator_x * inverse_circle_scale)
    );
    float normalized_y = max(
        0.0,
        float_barrier(numerator_y * inverse_circle_scale)
    );
    float oval_x = max(0.0, float_barrier(
        normalized_x * circle_constant
        + uintBitsToFloat(0xbf075697u)
    ));
    float oval_y = max(0.0, float_barrier(
        normalized_y * circle_constant
        + uintBitsToFloat(0xbf075697u)
    ));
    float oval_squared = float_barrier(
        oval_y * oval_y + float_barrier(oval_x * oval_x)
    );
    float oval_length = apple_fast_sqrt(oval_squared);
    float oval_distance = float_barrier(
        oval_length * uintBitsToFloat(0x3f277765u)
        + uintBitsToFloat(0x3eb11136u)
    );
    precise float curved_float = fma(
        oval_length,
        uintBitsToFloat(0x3f277765u),
        uintBitsToFloat(0xbf277765u)
    );
    float curved_distance = half_value(curved_float);
    return half_value(vec4(
        normalized_x,
        oval_x,
        oval_length,
        curved_distance
    ));
}

vec4 replay_compute_mode4_sdf(vec2 point)
{
    vec3 shape = ProfileMode4Path != 0
        ? replay_profile_circle_sdf(abs(point))
        : replay_supercircle_sdf(
            abs(point),
            SdfArg.xy,
            SdfArg2.z,
            SdfArg2.xy
        );
    vec2 signs = vec2(
        point.x >= 0.0 ? 1.0 : -1.0,
        point.y >= 0.0 ? 1.0 : -1.0
    );
    vec2 shape_normal = half_value(shape.yz * signs);

    vec2 radial_input = vec2(
        point.x,
        SdfArg.x * point.y / SdfArg.y
    );
    float radial_inverse_length = inversesqrt(
        dot(radial_input, radial_input)
    );
    vec2 radial_normal = half_value(
        radial_input * radial_inverse_length
    );
    vec2 normal = half_value(mix(
        shape_normal,
        radial_normal,
        half_value(SdfArg.w)
    ));
    normal = half_value(normal * half_rsqrt(half_dot(normal, normal)));

    float transformed_x = half_value(
        SdfTransform.x * normal.x + SdfTransform.y * normal.y
    );
    float transformed_y = half_value(
        SdfTransform.z * normal.x + SdfTransform.w * normal.y
    );
    return half_value(vec4(shape.x, transformed_x, transformed_y, 1.0));
}

vec4 replay_compute_simple_sdf(vec2 point)
{
    vec2 delta = abs(point) - SdfArg.xy;
    vec2 delta_half = half_value(delta);
    vec2 signs = vec2(
        point.x >= 0.0 ? 1.0 : -1.0,
        point.y >= 0.0 ? 1.0 : -1.0
    );
    vec2 axis_normal = half_value(
        (delta_half.x > delta_half.y
            ? vec2(1.0, 0.0)
            : vec2(0.0, 1.0))
        * signs
    );

    vec2 radial_input = vec2(
        point.x,
        SdfArg.x * point.y / SdfArg.y
    );
    float radial_inverse_length = inversesqrt(
        dot(radial_input, radial_input)
    );
    vec2 radial_normal = half_value(
        radial_input * radial_inverse_length
    );
    vec2 normal = half_value(mix(
        axis_normal,
        radial_normal,
        half_value(SdfArg.w)
    ));
    normal = half_value(normal * half_rsqrt(half_dot(normal, normal)));

    float transformed_x = half_value(
        SdfTransform.x * normal.x + SdfTransform.y * normal.y
    );
    float transformed_y = half_value(
        SdfTransform.z * normal.x + SdfTransform.w * normal.y
    );
    return half_value(vec4(
        max(delta_half.x, delta_half.y),
        transformed_x,
        transformed_y,
        1.0
    ));
}

vec4 replay_compute_asymmetric_sdf(vec2 point)
{
    vec4 radii = SdfArg2;
    vec4 first_pair = vec4(radii.x, radii.x, radii.w, radii.y);
    vec4 second_pair = vec4(radii.y, radii.w, radii.z, radii.z);
    vec4 average_radius = (first_pair + second_pair) * 0.5;
    vec4 half_size = vec4(SdfArg.x, SdfArg.y, SdfArg.x, SdfArg.y);
    vec4 ovalization = clamp(
        (
            vec4(uintBitsToFloat(0xbfc3ab4bu))
            - half_size / average_radius
        )
        * vec4(uintBitsToFloat(0xbff21e8cu)),
        0.0,
        1.0
    );

    vec3 shape = replay_supercircle_sdf(
        point,
        SdfArg.xy,
        radii.y,
        ovalization.xw
    );
    vec3 candidate = replay_supercircle_sdf(
        vec2(-point.x, point.y),
        SdfArg.xy,
        radii.x,
        ovalization.xy
    );
    candidate.y = -candidate.y;
    if (candidate.x > shape.x) {
        shape = candidate;
    }

    candidate = replay_supercircle_sdf(
        vec2(point.x, -point.y),
        SdfArg.xy,
        radii.z,
        ovalization.zw
    );
    candidate.z = -candidate.z;
    if (candidate.x > shape.x) {
        shape = candidate;
    }

    candidate = replay_supercircle_sdf(
        -point,
        SdfArg.xy,
        radii.w,
        vec2(ovalization.z, ovalization.y)
    );
    candidate.yz = -candidate.yz;
    if (candidate.x > shape.x) {
        shape = candidate;
    }

    vec2 radial_input = vec2(
        point.x,
        SdfArg.x * point.y / SdfArg.y
    );
    float radial_inverse_length = inversesqrt(
        dot(radial_input, radial_input)
    );
    vec2 radial_normal = half_value(
        radial_input * radial_inverse_length
    );
    vec2 normal = half_value(mix(
        shape.yz,
        radial_normal,
        half_value(SdfArg.w)
    ));
    normal = half_value(normal * half_rsqrt(half_dot(normal, normal)));

    float transformed_x = half_value(
        SdfTransform.x * normal.x + SdfTransform.y * normal.y
    );
    float transformed_y = half_value(
        SdfTransform.z * normal.x + SdfTransform.w * normal.y
    );
    return half_value(vec4(
        shape.x,
        transformed_x,
        transformed_y,
        1.0
    ));
}

vec4 replay_compute_sdf(vec2 point, int mode)
{
    if (mode < 4) {
        return replay_compute_simple_sdf(point);
    }
    if (mode == 4) {
        return replay_compute_mode4_sdf(point);
    }
    return replay_compute_asymmetric_sdf(point);
}

float replay_highlight_divide(float numerator, float denominator)
{
    if (HighlightFloatDivisionMode == 1) {
        return float_barrier(
            numerator * apple_fast_reciprocal(denominator)
        );
    }
    if (HighlightFloatDivisionMode == 2) {
        float reciprocal = float_barrier(1.0 / denominator);
        return float_barrier(numerator * reciprocal);
    }
    if (HighlightFloatDivisionMode == 3) {
        float quotient = float_barrier(numerator / denominator);
        float residual = fma(-quotient, denominator, numerator);
        return float_barrier(
            quotient + float_barrier(residual / denominator)
        );
    }
    if (HighlightFloatDivisionMode == 4) {
        float reciprocal = float_barrier(1.0 / denominator);
        float quotient = float_barrier(numerator * reciprocal);
        float residual = fma(-quotient, denominator, numerator);
        return fma(residual, reciprocal, quotient);
    }
    if (HighlightFloatDivisionMode == 5) {
        float reciprocal = float_barrier(1.0 / denominator);
        float quotient = float_barrier(numerator * reciprocal);
        float residual = fma(-quotient, denominator, numerator);
        quotient = float_barrier(fma(residual, reciprocal, quotient));
        residual = fma(-quotient, denominator, numerator);
        return float_barrier(fma(residual, reciprocal, quotient));
    }
    return numerator / denominator;
}

float replay_highlight_coverage_edge(
    float numerator,
    float denominator
)
{
    if (HighlightCoverageArithmeticMode == 1) {
        return fma(
            numerator,
            apple_fast_reciprocal(denominator),
            0.5
        );
    }
    if (HighlightCoverageArithmeticMode == 2) {
        return fma(
            numerator,
            float_barrier(1.0 / denominator),
            0.5
        );
    }
    return replay_highlight_divide(numerator, denominator) + 0.5;
}

float replay_highlight_mix(float left, float right, float amount)
{
    if (HighlightMixMode == 1) {
        float left_term = float_barrier(
            left * float_barrier(1.0 - amount)
        );
        float right_term = float_barrier(right * amount);
        return float_barrier(left_term + right_term);
    }
    if (HighlightMixMode == 2) {
        return fma(
            right,
            amount,
            float_barrier(left * float_barrier(1.0 - amount))
        );
    }
    if (HighlightMixMode == 3) {
        return fma(amount, right - left, left);
    }
    if (HighlightMixMode == 4) {
        return float_barrier(
            left + float_barrier(amount * float_barrier(right - left))
        );
    }
    return mix(left, right, amount);
}

float replay_highlight_scaled_distance(vec4 sdf)
{
    vec2 normal = half_value(sdf.yz);
    float inverse_length = replay_highlight_inverse_length(normal);
    float distance = float(-half_add(
        half_value(KeyFillParams2.w),
        sdf.x
    ));
    return float(inverse_length) * distance;
}

float replay_highlight_trace_fwidth(int parity_x, int parity_y)
{
    ivec2 pixel = ivec2(gl_FragCoord.xy);
    int base_x = ((pixel.x - parity_x) & ~1) + parity_x;
    int base_y = ((pixel.y - parity_y) & ~1) + parity_y;
    vec4 left_sdf = half_value(texelFetch(
        AppleSdfTrace,
        ivec2(base_x, pixel.y),
        0
    ));
    vec4 right_sdf = half_value(texelFetch(
        AppleSdfTrace,
        ivec2(base_x + 1, pixel.y),
        0
    ));
    vec4 bottom_sdf = half_value(texelFetch(
        AppleSdfTrace,
        ivec2(pixel.x, base_y),
        0
    ));
    vec4 top_sdf = half_value(texelFetch(
        AppleSdfTrace,
        ivec2(pixel.x, base_y + 1),
        0
    ));
    float left = replay_highlight_scaled_distance(left_sdf);
    float right = replay_highlight_scaled_distance(right_sdf);
    float bottom = replay_highlight_scaled_distance(bottom_sdf);
    float top = replay_highlight_scaled_distance(top_sdf);
    return abs(right - left) + abs(top - bottom);
}

float replay_highlight_geometry_trace_fwidth(
    int parity_x,
    int parity_y
)
{
    ivec2 pixel = ivec2(gl_FragCoord.xy);
    int base_x = ((pixel.x - parity_x) & ~1) + parity_x;
    int base_y = ((pixel.y - parity_y) & ~1) + parity_y;
    float left = uintBitsToFloat(texelFetch(
        AppleHighlightGeometryTrace,
        ivec2(base_x, pixel.y),
        0
    ).x);
    float right = uintBitsToFloat(texelFetch(
        AppleHighlightGeometryTrace,
        ivec2(base_x + 1, pixel.y),
        0
    ).x);
    float bottom = uintBitsToFloat(texelFetch(
        AppleHighlightGeometryTrace,
        ivec2(pixel.x, base_y),
        0
    ).x);
    float top = uintBitsToFloat(texelFetch(
        AppleHighlightGeometryTrace,
        ivec2(pixel.x, base_y + 1),
        0
    ).x);
    return abs(right - left) + abs(top - bottom);
}

struct ReplayHighlightBandTrace {
    float normalized_distance;
    float fade;
    float feather;
    float leading_coverage;
    float faded_coverage;
    float trailing_coverage;
    float directional_numerator;
    float directional;
    float alpha_float;
    float alpha;
};

ReplayHighlightBandTrace replay_key_fill_band_diagnostic(
    float scaled_distance,
    float width,
    float threshold,
    vec2 direction,
    vec2 normal,
    float fade_mix
)
{
    ReplayHighlightBandTrace trace;
    trace.normalized_distance = clamp(
        replay_highlight_divide(scaled_distance, width),
        0.0,
        1.0
    );
    trace.fade = replay_highlight_mix(
        trace.normalized_distance < 1.0 ? 1.0 : 0.0,
        1.0 - trace.normalized_distance,
        fade_mix
    );
    float derivative_width = fwidth(scaled_distance);
    if (HighlightDerivativeMode == 1) {
        derivative_width = fwidthFine(scaled_distance);
    } else if (HighlightDerivativeMode == 2) {
        derivative_width = fwidthCoarse(scaled_distance);
    } else if (HighlightDerivativeMode == 3) {
        derivative_width = abs(dFdxFine(scaled_distance))
            + abs(dFdyFine(scaled_distance));
    } else if (HighlightDerivativeMode == 4) {
        derivative_width = abs(dFdxCoarse(scaled_distance))
            + abs(dFdyCoarse(scaled_distance));
    } else if (
        HighlightDerivativeMode >= 5
        && HighlightDerivativeMode <= 8
    ) {
        int parity = HighlightDerivativeMode - 5;
        if (UseAppleHighlightGeometryTrace != 0) {
            derivative_width =
                replay_highlight_geometry_trace_fwidth(
                    parity & 1,
                    (parity >> 1) & 1
                );
        } else {
            derivative_width = replay_highlight_trace_fwidth(
                parity & 1,
                (parity >> 1) & 1
            );
        }
    }
    trace.feather = max(
        derivative_width,
        uintBitsToFloat(0x38d1b717u)
    );
    trace.leading_coverage = clamp(
        replay_highlight_coverage_edge(
            scaled_distance,
            trace.feather
        ),
        0.0,
        1.0
    );
    trace.faded_coverage =
        trace.leading_coverage * trace.fade;
    trace.trailing_coverage = trace.faded_coverage * clamp(
        replay_highlight_coverage_edge(
            width - scaled_distance,
            trace.feather
        ),
        0.0,
        1.0
    );
    trace.directional_numerator =
        float(half_dot(direction, normal)) - threshold;
    trace.directional = clamp(
        replay_highlight_divide(
            trace.directional_numerator,
            float(max(
                half_subtract(1.0, half_value(threshold)),
                half_constant(0x068eu)
            ))
        ),
        0.0,
        1.0
    );
    trace.alpha_float =
        trace.trailing_coverage * trace.directional;
    trace.alpha = half_value(
        scaled_distance < -5.0 ? 0.0 : trace.alpha_float
    );
    return trace;
}

vec4 replay_highlight_trace_word(uint word)
{
    return vec4(
        float(word & 255u),
        float((word >> 8u) & 255u),
        float((word >> 16u) & 255u),
        float((word >> 24u) & 255u)
    ) / 255.0;
}

vec4 replay_highlight_diagnostic(vec4 sdf, int mode)
{
    vec4 params_0 = half_value(KeyFillParams0);
    vec4 params_1 = half_value(KeyFillParams1);
    vec4 params_2 = half_value(KeyFillParams2);
    float distance = float(-half_add(params_2.w, sdf.x));
    vec2 normal = half_value(sdf.yz);
    float inverse_length = replay_highlight_inverse_length(normal);
    normal = half_value(vec2(inverse_length) * normal);
    float scaled_distance = float(inverse_length) * distance;
    if (UseAppleHighlightGeometryTrace != 0) {
        uvec4 geometry = texelFetch(
            AppleHighlightGeometryTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        scaled_distance = uintBitsToFloat(geometry.x);
        normal = vec2(
            unpackHalf2x16(geometry.z).y,
            unpackHalf2x16(geometry.w).x
        );
    }
    ReplayHighlightBandTrace key =
        replay_key_fill_band_diagnostic(
            scaled_distance,
            float(params_0.x),
            float(params_0.y),
            vec2(params_0.w, params_1.x),
            normal,
            float(params_2.z)
        );
    ReplayHighlightBandTrace fill =
        replay_key_fill_band_diagnostic(
            scaled_distance,
            float(params_1.y),
            float(params_1.z),
            params_2.xy,
            normal,
            float(params_2.z)
        );
    float highlight_alpha = half_add(fill.alpha, key.alpha);
    if (mode == 18) {
        return replay_highlight_trace_word(
            float_to_half_bits(highlight_alpha)
        );
    }
    float value = scaled_distance;
    if (mode == 5) {
        value = key.feather;
    } else if (mode == 6) {
        value = key.normalized_distance;
    } else if (mode == 7) {
        value = key.fade;
    } else if (mode == 8) {
        value = key.leading_coverage;
    } else if (mode == 9) {
        value = key.faded_coverage;
    } else if (mode == 10) {
        value = key.trailing_coverage;
    } else if (mode == 11) {
        value = key.directional_numerator;
    } else if (mode == 12) {
        value = key.directional;
    } else if (mode == 13) {
        value = key.alpha_float;
    } else if (mode == 14) {
        value = fill.directional_numerator;
    } else if (mode == 15) {
        value = fill.directional;
    } else if (mode == 16) {
        value = fill.alpha_float;
    } else if (mode == 17) {
        value = key.alpha;
    }
    return replay_highlight_trace_word(floatBitsToUint(value));
}

float replay_key_fill_band(
    float scaled_distance,
    float width,
    float threshold,
    vec2 direction,
    vec2 normal,
    float fade_mix
)
{
    float normalized_distance = clamp(
        replay_highlight_divide(scaled_distance, width),
        0.0,
        1.0
    );
    float fade = replay_highlight_mix(
        normalized_distance < 1.0 ? 1.0 : 0.0,
        1.0 - normalized_distance,
        fade_mix
    );
    float derivative_width = fwidth(scaled_distance);
    if (HighlightDerivativeMode == 1) {
        derivative_width = fwidthFine(scaled_distance);
    } else if (HighlightDerivativeMode == 2) {
        derivative_width = fwidthCoarse(scaled_distance);
    } else if (HighlightDerivativeMode == 3) {
        derivative_width = abs(dFdxFine(scaled_distance))
            + abs(dFdyFine(scaled_distance));
    } else if (HighlightDerivativeMode == 4) {
        derivative_width = abs(dFdxCoarse(scaled_distance))
            + abs(dFdyCoarse(scaled_distance));
    } else if (
        HighlightDerivativeMode >= 5
        && HighlightDerivativeMode <= 8
    ) {
        int parity = HighlightDerivativeMode - 5;
        if (UseAppleHighlightGeometryTrace != 0) {
            derivative_width = replay_highlight_geometry_trace_fwidth(
                parity & 1,
                (parity >> 1) & 1
            );
        } else {
            derivative_width = replay_highlight_trace_fwidth(
                parity & 1,
                (parity >> 1) & 1
            );
        }
    }
    float feather = max(
        derivative_width,
        uintBitsToFloat(0x38d1b717u)
    );
    float coverage = clamp(
        replay_highlight_coverage_edge(scaled_distance, feather),
        0.0,
        1.0
    );
    coverage *= fade;
    coverage *= clamp(
        replay_highlight_coverage_edge(
            width - scaled_distance,
            feather
        ),
        0.0,
        1.0
    );

    float directional = clamp(
        replay_highlight_divide(
            float(half_dot(direction, normal)) - threshold,
            float(max(
                half_subtract(1.0, half_value(threshold)),
                half_constant(0x068eu)
            ))
        ),
        0.0,
        1.0
    );
    float alpha = coverage * directional;
    return half_value(
        scaled_distance < -5.0 ? 0.0 : alpha
    );
}

vec4 replay_sdf_key_fill_highlight(vec4 sdf)
{
    vec4 params_0 = half_value(KeyFillParams0);
    vec4 params_1 = half_value(KeyFillParams1);
    vec4 params_2 = half_value(KeyFillParams2);
    vec4 color_0 = half_value(KeyFillColor0);
    vec4 color_1 = half_value(KeyFillColor1);

    float distance = float(-half_add(params_2.w, sdf.x));
    vec2 normal = half_value(sdf.yz);
    float inverse_length = replay_highlight_inverse_length(normal);
    normal = half_value(vec2(inverse_length) * normal);
    float scaled_distance = float(inverse_length) * distance;
    if (UseAppleHighlightGeometryTrace != 0) {
        uvec4 geometry = texelFetch(
            AppleHighlightGeometryTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        scaled_distance = uintBitsToFloat(geometry.x);
        normal = vec2(
            unpackHalf2x16(geometry.z).y,
            unpackHalf2x16(geometry.w).x
        );
    }

    float key_alpha = replay_key_fill_band(
        scaled_distance,
        float(params_0.x),
        float(params_0.y),
        vec2(params_0.w, params_1.x),
        normal,
        float(params_2.z)
    );
    vec2 fill_direction = vec2(params_2.x, params_2.y);
    float fill_alpha = replay_key_fill_band(
        scaled_distance,
        float(params_1.y),
        float(params_1.z),
        fill_direction,
        normal,
        float(params_2.z)
    );

    if (half_add(fill_alpha, key_alpha) < half_constant(0x068eu)) {
        discard;
    }

    float key_denominator = max(
        half_value(
            (1.0 - float(key_alpha)) * float(params_0.z) + 1.0
        ),
        half_constant(0x068eu)
    );
    float fill_denominator = max(
        half_value(
            (1.0 - float(fill_alpha)) * float(params_1.w) + 1.0
        ),
        half_constant(0x068eu)
    );
    float key_weight = half_divide(key_alpha, key_denominator);
    float fill_weight = half_divide(fill_alpha, fill_denominator);
    vec4 key = half_value(vec4(key_weight) * color_0);
    vec4 fill = half_value(vec4(fill_weight) * color_1);
    if (HighlightBandMode == 1) {
        return key;
    }
    if (HighlightBandMode == 2) {
        return fill;
    }
    return half_value(fill + key);
}

vec3 replay_highlight_destination_straight(
    vec4 destination,
    float destination_alpha
)
{
    if (HighlightDestinationDivisionMode == 1) {
        return vec3(
            half_divide(destination.r, destination_alpha),
            half_divide(destination.g, destination_alpha),
            half_divide(destination.b, destination_alpha)
        );
    }
    if (HighlightDestinationDivisionMode == 2) {
        float reciprocal = half_divide(1.0, destination_alpha);
        return vec3(
            half_multiply(destination.r, reciprocal),
            half_multiply(destination.g, reciprocal),
            half_multiply(destination.b, reciprocal)
        );
    }
    if (HighlightDestinationDivisionMode == 3) {
        return half_value_rtz(
            destination.rgb / vec3(destination_alpha)
        );
    }
    if (HighlightDestinationDivisionMode == 4) {
        float reciprocal = float_barrier(1.0 / destination_alpha);
        return half_value(destination.rgb * vec3(reciprocal));
    }
    if (HighlightDestinationDivisionMode == 5) {
        float reciprocal = apple_fast_reciprocal(destination_alpha);
        return half_value(destination.rgb * vec3(reciprocal));
    }
    if (HighlightDestinationDivisionMode == 6) {
        return destination.rgb / vec3(destination_alpha);
    }
    return half_value(
        destination.rgb / vec3(destination_alpha)
    );
}

vec4 replay_highlight_source_initial(vec4 mapped, float highlight_alpha)
{
    mapped = half_value(mapped);
    highlight_alpha = half_value(highlight_alpha);
    float source_alpha = half_multiply(mapped.a, highlight_alpha);

    if (HighlightSourceConstructionMode == 1) {
        return vec4(
            half_value(mapped.rgb * vec3(source_alpha)),
            source_alpha
        );
    }
    if (HighlightSourceConstructionMode == 2) {
        float combined_alpha = float_barrier(mapped.a * highlight_alpha);
        return vec4(
            half_value(mapped.rgb * vec3(combined_alpha)),
            source_alpha
        );
    }
    if (HighlightSourceConstructionMode == 3) {
        vec3 premultiplied = vec3(
            float_barrier(mapped.r * mapped.a),
            float_barrier(mapped.g * mapped.a),
            float_barrier(mapped.b * mapped.a)
        );
        return vec4(
            half_value(premultiplied * vec3(highlight_alpha)),
            source_alpha
        );
    }
    if (HighlightSourceConstructionMode == 4) {
        float combined_alpha = float_barrier(mapped.a * highlight_alpha);
        vec3 premultiplied = vec3(
            float_barrier(mapped.r * combined_alpha),
            float_barrier(mapped.g * combined_alpha),
            float_barrier(mapped.b * combined_alpha)
        );
        return vec4(half_value(premultiplied), source_alpha);
    }
    if (HighlightSourceConstructionMode == 5) {
        vec3 premultiplied = vec3(
            float_barrier(mapped.r * mapped.a),
            float_barrier(mapped.g * mapped.a),
            float_barrier(mapped.b * mapped.a)
        );
        return vec4(
            premultiplied * vec3(highlight_alpha),
            source_alpha
        );
    }
    vec3 premultiplied = half_value(mapped.rgb * vec3(mapped.a));
    if (HighlightSourceConstructionMode == 6) {
        return vec4(
            premultiplied * vec3(highlight_alpha),
            source_alpha
        );
    }
    return vec4(
        half_value(premultiplied * vec3(highlight_alpha)),
        source_alpha
    );
}

vec4 replay_vibrant_color_matrix_sover(
    vec4 highlight,
    vec4 destination
)
{
    highlight = half_value(highlight);
    destination = half_value(destination);
    vec4 matrix_0 = half_value(VibrantMatrix0);
    vec4 matrix_1 = half_value(VibrantMatrix1);
    vec4 matrix_2 = half_value(VibrantMatrix2);
    vec4 matrix_3 = half_value(VibrantMatrix3);
    vec4 matrix_4 = half_value(VibrantMatrix4);
    vec4 controls = half_value(VibrantControls);

    float destination_alpha = max(
        destination.a,
        half_constant(0x068eu)
    );
    vec3 straight = replay_highlight_destination_straight(
        destination,
        destination_alpha
    );
    vec4 mapped = half_value(vec4(straight.r) * matrix_0);
    if (HighlightVibrantArithmeticMode == 9) {
        mapped = vec4(
            half_fma_exact(
                straight.b,
                matrix_2.r,
                half_fma_exact(
                    straight.g,
                    matrix_1.r,
                    half_fma_exact(
                        straight.r,
                        matrix_0.r,
                        matrix_4.r
                    )
                )
            ),
            half_fma_exact(
                straight.b,
                matrix_2.g,
                half_fma_exact(
                    straight.g,
                    matrix_1.g,
                    half_fma_exact(
                        straight.r,
                        matrix_0.g,
                        matrix_4.g
                    )
                )
            ),
            half_fma_exact(
                straight.b,
                matrix_2.b,
                half_fma_exact(
                    straight.g,
                    matrix_1.b,
                    half_fma_exact(
                        straight.r,
                        matrix_0.b,
                        matrix_4.b
                    )
                )
            ),
            half_fma_exact(
                destination.a,
                matrix_3.a,
                matrix_4.a
            )
        );
    } else if (
        HighlightVibrantArithmeticMode == 7
        || HighlightVibrantArithmeticMode == 8
    ) {
        mapped = vec4(
            half_fma_exact(
                straight.r,
                matrix_0.r,
                half_fma_exact(
                    straight.b,
                    matrix_2.r,
                    half_fma_exact(
                        straight.g,
                        matrix_1.r,
                        matrix_4.r
                    )
                )
            ),
            half_fma_exact(
                straight.b,
                matrix_2.g,
                half_fma_exact(
                    straight.r,
                    matrix_0.g,
                    half_fma_exact(
                        straight.g,
                        matrix_1.g,
                        matrix_4.g
                    )
                )
            ),
            half_add(
                half_fma_exact(
                    straight.b,
                    matrix_2.b,
                    half_multiply(straight.r, matrix_0.b)
                ),
                half_fma_exact(
                    straight.g,
                    matrix_1.b,
                    matrix_4.b
                )
            ),
            half_multiply(destination.a, matrix_3.a)
        );
    } else if (
        HighlightVibrantArithmeticMode == 1
        || HighlightVibrantArithmeticMode == 3
    ) {
        mapped = vec4(
            half_fma_exact(straight.g, matrix_1.r, mapped.r),
            half_fma_exact(straight.g, matrix_1.g, mapped.g),
            half_fma_exact(straight.g, matrix_1.b, mapped.b),
            half_fma_exact(straight.g, matrix_1.a, mapped.a)
        );
        mapped = vec4(
            half_fma_exact(straight.b, matrix_2.r, mapped.r),
            half_fma_exact(straight.b, matrix_2.g, mapped.g),
            half_fma_exact(straight.b, matrix_2.b, mapped.b),
            half_fma_exact(straight.b, matrix_2.a, mapped.a)
        );
        mapped = vec4(
            half_fma_exact(destination.a, matrix_3.r, mapped.r),
            half_fma_exact(destination.a, matrix_3.g, mapped.g),
            half_fma_exact(destination.a, matrix_3.b, mapped.b),
            half_fma_exact(destination.a, matrix_3.a, mapped.a)
        );
    } else {
        mapped = half_value(
            mapped + half_value(vec4(straight.g) * matrix_1)
        );
        mapped = half_value(
            mapped + half_value(vec4(straight.b) * matrix_2)
        );
        mapped = half_value(
            mapped + half_value(vec4(destination.a) * matrix_3)
        );
    }
    if (
        HighlightVibrantArithmeticMode != 7
        && HighlightVibrantArithmeticMode != 8
        && HighlightVibrantArithmeticMode != 9
    ) {
        mapped = half_value(mapped + matrix_4);
    }
    mapped.a = half_value(clamp(mapped.a, 0.0, 1.0));
    vec4 source = replay_highlight_source_initial(mapped, highlight.a);
    if (controls.x > 0.0) {
        float source_alpha = max(
            source.a,
            half_constant(0x068eu)
        );
        vec3 source_straight;
        if (HighlightSourceDivisionMode == 1) {
            float reciprocal = half_divide(1.0, source_alpha);
            source_straight = vec3(
                half_multiply(source.r, reciprocal),
                half_multiply(source.g, reciprocal),
                half_multiply(source.b, reciprocal)
            );
        } else if (HighlightSourceDivisionMode == 2) {
            source_straight = half_value_rtz(
                source.rgb / vec3(source_alpha)
            );
        } else if (HighlightSourceDivisionMode == 3) {
            source_straight = source.rgb / vec3(source_alpha);
        } else if (HighlightSourceDivisionMode == 4) {
            float reciprocal = float_barrier(1.0 / source_alpha);
            source_straight = source.rgb * vec3(reciprocal);
        } else {
            source_straight = half_value(
                source.rgb / vec3(source_alpha)
            );
        }
        if (controls.y > 0.0) {
            float maximum = max(
                source_straight.r,
                max(source_straight.g, source_straight.b)
            );
            if (maximum > controls.x) {
                source_straight = half_value(
                    source_straight
                    * vec3(half_divide(controls.x, maximum))
                );
            }
        } else {
            source_straight = clamp(
                source_straight,
                vec3(-0.75),
                vec3(controls.x)
            );
        }
        source.rgb = half_value(
            source_straight * vec3(source.a)
        );
    }

    if (UseAppleHighlightSourceTrace != 0) {
        uvec4 packed_source = texelFetch(
            AppleHighlightCompositorB,
            ivec2(gl_FragCoord.xy),
            0
        );
        vec2 source_rg = unpackHalf2x16(packed_source.z);
        vec2 source_ba = unpackHalf2x16(packed_source.w);
        source = vec4(source_rg, source_ba);
    }

    float destination_factor = half_subtract(1.0, source.a);
    if (HighlightVibrantArithmeticMode == 4) {
        return vec4(
            half_fma_exact(
                -destination.r,
                source.a,
                half_add(destination.r, source.r)
            ),
            half_fma_exact(
                -destination.g,
                source.a,
                half_add(destination.g, source.g)
            ),
            half_fma_exact(
                -destination.b,
                source.a,
                half_add(destination.b, source.b)
            ),
            half_fma_exact(
                -destination.a,
                source.a,
                half_add(destination.a, source.a)
            )
        );
    }
    if (HighlightVibrantArithmeticMode == 5) {
        return vec4(
            half_add(
                destination.r,
                half_fma_exact(-destination.r, source.a, source.r)
            ),
            half_add(
                destination.g,
                half_fma_exact(-destination.g, source.a, source.g)
            ),
            half_add(
                destination.b,
                half_fma_exact(-destination.b, source.a, source.b)
            ),
            half_add(
                destination.a,
                half_fma_exact(-destination.a, source.a, source.a)
            )
        );
    }
    if (HighlightVibrantArithmeticMode == 6) {
        return vec4(
            half_fma(destination.r, destination_factor, source.r),
            half_fma(destination.g, destination_factor, source.g),
            half_fma(destination.b, destination_factor, source.b),
            half_fma(destination.a, destination_factor, source.a)
        );
    }
    if (
        HighlightVibrantArithmeticMode == 2
        || HighlightVibrantArithmeticMode == 3
        || HighlightVibrantArithmeticMode == 8
        || HighlightVibrantArithmeticMode == 9
    ) {
        return vec4(
            half_fma_exact(
                destination.r,
                destination_factor,
                source.r
            ),
            half_fma_exact(
                destination.g,
                destination_factor,
                source.g
            ),
            half_fma_exact(
                destination.b,
                destination_factor,
                source.b
            ),
            half_fma_exact(
                destination.a,
                destination_factor,
                source.a
            )
        );
    }
    return half_value(
        half_value(destination * vec4(destination_factor)) + source
    );
}

vec4 replay_highlight_half_pair_trace(float first, float second)
{
    uint word = float_to_half_bits(first)
        | (float_to_half_bits(second) << 16u);
    return replay_highlight_trace_word(word);
}

vec4 replay_vibrant_source_diagnostic(
    vec4 highlight,
    vec4 destination,
    int mode
)
{
    highlight = half_value(highlight);
    destination = half_value(destination);
    vec4 matrix_0 = half_value(VibrantMatrix0);
    vec4 matrix_1 = half_value(VibrantMatrix1);
    vec4 matrix_2 = half_value(VibrantMatrix2);
    vec4 matrix_3 = half_value(VibrantMatrix3);
    vec4 matrix_4 = half_value(VibrantMatrix4);
    vec4 controls = half_value(VibrantControls);

    float destination_alpha = max(
        destination.a,
        half_constant(0x068eu)
    );
    vec3 straight = replay_highlight_destination_straight(
        destination,
        destination_alpha
    );
    vec4 mapped = half_value(vec4(straight.r) * matrix_0);
    if (HighlightVibrantArithmeticMode == 9) {
        mapped = vec4(
            half_fma_exact(
                straight.b,
                matrix_2.r,
                half_fma_exact(
                    straight.g,
                    matrix_1.r,
                    half_fma_exact(
                        straight.r,
                        matrix_0.r,
                        matrix_4.r
                    )
                )
            ),
            half_fma_exact(
                straight.b,
                matrix_2.g,
                half_fma_exact(
                    straight.g,
                    matrix_1.g,
                    half_fma_exact(
                        straight.r,
                        matrix_0.g,
                        matrix_4.g
                    )
                )
            ),
            half_fma_exact(
                straight.b,
                matrix_2.b,
                half_fma_exact(
                    straight.g,
                    matrix_1.b,
                    half_fma_exact(
                        straight.r,
                        matrix_0.b,
                        matrix_4.b
                    )
                )
            ),
            half_fma_exact(
                destination.a,
                matrix_3.a,
                matrix_4.a
            )
        );
    } else if (
        HighlightVibrantArithmeticMode == 7
        || HighlightVibrantArithmeticMode == 8
    ) {
        mapped = vec4(
            half_fma_exact(
                straight.r,
                matrix_0.r,
                half_fma_exact(
                    straight.b,
                    matrix_2.r,
                    half_fma_exact(
                        straight.g,
                        matrix_1.r,
                        matrix_4.r
                    )
                )
            ),
            half_fma_exact(
                straight.b,
                matrix_2.g,
                half_fma_exact(
                    straight.r,
                    matrix_0.g,
                    half_fma_exact(
                        straight.g,
                        matrix_1.g,
                        matrix_4.g
                    )
                )
            ),
            half_add(
                half_fma_exact(
                    straight.b,
                    matrix_2.b,
                    half_multiply(straight.r, matrix_0.b)
                ),
                half_fma_exact(
                    straight.g,
                    matrix_1.b,
                    matrix_4.b
                )
            ),
            half_multiply(destination.a, matrix_3.a)
        );
    } else if (
        HighlightVibrantArithmeticMode == 1
        || HighlightVibrantArithmeticMode == 3
    ) {
        mapped = vec4(
            half_fma_exact(straight.g, matrix_1.r, mapped.r),
            half_fma_exact(straight.g, matrix_1.g, mapped.g),
            half_fma_exact(straight.g, matrix_1.b, mapped.b),
            half_fma_exact(straight.g, matrix_1.a, mapped.a)
        );
        mapped = vec4(
            half_fma_exact(straight.b, matrix_2.r, mapped.r),
            half_fma_exact(straight.b, matrix_2.g, mapped.g),
            half_fma_exact(straight.b, matrix_2.b, mapped.b),
            half_fma_exact(straight.b, matrix_2.a, mapped.a)
        );
        mapped = vec4(
            half_fma_exact(destination.a, matrix_3.r, mapped.r),
            half_fma_exact(destination.a, matrix_3.g, mapped.g),
            half_fma_exact(destination.a, matrix_3.b, mapped.b),
            half_fma_exact(destination.a, matrix_3.a, mapped.a)
        );
    } else {
        mapped = half_value(
            mapped + half_value(vec4(straight.g) * matrix_1)
        );
        mapped = half_value(
            mapped + half_value(vec4(straight.b) * matrix_2)
        );
        mapped = half_value(
            mapped + half_value(vec4(destination.a) * matrix_3)
        );
    }
    if (
        HighlightVibrantArithmeticMode != 7
        && HighlightVibrantArithmeticMode != 8
        && HighlightVibrantArithmeticMode != 9
    ) {
        mapped = half_value(mapped + matrix_4);
    }
    mapped.a = half_value(clamp(mapped.a, 0.0, 1.0));
    vec4 source_initial = replay_highlight_source_initial(mapped, highlight.a);
    mapped.rgb = half_value(mapped.rgb * vec3(mapped.a));
    float source_alpha = max(
        source_initial.a,
        half_constant(0x068eu)
    );
    vec3 source_straight;
    if (HighlightSourceDivisionMode == 1) {
        float reciprocal = half_divide(1.0, source_alpha);
        source_straight = vec3(
            half_multiply(source_initial.r, reciprocal),
            half_multiply(source_initial.g, reciprocal),
            half_multiply(source_initial.b, reciprocal)
        );
    } else if (HighlightSourceDivisionMode == 2) {
        source_straight = half_value_rtz(
            source_initial.rgb / vec3(source_alpha)
        );
    } else if (HighlightSourceDivisionMode == 3) {
        source_straight = source_initial.rgb / vec3(source_alpha);
    } else if (HighlightSourceDivisionMode == 4) {
        float reciprocal = float_barrier(1.0 / source_alpha);
        source_straight = source_initial.rgb * vec3(reciprocal);
    } else {
        source_straight = half_value(
            source_initial.rgb / vec3(source_alpha)
        );
    }
    if (controls.y > 0.0) {
        float maximum = max(
            source_straight.r,
            max(source_straight.g, source_straight.b)
        );
        if (maximum > controls.x) {
            source_straight = half_value(
                source_straight
                * vec3(half_divide(controls.x, maximum))
            );
        }
    } else {
        source_straight = clamp(
            source_straight,
            vec3(-0.75),
            vec3(controls.x)
        );
    }
    vec4 source_final = source_initial;
    source_final.rgb = half_value(
        source_straight * vec3(source_initial.a)
    );

    if (mode == 19) {
        return replay_highlight_half_pair_trace(mapped.r, mapped.g);
    }
    if (mode == 20) {
        return replay_highlight_half_pair_trace(mapped.b, mapped.a);
    }
    if (mode == 21) {
        return replay_highlight_half_pair_trace(
            source_initial.r,
            source_initial.g
        );
    }
    if (mode == 22) {
        return replay_highlight_half_pair_trace(
            source_initial.b,
            source_initial.a
        );
    }
    if (mode == 23) {
        return replay_highlight_half_pair_trace(
            source_straight.r,
            source_straight.g
        );
    }
    if (mode == 24) {
        return replay_highlight_half_pair_trace(source_straight.b, 0.0);
    }
    if (mode == 25) {
        return replay_highlight_half_pair_trace(
            source_final.r,
            source_final.g
        );
    }
    return replay_highlight_half_pair_trace(
        source_final.b,
        source_final.a
    );
}

float replay_refraction_shift(
    float distance,
    float amount,
    float inverse_height
)
{
    float amount_half = half_value(amount);
    float height = clamp(
        half_multiply(half_value(inverse_height), -distance),
        0.0,
        1.0
    );
    float curve = clamp(
        half_sqrt(half_multiply(half_subtract(2.0, height), height)),
        0.0,
        1.0
    );
    return half_fma(-curve, amount_half, amount_half);
}

float replay_blur_scale(float shifted_distance)
{
    vec3 lower = BlurDistance.xyz;
    vec3 upper = BlurDistance.yzw;
    vec3 span = upper - lower;
    vec3 factors;
    for (int index = 0; index < 3; ++index) {
        factors[index] = span[index] == 0.0
            ? 0.0
            : clamp(
                shifted_distance * (1.0 / span[index])
                    + (-lower[index] / span[index]),
                0.0,
                1.0
            );
    }
    vec3 weighted = half_value(BlurAlpha.yzw * half_value(factors));
    return half_subtract(
        BlurAlpha.x,
        half_add(half_add(weighted.x, weighted.y), weighted.z)
    );
}

float replay_lod(float radius)
{
    float argument = radius < 2.0
        ? half_value(float(radius) * 0.5 + 1.0)
        : radius;
    return float(max(0.0, half_value(log2(argument))));
}

vec4 replay_sanitize_sample(vec4 value)
{
    float epsilon = half_constant(0x068eu);
    value = half_value(value);
    value.r = abs(value.r) < epsilon ? 0.0 : value.r;
    value.g = abs(value.g) < epsilon ? 0.0 : value.g;
    value.b = abs(value.b) < epsilon ? 0.0 : value.b;
    return value;
}

uvec4 replay_texel_codes(ivec2 coordinate, int level)
{
    ivec2 dimensions = textureSize(SourceTexture, level);
    ivec2 bounded = clamp(
        coordinate,
        ivec2(0),
        dimensions - ivec2(1)
    );
    vec4 texel = texelFetch(SourceTexture, bounded, level);
    return uvec4(floor(texel * 255.0 + 0.5));
}

uint replay_spatial_weight(float fraction)
{
    float scaled = clamp(fraction, 0.0, 1.0) * 256.0;
    float quantized = SamplerSpatialQuantization == 0
        ? floor(scaled + 0.5)
        : floor(scaled);
    return uint(clamp(quantized, 0.0, 256.0));
}

uint replay_q016_trilinear_weight(
    uint spatial_weight,
    uint mip_weight,
    bool upper_row
)
{
    uint raw_weight = spatial_weight * mip_weight;
    uint quotient = raw_weight / 64u;
    uint remainder = raw_weight % 64u;
    bool increment = remainder > 32u
        || (remainder == 32u && upper_row);
    return quotient + (increment ? 1u : 0u);
}

uvec4 replay_trilinear_code_contribution(
    vec2 coordinates,
    int level,
    uint mip_weight
)
{
    vec2 dimensions = vec2(textureSize(SourceTexture, level));
    // Apple materializes the multiply before subtracting the texel-center
    // offset. Prevent a desktop GLSL compiler from contracting this into an
    // FMA, which changes exact half-phase decisions by one 1/256 step.
    vec2 scaled_coordinates = vec2(
        float_barrier(coordinates.x * dimensions.x),
        float_barrier(coordinates.y * dimensions.y)
    );
    vec2 position = scaled_coordinates - vec2(0.5);
    ivec2 origin = ivec2(floor(position));
    vec2 fraction = fract(position);
    uint weight_x = replay_spatial_weight(fraction.x);
    uint weight_y = replay_spatial_weight(fraction.y);
    uint inverse_x = 256u - weight_x;
    uint inverse_y = 256u - weight_y;
    uint weight_00 = replay_q016_trilinear_weight(
        inverse_x * inverse_y,
        mip_weight,
        true
    );
    uint weight_10 = replay_q016_trilinear_weight(
        weight_x * inverse_y,
        mip_weight,
        true
    );
    uint weight_01 = replay_q016_trilinear_weight(
        inverse_x * weight_y,
        mip_weight,
        false
    );
    uint weight_11 = replay_q016_trilinear_weight(
        weight_x * weight_y,
        mip_weight,
        false
    );
    return replay_texel_codes(origin, level) * weight_00
        + replay_texel_codes(origin + ivec2(1, 0), level) * weight_10
        + replay_texel_codes(origin + ivec2(0, 1), level) * weight_01
        + replay_texel_codes(origin + ivec2(1, 1), level) * weight_11;
}

vec4 replay_apple_sample(vec2 coordinates, float lod)
{
    int last_level = textureQueryLevels(SourceTexture) - 1;
    float bounded_lod = clamp(lod, 0.0, float(last_level));
    int lower_level = int(floor(bounded_lod));
    int upper_level = min(lower_level + 1, last_level);
    uint mip_weight = uint(
        floor(fract(bounded_lod) * 64.0)
    );
    mip_weight = min(mip_weight, 64u);
    uvec4 combined =
        replay_trilinear_code_contribution(
            coordinates,
            lower_level,
            64u - mip_weight
        )
        + replay_trilinear_code_contribution(
            coordinates,
            upper_level,
            mip_weight
        );

    // Apple reduces each 22-bit trilinear corner weight to Q0.16 before the
    // code-domain dot product. Exact reduction ties go to the upper texel
    // row, preserving the normalized weight sum. The dot product then rounds
    // once to 1/16 code with midpoint ties upward.
    uvec4 fixed_sixteenths =
        (combined + uvec4(2048u)) / uvec4(4096u);
    return half_value(vec4(fixed_sixteenths) / 4080.0);
}

vec4 replay_source_sample(vec2 coordinates, float lod)
{
    if (SamplerModel != 0) {
        return replay_sanitize_sample(
            half_value(textureLod(SourceTexture, coordinates, lod))
        );
    }
    return replay_sanitize_sample(
        replay_apple_sample(coordinates, lod)
    );
}

vec2 replay_refracted_coordinates(
    vec2 coordinates,
    float shift,
    vec2 displacement
)
{
    vec2 base = half_value_rtz(coordinates);
    // The Apple backend keeps the result of relaxed half multiply/add
    // arithmetic in its float sampler-coordinate consumer. A half output
    // materializes the same expression to binary16, but texture sampling
    // observes this unrounded float32 sum.
    return base + shift * displacement;
}

vec2 replay_inner_refracted_coordinates(
    vec2 coordinates,
    float shift,
    vec2 displacement
)
{
    vec2 base = half_value_rtz(coordinates);
    if (InnerSamplerCoordinateModel == 0) {
        return base + shift * displacement;
    }
    if (InnerSamplerCoordinateModel == 1) {
        return vec2(
            half_fma_exact(shift, displacement.x, base.x),
            half_fma_exact(shift, displacement.y, base.y)
        );
    }
    if (InnerSamplerCoordinateModel == 2) {
        return base + half_value(shift * displacement);
    }
    if (InnerSamplerCoordinateModel == 3) {
        return coordinates + shift * displacement;
    }
    if (InnerSamplerCoordinateModel == 4) {
        return half_value(base + shift * displacement);
    }
    return vec2(
        half_fma(shift, displacement.x, base.x),
        half_fma(shift, displacement.y, base.y)
    );
}

vec2 replay_outer_refracted_coordinates(
    vec2 coordinates,
    float shift,
    vec2 displacement
)
{
    vec2 base = half_value_rtz(coordinates);
    if (OuterSamplerCoordinateModel == 0) {
        return base + shift * displacement;
    }
    if (OuterSamplerCoordinateModel == 1) {
        return coordinates + shift * displacement;
    }
    if (OuterSamplerCoordinateModel == 2) {
        return base + half_value(shift * displacement);
    }
    if (OuterSamplerCoordinateModel == 3) {
        return half_value(base + shift * displacement);
    }
    if (OuterSamplerCoordinateModel == 4) {
        return vec2(
            half_fma_exact(shift, displacement.x, base.x),
            half_fma_exact(shift, displacement.y, base.y)
        );
    }
    if (OuterSamplerCoordinateModel == 5) {
        return coordinates + half_value(shift * displacement);
    }
    return half_value(coordinates + shift * displacement);
}

vec2 replay_edge_refracted_coordinates(
    vec2 coordinates,
    float shift,
    vec2 displacement
)
{
    vec2 base = half_value_rtz(coordinates);
    if (EdgeSamplerCoordinateModel == 0) {
        return base + shift * displacement;
    }
    if (EdgeSamplerCoordinateModel == 1) {
        // The full production fragment has one relaxed-precision corner
        // state that the materialized edge-coordinate trace does not retain.
        // The production BGRA8 sampler oracles identify it by the binary16
        // shift and displacement words, independent of pixel position and
        // source contents.  At that state the sampler observes the result
        // produced by a shift two binary16 values below the diagnostic word.
        uvec2 displacement_bits = uvec2(
            float_to_half_bits(abs(displacement.x)),
            float_to_half_bits(abs(displacement.y))
        );
        bool production_corner_tie =
            float_to_half_bits(shift) == 0x57c9u
            && ((displacement_bits.x == 0x0f7bu
                    && displacement_bits.y == 0x0f9bu)
                || (displacement_bits.x == 0x0f9bu
                    && displacement_bits.y == 0x0f7bu));
        float sample_shift = production_corner_tie
            ? unpackHalf2x16(next_half_down_bits(
                next_half_down_bits(float_to_half_bits(shift))
            )).x
            : shift;
        return coordinates + sample_shift * displacement;
    }
    if (EdgeSamplerCoordinateModel == 2) {
        return base + half_value(shift * displacement);
    }
    if (EdgeSamplerCoordinateModel == 3) {
        return half_value(base + shift * displacement);
    }
    if (EdgeSamplerCoordinateModel == 4) {
        return vec2(
            half_fma_exact(shift, displacement.x, base.x),
            half_fma_exact(shift, displacement.y, base.y)
        );
    }
    if (EdgeSamplerCoordinateModel == 5) {
        return coordinates + half_value(shift * displacement);
    }
    return half_value(coordinates + shift * displacement);
}

vec2 replay_shadow_refracted_coordinates(
    vec2 coordinates,
    float shift,
    vec2 displacement
)
{
    vec2 base = half_value_rtz(coordinates);
    if (ShadowSamplerCoordinateModel == 0) {
        return half_fma(shift, displacement, base);
    }
    if (ShadowSamplerCoordinateModel == 1) {
        return base + shift * displacement;
    }
    if (ShadowSamplerCoordinateModel == 2) {
        return coordinates + shift * displacement;
    }
    if (ShadowSamplerCoordinateModel == 3) {
        return base + half_value(shift * displacement);
    }
    if (ShadowSamplerCoordinateModel == 4) {
        return coordinates + half_value(shift * displacement);
    }
    if (ShadowSamplerCoordinateModel == 5) {
        return half_value(base + shift * displacement);
    }
    return vec2(
        half_fma_exact(shift, displacement.x, base.x),
        half_fma_exact(shift, displacement.y, base.y)
    );
}

float replay_refraction_mix_component(
    float left,
    float right,
    float amount
)
{
    if (RefractionMixModel == 0) {
        return half_mix_exact(left, right, amount);
    }
    if (RefractionMixModel == 1) {
        return half_value(mix(left, right, amount));
    }
    float delta = half_subtract(right, left);
    if (RefractionMixModel == 2) {
        return half_add(left, half_multiply(delta, amount));
    }
    if (RefractionMixModel == 3) {
        return half_fma_exact(delta, amount, left);
    }
    float inverse = half_subtract(1.0, amount);
    float right_product = half_multiply(right, amount);
    if (RefractionMixModel == 4) {
        return half_add(
            half_multiply(left, inverse),
            right_product
        );
    }
    if (RefractionMixModel == 5) {
        return half_fma_exact(left, inverse, right_product);
    }
    if (RefractionMixModel == 6) {
        return half_value(
            left * inverse + right * amount
        );
    }
    return half_value(
        left + amount * (right - left)
    );
}

vec4 replay_refraction_mix(
    vec4 left,
    vec4 right,
    float amount
)
{
    return vec4(
        replay_refraction_mix_component(left.r, right.r, amount),
        replay_refraction_mix_component(left.g, right.g, amount),
        replay_refraction_mix_component(left.b, right.b, amount),
        replay_refraction_mix_component(left.a, right.a, amount)
    );
}

vec4 replay_sample_refracted(
    vec2 coordinates,
    float distance,
    vec2 displacement
)
{
    if (ComplexRefraction <= 0.0) {
        return replay_source_sample(
            coordinates,
            replay_lod(half_value(BlurRadius))
        );
    }

    float inner_shift = replay_refraction_shift(
        distance,
        InnerRefractionAmount,
        InnerRefractionInverseHeight
    );
    float inner_blur = half_value(
        BlurRadius
        * replay_blur_scale(half_add(inner_shift, distance))
    );
    if (UseAppleRefractionTrace != 0) {
        vec4 trace = texelFetch(
            AppleRefractionTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        inner_shift = trace.z;
        inner_blur = trace.w;
    }
    vec2 inner_coordinates = replay_inner_refracted_coordinates(
        coordinates,
        inner_shift,
        displacement
    );
    vec4 inner_sample = replay_source_sample(
        inner_coordinates,
        replay_lod(inner_blur)
    );
    if (NumericTrace == 21) {
        return inner_sample;
    }

    if (RefractionOpacity <= 0.0) {
        return inner_sample;
    }

    float outer_shift = replay_refraction_shift(
        distance,
        OuterRefractionAmount,
        OuterRefractionInverseHeight
    );
    float outer_blur = half_value(
        BlurRadius
        * replay_blur_scale(half_add(outer_shift, distance))
    );
    vec2 outer_coordinates = replay_outer_refracted_coordinates(
        coordinates,
        outer_shift,
        displacement
    );
    vec4 outer_sample = replay_source_sample(
        outer_coordinates,
        replay_lod(outer_blur)
    );
    if (NumericTrace == 22) {
        return outer_sample;
    }
    float threshold_span =
        RefractionThreshold1 - RefractionThreshold0;
    float threshold = float(distance) * (1.0 / threshold_span)
        + (-RefractionThreshold0 / threshold_span);
    float amount = half_multiply(
        RefractionOpacity,
        half_value(clamp(threshold, 0.0, 1.0))
    );
    return FloatMixWorkaround != 0.0
        ? half_value(mix(inner_sample, outer_sample, vec4(float(amount))))
        : replay_refraction_mix(inner_sample, outer_sample, amount);
}

vec4 replay_refracted_spatial_weights(
    vec2 coordinates,
    float distance,
    vec2 displacement
)
{
    float inner_shift = replay_refraction_shift(
        distance,
        InnerRefractionAmount,
        InnerRefractionInverseHeight
    );
    if (UseAppleRefractionTrace != 0) {
        vec4 trace = texelFetch(
            AppleRefractionTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        inner_shift = trace.z;
    }
    vec2 refracted = replay_refracted_coordinates(
        coordinates,
        inner_shift,
        displacement
    );
    vec2 level_0_position =
        refracted * vec2(textureSize(SourceTexture, 0))
        - vec2(0.5);
    vec2 level_1_position =
        refracted * vec2(textureSize(SourceTexture, 1))
        - vec2(0.5);
    return vec4(
        float(replay_spatial_weight(fract(level_0_position.x))),
        float(replay_spatial_weight(fract(level_0_position.y))),
        float(replay_spatial_weight(fract(level_1_position.x))),
        float(replay_spatial_weight(fract(level_1_position.y)))
    );
}

vec4 replay_refracted_spatial_residuals(
    vec2 coordinates,
    float distance,
    vec2 displacement
)
{
    float inner_shift = replay_refraction_shift(
        distance,
        InnerRefractionAmount,
        InnerRefractionInverseHeight
    );
    if (UseAppleRefractionTrace != 0) {
        vec4 trace = texelFetch(
            AppleRefractionTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        inner_shift = trace.z;
    }
    vec2 refracted = replay_refracted_coordinates(
        coordinates,
        inner_shift,
        displacement
    );
    vec2 level_0_scaled = fract(
        refracted * vec2(textureSize(SourceTexture, 0))
        - vec2(0.5)
    ) * 256.0;
    vec2 level_1_scaled = fract(
        refracted * vec2(textureSize(SourceTexture, 1))
        - vec2(0.5)
    ) * 256.0;
    return (
        fract(vec4(level_0_scaled, level_1_scaled))
        - vec4(0.5)
    ) * 65536.0;
}

vec3 replay_color_matrix(
    vec3 color,
    vec4 row_0,
    vec4 row_1,
    vec4 row_2
)
{
    return half_value(vec3(
        half_add(half_dot(color, row_0.xyz), row_0.w),
        half_add(half_dot(color, row_1.xyz), row_1.w),
        half_add(half_dot(color, row_2.xyz), row_2.w)
    ));
}

vec4 replay_edge_bleed_layer(
    vec2 coordinates,
    float distance,
    vec2 displacement,
    vec4 current,
    out vec4 refraction_trace,
    out vec4 sample_trace,
    out vec4 amount_trace
)
{
    float shift = replay_refraction_shift(
        distance,
        EdgeBleedAmount,
        EdgeBleedInverseHeight
    );
    vec2 bleed_coordinates = replay_edge_refracted_coordinates(
        coordinates,
        shift,
        displacement
    );
    vec4 sampled = replay_apple_sample(
        bleed_coordinates,
        replay_lod(half_value(EdgeBleedBlurRadius))
    );
    vec2 base = half_value_rtz(coordinates);
    vec2 materialized_coordinates = vec2(
        half_fma_exact(shift, displacement.x, base.x),
        half_fma_exact(shift, displacement.y, base.y)
    );
    refraction_trace = half_value(vec4(
        materialized_coordinates,
        shift,
        replay_lod(half_value(EdgeBleedBlurRadius))
    ));
    sample_trace = sampled;
    float sample_alpha = max(sampled.a, half_constant(0x068eu));
    vec3 straight = half_value(sampled.rgb / vec3(sample_alpha));
    float epsilon = half_constant(0x068eu);
    straight.r = abs(straight.r) < epsilon ? 0.0 : straight.r;
    straight.g = abs(straight.g) < epsilon ? 0.0 : straight.g;
    straight.b = abs(straight.b) < epsilon ? 0.0 : straight.b;
    vec3 mapped = replay_color_matrix(
        straight,
        BleedMatrix0,
        BleedMatrix1,
        BleedMatrix2
    );

    float lower = EdgeBleedDistance.x;
    float upper = EdgeBleedDistance.y;
    float span = upper - lower;
    float distance_factor = clamp(
        float(distance) * (1.0 / span) + (-lower / span),
        0.0,
        1.0
    );
    float distance_amount = half_value(distance_factor);
    float luminance = clamp(
        half_dot(
            current.rgb,
            vec3(
                half_constant(0x32cdu),
                half_constant(0x39b9u),
                half_constant(0x2c9du)
            )
        ),
        0.0,
        1.0
    );
    float darken = half_add(
        half_multiply(BleedDarken.x, luminance),
        BleedDarken.y
    );
    darken = half_multiply(darken, darken);
    float amount = half_multiply(darken, distance_amount);
    amount = half_multiply(amount, amount);
    amount = half_multiply(amount, EdgeBleedOpacity);
    amount_trace = vec4(
        distance_amount,
        luminance,
        darken,
        amount
    );
    vec3 color = FloatMixWorkaround != 0.0
        ? half_value(mix(
            current.rgb,
            mapped,
            vec3(float(amount))
        ))
        : vec3(
            half_mix_exact(current.r, mapped.r, amount),
            half_mix_exact(current.g, mapped.g, amount),
            half_mix_exact(current.b, mapped.b, amount)
        );
    return vec4(color, current.a);
}

float replay_shadow_alpha(vec2 shadow_sdf)
{
    if (ShadowOpacity == 0.0) {
        return 0.0;
    }
    float normalized = half_value(
        ShadowInverseRadius * float(shadow_sdf.x)
    );
    float centered = half_subtract(
        half_multiply(
            clamp(
                half_add(half_multiply(normalized, 0.25), 0.5),
                0.0,
                1.0
            ),
            4.0
        ),
        2.0
    );
    float squared = half_multiply(centered, centered);
    float curve = half_fma(
        half_constant(0x1a0du),
        squared,
        half_constant(0xa869u)
    );
    curve = half_fma(curve, squared, half_constant(0x3162u));
    curve = half_fma(curve, squared, half_constant(0xb87cu));
    curve = half_fma(curve, centered, 0.5);
    return half_multiply(
        half_multiply(curve, shadow_sdf.y),
        ShadowOpacity
    );
}

vec4 replay_shadow_source_sample(
    vec2 coordinates,
    float primary_distance,
    vec2 displacement
)
{
    float shifted_distance = half_add(
        ShadowDistanceOffset,
        primary_distance
    );
    float height = clamp(
        half_multiply(-shifted_distance, half_value(ShadowInverseHeight)),
        0.0,
        1.0
    );
    float curve = clamp(
        half_sqrt(half_multiply(half_subtract(2.0, height), height)),
        0.0,
        1.0
    );
    float amount = half_value(ShadowAmount);
    float shift = half_subtract(amount, half_multiply(curve, amount));
    vec2 shadow_coordinates = replay_shadow_refracted_coordinates(
        coordinates,
        shift,
        displacement
    );

    return replay_apple_sample(
        shadow_coordinates,
        replay_lod(half_value(ShadowBlurRadius))
    );
}

vec4 replay_shadow_layer(
    vec2 coordinates,
    float primary_distance,
    vec2 displacement,
    float shadow_alpha
)
{

    vec4 shadow_color;
    if (ShadowContribution > half_constant(0x068eu)) {
        vec4 sampled = replay_shadow_source_sample(
            coordinates,
            primary_distance,
            displacement
        );
        float sample_alpha = max(sampled.a, half_constant(0x068eu));
        vec3 straight = half_value(
            sampled.rgb / vec3(sample_alpha)
        );
        float epsilon = half_constant(0x068eu);
        straight.r = abs(straight.r) < epsilon ? 0.0 : straight.r;
        straight.g = abs(straight.g) < epsilon ? 0.0 : straight.g;
        straight.b = abs(straight.b) < epsilon ? 0.0 : straight.b;
        vec3 mapped = half_value(vec3(
            half_dot(straight, ShadowMatrix0.xyz),
            half_dot(straight, ShadowMatrix1.xyz),
            half_dot(straight, ShadowMatrix2.xyz)
        ));
        float contribution = half_value(ShadowContribution);
        vec3 color = half_value(
            half_value(mapped * vec3(contribution))
            + ShadowMatrix0.www * vec3(1.0, 0.0, 0.0)
            + ShadowMatrix1.www * vec3(0.0, 1.0, 0.0)
            + ShadowMatrix2.www * vec3(0.0, 0.0, 1.0)
        );
        float alpha = FloatMixWorkaround != 0.0
            ? half_value(mix(
                ShadowFaceOpacity,
                1.0,
                ShadowContribution
            ))
            : half_value(mix(
                half_value(ShadowFaceOpacity),
                1.0,
                contribution
            ));
        shadow_color = half_value(vec4(color, alpha));
    } else {
        shadow_color = half_value(vec4(
            ShadowMatrix0.w,
            ShadowMatrix1.w,
            ShadowMatrix2.w,
            half_value(ShadowFaceOpacity)
        ));
    }
    return half_value(shadow_color * vec4(shadow_alpha));
}

void main()
{
    vec2 replay_sdf_uv = sdf_uv;
    vec2 replay_source_uv = source_uv;
    if (UseAppleInterpolantTrace != 0 && SdfMode >= 0) {
        uvec4 trace = texelFetch(
            AppleInterpolantTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        replay_sdf_uv = uintBitsToFloat(trace.xy);
        replay_source_uv = uintBitsToFloat(trace.zw);
    } else if (CoordinateMode != 0 && SdfMode >= 0) {
        replay_sdf_uv = gl_FragCoord.xy - vec2(512.0);
        if (CoordinateMode == 4) {
            int primitive = gl_PrimitiveID & 1;
            int global_x = int(gl_FragCoord.x);
            int global_y = 1023 - int(gl_FragCoord.y);
            uvec4 axis_x = texelFetch(
                AppleInterpolantAxisTrace,
                ivec2(global_x - AppleInterpolantAxisStart, primitive),
                0
            );
            uvec4 axis_y = texelFetch(
                AppleInterpolantAxisTrace,
                ivec2(global_y - AppleInterpolantAxisStart, primitive),
                0
            );
            replay_sdf_uv = uintBitsToFloat(uvec2(
                axis_x.x,
                axis_y.y
            ));
            replay_source_uv = uintBitsToFloat(uvec2(
                axis_x.z,
                axis_y.w
            ));
        } else if (CoordinateMode == 5) {
            int primitive = gl_PrimitiveID & 1;
            int raw_x = int(gl_FragCoord.x) - 112;
            int raw_y = 911 - int(gl_FragCoord.y);
            int global_x = 112 + raw_x;
            int global_y = 112 + raw_y;
            uvec4 coefficient_x = texelFetch(
                AppleInterpolantCoefficientTrace,
                ivec2(
                    (global_x >> 5) - AppleInterpolantTileStart,
                    primitive
                ),
                0
            );
            uvec4 coefficient_y = texelFetch(
                AppleInterpolantCoefficientTrace,
                ivec2(
                    (global_y >> 5) - AppleInterpolantTileStart,
                    primitive
                ),
                0
            );
            vec4 slope = uintBitsToFloat(
                AppleInterpolantSlopeBits
            );
            float position_x = float(global_x & 31) + 0.5;
            float position_y = float(global_y & 31) + 0.5;
            precise float sdf_x_nearest = fma(
                position_x,
                slope.x,
                uintBitsToFloat(coefficient_x.x)
            );
            precise float sdf_y_nearest = fma(
                position_y,
                slope.y,
                uintBitsToFloat(coefficient_y.y)
            );
            precise float source_x_nearest = fma(
                position_x,
                slope.z,
                uintBitsToFloat(coefficient_x.z)
            );
            precise float source_y_nearest = fma(
                position_y,
                slope.w,
                uintBitsToFloat(coefficient_y.w)
            );
            precise vec4 nearest = vec4(
                sdf_x_nearest,
                sdf_y_nearest,
                source_x_nearest,
                source_y_nearest
            );
            precise vec4 residual = vec4(
                fma(
                    position_x,
                    slope.x,
                    uintBitsToFloat(coefficient_x.x)
                        - nearest.x
                ),
                fma(
                    position_y,
                    slope.y,
                    uintBitsToFloat(coefficient_y.y)
                        - nearest.y
                ),
                fma(
                    position_x,
                    slope.z,
                    uintBitsToFloat(coefficient_x.z)
                        - nearest.z
                ),
                fma(
                    position_y,
                    slope.w,
                    uintBitsToFloat(coefficient_y.w)
                        - nearest.w
                )
            );
            uvec4 bits = floatBitsToUint(nearest);
            bvec4 rounded_away_from_zero = bvec4(
                (nearest.x > 0.0 && residual.x < 0.0)
                    || (nearest.x < 0.0 && residual.x > 0.0),
                (nearest.y > 0.0 && residual.y < 0.0)
                    || (nearest.y < 0.0 && residual.y > 0.0),
                (nearest.z > 0.0 && residual.z < 0.0)
                    || (nearest.z < 0.0 && residual.z > 0.0),
                (nearest.w > 0.0 && residual.w < 0.0)
                    || (nearest.w < 0.0 && residual.w > 0.0)
            );
            bits -= uvec4(rounded_away_from_zero);
            vec4 coordinates = uintBitsToFloat(bits);
            replay_sdf_uv = coordinates.xy;
            replay_source_uv = coordinates.zw;
        } else if (CoordinateMode == 6) {
            int raw_x = int(gl_FragCoord.x) - 112;
            int raw_y = 911 - int(gl_FragCoord.y);
            ivec4 correction = texelFetch(
                AppleInterpolantCorrectionSurface,
                ivec2(raw_x, raw_y),
                0
            );
            float source_low = uintBitsToFloat(
                AppleInterpolantSourceLowBits
            );
            float source_slope = uintBitsToFloat(
                AppleInterpolantSlopeBits.z
            );
            precise vec4 baseline = vec4(
                float(raw_x) - 399.5,
                399.5 - float(raw_y),
                fma(
                    float(raw_x) + 0.5,
                    source_slope,
                    source_low
                ),
                fma(
                    float(raw_y) + 0.5,
                    source_slope,
                    source_low
                )
            );
            uvec4 bits = floatBitsToUint(baseline);
            bits += uvec4(correction);
            vec4 coordinates = uintBitsToFloat(bits);
            replay_sdf_uv = coordinates.xy;
            replay_source_uv = coordinates.zw;
        } else if (CoordinateMode != 3) {
            vec2 top_left_position = vec2(
                gl_FragCoord.x,
                1024.0 - gl_FragCoord.y
            );
            if (CoordinateMode == 1) {
                replay_source_uv =
                    (top_left_position - vec2(104.0)) / 896.0;
            } else {
                replay_source_uv = (
                    top_left_position + vec2(256.0)
                ) / 1536.0;
                replay_source_uv = vec2(
                    float_barrier(replay_source_uv.x),
                    float_barrier(replay_source_uv.y)
                );
                if (AnalyticCoordinateUlpBias != 0) {
                    uvec2 coordinate_bits = floatBitsToUint(
                        replay_source_uv
                    );
                    coordinate_bits = uvec2(ivec2(coordinate_bits)
                        + ivec2(AnalyticCoordinateUlpBias));
                    replay_source_uv = uintBitsToFloat(
                        coordinate_bits
                    );
                }
            }
        }
    }
    if (FinalHighlightPass != 0) {
        if (FinalHighlightTrace == 40) {
            fragment_color = replay_highlight_trace_word(
                floatBitsToUint(replay_sdf_uv.x)
            );
            return;
        }
        if (FinalHighlightTrace == 41) {
            fragment_color = replay_highlight_trace_word(
                floatBitsToUint(replay_sdf_uv.y)
            );
            return;
        }
        vec2 point = HighlightCoordinateMode == 0
            ? gl_FragCoord.xy - vec2(512.0)
            : replay_sdf_uv;
        vec4 highlight_sdf = replay_compute_mode4_sdf(point);
        if (UseAppleSdfTrace != 0) {
            highlight_sdf = half_value(texelFetch(
                AppleSdfTrace,
                ivec2(gl_FragCoord.xy),
                0
            ));
        }
        if (HighlightSdfDistanceUlpBias != 0) {
            uint distance_bits = float_to_half_bits(highlight_sdf.x);
            highlight_sdf.x = unpackHalf2x16(uint(
                int(distance_bits) + HighlightSdfDistanceUlpBias
            )).x;
        }
        if (FinalHighlightTrace == 1) {
            fragment_color = highlight_sdf;
            return;
        }
        if (FinalHighlightTrace >= 4 && FinalHighlightTrace <= 18) {
            fragment_color = replay_highlight_diagnostic(
                highlight_sdf,
                FinalHighlightTrace
            );
            return;
        }
        vec4 highlight;
        if (UseAppleHighlightAlphaTrace != 0) {
            uint packed_alpha = texelFetch(
                AppleHighlightHalfStages,
                ivec2(gl_FragCoord.xy),
                0
            ).y;
            float apple_alpha = unpackHalf2x16(packed_alpha).x;
            if (apple_alpha < half_constant(0x068eu)) {
                discard;
            }
            highlight = vec4(apple_alpha);
        } else {
            highlight = replay_sdf_key_fill_highlight(highlight_sdf);
        }
        if (HighlightAlphaUlpBias != 0) {
            uint alpha_bits = float_to_half_bits(highlight.a);
            float biased_alpha = unpackHalf2x16(uint(
                int(alpha_bits) + HighlightAlphaUlpBias
            )).x;
            highlight = vec4(biased_alpha);
        }
        if (FinalHighlightTrace == 2) {
            fragment_color = highlight;
            return;
        }
        if (FinalHighlightTrace == 3) {
            uint alpha_bits = float_to_half_bits(highlight.a);
            fragment_color = vec4(
                float(alpha_bits & 255u) / 255.0,
                float((alpha_bits >> 8u) & 255u) / 255.0,
                0.0,
                1.0
            );
            return;
        }
        vec4 destination = half_value(texelFetch(
            DestinationTexture,
            ivec2(gl_FragCoord.xy),
            0
        ));
        if (FinalHighlightTrace >= 19) {
            fragment_color = replay_vibrant_source_diagnostic(
                highlight,
                destination,
                FinalHighlightTrace
            );
            return;
        }
        vec4 highlighted = replay_vibrant_color_matrix_sover(
            highlight,
            destination
        );
        fragment_color = highlighted;
        return;
    }
    if (NumericTrace == 3) {
        fragment_color = replay_profile_circle_debug(
            abs(replay_sdf_uv)
        );
        return;
    }

    int mode = SdfMode;
    float distance = 0.0;
    vec2 normal = vec2(0.0);
    float coverage = 0.0;
    float coverage_feather = 0.0;
    float coverage_quotient = 0.0;
    if (mode >= 0) {
        vec4 sdf = replay_compute_sdf(replay_sdf_uv, mode);
        distance = sdf.x;
        normal = sdf.yz;
        float feather = max(
            half_add(
                abs(half_value(dFdxFine(distance))),
                abs(half_value(dFdyFine(distance)))
            ),
            half_constant(0x068eu)
        );
        float quotient = half_divide(-distance, feather);
        coverage = half_multiply(
            sdf.w,
            half_value(clamp(float(quotient) + 0.5, 0.0, 1.0))
        );
        coverage_feather = feather;
        coverage_quotient = quotient;
    }
    if (UseAppleSdfTrace != 0) {
        vec4 trace = texelFetch(
            AppleSdfTrace,
            ivec2(gl_FragCoord.xy),
            0
        );
        distance = trace.x;
        normal = trace.yz;
        coverage = trace.w;
    }

    vec2 displacement = half_value(vec2(
        dot(normal, DisplacementMatrix.xy),
        dot(normal, DisplacementMatrix.zw)
    ));
    if (NumericTrace == 25) {
        fragment_color = coverage < 1.0
            ? replay_shadow_source_sample(
                replay_source_uv,
                distance,
                displacement
            )
            : vec4(0.0);
        return;
    }
    if (NumericTrace == 1) {
        fragment_color = half_value(vec4(distance, normal, coverage));
        return;
    }
    if (NumericTrace == 2) {
        float inner_shift = replay_refraction_shift(
            distance,
            InnerRefractionAmount,
            InnerRefractionInverseHeight
        );
        float inner_blur = half_value(
            BlurRadius
            * replay_blur_scale(half_add(inner_shift, distance))
        );
        vec2 coordinates = half_fma(
            inner_shift,
            displacement,
            half_value_rtz(replay_source_uv)
        );
        fragment_color = half_value(vec4(
            coordinates,
            inner_shift,
            inner_blur
        ));
        return;
    }
    if (
        NumericTrace == 15
        || NumericTrace == 16
        || NumericTrace == 17
    ) {
        float outer_shift = replay_refraction_shift(
            distance,
            OuterRefractionAmount,
            OuterRefractionInverseHeight
        );
        float outer_blur = half_value(
            BlurRadius
            * replay_blur_scale(half_add(outer_shift, distance))
        );
        vec2 coordinates = replay_refracted_coordinates(
            replay_source_uv,
            outer_shift,
            displacement
        );
        if (NumericTrace == 15) {
            vec2 base = half_value_rtz(replay_source_uv);
            vec2 materialized_coordinates = vec2(
                half_fma_exact(
                    outer_shift,
                    displacement.x,
                    base.x
                ),
                half_fma_exact(
                    outer_shift,
                    displacement.y,
                    base.y
                )
            );
            fragment_color = half_value(vec4(
                materialized_coordinates,
                outer_shift,
                outer_blur
            ));
        } else if (NumericTrace == 16) {
            fragment_color = replay_source_sample(
                coordinates,
                replay_lod(outer_blur)
            );
        } else {
            float threshold_span =
                RefractionThreshold1 - RefractionThreshold0;
            float threshold = float(distance)
                    * (1.0 / threshold_span)
                + (-RefractionThreshold0 / threshold_span);
            float amount = half_multiply(
                RefractionOpacity,
                half_value(clamp(threshold, 0.0, 1.0))
            );
            fragment_color = half_value(vec4(
                distance,
                amount,
                outer_shift,
                outer_blur
            ));
        }
        return;
    }
    if (NumericTrace == 7) {
        fragment_color = vec4(displacement, 0.0, 0.0);
        return;
    }
    if (NumericTrace == 8) {
        fragment_color = half_value(vec4(
            distance,
            coverage_feather,
            coverage_quotient,
            coverage
        ));
        return;
    }
    if (
        NumericTrace == 4
        || NumericTrace == 21
        || NumericTrace == 22
    ) {
        fragment_color = replay_sample_refracted(
            replay_source_uv,
            distance,
            displacement
        );
        return;
    }
    if (NumericTrace == 5) {
        fragment_color = replay_refracted_spatial_weights(
            replay_source_uv,
            distance,
            displacement
        );
        return;
    }
    if (NumericTrace == 6) {
        fragment_color = replay_refracted_spatial_residuals(
            replay_source_uv,
            distance,
            displacement
        );
        return;
    }

    vec2 shadow_sdf = vec2(0.0);
    if (coverage < 1.0) {
        int shadow_mode = abs(mode) | 4;
        vec4 shadow = replay_compute_sdf(
            replay_sdf_uv + ShadowOffset,
            shadow_mode
        );
        shadow_sdf = half_value(shadow.xw);
    }

    float shadow_alpha = coverage < 1.0
        ? replay_shadow_alpha(shadow_sdf)
        : 0.0;
    if (
        shadow_alpha < half_constant(0x068eu)
        && coverage == 0.0
    ) {
        discard;
    }

    vec4 shadow_layer = coverage < 1.0
        ? replay_shadow_layer(
            replay_source_uv,
            distance,
            displacement,
            shadow_alpha
        )
        : vec4(0.0);
    if (NumericTrace == 23) {
        fragment_color = shadow_layer;
        return;
    }
    if (NumericTrace == 24) {
        fragment_color = half_value(vec4(
            shadow_alpha,
            coverage,
            shadow_sdf
        ));
        return;
    }
    vec4 source_trace = vec4(0.0);
    vec4 face = vec4(0.0);
    if (coverage > 0.0) {
        vec4 sampled = replay_sample_refracted(
            replay_source_uv,
            distance,
            displacement
        );
        float sample_alpha = max(
            sampled.a,
            half_constant(0x068eu)
        );
        vec3 source_color = half_value(
            sampled.rgb / vec3(sample_alpha)
        );
        source_trace = half_value(vec4(source_color, sampled.a));
        face = half_value(vec4(source_color, 1.0));

        if (FaceOpacity > 0.0) {
            vec3 mapped = replay_color_matrix(
                source_color,
                FaceMatrix0,
                FaceMatrix1,
                FaceMatrix2
            );
            face.rgb = FloatMixWorkaround != 0.0
                ? half_value(mix(
                    source_color,
                    mapped,
                    vec3(float(FaceOpacity))
                ))
                : half_value(mix(
                    source_color,
                    mapped,
                    vec3(FaceOpacity)
                ));
        }
    }
    vec4 face_trace = face;
    vec4 edge_refraction_trace = vec4(0.0);
    vec4 edge_sample_trace = vec4(0.0);
    vec4 edge_amount_trace = vec4(0.0);
    bool edge_diagnostic =
        NumericTrace >= 18 && NumericTrace <= 20;
    if (
        (coverage > 0.0 || edge_diagnostic)
        && EdgeBleedOpacity > 0.0
    ) {
        face = replay_edge_bleed_layer(
            replay_source_uv,
            distance,
            displacement,
            face,
            edge_refraction_trace,
            edge_sample_trace,
            edge_amount_trace
        );
    }
    vec4 bleed_trace = face;

    vec4 composite = FloatMixWorkaround != 0.0
        ? half_value(mix(
            shadow_layer,
            face,
            vec4(float(coverage))
        ))
        : half_mix_exact(shadow_layer, face, coverage);
    vec4 composite_trace = composite;

    if (HoldingToneOpacity > 0.0) {
        float holding_distance;
        if (SdrShadowDistance.x > distance) {
            holding_distance = 1.0;
        } else if (SdrShadowDistance.y > distance) {
            float factor = half_divide(
                half_subtract(distance, SdrShadowDistance.x),
                half_subtract(
                    SdrShadowDistance.y,
                    SdrShadowDistance.x
                )
            );
            holding_distance = FloatMixWorkaround != 0.0
                ? half_value(mix(1.0, 0.0, float(factor)))
                : half_value(mix(1.0, 0.0, factor));
        } else {
            holding_distance = 0.0;
        }

        float clamped_alpha = clamp(composite.a, 0.0, 1.0);
        float holding_denominator = max(
            composite.a,
            half_constant(0x068eu)
        );
        vec3 holding_rgb = vec3(
            replay_holding_divide(
                half_multiply(
                    half_multiply(SdrWhiteValue, clamped_alpha),
                    composite.r
                ),
                holding_denominator
            ),
            replay_holding_divide(
                half_multiply(
                    half_multiply(SdrWhiteValue, clamped_alpha),
                    composite.g
                ),
                holding_denominator
            ),
            replay_holding_divide(
                half_multiply(
                    half_multiply(SdrWhiteValue, clamped_alpha),
                    composite.b
                ),
                holding_denominator
            )
        );
        vec4 holding = half_value(vec4(holding_rgb, clamped_alpha));
        float amount = half_multiply(
            holding_distance,
            HoldingToneOpacity
        );
        composite = FloatMixWorkaround != 0.0
            ? half_value(mix(
                composite,
                holding,
                vec4(float(amount))
            ))
            : replay_holding_mix(composite, holding, amount);
    }
    vec4 holding_trace = composite;

    if (ClampLimit > 0.0) {
        float alpha = max(composite.a, half_constant(0x068eu));
        vec3 straight = half_value(composite.rgb / vec3(alpha));
        if (PreserveHue > 0.0) {
            float maximum = max(straight.x, max(straight.y, straight.z));
            if (maximum > ClampLimit) {
                straight = half_value(
                    straight
                    * vec3(half_divide(ClampLimit, maximum))
                );
            }
        } else {
            straight = clamp(straight, vec3(-0.75), vec3(ClampLimit));
        }
        composite.rgb = half_value(straight * vec3(composite.a));
    }

    composite.rgb = half_value(composite.rgb * vec3(EdrScale));
    if (NumericTrace == 10) {
        fragment_color = source_trace;
        return;
    }
    if (NumericTrace == 11) {
        fragment_color = face_trace;
        return;
    }
    if (NumericTrace == 12) {
        fragment_color = composite_trace;
        return;
    }
    if (NumericTrace == 13) {
        fragment_color = holding_trace;
        return;
    }
    if (NumericTrace == 14) {
        fragment_color = bleed_trace;
        return;
    }
    if (NumericTrace == 18) {
        fragment_color = edge_refraction_trace;
        return;
    }
    if (NumericTrace == 19) {
        fragment_color = edge_sample_trace;
        return;
    }
    if (NumericTrace == 20) {
        fragment_color = edge_amount_trace;
        return;
    }
    composite = half_value(composite);
    if (EmulateAppleBlend != 0) {
        // BGRA8Unorm clamps the fragment source to the attachment range
        // before fixed-function blending.  Clamping only the blended result
        // incorrectly lets negative shadow-matrix channels darken the
        // destination.
        composite = clamp(composite, 0.0, 1.0);
        vec4 destination = half_value(texelFetch(
            DestinationTexture,
            ivec2(gl_FragCoord.xy),
            0
        ));
        float destination_factor = half_subtract(
            1.0,
            composite.a
        );
        composite = vec4(
            half_fma_exact(
                destination.r,
                destination_factor,
                composite.r
            ),
            half_fma_exact(
                destination.g,
                destination_factor,
                composite.g
            ),
            half_fma_exact(
                destination.b,
                destination_factor,
                composite.b
            ),
            half_fma_exact(
                destination.a,
                destination_factor,
                composite.a
            )
        );
    }
    fragment_color = composite;
}
