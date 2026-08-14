#include <EGL/egl.h>
#include <EGL/eglext.h>
#if defined(VERIFY_CORE_OPENGL)
#    define GL_GLEXT_PROTOTYPES 1
#    include <GL/glcorearb.h>
#else
#    include <GLES3/gl3.h>
#endif
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

constexpr int OUTPUT_WIDTH  = 192;
constexpr int OUTPUT_HEIGHT = 128;
constexpr int GLASS_WIDTH   = OUTPUT_WIDTH / 8;
constexpr int GLASS_HEIGHT  = OUTPUT_HEIGHT / 8;

static const char vertex_source[] = {
#embed "../shaders/vert.glsl" limit(4096) if_empty(0) suffix(, )
    0};

static const char fragment_source[] = {
#embed "../shaders/frag.glsl" limit(16384) if_empty(0) suffix(, )
    0};

static void fail(const char* message);

static char* core_shader_source(const char* source)
{
#if defined(VERIFY_CORE_OPENGL)
    constexpr char es_version[]   = "#version 300 es\n";
    constexpr char core_version[] = "#version 450 core\n";
    constexpr char precision[]    = "precision highp float;\n";
    if (strncmp(source, es_version, sizeof es_version - 1) != 0)
        fail("production shader has an unrecognized version directive");

    const char* body = source + sizeof es_version - 1;
    if (strncmp(body, precision, sizeof precision - 1) == 0)
        body += sizeof precision - 1;
    size_t result_size = sizeof core_version - 1 + strlen(body) + 1;
    char*  result      = malloc(result_size);
    if (!result)
        fail("desktop shader source allocation failed");
    memcpy(result, core_version, sizeof core_version - 1);
    strcpy(result + sizeof core_version - 1, body);
    return result;
#else
    return (char*)source;
#endif
}

static void free_core_shader_source(char* source)
{
#if defined(VERIFY_CORE_OPENGL)
    free(source);
#else
    (void)source;
#endif
}

static void fail(const char* message)
{
    fprintf(stderr, "%s\n", message);
    exit(EXIT_FAILURE);
}

static void require_gl(const char* operation)
{
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        fprintf(stderr, "%s failed with GL error 0x%04x\n", operation, error);
        exit(EXIT_FAILURE);
    }
}

static GLuint compile_shader(GLenum type, const char* source)
{
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);

    GLint compiled = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (compiled == GL_TRUE)
        return shader;

    char    log[4096];
    GLsizei length = 0;
    glGetShaderInfoLog(shader, sizeof(log), &length, log);
    fprintf(stderr, "shader compilation failed:\n%.*s\n", (int)length, log);
    glDeleteShader(shader);
    return 0;
}

static GLuint create_program(void)
{
    char*  vertex_text   = core_shader_source(vertex_source);
    char*  fragment_text = core_shader_source(fragment_source);
    GLuint vertex        = compile_shader(GL_VERTEX_SHADER, vertex_text);
    GLuint fragment      = compile_shader(GL_FRAGMENT_SHADER, fragment_text);
    free_core_shader_source(vertex_text);
    free_core_shader_source(fragment_text);
    if (!vertex || !fragment)
        fail("could not compile the production shaders");

    GLuint program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glLinkProgram(program);
    glDeleteShader(vertex);
    glDeleteShader(fragment);

    GLint linked = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked == GL_TRUE)
        return program;

    char    log[4096];
    GLsizei length = 0;
    glGetProgramInfoLog(program, sizeof(log), &length, log);
    fprintf(stderr, "shader link failed:\n%.*s\n", (int)length, log);
    glDeleteProgram(program);
    return 0;
}

static uint32_t next_random(uint32_t* state)
{
    uint32_t value = *state;
    value ^= value << 13;
    value ^= value >> 17;
    value ^= value << 5;
    *state = value;
    return value;
}

