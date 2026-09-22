#ifndef WM_MATERIAL_INTERNAL_H
#define WM_MATERIAL_INTERNAL_H
#include "material_boundary.h"
struct wm_map
{
    double in[2], out[2], upper, transition;
    bool   reverse, has_upper;
};
struct wm_iom
{
    double        value;
    struct wm_map map;
    bool          mapped;
};
struct wm_ycc
{
    float    black, white, saturation;
    float    fill[3][4];
    uint32_t mask;
};
struct wm_spec
{
    float backdrop_scale;
    struct
    {
        double        height, amount, blur_radius, radius, offset[2];
        struct wm_map opacity, vibrancy;
        struct wm_ycc ycc;
    } shadow;
    struct
    {
        float         opacity, opacities[5];
        double        distances[5];
        uint32_t      distance_kinds[5];
        struct wm_map radius;
    } blur;
    struct
    {
        double inner_height, inner_amount, inner_height_range[2], inner_amount_range[2],
            outer_height, outer_amount;
        float outer_opacity;
    } refraction;
    struct
    {
        float         opacity;
        struct wm_ycc ycc;
    } face;
    struct
    {
        double        amount, height, blur_radius, maximum_blur;
        struct wm_map opacity;
        struct wm_ycc ycc;
        bool          darken;
    } bleed;
    struct
    {
        float         hdr, key_opacity, fill_opacity;
        double        curvature, amount, key_offset, fill_offset;
        struct wm_iom spread, key_height, fill_height;
        struct wm_ycc key_ycc, fill_ycc;
    } highlight;
};
struct wm_recipe
{
    struct wm_material_input input;
    struct wm_spec           spec;
    double                   D, pixel_length;
    double shadow_offset[2], shadow_amount, shadow_height, shadow_blur, shadow_radius;
    float  shadow_opacity;
    double shadow_vibrancy, blur_radius, blur_distances[5];
    float  blur_opacities[5];
    double inner_height, inner_amount, outer_height, outer_amount, refraction_distances[2];
    float  refraction_opacity;
    double bleed_amount, bleed_height, bleed_blur;
    float  bleed_opacity;
    float  sdr_shadow;
    double key_height, fill_height, spread;
    float  face_matrix[20], highlight_matrix[20], tint_matrix[20];
    struct wm_plan_parameters plan;
};
#endif
