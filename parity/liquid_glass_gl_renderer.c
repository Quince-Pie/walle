#define GL_GLEXT_PROTOTYPES 1
#include "liquid_glass_gl_renderer.h"

#include <GL/glcorearb.h>
#include <float.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct geometry
{
    GLuint vao;
    GLuint vertex_buffer;
    GLuint index_buffer;
};

struct walle_lg_gl_renderer
{
    GLuint program[2];

    struct geometry main;
    struct geometry shadow;
    struct geometry highlight;

    GLuint source_texture;
    GLuint destination_texture;
    GLuint color_texture;
    GLuint coefficient_texture;
    GLuint main_axis_texture;
    GLuint highlight_axis_texture;
    GLuint intrinsic_texture;
    GLuint shadow_coefficient_texture;
    GLuint shadow_slope_texture;
    GLuint framebuffer;

    uint32_t width;
    uint32_t height;
};

struct uniform_field
{
    const char* name;
    size_t      offset;
    size_t      count;
    bool        half;
};

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(_Float16) == 2);

static void print_shader_log(GLuint shader, const char* stage)
{
    char    log[8192];
    GLsizei length = 0;
    glGetShaderInfoLog(shader, sizeof log, &length, log);
    fprintf(stderr, "%s shader compilation failed:\n%.*s\n", stage, (int)length, log);
}

static GLuint compile_shader(GLenum type, const char* source, const char* stage)
{
    if (source == nullptr)
        return 0;
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint compiled = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (compiled == GL_TRUE)
        return shader;
    print_shader_log(shader, stage);
    glDeleteShader(shader);
    return 0;
}

static GLuint link_program(const char* vertex_source, const char* fragment_source)
{
    GLuint vertex   = compile_shader(GL_VERTEX_SHADER, vertex_source, "vertex");
    GLuint fragment = compile_shader(GL_FRAGMENT_SHADER, fragment_source, "fragment");
    if (vertex == 0 || fragment == 0) {
        if (vertex != 0)
            glDeleteShader(vertex);
        if (fragment != 0)
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
    if (linked == GL_TRUE)
        return program;
    char    log[8192];
    GLsizei length = 0;
    glGetProgramInfoLog(program, sizeof log, &length, log);
    fprintf(stderr, "Liquid Glass program link failed:\n%.*s\n", (int)length, log);
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
    if (count == 2)
        glUniform2fv(location, 1, values);
    else if (count == 3)
        glUniform3fv(location, 1, values);
    else if (count == 4)
        glUniform4fv(location, 1, values);
}

static float load_float(const uint8_t* payload, size_t offset)
{
    float value;
    memcpy(&value, payload + offset, sizeof value);
    return value;
}

static float load_half(const uint8_t* payload, size_t offset)
{
    _Float16 value;
    memcpy(&value, payload + offset, sizeof value);
    return (float)value;
}

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

static void apply_profile(GLuint program, const uint8_t* profile)
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

static void apply_highlight(GLuint program, const uint8_t* payload)
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
        {"KeyFillParams0", 0xd0, 4, true},
        {"KeyFillParams1", 0xd8, 4, true},
        {"KeyFillParams2", 0xe0, 4, true},
        {"KeyFillColor0", 0xe8, 4, true},
        {"KeyFillColor1", 0xf0, 4, true},
    };
    apply_uniform_fields(program, payload, fields, sizeof fields / sizeof fields[0]);
}

static void configure_fixed_uniforms(GLuint program)
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
    } values[] = {
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
        {"UseAppleShadowInterpolantModel", 1},
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
        {"HighlightCoordinateMode", 1},
        {"HighlightAlphaUlpBias", 0},
        {"HighlightFloatDivisionMode", 1},
        {"HighlightCoverageArithmeticMode", 1},
        {"HighlightMixMode", 0},
        {"HighlightBandMode", 0},
        {"HighlightNormalizeMode", 1},
        {"HighlightNormalizedCoordinateMode", 0},
        {"HighlightSdfArithmeticMode", 0},
        {"HighlightSdfNormalMode", 0},
        {"HighlightSdfSquaredUlpBias", 0},
        {"HighlightSdfDistanceUlpBias", 0},
        {"HighlightVibrantArithmeticMode", 10},
        {"HighlightSourceDivisionMode", 0},
        {"HighlightSourceConstructionMode", 1},
        {"HighlightDestinationDivisionMode", 0},
        {"UseAppleHighlightAlphaTrace", 0},
        {"UseAppleHighlightSourceTrace", 0},
        {"UseAppleHighlightGeometryTrace", 0},
    };
    for (size_t index = 0; index < sizeof values / sizeof values[0]; ++index)
        uniform_i(program, values[index].name, values[index].value);
    uniform_ui(program, "AppleInterpolantSourceLowBits", 0);
    uniform_ui(program, "AppleFastSqrtBias", 0);
    uniform_ui(program, "AppleFastReciprocalBias", 1);
    uniform_ui(program, "ArithmeticBarrier", 0);
}

