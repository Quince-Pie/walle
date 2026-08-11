#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "liquid_glass_reveal_mask_model.h"

static void check(bool condition, const char* message)
{
    if (condition)
        return;
    fprintf(stderr, "reveal mask model test failed: %s\n", message);
    exit(EXIT_FAILURE);
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static uint8_t* load_table(const char* path)
{
    FILE* file = fopen(path, "rb");
    check(file != nullptr, "open fast-sqrt table");
    uint8_t* table = malloc(WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT);
    check(table != nullptr, "allocate fast-sqrt table");
    check(fread(table, 1, WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT, file)
              == WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT,
          "read fast-sqrt table");
    check(fgetc(file) == EOF, "fast-sqrt table has exact length");
    check(fclose(file) == 0, "close fast-sqrt table");
    return table;
}

static void test_circle_state(void)
{
    const struct walle_lg_reveal_mask_request request = {
        .target_width   = 2'048,
        .target_height  = 2'048,
        .center_x       = 512.0,
        .center_y       = 614.4,
        .maximum_radius = 2164.104505809273,
        .progress       = 42.0 / 64.0,
    };
    struct walle_lg_reveal_mask_circle circle;
    check(walle_lg_reveal_mask_circle_construct(&request, &circle),
          "construct state-42 public circle");
    check(!circle.empty, "state-42 circle is visible");
    check(circle.bounds[0] == -908 && circle.bounds[1] == -806 && circle.bounds[2] == 1932
              && circle.bounds[3] == 2035,
          "state-42 snapped bounds");
    check(circle.scissor[0] == 0 && circle.scissor[1] == 0 && circle.scissor[2] == 1932
              && circle.scissor[3] == 2035,
          "state-42 scissor");
    check(float_bits(circle.center[0]) == UINT32_C(0x44000000)
              && float_bits(circle.center[1]) == UINT32_C(0x4419a000)
              && float_bits(circle.radius) == UINT32_C(0x44b18000)
              && float_bits(circle.expanded_radius) == UINT32_C(0x44b1a000)
              && float_bits(circle.normalized_extent) == UINT32_C(0x3f801713),
          "state-42 float materialization");

    struct walle_lg_reveal_mask_request endpoint = request;
    endpoint.progress                            = 0.0;
    check(walle_lg_reveal_mask_circle_construct(&endpoint, &circle) && circle.empty,
          "zero progress is an empty circle");
    endpoint.progress = 1.01;
    check(!walle_lg_reveal_mask_circle_construct(&endpoint, &circle),
          "progress above one rejected");
}

static void test_public_geometry(void)
{
    struct walle_lg_reveal_mask_request request = {
        .target_width   = 2'048,
        .target_height  = 2'048,
        .center_x       = 512.0,
        .center_y       = 614.4,
        .maximum_radius = 2164.104505809273,
        .progress       = 1.0 / 64.0,
    };
    struct walle_lg_reveal_mask_geometry geometry;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct state-1 public geometry");
    check(geometry.family == WALLE_LG_REVEAL_MASK_BORDER_GRID && geometry.vertex_count == 16
              && geometry.index_count == 54 && !geometry.clear_to_inside,
          "state-1 border family and counts");
    check(float_bits(geometry.vertices[0].position[0]) == UINT32_C(0x43eec000)
              && float_bits(geometry.vertices[0].position[1]) == UINT32_C(0x44110000)
              && float_bits(geometry.vertices[0].second_coordinates[0]) == UINT32_C(0xbf83d226)
              && geometry.vertices[0].half_color[0] == UINT16_C(0x3c00),
          "state-1 active vertex words");

    request.progress = 5.0 / 64.0;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct state-5 public geometry");
    check(geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS
              && geometry.vertex_count == 16 && geometry.index_count == 24
              && geometry.clear_to_inside,
          "state-5 compact family and counts");
    check(float_bits(geometry.vertices[0].position[0]) == UINT32_C(0x43ab8000)
              && float_bits(geometry.vertices[0].first_coordinates[0]) == UINT32_C(0xbf800000)
              && geometry.indices[23] == 12,
          "state-5 active vertex and index words");

    request.progress = 42.0 / 64.0;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct state-42 public geometry");
    check(geometry.family == WALLE_LG_REVEAL_MASK_BORDER_GRID && geometry.vertex_count == 16
              && geometry.index_count == 48,
          "state-42 clipped border family and counts");
    check(float_bits(geometry.vertices[0].position[0]) == UINT32_C(0xc4634000)
              && float_bits(geometry.vertices[0].second_coordinates[0]) == UINT32_C(0xbf801713),
          "state-42 active vertex words");

    request.progress = 48.0 / 64.0;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct state-48 public geometry");
    check(geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS && geometry.vertex_count == 8
              && geometry.index_count == 12,
          "state-48 visible compact quadrants");

    request.progress = 0.0;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry)
              && geometry.family == WALLE_LG_REVEAL_MASK_EMPTY && geometry.vertex_count == 0
              && geometry.index_count == 0,
          "empty endpoint geometry");

    size_t border_count  = 0;
    size_t compact_count = 0;
    for (uint32_t state = 1; state <= 64; ++state) {
        request.progress = (double)state / 64.0;
        check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
              "construct exhaustive public geometry");
        border_count += geometry.family == WALLE_LG_REVEAL_MASK_BORDER_GRID;
        compact_count += geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS;
        check(geometry.vertex_count <= WALLE_LG_REVEAL_MAX_VERTEX_COUNT
                  && geometry.index_count <= WALLE_LG_REVEAL_MAX_INDEX_COUNT,
              "exhaustive geometry stays within fixed capacity");
    }
    check(border_count == 52 && compact_count == 12,
          "all 64 nonempty states select the frozen family census");

    request.center_y       = 614.0;
    request.maximum_radius = 2'200.0;
    request.progress       = 1.0;
    check(walle_lg_reveal_mask_geometry_construct(&request, &geometry),
          "construct fully covering compact geometry");
    check(geometry.family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS && geometry.vertex_count == 0
              && geometry.index_count == 0 && geometry.clear_to_inside,
          "fully covering compact geometry needs only its inside clear");
}

