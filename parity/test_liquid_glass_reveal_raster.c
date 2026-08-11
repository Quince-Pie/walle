#include "liquid_glass_raster.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

constexpr size_t P25_BYTE_COUNT = 1u << 21;
constexpr size_t APPLE_FAST_SQRT_CODE_COUNT = 1u << 23;
constexpr size_t APPLE_FAST_SQRT_PACKED_BYTE_COUNT = APPLE_FAST_SQRT_CODE_COUNT / 2;

static void check(bool condition, const char* message)
{
    if (condition)
        return;
    fprintf(stderr, "reveal raster test failed: %s\n", message);
    exit(EXIT_FAILURE);
}

static uint8_t* load_p25(const char* path)
{
    FILE* input = fopen(path, "rb");
    check(input != nullptr, "open P25 calibration");
    uint8_t* bitmap = malloc(P25_BYTE_COUNT);
    check(bitmap != nullptr, "allocate P25 calibration");
    check(fread(bitmap, 1, P25_BYTE_COUNT, input) == P25_BYTE_COUNT,
          "read P25 calibration");
    check(fgetc(input) == EOF, "P25 calibration has exact length");
    check(fclose(input) == 0, "close P25 calibration");
    return bitmap;
}

static uint8_t* load_exact(const char* path, size_t byte_count, const char* description)
{
    FILE* input = fopen(path, "rb");
    check(input != nullptr, description);
    uint8_t* bytes = malloc(byte_count);
    check(bytes != nullptr, "allocate exact provenance input");
    check(fread(bytes, 1, byte_count, input) == byte_count, description);
    check(fgetc(input) == EOF, "provenance input has exact length");
    check(fclose(input) == 0, "close provenance input");
    return bytes;
}

static void test_apple_fast_sqrt_packing(const char* original_path, const char* packed_path)
{
    uint8_t* original = load_exact(
        original_path, APPLE_FAST_SQRT_CODE_COUNT, "read original Apple arithmetic table");
    uint8_t* packed = load_exact(
        packed_path, APPLE_FAST_SQRT_PACKED_BYTE_COUNT, "read packed Apple fast-sqrt table");
    bool unrelated_high_nibble_present = false;
    for (size_t index = 0; index < APPLE_FAST_SQRT_CODE_COUNT; ++index) {
        unsigned shift = (unsigned)(index & 1u) * 4u;
        uint8_t decoded = (uint8_t)((packed[index / 2u] >> shift) & UINT8_C(0x0f));
        check(decoded == (original[index] & UINT8_C(0x0f)),
              "packed Apple fast-sqrt code matches original low nibble");
        /* The discarded high nibble belongs to other recovered arithmetic tables. */
        unrelated_high_nibble_present |= (original[index] & UINT8_C(0xf0)) != 0;
    }
    check(unrelated_high_nibble_present,
          "original table retains unrelated high-nibble arithmetic codes");
    free(packed);
    free(original);
}

static uint64_t packed_hash(const struct walle_lg_reveal_raster* raster)
{
    uint64_t hash = UINT64_C(0xcbf29ce484222325);
    for (size_t index = 0; index < raster->packed_word_count; ++index) {
        uint32_t word = raster->packed_words[index];
        for (unsigned shift = 0; shift < 32; shift += 8) {
            hash ^= (word >> shift) & UINT32_C(0xff);
            hash *= UINT64_C(0x100000001b3);
        }
    }
    return hash;
}

static uint64_t hash_u32(uint64_t hash, uint32_t value)
{
    for (unsigned shift = 0; shift < 32; shift += 8) {
        hash ^= (value >> shift) & UINT32_C(0xff);
        hash *= UINT64_C(0x100000001b3);
    }
    return hash;
}

