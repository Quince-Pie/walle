#include "liquid_glass_materialize.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static constexpr unsigned char fixture[] = {
#embed "dematerialize_v1_fixture.bin"
};

static uint16_t load_u16(const unsigned char *bytes)
{
    return (uint16_t)((uint16_t)bytes[0] | (uint16_t)bytes[1] << 8);
}

static uint32_t load_u32(const unsigned char *bytes)
{
    return (uint32_t)bytes[0]
        | (uint32_t)bytes[1] << 8
        | (uint32_t)bytes[2] << 16
        | (uint32_t)bytes[3] << 24;
}

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

int main(void)
{
    static constexpr unsigned char magic[] = "WLGDTV1";
    static constexpr size_t header_size = 24;
    static constexpr size_t case_header_size = 8;
    static constexpr uint16_t expected_direction = 1;
    static constexpr uint32_t expected_field_count = 47;
    static constexpr uint32_t expected_case_count = 4;
    static constexpr uint32_t expected_sample_count = 31;
    static constexpr uint32_t expected_comparison_count = 5828;

    if (sizeof(fixture) < header_size
        || memcmp(fixture, magic, sizeof(magic)) != 0
        || load_u32(fixture + 8) != 1
        || load_u32(fixture + 12) != expected_field_count
        || load_u32(fixture + 16) != expected_case_count
        || load_u32(fixture + 20) != expected_sample_count) {
        fputs("dematerialize fixture contract differs\n", stderr);
        return 1;
    }

    size_t offset = header_size;
    uint32_t comparisons = 0;
    for (uint32_t case_index = 0; case_index < expected_case_count; ++case_index) {
        if (offset + case_header_size > sizeof(fixture)
            || load_u16(fixture + offset + 2) != expected_direction) {
            fputs("dematerialize fixture case header differs\n", stderr);
            return 1;
        }
        enum walle_lg_material material = fixture[offset];
        enum walle_lg_appearance appearance = fixture[offset + 1];
        uint32_t diameter = load_u32(fixture + offset + 4);
        offset += case_header_size;

        for (uint32_t sample_index = 1; sample_index <= expected_sample_count;
             ++sample_index) {
            size_t record_size = sizeof(uint32_t) * (1 + expected_field_count);
            if (offset + record_size > sizeof(fixture)) {
                fputs("dematerialize fixture ends inside a sample\n", stderr);
                return 1;
            }
            struct walle_lg_transition_request request = {
                .material = material,
                .appearance = appearance,
                .diameter = diameter,
                .visible_fraction = float_from_bits(load_u32(fixture + offset)),
            };
            struct walle_lg_numeric_inputs observed;
            offset += sizeof(uint32_t);
            if (!walle_lg_transition_numeric_inputs(&request, &observed)) {
                fprintf(stderr, "dematerialize case %u sample %u was rejected\n",
                        case_index, sample_index);
                return 1;
            }
            for (uint32_t field = 0; field < expected_field_count; ++field) {
                uint32_t expected = load_u32(fixture + offset);
                uint32_t actual = float_bits(observed.value[field]);
                offset += sizeof(uint32_t);
                ++comparisons;
                if (actual != expected) {
                    fprintf(
                        stderr,
                        "dematerialize case %u sample %u field %u: expected %08x, got %08x\n",
                        case_index,
                        sample_index,
                        field,
                        expected,
                        actual
                    );
                    return 1;
                }
            }
        }
    }

    if (offset != sizeof(fixture) || comparisons != expected_comparison_count) {
        fputs("dematerialize fixture coverage differs\n", stderr);
        return 1;
    }
    printf("dematerialize numeric transfer: %u/%u exact binary32 words\n",
           comparisons, expected_comparison_count);
    return 0;
}
