#include "liquid_glass_materialize.h"
#include "liquid_glass_selected_region.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static constexpr unsigned char fixture[] = {
#embed "selected_region_v1_fixture.bin"
};

static uint32_t load_u32(const unsigned char *bytes)
{
    return (uint32_t)bytes[0]
        | (uint32_t)bytes[1] << 8
        | (uint32_t)bytes[2] << 16
        | (uint32_t)bytes[3] << 24;
}

static int32_t load_i32(const unsigned char *bytes)
{
    uint32_t bits = load_u32(bytes);
    int32_t value;
    memcpy(&value, &bits, sizeof(value));
    return value;
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

static bool rejects_invalid_requests(void)
{
    struct walle_lg_selected_region result;
    struct walle_lg_selected_region_request request = {
        .bounds = {0, 0, 500, 500},
        .blur_radius = 2.0f,
        .bleed_blur_radius = 80.0f,
        .backdrop_scale = 1.0f,
    };
    if (walle_lg_regular_selected_region(nullptr, &result)
        || walle_lg_regular_selected_region(&request, nullptr)) {
        return false;
    }
    request.bounds[2] = 0;
    if (walle_lg_regular_selected_region(&request, &result)) {
        return false;
    }
    request.bounds[2] = 500;
    request.backdrop_scale = float_from_bits(0x7fc00000u);
    return !walle_lg_regular_selected_region(&request, &result);
}

static bool compare_u32(
    uint32_t actual,
    uint32_t expected,
    uint32_t sample,
    const char *field,
    uint32_t *comparisons
)
{
    ++*comparisons;
    if (actual == expected) {
        return true;
    }
    fprintf(stderr, "selected-region sample %u %s: expected %08x, got %08x\n",
            sample, field, expected, actual);
    return false;
}

int main(void)
{
    static constexpr unsigned char magic[] = "WLGSRO1";
    static constexpr size_t header_size = 24;
    static constexpr size_t record_size = 80;
    static constexpr uint32_t expected_samples = 32;
    static constexpr uint32_t expected_comparisons = 448;

    if (sizeof(fixture) != header_size + record_size * expected_samples
        || memcmp(fixture, magic, sizeof(magic)) != 0
        || load_u32(fixture + 8) != 1
        || load_u32(fixture + 12) != expected_samples
        || load_u32(fixture + 16) != expected_comparisons
        || load_u32(fixture + 20) != 500
        || !rejects_invalid_requests()) {
        fputs("selected-region fixture contract differs\n", stderr);
        return 1;
    }

    size_t offset = header_size;
    uint32_t comparisons = 0;
    for (uint32_t sample = 1; sample <= expected_samples; ++sample) {
        float fraction = float_from_bits(load_u32(fixture + offset));
        float backdrop_scale = float_from_bits(load_u32(fixture + offset + 4));
        offset += 8;
        struct walle_lg_selected_region_request request = {
            .bounds = {
                load_i32(fixture + offset),
                load_i32(fixture + offset + 4),
                load_i32(fixture + offset + 8),
                load_i32(fixture + offset + 12),
            },
            .backdrop_scale = backdrop_scale,
        };
        offset += 16;

        struct walle_lg_transition_request transition_request = {
            .material = WALLE_LG_MATERIAL_REGULAR,
            .appearance = WALLE_LG_APPEARANCE_DARK,
            .diameter = 500,
            .visible_fraction = fraction,
        };
        struct walle_lg_numeric_inputs material;
        struct walle_lg_selected_region selected;
        if (!walle_lg_transition_numeric_inputs(&transition_request, &material)) {
            fputs("selected-region material join was rejected\n", stderr);
            return 1;
        }
        request.blur_radius = material.value[WALLE_LG_INPUT_BLUR_RADIUS];
        request.bleed_blur_radius = material.value[WALLE_LG_INPUT_BLEED_BLUR_RADIUS];
        if (!walle_lg_regular_selected_region(&request, &selected)) {
            fprintf(stderr, "selected-region sample %u was rejected\n", sample);
            return 1;
        }

        const char *scalar_names[] = {
            "radius1",
            "scaled_radius",
            "maximum_level_count",
            "level_count",
            "alignment_exponent",
            "alignment_scale",
        };
        uint32_t scalar_values[] = {
            float_bits(selected.radius1),
            float_bits(selected.scaled_radius),
            selected.maximum_level_count,
            selected.level_count,
            selected.alignment_exponent,
            selected.alignment_scale,
        };
        for (size_t index = 0; index < 6; ++index) {
            uint32_t expected = load_u32(fixture + offset);
            offset += 4;
            if (!compare_u32(scalar_values[index], expected, sample,
                             scalar_names[index], &comparisons)) {
                return 1;
            }
        }
        for (size_t index = 0; index < 4; ++index) {
            uint32_t expected = load_u32(fixture + offset);
            offset += 4;
            if (!compare_u32((uint32_t)selected.integer_bounds[index], expected,
                             sample, "integer_bounds", &comparisons)) {
                return 1;
            }
        }
        for (size_t index = 0; index < 2; ++index) {
            uint32_t expected = load_u32(fixture + offset);
            offset += 4;
            if (!compare_u32(selected.allocated_extent[index], expected,
                             sample, "allocated_extent", &comparisons)) {
                return 1;
            }
        }
        for (size_t index = 0; index < 2; ++index) {
            uint32_t expected = load_u32(fixture + offset);
            offset += 4;
            if (!compare_u32((uint32_t)selected.copy_offset[index], expected,
                             sample, "copy_offset", &comparisons)) {
                return 1;
            }
        }
    }

    if (offset != sizeof(fixture) || comparisons != expected_comparisons) {
        fputs("selected-region fixture coverage differs\n", stderr);
        return 1;
    }
    printf("selected-region transfer: %u/%u exact values\n",
           comparisons, expected_comparisons);
    return 0;
}
