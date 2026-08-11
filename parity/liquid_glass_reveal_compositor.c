#define GL_GLEXT_PROTOTYPES 1
#include "liquid_glass_reveal_compositor.h"

#include <GL/glcorearb.h>
#include <float.h>
#include <limits.h>
#include <stdbit.h>
#include <stdint.h>
#include <stdlib.h>

struct walle_lg_reveal_compositor
{
    GLuint  framebuffer;
    GLuint  auxiliary_renderbuffer;
    GLsizei auxiliary_width;
    GLsizei auxiliary_height;
    GLuint  vertex_array;
    GLuint  vertex_buffer;
    GLuint  index_buffer;
};

struct saved_state
{
    GLint     array_buffer;
    GLint     blend_destination_alpha[WALLE_LG_REVEAL_ATTACHMENT_COUNT];
    GLint     blend_destination_rgb[WALLE_LG_REVEAL_ATTACHMENT_COUNT];
    GLint     blend_equation_alpha[WALLE_LG_REVEAL_ATTACHMENT_COUNT];
    GLint     blend_equation_rgb[WALLE_LG_REVEAL_ATTACHMENT_COUNT];
    GLint     blend_source_alpha[WALLE_LG_REVEAL_ATTACHMENT_COUNT];
    GLint     blend_source_rgb[WALLE_LG_REVEAL_ATTACHMENT_COUNT];
    GLint     draw_framebuffer;
    GLint     program;
    GLint     renderbuffer;
    GLint     scissor[4];
    GLint     vertex_array;
    GLfloat   viewport[4];
    GLboolean blend_enabled[WALLE_LG_REVEAL_ATTACHMENT_COUNT];
    GLboolean color_mask[WALLE_LG_REVEAL_ATTACHMENT_COUNT][4];
    GLboolean scissor_enabled;
};

static_assert(WALLE_LG_REVEAL_VERTEX_BYTE_COUNT == 768);
static_assert(WALLE_LG_REVEAL_INDEX_BYTE_COUNT == 96);
static_assert(WALLE_LG_REVEAL_UNUSED_TAIL_OFFSET < WALLE_LG_REVEAL_VERTEX_STRIDE);
static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(_Float16) == 2);
static_assert(__STDC_ENDIAN_NATIVE__ == __STDC_ENDIAN_LITTLE__);

static void save_state(struct saved_state* state)
{
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &state->array_buffer);
    for (GLuint index = 0; index < WALLE_LG_REVEAL_ATTACHMENT_COUNT; ++index) {
        glGetIntegeri_v(GL_BLEND_DST_ALPHA, index, &state->blend_destination_alpha[index]);
        glGetIntegeri_v(GL_BLEND_DST_RGB, index, &state->blend_destination_rgb[index]);
        glGetIntegeri_v(GL_BLEND_EQUATION_ALPHA, index, &state->blend_equation_alpha[index]);
        glGetIntegeri_v(GL_BLEND_EQUATION_RGB, index, &state->blend_equation_rgb[index]);
        glGetIntegeri_v(GL_BLEND_SRC_ALPHA, index, &state->blend_source_alpha[index]);
        glGetIntegeri_v(GL_BLEND_SRC_RGB, index, &state->blend_source_rgb[index]);
        state->blend_enabled[index] = glIsEnabledi(GL_BLEND, index);
        glGetBooleani_v(GL_COLOR_WRITEMASK, index, state->color_mask[index]);
    }
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &state->draw_framebuffer);
    glGetIntegerv(GL_CURRENT_PROGRAM, &state->program);
    glGetIntegerv(GL_RENDERBUFFER_BINDING, &state->renderbuffer);
    glGetIntegeri_v(GL_SCISSOR_BOX, 0, state->scissor);
    glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &state->vertex_array);
    glGetFloati_v(GL_VIEWPORT, 0, state->viewport);
    state->scissor_enabled = glIsEnabledi(GL_SCISSOR_TEST, 0);
}

