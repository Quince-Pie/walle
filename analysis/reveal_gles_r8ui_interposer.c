#define _POSIX_C_SOURCE 200809L

#include <GLES3/gl3.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void(GL_APIENTRYP shader_source_fn)(GLuint, GLsizei, const GLchar* const*, const GLint*);
typedef void(GL_APIENTRYP tex_image_2d_fn)(GLenum,
                                          GLint,
                                          GLint,
                                          GLsizei,
                                          GLsizei,
                                          GLint,
                                          GLenum,
                                          GLenum,
                                          const void*);
typedef void(GL_APIENTRYP read_pixels_fn)(GLint,
                                         GLint,
                                         GLsizei,
                                         GLsizei,
                                         GLenum,
                                         GLenum,
                                         void*);
typedef void(GL_APIENTRYP clear_color_fn)(GLfloat, GLfloat, GLfloat, GLfloat);
typedef void(GL_APIENTRYP clear_fn)(GLbitfield);
typedef void(GL_APIENTRYP clear_buffer_uiv_fn)(GLenum, GLint, const GLuint*);

static shader_source_fn     real_shader_source;
static tex_image_2d_fn      real_tex_image_2d;
static read_pixels_fn       real_read_pixels;
static clear_color_fn       real_clear_color;
static clear_fn             real_clear;
static clear_buffer_uiv_fn  real_clear_buffer_uiv;
static GLfloat              clear_red;
static bool                 integer_target_active;

static void fail(const char* message)
{
    fprintf(stderr, "reveal R8UI interposer: %s\n", message);
    exit(EXIT_FAILURE);
}

static void load_real_symbol(void* destination, size_t size, const char* name)
{
    void* symbol = dlsym(RTLD_NEXT, name);
    if (symbol == nullptr || size != sizeof symbol)
        fail("cannot resolve a GLES entry point");
    memcpy(destination, &symbol, size);
}

static char* join_source(GLsizei count, const GLchar* const* strings, const GLint* lengths)
{
    size_t total = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != nullptr && lengths[index] >= 0 ? (size_t)lengths[index]
                                                                  : strlen(strings[index]);
        if (length > SIZE_MAX - total - 1)
            return nullptr;
        total += length;
    }
    char* result = malloc(total + 1);
    if (result == nullptr)
        return nullptr;
    size_t offset = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != nullptr && lengths[index] >= 0 ? (size_t)lengths[index]
                                                                  : strlen(strings[index]);
        memcpy(result + offset, strings[index], length);
        offset += length;
    }
    result[offset] = '\0';
    return result;
}

static char* replace_once(const char* source, const char* needle, const char* replacement)
{
    const char* match = strstr(source, needle);
    if (match == nullptr)
        return nullptr;
    size_t prefix = (size_t)(match - source);
    size_t source_length = strlen(source);
    size_t needle_length = strlen(needle);
    size_t replacement_length = strlen(replacement);
    if (source_length - needle_length > SIZE_MAX - replacement_length - 1)
        return nullptr;
    char* result = malloc(source_length - needle_length + replacement_length + 1);
    if (result == nullptr)
        return nullptr;
    memcpy(result, source, prefix);
    memcpy(result + prefix, replacement, replacement_length);
    memcpy(result + prefix + replacement_length,
           match + needle_length,
           source_length - prefix - needle_length + 1);
    return result;
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glShaderSource(GLuint shader, GLsizei count, const GLchar* const* strings, const GLint* lengths)
{
    if (real_shader_source == nullptr)
        load_real_symbol(&real_shader_source, sizeof real_shader_source, "glShaderSource");
    char* source = join_source(count, strings, lengths);
    if (source == nullptr)
        fail("cannot join shader source");
    if (strstr(source, "AxisTable") == nullptr || strstr(source, "RevealCoverage") == nullptr) {
        free(source);
        real_shader_source(shader, count, strings, lengths);
        return;
    }
    const char* declaration = "layout(location=0) out float RevealCoverage;";
    char* integer_source = replace_once(
        source,
        declaration,
        "layout(location=0) out highp uint RevealCoverage;");
    if (integer_source == nullptr)
        fail("axis fragment output declaration differs");
    char* final_source = replace_once(integer_source,
                                      "RevealCoverage=roundEven(h*255.0)/255.0;",
                                      "RevealCoverage=uint(roundEven(h*255.0));");
    free(integer_source);
    free(source);
    if (final_source == nullptr)
        fail("axis fragment final encoding differs");
    const GLchar* replacement = final_source;
    real_shader_source(shader, 1, &replacement, nullptr);
    free(final_source);
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glTexImage2D(GLenum      target,
             GLint       level,
             GLint       internal_format,
             GLsizei     width,
             GLsizei     height,
             GLint       border,
             GLenum      format,
             GLenum      type,
             const void* pixels)
{
    if (real_tex_image_2d == nullptr)
        load_real_symbol(&real_tex_image_2d, sizeof real_tex_image_2d, "glTexImage2D");
    if (target == GL_TEXTURE_2D && level == 0 && internal_format == GL_R8 && format == GL_RED
        && type == GL_UNSIGNED_BYTE && pixels == nullptr) {
        integer_target_active = true;
        real_tex_image_2d(target,
                          level,
                          GL_R8UI,
                          width,
                          height,
                          border,
                          GL_RED_INTEGER,
                          type,
                          pixels);
        return;
    }
    real_tex_image_2d(
        target, level, internal_format, width, height, border, format, type, pixels);
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glReadPixels(GLint x, GLint y, GLsizei width, GLsizei height, GLenum format, GLenum type, void* pixels)
{
    if (real_read_pixels == nullptr)
        load_real_symbol(&real_read_pixels, sizeof real_read_pixels, "glReadPixels");
    if (integer_target_active && format == GL_RED && type == GL_UNSIGNED_BYTE)
        format = GL_RED_INTEGER;
    real_read_pixels(x, y, width, height, format, type, pixels);
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glClearColor(GLfloat red, GLfloat green, GLfloat blue, GLfloat alpha)
{
    if (real_clear_color == nullptr)
        load_real_symbol(&real_clear_color, sizeof real_clear_color, "glClearColor");
    clear_red = red;
    real_clear_color(red, green, blue, alpha);
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY glClear(GLbitfield mask)
{
    if (real_clear == nullptr)
        load_real_symbol(&real_clear, sizeof real_clear, "glClear");
    if (real_clear_buffer_uiv == nullptr) {
        load_real_symbol(&real_clear_buffer_uiv,
                         sizeof real_clear_buffer_uiv,
                         "glClearBufferuiv");
    }
    if (integer_target_active && (mask & GL_COLOR_BUFFER_BIT) != 0) {
        GLfloat clamped = clear_red < 0.0f ? 0.0f : clear_red > 1.0f ? 1.0f : clear_red;
        GLuint value = (GLuint)(clamped * 255.0f + 0.5f);
        real_clear_buffer_uiv(GL_COLOR, 0, &value);
        mask &= (GLbitfield)~(GLbitfield)GL_COLOR_BUFFER_BIT;
    }
    if (mask != 0)
        real_clear(mask);
}