static void check_invariants(const struct walle_lg_reveal_raster* raster)
{
    size_t expected_words = (size_t)raster->owner_count * WALLE_LG_RASTER_PRIMITIVE_COUNT
                            * raster->packed_width * WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT;
    check(raster->base_owner_count <= WALLE_LG_REVEAL_RASTER_MAX_BASE_OWNER_COUNT
              && raster->base_owner_count <= raster->owner_count
              && raster->owner_count <= WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT,
          "owner counts stay within capacity");
    check(raster->postguard_child_count
                  == raster->supported_postguard_child_count
                         + raster->unsupported_postguard_child_count
                         + raster->offscreen_postguard_child_count
              && raster->supported_postguard_child_count
                     == raster->owner_count - raster->base_owner_count,
          "every postguard child has one typed outcome");
    check(raster->owner_block.counts[0] == (int32_t)raster->owner_count
              && raster->owner_block.counts[1] == (int32_t)raster->base_owner_count
              && raster->owner_block.counts[2] == 0 && raster->owner_block.counts[3] == 0,
          "owner block count header matches raster");
    check(raster->packed_word_count == expected_words, "packed word count matches image extent");
    check((expected_words == 0) == (raster->packed_words == nullptr),
          "packed allocation follows image extent");
    for (size_t slot = 0; slot < raster->owner_count; ++slot) {
        const struct walle_lg_reveal_raster_quad* quad = &raster->owners[slot];
        int32_t lower = quad->visible_bounds[0] < quad->visible_bounds[1]
                            ? quad->visible_bounds[0]
                            : quad->visible_bounds[1];
        int32_t upper = quad->visible_bounds[2] > quad->visible_bounds[3]
                            ? quad->visible_bounds[2]
                            : quad->visible_bounds[3];
        uint32_t count = (uint32_t)((int64_t)upper + 1 - quad->axis_start);
        check(quad->axis_start == lower - 1, "axis begins with one helper-lane halo pixel");
        check(count <= raster->packed_width, "axis row fits common width");
        check(quad->extent_fixed[0] > 0 && quad->extent_fixed[1] > 0,
              "quad has positive fixed extent");
        check(quad->active_primitive_mask > 0 && quad->active_primitive_mask <= 3,
              "quad active mask addresses geometric primitives");
        check(memcmp(raster->owner_block.bounds[slot],
                     quad->visible_bounds,
                     sizeof quad->visible_bounds)
                      == 0
                  && raster->owner_block.origin_extent[slot][0] == quad->origin_fixed[0]
                  && raster->owner_block.origin_extent[slot][1] == quad->origin_fixed[1]
                  && raster->owner_block.origin_extent[slot][2] == quad->extent_fixed[0]
                  && raster->owner_block.origin_extent[slot][3] == quad->extent_fixed[1]
                  && raster->owner_block.control[slot][0] == quad->axis_start
                  && raster->owner_block.control[slot][1] == quad->ascending_diagonal
                  && raster->owner_block.control[slot][2] == quad->active_primitive_mask
                  && raster->owner_block.control[slot][3]
                         == (slot < raster->base_owner_count
                                 ? 0
                                 : WALLE_LG_POSTGUARD_CHILD_SCOPED_CENTER_FALLBACK),
              "owner block row matches raster metadata");
        for (size_t primitive = 0; primitive < WALLE_LG_RASTER_PRIMITIVE_COUNT; ++primitive) {
            if ((quad->active_primitive_mask & (uint8_t)(1u << primitive)) == 0) {
                for (size_t coordinate = 0; coordinate < count; ++coordinate) {
                    size_t base = ((slot * WALLE_LG_RASTER_PRIMITIVE_COUNT + primitive)
                                       * (size_t)raster->packed_width
                                   + coordinate)
                                  * WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT;
                    check(raster->packed_words[base] == 0 && raster->packed_words[base + 1] == 0,
                          "inactive primitive rows stay zero");
                }
            }
            for (size_t coordinate = count; coordinate < raster->packed_width; ++coordinate) {
                size_t base = ((slot * WALLE_LG_RASTER_PRIMITIVE_COUNT + primitive)
                                   * (size_t)raster->packed_width
                               + coordinate)
                              * WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT;
                for (size_t channel = 0; channel < WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT;
                     ++channel) {
                    check(raster->packed_words[base + channel] == 0,
                          "short rows have zero padding");
                }
            }
        }
    }
    for (size_t ordinal = 0; ordinal < WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT; ++ordinal) {
        const struct walle_lg_reveal_raster_primitive* mapping = &raster->primitives[ordinal];
        bool invalid = mapping->packed_slot == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING
                       && mapping->geometric_primitive
                              == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING;
        if (ordinal >= raster->original_primitive_count) {
            check(invalid, "unused mappings retain invalid sentinel");
        } else if (!invalid) {
            check(mapping->packed_slot < raster->base_owner_count
                      && mapping->geometric_primitive < 2,
                  "active mapping addresses packed image");
            check((raster->owners[mapping->packed_slot].active_primitive_mask
                   & (uint8_t)(1u << mapping->geometric_primitive))
                      != 0,
                  "mapping is present in owner mask");
        }
    }
}

