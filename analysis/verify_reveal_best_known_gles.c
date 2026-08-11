#define _POSIX_C_SOURCE 200809L

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../parity/liquid_glass_raster.h"
#include "../parity/liquid_glass_reveal_mask_model.h"

constexpr int TEST_MASK_WIDTH  = 192;
constexpr int TEST_MASK_HEIGHT = 128;

static const char fullscreen_vertex_source[] = {
#embed "../shaders/vert.glsl" limit(4096) if_empty(0) suffix(, )
    0};

static const char reveal_vertex_source[] = {
#embed "../shaders/reveal_mask.vert.glsl" limit(4096) if_empty(0) suffix(, )
    0};

static const char reveal_fragment_source[] = {
#embed "../shaders/reveal_mask.frag.glsl" limit(32768) if_empty(0) suffix(, )
    0};

static const char composition_fragment_source[] = {
#embed "../shaders/frag_reveal_best_known.glsl" limit(16384) if_empty(0) suffix(, )
    0};

static const uint8_t reveal_raster_p25[] = {
#embed "../parity/raster_p25_selector_ceil_bits.bin" limit(2097152) if_empty(0) suffix(, )
};

static const uint8_t apple_fast_sqrt[] = {
#embed "../parity/apple_fast_sqrt_correction_nibbles.bin" limit(4194304) if_empty(0) suffix(, )
};

