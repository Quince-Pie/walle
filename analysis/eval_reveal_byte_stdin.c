/* Evaluate the reveal mask byte from three (x,y) coordinate word pairs fed
 * on stdin as "label cx cy hx hy vx vy" hex words; prints "label byte". */
#include "../parity/liquid_glass_reveal_mask_model.h"

#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

static float bits_float(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof bits);
    return bits;
}

static float half_round_trip(float value)
{
    uint32_t bits         = float_bits(value);
    uint32_t sign         = (bits >> 16u) & 0x8000u;
    uint32_t exponent     = (bits >> 23u) & 0xffu;
    uint32_t significand  = bits & 0x7fffffu;
    uint32_t half;
    if (exponent == 0xffu) {
        half = sign | (significand == 0u ? 0x7c00u : 0x7e00u);
    } else if (exponent == 0u) {
        half = sign;
    } else {
        int unbiased = (int)exponent - 127;
        int half_exponent = unbiased + 15;
        if (half_exponent >= 31) {
            half = sign | 0x7c00u;
        } else if (half_exponent <= 0) {
            if (unbiased < -25) {
                half = sign;
            } else {
                unsigned shift = (unsigned)(-unbiased - 1);
                uint32_t source = significand | 0x800000u;
                uint32_t rounded = source >> shift;
                uint32_t remainder = source & ((1u << shift) - 1u);
                uint32_t halfway = 1u << (shift - 1u);
                if (remainder > halfway || (remainder == halfway && (rounded & 1u) != 0u))
                    ++rounded;
                half = sign | (rounded < 0x400u ? rounded : 0x400u);
            }
        } else {
            uint32_t rounded   = significand >> 13u;
            uint32_t remainder = significand & 0x1fffu;
            if (remainder > 0x1000u || (remainder == 0x1000u && (rounded & 1u) != 0u))
                ++rounded;
            if (rounded == 0x400u) {
                rounded = 0u;
                ++half_exponent;
            }
            half = half_exponent >= 31 ? (sign | 0x7c00u)
                                       : (sign | ((uint32_t)half_exponent << 10u) | rounded);
        }
    }

    uint32_t out_sign        = (half & 0x8000u) << 16u;
    uint32_t half_exponent   = (half >> 10u) & 0x1fu;
    uint32_t half_significand = half & 0x3ffu;
    uint32_t result;
    if (half_exponent == 0u) {
        if (half_significand == 0u) {
            result = out_sign;
        } else {
            int shifted = -14;
            while ((half_significand & 0x400u) == 0u) {
                half_significand <<= 1u;
                --shifted;
            }
            half_significand &= 0x3ffu;
            result = out_sign | ((uint32_t)(shifted + 127) << 23u) | (half_significand << 13u);
        }
    } else if (half_exponent == 0x1fu) {
        result = out_sign | 0x7f800000u | (half_significand << 13u);
    } else {
        result = out_sign | ((half_exponent + 112u) << 23u) | (half_significand << 13u);
    }
    return bits_float(result);
}

int main(void)
{
    static uint8_t packed_sqrt[1u << 22];
    FILE* sqrt_file = fopen("parity/apple_fast_sqrt_correction_nibbles.bin", "rb");
    if (sqrt_file == NULL)
        return 2;
    if (fread(packed_sqrt, 1, sizeof packed_sqrt, sqrt_file) != sizeof packed_sqrt)
        return 2;
    fclose(sqrt_file);
    static uint8_t sqrt_table[WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT];
    for (size_t m = 0; m < sizeof sqrt_table; ++m)
        sqrt_table[m] = (uint8_t)((packed_sqrt[m >> 1] >> ((m & 1u) * 4u)) & 0x0fu);

    char     label[256];
    uint32_t w[6];
    while (scanf("%255s %x %x %x %x %x %x", label,
                 &w[0], &w[1], &w[2], &w[3], &w[4], &w[5]) == 7) {
        float center[2]     = {bits_float(w[0]), bits_float(w[1])};
        float horizontal[2] = {bits_float(w[2]), bits_float(w[3])};
        float vertical[2]   = {bits_float(w[4]), bits_float(w[5])};
        float cd, hd, vd;
        if (!walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table, sizeof sqrt_table,
                fmaf(center[1], center[1], center[0] * center[0]), &cd)
            || !walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table, sizeof sqrt_table,
                fmaf(horizontal[1], horizontal[1],
                     horizontal[0] * horizontal[0]), &hd)
            || !walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table, sizeof sqrt_table,
                fmaf(vertical[1], vertical[1], vertical[0] * vertical[0]),
                &vd)) {
            printf("%s FAIL\n", label);
            continue;
        }
        float feather = fabsf(hd - cd) + fabsf(vd - cd);
        if (feather < 1.0e-4f)
            feather = 1.0e-4f;
        float alpha = (1.0f - cd) / feather + 0.5f;
        alpha       = alpha < 0.0f ? 0.0f : (alpha > 1.0f ? 1.0f : alpha);
        float half_alpha
            = alpha == 0.0f || alpha == 1.0f ? alpha : half_round_trip(alpha);
        float    scaled    = half_alpha * 255.0f;
        unsigned truncated = (unsigned)scaled;
        float    remainder = scaled - (float)truncated;
        if (remainder > 0.5f || (remainder == 0.5f && (truncated & 1u)))
            ++truncated;
        unsigned coverage = truncated > 255u ? 255u : truncated;
        printf("%s %u\n", label, coverage);
    }
    return 0;
}
