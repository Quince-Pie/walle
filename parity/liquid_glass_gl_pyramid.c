#define GL_GLEXT_PROTOTYPES 1
#include "liquid_glass_gl_pyramid.h"

#include "liquid_glass_pyramid.h"

#include <GL/glcorearb.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum
{
    PRODUCER_META_WORDS = 12,
};

struct walle_lg_gl_pyramid_builder
{
    GLuint program;
    GLuint metadata_buffer;
    GLuint axis_buffer;
    GLuint texture;
    uint32_t width;
    uint32_t height;
    uint32_t level_count;
};

static GLuint compile_compute_shader(const char* source)
{
    GLuint shader = glCreateShader(GL_COMPUTE_SHADER);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint compiled = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (compiled == GL_TRUE)
        return shader;
    char    log[16384];
    GLsizei length = 0;
    glGetShaderInfoLog(shader, sizeof log, &length, log);
    fprintf(stderr, "Liquid Glass backdrop compute shader failed:\n%.*s\n", (int)length, log);
    glDeleteShader(shader);
    return 0;
}

static GLuint link_compute_program(const char* source)
{
    GLuint shader = compile_compute_shader(source);
    if (shader == 0)
        return 0;
    GLuint program = glCreateProgram();
    glAttachShader(program, shader);
    glLinkProgram(program);
    glDeleteShader(shader);
    GLint linked = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked == GL_TRUE)
        return program;
    char    log[16384];
    GLsizei length = 0;
    glGetProgramInfoLog(program, sizeof log, &length, log);
    fprintf(stderr, "Liquid Glass backdrop compute link failed:\n%.*s\n", (int)length, log);
    glDeleteProgram(program);
    return 0;
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

static void uniform_2i(GLuint program, const char* name, GLint x, GLint y)
{
    GLint location = glGetUniformLocation(program, name);
    if (location >= 0)
        glUniform2i(location, x, y);
}

static void uniform_2f(GLuint program, const char* name, GLfloat x, GLfloat y)
{
    GLint location = glGetUniformLocation(program, name);
    if (location >= 0)
        glUniform2f(location, x, y);
}

static void uniform_4i(
    GLuint program, const char* name, GLint x, GLint y, GLint width, GLint height)
{
    GLint location = glGetUniformLocation(program, name);
    if (location >= 0)
        glUniform4i(location, x, y, width, height);
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

struct walle_lg_gl_pyramid_builder* walle_lg_gl_pyramid_builder_create(
    const char* compute_shader)
{
    if (compute_shader == nullptr)
        return nullptr;
    struct walle_lg_gl_pyramid_builder* builder = calloc(1, sizeof(*builder));
    if (builder == nullptr)
        return nullptr;
    builder->program = link_compute_program(compute_shader);
    if (builder->program == 0) {
        walle_lg_gl_pyramid_builder_destroy(builder);
        return nullptr;
    }
    glGenBuffers(1, &builder->metadata_buffer);
    glGenBuffers(1, &builder->axis_buffer);
    glGenTextures(1, &builder->texture);
    if (builder->metadata_buffer == 0 || builder->axis_buffer == 0 || builder->texture == 0
        || glGetError() != GL_NO_ERROR) {
        walle_lg_gl_pyramid_builder_destroy(builder);
        return nullptr;
    }
    return builder;
}

static bool ensure_texture(struct walle_lg_gl_pyramid_builder* builder,
                           uint32_t                             width,
                           uint32_t                             height,
                           uint32_t                             levels)
{
    if (builder->width == width && builder->height == height && builder->level_count == levels)
        return true;
    if (builder->texture != 0)
        glDeleteTextures(1, &builder->texture);
    glGenTextures(1, &builder->texture);
    glBindTexture(GL_TEXTURE_2D, builder->texture);
    glTexStorage2D(GL_TEXTURE_2D, (GLsizei)levels, GL_RGBA8, (GLsizei)width, (GLsizei)height);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST_MIPMAP_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 0);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, (GLint)levels - 1);
    if (glGetError() != GL_NO_ERROR) {
        builder->width       = 0;
        builder->height      = 0;
        builder->level_count = 0;
        return false;
    }
    builder->width       = width;
    builder->height      = height;
    builder->level_count = levels;
    return true;
}