static void initialize_geometry(struct geometry* geometry, bool indexed)
{
    glGenVertexArrays(1, &geometry->vao);
    glBindVertexArray(geometry->vao);
    glGenBuffers(1, &geometry->vertex_buffer);
    glBindBuffer(GL_ARRAY_BUFFER, geometry->vertex_buffer);
    if (indexed) {
        glGenBuffers(1, &geometry->index_buffer);
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, geometry->index_buffer);
    }
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, sizeof(struct walle_lg_vertex), nullptr);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1,
                          2,
                          GL_FLOAT,
                          GL_FALSE,
                          sizeof(struct walle_lg_vertex),
                          (void*)(uintptr_t)offsetof(struct walle_lg_vertex, sdf));
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(2,
                          2,
                          GL_FLOAT,
                          GL_FALSE,
                          sizeof(struct walle_lg_vertex),
                          (void*)(uintptr_t)offsetof(struct walle_lg_vertex, source));
}

static void upload_geometry(const struct geometry*        geometry,
                            const struct walle_lg_vertex* vertices,
                            size_t                        vertex_count,
                            const uint16_t*               indices,
                            size_t                        index_count)
{
    glBindVertexArray(geometry->vao);
    glBindBuffer(GL_ARRAY_BUFFER, geometry->vertex_buffer);
    glBufferData(
        GL_ARRAY_BUFFER, (GLsizeiptr)(vertex_count * sizeof(*vertices)), vertices, GL_STREAM_DRAW);
    if (indices != nullptr) {
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, geometry->index_buffer);
        glBufferData(GL_ELEMENT_ARRAY_BUFFER,
                     (GLsizeiptr)(index_count * sizeof(*indices)),
                     indices,
                     GL_STREAM_DRAW);
    }
}

static void destroy_geometry(struct geometry* geometry)
{
    if (geometry->index_buffer != 0)
        glDeleteBuffers(1, &geometry->index_buffer);
    if (geometry->vertex_buffer != 0)
        glDeleteBuffers(1, &geometry->vertex_buffer);
    if (geometry->vao != 0)
        glDeleteVertexArrays(1, &geometry->vao);
    *geometry = (struct geometry){};
}

static void texture_parameters(GLenum min_filter, GLenum mag_filter)
{
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, min_filter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, mag_filter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
}

static void bind_texture(GLuint program, const char* name, GLint unit, GLuint texture)
{
    uniform_i(program, name, unit);
    glActiveTexture((GLenum)(GL_TEXTURE0 + unit));
    glBindTexture(GL_TEXTURE_2D, texture);
}

static bool initialize_programs(struct walle_lg_gl_renderer*               renderer,
                                const struct walle_lg_gl_renderer_sources* sources)
{
    const char* fragments[2] = {
        sources->clear_fragment_shader,
        sources->regular_fragment_shader,
    };
    for (size_t material = 0; material < 2; ++material) {
        if (fragments[material] == nullptr)
            continue;
        renderer->program[material] = link_program(sources->vertex_shader, fragments[material]);
        if (renderer->program[material] == 0)
            return false;
        glUseProgram(renderer->program[material]);
        configure_fixed_uniforms(renderer->program[material]);
    }
    glUseProgram(0);
    return renderer->program[WALLE_LG_MATERIAL_REGULAR] != 0;
}

