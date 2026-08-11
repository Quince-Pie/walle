#include "liquid_glass_transition_frame.h"

#include <float.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "liquid_glass_materialize.h"

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53);
static_assert(sizeof(struct walle_lg_vertex) == 8 * sizeof(float));
static_assert(sizeof(struct walle_lg_producer_vertex) == 8 * sizeof(float));

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#    error "The Liquid Glass render payload is little-endian"
#endif

constexpr double HIGHLIGHT_EXPANSION = 9.0;

static const uint16_t shadow_indices[WALLE_LG_SHADOW_INDEX_COUNT] = {
    0, 1, 5, 5, 4, 0, 3, 7, 6, 6, 2, 3, 10, 11, 15, 15, 14, 10, 9, 13, 12, 12, 8,  9,
    1, 2, 6, 6, 5, 1, 4, 5, 9, 9, 8, 4, 6,  7,  11, 11, 10, 6,  9, 10, 14, 14, 13, 9,
};

static const uint16_t highlight_quad_indices[6] = {0, 1, 2, 2, 3, 0};

static const uint16_t highlight_vibrant_words[2][24] = {
    [WALLE_LG_APPEARANCE_LIGHT] = {
        0x3ccf, 0xb4c3, 0xb4c3, 0x0000, 0xbc01, 0x37fb, 0xbc01, 0x0000,
        0xae77, 0xae78, 0x3d98, 0x0000, 0x0000, 0x0000, 0x0000, 0x3c00,
        0x3b33, 0x3b33, 0x3b33, 0x0000, 0x3c00, 0x0000, 0x0000, 0x0000,
    },
    [WALLE_LG_APPEARANCE_DARK] = {
        0x414c, 0xb59c, 0xb59d, 0x0000, 0xbcb9, 0x3f48, 0xbcb8, 0x0000,
        0xaf9c, 0xafa1, 0x41c3, 0x0000, 0x0000, 0x0000, 0x0000, 0x3c00,
        0x30cd, 0x30cd, 0x30cd, 0x0000, 0x3c00, 0x0000, 0x0000, 0x0000,
    },
};

static const uint16_t highlight_key_fill_words[20] = {
    0x3c00, 0xbb84, 0x0000, 0xb9a8, 0xb9a8, 0x3c00, 0xbb84, 0x0000, 0x39a8, 0x39a8,
    0x399a, 0x0000, 0x3c00, 0x3c00, 0x3c00, 0x3c00, 0x3c00, 0x3c00, 0x3c00, 0x3c00,
};

static float f32(double value)
{
    volatile float rounded = (float)value;
    return rounded;
}

static double add64(double left, double right)
{
    volatile double result = left + right;
    return result;
}

static double subtract64(double left, double right)
{
    volatile double result = left - right;
    return result;
}

static double multiply64(double left, double right)
{
    volatile double result = left * right;
    return result;
}

static int64_t maximum_i64(int64_t left, int64_t right)
{
    return left > right ? left : right;
}

static int64_t minimum_i64(int64_t left, int64_t right)
{
    return left < right ? left : right;
}

static double centered_affine(double value, double scale, double half)
{
    double          negative_half_scale = multiply64(-half, scale);
    double          translation         = add64(half, negative_half_scale);
    volatile double fused               = fma(scale, value, 0.0);
    return add64(fused, translation);
}

static bool align_extent(uint32_t extent, uint32_t* result)
{
    if (extent == 0 || extent > UINT32_MAX - 63u)
        return false;
    *result = 64u * ((extent + 63u) / 64u);
    return true;
}

static bool construct_layer(const struct walle_lg_transition_frame_request* request,
                            struct walle_lg_dynamic_layer_state*            result)
{
    double width     = request->diameter;
    float  remaining = request->visible_fraction;

    double carrier_extent = multiply64(width, remaining);
    double carrier_half   = multiply64(carrier_extent, 0.5);
    double carrier_x      = subtract64(request->center_x, carrier_half);
    double carrier_y      = subtract64(request->center_y, carrier_half);

    float  progress     = f32(subtract64(1.0, remaining));
    double scale_limit  = fmin((width + 16.0) / width, 1.2);
    double transition   = add64(1.0, multiply64(progress, subtract64(scale_limit, 1.0)));
    double half         = width / 2.0;
    double lower        = centered_affine(0.0, transition, half);
    double upper        = centered_affine(width, transition, half);
    double square_root  = sqrt(scale_limit);
    double inverse_root = 1.0 / square_root;
    lower               = centered_affine(lower, inverse_root, half);
    upper               = centered_affine(upper, inverse_root, half);
    lower               = centered_affine(lower, square_root, half);
    upper               = centered_affine(upper, square_root, half);

