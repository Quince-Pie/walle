#ifndef WALLE_LIQUID_GLASS_POSTGUARD_H
#define WALLE_LIQUID_GLASS_POSTGUARD_H

#include <stdint.h>

#include "liquid_glass_reveal_mask_model.h"

enum
{
    WALLE_LG_POSTGUARD_VERTEX_COMPONENT_COUNT     = 6,
    WALLE_LG_POSTGUARD_MAX_POLYGON_VERTEX_COUNT   = 7,
    WALLE_LG_POSTGUARD_MAX_CHILDREN_PER_PRIMITIVE = 5,
    WALLE_LG_POSTGUARD_MAX_SOURCE_PRIMITIVE_COUNT = WALLE_LG_REVEAL_MAX_INDEX_COUNT / 3,
    WALLE_LG_POSTGUARD_MAX_CHILD_COUNT
    = WALLE_LG_POSTGUARD_MAX_SOURCE_PRIMITIVE_COUNT * WALLE_LG_POSTGUARD_MAX_CHILDREN_PER_PRIMITIVE,
};

enum walle_lg_postguard_status : uint8_t
{
    WALLE_LG_POSTGUARD_OK = 0,
    WALLE_LG_POSTGUARD_INVALID_ARGUMENT,
    WALLE_LG_POSTGUARD_INVALID_GEOMETRY,
    WALLE_LG_POSTGUARD_ARITHMETIC_RANGE,
    WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED,
};

enum walle_lg_postguard_owner_policy : uint8_t
{
    /* Base owners stay global.  Child helpers resolve only inside that child,
     * then fall back to the center primitive when its partner is inactive. */
    WALLE_LG_POSTGUARD_CHILD_SCOPED_CENTER_FALLBACK = 1,
};

struct walle_lg_postguard_vertex
{
    uint32_t component_bits[WALLE_LG_POSTGUARD_VERTEX_COMPONENT_COUNT];
};

struct walle_lg_postguard_child
{
    struct walle_lg_postguard_vertex     vertices[3];
    uint8_t                              source_primitive;
    enum walle_lg_postguard_owner_policy owner_policy;
};

struct walle_lg_postguard_children
{
    /* x-low, x-high, y-low, y-high in exact IEEE-754 binary32 encoding. */
    uint32_t                        guard_bits[4];
    uint32_t                        child_count;
    struct walle_lg_postguard_child children[WALLE_LG_POSTGUARD_MAX_CHILD_COUNT];
};

[[nodiscard]]
enum walle_lg_postguard_status
walle_lg_postguard_children_construct(const struct walle_lg_reveal_mask_geometry* geometry,
                                      const uint32_t                              target_extent[2],
                                      struct walle_lg_postguard_children*         result);

#endif