struct walle_lg_gl_renderer*
walle_lg_gl_renderer_create(const struct walle_lg_gl_renderer_sources* sources)
{
    if (sources == nullptr || sources->vertex_shader == nullptr
        || sources->regular_fragment_shader == nullptr
        || sources->float_intrinsic_table == nullptr) {
        return nullptr;
    }
    struct walle_lg_gl_renderer* renderer = calloc(1, sizeof(*renderer));
    if (renderer == nullptr)
        return nullptr;
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    if (!initialize_programs(renderer, sources)) {
        walle_lg_gl_renderer_destroy(renderer);
        return nullptr;
    }
    initialize_geometry(&renderer->main, false);
    initialize_geometry(&renderer->shadow, true);
    initialize_geometry(&renderer->highlight, true);

    GLuint textures[9];
    glGenTextures(9, textures);
    renderer->source_texture             = textures[0];
    renderer->destination_texture        = textures[1];
    renderer->color_texture              = textures[2];
    renderer->coefficient_texture        = textures[3];
    renderer->main_axis_texture          = textures[4];
    renderer->highlight_axis_texture     = textures[5];
    renderer->intrinsic_texture          = textures[6];
    renderer->shadow_coefficient_texture = textures[7];
    renderer->shadow_slope_texture       = textures[8];
    glGenFramebuffers(1, &renderer->framebuffer);

    glBindTexture(GL_TEXTURE_2D, renderer->intrinsic_texture);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_R8UI,
                 4096,
                 2048,
                 0,
                 GL_RED_INTEGER,
                 GL_UNSIGNED_BYTE,
                 sources->float_intrinsic_table);
    texture_parameters(GL_NEAREST, GL_NEAREST);
    if (glGetError() != GL_NO_ERROR) {
        walle_lg_gl_renderer_destroy(renderer);
        return nullptr;
    }
    return renderer;
}

static bool validate_frame(const struct walle_lg_gl_frame* frame)
{
    if (frame == nullptr || frame->transition == nullptr || frame->raster == nullptr
        || frame->destination.width == 0 || frame->destination.height == 0
        || (frame->destination.pixels == nullptr) == (frame->destination_texture == 0)
        || frame->source_mip_count == 0
        || frame->source_mip_count != frame->transition->selected_region.level_count
        || frame->transition->material != WALLE_LG_MATERIAL_REGULAR
        || frame->transition->appearance != WALLE_LG_APPEARANCE_DARK
        || frame->raster->axis_extent
               != (frame->destination.width > frame->destination.height ? frame->destination.width
                                                                        : frame->destination.height)
        || frame->raster->coefficient_word_count
               != 2u * frame->raster->coefficient_width * WALLE_LG_RASTER_CHANNEL_COUNT
        || frame->raster->main_axis_word_count
               != 2u * frame->raster->axis_extent * WALLE_LG_RASTER_CHANNEL_COUNT
        || frame->raster->shadow_coefficient_word_count
               != 16u * WALLE_LG_SHADOW_COEFFICIENT_TILE_COUNT * WALLE_LG_RASTER_CHANNEL_COUNT
        || frame->raster->shadow_slope_word_count
               != WALLE_LG_SHADOW_QUAD_COUNT * WALLE_LG_RASTER_CHANNEL_COUNT
        || frame->raster->highlight_axis_rows
               != (frame->transition->highlight_index_count == 24 ? 8u : 2u)
        || frame->raster->highlight_axis_word_count
               != frame->raster->highlight_axis_rows * frame->raster->axis_extent
                      * WALLE_LG_RASTER_CHANNEL_COUNT) {
        return false;
    }
    uint32_t width  = frame->transition->selected_region.allocated_extent[0];
    uint32_t height = frame->transition->selected_region.allocated_extent[1];
    if (frame->source_texture != 0) {
        if (frame->source_mips != nullptr || frame->source_texture_width != width
            || frame->source_texture_height != height) {
            return false;
        }
    } else if (frame->source_mips == nullptr) {
        return false;
    }
    for (uint32_t level = 0; level < frame->source_mip_count; ++level) {
        if (frame->source_texture == 0
            && (frame->source_mips[level].pixels == nullptr
                || frame->source_mips[level].width != width
                || frame->source_mips[level].height != height)) {
            return false;
        }
        width  = width > 1 ? width / 2 : 1;
        height = height > 1 ? height / 2 : 1;
    }
    const int32_t* scissor = frame->transition->background_scissor;
    return scissor[0] >= 0 && scissor[1] >= 0 && scissor[2] > 0 && scissor[3] > 0
           && (uint64_t)(uint32_t)scissor[0] + (uint32_t)scissor[2] <= frame->destination.width
           && (uint64_t)(uint32_t)scissor[1] + (uint32_t)scissor[3] <= frame->destination.height;
}

