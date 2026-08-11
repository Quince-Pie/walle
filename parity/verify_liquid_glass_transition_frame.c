#include "liquid_glass_transition_frame.h"
#include "liquid_glass_raster.h"
#include "liquid_glass_gl_renderer.h"

#include <EGL/egl.h>
#include <EGL/eglext.h>

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct gate_config
{
    char     magic[8];
    uint32_t width;
    uint32_t height;
    uint32_t material;
    uint32_t appearance;
    uint32_t mip_count;
    uint32_t tile_start;
    uint32_t coefficient_width;
    uint32_t slopes[4];
    uint32_t source_width;
    uint32_t source_height;
    uint32_t main_vertex_count;
    uint32_t shadow_vertex_count;
    uint32_t shadow_index_count;
    uint32_t highlight_vertex_count;
    uint32_t highlight_index_count;
    uint32_t vibrant_arithmetic_mode;
    uint32_t background_scissor_x;
    uint32_t background_scissor_y;
    uint32_t background_scissor_width;
    uint32_t background_scissor_height;
    int32_t  highlight_modes[15];
    uint32_t use_apple_half_intrinsic_table;
};

struct fixture_case
{
    uint32_t sample;
    uint32_t fraction_bits;
};

static_assert(sizeof(struct gate_config) == 164);

static const struct fixture_case cases[] = {
    {1, UINT32_C(0x3f777ca0)},
    {4, UINT32_C(0x3f5ffe30)},
    {8, UINT32_C(0x3f3fa230)},
    {12, UINT32_C(0x3f1fab80)},
    {16, UINT32_C(0x3eff2f80)},
    {20, UINT32_C(0x3ebff980)},
    {24, UINT32_C(0x3e7f4380)},
    {28, UINT32_C(0x3dffa900)},
};

struct calibration_storage
{
    uint8_t* p25_ceil_bits;
};

struct gl_context
{
    EGLDisplay display;
    EGLContext context;
    EGLSurface surface;
};

static bool load_file(const char* path, void* destination, size_t size)
{
    FILE* stream = fopen(path, "rb");
    if (stream == nullptr) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return false;
    }
    bool success = fread(destination, 1, size, stream) == size && fgetc(stream) == EOF;
    if (!success)
        fprintf(stderr, "%s: unexpected byte count\n", path);
    if (fclose(stream) != 0) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        success = false;
    }
    return success;
}

static uint8_t* load_allocated_file(const char* path, size_t size)
{
    uint8_t* result = malloc(size);
    if (result == nullptr || !load_file(path, result, size)) {
        free(result);
        return nullptr;
    }
    return result;
}

static char* load_text_file(const char* path)
{
    FILE* stream = fopen(path, "rb");
    if (stream == nullptr || fseek(stream, 0, SEEK_END) != 0) {
        if (stream != nullptr)
            fclose(stream);
        return nullptr;
    }
    long size = ftell(stream);
    if (size < 0 || fseek(stream, 0, SEEK_SET) != 0) {
        fclose(stream);
        return nullptr;
    }
    char* result = malloc((size_t)size + 1);
    if (result == nullptr || fread(result, 1, (size_t)size, stream) != (size_t)size
        || fclose(stream) != 0) {
        free(result);
        return nullptr;
    }
    result[size] = '\0';
    return result;
}

