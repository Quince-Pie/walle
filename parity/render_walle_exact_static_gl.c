#include <EGL/egl.h>
#include <EGL/eglext.h>
#define GL_GLEXT_PROTOTYPES 1
#include <GL/glcorearb.h>
#include <errno.h>
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include <wayland-egl.h>

#include "protocols/xdg-shell.h"
#include "render_walle_exact_static_gl.h"

constexpr size_t DEVICE_IDENTITY_CAPACITY = 256;
constexpr size_t PATH_CAPACITY            = 4096;
constexpr size_t PROFILE_BYTES            = 258;
constexpr size_t HIGHLIGHT_UNIFORM_BYTES  = 248;
constexpr size_t SHADOW_COEFFICIENT_BYTES = 16u * 32u * 4u * sizeof(uint32_t);
constexpr size_t SHADOW_SLOPE_BYTES       = 8u * 4u * sizeof(uint32_t);
constexpr uint32_t MAX_GATE_VERTICES      = 1u << 20;
constexpr uint32_t MAX_GATE_INDICES       = 1u << 22;

struct source_file
{
    uint8_t* data;
    size_t   size;
};

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
    int32_t  highlight_derivative_mode;
    int32_t  highlight_coordinate_mode;
    int32_t  highlight_alpha_ulp_bias;
    int32_t  highlight_float_division_mode;
    int32_t  highlight_coverage_arithmetic_mode;
    int32_t  highlight_mix_mode;
    int32_t  highlight_band_mode;
    int32_t  highlight_normalize_mode;
    int32_t  highlight_normalized_coordinate_mode;
    int32_t  highlight_sdf_arithmetic_mode;
    int32_t  highlight_sdf_squared_ulp_bias;
    int32_t  highlight_sdf_distance_ulp_bias;
    int32_t  highlight_source_division_mode;
    int32_t  highlight_source_construction_mode;
    int32_t  highlight_destination_division_mode;
    uint32_t use_apple_half_intrinsic_table;
};

struct gate_config_v2
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
};

struct gate_config_v1
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
};

struct geometry
{
    GLuint  vao;
    GLuint  vertex_buffer;
    GLuint  index_buffer;
    GLsizei count;
    bool    indexed;
};

struct gl_context
{
    EGLDisplay            display;
    EGLContext            context;
    EGLSurface            surface;
    struct wl_display*    wayland_display;
    struct wl_registry*   registry;
    struct wl_compositor* compositor;
    struct wl_surface*    wayland_surface;
    struct xdg_wm_base*   wm_base;
    struct xdg_surface*   xdg_surface;
    struct xdg_toplevel*  toplevel;
    struct wl_egl_window* egl_window;
    bool                  wayland;
    bool                  configured;
    bool                  closed;
    bool                  owns_resources;
};

struct external_context
{
    EGLDisplay         display;
    EGLSurface         surface;
    struct wl_display* wayland_display;
    bool               active;
};

static struct external_context external_context;

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(_Float16) == 2);
static_assert(sizeof(struct gate_config_v1) == 52);
static_assert(sizeof(struct gate_config_v2) == 100);
static_assert(sizeof(struct gate_config) == 164);
static_assert(offsetof(struct gate_config, source_width) == sizeof(struct gate_config_v1));
static_assert(offsetof(struct gate_config, highlight_derivative_mode)
              == sizeof(struct gate_config_v2));

[[nodiscard]]
static bool parse_device_index(const char* text, EGLint* result)
{
    char* end  = nullptr;
    errno      = 0;
    long value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < 0 || value > INT_MAX)
        return false;
    *result = (EGLint)value;
    return true;
}

[[nodiscard]]
static struct source_file read_file(const char* path, size_t expected_size)
{
    struct source_file result = {};
    FILE*              stream = fopen(path, "rb");
    if (!stream) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return result;
    }
    if (fseek(stream, 0, SEEK_END) != 0) {
        fprintf(stderr, "%s: cannot seek: %s\n", path, strerror(errno));
        fclose(stream);
        return result;
    }
    long length = ftell(stream);
    if (length < 0 || (expected_size != 0 && (size_t)length != expected_size)
        || fseek(stream, 0, SEEK_SET) != 0) {
        fprintf(stderr, "%s: invalid byte count %ld (expected %zu)\n", path, length, expected_size);
        fclose(stream);
        return result;
    }
    result.size = (size_t)length;
    result.data = malloc(result.size + 1);
    if (!result.data) {
        fprintf(stderr, "%s: allocation failed\n", path);
        fclose(stream);
        return (struct source_file){};
    }
    if (fread(result.data, 1, result.size, stream) != result.size) {
        fprintf(stderr, "%s: incomplete read\n", path);
        free(result.data);
        fclose(stream);
        return (struct source_file){};
    }
    result.data[result.size] = 0;
    fclose(stream);
    return result;
}

[[nodiscard]]
static struct source_file
read_fixture_file(const char* directory, const char* name, size_t expected_size)
{
    char path[PATH_CAPACITY];
    int  length = snprintf(path, sizeof path, "%s/%s", directory, name);
    if (length < 0 || (size_t)length >= sizeof path) {
        fprintf(stderr, "fixture path is too long: %s/%s\n", directory, name);
        return (struct source_file){};
    }
    return read_file(path, expected_size);
}

[[nodiscard]]
static struct source_file
read_optional_fixture_file(const char* directory, const char* name, size_t expected_size)
{
    char path[PATH_CAPACITY];
    int  length = snprintf(path, sizeof path, "%s/%s", directory, name);
    if (length < 0 || (size_t)length >= sizeof path) {
        fprintf(stderr, "fixture path is too long: %s/%s\n", directory, name);
        return (struct source_file){};
    }
    FILE* stream = fopen(path, "rb");
    if (!stream) {
        if (errno != ENOENT)
            fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return (struct source_file){};
    }
    fclose(stream);
    return read_file(path, expected_size);
}

[[nodiscard]]
static bool write_exact_file(const char* path, const void* data, size_t size)
{
    FILE* stream = fopen(path, "wb");
    if (!stream) {
        fprintf(stderr, "%s: cannot open for writing: %s\n", path, strerror(errno));
        return false;
    }
    bool complete = fwrite(data, 1, size, stream) == size;
    if (fclose(stream) != 0)
        complete = false;
    if (!complete)
        fprintf(stderr, "%s: incomplete write\n", path);
    return complete;
}

static void print_shader_log(GLuint shader, const char* stage)
{
    GLint size = 0;
    glGetShaderiv(shader, GL_INFO_LOG_LENGTH, &size);
    if (size <= 1)
        return;
    char* log = malloc((size_t)size);
    if (!log)
        return;
    glGetShaderInfoLog(shader, size, nullptr, log);
    fprintf(stderr, "%s shader log:\n%s\n", stage, log);
    free(log);
}

[[nodiscard]]
static GLuint compile_shader(GLenum type, const char* source, const char* stage)
{
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint compiled = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    print_shader_log(shader, stage);
    if (compiled == GL_TRUE)
        return shader;
    glDeleteShader(shader);
    return 0;
}

[[nodiscard]]
static GLuint link_program(const char* vertex_source, const char* fragment_source)
{
    GLuint vertex   = compile_shader(GL_VERTEX_SHADER, vertex_source, "vertex");
    GLuint fragment = compile_shader(GL_FRAGMENT_SHADER, fragment_source, "fragment");
    if (!vertex || !fragment) {
        if (vertex)
            glDeleteShader(vertex);
        if (fragment)
            glDeleteShader(fragment);
        return 0;
    }
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glLinkProgram(program);
    glDeleteShader(vertex);
    glDeleteShader(fragment);
    GLint linked = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked == GL_FALSE) {
        GLint size = 0;
        glGetProgramiv(program, GL_INFO_LOG_LENGTH, &size);
        char* log = size > 1 ? malloc((size_t)size) : nullptr;
        if (log) {
            glGetProgramInfoLog(program, size, nullptr, log);
            fprintf(stderr, "program link log:\n%s\n", log);
            free(log);
        }
        glDeleteProgram(program);
        return 0;
    }
    return program;
}