static_assert(sizeof reveal_raster_p25 == 2u * 1024u * 1024u);
static_assert(sizeof apple_fast_sqrt == 4u * 1024u * 1024u);
static_assert(sizeof reveal_fragment_source < 32'769);

static void fail(const char* message)
{
    fprintf(stderr, "best-known reveal GLES gate failed: %s\n", message);
    exit(EXIT_FAILURE);
}

static void check(bool condition, const char* message)
{
    if (!condition)
        fail(message);
}

static void require_gl(const char* operation)
{
    GLenum error = glGetError();
    if (error == GL_NO_ERROR)
        return;
    fprintf(stderr, "best-known reveal GLES gate: %s returned GL error 0x%04x\n", operation, error);
    exit(EXIT_FAILURE);
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

static GLuint create_program(const char* vertex_source, const char* fragment_source)
{
    GLuint vertex   = compile_shader(GL_VERTEX_SHADER, vertex_source);
    GLuint fragment = compile_shader(GL_FRAGMENT_SHADER, fragment_source);
    check(vertex != 0 && fragment != 0, "compile shaders");
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
    fprintf(stderr, "program link failed:\n%.*s\n", (int)length, log);
    glDeleteProgram(program);
    return 0;
}

static GLint required_uniform(GLuint program, const char* name)
{
    GLint location = glGetUniformLocation(program, name);
    if (location < 0) {
        fprintf(stderr, "required uniform is absent: %s\n", name);
        exit(EXIT_FAILURE);
    }
    return location;
}

static void configure_reveal_owner_block(GLuint program)
{
    GLint maximum_size = 0;
    glGetIntegerv(GL_MAX_UNIFORM_BLOCK_SIZE, &maximum_size);
    check(maximum_size >= (GLint)sizeof(struct walle_lg_reveal_owner_block),
          "reveal owner block fits GL_MAX_UNIFORM_BLOCK_SIZE");

    GLuint block = glGetUniformBlockIndex(program, "RevealOwnerBlock");
    check(block != GL_INVALID_INDEX, "reveal owner block is active");
    GLint block_size = 0;
    glGetActiveUniformBlockiv(program, block, GL_UNIFORM_BLOCK_DATA_SIZE, &block_size);
    check(block_size == (GLint)sizeof(struct walle_lg_reveal_owner_block),
          "reveal owner block has exact C/std140 size");

    const GLchar* names[] = {
        "OwnerCounts",
        "OwnerBounds[0]",
        "OwnerOriginExtent[0]",
        "OwnerControl[0]",
    };
    GLuint indices[4];
    glGetUniformIndices(program, 4, names, indices);
    for (size_t index = 0; index < 4; ++index)
        check(indices[index] != GL_INVALID_INDEX, "reveal owner block member is active");
    GLint offsets[4];
    glGetActiveUniformsiv(program, 4, indices, GL_UNIFORM_OFFSET, offsets);
    check(offsets[0] == 0 && offsets[1] == 16 && offsets[2] == 1'520
              && offsets[3] == 3'024,
          "reveal owner block member offsets match C/std140");
    GLint strides[3];
    glGetActiveUniformsiv(program, 3, indices + 1, GL_UNIFORM_ARRAY_STRIDE, strides);
    check(strides[0] == 16 && strides[1] == 16 && strides[2] == 16,
          "reveal owner block arrays use std140 ivec4 stride");
    GLint sizes[3];
    glGetActiveUniformsiv(program, 3, indices + 1, GL_UNIFORM_SIZE, sizes);
    check(sizes[0] == WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT
              && sizes[1] == WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT
              && sizes[2] == WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT,
          "reveal owner block exposes all 94 array elements");
    glUniformBlockBinding(program, block, WALLE_LG_REVEAL_OWNER_BLOCK_BINDING);
    require_gl("configure reveal owner block");
}

struct reveal_target
{
    GLuint texture;
    GLuint framebuffer;
    GLuint vertex_array;
    GLuint vertex_buffer;
    GLuint index_buffer;
};

struct reveal_arithmetic
{
    GLuint  owner_buffer;
    GLuint  axis_texture;
    GLuint  apple_fast_sqrt_texture;
    GLsizei axis_texture_width;
};

static struct reveal_arithmetic create_reveal_arithmetic(void)
{
    struct reveal_arithmetic arithmetic = {};
    glGenBuffers(1, &arithmetic.owner_buffer);
    glBindBuffer(GL_UNIFORM_BUFFER, arithmetic.owner_buffer);
    glBufferData(GL_UNIFORM_BUFFER,
                 sizeof(struct walle_lg_reveal_owner_block),
                 nullptr,
                 GL_STREAM_DRAW);
    glGenTextures(1, &arithmetic.axis_texture);
    glActiveTexture(GL_TEXTURE14);
    glBindTexture(GL_TEXTURE_2D, arithmetic.axis_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

    glGenTextures(1, &arithmetic.apple_fast_sqrt_texture);
    glActiveTexture(GL_TEXTURE15);
    glBindTexture(GL_TEXTURE_2D, arithmetic.apple_fast_sqrt_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_R8UI,
                 4096,
                 1024,
                 0,
                 GL_RED_INTEGER,
                 GL_UNSIGNED_BYTE,
                 apple_fast_sqrt);
    glActiveTexture(GL_TEXTURE0);
    require_gl("create reveal arithmetic textures");
    return arithmetic;
}

static bool upload_reveal_arithmetic(GLuint                                      program,
                                     struct reveal_arithmetic*                   arithmetic,
                                     const struct walle_lg_reveal_mask_geometry* geometry,
                                     uint32_t                                    target_width,
                                     uint32_t                                    target_height)
{
    const struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = reveal_raster_p25,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
    struct walle_lg_reveal_raster raster;
    check(walle_lg_reveal_raster_construct(
              geometry, target_width, target_height, &calibration, &raster)
              == WALLE_LG_REVEAL_RASTER_OK,
          "construct exact reveal raster");
    if (raster.owner_count == 0) {
        walle_lg_reveal_raster_destroy(&raster);
        return false;
    }
    check(raster.owner_count <= WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT
              && raster.base_owner_count > 0
              && raster.base_owner_count <= WALLE_LG_REVEAL_RASTER_MAX_BASE_OWNER_COUNT
              && raster.base_owner_count <= raster.owner_count && raster.packed_width > 0
              && raster.packed_words != nullptr
              && raster.owner_block.counts[0] == (int32_t)raster.owner_count
              && raster.owner_block.counts[1] == (int32_t)raster.base_owner_count,
          "nonempty geometry has packed reveal axes");

    GLint primitive_slots[WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT] = {};
    GLint primitive_rows[WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT]  = {};
    for (size_t primitive = 0; primitive < raster.original_primitive_count; ++primitive) {
        const struct walle_lg_reveal_raster_primitive* mapping = &raster.primitives[primitive];
        bool invalid_slot
            = mapping->packed_slot == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING;
        bool invalid_primitive
            = mapping->geometric_primitive == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING;
        check(invalid_slot == invalid_primitive, "primitive mapping sentinel is atomic");
        if (invalid_slot) {
            continue;
        }
        check(mapping->packed_slot < raster.base_owner_count && mapping->geometric_primitive < 2,
              "original primitive mapping addresses a base owner");
        primitive_slots[primitive] = mapping->packed_slot;
        primitive_rows[primitive]  = mapping->geometric_primitive;
    }

    glBindBuffer(GL_UNIFORM_BUFFER, arithmetic->owner_buffer);
    glBufferSubData(
        GL_UNIFORM_BUFFER, 0, sizeof raster.owner_block, &raster.owner_block);
    glBindBufferBase(
        GL_UNIFORM_BUFFER, WALLE_LG_REVEAL_OWNER_BLOCK_BINDING, arithmetic->owner_buffer);
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
    glActiveTexture(GL_TEXTURE14);
    glBindTexture(GL_TEXTURE_2D, arithmetic->axis_texture);
    if (arithmetic->axis_texture_width < (GLsizei)raster.packed_width) {
        glTexImage2D(GL_TEXTURE_2D,
                     0,
                     GL_RG32UI,
                     (GLsizei)raster.packed_width,
                     WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT
                         * WALLE_LG_RASTER_PRIMITIVE_COUNT,
                     0,
                     GL_RG_INTEGER,
                     GL_UNSIGNED_INT,
                     nullptr);
        arithmetic->axis_texture_width = (GLsizei)raster.packed_width;
    }
    glTexSubImage2D(GL_TEXTURE_2D,
                    0,
                    0,
                    0,
                    (GLsizei)raster.packed_width,
                    (GLsizei)(raster.owner_count * WALLE_LG_RASTER_PRIMITIVE_COUNT),
                    GL_RG_INTEGER,
                    GL_UNSIGNED_INT,
                    raster.packed_words);
    glUniform1i(required_uniform(program, "AxisTable"), 14);
    glActiveTexture(GL_TEXTURE15);
    glBindTexture(GL_TEXTURE_2D, arithmetic->apple_fast_sqrt_texture);
    glUniform1i(required_uniform(program, "AppleFastSqrtTable"), 15);
    glActiveTexture(GL_TEXTURE0);

    glUniform1iv(required_uniform(program, "PrimitiveSlots[0]"),
                 WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT,
                 primitive_slots);
    glUniform1iv(required_uniform(program, "PrimitiveRows[0]"),
                 WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT,
                 primitive_rows);
    require_gl("upload exact reveal arithmetic");
    walle_lg_reveal_raster_destroy(&raster);
    return true;
}

static struct reveal_target create_reveal_target(int width, int height)
{
    struct reveal_target target = {};
    glGenTextures(1, &target.texture);
    glBindTexture(GL_TEXTURE_2D, target.texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, width, height, 0, GL_RED, GL_UNSIGNED_BYTE, nullptr);
    glGenFramebuffers(1, &target.framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, target.framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, target.texture, 0);
    check(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE,
          "R8 framebuffer completeness");

    glGenVertexArrays(1, &target.vertex_array);
    glGenBuffers(1, &target.vertex_buffer);
    glGenBuffers(1, &target.index_buffer);
    glBindVertexArray(target.vertex_array);
    glBindBuffer(GL_ARRAY_BUFFER, target.vertex_buffer);
    glBufferData(GL_ARRAY_BUFFER,
                 WALLE_LG_REVEAL_MAX_VERTEX_COUNT * WALLE_LG_REVEAL_VERTEX_STRIDE,
                 nullptr,
                 GL_STREAM_DRAW);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, target.index_buffer);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER,
                 WALLE_LG_REVEAL_MAX_INDEX_COUNT * sizeof(uint16_t),
                 nullptr,
                 GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, WALLE_LG_REVEAL_VERTEX_STRIDE, (const void*)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(
        1, 2, GL_FLOAT, GL_FALSE, WALLE_LG_REVEAL_VERTEX_STRIDE, (const void*)(uintptr_t)16);
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(
        2, 2, GL_FLOAT, GL_FALSE, WALLE_LG_REVEAL_VERTEX_STRIDE, (const void*)(uintptr_t)24);
    require_gl("create reveal target");
    return target;
}

static void render_public_mask(GLuint                                      program,
                               const struct reveal_target*                 target,
                               struct reveal_arithmetic*                   arithmetic,
                               const struct walle_lg_reveal_mask_geometry* geometry,
                               int                                         width,
                               int                                         height,
                               uint8_t*                                    pixels)
{
    glBindFramebuffer(GL_FRAMEBUFFER, target->framebuffer);
    glViewport(0, 0, width, height);
    glDisable(GL_BLEND);
    glDisable(GL_CULL_FACE);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_SCISSOR_TEST);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    if (!geometry->circle.empty) {
        int32_t y = height - (geometry->circle.scissor[1] + geometry->circle.scissor[3]);
        check(y >= 0, "top-left to GL scissor conversion");
        glEnable(GL_SCISSOR_TEST);
        glScissor(geometry->circle.scissor[0],
                  y,
                  geometry->circle.scissor[2],
                  geometry->circle.scissor[3]);
        if (geometry->clear_to_inside) {
            glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
            glClear(GL_COLOR_BUFFER_BIT);
        }
    }
    if (geometry->index_count > 0) {
        glUseProgram(program);
        glUniform2f(required_uniform(program, "RevealResolution"), (float)width, (float)height);
        glUniform1f(required_uniform(program, "RevealCompactFamily"),
                    geometry->family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS ? 1.0f : 0.0f);
        if (upload_reveal_arithmetic(
                program, arithmetic, geometry, (uint32_t)width, (uint32_t)height)) {
            glBindVertexArray(target->vertex_array);
            glBindBuffer(GL_ARRAY_BUFFER, target->vertex_buffer);
            glBufferSubData(GL_ARRAY_BUFFER,
                            0,
                            (GLsizeiptr)(geometry->vertex_count * sizeof(geometry->vertices[0])),
                            geometry->vertices);
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, target->index_buffer);
            glBufferSubData(GL_ELEMENT_ARRAY_BUFFER,
                            0,
                            (GLsizeiptr)(geometry->index_count * sizeof(geometry->indices[0])),
                            geometry->indices);
            glDrawElements(
                GL_TRIANGLES, (GLsizei)geometry->index_count, GL_UNSIGNED_SHORT, nullptr);
        }
    }
    glDisable(GL_SCISSOR_TEST);
    if (pixels != nullptr)
        glReadPixels(0, 0, width, height, GL_RED, GL_UNSIGNED_BYTE, pixels);
    require_gl("render/read public reveal mask");
}

