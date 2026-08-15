#language slang 2026

// Recovered reveal coverage raster, compiled offline to Vulkan SPIR-V.

static const uint kMaxOwners = 94;
static const uint kOwnerBoundsOffset = 1;
static const uint kOwnerOriginExtentOffset = 1 + kMaxOwners;
static const uint kOwnerControlOffset = 1 + 2 * kMaxOwners;

struct MaskPush
{
    float2 resolution;
    float compactFamily;
    float _padding;
    uint ownerCount;
    uint baseOwnerCount;
    uint packedWidth;
    uint primitiveCount;
};

struct MaskVertexInput
{
    [[vk::location(0)]] float4 position;
    [[vk::location(1)]] float2 firstCoordinates;
    [[vk::location(2)]] float2 secondCoordinates;
};

struct MaskVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 sdf;
};

[[vk::binding(0, 0)]] StructuredBuffer<uint2> axisTable;
[[vk::binding(1, 0)]] ByteAddressBuffer appleFastSqrtTable;
[[vk::binding(2, 0)]] StructuredBuffer<int4> ownerData;
[[vk::binding(3, 0)]] StructuredBuffer<int2> primitiveMappings;

uint roundShiftRightToEven(uint value, uint shift)
{
    if (shift == 0)
        return value;

    uint truncated = value >> shift;
    uint remainder = value & ((1u << shift) - 1u);
    uint halfway = 1u << (shift - 1u);
    if (remainder > halfway || (remainder == halfway && (truncated & 1u) != 0u))
        ++truncated;
    return truncated;
}

uint float32ToFloat16RNEBits(float value)
{
    uint bits = asuint(value);
    uint sign = (bits >> 16u) & 0x8000u;
    uint exponent = (bits >> 23u) & 0xffu;
    uint significand = bits & 0x7fffffu;

    if (exponent == 0xffu)
        return sign | (significand == 0u ? 0x7c00u : 0x7e00u);
    if (exponent == 0u)
        return sign;

    int unbiasedExponent = int(exponent) - 127;
    int halfExponent = unbiasedExponent + 15;
    if (halfExponent >= 31)
        return sign | 0x7c00u;
    if (halfExponent <= 0)
    {
        if (unbiasedExponent < -25)
            return sign;
        uint rounded = roundShiftRightToEven(
            significand | 0x800000u,
            uint(-unbiasedExponent - 1));
        return sign | min(rounded, 0x400u);
    }

    uint rounded = roundShiftRightToEven(significand, 13u);
    if (rounded == 0x400u)
    {
        rounded = 0u;
        ++halfExponent;
        if (halfExponent >= 31)
            return sign | 0x7c00u;
    }
    return sign | (uint(halfExponent) << 10u) | rounded;
}

float float16BitsToFloat32(uint bits)
{
    uint sign = (bits & 0x8000u) << 16u;
    uint exponent = (bits >> 10u) & 0x1fu;
    uint significand = bits & 0x3ffu;
    if (exponent == 0u)
    {
        if (significand == 0u)
            return asfloat(sign);
        int unbiasedExponent = -14;
        while ((significand & 0x400u) == 0u)
        {
            significand <<= 1u;
            --unbiasedExponent;
        }
        significand &= 0x3ffu;
        return asfloat(sign | (uint(unbiasedExponent + 127) << 23u) | (significand << 13u));
    }
    if (exponent == 0x1fu)
        return asfloat(sign | 0x7f800000u | (significand << 13u));
    return asfloat(sign | ((exponent + 112u) << 23u) | (significand << 13u));
}

void finiteFloatComponents(uint bits, out uint significand, out int exponent)
{
    uint encodedExponent = (bits >> 23u) & 0xffu;
    if (encodedExponent == 0u)
    {
        significand = bits & 0x7fffffu;
        exponent = -149;
    }
    else
    {
        significand = (bits & 0x7fffffu) | 0x800000u;
        exponent = int(encodedExponent) - 150;
    }
}

void midpointComponents(
    uint leftBits,
    uint rightBits,
    out uint significand,
    out int exponent)
{
    uint leftSignificand;
    uint rightSignificand;
    int leftExponent;
    int rightExponent;
    finiteFloatComponents(leftBits, leftSignificand, leftExponent);
    finiteFloatComponents(rightBits, rightSignificand, rightExponent);
    exponent = min(leftExponent, rightExponent);
    significand = (leftSignificand << uint(leftExponent - exponent))
        + (rightSignificand << uint(rightExponent - exponent));
    --exponent;
}

