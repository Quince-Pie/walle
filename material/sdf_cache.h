#ifndef WM_SDF_CACHE_H
#define WM_SDF_CACHE_H
#include <stdint.h>
#include "geometry.h"

/* One source SDFState owner, shared by its copied effects. The renderer
 * serializes updates. Times use the same monotonic scene clock as rendering;
 * bounds and the full root transform are the original updater's inputs. */
struct wm_sdf_cache_state
{
    int32_t bounds[4];
    double durations[4], last_change;
    uint64_t index;
    double offset[2], transform[16];
    bool eligible, changed, nested_disabled;
};

void wm_sdf_cache_init(struct wm_sdf_cache_state* state, bool nested_disabled);
bool wm_sdf_cache_advance(struct wm_sdf_cache_state* state, double time,
                           const double transform[16], const int32_t bounds[4],
                           uint16_t elements, uint64_t copies, bool disable_cache);

struct wm_sdf_cache_plan
{
    int32_t child_dod[4], requested[4], surface[4];
    uint32_t texture[2];
    int32_t anchor[2], relative_origin[2], cache_bounds[4];
    float padding;
    int32_t native_cost;
};
bool wm_sdf_cache_plan_build(const double local_bounds[4], const double affine[6], float cache_pad,
                              const double node_offset[2], struct wm_sdf_cache_plan* out);
bool wm_sdf_cache_reuse_origin(const double local_bounds[4], const double affine[6],
                                const int32_t relative_origin[2], int32_t out_origin[2]);
bool wm_sdf_cache_copy_dod(const int32_t child_dod[4], double scale, float cache_pad,
                            float own_pad, int32_t out[4]);
bool wm_sdf_cache_plan_contains(const struct wm_sdf_cache_plan* stored,
                                 const struct wm_sdf_cache_plan* requested);
bool wm_sdf_cache_contains(const int32_t cached_bounds[4], const int32_t requested_bounds[4]);

struct wm_recipe;
struct wm_render_domain;
/* Cached texture-SDF glass packet. Like wm_recipe_pack_glass, this zeros
 * every nonnull output on failure and writes all216 bytes on success. */
bool wm_recipe_pack_glass_texture(const struct wm_recipe* recipe, const struct wm_render_domain* domain,
                                    float sdf_scale, const uint32_t sdf_texture_size[2],
                                    uint8_t glass_lph[216], uint32_t* texture_function);
bool wm_sdf_cache_glass_quad(const struct wm_recipe* recipe, const struct wm_render_domain* domain,
                              const int32_t backdrop_bounds[4], const int32_t sdf_origin[2],
                              const uint32_t sdf_texture_size[2], float sdf_scale, float render_scale,
                              struct wm_vertex out[4]);

#endif