static void test_public_mask(GLuint                      program,
                             const struct reveal_target* target,
                             struct reveal_arithmetic*   arithmetic)
{
    size_t   pixel_count = (size_t)TEST_MASK_WIDTH * TEST_MASK_HEIGHT;
    uint8_t* pixels      = malloc(pixel_count);
    check(pixels != nullptr, "allocate mask readback");
    struct walle_lg_reveal_mask_request request = {
        .target_width   = TEST_MASK_WIDTH,
        .target_height  = TEST_MASK_HEIGHT,
        .center_x       = 37.25,
        .center_y       = 26.75,
        .maximum_radius = 145.0,
        .progress       = 0.0,
    };
    struct walle_lg_reveal_mask_geometry geometry;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry), "construct empty mask");
    render_public_mask(
        program, target, arithmetic, &geometry, TEST_MASK_WIDTH, TEST_MASK_HEIGHT, pixels);
    for (size_t index = 0; index < pixel_count; ++index)
        check(pixels[index] == 0, "empty mask remains zero");

    request.progress = 0.15;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry), "construct visible mask");
    render_public_mask(
        program, target, arithmetic, &geometry, TEST_MASK_WIDTH, TEST_MASK_HEIGHT, pixels);
    size_t full           = 0;
    size_t partial        = 0;
    size_t empty          = 0;
    double weighted_x     = 0.0;
    double weighted_top_y = 0.0;
    double weight         = 0.0;
    for (int gl_y = 0; gl_y < TEST_MASK_HEIGHT; ++gl_y) {
        for (int x = 0; x < TEST_MASK_WIDTH; ++x) {
            uint8_t value = pixels[(size_t)gl_y * TEST_MASK_WIDTH + (size_t)x];
            full += value == UINT8_MAX;
            partial += value > 0 && value < UINT8_MAX;
            empty += value == 0;
            double normalized = (double)value / 255.0;
            weighted_x += ((double)x + 0.5) * normalized;
            weighted_top_y += ((double)TEST_MASK_HEIGHT - (double)gl_y - 0.5) * normalized;
            weight += normalized;

            double top_y    = (double)TEST_MASK_HEIGHT - (double)gl_y - 0.5;
            double dx       = ((double)x + 0.5) - geometry.circle.center[0];
            double dy       = top_y - geometry.circle.center[1];
            double distance = hypot(dx, dy);
            if (distance <= (double)geometry.circle.radius - 2.0 && value != UINT8_MAX) {
                fprintf(stderr,
                        "core mismatch x=%d glY=%d topY=%.1f distance=%.9g radius=%.9g value=%u\n",
                        x,
                        gl_y,
                        top_y,
                        distance,
                        (double)geometry.circle.radius,
                        value);
                fail("circle core is fully covered");
            }
            if (distance >= (double)geometry.circle.radius + 2.0 && value != 0) {
                fprintf(
                    stderr,
                    "exterior mismatch x=%d glY=%d topY=%.1f distance=%.9g radius=%.9g value=%u\n",
                    x,
                    gl_y,
                    top_y,
                    distance,
                    (double)geometry.circle.radius,
                    value);
                fail("circle exterior is empty");
            }
        }
    }
    check(full > 0 && partial > 0 && empty > 0, "mask contains full/partial/empty coverage");
    check(fabs(weighted_x / weight - geometry.circle.center[0]) < 0.75,
          "mask horizontal center follows public input");
    check(fabs(weighted_top_y / weight - geometry.circle.center[1]) < 0.75,
          "mask top-left vertical center follows public input");

    bool found_compact = false;
    for (int step = 1; step <= 64 && !found_compact; ++step) {
        request.progress = (double)step / 64.0;
        check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
              "construct compact search geometry");
        found_compact = geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS;
    }
    check(found_compact && geometry.clear_to_inside, "public compact family is renderable");
    render_public_mask(
        program, target, arithmetic, &geometry, TEST_MASK_WIDTH, TEST_MASK_HEIGHT, pixels);
    full           = 0;
    partial        = 0;
    empty          = 0;
    weighted_x     = 0.0;
    weighted_top_y = 0.0;
    weight         = 0.0;
    for (int gl_y = 0; gl_y < TEST_MASK_HEIGHT; ++gl_y) {
        for (int x = 0; x < TEST_MASK_WIDTH; ++x) {
            uint8_t value = pixels[(size_t)gl_y * TEST_MASK_WIDTH + (size_t)x];
            full += value == UINT8_MAX;
            partial += value > 0 && value < UINT8_MAX;
            empty += value == 0;
            double normalized = (double)value / 255.0;
            weighted_x += ((double)x + 0.5) * normalized;
            weighted_top_y += ((double)TEST_MASK_HEIGHT - (double)gl_y - 0.5) * normalized;
            weight += normalized;
        }
    }
    check(full > 0 && partial > 0 && empty > 0,
          "partial compact mask contains full/partial/empty coverage");
    check(fabs(weighted_x / weight - geometry.circle.center[0]) < 0.75,
          "compact mask horizontal center follows public input");
    check(fabs(weighted_top_y / weight - geometry.circle.center[1]) < 0.75,
          "compact mask top-left vertical center follows public input");

    request.center_x       = 37.0;
    request.center_y       = 27.0;
    request.maximum_radius = 200.0;
    request.progress       = 1.0;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct fully covering compact mask");
    check(geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS && geometry.vertex_count == 0
              && geometry.index_count == 0 && geometry.clear_to_inside,
          "fully covering compact mask uses its inside clear only");
    render_public_mask(
        program, target, arithmetic, &geometry, TEST_MASK_WIDTH, TEST_MASK_HEIGHT, pixels);
    for (size_t index = 0; index < pixel_count; ++index)
        check(pixels[index] == UINT8_MAX, "fully covering compact mask is opaque");
    free(pixels);
}

