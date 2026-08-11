#include "liquid_glass_pyramid.h"

#include <math.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef uint16_t half_bits;

static_assert(sizeof(_Float16) == sizeof(half_bits));

static const int8_t  tap_x[]            = {0, -4, 0, 4, -2, -2, 2, 2, 0, -2, 0, 2, 0};
static const int8_t  tap_y[]            = {-4, 0, 4, 0, 2, -2, -2, 2, -2, 0, 2, 0, 0};
static const uint8_t group_indices[][4] = {
    {1, 0, 3, 2},
    {6, 5, 4, 7},
    {9, 8, 11, 10},
};
static const half_bits weights[]              = {0x2b36u, 0x2cefu, 0x2dc6u, 0x2ec0u};
static const half_bits quarter                = 0x3400u;
static const half_bits copy_base_denorm_limit = 0x068eu;

struct half_tables
{
    half_bits code[256];
    half_bits four_code_sum[1021];
};

typedef void (*row_function)(void* context, uint32_t begin, uint32_t end);

struct row_task
{
    row_function function;
    void*        context;
    uint32_t     begin;
    uint32_t     end;
};

static int32_t clamp_coordinate(int64_t value, int32_t lower, int32_t upper);

static void* run_row_task(void* opaque)
{
    struct row_task* task = opaque;
    task->function(task->context, task->begin, task->end);
    return nullptr;
}

static void parallel_rows(uint32_t rows, row_function function, void* context)
{
    constexpr uint32_t maximum_threads = 8;
    if (rows < 64) {
        function(context, 0, rows);
        return;
    }

    uint32_t        thread_count = rows < maximum_threads ? rows : maximum_threads;
    pthread_t       threads[maximum_threads - 1];
    struct row_task tasks[maximum_threads];
    uint32_t        created = 0;
    for (uint32_t index = 0; index < thread_count; ++index) {
        tasks[index] = (struct row_task){
            .function = function,
            .context  = context,
            .begin    = (uint32_t)((uint64_t)rows * index / thread_count),
            .end      = (uint32_t)((uint64_t)rows * (index + 1u) / thread_count),
        };
        if (index > 0) {
            if (pthread_create(&threads[index - 1u], nullptr, run_row_task, &tasks[index]) != 0) {
                break;
            }
            ++created;
        }
    }
    if (created + 1u != thread_count) {
        for (uint32_t index = 0; index < created; ++index) {
            (void)pthread_join(threads[index], nullptr);
        }
        function(context, 0, rows);
        return;
    }
    run_row_task(&tasks[0]);
    for (uint32_t index = 0; index < created; ++index) {
        (void)pthread_join(threads[index], nullptr);
    }
}

static uint64_t round_positive_to_even(double value)
{
    double   lower_value = floor(value);
    double   remainder   = value - lower_value;
    uint64_t lower       = (uint64_t)lower_value;
    if (remainder > 0.5 || (remainder == 0.5 && (lower & 1u) != 0)) {
        return lower + 1u;
    }
    return lower;
}

