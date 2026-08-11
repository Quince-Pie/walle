#include "liquid_glass_darwin_powf.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static float float_from_bits(uint32_t bits)
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

int main(int argc, char** argv)
{
    if (argc != 2 || strcmp(argv[1], "--emit") != 0) {
        fputs("usage: test_liquid_glass_profile_clamp_powf_sweep --emit\n", stderr);
        return 64;
    }

    volatile float gamma    = 2.2f;
    volatile float exponent = 1.0f / gamma;
    if (float_bits(exponent) != 0x3ee8ba2eu) {
        fputs("profile clamp exponent differs\n", stderr);
        return 1;
    }

    static constexpr uint32_t first = 0x3f800000u;
    static constexpr uint32_t last  = 0x40000000u;
    uint16_t                  output[4096];
    size_t                    count = 0;
    for (uint32_t bits = first;; ++bits) {
        float powered;
        if (!walle_lg_darwin_powf_1_over_2_2(float_from_bits(bits), &powered)) {
            fprintf(stderr, "portable profile clamp powf rejected base %08x\n", bits);
            return 1;
        }
#ifdef __APPLE__
        volatile float expected = powf(float_from_bits(bits), exponent);
        if (float_bits(powered) != float_bits(expected)) {
            fprintf(stderr,
                    "profile clamp powf base %08x: expected %08x, got %08x\n",
                    bits,
                    float_bits(expected),
                    float_bits(powered));
            return 1;
        }
#endif
        volatile _Float16 packed  = (_Float16)powered;
        memcpy(&output[count++], (const void*)&packed, sizeof packed);
        if (count == sizeof output / sizeof output[0] || bits == last) {
            if (fwrite(output, sizeof output[0], count, stdout) != count) {
                fputs("profile clamp sweep write failed\n", stderr);
                return 1;
            }
            count = 0;
        }
        if (bits == last) {
            break;
        }
    }
    fprintf(stderr,
            "profile clamp Darwin powf interval: %u/%u exact words\n",
            last - first + 1,
            last - first + 1);
    return 0;
}