static void fill_pixels(uint8_t* pixels, size_t pixel_count, uint32_t seed)
{
    for (size_t i = 0; i < pixel_count; i++) {
        uint32_t value    = next_random(&seed);
        pixels[4 * i]     = (uint8_t)value;
        pixels[4 * i + 1] = (uint8_t)(value >> 8);
        pixels[4 * i + 2] = (uint8_t)(value >> 16);
        pixels[4 * i + 3] = UINT8_MAX;
    }
}

static void configure_texture(GLuint texture)
{
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
}

static void upload_texture(GLuint pbo, GLuint texture, int width, int height, const uint8_t* pixels)
{
    size_t size = (size_t)width * (size_t)height * 4;
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, pbo);
    glBufferData(GL_PIXEL_UNPACK_BUFFER, (GLsizeiptr)size, nullptr, GL_STREAM_DRAW);
    void* mapped = glMapBufferRange(GL_PIXEL_UNPACK_BUFFER,
                                    0,
                                    (GLsizeiptr)size,
                                    GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT);
    if (!mapped)
        fail("could not map the verification PBO");
    memcpy(mapped, pixels, size);
    if (glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER) != GL_TRUE)
        fail("verification PBO contents became invalid");

    configure_texture(texture);
    glTexImage2D(
        GL_TEXTURE_2D, 0, GL_SRGB8_ALPHA8, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
    require_gl("texture upload");
}

static GLuint create_output(GLuint* texture)
{
    glGenTextures(1, texture);
    glBindTexture(GL_TEXTURE_2D, *texture);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RGBA8,
                 OUTPUT_WIDTH,
                 OUTPUT_HEIGHT,
                 0,
                 GL_RGBA,
                 GL_UNSIGNED_BYTE,
                 nullptr);

    GLuint framebuffer;
    glGenFramebuffers(1, &framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, *texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
        fail("verification framebuffer is incomplete");
    return framebuffer;
}

static void bind_inputs(GLuint program, const GLuint textures[4])
{
    static const char* const names[] = {"TexA", "TexGlassA", "TexB", "TexGlassB"};
    for (int unit = 0; unit < 4; unit++) {
        glActiveTexture((GLenum)(GL_TEXTURE0 + unit));
        glBindTexture(GL_TEXTURE_2D, textures[unit]);
        glUniform1i(glGetUniformLocation(program, names[unit]), unit);
    }
}

static void render_case(GLuint       program,
                        GLuint       vao,
                        GLuint       framebuffer,
                        const GLuint textures[4],
                        float        time,
                        float        variant,
                        float        center_x,
                        float        center_y,
                        uint8_t*     output)
{
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glViewport(0, 0, OUTPUT_WIDTH, OUTPUT_HEIGHT);
    glUseProgram(program);
    bind_inputs(program, textures);
    glUniform1f(glGetUniformLocation(program, "Time"), time);
    glUniform2f(glGetUniformLocation(program, "Resolution"), OUTPUT_WIDTH, OUTPUT_HEIGHT);
    glUniform2f(glGetUniformLocation(program, "CenterPointPixels"), center_x, center_y);
    glUniform1f(glGetUniformLocation(program, "MaxRadiusPixels"),
                hypotf(OUTPUT_WIDTH, OUTPUT_HEIGHT) * 1.03f);
    glUniform1f(glGetUniformLocation(program, "Variant"), variant);
    glBindVertexArray(vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glReadPixels(0, 0, OUTPUT_WIDTH, OUTPUT_HEIGHT, GL_RGBA, GL_UNSIGNED_BYTE, output);
    require_gl("verification render/readback");
}

int main(int argc, char** argv)
{
    if (argc > 2)
        fail("usage: shader gate [matrix-output]");
    const char* extensions = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
    if (!extensions || !strstr(extensions, "EGL_MESA_platform_surfaceless"))
        fail("EGL_MESA_platform_surfaceless is unavailable");
    auto get_platform_display
        = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!get_platform_display)
        fail("eglGetPlatformDisplayEXT is unavailable");

    EGLDisplay display
        = get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    EGLint major, minor;
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor))
        fail("could not initialize a surfaceless EGL display");

    const EGLint config_attributes[] = {EGL_SURFACE_TYPE,
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
#if defined(VERIFY_CORE_OPENGL)
                                        EGL_OPENGL_BIT,
#else
                                        EGL_OPENGL_ES3_BIT,
#endif
                                        EGL_NONE};
    EGLConfig config;
    EGLint    config_count = 0;
    if (!eglChooseConfig(display, config_attributes, &config, 1, &config_count)
        || config_count != 1)
        fail("could not choose the shader-gate pbuffer configuration");

    const EGLint pbuffer_attributes[]
        = {EGL_WIDTH, OUTPUT_WIDTH, EGL_HEIGHT, OUTPUT_HEIGHT, EGL_NONE};
    EGLSurface surface = eglCreatePbufferSurface(display, config, pbuffer_attributes);