static bool create_gl_context(struct gl_context* result)
{
    const char* extensions = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
    if (extensions == nullptr || strstr(extensions, "EGL_MESA_platform_surfaceless") == nullptr)
        return false;
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_platform_display
        = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (get_platform_display == nullptr)
        return false;
    EGLDisplay display
        = get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    EGLint major, minor;
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor)
        || !eglBindAPI(EGL_OPENGL_API)) {
        return false;
    }
    const EGLint config_attributes[] = {
        EGL_SURFACE_TYPE,
        EGL_PBUFFER_BIT,
        EGL_RED_SIZE,
        8,
        EGL_GREEN_SIZE,
        8,
        EGL_BLUE_SIZE,
        8,
        EGL_ALPHA_SIZE,
        8,
        EGL_RENDERABLE_TYPE,
        EGL_OPENGL_BIT,
        EGL_NONE,
    };
    EGLConfig config;
    EGLint count = 0;
    if (!eglChooseConfig(display, config_attributes, &config, 1, &count) || count != 1)
        return false;
    const EGLint surface_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attributes);
    const EGLint context_attributes[] = {
        EGL_CONTEXT_MAJOR_VERSION_KHR,
        4,
        EGL_CONTEXT_MINOR_VERSION_KHR,
        5,
        EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR,
        EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,
        EGL_NONE,
    };
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    if (surface == EGL_NO_SURFACE || context == EGL_NO_CONTEXT
        || !eglMakeCurrent(display, surface, surface, context)) {
        return false;
    }
    *result = (struct gl_context){.display = display, .context = context, .surface = surface};
    return true;
}

