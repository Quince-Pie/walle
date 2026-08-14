#define _POSIX_C_SOURCE 200809L

#include <GLES3/gl32.h>
#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../parity/liquid_glass_raster.h"

/*
 * Analysis-only experiment: replace native triangle clipping/rasterization for
 * the reveal mask with one integer-owned compute invocation per destination
 * pixel. The arithmetic body is taken from the production fragment shader at
 * build time; this file only supplies compute-stage plumbing and a new main.
 */

static const char production_fragment_source[] = {
#embed "../shaders/reveal_mask.frag.glsl" limit(32768) if_empty(0) suffix(, )
    0};

typedef void(GL_APIENTRYP draw_elements_fn)(GLenum, GLsizei, GLenum, const void*);
typedef void(GL_APIENTRYP buffer_sub_data_fn)(GLenum, GLintptr, GLsizeiptr, const void*);

static draw_elements_fn real_draw_elements;
static buffer_sub_data_fn real_buffer_sub_data;
static GLuint           compute_program;
static GLuint           compute_texture;
static GLuint           compute_framebuffer;
static GLuint           overlay_axis_texture;
static GLsizei          compute_width;
static GLsizei          compute_height;
static uint64_t         dispatch_count;
static uint64_t         dispatch_nanoseconds;
static uint64_t         overlay_triangle_count;
static uint64_t         unsupported_overlay_count;
static struct walle_lg_reveal_mask_vertex captured_vertices[WALLE_LG_REVEAL_MAX_VERTEX_COUNT];
static size_t                             captured_vertex_count;
static uint16_t captured_indices[WALLE_LG_REVEAL_MAX_INDEX_COUNT];
static size_t   captured_index_count;

static const uint8_t reveal_raster_p25[] = {
#embed "../parity/raster_p25_selector_ceil_bits.bin" limit(2097152) if_empty(0) suffix(, )
};

static_assert(sizeof(struct walle_lg_reveal_mask_vertex) == WALLE_LG_REVEAL_VERTEX_STRIDE);
static_assert(sizeof reveal_raster_p25 == 2u * 1024u * 1024u);

static void fail(const char* message)
{
    fprintf(stderr, "reveal direct compute interposer: %s\n", message);
    exit(EXIT_FAILURE);
}

static void require_gl(const char* operation)
{
    GLenum error = glGetError();
    if (error == GL_NO_ERROR)
        return;
    fprintf(stderr,
            "reveal direct compute interposer: %s returned GL error 0x%04x\n",
            operation,
            error);
    exit(EXIT_FAILURE);
}

static bool enabled(void)
{
    const char* value = getenv("WALLE_REVEAL_DIRECT_COMPUTE");
    return value != nullptr && strcmp(value, "1") == 0;
}

static void load_real_draw(void)
{
    void* symbol = dlsym(RTLD_NEXT, "glDrawElements");
    if (symbol == nullptr || sizeof symbol != sizeof real_draw_elements)
        fail("cannot resolve glDrawElements");
    memcpy(&real_draw_elements, &symbol, sizeof symbol);
}

static void load_real_buffer_sub_data(void)
{
    void* symbol = dlsym(RTLD_NEXT, "glBufferSubData");
    if (symbol == nullptr || sizeof symbol != sizeof real_buffer_sub_data)
        fail("cannot resolve glBufferSubData");
    memcpy(&real_buffer_sub_data, &symbol, sizeof symbol);
}

struct clip_vertex
{
    float position[4];
    float sdf[2];
};

static double triangle_area(const struct clip_vertex triangle[static 3])
{
    double ab_x = (double)triangle[1].position[0] - triangle[0].position[0];
    double ab_y = (double)triangle[1].position[1] - triangle[0].position[1];
    double ac_x = (double)triangle[2].position[0] - triangle[0].position[0];
    double ac_y = (double)triangle[2].position[1] - triangle[0].position[1];
    return ab_x * ac_y - ab_y * ac_x;
}

