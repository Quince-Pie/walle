/* Feasibility probe for a two-dimensional post-guard child setup path.
 *
 * The reveal raster drops every clipped child whose SDF varies along both
 * axes because the packed table is one-dimensional per axis.  This probe
 * builds those children's coefficient planes with the recovered AGX setup
 * arithmetic, evaluates them at the known residual coordinates, and reports
 * the resulting R8 byte so the two-dimensional path can be justified before
 * it is built into the renderer.
 *
 * It includes the raster translation unit directly so the already validated
 * static arithmetic primitives are reused rather than reimplemented. */

#include "../parity/liquid_glass_raster.c"

#include "../parity/liquid_glass_reveal_mask_model.h"

#include <stdio.h>
#include <stdlib.h>

struct general_child
{
    struct walle_lg_vertex vertices[3];
    int32_t                fixed[3][2];
    int64_t                determinant;
    size_t                 anchor;
    uint32_t               selector;
    int                    selector_exponent;
};

/* Signed area of the triangle in 1/256 subpixel units. */
static int64_t triangle_determinant(const int32_t fixed[3][2])
{
    return (int64_t)(fixed[1][0] - fixed[0][0]) * (fixed[2][1] - fixed[0][1])
           - (int64_t)(fixed[1][1] - fixed[0][1]) * (fixed[2][0] - fixed[0][0]);
}

static size_t top_left_anchor(const int32_t fixed[3][2])
{
    size_t anchor = 0;
    for (size_t vertex = 1; vertex < 3; ++vertex) {
        if (fixed[vertex][1] < fixed[anchor][1]
            || (fixed[vertex][1] == fixed[anchor][1] && fixed[vertex][0] < fixed[anchor][0])) {
            anchor = vertex;
        }
    }
    return anchor;
}

static bool general_child_prepare(const struct walle_lg_vertex          vertices[static 3],
                                  const struct walle_lg_raster_calibration* calibration,
                                  struct general_child*                 result)
{
    struct general_child child = {};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        child.vertices[vertex] = vertices[vertex];
        for (size_t axis = 0; axis < 2; ++axis)
            child.fixed[vertex][axis] = subpixel_fixed(vertices[vertex].position[axis]);
    }
    child.determinant = triangle_determinant(child.fixed);
    if (child.determinant == 0)
        return false;
    child.anchor = top_left_anchor(child.fixed);

    /* The P25 selector consumes the magnitude of the determinant. */
    uint64_t determinant
        = child.determinant < 0 ? (uint64_t)(-child.determinant) : (uint64_t)child.determinant;
    if (calibration->p25_ceil_bits == nullptr
        || calibration->p25_selector_bit_count != P25_KEY_UPPER - P25_KEY_LOWER) {
        return false;
    }
    unsigned determinant_exponent = bit_length_u64(determinant) - 1;
    uint64_t key;
    if (determinant_exponent <= 24) {
        key = determinant << (24 - determinant_exponent);
    } else {
        unsigned shift     = determinant_exponent - 24;
        uint64_t quotient  = determinant >> shift;
        uint64_t remainder = determinant - (quotient << shift);
        key = quotient + (remainder >= (UINT64_C(1) << (shift - 1)) ? 1u : 0u);
    }
    child.selector_exponent = -(int)bit_length_u64(determinant - 1) - 8;
    if ((determinant & (determinant - 1)) == 0 || key == P25_KEY_UPPER) {
        child.selector = UINT32_C(1) << 24;
    } else if (key < P25_KEY_LOWER || key >= P25_KEY_UPPER) {
        return false;
    } else {
        uint64_t bit_index = key - P25_KEY_LOWER;
        bool     ceil
            = (((uint32_t)calibration->p25_ceil_bits[bit_index >> 3] >> (bit_index & 7u)) & 1u) != 0;
        uint64_t floor = P25_RECIPROCAL / key;
        child.selector = (uint32_t)(floor + (ceil && P25_RECIPROCAL % key != 0 ? 1u : 0u));
    }
    *result = child;
    return true;
}

