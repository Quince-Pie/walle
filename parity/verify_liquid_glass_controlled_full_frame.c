#define _POSIX_C_SOURCE 200809L
#define GL_GLEXT_PROTOTYPES 1

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/glcorearb.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "liquid_glass_gl_renderer.h"
#include "liquid_glass_gl_pyramid.h"
#include "liquid_glass_pyramid.h"

struct fixture_case
{
    uint32_t sample;
    uint32_t fraction_bits;
};

static const struct fixture_case cases[] = {
    {1, UINT32_C(0x3f77e0c0)},
    {4, UINT32_C(0x3f5fdaa0)},
    {8, UINT32_C(0x3f3f9a60)},
    {12, UINT32_C(0x3f1fd910)},
    {16, UINT32_C(0x3eff9040)},
    {20, UINT32_C(0x3ebf4960)},
    {24, UINT32_C(0x3e7eb3c0)},
    {28, UINT32_C(0x3dfdab00)},
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

static uint64_t monotonic_ns(void)
{
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0)
        return 0;
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) + (uint64_t)value.tv_nsec;
}

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
    char* result = malloc((size_t)size + 1u);
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
    EGLint    count = 0;
    if (!eglChooseConfig(display, config_attributes, &config, 1, &count) || count != 1)
        return false;
    const EGLint surface_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    EGLSurface   surface = eglCreatePbufferSurface(display, config, surface_attributes);
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
    *context = (struct gl_context){
        .display = EGL_NO_DISPLAY,
        .context = EGL_NO_CONTEXT,
        .surface = EGL_NO_SURFACE,
    };
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
#define PATH(name) (snprintf(path, sizeof path, "%s/%s", root, (name)) > 0)
    bool success = PATH("raster_p25_selector_ceil_bits.bin")
                   && load_file(path, storage->p25_ceil_bits, selector_byte_count);
#undef PATH
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

static uint8_t* bgra_to_rgba(const uint8_t* source, size_t byte_count)
{
    uint8_t* result = malloc(byte_count);
    if (result == nullptr)
        return nullptr;
    for (size_t offset = 0; offset < byte_count; offset += 4u) {
        result[offset]     = source[offset + 2u];
        result[offset + 1] = source[offset + 1u];
        result[offset + 2] = source[offset];
        result[offset + 3] = source[offset + 3u];
    }
    return result;
}

static GLuint upload_source_texture(const uint8_t* rgba, uint32_t width, uint32_t height)
{
    GLuint texture = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, (GLsizei)width, (GLsizei)height);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexSubImage2D(GL_TEXTURE_2D,
                    0,
                    0,
                    0,
                    (GLsizei)width,
                    (GLsizei)height,
                    GL_RGBA,
                    GL_UNSIGNED_BYTE,
                    rgba);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    if (glGetError() == GL_NO_ERROR)
        return texture;
    glDeleteTextures(1, &texture);
    return 0;
}

static size_t compare_reference(const uint8_t* candidate,
                                const uint8_t* reference_bgra,
                                uint32_t       width,
                                uint32_t       height,
                                uint32_t       sample,
                                const char*    stage,
                                size_t*        checked)
{
    size_t mismatches = 0;
    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            size_t        actual_offset   = ((size_t)y * width + x) * 4u;
            size_t        expected_offset = ((size_t)(height - 1u - y) * width + x) * 4u;
            const uint8_t expected[4]     = {
                reference_bgra[expected_offset + 2u],
                reference_bgra[expected_offset + 1u],
                reference_bgra[expected_offset],
                reference_bgra[expected_offset + 3u],
            };
            for (size_t channel = 0; channel < 4; ++channel) {
                ++*checked;
                if (candidate[actual_offset + channel] == expected[channel])
                    continue;
                if (mismatches < 8) {
                    fprintf(stderr,
                            "sample %02" PRIu32 " %s pixel (%" PRIu32 ",%" PRIu32
                            ") channel %zu: got %02x, expected %02x\n",
                            sample,
                            stage,
                            x,
                            y,
                            channel,
                            candidate[actual_offset + channel],
                            expected[channel]);
                }
                ++mismatches;
            }
        }
    }
    return mismatches;
}

