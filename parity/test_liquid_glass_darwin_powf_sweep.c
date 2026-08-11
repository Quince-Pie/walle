#include "liquid_glass_darwin_powf.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static float float_from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static bool flush_word(unsigned char *buffer, size_t *used, uint32_t word)
{
    buffer[(*used)++] = (unsigned char)(word & UINT32_C(0xff));
    buffer[(*used)++] = (unsigned char)(word >> 8 & UINT32_C(0xff));
    buffer[(*used)++] = (unsigned char)(word >> 16 & UINT32_C(0xff));
    buffer[(*used)++] = (unsigned char)(word >> 24);
    if (*used != 16'384) {
        return true;
    }
    bool written = fwrite(buffer, 1, *used, stdout) == *used;
    *used = 0;
    return written;
}

int main(int argc, char **argv)
{
    bool emit = argc == 2 && strcmp(argv[1], "--emit") == 0;
    if (argc > 2 || (argc == 2 && !emit)) {
        fputs("usage: test_liquid_glass_darwin_powf_sweep [--emit]\n", stderr);
        return 2;
    }

    static constexpr uint32_t first_base_bits = UINT32_C(0x3f000000);
    static constexpr uint32_t last_base_bits = UINT32_C(0x3fa00000);
    unsigned char buffer[16'384];
    size_t used = 0;
    uint64_t comparisons = 0;

    for (uint32_t base_bits = first_base_bits;; ++base_bits) {
        float base = float_from_bits(base_bits);
        float actual;
        if (!walle_lg_darwin_powf_2_4(base, &actual)) {
            fprintf(stderr, "portable Darwin powf rejected base %08x\n", base_bits);
            return 1;
        }
#ifdef __APPLE__
        uint32_t expected_bits = float_bits(powf(base, 2.4f));
        if (float_bits(actual) != expected_bits) {
            fprintf(
                stderr,
                "Darwin powf base %08x: expected %08x, got %08x\n",
                base_bits,
                expected_bits,
                float_bits(actual)
            );
            return 1;
        }
#endif
        if (emit && !flush_word(buffer, &used, float_bits(actual))) {
            fputs("failed to write Darwin powf sweep\n", stderr);
            return 1;
        }
        ++comparisons;
        if (base_bits == last_base_bits) {
            break;
        }
    }

    if (emit && used != 0 && fwrite(buffer, 1, used, stdout) != used) {
        fputs("failed to finish Darwin powf sweep\n", stderr);
        return 1;
    }
    fprintf(stderr, "Darwin powf interval: %llu/%llu exact words\n",
            (unsigned long long)comparisons,
            (unsigned long long)comparisons);
    return 0;
}