static _Float16 half_value(half_bits bits)
{
    _Float16 value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static half_bits half_bits_from_value(_Float16 value)
{
    half_bits bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static half_bits half_from_double(double value)
{
    volatile _Float16 rounded = (_Float16)value;
    return half_bits_from_value(rounded);
}

static double half_to_double(half_bits value)
{
    return (double)half_value(value);
}

static half_bits half_add(half_bits left, half_bits right)
{
    volatile _Float16 result = half_value(left) + half_value(right);
    return half_bits_from_value(result);
}

static half_bits half_multiply(half_bits left, half_bits right)
{
    volatile _Float16 result = half_value(left) * half_value(right);
    return half_bits_from_value(result);
}

static half_bits half_fma(half_bits left, half_bits right, half_bits addend)
{
    volatile _Float16 result = (_Float16)fmaf(
        (float)half_value(left), (float)half_value(right), (float)half_value(addend));
    return half_bits_from_value(result);
}

static float float32_add(float left, float right)
{
    volatile float result = left + right;
    return result;
}

static float float32_subtract(float left, float right)
{
    volatile float result = left - right;
    return result;
}

static float float32_multiply(float left, float right)
{
    volatile float result = left * right;
    return result;
}

static float float32_divide(float left, float right)
{
    volatile float result = left / right;
    return result;
}

static unsigned char half_to_unorm8(half_bits value)
{
    double scaled = half_to_double(value) * 255.0;
    if (!(scaled > 0.0)) {
        return 0;
    }
    if (scaled >= 255.0) {
        return 255;
    }
    return (unsigned char)round_positive_to_even(scaled);
}

static half_bits linear_bgra8_sample(const unsigned char* source,
                                     uint32_t             width,
                                     uint32_t             height,
                                     float                coordinate_x,
                                     float                coordinate_y,
                                     size_t               channel)
{
    float    position_x = float32_subtract(float32_multiply(coordinate_x, (float)width), 0.5f);
    float    position_y = float32_subtract(float32_multiply(coordinate_y, (float)height), 0.5f);
    float    floor_x    = floorf(position_x);
    float    floor_y    = floorf(position_y);
    int64_t  origin_x   = (int64_t)floor_x;
    int64_t  origin_y   = (int64_t)floor_y;
    float    fraction_x = float32_subtract(position_x, floor_x);
    float    fraction_y = float32_subtract(position_y, floor_y);
    uint32_t weight_x   = (uint32_t)floorf(float32_add(float32_multiply(fraction_x, 256.0f), 0.5f));
    uint32_t weight_y   = (uint32_t)floorf(float32_add(float32_multiply(fraction_y, 256.0f), 0.5f));
    uint32_t inverse_x  = 256u - weight_x;
    uint32_t inverse_y  = 256u - weight_y;
    int32_t  x0         = clamp_coordinate(origin_x, 0, (int32_t)width - 1);
    int32_t  x1         = clamp_coordinate(origin_x + 1, 0, (int32_t)width - 1);
    int32_t  y0         = clamp_coordinate(origin_y, 0, (int32_t)height - 1);
    int32_t  y1         = clamp_coordinate(origin_y + 1, 0, (int32_t)height - 1);
    size_t   row_stride = (size_t)width * 4u;
    uint64_t weighted_codes = (uint64_t)inverse_y * inverse_x
                                  * source[(size_t)y0 * row_stride + (size_t)x0 * 4u + channel]
                              + (uint64_t)inverse_y * weight_x
                                    * source[(size_t)y0 * row_stride + (size_t)x1 * 4u + channel]
                              + (uint64_t)weight_y * inverse_x
                                    * source[(size_t)y1 * row_stride + (size_t)x0 * 4u + channel]
                              + (uint64_t)weight_y * weight_x
                                    * source[(size_t)y1 * row_stride + (size_t)x1 * 4u + channel];
    uint64_t fixed_sixteenths = (weighted_codes + 2'048u) / 4'096u;
    return half_from_double((double)fixed_sixteenths / (255.0 * 16.0));
}

static void initialize_half_tables(struct half_tables* tables)
{
    for (uint32_t code = 0; code < 256; ++code) {
        tables->code[code] = half_from_double((double)code / 255.0);
    }
    for (uint32_t sum = 0; sum <= 1020; ++sum) {
        tables->four_code_sum[sum] = half_from_double((double)sum * (0.25 / 255.0));
    }
}

static bool image_byte_count(uint32_t width, uint32_t height, size_t* result)
{
    if (width == 0 || height == 0 || width > SIZE_MAX / height) {
        return false;
    }
    size_t pixels = (size_t)width * height;
    if (pixels > SIZE_MAX / 4u) {
        return false;
    }
    *result = pixels * 4u;
    return true;
}

static bool allocate_level(struct walle_lg_pyramid_level* level, uint32_t width, uint32_t height)
{
    size_t byte_count;
    if (!image_byte_count(width, height, &byte_count)) {
        return false;
    }
    unsigned char* pixels = malloc(byte_count);
    if (pixels == nullptr) {
        return false;
    }
    *level = (struct walle_lg_pyramid_level){
        .width      = width,
        .height     = height,
        .byte_count = byte_count,
        .bgra8      = pixels,
    };
    return true;
}

void walle_lg_destroy_pyramid(struct walle_lg_pyramid* pyramid)
{
    if (pyramid == nullptr) {
        return;
    }
    for (uint32_t level = 0; level < pyramid->level_count; ++level) {
        free(pyramid->levels[level].bgra8);
    }
    *pyramid = (struct walle_lg_pyramid){};
}

static void regular_producer_pixel(const unsigned char*      wallpaper,
                                   uint32_t                  width,
                                   uint32_t                  height,
                                   uint32_t                  producer_x,
                                   uint32_t                  producer_y,
                                   const struct half_tables* tables,
                                   unsigned char             output[static 4])
{
    static const uint8_t quadrant_x[] = {0, 2, 0, 2};
    static const uint8_t quadrant_y[] = {2, 2, 0, 0};
    half_bits            result[4]    = {};
    for (size_t quadrant = 0; quadrant < 4; ++quadrant) {
        uint32_t code_sum[4] = {};
        for (uint32_t delta_y = 0; delta_y < 2; ++delta_y) {
            for (uint32_t delta_x = 0; delta_x < 2; ++delta_x) {
                uint32_t source_x          = 4u * producer_x + quadrant_x[quadrant] + delta_x;
                uint32_t bottom_left_y     = 4u * producer_y + quadrant_y[quadrant] + delta_y;
                uint32_t source_y          = height - 1u - bottom_left_y;
                const unsigned char* pixel = wallpaper + ((size_t)source_y * width + source_x) * 4u;
                code_sum[0] += pixel[2];
                code_sum[1] += pixel[1];
                code_sum[2] += pixel[0];
                code_sum[3] += pixel[3];
            }
        }
        for (size_t channel = 0; channel < 4; ++channel) {
            half_bits sample = tables->four_code_sum[code_sum[channel]];
            result[channel]  = half_fma(sample, quarter, result[channel]);
        }
    }
    for (size_t channel = 0; channel < 4; ++channel) {
        output[channel] = half_to_unorm8(result[channel]);
    }
}

static int32_t clamp_coordinate(int64_t value, int32_t lower, int32_t upper)
{
    if (value < lower) {
        return lower;
    }
    if (value > upper) {
        return upper;
    }
    return (int32_t)value;
}

static void copy_base_sample_pixel(const struct walle_lg_pyramid_level* source,
                                   uint32_t                             output_x,
                                   uint32_t                             output_y,
                                   int8_t                               offset_x,
                                   int8_t                               offset_y,
                                   const struct half_tables*            tables,
                                   half_bits                            output[static 4])
{
    int64_t              base_x      = 2 * (int64_t)output_x + offset_x;
    int64_t              base_y      = 2 * (int64_t)output_y + offset_y;
    int32_t              x0          = clamp_coordinate(base_x, 0, (int32_t)source->width - 1);
    int32_t              x1          = clamp_coordinate(base_x + 1, 0, (int32_t)source->width - 1);
    int32_t              y0          = clamp_coordinate(base_y, 0, (int32_t)source->height - 1);
    int32_t              y1          = clamp_coordinate(base_y + 1, 0, (int32_t)source->height - 1);
    size_t               row_stride  = (size_t)source->width * 4u;
    const unsigned char* top_left    = source->bgra8 + (size_t)y0 * row_stride + (size_t)x0 * 4u;
    const unsigned char* top_right   = source->bgra8 + (size_t)y0 * row_stride + (size_t)x1 * 4u;
    const unsigned char* bottom_left = source->bgra8 + (size_t)y1 * row_stride + (size_t)x0 * 4u;
    const unsigned char* bottom_right = source->bgra8 + (size_t)y1 * row_stride + (size_t)x1 * 4u;
    for (size_t channel = 0; channel < 4; ++channel) {
        half_bits sum = half_add(tables->code[top_right[channel]], tables->code[top_left[channel]]);
        sum           = half_add(sum, tables->code[bottom_left[channel]]);
        sum           = half_add(sum, tables->code[bottom_right[channel]]);
        output[channel] = half_multiply(sum, quarter);
    }
}

static void agx2_sample_pixel(const struct walle_lg_pyramid_level* source,
                              uint32_t                             output_x,
                              uint32_t                             output_y,
                              int8_t                               offset_x,
                              int8_t                               offset_y,
                              const struct half_tables*            tables,
                              half_bits                            output[static 4])
{
    int64_t              base_x      = 2 * (int64_t)output_x + offset_x;
    int64_t              base_y      = 2 * (int64_t)output_y + offset_y;
    int32_t              x0          = clamp_coordinate(base_x, 0, (int32_t)source->width - 1);
    int32_t              x1          = clamp_coordinate(base_x + 1, 0, (int32_t)source->width - 1);
    int32_t              y0          = clamp_coordinate(base_y, 0, (int32_t)source->height - 1);
    int32_t              y1          = clamp_coordinate(base_y + 1, 0, (int32_t)source->height - 1);
    size_t               row_stride  = (size_t)source->width * 4u;
    const unsigned char* top_left    = source->bgra8 + (size_t)y0 * row_stride + (size_t)x0 * 4u;
    const unsigned char* top_right   = source->bgra8 + (size_t)y0 * row_stride + (size_t)x1 * 4u;
    const unsigned char* bottom_left = source->bgra8 + (size_t)y1 * row_stride + (size_t)x0 * 4u;
    const unsigned char* bottom_right = source->bgra8 + (size_t)y1 * row_stride + (size_t)x1 * 4u;
    for (size_t channel = 0; channel < 4; ++channel) {
        uint32_t code_sum = (uint32_t)top_left[channel] + top_right[channel] + bottom_left[channel]
                            + bottom_right[channel];
        output[channel] = tables->four_code_sum[code_sum];
    }
}

static half_bits
ordered_group_sum(const half_bits samples[static 13][4], size_t group, size_t channel)
{
    const uint8_t* order  = group_indices[group];
    half_bits      result = samples[order[0]][channel];
    for (size_t index = 1; index < 4; ++index) {
        result = half_add(result, samples[order[index]][channel]);
    }
    return result;
}

static void filtered_pixel(const struct walle_lg_pyramid_level* source,
                           uint32_t                             output_x,
                           uint32_t                             output_y,
                           bool                                 copy_base,
                           const struct half_tables*            tables,
                           unsigned char                        output[static 4])
{
    half_bits samples[13][4];
    for (size_t tap = 0; tap < 13; ++tap) {
        if (copy_base) {
            copy_base_sample_pixel(
                source, output_x, output_y, tap_x[tap], tap_y[tap], tables, samples[tap]);
        } else {
            agx2_sample_pixel(
                source, output_x, output_y, tap_x[tap], tap_y[tap], tables, samples[tap]);
        }
    }
    for (size_t channel = 0; channel < 4; ++channel) {
        half_bits groups[3] = {
            ordered_group_sum(samples, 0, channel),
            ordered_group_sum(samples, 1, channel),
            ordered_group_sum(samples, 2, channel),
        };
        half_bits result = half_multiply(samples[12][channel], weights[3]);
        result           = half_fma(groups[1], weights[1], result);
        result           = half_fma(groups[2], weights[2], result);
        result           = half_fma(groups[0], weights[0], result);
        if (copy_base && channel < 3 && (result & 0x7fffu) < copy_base_denorm_limit) {
            result = 0;
        }
        output[channel] = half_to_unorm8(result);
    }
}

struct downsample_rows_context
{
    const struct walle_lg_pyramid_level* source;
    struct walle_lg_pyramid_level*       destination;
    bool                                 copy_base;
    const struct half_tables*            tables;
};

static void downsample_rows(void* opaque, uint32_t begin, uint32_t end)
{
    const struct downsample_rows_context* context = opaque;
    for (uint32_t y = begin; y < end; ++y) {
        for (uint32_t x = 0; x < context->destination->width; ++x) {
            size_t offset = ((size_t)y * context->destination->width + x) * 4u;
            filtered_pixel(context->source,
                           x,
                           y,
                           context->copy_base,
                           context->tables,
                           context->destination->bgra8 + offset);
        }
    }
}

static bool downsample_level(const struct walle_lg_pyramid_level* source,
                             struct walle_lg_pyramid_level*       destination,
                             bool                                 copy_base,
                             const struct half_tables*            tables)
{
    if ((source->width & 1u) != 0 || (source->height & 1u) != 0
        || !allocate_level(destination, source->width / 2u, source->height / 2u)) {
        return false;
    }
    struct downsample_rows_context context = {
        .source      = source,
        .destination = destination,
        .copy_base   = copy_base,
        .tables      = tables,
    };
    parallel_rows(destination->height, downsample_rows, &context);
    return true;
}

struct producer_rows_context
{
    const unsigned char*                           wallpaper;
    const struct walle_lg_static_regular_request*  request;
    const struct walle_lg_static_regular_geometry* geometry;
    const struct half_tables*                      tables;
    unsigned char*                                 producer;
};

static void producer_rows(void* opaque, uint32_t begin, uint32_t end)
{
    const struct producer_rows_context* context = opaque;
    for (uint32_t y = begin; y < end; ++y) {
        for (uint32_t x = 0; x < context->geometry->active_extent[0]; ++x) {
            size_t offset = ((size_t)y * context->geometry->producer_extent[0] + x) * 4u;
            regular_producer_pixel(context->wallpaper,
                                   context->request->window_width,
                                   context->request->window_height,
                                   (uint32_t)context->geometry->crop_origin[0] + x,
                                   (uint32_t)context->geometry->crop_origin[1] + y,
                                   context->tables,
                                   context->producer + offset);
        }
    }
}

bool walle_lg_build_static_regular_pyramid(const unsigned char* wallpaper_rgba8,
                                           size_t               wallpaper_byte_count,
                                           const struct walle_lg_static_regular_request* request,
                                           struct walle_lg_pyramid*                      result)
{
    if (wallpaper_rgba8 == nullptr || request == nullptr || result == nullptr
        || request->window_width % 4u != 0 || request->window_height % 4u != 0) {
        return false;
    }
    size_t expected_wallpaper_bytes;
    if (!image_byte_count(request->window_width, request->window_height, &expected_wallpaper_bytes)
        || wallpaper_byte_count != expected_wallpaper_bytes) {
        return false;
    }

    *result = (struct walle_lg_pyramid){};
    struct walle_lg_static_regular_geometry geometry;
    if (!walle_lg_static_regular_geometry(request, &geometry)
        || geometry.selected_region.level_count > WALLE_LG_MAX_PYRAMID_LEVELS) {
        return false;
    }

    struct half_tables tables;
    initialize_half_tables(&tables);

    size_t producer_byte_count;
    if (!image_byte_count(
            geometry.producer_extent[0], geometry.producer_extent[1], &producer_byte_count)) {
        return false;
    }
    unsigned char* producer = calloc(1, producer_byte_count);
    if (producer == nullptr) {
        return false;
    }
    struct producer_rows_context producer_context = {
        .wallpaper = wallpaper_rgba8,
        .request   = request,
        .geometry  = &geometry,
        .tables    = &tables,
        .producer  = producer,
    };
    parallel_rows(geometry.active_extent[1], producer_rows, &producer_context);

    struct walle_lg_pyramid_level* base = &result->levels[0];
    if (!allocate_level(base,
                        geometry.selected_region.allocated_extent[0],
                        geometry.selected_region.allocated_extent[1])) {
        free(producer);
        return false;
    }
    result->level_count = 1;
    for (uint32_t y = 0; y < base->height; ++y) {
        int32_t source_y = clamp_coordinate((int64_t)y + geometry.selected_region.copy_offset[1],
                                            geometry.texture_coordinate_clamp[1],
                                            geometry.texture_coordinate_clamp[3]);
        for (uint32_t x = 0; x < base->width; ++x) {
            int32_t source_x
                = clamp_coordinate((int64_t)x + geometry.selected_region.copy_offset[0],
                                   geometry.texture_coordinate_clamp[0],
                                   geometry.texture_coordinate_clamp[2]);
            memcpy(base->bgra8 + ((size_t)y * base->width + x) * 4u,
                   producer
                       + ((size_t)source_y * geometry.producer_extent[0] + (uint32_t)source_x) * 4u,
                   4u);
        }
    }
    free(producer);

    while (result->level_count < geometry.selected_region.level_count) {
        uint32_t level = result->level_count;
        if (!downsample_level(
                &result->levels[level - 1u], &result->levels[level], level == 1u, &tables)) {
            walle_lg_destroy_pyramid(result);
            return false;
        }
        ++result->level_count;
    }
    return true;
}

static void dynamic_producer_pixel(const unsigned char*        source,
                                   uint32_t                    source_width,
                                   uint32_t                    source_height,
                                   enum walle_lg_producer_kind kind,
                                   float                       downsample_offset_x,
                                   float                       downsample_offset_y,
                                   const float                 coordinate[static 2],
                                   unsigned char               output[static 4])
{
    if (kind == WALLE_LG_PRODUCER_DIRECT) {
        for (size_t channel = 0; channel < 4; ++channel) {
            output[channel] = half_to_unorm8(linear_bgra8_sample(
                source, source_width, source_height, coordinate[0], coordinate[1], channel));
        }
        return;
    }

    static const int8_t offset_sign_x[4] = {-1, 1, -1, 1};
    static const int8_t offset_sign_y[4] = {1, 1, -1, -1};
    half_bits           accumulated[4]   = {};
    for (size_t tap = 0; tap < 4; ++tap) {
        float x = float32_add(coordinate[0],
                              offset_sign_x[tap] < 0 ? -downsample_offset_x : downsample_offset_x);
        float y = float32_add(coordinate[1],
                              offset_sign_y[tap] < 0 ? -downsample_offset_y : downsample_offset_y);
        for (size_t channel = 0; channel < 4; ++channel) {
            half_bits sample
                = linear_bgra8_sample(source, source_width, source_height, x, y, channel);
            accumulated[channel] = half_fma(sample, quarter, accumulated[channel]);
        }
    }
    for (size_t channel = 0; channel < 4; ++channel) {
        output[channel] = half_to_unorm8(accumulated[channel]);
    }
}

struct dynamic_producer_rows_context
{
    const unsigned char*                        source;
    uint32_t                                    source_width;
    uint32_t                                    source_height;
    enum walle_lg_producer_kind                 kind;
    float                                       downsample_offset_x;
    float                                       downsample_offset_y;
    const struct walle_lg_producer_raster_quad* quad;
    int32_t                                     left;
    int32_t                                     right;
    struct walle_lg_pyramid_level*              destination;
};

static void dynamic_producer_rows(void* opaque, uint32_t begin, uint32_t end)
{
    const struct dynamic_producer_rows_context* context = opaque;
    for (uint32_t row = begin; row < end; ++row) {
        int32_t y = context->quad->visible_bounds[1] + (int32_t)row;
        for (int32_t x = context->left; x < context->right; ++x) {
            float coordinate[2];
            if (!walle_lg_producer_raster_coordinates(context->quad, x, y, coordinate)) {
                continue;
            }
            size_t offset = ((size_t)y * context->destination->width + (uint32_t)x) * 4u;
            dynamic_producer_pixel(context->source,
                                   context->source_width,
                                   context->source_height,
                                   context->kind,
                                   context->downsample_offset_x,
                                   context->downsample_offset_y,
                                   coordinate,
                                   context->destination->bgra8 + offset);
        }
    }
}

static int32_t maximum_i32(int32_t left, int32_t right)
{
    return left > right ? left : right;
}

static int32_t minimum_i32(int32_t left, int32_t right)
{
    return left < right ? left : right;
}

static void draw_dynamic_producer_quad(const unsigned char*                        source,
                                       uint32_t                                    source_width,
                                       uint32_t                                    source_height,
                                       const struct walle_lg_transition_frame*     frame,
                                       const struct walle_lg_producer_raster_quad* quad,
                                       float                          downsample_offset_x,
                                       float                          downsample_offset_y,
                                       struct walle_lg_pyramid_level* destination)
{
    int32_t left
        = maximum_i32(maximum_i32(quad->visible_bounds[0], frame->producer_mesh.scissor[0]), 0);
    int32_t bottom
        = maximum_i32(maximum_i32(quad->visible_bounds[1], frame->producer_mesh.scissor[1]), 0);
    int32_t right = minimum_i32(
        minimum_i32(quad->visible_bounds[2],
                    frame->producer_mesh.scissor[0] + frame->producer_mesh.scissor[2]),
        (int32_t)destination->width);
    int32_t top = minimum_i32(
        minimum_i32(quad->visible_bounds[3],
                    frame->producer_mesh.scissor[1] + frame->producer_mesh.scissor[3]),
        (int32_t)destination->height);
    if (left >= right || bottom >= top) {
        return;
    }
    struct dynamic_producer_rows_context context = {
        .source              = source,
        .source_width        = source_width,
        .source_height       = source_height,
        .kind                = frame->producer_mesh.kind,
        .downsample_offset_x = downsample_offset_x,
        .downsample_offset_y = downsample_offset_y,
        .quad                = quad,
        .left                = left,
        .right               = right,
        .destination         = destination,
    };
    uint32_t row_count                           = (uint32_t)(top - bottom);
    context.quad                                 = quad;
    struct walle_lg_producer_raster_quad shifted = *quad;
    shifted.visible_bounds[1]                    = bottom;
    context.quad                                 = &shifted;
    parallel_rows(row_count, dynamic_producer_rows, &context);
}

struct dynamic_copy_rows_context
{
    const struct walle_lg_pyramid_level* source;
    struct walle_lg_pyramid_level*       destination;
    int32_t                              copy_offset[2];
    int32_t                              clamp_upper[2];
};

static void dynamic_copy_rows(void* opaque, uint32_t begin, uint32_t end)
{
    const struct dynamic_copy_rows_context* context = opaque;
    for (uint32_t y = begin; y < end; ++y) {
        int32_t source_y
            = clamp_coordinate((int64_t)y + context->copy_offset[1], 0, context->clamp_upper[1]);
        for (uint32_t x = 0; x < context->destination->width; ++x) {
            int32_t source_x = clamp_coordinate(
                (int64_t)x + context->copy_offset[0], 0, context->clamp_upper[0]);
            memcpy(context->destination->bgra8 + ((size_t)y * context->destination->width + x) * 4u,
                   context->source->bgra8
                       + ((size_t)source_y * context->source->width + (uint32_t)source_x) * 4u,
                   4u);
        }
    }
}

void walle_lg_destroy_dynamic_regular_backdrop(struct walle_lg_dynamic_regular_backdrop* backdrop)
{
    if (backdrop == nullptr) {
        return;
    }
    free(backdrop->producer.bgra8);
    walle_lg_destroy_pyramid(&backdrop->pyramid);
    *backdrop = (struct walle_lg_dynamic_regular_backdrop){};
}

bool walle_lg_build_dynamic_regular_backdrop(const unsigned char* source_bgra8,
                                             size_t               source_byte_count,
                                             uint32_t             source_width,
                                             uint32_t             source_height,
                                             const struct walle_lg_transition_frame*   frame,
                                             const struct walle_lg_raster_calibration* calibration,
                                             struct walle_lg_dynamic_regular_backdrop* result)
{
    if (source_bgra8 == nullptr || frame == nullptr || calibration == nullptr || result == nullptr
        || source_width == 0 || source_height == 0 || source_width > INT32_MAX
        || source_height > INT32_MAX || frame->material != WALLE_LG_MATERIAL_REGULAR
        || frame->producer.active_extent[0] == 0 || frame->producer.active_extent[1] == 0
        || frame->producer.active_extent[0] > frame->producer.storage_extent[0]
        || frame->producer.active_extent[1] > frame->producer.storage_extent[1]
        || frame->producer.storage_extent[0] > INT32_MAX
        || frame->producer.storage_extent[1] > INT32_MAX || frame->selected_region.level_count == 0
        || frame->selected_region.level_count > WALLE_LG_MAX_PYRAMID_LEVELS) {
        return false;
    }
    size_t expected_source_bytes;
    if (!image_byte_count(source_width, source_height, &expected_source_bytes)
        || source_byte_count != expected_source_bytes) {
        return false;
    }

    struct walle_lg_dynamic_regular_backdrop backdrop = {};
    if (!allocate_level(&backdrop.producer,
                        frame->producer.storage_extent[0],
                        frame->producer.storage_extent[1])) {
        return false;
    }
    memset(backdrop.producer.bgra8, 0, backdrop.producer.byte_count);

    struct walle_lg_producer_raster raster = {};
    if (!walle_lg_producer_raster_construct(
            frame, source_width, source_height, calibration, &raster)) {
        walle_lg_destroy_dynamic_regular_backdrop(&backdrop);
        return false;
    }
    float downsample_offset_x = 0.0f;
    float downsample_offset_y = 0.0f;
    if (frame->producer_mesh.kind == WALLE_LG_PRODUCER_DOWNSAMPLE_4) {
        float radicand = float32_subtract(float32_multiply(3.0f, frame->visible_fraction), 2.0f);
        if (!(radicand >= 0.0f)) {
            walle_lg_producer_raster_destroy(&raster);
            walle_lg_destroy_dynamic_regular_backdrop(&backdrop);
            return false;
        }
        float radius        = sqrtf(radicand);
        downsample_offset_x = float32_divide(radius, (float)source_width);
        downsample_offset_y = float32_divide(radius, (float)source_height);
    }
    for (uint32_t quad = 0; quad < raster.quad_count; ++quad) {
        draw_dynamic_producer_quad(source_bgra8,
                                   source_width,
                                   source_height,
                                   frame,
                                   &raster.quads[quad],
                                   downsample_offset_x,
                                   downsample_offset_y,
                                   &backdrop.producer);
    }
    walle_lg_producer_raster_destroy(&raster);

    struct walle_lg_pyramid_level* base = &backdrop.pyramid.levels[0];
    if (!allocate_level(base,
                        frame->selected_region.allocated_extent[0],
                        frame->selected_region.allocated_extent[1])) {
        walle_lg_destroy_dynamic_regular_backdrop(&backdrop);
        return false;
    }
    backdrop.pyramid.level_count = 1;
    struct dynamic_copy_rows_context copy_context = {
        .source = &backdrop.producer,
        .destination = base,
        .copy_offset = {
            frame->selected_region.copy_offset[0],
            frame->selected_region.copy_offset[1],
        },
        .clamp_upper = {
            (int32_t)frame->producer.active_extent[0] - 1,
            (int32_t)frame->producer.active_extent[1] - 1,
        },
    };
    parallel_rows(base->height, dynamic_copy_rows, &copy_context);

    struct half_tables tables;
    initialize_half_tables(&tables);
    while (backdrop.pyramid.level_count < frame->selected_region.level_count) {
        uint32_t level = backdrop.pyramid.level_count;
        if (!downsample_level(&backdrop.pyramid.levels[level - 1u],
                              &backdrop.pyramid.levels[level],
                              level == 1u,
                              &tables)) {
            walle_lg_destroy_dynamic_regular_backdrop(&backdrop);
            return false;
        }
        ++backdrop.pyramid.level_count;
    }
    *result = backdrop;
    return true;
}