static void test_fast_sqrt(const uint8_t* table)
{
    static const struct
    {
        float    input;
        uint32_t expected_bits;
    } cases[] = {
        {0.0f, UINT32_C(0x00000000)},
        {0.25f, UINT32_C(0x3f000000)},
        {0.5f, UINT32_C(0x3f3504f3)},
        {1.0f, UINT32_C(0x3f800000)},
        {2.0f, UINT32_C(0x3fb504f3)},
        {3.0f, UINT32_C(0x3fddb3d7)},
    };
    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
        float result;
        check(walle_lg_reveal_mask_apple_fast_sqrt(
                  table, WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT, cases[index].input, &result),
              "evaluate Apple fast sqrt");
        check(float_bits(result) == cases[index].expected_bits, "Apple fast-sqrt bits");
    }
    float ignored;
    check(!walle_lg_reveal_mask_apple_fast_sqrt(
              table, WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT - 1u, 1.0f, &ignored),
          "wrong fast-sqrt table length rejected");
    check(!walle_lg_reveal_mask_apple_fast_sqrt(
              table, WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT, -1.0f, &ignored),
          "negative square-root input rejected");
}

static void test_coverage(const uint8_t* table)
{
    static const struct
    {
        struct walle_lg_reveal_mask_sample sample;
        uint32_t                           distance_bits;
        uint32_t                           feather_bits;
        uint16_t                           alpha_half_bits;
        uint8_t                            coverage;
    } cases[] = {
        {{0.0f, 0.0f, 0.1f, 0.1f},
         UINT32_C(0x00000000),
         UINT32_C(0x3e4cccce),
         UINT16_C(0x3c00),
         UINT8_C(255)},
        {{1.0f, 0.0f, 1.1f, 0.1f},
         UINT32_C(0x3f800000),
         UINT32_C(0x3dd703c0),
         UINT16_C(0x3800),
         UINT8_C(128)},
        {{0.999f, 0.0f, 1.001f, 0.002f},
         UINT32_C(0x3f7fbe77),
         UINT32_C(0x3b033500),
         UINT16_C(0x3bff),
         UINT8_C(255)},
        {{1.02f, 0.03f, 1.01f, 0.02f},
         UINT32_C(0x3f829dd0),
         UINT32_C(0x3c27c880),
         UINT16_C(0x0000),
         UINT8_C(0)},
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
        struct walle_lg_reveal_mask_sample_result result;
        check(walle_lg_reveal_mask_sample_r8(
                  table, WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT, &cases[index].sample, &result),
              "evaluate reveal sample");
        check(float_bits(result.distance) == cases[index].distance_bits, "sample distance bits");
        check(float_bits(result.feather) == cases[index].feather_bits, "sample feather bits");
        check(result.alpha_half_bits == cases[index].alpha_half_bits, "sample binary16 alpha");
        check(result.coverage == cases[index].coverage, "sample R8 coverage");
    }
}

int main(int argc, char** argv)
{
    const char* path  = argc > 1 ? argv[1] : "artifacts/apple-float-intrinsics-r8-30556057571.bin";
    uint8_t*    table = load_table(path);
    test_circle_state();
    test_public_geometry();
    test_fast_sqrt(table);
    test_coverage(table);
    free(table);
    printf("reveal mask model: public state and Apple circle arithmetic exact\n");
    printf("fastSqrtSha256=%s\n", WALLE_LG_REVEAL_FAST_SQRT_SHA256);
    return EXIT_SUCCESS;
}