static void test_state(uint32_t                                  state,
                       enum walle_lg_reveal_mask_family          expected_family,
                       uint32_t                                  expected_quads,
                       uint32_t                                  expected_width,
                       size_t                                    expected_words,
                       uint64_t                                  expected_hash,
                       const uint8_t*                            expected_mapping,
                       const struct walle_lg_raster_calibration* calibration)
{
    const struct walle_lg_reveal_mask_request request = {
        .target_width   = 2'048,
        .target_height  = 2'048,
        .center_x       = 512.0,
        .center_y       = 614.4,
        .maximum_radius = 2164.104505809273,
        .progress       = (double)state / 64.0,
    };
    struct walle_lg_reveal_mask_geometry geometry;
    struct walle_lg_reveal_raster        raster;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct public reveal geometry");
    check(walle_lg_reveal_raster_construct(&geometry, 2'048, 2'048, calibration, &raster)
              == WALLE_LG_REVEAL_RASTER_OK,
          "construct exact packed reveal raster");
    check_invariants(&raster);
    check(geometry.family == expected_family && raster.owner_count == expected_quads
              && raster.packed_width == expected_width
              && raster.packed_word_count == expected_words
              && packed_hash(&raster) == expected_hash,
          "selected state packed table signature");
    for (size_t ordinal = 0; ordinal < raster.original_primitive_count; ++ordinal) {
        check(raster.primitives[ordinal].packed_slot == expected_mapping[ordinal * 2]
                  && raster.primitives[ordinal].geometric_primitive
                         == expected_mapping[ordinal * 2 + 1],
              "selected state original-to-packed mapping");
    }
    walle_lg_reveal_raster_destroy(&raster);
}

static void test_corpus(const struct walle_lg_raster_calibration* calibration)
{
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
    uint32_t child_count = 0;
    uint32_t supported_child_count = 0;
    uint32_t unsupported_child_count = 0;
    uint32_t offscreen_child_count = 0;
    for (uint32_t state = 1; state <= 64; ++state) {
        request.progress = (double)state / 64.0;
        struct walle_lg_reveal_mask_geometry geometry;
        struct walle_lg_reveal_raster        raster;
        check(walle_lg_reveal_mask_geometry_construct(&request, &geometry)
                  && walle_lg_reveal_raster_construct(
                         &geometry, 2'048, 2'048, calibration, &raster)
                         == WALLE_LG_REVEAL_RASTER_OK,
              "construct exhaustive exact reveal raster");
        check_invariants(&raster);
        compact_count += geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS;
        child_count += raster.postguard_child_count;
        supported_child_count += raster.supported_postguard_child_count;
        unsupported_child_count += raster.unsupported_postguard_child_count;
        offscreen_child_count += raster.offscreen_postguard_child_count;
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
            const struct walle_lg_reveal_raster_quad* quad = &raster.owners[slot];
            signature = hash_u32(signature, (uint32_t)quad->axis_start);
            for (size_t axis = 0; axis < 2; ++axis) {
                signature = hash_u32(signature, (uint32_t)quad->origin_fixed[axis]);
                signature = hash_u32(signature, (uint32_t)quad->extent_fixed[axis]);
            }
            for (size_t bound = 0; bound < 4; ++bound)
                signature = hash_u32(signature, (uint32_t)quad->visible_bounds[bound]);
            signature = hash_u32(signature, quad->ascending_diagonal ? 1u : 0u);
            signature = hash_u32(signature, quad->active_primitive_mask);
        }
        for (size_t ordinal = 0; ordinal < raster.original_primitive_count; ++ordinal) {
            signature = hash_u32(signature, raster.primitives[ordinal].packed_slot);
            signature = hash_u32(signature, raster.primitives[ordinal].geometric_primitive);
        }
        walle_lg_reveal_raster_destroy(&raster);
    }
    check(compact_count == 12 && child_count == 271 && supported_child_count == 153
              && unsupported_child_count == 88 && offscreen_child_count == 30
              && maximum_width == 4'434 && total_words == 4'265'112
              && signature == UINT64_C(0x3b6772fbfa33dc13),
          "64-state packed raster census and signature");
}