static void restore_state(const struct saved_state* state)
{
    for (GLuint index = 0; index < WALLE_LG_REVEAL_ATTACHMENT_COUNT; ++index) {
        glBlendEquationSeparatei(index,
                                 (GLenum)state->blend_equation_rgb[index],
                                 (GLenum)state->blend_equation_alpha[index]);
        glBlendFuncSeparatei(index,
                             (GLenum)state->blend_source_rgb[index],
                             (GLenum)state->blend_destination_rgb[index],
                             (GLenum)state->blend_source_alpha[index],
                             (GLenum)state->blend_destination_alpha[index]);
        glColorMaski(index,
                     state->color_mask[index][0],
                     state->color_mask[index][1],
                     state->color_mask[index][2],
                     state->color_mask[index][3]);
        if (state->blend_enabled[index])
            glEnablei(GL_BLEND, index);
        else
            glDisablei(GL_BLEND, index);
    }
    if (state->scissor_enabled)
        glEnablei(GL_SCISSOR_TEST, 0);
    else
        glDisablei(GL_SCISSOR_TEST, 0);
    glScissorIndexed(0, state->scissor[0], state->scissor[1], state->scissor[2], state->scissor[3]);
    glViewportIndexedf(
        0, state->viewport[0], state->viewport[1], state->viewport[2], state->viewport[3]);
    glUseProgram((GLuint)state->program);
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)state->array_buffer);
    glBindVertexArray((GLuint)state->vertex_array);
    glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)state->renderbuffer);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)state->draw_framebuffer);
}

static bool valid_scissor(const struct walle_lg_reveal_compositor_draw* draw)
{
    int64_t right  = (int64_t)draw->scissor[0] + draw->scissor[2];
    int64_t bottom = (int64_t)draw->scissor[1] + draw->scissor[3];
    return draw->scissor[0] >= 0 && draw->scissor[1] >= 0 && draw->scissor[2] > 0
           && draw->scissor[3] > 0 && right <= draw->width && bottom <= draw->height;
}

static bool valid_target(const struct walle_lg_reveal_compositor_draw* draw)
{
    if (!glIsTexture(draw->target_texture))
        return false;
    GLint active_texture;
    GLint previous_texture;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &active_texture);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous_texture);
    glBindTexture(GL_TEXTURE_2D, draw->target_texture);
    GLint width;
    GLint height;
    GLint internal_format;
    GLint samples;
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &width);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &height);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT, &internal_format);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_SAMPLES, &samples);
    glBindTexture(GL_TEXTURE_2D, (GLuint)previous_texture);
    glActiveTexture((GLenum)active_texture);
    return width == (GLint)draw->width && height == (GLint)draw->height
           && internal_format == GL_RGBA8 && samples == 0 && glGetError() == GL_NO_ERROR;
}

static bool valid_indices(const void* bytes)
{
    const uint8_t* values = bytes;
    for (size_t index = 0; index < WALLE_LG_REVEAL_INDEX_COUNT; ++index) {
        uint16_t value
            = (uint16_t)((uint16_t)values[2 * index] | (uint16_t)values[2 * index + 1] << 8);
        if (value >= WALLE_LG_REVEAL_VERTEX_COUNT)
            return false;
    }
    return true;
}

static bool valid_draw(const struct walle_lg_reveal_compositor*      compositor,
                       const struct walle_lg_reveal_compositor_draw* draw)
{
    if (compositor == nullptr || draw == nullptr || draw->program == 0 || draw->target_texture == 0
        || draw->width == 0 || draw->height == 0 || draw->width > INT_MAX || draw->height > INT_MAX
        || draw->vertex_bytes == nullptr || draw->index_bytes == nullptr
        || draw->vertex_byte_count != WALLE_LG_REVEAL_VERTEX_BYTE_COUNT
        || draw->index_byte_count != WALLE_LG_REVEAL_INDEX_BYTE_COUNT
        || !valid_indices(draw->index_bytes) || !valid_scissor(draw)
        || !glIsProgram(draw->program)) {
        return false;
    }
    GLint linked;
    glGetProgramiv(draw->program, GL_LINK_STATUS, &linked);
    return linked == GL_TRUE && valid_target(draw);
}

