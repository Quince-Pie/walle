#version 450 core

layout(local_size_x = 8, local_size_y = 8) in;

layout(binding = 0) uniform sampler2D InputTexture;
layout(binding = 1) uniform sampler2D PyramidTexture;
layout(rgba8, binding = 0) writeonly uniform image2D OutputImage;

layout(std430, binding = 0) readonly buffer ProducerMetadata {
    int ProducerMeta[];
};

layout(std430, binding = 1) readonly buffer ProducerAxes {
    uint ProducerAxisBits[];
};

uniform int Mode;
uniform int ProducerKind;
uniform int ProducerQuadCount;
uniform int PyramidLevel;
uniform ivec2 SourceExtent;
uniform ivec2 ActiveExtent;
uniform ivec2 CopyOffset;
uniform ivec4 ProducerScissor;
uniform vec2 DownsampleOffset;
uniform uint ArithmeticBarrier;

const ivec2 Taps[13] = ivec2[](
    ivec2(0, -4),
    ivec2(-4, 0),
    ivec2(0, 4),
    ivec2(4, 0),
    ivec2(-2, 2),
    ivec2(-2, -2),
    ivec2(2, -2),
    ivec2(2, 2),
    ivec2(0, -2),
    ivec2(-2, 0),
    ivec2(0, 2),
    ivec2(2, 0),
    ivec2(0, 0)
);

const ivec4 GroupOrder[3] = ivec4[](
    ivec4(1, 0, 3, 2),
    ivec4(6, 5, 4, 7),
    ivec4(9, 8, 11, 10)
);

uint round_shift_right_even(uint value, uint shift)
{
    if (shift == 0u)
        return value;
    uint truncated = value >> shift;
    uint mask = (1u << shift) - 1u;
    uint remainder = value & mask;
    uint midpoint = 1u << (shift - 1u);
    if (remainder > midpoint || (remainder == midpoint && (truncated & 1u) != 0u))
        truncated += 1u;
    return truncated;
}

uint float_to_half_bits(float value)
{
    uint bits = floatBitsToUint(value);
    uint sign = (bits >> 16u) & 0x8000u;
    uint exponent = (bits >> 23u) & 0xffu;
    uint mantissa = bits & 0x7fffffu;
    if (exponent == 0xffu)
        return sign | (mantissa == 0u ? 0x7c00u : 0x7e00u);
    if (exponent == 0u)
        return sign;
    int half_exponent = int(exponent) - 112;
    if (half_exponent >= 31)
        return sign | 0x7c00u;
    if (half_exponent <= 0) {
        int unbiased = int(exponent) - 127;
        if (unbiased < -25)
            return sign;
        uint significand = mantissa | 0x800000u;
        uint shift = uint(-unbiased - 1);
        return sign | min(round_shift_right_even(significand, shift), 0x0400u);
    }
    uint rounded_mantissa = round_shift_right_even(mantissa, 13u);
    if (rounded_mantissa == 0x0400u) {
        rounded_mantissa = 0u;
        half_exponent += 1;
        if (half_exponent >= 31)
            return sign | 0x7c00u;
    }
    return sign | (uint(half_exponent) << 10u) | rounded_mantissa;
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

uint next_half_up_bits(uint bits)
{
    if ((bits & 0x7fffu) == 0u)
        return 0x0001u;
    if ((bits & 0x8000u) != 0u)
        return bits == 0xfc00u ? 0xfbffu : bits - 1u;
    return bits == 0x7c00u ? bits : bits + 1u;
}

uint next_half_down_bits(uint bits)
{
    if ((bits & 0x7fffu) == 0u)
        return 0x8001u;
    if ((bits & 0x8000u) != 0u)
        return bits == 0xfc00u ? bits : bits + 1u;
    return bits == 0x7c00u ? 0x7bffu : bits - 1u;
}

float float_barrier(float value)
{
    return uintBitsToFloat(floatBitsToUint(value) ^ ArithmeticBarrier);
}

float half_fma_exact(float left, float right, float addend)
{
    float product = float_barrier(left * right);
    float sum = float_barrier(product + addend);
    float virtual_addend = float_barrier(sum - product);
    float product_error = float_barrier(product - float_barrier(sum - virtual_addend));
    float addend_error = float_barrier(addend - virtual_addend);
    float error = float_barrier(product_error + addend_error);
    uint bits = float_to_half_bits(sum);
    float rounded = unpackHalf2x16(bits).x;
    if (error == 0.0 || isnan(sum) || isinf(sum))
        return rounded;
    bool sum_is_higher = sum > rounded;
    uint adjacent_bits = sum_is_higher ? next_half_up_bits(bits) : next_half_down_bits(bits);
    float adjacent = unpackHalf2x16(adjacent_bits).x;
    float midpoint = float_barrier((rounded + adjacent) * 0.5);
    if (sum == midpoint
        && ((sum_is_higher && error > 0.0) || (!sum_is_higher && error < 0.0)))
        bits = adjacent_bits;
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
        half_fma_exact(left.x, right, addend.x),
        half_fma_exact(left.y, right, addend.y),
        half_fma_exact(left.z, right, addend.z),
        half_fma_exact(left.w, right, addend.w)
    );
}