static void upload_source(GLuint texture, const struct walle_lg_gl_frame* frame)
{
    glBindTexture(GL_TEXTURE_2D, texture);
    for (uint32_t level = 0; level < frame->source_mip_count; ++level) {
        const struct walle_lg_rgba8_image* mip = &frame->source_mips[level];
        glTexImage2D(GL_TEXTURE_2D,
                     (GLint)level,
                     GL_RGBA8,
                     (GLsizei)mip->width,
                     (GLsizei)mip->height,
                     0,
                     GL_RGBA,
                     GL_UNSIGNED_BYTE,
                     mip->pixels);
    }
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 0);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, (GLint)frame->source_mip_count - 1);
    texture_parameters(GL_LINEAR_MIPMAP_LINEAR, GL_LINEAR);
}

static void upload_rgba8(GLuint texture, const struct walle_lg_rgba8_image* image)
{
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RGBA8,
                 (GLsizei)image->width,
                 (GLsizei)image->height,
                 0,
                 GL_RGBA,
                 GL_UNSIGNED_BYTE,
                 image->pixels);
    texture_parameters(GL_NEAREST, GL_NEAREST);
}

static void allocate_rgba8(GLuint texture, uint32_t width, uint32_t height)
{
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RGBA8,
                 (GLsizei)width,
                 (GLsizei)height,
                 0,
                 GL_RGBA,
                 GL_UNSIGNED_BYTE,
                 nullptr);
    texture_parameters(GL_NEAREST, GL_NEAREST);
}

static void upload_rgba32ui(GLuint texture, uint32_t width, uint32_t height, const uint32_t* words)
{
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RGBA32UI,
                 (GLsizei)width,
                 (GLsizei)height,
                 0,
                 GL_RGBA_INTEGER,
                 GL_UNSIGNED_INT,
                 words);
    texture_parameters(GL_NEAREST, GL_NEAREST);
}

static bool upload_frame_resources(struct walle_lg_gl_renderer*    renderer,
                                   const struct walle_lg_gl_frame* frame)
{
    upload_geometry(
        &renderer->main, frame->transition->main_vertices, WALLE_LG_MAIN_VERTEX_COUNT, nullptr, 0);
    upload_geometry(&renderer->shadow,
                    frame->transition->shadow_vertices,
                    WALLE_LG_SHADOW_VERTEX_COUNT,
                    frame->transition->shadow_indices,
                    WALLE_LG_SHADOW_INDEX_COUNT);
    upload_geometry(&renderer->highlight,
                    frame->transition->highlight_vertices,
                    frame->transition->highlight_vertex_count,
                    frame->transition->highlight_indices,
                    frame->transition->highlight_index_count);
    if (frame->source_texture == 0) {
        upload_source(renderer->source_texture, frame);
    } else {
        glBindTexture(GL_TEXTURE_2D, frame->source_texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 0);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, (GLint)frame->source_mip_count - 1);
        texture_parameters(GL_LINEAR_MIPMAP_LINEAR, GL_LINEAR);
    }
    if (frame->destination_texture == 0) {
        upload_rgba8(renderer->destination_texture, &frame->destination);
        upload_rgba8(renderer->color_texture, &frame->destination);
    } else {
        allocate_rgba8(renderer->destination_texture,
                       frame->destination.width,
                       frame->destination.height);
        allocate_rgba8(renderer->color_texture, frame->destination.width, frame->destination.height);
        glCopyImageSubData(frame->destination_texture,
                           GL_TEXTURE_2D,
                           0,
                           0,
                           0,
                           0,
                           renderer->color_texture,
                           GL_TEXTURE_2D,
                           0,
                           0,
                           0,
                           0,
                           (GLsizei)frame->destination.width,
                           (GLsizei)frame->destination.height,
                           1);
    }
    upload_rgba32ui(renderer->coefficient_texture,
                    frame->raster->coefficient_width,
                    2,
                    frame->raster->coefficients);
    upload_rgba32ui(
        renderer->main_axis_texture, frame->raster->axis_extent, 2, frame->raster->main_axis);
    upload_rgba32ui(renderer->highlight_axis_texture,
                    frame->raster->axis_extent,
                    frame->raster->highlight_axis_rows,
                    frame->raster->highlight_axis);
    upload_rgba32ui(renderer->shadow_coefficient_texture,
                    WALLE_LG_SHADOW_COEFFICIENT_TILE_COUNT,
                    16,
                    frame->raster->shadow_coefficients);
    upload_rgba32ui(renderer->shadow_slope_texture,
                    WALLE_LG_SHADOW_QUAD_COUNT,
                    1,
                    frame->raster->shadow_slopes);
    glBindFramebuffer(GL_FRAMEBUFFER, renderer->framebuffer);
    glFramebufferTexture2D(
        GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, renderer->color_texture, 0);
    return glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE
           && glGetError() == GL_NO_ERROR;
}