    double carrier_floor = floor(carrier_extent);
    if (carrier_extent - carrier_floor == 0.5)
        return false;
    double snapped_extent = floor(carrier_extent + 0.5);
    double snap_delta     = subtract64(snapped_extent, carrier_extent);
    double root_x         = add64(subtract64(request->center_x, half), snap_delta / 2.0);
    double root_y         = add64(subtract64(request->center_y, half), snap_delta / 2.0);
    double root_lower_x   = add64(lower, root_x);
    double root_upper_x   = add64(upper, root_x);
    double element_extent = subtract64(root_upper_x, root_lower_x);
    double element_x      = subtract64(root_lower_x, carrier_x);
    double element_y      = subtract64(add64(lower, root_y), carrier_y);

    *result = (struct walle_lg_dynamic_layer_state){
        .carrier_bounds   = {0.0, 0.0, carrier_extent, carrier_extent},
        .carrier_position = {carrier_x, carrier_y},
        .element_bounds   = {0.0, 0.0, element_extent, element_extent},
        .element_position = {element_x, element_y},
    };
    return true;
}

static bool construct_producer(const struct walle_lg_transition_frame_request* request,
                               const struct walle_lg_dynamic_layer_state*      layer,
                               float                                           backdrop_scale,
                               struct walle_lg_producer_crop*                  result)
{
    float  margin   = request->material == WALLE_LG_MATERIAL_REGULAR
                          ? f32(0.35 * (double)request->diameter)
                          : 0.0f;
    double lower[2] = {
        fmax(0.0, layer->carrier_position[0] - margin),
        fmax(0.0,
             (double)request->window_height - (layer->carrier_position[1] + request->diameter)
                 - margin),
    };
    double upper[2] = {
        fmin(request->window_width, layer->carrier_position[0] + request->diameter + margin),
        fmin(request->window_height,
             (double)request->window_height - layer->carrier_position[1] + margin),
    };

    struct walle_lg_producer_crop producer = {.allocation_margin = margin};
    for (size_t axis = 0; axis < 2; ++axis) {
        if (!(lower[axis] >= 0.0 && lower[axis] < upper[axis]))
            return false;
        double scaled_lower = multiply64(backdrop_scale, lower[axis]);
        double scaled_upper = multiply64(backdrop_scale, upper[axis]);
        double origin_value = lower[axis] == 0.0 ? 0.0
                              : axis == 0        ? floor(scaled_lower) + 1.0
                                                 : ceil(scaled_lower);
        double extent_value = floor(scaled_upper) - origin_value;
        if (origin_value < INT32_MIN || origin_value > INT32_MAX || extent_value <= 0.0
            || extent_value > UINT32_MAX) {
            return false;
        }
        producer.origin[axis]        = (int32_t)origin_value;
        producer.active_extent[axis] = (uint32_t)extent_value;
        if (!align_extent(producer.active_extent[axis], &producer.storage_extent[axis]))
            return false;
    }
    *result = producer;
    return true;
}

static float source_coordinate(float    position,
                               float    backdrop_scale,
                               int32_t  crop_origin,
                               int32_t  copy_offset,
                               uint32_t allocation_extent)
{
    double staged64  = multiply64(position, backdrop_scale);
    staged64         = subtract64(staged64, crop_origin);
    staged64         = subtract64(staged64, copy_offset);
    float staged     = f32(staged64);
    float reciprocal = f32(1.0 / allocation_extent);
    return f32(multiply64(staged, reciprocal));
}

static struct walle_lg_vertex vertex(float position_x, float position_y, float sdf_x, float sdf_y)
{
    return (struct walle_lg_vertex){
        .position = {position_x, position_y, 0.0f, 1.0f},
        .sdf      = {sdf_x, sdf_y},
    };
}

static void add_source_coordinates(struct walle_lg_vertex*                vertices,
                                   size_t                                 count,
                                   float                                  backdrop_scale,
                                   const struct walle_lg_producer_crop*   producer,
                                   const struct walle_lg_selected_region* selected)
{
    for (size_t index = 0; index < count; ++index) {
        vertices[index].source[0] = source_coordinate(vertices[index].position[0],
                                                      backdrop_scale,
                                                      producer->origin[0],
                                                      selected->copy_offset[0],
                                                      selected->allocated_extent[0]);
        vertices[index].source[1] = source_coordinate(vertices[index].position[1],
                                                      backdrop_scale,
                                                      producer->origin[1],
                                                      selected->copy_offset[1],
                                                      selected->allocated_extent[1]);
    }
}