[[nodiscard]]
static EGLDisplay device_display(EGLint index, char* identity, size_t identity_size)
{
    auto query_devices = (PFNEGLQUERYDEVICESEXTPROC)eglGetProcAddress("eglQueryDevicesEXT");
    auto get_platform_display
        = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    auto query_device_string
        = (PFNEGLQUERYDEVICESTRINGEXTPROC)eglGetProcAddress("eglQueryDeviceStringEXT");
    if (!query_devices || !get_platform_display)
        return EGL_NO_DISPLAY;
    EGLDeviceEXT devices[16];
    EGLint       count = 0;
    if (index < 0
        || !query_devices((EGLint)(sizeof(devices) / sizeof(devices[0])), devices, &count)
        || index >= count) {
        fprintf(stderr, "EGL device %d is unavailable; enumerated %d\n", index, count);
        return EGL_NO_DISPLAY;
    }
    const char* node = query_device_string
                           ? query_device_string(devices[index], EGL_DRM_RENDER_NODE_FILE_EXT)
                           : nullptr;
    snprintf(identity, identity_size, "%s", node ? node : "unknown-render-node");
    return get_platform_display(EGL_PLATFORM_DEVICE_EXT, devices[index], nullptr);
}

static void registry_global(void*               data,
                            struct wl_registry* registry,
                            uint32_t            name,
                            const char*         interface,
                            uint32_t            version)
{
    struct gl_context* context = data;
    if (strcmp(interface, wl_compositor_interface.name) == 0) {
        uint32_t admitted_version = version < 4 ? version : 4;
        context->compositor
            = wl_registry_bind(registry, name, &wl_compositor_interface, admitted_version);
    } else if (strcmp(interface, xdg_wm_base_interface.name) == 0) {
        context->wm_base = wl_registry_bind(registry, name, &xdg_wm_base_interface, 1);
    }
}

static void registry_global_remove(void* data, struct wl_registry* registry, uint32_t name)
{
    (void)data;
    (void)registry;
    (void)name;
}

static const struct wl_registry_listener registry_listener = {
    .global        = registry_global,
    .global_remove = registry_global_remove,
};

static void wm_base_ping(void* data, struct xdg_wm_base* wm_base, uint32_t serial)
{
    (void)data;
    xdg_wm_base_pong(wm_base, serial);
}

static const struct xdg_wm_base_listener wm_base_listener = {
    .ping = wm_base_ping,
};

static void surface_configure(void* data, struct xdg_surface* surface, uint32_t serial)
{
    struct gl_context* context = data;
    xdg_surface_ack_configure(surface, serial);
    context->configured = true;
}

static const struct xdg_surface_listener surface_listener = {
    .configure = surface_configure,
};

static void toplevel_configure(void*                data,
                               struct xdg_toplevel* toplevel,
                               int32_t              width,
                               int32_t              height,
                               struct wl_array*     states)
{
    (void)data;
    (void)toplevel;
    (void)width;
    (void)height;
    (void)states;
}

static void toplevel_close(void* data, struct xdg_toplevel* toplevel)
{
    struct gl_context* context = data;
    (void)toplevel;
    context->closed = true;
}

static const struct xdg_toplevel_listener toplevel_listener = {
    .configure = toplevel_configure,
    .close     = toplevel_close,
};

static void destroy_context(struct gl_context* context);

[[nodiscard]]
static bool create_context(EGLint device_index, struct gl_context* result)
{
    char       identity[DEVICE_IDENTITY_CAPACITY] = {};
    EGLDisplay display = device_display(device_index, identity, sizeof identity);
    if (display == EGL_NO_DISPLAY) {
        fprintf(stderr, "cannot acquire EGL device display (0x%04x)\n", eglGetError());
        return false;
    }
    EGLint egl_major = 0;
    EGLint egl_minor = 0;
    if (!eglInitialize(display, &egl_major, &egl_minor)) {
        fprintf(stderr, "eglInitialize failed (0x%04x)\n", eglGetError());
        return false;
    }
    const EGLint config_attributes[] = {
        EGL_SURFACE_TYPE,
        EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE,
        EGL_OPENGL_BIT,
        EGL_RED_SIZE,
        8,
        EGL_GREEN_SIZE,
        8,
        EGL_BLUE_SIZE,
        8,
        EGL_ALPHA_SIZE,
        8,
        EGL_NONE,
    };
    EGLConfig config       = nullptr;
    EGLint    config_count = 0;
    if (!eglChooseConfig(display, config_attributes, &config, 1, &config_count) || config_count != 1
        || !eglBindAPI(EGL_OPENGL_API)) {
        fprintf(stderr, "cannot select an RGBA8 OpenGL EGL config (0x%04x)\n", eglGetError());
        eglTerminate(display);
        return false;
    }
    const EGLint context_attributes[] = {
        EGL_CONTEXT_MAJOR_VERSION_KHR,
        4,
        EGL_CONTEXT_MINOR_VERSION_KHR,
        5,
        EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR,
        EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,
        EGL_NONE,
    };
    EGLContext   context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    const EGLint surface_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    EGLSurface   surface = eglCreatePbufferSurface(display, config, surface_attributes);
    if (context == EGL_NO_CONTEXT || surface == EGL_NO_SURFACE
        || !eglMakeCurrent(display, surface, surface, context)) {
        fprintf(stderr, "cannot create an OpenGL 4.5 core context (0x%04x)\n", eglGetError());
        if (surface != EGL_NO_SURFACE)
            eglDestroySurface(display, surface);
        if (context != EGL_NO_CONTEXT)
            eglDestroyContext(display, context);
        eglTerminate(display);
        return false;
    }
    printf("device=%s\nGL_VENDOR=%s\nGL_RENDERER=%s\nGL_VERSION=%s\n",
           identity,
           glGetString(GL_VENDOR),
           glGetString(GL_RENDERER),
           glGetString(GL_VERSION));
    *result = (struct gl_context){
        .display        = display,
        .context        = context,
        .surface        = surface,
        .owns_resources = true,
    };
    return true;
}