uvec4 texture_codes(sampler2D texture_value, ivec2 coordinate, ivec2 extent, int level)
{
    ivec2 bounded = clamp(coordinate, ivec2(0), extent - ivec2(1));
    return uvec4(floor(texelFetch(texture_value, bounded, level) * 255.0 + 0.5));
}

vec4 codes_to_half(uvec4 codes)
{
    return half_value(vec4(codes) / 255.0);
}

uvec4 half_to_codes(vec4 value)
{
    return uvec4(clamp(roundEven(value * 255.0), 0.0, 255.0));
}

vec4 linear_sample(vec2 coordinate)
{
    vec2 scaled = vec2(
        float_barrier(coordinate.x * float(SourceExtent.x)),
        float_barrier(coordinate.y * float(SourceExtent.y))
    );
    vec2 position = scaled - vec2(0.5);
    ivec2 origin = ivec2(floor(position));
    vec2 fraction = fract(position);
    uvec2 weight = uvec2(floor(fraction * 256.0 + 0.5));
    uvec2 inverse = uvec2(256u) - weight;
    uint weight_00 = inverse.x * inverse.y;
    uint weight_10 = weight.x * inverse.y;
    uint weight_01 = inverse.x * weight.y;
    uint weight_11 = weight.x * weight.y;
    uvec4 combined =
        texture_codes(InputTexture, origin, SourceExtent, 0) * weight_00
        + texture_codes(InputTexture, origin + ivec2(1, 0), SourceExtent, 0) * weight_10
        + texture_codes(InputTexture, origin + ivec2(0, 1), SourceExtent, 0) * weight_01
        + texture_codes(InputTexture, origin + ivec2(1, 1), SourceExtent, 0) * weight_11;
    uvec4 fixed_sixteenths = (combined + uvec4(2048u)) / 4096u;
    return half_value(vec4(fixed_sixteenths) / 4080.0);
}

bool producer_coordinate(ivec2 producer, out vec2 coordinate)
{
    if (any(lessThan(producer, ProducerScissor.xy))
        || any(greaterThanEqual(producer, ProducerScissor.xy + ProducerScissor.zw)))
        return false;
    for (int quad = ProducerQuadCount - 1; quad >= 0; --quad) {
        int base = quad * 12;
        ivec4 bounds = ivec4(
            ProducerMeta[base + 4],
            ProducerMeta[base + 5],
            ProducerMeta[base + 6],
            ProducerMeta[base + 7]
        );
        if (any(lessThan(producer, bounds.xy)) || any(greaterThanEqual(producer, bounds.zw)))
            continue;
        ivec2 origin_fixed = ivec2(ProducerMeta[base], ProducerMeta[base + 1]);
        ivec2 extent_fixed = ivec2(ProducerMeta[base + 2], ProducerMeta[base + 3]);
        int axis_start = ProducerMeta[base + 8];
        int axis_count = ProducerMeta[base + 9];
        int axis_offset = ProducerMeta[base + 10];
        double relative_x = double(producer.x * 256 + 128 - origin_fixed.x);
        double relative_y = double(producer.y * 256 + 128 - origin_fixed.y);
        bool ascending = ProducerMeta[base + 11] != 0;
        int primitive;
        if (ascending) {
            primitive = relative_y * double(extent_fixed.x)
                        > relative_x * double(extent_fixed.y) ? 1 : 0;
        } else {
            double diagonal = relative_x * double(extent_fixed.y)
                              + relative_y * double(extent_fixed.x);
            double area = double(extent_fixed.x) * double(extent_fixed.y);
            primitive = diagonal < area ? 1 : 0;
        }
        int x_index = axis_offset
                      + (primitive * axis_count + producer.x - axis_start) * 4;
        int y_index = axis_offset
                      + (primitive * axis_count + producer.y - axis_start) * 4;
        coordinate = vec2(
            uintBitsToFloat(ProducerAxisBits[x_index]),
            uintBitsToFloat(ProducerAxisBits[y_index + 1])
        );
        return true;
    }
    return false;
}

