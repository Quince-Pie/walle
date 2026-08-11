#ifndef WALLE_LIQUID_GLASS_REVEAL_MASK_MODEL_H
#define WALLE_LIQUID_GLASS_REVEAL_MASK_MODEL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WALLE_LG_REVEAL_FAST_SQRT_SHA256                                                           \
    "fff71cc0d4428677ca5bc58b91212a7166b701e4efe504c3d71cab70846d0449"

enum
{
    WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT = 1u << 23,
    WALLE_LG_REVEAL_MAX_VERTEX_COUNT           = 16,
    WALLE_LG_REVEAL_MAX_INDEX_COUNT            = 54,
    WALLE_LG_REVEAL_VERTEX_STRIDE              = 48,
};

enum walle_lg_reveal_mask_family : uint8_t
{
    WALLE_LG_REVEAL_MASK_EMPTY = 0,
    WALLE_LG_REVEAL_MASK_BORDER_GRID,
    WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS,
};

/*
 * Public transition inputs.  The caller owns timeline easing: progress is the
 * already-selected radius fraction, not wall-clock time.  Coordinates use the
 * top-left-origin pixel space of the authenticated reveal-mask corpus.
 */
struct walle_lg_reveal_mask_request
{
    uint32_t target_width;
    uint32_t target_height;
    double   center_x;
    double   center_y;
    double   maximum_radius;
    double   progress;
};

struct walle_lg_reveal_mask_circle
{
    double unsnapped_radius;
    float  center[2];
    float  radius;
    float  expanded_radius;
    float  normalized_extent;

    /* left, top, right, bottom; right and bottom are exclusive. */
    int32_t bounds[4];
    /* x, y, width, height, clipped to the target. */
    int32_t scissor[4];
    bool    empty;
};

/*
 * The 48-byte layout is the public-input reconstruction of the two observed
 * reveal vertex families.  Compact meshes consume first_coordinates as their
 * circle SDF; border meshes consume second_coordinates.  The remaining active
 * fields are retained so the constructor can be audited byte-for-byte against
 * captured geometry without embedding any captured stream.
 */
struct walle_lg_reveal_mask_vertex
{
    float    position[4];
    float    first_coordinates[2];
    float    second_coordinates[2];
    uint16_t half_color[4];
    uint32_t unused_tail[2];
};

struct walle_lg_reveal_mask_geometry
{
    struct walle_lg_reveal_mask_circle circle;
    enum walle_lg_reveal_mask_family   family;
    uint32_t                           vertex_count;
    uint32_t                           index_count;
    struct walle_lg_reveal_mask_vertex vertices[WALLE_LG_REVEAL_MAX_VERTEX_COUNT];
    uint16_t                           indices[WALLE_LG_REVEAL_MAX_INDEX_COUNT];

    /* Compact meshes begin with an inside scissor that boundary quads replace. */
    bool clear_to_inside;
};

/*
 * Exact interpolated SDF coordinates for one fragment and its XOR-quad
 * horizontal/vertical helper lanes.  Raster setup is deliberately a separate
 * input: this sampling API never selects geometry from captured states or
 * pixels.  Geometry selection is handled separately by
 * walle_lg_reveal_mask_geometry_construct().
 */
struct walle_lg_reveal_mask_sample
{
    float x;
    float y;
    float horizontal_partner_x;
    float vertical_partner_y;
};

struct walle_lg_reveal_mask_sample_result
{
    float    distance;
    float    horizontal_distance;
    float    vertical_distance;
    float    feather;
    uint16_t alpha_half_bits;
    uint8_t  coverage;
};

[[nodiscard]]
bool walle_lg_reveal_mask_circle_construct(const struct walle_lg_reveal_mask_request* request,
                                           struct walle_lg_reveal_mask_circle*        result);

[[nodiscard]]
bool walle_lg_reveal_mask_geometry_construct(const struct walle_lg_reveal_mask_request* request,
                                             struct walle_lg_reveal_mask_geometry*      result);

[[nodiscard]]
bool walle_lg_reveal_mask_apple_fast_sqrt(const uint8_t* table,
                                          size_t         table_byte_count,
                                          float          value,
                                          float*         result);

[[nodiscard]]
bool walle_lg_reveal_mask_sample_r8(const uint8_t* fast_sqrt_table,
                                    size_t         fast_sqrt_table_byte_count,
                                    const struct walle_lg_reveal_mask_sample*  sample,
                                    struct walle_lg_reveal_mask_sample_result* result);

#endif
