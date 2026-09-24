#ifndef WM_SCISSOR_H
#define WM_SCISSOR_H
#include <stdint.h>

struct wm_recipe;
struct wm_glass_extent
{
    double shadow_offset[2], shadow_radius, shadow_opacity;
    double blur_radius, bleed_blur_radius, shadow_contribution, outer_refraction;
};

/* All rectangles are x,y,width,height in one source SDF-root point space. */
bool   wm_recipe_glass_extent(const struct wm_recipe* recipe, struct wm_glass_extent* out);
double wm_gaussian_expansion(double opacity, bool debug_2_8);
bool   wm_glass_dod(const struct wm_glass_extent* parameters,
                    const double                  content[4],
                    const double                  backdrop[4],
                    double                        output[4]);
bool   wm_aa_round(const double rectangle[4], bool antialias, int32_t output[4]);

/* Explicit application transform after source DOD. With antialias=true, apply
 * Updater AA rounding before canvas intersection. Plain scissors use exterior
 * integer bounds after intersection. affine={a,b,c,d,tx,ty}; negative Y scale is supported.
 * Scissor output is top-left target-pixel x,y,width,height. Canvas is positive
 * and bounded by INT32_MAX. Glass uses antialias=false.
 */
bool wm_scissor_transform(const double rectangle[4],
                          const double affine[6],
                          uint32_t     width,
                          uint32_t     height,
                          bool         antialias,
                          int32_t      output[4]);
#endif