static void test_general_extent_masks(GLuint program, struct reveal_arithmetic* arithmetic)
{
    static constexpr int extents[][2] = {
        {1, 1},
        {3, 5},
        {7, 9},
        {65, 33},
        {193, 127},
    };
    for (size_t extent = 0; extent < sizeof extents / sizeof extents[0]; ++extent) {
        int width  = extents[extent][0];
        int height = extents[extent][1];
        struct reveal_target target = create_reveal_target(width, height);
        size_t pixel_count = (size_t)width * (size_t)height;
        uint8_t* first = malloc(pixel_count);
        uint8_t* second = malloc(pixel_count);
        check(first != nullptr && second != nullptr, "allocate general-extent mask readbacks");
        double center_x = (double)width * 0.37;
        double center_y = (double)height * 0.61;
        double radius = fmax(hypot(center_x, center_y),
                             fmax(hypot((double)width - center_x, center_y),
                                  fmax(hypot(center_x, (double)height - center_y),
                                       hypot((double)width - center_x,
                                             (double)height - center_y))))
                        * 1.03;
        for (int state = 1; state < 8; state += 2) {
            const struct walle_lg_reveal_mask_request request = {
                .target_width   = (uint32_t)width,
                .target_height  = (uint32_t)height,
                .center_x       = center_x,
                .center_y       = center_y,
                .maximum_radius = radius,
                .progress       = (double)state / 8.0,
            };
            struct walle_lg_reveal_mask_geometry geometry;
            check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
                  "construct general-extent GPU geometry");
            render_public_mask(program, &target, arithmetic, &geometry, width, height, first);
            render_public_mask(program, &target, arithmetic, &geometry, width, height, second);
            check(memcmp(first, second, pixel_count) == 0,
                  "general-extent GPU mask is deterministic");
        }
        free(second);
        free(first);
        glDeleteBuffers(1, &target.index_buffer);
        glDeleteBuffers(1, &target.vertex_buffer);
        glDeleteVertexArrays(1, &target.vertex_array);
        glDeleteFramebuffers(1, &target.framebuffer);
        glDeleteTextures(1, &target.texture);
    }
}

