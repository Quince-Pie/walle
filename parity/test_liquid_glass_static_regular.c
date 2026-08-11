#include "liquid_glass_static_regular.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct case_data {
    struct walle_lg_static_regular_request request;
    uint32_t bleed_bits;
    int32_t crop_origin[2];
    uint32_t active_extent[2];
    uint32_t producer_extent[2];
    int32_t clamp[4];
    int32_t selected_bounds[4];
    uint32_t destination_extent[2];
    int32_t copy_offset[2];
    int32_t effective_origin[2];
};

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static bool equal_i32(const int32_t *left, const int32_t *right, size_t count)
{
    return memcmp(left, right, count * sizeof(*left)) == 0;
}

static bool equal_u32(const uint32_t *left, const uint32_t *right, size_t count)
{
    return memcmp(left, right, count * sizeof(*left)) == 0;
}

int main(void)
{
    static const struct case_data cases[] = {
        {
            .request = {256, 512.0, 512.0, 1024, 1024},
            .bleed_bits = UINT32_C(0x42b33333),
            .crop_origin = {74, 74},
            .active_extent = {108, 108},
            .producer_extent = {128, 128},
            .clamp = {0, 0, 107, 107},
            .selected_bounds = {0, 0, 256, 256},
            .destination_extent = {256, 256},
            .copy_offset = {-74, -74},
            .effective_origin = {0, 0},
        },
        {
            .request = {512, 337.0, 419.0, 1024, 1024},
            .bleed_bits = UINT32_C(0x43333333),
            .crop_origin = {0, 43},
            .active_extent = {193, 213},
            .producer_extent = {256, 256},
            .clamp = {0, 0, 192, 212},
            .selected_bounds = {-64, -64, 320, 384},
            .destination_extent = {320, 384},
            .copy_offset = {-64, -107},
            .effective_origin = {-64, -64},
        },
        {
            .request = {640, 602.25, 377.75, 1024, 1024},
            .bleed_bits = UINT32_C(0x43600000),
            .crop_origin = {15, 26},
            .active_extent = {241, 230},
            .producer_extent = {256, 256},
            .clamp = {0, 0, 240, 229},
            .selected_bounds = {-64, -64, 384, 384},
            .destination_extent = {384, 384},
            .copy_offset = {-79, -90},
            .effective_origin = {-64, -64},
        },
        {
            .request = {896, 512.0, 512.0, 1024, 1024},
            .bleed_bits = UINT32_C(0x439ccccd),
            .crop_origin = {0, 0},
            .active_extent = {256, 256},
            .producer_extent = {256, 256},
            .clamp = {0, 0, 255, 255},
            .selected_bounds = {-64, -64, 384, 384},
            .destination_extent = {384, 384},
            .copy_offset = {-64, -64},
            .effective_origin = {-64, -64},
        },
        {
            .request = {1536, 512.0, 512.0, 1024, 1024},
            .bleed_bits = UINT32_C(0x44066666),
            .crop_origin = {0, 0},
            .active_extent = {256, 256},
            .producer_extent = {256, 256},
            .clamp = {0, 0, 255, 255},
            .selected_bounds = {-64, -64, 384, 384},
            .destination_extent = {384, 384},
            .copy_offset = {-64, -64},
            .effective_origin = {-64, -64},
        },
        {
            .request = {377, 301.25, 699.75, 1024, 1024},
            .bleed_bits = UINT32_C(0x4303f333),
            .crop_origin = {0, 1},
            .active_extent = {155, 160},
            .producer_extent = {192, 192},
            .clamp = {0, 0, 154, 159},
            .selected_bounds = {-64, -64, 320, 320},
            .destination_extent = {320, 320},
            .copy_offset = {-64, -65},
            .effective_origin = {-64, -64},
        },
    };

    uint32_t comparisons = 0;
    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
        const struct case_data *expected = &cases[index];
        struct walle_lg_static_regular_geometry actual;
        if (!walle_lg_static_regular_geometry(&expected->request, &actual)
            || float_bits(actual.input_bleed_amount) != expected->bleed_bits
            || !equal_i32(actual.crop_origin, expected->crop_origin, 2)
            || !equal_u32(actual.active_extent, expected->active_extent, 2)
            || !equal_u32(actual.producer_extent, expected->producer_extent, 2)
            || !equal_i32(actual.texture_coordinate_clamp, expected->clamp, 4)
            || !equal_i32(
                actual.selected_region.integer_bounds,
                expected->selected_bounds,
                4)
            || !equal_u32(
                actual.selected_region.allocated_extent,
                expected->destination_extent,
                2)
            || !equal_i32(actual.selected_region.copy_offset, expected->copy_offset, 2)
            || !equal_i32(actual.effective_origin, expected->effective_origin, 2)) {
            fprintf(stderr, "static regular geometry case %zu differs\n", index);
            return 1;
        }
        comparisons += 23;
    }

    struct walle_lg_static_regular_request invalid = cases[0].request;
    invalid.diameter = 0;
    struct walle_lg_static_regular_geometry ignored;
    if (walle_lg_static_regular_geometry(&invalid, &ignored)) {
        fputs("static regular geometry accepted an empty circle\n", stderr);
        return 1;
    }
    printf("static regular geometry: %u/%u exact values\n", comparisons, comparisons);
    return 0;
}