static bool position_equal(const struct clip_vertex* left, const struct clip_vertex* right)
{
    return left->position[0] == right->position[0]
           && left->position[1] == right->position[1];
}

static float rounded_intersection(float start, float end, long double fraction)
{
    volatile float rounded = (float)((long double)start
                                     + fraction * ((long double)end - (long double)start));
    return rounded;
}

static struct clip_vertex intersection(const struct clip_vertex* start,
                                       const struct clip_vertex* end,
                                       size_t                    axis,
                                       float                     edge)
{
    long double fraction = ((long double)edge - (long double)start->position[axis])
                           / ((long double)end->position[axis]
                              - (long double)start->position[axis]);
    struct clip_vertex result = *start;
    for (size_t component = 0; component < 4; ++component) {
        result.position[component]
            = component == axis
                  ? edge
                  : rounded_intersection(
                        start->position[component], end->position[component], fraction);
    }
    for (size_t component = 0; component < 2; ++component) {
        result.sdf[component]
            = rounded_intersection(start->sdf[component], end->sdf[component], fraction);
    }
    return result;
}

static size_t clip_triangle(const struct clip_vertex triangle[static 3],
                            struct clip_vertex       output[static 8])
{
    struct clip_plane
    {
        size_t axis;
        float  edge;
        bool   keep_greater;
    };
    static const struct clip_plane planes[] = {
        {0, -512.0f, true},
        {0, 2'560.0f, false},
        {1, -512.0f, true},
        {1, 2'560.0f, false},
    };
    struct clip_vertex current[8] = {triangle[0], triangle[1], triangle[2]};
    size_t             current_count = 3;
    for (size_t plane = 0; plane < sizeof planes / sizeof planes[0]; ++plane) {
        if (current_count == 0)
            break;
        struct clip_vertex next[8];
        size_t             next_count = 0;
        struct clip_vertex previous   = current[current_count - 1];
        bool previous_inside
            = planes[plane].keep_greater
                  ? previous.position[planes[plane].axis] >= planes[plane].edge
                  : previous.position[planes[plane].axis] <= planes[plane].edge;
        for (size_t index = 0; index < current_count; ++index) {
            struct clip_vertex vertex = current[index];
            bool inside
                = planes[plane].keep_greater
                      ? vertex.position[planes[plane].axis] >= planes[plane].edge
                      : vertex.position[planes[plane].axis] <= planes[plane].edge;
            if (inside) {
                if (!previous_inside) {
                    next[next_count++] = intersection(&previous,
                                                      &vertex,
                                                      planes[plane].axis,
                                                      planes[plane].edge);
                }
                next[next_count++] = vertex;
            } else if (previous_inside) {
                next[next_count++] = intersection(&previous,
                                                  &vertex,
                                                  planes[plane].axis,
                                                  planes[plane].edge);
            }
            previous        = vertex;
            previous_inside = inside;
        }

        current_count = 0;
        for (size_t index = 0; index < next_count; ++index) {
            if (current_count == 0 || !position_equal(&current[current_count - 1], &next[index]))
                current[current_count++] = next[index];
        }
        if (current_count > 1 && position_equal(&current[0], &current[current_count - 1]))
            --current_count;
    }
    memcpy(output, current, current_count * sizeof *output);
    return current_count;
}

static char* compute_source(void)
{
    const char* uniforms = strstr(production_fragment_source, "uniform vec2 RevealResolution;");
    const char* main     = strstr(production_fragment_source, "void main() {");
    if (uniforms == nullptr || main == nullptr || main <= uniforms)
        fail("production fragment shader shape changed");

    static const char header[] =
        "#version 320 es\n"
        "precision highp float;\n"
        "precision highp int;\n"
        "layout(local_size_x=8,local_size_y=8,local_size_z=1) in;\n"
        "layout(rgba8,binding=0) writeonly uniform highp image2D DirectOutput;\n"
        "uniform ivec2 DirectSize;\n"
        "uniform ivec2 DirectOffset;\n"
        "uniform ivec4 DirectScissor;\n";
    static const char replacement_main[] =
        "void main(){\n"
        " ivec2 g=ivec2(gl_GlobalInvocationID.xy)+DirectOffset;\n"
        " if(any(greaterThanEqual(g,DirectSize)))return;\n"
        " if(g.x<DirectScissor.x||g.y<DirectScissor.y"
        "||g.x>=DirectScissor.x+DirectScissor.z"
        "||g.y>=DirectScissor.y+DirectScissor.w)return;\n"
        " ivec2 c=ivec2(g.x,DirectSize.y-1-g.y);\n"
        " int code=ownerCode(c);if(code<=0)return;--code;\n"
        " int slot=code/2,primitive=code&1;\n"
        " float d=appleLength(exactCoordinates(c,slot,primitive));\n"
        " float dx=appleLength(exactCoordinates(ivec2(c.x^1,c.y),slot,primitive));\n"
        " float dy=appleLength(exactCoordinates(ivec2(c.x,c.y^1),slot,primitive));\n"
        " float feather=max(abs(dx-d)+abs(dy-d),1.0e-4);\n"
        " float alpha=clamp((1.0-d)/feather+0.5,0.0,1.0);\n"
        " float half_alpha=alpha==0.0||alpha==1.0?alpha:"
        "unpackHalf2x16(float32ToFloat16RNEBits(alpha)).x;\n"
        " float encoded=roundEven(half_alpha*255.0)/255.0;\n"
        " imageStore(DirectOutput,g,vec4(encoded,0.0,0.0,1.0));\n"
        "}\n";

    size_t body_bytes = (size_t)(main - uniforms);
    size_t total;
    if (__builtin_add_overflow(sizeof header - 1, body_bytes, &total)
        || __builtin_add_overflow(total, sizeof replacement_main, &total)) {
        fail("compute shader byte count overflows");
    }
    char* source = malloc(total);
    if (source == nullptr)
        fail("cannot allocate compute shader source");
    size_t offset = 0;
    memcpy(source + offset, header, sizeof header - 1);
    offset += sizeof header - 1;
    memcpy(source + offset, uniforms, body_bytes);
    offset += body_bytes;
    memcpy(source + offset, replacement_main, sizeof replacement_main);
    return source;
}

static GLuint compile_compute(void)
{
    char*        source = compute_source();
    const GLchar* text  = source;
    GLuint       shader = glCreateShader(GL_COMPUTE_SHADER);
    glShaderSource(shader, 1, &text, nullptr);
    glCompileShader(shader);
    free(source);
    GLint compiled = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (compiled != GL_TRUE) {
        char    log[8192];
        GLsizei length = 0;
        glGetShaderInfoLog(shader, sizeof log, &length, log);
        fprintf(stderr, "direct compute shader compilation failed:\n%.*s\n", (int)length, log);
        exit(EXIT_FAILURE);
    }
    GLuint program = glCreateProgram();
    glAttachShader(program, shader);
    glLinkProgram(program);
    glDeleteShader(shader);
    GLint linked = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked != GL_TRUE) {
        char    log[8192];
        GLsizei length = 0;
        glGetProgramInfoLog(program, sizeof log, &length, log);
        fprintf(stderr, "direct compute program link failed:\n%.*s\n", (int)length, log);
        exit(EXIT_FAILURE);
    }
    return program;
}

static GLint location(GLuint program, const char* name)
{
    GLint result = glGetUniformLocation(program, name);
    if (result < 0) {
        fprintf(stderr, "reveal direct compute interposer: missing uniform %s\n", name);
        exit(EXIT_FAILURE);
    }
    return result;
}

static void copy_scalar_array(GLuint source,
                              GLuint destination,
                              const char* base,
                              size_t      count)
{
    GLint values[18] = {};
    if (count > sizeof values / sizeof values[0])
        fail("scalar uniform array is too large");
    char name[64];
    for (size_t index = 0; index < count; ++index) {
        int length = snprintf(name, sizeof name, "%s[%zu]", base, index);
        if (length < 0 || (size_t)length >= sizeof name)
            fail("scalar uniform name overflows");
        GLint source_location = glGetUniformLocation(source, name);
        if (source_location < 0)
            fail("source scalar uniform is absent");
        glGetUniformiv(source, source_location, &values[index]);
    }
    glUniform1iv(location(destination, base), (GLsizei)count, values);
}

static void copy_vector_array(GLuint source,
                              GLuint destination,
                              const char* base,
                              size_t      count,
                              size_t      components)
{
    GLint values[16] = {};
    if (count * components > sizeof values / sizeof values[0])
        fail("vector uniform array is too large");
    char name[64];
    for (size_t index = 0; index < count; ++index) {
        int length = snprintf(name, sizeof name, "%s[%zu]", base, index);
        if (length < 0 || (size_t)length >= sizeof name)
            fail("vector uniform name overflows");
        GLint source_location = glGetUniformLocation(source, name);
        if (source_location < 0)
            fail("source vector uniform is absent");
        glGetUniformiv(source, source_location, &values[index * components]);
    }
    GLint destination_location = location(destination, base);
    if (components == 2) {
        glUniform2iv(destination_location, (GLsizei)count, values);
    } else if (components == 4) {
        glUniform4iv(destination_location, (GLsizei)count, values);
    } else {
        fail("unsupported vector width");
    }
}

static uint64_t elapsed_nanoseconds(struct timespec start, struct timespec finish)
{
    int64_t seconds = (int64_t)finish.tv_sec - (int64_t)start.tv_sec;
    int64_t nanoseconds = seconds * INT64_C(1'000'000'000) + (int64_t)finish.tv_nsec
                          - (int64_t)start.tv_nsec;
    return nanoseconds < 0 ? 0 : (uint64_t)nanoseconds;
}

static void ensure_compute_target(GLsizei width, GLsizei height)
{
    if (compute_framebuffer == 0)
        glGenFramebuffers(1, &compute_framebuffer);
    if (compute_width == width && compute_height == height)
        return;
    if (compute_texture != 0)
        glDeleteTextures(1, &compute_texture);
    glGenTextures(1, &compute_texture);
    glBindTexture(GL_TEXTURE_2D, compute_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, width, height);
    glBindFramebuffer(GL_FRAMEBUFFER, compute_framebuffer);
    glFramebufferTexture2D(
        GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, compute_texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
        fail("RGBA8 compute framebuffer is incomplete");
    compute_width  = width;
    compute_height = height;
}

struct dispatch_rectangle
{
    GLint   x;
    GLint   y;
    GLsizei width;
    GLsizei height;
};

static bool intersect_dispatch_rectangle(int64_t                         left,
                                         int64_t                         bottom,
                                         int64_t                         right,
                                         int64_t                         top,
                                         GLsizei                         framebuffer_width,
                                         GLsizei                         framebuffer_height,
                                         const GLint                     scissor[static 4],
                                         struct dispatch_rectangle* const result)
{
    if (framebuffer_width <= 0 || framebuffer_height <= 0 || scissor[2] < 0
        || scissor[3] < 0) {
        fail("invalid direct-dispatch dimensions");
    }
    int64_t scissor_right = (int64_t)scissor[0] + scissor[2];
    int64_t scissor_top   = (int64_t)scissor[1] + scissor[3];
    if (left < 0)
        left = 0;
    if (bottom < 0)
        bottom = 0;
    if (right > framebuffer_width)
        right = framebuffer_width;
    if (top > framebuffer_height)
        top = framebuffer_height;
    if (left < scissor[0])
        left = scissor[0];
    if (bottom < scissor[1])
        bottom = scissor[1];
    if (right > scissor_right)
        right = scissor_right;
    if (top > scissor_top)
        top = scissor_top;
    if (left >= right || bottom >= top)
        return false;
    result->x      = (GLint)left;
    result->y      = (GLint)bottom;
    result->width  = (GLsizei)(right - left);
    result->height = (GLsizei)(top - bottom);
    return true;
}

static bool source_triangle_outside_guard(const struct clip_vertex triangle[static 3])
{
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        float x = triangle[vertex].position[0];
        float y = triangle[vertex].position[1];
        if (x < -512.0f || x > 2'560.0f || y < -512.0f || y > 2'560.0f)
            return true;
    }
    return false;
}

static bool dispatch_overlay_triangle(const struct clip_vertex triangle[static 3],
                                      bool                     compact,
                                      GLsizei                  width,
                                      GLsizei                  height,
                                      const GLint              scissor[static 4])
{
    struct walle_lg_reveal_mask_geometry geometry = {
        .family = compact ? WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS
                          : WALLE_LG_REVEAL_MASK_BORDER_GRID,
        .vertex_count = 3,
        .index_count  = 6,
        .indices      = {0, 1, 2, 0, 0, 0},
    };
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        memcpy(geometry.vertices[vertex].position,
               triangle[vertex].position,
               sizeof triangle[vertex].position);
        float* coordinates = compact ? geometry.vertices[vertex].first_coordinates
                                     : geometry.vertices[vertex].second_coordinates;
        memcpy(coordinates, triangle[vertex].sdf, sizeof triangle[vertex].sdf);
    }
    const struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = reveal_raster_p25,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
    struct walle_lg_reveal_raster raster;
    if (!walle_lg_reveal_raster_construct(&geometry, &calibration, &raster)) {
        ++unsupported_overlay_count;
        return false;
    }
    if (raster.quad_count == 0) {
        walle_lg_reveal_raster_destroy(&raster);
        return true;
    }
    if (raster.quad_count != 1 || raster.packed_width == 0 || raster.packed_words == nullptr)
        fail("single-triangle overlay produced an invalid raster");

    const int32_t* visible_bounds = raster.quads[0].visible_bounds;
    struct dispatch_rectangle dispatch;
    if (!intersect_dispatch_rectangle(visible_bounds[0],
                                      (int64_t)height - visible_bounds[3],
                                      visible_bounds[2],
                                      (int64_t)height - visible_bounds[1],
                                      width,
                                      height,
                                      scissor,
                                      &dispatch)) {
        walle_lg_reveal_raster_destroy(&raster);
        return true;
    }

    if (overlay_axis_texture == 0)
        glGenTextures(1, &overlay_axis_texture);
    GLint prior_active;
    GLint prior_binding;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &prior_active);
    glActiveTexture(GL_TEXTURE13);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &prior_binding);
    glBindTexture(GL_TEXTURE_2D, overlay_axis_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RG32UI,
                 (GLsizei)raster.packed_width,
                 2,
                 0,
                 GL_RG_INTEGER,
                 GL_UNSIGNED_INT,
                 raster.packed_words);

    GLint starts[4]      = {raster.quads[0].axis_start};
    GLint origins[8]     = {};
    GLint extents[8]     = {};
    GLint bounds[16]     = {};
    GLint ascending[4]   = {raster.quads[0].ascending_diagonal ? 1 : 0};
    GLint active_masks[4] = {raster.quads[0].active_primitive_mask};
    for (size_t axis = 0; axis < 2; ++axis) {
        origins[axis] = raster.quads[0].origin_fixed[axis];
        extents[axis] = raster.quads[0].extent_fixed[axis];
    }
    for (size_t component = 0; component < 4; ++component)
        bounds[component] = raster.quads[0].visible_bounds[component];
    glUniform1i(location(compute_program, "AxisTable"), 13);
    glUniform1iv(location(compute_program, "AxisStarts"), 4, starts);
    glUniform1i(location(compute_program, "OwnerSlotCount"), 1);
    glUniform2iv(location(compute_program, "OwnerOriginFixed"), 4, origins);
    glUniform2iv(location(compute_program, "OwnerExtentFixed"), 4, extents);
    glUniform4iv(location(compute_program, "OwnerBounds"), 4, bounds);
    glUniform1iv(location(compute_program, "OwnerAscending"), 4, ascending);
    glUniform1iv(location(compute_program, "OwnerActiveMask"), 4, active_masks);
    glUniform2i(location(compute_program, "DirectOffset"), dispatch.x, dispatch.y);
    glUniform4i(location(compute_program, "DirectScissor"),
                dispatch.x,
                dispatch.y,
                dispatch.width,
                dispatch.height);
    require_gl("upload canonical overlay uniforms");

    glDispatchCompute(((GLuint)dispatch.width + 7u) / 8u,
                      ((GLuint)dispatch.height + 7u) / 8u,
                      1);
    require_gl("dispatch canonical overlay");
    glMemoryBarrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT);
    ++dispatch_count;
    ++overlay_triangle_count;
    glBindTexture(GL_TEXTURE_2D, (GLuint)prior_binding);
    glActiveTexture((GLenum)prior_active);
    walle_lg_reveal_raster_destroy(&raster);
    return true;
}