static GLuint create_rgba_texture(uint8_t red, uint8_t green, uint8_t blue)
{
    GLuint texture;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    const uint8_t pixel[4] = {red, green, blue, UINT8_MAX};
    glTexImage2D(GL_TEXTURE_2D, 0, GL_SRGB8_ALPHA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
    return texture;
}

static uint8_t expected_srgb(uint8_t coverage)
{
    float linear = (float)coverage / 255.0f;
    float encoded
        = linear <= 0.0031308f ? 12.92f * linear : 1.055f * powf(linear, 1.0f / 2.4f) - 0.055f;
    return (uint8_t)lroundf(encoded * 255.0f);
}

static void test_mask_is_composition_authority(GLuint program)
{
    constexpr int width                = 6;
    constexpr int height               = 2;
    const uint8_t mask[width * height] = {
        0,
        1,
        127,
        128,
        254,
        255,
        0,
        1,
        127,
        128,
        254,
        255,
    };
    GLuint mask_texture;
    glGenTextures(1, &mask_texture);
    glBindTexture(GL_TEXTURE_2D, mask_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, width, height, 0, GL_RED, GL_UNSIGNED_BYTE, mask);

    GLuint output_texture;
    glGenTextures(1, &output_texture);
    glBindTexture(GL_TEXTURE_2D, output_texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    GLuint framebuffer;
    glGenFramebuffers(1, &framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, output_texture, 0);
    check(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE,
          "composition framebuffer completeness");

    const float vertices[] = {
        -1.0f,
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
        1.0f,
    };
    GLuint vertex_array;
    GLuint vertex_buffer;
    glGenVertexArrays(1, &vertex_array);
    glGenBuffers(1, &vertex_buffer);
    glBindVertexArray(vertex_array);
    glBindBuffer(GL_ARRAY_BUFFER, vertex_buffer);
    glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (const void*)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(
        1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (const void*)(2 * sizeof(float)));

    GLuint            black       = create_rgba_texture(0, 0, 0);
    GLuint            white       = create_rgba_texture(UINT8_MAX, UINT8_MAX, UINT8_MAX);
    const GLuint      textures[4] = {black, black, white, white};
    const char* const names[4]    = {"TexA", "TexGlassA", "TexB", "TexGlassB"};
    glViewport(0, 0, width, height);
    glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glUseProgram(program);
    for (int unit = 0; unit < 4; ++unit) {
        glActiveTexture((GLenum)(GL_TEXTURE0 + unit));
        glBindTexture(GL_TEXTURE_2D, textures[unit]);
        glUniform1i(required_uniform(program, names[unit]), unit);
    }
    glActiveTexture(GL_TEXTURE4);
    glBindTexture(GL_TEXTURE_2D, mask_texture);
    glUniform1i(required_uniform(program, "RevealMask"), 4);
    glUniform1f(required_uniform(program, "Time"), 1.0f);
    glUniform2f(required_uniform(program, "Resolution"), width, height);
    glUniform1f(required_uniform(program, "Variant"), 0.0f);
    /* Deliberately contradict the R8 coverage: this analytic circle is far
     * offscreen. Any hidden multiply/override would turn the ramp black. */
    glUniform2f(required_uniform(program, "RevealCenterPointPixels"), -100.0f, -100.0f);
    glUniform1f(required_uniform(program, "RevealRadiusPixels"), 1.0f);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    uint8_t output[width * height * 4];
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, output);
    require_gl("render/read R8-authoritative composition");
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            size_t  index    = ((size_t)y * width + (size_t)x) * 4;
            uint8_t expected = expected_srgb(mask[y * width + x]);
            int     delta    = (int)output[index] - expected;
            if (delta < -1 || delta > 1) {
                fprintf(stderr,
                        "composition mismatch x=%d y=%d mask=%u expected=%u actual=%u\n",
                        x,
                        y,
                        mask[y * width + x],
                        expected,
                        output[index]);
                fail("R8 mask controls composition red");
            }
            check(output[index] == output[index + 1] && output[index] == output[index + 2]
                      && output[index + 3] == UINT8_MAX,
                  "R8 mask controls all composition channels");
        }
    }

    glDeleteTextures(1, &white);
    glDeleteTextures(1, &black);
    glDeleteBuffers(1, &vertex_buffer);
    glDeleteVertexArrays(1, &vertex_array);
    glDeleteFramebuffers(1, &framebuffer);
    glDeleteTextures(1, &output_texture);
    glDeleteTextures(1, &mask_texture);
}

