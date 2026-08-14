#include "../parity/liquid_glass_raster.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

constexpr size_t P25_BYTE_COUNT = 1u << 21;

static uint64_t hash_u32(uint64_t hash, uint32_t value)
{
    for (unsigned shift = 0; shift < 32; shift += 8) {
        hash ^= (value >> shift) & UINT32_C(0xff);
        hash *= UINT64_C(0x100000001b3);
    }
    return hash;
}

static uint64_t packed_hash(const struct walle_lg_reveal_raster* raster)
{
    uint64_t hash = UINT64_C(0xcbf29ce484222325);
    for (size_t index = 0; index < raster->packed_word_count; ++index)
        hash = hash_u32(hash, raster->packed_words[index]);
    return hash;
}

static float from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static bool child_outside(const struct walle_lg_postguard_child* child,
                          int32_t                                left,
                          int32_t                                top,
                          int32_t                                right,
                          int32_t                                bottom)
{
    float low_x = from_bits(child->vertices[0].component_bits[0]);
    float high_x = low_x;
    float low_y = from_bits(child->vertices[0].component_bits[1]);
    float high_y = low_y;
    for (size_t vertex = 1; vertex < 3; ++vertex) {
        float x = from_bits(child->vertices[vertex].component_bits[0]);
        float y = from_bits(child->vertices[vertex].component_bits[1]);
        if (x < low_x)
            low_x = x;
        if (x > high_x)
            high_x = x;
        if (y < low_y)
            low_y = y;
        if (y > high_y)
            high_y = y;
    }
    return high_x <= left || high_y <= top || low_x >= right || low_y >= bottom;
}

int main(void)
{
    FILE* input = fopen("parity/raster_p25_selector_ceil_bits.bin", "rb");
    if (input == nullptr)
        return EXIT_FAILURE;
    uint8_t* p25 = malloc(P25_BYTE_COUNT);
    if (p25 == nullptr || fread(p25, 1, P25_BYTE_COUNT, input) != P25_BYTE_COUNT
        || fgetc(input) != EOF || fclose(input) != 0) {
        return EXIT_FAILURE;
    }
    const struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = p25,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
    struct walle_lg_reveal_mask_request request = {
        .target_width   = 2'048,
        .target_height  = 2'048,
        .center_x       = 512.0,
        .center_y       = 614.4,
        .maximum_radius = 2164.104505809273,
    };
    uint64_t signature     = UINT64_C(0xcbf29ce484222325);
    uint64_t total_words   = 0;
    uint32_t maximum_width = 0;
    uint32_t compact_count = 0;
    uint32_t total_children = 0;
    uint32_t total_supported = 0;
    uint32_t total_unsupported = 0;
    uint32_t total_offscreen = 0;
    uint32_t child_outside_viewport = 0;
    uint32_t child_outside_scissor = 0;
    for (uint32_t state = 1; state <= 64; ++state) {
        request.progress = (double)state / 64.0;
        struct walle_lg_reveal_mask_geometry geometry;
        struct walle_lg_reveal_raster        raster;
        if (!walle_lg_reveal_mask_geometry_construct(&request, &geometry)
            || walle_lg_reveal_raster_construct(
                   &geometry, 2'048, 2'048, &calibration, &raster)
                   != WALLE_LG_REVEAL_RASTER_OK) {
            return EXIT_FAILURE;
        }
        compact_count += geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS;
        total_children += raster.postguard_child_count;
        total_supported += raster.supported_postguard_child_count;
        total_unsupported += raster.unsupported_postguard_child_count;
        total_offscreen += raster.offscreen_postguard_child_count;
        struct walle_lg_postguard_children children;
        const uint32_t extent[2] = {2'048, 2'048};
        if (walle_lg_postguard_children_construct(&geometry, extent, &children)
            != WALLE_LG_POSTGUARD_OK) {
            return EXIT_FAILURE;
        }
        for (size_t child = 0; child < children.child_count; ++child) {
            child_outside_viewport += child_outside(&children.children[child], 0, 0, 2'048, 2'048);
            child_outside_scissor += child_outside(
                &children.children[child],
                geometry.circle.scissor[0],
                geometry.circle.scissor[1],
                geometry.circle.scissor[0] + geometry.circle.scissor[2],
                geometry.circle.scissor[1] + geometry.circle.scissor[3]);
        }
        total_words += raster.packed_word_count;
        if (raster.packed_width > maximum_width)
            maximum_width = raster.packed_width;
        signature = hash_u32(signature, state);
        signature = hash_u32(signature, (uint32_t)geometry.family);
        signature = hash_u32(signature, raster.owner_count);
        signature = hash_u32(signature, raster.packed_width);
        signature = hash_u32(signature, (uint32_t)raster.packed_word_count);
        for (size_t word = 0; word < raster.packed_word_count; ++word)
            signature = hash_u32(signature, raster.packed_words[word]);
        for (size_t slot = 0; slot < raster.owner_count; ++slot) {
            const struct walle_lg_reveal_raster_quad* owner = &raster.owners[slot];
            signature = hash_u32(signature, (uint32_t)owner->axis_start);
            for (size_t axis = 0; axis < 2; ++axis) {
                signature = hash_u32(signature, (uint32_t)owner->origin_fixed[axis]);
                signature = hash_u32(signature, (uint32_t)owner->extent_fixed[axis]);
            }
            for (size_t bound = 0; bound < 4; ++bound)
                signature = hash_u32(signature, (uint32_t)owner->visible_bounds[bound]);
            signature = hash_u32(signature, owner->ascending_diagonal ? 1u : 0u);
            signature = hash_u32(signature, owner->active_primitive_mask);
        }
        for (size_t ordinal = 0; ordinal < raster.original_primitive_count; ++ordinal) {
            signature = hash_u32(signature, raster.primitives[ordinal].packed_slot);
            signature = hash_u32(signature, raster.primitives[ordinal].geometric_primitive);
        }
        if (state == 1 || state == 5 || state == 42 || state == 48) {
            printf("state=%u family=%u owners=%u base=%u children=%u supported=%u unsupported=%u "
                   "offscreen=%u width=%u words=%zu packedHash=0x%016llx\n",
                   state,
                   geometry.family,
                   raster.owner_count,
                   raster.base_owner_count,
                   raster.postguard_child_count,
                   raster.supported_postguard_child_count,
                   raster.unsupported_postguard_child_count,
                   raster.offscreen_postguard_child_count,
                   raster.packed_width,
                   raster.packed_word_count,
                   (unsigned long long)packed_hash(&raster));
        }
        walle_lg_reveal_raster_destroy(&raster);
    }
    printf("compact=%u children=%u supported=%u unsupported=%u offscreen=%u "
           "outsideViewport=%u outsideScissor=%u "
           "maximumWidth=%u totalWords=%llu signature=0x%016llx\n",
           compact_count,
           total_children,
           total_supported,
           total_unsupported,
           total_offscreen,
           child_outside_viewport,
           child_outside_scissor,
           maximum_width,
           (unsigned long long)total_words,
           (unsigned long long)signature);
    free(p25);
    return EXIT_SUCCESS;
}
