#version 450 core

layout(local_size_x = 8, local_size_y = 8, local_size_z = 1) in;

layout(binding = 0) uniform usampler2D SourceCodes;
layout(binding = 1, rgba8ui) writeonly uniform uimage2D DestinationCodes;

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

vec4 half_value(vec4 value)
{
    return vec4(
        half_value(value.x),
        half_value(value.y),
        half_value(value.z),
        half_value(value.w)
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
    return vec4(
        half_value(fma(left.x, right, addend.x)),
        half_value(fma(left.y, right, addend.y)),
        half_value(fma(left.z, right, addend.z)),
        half_value(fma(left.w, right, addend.w))
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

vec4 copy_base_prefilter(ivec2 output_coordinate, ivec2 offset)
{
    ivec2 coordinate = 2 * output_coordinate + offset;
    vec4 top_left = source_half(coordinate);
    vec4 top_right = source_half(coordinate + ivec2(1, 0));
    vec4 bottom_left = source_half(coordinate + ivec2(0, 1));
    vec4 bottom_right = source_half(coordinate + ivec2(1, 1));
    vec4 summed = half_add(top_right, top_left);
    summed = half_add(summed, bottom_left);
    summed = half_add(summed, bottom_right);
    return half_multiply(summed, half_constant(0x3400u));
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
    ivec2 output_coordinate = ivec2(gl_GlobalInvocationID.xy);
    ivec2 output_size = imageSize(DestinationCodes);
    if (any(greaterThanEqual(output_coordinate, output_size))) {
        return;
    }

    vec4 outer_0 = copy_base_prefilter(
        output_coordinate,
        ivec2(0, -4)
    );
    vec4 outer_1 = copy_base_prefilter(
        output_coordinate,
        ivec2(-4, 0)
    );
    vec4 outer_2 = copy_base_prefilter(
        output_coordinate,
        ivec2(0, 4)
    );
    vec4 outer_3 = copy_base_prefilter(
        output_coordinate,
        ivec2(4, 0)
    );
    vec4 outer = half_add(outer_1, outer_0);
    outer = half_add(outer, outer_3);
    outer = half_add(outer, outer_2);

    vec4 diagonal_0 = copy_base_prefilter(
        output_coordinate,
        ivec2(-2, 2)
    );
    vec4 diagonal_1 = copy_base_prefilter(
        output_coordinate,
        ivec2(-2, -2)
    );
    vec4 diagonal_2 = copy_base_prefilter(
        output_coordinate,
        ivec2(2, -2)
    );
    vec4 diagonal_3 = copy_base_prefilter(
        output_coordinate,
        ivec2(2, 2)
    );
    vec4 diagonal = half_add(diagonal_2, diagonal_1);
    diagonal = half_add(diagonal, diagonal_0);
    diagonal = half_add(diagonal, diagonal_3);

    vec4 inner_0 = copy_base_prefilter(
        output_coordinate,
        ivec2(0, -2)
    );
    vec4 inner_1 = copy_base_prefilter(
        output_coordinate,
        ivec2(-2, 0)
    );
    vec4 inner_2 = copy_base_prefilter(
        output_coordinate,
        ivec2(0, 2)
    );
    vec4 inner_3 = copy_base_prefilter(
        output_coordinate,
        ivec2(2, 0)
    );
    vec4 inner = half_add(inner_1, inner_0);
    inner = half_add(inner, inner_3);
    inner = half_add(inner, inner_2);

    vec4 center = copy_base_prefilter(
        output_coordinate,
        ivec2(0)
    );
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