#if defined(VERIFY_CORE_OPENGL)
    if (!eglBindAPI(EGL_OPENGL_API))
        fail("could not bind the OpenGL API");
    const EGLint context_attributes[] = {EGL_CONTEXT_MAJOR_VERSION_KHR,
                                         4,
                                         EGL_CONTEXT_MINOR_VERSION_KHR,
                                         5,
                                         EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR,
                                         EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,
                                         EGL_NONE};
#else
    if (!eglBindAPI(EGL_OPENGL_ES_API))
        fail("could not bind the OpenGL ES API");
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
#endif
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    if (surface == EGL_NO_SURFACE || context == EGL_NO_CONTEXT
        || !eglMakeCurrent(display, surface, surface, context))
        fail("could not create the shader-gate context");

    GLuint program = create_program();
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);

    constexpr float vertices[] = {-1.0f,
                                  -1.0f,
                                  0.0f,
                                  0.0f,
                                  1.0f,
                                  -1.0f,
                                  1.0f,
                                  0.0f,
                                  -1.0f,
                                  1.0f,
                                  0.0f,
                                  1.0f,
                                  1.0f,
                                  1.0f,
                                  1.0f,
                                  1.0f};
    GLuint          vao, vbo;
    glGenVertexArrays(1, &vao);
    glGenBuffers(1, &vbo);
    glBindVertexArray(vao);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), nullptr);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void*)(2 * sizeof(float)));

    size_t           standard_size = (size_t)OUTPUT_WIDTH * OUTPUT_HEIGHT * 4;
    size_t           glass_size    = (size_t)GLASS_WIDTH * GLASS_HEIGHT * 4;
    uint8_t*         standard      = malloc(standard_size);
    uint8_t*         glass         = malloc(glass_size);
    uint8_t*         reference     = malloc(standard_size);
    uint8_t*         optimized     = malloc(standard_size);
    constexpr size_t case_count    = 2 * 3 * 8;
    uint8_t*         matrix        = argc == 2 ? malloc(case_count * standard_size) : nullptr;
    if (!standard || !glass || !reference || !optimized || (argc == 2 && !matrix))
        fail("verification allocation failed");
    fill_pixels(standard, (size_t)OUTPUT_WIDTH * OUTPUT_HEIGHT, UINT32_C(0x9e3779b9));
    fill_pixels(glass, (size_t)GLASS_WIDTH * GLASS_HEIGHT, UINT32_C(0x243f6a88));

    GLuint pbo;
    GLuint inputs[4];
    glGenBuffers(1, &pbo);
    glGenTextures(4, inputs);
    upload_texture(pbo, inputs[0], OUTPUT_WIDTH, OUTPUT_HEIGHT, standard);
    upload_texture(pbo, inputs[1], GLASS_WIDTH, GLASS_HEIGHT, glass);
    upload_texture(pbo, inputs[2], OUTPUT_WIDTH, OUTPUT_HEIGHT, standard);
    upload_texture(pbo, inputs[3], GLASS_WIDTH, GLASS_HEIGHT, glass);

    GLuint       reference_texture, optimized_texture;
    GLuint       reference_fbo     = create_output(&reference_texture);
    GLuint       optimized_fbo     = create_output(&optimized_texture);
    const GLuint aliased_inputs[4] = {inputs[2], inputs[3], inputs[2], inputs[3]};

    constexpr float times[]      = {0.0f, 0.01f, 0.06f, 0.12f, 0.31f, 0.62f, 0.79f, 1.0f};
    constexpr float centers[][2] = {{96.0f, 64.0f}, {1.0f, 1.0f}, {151.25f, 27.75f}};
    size_t          cases        = 0;
    for (int variant = 0; variant < 2; variant++) {
        for (size_t center = 0; center < sizeof(centers) / sizeof(centers[0]); center++) {
            for (size_t time = 0; time < sizeof(times) / sizeof(times[0]); time++) {
                render_case(program,
                            vao,
                            reference_fbo,
                            inputs,
                            times[time],
                            (float)variant,
                            centers[center][0],
                            centers[center][1],
                            reference);

                /* Match Walle's optimized lifetime: queued texture transfers
                 * are complete from GL's perspective before the PBO store is
                 * orphaned, and A aliases byte-identical B on first boot. */
                glBindBuffer(GL_PIXEL_UNPACK_BUFFER, pbo);
                glBufferData(GL_PIXEL_UNPACK_BUFFER, 0, nullptr, GL_STREAM_DRAW);
                glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);

                render_case(program,
                            vao,
                            optimized_fbo,
                            aliased_inputs,
                            times[time],
                            (float)variant,
                            centers[center][0],
                            centers[center][1],
                            optimized);
                if (memcmp(reference, optimized, standard_size) != 0) {
                    size_t unequal = 0;
                    size_t first   = standard_size;
                    for (size_t i = 0; i < standard_size; i++) {
                        if (reference[i] != optimized[i]) {
                            if (first == standard_size)
                                first = i;
                            unequal++;
                        }
                    }
                    fprintf(stderr,
                            "alias gate failed: variant=%d center=%zu time=%.8g "
                            "unequalBytes=%zu firstByte=%zu reference=%u optimized=%u\n",
                            variant,
                            center,
                            (double)times[time],
                            unequal,
                            first,
                            reference[first],
                            optimized[first]);
                    return EXIT_FAILURE;
                }
                if (matrix)
                    memcpy(matrix + cases * standard_size, optimized, standard_size);
                cases++;
            }
        }
    }

    if (cases != case_count)
        fail("shader gate produced an incomplete matrix");
    if (matrix) {
        FILE* stream = fopen(argv[1], "wb");
        if (!stream || fwrite(matrix, standard_size, cases, stream) != cases || fclose(stream) != 0)
            fail("could not write the shader-gate matrix");
    }

#if defined(VERIFY_CORE_OPENGL)
    constexpr char api[] = "core OpenGL";
#else
    constexpr char api[] = "GLES3";
#endif
    printf("%s first-boot texture alias: %zu cases, 0 unequal RGBA8 bytes\n", api, cases);

    free(matrix);
    free(optimized);
    free(reference);
    free(glass);
    free(standard);
    glDeleteFramebuffers(1, &optimized_fbo);
    glDeleteFramebuffers(1, &reference_fbo);
    glDeleteTextures(1, &optimized_texture);
    glDeleteTextures(1, &reference_texture);
    glDeleteTextures(4, inputs);
    glDeleteBuffers(1, &pbo);
    glDeleteBuffers(1, &vbo);
    glDeleteVertexArrays(1, &vao);
    glDeleteProgram(program);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(display, context);
    eglDestroySurface(display, surface);
    eglTerminate(display);
    return EXIT_SUCCESS;
}