uint3 shiftTo96(uint value, int shift)
{
    if (shift < 0 || shift >= 96)
        return uint3(0u);
    if (shift == 0)
        return uint3(value, 0u, 0u);
    if (shift < 32)
        return uint3(value << uint(shift), value >> uint(32 - shift), 0u);
    if (shift == 32)
        return uint3(0u, value, 0u);
    if (shift < 64)
        return uint3(0u, value << uint(shift - 32), value >> uint(64 - shift));
    return uint3(0u, 0u, value << uint(shift - 64));
}

int compareSquareToValue(uint valueBits, uint midpointSignificand, int midpointExponent)
{
    uint valueSignificand;
    int valueExponent;
    finiteFloatComponents(valueBits, valueSignificand, valueExponent);

    uint64_t squareWide = uint64_t(midpointSignificand) * uint64_t(midpointSignificand);
    uint squareHigh = uint(squareWide >> 32u);
    uint squareLow = uint(squareWide);
    int shift = valueExponent - 2 * midpointExponent;
    if (shift < 0)
        return 1;
    if (shift >= 96)
        return -1;

    uint3 square = uint3(squareLow, squareHigh, 0u);
    uint3 value = shiftTo96(valueSignificand, shift);
    if (square.z != value.z)
        return square.z < value.z ? -1 : 1;
    if (square.y != value.y)
        return square.y < value.y ? -1 : 1;
    if (square.x != value.x)
        return square.x < value.x ? -1 : 1;
    return 0;
}

float ieeeSqrt(float value)
{
    float result = sqrt(value);
    if (!(value > 0.0) || isinf(value))
        return result;

    uint valueBits = asuint(value);
    uint resultBits = asuint(result);
    for (int iteration = 0; iteration < 4; ++iteration)
    {
        uint midpointSignificand;
        int midpointExponent;
        midpointComponents(
            resultBits - 1u,
            resultBits,
            midpointSignificand,
            midpointExponent);
        if (compareSquareToValue(
                valueBits,
                midpointSignificand,
                midpointExponent) > 0)
        {
            --resultBits;
            continue;
        }

        midpointComponents(
            resultBits,
            resultBits + 1u,
            midpointSignificand,
            midpointExponent);
        if (compareSquareToValue(
                valueBits,
                midpointSignificand,
                midpointExponent) < 0)
        {
            ++resultBits;
            continue;
        }
        break;
    }
    return asfloat(resultBits);
}

float appleLength(float2 value)
{
    float square = fma(value.y, value.y, value.x * value.x);
    float root = ieeeSqrt(square);
    uint squareBits = asuint(square);
    uint mantissa = squareBits & 0x7fffffu;
    uint packedMantissa = mantissa >> 1u;
    uint byteAddress = packedMantissa & ~3u;
    uint packedWord = appleFastSqrtTable.Load(int(byteAddress));
    uint packedCode = (packedWord >> ((packedMantissa & 3u) * 8u)) & 0xffu;
    uint code = (packedCode >> ((mantissa & 1u) * 4u)) & 15u;
    uint correction = ((squareBits >> 23u) & 1u) == 0u
        ? code & 3u
        : (code >> 2u) & 3u;
    return asfloat(uint(int(asuint(root)) + int(correction) - 1));
}

uint2 multiplySigned64(int left, int right)
{
    uint64_t product = uint64_t(int64_t(left) * int64_t(right));
    return uint2(uint(product), uint(product >> 32u));
}

uint2 addSigned64(uint2 left, uint2 right)
{
    uint low = left.x + right.x;
    return uint2(low, left.y + right.y + uint(low < left.x));
}

bool signedLess64(uint2 left, uint2 right)
{
    int leftHigh = int(left.y);
    int rightHigh = int(right.y);
    return leftHigh != rightHigh ? leftHigh < rightHigh : left.x < right.x;
}

int4 ownerBounds(int slot)
{
    return ownerData[kOwnerBoundsOffset + uint(slot)];
}

int4 ownerOriginExtent(int slot)
{
    return ownerData[kOwnerOriginExtentOffset + uint(slot)];
}

int4 ownerControl(int slot)
{
    return ownerData[kOwnerControlOffset + uint(slot)];
}

int ownerPrimitive(int slot, int2 coordinate)
{
    int4 transform = ownerOriginExtent(slot);
    int2 relative = coordinate * 256 + int2(128) - transform.xy;
    uint2 x = multiplySigned64(relative.x, transform.w);
    uint2 y = multiplySigned64(relative.y, transform.z);
    if (ownerControl(slot).y != 0)
        return signedLess64(x, y) ? 1 : 0;
    return signedLess64(
        addSigned64(x, y),
        multiplySigned64(transform.z, transform.w)) ? 1 : 0;
}

int ownerPrimitiveAt(int slot, int2 coordinate)
{
    int4 bounds = ownerBounds(slot);
    if (coordinate.x < bounds.x || coordinate.y < bounds.y
        || coordinate.x >= bounds.z || coordinate.y >= bounds.w)
        return -1;
    int primitive = ownerPrimitive(slot, coordinate);
    return (ownerControl(slot).z & (1 << primitive)) != 0 ? primitive : -1;
}

