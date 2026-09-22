#ifndef WM_CAPTURE_H
#define WM_CAPTURE_H
#include <stdint.h>
#include <stdbool.h>

struct wm_capture_quad
{
    float position[4], texcoord[4];
};
struct wm_capture_plan
{
    double                 scale;
    int32_t                surface[4];
    uint32_t               texture[2], extent[2];
    float                  projection[16], texture_matrix[4];
    uint32_t               tap_count, quad_count;
    float                  taps[16];
    struct wm_capture_quad quads[9];
};
struct wm_blur_downsample
{
    uint32_t src_level, dst_level, width, height, groups_x, groups_y;
    float    dx, dy;
};
struct wm_pyramid_plan
{
    uint32_t                  mip_count, texture[2];
    int32_t                   bounds[4];
    double                    sample_scale;
    int32_t                   coordinate_base[2], coordinate_clamp[4];
    uint32_t                  dst0[2], dst1[2], dst1_level, no_base, groups[2];
    uint32_t                  down_count;
    struct wm_blur_downsample down[32];
};

/* Explicit full-canvas source boundary. Both allocation roundings are64;
 * copy workgroups cover32x32 texels. No fixed input-axis cap is imposed.
 * Capture bounds must fit int32 and64-rounded allocations uint32. A live
 * pyramid must additionally fit the original signed16 base/clamp and unsigned16
 * destination fields. The Vulkan adapter widens transport, preserving these
 * native-domain values; that is not an expanded native semantic claim.
 * Device allocation limits remain the renderer's responsibility. Each function
 * zeroes its nonnull output on every failure and only publishes a full plan.
 */
bool wm_capture_full(uint32_t                width,
                     uint32_t                height,
                     double                  scale,
                     bool                    replicate_edges,
                     struct wm_capture_plan* out);
bool wm_pyramid_build(const struct wm_capture_plan* capture,
                      float                         minimum_radius,
                      float                         maximum_radius,
                      double                        transform_scale,
                      struct wm_pyramid_plan*       out);
#endif