struct walle_lg_reveal_compositor* walle_lg_reveal_compositor_create(void)
{
    if (glGetString(GL_VERSION) == nullptr || glGetError() != GL_NO_ERROR)
        return nullptr;
    struct saved_state state;
    save_state(&state);
    struct walle_lg_reveal_compositor* compositor = calloc(1, sizeof(*compositor));
    if (compositor == nullptr)
        return nullptr;
    glGenFramebuffers(1, &compositor->framebuffer);
    glGenRenderbuffers(1, &compositor->auxiliary_renderbuffer);
    glGenVertexArrays(1, &compositor->vertex_array);
    glGenBuffers(1, &compositor->vertex_buffer);
    glGenBuffers(1, &compositor->index_buffer);
    glBindVertexArray(compositor->vertex_array);
    glBindBuffer(GL_ARRAY_BUFFER, compositor->vertex_buffer);
    glBufferData(GL_ARRAY_BUFFER, WALLE_LG_REVEAL_VERTEX_BYTE_COUNT, nullptr, GL_STREAM_DRAW);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, compositor->index_buffer);
    glBufferData(
        GL_ELEMENT_ARRAY_BUFFER, WALLE_LG_REVEAL_INDEX_BYTE_COUNT, nullptr, GL_STREAM_DRAW);

    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0,
                          4,
                          GL_FLOAT,
                          GL_FALSE,
                          WALLE_LG_REVEAL_VERTEX_STRIDE,
                          (const void*)(uintptr_t)WALLE_LG_REVEAL_POSITION_OFFSET);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1,
                          2,
                          GL_FLOAT,
                          GL_FALSE,
                          WALLE_LG_REVEAL_VERTEX_STRIDE,
                          (const void*)(uintptr_t)WALLE_LG_REVEAL_SDF_OFFSET);
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(2,
                          2,
                          GL_FLOAT,
                          GL_FALSE,
                          WALLE_LG_REVEAL_VERTEX_STRIDE,
                          (const void*)(uintptr_t)WALLE_LG_REVEAL_SOURCE_OFFSET);
    glEnableVertexAttribArray(3);
    glVertexAttribPointer(3,
                          4,
                          GL_HALF_FLOAT,
                          GL_FALSE,
                          WALLE_LG_REVEAL_VERTEX_STRIDE,
                          (const void*)(uintptr_t)WALLE_LG_REVEAL_HALF4_OFFSET);
    GLint maximum_color_attachments;
    GLint maximum_draw_buffers;
    glGetIntegerv(GL_MAX_COLOR_ATTACHMENTS, &maximum_color_attachments);
    glGetIntegerv(GL_MAX_DRAW_BUFFERS, &maximum_draw_buffers);
    bool success = compositor->framebuffer != 0 && compositor->auxiliary_renderbuffer != 0
                   && maximum_color_attachments >= WALLE_LG_REVEAL_ATTACHMENT_COUNT
                   && maximum_draw_buffers >= WALLE_LG_REVEAL_ATTACHMENT_COUNT
                   && compositor->vertex_array != 0 && compositor->vertex_buffer != 0
                   && compositor->index_buffer != 0 && glGetError() == GL_NO_ERROR;
    restore_state(&state);
    if (success)
        return compositor;
    walle_lg_reveal_compositor_destroy(compositor);
    return nullptr;
}