static void construct_background_vertices(const struct walle_lg_transition_frame_request* request,
                                          struct walle_lg_transition_frame*               frame)
{
    const struct walle_lg_dynamic_layer_state* layer  = &frame->layer;
    double                                     extent = layer->element_bounds[2];
    double horizontal = add64(layer->carrier_position[0], layer->element_position[0]);
    double vertical   = subtract64(subtract64(request->window_height, layer->carrier_position[1]),
                                 layer->element_position[1]);
    float  left       = f32(horizontal);
    float  right      = f32(add64(horizontal, extent));
    float  top        = f32(vertical);
    float  bottom     = f32(subtract64(vertical, extent));
    float  local_low  = f32(-extent / 2.0);
    float  local_high = f32(extent / 2.0);
    struct walle_lg_vertex main[WALLE_LG_MAIN_VERTEX_COUNT] = {
        vertex(left, top, local_low, local_low),
        vertex(right, top, local_high, local_low),
        vertex(right, bottom, local_high, local_high),
        vertex(right, bottom, local_high, local_high),
        vertex(left, bottom, local_low, local_high),
        vertex(left, top, local_low, local_low),
    };
    memcpy(frame->main_vertices, main, sizeof main);

    float margin          = request->material == WALLE_LG_MATERIAL_REGULAR
                                ? f32(48.0 * request->visible_fraction)
                                : 0.0f;
    float top_margin      = fmaxf(margin - 8.0f, 0.0f);
    float extended_width  = f32(extent + margin);
    float extended_height = f32((extent + margin) + 8.0);
    float position_x[4]   = {
        f32(horizontal - margin),
        f32(horizontal),
        f32(horizontal + extent),
        f32(horizontal + extended_width),
    };
    float position_y[4] = {
        f32(vertical + top_margin),
        f32(vertical),
        f32(vertical - extent),
        f32(vertical - extended_height),
    };
    float coordinate_x[4] = {
        f32((double)local_low + (double)f32(-margin)),
        local_low,
        local_high,
        f32((double)local_high + ((double)extended_width - extent)),
    };
    float coordinate_y[4] = {
        f32((double)local_low + (double)f32(-top_margin)),
        local_low,
        local_high,
        f32((double)local_high + ((double)extended_height - extent)),
    };
    size_t index = 0;
    for (size_t y = 0; y < 4; ++y) {
        for (size_t x = 0; x < 4; ++x) {
            frame->shadow_vertices[index++]
                = vertex(position_x[x], position_y[y], coordinate_x[x], coordinate_y[y]);
        }
    }
    add_source_coordinates(frame->main_vertices,
                           WALLE_LG_MAIN_VERTEX_COUNT,
                           frame->backdrop_scale,
                           &frame->producer,
                           &frame->selected_region);
    add_source_coordinates(frame->shadow_vertices,
                           WALLE_LG_SHADOW_VERTEX_COUNT,
                           frame->backdrop_scale,
                           &frame->producer,
                           &frame->selected_region);
    memcpy(frame->shadow_indices, shadow_indices, sizeof shadow_indices);
}

static void store_float(uint8_t* destination, size_t offset, float value)
{
    memcpy(destination + offset, &value, sizeof value);
}

static void construct_highlight_uniform(const struct walle_lg_transition_frame_request* request,
                                        struct walle_lg_transition_frame*               frame,
                                        float                                           half_extent,
                                        float                                           radius)
{
    memset(frame->highlight_uniform, 0, sizeof frame->highlight_uniform);
    store_float(frame->highlight_uniform, 0x00, radius);
    store_float(frame->highlight_uniform, 0x04, radius);
    store_float(frame->highlight_uniform, 0x08, 4.0f);
    store_float(frame->highlight_uniform,
                0x0c,
                request->material == WALLE_LG_MATERIAL_REGULAR ? 0.5f : 0.0f);
    store_float(frame->highlight_uniform, 0x10, 1.0f);
    store_float(frame->highlight_uniform, 0x1c, 1.0f);
    store_float(frame->highlight_uniform, 0x20, 1.0f);
    store_float(frame->highlight_uniform, 0x24, 1.0f);
    store_float(frame->highlight_uniform, 0x28, half_extent);
    memcpy(frame->highlight_uniform + 0x60,
           highlight_vibrant_words[request->appearance],
           sizeof highlight_vibrant_words[0]);
    memcpy(frame->highlight_uniform + 0x90, frame->profile.byte + 144, 64);
    memcpy(
        frame->highlight_uniform + 0xd0, highlight_key_fill_words, sizeof highlight_key_fill_words);
}

