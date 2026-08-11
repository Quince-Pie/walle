#ifndef WALLE_LIQUID_GLASS_SELECTED_REGION_H
#define WALLE_LIQUID_GLASS_SELECTED_REGION_H

#include <stdbool.h>
#include <stdint.h>

struct walle_lg_selected_region_request {
    int32_t bounds[4];
    float blur_radius;
    float bleed_blur_radius;
    float backdrop_scale;
};

struct walle_lg_selected_region {
    float radius1;
    float scaled_radius;
    uint32_t maximum_level_count;
    uint32_t level_count;
    uint32_t alignment_exponent;
    uint32_t alignment_scale;
    int32_t integer_bounds[4];
    uint32_t allocated_extent[2];
    int32_t copy_offset[2];
};

[[nodiscard]] bool walle_lg_regular_selected_region(
    const struct walle_lg_selected_region_request *request,
    struct walle_lg_selected_region *result
);

#endif