[[nodiscard]]
static bool create_wayland_context(const char*        display_name,
                                   uint32_t           width,
                                   uint32_t           height,
                                   struct gl_context* result)
{
    *result = (struct gl_context){
        .display        = EGL_NO_DISPLAY,
        .context        = EGL_NO_CONTEXT,
        .surface        = EGL_NO_SURFACE,
        .wayland        = true,
        .owns_resources = true,
    };
    result->wayland_display = wl_display_connect(display_name);
    if (!result->wayland_display) {
        fprintf(stderr, "cannot connect to Wayland display %s\n", display_name);
        return false;
    }
    result->registry = wl_display_get_registry(result->wayland_display);
    wl_registry_add_listener(result->registry, &registry_listener, result);
    if (wl_display_roundtrip(result->wayland_display) < 0 || !result->compositor
        || !result->wm_base) {
        fprintf(stderr, "Wayland compositor or xdg-shell is unavailable\n");
        destroy_context(result);
        return false;
    }
    xdg_wm_base_add_listener(result->wm_base, &wm_base_listener, result);
    result->wayland_surface = wl_compositor_create_surface(result->compositor);
    result->xdg_surface     = xdg_wm_base_get_xdg_surface(result->wm_base, result->wayland_surface);
    xdg_surface_add_listener(result->xdg_surface, &surface_listener, result);
    result->toplevel = xdg_surface_get_toplevel(result->xdg_surface);
    xdg_toplevel_add_listener(result->toplevel, &toplevel_listener, result);
    xdg_toplevel_set_title(result->toplevel, "Walle exact static parity gate");
    xdg_toplevel_set_app_id(result->toplevel, "walle-exact-static-gate");
    wl_surface_commit(result->wayland_surface);
    for (int attempt = 0; attempt < 4 && !result->configured; ++attempt) {
        if (wl_display_roundtrip(result->wayland_display) < 0)
            break;
    }
    if (!result->configured || result->closed) {
        fprintf(stderr, "Wayland surface was not configured\n");
        destroy_context(result);
        return false;
    }
    result->egl_window = wl_egl_window_create(result->wayland_surface, (int)width, (int)height);
    auto get_platform_display
        = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    result->display
        = get_platform_display
              ? get_platform_display(EGL_PLATFORM_WAYLAND_KHR, result->wayland_display, nullptr)
              : eglGetDisplay((EGLNativeDisplayType)result->wayland_display);
    EGLint egl_major = 0;
    EGLint egl_minor = 0;
    if (result->display == EGL_NO_DISPLAY
        || !eglInitialize(result->display, &egl_major, &egl_minor)) {
        fprintf(stderr, "Wayland eglInitialize failed (0x%04x)\n", eglGetError());
        destroy_context(result);
        return false;
    }
    const EGLint config_attributes[] = {
        EGL_SURFACE_TYPE,
        EGL_WINDOW_BIT,
        EGL_RENDERABLE_TYPE,
        EGL_OPENGL_BIT,
        EGL_RED_SIZE,
        8,
        EGL_GREEN_SIZE,
        8,
        EGL_BLUE_SIZE,
        8,
        EGL_ALPHA_SIZE,
        8,
        EGL_NONE,
    };
    EGLConfig config       = nullptr;
    EGLint    config_count = 0;
    if (!eglChooseConfig(result->display, config_attributes, &config, 1, &config_count)
        || config_count != 1 || !eglBindAPI(EGL_OPENGL_API)) {
        fprintf(stderr, "cannot select a Wayland OpenGL config (0x%04x)\n", eglGetError());
        destroy_context(result);
        return false;
    }
    const EGLint context_attributes[] = {
        EGL_CONTEXT_MAJOR_VERSION_KHR,
        4,
        EGL_CONTEXT_MINOR_VERSION_KHR,
        5,
        EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR,
        EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,
        EGL_NONE,
    };
    result->context = eglCreateContext(result->display, config, EGL_NO_CONTEXT, context_attributes);
    result->surface = eglCreateWindowSurface(
        result->display, config, (EGLNativeWindowType)result->egl_window, nullptr);
    if (result->context == EGL_NO_CONTEXT || result->surface == EGL_NO_SURFACE
        || !eglMakeCurrent(result->display, result->surface, result->surface, result->context)) {
        fprintf(stderr, "cannot create a Wayland OpenGL context (0x%04x)\n", eglGetError());
        destroy_context(result);
        return false;
    }
    printf("waylandDisplay=%s\nGL_VENDOR=%s\nGL_RENDERER=%s\nGL_VERSION=%s\n",
           display_name,
           glGetString(GL_VENDOR),
           glGetString(GL_RENDERER),
           glGetString(GL_VERSION));
    return true;
}

static void destroy_context(struct gl_context* context)
{
    if (!context->owns_resources)
        return;
    if (context->display != EGL_NO_DISPLAY) {
        eglMakeCurrent(context->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (context->surface != EGL_NO_SURFACE)
            eglDestroySurface(context->display, context->surface);
        if (context->context != EGL_NO_CONTEXT)
            eglDestroyContext(context->display, context->context);
        eglTerminate(context->display);
    }
    if (context->egl_window)
        wl_egl_window_destroy(context->egl_window);
    if (context->toplevel)
        xdg_toplevel_destroy(context->toplevel);
    if (context->xdg_surface)
        xdg_surface_destroy(context->xdg_surface);
    if (context->wayland_surface)
        wl_surface_destroy(context->wayland_surface);
    if (context->wm_base)
        xdg_wm_base_destroy(context->wm_base);
    if (context->compositor)
        wl_compositor_destroy(context->compositor);
    if (context->registry)
        wl_registry_destroy(context->registry);
    if (context->wayland_display)
        wl_display_disconnect(context->wayland_display);
}

static void uniform_i(GLuint program, const char* name, GLint value)
{
    GLint location = glGetUniformLocation(program, name);
    if (location >= 0)
        glUniform1i(location, value);
}

static void uniform_ui(GLuint program, const char* name, GLuint value)
{
    GLint location = glGetUniformLocation(program, name);
    if (location >= 0)
        glUniform1ui(location, value);
}

static void uniform_f(GLuint program, const char* name, GLfloat value)
{
    GLint location = glGetUniformLocation(program, name);
    if (location >= 0)
        glUniform1f(location, value);
}

static void uniform_vector(GLuint program, const char* name, const GLfloat* values, size_t count)
{
    GLint location = glGetUniformLocation(program, name);
    if (location < 0)
        return;
    switch (count) {
        case 2:
            glUniform2fv(location, 1, values);
            break;
        case 3:
            glUniform3fv(location, 1, values);
            break;
        case 4:
            glUniform4fv(location, 1, values);
            break;
        default:
            fprintf(stderr, "unsupported uniform width for %s: %zu\n", name, count);
            abort();
    }
}

static float load_float(const uint8_t* payload, size_t offset)
{
    float value;
    memcpy(&value, &payload[offset], sizeof value);
    return value;
}

static float load_half(const uint8_t* payload, size_t offset)
{
    _Float16 value;
    memcpy(&value, &payload[offset], sizeof value);
    return (float)value;
}

struct uniform_field
{
    const char* name;
    size_t      offset;
    size_t      count;
    bool        half;
};

static void apply_uniform_fields(GLuint                      program,
                                 const uint8_t*              payload,
                                 const struct uniform_field* fields,
                                 size_t                      count)
{
    for (size_t field_index = 0; field_index < count; ++field_index) {
        const struct uniform_field* field = &fields[field_index];
        float                       values[4];
        size_t                      stride = field->half ? sizeof(_Float16) : sizeof(float);
        for (size_t component = 0; component < field->count; ++component) {
            size_t offset = field->offset + component * stride;
            values[component]
                = field->half ? load_half(payload, offset) : load_float(payload, offset);
        }
        if (field->count == 1)
            uniform_f(program, field->name, values[0]);
        else
            uniform_vector(program, field->name, values, field->count);
    }
}

static void apply_profile(GLuint program, const uint8_t profile[static PROFILE_BYTES])
{
    static const struct uniform_field fields[] = {
        {"SdfArg", 0, 4, false},
        {"SdfTransform", 16, 4, false},
        {"SdfArg2", 32, 4, false},
        {"DisplacementMatrix", 48, 4, false},
        {"InnerRefractionAmount", 64, 1, false},
        {"InnerRefractionInverseHeight", 68, 1, false},
        {"OuterRefractionAmount", 72, 1, false},
        {"OuterRefractionInverseHeight", 76, 1, false},
        {"RefractionThreshold0", 80, 1, false},
        {"RefractionThreshold1", 84, 1, false},
        {"BlurRadius", 88, 1, false},
        {"EdgeBleedBlurRadius", 92, 1, false},
        {"EdgeBleedAmount", 96, 1, false},
        {"EdgeBleedInverseHeight", 100, 1, false},
        {"ShadowAmount", 104, 1, false},
        {"ShadowInverseHeight", 108, 1, false},
        {"ShadowOffset", 112, 2, false},
        {"ShadowBlurRadius", 120, 1, false},
        {"ShadowInverseRadius", 124, 1, false},
        {"FaceMatrix0", 128, 4, true},
        {"FaceMatrix1", 136, 4, true},
        {"FaceMatrix2", 144, 4, true},
        {"BleedMatrix0", 152, 4, true},
        {"BleedMatrix1", 160, 4, true},
        {"BleedMatrix2", 168, 4, true},
        {"ShadowMatrix0", 176, 4, true},
        {"ShadowMatrix1", 184, 4, true},
        {"ShadowMatrix2", 192, 4, true},
        {"ShadowContribution", 200, 1, false},
        {"ShadowFaceOpacity", 204, 1, false},
        {"BlurAlpha", 208, 4, true},
        {"BlurDistance", 216, 4, true},
        {"EdgeBleedDistance", 224, 2, true},
        {"EdgeBleedOpacity", 228, 1, true},
        {"FaceOpacity", 230, 1, true},
        {"BleedDarken", 232, 2, true},
        {"ShadowDistanceOffset", 236, 1, true},
        {"ShadowOpacity", 238, 1, true},
        {"RefractionOpacity", 240, 1, true},
        {"HoldingToneOpacity", 242, 1, true},
        {"SdrShadowDistance", 244, 2, true},
        {"ClampLimit", 248, 1, true},
        {"PreserveHue", 250, 1, true},
        {"SdrWhiteValue", 252, 1, true},
        {"FloatMixWorkaround", 254, 1, true},
        {"ComplexRefraction", 256, 1, true},
    };
    apply_uniform_fields(program, profile, fields, sizeof fields / sizeof fields[0]);
    uniform_f(program, "EdrScale", 1.0f);
}

static void apply_highlight(GLuint program, const uint8_t payload[static HIGHLIGHT_UNIFORM_BYTES])
{
    static const struct uniform_field fields[] = {
        {"SdfArg", 0x00, 4, false},
        {"SdfTransform", 0x10, 4, false},
        {"SdfArg2", 0x20, 4, false},
        {"VibrantMatrix0", 0x60, 4, true},
        {"VibrantMatrix1", 0x68, 4, true},
        {"VibrantMatrix2", 0x70, 4, true},
        {"VibrantMatrix3", 0x78, 4, true},
        {"VibrantMatrix4", 0x80, 4, true},
        {"VibrantControls", 0x88, 4, true},
        {"KeyFillParams0", 0xD0, 4, true},
        {"KeyFillParams1", 0xD8, 4, true},
        {"KeyFillParams2", 0xE0, 4, true},
        {"KeyFillColor0", 0xE8, 4, true},
        {"KeyFillColor1", 0xF0, 4, true},
    };
    apply_uniform_fields(program, payload, fields, sizeof fields / sizeof fields[0]);
}

[[nodiscard]]
static struct geometry create_geometry(const void* vertices,
                                       size_t      vertex_bytes,
                                       const void* indices,
                                       size_t      index_bytes,
                                       GLsizei     count)
{
    struct geometry result = {.count = count, .indexed = indices != nullptr};
    glGenVertexArrays(1, &result.vao);
    glBindVertexArray(result.vao);
    glGenBuffers(1, &result.vertex_buffer);
    glBindBuffer(GL_ARRAY_BUFFER, result.vertex_buffer);
    glBufferData(GL_ARRAY_BUFFER, (GLsizeiptr)vertex_bytes, vertices, GL_STATIC_DRAW);
    if (indices) {
        glGenBuffers(1, &result.index_buffer);
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, result.index_buffer);
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, (GLsizeiptr)index_bytes, indices, GL_STATIC_DRAW);
    }
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, 8 * sizeof(float), nullptr);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(
        1, 2, GL_FLOAT, GL_FALSE, 8 * sizeof(float), (void*)(uintptr_t)(4 * sizeof(float)));
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(
        2, 2, GL_FLOAT, GL_FALSE, 8 * sizeof(float), (void*)(uintptr_t)(6 * sizeof(float)));
    return result;
}

