#include "liquid_glass_static_regular.h"

#include "liquid_glass_materialize.h"

#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>

static bool align_extent(uint32_t extent, uint32_t *result)
{
    if (extent == 0 || extent > UINT32_MAX - 63u) {
        return false;
    }
    *result = 64u * ((extent + 63u) / 64u);
    return true;
}

static bool crop_axis(
    double lower,
    double upper,
    uint32_t window_extent,
    int32_t *origin,
    uint32_t *active_extent,
    uint32_t *allocated_extent
)
{
    double clipped_lower = fmax(0.0, lower);
    double clipped_upper = fmin((double)window_extent, upper);
    if (!(clipped_upper > clipped_lower)) {
        return false;
    }
    double lower_value = ceil(0.25 * clipped_lower);
    double upper_value = floor(0.25 * clipped_upper);
    if (lower_value < 0.0 || lower_value > (double)INT32_MAX
        || upper_value <= lower_value || upper_value > (double)INT32_MAX) {
        return false;
    }
    int64_t lower_integer = (int64_t)lower_value;
    int64_t upper_integer = (int64_t)upper_value;
    uint64_t active = (uint64_t)(upper_integer - lower_integer);
    if (active > UINT32_MAX
        || !align_extent((uint32_t)active, allocated_extent)) {
        return false;
    }
    *origin = (int32_t)lower_integer;
    *active_extent = (uint32_t)active;
    return true;
}

bool walle_lg_static_regular_geometry(
    const struct walle_lg_static_regular_request *request,
    struct walle_lg_static_regular_geometry *result
)
{
    if (request == nullptr || result == nullptr || request->diameter == 0
        || request->window_width == 0 || request->window_height == 0
        || !isfinite(request->center_x) || !isfinite(request->center_y)) {
        return false;
    }

    struct walle_lg_numeric_inputs inputs;
    const struct walle_lg_transition_request transition = {
        .material = WALLE_LG_MATERIAL_REGULAR,
        .appearance = WALLE_LG_APPEARANCE_LIGHT,
        .diameter = request->diameter,
        .visible_fraction = 1.0f,
    };
    if (!walle_lg_transition_numeric_inputs(&transition, &inputs)) {
        return false;
    }

    double half_diameter = (double)request->diameter / 2.0;
    double margin = (double)inputs.value[WALLE_LG_INPUT_BLEED_AMOUNT];
    int32_t crop_origin[2];
    uint32_t active_extent[2];
    uint32_t producer_extent[2];
    if (!crop_axis(
            request->center_x - half_diameter - margin,
            request->center_x + half_diameter + margin,
            request->window_width,
            &crop_origin[0],
            &active_extent[0],
            &producer_extent[0])
        || !crop_axis(
            (double)request->window_height
                - (request->center_y + half_diameter) - margin,
            (double)request->window_height
                - (request->center_y - half_diameter) + margin,
            request->window_height,
            &crop_origin[1],
            &active_extent[1],
            &producer_extent[1])) {
        return false;
    }
    if (active_extent[0] > INT32_MAX || active_extent[1] > INT32_MAX) {
        return false;
    }

    const struct walle_lg_selected_region_request selected_request = {
        .bounds = {
            crop_origin[0],
            crop_origin[1],
            (int32_t)active_extent[0],
            (int32_t)active_extent[1],
        },
        .blur_radius = inputs.value[WALLE_LG_INPUT_BLUR_RADIUS],
        .bleed_blur_radius = inputs.value[WALLE_LG_INPUT_BLEED_BLUR_RADIUS],
        .backdrop_scale = 0.25f,
    };
    struct walle_lg_selected_region selected;
    if (!walle_lg_regular_selected_region(&selected_request, &selected)) {
        return false;
    }
    int64_t effective_x = (int64_t)crop_origin[0] + selected.copy_offset[0];
    int64_t effective_y = (int64_t)crop_origin[1] + selected.copy_offset[1];
    if (effective_x < INT32_MIN || effective_x > INT32_MAX
        || effective_y < INT32_MIN || effective_y > INT32_MAX) {
        return false;
    }

    *result = (struct walle_lg_static_regular_geometry){
        .input_bleed_amount = inputs.value[WALLE_LG_INPUT_BLEED_AMOUNT],
        .crop_origin = {crop_origin[0], crop_origin[1]},
        .active_extent = {active_extent[0], active_extent[1]},
        .producer_extent = {producer_extent[0], producer_extent[1]},
        .texture_coordinate_clamp = {
            0,
            0,
            (int32_t)active_extent[0] - 1,
            (int32_t)active_extent[1] - 1,
        },
        .selected_region = selected,
        .effective_origin = {(int32_t)effective_x, (int32_t)effective_y},
    };
    return true;
}