static bool configure_frame_uniforms(GLuint program, const struct walle_lg_gl_frame* frame)
{
    float       reciprocal_width  = (float)(2.0 / frame->destination.width);
    float       reciprocal_height = (float)(2.0 / frame->destination.height);
    const float mvp[16]           = {
        reciprocal_width,
        0.0f,
        0.0f,
        0.0f,
        0.0f,
        -reciprocal_height,
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
    apply_profile(program, frame->transition->profile.byte);
    uniform_i(program, "AppleInterpolantTileStart", (GLint)frame->raster->tile_start);
    GLint slope_location = glGetUniformLocation(program, "AppleInterpolantSlopeBits");
    if (slope_location >= 0)
        glUniform4uiv(slope_location, 1, frame->raster->slopes);
    uniform_i(program, "AppleInterpolantAxisStart", 0);
    return true;
}

static void draw_main(const struct walle_lg_gl_renderer* renderer)
{
    glBindVertexArray(renderer->main.vao);
    glDrawArrays(GL_TRIANGLES, 0, WALLE_LG_MAIN_VERTEX_COUNT);
}

static void draw_indexed(const struct geometry* geometry, uint32_t index_count)
{
    glBindVertexArray(geometry->vao);
    glDrawElements(GL_TRIANGLES, (GLsizei)index_count, GL_UNSIGNED_SHORT, nullptr);
}

bool walle_lg_gl_renderer_render_prefix(struct walle_lg_gl_renderer*    renderer,
                                        const struct walle_lg_gl_frame* frame)
{
    if (renderer == nullptr || !validate_frame(frame))
        return false;
    while (glGetError() != GL_NO_ERROR) {
    }
    GLuint program = renderer->program[frame->transition->material];
    if (program == 0 || !upload_frame_resources(renderer, frame))
        return false;
    renderer->width  = frame->destination.width;
    renderer->height = frame->destination.height;
    glUseProgram(program);
    if (!configure_frame_uniforms(program, frame))
        return false;
    bind_texture(program,
                 "SourceTexture",
                 0,
                 frame->source_texture == 0 ? renderer->source_texture : frame->source_texture);
    bind_texture(program, "AppleFloatIntrinsicTable", 6, renderer->intrinsic_texture);
    bind_texture(program,
                 "DestinationTexture",
                 7,
                 frame->destination_texture == 0 ? renderer->destination_texture
                                                 : frame->destination_texture);
    bind_texture(program, "AppleInterpolantAxisTrace", 8, renderer->main_axis_texture);
    bind_texture(program, "AppleInterpolantCoefficientTrace", 9, renderer->coefficient_texture);
    bind_texture(program,
                 "AppleShadowInterpolantCoefficientTrace",
                 17,
                 renderer->shadow_coefficient_texture);
    bind_texture(program, "AppleShadowInterpolantSlopeTrace", 18, renderer->shadow_slope_texture);

    glBindFramebuffer(GL_FRAMEBUFFER, renderer->framebuffer);
    glViewport(0, 0, (GLsizei)renderer->width, (GLsizei)renderer->height);
    glEnable(GL_SCISSOR_TEST);
    glScissor(frame->transition->background_scissor[0],
              frame->transition->background_scissor[1],
              frame->transition->background_scissor[2],
              frame->transition->background_scissor[3]);
    glDisable(GL_BLEND);
    glDisable(GL_CULL_FACE);
    uniform_i(program, "CoordinateMode", 4);
    uniform_i(program, "HighlightSdfNormalMode", 0);
    uniform_i(program, "SdfMode", 4);
    draw_main(renderer);

    glCopyImageSubData(renderer->color_texture,
                       GL_TEXTURE_2D,
                       0,
                       0,
                       0,
                       0,
                       renderer->destination_texture,
                       GL_TEXTURE_2D,
                       0,
                       0,
                       0,
                       0,
                       (GLsizei)renderer->width,
                       (GLsizei)renderer->height,
                       1);
    bind_texture(program, "DestinationTexture", 7, renderer->destination_texture);
    uniform_i(program, "CoordinateMode", 0);
    uniform_i(program, "SdfMode", -4);
    draw_indexed(&renderer->shadow, WALLE_LG_SHADOW_INDEX_COUNT);

    return glGetError() == GL_NO_ERROR;
}

bool walle_lg_gl_renderer_render(struct walle_lg_gl_renderer*    renderer,
                                 const struct walle_lg_gl_frame* frame)
{
    if (!walle_lg_gl_renderer_render_prefix(renderer, frame))
        return false;
    GLuint program = renderer->program[frame->transition->material];

    glCopyImageSubData(renderer->color_texture,
                       GL_TEXTURE_2D,
                       0,
                       0,
                       0,
                       0,
                       renderer->destination_texture,
                       GL_TEXTURE_2D,
                       0,
                       0,
                       0,
                       0,
                       (GLsizei)renderer->width,
                       (GLsizei)renderer->height,
                       1);
    apply_highlight(program, frame->transition->highlight_uniform);
    glScissor(0, 0, (GLsizei)renderer->width, (GLsizei)renderer->height);
    bind_texture(program, "AppleInterpolantAxisTrace", 8, renderer->highlight_axis_texture);
    uniform_i(program, "CoordinateMode", frame->transition->highlight_index_count == 24 ? 7 : 4);
    uniform_i(program, "SdfMode", 4);
    uniform_i(program, "HighlightSdfNormalMode", 5);
    uniform_i(program, "FinalHighlightPass", 1);
    glFrontFace(GL_CW);
    glCullFace(GL_BACK);
    if (frame->transition->highlight_index_count != 24)
        glEnable(GL_CULL_FACE);
    draw_indexed(&renderer->highlight, frame->transition->highlight_index_count);
    glDisable(GL_CULL_FACE);
    uniform_i(program, "FinalHighlightPass", 0);
    return glGetError() == GL_NO_ERROR;
}

bool walle_lg_gl_renderer_read_rgba8(struct walle_lg_gl_renderer* renderer,
                                     uint8_t*                     pixels,
                                     size_t                       byte_count)
{
    if (renderer == nullptr || pixels == nullptr || renderer->width == 0 || renderer->height == 0
        || byte_count != (size_t)renderer->width * renderer->height * 4u) {
        return false;
    }
    glBindFramebuffer(GL_FRAMEBUFFER, renderer->framebuffer);
    glReadPixels(0,
                 0,
                 (GLsizei)renderer->width,
                 (GLsizei)renderer->height,
                 GL_RGBA,
                 GL_UNSIGNED_BYTE,
                 pixels);
    return glGetError() == GL_NO_ERROR;
}

bool walle_lg_gl_renderer_present(struct walle_lg_gl_renderer* renderer)
{
    if (renderer == nullptr || renderer->width == 0 || renderer->height == 0)
        return false;
    glBindFramebuffer(GL_READ_FRAMEBUFFER, renderer->framebuffer);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
    glBlitFramebuffer(0,
                      0,
                      (GLint)renderer->width,
                      (GLint)renderer->height,
                      0,
                      0,
                      (GLint)renderer->width,
                      (GLint)renderer->height,
                      GL_COLOR_BUFFER_BIT,
                      GL_NEAREST);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    return glGetError() == GL_NO_ERROR;
}

void walle_lg_gl_renderer_destroy(struct walle_lg_gl_renderer* renderer)
{
    if (renderer == nullptr)
        return;
    destroy_geometry(&renderer->main);
    destroy_geometry(&renderer->shadow);
    destroy_geometry(&renderer->highlight);
    GLuint textures[] = {
        renderer->source_texture,
        renderer->destination_texture,
        renderer->color_texture,
        renderer->coefficient_texture,
        renderer->main_axis_texture,
        renderer->highlight_axis_texture,
        renderer->intrinsic_texture,
        renderer->shadow_coefficient_texture,
        renderer->shadow_slope_texture,
    };
    glDeleteTextures((GLsizei)(sizeof textures / sizeof textures[0]), textures);
    if (renderer->framebuffer != 0)
        glDeleteFramebuffers(1, &renderer->framebuffer);
    for (size_t material = 0; material < 2; ++material) {
        if (renderer->program[material] != 0)
            glDeleteProgram(renderer->program[material]);
    }
    free(renderer);
}