/* Edge deltas in 1/256 units, ordered so that term i pairs with vertex i. */
static void child_edges(const struct general_child* child, int32_t edges[2][3])
{
    edges[0][0] = child->fixed[1][1] - child->fixed[2][1];
    edges[0][1] = child->fixed[2][1] - child->fixed[0][1];
    edges[0][2] = child->fixed[0][1] - child->fixed[1][1];
    edges[1][0] = child->fixed[2][0] - child->fixed[1][0];
    edges[1][1] = child->fixed[0][0] - child->fixed[2][0];
    edges[1][2] = child->fixed[1][0] - child->fixed[0][0];
}

static float child_component(const struct general_child* child, size_t vertex, size_t channel)
{
    return channel == 0 ? child->vertices[vertex].sdf[0] : child->vertices[vertex].sdf[1];
}

/* Slope numerator: the two nonanchor displacement-weighted products joined on
 * the measured 27-bit setup lattice. */
static bool child_numerator(const struct general_child* child,
                            size_t                      channel,
                            size_t                      axis,
                            int*                        result_sign,
                            uint64_t*                   result_index,
                            int*                        result_exponent)
{
    int32_t edges[2][3];
    child_edges(child, edges);
    float anchor_value = child_component(child, child->anchor, channel);

    struct dyadic total = {};
    bool          any   = false;
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        if (vertex == child->anchor)
            continue;
        float delta = subtract_f32(child_component(child, vertex, channel), anchor_value);
        float edge  = round_f32((double)edges[axis][vertex] / SUBPIXEL_SCALE);
        if (delta == 0.0f || edge == 0.0f)
            continue;
        uint64_t delta_index, edge_index;
        int      delta_exponent, edge_exponent;
        if (!positive_float_components(float_bits(fabsf(delta)), &delta_index, &delta_exponent)
            || !positive_float_components(float_bits(fabsf(edge)), &edge_index, &edge_exponent)) {
            return false;
        }
        uint64_t product_index;
        int      product_exponent;
        if (!product_stage(delta_index,
                           delta_exponent,
                           edge_index,
                           edge_exponent,
                           27,
                           16,
                           15,
                           &product_index,
                           &product_exponent)) {
            return false;
        }
        int           sign = (signbit(delta) != signbit(edge)) ? -1 : 1;
        struct dyadic term = {
            .numerator = sign * (i128)product_index,
            .exponent  = product_exponent,
        };
        if (!dyadic_add(total, term, &total))
            return false;
        any = true;
    }
    if (!any || total.numerator == 0) {
        *result_sign     = 0;
        *result_index    = 0;
        *result_exponent = 0;
        return true;
    }
    struct dyadic normalized;
    if (!quantize_significand(total, 27, &normalized))
        return false;
    *result_sign     = normalized.numerator < 0 ? -1 : 1;
    *result_index    = (uint64_t)magnitude_i128(normalized.numerator);
    *result_exponent = normalized.exponent;
    return true;
}

static bool child_slope_bits(const struct general_child* child,
                             size_t                      channel,
                             size_t                      axis,
                             uint32_t*                   result)
{
    int      sign;
    uint64_t numerator;
    int      numerator_exponent;
    if (!child_numerator(child, channel, axis, &sign, &numerator, &numerator_exponent))
        return false;
    if (sign == 0) {
        *result = 0;
        return true;
    }
    uint64_t coefficient;
    int      coefficient_exponent;
    if (!product_stage(numerator,
                       numerator_exponent,
                       child->selector,
                       child->selector_exponent,
                       27,
                       19,
                       20,
                       &coefficient,
                       &coefficient_exponent)) {
        return false;
    }
    if (child->determinant < 0)
        sign = -sign;
    double value = ldexp((double)(sign * (int64_t)coefficient), coefficient_exponent);
    *result      = float_bits(round_f32(value));
    return true;
}