struct dump_request
{
    int         width;
    int         height;
    double      center_x;
    double      center_y;
    int         state;
    int         state_count;
    const char* output_path;
};

static int parse_integer(const char* text, int minimum, int maximum, const char* field)
{
    errno       = 0;
    char* end   = nullptr;
    long  value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < minimum || value > maximum) {
        fprintf(stderr, "invalid %s: %s\n", field, text);
        exit(EXIT_FAILURE);
    }
    return (int)value;
}

static double parse_finite_double(const char* text, const char* field)
{
    errno        = 0;
    char*  end   = nullptr;
    double value = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || !isfinite(value)) {
        fprintf(stderr, "invalid %s: %s\n", field, text);
        exit(EXIT_FAILURE);
    }
    return value;
}

static struct dump_request parse_dump_request(int argc, char* argv[])
{
    check(argc == 9 && strcmp(argv[1], "--dump-public-mask") == 0,
          "usage: --dump-public-mask WIDTH HEIGHT CENTER_X CENTER_Y STATE STATE_COUNT OUTPUT");
    struct dump_request request = {
        .width       = parse_integer(argv[2], 1, 16'384, "width"),
        .height      = parse_integer(argv[3], 1, 16'384, "height"),
        .center_x    = parse_finite_double(argv[4], "center x"),
        .center_y    = parse_finite_double(argv[5], "center y"),
        .state       = parse_integer(argv[6], 0, 1'000'000, "state"),
        .state_count = parse_integer(argv[7], 2, 1'000'001, "state count"),
        .output_path = argv[8],
    };
    check(request.state < request.state_count, "state must be below state count");
    return request;
}

static void dump_public_mask(GLuint                     program,
                             struct reveal_arithmetic*  arithmetic,
                             const struct dump_request* dump)
{
    GLint maximum_texture_size = 0;
    glGetIntegerv(GL_MAX_TEXTURE_SIZE, &maximum_texture_size);
    check(dump->width <= maximum_texture_size && dump->height <= maximum_texture_size,
          "dump target exceeds GL_MAX_TEXTURE_SIZE");
    size_t pixel_count = (size_t)dump->width * (size_t)dump->height;
    check(pixel_count / (size_t)dump->width == (size_t)dump->height,
          "dump target byte count overflows");

    double d1 = hypot(dump->center_x, dump->center_y);
    double d2 = hypot((double)dump->width - dump->center_x, dump->center_y);
    double d3 = hypot(dump->center_x, (double)dump->height - dump->center_y);
    double d4 = hypot((double)dump->width - dump->center_x, (double)dump->height - dump->center_y);
    struct walle_lg_reveal_mask_request request = {
        .target_width   = (uint32_t)dump->width,
        .target_height  = (uint32_t)dump->height,
        .center_x       = dump->center_x,
        .center_y       = dump->center_y,
        .maximum_radius = fmax(d1, fmax(d2, fmax(d3, d4))) * 1.03,
        .progress       = (double)dump->state / (double)(dump->state_count - 1),
    };
    struct walle_lg_reveal_mask_geometry geometry;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct dumped public mask");
    struct reveal_target target = create_reveal_target(dump->width, dump->height);
    uint8_t*             pixels = malloc(pixel_count);
    check(pixels != nullptr, "allocate dumped public mask");
    render_public_mask(program, &target, arithmetic, &geometry, dump->width, dump->height, pixels);

    FILE* output = fopen(dump->output_path, "wb");
    check(output != nullptr, "open dumped public mask");
    for (int row = dump->height; row-- > 0;) {
        check(fwrite(pixels + (size_t)row * (size_t)dump->width, (size_t)dump->width, 1, output)
                  == 1,
              "write dumped public mask");
    }
    check(fclose(output) == 0, "close dumped public mask");

    free(pixels);
    glDeleteBuffers(1, &target.index_buffer);
    glDeleteBuffers(1, &target.vertex_buffer);
    glDeleteVertexArrays(1, &target.vertex_array);
    glDeleteFramebuffers(1, &target.framebuffer);
    glDeleteTextures(1, &target.texture);
}

static double elapsed_seconds(struct timespec start, struct timespec finish)
{
    return (double)(finish.tv_sec - start.tv_sec)
           + (double)(finish.tv_nsec - start.tv_nsec) / 1'000'000'000.0;
}

static void
benchmark_public_mask(GLuint program, struct reveal_arithmetic* arithmetic, int iterations)
{
    constexpr int                       width   = 2'048;
    constexpr int                       height  = 2'048;
    struct reveal_target                target  = create_reveal_target(width, height);
    struct walle_lg_reveal_mask_request request = {
        .target_width   = width,
        .target_height  = height,
        .center_x       = 512.0,
        .center_y       = 614.4,
        .maximum_radius = 2164.104505809273,
    };

    for (int state = 1; state <= 64; ++state) {
        request.progress = (double)state / 64.0;
        struct walle_lg_reveal_mask_geometry geometry;
        check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
              "construct warm-up reveal geometry");
        render_public_mask(program, &target, arithmetic, &geometry, width, height, nullptr);
    }
    glFinish();
    require_gl("finish reveal warm-up");