static void draw_geometry(const struct geometry* geometry)
{
    glBindVertexArray(geometry->vao);
    if (geometry->indexed)
        glDrawElements(GL_TRIANGLES, geometry->count, GL_UNSIGNED_SHORT, nullptr);
    else
        glDrawArrays(GL_TRIANGLES, 0, geometry->count);
}

static void destroy_geometry(struct geometry* geometry)
{
    if (geometry->index_buffer)
        glDeleteBuffers(1, &geometry->index_buffer);
    if (geometry->vertex_buffer)
        glDeleteBuffers(1, &geometry->vertex_buffer);
    if (geometry->vao)
        glDeleteVertexArrays(1, &geometry->vao);
}

[[nodiscard]]
static GLuint create_rgba8_texture(uint32_t width, uint32_t height, const void* pixels)
{
    GLuint texture = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, (GLsizei)width, (GLsizei)height);
    glTexSubImage2D(
        GL_TEXTURE_2D, 0, 0, 0, (GLsizei)width, (GLsizei)height, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    return texture;
}

[[nodiscard]]
static GLuint create_rgba32ui_texture(uint32_t width, uint32_t height, const void* words)
{
    GLuint texture = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA32UI, (GLsizei)width, (GLsizei)height);
    glTexSubImage2D(GL_TEXTURE_2D,
                    0,
                    0,
                    0,
                    (GLsizei)width,
                    (GLsizei)height,
                    GL_RGBA_INTEGER,
                    GL_UNSIGNED_INT,
                    words);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    return texture;
}

[[nodiscard]]
static GLuint create_source_texture(const char* directory, const struct gate_config* config)
{
    uint32_t           width  = config->source_width;
    uint32_t           height = config->source_height;
    struct source_file base
        = read_fixture_file(directory, "source-mip-0.rgba8", (size_t)width * height * 4);
    if (!base.data)
        return 0;
    GLuint texture = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RGBA8,
                 (GLsizei)width,
                 (GLsizei)height,
                 0,
                 GL_RGBA,
                 GL_UNSIGNED_BYTE,
                 base.data);
    glGenerateMipmap(GL_TEXTURE_2D);
    free(base.data);
    for (uint32_t level = 1; level < config->mip_count; ++level) {
        width  = width > 1 ? width / 2 : 1;
        height = height > 1 ? height / 2 : 1;
        char name[64];
        snprintf(name, sizeof name, "source-mip-%u.rgba8", level);
        struct source_file pixels = read_fixture_file(directory, name, (size_t)width * height * 4);
        if (!pixels.data) {
            glDeleteTextures(1, &texture);
            return 0;
        }
        glTexSubImage2D(GL_TEXTURE_2D,
                        (GLint)level,
                        0,
                        0,
                        (GLsizei)width,
                        (GLsizei)height,
                        GL_RGBA,
                        GL_UNSIGNED_BYTE,
                        pixels.data);
        free(pixels.data);
    }
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    return texture;
}

static void bind_texture(GLuint program, const char* name, GLint unit, GLuint texture)
{
    uniform_i(program, name, unit);
    glActiveTexture((GLenum)(GL_TEXTURE0 + unit));
    glBindTexture(GL_TEXTURE_2D, texture);
}