static void destroy_gl_context(struct gl_context* context)
{
    if (context->display == EGL_NO_DISPLAY)
        return;
    eglMakeCurrent(context->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (context->context != EGL_NO_CONTEXT)
        eglDestroyContext(context->display, context->context);
    if (context->surface != EGL_NO_SURFACE)
        eglDestroySurface(context->display, context->surface);
    eglTerminate(context->display);
    *context = (struct gl_context){.display = EGL_NO_DISPLAY,
                                   .context = EGL_NO_CONTEXT,
                                   .surface = EGL_NO_SURFACE};
}

static bool calibration_path(char       result[static 4096],
                             const char* root,
                             const char* name)
{
    int count = snprintf(result, 4096, "%s/%s", root, name);
    return count >= 0 && count < 4096;
}

static void free_calibration(struct calibration_storage* storage)
{
    free(storage->p25_ceil_bits);
    *storage = (struct calibration_storage){};
}

static bool load_calibration(const char*                         root,
                             struct calibration_storage*         storage,
                             struct walle_lg_raster_calibration* calibration)
{
    constexpr size_t selector_bit_count = 1u << 24;
    constexpr size_t selector_byte_count = selector_bit_count / 8u;
    *storage = (struct calibration_storage){.p25_ceil_bits = malloc(selector_byte_count)};
    if (storage->p25_ceil_bits == nullptr) {
        free_calibration(storage);
        return false;
    }
    char path[4096];
    bool success = calibration_path(path, root, "raster_p25_selector_ceil_bits.bin")
                   && load_file(path, storage->p25_ceil_bits, selector_byte_count);
    if (!success) {
        free_calibration(storage);
        return false;
    }
    *calibration = (struct walle_lg_raster_calibration){
        .p25_ceil_bits          = storage->p25_ceil_bits,
        .p25_selector_bit_count = selector_bit_count,
    };
    return true;
}

static bool compare_file(const char* path,
                         const void* candidate,
                         size_t      size,
                         size_t*     checked_bytes)
{
    uint8_t* expected = malloc(size);
    if (expected == nullptr) {
        fprintf(stderr, "could not allocate %zu comparison bytes\n", size);
        return false;
    }
    bool success = load_file(path, expected, size);
    if (success) {
        const uint8_t* actual = candidate;
        for (size_t index = 0; index < size; ++index) {
            if (actual[index] != expected[index]) {
                fprintf(stderr,
                        "%s: byte %zu differs (candidate=%02x expected=%02x)\n",
                        path,
                        index,
                        actual[index],
                        expected[index]);
                success = false;
                break;
            }
        }
    }
    free(expected);
    if (success)
        *checked_bytes += size;
    return success;
}

static bool fixture_path(char       result[static 4096],
                         const char* root,
                         uint32_t    sample,
                         const char* name)
{
    int count = snprintf(result, 4096, "%s/fixture-%02" PRIu32 "/%s", root, sample, name);
    if (count < 0 || count >= 4096) {
        fprintf(stderr, "fixture path exceeds 4095 bytes\n");
        return false;
    }
    return true;
}

static bool compare_config(const struct gate_config*                config,
                           const struct walle_lg_transition_frame* frame,
                           const struct walle_lg_raster_tables*    raster,
                           uint32_t                                 sample)
{
    const uint32_t expected_scissor[4] = {
        (uint32_t)frame->background_scissor[0],
        (uint32_t)frame->background_scissor[1],
        (uint32_t)frame->background_scissor[2],
        (uint32_t)frame->background_scissor[3],
    };
    const uint32_t observed_scissor[4] = {
        config->background_scissor_x,
        config->background_scissor_y,
        config->background_scissor_width,
        config->background_scissor_height,
    };
    bool exact = memcmp(config->magic, "WALLELG3", 8) == 0 && config->width == 1024
              && config->height == 1024 && config->material == WALLE_LG_MATERIAL_REGULAR
              && config->appearance == WALLE_LG_APPEARANCE_DARK
              && config->mip_count == frame->selected_region.level_count
              && config->tile_start == raster->tile_start
              && config->coefficient_width == raster->coefficient_width
              && memcmp(config->slopes, raster->slopes, sizeof config->slopes) == 0
              && config->source_width == frame->selected_region.allocated_extent[0]
              && config->source_height == frame->selected_region.allocated_extent[1]
              && config->main_vertex_count == WALLE_LG_MAIN_VERTEX_COUNT
              && config->shadow_vertex_count == WALLE_LG_SHADOW_VERTEX_COUNT
              && config->shadow_index_count == WALLE_LG_SHADOW_INDEX_COUNT
              && config->highlight_vertex_count == frame->highlight_vertex_count
              && config->highlight_index_count == frame->highlight_index_count
              && memcmp(observed_scissor, expected_scissor, sizeof expected_scissor) == 0;
    if (!exact)
        fprintf(stderr, "fixture %02" PRIu32 ": constructed config fields differ\n", sample);
    return exact;
}

static bool render_case(const char*                              root,
                        uint32_t                                 sample,
                        const struct gate_config*                config,
                        const struct walle_lg_transition_frame* frame,
                        const struct walle_lg_raster_tables*    raster,
                        struct walle_lg_gl_renderer*             renderer,
                        size_t*                                  rendered_bytes)
{
    struct walle_lg_rgba8_image mips[16] = {};
    if (config->mip_count == 0 || config->mip_count > 16)
        return false;
    char path[4096];
    uint32_t width = config->source_width, height = config->source_height;
    bool success = false;
    uint8_t* destination = nullptr;
    uint8_t* candidate   = nullptr;
    for (uint32_t level = 0; level < config->mip_count; ++level) {
        char name[64];
        int count = snprintf(name, sizeof name, "source-mip-%" PRIu32 ".rgba8", level);
        if (count < 0 || (size_t)count >= sizeof name
            || !fixture_path(path, root, sample, name)) {
            goto cleanup;
        }
        size_t size = (size_t)width * height * 4u;
        mips[level] = (struct walle_lg_rgba8_image){
            .width  = width,
            .height = height,
            .pixels = load_allocated_file(path, size),
        };
        if (mips[level].pixels == nullptr)
            goto cleanup;
        width  = width > 1 ? width / 2 : 1;
        height = height > 1 ? height / 2 : 1;
    }
    size_t frame_bytes = (size_t)config->width * config->height * 4u;
    if (!fixture_path(path, root, sample, "destination.rgba8"))
        goto cleanup;
    destination = load_allocated_file(path, frame_bytes);
    candidate   = malloc(frame_bytes);
    if (destination == nullptr || candidate == nullptr)
        goto cleanup;
    struct walle_lg_gl_frame gl_frame = {
        .transition = frame,
        .raster     = raster,
        .destination = {
            .width  = config->width,
            .height = config->height,
            .pixels = destination,
        },
        .source_mips      = mips,
        .source_mip_count = config->mip_count,
    };
    if (!walle_lg_gl_renderer_render(renderer, &gl_frame)
        || !walle_lg_gl_renderer_read_rgba8(renderer, candidate, frame_bytes)
        || !fixture_path(path, root, sample, "reference-bottom-left.rgba8")
        || !compare_file(path, candidate, frame_bytes, rendered_bytes)) {
        goto cleanup;
    }
    success = true;

cleanup:
    for (uint32_t level = 0; level < config->mip_count && level < 16; ++level)
        free((void*)mips[level].pixels);
    free(destination);
    free(candidate);
    return success;
}

static bool run_case(const char*                                root,
                     const struct fixture_case*                 fixture,
                     const struct walle_lg_raster_calibration* calibration,
                     struct walle_lg_gl_renderer*             renderer,
                     size_t*                                    constructor_bytes,
                     size_t*                                    raster_bytes,
                     size_t*                                    rendered_bytes)
{
    float fraction;
    memcpy(&fraction, &fixture->fraction_bits, sizeof fraction);
    struct walle_lg_transition_frame_request request = {
        .material             = WALLE_LG_MATERIAL_REGULAR,
        .appearance           = WALLE_LG_APPEARANCE_DARK,
        .window_width         = 1024,
        .window_height        = 1024,
        .diameter             = 480,
        .center_x             = 512.0,
        .center_y             = 512.0,
        .visible_fraction     = fraction,
        .sdf_enclosure_radius = 0x1.53b608p+5,
    };
    struct walle_lg_transition_frame frame;
    if (!walle_lg_transition_frame_construct(&request, &frame)) {
        fprintf(stderr, "fixture %02" PRIu32 ": frame construction failed\n", fixture->sample);
        return false;
    }

    struct walle_lg_raster_tables raster;
    if (!walle_lg_raster_tables_construct(&frame, 1024, 1024, calibration, &raster)) {
        fprintf(stderr, "fixture %02" PRIu32 ": raster construction failed\n", fixture->sample);
        return false;
    }

    char               path[4096];
    struct gate_config config;
    if (!fixture_path(path, root, fixture->sample, "config.bin")
        || !load_file(path, &config, sizeof config)
        || !compare_config(&config, &frame, &raster, fixture->sample)) {
        walle_lg_raster_tables_destroy(&raster);
        return false;
    }

    struct comparison
    {
        const char* name;
        const void* bytes;
        size_t      size;
    } comparisons[] = {
        {"main-vertices.f32", frame.main_vertices, sizeof frame.main_vertices},
        {"shadow-vertices.f32", frame.shadow_vertices, sizeof frame.shadow_vertices},
        {"shadow-indices.u16", frame.shadow_indices, sizeof frame.shadow_indices},
        {"highlight-vertices.f32",
         frame.highlight_vertices,
         frame.highlight_vertex_count * sizeof frame.highlight_vertices[0]},
        {"highlight-indices.u16",
         frame.highlight_indices,
         frame.highlight_index_count * sizeof frame.highlight_indices[0]},
        {"profile.bin", frame.profile.byte, sizeof frame.profile.byte},
        {"highlight-uniform.bin", frame.highlight_uniform, sizeof frame.highlight_uniform},
    };
    for (size_t index = 0; index < sizeof comparisons / sizeof comparisons[0]; ++index) {
        if (!fixture_path(path, root, fixture->sample, comparisons[index].name)
            || !compare_file(
                path, comparisons[index].bytes, comparisons[index].size, constructor_bytes)) {
            walle_lg_raster_tables_destroy(&raster);
            return false;
        }
    }
    struct comparison raster_comparisons[] = {
        {"interpolant-coefficients.rgba32ui",
         raster.coefficients,
         raster.coefficient_word_count * sizeof(uint32_t)},
        {"interpolant-axis.rgba32ui",
         raster.main_axis,
         raster.main_axis_word_count * sizeof(uint32_t)},
        {"shadow-interpolant-coefficients.rgba32ui",
         raster.shadow_coefficients,
         raster.shadow_coefficient_word_count * sizeof(uint32_t)},
        {"shadow-interpolant-slopes.rgba32ui",
         raster.shadow_slopes,
         raster.shadow_slope_word_count * sizeof(uint32_t)},
        {"highlight-interpolant-axis.rgba32ui",
         raster.highlight_axis,
         raster.highlight_axis_word_count * sizeof(uint32_t)},
    };
    for (size_t index = 0; index < sizeof raster_comparisons / sizeof raster_comparisons[0];
         ++index) {
        if (!fixture_path(path, root, fixture->sample, raster_comparisons[index].name)
            || !compare_file(path,
                             raster_comparisons[index].bytes,
                             raster_comparisons[index].size,
                             raster_bytes)) {
            walle_lg_raster_tables_destroy(&raster);
            return false;
        }
    }
    if (!render_case(root,
                     fixture->sample,
                     &config,
                     &frame,
                     &raster,
                     renderer,
                     rendered_bytes)) {
        walle_lg_raster_tables_destroy(&raster);
        return false;
    }
    walle_lg_raster_tables_destroy(&raster);
    return true;
}

int main(int argc, char** argv)
{
    if (argc != 6) {
        fprintf(stderr,
                "usage: %s FIXTURE_ROOT CALIBRATION_ROOT VERTEX_SHADER "
                "REGULAR_FRAGMENT_SHADER FLOAT_INTRINSIC\n",
                argv[0]);
        return 2;
    }
    struct calibration_storage         storage;
    struct walle_lg_raster_calibration calibration;
    if (!load_calibration(argv[2], &storage, &calibration))
        return 1;
    char* vertex_shader   = load_text_file(argv[3]);
    char* fragment_shader = load_text_file(argv[4]);
    uint8_t* intrinsic    = load_allocated_file(argv[5], WALLE_LG_FLOAT_INTRINSIC_BYTE_COUNT);
    struct gl_context context = {.display = EGL_NO_DISPLAY,
                                 .context = EGL_NO_CONTEXT,
                                 .surface = EGL_NO_SURFACE};
    if (vertex_shader == nullptr || fragment_shader == nullptr || intrinsic == nullptr
        || !create_gl_context(&context)) {
        free(vertex_shader);
        free(fragment_shader);
        free(intrinsic);
        free_calibration(&storage);
        return 1;
    }
    struct walle_lg_gl_renderer_sources sources = {
        .vertex_shader            = vertex_shader,
        .regular_fragment_shader = fragment_shader,
        .float_intrinsic_table    = intrinsic,
    };
    struct walle_lg_gl_renderer* renderer = walle_lg_gl_renderer_create(&sources);
    free(vertex_shader);
    free(fragment_shader);
    free(intrinsic);
    if (renderer == nullptr) {
        destroy_gl_context(&context);
        free_calibration(&storage);
        return 1;
    }
    size_t constructor_bytes = 0;
    size_t raster_bytes      = 0;
    size_t rendered_bytes    = 0;
    for (size_t index = 0; index < sizeof cases / sizeof cases[0]; ++index) {
        if (!run_case(argv[1],
                      &cases[index],
                      &calibration,
                      renderer,
                      &constructor_bytes,
                      &raster_bytes,
                      &rendered_bytes)) {
            walle_lg_gl_renderer_destroy(renderer);
            destroy_gl_context(&context);
            free_calibration(&storage);
            return 1;
        }
    }
    walle_lg_gl_renderer_destroy(renderer);
    destroy_gl_context(&context);
    free_calibration(&storage);
    printf("transitionFrames=%zu\ncheckedConstructorBytes=%zu\ncheckedRasterBytes=%zu\n"
           "checkedRenderedBytes=%zu\n"
           "mismatchedBytes=0\nexact=true\n",
           sizeof cases / sizeof cases[0],
           constructor_bytes,
           raster_bytes,
           rendered_bytes);
    return 0;
}