    const struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = reveal_raster_p25,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
    struct timespec cpu_start;
    struct timespec cpu_finish;
    check(clock_gettime(CLOCK_MONOTONIC, &cpu_start) == 0, "start reveal CPU benchmark clock");
    for (int iteration = 0; iteration < iterations; ++iteration) {
        int state        = iteration % 64 + 1;
        request.progress = (double)state / 64.0;
        struct walle_lg_reveal_mask_geometry geometry;
        struct walle_lg_reveal_raster        raster;
        check(walle_lg_reveal_mask_geometry_construct(&request, &geometry)
                  && walle_lg_reveal_raster_construct(
                         &geometry, width, height, &calibration, &raster)
                         == WALLE_LG_REVEAL_RASTER_OK,
              "construct benchmark reveal raster");
        walle_lg_reveal_raster_destroy(&raster);
    }
    check(clock_gettime(CLOCK_MONOTONIC, &cpu_finish) == 0, "finish reveal CPU benchmark clock");

    struct timespec start;
    struct timespec finish;
    check(clock_gettime(CLOCK_MONOTONIC, &start) == 0, "start reveal benchmark clock");
    for (int iteration = 0; iteration < iterations; ++iteration) {
        int state        = iteration % 64 + 1;
        request.progress = (double)state / 64.0;
        struct walle_lg_reveal_mask_geometry geometry;
        check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
              "construct benchmark reveal geometry");
        render_public_mask(program, &target, arithmetic, &geometry, width, height, nullptr);
        glFinish();
    }
    check(clock_gettime(CLOCK_MONOTONIC, &finish) == 0, "finish reveal benchmark clock");
    require_gl("run reveal benchmark");