/* Tile constant: both signed displacement-weighted middle products joined on
 * the measured 28-bit lattice, one shared reciprocal product, then the
 * anchor. */
static bool child_constant_bits(const struct general_child* child,
                                size_t                      channel,
                                int32_t                     tile_x,
                                int32_t                     tile_y,
                                uint32_t*                   result)
{
    struct dyadic value = dyadic_from_float_bits(
        float_bits(child_component(child, child->anchor, channel)));
    int32_t tiles[2] = {tile_x, tile_y};

    struct dyadic middle_total = {};
    bool          any          = false;
    for (size_t axis = 0; axis < 2; ++axis) {
        int      sign;
        uint64_t numerator;
        int      numerator_exponent;
        if (!child_numerator(child, channel, axis, &sign, &numerator, &numerator_exponent))
            return false;
        int64_t displacement = (int64_t)tiles[axis] * TILE_SIZE * SUBPIXEL_SCALE
                               - child->fixed[child->anchor][axis];
        if (sign == 0 || displacement == 0)
            continue;
        uint64_t distance_index;
        int      distance_exponent;
        float    distance = round_f32((double)llabs(displacement) / SUBPIXEL_SCALE);
        if (!positive_float_components(float_bits(distance), &distance_index, &distance_exponent))
            return false;
        uint64_t middle;
        int      middle_exponent;
        if (!column_product_stage(numerator,
                                  numerator_exponent,
                                  distance_index,
                                  distance_exponent,
                                  &middle,
                                  &middle_exponent)) {
            return false;
        }
        int           term_sign = displacement < 0 ? -sign : sign;
        struct dyadic term      = {
                 .numerator = term_sign * (i128)middle,
                 .exponent  = middle_exponent,
        };
        if (!dyadic_add(middle_total, term, &middle_total))
            return false;
        any = true;
    }
    if (any && middle_total.numerator != 0) {
        struct dyadic joined;
        if (!quantize_significand(middle_total, 28, &joined))
            return false;
        int      joined_sign  = joined.numerator < 0 ? -1 : 1;
        uint64_t joined_index = (uint64_t)magnitude_i128(joined.numerator);
        uint64_t coefficient;
        int      coefficient_exponent;
        if (!product_stage(joined_index,
                           joined.exponent,
                           child->selector,
                           child->selector_exponent,
                           27,
                           20,
                           20,
                           &coefficient,
                           &coefficient_exponent)) {
            return false;
        }
        if (child->determinant < 0)
            joined_sign = -joined_sign;
        struct dyadic term = {
            .numerator = joined_sign * (i128)coefficient,
            .exponent  = coefficient_exponent,
        };
        if (!dyadic_add(value, term, &value))
            return false;
    }
    return quantize_composite_constant(value, result);
}

/* Two-dimensional generalization of the measured 36-bit iterator: the quad's
 * even lane is materialized once, then the partner lanes add a slope. */
