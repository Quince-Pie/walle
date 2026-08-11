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
#include <zlib.h>

#include "liquid_glass_gl_pyramid.h"
#include "liquid_glass_pyramid.h"

struct fixture_case
{
    uint32_t sample;
    uint32_t fraction_bits;
    uint32_t texture_index;
    uint32_t base_width;
    uint32_t base_height;
};

static const struct fixture_case cases[] = {
    {1, UINT32_C(0x3f77e0c0), 0, 512, 512},
    {4, UINT32_C(0x3f5fdaa0), 0, 640, 512},
    {8, UINT32_C(0x3f3f9a60), 0, 640, 640},
    {12, UINT32_C(0x3f1fd910), 1, 640, 768},
    {16, UINT32_C(0x3eff9040), 1, 768, 768},
    {20, UINT32_C(0x3ebf4960), 1, 896, 768},
    {24, UINT32_C(0x3e7eb3c0), 1, 768, 768},
    {28, UINT32_C(0x3dfdab00), 1, 704, 704},
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

static bool load_compressed(const char* path, void* destination, size_t size)
{
    FILE* stream = fopen(path, "rb");
    if (stream == nullptr) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return false;
    }
    if (fseek(stream, 0, SEEK_END) != 0) {
        fclose(stream);
        return false;
    }
    long compressed_size = ftell(stream);
    if (compressed_size <= 0 || fseek(stream, 0, SEEK_SET) != 0) {
        fclose(stream);
        return false;
    }
    uint8_t* compressed = malloc((size_t)compressed_size);
    if (compressed == nullptr
        || fread(compressed, 1, (size_t)compressed_size, stream) != (size_t)compressed_size
        || fclose(stream) != 0) {
        free(compressed);
        return false;
    }
    uLongf output_size = size;
    int    status      = uncompress(destination, &output_size, compressed, (uLong)compressed_size);
    free(compressed);
    return status == Z_OK && output_size == size;
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

static bool has_gl_extension(const char* expected)
{
    GLint count = 0;
    glGetIntegerv(GL_NUM_EXTENSIONS, &count);
    for (GLint index = 0; index < count; ++index) {
        const char* extension = (const char*)glGetStringi(GL_EXTENSIONS, (GLuint)index);
        if (extension != nullptr && strcmp(extension, expected) == 0)
            return true;
    }
    return false;
}

static bool artifact_path(char        result[static 4096],
                          const char* root,
                          uint32_t    sample,
                          uint32_t    texture_index,
                          uint32_t    width,
                          uint32_t    height,
                          uint32_t    level)
{
    int count;
    if (level == 0) {
        count = snprintf(result,
                         4096,
                         "%s/sdf-generator-transition-background-uniform-%02" PRIu32
                         "-texture-%03" PRIu32 "-pf80-%" PRIu32 "x%" PRIu32 ".raw",
                         root,
                         sample,
                         texture_index,
                         width,
                         height);
    } else {
        count = snprintf(result,
                         4096,
                         "%s/sdf-generator-transition-background-uniform-%02" PRIu32
                         "-texture-%03" PRIu32 "-pf80-%" PRIu32 "x%" PRIu32
                         "-mip-%02" PRIu32 ".raw",
                         root,
                         sample,
                         texture_index,
                         width,
                         height,
                         level);
    }
    return count >= 0 && count < 4096;
}

static size_t compare_rgba_to_bgra(const uint8_t* candidate,
                                   const uint8_t* reference,
                                   size_t         byte_count,
                                   uint32_t       sample,
                                   uint32_t       level)
{
    size_t mismatches = 0;
    for (size_t offset = 0; offset < byte_count; offset += 4u) {
        const uint8_t expected[] = {
            reference[offset + 2u],
            reference[offset + 1u],
            reference[offset],
            reference[offset + 3u],
        };
        for (size_t channel = 0; channel < 4; ++channel) {
            if (candidate[offset + channel] == expected[channel])
                continue;
            if (mismatches < 8) {
                fprintf(stderr,
                        "sample %02" PRIu32 " mip %" PRIu32 " byte %zu: got %02x, expected %02x\n",
                        sample,
                        level,
                        offset + channel,
                        candidate[offset + channel],
                        expected[channel]);
            }
            ++mismatches;
        }
    }
    return mismatches;
}

int main(int argc, char** argv)
{
    const char* artifact_root
        = argc > 1 ? argv[1] : "artifacts/local-walle-regular-controlled-backdrop-1cd9af4-run1-v1";
    const char* calibration_root = argc > 2 ? argv[2] : "lg-test/Analysis";
    const char* shader_path
        = argc > 3 ? argv[3] : "parity/liquid_glass_backdrop.comp.glsl";
    constexpr uint32_t source_width   = 1024;
    constexpr uint32_t source_height  = 1024;
    constexpr size_t   source_bytes   = (size_t)source_width * source_height * 4u;
    constexpr size_t   selector_count = 2'097'153;
    char               path[4096];

    uint32_t* selectors = malloc(selector_count * sizeof(*selectors));
    int selector_path_count
        = snprintf(path,
                   sizeof path,
                   "%s/raster_fractional_subpixel_resolved_selectors.zlib",
                   calibration_root);
    int source_path_count
        = snprintf(path,
                   sizeof path,
                   "%s/transition-background-uniform-01-dynamic-backdrop-producer-input-0-bgra8.raw",
                   artifact_root);
    uint8_t* source_bgra = source_path_count > 0 && source_path_count < (int)sizeof path
                               ? load_allocated_file(path, source_bytes)
                               : nullptr;
    char*    shader      = load_text_file(shader_path);
    if (selectors == nullptr || selector_path_count <= 0
        || selector_path_count >= (int)sizeof path) {
        free(selectors);
        free(source_bgra);
        free(shader);
        return 1;
    }
    snprintf(path,
             sizeof path,
             "%s/raster_fractional_subpixel_resolved_selectors.zlib",
             calibration_root);
    if (source_bgra == nullptr || shader == nullptr
        || !load_compressed(path, selectors, selector_count * sizeof(*selectors))) {
        free(selectors);
        free(source_bgra);
        free(shader);
        return 1;
    }
    uint8_t* source_rgba = bgra_to_rgba(source_bgra, source_bytes);
    struct walle_lg_raster_calibration calibration = {
        .base_selectors      = selectors,
        .base_selector_count = selector_count,
    };
    struct gl_context context = {
        .display = EGL_NO_DISPLAY,
        .context = EGL_NO_CONTEXT,
        .surface = EGL_NO_SURFACE,
    };
    if (source_rgba == nullptr || !create_gl_context(&context)) {
        free(selectors);
        free(source_bgra);
        free(source_rgba);
        free(shader);
        destroy_gl_context(&context);
        return 1;
    }
    fprintf(stderr, "OpenGL renderer: %s\n", glGetString(GL_RENDERER));
    fprintf(stderr, "OpenGL version: %s\n", glGetString(GL_VERSION));
    fprintf(stderr,
            "GL_EXT_shader_explicit_arithmetic_types_float16: %s\n",
            has_gl_extension("GL_EXT_shader_explicit_arithmetic_types_float16") ? "yes" : "no");
    fprintf(stderr,
            "GL_AMD_gpu_shader_half_float: %s\n",
            has_gl_extension("GL_AMD_gpu_shader_half_float") ? "yes" : "no");
    GLuint source_texture = upload_source_texture(source_rgba, source_width, source_height);
    struct walle_lg_gl_pyramid_builder* builder
        = walle_lg_gl_pyramid_builder_create(shader);
    free(shader);
    if (source_texture == 0 || builder == nullptr) {
        if (source_texture != 0)
            glDeleteTextures(1, &source_texture);
        walle_lg_gl_pyramid_builder_destroy(builder);
        free(selectors);
        free(source_bgra);
        free(source_rgba);
        destroy_gl_context(&context);
        return 1;
    }

    size_t   checked_bytes     = 0;
    size_t   mismatches        = 0;
    size_t   built_cases       = 0;
    size_t   peak_cpu_bytes    = 0;
    size_t   peak_gpu_bytes    = 0;
    uint64_t cpu_total_ns      = 0;
    uint64_t gpu_total_ns      = 0;
    uint64_t gpu_host_total_ns = 0;
    for (size_t case_index = 0; case_index < sizeof cases / sizeof cases[0]; ++case_index) {
        const struct fixture_case* fixture = &cases[case_index];
        float fraction;
        memcpy(&fraction, &fixture->fraction_bits, sizeof fraction);
        struct walle_lg_transition_frame_request request = {
            .material             = WALLE_LG_MATERIAL_REGULAR,
            .appearance           = WALLE_LG_APPEARANCE_DARK,
            .window_width         = source_width,
            .window_height        = source_height,
            .diameter             = 480,
            .center_x             = 512.0,
            .center_y             = 512.0,
            .visible_fraction     = fraction,
            .sdf_enclosure_radius = 0x1.53b608p+5,
        };
        struct walle_lg_transition_frame frame;
        if (!walle_lg_transition_frame_construct(&request, &frame)) {
            fprintf(stderr, "sample %02" PRIu32 ": frame construction failed\n", fixture->sample);
            ++mismatches;
            continue;
        }

        struct walle_lg_dynamic_regular_backdrop cpu_backdrop = {};
        uint64_t cpu_start = monotonic_ns();
        bool cpu_built = walle_lg_build_dynamic_regular_backdrop(source_bgra,
                                                                 source_bytes,
                                                                 source_width,
                                                                 source_height,
                                                                 &frame,
                                                                 &calibration,
                                                                 &cpu_backdrop);
        uint64_t cpu_ns = monotonic_ns() - cpu_start;
        cpu_total_ns += cpu_ns;
        if (!cpu_built) {
            fprintf(stderr, "sample %02" PRIu32 ": CPU backdrop failed\n", fixture->sample);
            ++mismatches;
            continue;
        }
        size_t cpu_bytes = cpu_backdrop.producer.byte_count;
        for (uint32_t level = 0; level < cpu_backdrop.pyramid.level_count; ++level)
            cpu_bytes += cpu_backdrop.pyramid.levels[level].byte_count;
        if (cpu_bytes > peak_cpu_bytes)
            peak_cpu_bytes = cpu_bytes;

        GLuint query = 0;
        glGenQueries(1, &query);
        glFinish();
        uint64_t host_start = monotonic_ns();
        glBeginQuery(GL_TIME_ELAPSED, query);
        bool gpu_built = walle_lg_gl_pyramid_builder_build(builder,
                                                           source_texture,
                                                           source_width,
                                                           source_height,
                                                           &frame,
                                                           &calibration);
        glEndQuery(GL_TIME_ELAPSED);
        GLuint64 gpu_ns = 0;
        glGetQueryObjectui64v(query, GL_QUERY_RESULT, &gpu_ns);
        uint64_t host_ns = monotonic_ns() - host_start;
        glDeleteQueries(1, &query);
        gpu_total_ns += gpu_ns;
        gpu_host_total_ns += host_ns;
        printf("sample%02" PRIu32 "CpuBackdropNs=%" PRIu64 "\n", fixture->sample, cpu_ns);
        printf("sample%02" PRIu32 "GpuBackdropNs=%" PRIu64 "\n",
               fixture->sample,
               (uint64_t)gpu_ns);
        printf("sample%02" PRIu32 "GpuHostCompletionNs=%" PRIu64 "\n",
               fixture->sample,
               host_ns);
        if (!gpu_built) {
            fprintf(stderr, "sample %02" PRIu32 ": GPU backdrop failed\n", fixture->sample);
            ++mismatches;
            walle_lg_destroy_dynamic_regular_backdrop(&cpu_backdrop);
            continue;
        }

        size_t case_gpu_bytes = 0;
        size_t case_mismatches = 0;
        for (uint32_t level = 0; level < frame.selected_region.level_count; ++level) {
            uint32_t level_width  = fixture->base_width >> level;
            uint32_t level_height = fixture->base_height >> level;
            if (level_width == 0)
                level_width = 1;
            if (level_height == 0)
                level_height = 1;
            size_t level_bytes = (size_t)level_width * level_height * 4u;
            case_gpu_bytes += level_bytes;
            if (!artifact_path(path,
                               artifact_root,
                               fixture->sample,
                               fixture->texture_index,
                               fixture->base_width,
                               fixture->base_height,
                               level)) {
                ++case_mismatches;
                break;
            }
            uint8_t* expected  = load_allocated_file(path, level_bytes);
            uint8_t* candidate = malloc(level_bytes);
            if (expected == nullptr || candidate == nullptr
                || !walle_lg_gl_pyramid_builder_read_rgba8(
                    builder, level, candidate, level_bytes)) {
                free(expected);
                free(candidate);
                ++case_mismatches;
                break;
            }
            case_mismatches += compare_rgba_to_bgra(
                candidate, expected, level_bytes, fixture->sample, level);
            checked_bytes += level_bytes;
            free(expected);
            free(candidate);
        }
        if (case_gpu_bytes > peak_gpu_bytes)
            peak_gpu_bytes = case_gpu_bytes;
        printf("sample%02" PRIu32 "MismatchedBytes=%zu\n",
               fixture->sample,
               case_mismatches);
        mismatches += case_mismatches;
        if (case_mismatches == 0)
            ++built_cases;
        walle_lg_destroy_dynamic_regular_backdrop(&cpu_backdrop);
    }

    walle_lg_gl_pyramid_builder_destroy(builder);
    glDeleteTextures(1, &source_texture);
    free(selectors);
    free(source_bgra);
    free(source_rgba);
    destroy_gl_context(&context);
    printf("glBackdropCases=%zu\n", built_cases);
    printf("checkedPyramidBytes=%zu\n", checked_bytes);
    printf("mismatchedBytes=%zu\n", mismatches);
    printf("cpuBackdropTotalNs=%" PRIu64 "\n", cpu_total_ns);
    printf("gpuBackdropTotalNs=%" PRIu64 "\n", gpu_total_ns);
    printf("gpuHostCompletionTotalNs=%" PRIu64 "\n", gpu_host_total_ns);
    printf("peakCpuBackdropBytes=%zu\n", peak_cpu_bytes);
    printf("peakGpuBackdropBytes=%zu\n", peak_gpu_bytes);
    printf("exact=%s\n",
           mismatches == 0 && built_cases == sizeof cases / sizeof cases[0] ? "true" : "false");
    return mismatches == 0 && built_cases == sizeof cases / sizeof cases[0] ? 0 : 1;
}
