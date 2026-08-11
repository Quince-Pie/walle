#version 320 es
precision highp float;
precision highp int;

in vec2 v_SDF;
layout(location = 0) out float RevealCoverage;

uniform vec2 RevealResolution;
uniform highp usampler2D AxisTable;
uniform highp usampler2D AppleFastSqrtTable;
uniform int PrimitiveSlots[18];
uniform int PrimitiveRows[18];

layout(std140, binding = 0) uniform RevealOwnerBlock {
    ivec4 OwnerCounts;
    ivec4 OwnerBounds[94];
    ivec4 OwnerOriginExtent[94];
    ivec4 OwnerControl[94];
};

uint roundShiftRightToEven(uint value, uint shift) {
    if (shift == 0u) {
        return value;
    }

    uint truncated = value >> shift;
    uint remainder = value & ((1u << shift) - 1u);
    uint halfway = 1u << (shift - 1u);
    if (remainder > halfway || (remainder == halfway && (truncated & 1u) != 0u)) {
        ++truncated;
    }
    return truncated;
}

uint float32ToFloat16RNEBits(float value) {
    uint bits = floatBitsToUint(value);
    uint sign = (bits >> 16u) & 0x8000u;
    uint exponent = (bits >> 23u) & 0xffu;
    uint significand = bits & 0x7fffffu;

    if (exponent == 0xffu) {
        return sign | (significand == 0u ? 0x7c00u : 0x7e00u);
    }
    if (exponent == 0u) {
        return sign;
    }

    int unbiased_exponent = int(exponent) - 127;
    int half_exponent = unbiased_exponent + 15;
    if (half_exponent >= 31) {
        return sign | 0x7c00u;
    }
    if (half_exponent <= 0) {
        if (unbiased_exponent < -25) {
            return sign;
        }
        uint rounded = roundShiftRightToEven(
            significand | 0x800000u,
            uint(-unbiased_exponent - 1));
        return sign | min(rounded, 0x400u);
    }

    uint rounded = roundShiftRightToEven(significand, 13u);
    if (rounded == 0x400u) {
        rounded = 0u;
        ++half_exponent;
        if (half_exponent >= 31) {
            return sign | 0x7c00u;
        }
    }
    return sign | (uint(half_exponent) << 10u) | rounded;
}

void finiteFloatComponents(uint bits, out uint significand, out int exponent) {
    uint encoded_exponent = (bits >> 23u) & 0xffu;
    if (encoded_exponent == 0u) {
        significand = bits & 0x7fffffu;
        exponent = -149;
    } else {
        significand = (bits & 0x7fffffu) | 0x800000u;
        exponent = int(encoded_exponent) - 150;
    }
}

void midpointComponents(
    uint left_bits,
    uint right_bits,
    out uint significand,
    out int exponent
) {
    uint left_significand;
    uint right_significand;
    int left_exponent;
    int right_exponent;
    finiteFloatComponents(left_bits, left_significand, left_exponent);
    finiteFloatComponents(right_bits, right_significand, right_exponent);
    exponent = min(left_exponent, right_exponent);
    significand = (left_significand << uint(left_exponent - exponent))
        + (right_significand << uint(right_exponent - exponent));
    --exponent;
}

uvec3 shiftTo96(uint value, int shift) {
    if (shift < 0 || shift >= 96) {
        return uvec3(0u);
    }
    if (shift == 0) {
        return uvec3(value, 0u, 0u);
    }
    if (shift < 32) {
        return uvec3(value << uint(shift), value >> uint(32 - shift), 0u);
    }
    if (shift == 32) {
        return uvec3(0u, value, 0u);
    }
    if (shift < 64) {
        return uvec3(0u, value << uint(shift - 32), value >> uint(64 - shift));
    }
    return uvec3(0u, 0u, value << uint(shift - 64));
}

int compareSquareToValue(uint value_bits, uint midpoint_significand, int midpoint_exponent) {
    uint value_significand;
    int value_exponent;
    finiteFloatComponents(value_bits, value_significand, value_exponent);

    uint square_high;
    uint square_low;
    umulExtended(
        midpoint_significand,
        midpoint_significand,
        square_high,
        square_low);
    int shift = value_exponent - 2 * midpoint_exponent;
    if (shift < 0) {
        return 1;
    }
    if (shift >= 96) {
        return -1;
    }

    uvec3 square = uvec3(square_low, square_high, 0u);
    uvec3 value = shiftTo96(value_significand, shift);
    if (square.z != value.z) {
        return square.z < value.z ? -1 : 1;
    }
    if (square.y != value.y) {
        return square.y < value.y ? -1 : 1;
    }
    if (square.x != value.x) {
        return square.x < value.x ? -1 : 1;
    }
    return 0;
}

float ieeeSqrt(float value) {
    float result = sqrt(value);
    if (!(value > 0.0) || isinf(value)) {
        return result;
    }

    uint value_bits = floatBitsToUint(value);
    uint result_bits = floatBitsToUint(result);
    for (int iteration = 0; iteration < 4; ++iteration) {
        uint midpoint_significand;
        int midpoint_exponent;
        midpointComponents(
            result_bits - 1u,
            result_bits,
            midpoint_significand,
            midpoint_exponent);
        if (compareSquareToValue(
                value_bits,
                midpoint_significand,
                midpoint_exponent) > 0) {
            --result_bits;
            continue;
        }

        midpointComponents(
            result_bits,
            result_bits + 1u,
            midpoint_significand,
            midpoint_exponent);
        if (compareSquareToValue(
                value_bits,
                midpoint_significand,
                midpoint_exponent) < 0) {
            ++result_bits;
            continue;
        }
        break;
    }
    return uintBitsToFloat(result_bits);
}