static void construct_highlight_vertices(const struct walle_lg_transition_frame_request* request,
                                         struct walle_lg_transition_frame*               frame)
{
    double extent      = frame->layer.element_bounds[2];
    float  half_extent = f32(extent / 2.0);
    float  staged      = f32((double)half_extent + HIGHLIGHT_EXPANSION);
    float  radius      = f32((double)staged - HIGHLIGHT_EXPANSION);
    float  outer       = f32((double)radius + HIGHLIGHT_EXPANSION);
    double horizontal  = frame->layer.carrier_position[0] + frame->layer.element_position[0];
    double vertical    = (double)request->window_height - frame->layer.carrier_position[1]
                      - frame->layer.element_position[1];
    float left   = f32(horizontal - HIGHLIGHT_EXPANSION);
    float right  = f32((horizontal + extent) + HIGHLIGHT_EXPANSION);
    float top    = f32(vertical + HIGHLIGHT_EXPANSION);
    float bottom = f32((vertical - extent) - HIGHLIGHT_EXPANSION);

    if (radius > half_extent) {
        float positions_x[4] = {left, f32((double)left + outer), f32((double)right - outer), right};
        float positions_y[4] = {top, f32((double)top - outer), f32((double)bottom + outer), bottom};
        float sdf[4]         = {-outer, 0.0f, 0.0f, outer};
        size_t index         = 0;
        for (size_t y = 0; y < 4; ++y) {
            for (size_t x = 0; x < 4; ++x) {
                frame->highlight_vertices[index]
                    = vertex(positions_x[x], positions_y[y], sdf[x], sdf[y]);
                frame->highlight_vertices[index].source[0]
                    = frame->shadow_vertices[index].source[0];
                frame->highlight_vertices[index].source[1]
                    = frame->shadow_vertices[index].source[1];
                ++index;
            }
        }
        memcpy(frame->highlight_indices, shadow_indices, 24 * sizeof(uint16_t));
        frame->highlight_vertex_count = 16;
        frame->highlight_index_count  = 24;
    } else {
        frame->highlight_vertices[0] = vertex(left, bottom, -outer, outer);
        frame->highlight_vertices[1] = vertex(right, bottom, outer, outer);
        frame->highlight_vertices[2] = vertex(right, top, outer, -outer);
        frame->highlight_vertices[3] = vertex(left, top, -outer, -outer);
        for (size_t index = 0; index < 4; ++index) {
            frame->highlight_vertices[index].source[0] = frame->shadow_vertices[index].source[0];
            frame->highlight_vertices[index].source[1] = frame->shadow_vertices[index].source[1];
        }
        memcpy(frame->highlight_indices, highlight_quad_indices, sizeof highlight_quad_indices);
        frame->highlight_vertex_count = 4;
        frame->highlight_index_count  = 6;
    }

    if (request->visible_fraction == 1.0f) {
        constexpr float special[4][2]
            = {{-1.5f, -1.5f}, {0.0f, -1.5f}, {0.0f, -1.5f}, {1.5f, -1.5f}};
        for (size_t index = 0; index < 4; ++index) {
            frame->highlight_vertices[index].source[0] = special[index][0];
            frame->highlight_vertices[index].source[1] = special[index][1];
        }
    }
    construct_highlight_uniform(request, frame, half_extent, radius);
}

