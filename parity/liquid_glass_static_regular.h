#ifndef WALLE_LIQUID_GLASS_STATIC_REGULAR_H
#define WALLE_LIQUID_GLASS_STATIC_REGULAR_H

#include "liquid_glass_selected_region.h"

#include <stdbool.h>
#include <stdint.h>

struct walle_lg_static_regular_request {
    uint32_t diameter;
    double center_x;
    double center_y;
    uint32_t window_width;
    uint32_t window_height;
};

struct walle_lg_static_regular_geometry {
    float input_bleed_amount;
    int32_t crop_origin[2];
    uint32_t active_extent[2];
    uint32_t producer_extent[2];
    int32_t texture_coordinate_clamp[4];
    struct walle_lg_selected_region selected_region;
    int32_t effective_origin[2];
};

[[nodiscard]] bool walle_lg_static_regular_geometry(
    const struct walle_lg_static_regular_request *request,
    struct walle_lg_static_regular_geometry *result
);

#endif