int ownerCode(int2 coordinate, uint slotCount)
{
    int code = 0;
    for (uint slot = 0; slot < kMaxOwners; ++slot)
    {
        if (slot >= slotCount)
            break;
        int primitive = ownerPrimitiveAt(int(slot), coordinate);
        if (primitive >= 0)
            code = int(slot) * 2 + primitive + 1;
    }
    return code;
}

float2 ownerCoordinates(int2 coordinate, int slot, int primitive, uint packedWidth)
{
    int start = ownerControl(slot).x;
    int offsetX = coordinate.x - start;
    int offsetY = coordinate.y - start;
    uint row = uint(slot * 2 + primitive);
    uint2 xAxis = (offsetX >= 0 && uint(offsetX) < packedWidth)
        ? axisTable[row * packedWidth + uint(offsetX)]
        : uint2(0u);
    uint2 yAxis = (offsetY >= 0 && uint(offsetY) < packedWidth)
        ? axisTable[row * packedWidth + uint(offsetY)]
        : uint2(0u);
    return float2(asfloat(xAxis.x), asfloat(yAxis.y));
}

void decodeOwnerCode(int code, out int slot, out int primitive)
{
    --code;
    slot = code / 2;
    primitive = code & 1;
}

float2 partnerCoordinates(
    int2 coordinate,
    int centerSlot,
    int centerPrimitive,
    int fallbackSlot,
    int fallbackPrimitive,
    uniform MaskPush push)
{
    int slot = fallbackSlot;
    int primitive = fallbackPrimitive;
    if (ownerControl(centerSlot).w == 1)
    {
        slot = centerSlot;
        primitive = ownerPrimitiveAt(slot, coordinate);
        if (primitive < 0)
            primitive = centerPrimitive;
    }
    else
    {
        int code = ownerCode(coordinate, push.baseOwnerCount);
        if (code > 0)
            decodeOwnerCode(code, slot, primitive);
    }
    return ownerCoordinates(coordinate, slot, primitive, push.packedWidth);
}

[shader("vertex")]
MaskVertexOutput maskVertex(MaskVertexInput input, uniform MaskPush push)
{
    MaskVertexOutput output;
    output.position = float4(
        2.0 * input.position.x / push.resolution.x - 1.0,
        1.0 - 2.0 * input.position.y / push.resolution.y,
        0.0,
        1.0);
    output.sdf = lerp(input.secondCoordinates, input.firstCoordinates, push.compactFamily);
    return output;
}

uint roundR8Even(float value)
{
    float scaled = value * 255.0;
    uint truncated = uint(scaled);
    float remainder = scaled - float(truncated);
    if (remainder > 0.5 || (remainder == 0.5 && (truncated & 1u) != 0u))
        ++truncated;
    return min(truncated, 255u);
}

[shader("fragment")]
uint maskFragment(
    MaskVertexOutput input,
    uint primitiveID : SV_PrimitiveID,
    uniform MaskPush push) : SV_Target0
{
    int2 fallback = primitiveMappings[min(primitiveID, push.primitiveCount - 1u)];
    int fallbackSlot = fallback.x;
    int fallbackPrimitive = fallback.y;
    int2 coordinate = int2(input.position.xy);
    int centerSlot = fallbackSlot;
    int centerPrimitive = fallbackPrimitive;
    int centerCode = ownerCode(coordinate, push.ownerCount);
    if (centerCode > 0)
        decodeOwnerCode(centerCode, centerSlot, centerPrimitive);

    float distanceValue = appleLength(ownerCoordinates(
        coordinate, centerSlot, centerPrimitive, push.packedWidth));
    float distanceX = appleLength(partnerCoordinates(
        int2(coordinate.x ^ 1, coordinate.y),
        centerSlot,
        centerPrimitive,
        fallbackSlot,
        fallbackPrimitive,
        push));
    float distanceY = appleLength(partnerCoordinates(
        int2(coordinate.x, coordinate.y ^ 1),
        centerSlot,
        centerPrimitive,
        fallbackSlot,
        fallbackPrimitive,
        push));
    if (input.sdf.x > 1.0e30)
        distanceValue = input.sdf.x;

    float feather = max(
        abs(distanceX - distanceValue) + abs(distanceY - distanceValue),
        1.0e-4);
    float alpha = clamp((1.0 - distanceValue) / feather + 0.5, 0.0, 1.0);
    float halfAlpha = alpha == 0.0 || alpha == 1.0
        ? alpha
        : float16BitsToFloat32(float32ToFloat16RNEBits(alpha));
    return roundR8Even(halfAlpha);
}