bool walle_lg_reveal_compositor_draw(struct walle_lg_reveal_compositor*            compositor,
                                     const struct walle_lg_reveal_compositor_draw* draw)
{
    if (!valid_draw(compositor, draw))
        return false;
    struct saved_state state;
    save_state(&state);

    glBindVertexArray(compositor->vertex_array);
    glBindBuffer(GL_ARRAY_BUFFER, compositor->vertex_buffer);
    glBufferSubData(GL_ARRAY_BUFFER, 0, WALLE_LG_REVEAL_VERTEX_BYTE_COUNT, draw->vertex_bytes);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, compositor->index_buffer);
    glBufferSubData(
        GL_ELEMENT_ARRAY_BUFFER, 0, WALLE_LG_REVEAL_INDEX_BYTE_COUNT, draw->index_bytes);

    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, compositor->framebuffer);
    glFramebufferTexture2D(
        GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, draw->target_texture, 0);
    glBindRenderbuffer(GL_RENDERBUFFER, compositor->auxiliary_renderbuffer);
    bool storage_success = true;
    if (compositor->auxiliary_width != (GLsizei)draw->width
        || compositor->auxiliary_height != (GLsizei)draw->height) {
        glRenderbufferStorage(GL_RENDERBUFFER, GL_R8, (GLsizei)draw->width, (GLsizei)draw->height);
        storage_success = glGetError() == GL_NO_ERROR;
        if (storage_success) {
            compositor->auxiliary_width  = (GLsizei)draw->width;
            compositor->auxiliary_height = (GLsizei)draw->height;
        } else {
            compositor->auxiliary_width  = 0;
            compositor->auxiliary_height = 0;
        }
    }
    glFramebufferRenderbuffer(GL_DRAW_FRAMEBUFFER,
                              GL_COLOR_ATTACHMENT1,
                              GL_RENDERBUFFER,
                              compositor->auxiliary_renderbuffer);
    const GLenum draw_buffers[WALLE_LG_REVEAL_ATTACHMENT_COUNT] = {
        GL_COLOR_ATTACHMENT0,
        GL_COLOR_ATTACHMENT1,
    };
    glDrawBuffers(WALLE_LG_REVEAL_ATTACHMENT_COUNT, draw_buffers);
    bool success = storage_success
                   && glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE;
    if (success) {
        glViewportIndexedf(0, 0.0f, 0.0f, (GLfloat)draw->width, (GLfloat)draw->height);
        glEnablei(GL_SCISSOR_TEST, 0);
        glScissorIndexed(0, draw->scissor[0], draw->scissor[1], draw->scissor[2], draw->scissor[3]);
        glEnablei(GL_BLEND, 0);
        glBlendEquationSeparatei(0, GL_FUNC_ADD, GL_FUNC_ADD);
        glBlendFuncSeparatei(0, GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        glColorMaski(0, GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
        glDisablei(GL_BLEND, 1);
        glColorMaski(1, GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);
        glUseProgram(draw->program);
        glDrawElements(GL_TRIANGLES, WALLE_LG_REVEAL_INDEX_COUNT, GL_UNSIGNED_SHORT, nullptr);
        success = glGetError() == GL_NO_ERROR;
    }
    glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, 0, 0);
    glFramebufferRenderbuffer(GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT1, GL_RENDERBUFFER, 0);
    restore_state(&state);
    return success;
}

void walle_lg_reveal_compositor_destroy(struct walle_lg_reveal_compositor* compositor)
{
    if (compositor == nullptr)
        return;
    if (compositor->auxiliary_renderbuffer != 0)
        glDeleteRenderbuffers(1, &compositor->auxiliary_renderbuffer);
    if (compositor->index_buffer != 0)
        glDeleteBuffers(1, &compositor->index_buffer);
    if (compositor->vertex_buffer != 0)
        glDeleteBuffers(1, &compositor->vertex_buffer);
    if (compositor->vertex_array != 0)
        glDeleteVertexArrays(1, &compositor->vertex_array);
    if (compositor->framebuffer != 0)
        glDeleteFramebuffers(1, &compositor->framebuffer);
    free(compositor);
}
