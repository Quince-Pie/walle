#ifndef WALLE_LIQUID_GLASS_TRANSITION_PROFILE_H
#define WALLE_LIQUID_GLASS_TRANSITION_PROFILE_H

#include <stdbool.h>

#include "liquid_glass_static_profile.h"

struct walle_lg_transition_profile_request
{
    struct walle_lg_transition_request transition;
    float                              sdf_half_width;
    float                              sdf_half_height;
    float                              source_texel_step_x;
    float                              source_texel_step_y;
};

enum
{
    WALLE_LG_SMALL_CLEAR_BACKGROUND_PROFILE_BYTE_COUNT = 210
};

struct walle_lg_small_clear_background_profile_request
{
    enum walle_lg_appearance appearance;
    uint32_t                 diameter;
    float                    visible_fraction;
    double                   element_extent;
    float                    backdrop_scale;
};

struct walle_lg_small_clear_background_profile_payload
{
    uint8_t byte[WALLE_LG_SMALL_CLEAR_BACKGROUND_PROFILE_BYTE_COUNT];
};

[[nodiscard]]
bool walle_lg_transition_profile(const struct walle_lg_transition_profile_request* request,
                                 struct walle_lg_profile_payload*                  result);

[[nodiscard]]
bool walle_lg_small_clear_background_profile(
    const struct walle_lg_small_clear_background_profile_request* request,
    struct walle_lg_small_clear_background_profile_payload*       result);

#endif
