#ifndef WALLE_LIQUID_GLASS_TRANSITION_FRAME_H
#define WALLE_LIQUID_GLASS_TRANSITION_FRAME_H

#include <stdbool.h>
#include <stdint.h>

#include "liquid_glass_selected_region.h"
#include "liquid_glass_transition_profile.h"

enum
{
    WALLE_LG_MAIN_VERTEX_COUNT            = 6,
    WALLE_LG_SHADOW_VERTEX_COUNT          = 16,
    WALLE_LG_SHADOW_INDEX_COUNT           = 48,
    WALLE_LG_HIGHLIGHT_MAX_VERTEX_COUNT   = 16,
    WALLE_LG_HIGHLIGHT_MAX_INDEX_COUNT    = 24,
    WALLE_LG_HIGHLIGHT_UNIFORM_BYTE_COUNT = 248,
    WALLE_LG_PRODUCER_MAX_VERTEX_COUNT    = 16,
    WALLE_LG_PRODUCER_MAX_INDEX_COUNT     = 24,
};

enum walle_lg_producer_kind : uint8_t
{
    WALLE_LG_PRODUCER_DIRECT,
    WALLE_LG_PRODUCER_DOWNSAMPLE_4,
};

struct walle_lg_vertex
{
    float position[4];
    float sdf[2];
    float source[2];
};

struct walle_lg_producer_vertex
{
    float position[4];
    float source[2];
    float tail[2];
};

struct walle_lg_dynamic_producer_mesh
{
    enum walle_lg_producer_kind     kind;
    int32_t                         working_crop[4];
    int32_t                         visible_crop[4];
    int32_t                         scissor[4];
    struct walle_lg_producer_vertex vertices[WALLE_LG_PRODUCER_MAX_VERTEX_COUNT];
    uint16_t                        indices[WALLE_LG_PRODUCER_MAX_INDEX_COUNT];
    uint32_t                        vertex_count;
    uint32_t                        index_count;
};

struct walle_lg_transition_frame_request
{
    enum walle_lg_material   material;
    enum walle_lg_appearance appearance;
    uint32_t                 window_width;
    uint32_t                 window_height;
    uint32_t                 diameter;
    double                   center_x;
    double                   center_y;
    float                    visible_fraction;
    double                   sdf_enclosure_radius;
};

struct walle_lg_dynamic_layer_state
{
    double carrier_bounds[4];
    double carrier_position[2];
    double element_bounds[4];
    double element_position[2];
};

struct walle_lg_producer_crop
{
    float    allocation_margin;
    int32_t  origin[2];
    uint32_t active_extent[2];
    uint32_t storage_extent[2];
};

struct walle_lg_transition_frame
{
    enum walle_lg_material                material;
    enum walle_lg_appearance              appearance;
    struct walle_lg_dynamic_layer_state   layer;
    struct walle_lg_producer_crop         producer;
    struct walle_lg_selected_region       selected_region;
    struct walle_lg_profile_payload       profile;
    float                                 visible_fraction;
    float                                 backdrop_scale;
    int32_t                               background_scissor[4];
    struct walle_lg_dynamic_producer_mesh producer_mesh;

    struct walle_lg_vertex main_vertices[WALLE_LG_MAIN_VERTEX_COUNT];
    struct walle_lg_vertex shadow_vertices[WALLE_LG_SHADOW_VERTEX_COUNT];
    uint16_t               shadow_indices[WALLE_LG_SHADOW_INDEX_COUNT];

    struct walle_lg_vertex highlight_vertices[WALLE_LG_HIGHLIGHT_MAX_VERTEX_COUNT];
    uint16_t               highlight_indices[WALLE_LG_HIGHLIGHT_MAX_INDEX_COUNT];
    uint32_t               highlight_vertex_count;
    uint32_t               highlight_index_count;
    uint8_t                highlight_uniform[WALLE_LG_HIGHLIGHT_UNIFORM_BYTE_COUNT];
};

[[nodiscard]]
bool walle_lg_transition_frame_construct(const struct walle_lg_transition_frame_request* request,
                                         struct walle_lg_transition_frame*               result);

#endif
