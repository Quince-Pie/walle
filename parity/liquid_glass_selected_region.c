#include "liquid_glass_selected_region.h"

#include <float.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53);

static float rounded_multiply(float left, float right)
{
    volatile float result = left * right;
    return result;
}

static bool align_extent(int64_t extent, uint32_t *result)
{
    if (extent <= 0 || extent > UINT32_MAX - 63u) {
        return false;
    }
    uint64_t aligned = 64u * (((uint64_t)extent + 63u) / 64u);
    if (aligned > UINT32_MAX) {
        return false;
    }
    *result = (uint32_t)aligned;
    return true;
}

bool walle_lg_regular_selected_region(
    const struct walle_lg_selected_region_request *request,
    struct walle_lg_selected_region *result
)
{
    if (request == nullptr || result == nullptr
        || request->bounds[2] <= 0 || request->bounds[3] <= 0
        || !(request->blur_radius >= 0.0f)
        || !(request->bleed_blur_radius >= 0.0f)
        || !(request->backdrop_scale > 0.0f)) {
        return false;
    }

    volatile double doubled_blur = 2.0 * (double)request->blur_radius;
    double dominant_blur = doubled_blur > (double)request->bleed_blur_radius
        ? doubled_blur
        : (double)request->bleed_blur_radius;
    volatile double half_blur = 0.5 * dominant_blur;
    volatile double scaled_blur = half_blur * (double)request->backdrop_scale;
    float radius1 = (float)scaled_blur;
    float scaled_radius = rounded_multiply(radius1, 1.6f);

    int32_t maximum_extent = request->bounds[2] > request->bounds[3]
        ? request->bounds[2]
        : request->bounds[3];
    float maximum_log2 = log2f((float)maximum_extent);
    if (!isfinite(maximum_log2)) {
        return false;
    }
    uint32_t maximum_level_count = (uint32_t)floorf(maximum_log2) + 1u;
    uint32_t requested_level_count;
    if (scaled_radius == 0.0f) {
        requested_level_count = 1u;
    } else {
        float radius_log2 = log2f(scaled_radius);
        if (!isfinite(radius_log2)) {
            return false;
        }
        float rounded_level = ceilf(radius_log2);
        requested_level_count = (uint32_t)(rounded_level > 0.0f ? rounded_level : 0.0f) + 1u;
        if (requested_level_count == 1u) {
            requested_level_count = 2u;
        }
    }
    uint32_t level_count = requested_level_count < maximum_level_count
        ? requested_level_count
        : maximum_level_count;
    uint32_t alignment_exponent = level_count < 7u ? level_count : 7u;
    uint32_t alignment_scale = 1u << alignment_exponent;
    double reciprocal = 1.0 / (double)alignment_scale;

    int32_t integer_bounds[4];
    for (size_t axis = 0; axis < 2; ++axis) {
        double lower = (double)request->bounds[axis];
        double extent = (double)request->bounds[axis + 2];
        volatile double expansion = (-(double)radius1) * 2.8;
        volatile double expanded_lower = lower + expansion;
        volatile double expanded_extent = fma(-(double)radius1, -5.6, extent);
        volatile double reduced_lower = expanded_lower * reciprocal;
        volatile double reduced_extent = expanded_extent * reciprocal;
        volatile double reduced_upper = reduced_lower + reduced_extent;
        double lower_units_value = floor(reduced_lower);
        double upper_units_value = ceil(reduced_upper);
        if (lower_units_value < (double)INT32_MIN
            || lower_units_value > (double)INT32_MAX
            || upper_units_value < (double)INT32_MIN
            || upper_units_value > (double)INT32_MAX) {
            return false;
        }
        int64_t lower_units = (int64_t)lower_units_value;
        int64_t upper_units = (int64_t)upper_units_value;
        int64_t origin = lower_units * (int64_t)alignment_scale;
        int64_t desired_extent = (upper_units - lower_units) * (int64_t)alignment_scale;
        if (origin < INT32_MIN || origin > INT32_MAX
            || desired_extent <= 0 || desired_extent > INT32_MAX) {
            return false;
        }
        integer_bounds[axis] = (int32_t)origin;
        integer_bounds[axis + 2] = (int32_t)desired_extent;
    }

    uint32_t allocated_extent[2];
    if (!align_extent(integer_bounds[2], &allocated_extent[0])
        || !align_extent(integer_bounds[3], &allocated_extent[1])) {
        return false;
    }
    int64_t copy_offset_x = (int64_t)integer_bounds[0] - request->bounds[0];
    int64_t copy_offset_y = (int64_t)integer_bounds[1] - request->bounds[1];
    if (copy_offset_x < INT32_MIN || copy_offset_x > INT32_MAX
        || copy_offset_y < INT32_MIN || copy_offset_y > INT32_MAX) {
        return false;
    }

    *result = (struct walle_lg_selected_region){
        .radius1 = radius1,
        .scaled_radius = scaled_radius,
        .maximum_level_count = maximum_level_count,
        .level_count = level_count,
        .alignment_exponent = alignment_exponent,
        .alignment_scale = alignment_scale,
        .integer_bounds = {
            integer_bounds[0],
            integer_bounds[1],
            integer_bounds[2],
            integer_bounds[3],
        },
        .allocated_extent = {allocated_extent[0], allocated_extent[1]},
        .copy_offset = {(int32_t)copy_offset_x, (int32_t)copy_offset_y},
    };
    return true;
}