static bool upload_raster(struct walle_lg_gl_pyramid_builder* builder,
                          const struct walle_lg_producer_raster* raster)
{
    int32_t metadata[WALLE_LG_PRODUCER_MAX_QUAD_COUNT * PRODUCER_META_WORDS] = {};
    size_t  axis_word_count = 0;
    for (uint32_t quad = 0; quad < raster->quad_count; ++quad)
        axis_word_count += (size_t)raster->quads[quad].axis_count * 2u * WALLE_LG_RASTER_CHANNEL_COUNT;
    if (axis_word_count == 0 || axis_word_count > SIZE_MAX / sizeof(uint32_t))
        return false;
    uint32_t* axes = malloc(axis_word_count * sizeof(uint32_t));
    if (axes == nullptr)
        return false;
    size_t axis_offset = 0;
    for (uint32_t quad = 0; quad < raster->quad_count; ++quad) {
        const struct walle_lg_producer_raster_quad* source = &raster->quads[quad];
        int32_t* destination = &metadata[quad * PRODUCER_META_WORDS];
        destination[0]  = source->origin_fixed[0];
        destination[1]  = source->origin_fixed[1];
        destination[2]  = source->extent_fixed[0];
        destination[3]  = source->extent_fixed[1];
        destination[4]  = source->visible_bounds[0];
        destination[5]  = source->visible_bounds[1];
        destination[6]  = source->visible_bounds[2];
        destination[7]  = source->visible_bounds[3];
        destination[8]  = source->axis_start;
        destination[9]  = (int32_t)source->axis_count;
        if (axis_offset > INT32_MAX) {
            free(axes);
            return false;
        }
        destination[10] = (int32_t)axis_offset;
        destination[11] = source->ascending_diagonal;
        size_t words = (size_t)source->axis_count * 2u * WALLE_LG_RASTER_CHANNEL_COUNT;
        memcpy(&axes[axis_offset], source->axis_bits, words * sizeof(uint32_t));
        axis_offset += words;
    }
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, builder->metadata_buffer);
    glBufferData(GL_SHADER_STORAGE_BUFFER,
                 (GLsizeiptr)(raster->quad_count * PRODUCER_META_WORDS * sizeof(int32_t)),
                 metadata,
                 GL_STREAM_DRAW);
    glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 0, builder->metadata_buffer);
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, builder->axis_buffer);
    glBufferData(GL_SHADER_STORAGE_BUFFER,
                 (GLsizeiptr)(axis_word_count * sizeof(uint32_t)),
                 axes,
                 GL_STREAM_DRAW);
    glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 1, builder->axis_buffer);
    free(axes);
    return glGetError() == GL_NO_ERROR;
}

static void dispatch_level(GLuint texture, uint32_t level, uint32_t width, uint32_t height)
{
    glBindImageTexture(0, texture, (GLint)level, GL_FALSE, 0, GL_WRITE_ONLY, GL_RGBA8);
    glDispatchCompute((width + 7u) / 8u, (height + 7u) / 8u, 1);
    glMemoryBarrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT | GL_TEXTURE_FETCH_BARRIER_BIT);
}