vec4 build_base(ivec2 output_coordinate)
{
    ivec2 producer = clamp(
        output_coordinate + CopyOffset,
        ivec2(0),
        ActiveExtent - ivec2(1)
    );
    vec2 coordinate;
    if (!producer_coordinate(producer, coordinate))
        return vec4(0.0);
    if (ProducerKind == 0)
        return linear_sample(coordinate);
    const ivec2 signs[4] = ivec2[](
        ivec2(-1, 1),
        ivec2(1, 1),
        ivec2(-1, -1),
        ivec2(1, -1)
    );
    vec4 result = vec4(0.0);
    for (int tap = 0; tap < 4; ++tap) {
        vec2 shifted = coordinate + vec2(signs[tap]) * DownsampleOffset;
        result = half_fma(linear_sample(shifted), unpackHalf2x16(0x3400u).x, result);
    }
    return result;
}

vec4 sample_2x2(ivec2 output_coordinate, ivec2 offset, bool copy_base)
{
    ivec2 source_extent = textureSize(PyramidTexture, PyramidLevel);
    ivec2 base = 2 * output_coordinate + offset;
    uvec4 top_left = texture_codes(PyramidTexture, base, source_extent, PyramidLevel);
    uvec4 top_right = texture_codes(
        PyramidTexture, base + ivec2(1, 0), source_extent, PyramidLevel);
    uvec4 bottom_left = texture_codes(
        PyramidTexture, base + ivec2(0, 1), source_extent, PyramidLevel);
    uvec4 bottom_right = texture_codes(
        PyramidTexture, base + ivec2(1, 1), source_extent, PyramidLevel);
    if (!copy_base) {
        uvec4 sum = top_left + top_right + bottom_left + bottom_right;
        return half_value(vec4(sum) / 1020.0);
    }
    vec4 result = half_add(codes_to_half(top_right), codes_to_half(top_left));
    result = half_add(result, codes_to_half(bottom_left));
    result = half_add(result, codes_to_half(bottom_right));
    return half_multiply(result, unpackHalf2x16(0x3400u).x);
}

vec4 ordered_group_sum(vec4 samples[13], int group)
{
    ivec4 order = GroupOrder[group];
    vec4 result = samples[order.x];
    result = half_add(result, samples[order.y]);
    result = half_add(result, samples[order.z]);
    return half_add(result, samples[order.w]);
}

vec4 build_mip(ivec2 output_coordinate, bool copy_base)
{
    vec4 samples[13];
    for (int tap = 0; tap < 13; ++tap)
        samples[tap] = sample_2x2(output_coordinate, Taps[tap], copy_base);
    vec4 group_0 = ordered_group_sum(samples, 0);
    vec4 group_1 = ordered_group_sum(samples, 1);
    vec4 group_2 = ordered_group_sum(samples, 2);
    vec4 result = half_multiply(samples[12], unpackHalf2x16(0x2ec0u).x);
    result = half_fma(group_1, unpackHalf2x16(0x2cefu).x, result);
    result = half_fma(group_2, unpackHalf2x16(0x2dc6u).x, result);
    result = half_fma(group_0, unpackHalf2x16(0x2b36u).x, result);
    if (copy_base) {
        for (int channel = 0; channel < 3; ++channel) {
            if ((float_to_half_bits(result[channel]) & 0x7fffu) < 0x068eu)
                result[channel] = 0.0;
        }
    }
    return result;
}

void main()
{
    ivec2 output_coordinate = ivec2(gl_GlobalInvocationID.xy);
    ivec2 output_extent = imageSize(OutputImage);
    if (any(greaterThanEqual(output_coordinate, output_extent)))
        return;
    vec4 value = Mode == 0
        ? build_base(output_coordinate)
        : build_mip(output_coordinate, Mode == 1);
    uvec4 codes = half_to_codes(value);
    imageStore(OutputImage, output_coordinate, vec4(codes) / 255.0);
}
