#version 450 core

layout(local_size_x = 16, local_size_y = 16, local_size_z = 1) in;

layout(binding = 0) uniform usampler2D SourceCodes;
layout(binding = 1, rgba8ui) writeonly uniform uimage2D DestinationCodes;

shared vec4 PrefilterCache[400];

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

uint half_bits_rne_positive(float value, uint bits)
{
    if (value > 0.0 && bits < 0x7bffu) {
        float lower = unpackHalf2x16(bits).x;
        float upper = unpackHalf2x16(bits + 1u).x;
        float midpoint = (lower + upper) * 0.5;
        if (
            value > midpoint
            || (value == midpoint && (bits & 1u) != 0u)
        ) {
            bits += 1u;
        }
    }
    return bits;
}

vec2 half_value(vec2 value)
{
    uint packed_bits = packHalf2x16(value);
    uint low = half_bits_rne_positive(
        value.x,
        packed_bits & 0xffffu
    );
    uint high = half_bits_rne_positive(
        value.y,
        packed_bits >> 16u
    );
    return unpackHalf2x16(low | (high << 16u));
}

vec4 half_value(vec4 value)
{
    return vec4(
        half_value(value.xy),
        half_value(value.zw)
    );
}

float half_constant(uint bits)
{
    return unpackHalf2x16(bits).x;
}

vec4 half_add(vec4 left, vec4 right)
{
    return half_value(left + right);
}

vec4 half_multiply(vec4 left, float right)
{
    return half_value(left * right);
}

vec4 half_fma(vec4 left, float right, vec4 addend)
{
    return half_value(
        vec4(
            fma(left.x, right, addend.x),
            fma(left.y, right, addend.y),
            fma(left.z, right, addend.z),
            fma(left.w, right, addend.w)
        )
    );
}

vec4 source_half(ivec2 coordinate)
{
    ivec2 size = textureSize(SourceCodes, 0);
    coordinate = clamp(coordinate, ivec2(0), size - ivec2(1));
    return half_value(
        vec4(texelFetch(SourceCodes, coordinate, 0)) / 255.0
    );
}

vec4 copy_base_prefilter(ivec2 prefilter_coordinate)
{
    ivec2 coordinate = 2 * prefilter_coordinate;
    vec4 top_left = source_half(coordinate);
    vec4 top_right = source_half(coordinate + ivec2(1, 0));
    vec4 bottom_left = source_half(coordinate + ivec2(0, 1));
    vec4 bottom_right = source_half(coordinate + ivec2(1, 1));
    vec4 summed = half_add(top_right, top_left);
    summed = half_add(summed, bottom_left);
    summed = half_add(summed, bottom_right);
    return half_multiply(summed, half_constant(0x3400u));
}

vec4 cached_prefilter(ivec2 offset)
{
    ivec2 coordinate = (
        ivec2(gl_LocalInvocationID.xy)
        + ivec2(2)
        + offset
    );
    return PrefilterCache[coordinate.y * 20 + coordinate.x];
}

uint unorm8_code(float value)
{
    float scaled = clamp(value, 0.0, 1.0) * 255.0;
    uint lower = uint(floor(scaled));
    float remainder = scaled - float(lower);
    if (
        remainder > 0.5
        || (remainder == 0.5 && (lower & 1u) != 0u)
    ) {
        lower += 1u;
    }
    return min(lower, 255u);
}

void main()
{
    ivec2 local_output = ivec2(gl_LocalInvocationID.xy);
    ivec2 output_origin = ivec2(gl_WorkGroupID.xy) * 16;
    uint local_linear = (
        gl_LocalInvocationID.y * 16u
        + gl_LocalInvocationID.x
    );
    for (
        uint cache_index = local_linear;
        cache_index < 400u;
        cache_index += 256u
    ) {
        ivec2 cache_coordinate = ivec2(
            int(cache_index % 20u),
            int(cache_index / 20u)
        );
        ivec2 prefilter_coordinate = (
            output_origin + cache_coordinate - ivec2(2)
        );
        PrefilterCache[cache_index] =
            copy_base_prefilter(prefilter_coordinate);
    }
    barrier();

    ivec2 output_coordinate = output_origin + local_output;
    ivec2 output_size = imageSize(DestinationCodes);
    if (any(greaterThanEqual(output_coordinate, output_size))) {
        return;
    }

    vec4 outer_0 = cached_prefilter(ivec2(0, -2));
    vec4 outer_1 = cached_prefilter(ivec2(-2, 0));
    vec4 outer_2 = cached_prefilter(ivec2(0, 2));
    vec4 outer_3 = cached_prefilter(ivec2(2, 0));
    vec4 outer = half_add(outer_1, outer_0);
    outer = half_add(outer, outer_3);
    outer = half_add(outer, outer_2);

    vec4 diagonal_0 = cached_prefilter(ivec2(-1, 1));
    vec4 diagonal_1 = cached_prefilter(ivec2(-1, -1));
    vec4 diagonal_2 = cached_prefilter(ivec2(1, -1));
    vec4 diagonal_3 = cached_prefilter(ivec2(1, 1));
    vec4 diagonal = half_add(diagonal_2, diagonal_1);
    diagonal = half_add(diagonal, diagonal_0);
    diagonal = half_add(diagonal, diagonal_3);

    vec4 inner_0 = cached_prefilter(ivec2(0, -1));
    vec4 inner_1 = cached_prefilter(ivec2(-1, 0));
    vec4 inner_2 = cached_prefilter(ivec2(0, 1));
    vec4 inner_3 = cached_prefilter(ivec2(1, 0));
    vec4 inner = half_add(inner_1, inner_0);
    inner = half_add(inner, inner_3);
    inner = half_add(inner, inner_2);

    vec4 center = cached_prefilter(ivec2(0));
    vec4 result = half_multiply(
        center,
        half_constant(0x2ec0u)
    );
    result = half_fma(
        diagonal,
        half_constant(0x2cefu),
        result
    );
    result = half_fma(
        inner,
        half_constant(0x2dc6u),
        result
    );
    result = half_fma(
        outer,
        half_constant(0x2b36u),
        result
    );

    float denorm_limit = half_constant(0x068eu);
    if (abs(result.r) < denorm_limit) {
        result.r = 0.0;
    }
    if (abs(result.g) < denorm_limit) {
        result.g = 0.0;
    }
    if (abs(result.b) < denorm_limit) {
        result.b = 0.0;
    }

    imageStore(
        DestinationCodes,
        output_coordinate,
        uvec4(
            unorm8_code(result.r),
            unorm8_code(result.g),
            unorm8_code(result.b),
            unorm8_code(result.a)
        )
    );
}
