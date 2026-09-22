#ifndef WALLE_MATERIAL_BOUNDARY_H
#define WALLE_MATERIAL_BOUNDARY_H

/* Source-derived C23 public regular/clear fixed-appearance material library.
 * Material recipe geometry and animated draw geometry are deliberately
 * separate inputs. All byte packets preserve the extracted little-endian ABI.
 */
#include <stddef.h>
#include <stdint.h>
#include <float.h>

enum wm_style : uint8_t
{
    WM_REGULAR = 0,
    WM_CLEAR   = 1
};

struct wm_tint
{
    bool    present; /* false differs from a present alpha-zero tint */
    uint8_t srgb[4]; /* user #RRGGBB[AA], straight/unpremultiplied */
};

struct wm_material_input
{
    enum wm_style  style;
    bool           dark;   /* incoming appearance; portal auto resolved outside */
    bool           active; /* current specialization requires true */
    double         width_points, height_points, backing_scale;
    struct wm_tint tint;
};

struct wm_render_domain
{
    uint32_t source_width, source_height;
    double   source_scale;                              /* actual capture/pyramid sample scale */
    double   transform[4];                              /* m0,m1,m4,m5; identity = 1,0,0,1 */
    double   headroom, gamma;                           /* declared SDR contract = 1,2.2 */
    bool     global_light;
    double   light_angle;                               /* original unmodified default pi/2 */
    double   light_opacity, light_spread, light_height; /* NaN = no override */
};

struct wm_plan_parameters
{
    double backdrop_scale, margin;
    double blur_min, blur_max;
    double output_minimum, output_maximum, smoothness, ovalization;
    double highlight_pad, highlight_maximum;
    double shadow_grow, shadow_offset[2];
    double maximum_refraction;
    double tint_mask_pad, tint_mask_maximum, tint_gradient_pad, tint_gradient_maximum;
    double face_opacity, highlight_opacity, tint_group_opacity;
    double tint_gradient_smoothness, tint_mask_smoothness, tint_effect_offset;
    bool   has_backdrop, has_highlight, has_tint, tracks_luma;
    float  tint_distances[3];
};

struct wm_shader_packet
{
    uint8_t                   glass_lph[216];     /* lg_host.pack_glass_background_lph */
    uint8_t                   face_vcm[48];       /* 24 IEEE binary16 words */
    uint8_t                   highlight_vcm[48];
    uint8_t                   key_fill[40];       /* 20 IEEE binary16 words */
    uint8_t                   tint_vcm[48];
    uint8_t                   tint_gradient[24];  /* four float32 + four binary16 fields */
    uint8_t                   tint_mask_fill[16]; /* eight binary16 fields */
    uint8_t                   tint_ramp_rgba16f[256 * 4 * 2];
    uint32_t                  glass_texture_function;
    struct wm_plan_parameters plan;
};

struct wm_draw_geometry
{
    double x, y, width, height, radius; /* backend coordinates, explicit conversion */
    double transform[6];
};

/* The implementation owns the typed Parameters/filter/gradient records.
 * Fixed incoming appearance deliberately disables the optional adaptive owner;
 * no sampled recipe grids or native/captured runtime values are used.
 */
struct wm_recipe;
struct wm_recipe* wm_recipe_create(const struct wm_material_input* input);
void              wm_recipe_destroy(struct wm_recipe* recipe);
bool              wm_recipe_update(struct wm_recipe* recipe, const struct wm_material_input* input);
bool              wm_recipe_pack(const struct wm_recipe*        recipe,
                                 const struct wm_render_domain* domain,
                                 struct wm_shader_packet*       packet);

/* Geometry entry points are declared in geometry.h. An adaptive-owner module
 * is a separate optional extension; no unimplemented entry points are exported.
 */

static_assert(sizeof(float) == 4 && sizeof(double) == 8,
              "the extracted material arithmetic requires binary32/binary64 storage");
static_assert(FLT_RADIX == 2 && FLT_MANT_DIG == 24 && DBL_MANT_DIG == 53,
              "the extracted material arithmetic requires IEEE precision widths");
#endif