    struct timespec throughput_start;
    struct timespec throughput_finish;
    check(clock_gettime(CLOCK_MONOTONIC, &throughput_start) == 0,
          "start reveal throughput benchmark clock");
    for (int iteration = 0; iteration < iterations; ++iteration) {
        int state        = iteration % 64 + 1;
        request.progress = (double)state / 64.0;
        struct walle_lg_reveal_mask_geometry geometry;
        check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
              "construct throughput benchmark reveal geometry");
        render_public_mask(program, &target, arithmetic, &geometry, width, height, nullptr);
    }
    glFinish();
    check(clock_gettime(CLOCK_MONOTONIC, &throughput_finish) == 0,
          "finish reveal throughput benchmark clock");
    require_gl("run reveal throughput benchmark");

    double total            = elapsed_seconds(start, finish);
    double cpu_total        = elapsed_seconds(cpu_start, cpu_finish);
    double throughput_total = elapsed_seconds(throughput_start, throughput_finish);
    printf("benchmarkRenderer=%s\n", (const char*)glGetString(GL_RENDERER));
    printf("benchmarkIterations=%d\n", iterations);
    printf("benchmarkWarmTotalSeconds=%.9f\n", total);
    printf("benchmarkWarmMeanMilliseconds=%.6f\n", total * 1'000.0 / iterations);
    printf("benchmarkWarmFramesPerSecond=%.3f\n", iterations / total);
    printf("benchmarkWarmThroughputMeanMilliseconds=%.6f\n",
           throughput_total * 1'000.0 / iterations);
    printf("benchmarkWarmThroughputFramesPerSecond=%.3f\n", iterations / throughput_total);
    printf("benchmarkCpuRasterMeanMilliseconds=%.6f\n", cpu_total * 1'000.0 / iterations);
    printf("benchmarkSharedAppleTextureBytes=%u\n", 4096u * 1024u);
    printf("benchmarkSharedAxisTextureBytes=%lld\n",
           (long long)arithmetic->axis_texture_width * WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT
               * WALLE_LG_RASTER_PRIMITIVE_COUNT * WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT
               * (int)sizeof(uint32_t));

    glDeleteBuffers(1, &target.index_buffer);
    glDeleteBuffers(1, &target.vertex_buffer);
    glDeleteVertexArrays(1, &target.vertex_array);
    glDeleteFramebuffers(1, &target.framebuffer);
    glDeleteTextures(1, &target.texture);
}

int main(int argc, char* argv[])
{
    bool dump_mode           = argc > 1 && strcmp(argv[1], "--dump-public-mask") == 0;
    bool benchmark_mode      = argc > 1 && strcmp(argv[1], "--benchmark-public-mask") == 0;
    struct dump_request dump = {};
    if (dump_mode)
        dump = parse_dump_request(argc, argv);
    int benchmark_iterations = 0;
    if (benchmark_mode) {
        check(argc == 3, "usage: --benchmark-public-mask ITERATIONS");
        benchmark_iterations = parse_integer(argv[2], 1, 10'000, "benchmark iterations");
    }
    check(argc == 1 || dump_mode || benchmark_mode, "unknown command-line mode");
    const char* extensions = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
    check(extensions && strstr(extensions, "EGL_MESA_platform_surfaceless"),
          "EGL_MESA_platform_surfaceless availability");
    auto get_platform_display
        = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    check(get_platform_display != nullptr, "eglGetPlatformDisplayEXT availability");
    EGLDisplay display
        = get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    EGLint major;
    EGLint minor;
    check(display != EGL_NO_DISPLAY && eglInitialize(display, &major, &minor),
          "initialize surfaceless EGL");
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
        EGL_OPENGL_ES3_BIT,
        EGL_NONE,
    };
    EGLConfig config;
    EGLint    config_count = 0;
    check(eglChooseConfig(display, config_attributes, &config, 1, &config_count)
              && config_count == 1,
          "choose EGL config");
    const EGLint pbuffer_attributes[] = {
        EGL_WIDTH,
        TEST_MASK_WIDTH,
        EGL_HEIGHT,
        TEST_MASK_HEIGHT,
        EGL_NONE,
    };
    EGLSurface surface = eglCreatePbufferSurface(display, config, pbuffer_attributes);
    check(eglBindAPI(EGL_OPENGL_ES_API), "bind GLES API");
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    const EGLint context32_attributes[] = {EGL_CONTEXT_MAJOR_VERSION_KHR,
                                           3,
                                           EGL_CONTEXT_MINOR_VERSION_KHR,
                                           2,
                                           EGL_NONE};
    const char* display_extensions = eglQueryString(display, EGL_EXTENSIONS);
    const EGLint* selected_context_attributes
        = display_extensions && strstr(display_extensions, "EGL_KHR_create_context")
            ? context32_attributes
            : context_attributes;
    EGLContext context
        = eglCreateContext(display, config, EGL_NO_CONTEXT, selected_context_attributes);
    check(surface != EGL_NO_SURFACE && context != EGL_NO_CONTEXT
              && eglMakeCurrent(display, surface, surface, context),
          "create GLES3 context");
    GLint context_major = 0;
    GLint context_minor = 0;
    glGetIntegerv(GL_MAJOR_VERSION, &context_major);
    glGetIntegerv(GL_MINOR_VERSION, &context_minor);
    check(context_major > 3 || (context_major == 3 && context_minor >= 2),
          "OpenGL ES 3.2 context availability");
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);

    GLuint reveal_program = create_program(reveal_vertex_source, reveal_fragment_source);
    check(reveal_program != 0, "link reveal mask program");
    configure_reveal_owner_block(reveal_program);
    struct reveal_arithmetic arithmetic = create_reveal_arithmetic();
    if (dump_mode) {
        fprintf(stderr,
                "GL_VENDOR=%s\nGL_RENDERER=%s\nGL_VERSION=%s\n",
                (const char*)glGetString(GL_VENDOR),
                (const char*)glGetString(GL_RENDERER),
                (const char*)glGetString(GL_VERSION));
        dump_public_mask(reveal_program, &arithmetic, &dump);
    } else if (benchmark_mode) {
        benchmark_public_mask(reveal_program, &arithmetic, benchmark_iterations);
    } else {
        GLuint composition_program
            = create_program(fullscreen_vertex_source, composition_fragment_source);
        check(composition_program != 0, "link reveal composition program");
        struct reveal_target target = create_reveal_target(TEST_MASK_WIDTH, TEST_MASK_HEIGHT);
        test_public_mask(reveal_program, &target, &arithmetic);
        test_general_extent_masks(reveal_program, &arithmetic);
        test_mask_is_composition_authority(composition_program);
        glDeleteBuffers(1, &target.index_buffer);
        glDeleteBuffers(1, &target.vertex_buffer);
        glDeleteVertexArrays(1, &target.vertex_array);
        glDeleteFramebuffers(1, &target.framebuffer);
        glDeleteTextures(1, &target.texture);
        glDeleteProgram(composition_program);
    }
    glDeleteBuffers(1, &arithmetic.owner_buffer);
    glDeleteTextures(1, &arithmetic.apple_fast_sqrt_texture);
    glDeleteTextures(1, &arithmetic.axis_texture);
    glDeleteProgram(reveal_program);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(display, context);
    eglDestroySurface(display, surface);
    eglTerminate(display);
    if (!dump_mode && !benchmark_mode)
        printf("best-known reveal GLES: geometry mask and R8 composition gate passed\n");
    return EXIT_SUCCESS;
}
