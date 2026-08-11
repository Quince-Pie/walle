#define GL_GLEXT_PROTOTYPES 1

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/glcorearb.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "liquid_glass_reveal_compositor.h"

struct gl_context
{
    EGLDisplay display;
    EGLContext context;
    EGLSurface surface;
};

static void check(bool condition, const char* message)
{
    if (!condition) {
        fprintf(stderr, "reveal compositor test failed: %s\n", message);
        exit(1);
    }
}

static bool create_context(struct gl_context* result)
{
    const char* extensions = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
    if (extensions == nullptr || strstr(extensions, "EGL_MESA_platform_surfaceless") == nullptr)
        return false;
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_platform_display
        = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (get_platform_display == nullptr)
        return false;
    result->display
        = get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    EGLint major;
    EGLint minor;
    if (result->display == EGL_NO_DISPLAY || !eglInitialize(result->display, &major, &minor)
        || !eglBindAPI(EGL_OPENGL_API)) {
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
    EGLConfig config;
    EGLint    count;
    if (!eglChooseConfig(result->display, config_attributes, &config, 1, &count) || count != 1)
        return false;
    const EGLint surface_attributes[] = {EGL_WIDTH, 2, EGL_HEIGHT, 2, EGL_NONE};
    result->surface = eglCreatePbufferSurface(result->display, config, surface_attributes);
    const EGLint context_attributes[] = {
        EGL_CONTEXT_MAJOR_VERSION,
        4,
        EGL_CONTEXT_MINOR_VERSION,
        5,
        EGL_CONTEXT_OPENGL_PROFILE_MASK,
        EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
        EGL_NONE,
    };
    result->context = eglCreateContext(result->display, config, EGL_NO_CONTEXT, context_attributes);
    return result->surface != EGL_NO_SURFACE && result->context != EGL_NO_CONTEXT
           && eglMakeCurrent(result->display, result->surface, result->surface, result->context);
}

static void destroy_context(struct gl_context* context)
{
    if (context->display == EGL_NO_DISPLAY)
        return;
    eglMakeCurrent(context->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (context->context != EGL_NO_CONTEXT)
        eglDestroyContext(context->display, context->context);
    if (context->surface != EGL_NO_SURFACE)
        eglDestroySurface(context->display, context->surface);
    eglTerminate(context->display);
}

static GLuint compile_shader(GLenum type, const char* source)
{
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint compiled;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (compiled == GL_TRUE)
        return shader;
    glDeleteShader(shader);
    return 0;
}

static GLuint create_program(void)
{
    static const char vertex_source[]
        = "#version 450 core\n"
          "layout(location=0) in vec4 p;\n"
          "layout(location=1) in vec2 a;\n"
          "layout(location=2) in vec2 b;\n"
          "layout(location=3) in vec4 c;\n"
          "out vec4 source;\n"
          "void main(){gl_Position=p;source=vec4(a.y,b.y,c.z,c.w);}\n";
    static const char fragment_source[]
        = "#version 450 core\n"
          "in vec4 source;\n"
          "layout(location=0) out vec4 color;\n"
          "void main(){color=source;}\n";
    GLuint vertex   = compile_shader(GL_VERTEX_SHADER, vertex_source);
    GLuint fragment = compile_shader(GL_FRAGMENT_SHADER, fragment_source);
    if (vertex == 0 || fragment == 0)
        return 0;
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glLinkProgram(program);
    glDeleteShader(vertex);
    glDeleteShader(fragment);
    GLint linked;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked == GL_TRUE)
        return program;
    glDeleteProgram(program);
    return 0;
}

static void store_float(uint8_t* record, size_t offset, float value)
{
    memcpy(record + offset, &value, sizeof value);
}

static void store_half(uint8_t* record, size_t offset, _Float16 value)
{
    memcpy(record + offset, &value, sizeof value);
}

static void build_vertices(uint8_t bytes[static WALLE_LG_REVEAL_VERTEX_BYTE_COUNT])
{
    static const float positions[4][2] = {
        {-1.0f, -1.0f},
        {1.0f, -1.0f},
        {1.0f, 1.0f},
        {-1.0f, 1.0f},
    };
    for (size_t vertex = 0; vertex < WALLE_LG_REVEAL_VERTEX_COUNT; ++vertex) {
        uint8_t* record = bytes + vertex * WALLE_LG_REVEAL_VERTEX_STRIDE;
        size_t   source = vertex < 4 ? vertex : 0;
        store_float(record, 0, positions[source][0]);
        store_float(record, 4, positions[source][1]);
        store_float(record, 8, 0.0f);
        store_float(record, 12, 1.0f);
        store_float(record, WALLE_LG_REVEAL_SDF_OFFSET, 0.25f);
        store_float(record, WALLE_LG_REVEAL_SDF_OFFSET + 4, 0.25f);
        store_float(record, WALLE_LG_REVEAL_SOURCE_OFFSET, 0.25f);
        store_float(record, WALLE_LG_REVEAL_SOURCE_OFFSET + 4, 0.25f);
        store_half(record, WALLE_LG_REVEAL_HALF4_OFFSET, (_Float16)0.25f);
        store_half(record, WALLE_LG_REVEAL_HALF4_OFFSET + 4, (_Float16)0.25f);
        store_half(record, WALLE_LG_REVEAL_HALF4_OFFSET + 6, (_Float16)0.5f);
    }
}

int main(void)
{
    struct gl_context context = {
        .display = EGL_NO_DISPLAY,
        .context = EGL_NO_CONTEXT,
        .surface = EGL_NO_SURFACE,
    };
    check(create_context(&context), "OpenGL 4.5 context creation");
    glDisable(GL_DITHER);
    GLuint program = create_program();
    check(program != 0, "synthetic program creation");

    const uint8_t base[16] = {
        64,
        128,
        192,
        255,
        64,
        128,
        192,
        255,
        64,
        128,
        192,
        255,
        64,
        128,
        192,
        255,
    };
    GLuint target;
    glGenTextures(1, &target);
    glBindTexture(GL_TEXTURE_2D, target);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 2, 2, 0, GL_RGBA, GL_UNSIGNED_BYTE, base);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);

    uint8_t vertices[WALLE_LG_REVEAL_VERTEX_BYTE_COUNT] = {};
    build_vertices(vertices);
    uint16_t indices[WALLE_LG_REVEAL_INDEX_COUNT] = {0, 1, 2, 2, 3, 0};
    for (size_t index = 6; index < WALLE_LG_REVEAL_INDEX_COUNT; ++index)
        indices[index] = 4;

    struct walle_lg_reveal_compositor* compositor = walle_lg_reveal_compositor_create();
    check(compositor != nullptr, "compositor creation");
    GLint maximum_draw_buffers;
    GLint maximum_viewports;
    glGetIntegerv(GL_MAX_DRAW_BUFFERS, &maximum_draw_buffers);
    glGetIntegerv(GL_MAX_VIEWPORTS, &maximum_viewports);
    check(maximum_draw_buffers >= 2 && maximum_viewports >= 2, "two indexed state lanes");
    glViewportIndexedf(0, 7.0f, 8.0f, 9.0f, 10.0f);
    glScissorIndexed(0, 3, 4, 5, 6);
    glDisablei(GL_SCISSOR_TEST, 0);
    glDisablei(GL_BLEND, 0);
    glColorMaski(0, GL_FALSE, GL_TRUE, GL_FALSE, GL_TRUE);
    glViewportIndexedf(1, 17.0f, 18.0f, 19.0f, 20.0f);
    glScissorIndexed(1, 13, 14, 15, 16);
    glEnablei(GL_SCISSOR_TEST, 1);
    glEnablei(GL_BLEND, 1);
    glBlendEquationSeparatei(1, GL_FUNC_SUBTRACT, GL_FUNC_REVERSE_SUBTRACT);
    glBlendFuncSeparatei(1, GL_ZERO, GL_ONE, GL_SRC_ALPHA, GL_DST_ALPHA);
    glColorMaski(1, GL_TRUE, GL_FALSE, GL_TRUE, GL_FALSE);
    struct walle_lg_reveal_compositor_draw draw = {
        .program           = program,
        .target_texture    = target,
        .width             = 2,
        .height            = 2,
        .scissor           = {0, 0, 1, 1},
        .vertex_bytes      = vertices,
        .vertex_byte_count = sizeof vertices,
        .index_bytes       = indices,
        .index_byte_count  = sizeof indices,
    };
    check(walle_lg_reveal_compositor_draw(compositor, &draw), "captured-state draw");

    GLfloat   viewport[4];
    GLfloat   viewport_one[4];
    GLint     scissor[4];
    GLint     scissor_one[4];
    GLboolean color_mask[4];
    GLboolean color_mask_one[4];
    GLint     equation_rgb_one;
    GLint     equation_alpha_one;
    GLint     source_rgb_one;
    GLint     destination_rgb_one;
    GLint     source_alpha_one;
    GLint     destination_alpha_one;
    glGetFloati_v(GL_VIEWPORT, 0, viewport);
    glGetFloati_v(GL_VIEWPORT, 1, viewport_one);
    glGetIntegeri_v(GL_SCISSOR_BOX, 0, scissor);
    glGetIntegeri_v(GL_SCISSOR_BOX, 1, scissor_one);
    glGetBooleani_v(GL_COLOR_WRITEMASK, 0, color_mask);
    glGetBooleani_v(GL_COLOR_WRITEMASK, 1, color_mask_one);
    glGetIntegeri_v(GL_BLEND_EQUATION_RGB, 1, &equation_rgb_one);
    glGetIntegeri_v(GL_BLEND_EQUATION_ALPHA, 1, &equation_alpha_one);
    glGetIntegeri_v(GL_BLEND_SRC_RGB, 1, &source_rgb_one);
    glGetIntegeri_v(GL_BLEND_DST_RGB, 1, &destination_rgb_one);
    glGetIntegeri_v(GL_BLEND_SRC_ALPHA, 1, &source_alpha_one);
    glGetIntegeri_v(GL_BLEND_DST_ALPHA, 1, &destination_alpha_one);
    check(memcmp(viewport, (GLfloat[4]){7.0f, 8.0f, 9.0f, 10.0f}, sizeof viewport) == 0,
          "viewport zero restoration");
    check(memcmp(scissor, (GLint[4]){3, 4, 5, 6}, sizeof scissor) == 0, "scissor zero restoration");
    check(!glIsEnabledi(GL_SCISSOR_TEST, 0) && !glIsEnabledi(GL_BLEND, 0),
          "enable zero restoration");
    check(
        memcmp(color_mask, (GLboolean[4]){GL_FALSE, GL_TRUE, GL_FALSE, GL_TRUE}, sizeof color_mask)
            == 0,
        "color mask zero restoration");
    check(memcmp(viewport_one, (GLfloat[4]){17.0f, 18.0f, 19.0f, 20.0f}, sizeof viewport_one) == 0,
          "viewport one isolation");
    check(memcmp(scissor_one, (GLint[4]){13, 14, 15, 16}, sizeof scissor_one) == 0,
          "scissor one isolation");
    check(glIsEnabledi(GL_SCISSOR_TEST, 1) && glIsEnabledi(GL_BLEND, 1), "enable one restoration");
    check(memcmp(color_mask_one,
                 (GLboolean[4]){GL_TRUE, GL_FALSE, GL_TRUE, GL_FALSE},
                 sizeof color_mask_one)
              == 0,
          "color mask one restoration");
    check(equation_rgb_one == GL_FUNC_SUBTRACT && equation_alpha_one == GL_FUNC_REVERSE_SUBTRACT
              && source_rgb_one == GL_ZERO && destination_rgb_one == GL_ONE
              && source_alpha_one == GL_SRC_ALPHA && destination_alpha_one == GL_DST_ALPHA,
          "blend one restoration");

    uint8_t actual[sizeof base];
    glBindTexture(GL_TEXTURE_2D, target);
    glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, actual);
    const uint8_t expected[16] = {
        96,
        128,
        160,
        255,
        64,
        128,
        192,
        255,
        64,
        128,
        192,
        255,
        64,
        128,
        192,
        255,
    };
    check(memcmp(actual, expected, sizeof expected) == 0, "LOAD plus premultiplied blend");

    draw.vertex_byte_count--;
    check(!walle_lg_reveal_compositor_draw(compositor, &draw), "wrong vertex size rejection");
    draw.vertex_byte_count = sizeof vertices;
    indices[47]            = WALLE_LG_REVEAL_VERTEX_COUNT;
    check(!walle_lg_reveal_compositor_draw(compositor, &draw), "index range rejection");
    check(glGetError() == GL_NO_ERROR, "unexpected GL error");

    walle_lg_reveal_compositor_destroy(compositor);
    glDeleteTextures(1, &target);
    glDeleteProgram(program);
    destroy_context(&context);
    printf("pipelineEvidenceSha256=%s\n", WALLE_LG_REVEAL_PIPELINE_EVIDENCE_SHA256);
    printf("vertexStride=%d\n", WALLE_LG_REVEAL_VERTEX_STRIDE);
    printf("indexCount=%d\n", WALLE_LG_REVEAL_INDEX_COUNT);
    printf("attachment0LoadPreserved=true\n");
    printf("attachment1R8WriteDisabled=true\n");
    printf("premultipliedBlendExact=true\n");
    printf("ordinaryWalleLinked=false\n");
    return 0;
}
