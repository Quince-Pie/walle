#include <EGL/egl.h>
#include <EGL/eglext.h>
#define GL_GLEXT_PROTOTYPES 1
#include <GL/glcorearb.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct source_file
{
    char*  data;
    size_t size;
};

constexpr size_t DEVICE_IDENTITY_CAPACITY = 256;

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
static struct source_file read_source(const char* path)
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
    if (length < 0 || length > 1024L * 1024L || fseek(stream, 0, SEEK_SET) != 0) {
        fprintf(stderr, "%s: invalid shader size\n", path);
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
    result.data[result.size] = '\0';
    fclose(stream);
    return result;
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

static void print_program_log(GLuint program)
{
    GLint size = 0;
    glGetProgramiv(program, GL_INFO_LOG_LENGTH, &size);
    if (size <= 1)
        return;
    char* log = malloc((size_t)size);
    if (!log)
        return;
    glGetProgramInfoLog(program, size, nullptr, log);
    fprintf(stderr, "program link log:\n%s\n", log);
    free(log);
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
    if (!query_devices((EGLint)(sizeof(devices) / sizeof(devices[0])), devices, &count)
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

[[nodiscard]]
static EGLDisplay surfaceless_display(void)
{
    auto get_platform_display
        = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    return get_platform_display
               ? get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr)
               : eglGetDisplay(EGL_DEFAULT_DISPLAY);
}

int main(int argc, char** argv)
{
    if (argc != 3 && argc != 5) {
        fprintf(stderr, "usage: %s [--device-index N] VERTEX_SHADER FRAGMENT_SHADER\n", argv[0]);
        return 2;
    }

    EGLint      device_index  = -1;
    const char* vertex_path   = argv[argc - 2];
    const char* fragment_path = argv[argc - 1];
    if (argc == 5
        && (strcmp(argv[1], "--device-index") != 0
            || !parse_device_index(argv[2], &device_index))) {
        fprintf(stderr, "invalid device selection\n");
        return 2;
    }

    struct source_file vertex   = read_source(vertex_path);
    struct source_file fragment = read_source(fragment_path);
    if (!vertex.data || !fragment.data) {
        free(vertex.data);
        free(fragment.data);
        return 1;
    }

    char       device_identity[DEVICE_IDENTITY_CAPACITY] = "surfaceless-default";
    EGLDisplay display
        = device_index >= 0 ? device_display(device_index, device_identity, sizeof(device_identity))
                            : surfaceless_display();
    if (display == EGL_NO_DISPLAY) {
        fprintf(stderr, "cannot acquire EGL display (0x%04x)\n", eglGetError());
        free(vertex.data);
        free(fragment.data);
        return 1;
    }

    EGLint egl_major = 0;
    EGLint egl_minor = 0;
    if (!eglInitialize(display, &egl_major, &egl_minor)) {
        fprintf(stderr, "eglInitialize failed (0x%04x)\n", eglGetError());
        free(vertex.data);
        free(fragment.data);
        return 1;
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
        free(vertex.data);
        free(fragment.data);
        return 1;
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
        free(vertex.data);
        free(fragment.data);
        return 1;
    }

    printf("device=%s\nEGL=%d.%d\nGL_VENDOR=%s\nGL_RENDERER=%s\nGL_VERSION=%s\n",
           device_identity,
           egl_major,
           egl_minor,
           glGetString(GL_VENDOR),
           glGetString(GL_RENDERER),
           glGetString(GL_VERSION));

    GLuint vertex_shader   = compile_shader(GL_VERTEX_SHADER, vertex.data, "vertex");
    GLuint fragment_shader = compile_shader(GL_FRAGMENT_SHADER, fragment.data, "fragment");
    GLuint program         = 0;
    GLint  linked          = GL_FALSE;
    if (vertex_shader && fragment_shader) {
        program = glCreateProgram();
        glAttachShader(program, vertex_shader);
        glAttachShader(program, fragment_shader);
        glLinkProgram(program);
        glGetProgramiv(program, GL_LINK_STATUS, &linked);
        print_program_log(program);
    }

    GLint active_uniforms  = 0;
    GLint texture_units    = 0;
    GLint storage_bindings = 0;
    if (linked == GL_TRUE) {
        glGetProgramiv(program, GL_ACTIVE_UNIFORMS, &active_uniforms);
        glGetIntegerv(GL_MAX_TEXTURE_IMAGE_UNITS, &texture_units);
        glGetIntegerv(GL_MAX_SHADER_STORAGE_BUFFER_BINDINGS, &storage_bindings);
        printf(
            "linked=true\nactiveUniforms=%d\nfragmentTextureUnits=%d\n"
            "shaderStorageBindings=%d\n",
            active_uniforms,
            texture_units,
            storage_bindings);
    } else {
        fprintf(stderr, "linked=false\n");
    }

    if (program)
        glDeleteProgram(program);
    if (vertex_shader)
        glDeleteShader(vertex_shader);
    if (fragment_shader)
        glDeleteShader(fragment_shader);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroySurface(display, surface);
    eglDestroyContext(display, context);
    eglTerminate(display);
    free(vertex.data);
    free(fragment.data);
    return linked == GL_TRUE ? 0 : 1;
}
