/* Report every post-guard child the reveal raster currently drops.
 *
 * The reveal raster packs axis-separable owners as one-dimensional tables, so
 * a clipped child whose SDF varies along both axes cannot be represented and
 * is skipped.  This census reports each skipped child's geometry so the cost
 * of a two-dimensional path can be measured before it is built. */

#include "../parity/liquid_glass_postguard.h"
#include "../parity/liquid_glass_reveal_mask_model.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static float from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

int main(void)
{
    uint32_t target[2] = {2048, 2048};
    size_t   total = 0;
    size_t   axis_aligned = 0;
    size_t   general = 0;
    int64_t  widest_tiles = 0;
    int64_t  tallest_tiles = 0;
    int64_t  worst_tile_area = 0;

    for (uint32_t state = 0; state <= 64; ++state) {
        struct walle_lg_reveal_mask_request request = {
            .target_width = target[0],
            .target_height = target[1],
            .center_x = 512.0,
            .center_y = 614.4,
            .maximum_radius = 2164.104505809273,
            .progress = (double)state / 64.0,
        };
        struct walle_lg_reveal_mask_geometry geometry;
        if (!walle_lg_reveal_mask_geometry_construct(&request, &geometry)) {
            fprintf(stderr, "state %u geometry failed\n", state);
            return 1;
        }
        struct walle_lg_postguard_children children;
        if (walle_lg_postguard_children_construct(&geometry, target, &children)
            != WALLE_LG_POSTGUARD_OK) {
            fprintf(stderr, "state %u postguard failed\n", state);
            return 1;
        }
        for (size_t index = 0; index < children.child_count; ++index) {
            const struct walle_lg_postguard_child* child = &children.children[index];
            float low[2];
            float high[2];
            for (size_t axis = 0; axis < 2; ++axis) {
                low[axis] = from_bits(child->vertices[0].component_bits[axis]);
                high[axis] = low[axis];
                for (size_t vertex = 1; vertex < 3; ++vertex) {
                    float value = from_bits(child->vertices[vertex].component_bits[axis]);
                    if (value < low[axis])
                        low[axis] = value;
                    if (value > high[axis])
                        high[axis] = value;
                }
            }
            /* A child is axis separable exactly when each of its three vertices
             * sits on a corner of the bounding box, which is what the packed
             * one-dimensional representation assumes. */
            size_t corner_vertices = 0;
            for (size_t vertex = 0; vertex < 3; ++vertex) {
                float x = from_bits(child->vertices[vertex].component_bits[0]);
                float y = from_bits(child->vertices[vertex].component_bits[1]);
                bool on_x = x == low[0] || x == high[0];
                bool on_y = y == low[1] || y == high[1];
                corner_vertices += on_x && on_y ? 1u : 0u;
            }
            bool separable = corner_vertices == 3 && high[0] > low[0] && high[1] > low[1];
            ++total;
            if (separable) {
                ++axis_aligned;
                continue;
            }
            ++general;
            int64_t first_tile_x = (int64_t)(low[0] / 32.0f);
            int64_t last_tile_x = (int64_t)(high[0] / 32.0f);
            int64_t first_tile_y = (int64_t)(low[1] / 32.0f);
            int64_t last_tile_y = (int64_t)(high[1] / 32.0f);
            int64_t tiles_x = last_tile_x - first_tile_x + 1;
            int64_t tiles_y = last_tile_y - first_tile_y + 1;
            if (tiles_x > widest_tiles)
                widest_tiles = tiles_x;
            if (tiles_y > tallest_tiles)
                tallest_tiles = tiles_y;
            if (tiles_x * tiles_y > worst_tile_area)
                worst_tile_area = tiles_x * tiles_y;
            printf("state %2u child %2zu source %u  x[%10.4f %10.4f] y[%10.4f %10.4f]"
                   "  tiles %lldx%lld\n",
                   state,
                   index,
                   child->source_primitive,
                   (double)low[0],
                   (double)high[0],
                   (double)low[1],
                   (double)high[1],
                   (long long)tiles_x,
                   (long long)tiles_y);
        }
    }
    printf("total children %zu  axis-separable %zu  general %zu\n", total, axis_aligned, general);
    printf("worst general child tile span %lldx%lld, worst tile area %lld\n",
           (long long)widest_tiles,
           (long long)tallest_tiles,
           (long long)worst_tile_area);
    return 0;
}