static bool construct_scissor(const struct walle_lg_transition_frame_request* request,
                              const struct walle_lg_numeric_inputs*           inputs,
                              const struct walle_lg_dynamic_layer_state*      layer,
                              int32_t                                         result[static 4])
{
    double transform_x = layer->carrier_position[0] + layer->element_position[0];
    double transform_y
        = (double)request->window_height - layer->carrier_position[1] - layer->element_position[1];
    double extent         = layer->element_bounds[2];
    double element_bottom = transform_y - extent;
    double radius         = request->sdf_enclosure_radius;
    double base_low_x     = floor(transform_x - radius);
    double base_low_y     = floor(element_bottom - radius);
    double base_high_x    = ceil((transform_x + extent) + radius);
    double base_high_y    = ceil(transform_y + radius);
    double base_width     = base_high_x - base_low_x;
    double base_height    = base_high_y - base_low_y;
    double local_x        = base_low_x - transform_x;
    double local_y        = -((base_low_y - transform_y) + base_height);

    double  blur       = inputs->value[WALLE_LG_INPUT_BLUR_RADIUS];
    double  bleed_blur = inputs->value[WALLE_LG_INPUT_BLEED_BLUR_RADIUS];
    double  roi_radius = 1.4 * fmax(2.0 * blur, bleed_blur);
    double  roi_x      = local_x - roi_radius;
    double  roi_y      = local_y - roi_radius;
    double  roi_width  = base_width + 2.0 * roi_radius;
    double  roi_height = base_height + 2.0 * roi_radius;
    double  world_x    = roi_x + transform_x;
    double  world_y    = -(roi_y + roi_height) + transform_y;
    int64_t roi_low_x  = (int64_t)floor(world_x);
    int64_t roi_low_y  = (int64_t)floor(world_y);
    int64_t roi_high_x = (int64_t)ceil((roi_x + roi_width) + transform_x);
    int64_t roi_high_y = (int64_t)ceil(world_y + roi_height);

    float terminal_bleed
        = request->material == WALLE_LG_MATERIAL_REGULAR ? f32(0.35 * request->diameter) : 0.0f;
    double dod_extent_value = request->diameter + 2.0 * terminal_bleed;
    if (dod_extent_value != floor(dod_extent_value))
        return false;
    int64_t dod_extent = (int64_t)dod_extent_value;
    int64_t dod_low_x  = (int64_t)floor((layer->carrier_position[0] - terminal_bleed) + 0.5);
    int64_t dod_low_y  = (int64_t)floor((layer->carrier_position[1] - terminal_bleed) + 0.5);
    int64_t low_x      = maximum_i64(dod_low_x, maximum_i64(roi_low_x, INT64_C(0)));
    int64_t low_y      = maximum_i64(dod_low_y, maximum_i64(roi_low_y, INT64_C(0)));
    int64_t high_x     = minimum_i64(dod_low_x + dod_extent,
                                 minimum_i64(roi_high_x, (int64_t)request->window_width));
    int64_t high_y     = minimum_i64(dod_low_y + dod_extent,
                                 minimum_i64(roi_high_y, (int64_t)request->window_height));
    if (low_x < INT32_MIN || low_y < INT32_MIN || high_x > INT32_MAX || high_y > INT32_MAX
        || high_x <= low_x || high_y <= low_y) {
        return false;
    }
    result[0] = (int32_t)low_x;
    result[1] = (int32_t)low_y;
    result[2] = (int32_t)(high_x - low_x);
    result[3] = (int32_t)(high_y - low_y);
    return true;
}

static bool finite_enclosure(const double rectangle[static 4], int32_t result[static 4])
{
    constexpr double lower_bound = -536'870'911.0;
    constexpr double upper_bound = 536'870'912.0;
    double           origin_x    = fmax(rectangle[0], lower_bound);
    double           origin_y    = fmax(rectangle[1], lower_bound);
    double           width       = fmin(rectangle[2], upper_bound - origin_x);
    double           height      = fmin(rectangle[3], upper_bound - origin_y);
    if (!isfinite(origin_x) || !isfinite(origin_y) || !isfinite(width) || !isfinite(height)
        || width < 0.0 || height < 0.0) {
        return false;
    }

    double lower_x         = floor(origin_x);
    double lower_y         = floor(origin_y);
    double enclosed_width  = ceil(origin_x + width) - lower_x;
    double enclosed_height = ceil(origin_y + height) - lower_y;
    bool   fractional      = lower_x != origin_x || lower_y != origin_y || enclosed_width != width
                      || enclosed_height != height;
    if (fractional) {
        lower_x -= 1.0;
        lower_y -= 1.0;
        enclosed_width += 2.0;
        enclosed_height += 2.0;
    }
    if (lower_x < INT32_MIN || lower_x > INT32_MAX || lower_y < INT32_MIN || lower_y > INT32_MAX
        || enclosed_width < 0.0 || enclosed_width > INT32_MAX || enclosed_height < 0.0
        || enclosed_height > INT32_MAX) {
        return false;
    }
    result[0] = (int32_t)lower_x;
    result[1] = (int32_t)lower_y;
    result[2] = (int32_t)enclosed_width;
    result[3] = (int32_t)enclosed_height;
    return true;
}

static bool
intersect_f64(const double left[static 4], const double right[static 4], double result[static 4])
{
    double lower_x = fmax(left[0], right[0]);
    double lower_y = fmax(left[1], right[1]);
    double far_x   = fmin(left[0] + left[2], right[0] + right[2]);
    double far_y   = fmin(left[1] + left[3], right[1] + right[3]);
    if (!(far_x > lower_x && far_y > lower_y))
        return false;
    result[0] = lower_x;
    result[1] = lower_y;
    result[2] = far_x - lower_x;
    result[3] = far_y - lower_y;
    return true;
}

static void
union_f64_i32(const double left[static 4], const int32_t right[static 4], double result[static 4])
{
    double lower_x = fmin(left[0], right[0]);
    double lower_y = fmin(left[1], right[1]);
    double far_x   = fmax(left[0] + left[2], (double)right[0] + right[2]);
    double far_y   = fmax(left[1] + left[3], (double)right[1] + right[3]);
    result[0]      = lower_x;
    result[1]      = lower_y;
    result[2]      = far_x - lower_x;
    result[3]      = far_y - lower_y;
}

