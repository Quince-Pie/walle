#include "liquid_glass_reveal_mask_model.h"

#include <fenv.h>
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53);
static_assert(sizeof(_Float16) == 2);
static_assert(WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT == 8'388'608);
static_assert(sizeof(struct walle_lg_reveal_mask_vertex) == WALLE_LG_REVEAL_VERTEX_STRIDE);
static_assert(offsetof(struct walle_lg_reveal_mask_vertex, position) == 0);
static_assert(offsetof(struct walle_lg_reveal_mask_vertex, first_coordinates) == 16);
static_assert(offsetof(struct walle_lg_reveal_mask_vertex, second_coordinates) == 24);
static_assert(offsetof(struct walle_lg_reveal_mask_vertex, half_color) == 32);
static_assert(offsetof(struct walle_lg_reveal_mask_vertex, unused_tail) == 40);

static const uint16_t border_indices[WALLE_LG_REVEAL_MAX_INDEX_COUNT] = {
    0, 1, 5, 5, 4, 0, 3, 7, 6, 6, 2, 3,  10, 11, 15, 15, 14, 10, 9,  13, 12, 12, 8, 9,  1,  2, 6,
    6, 5, 1, 4, 5, 9, 9, 8, 4, 6, 7, 11, 11, 10, 6,  9,  10, 14, 14, 13, 9,  5,  6, 10, 10, 9, 5,
};

static float rounded_add(float left, float right)
{
    volatile float value = left + right;
    return value;
}

static float rounded_subtract(float left, float right)
{
    volatile float value = left - right;
    return value;
}

static float rounded_multiply(float left, float right)
{
    volatile float value = left * right;
    return value;
}

static float rounded_divide(float left, float right)
{
    volatile float value = left / right;
    return value;
}

static double rounded_add_double(double left, double right)
{
    volatile double value = left + right;
    return value;
}

static double rounded_subtract_double(double left, double right)
{
    volatile double value = left - right;
    return value;
}

