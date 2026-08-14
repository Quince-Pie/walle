#include "reveal_postguard_children_test_shim.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "reveal_postguard_children.h"

uint32_t walle_test_postguard_construct(uint8_t         family,
                                        uint32_t        width,
                                        uint32_t        height,
                                        uint32_t        vertex_count,
                                        uint32_t        index_count,
                                        const uint32_t* vertex_bits,
                                        const uint16_t* indices,
                                        uint32_t        child_capacity,
                                        uint32_t        guard_bits[4],
                                        uint32_t*       child_count,
                                        uint32_t*       child_bits,
                                        uint8_t*        child_metadata)
{
    if (vertex_bits == nullptr || indices == nullptr || guard_bits == nullptr
        || child_count == nullptr || child_bits == nullptr || child_metadata == nullptr
        || vertex_count > WALLE_LG_REVEAL_MAX_VERTEX_COUNT
        || index_count > WALLE_LG_REVEAL_MAX_INDEX_COUNT) {
        return WALLE_LG_POSTGUARD_INVALID_ARGUMENT;
    }
    struct walle_lg_reveal_mask_geometry geometry = {
        .family       = (enum walle_lg_reveal_mask_family)family,
        .vertex_count = vertex_count,
        .index_count  = index_count,
    };
    for (size_t vertex = 0; vertex < vertex_count; ++vertex) {
        for (size_t component = 0; component < 4; ++component) {
            memcpy(&geometry.vertices[vertex].position[component],
                   &vertex_bits[vertex * 8 + component],
                   sizeof(uint32_t));
        }
        for (size_t component = 0; component < 2; ++component) {
            memcpy(&geometry.vertices[vertex].first_coordinates[component],
                   &vertex_bits[vertex * 8 + component + 4],
                   sizeof(uint32_t));
            memcpy(&geometry.vertices[vertex].second_coordinates[component],
                   &vertex_bits[vertex * 8 + component + 6],
                   sizeof(uint32_t));
        }
    }
    memcpy(geometry.indices, indices, index_count * sizeof *indices);
    const uint32_t                     extent[2] = {width, height};
    struct walle_lg_postguard_children children;
    enum walle_lg_postguard_status     status
        = walle_lg_postguard_children_construct(&geometry, extent, &children);
    if (status != WALLE_LG_POSTGUARD_OK)
        return (uint32_t)status;
    if (children.child_count > child_capacity)
        return WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED;
    memcpy(guard_bits, children.guard_bits, sizeof children.guard_bits);
    *child_count = children.child_count;
    for (size_t child = 0; child < children.child_count; ++child) {
        for (size_t vertex = 0; vertex < 3; ++vertex) {
            memcpy(child_bits + child * 18 + vertex * 6,
                   children.children[child].vertices[vertex].component_bits,
                   6 * sizeof(uint32_t));
        }
        child_metadata[child * 2]     = children.children[child].source_primitive;
        child_metadata[child * 2 + 1] = (uint8_t)children.children[child].owner_policy;
    }
    return WALLE_LG_POSTGUARD_OK;
}