static bool intersect_viewport(const int32_t rectangle[static 4],
                               uint32_t      width,
                               uint32_t      height,
                               int32_t       result[static 4])
{
    int64_t lower_x = maximum_i64(rectangle[0], 0);
    int64_t lower_y = maximum_i64(rectangle[1], 0);
    int64_t far_x   = minimum_i64((int64_t)rectangle[0] + rectangle[2], width);
    int64_t far_y   = minimum_i64((int64_t)rectangle[1] + rectangle[3], height);
    if (far_x <= lower_x || far_y <= lower_y || lower_x > INT32_MAX || lower_y > INT32_MAX
        || far_x - lower_x > INT32_MAX || far_y - lower_y > INT32_MAX) {
        return false;
    }
    result[0] = (int32_t)lower_x;
    result[1] = (int32_t)lower_y;
    result[2] = (int32_t)(far_x - lower_x);
    result[3] = (int32_t)(far_y - lower_y);
    return true;
}

static bool append_producer_quad(struct walle_lg_dynamic_producer_mesh* mesh,
                                 const float                            position[static 4],
                                 const float                            source[static 4])
{
    if (mesh->vertex_count > WALLE_LG_PRODUCER_MAX_VERTEX_COUNT - 4
        || mesh->index_count > WALLE_LG_PRODUCER_MAX_INDEX_COUNT - 6) {
        return false;
    }
    uint32_t    base           = mesh->vertex_count;
    const float vertices[4][4] = {
        {position[0], position[1], source[0], source[1]},
        {position[2], position[1], source[2], source[1]},
        {position[2], position[3], source[2], source[3]},
        {position[0], position[3], source[0], source[3]},
    };
    for (size_t index = 0; index < 4; ++index) {
        mesh->vertices[base + index] = (struct walle_lg_producer_vertex){
            .position = {vertices[index][0], vertices[index][1], 0.0f, 1.0f},
            .source   = {vertices[index][2], vertices[index][3]},
        };
    }
    constexpr uint16_t quad_indices[6] = {0, 1, 2, 2, 3, 0};
    for (size_t index = 0; index < 6; ++index)
        mesh->indices[mesh->index_count + index] = (uint16_t)(base + quad_indices[index]);
    mesh->vertex_count += 4;
    mesh->index_count += 6;
    return true;
}

static bool append_clipped_producer_quads(struct walle_lg_dynamic_producer_mesh* mesh,
                                          bool                                   x_lower,
                                          bool                                   x_upper,
                                          bool                                   y_lower,
                                          bool                                   y_upper)
{
    if (!x_lower && !x_upper && !y_lower && !y_upper)
        return true;
    const struct walle_lg_producer_vertex* primary = mesh->vertices;
    float                                  x0      = primary[0].position[0];
    float                                  y0      = primary[0].position[1];
    float                                  x1      = primary[2].position[0];
    float                                  y1      = primary[2].position[1];
    float                                  u0      = primary[0].source[0];
    float                                  v0      = primary[0].source[1];
    float                                  u1      = primary[2].source[0];
    float                                  v1      = primary[2].source[1];

#define APPEND(px0, py0, px1, py1, su0, sv0, su1, sv1)                                             \
    do {                                                                                           \
        const float position[] = {(px0), (py0), (px1), (py1)};                                     \
        const float source[]   = {(su0), (sv0), (su1), (sv1)};                                     \
        if (!append_producer_quad(mesh, position, source))                                         \
            return false;                                                                          \
    } while (false)

    if (!x_lower && x_upper && y_lower && !y_upper) {
        APPEND(x0, y0 - 1.0f, x1, y0, u0, v0 + 0.5f, u1, v0 + 0.5f);
        APPEND(x1, y0 - 1.0f, x1 + 1.0f, y0, u1 - 0.5f, v0 + 0.5f, u1 - 0.5f, v0 + 0.5f);
        APPEND(x1, y0, x1 + 1.0f, y1, u1 - 0.5f, v0, u1 - 0.5f, v1);
        return true;
    }
    if (x_lower && x_upper && y_lower && y_upper) {
        APPEND(x0 - 1.0f, y0, x0, y1, u0 + 0.5f, v0, u0 + 0.5f, v1);
        APPEND(x0 - 1.0f, y0 - 1.0f, x0, y0, u0 + 0.5f, v0 + 0.5f, u0 + 0.5f, v0 + 0.5f);
        APPEND(x0, y0 - 1.0f, x1, y0, u0, v0 + 0.5f, u1, v0 + 0.5f);
        APPEND(x1, y0 - 1.0f, x1 + 1.0f, y0, u1 - 0.5f, v0 + 0.5f, u1 - 0.5f, v0 + 0.5f);
        APPEND(x1, y0, x1 + 1.0f, y1, u1 - 0.5f, v0, u1 - 0.5f, v1);
        APPEND(x1, y1, x1 + 1.0f, y1 + 1.0f, u1 - 0.5f, v1 - 0.5f, u1 - 0.5f, v1 - 0.5f);
        APPEND(x0, y1, x1, y1 + 1.0f, u0, v1 - 0.5f, u1, v1 - 0.5f);
        APPEND(x0 - 1.0f, y1, x0, y1 + 1.0f, u0 + 0.5f, v1 - 0.5f, u0 + 0.5f, v1 - 0.5f);
        return true;
    }
#undef APPEND
    return false;
}