static void test_rejection(const struct walle_lg_raster_calibration* calibration)
{
    struct walle_lg_reveal_mask_geometry geometry = {
        .family       = WALLE_LG_REVEAL_MASK_BORDER_GRID,
        .vertex_count = 4,
        .index_count  = 30,
    };
    const float position[4][2] = {{0, 0}, {1, 0}, {1, 1}, {0, 1}};
    for (size_t vertex = 0; vertex < 4; ++vertex) {
        geometry.vertices[vertex].position[0] = position[vertex][0];
        geometry.vertices[vertex].position[1] = position[vertex][1];
        geometry.vertices[vertex].position[3] = 1.0f;
        geometry.vertices[vertex].second_coordinates[0]
            = position[vertex][0] * 2.0f - 1.0f;
        geometry.vertices[vertex].second_coordinates[1]
            = position[vertex][1] * 2.0f - 1.0f;
    }
    constexpr uint16_t indices[6] = {0, 1, 2, 2, 3, 0};
    for (size_t group = 0; group < 5; ++group) {
        for (size_t local = 0; local < 6; ++local)
            geometry.indices[group * 6 + local] = indices[local];
    }
    struct walle_lg_reveal_raster result = {.owner_count = 99};
    check(walle_lg_reveal_raster_construct(&geometry, 2'048, 2'048, calibration, &result)
              == WALLE_LG_REVEAL_RASTER_CAPACITY_EXCEEDED,
          "fifth active quad is rejected");
    check(result.owner_count == 99, "failed construction leaves result untouched");

    constexpr float tiny_position[4][2] = {
        {0.10f, 0.10f},
        {0.20f, 0.10f},
        {0.20f, 0.20f},
        {0.10f, 0.20f},
    };
    geometry.vertex_count = 8;
    for (size_t vertex = 0; vertex < 4; ++vertex) {
        size_t destination = vertex + 4;
        geometry.vertices[destination].position[0] = tiny_position[vertex][0];
        geometry.vertices[destination].position[1] = tiny_position[vertex][1];
        geometry.vertices[destination].position[3] = 1.0f;
        geometry.vertices[destination].second_coordinates[0]
            = tiny_position[vertex][0] * 2.0f - 1.0f;
        geometry.vertices[destination].second_coordinates[1]
            = tiny_position[vertex][1] * 2.0f - 1.0f;
    }
    for (size_t local = 0; local < 6; ++local)
        geometry.indices[24 + local] = (uint16_t)(indices[local] + 4);
    check(walle_lg_reveal_raster_construct(&geometry, 2'048, 2'048, calibration, &result)
              == WALLE_LG_REVEAL_RASTER_OK,
          "empty fifth quad does not consume packed capacity");
    check_invariants(&result);
    check(result.base_owner_count == 4, "only integer-visible quads consume base owner slots");
    walle_lg_reveal_raster_destroy(&result);

    struct walle_lg_raster_calibration wrong = *calibration;
    --wrong.p25_selector_bit_count;
    geometry.index_count = 6;
    check(walle_lg_reveal_raster_construct(&geometry, 2'048, 2'048, &wrong, &result)
              == WALLE_LG_REVEAL_RASTER_INVALID_ARGUMENT,
          "wrong P25 bitmap length is rejected");
    result.owner_count = 77;
    check(walle_lg_reveal_raster_construct(&geometry, 0, 2'048, calibration, &result)
                  == WALLE_LG_REVEAL_RASTER_INVALID_ARGUMENT
              && result.owner_count == 77,
          "zero target extent is rejected without touching the result");
}

static void test_empty_integer_coverage(const struct walle_lg_raster_calibration* calibration)
{
    const struct walle_lg_reveal_mask_request request = {
        .target_width   = 1,
        .target_height  = 1,
        .center_x       = 0.5,
        .center_y       = 0.5,
        .maximum_radius = 0.728319984622144,
        .progress       = 0.25,
    };
    struct walle_lg_reveal_mask_geometry geometry;
    struct walle_lg_reveal_raster        raster;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct tiny public reveal geometry");
    check(geometry.index_count > 0, "tiny geometry exercises nonempty source triangles");
    check(walle_lg_reveal_raster_construct(&geometry, 1, 1, calibration, &raster)
              == WALLE_LG_REVEAL_RASTER_OK,
          "tiny geometry with no covered sample center is valid");
    check_invariants(&raster);
    bool skipped_source_group = false;
    for (size_t primitive = 0; primitive < raster.original_primitive_count; ++primitive) {
        skipped_source_group
            |= raster.primitives[primitive].packed_slot == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING;
    }
    check(skipped_source_group, "integer-empty source groups are omitted from the packed table");
    walle_lg_reveal_raster_destroy(&raster);
}

static void test_extreme_fixed_coordinates(const struct walle_lg_raster_calibration* calibration)
{
    constexpr float base            = -8'388'608.0f;
    constexpr float positions[4][2] = {
        {base, base},
        {base + 1.0f, base},
        {base + 1.0f, base + 1.0f},
        {base, base + 1.0f},
    };
    constexpr float                      coordinates[4][2] = {{-1, -1}, {1, -1}, {1, 1}, {-1, 1}};
    constexpr uint16_t                   indices[6]        = {0, 1, 2, 2, 3, 0};
    struct walle_lg_reveal_mask_geometry geometry          = {
                 .family       = WALLE_LG_REVEAL_MASK_BORDER_GRID,
                 .vertex_count = 4,
                 .index_count  = 6,
    };
    for (size_t vertex = 0; vertex < 4; ++vertex) {
        geometry.vertices[vertex].position[0]           = positions[vertex][0];
        geometry.vertices[vertex].position[1]           = positions[vertex][1];
        geometry.vertices[vertex].position[3]           = 1.0f;
        geometry.vertices[vertex].second_coordinates[0] = coordinates[vertex][0];
        geometry.vertices[vertex].second_coordinates[1] = coordinates[vertex][1];
    }
    memcpy(geometry.indices, indices, sizeof indices);

    struct walle_lg_reveal_raster raster;
    check(walle_lg_reveal_raster_construct(&geometry, 2'048, 2'048, calibration, &raster)
              == WALLE_LG_REVEAL_RASTER_OK,
          "INT32_MIN-adjacent fixed coordinates are handled without overflow");
    check_invariants(&raster);
    check(raster.base_owner_count == 1
              && raster.owners[0].visible_bounds[0] == -8'388'608
              && raster.owners[0].visible_bounds[1] == -8'388'608
              && raster.owners[0].visible_bounds[2] == -8'388'607
              && raster.owners[0].visible_bounds[3] == -8'388'607,
          "INT32_MIN-adjacent visible bounds use wide arithmetic");
    walle_lg_reveal_raster_destroy(&raster);
}

static void test_postguard_capacity_and_ordering(void)
{
    static constexpr float positions[3][2] = {
        {2.989964485168457f, -2.5969796180725098f},
        {0.4485771954059601f, 1.823648452758789f},
        {-0.6626309752464294f, 0.8036954402923584f},
    };
    struct walle_lg_reveal_mask_geometry geometry = {
        .family       = WALLE_LG_REVEAL_MASK_BORDER_GRID,
        .vertex_count = 3,
        .index_count  = WALLE_LG_REVEAL_MAX_INDEX_COUNT,
    };
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        geometry.vertices[vertex].position[0]           = positions[vertex][0];
        geometry.vertices[vertex].position[1]           = positions[vertex][1];
        geometry.vertices[vertex].position[3]           = 1.0f;
        geometry.vertices[vertex].second_coordinates[0] = (float)vertex - 1.0f;
        geometry.vertices[vertex].second_coordinates[1] = 1.0f - (float)vertex;
    }
    for (size_t primitive = 0; primitive < WALLE_LG_POSTGUARD_MAX_SOURCE_PRIMITIVE_COUNT;
         ++primitive) {
        geometry.indices[primitive * 3]     = 0;
        geometry.indices[primitive * 3 + 1] = 1;
        geometry.indices[primitive * 3 + 2] = 2;
    }
    const uint32_t                   extent[2] = {1, 1};
    struct walle_lg_postguard_children children;
    check(walle_lg_postguard_children_construct(&geometry, extent, &children)
                  == WALLE_LG_POSTGUARD_OK
              && children.child_count == WALLE_LG_POSTGUARD_MAX_CHILD_COUNT,
          "seven-vertex fan reaches the exact 90-child capacity");
    for (size_t child = 0; child < children.child_count; ++child) {
        check(children.children[child].source_primitive
                      == child / WALLE_LG_POSTGUARD_MAX_CHILDREN_PER_PRIMITIVE
                  && children.children[child].owner_policy
                         == WALLE_LG_POSTGUARD_CHILD_SCOPED_CENTER_FALLBACK,
              "maximum-capacity children preserve source/fan ordering and policy");
    }
}

