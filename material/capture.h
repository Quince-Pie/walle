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
    double                 backdrop[4], clipped[4];
    int32_t                surface[4];
    uint32_t               texture[2], extent[2];
    float                  projection[16], texture_matrix[4];
    uint32_t               tap_count, quad_count;
    float                  taps[16];
    struct wm_capture_quad quads[9];
};

enum wm_capture_status
{
    WM_CAPTURE_INVALID = -1,
    WM_CAPTURE_EMPTY = 0,
    WM_CAPTURE_READY = 1,
    WM_CAPTURE_FILTER_FALLBACK = 2,
};
struct wm_capture_request
{
    uint32_t source_size[2];
    /* Current backdrop bounds and scalar margin, already mapped into the
     * destination pixel coordinate space. For uniform reflected backing,
     * transform the bounds as a bbox and multiply margin by backing scale. */
    double frame[4], margin, scale;
    /* Renderer scale for the non-replicating raster-round branch; source_size
     * is already in pixels. Glass's normal replicating branch ignores it. */
    double renderer_scale;
    bool replicate_edges, raster_flipped;
    /* One source-derived group band rectangle in destination pixels; nullptr
     * selects exterior(C), as capture_in_stack's single full-capture fallback.
     * This is borrowed during this call, not retained. */
    const double* region;
};

/* Native single-source destination-space specialization of capture_backdrop.
 * Source dimensions must fit positive native signed32 bounds. The source
 * must cover the entire output. Thus collect_sources is supplied
 * explicitly by the caller and the separate outside-source replicate draw is
 * inapplicable. No source acquisition, composition, caching or GPU work occurs.
 * Projection and extent MUST be honored by the renderer. EMPTY/INVALID zero
 * the entire output; EMPTY must not be allocated or passed to pyramid_build.
 * EMPTY classifies nonpositive native rounded capture geometry; it does not
 * claim the original renderer's degenerate allocation/lifetime behavior.
 */
enum wm_capture_status wm_capture_clipped(const struct wm_capture_request* request,
                                          struct wm_capture_plan* out);

/* get_backdrop_bounds @18a7fb838, then CA::Rect::apply_transform. The margin
 * narrows to Float in the layer before local expansion. This specialization
 * accepts the product's uniform, optionally reflected axis-aligned transform.
 * Both outputs are required; local/world are expanded bounds, before clipping.
 * Pass world as capture_request.frame and margin=0 to preserve this schedule. */
bool wm_backdrop_bounds(const double frame[4], double margin, const double transform[6],
                         double local[4], double world[4]);
/* Same CA simple-transform schedule without backdrop expansion. Accepts
 * finite nonsingular axis-aligned transforms with equal absolute X/Y scale;
 * callers retaining a general-affine route should use it for other matrices. */
bool wm_uniform_rect_transform(const double rectangle[4], const double transform[6],
                                 double world[4]);

struct wm_recipe;
/* FilterNode::compute_dod for the same material-root transform. A valid empty
 * result culls the glass-background draw only; face/highlight/reveal ownership
 * remains with the caller. Native Float raster rounding uses bias255/512. */
bool wm_glass_filter_dod(const struct wm_recipe* recipe, const double transform[6],
                          int32_t bounds[4]);
/* When a live glass FilterNode has no backdrop surface, its LayerNode source
 * is rendered to a cleared ImageOffscreen. output_roi is the requested output
 * region in target pixels; it is intersected with the native filter DOD. The helper un-applies the
 * root transform, evaluates the original local glass ROI, applies the root
 * transform, then derives RenderSurface::set_dest/extend_surface geometry.
 * FILTER_FALLBACK requires a transparent clear, no capture draws, no pyramid,
 * and source scale1. EMPTY means the source DOD/ROI intersection is empty.
 * In this result backdrop is expanded world G, clipped is the propagated soft
 * ROI, and surface is the source-DOD intersection used for allocation. */
enum wm_capture_status wm_capture_filter_fallback(const struct wm_recipe* recipe,
                                                  const double transform[6],
                                                  const int32_t output_roi[4],
                                                  struct wm_capture_plan* out);
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