static bool construct_producer_mesh(const struct walle_lg_transition_frame_request* request,
                                    const struct walle_lg_numeric_inputs*           inputs,
                                    const struct walle_lg_dynamic_layer_state*      layer,
                                    float                                           backdrop_scale,
                                    const struct walle_lg_producer_crop*            producer,
                                    struct walle_lg_dynamic_producer_mesh*          result)
{
    if (request->material != WALLE_LG_MATERIAL_REGULAR) {
        *result = (struct walle_lg_dynamic_producer_mesh){};
        return true;
    }

    double transform_x = layer->carrier_position[0] + layer->element_position[0];
    double transform_y
        = (double)request->window_height - layer->carrier_position[1] - layer->element_position[1];
    double extent         = layer->element_bounds[2];
    double element_bottom = transform_y - extent;
    double radius         = request->sdf_enclosure_radius;
    double base_low_x     = floor(transform_x - radius);
    double base_low_y     = floor(element_bottom - radius);
    double base_high_x    = ceil((transform_x + extent) + radius);
    double base_high_y    = ceil(transform_y + radius);
    double base_width     = base_high_x - base_low_x;
    double base_height    = base_high_y - base_low_y;
    double local_x        = base_low_x - transform_x;
    double local_y        = -((base_low_y - transform_y) + base_height);
    double blur           = inputs->value[WALLE_LG_INPUT_BLUR_RADIUS];
    double bleed_blur     = inputs->value[WALLE_LG_INPUT_BLEED_BLUR_RADIUS];
    double roi_radius     = 1.4 * fmax(2.0 * blur, bleed_blur);
    double roi_x          = local_x - roi_radius;
    double roi_y          = local_y - roi_radius;
    double roi_width      = base_width + 2.0 * roi_radius;
    double roi_height     = base_height + 2.0 * roi_radius;
    double roi[4]         = {
        roi_x + transform_x,
        -(roi_y + roi_height) + transform_y,
        roi_width,
        roi_height,
    };

    float  margin = f32(0.35 * request->diameter);
    double dod[4] = {
        subtract64(layer->carrier_position[0], margin),
        subtract64(subtract64((double)request->window_height,
                              add64(layer->carrier_position[1], request->diameter)),
                   margin),
        add64(request->diameter, multiply64(2.0, margin)),
        add64(request->diameter, multiply64(2.0, margin)),
    };
    double                                nested[4];
    int32_t                               nested_crop[4];
    double                                aggregate[4];
    struct walle_lg_dynamic_producer_mesh mesh = {
        .kind = request->visible_fraction >= f32(2.0 / 3.0) ? WALLE_LG_PRODUCER_DOWNSAMPLE_4
                                                            : WALLE_LG_PRODUCER_DIRECT,
    };
    if (!intersect_f64(dod, roi, nested) || !finite_enclosure(nested, nested_crop))
        return false;
    union_f64_i32(dod, nested_crop, aggregate);
    if (!finite_enclosure(aggregate, mesh.working_crop)
        || !intersect_viewport(
            mesh.working_crop, request->window_width, request->window_height, mesh.visible_crop)) {
        return false;
    }

    float x0 = f32((double)backdrop_scale * f32(mesh.visible_crop[0]));
    float y0 = f32((double)backdrop_scale * f32(mesh.visible_crop[1]));
    float x1
        = f32((double)backdrop_scale * f32((int64_t)mesh.visible_crop[0] + mesh.visible_crop[2]));
    float y1
        = f32((double)backdrop_scale * f32((int64_t)mesh.visible_crop[1] + mesh.visible_crop[3]));
    float position[4] = {f32(floor(x0)), f32(floor(y0)), f32(ceil(x1)), f32(ceil(y1))};
    float source[4]   = {
        f32((double)position[0] / backdrop_scale),
        f32((double)position[1] / backdrop_scale),
        f32((double)position[2] / backdrop_scale),
        f32((double)position[3] / backdrop_scale),
    };
    if (!append_producer_quad(&mesh, position, source))
        return false;