static bool child_quad_values(const struct general_child* child,
                              size_t                      channel,
                              int32_t                     x,
                              int32_t                     y,
                              float                       result[static 4])
{
    int32_t tile_x = floor_div_i32(x, TILE_SIZE);
    int32_t tile_y = floor_div_i32(y, TILE_SIZE);
    uint32_t constant_bits;
    if (!child_constant_bits(child, channel, tile_x, tile_y, &constant_bits))
        return false;
    uint32_t slope_x_bits, slope_y_bits;
    if (!child_slope_bits(child, channel, 0, &slope_x_bits)
        || !child_slope_bits(child, channel, 1, &slope_y_bits)) {
        return false;
    }
    struct dyadic constant = dyadic_from_float_bits(constant_bits);
    struct dyadic slope_x  = dyadic_from_float_bits(slope_x_bits);
    struct dyadic slope_y  = dyadic_from_float_bits(slope_y_bits);

    int32_t local_x = x - tile_x * TILE_SIZE;
    int32_t local_y = y - tile_y * TILE_SIZE;
    int64_t even_x  = 2 * (local_x & ~1) + 1;
    int64_t even_y  = 2 * (local_y & ~1) + 1;

    struct dyadic term_x, term_y, exact;
    if (!dyadic_multiply_integer(slope_x, even_x, &term_x)
        || !dyadic_multiply_integer(slope_y, even_y, &term_y)) {
        return false;
    }
    --term_x.exponent;
    --term_y.exponent;
    if (!dyadic_add(constant, term_x, &exact) || !dyadic_add(exact, term_y, &exact))
        return false;

    int step_exponent;
    if (constant.numerator != 0) {
        step_exponent = floor_binary_exponent(constant) - (int)CENTER_PRECISION_BITS + 1;
    } else {
        struct dyadic reference = exact.numerator != 0 ? exact : slope_x;
        if (reference.numerator == 0)
            reference = slope_y;
        if (reference.numerator == 0) {
            for (size_t lane = 0; lane < 4; ++lane)
                result[lane] = 0.0f;
            return true;
        }
        step_exponent = floor_binary_exponent(reference) - (int)CENTER_PRECISION_BITS + 1;
    }
    int64_t index;
    if (!dyadic_floor_ratio_power_two(exact, step_exponent, &index))
        return false;

    struct dyadic base = {.numerator = index, .exponent = step_exponent};
    struct dyadic lanes[4];
    lanes[0] = base;
    if (!dyadic_add(base, slope_x, &lanes[1]) || !dyadic_add(base, slope_y, &lanes[2])
        || !dyadic_add(lanes[1], slope_y, &lanes[3])) {
        return false;
    }
    for (size_t lane = 0; lane < 4; ++lane) {
        uint32_t bits;
        if (!dyadic_toward_zero_float_bits(lanes[lane], &bits))
            return false;
        result[lane] = bits_float(bits);
    }
    return true;
}

/* Pixel-center containment using the same 1/256 fixed-point space. */
static bool child_contains(const struct general_child* child, int32_t x, int32_t y)
{
    int64_t center_x = (int64_t)x * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2;
    int64_t center_y = (int64_t)y * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2;
    int     expected = child->determinant < 0 ? -1 : 1;
    for (size_t edge = 0; edge < 3; ++edge) {
        size_t  next = (edge + 1) % 3;
        int64_t cross
            = (int64_t)(child->fixed[next][0] - child->fixed[edge][0])
                  * (center_y - child->fixed[edge][1])
              - (int64_t)(child->fixed[next][1] - child->fixed[edge][1])
                    * (center_x - child->fixed[edge][0]);
        if (cross == 0)
            continue;
        if ((cross < 0 ? -1 : 1) != expected)
            return false;
    }
    return true;
}