static void test_general_extents(const struct walle_lg_raster_calibration* calibration)
{
    static constexpr uint32_t extents[][2] = {
        {1, 1},
        {3, 5},
        {7, 9},
        {191, 127},
        {2'049, 3'073},
    };
    for (size_t extent = 0; extent < sizeof extents / sizeof extents[0]; ++extent) {
        uint32_t width  = extents[extent][0];
        uint32_t height = extents[extent][1];
        double   center_x = (double)width * 0.37;
        double   center_y = (double)height * 0.61;
        double   radius = fmax(hypot(center_x, center_y),
                            fmax(hypot((double)width - center_x, center_y),
                                 fmax(hypot(center_x, (double)height - center_y),
                                      hypot((double)width - center_x,
                                            (double)height - center_y))))
                        * 1.03;
        for (uint32_t state = 1; state < 8; state += 2) {
            const struct walle_lg_reveal_mask_request request = {
                .target_width   = width,
                .target_height  = height,
                .center_x       = center_x,
                .center_y       = center_y,
                .maximum_radius = radius,
                .progress       = (double)state / 8.0,
            };
            struct walle_lg_reveal_mask_geometry geometry;
            struct walle_lg_reveal_raster        raster;
            check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
                  "construct odd/general-extent reveal geometry");
            check(walle_lg_reveal_raster_construct(
                      &geometry, width, height, calibration, &raster)
                          == WALLE_LG_REVEAL_RASTER_OK,
                  "construct odd/general-extent reveal raster");
            check_invariants(&raster);
            walle_lg_reveal_raster_destroy(&raster);
        }
    }
}