    bool x_lower = mesh.visible_crop[0] != mesh.working_crop[0];
    bool y_lower = mesh.visible_crop[1] != mesh.working_crop[1];
    bool x_upper = (int64_t)mesh.visible_crop[0] + mesh.visible_crop[2]
                   != (int64_t)mesh.working_crop[0] + mesh.working_crop[2];
    bool y_upper = (int64_t)mesh.visible_crop[1] + mesh.visible_crop[3]
                   != (int64_t)mesh.working_crop[1] + mesh.working_crop[3];
    if (mesh.kind == WALLE_LG_PRODUCER_DIRECT) {
        if (!append_clipped_producer_quads(&mesh, x_lower, x_upper, y_lower, y_upper))
            return false;
    } else if (x_lower || x_upper || y_lower || y_upper) {
        return false;
    }

    for (size_t axis = 0; axis < 2; ++axis) {
        uint64_t active         = producer->active_extent[axis];
        uint64_t scissor_extent = active + 17u;
        if (scissor_extent > producer->storage_extent[axis])
            scissor_extent = producer->storage_extent[axis];
        mesh.scissor[2 + axis] = (int32_t)scissor_extent;
    }
    *result = mesh;
    return true;
}

bool walle_lg_transition_frame_construct(const struct walle_lg_transition_frame_request* request,
                                         struct walle_lg_transition_frame*               result)
{
    if (request == nullptr || result == nullptr || request->material < WALLE_LG_MATERIAL_CLEAR
        || request->material > WALLE_LG_MATERIAL_REGULAR
        || request->appearance < WALLE_LG_APPEARANCE_LIGHT
        || request->appearance > WALLE_LG_APPEARANCE_DARK || request->window_width == 0
        || request->window_height == 0 || request->diameter == 0 || !isfinite(request->center_x)
        || !isfinite(request->center_y)
        || !(request->visible_fraction > 0.0f && request->visible_fraction <= 1.0f)
        || !(request->sdf_enclosure_radius >= 0.0) || !isfinite(request->sdf_enclosure_radius)) {
        return false;
    }

    struct walle_lg_transition_frame frame = {
        .material         = request->material,
        .appearance       = request->appearance,
        .visible_fraction = request->visible_fraction,
    };
    if (!construct_layer(request, &frame.layer))
        return false;
    frame.backdrop_scale = f32(1.0
                               - (request->material == WALLE_LG_MATERIAL_REGULAR ? 0.75 : 0.5)
                                     * request->visible_fraction);
    if (!construct_producer(request, &frame.layer, frame.backdrop_scale, &frame.producer))
        return false;

    struct walle_lg_transition_request transition = {
        .material         = request->material,
        .appearance       = request->appearance,
        .diameter         = request->diameter,
        .visible_fraction = request->visible_fraction,
    };
    struct walle_lg_numeric_inputs numeric;
    if (!walle_lg_transition_numeric_inputs(&transition, &numeric))
        return false;
    struct walle_lg_selected_region_request selected_request = {
        .bounds = {
            frame.producer.origin[0],
            frame.producer.origin[1],
            (int32_t)frame.producer.active_extent[0],
            (int32_t)frame.producer.active_extent[1],
        },
        .blur_radius       = numeric.value[WALLE_LG_INPUT_BLUR_RADIUS],
        .bleed_blur_radius = numeric.value[WALLE_LG_INPUT_BLEED_BLUR_RADIUS],
        .backdrop_scale    = frame.backdrop_scale,
    };
    if (frame.producer.active_extent[0] > INT32_MAX || frame.producer.active_extent[1] > INT32_MAX
        || !walle_lg_regular_selected_region(&selected_request, &frame.selected_region)) {
        return false;
    }

    float source_step_x
        = f32((double)frame.backdrop_scale / frame.selected_region.allocated_extent[0]);
    float source_step_y
        = f32((double)frame.backdrop_scale / frame.selected_region.allocated_extent[1]);
    float half_extent = f32(frame.layer.element_bounds[2] / 2.0);
    struct walle_lg_transition_profile_request profile_request = {
        .transition          = transition,
        .sdf_half_width      = half_extent,
        .sdf_half_height     = half_extent,
        .source_texel_step_x = source_step_x,
        .source_texel_step_y = source_step_y,
    };
    if (!walle_lg_transition_profile(&profile_request, &frame.profile)
        || !construct_scissor(request, &numeric, &frame.layer, frame.background_scissor)
        || !construct_producer_mesh(request,
                                    &numeric,
                                    &frame.layer,
                                    frame.backdrop_scale,
                                    &frame.producer,
                                    &frame.producer_mesh)) {
        return false;
    }

    construct_background_vertices(request, &frame);
    construct_highlight_vertices(request, &frame);
    *result = frame;
    return true;
}