int main(int argc, char** argv)
{
    const char* artifact_root
        = argc > 1 ? argv[1] : "artifacts/local-walle-regular-controlled-backdrop-1cd9af4-run1-v1";
    const char* calibration_root = argc > 2 ? argv[2] : "lg-test/Analysis";
    const char* vertex_path
        = argc > 3 ? argv[3] : "/tmp/walle-multipass-shaders.Ic8QQg/apple_glass_exact.vert.glsl";
    const char* fragment_path = argc > 4 ? argv[4]
                                         : "/tmp/walle-multipass-shaders.Ic8QQg/"
                                           "apple_glass_exact_regular.frag.glsl";
    const char* intrinsic_path
        = argc > 5 ? argv[5] : "artifacts/apple-float-intrinsics-r8-30556057571.bin";
    const char* compute_path
        = argc > 6 ? argv[6] : "parity/liquid_glass_backdrop.comp.glsl";
    constexpr uint32_t width       = 1024;
    constexpr uint32_t height      = 1024;
    constexpr size_t   frame_bytes = (size_t)width * height * 4u;
    char               path[4096];

    struct calibration_storage         calibration_storage = {};
    struct walle_lg_raster_calibration calibration;
    char*                              vertex_shader   = load_text_file(vertex_path);
    char*                              fragment_shader = load_text_file(fragment_path);
    char*                              compute_shader  = load_text_file(compute_path);
    uint8_t* intrinsic   = load_allocated_file(intrinsic_path, WALLE_LG_FLOAT_INTRINSIC_BYTE_COUNT);
    int      input_count = snprintf(path,
                               sizeof path,
                               "%s/transition-background-uniform-01-dynamic-backdrop-"
                                    "producer-input-0-bgra8.raw",
                               artifact_root);
    uint8_t* source      = input_count > 0 && input_count < (int)sizeof path
                               ? load_allocated_file(path, frame_bytes)
                               : nullptr;
    struct gl_context context = {
        .display = EGL_NO_DISPLAY,
        .context = EGL_NO_CONTEXT,
        .surface = EGL_NO_SURFACE,
    };
    if (vertex_shader == nullptr || fragment_shader == nullptr || compute_shader == nullptr
        || intrinsic == nullptr
        || source == nullptr
        || !load_calibration(calibration_root, &calibration_storage, &calibration)
        || !create_gl_context(&context)) {
        free(vertex_shader);
        free(fragment_shader);
        free(compute_shader);
        free(intrinsic);
        free(source);
        free_calibration(&calibration_storage);
        destroy_gl_context(&context);
        return 1;
    }
    uint8_t* source_rgba   = bgra_to_rgba(source, frame_bytes);
    GLuint   source_texture
        = source_rgba != nullptr ? upload_source_texture(source_rgba, width, height) : 0;
    free(source_rgba);
    struct walle_lg_gl_pyramid_builder* pyramid_builder
        = walle_lg_gl_pyramid_builder_create(compute_shader);
    free(compute_shader);
    struct walle_lg_gl_renderer_sources renderer_sources = {
        .vertex_shader           = vertex_shader,
        .regular_fragment_shader = fragment_shader,
        .float_intrinsic_table   = intrinsic,
    };
    struct walle_lg_gl_renderer* renderer = walle_lg_gl_renderer_create(&renderer_sources);
    free(vertex_shader);
    free(fragment_shader);
    free(intrinsic);
    if (source_texture == 0 || pyramid_builder == nullptr || renderer == nullptr) {
        if (source_texture != 0)
            glDeleteTextures(1, &source_texture);
        walle_lg_gl_pyramid_builder_destroy(pyramid_builder);
        walle_lg_gl_renderer_destroy(renderer);
        free(source);
        free_calibration(&calibration_storage);
        destroy_gl_context(&context);
        return 1;
    }

    uint8_t* destination    = calloc(1, frame_bytes);
    uint8_t* candidate      = malloc(frame_bytes);
    GLuint   destination_texture
        = destination != nullptr ? upload_source_texture(destination, width, height) : 0;
    size_t   prefix_checked = 0;
    size_t   final_checked  = 0;
    size_t   mismatches     = 0;
    size_t   rendered       = 0;
    uint64_t construct_ns   = 0;
    uint64_t raster_ns      = 0;
    uint64_t backdrop_ns    = 0;
    uint64_t prefix_ns      = 0;
    uint64_t final_ns       = 0;
    for (size_t case_index = 0; destination != nullptr && candidate != nullptr
                                && destination_texture != 0
                                && case_index < sizeof cases / sizeof cases[0];
         ++case_index) {
        const struct fixture_case* fixture = &cases[case_index];
        float                      fraction;
        memcpy(&fraction, &fixture->fraction_bits, sizeof fraction);
        struct walle_lg_transition_frame_request request = {
            .material             = WALLE_LG_MATERIAL_REGULAR,
            .appearance           = WALLE_LG_APPEARANCE_DARK,
            .window_width         = width,
            .window_height        = height,
            .diameter             = 480,
            .center_x             = 512.0,
            .center_y             = 512.0,
            .visible_fraction     = fraction,
            .sdf_enclosure_radius = 0x1.53b608p+5,
        };
        struct walle_lg_transition_frame         frame;
        struct walle_lg_raster_tables            raster      = {};
        uint64_t                                 stage_start = monotonic_ns();
        bool constructed = walle_lg_transition_frame_construct(&request, &frame);
        construct_ns += monotonic_ns() - stage_start;
        stage_start = monotonic_ns();
        bool raster_built
            = constructed
              && walle_lg_raster_tables_construct(&frame, width, height, &calibration, &raster);
        raster_ns += monotonic_ns() - stage_start;
        stage_start = monotonic_ns();
        bool backdrop_built
            = raster_built
              && walle_lg_gl_pyramid_builder_build(pyramid_builder,
                                                   source_texture,
                                                   width,
                                                   height,
                                                   &frame,
                                                   &calibration);
        backdrop_ns += monotonic_ns() - stage_start;
        if (!backdrop_built) {
            fprintf(
                stderr, "sample %02" PRIu32 ": exact input construction failed\n", fixture->sample);
            ++mismatches;
            walle_lg_raster_tables_destroy(&raster);
            continue;
        }
        struct walle_lg_rgba8_image destination_image = {
            .width  = width,
            .height = height,
            .pixels = nullptr,
        };
        struct walle_lg_gl_frame gl_frame = {
            .transition       = &frame,
            .raster           = &raster,
            .destination      = destination_image,
            .destination_texture = destination_texture,
            .source_texture   = walle_lg_gl_pyramid_builder_texture(pyramid_builder),
            .source_texture_width = frame.selected_region.allocated_extent[0],
            .source_texture_height = frame.selected_region.allocated_extent[1],
            .source_mip_count = frame.selected_region.level_count,
        };
        int      prefix_count     = snprintf(path,
                                    sizeof path,
                                    "%s/transition-background-uniform-%02" PRIu32
                                    "-glass-prefix-reference-bgra8.raw",
                                    artifact_root,
                                    fixture->sample);
        uint8_t* prefix_reference = prefix_count > 0 && prefix_count < (int)sizeof path
                                        ? load_allocated_file(path, frame_bytes)
                                        : nullptr;
        int      reference_count  = snprintf(path,
                                       sizeof path,
                                       "%s/transition-background-uniform-%02" PRIu32 "-bgra8.raw",
                                       artifact_root,
                                       fixture->sample);
        uint8_t* reference        = reference_count > 0 && reference_count < (int)sizeof path
                                        ? load_allocated_file(path, frame_bytes)
                                        : nullptr;
        stage_start               = monotonic_ns();
        bool prefix_rendered      = prefix_reference != nullptr
                               && walle_lg_gl_renderer_render_prefix(renderer, &gl_frame)
                               && walle_lg_gl_renderer_read_rgba8(renderer, candidate, frame_bytes);
        prefix_ns += monotonic_ns() - stage_start;
        if (prefix_rendered) {
            size_t prefix_mismatches = compare_reference(candidate,
                                                         prefix_reference,
                                                         width,
                                                         height,
                                                         fixture->sample,
                                                         "prefix",
                                                         &prefix_checked);
            printf("sample%02" PRIu32 "PrefixMismatchedBytes=%zu\n",
                   fixture->sample,
                   prefix_mismatches);
            mismatches += prefix_mismatches;
        } else {
            fprintf(stderr, "sample %02" PRIu32 ": exact GL prefix failed\n", fixture->sample);
            ++mismatches;
        }
        stage_start         = monotonic_ns();
        bool final_rendered = reference != nullptr
                              && walle_lg_gl_renderer_render(renderer, &gl_frame)
                              && walle_lg_gl_renderer_read_rgba8(renderer, candidate, frame_bytes);
        final_ns += monotonic_ns() - stage_start;
        if (!final_rendered) {
            fprintf(stderr, "sample %02" PRIu32 ": exact GL render failed\n", fixture->sample);
            ++mismatches;
        } else {
            size_t final_mismatches = compare_reference(
                candidate, reference, width, height, fixture->sample, "final", &final_checked);
            printf(
                "sample%02" PRIu32 "FinalMismatchedBytes=%zu\n", fixture->sample, final_mismatches);
            mismatches += final_mismatches;
            ++rendered;
        }
        free(prefix_reference);
        free(reference);
        walle_lg_raster_tables_destroy(&raster);
    }

    free(destination);
    free(candidate);
    if (destination_texture != 0)
        glDeleteTextures(1, &destination_texture);
    walle_lg_gl_renderer_destroy(renderer);
    walle_lg_gl_pyramid_builder_destroy(pyramid_builder);
    glDeleteTextures(1, &source_texture);
    free(source);
    free_calibration(&calibration_storage);
    destroy_gl_context(&context);
    printf("controlledFullFrameCases=%zu\n", rendered);
    printf("checkedPrefixBytes=%zu\n", prefix_checked);
    printf("checkedRenderedBytes=%zu\n", final_checked);
    printf("mismatchedBytes=%zu\n", mismatches);
    printf("constructTotalNs=%" PRIu64 "\n", construct_ns);
    printf("rasterTotalNs=%" PRIu64 "\n", raster_ns);
    printf("gpuBackdropHostTotalNs=%" PRIu64 "\n", backdrop_ns);
    printf("prefixRenderReadbackTotalNs=%" PRIu64 "\n", prefix_ns);
    printf("finalRenderReadbackTotalNs=%" PRIu64 "\n", final_ns);
    printf("exact=%s\n",
           mismatches == 0 && rendered == sizeof cases / sizeof cases[0] ? "true" : "false");
    return mismatches == 0 && rendered == sizeof cases / sizeof cases[0] ? 0 : 1;
}