int main(int argc, char** argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s P25_TABLE RESIDUAL_LIST\n", argv[0]);
        return 2;
    }
    FILE* table_file = fopen(argv[1], "rb");
    if (table_file == nullptr)
        return 2;
    static uint8_t p25[1u << 21];
    if (fread(p25, 1, sizeof p25, table_file) != sizeof p25)
        return 2;
    fclose(table_file);
    struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = p25,
        .p25_selector_bit_count = sizeof p25 * 8,
    };

    static uint8_t sqrt_table[4u << 20];
    FILE*          sqrt_file = fopen("parity/apple_fast_sqrt_correction_nibbles.bin", "rb");
    if (sqrt_file == nullptr)
        return 2;
    size_t sqrt_bytes = fread(sqrt_table, 1, sizeof sqrt_table, sqrt_file);
    fclose(sqrt_file);

    FILE* residuals = fopen(argv[2], "r");
    if (residuals == nullptr)
        return 2;

    size_t covered = 0;
    size_t matched = 0;
    size_t total   = 0;
    int    state, x, y, walle_byte, apple_byte;
    while (fscanf(residuals, "%d %d %d %d %d", &state, &x, &y, &walle_byte, &apple_byte) == 5) {
        ++total;
        struct walle_lg_reveal_mask_request request = {
            .target_width   = 2048,
            .target_height  = 2048,
            .center_x       = 512.0,
            .center_y       = 614.4,
            .maximum_radius = 2164.104505809273,
            .progress       = (double)state / 64.0,
        };
        struct walle_lg_reveal_mask_geometry geometry;
        if (!walle_lg_reveal_mask_geometry_construct(&request, &geometry))
            return 1;
        uint32_t                              target[2] = {2048, 2048};
        struct walle_lg_postguard_children    children;
        if (walle_lg_postguard_children_construct(&geometry, target, &children)
            != WALLE_LG_POSTGUARD_OK) {
            return 1;
        }

        bool  found = false;
        float lanes[2][4];
        for (size_t index = 0; index < children.child_count && !found; ++index) {
            struct walle_lg_vertex triangle[3];
            struct walle_lg_vertex completed[6];
            for (size_t vertex = 0; vertex < 3; ++vertex)
                postguard_vertex(&children.children[index].vertices[vertex], &triangle[vertex]);
            if (complete_reveal_quad(triangle, completed))
                continue; /* already representable by the packed path */
            struct general_child child;
            if (!general_child_prepare(triangle, &calibration, &child))
                continue;
            if (!child_contains(&child, x, y))
                continue;
            if (!child_quad_values(&child, 0, x, y, lanes[0])
                || !child_quad_values(&child, 1, x, y, lanes[1])) {
                continue;
            }
            found = true;
        }
        if (!found)
            continue;
        ++covered;

        size_t lane    = (size_t)((x & 1) + 2 * (y & 1));
        size_t lane_hx = (size_t)(((x ^ 1) & 1) + 2 * (y & 1));
        size_t lane_vy = (size_t)((x & 1) + 2 * ((y ^ 1) & 1));

        float center_distance, horizontal_distance, vertical_distance;
        if (!walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table,
                sqrt_bytes,
                fmaf(lanes[1][lane], lanes[1][lane], lanes[0][lane] * lanes[0][lane]),
                &center_distance)
            || !walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table,
                sqrt_bytes,
                fmaf(lanes[1][lane_hx], lanes[1][lane_hx], lanes[0][lane_hx] * lanes[0][lane_hx]),
                &horizontal_distance)
            || !walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table,
                sqrt_bytes,
                fmaf(lanes[1][lane_vy], lanes[1][lane_vy], lanes[0][lane_vy] * lanes[0][lane_vy]),
                &vertical_distance)) {
            continue;
        }
        float feather = fabsf(horizontal_distance - center_distance)
                        + fabsf(vertical_distance - center_distance);
        if (feather < 1.0e-4f)
            feather = 1.0e-4f;
        float alpha = (1.0f - center_distance) / feather + 0.5f;
        alpha       = alpha < 0.0f ? 0.0f : (alpha > 1.0f ? 1.0f : alpha);
        float scaled = alpha * 255.0f;
        unsigned truncated = (unsigned)scaled;
        float    remainder = scaled - (float)truncated;
        if (remainder > 0.5f || (remainder == 0.5f && (truncated & 1u) != 0u))
            ++truncated;
        unsigned coverage = truncated > 255u ? 255u : truncated;

        matched += coverage == (unsigned)apple_byte ? 1u : 0u;
        printf("state %2d (%4d,%4d) walle=%3d apple=%3d general=%3u %s\n",
               state,
               x,
               y,
               walle_byte,
               apple_byte,
               coverage,
               coverage == (unsigned)apple_byte ? "MATCH" : "differ");
    }
    fclose(residuals);
    printf("residuals %zu, covered by a general child %zu, reference byte reproduced %zu\n",
           total,
           covered,
           matched);
    return 0;
}
