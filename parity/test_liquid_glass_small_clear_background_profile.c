#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "liquid_glass_transition_profile.h"

static constexpr unsigned char fixture[] = {
#embed "small_clear_background_profile_v1_fixture.bin"
};

static uint32_t load_u32(const unsigned char* bytes)
{
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 | (uint32_t)bytes[2] << 16
           | (uint32_t)bytes[3] << 24;
}

static uint64_t load_u64(const unsigned char* bytes)
{
    return (uint64_t)load_u32(bytes) | (uint64_t)load_u32(bytes + 4) << 32;
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

int main(void)
{
    static constexpr unsigned char magic[]            = "WLGSCB1";
    static constexpr size_t        header_size        = 24;
    static constexpr size_t        record_header_size = 28;
    static constexpr uint32_t      expected_profiles  = 60;
    static constexpr uint32_t expected_size = WALLE_LG_SMALL_CLEAR_BACKGROUND_PROFILE_BYTE_COUNT;

    if (sizeof fixture < header_size || memcmp(fixture, magic, sizeof magic) != 0
        || load_u32(fixture + 8) != 1 || load_u32(fixture + 12) != expected_size
        || load_u32(fixture + 16) != record_header_size
        || load_u32(fixture + 20) != expected_profiles) {
        fputs("small-clear background profile fixture contract differs\n", stderr);
        return 1;
    }

    size_t   offset         = header_size;
    uint32_t exact_profiles = 0;
    uint32_t exact_bytes    = 0;
    for (uint32_t record = 0; record < expected_profiles; ++record) {
        if (offset + record_header_size + expected_size > sizeof fixture
            || fixture[offset] > WALLE_LG_APPEARANCE_DARK || fixture[offset + 1] > 1
            || fixture[offset + 2] != 0 || fixture[offset + 3] != 0) {
            fprintf(stderr, "small-clear background profile record %u header differs\n", record);
            return 1;
        }
        struct walle_lg_small_clear_background_profile_request request = {
            .appearance       = (enum walle_lg_appearance)fixture[offset],
            .diameter         = load_u32(fixture + offset + 4),
            .visible_fraction = float_from_bits(load_u32(fixture + offset + 12)),
            .element_extent   = double_from_bits(load_u64(fixture + offset + 16)),
            .backdrop_scale   = float_from_bits(load_u32(fixture + offset + 24)),
        };
        uint32_t sample_index = load_u32(fixture + offset + 8);
        offset += record_header_size;

        struct walle_lg_small_clear_background_profile_payload actual;
        if (!walle_lg_small_clear_background_profile(&request, &actual)) {
            fprintf(stderr,
                    "small-clear background profile record %u sample %u was rejected\n",
                    record,
                    sample_index);
            return 1;
        }
        for (uint32_t byte = 0; byte < expected_size; ++byte) {
            if (actual.byte[byte] != fixture[offset + byte]) {
                fprintf(stderr,
                        "small-clear background profile record %u sample %u byte %u: "
                        "expected %02x, got %02x\n",
                        record,
                        sample_index,
                        byte,
                        fixture[offset + byte],
                        actual.byte[byte]);
                return 1;
            }
            ++exact_bytes;
        }
        ++exact_profiles;
        offset += expected_size;
    }

    if (offset != sizeof fixture || exact_profiles != expected_profiles
        || exact_bytes != expected_profiles * expected_size) {
        fputs("small-clear background profile fixture coverage differs\n", stderr);
        return 1;
    }
    printf("small-clear Tghn profile packing: %u/%u exact profiles, %u/%u exact bytes\n",
           exact_profiles,
           expected_profiles,
           exact_bytes,
           expected_profiles * expected_size);
    return 0;
}
