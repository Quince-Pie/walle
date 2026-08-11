#include <errno.h>
#include <limits.h>
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

static float float_from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

int main(int argc, char** argv)
{
    if (argc != 9) {
        fprintf(stderr,
                "usage: %s MATERIAL APPEARANCE DIAMETER FRACTION_BITS HALF_WIDTH_BITS "
                "HALF_HEIGHT_BITS STEP_X_BITS STEP_Y_BITS\n",
                argv[0]);
        return 64;
    }

    uint32_t material;
    uint32_t appearance;
    uint32_t diameter;
    uint32_t fraction_bits;
    uint32_t half_width_bits;
    uint32_t half_height_bits;
    uint32_t step_x_bits;
    uint32_t step_y_bits;
    if (!parse_u32(argv[1], 10, &material) || !parse_u32(argv[2], 10, &appearance)
        || !parse_u32(argv[3], 10, &diameter) || !parse_u32(argv[4], 16, &fraction_bits)
        || !parse_u32(argv[5], 16, &half_width_bits) || !parse_u32(argv[6], 16, &half_height_bits)
        || !parse_u32(argv[7], 16, &step_x_bits) || !parse_u32(argv[8], 16, &step_y_bits)
        || material > WALLE_LG_MATERIAL_REGULAR || appearance > WALLE_LG_APPEARANCE_DARK) {
        fputs("transition-profile arguments differ\n", stderr);
        return 64;
    }

    struct walle_lg_transition_profile_request request = {
        .transition = {
            .material = (enum walle_lg_material)material,
            .appearance = (enum walle_lg_appearance)appearance,
            .diameter = diameter,
            .visible_fraction = float_from_bits(fraction_bits),
        },
        .sdf_half_width = float_from_bits(half_width_bits),
        .sdf_half_height = float_from_bits(half_height_bits),
        .source_texel_step_x = float_from_bits(step_x_bits),
        .source_texel_step_y = float_from_bits(step_y_bits),
    };
    struct walle_lg_profile_payload payload;
    if (!walle_lg_transition_profile(&request, &payload)) {
        fputs("transition-profile request was rejected\n", stderr);
        return 1;
    }
    for (size_t index = 0; index < sizeof payload.byte; ++index) {
        printf("%02x", payload.byte[index]);
    }
    putchar('\n');
    return 0;
}
