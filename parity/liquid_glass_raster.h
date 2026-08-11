#ifndef WALLE_LIQUID_GLASS_RASTER_H
#define WALLE_LIQUID_GLASS_RASTER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "liquid_glass_postguard.h"
#include "liquid_glass_reveal_mask_model.h"
#include "liquid_glass_transition_frame.h"

enum
{
    WALLE_LG_RASTER_CHANNEL_COUNT          = 4,
    WALLE_LG_RASTER_PRIMITIVE_COUNT        = 2,
    WALLE_LG_SHADOW_QUAD_COUNT             = 8,
    WALLE_LG_SHADOW_COEFFICIENT_TILE_COUNT = 32,
    WALLE_LG_PRODUCER_MAX_QUAD_COUNT       = 4,
    WALLE_LG_REVEAL_RASTER_MAX_BASE_OWNER_COUNT = 4,
    WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT
        = WALLE_LG_REVEAL_RASTER_MAX_BASE_OWNER_COUNT + WALLE_LG_POSTGUARD_MAX_CHILD_COUNT,
    WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT   = 2,
    WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT
        = WALLE_LG_REVEAL_MAX_INDEX_COUNT / 3,
    WALLE_LG_REVEAL_RASTER_INVALID_MAPPING = UINT8_MAX,
    WALLE_LG_REVEAL_OWNER_BLOCK_BINDING     = 0,
};

enum walle_lg_reveal_raster_status : uint8_t
{
    WALLE_LG_REVEAL_RASTER_OK = 0,
    WALLE_LG_REVEAL_RASTER_INVALID_ARGUMENT,
    WALLE_LG_REVEAL_RASTER_INVALID_GEOMETRY,
    WALLE_LG_REVEAL_RASTER_ARITHMETIC_RANGE,
    WALLE_LG_REVEAL_RASTER_CAPACITY_EXCEEDED,
    WALLE_LG_REVEAL_RASTER_ALLOCATION_FAILED,
    WALLE_LG_REVEAL_RASTER_SETUP_FAILED,
};

struct walle_lg_raster_case_selector
{
    uint32_t width_fixed;
    uint32_t height_fixed;
};

struct walle_lg_raster_calibration
{
    const uint8_t* p25_ceil_bits;
    size_t         p25_selector_bit_count;

    const uint32_t* base_selectors;
    size_t          base_selector_count;

    const uint32_t* square_selectors;
    size_t          square_selector_count;
    uint32_t        square_width_fixed_lower;

    const uint32_t* near_square_selectors;
    size_t          near_square_selector_count;

    const struct walle_lg_raster_case_selector* natural_shadow_cases;
    const uint32_t*                             natural_shadow_selectors;
    size_t                                      natural_shadow_count;
};

struct walle_lg_raster_tables
{
    uint32_t  axis_extent;
    uint32_t  tile_start;
    uint32_t  coefficient_width;
    uint32_t  slopes[WALLE_LG_RASTER_CHANNEL_COUNT];
    uint32_t* coefficients;
    size_t    coefficient_word_count;

    uint32_t* main_axis;
    size_t    main_axis_word_count;

    uint32_t* shadow_coefficients;
    size_t    shadow_coefficient_word_count;
    uint32_t* shadow_slopes;
    size_t    shadow_slope_word_count;

    uint32_t* highlight_axis;
    size_t    highlight_axis_word_count;
    uint32_t  highlight_axis_rows;
    bool      highlight_back_facing;
};

struct walle_lg_producer_raster_quad
{
    int32_t   origin_fixed[2];
    int32_t   extent_fixed[2];
    int32_t   visible_bounds[4];
    int32_t   axis_start;
    uint32_t  axis_count;
    uint32_t* axis_bits;
    bool      ascending_diagonal;
};

struct walle_lg_producer_raster
{
    uint32_t                             quad_count;
    struct walle_lg_producer_raster_quad quads[WALLE_LG_PRODUCER_MAX_QUAD_COUNT];
};

struct walle_lg_reveal_raster_quad
{
    int32_t origin_fixed[2];
    int32_t extent_fixed[2];
    int32_t visible_bounds[4];
    int32_t axis_start;
    bool    ascending_diagonal;
    uint8_t active_primitive_mask;
};

struct walle_lg_reveal_raster_primitive
{
    uint8_t packed_slot;
    uint8_t geometric_primitive;
};

/* This is the byte-exact std140 layout consumed by reveal_mask.frag.glsl. */
struct walle_lg_reveal_owner_block
{
    int32_t counts[4];
    int32_t bounds[WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT][4];
    int32_t origin_extent[WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT][4];
    int32_t control[WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT][4];
};

static_assert(offsetof(struct walle_lg_reveal_owner_block, bounds) == 16);
static_assert(offsetof(struct walle_lg_reveal_owner_block, origin_extent) == 1'520);
static_assert(offsetof(struct walle_lg_reveal_owner_block, control) == 3'024);
static_assert(sizeof(struct walle_lg_reveal_owner_block) == 4'528);

struct walle_lg_reveal_raster
{
    uint32_t owner_count;
    uint32_t base_owner_count;
    uint32_t packed_width;
    size_t   packed_word_count;
    uint32_t* packed_words;

    uint32_t original_primitive_count;
    struct walle_lg_reveal_raster_quad
        owners[WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT];
    struct walle_lg_reveal_raster_primitive
        primitives[WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT];
    uint32_t postguard_child_count;
    uint32_t supported_postguard_child_count;
    uint32_t unsupported_postguard_child_count;
    uint32_t offscreen_postguard_child_count;
    struct walle_lg_reveal_owner_block owner_block;
};

[[nodiscard]]
bool walle_lg_raster_tables_construct(const struct walle_lg_transition_frame*   frame,
                                      uint32_t                                  target_width,
                                      uint32_t                                  target_height,
                                      const struct walle_lg_raster_calibration* calibration,
                                      struct walle_lg_raster_tables*            result);

void walle_lg_raster_tables_destroy(struct walle_lg_raster_tables* tables);

[[nodiscard]]
bool walle_lg_producer_raster_construct(const struct walle_lg_transition_frame*   frame,
                                        uint32_t                                  source_width,
                                        uint32_t                                  source_height,
                                        const struct walle_lg_raster_calibration* calibration,
                                        struct walle_lg_producer_raster*          result);

[[nodiscard]]
bool walle_lg_producer_raster_coordinates(const struct walle_lg_producer_raster_quad* quad,
                                          int32_t                                     x,
                                          int32_t                                     y,
                                          float result[static 2]);

void walle_lg_producer_raster_destroy(struct walle_lg_producer_raster* raster);

/*
 * packed_words is an RG32UI image with packed_width columns.  Row
 * 2 * packed_slot + geometric_primitive selects the exact AGX axis values.
 * Degenerate original primitives use WALLE_LG_REVEAL_RASTER_INVALID_MAPPING
 * in both mapping fields.
 */
[[nodiscard]]
enum walle_lg_reveal_raster_status
walle_lg_reveal_raster_construct(const struct walle_lg_reveal_mask_geometry* geometry,
                                 uint32_t                                    target_width,
                                 uint32_t                                    target_height,
                                 const struct walle_lg_raster_calibration*   calibration,
                                 struct walle_lg_reveal_raster*              result);

void walle_lg_reveal_raster_destroy(struct walle_lg_reveal_raster* raster);

#endif
