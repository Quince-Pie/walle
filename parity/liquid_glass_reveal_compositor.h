#ifndef WALLE_LIQUID_GLASS_REVEAL_COMPOSITOR_H
#define WALLE_LIQUID_GLASS_REVEAL_COMPOSITOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WALLE_LG_REVEAL_PIPELINE_EVIDENCE_SHA256                                                   \
    "f1771204765c671caaebccd2d53a45e5dd2bad94e60ee2baeb3bec6035c93ad0"

enum
{
    WALLE_LG_REVEAL_VERTEX_COUNT       = 16,
    WALLE_LG_REVEAL_VERTEX_STRIDE      = 48,
    WALLE_LG_REVEAL_VERTEX_BYTE_COUNT  = 16 * 48,
    WALLE_LG_REVEAL_INDEX_COUNT        = 48,
    WALLE_LG_REVEAL_INDEX_BYTE_COUNT   = 48 * 2,
    WALLE_LG_REVEAL_ATTRIBUTE_COUNT    = 4,
    WALLE_LG_REVEAL_ATTACHMENT_COUNT   = 2,
    WALLE_LG_REVEAL_POSITION_OFFSET    = 0,
    WALLE_LG_REVEAL_SDF_OFFSET         = 16,
    WALLE_LG_REVEAL_SOURCE_OFFSET      = 24,
    WALLE_LG_REVEAL_HALF4_OFFSET       = 32,
    WALLE_LG_REVEAL_UNUSED_TAIL_OFFSET = 40,
};

struct walle_lg_reveal_compositor;

struct walle_lg_reveal_compositor_draw
{
    /* The caller owns the linked program and its still-unresolved shader law. */
    uint32_t program;

    /* RGBA8, single-sample, mip zero.  Existing contents are the LOAD input. */
    uint32_t target_texture;
    uint32_t width;
    uint32_t height;
    int32_t  scissor[4];

    const void* vertex_bytes;
    size_t      vertex_byte_count;
    const void* index_bytes;
    size_t      index_byte_count;
};

/*
 * This shell encodes only the admitted fixed state: float4/float2/float2/half4
 * attributes at stride 48, an RGBA8 target whose contents are preserved, an
 * attached write-disabled R8 auxiliary target, and ADD blending with ONE /
 * ONE_MINUS_SRC_ALPHA for RGB and alpha on attachment zero.  RGBA8 is the
 * logical-channel projection of captured BGRA8Unorm; the auxiliary is a
 * renderbuffer projection of Metal's memoryless load/store-dont-care target.
 * Neither projection claims implementation identity.  This shell does not
 * clear the target, choose a shader, bind the captured config/textures, synthesize
 * vertices, or grant reveal/parity authority.  Call it with a current OpenGL
 * 4.5 core context and no pending GL error.
 * The caller must prevent framebuffer feedback and establish any raster state
 * not represented by the admitted descriptor (for example cull and depth).
 */
[[nodiscard]]
struct walle_lg_reveal_compositor* walle_lg_reveal_compositor_create(void);

[[nodiscard]]
bool walle_lg_reveal_compositor_draw(struct walle_lg_reveal_compositor*            compositor,
                                     const struct walle_lg_reveal_compositor_draw* draw);

void walle_lg_reveal_compositor_destroy(struct walle_lg_reveal_compositor* compositor);

#endif