int main(int argc, char** argv)
{
    const char* path = argc > 1 ? argv[1] : "parity/raster_p25_selector_ceil_bits.bin";
    const char* original_fast_sqrt
        = argc > 2 ? argv[2] : "artifacts/apple-float-intrinsics-r8-30556057571.bin";
    const char* packed_fast_sqrt
        = argc > 3 ? argv[3] : "parity/apple_fast_sqrt_correction_nibbles.bin";
    check(argc <= 4, "accept at most P25, original arithmetic, and packed fast-sqrt paths");
    test_apple_fast_sqrt_packing(original_fast_sqrt, packed_fast_sqrt);
    uint8_t*    p25 = load_p25(path);
    const struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = p25,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
    static constexpr uint8_t state_1_mapping[] = {
        0, 0, 0, 1, 1, 0, 1, 1, 2, 0, 2, 1, 3, 0, 3, 1,
        255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255,
        255, 255, 255, 255,
    };
    static constexpr uint8_t state_5_mapping[] = {
        0, 1, 255, 255, 1, 0, 255, 255, 255, 255, 2, 1, 255, 255, 3, 0,
    };
    static constexpr uint8_t state_42_mapping[] = {
        0, 0, 0, 1, 1, 0, 1, 1, 2, 0, 2, 1, 3, 0, 3, 1,
        255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255,
    };
    static constexpr uint8_t state_48_mapping[] = {
        0, 0, 255, 255, 255, 255, 1, 0,
    };
    test_state(1,
               WALLE_LG_REVEAL_MASK_BORDER_GRID,
               4,
               174,
               2'784,
               UINT64_C(0xad03ae84d1c2eee5),
               state_1_mapping,
               &calibration);
    test_state(5,
               WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS,
               4,
               442,
               7'072,
               UINT64_C(0x57172554a75c00ac),
               state_5_mapping,
               &calibration);
    test_state(42,
               WALLE_LG_REVEAL_MASK_BORDER_GRID,
               9,
               2'946,
               106'056,
               UINT64_C(0x35801e8108bf0aa8),
               state_42_mapping,
               &calibration);
    test_state(48,
               WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS,
               3,
               3'146,
               37'752,
               UINT64_C(0xbc06b1e9a1866d03),
               state_48_mapping,
               &calibration);
    test_corpus(&calibration);
    test_postguard_capacity_and_ordering();
    test_general_extents(&calibration);
    test_empty_integer_coverage(&calibration);
    test_extreme_fixed_coordinates(&calibration);
    test_rejection(&calibration);
    free(p25);
    puts("reveal raster: 64 public states exact and packed");
    return EXIT_SUCCESS;
}