static void dispatch_postguard_overlays(GLuint       source_program,
                                        GLsizei      width,
                                        GLsizei      height,
                                        const GLint scissor[static 4])
{
    if (captured_vertex_count == 0 || captured_index_count == 0
        || captured_index_count % 6 != 0) {
        fail("captured reveal geometry is incomplete");
    }
    GLfloat compact_value = 0.0f;
    glGetUniformfv(
        source_program, location(source_program, "RevealCompactFamily"), &compact_value);
    bool   compact     = compact_value == 1.0f;
    size_t group_count = captured_index_count / 6;
    if (!compact && group_count > 4)
        group_count = 4;

    for (size_t group = 0; group < group_count; ++group) {
        struct clip_vertex source[6];
        for (size_t local = 0; local < 6; ++local) {
            uint16_t index = captured_indices[group * 6 + local];
            if (index >= captured_vertex_count)
                fail("captured reveal index is out of range");
            const struct walle_lg_reveal_mask_vertex* vertex = &captured_vertices[index];
            memcpy(source[local].position, vertex->position, sizeof source[local].position);
            const float* coordinates
                = compact ? vertex->first_coordinates : vertex->second_coordinates;
            memcpy(source[local].sdf, coordinates, sizeof source[local].sdf);
        }

        size_t first_triangle = 0;
        size_t triangle_count = 2;
        if (compact) {
            bool first_active  = triangle_area(source) != 0.0;
            bool second_active = triangle_area(source + 3) != 0.0;
            if (first_active == second_active)
                fail("compact group does not have exactly one active triangle");
            first_triangle = first_active ? 0 : 1;
            triangle_count = 1;
        }
        for (size_t ordinal = first_triangle; ordinal < first_triangle + triangle_count; ++ordinal) {
            const struct clip_vertex* triangle = source + ordinal * 3;
            if (!source_triangle_outside_guard(triangle))
                continue;
            struct clip_vertex clipped[8];
            size_t clipped_count = clip_triangle(triangle, clipped);
            if (clipped_count == 3) {
                if (triangle_area(clipped) != 0.0)
                    (void)dispatch_overlay_triangle(clipped, compact, width, height, scissor);
            } else if (clipped_count == 4) {
                struct clip_vertex first[3]  = {clipped[0], clipped[1], clipped[2]};
                struct clip_vertex second[3] = {clipped[0], clipped[2], clipped[3]};
                if (triangle_area(first) != 0.0)
                    (void)dispatch_overlay_triangle(first, compact, width, height, scissor);
                if (triangle_area(second) != 0.0)
                    (void)dispatch_overlay_triangle(second, compact, width, height, scissor);
            }
        }
    }
}

