#ifndef WALLE_LIQUID_GLASS_REVEAL_STAGE_H
#define WALLE_LIQUID_GLASS_REVEAL_STAGE_H

#include <stdbool.h>
#include <stdint.h>

/*
 * A full-frame texture before Liquid Glass backdrop construction.  The
 * texture name is deliberately an integer instead of GLuint so routing stays
 * testable without a current GL context; OpenGL defines GLuint as uint32_t on
 * the supported ABI.
 */
struct walle_lg_gl_texture_view
{
    uint32_t texture;
    uint32_t width;
    uint32_t height;
};

struct walle_lg_gl_frame_inputs
{
    struct walle_lg_gl_texture_view pyramid_source;
    struct walle_lg_gl_texture_view destination;
    bool                            reveal_applied;
};

enum walle_lg_reveal_stage_intent : uint8_t
{
    WALLE_LG_REVEAL_STAGE_DISABLED = 0,
    WALLE_LG_REVEAL_STAGE_EXACT    = 1,
};

/*
 * Callers cannot construct or inspect this capability.  Before the exact
 * selector and prospective holdout are approved, acquire() returns nullptr
 * and every EXACT request fails closed.
 */
struct walle_lg_reveal_stage_authority;

struct walle_lg_reveal_stage_request
{
    enum walle_lg_reveal_stage_intent intent;

    /*
     * This is the finished full-frame RGBA8 composition, not a raw reveal
     * coverage plane.  Coverage-to-color composition remains outside this
     * seam until that operation has its own exact transfer evidence.  The
     * caller owns the texture, must make prior writes visible to texture
     * fetches, and must keep it alive through pyramid construction and frame
     * rendering; route() performs no GL calls or synchronization.
     */
    struct walle_lg_gl_texture_view composition;
    const struct walle_lg_reveal_stage_authority* authority;
};

[[nodiscard]]
const struct walle_lg_reveal_stage_authority*
walle_lg_reveal_stage_authority_acquire(void);

/*
 * Resolve the two texture consumers without performing GL work.
 *
 * DISABLED (or request == nullptr) is a literal pass-through: the two base
 * descriptors are copied without normalization or validation.  EXACT routes
 * one authority-approved, full-frame composition texture to both the pyramid
 * builder and DestinationTexture.
 */
[[nodiscard]]
bool walle_lg_reveal_stage_route(
    const struct walle_lg_gl_texture_view* base_pyramid_source,
    const struct walle_lg_gl_texture_view* base_destination,
    const struct walle_lg_reveal_stage_request* request,
    struct walle_lg_gl_frame_inputs* result);

#if defined(WALLE_LG_REVEAL_STAGE_TESTING)
/* Test builds can exercise the post-approval route without publishing a
 * production capability.  This symbol is absent from normal objects. */
[[nodiscard]]
const struct walle_lg_reveal_stage_authority*
walle_lg_reveal_stage_testing_authority(void);
#endif

#endif
