#ifndef WALLE_LIQUID_GLASS_GL_RENDERER_H
#define WALLE_LIQUID_GLASS_GL_RENDERER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "liquid_glass_raster.h"

enum
{
    WALLE_LG_FLOAT_INTRINSIC_BYTE_COUNT = 4096 * 2048,
};

struct walle_lg_rgba8_image
{
    uint32_t       width;
    uint32_t       height;
    const uint8_t* pixels;
};

struct walle_lg_gl_renderer;

struct walle_lg_gl_renderer_sources
{
    const char*    vertex_shader;
    const char*    clear_fragment_shader;
    const char*    regular_fragment_shader;
    const uint8_t* float_intrinsic_table;
};

struct walle_lg_gl_frame
{
    const struct walle_lg_transition_frame* transition;
    const struct walle_lg_raster_tables*    raster;
    struct walle_lg_rgba8_image             destination;
    uint32_t                                destination_texture;
    const struct walle_lg_rgba8_image*      source_mips;
    uint32_t                                source_texture;
    uint32_t                                source_texture_width;
    uint32_t                                source_texture_height;
    uint32_t                                source_mip_count;
};

[[nodiscard]]
struct walle_lg_gl_renderer*
walle_lg_gl_renderer_create(const struct walle_lg_gl_renderer_sources* sources);

[[nodiscard]]
bool walle_lg_gl_renderer_render(struct walle_lg_gl_renderer*    renderer,
                                 const struct walle_lg_gl_frame* frame);

[[nodiscard]]
bool walle_lg_gl_renderer_render_prefix(struct walle_lg_gl_renderer*    renderer,
                                        const struct walle_lg_gl_frame* frame);

[[nodiscard]]
bool walle_lg_gl_renderer_read_rgba8(struct walle_lg_gl_renderer* renderer,
                                     uint8_t*                     pixels,
                                     size_t                       byte_count);

[[nodiscard]]
bool walle_lg_gl_renderer_present(struct walle_lg_gl_renderer* renderer);

void walle_lg_gl_renderer_destroy(struct walle_lg_gl_renderer* renderer);

#endif
