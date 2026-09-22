#ifndef WALLE_TRANSITION_H
#define WALLE_TRANSITION_H

#include <stddef.h>
#include <stdint.h>
#include "material_boundary.h"
#include "vulkan_renderer.h"

enum walle_transition_motion : uint8_t
{
    WALLE_TRANSITION_SWEEP = 0,
    WALLE_TRANSITION_LENS  = 1,
};

struct walle_transition_options
{
    enum wm_style                style;
    bool                         dark;      /* Resolved desktop appearance; no portal query here. */
    bool                         active;    /* Current source specialization requires true. */
    struct wm_tint               tint;      /* Straight encoded sRGB bytes, no fitted colors. */
    enum walle_transition_motion motion;
    double                       origin[2]; /* Normalized output location, each in [0,1]. */
    double direction[2];                    /* Finite nonzero vector; normalized by this module. */
};

struct walle_transition;

/* draw.shape_mode is contextual:
 * glass: int32 native modes4/-4/0 represented asuint32 bits, mirrored in
 *        glass.sdf.arg.z(float), which is the actual glass shader consumer;
 * SDF effects:0=rect,4=uniform supercircle,5=per-corner (never image20/21/22);
 * face:0=plain,10=native circle_image,11=continuous supercircle_image.
 * All draw indices are triangle-expanded, not native quad-index lists.
 */

[[nodiscard]]
bool walle_transition_create(uint32_t                               width,
                             uint32_t                               height,
                             double                                 backing_scale,
                             const struct walle_transition_options* options,
                             struct walle_transition**              result);

/* A successful update invalidates borrowed frames and regenerates the recipe,
 * image-capture plan and geometry for the new output/options. No GPU work.
 */
[[nodiscard]]
bool walle_transition_update(struct walle_transition*               transition,
                             uint32_t                               width,
                             uint32_t                               height,
                             double                                 backing_scale,
                             const struct walle_transition_options* options);

/* Frame storage remains owned by transition until the next build/update or
 * destroy. The renderer copies spans before returning. No allocation in build.
 * Progress0 is plain A, progress1 and first_boot are exact plain B.
 */
[[nodiscard]]
bool walle_transition_build(struct walle_transition*      transition,
                            double                        progress,
                            bool                          first_boot,
                            const struct walle_vk_frame** result);

void walle_transition_destroy(struct walle_transition* transition);

/* Product geometry diagnostics, independent of native material evaluation. */
struct walle_transition_geometry
{
    double center[2], radius, scale, material_opacity;
};
[[nodiscard]]
bool walle_transition_geometry_at(const struct walle_transition*    transition,
                                  double                            progress,
                                  struct walle_transition_geometry* result);

#endif