[[nodiscard]]
static bool configure_program(GLuint                    program,
                              const struct gate_config* config,
                              const uint8_t             profile[static PROFILE_BYTES])
{
    static const char* const samplers[] = {
        "SourceTexture",
        "AppleRefractionTrace",
        "AppleInterpolantTrace",
        "AppleSdfTrace",
        "AppleSdfFloatTrace",
        "AppleSdfNormalTrace",
        "AppleFloatIntrinsicTable",
        "DestinationTexture",
        "AppleInterpolantAxisTrace",
        "AppleInterpolantCoefficientTrace",
        "AppleInterpolantCorrectionSurface",
        "AppleSqrtIntrinsicTable",
        "AppleRsqrtIntrinsicTable",
        "AppleHalfIntrinsicTable",
        "AppleHighlightHalfStages",
        "AppleHighlightCompositorB",
        "AppleHighlightGeometryTrace",
        "AppleShadowInterpolantCoefficientTrace",
        "AppleShadowInterpolantSlopeTrace",
    };
    for (size_t unit = 0; unit < sizeof samplers / sizeof samplers[0]; ++unit)
        uniform_i(program, samplers[unit], (GLint)unit);

    static const struct
    {
        const char* name;
        GLint       value;
    } integer_uniforms[] = {
        {"SamplerSpatialQuantization", 0},
        {"SamplerModel", 0},
        {"InnerSamplerCoordinateModel", 0},
        {"OuterSamplerCoordinateModel", 0},
        {"EdgeSamplerCoordinateModel", 0},
        {"ShadowSamplerCoordinateModel", 0},
        {"RefractionMixModel", 0},
        {"HoldingMixMode", 0},
        {"HoldingDivideMode", 0},
        {"UseAppleRefractionTrace", 0},
        {"UseAppleInterpolantTrace", 0},
        {"UseAppleShadowInterpolantModel", 0},
        {"UseAppleSdfTrace", 0},
        {"UseAppleSqrtTrace", 0},
        {"UseAppleRsqrtTrace", 0},
        {"UseAppleIntrinsicTable", 1},
        {"UseAppleHalfIntrinsicTable", 0},
        {"RecordAppleIntrinsicUsage", 0},
        {"NumericTrace", 0},
        {"CoordinateMode", 0},
        {"AnalyticCoordinateUlpBias", 0},
        {"ProfileMode4Path", 0},
        {"EmulateAppleBlend", 1},
        {"FinalHighlightPass", 0},
        {"FinalHighlightTrace", 0},
        {"HighlightDerivativeMode", 1},
        {"HighlightCoordinateMode", 0},
        {"HighlightAlphaUlpBias", 0},
        {"HighlightFloatDivisionMode", 3},
        {"HighlightCoverageArithmeticMode", 1},
        {"HighlightMixMode", 0},
        {"HighlightBandMode", 0},
        {"HighlightNormalizeMode", 1},
        {"HighlightNormalizedCoordinateMode", 0},
        {"HighlightSdfArithmeticMode", 0},
        {"HighlightSdfNormalMode", 0},
        {"HighlightSdfSquaredUlpBias", 0},
        {"HighlightSdfDistanceUlpBias", 0},
        {"HighlightVibrantArithmeticMode", 9},
        {"HighlightSourceDivisionMode", 0},
        {"HighlightSourceConstructionMode", 1},
        {"HighlightDestinationDivisionMode", 0},
        {"UseAppleHighlightAlphaTrace", 0},
        {"UseAppleHighlightSourceTrace", 0},
        {"UseAppleHighlightGeometryTrace", 0},
    };
    for (size_t index = 0; index < sizeof integer_uniforms / sizeof integer_uniforms[0]; ++index) {
        uniform_i(program, integer_uniforms[index].name, integer_uniforms[index].value);
    }
    uniform_i(program,
              "HighlightVibrantArithmeticMode",
              (GLint)config->vibrant_arithmetic_mode);
    uniform_i(program, "HighlightDerivativeMode", config->highlight_derivative_mode);
    uniform_i(program, "HighlightCoordinateMode", config->highlight_coordinate_mode);
    uniform_i(program, "HighlightAlphaUlpBias", config->highlight_alpha_ulp_bias);
    uniform_i(program, "HighlightFloatDivisionMode", config->highlight_float_division_mode);
    uniform_i(program,
              "HighlightCoverageArithmeticMode",
              config->highlight_coverage_arithmetic_mode);
    uniform_i(program, "HighlightMixMode", config->highlight_mix_mode);
    uniform_i(program, "HighlightBandMode", config->highlight_band_mode);
    uniform_i(program, "HighlightNormalizeMode", config->highlight_normalize_mode);
    uniform_i(program,
              "HighlightNormalizedCoordinateMode",
              config->highlight_normalized_coordinate_mode);
    uniform_i(program, "HighlightSdfArithmeticMode", config->highlight_sdf_arithmetic_mode);
    uniform_i(program,
              "HighlightSdfSquaredUlpBias",
              config->highlight_sdf_squared_ulp_bias);
    uniform_i(program,
              "HighlightSdfDistanceUlpBias",
              config->highlight_sdf_distance_ulp_bias);
    uniform_i(program,
              "HighlightSourceDivisionMode",
              config->highlight_source_division_mode);
    uniform_i(program,
              "HighlightSourceConstructionMode",
              config->highlight_source_construction_mode);
    uniform_i(program,
              "HighlightDestinationDivisionMode",
              config->highlight_destination_division_mode);
    uniform_i(program,
              "UseAppleHalfIntrinsicTable",
              (GLint)config->use_apple_half_intrinsic_table);
    uniform_i(program, "AppleInterpolantTileStart", (GLint)config->tile_start);
    GLint slope_location = glGetUniformLocation(program, "AppleInterpolantSlopeBits");
    if (slope_location >= 0)
        glUniform4uiv(slope_location, 1, config->slopes);
    uniform_ui(program, "AppleInterpolantSourceLowBits", 0);
    uniform_ui(program, "AppleFastSqrtBias", 0);
    uniform_ui(program, "AppleFastReciprocalBias", 1);
    uniform_ui(program, "ArithmeticBarrier", 0);
    const float mvp[16] = {
        0.001953125f,
        0.0f,
        0.0f,
        0.0f,
        0.0f,
        -0.001953125f,
        0.0f,
        0.0f,
        0.0f,
        0.0f,
        0.0f,
        0.0f,
        -1.0f,
        1.0f,
        0.0f,
        1.0f,
    };
    GLint mvp_location = glGetUniformLocation(program, "MVP");
    if (mvp_location < 0)
        return false;
    glUniformMatrix4fv(mvp_location, 1, GL_FALSE, mvp);
    apply_profile(program, profile);
    return true;
}

struct comparison
{
    size_t   mismatched_bytes;
    size_t   mismatched_pixels;
    unsigned maximum_delta;
};

static struct comparison
compare_pixels(const uint8_t* candidate, const uint8_t* reference, size_t pixel_bytes)
{
    struct comparison result = {};
    for (size_t pixel = 0; pixel < pixel_bytes / 4; ++pixel) {
        bool changed = false;
        for (size_t channel = 0; channel < 4; ++channel) {
            size_t   index = pixel * 4 + channel;
            unsigned left  = candidate[index];
            unsigned right = reference[index];
            unsigned delta = left > right ? left - right : right - left;
            if (delta != 0) {
                ++result.mismatched_bytes;
                changed = true;
                if (delta > result.maximum_delta)
                    result.maximum_delta = delta;
            }
        }
        result.mismatched_pixels += changed ? 1u : 0u;
    }
    return result;
}