static void dispatch_direct(GLuint source_program)
{
    if (compute_program == 0)
        compute_program = compile_compute();

    GLint viewport[4];
    GLint scissor[4];
    GLint original_framebuffer = 0;
    glGetIntegerv(GL_VIEWPORT, viewport);
    glGetIntegerv(GL_SCISSOR_BOX, scissor);
    if (glIsEnabled(GL_SCISSOR_TEST) != GL_TRUE) {
        memcpy(scissor, viewport, sizeof scissor);
    }
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &original_framebuffer);
    if (viewport[0] != 0 || viewport[1] != 0 || viewport[2] <= 0 || viewport[3] <= 0
        || original_framebuffer <= 0) {
        fail("unsupported reveal viewport or framebuffer attachment");
    }

    GLint owner_count;
    glGetUniformiv(source_program, location(source_program, "OwnerSlotCount"), &owner_count);
    if (owner_count < 0 || owner_count > 4)
        fail("source owner count is out of range");
    int64_t owner_left   = INT64_MAX;
    int64_t owner_bottom = INT64_MAX;
    int64_t owner_right  = INT64_MIN;
    int64_t owner_top    = INT64_MIN;
    char    owner_name[32];
    for (GLint slot = 0; slot < owner_count; ++slot) {
        int length = snprintf(owner_name, sizeof owner_name, "OwnerBounds[%d]", slot);
        if (length < 0 || (size_t)length >= sizeof owner_name)
            fail("source owner uniform name overflows");
        GLint bounds[4];
        glGetUniformiv(source_program, location(source_program, owner_name), bounds);
        if (bounds[0] >= bounds[2] || bounds[1] >= bounds[3])
            continue;
        int64_t bottom = (int64_t)viewport[3] - bounds[3];
        int64_t top    = (int64_t)viewport[3] - bounds[1];
        if (owner_left > bounds[0])
            owner_left = bounds[0];
        if (owner_bottom > bottom)
            owner_bottom = bottom;
        if (owner_right < bounds[2])
            owner_right = bounds[2];
        if (owner_top < top)
            owner_top = top;
    }
    struct dispatch_rectangle dispatch;
    if (!intersect_dispatch_rectangle(owner_left,
                                      owner_bottom,
                                      owner_right,
                                      owner_top,
                                      viewport[2],
                                      viewport[3],
                                      scissor,
                                      &dispatch)) {
        return;
    }

    ensure_compute_target(viewport[2], viewport[3]);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)original_framebuffer);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, compute_framebuffer);
    glBlitFramebuffer(dispatch.x,
                      dispatch.y,
                      dispatch.x + dispatch.width,
                      dispatch.y + dispatch.height,
                      dispatch.x,
                      dispatch.y,
                      dispatch.x + dispatch.width,
                      dispatch.y + dispatch.height,
                      GL_COLOR_BUFFER_BIT,
                      GL_NEAREST);
    require_gl("copy R8 framebuffer into compute target");

    GLint axis_unit;
    GLint sqrt_unit;
    glGetUniformiv(source_program, location(source_program, "AxisTable"), &axis_unit);
    glGetUniformiv(
        source_program, location(source_program, "AppleFastSqrtTable"), &sqrt_unit);

    glUseProgram(compute_program);
    glUniform1i(location(compute_program, "AxisTable"), axis_unit);
    glUniform1i(location(compute_program, "AppleFastSqrtTable"), sqrt_unit);
    glUniform1i(location(compute_program, "OwnerSlotCount"), owner_count);
    glUniform2i(location(compute_program, "DirectSize"), viewport[2], viewport[3]);
    glUniform2i(location(compute_program, "DirectOffset"), dispatch.x, dispatch.y);
    glUniform4i(location(compute_program, "DirectScissor"),
                dispatch.x,
                dispatch.y,
                dispatch.width,
                dispatch.height);
    copy_scalar_array(source_program, compute_program, "AxisStarts", 4);
    copy_vector_array(source_program, compute_program, "OwnerOriginFixed", 4, 2);
    copy_vector_array(source_program, compute_program, "OwnerExtentFixed", 4, 2);
    copy_vector_array(source_program, compute_program, "OwnerBounds", 4, 4);
    copy_scalar_array(source_program, compute_program, "OwnerAscending", 4);
    copy_scalar_array(source_program, compute_program, "OwnerActiveMask", 4);
    require_gl("copy direct compute uniforms");

    glMemoryBarrier(GL_FRAMEBUFFER_BARRIER_BIT);
    require_gl("barrier before direct compute");
    glBindImageTexture(0, compute_texture, 0, GL_FALSE, 0, GL_WRITE_ONLY, GL_RGBA8);
    require_gl("bind compute output image");
    struct timespec start;
    struct timespec finish;
    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0)
        fail("cannot start dispatch clock");
    glDispatchCompute(((GLuint)dispatch.width + 7u) / 8u,
                      ((GLuint)dispatch.height + 7u) / 8u,
                      1);
    require_gl("dispatch direct compute");
    glMemoryBarrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT);
    dispatch_postguard_overlays(source_program, viewport[2], viewport[3], scissor);
    glMemoryBarrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT | GL_FRAMEBUFFER_BARRIER_BIT);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, compute_framebuffer);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)original_framebuffer);
    glBlitFramebuffer(dispatch.x,
                      dispatch.y,
                      dispatch.x + dispatch.width,
                      dispatch.y + dispatch.height,
                      dispatch.x,
                      dispatch.y,
                      dispatch.x + dispatch.width,
                      dispatch.y + dispatch.height,
                      GL_COLOR_BUFFER_BIT,
                      GL_NEAREST);
    require_gl("copy compute target into R8 framebuffer");
    glBindFramebuffer(GL_FRAMEBUFFER, (GLuint)original_framebuffer);
    if (clock_gettime(CLOCK_MONOTONIC, &finish) != 0)
        fail("cannot stop dispatch clock");
    ++dispatch_count;
    dispatch_nanoseconds += elapsed_nanoseconds(start, finish);
    glUseProgram(source_program);
    require_gl("finish direct compute dispatch");
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glBufferSubData(GLenum target, GLintptr offset, GLsizeiptr size, const void* data)
{
    if (real_buffer_sub_data == nullptr)
        load_real_buffer_sub_data();
    real_buffer_sub_data(target, offset, size, data);
    if (!enabled() || offset != 0 || data == nullptr)
        return;
    if (target == GL_ARRAY_BUFFER
        && size >= (GLsizeiptr)(3 * sizeof captured_vertices[0])
        && size <= (GLsizeiptr)sizeof captured_vertices
        && size % (GLsizeiptr)sizeof captured_vertices[0] == 0) {
        memcpy(captured_vertices, data, (size_t)size);
        captured_vertex_count = (size_t)size / sizeof captured_vertices[0];
    } else if (target == GL_ELEMENT_ARRAY_BUFFER
               && size >= (GLsizeiptr)(6 * sizeof captured_indices[0])
               && size <= (GLsizeiptr)sizeof captured_indices
               && size % (GLsizeiptr)(6 * sizeof captured_indices[0]) == 0) {
        memcpy(captured_indices, data, (size_t)size);
        captured_index_count = (size_t)size / sizeof captured_indices[0];
    }
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glDrawElements(GLenum mode, GLsizei count, GLenum type, const void* indices)
{
    if (real_draw_elements == nullptr)
        load_real_draw();
    if (!enabled() || mode != GL_TRIANGLES || type != GL_UNSIGNED_SHORT || indices != nullptr
        || count <= 0) {
        real_draw_elements(mode, count, type, indices);
        return;
    }

    GLint source_program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &source_program);
    if (source_program <= 0
        || glGetUniformLocation((GLuint)source_program, "OwnerSlotCount") < 0) {
        real_draw_elements(mode, count, type, indices);
        return;
    }
    dispatch_direct((GLuint)source_program);
}

__attribute__((destructor)) static void report(void)
{
    if (!enabled())
        return;
    fprintf(stderr,
            "REVEAL_DIRECT_COMPUTE_DISPATCHES=%llu\n"
            "REVEAL_DIRECT_COMPUTE_NANOSECONDS=%llu\n"
            "REVEAL_DIRECT_COMPUTE_OVERLAYS=%llu\n"
            "REVEAL_DIRECT_COMPUTE_UNSUPPORTED=%llu\n",
            (unsigned long long)dispatch_count,
            (unsigned long long)dispatch_nanoseconds,
            (unsigned long long)overlay_triangle_count,
            (unsigned long long)unsupported_overlay_count);
}
