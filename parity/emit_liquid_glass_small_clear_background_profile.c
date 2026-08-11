#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "liquid_glass_transition_profile.h"

static bool parse_u32(const char* text, int base, uint32_t* result)
{
    char* end           = nullptr;
    errno               = 0;
    unsigned long value = strtoul(text, &end, base);
    if (errno != 0 || end == text || *end != '\0' || value > UINT32_MAX) {
        return false;
    }
    *result = (uint32_t)value;
    return true;
}

static bool parse_u64(const char* text, int base, uint64_t* result)
{
    char* end                = nullptr;
    errno                    = 0;
    unsigned long long value = strtoull(text, &end, base);
    if (errno != 0 || end == text || *end != '\0' || value > UINT64_MAX) {
        return false;
    }
    *result = (uint64_t)value;
    return true;
}

static float float_from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static double double_from_bits(uint64_t bits)
{
    double value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

int main(int argc, char** argv)
{
    if (argc != 6) {
        fprintf(stderr,
                "usage: %s APPEARANCE DIAMETER FRACTION_BITS ELEMENT_EXTENT_BITS "
                "BACKDROP_SCALE_BITS\n",
                argv[0]);
        return 64;
    }

    uint32_t appearance;
    uint32_t diameter;
    uint32_t fraction_bits;
    uint64_t element_extent_bits;
    uint32_t backdrop_scale_bits;
    if (!parse_u32(argv[1], 10, &appearance) || !parse_u32(argv[2], 10, &diameter)
        || !parse_u32(argv[3], 16, &fraction_bits) || !parse_u64(argv[4], 16, &element_extent_bits)
        || !parse_u32(argv[5], 16, &backdrop_scale_bits) || appearance > WALLE_LG_APPEARANCE_DARK) {
        fputs("small-clear background profile arguments differ\n", stderr);
        return 64;
    }

    struct walle_lg_small_clear_background_profile_request request = {
        .appearance       = (enum walle_lg_appearance)appearance,
        .diameter         = diameter,
        .visible_fraction = float_from_bits(fraction_bits),
        .element_extent   = double_from_bits(element_extent_bits),
        .backdrop_scale   = float_from_bits(backdrop_scale_bits),
    };
    struct walle_lg_small_clear_background_profile_payload payload;
    if (!walle_lg_small_clear_background_profile(&request, &payload)) {
        fputs("small-clear background profile request was rejected\n", stderr);
        return 1;
    }
    for (size_t index = 0; index < sizeof payload.byte; ++index) {
        printf("%02x", payload.byte[index]);
    }
    putchar('\n');
    return 0;
}