bool walle_lg_gl_pyramid_builder_build(
    struct walle_lg_gl_pyramid_builder*      builder,
    GLuint                                   source_texture,
    uint32_t                                 source_width,
    uint32_t                                 source_height,
    const struct walle_lg_transition_frame* frame,
    const struct walle_lg_raster_calibration* calibration)
{
    if (builder == nullptr || source_texture == 0 || source_width == 0 || source_height == 0
        || source_width > INT_MAX || source_height > INT_MAX
        || frame == nullptr || calibration == nullptr || frame->material != WALLE_LG_MATERIAL_REGULAR
        || frame->selected_region.level_count == 0
        || frame->selected_region.level_count > WALLE_LG_MAX_PYRAMID_LEVELS)
        return false;
    while (glGetError() != GL_NO_ERROR) {
    }
    struct walle_lg_producer_raster raster = {};
    if (!walle_lg_producer_raster_construct(
            frame, source_width, source_height, calibration, &raster))
        return false;
    uint32_t width  = frame->selected_region.allocated_extent[0];
    uint32_t height = frame->selected_region.allocated_extent[1];
    bool success = ensure_texture(builder, width, height, frame->selected_region.level_count)
                   && upload_raster(builder, &raster);
    if (!success) {
        walle_lg_producer_raster_destroy(&raster);
        return false;
    }
    glUseProgram(builder->program);
    uniform_i(builder->program, "InputTexture", 0);
    uniform_i(builder->program, "PyramidTexture", 1);
    uniform_i(builder->program, "Mode", 0);
    uniform_i(builder->program, "ProducerKind", frame->producer_mesh.kind);
    uniform_i(builder->program, "ProducerQuadCount", (GLint)raster.quad_count);
    uniform_i(builder->program, "PyramidLevel", 0);
    uniform_2i(builder->program, "SourceExtent", (GLint)source_width, (GLint)source_height);
    uniform_2i(builder->program,
               "ActiveExtent",
               (GLint)frame->producer.active_extent[0],
               (GLint)frame->producer.active_extent[1]);
    uniform_2i(builder->program,
               "CopyOffset",
               frame->selected_region.copy_offset[0],
               frame->selected_region.copy_offset[1]);
    uniform_4i(builder->program,
               "ProducerScissor",
               frame->producer_mesh.scissor[0],
               frame->producer_mesh.scissor[1],
               frame->producer_mesh.scissor[2],
               frame->producer_mesh.scissor[3]);
    float downsample_offset_x = 0.0f;
    float downsample_offset_y = 0.0f;
    if (frame->producer_mesh.kind == WALLE_LG_PRODUCER_DOWNSAMPLE_4) {
        float radicand
            = float32_subtract(float32_multiply(3.0f, frame->visible_fraction), 2.0f);
        if (!(radicand >= 0.0f)) {
            walle_lg_producer_raster_destroy(&raster);
            return false;
        }
        float radius   = sqrtf(radicand);
        downsample_offset_x = float32_divide(radius, (float)source_width);
        downsample_offset_y = float32_divide(radius, (float)source_height);
    }
    uniform_2f(
        builder->program, "DownsampleOffset", downsample_offset_x, downsample_offset_y);
    uniform_ui(builder->program, "ArithmeticBarrier", 0);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, source_texture);
    glActiveTexture(GL_TEXTURE1);
    glBindTexture(GL_TEXTURE_2D, builder->texture);
    dispatch_level(builder->texture, 0, width, height);
    for (uint32_t level = 1; level < frame->selected_region.level_count; ++level) {
        width /= 2u;
        height /= 2u;
        uniform_i(builder->program, "Mode", level == 1u ? 1 : 2);
        uniform_i(builder->program, "PyramidLevel", (GLint)level - 1);
        dispatch_level(builder->texture, level, width, height);
    }
    glMemoryBarrier(GL_TEXTURE_UPDATE_BARRIER_BIT);
    success = glGetError() == GL_NO_ERROR;
    walle_lg_producer_raster_destroy(&raster);
    return success;
}

GLuint walle_lg_gl_pyramid_builder_texture(
    const struct walle_lg_gl_pyramid_builder* builder)
{
    return builder == nullptr ? 0 : builder->texture;
}

bool walle_lg_gl_pyramid_builder_read_rgba8(
    const struct walle_lg_gl_pyramid_builder* builder,
    uint32_t                                  level,
    void*                                     pixels,
    size_t                                    byte_count)
{
    if (builder == nullptr || pixels == nullptr || level >= builder->level_count)
        return false;
    uint32_t width  = builder->width >> level;
    uint32_t height = builder->height >> level;
    if (width == 0)
        width = 1;
    if (height == 0)
        height = 1;
    if (byte_count != (size_t)width * height * 4u)
        return false;
    glBindTexture(GL_TEXTURE_2D, builder->texture);
    glGetTexImage(GL_TEXTURE_2D, (GLint)level, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    return glGetError() == GL_NO_ERROR;
}

void walle_lg_gl_pyramid_builder_destroy(struct walle_lg_gl_pyramid_builder* builder)
{
    if (builder == nullptr)
        return;
    if (builder->texture != 0)
        glDeleteTextures(1, &builder->texture);
    if (builder->axis_buffer != 0)
        glDeleteBuffers(1, &builder->axis_buffer);
    if (builder->metadata_buffer != 0)
        glDeleteBuffers(1, &builder->metadata_buffer);
    if (builder->program != 0)
        glDeleteProgram(builder->program);
    free(builder);
}