static int run_gate(int argc, char** argv)
{
    if ((argc != 7 && argc != 8 && argc != 9)
        || (strcmp(argv[1], "--device-index") != 0 && strcmp(argv[1], "--wayland-display") != 0
            && strcmp(argv[1], "--current-context") != 0)) {
        fprintf(stderr,
                "usage: %s (--device-index N | --wayland-display NAME | --current-context -) "
                "FIXTURE_DIR VERTEX FRAGMENT INTRINSIC_TABLE "
                "[CANDIDATE_OUTPUT [DRAW_COUNT]]\n",
                argv[0]);
        return 2;
    }
    EGLint device_index = -1;
    bool   wayland_mode = strcmp(argv[1], "--wayland-display") == 0;
    bool   current_mode = strcmp(argv[1], "--current-context") == 0;
    if (!wayland_mode && !current_mode && !parse_device_index(argv[2], &device_index)) {
        fprintf(stderr, "invalid device selection\n");
        return 2;
    }
    EGLint requested_draw_count = 3;
    if (argc == 9
        && (!parse_device_index(argv[8], &requested_draw_count) || requested_draw_count < 1
            || requested_draw_count > 3)) {
        fprintf(stderr, "invalid draw count\n");
        return 2;
    }
    EGLint requested_final_highlight_trace = 0;
    const char* final_highlight_trace = getenv("WALLE_FINAL_HIGHLIGHT_TRACE");
    if (final_highlight_trace
        && (!parse_device_index(final_highlight_trace, &requested_final_highlight_trace)
            || requested_final_highlight_trace < 0
            || requested_final_highlight_trace > 42)) {
        fprintf(stderr, "invalid final-highlight trace\n");
        return 2;
    }
    EGLint requested_highlight_sdf_normal_mode = 5;
    const char* highlight_sdf_normal_mode = getenv("WALLE_HIGHLIGHT_SDF_NORMAL_MODE");
    if (highlight_sdf_normal_mode
        && (!parse_device_index(highlight_sdf_normal_mode, &requested_highlight_sdf_normal_mode)
            || requested_highlight_sdf_normal_mode < 0
            || requested_highlight_sdf_normal_mode > 23)) {
        fprintf(stderr, "invalid highlight SDF-normal mode\n");
        return 2;
    }
    const char*        wayland_display   = wayland_mode ? argv[2] : nullptr;
    const char*        fixture_directory = argv[3];
    struct source_file config_file = read_fixture_file(fixture_directory, "config.bin", 0);
    struct source_file vertex_shader   = read_file(argv[4], 0);
    struct source_file fragment_shader = read_file(argv[5], 0);
    struct source_file intrinsic       = read_file(argv[6], 4096u * 2048u);
    if (!config_file.data || !vertex_shader.data || !fragment_shader.data || !intrinsic.data)
        return 1;
    struct gate_config config = {
        .highlight_derivative_mode           = 1,
        .highlight_coordinate_mode           = 0,
        .highlight_alpha_ulp_bias             = 0,
        .highlight_float_division_mode        = 3,
        .highlight_coverage_arithmetic_mode   = 1,
        .highlight_mix_mode                   = 0,
        .highlight_band_mode                  = 0,
        .highlight_normalize_mode             = 1,
        .highlight_normalized_coordinate_mode = 0,
        .highlight_sdf_arithmetic_mode        = 0,
        .highlight_sdf_squared_ulp_bias       = 0,
        .highlight_sdf_distance_ulp_bias      = 0,
        .highlight_source_division_mode       = 0,
        .highlight_source_construction_mode   = 1,
        .highlight_destination_division_mode  = 0,
        .use_apple_half_intrinsic_table       = 0,
    };
    if (config_file.size == sizeof(struct gate_config_v1)
        && memcmp(config_file.data, "WALLELG1", 8) == 0) {
        memcpy(&config, config_file.data, sizeof(struct gate_config_v1));
        config.source_width              = config.material == 0 ? 448u : 384u;
        config.source_height             = config.source_width;
        config.main_vertex_count         = 6;
        config.shadow_vertex_count       = 16;
        config.shadow_index_count        = 48;
        config.highlight_vertex_count    = 4;
        config.highlight_index_count     = 6;
        config.vibrant_arithmetic_mode   = 9;
        config.background_scissor_x      = 0;
        config.background_scissor_y      = 0;
        config.background_scissor_width  = config.width;
        config.background_scissor_height = config.height;
    } else if (config_file.size == sizeof(struct gate_config_v2)
               && memcmp(config_file.data, "WALLELG2", 8) == 0) {
        memcpy(&config, config_file.data, sizeof(struct gate_config_v2));
    } else if (config_file.size == sizeof config
               && memcmp(config_file.data, "WALLELG3", 8) == 0) {
        memcpy(&config, config_file.data, sizeof config);
    } else {
        fprintf(stderr, "fixture config has an unsupported schema or byte count\n");
        free(config_file.data);
        return 1;
    }
    free(config_file.data);
    if (config.width != 1024 || config.height != 1024 || config.material > 1
        || config.appearance > 1 || config.mip_count == 0 || config.mip_count > 16
        || config.coefficient_width == 0 || config.source_width == 0
        || config.source_height == 0 || config.main_vertex_count == 0
        || config.main_vertex_count > MAX_GATE_VERTICES || config.shadow_vertex_count == 0
        || config.shadow_vertex_count > MAX_GATE_VERTICES || config.shadow_index_count == 0
        || config.shadow_index_count > MAX_GATE_INDICES || config.highlight_vertex_count == 0
        || config.highlight_vertex_count > MAX_GATE_VERTICES || config.highlight_index_count == 0
        || config.highlight_index_count > MAX_GATE_INDICES
        || (config.vibrant_arithmetic_mode != 9 && config.vibrant_arithmetic_mode != 10)
        || config.background_scissor_width == 0 || config.background_scissor_height == 0
        || config.background_scissor_x > config.width
        || config.background_scissor_width > config.width - config.background_scissor_x
        || config.background_scissor_y > config.height
        || config.background_scissor_height > config.height - config.background_scissor_y
        || config.highlight_derivative_mode < 0 || config.highlight_derivative_mode > 8
        || config.highlight_coordinate_mode < 0 || config.highlight_coordinate_mode > 1
        || config.highlight_alpha_ulp_bias < -8 || config.highlight_alpha_ulp_bias > 8
        || config.highlight_float_division_mode < 0
        || config.highlight_float_division_mode > 5
        || config.highlight_coverage_arithmetic_mode < 0
        || config.highlight_coverage_arithmetic_mode > 2 || config.highlight_mix_mode < 0
        || config.highlight_mix_mode > 4 || config.highlight_band_mode < 0
        || config.highlight_band_mode > 2 || config.highlight_normalize_mode < 0
        || config.highlight_normalize_mode > 5
        || config.highlight_normalized_coordinate_mode < 0
        || config.highlight_normalized_coordinate_mode > 8
        || config.highlight_sdf_arithmetic_mode < 0
        || config.highlight_sdf_arithmetic_mode > 3
        || config.highlight_sdf_squared_ulp_bias < -8
        || config.highlight_sdf_squared_ulp_bias > 8
        || config.highlight_sdf_distance_ulp_bias < -8
        || config.highlight_sdf_distance_ulp_bias > 8
        || config.highlight_source_division_mode < 0
        || config.highlight_source_division_mode > 4
        || config.highlight_source_construction_mode < 0
        || config.highlight_source_construction_mode > 6
        || config.highlight_destination_division_mode < 0
        || config.highlight_destination_division_mode > 6
        || config.use_apple_half_intrinsic_table > 1
    ) {
        fprintf(stderr, "invalid fixture config\n");
        return 1;
    }
    uint32_t mip_width  = config.source_width;
    uint32_t mip_height = config.source_height;
    for (uint32_t level = 1; level < config.mip_count; ++level) {
        mip_width /= 2;
        mip_height /= 2;
        if (mip_width == 0 || mip_height == 0) {
            fprintf(stderr, "fixture source mip chain underflows\n");
            return 1;
        }
    }

    size_t             pixel_bytes = (size_t)config.width * config.height * 4;
    struct source_file destination
        = read_fixture_file(fixture_directory, "destination.rgba8", pixel_bytes);
    struct source_file reference
        = read_fixture_file(fixture_directory, "reference-bottom-left.rgba8", pixel_bytes);
    struct source_file profile = read_fixture_file(fixture_directory, "profile.bin", PROFILE_BYTES);
    struct source_file highlight_uniform
        = read_fixture_file(fixture_directory, "highlight-uniform.bin", HIGHLIGHT_UNIFORM_BYTES);
    struct source_file main_vertices = read_fixture_file(
        fixture_directory,
        "main-vertices.f32",
        (size_t)config.main_vertex_count * 8 * sizeof(float));
    struct source_file shadow_vertices = read_fixture_file(
        fixture_directory,
        "shadow-vertices.f32",
        (size_t)config.shadow_vertex_count * 8 * sizeof(float));
    struct source_file shadow_indices = read_fixture_file(
        fixture_directory,
        "shadow-indices.u16",
        (size_t)config.shadow_index_count * sizeof(uint16_t));
    struct source_file highlight_vertices = read_fixture_file(
        fixture_directory,
        "highlight-vertices.f32",
        (size_t)config.highlight_vertex_count * 8 * sizeof(float));
    struct source_file highlight_indices = read_fixture_file(
        fixture_directory,
        "highlight-indices.u16",
        (size_t)config.highlight_index_count * sizeof(uint16_t));
    struct source_file coefficients
        = read_fixture_file(fixture_directory,
                            "interpolant-coefficients.rgba32ui",
                            (size_t)2 * config.coefficient_width * 4 * sizeof(uint32_t));
    struct source_file interpolant_axis = read_optional_fixture_file(
        fixture_directory,
        "interpolant-axis.rgba32ui",
        (size_t)2 * config.width * 4 * sizeof(uint32_t));
    struct source_file highlight_interpolant_axis = read_optional_fixture_file(
        fixture_directory,
        "highlight-interpolant-axis.rgba32ui",
        (size_t)(config.highlight_index_count == 24 ? 8 : 2) * config.width * 4
            * sizeof(uint32_t));
    struct source_file shadow_coefficients = read_optional_fixture_file(
        fixture_directory,
        "shadow-interpolant-coefficients.rgba32ui",
        SHADOW_COEFFICIENT_BYTES);
    struct source_file shadow_slopes = read_optional_fixture_file(
        fixture_directory, "shadow-interpolant-slopes.rgba32ui", SHADOW_SLOPE_BYTES);
    bool use_shadow_interpolant_model
        = shadow_coefficients.data != nullptr && shadow_slopes.data != nullptr;
    if ((shadow_coefficients.data == nullptr) != (shadow_slopes.data == nullptr)) {
        fprintf(stderr, "shadow interpolant fixture is incomplete\n");
        return 1;
    }
    struct source_file half_intrinsic = {};
    if (config.use_apple_half_intrinsic_table != 0) {
        half_intrinsic = read_fixture_file(
            fixture_directory, "half-intrinsics.r32ui", 256u * 256u * sizeof(uint32_t));
    }
    if (!destination.data || !reference.data || !profile.data || !highlight_uniform.data
        || !main_vertices.data || !shadow_vertices.data || !shadow_indices.data
        || !highlight_vertices.data || !highlight_indices.data || !coefficients.data
        || (config.use_apple_half_intrinsic_table != 0 && !half_intrinsic.data)) {
        return 1;
    }

    struct gl_context context = {};
    bool              context_created;
    if (current_mode) {
        context = (struct gl_context){
            .display         = external_context.display,
            .context         = eglGetCurrentContext(),
            .surface         = external_context.surface,
            .wayland_display = external_context.wayland_display,
            .wayland         = true,
            .owns_resources  = false,
        };
        context_created = external_context.active && context.display != EGL_NO_DISPLAY
                          && context.context != EGL_NO_CONTEXT && context.surface != EGL_NO_SURFACE
                          && eglGetCurrentDisplay() == context.display
                          && eglGetCurrentSurface(EGL_DRAW) == context.surface;
        if (!context_created)
            fprintf(stderr, "the supplied OpenGL context is not current\n");
    } else {
        context_created
            = wayland_mode
                  ? create_wayland_context(wayland_display, config.width, config.height, &context)
                  : create_context(device_index, &context);
    }
    if (!context_created)
        return 1;
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    GLuint program
        = link_program((const char*)vertex_shader.data, (const char*)fragment_shader.data);
    free(vertex_shader.data);
    free(fragment_shader.data);
    if (!program) {
        destroy_context(&context);
        return 1;
    }
    glUseProgram(program);
    if (!configure_program(program, &config, profile.data)) {
        fprintf(stderr, "required shader uniforms are absent\n");
        glDeleteProgram(program);
        destroy_context(&context);
        return 1;
    }

    struct geometry main = create_geometry(main_vertices.data,
                                           main_vertices.size,
                                           nullptr,
                                           0,
                                           (GLsizei)config.main_vertex_count);
    struct geometry shadow = create_geometry(
        shadow_vertices.data,
        shadow_vertices.size,
        shadow_indices.data,
        shadow_indices.size,
        (GLsizei)config.shadow_index_count);
    struct geometry highlight = create_geometry(highlight_vertices.data,
                                                highlight_vertices.size,
                                                highlight_indices.data,
                                                highlight_indices.size,
                                                (GLsizei)config.highlight_index_count);
    free(main_vertices.data);
    free(shadow_vertices.data);
    free(shadow_indices.data);
    free(highlight_vertices.data);
    free(highlight_indices.data);

    GLuint source_texture = create_source_texture(fixture_directory, &config);
    GLuint destination_texture
        = create_rgba8_texture(config.width, config.height, destination.data);
    GLuint color_texture = create_rgba8_texture(config.width, config.height, destination.data);
    free(destination.data);
    GLuint coefficient_texture = 0;
    glGenTextures(1, &coefficient_texture);
    glBindTexture(GL_TEXTURE_2D, coefficient_texture);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA32UI, (GLsizei)config.coefficient_width, 2);
    glTexSubImage2D(GL_TEXTURE_2D,
                    0,
                    0,
                    0,
                    (GLsizei)config.coefficient_width,
                    2,
                    GL_RGBA_INTEGER,
                    GL_UNSIGNED_INT,
                    coefficients.data);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    free(coefficients.data);
    GLuint interpolant_axis_texture = 0;
    if (interpolant_axis.data != nullptr) {
        interpolant_axis_texture
            = create_rgba32ui_texture(config.width, 2, interpolant_axis.data);
        free(interpolant_axis.data);
    }
    GLuint highlight_interpolant_axis_texture = 0;
    if (highlight_interpolant_axis.data != nullptr) {
        highlight_interpolant_axis_texture = create_rgba32ui_texture(
            config.width,
            config.highlight_index_count == 24 ? 8 : 2,
            highlight_interpolant_axis.data);
        free(highlight_interpolant_axis.data);
    }
    GLuint shadow_coefficient_texture = 0;
    GLuint shadow_slope_texture       = 0;
    if (use_shadow_interpolant_model) {
        shadow_coefficient_texture
            = create_rgba32ui_texture(32, 16, shadow_coefficients.data);
        shadow_slope_texture = create_rgba32ui_texture(8, 1, shadow_slopes.data);
        free(shadow_coefficients.data);
        free(shadow_slopes.data);
    }
    GLuint intrinsic_texture = 0;
    glGenTextures(1, &intrinsic_texture);
    glBindTexture(GL_TEXTURE_2D, intrinsic_texture);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_R8UI, 4096, 2048);
    glTexSubImage2D(
        GL_TEXTURE_2D, 0, 0, 0, 4096, 2048, GL_RED_INTEGER, GL_UNSIGNED_BYTE, intrinsic.data);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    free(intrinsic.data);
    GLuint half_intrinsic_texture = 0;
    if (config.use_apple_half_intrinsic_table != 0) {
        glGenTextures(1, &half_intrinsic_texture);
        glBindTexture(GL_TEXTURE_2D, half_intrinsic_texture);
        glTexStorage2D(GL_TEXTURE_2D, 1, GL_R32UI, 256, 256);
        glTexSubImage2D(GL_TEXTURE_2D,
                        0,
                        0,
                        0,
                        256,
                        256,
                        GL_RED_INTEGER,
                        GL_UNSIGNED_INT,
                        half_intrinsic.data);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    }
    free(half_intrinsic.data);

    GLuint framebuffer = 0;
    glGenFramebuffers(1, &framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color_texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "render framebuffer is incomplete\n");
        return 1;
    }
    bind_texture(program, "SourceTexture", 0, source_texture);
    bind_texture(program, "AppleFloatIntrinsicTable", 6, intrinsic_texture);
    bind_texture(program, "DestinationTexture", 7, destination_texture);
    bind_texture(program, "AppleInterpolantCoefficientTrace", 9, coefficient_texture);
    if (interpolant_axis_texture != 0) {
        bind_texture(program, "AppleInterpolantAxisTrace", 8, interpolant_axis_texture);
        uniform_i(program, "AppleInterpolantAxisStart", 0);
    }
    if (half_intrinsic_texture != 0)
        bind_texture(program, "AppleHalfIntrinsicTable", 13, half_intrinsic_texture);
    if (use_shadow_interpolant_model) {
        bind_texture(
            program, "AppleShadowInterpolantCoefficientTrace", 17, shadow_coefficient_texture);
        bind_texture(program, "AppleShadowInterpolantSlopeTrace", 18, shadow_slope_texture);
        uniform_i(program, "UseAppleShadowInterpolantModel", 1);
    }
    glViewport(0, 0, (GLsizei)config.width, (GLsizei)config.height);
    glEnable(GL_SCISSOR_TEST);
    glScissor((GLint)config.background_scissor_x,
              (GLint)config.background_scissor_y,
              (GLsizei)config.background_scissor_width,
              (GLsizei)config.background_scissor_height);
    glDisable(GL_BLEND);

    uniform_i(program, "CoordinateMode", interpolant_axis_texture != 0 ? 4 : 5);
    uniform_i(program, "HighlightSdfNormalMode", 0);
    uniform_i(program, "SdfMode", 4);
    draw_geometry(&main);
    uniform_i(program, "CoordinateMode", 0);
    if (requested_draw_count >= 2) {
        glCopyImageSubData(color_texture,
                           GL_TEXTURE_2D,
                           0,
                           0,
                           0,
                           0,
                           destination_texture,
                           GL_TEXTURE_2D,
                           0,
                           0,
                           0,
                           0,
                           (GLsizei)config.width,
                           (GLsizei)config.height,
                           1);
        uniform_i(program, "SdfMode", -4);
        draw_geometry(&shadow);
    }
    if (requested_draw_count >= 3) {
        glCopyImageSubData(color_texture,
                           GL_TEXTURE_2D,
                           0,
                           0,
                           0,
                           0,
                           destination_texture,
                           GL_TEXTURE_2D,
                           0,
                           0,
                           0,
                           0,
                           (GLsizei)config.width,
                           (GLsizei)config.height,
                           1);
        apply_highlight(program, highlight_uniform.data);
    }
    free(highlight_uniform.data);
    if (requested_draw_count >= 3) {
        glScissor(0, 0, (GLsizei)config.width, (GLsizei)config.height);
        if (highlight_interpolant_axis_texture != 0) {
            bind_texture(
                program,
                "AppleInterpolantAxisTrace",
                8,
                highlight_interpolant_axis_texture);
            uniform_i(program, "AppleInterpolantAxisStart", 0);
            uniform_i(program,
                      "CoordinateMode",
                      config.highlight_index_count == 24 ? 7 : 4);
        }
        uniform_i(program, "SdfMode", 4);
        uniform_i(program, "HighlightSdfNormalMode", requested_highlight_sdf_normal_mode);
        uniform_i(program, "FinalHighlightPass", 1);
        uniform_i(program, "FinalHighlightTrace", requested_final_highlight_trace);
        glFrontFace(GL_CW);
        glCullFace(GL_BACK);
        if (config.highlight_index_count != 24)
            glEnable(GL_CULL_FACE);
        draw_geometry(&highlight);
        glDisable(GL_CULL_FACE);
        uniform_i(program, "FinalHighlightPass", 0);
    }
    glFinish();

    uint8_t* candidate = malloc(pixel_bytes);
    if (!candidate) {
        fprintf(stderr, "candidate allocation failed\n");
        return 1;
    }
    glReadPixels(
        0, 0, (GLsizei)config.width, (GLsizei)config.height, GL_RGBA, GL_UNSIGNED_BYTE, candidate);
    if (argc >= 8 && !write_exact_file(argv[7], candidate, pixel_bytes))
        return 1;
    struct comparison offscreen = compare_pixels(candidate, reference.data, pixel_bytes);
    printf(
        "checkedBytes=%zu\nmismatchedBytes=%zu\nmismatchedPixels=%zu\n"
        "maximumChannelDelta=%u\nexact=%s\n",
        pixel_bytes,
        offscreen.mismatched_bytes,
        offscreen.mismatched_pixels,
        offscreen.maximum_delta,
        offscreen.mismatched_bytes == 0 ? "true" : "false");

    bool wayland_exact = true;
    if (context.wayland) {
        glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
        glBlitFramebuffer(0,
                          0,
                          (GLint)config.width,
                          (GLint)config.height,
                          0,
                          0,
                          (GLint)config.width,
                          (GLint)config.height,
                          GL_COLOR_BUFFER_BIT,
                          GL_NEAREST);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glReadBuffer(GL_BACK);
        glFinish();
        glReadPixels(0,
                     0,
                     (GLsizei)config.width,
                     (GLsizei)config.height,
                     GL_RGBA,
                     GL_UNSIGNED_BYTE,
                     candidate);
        struct comparison wayland = compare_pixels(candidate, reference.data, pixel_bytes);
        wayland_exact             = wayland.mismatched_bytes == 0;
        printf(
            "waylandCheckedBytes=%zu\nwaylandMismatchedBytes=%zu\n"
            "waylandMismatchedPixels=%zu\nwaylandMaximumChannelDelta=%u\n"
            "waylandExact=%s\n",
            pixel_bytes,
            wayland.mismatched_bytes,
            wayland.mismatched_pixels,
            wayland.maximum_delta,
            wayland_exact ? "true" : "false");
        if (!eglSwapBuffers(context.display, context.surface)) {
            fprintf(stderr, "eglSwapBuffers failed (0x%04x)\n", eglGetError());
            wayland_exact = false;
        }
        if (context.wayland_display && wl_display_roundtrip(context.wayland_display) < 0)
            wayland_exact = false;
    }

    free(candidate);
    free(reference.data);
    free(profile.data);
    destroy_geometry(&main);
    destroy_geometry(&shadow);
    destroy_geometry(&highlight);
    GLuint textures[] = {
        source_texture,
        destination_texture,
        color_texture,
        coefficient_texture,
        interpolant_axis_texture,
        highlight_interpolant_axis_texture,
        intrinsic_texture,
        half_intrinsic_texture,
        shadow_coefficient_texture,
        shadow_slope_texture,
    };
    glDeleteTextures((GLsizei)(sizeof textures / sizeof textures[0]), textures);
    glDeleteFramebuffers(1, &framebuffer);
    glDeleteProgram(program);
    destroy_context(&context);
    return offscreen.mismatched_bytes == 0 && wayland_exact ? 0 : 1;
}

int walle_exact_static_gl_render_current(EGLDisplay         display,
                                         EGLSurface         surface,
                                         struct wl_display* wayland_display,
                                         const char*        fixture_directory,
                                         const char*        vertex_shader,
                                         const char*        fragment_shader,
                                         const char*        intrinsic_table)
{
    if (external_context.active)
        return 1;
    external_context = (struct external_context){
        .display         = display,
        .surface         = surface,
        .wayland_display = wayland_display,
        .active          = true,
    };
    char* arguments[] = {
        "walle",
        "--current-context",
        "-",
        (char*)fixture_directory,
        (char*)vertex_shader,
        (char*)fragment_shader,
        (char*)intrinsic_table,
    };
    int result       = run_gate((int)(sizeof arguments / sizeof arguments[0]), arguments);
    external_context = (struct external_context){};
    return result;
}

#if !defined(WALLE_EXACT_STATIC_GL_NO_MAIN)
int main(int argc, char** argv)
{
    return run_gate(argc, argv);
}
#endif
