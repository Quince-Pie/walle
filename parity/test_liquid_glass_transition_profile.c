#include "liquid_glass_transition_profile.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static constexpr unsigned char fixture[] = {
#embed "transition_profile_v1_fixture.bin"
};

static uint32_t load_u32(const unsigned char* bytes)
{
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 | (uint32_t)bytes[2] << 16
           | (uint32_t)bytes[3] << 24;
}

static float float_from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

int main(void)
{
    static constexpr unsigned char magic[]             = "WLGTPV1";
    static constexpr size_t        header_size         = 24;
    static constexpr size_t        record_header_size  = 28;
    static constexpr uint32_t      expected_profiles   = 252;
    static constexpr uint32_t      expected_size       = 258;
    static constexpr uint32_t      expected_model_base = 64;

    if (sizeof fixture < header_size || memcmp(fixture, magic, sizeof magic) != 0
        || load_u32(fixture + 8) != 1 || load_u32(fixture + 12) != expected_size
        || load_u32(fixture + 16) != expected_model_base
        || load_u32(fixture + 20) != expected_profiles) {
        fputs("transition-profile fixture contract differs\n", stderr);
        return 1;
    }

    size_t   offset         = header_size;
    uint32_t exact_profiles = 0;
    uint32_t exact_bytes    = 0;
    uint32_t modeled_bytes  = 0;
    for (uint32_t record = 0; record < expected_profiles; ++record) {
        if (offset + record_header_size + expected_size > sizeof fixture
            || fixture[offset] > WALLE_LG_MATERIAL_REGULAR
            || fixture[offset + 1] > WALLE_LG_APPEARANCE_DARK
            || fixture[offset + 2] > 1 || fixture[offset + 3] != 0) {
            fprintf(stderr, "transition-profile record %u header differs\n", record);
            return 1;
        }
        struct walle_lg_transition_profile_request request = {
            .transition = {
                .material = (enum walle_lg_material)fixture[offset],
                .appearance = (enum walle_lg_appearance)fixture[offset + 1],
                .diameter = load_u32(fixture + offset + 4),
                .visible_fraction = float_from_bits(load_u32(fixture + offset + 8)),
            },
            .sdf_half_width = float_from_bits(load_u32(fixture + offset + 12)),
            .sdf_half_height = float_from_bits(load_u32(fixture + offset + 16)),
            .source_texel_step_x = float_from_bits(load_u32(fixture + offset + 20)),
            .source_texel_step_y = float_from_bits(load_u32(fixture + offset + 24)),
        };
        offset += record_header_size;

        struct walle_lg_profile_payload actual;
        if (!walle_lg_transition_profile(&request, &actual)) {
            fprintf(stderr, "transition-profile record %u was rejected\n", record);
            return 1;
        }
        bool exact = true;
        for (uint32_t byte = 0; byte < expected_size; ++byte) {
            if (actual.byte[byte] != fixture[offset + byte]) {
                fprintf(stderr,
                        "transition-profile record %u byte %u: expected %02x, got %02x\n",
                        record,
                        byte,
                        fixture[offset + byte],
                        actual.byte[byte]);
                exact = false;
                break;
            }
            ++exact_bytes;
            if (byte >= expected_model_base) {
                ++modeled_bytes;
            }
        }
        if (!exact) {
            return 1;
        }
        ++exact_profiles;
        offset += expected_size;
    }

    if (offset != sizeof fixture || exact_profiles != expected_profiles
        || exact_bytes != expected_profiles * expected_size
        || modeled_bytes != expected_profiles * (expected_size - expected_model_base)) {
        fputs("transition-profile fixture coverage differs\n", stderr);
        return 1;
    }
    printf("transition profile packing: %u/%u exact profiles, %u/%u exact bytes, "
           "%u/%u exact modeled bytes\n",
           exact_profiles,
           expected_profiles,
           exact_bytes,
           expected_profiles * expected_size,
           modeled_bytes,
           expected_profiles * (expected_size - expected_model_base));
    return 0;
}