float appleLength(vec2 value) {
    float square = fma(value.y, value.y, value.x * value.x);
    float root = ieeeSqrt(square);
    uint square_bits = floatBitsToUint(square);
    uint mantissa = square_bits & 0x7fffffu;
    uint packed_mantissa = mantissa >> 1u;
    uint packed_code = texelFetch(
        AppleFastSqrtTable,
        ivec2(int(packed_mantissa & 4095u), int(packed_mantissa >> 12u)),
        0).r;
    uint code = (packed_code >> ((mantissa & 1u) * 4u)) & 15u;
    uint correction = ((square_bits >> 23u) & 1u) == 0u
        ? code & 3u
        : (code >> 2u) & 3u;
    return uintBitsToFloat(uint(int(floatBitsToUint(root)) + int(correction) - 1));
}

uvec2 multiplySigned64(int left, int right) {
    int high;
    int low;
    imulExtended(left, right, high, low);
    return uvec2(uint(low), uint(high));
}

uvec2 addSigned64(uvec2 left, uvec2 right) {
    uint low = left.x + right.x;
    return uvec2(low, left.y + right.y + uint(low < left.x));
}

bool signedLess64(uvec2 left, uvec2 right) {
    int left_high = int(left.y);
    int right_high = int(right.y);
    return left_high != right_high ? left_high < right_high : left.x < right.x;
}

int ownerPrimitive(int slot, ivec2 coordinate) {
    ivec4 transform = OwnerOriginExtent[slot];
    ivec2 relative = coordinate * 256 + ivec2(128) - transform.xy;
    uvec2 x = multiplySigned64(relative.x, transform.w);
    uvec2 y = multiplySigned64(relative.y, transform.z);
    if (OwnerControl[slot].y != 0) {
        return signedLess64(x, y) ? 1 : 0;
    }
    return signedLess64(
        addSigned64(x, y),
        multiplySigned64(transform.z, transform.w)) ? 1 : 0;
}

int ownerPrimitiveAt(int slot, ivec2 coordinate) {
    ivec4 bounds = OwnerBounds[slot];
    if (coordinate.x < bounds.x || coordinate.y < bounds.y
        || coordinate.x >= bounds.z || coordinate.y >= bounds.w) {
        return -1;
    }
    int primitive = ownerPrimitive(slot, coordinate);
    return (OwnerControl[slot].z & (1 << primitive)) != 0 ? primitive : -1;
}

int ownerCode(ivec2 coordinate, int slot_count) {
    int code = 0;
    for (int slot = 0; slot < 94; ++slot) {
        if (slot >= slot_count) {
            break;
        }
        int primitive = ownerPrimitiveAt(slot, coordinate);
        if (primitive >= 0) {
            code = slot * 2 + primitive + 1;
        }
    }
    return code;
}

vec2 ownerCoordinates(ivec2 coordinate, int slot, int primitive) {
    int start = OwnerControl[slot].x;
    uvec4 x_axis = texelFetch(
        AxisTable,
        ivec2(coordinate.x - start, slot * 2 + primitive),
        0);
    uvec4 y_axis = texelFetch(
        AxisTable,
        ivec2(coordinate.y - start, slot * 2 + primitive),
        0);
    return vec2(uintBitsToFloat(x_axis.x), uintBitsToFloat(y_axis.y));
}

void decodeOwnerCode(int code, out int slot, out int primitive) {
    --code;
    slot = code / 2;
    primitive = code & 1;
}

vec2 partnerCoordinates(
    ivec2 coordinate,
    int center_slot,
    int center_primitive,
    int fallback_slot,
    int fallback_primitive
) {
    int slot = fallback_slot;
    int primitive = fallback_primitive;
    if (OwnerControl[center_slot].w == 1) {
        slot = center_slot;
        primitive = ownerPrimitiveAt(slot, coordinate);
        if (primitive < 0) {
            primitive = center_primitive;
        }
    } else {
        int code = ownerCode(coordinate, OwnerCounts.y);
        if (code > 0) {
            decodeOwnerCode(code, slot, primitive);
        }
    }
    return ownerCoordinates(coordinate, slot, primitive);
}

void main() {
    int fallback_slot = PrimitiveSlots[gl_PrimitiveID];
    int fallback_primitive = PrimitiveRows[gl_PrimitiveID];
    ivec2 coordinate = ivec2(
        int(gl_FragCoord.x),
        int(RevealResolution.y) - 1 - int(gl_FragCoord.y));
    int center_slot = fallback_slot;
    int center_primitive = fallback_primitive;
    int center_code = ownerCode(coordinate, OwnerCounts.x);
    if (center_code > 0) {
        decodeOwnerCode(center_code, center_slot, center_primitive);
    }
    float distance_value = appleLength(ownerCoordinates(
        coordinate, center_slot, center_primitive));
    float distance_x = appleLength(partnerCoordinates(
        ivec2(coordinate.x ^ 1, coordinate.y),
        center_slot,
        center_primitive,
        fallback_slot,
        fallback_primitive));
    float distance_y = appleLength(partnerCoordinates(
        ivec2(coordinate.x, coordinate.y ^ 1),
        center_slot,
        center_primitive,
        fallback_slot,
        fallback_primitive));
    if (v_SDF.x > 1.0e30) {
        distance_value = v_SDF.x;
    }
    float feather = max(
        abs(distance_x - distance_value) + abs(distance_y - distance_value),
        1.0e-4);
    float alpha = clamp((1.0 - distance_value) / feather + 0.5, 0.0, 1.0);
    float half_alpha = alpha == 0.0 || alpha == 1.0
        ? alpha
        : unpackHalf2x16(float32ToFloat16RNEBits(alpha)).x;
    RevealCoverage = roundEven(half_alpha * 255.0) / 255.0;
}