static double rounded_multiply_double(double left, double right)
{
    volatile double value = left * right;
    return value;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static float bits_float(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static bool rounded_bound(double value, int32_t* result)
{
    double rounded = floor(rounded_add_double(value, 0.5));
    if (!isfinite(rounded) || rounded < (double)INT32_MIN || rounded > (double)INT32_MAX)
        return false;
    *result = (int32_t)rounded;
    return true;
}

static bool binary16_supported(void)
{
    volatile _Float16 one  = (_Float16)1.0f;
    volatile _Float16 half = (_Float16)0.5f;
    uint16_t          one_bits;
    uint16_t          half_bits;
    memcpy(&one_bits, (const void*)&one, sizeof(one_bits));
    memcpy(&half_bits, (const void*)&half, sizeof(half_bits));
    return one_bits == UINT16_C(0x3c00) && half_bits == UINT16_C(0x3800);
}

static uint16_t float_to_half_bits(float value)
{
    volatile _Float16 rounded = (_Float16)value;
    uint16_t          bits;
    memcpy(&bits, (const void*)&rounded, sizeof(bits));
    return bits;
}

static float half_bits_to_float(uint16_t bits)
{
    _Float16 value;
    memcpy(&value, &bits, sizeof(value));
    return (float)value;
}

static uint8_t half_to_unorm8(uint16_t bits)
{
    float value  = half_bits_to_float(bits);
    float scaled = rounded_multiply(value, 255.0f);
    if (!(scaled > 0.0f))
        return 0;
    if (scaled >= 255.0f)
        return UINT8_MAX;

    float    lower_value = floorf(scaled);
    float    remainder   = rounded_subtract(scaled, lower_value);
    uint32_t lower       = (uint32_t)lower_value;
    if (remainder > 0.5f || (remainder == 0.5f && (lower & 1u) != 0))
        ++lower;
    return (uint8_t)lower;
}

static bool
circle_distance(const uint8_t* table, size_t table_byte_count, float x, float y, float* result)
{
    float          x_squared = rounded_multiply(x, x);
    volatile float squared   = fmaf(y, y, x_squared);
    return walle_lg_reveal_mask_apple_fast_sqrt(table, table_byte_count, squared, result);
}

/* The circle's bounds, rounded to whole pixels the way the hardware does.
 *
 * This is the whole geometry law for an explicitly set progress, and it is
 * proven unique rather than merely sufficient: rounding to integer POINTS
 * instead changes 58 of the 65 byte-exact ladder states and rounding to half
 * pixels changes 52, while this changes none.
 */
static bool snapped_bounds(const struct walle_lg_reveal_mask_request* request,
                           double                                     radius,
                           int32_t                                    bounds[static 4])
{
    return rounded_bound(rounded_subtract_double(request->center_x, radius), &bounds[0])
           && rounded_bound(rounded_subtract_double(request->center_y, radius), &bounds[1])
           && rounded_bound(rounded_add_double(request->center_x, radius), &bounds[2])
           && rounded_bound(rounded_add_double(request->center_y, radius), &bounds[3]);
}

bool walle_lg_reveal_mask_circle_construct(const struct walle_lg_reveal_mask_request* request,
                                           struct walle_lg_reveal_mask_circle*        result)
{
    if (request == nullptr || result == nullptr || request->target_width == 0
        || request->target_height == 0 || request->target_width > INT32_MAX
        || request->target_height > INT32_MAX || !isfinite(request->center_x)
        || !isfinite(request->center_y) || !isfinite(request->maximum_radius)
        || !isfinite(request->progress) || request->maximum_radius < 0.0 || request->progress < 0.0
        || request->progress > 1.0 || fegetround() != FE_TONEAREST) {
        return false;
    }

    struct walle_lg_reveal_mask_circle circle = {};
    circle.unsnapped_radius = rounded_multiply_double(request->maximum_radius, request->progress);

    /* An ANIMATING reveal does not round.  Measured on 16 live frames spanning
     * radii 29 to 1454, its circle is the linear interpolation between the two
     * ROUNDED endpoint rects - which is what Core Animation does: an
     * explicitly set progress goes through the model layer, which lays out and
     * rounds its bounds, while an animating layer's presentation values are
     * interpolated without re-laying-out.  The model reproduces those frames to
     * 0.008 px where rounding is out by up to 0.49, and it predicts an
     * off-ladder frame from an entirely separate capture session to 0.002 px.
     *
     * It costs nothing in generality: both endpoints come from the same
     * rounding law, at progress 0 and 1, so any origin, radius or frame size
     * follows. */
    if (request->presentation_geometry) {
        int32_t start[4];
        int32_t end[4];
        if (!snapped_bounds(request, 0.0, start)
            || !snapped_bounds(request, request->maximum_radius, end)) {
            return false;
        }
        double interpolated[4];
        for (size_t edge = 0; edge < 4; ++edge) {
            interpolated[edge]
                = (double)start[edge]
                  + ((double)end[edge] - (double)start[edge]) * request->progress;
        }
        double width  = interpolated[2] - interpolated[0];
        double height = interpolated[3] - interpolated[1];
        if (width <= 0.0 || height <= 0.0) {
            circle.center[0] = (float)request->center_x;
            circle.center[1] = (float)request->center_y;
            circle.empty     = true;
            *result          = circle;
            return true;
        }
        double radius = (width < height ? width : height) * 0.5;
        circle.extent[0]         = width;
        circle.extent[1]         = height;
        circle.center[0]         = (float)((interpolated[0] + interpolated[2]) * 0.5);
        circle.center[1]         = (float)((interpolated[1] + interpolated[3]) * 0.5);
        circle.radius            = (float)radius;
        circle.expanded_radius   = (float)(radius + 1.0);
        circle.normalized_extent = (float)((radius + 1.0) / radius);
        /* The scissor must be whole pixels, so it takes the smallest rect that
         * contains the continuous one rather than the rounded one. */
        circle.bounds[0] = (int32_t)floor(interpolated[0]);
        circle.bounds[1] = (int32_t)floor(interpolated[1]);
        circle.bounds[2] = (int32_t)ceil(interpolated[2]);
        circle.bounds[3] = (int32_t)ceil(interpolated[3]);
        int32_t left     = circle.bounds[0] < 0 ? 0 : circle.bounds[0];
        int32_t top      = circle.bounds[1] < 0 ? 0 : circle.bounds[1];
        int32_t right    = circle.bounds[2] > (int32_t)request->target_width
                               ? (int32_t)request->target_width
                               : circle.bounds[2];
        int32_t bottom   = circle.bounds[3] > (int32_t)request->target_height
                               ? (int32_t)request->target_height
                               : circle.bounds[3];
        if (right <= left || bottom <= top) {
            circle.empty = true;
        } else {
            circle.scissor[0] = left;
            circle.scissor[1] = top;
            circle.scissor[2] = right - left;
            circle.scissor[3] = bottom - top;
        }
        *result = circle;
        return true;
    }

    if (!snapped_bounds(request, circle.unsnapped_radius, circle.bounds))
        return false;

    int64_t width    = (int64_t)circle.bounds[2] - circle.bounds[0];
    int64_t height   = (int64_t)circle.bounds[3] - circle.bounds[1];
    circle.extent[0] = (double)width;
    circle.extent[1] = (double)height;
    if (width <= 0 || height <= 0) {
        circle.center[0] = (float)request->center_x;
        circle.center[1] = (float)request->center_y;
        circle.empty     = true;
        *result          = circle;
        return true;
    }

    double snapped_center_x  = ((double)circle.bounds[0] + (double)circle.bounds[2]) * 0.5;
    double snapped_center_y  = ((double)circle.bounds[1] + (double)circle.bounds[3]) * 0.5;
    double radius            = (double)(width < height ? width : height) * 0.5;
    double expanded_radius   = radius + 1.0;
    circle.center[0]         = (float)snapped_center_x;
    circle.center[1]         = (float)snapped_center_y;
    circle.radius            = (float)radius;
    circle.expanded_radius   = (float)expanded_radius;
    circle.normalized_extent = (float)(expanded_radius / radius);

    int32_t clip_left   = circle.bounds[0] < 0 ? 0 : circle.bounds[0];
    int32_t clip_top    = circle.bounds[1] < 0 ? 0 : circle.bounds[1];
    int32_t clip_right  = circle.bounds[2] > (int32_t)request->target_width
                              ? (int32_t)request->target_width
                              : circle.bounds[2];
    int32_t clip_bottom = circle.bounds[3] > (int32_t)request->target_height
                              ? (int32_t)request->target_height
                              : circle.bounds[3];
    if (clip_right <= clip_left || clip_bottom <= clip_top) {
        circle.empty = true;
    } else {
        circle.scissor[0] = clip_left;
        circle.scissor[1] = clip_top;
        circle.scissor[2] = clip_right - clip_left;
        circle.scissor[3] = clip_bottom - clip_top;
    }
    *result = circle;
    return true;
}

static struct walle_lg_reveal_mask_vertex reveal_vertex(double position_x,
                                                        double position_y,
                                                        double first_x,
                                                        double first_y,
                                                        double second_x,
                                                        double second_y)
{
    return (struct walle_lg_reveal_mask_vertex){
        .position           = {(float)position_x, (float)position_y, 0.0f, 1.0f},
        .first_coordinates  = {(float)first_x, (float)first_y},
        .second_coordinates = {(float)second_x, (float)second_y},
        .half_color = {UINT16_C(0x3c00), UINT16_C(0x3c00), UINT16_C(0x3c00), UINT16_C(0x3c00)},
    };
}

static void construct_border_geometry(const struct walle_lg_reveal_mask_request* request,
                                      struct walle_lg_reveal_mask_geometry*      geometry)
{
    const struct walle_lg_reveal_mask_circle* circle   = &geometry->circle;
    double                                    center_x = circle->center[0];
    double                                    center_y = circle->center[1];
    double                                    expanded = circle->expanded_radius;
    float        magnitude = rounded_divide((float)circle->expanded_radius, (float)circle->radius);
    const double positions_x[4] = {
        center_x - expanded,
        center_x,
        center_x,
        center_x + expanded,
    };
    const double positions_y[4] = {
        center_y - expanded,
        center_y,
        center_y,
        center_y + expanded,
    };
    const float sdf[4] = {-magnitude, 0.0f, 0.0f, magnitude};

    for (size_t row = 0; row < 4; ++row) {
        for (size_t column = 0; column < 4; ++column) {
            double x                             = positions_x[column];
            double y                             = positions_y[row];
            geometry->vertices[row * 4 + column] = reveal_vertex(x,
                                                                 y,
                                                                 x - circle->scissor[0] + 1.0,
                                                                 y - circle->scissor[1] + 1.0,
                                                                 sdf[column],
                                                                 sdf[row]);
        }
    }
    geometry->vertex_count = WALLE_LG_REVEAL_MAX_VERTEX_COUNT;
    bool fully_inside      = circle->bounds[0] >= 0 && circle->bounds[1] >= 0
                        && circle->bounds[2] <= (int32_t)request->target_width
                        && circle->bounds[3] <= (int32_t)request->target_height;
    geometry->index_count = fully_inside ? WALLE_LG_REVEAL_MAX_INDEX_COUNT : 48;
    memcpy(geometry->indices,
           border_indices,
           (size_t)geometry->index_count * sizeof(geometry->indices[0]));
    geometry->family = WALLE_LG_REVEAL_MASK_BORDER_GRID;
}

static void append_compact_quadrant(struct walle_lg_reveal_mask_geometry* geometry,
                                    const double                          values[static 24])
{
    uint32_t base = geometry->vertex_count;
    for (size_t vertex = 0; vertex < 4; ++vertex) {
        const double* value = values + vertex * 6;
        geometry->vertices[base + vertex]
            = reveal_vertex(value[0], value[1], value[2], value[3], value[4], value[5]);
    }
    const uint16_t group_indices[6] = {
        (uint16_t)base,
        (uint16_t)(base + 1),
        (uint16_t)(base + 2),
        (uint16_t)(base + 2),
        (uint16_t)(base + 3),
        (uint16_t)base,
    };
    memcpy(geometry->indices + geometry->index_count, group_indices, sizeof(group_indices));
    geometry->vertex_count += 4;
    geometry->index_count += 6;
}

static void construct_compact_geometry(const struct walle_lg_reveal_mask_request* request,
                                       struct walle_lg_reveal_mask_geometry*      geometry)
{
    const struct walle_lg_reveal_mask_circle* circle           = &geometry->circle;
    double                                    left             = circle->bounds[0];
    double                                    top              = circle->bounds[1];
    double                                    right            = circle->bounds[2];
    double                                    bottom           = circle->bounds[3];
    double                                    center_x         = circle->center[0];
    double                                    center_y         = circle->center[1];
    double                                    radius           = circle->radius;
    double                                    low              = 1.0;
    double                                    middle_low       = radius + 1.0;
    double                                    middle_high      = radius + 3.0;
    double                                    high             = 2.0 * radius + 3.0;
    const double                              quadrants[4][24] = {
        {left, top,        -1.0,       -1.0,     low,  low,      center_x, top,
                                      0.0,  -1.0,       middle_low, low,      left, center_y, -1.0,     0.0,
                                      low,  middle_low, left,       center_y, -1.0, 0.0,      low,      middle_low},
        {center_x, top,        0.0,      -1.0, middle_high, low,      right,       top,
                                      -1.0,     -1.0,       high,     low,  right,       center_y, -1.0,        0.0,
                                      high,     middle_low, center_x, top,  0.0,         -1.0,     middle_high, low},
        {left,       center_y, -1.0,       0.0,    low,      middle_high, center_x, bottom,
                                      0.0,        -1.0,     middle_low, high,   center_x, bottom,      0.0,      -1.0,
                                      middle_low, high,     left,       bottom, -1.0,     -1.0,        low,      high},
        {right, center_y, -1.0,     0.0,         high,  middle_high, right,       center_y,
                                      -1.0,  0.0,      high,     middle_high, right, bottom,      -1.0,        -1.0,
                                      high,  high,     center_x, bottom,      0.0,   -1.0,        middle_high, high},
    };
    const double corners[4][2] = {
        {0.0, 0.0},
        {(double)request->target_width, 0.0},
        {0.0, (double)request->target_height},
        {(double)request->target_width, (double)request->target_height},
    };
    double radius_squared = radius * radius;
    for (size_t quadrant = 0; quadrant < 4; ++quadrant) {
        double delta_x = corners[quadrant][0] - center_x;
        double delta_y = corners[quadrant][1] - center_y;
        if (delta_x * delta_x + delta_y * delta_y >= radius_squared)
            append_compact_quadrant(geometry, quadrants[quadrant]);
    }
    geometry->family          = WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS;
    geometry->clear_to_inside = true;
}

bool walle_lg_reveal_mask_geometry_construct(const struct walle_lg_reveal_mask_request* request,
                                             struct walle_lg_reveal_mask_geometry*      result)
{
    if (result == nullptr)
        return false;
    struct walle_lg_reveal_mask_geometry geometry = {};
    if (!walle_lg_reveal_mask_circle_construct(request, &geometry.circle))
        return false;
    if (geometry.circle.empty) {
        geometry.family = WALLE_LG_REVEAL_MASK_EMPTY;
        *result         = geometry;
        return true;
    }

    if (geometry.circle.extent[0] == geometry.circle.extent[1])
        construct_compact_geometry(request, &geometry);
    else
        construct_border_geometry(request, &geometry);
    *result = geometry;
    return true;
}

bool walle_lg_reveal_mask_apple_fast_sqrt(const uint8_t* table,
                                          size_t         table_byte_count,
                                          float          value,
                                          float*         result)
{
    if (table == nullptr || table_byte_count != WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT
        || result == nullptr || !isfinite(value) || value < 0.0f || fegetround() != FE_TONEAREST) {
        return false;
    }

    float    ieee_root       = sqrtf(value);
    uint32_t value_bits      = float_bits(value);
    uint32_t root_bits       = float_bits(ieee_root);
    uint8_t  code            = table[value_bits & UINT32_C(0x007fffff)];
    uint32_t exponent_parity = (value_bits >> 23u) & 1u;
    uint32_t delta           = exponent_parity == 0 ? code & 3u : (code >> 2u) & 3u;
    if (delta == 0) {
        if (root_bits == 0)
            return false;
        --root_bits;
    } else {
        root_bits += delta - 1u;
    }
    *result = bits_float(root_bits);
    return isfinite(*result) && *result >= 0.0f;
}

bool walle_lg_reveal_mask_sample_r8(const uint8_t* fast_sqrt_table,
                                    size_t         fast_sqrt_table_byte_count,
                                    const struct walle_lg_reveal_mask_sample*  sample,
                                    struct walle_lg_reveal_mask_sample_result* result)
{
    if (sample == nullptr || result == nullptr || !binary16_supported() || !isfinite(sample->x)
        || !isfinite(sample->y) || !isfinite(sample->horizontal_partner_x)
        || !isfinite(sample->vertical_partner_y) || fegetround() != FE_TONEAREST) {
        return false;
    }

    struct walle_lg_reveal_mask_sample_result evaluated = {};
    if (!circle_distance(
            fast_sqrt_table, fast_sqrt_table_byte_count, sample->x, sample->y, &evaluated.distance)
        || !circle_distance(fast_sqrt_table,
                            fast_sqrt_table_byte_count,
                            sample->horizontal_partner_x,
                            sample->y,
                            &evaluated.horizontal_distance)
        || !circle_distance(fast_sqrt_table,
                            fast_sqrt_table_byte_count,
                            sample->x,
                            sample->vertical_partner_y,
                            &evaluated.vertical_distance)) {
        return false;
    }

    float horizontal_delta
        = fabsf(rounded_subtract(evaluated.horizontal_distance, evaluated.distance));
    float vertical_delta = fabsf(rounded_subtract(evaluated.vertical_distance, evaluated.distance));
    evaluated.feather    = rounded_add(horizontal_delta, vertical_delta);
    if (evaluated.feather < 1.0e-4f)
        evaluated.feather = 1.0e-4f;

    float numerator = rounded_subtract(1.0f, evaluated.distance);
    float alpha     = rounded_add(rounded_divide(numerator, evaluated.feather), 0.5f);
    if (alpha < 0.0f)
        alpha = 0.0f;
    else if (alpha > 1.0f)
        alpha = 1.0f;
    evaluated.alpha_half_bits = float_to_half_bits(alpha);
    evaluated.coverage        = half_to_unorm8(evaluated.alpha_half_bits);
    *result                   = evaluated;
    return true;
}
