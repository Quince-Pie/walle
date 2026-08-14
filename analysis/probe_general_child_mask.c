/* CPU model of the reveal-mask fragment stage, with an optional
 * two-dimensional path for the post-guard children the packed representation
 * currently drops.
 *
 * The model first reproduces the shipped renderer exactly, which validates it,
 * and then reports what the two-dimensional children would change.  It
 * includes the raster translation unit so the already validated static setup
 * arithmetic is reused rather than reimplemented. */

#include "../parity/liquid_glass_raster.c"

#include "../parity/liquid_glass_reveal_mask_model.h"

#include <stdio.h>
#include <stdlib.h>

enum
{
    TARGET_EXTENT = 2048,
    MAX_GENERAL_CHILDREN = 192,
};

struct general_child
{
    struct walle_lg_vertex vertices[3];
    double                 wide_sdf[3][2];
    double                 wide_pos[3][2];
    bool                   has_wide;
    int32_t                fixed[3][2];
    int64_t                determinant;
    size_t                 anchor;
    uint32_t               selector;
    int                    selector_exponent;
    int32_t                visible_bounds[4];
    bool                   from_postguard;
    int                    ordinal;
    int                    source_primitive;
    /* Cached per channel and axis. */
    uint32_t slope_bits[2][2];
    int      numerator_sign[2][2];
    uint64_t numerator_index[2][2];
    int      numerator_exponent[2][2];
};

/* The measured first-product join rounds halfway cases away from zero rather
 * than to even, which is the only place this differs from the packed path. */
static bool quantize_significand_half_up(struct dyadic value,
                                         unsigned      precision_bits,
                                         struct dyadic* result)
{
    if (value.numerator == 0) {
        *result = value;
        return true;
    }
    bool negative = value.numerator < 0;
    u128 magnitude = magnitude_i128(value.numerator);
    unsigned length = bit_length_u128(magnitude);
    if (length > precision_bits) {
        unsigned shift = length - precision_bits;
        u128     half  = (u128)1 << (shift - 1);
        magnitude      = (magnitude + half) >> shift;
        if (bit_length_u128(magnitude) > precision_bits) {
            magnitude >>= 1;
            ++shift;
        }
        value.exponent += (int)shift;
    }
    i128 numerator = (i128)magnitude;
    if (negative)
        numerator = -numerator;
    *result = (struct dyadic){.numerator = numerator, .exponent = value.exponent};
    return true;
}

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
    return child->vertices[vertex].sdf[channel];
}

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
            if (getenv("PROBE_TRACE_PREP")) fprintf(stderr, "prep: components fail delta=%08x edge=%08x\n", float_bits(delta), float_bits(edge));
            return false;
        }
        uint64_t product_index;
        int      product_exponent;
        if (!general_product_stage(delta_index,
                                   delta_exponent,
                                   edge_index,
                                   edge_exponent,
                                   27,
                                   16,
                                   15,
                                   &product_index,
                                   &product_exponent)) {
            if (getenv("PROBE_TRACE_PREP")) fprintf(stderr, "prep: first product fail\n");
            return false;
        }
        int           sign = (signbit(delta) != signbit(edge)) ? -1 : 1;
        struct dyadic term = {
            .numerator = sign * (i128)product_index,
            .exponent  = product_exponent,
        };
        if (!dyadic_add(total, term, &total))
            return false;
    }
    if (total.numerator == 0) {
        *result_sign     = 0;
        *result_index    = 0;
        *result_exponent = 0;
        return true;
    }
    struct dyadic normalized;
    static int join_mode = -1;
    if (join_mode < 0) {
        const char* env = getenv("PROBE_FIRST_JOIN");
        join_mode = env != nullptr ? atoi(env) : 0;
    }
    /* Default: the measured 28-bit RNE numerator (word-sweep/join-isolation
     * captures); PROBE_FIRST_JOIN=1 restores the historical 27-bit half-up
     * for comparison. */
    if (join_mode == 1 ? !quantize_significand_half_up(total, 27, &normalized)
                       : !quantize_significand(total, 28, &normalized)) {
        if (getenv("PROBE_TRACE_PREP")) fprintf(stderr, "prep: join quantize fail\n");
        return false;
    }
    *result_sign     = normalized.numerator < 0 ? -1 : 1;
    *result_index    = (uint64_t)magnitude_i128(normalized.numerator);
    *result_exponent = normalized.exponent;
    return true;
}

static int result_ordinal_hint = -1;
int g_current_state = -1;

static bool child_prepare(const struct walle_lg_vertex              vertices[static 3],
                          const struct walle_lg_raster_calibration* calibration,
                          struct general_child*                     result)
{
    struct general_child child = {};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        child.vertices[vertex] = vertices[vertex];
        for (size_t axis = 0; axis < 2; ++axis)
            child.fixed[vertex][axis] = subpixel_fixed(vertices[vertex].position[axis]);
        for (size_t ch = 0; ch < 2; ++ch) {
            child.wide_sdf[vertex][ch] = vertices[vertex].sdf[ch];
            child.wide_pos[vertex][ch] = vertices[vertex].position[ch];
        }
    }
    child.determinant = triangle_determinant(child.fixed);
    if (child.determinant == 0)
        return false;
    child.anchor = top_left_anchor(child.fixed);

    uint64_t determinant
        = child.determinant < 0 ? (uint64_t)(-child.determinant) : (uint64_t)child.determinant;
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
        if (getenv("PROBE_TRACE_PREP")) fprintf(stderr, "prep: selector key %llu out of range\n", (unsigned long long)key);
        return false;
    } else {
        uint64_t bit_index = key - P25_KEY_LOWER;
        bool     ceil
            = (((uint32_t)calibration->p25_ceil_bits[bit_index >> 3] >> (bit_index & 7u)) & 1u) != 0;
        uint64_t floor = P25_RECIPROCAL / key;
        child.selector = (uint32_t)(floor + (ceil && P25_RECIPROCAL % key != 0 ? 1u : 0u));
    }

    for (size_t channel = 0; channel < 2; ++channel) {
        for (size_t axis = 0; axis < 2; ++axis) {
            int      sign;
            uint64_t numerator;
            int      exponent;
            if (!child_numerator(&child, channel, axis, &sign, &numerator, &exponent)) {
                if (getenv("PROBE_TRACE_PREP")) fprintf(stderr, "prep: numerator ch%zu axis%zu fail\n", channel, axis);
                return false;
            }
            child.numerator_sign[channel][axis]     = sign;
            child.numerator_index[channel][axis]    = numerator;
            child.numerator_exponent[channel][axis] = exponent;
            if (sign == 0) {
                child.slope_bits[channel][axis] = 0;
                continue;
            }
            uint64_t coefficient;
            int      coefficient_exponent;
            if (!selector_product_stage(numerator,
                                        exponent,
                                        child.selector,
                                        child.selector_exponent,
                                        &coefficient,
                                        &coefficient_exponent)) {
                if (getenv("PROBE_TRACE_PREP")) fprintf(stderr, "prep: slope product ch%zu axis%zu nidx=%llu ne=%d fail\n", channel, axis, (unsigned long long)numerator, exponent);
                return false;
            }
            int slope_sign = child.determinant < 0 ? -sign : sign;
            child.slope_bits[channel][axis]
                = float_bits(round_f32(ldexp((double)(slope_sign * (int64_t)coefficient),
                                             coefficient_exponent)));
        }
    }

    /* AB oracle: override slope numerators with values inverted from
     * MEASURED production words (architecture validation only - the
     * residue formula replaces this once closed). */
    {
        static int oracle_loaded = -1;
        static struct { int st, od, ch, ax; uint32_t word; } oracle[512];
        static int oracle_count = 0;
        if (oracle_loaded < 0) {
            oracle_loaded = 0;
            const char* path = getenv("PROBE_AB_ORACLE");
            if (path != nullptr) {
                FILE* f = fopen(path, "r");
                if (f != nullptr) {
                    while (oracle_count < 512
                           && fscanf(f, "%d %d %d %d %x",
                                     &oracle[oracle_count].st,
                                     &oracle[oracle_count].od,
                                     &oracle[oracle_count].ch,
                                     &oracle[oracle_count].ax,
                                     &oracle[oracle_count].word) == 5) {
                        ++oracle_count;
                    }
                    fclose(f);
                    oracle_loaded = 1;
                }
            }
        }
        extern int g_current_state;
        int cur_state = g_current_state;
        if (oracle_loaded == 1 && cur_state >= 0) {
            for (int i = 0; i < oracle_count; ++i) {
                if (oracle[i].st != cur_state || oracle[i].od != result_ordinal_hint)
                    continue;
                size_t ch = (size_t)oracle[i].ch, ax = (size_t)oracle[i].ax;
                uint32_t w = oracle[i].word;
                if ((w & 0x7fffffffu) == 0) {
                    child.numerator_sign[ch][ax] = 0;
                    child.numerator_index[ch][ax] = 0;
                    child.numerator_exponent[ch][ax] = 0;
                    child.slope_bits[ch][ax] = w;
                    continue;
                }
                /* invert: numerator = word / (det_sign * sel * 2^se) */
                uint64_t widx; int wexp;
                if (!positive_float_components(w & 0x7fffffffu, &widx, &wexp))
                    continue;
                /* numerator ~= widx * 2^wexp / (sel * 2^se): compute as
                 * (widx << 40) / sel with exponent bookkeeping, RNE-28. */
                u128 scaled = (u128)widx << 40;
                uint64_t q = (uint64_t)(scaled / child.selector);
                int qe = wexp - 40 - child.selector_exponent;
                struct dyadic nd = {.numerator = (i128)q, .exponent = qe};
                struct dyadic n28;
                if (!quantize_significand(nd, 28, &n28))
                    continue;
                int sgn = (w >> 31) != 0 ? -1 : 1;
                if (child.determinant < 0) sgn = -sgn;
                child.numerator_sign[ch][ax] = sgn;
                child.numerator_index[ch][ax] = (uint64_t)magnitude_i128(n28.numerator);
                child.numerator_exponent[ch][ax] = n28.exponent;
                child.slope_bits[ch][ax] = w;
            }
        }
    }

    int32_t low[2]  = {INT32_MAX, INT32_MAX};
    int32_t high[2] = {INT32_MIN, INT32_MIN};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis) {
            if (child.fixed[vertex][axis] < low[axis])
                low[axis] = child.fixed[vertex][axis];
            if (child.fixed[vertex][axis] > high[axis])
                high[axis] = child.fixed[vertex][axis];
        }
    }
    struct raster_case bounds_case = {
        .origin_x_fixed = low[0],
        .origin_y_fixed = low[1],
        .width_fixed    = high[0] - low[0],
        .height_fixed   = high[1] - low[1],
    };
    if (!visible_bounds(&bounds_case, child.visible_bounds)) {
        if (getenv("PROBE_TRACE_PREP")) fprintf(stderr, "prep: visible bounds fail\n");
        return false;
    }
    *result = child;
    return true;
}

/* The exact anchor + selector-chain sum before any output narrowing. */
static bool child_constant_exact(const struct general_child* child,
                                 size_t                      channel,
                                 int32_t                     tile_x,
                                 int32_t                     tile_y,
                                 struct dyadic*              result);

static bool dyadic_from_double(double v, struct dyadic* out)
{
    if (v == 0.0) {
        *out = (struct dyadic){};
        return true;
    }
    int exponent;
    double mantissa = frexp(v, &exponent);
    double scaled = ldexp(mantissa, 53);
    *out = (struct dyadic){.numerator = (i128)(int64_t)scaled,
                           .exponent  = exponent - 53};
    return true;
}

static bool child_constant_exact(const struct general_child* child,
                                 size_t                      channel,
                                 int32_t                     tile_x,
                                 int32_t                     tile_y,
                                 struct dyadic*              result)
{
    static int wide_anchor = -1;
    if (wide_anchor < 0) {
        const char* env = getenv("PROBE_C_WIDE_ANCHOR");
        wide_anchor = env != nullptr ? atoi(env) : 0;
    }
    struct dyadic value;
    if (wide_anchor && child->has_wide) {
        if (!dyadic_from_double(child->wide_sdf[child->anchor][channel], &value))
            return false;
    } else {
        value = dyadic_from_float_bits(
            float_bits(child_component(child, child->anchor, channel)));
    }
    int32_t tiles[2] = {tile_x, tile_y};

    struct dyadic middle_total = {};
    static int tiny_mode = -1;
    static int tiny_shift = 26;
    if (tiny_mode < 0) {
        const char* env = getenv("PROBE_TINY_RESIDUE");
        tiny_mode = env != nullptr ? atoi(env) : 0;
        const char* envs = getenv("PROBE_TINY_SHIFT");
        if (envs != nullptr) tiny_shift = atoi(envs);
    }
    for (size_t axis = 0; axis < 2; ++axis) {
        int      sign     = child->numerator_sign[channel][axis];
        uint64_t numerator = child->numerator_index[channel][axis];
        int      exponent  = child->numerator_exponent[channel][axis];
        if (tiny_mode != 0 && sign == 0
            && child->numerator_sign[channel][1 - axis] != 0) {
            /* Exact cancellation: the hardware leaves the OTHER axis's
             * coefficient bleeding in at a fixed column offset (basis
             * capture o6 family: same mantissa, 2^-26).  Model the
             * residue as other-axis numerator scaled by 2^-shift. */
            sign      = tiny_mode > 0
                            ? child->numerator_sign[channel][1 - axis]
                            : -child->numerator_sign[channel][1 - axis];
            numerator = child->numerator_index[channel][1 - axis];
            exponent  = child->numerator_exponent[channel][1 - axis]
                        - tiny_shift;
        }
        static int wide_disp = -1;
        if (wide_disp < 0) {
            const char* env = getenv("PROBE_C_WIDE_DISP");
            wide_disp = env != nullptr ? atoi(env) : 0;
        }
        double displacement_wide
            = wide_disp && child->has_wide
                  ? (double)tiles[axis] * TILE_SIZE * SUBPIXEL_SCALE
                        - child->wide_pos[child->anchor][axis] * SUBPIXEL_SCALE
                  : (double)((int64_t)tiles[axis] * TILE_SIZE * SUBPIXEL_SCALE
                             - child->fixed[child->anchor][axis]);
        int64_t displacement = (int64_t)llround(displacement_wide);
        if (sign == 0 || displacement_wide == 0.0)
            continue;
        uint64_t distance_index;
        int      distance_exponent;
        float    distance = round_f32(fabs(displacement_wide) / SUBPIXEL_SCALE);
        if (!positive_float_components(float_bits(distance), &distance_index, &distance_exponent))
            return false;
        uint64_t middle;
        int      middle_exponent;
        if (!general_column_product_stage(numerator,
                                          exponent,
                                          distance_index,
                                          distance_exponent,
                                          &middle,
                                          &middle_exponent)) {
            return false;
        }
        struct dyadic term = {
            .numerator = (displacement < 0 ? -sign : sign) * (i128)middle,
            .exponent  = middle_exponent,
        };
        if (getenv("PROBE_TRACE_C") != nullptr) {
            fprintf(stderr,
                    "TRC o%d ch%zu tile(%d,%d) axis%zu num=%llu ne=%d "
                    "disp=%lld dist=%08x mid=%llu me=%d\n",
                    child->ordinal, channel, tile_x, tile_y, axis,
                    (unsigned long long)numerator, exponent,
                    (long long)displacement, float_bits(distance),
                    (unsigned long long)middle, middle_exponent);
        }
        if (!dyadic_add(middle_total, term, &middle_total))
            return false;
    }
    if (middle_total.numerator != 0) {
        struct dyadic joined;
        static int mid_join = -1;
        if (mid_join < 0) {
            const char* env = getenv("PROBE_MID_JOIN");
            mid_join = env != nullptr ? atoi(env) : 0;
        }
        if (mid_join == 1
                ? !quantize_significand_half_up(middle_total, 28, &joined)
                : !quantize_significand(middle_total, 28, &joined))
            return false;
        int      joined_sign  = joined.numerator < 0 ? -1 : 1;
        uint64_t joined_index = (uint64_t)magnitude_i128(joined.numerator);
        uint64_t coefficient;
        int      coefficient_exponent;
        if (!constant_selector_product_stage(joined_index,
                                             joined.exponent,
                                             child->selector,
                                             child->selector_exponent,
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
        if (getenv("PROBE_TRACE_C") != nullptr) {
            fprintf(stderr,
                    "TRC o%d ch%zu tile(%d,%d) join=%lld je=%d "
                    "sel=%u sele=%d coef=%llu ce=%d anchor=%08x\n",
                    child->ordinal, channel, tile_x, tile_y,
                    (long long)joined.numerator, joined.exponent,
                    child->selector, child->selector_exponent,
                    (unsigned long long)coefficient, coefficient_exponent,
                    float_bits(child_component(child, child->anchor, channel)));
        }
        if (!dyadic_add(value, term, &value))
            return false;
    }
    *result = value;
    return true;
}


/* --- Hardware guard-band clip (viewport +-512, NDC +-1.5): measured law:
 * SH polygon clip; cut-vertex channel values = RTZ-24 of the wide lerp,
 * rounded independently per channel; positions lerped wide and snapped
 * half-up downstream (t-readback/apex-x captures, TASK.md later 9-12). --- */

static float clip_rtz24(double value)
{
    if (value == 0.0)
        return 0.0f;
    int    exponent;
    double mantissa = frexp(fabs(value), &exponent);
    double truncated = floor(ldexp(mantissa, 24));
    double out = ldexp(truncated, exponent - 24);
    return (float)(value < 0 ? -out : out);
}

struct clip_vertex
{
    double position[2];
    double sdf[2];       /* value fed to the setup (RTZ24 by default) */
    double wide_sdf[2];  /* unrounded lerp for the wide-anchor C chain */
    bool   lerped;
};

static size_t clip_polygon_guard(struct clip_vertex* poly, size_t count)
{
    static const struct
    {
        int    axis;
        double boundary;
        int    sign;
    } planes[4] = {
        {0, -512.0, 1}, {1, -512.0, 1}, {0, 2560.0, -1}, {1, 2560.0, -1}};
    struct clip_vertex buffer[16];
    for (size_t plane = 0; plane < 4 && count > 0; ++plane) {
        int    axis = planes[plane].axis;
        double c    = planes[plane].boundary;
        int    sign = planes[plane].sign;
        size_t out  = 0;
        for (size_t i = 0; i < count; ++i) {
            struct clip_vertex a = poly[i];
            struct clip_vertex b = poly[(i + 1) % count];
            bool ain = (a.position[axis] - c) * sign >= 0.0;
            bool bin = (b.position[axis] - c) * sign >= 0.0;
            if (ain && out < 16)
                buffer[out++] = a;
            if (ain != bin && out < 16) {
                double t = (c - a.position[axis])
                           / (b.position[axis] - a.position[axis]);
                struct clip_vertex n;
                n.position[axis]     = c;
                n.position[1 - axis] = a.position[1 - axis]
                                       + t * (b.position[1 - axis]
                                              - a.position[1 - axis]);
                static int clip_vq = -1;
                if (clip_vq < 0) {
                    const char* env = getenv("PROBE_CLIP_VQ");
                    clip_vq = env != nullptr ? atoi(env) : 1;
                }
                for (size_t ch = 0; ch < 2; ++ch) {
                    double lerped = a.wide_sdf[ch]
                                    + t * (b.wide_sdf[ch] - a.wide_sdf[ch]);
                    n.wide_sdf[ch] = lerped;
                    n.sdf[ch] = clip_vq ? clip_rtz24(lerped) : lerped;
                }
                n.lerped = true;
                buffer[out++] = n;
            }
        }
        memcpy(poly, buffer, out * sizeof *poly);
        count = out;
    }
    return count;
}


static bool child_constant_bits(const struct general_child* child,
                                size_t                      channel,
                                int32_t                     tile_x,
                                int32_t                     tile_y,
                                uint32_t*                   result)
{
    struct dyadic value;
    if (!child_constant_exact(child, channel, tile_x, tile_y, &value))
        return false;
    static int c_mode = -1;
    if (c_mode < 0) {
        const char* env = getenv("PROBE_C_MODE");
        c_mode = env != nullptr ? atoi(env) : 0;
    }
    if (c_mode == 1) {
        /* 28-bit nearest, then toward zero to binary32. */
        struct dyadic internal;
        if (!quantize_significand(value, 28, &internal))
            return false;
        return dyadic_toward_zero_float_bits(internal, result);
    }
    if (c_mode == 2)
        return dyadic_toward_zero_float_bits(value, result);
    if (c_mode == 3) {
        struct dyadic internal;
        if (!quantize_significand_half_up(value, 28, &internal))
            return false;
        struct dyadic narrow;
        if (!quantize_significand_half_up(internal, 24, &narrow))
            return false;
        return dyadic_float_bits(narrow, result);
    }
    if (c_mode == 4) {
        struct dyadic internal;
        if (!quantize_significand_half_up(value, 28, &internal))
            return false;
        return dyadic_float_bits(internal, result);
    }
    if (c_mode == 5) {
        struct dyadic internal;
        if (!quantize_significand(value, 28, &internal))
            return false;
        struct dyadic narrow;
        if (!quantize_significand_half_up(internal, 24, &narrow))
            return false;
        return dyadic_float_bits(narrow, result);
    }
    return quantize_composite_constant(value, result);
}

/* Two-dimensional generalization of the measured 36-bit iterator. */
static bool child_lane_values(const struct general_child* child,
                              size_t                      channel,
                              int32_t                     x,
                              int32_t                     y,
                              float                       result[static 4])
{
    int32_t  tile_x = floor_div_i32(x, TILE_SIZE);
    int32_t  tile_y = floor_div_i32(y, TILE_SIZE);
    uint32_t constant_bits;
    if (!child_constant_bits(child, channel, tile_x, tile_y, &constant_bits))
        return false;
    struct dyadic constant = dyadic_from_float_bits(constant_bits);
    struct dyadic slope_x  = dyadic_from_float_bits(child->slope_bits[channel][0]);
    struct dyadic slope_y  = dyadic_from_float_bits(child->slope_bits[channel][1]);

    int32_t local_x = x - tile_x * TILE_SIZE;
    int32_t local_y = y - tile_y * TILE_SIZE;
    struct dyadic term_x, term_y, exact;
    if (!dyadic_multiply_integer(slope_x, (int64_t)(2 * (local_x & ~1) + 1), &term_x)
        || !dyadic_multiply_integer(slope_y, (int64_t)(2 * (local_y & ~1) + 1), &term_y)) {
        return false;
    }
    --term_x.exponent;
    --term_y.exponent;
    if (!dyadic_add(constant, term_x, &exact) || !dyadic_add(exact, term_y, &exact))
        return false;

    struct dyadic reference = constant.numerator != 0 ? constant : exact;
    if (reference.numerator == 0)
        reference = slope_x.numerator != 0 ? slope_x : slope_y;
    if (reference.numerator == 0) {
        for (size_t lane = 0; lane < 4; ++lane)
            result[lane] = 0.0f;
        return true;
    }
    int     step_exponent = floor_binary_exponent(reference) - (int)CENTER_PRECISION_BITS + 1;
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

static bool child_contains(const struct general_child* child, int32_t x, int32_t y)
{
    if (x < child->visible_bounds[0] || y < child->visible_bounds[1]
        || x >= child->visible_bounds[2] || y >= child->visible_bounds[3]) {
        return false;
    }
    int64_t center_x = (int64_t)x * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2;
    int64_t center_y = (int64_t)y * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2;
    int     expected = child->determinant < 0 ? -1 : 1;
    for (size_t edge = 0; edge < 3; ++edge) {
        size_t  next = (edge + 1) % 3;
        int64_t edge_x = child->fixed[next][0] - child->fixed[edge][0];
        int64_t edge_y = child->fixed[next][1] - child->fixed[edge][1];
        int64_t cross
            = edge_x * (center_y - child->fixed[edge][1])
              - edge_y * (center_x - child->fixed[edge][0]);
        if (cross == 0) {
            /* Top-left fill rule in the winding of this triangle. */
            int64_t ox = expected < 0 ? -edge_x : edge_x;
            int64_t oy = expected < 0 ? -edge_y : edge_y;
            bool top  = oy == 0 && ox < 0;
            bool left = oy > 0;
            if (!(top || left))
                return false;
            continue;
        }
        if ((cross < 0 ? -1 : 1) != expected)
            return false;
    }
    return true;
}

/* binary32 to binary16 round-to-nearest-even and back, matching the shader. */
static float half_round_trip(float value)
{
    uint32_t bits         = float_bits(value);
    uint32_t sign         = (bits >> 16u) & 0x8000u;
    uint32_t exponent     = (bits >> 23u) & 0xffu;
    uint32_t significand  = bits & 0x7fffffu;
    uint32_t half;
    if (exponent == 0xffu) {
        half = sign | (significand == 0u ? 0x7c00u : 0x7e00u);
    } else if (exponent == 0u) {
        half = sign;
    } else {
        int unbiased = (int)exponent - 127;
        int half_exponent = unbiased + 15;
        if (half_exponent >= 31) {
            half = sign | 0x7c00u;
        } else if (half_exponent <= 0) {
            if (unbiased < -25) {
                half = sign;
            } else {
                unsigned shift = (unsigned)(-unbiased - 1);
                uint32_t source = significand | 0x800000u;
                uint32_t rounded = source >> shift;
                uint32_t remainder = source & ((1u << shift) - 1u);
                uint32_t halfway = 1u << (shift - 1u);
                if (remainder > halfway || (remainder == halfway && (rounded & 1u) != 0u))
                    ++rounded;
                half = sign | (rounded < 0x400u ? rounded : 0x400u);
            }
        } else {
            uint32_t rounded   = significand >> 13u;
            uint32_t remainder = significand & 0x1fffu;
            if (remainder > 0x1000u || (remainder == 0x1000u && (rounded & 1u) != 0u))
                ++rounded;
            if (rounded == 0x400u) {
                rounded = 0u;
                ++half_exponent;
            }
            half = half_exponent >= 31 ? (sign | 0x7c00u)
                                       : (sign | ((uint32_t)half_exponent << 10u) | rounded);
        }
    }

    uint32_t out_sign        = (half & 0x8000u) << 16u;
    uint32_t half_exponent   = (half >> 10u) & 0x1fu;
    uint32_t half_significand = half & 0x3ffu;
    uint32_t result;
    if (half_exponent == 0u) {
        if (half_significand == 0u) {
            result = out_sign;
        } else {
            int shifted = -14;
            while ((half_significand & 0x400u) == 0u) {
                half_significand <<= 1u;
                --shifted;
            }
            half_significand &= 0x3ffu;
            result = out_sign | ((uint32_t)(shifted + 127) << 23u) | (half_significand << 13u);
        }
    } else if (half_exponent == 0x1fu) {
        result = out_sign | 0x7f800000u | (half_significand << 13u);
    } else {
        result = out_sign | ((half_exponent + 112u) << 23u) | (half_significand << 13u);
    }
    return bits_float(result);
}

static bool evaluate_byte(const uint8_t* sqrt_table,
                          size_t         sqrt_bytes,
                          const float    center[static 2],
                          const float    horizontal[static 2],
                          const float    vertical[static 2],
                          unsigned*      result);

/* ---- packed-owner evaluation, mirroring the fragment shader ---- */

struct wide_owner
{
    struct runtime_quad quad;
    uint32_t            selector;
    int                 selector_exponent;
    bool                valid;
};

struct scene
{
    struct walle_lg_reveal_raster raster;
    struct general_child          general[MAX_GENERAL_CHILDREN];
    size_t                        general_count;
    bool                          use_general;
    struct wide_owner             wide[WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT];
    bool                          use_wide;
    /* Final-owner (slot, primitive) -> drawn-triangle ordinal in the
     * general list; -1 when that owner has no general setup.  The
     * fragment's rasterizer triple always belongs to the final drawn
     * primitive covering the sample, so this map IS the LDCF semantics. */
    int slot_ordinal[WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT][2];
};

/* Register one drawn triangle as general children, clipping at the guard
 * band when a vertex lies strictly beyond it. */
static void register_general_triangle(struct scene*                             scene,
                                      const struct walle_lg_raster_calibration* calibration,
                                      const struct walle_lg_vertex              triangle[static 3],
                                      int                                       ordinal,
                                      int                                       source,
                                      bool                                      from_postguard)
{
    bool beyond = false;
    for (size_t v = 0; v < 3 && !beyond; ++v) {
        for (size_t axis = 0; axis < 2; ++axis) {
            if (triangle[v].position[axis] < -512.0f
                || triangle[v].position[axis] > 2560.0f) {
                beyond = true;
                break;
            }
        }
    }
    if (!beyond) {
        if (scene->general_count >= MAX_GENERAL_CHILDREN)
            return;
        result_ordinal_hint = ordinal;
        if (child_prepare(triangle, calibration,
                          &scene->general[scene->general_count])) {
            scene->general[scene->general_count].ordinal          = ordinal;
            scene->general[scene->general_count].source_primitive = source;
            scene->general[scene->general_count].from_postguard   = from_postguard;
            ++scene->general_count;
        }
        return;
    }
    struct clip_vertex poly[16];
    for (size_t v = 0; v < 3; ++v) {
        poly[v].position[0] = triangle[v].position[0];
        poly[v].position[1] = triangle[v].position[1];
        poly[v].sdf[0]      = triangle[v].sdf[0];
        poly[v].sdf[1]      = triangle[v].sdf[1];
        poly[v].wide_sdf[0] = triangle[v].sdf[0];
        poly[v].wide_sdf[1] = triangle[v].sdf[1];
        poly[v].lerped      = false;
    }
    size_t count = clip_polygon_guard(poly, 3);
    for (size_t fan = 1; fan + 1 < count; ++fan) {
        size_t tri_index[3] = {0, fan, fan + 1};
        struct walle_lg_vertex sub[3];
        for (size_t v = 0; v < 3; ++v) {
            const struct clip_vertex* cv = &poly[tri_index[v]];
            sub[v].position[0] = (float)cv->position[0];
            sub[v].position[1] = (float)cv->position[1];
            sub[v].sdf[0]      = (float)cv->sdf[0];
            sub[v].sdf[1]      = (float)cv->sdf[1];
        }
        if (!reveal_vertices_valid(sub, 3))
            continue;
        if (reveal_triangle_target_status(sub, TARGET_EXTENT, TARGET_EXTENT)
            != PREPARED_REVEAL_OWNER_READY) {
            continue;
        }
        if (scene->general_count >= MAX_GENERAL_CHILDREN)
            return;
        result_ordinal_hint = ordinal;
        if (child_prepare(sub, calibration,
                          &scene->general[scene->general_count])) {
            struct general_child* gc = &scene->general[scene->general_count];
            gc->ordinal          = ordinal;
            gc->source_primitive = source;
            gc->from_postguard   = from_postguard;
            for (size_t v = 0; v < 3; ++v) {
                const struct clip_vertex* cv = &poly[tri_index[v]];
                gc->wide_sdf[v][0] = cv->sdf[0];
                gc->wide_sdf[v][1] = cv->sdf[1];
                gc->wide_pos[v][0] = cv->position[0];
                gc->wide_pos[v][1] = cv->position[1];
            }
            gc->has_wide = true;
            ++scene->general_count;
        }
    }
}


/* Wide slope: the p27 reciprocal product before binary32 rounding. */
static bool wide_slope(const struct wide_owner* owner,
                       size_t                   channel,
                       struct dyadic*           result)
{
    int      sign;
    uint64_t numerator;
    int      numerator_exponent;
    if (!first_stage_numerator(&owner->quad, channel, &sign, &numerator,
                               &numerator_exponent)) {
        return false;
    }
    if (sign == 0) {
        *result = (struct dyadic){};
        return true;
    }
    uint64_t coefficient;
    int      coefficient_exponent;
    if (!reciprocal_stage(&owner->quad,
                          owner->selector,
                          owner->selector_exponent,
                          numerator,
                          numerator_exponent,
                          &coefficient,
                          &coefficient_exponent)) {
        return false;
    }
    static int wide_slope_enabled = -1;
    if (wide_slope_enabled < 0) {
        const char* env    = getenv("PROBE_WIDE_SLOPE");
        wide_slope_enabled = env != nullptr ? atoi(env) : 1;
    }
    if (!wide_slope_enabled) {
        double value = ldexp((double)(sign * (int64_t)coefficient),
                             coefficient_exponent);
        *result = dyadic_from_float_bits(float_bits(round_f32(value)));
        return true;
    }
    *result = (struct dyadic){
        .numerator = sign * (i128)coefficient,
        .exponent  = coefficient_exponent,
    };
    return true;
}

/* Anchor-side override for experiments: -1 keeps the packed rule. */
static int g_anchor_override = -1;

/* Wide constant: anchor plus middle term at 28 bits, before binary32. */
static bool wide_constant(const struct wide_owner* owner,
                          size_t                   channel,
                          size_t                   primitive,
                          int32_t                  tile,
                          struct dyadic*           result)
{
    const struct endpoint* endpoint = &owner->quad.endpoint[channel];
    uint8_t                axis     = owner->quad.channel_axis[channel];
    bool                   anchor_high
        = axis == 0 && primitive == 0 && !owner->quad.ascending_diagonal;
    if (g_anchor_override >= 0)
        anchor_high = (g_anchor_override >> channel) & 1;
    uint32_t anchor_bits = anchor_high ? endpoint->high_bits : endpoint->low_bits;
    int32_t  anchor_fixed;
    if (axis == 0) {
        anchor_fixed = owner->quad.raster.origin_x_fixed
                       + (anchor_high ? owner->quad.raster.width_fixed : 0);
    } else {
        anchor_fixed = owner->quad.raster.origin_y_fixed
                       + (anchor_high ? owner->quad.raster.height_fixed : 0);
    }
    int64_t displacement64
        = (int64_t)tile * TILE_SIZE * SUBPIXEL_SCALE - anchor_fixed;
    if (displacement64 < INT32_MIN || displacement64 > INT32_MAX)
        return false;
    int32_t       displacement = (int32_t)displacement64;
    struct dyadic value        = dyadic_from_float_bits(anchor_bits);

    int      sign;
    uint64_t numerator;
    int      numerator_exponent;
    if (!first_stage_numerator(&owner->quad, channel, &sign, &numerator,
                               &numerator_exponent)) {
        return false;
    }
    if (sign != 0 && displacement != 0) {
        uint64_t distance_index;
        int      distance_exponent;
        float    distance = round_f32((double)llabs(displacement) / SUBPIXEL_SCALE);
        if (!positive_float_components(float_bits(distance), &distance_index,
                                       &distance_exponent)) {
            return false;
        }
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
        uint64_t coefficient;
        int      coefficient_exponent;
        if (!reciprocal_stage(&owner->quad,
                              owner->selector,
                              owner->selector_exponent,
                              middle,
                              middle_exponent,
                              &coefficient,
                              &coefficient_exponent)) {
            return false;
        }
        struct dyadic term = {
            .numerator
            = sign * (displacement < 0 ? -(i128)coefficient : (i128)coefficient),
            .exponent = coefficient_exponent,
        };
        if (!dyadic_add(value, term, &value))
            return false;
    }
    if (value.numerator == 0) {
        *result = value;
        return true;
    }
    static int precision = -1;
    if (precision < 0) {
        const char* env = getenv("PROBE_WIDE_C_BITS");
        precision = env != nullptr ? atoi(env) : 28;
    }
    if (precision == 0) {
        *result = value;
        return true;
    }
    return quantize_significand(value, (unsigned)precision, result);
}

/* Wide center evaluation: 36-bit iterator on the unrounded plane. */
static bool wide_axis_value(const struct wide_owner* owner,
                            size_t                   channel,
                            size_t                   primitive,
                            int32_t                  coordinate,
                            uint32_t*                result)
{
    struct dyadic slope;
    if (!wide_slope(owner, channel, &slope))
        return false;
    int32_t       tile = floor_div_i32(coordinate, TILE_SIZE);
    struct dyadic constant;
    if (!wide_constant(owner, channel, primitive, tile, &constant))
        return false;
    int step_exponent;
    if (constant.numerator != 0) {
        step_exponent
            = floor_binary_exponent(constant) - (int)CENTER_PRECISION_BITS + 1;
    } else if (slope.numerator != 0) {
        struct dyadic reference = slope;
        uint8_t axis = owner->quad.channel_axis[channel];
        int32_t extent = axis == 0 ? owner->quad.raster.width_fixed
                                   : owner->quad.raster.height_fixed;
        (void)extent;
        step_exponent
            = floor_binary_exponent(reference) - (int)CENTER_PRECISION_BITS + 1;
    } else {
        *result = 0;
        return true;
    }
    int32_t local_pixel = coordinate - tile * TILE_SIZE;
    static int affine = -1;
    if (affine < 0) {
        const char* env = getenv("PROBE_AFFINE");
        affine = env != nullptr ? atoi(env) : 0;
    }
    if (affine) {
        /* Per-pixel affine evaluation: toward_zero_f32(C + B*(l + 1/2)). */
        struct dyadic term;
        if (!dyadic_multiply_integer(slope, (int64_t)(2 * local_pixel + 1),
                                     &term)) {
            return false;
        }
        --term.exponent;
        struct dyadic exact;
        if (!dyadic_add(constant, term, &exact))
            return false;
        return dyadic_toward_zero_float_bits(exact, result);
    }
    uint32_t pair[2];
    if (!center_pair_bits(local_pixel, slope, constant, step_exponent, pair))
        return false;
    *result = pair[(uint32_t)local_pixel & 1u];
    return true;
}

static bool wide_owner_coordinates(const struct scene* scene,
                                   int32_t             x,
                                   int32_t             y,
                                   size_t              slot,
                                   int                 primitive,
                                   float               result[static 2])
{
    const struct wide_owner* owner = &scene->wide[slot];
    if (!owner->valid)
        return false;
    uint32_t channel_x, channel_y;
    /* channel 0 varies along its assigned axis; feed the matching pixel
     * coordinate exactly as axis_values does. */
    int32_t coordinate0
        = owner->quad.channel_axis[0] == 0 ? x : y;
    int32_t coordinate1
        = owner->quad.channel_axis[1] == 0 ? x : y;
    if (!wide_axis_value(owner, 0, (size_t)primitive, coordinate0, &channel_x)
        || !wide_axis_value(owner, 1, (size_t)primitive, coordinate1,
                            &channel_y)) {
        return false;
    }
    result[0] = bits_float(channel_x);
    result[1] = bits_float(channel_y);
    return true;
}

static int owner_primitive(const struct walle_lg_reveal_raster* raster,
                           size_t                               slot,
                           int32_t                              x,
                           int32_t                              y)
{
    const int32_t* transform = raster->owner_block.origin_extent[slot];
    int64_t        relative_x = (int64_t)x * 256 + 128 - transform[0];
    int64_t        relative_y = (int64_t)y * 256 + 128 - transform[1];
    __int128       left       = (__int128)relative_x * transform[3];
    __int128       right      = (__int128)relative_y * transform[2];
    if (raster->owner_block.control[slot][1] != 0)
        return left < right ? 1 : 0;
    return left + right < (__int128)transform[2] * transform[3] ? 1 : 0;
}

static int owner_primitive_at(const struct walle_lg_reveal_raster* raster,
                              size_t                               slot,
                              int32_t                              x,
                              int32_t                              y)
{
    const int32_t* bounds = raster->owner_block.bounds[slot];
    if (x < bounds[0] || y < bounds[1] || x >= bounds[2] || y >= bounds[3])
        return -1;
    int primitive = owner_primitive(raster, slot, x, y);
    return (raster->owner_block.control[slot][2] & (1 << primitive)) != 0 ? primitive : -1;
}

static int owner_code(const struct walle_lg_reveal_raster* raster,
                      int32_t                              x,
                      int32_t                              y,
                      size_t                               slot_count)
{
    int code = 0;
    for (size_t slot = 0; slot < slot_count; ++slot) {
        int primitive = owner_primitive_at(raster, slot, x, y);
        if (primitive >= 0)
            code = (int)slot * 2 + primitive + 1;
    }
    return code;
}

static void owner_coordinates(const struct walle_lg_reveal_raster* raster,
                              int32_t                              x,
                              int32_t                              y,
                              size_t                               slot,
                              int                                  primitive,
                              float                                result[static 2])
{
    int32_t start = raster->owner_block.control[slot][0];
    size_t  row   = slot * 2 + (size_t)primitive;
    size_t  width = raster->packed_width;
    size_t  base_x = (row * width + (size_t)(x - start)) * 2;
    size_t  base_y = (row * width + (size_t)(y - start)) * 2;
    result[0] = bits_float(raster->packed_words[base_x]);
    result[1] = bits_float(raster->packed_words[base_y + 1]);
}

/* Returns false when no general child owns the pixel. */
static int g_owner_ordinal = -1;

/* Set while sampling the quad's partner lanes: the hardware evaluates the
 * center via the tile iterator (round toward zero) but partner lanes via
 * the LDCF+FFMA offset path (round to nearest even) - measured directly by
 * the residual-value capture (capture2, e49d6f77...). */
static bool g_partner_sample = false;

static bool general_child_value(const struct general_child* child,
                                int32_t                     x,
                                int32_t                     y,
                                float                       result[static 2])
{
    static int value_mode = -1;
    if (value_mode < 0) {
        const char* env = getenv("PROBE_VALUE_ROUND");
        value_mode = env != nullptr ? atoi(env) : 0;
    }
    if (value_mode == 9) {
        /* Hybrid measured law: exact plane sum from the f32 C word and
         * slope words; center RTZ, partners RNE. */
        int32_t  tile_x = floor_div_i32(x, TILE_SIZE);
        int32_t  tile_y = floor_div_i32(y, TILE_SIZE);
        uint32_t cx_bits, cy_bits;
        if (!child_constant_bits(child, 0, tile_x, tile_y, &cx_bits)
            || !child_constant_bits(child, 1, tile_x, tile_y, &cy_bits)) {
            return false;
        }
        for (size_t channel = 0; channel < 2; ++channel) {
            struct dyadic constant
                = dyadic_from_float_bits(channel == 0 ? cx_bits : cy_bits);
            struct dyadic sx
                = dyadic_from_float_bits(child->slope_bits[channel][0]);
            struct dyadic sy
                = dyadic_from_float_bits(child->slope_bits[channel][1]);
            struct dyadic tx, ty, exact;
            int32_t       lx = x - tile_x * TILE_SIZE;
            int32_t       ly = y - tile_y * TILE_SIZE;
            if (!dyadic_multiply_integer(sx, (int64_t)(2 * lx + 1), &tx)
                || !dyadic_multiply_integer(sy, (int64_t)(2 * ly + 1), &ty)) {
                return false;
            }
            --tx.exponent;
            --ty.exponent;
            if (!dyadic_add(constant, tx, &exact)
                || !dyadic_add(exact, ty, &exact)) {
                return false;
            }
            uint32_t bits = 0;
            bool     ok   = g_partner_sample
                                ? dyadic_float_bits(exact, &bits)
                                : dyadic_toward_zero_float_bits(exact, &bits);
            if (!ok)
                return false;
            result[channel] = bits_float(bits);
        }
        return true;
    }
    if (value_mode == 4) {
        /* Measured 36-bit iterator lanes (quad-corner base + slope adds). */
        size_t lane = (size_t)((x & 1) + 2 * (y & 1));
        for (size_t channel = 0; channel < 2; ++channel) {
            float lanes[4];
            if (!child_lane_values(child, channel, x, y, lanes))
                return false;
            result[channel] = lanes[lane];
        }
        return true;
    }
    if (value_mode >= 5 && value_mode <= 8) {
        /* Evaluate from the INTERNAL wide state: the 28-bit C (before the
         * f32 narrowing seen in captures) and either the 24-bit slope words
         * (modes 5/8) or the 27-bit selector-product slopes (modes 6/7).
         * Output: RTZ (5/6) or RNE (7/8). */
        int32_t tx = floor_div_i32(x, TILE_SIZE);
        int32_t ty = floor_div_i32(y, TILE_SIZE);
        int32_t lx = x - tx * TILE_SIZE;
        int32_t ly = y - ty * TILE_SIZE;
        for (size_t channel = 0; channel < 2; ++channel) {
            struct dyadic exact, c28;
            if (!child_constant_exact(child, channel, tx, ty, &exact)
                || !quantize_significand(exact, 28, &c28)) {
                return false;
            }
            struct dyadic sum = c28;
            for (size_t axis = 0; axis < 2; ++axis) {
                struct dyadic slope = {};
                if (value_mode == 6 || value_mode == 7) {
                    int      sign = child->numerator_sign[channel][axis];
                    if (sign != 0) {
                        uint64_t coefficient;
                        int      coefficient_exponent;
                        if (!selector_product_stage(
                                child->numerator_index[channel][axis],
                                child->numerator_exponent[channel][axis],
                                child->selector,
                                child->selector_exponent,
                                &coefficient,
                                &coefficient_exponent)) {
                            return false;
                        }
                        int s = child->determinant < 0 ? -sign : sign;
                        slope.numerator = s * (i128)coefficient;
                        slope.exponent  = coefficient_exponent;
                    }
                } else {
                    slope = dyadic_from_float_bits(
                        child->slope_bits[channel][axis]);
                }
                if (slope.numerator == 0)
                    continue;
                struct dyadic term;
                int64_t offset = axis == 0 ? 2 * lx + 1 : 2 * ly + 1;
                if (!dyadic_multiply_integer(slope, offset, &term))
                    return false;
                --term.exponent;
                if (!dyadic_add(sum, term, &sum))
                    return false;
            }
            uint32_t bits = 0;
            bool     ok   = (value_mode == 7 || value_mode == 8)
                                ? dyadic_float_bits(sum, &bits)
                                : dyadic_toward_zero_float_bits(sum, &bits);
            if (!ok)
                return false;
            result[channel] = bits_float(bits);
        }
        return true;
    }
    int32_t  tile_x = floor_div_i32(x, TILE_SIZE);
    int32_t  tile_y = floor_div_i32(y, TILE_SIZE);
    uint32_t cx_bits, cy_bits;
    if (!child_constant_bits(child, 0, tile_x, tile_y, &cx_bits)
        || !child_constant_bits(child, 1, tile_x, tile_y, &cy_bits)) {
        return false;
    }
    uint32_t out[2] = {0, 0};
    for (size_t channel = 0; channel < 2; ++channel) {
        struct dyadic constant
            = dyadic_from_float_bits(channel == 0 ? cx_bits : cy_bits);
        struct dyadic sx = dyadic_from_float_bits(child->slope_bits[channel][0]);
        struct dyadic sy = dyadic_from_float_bits(child->slope_bits[channel][1]);
        struct dyadic tx, ty, exact;
        int32_t       lx = x - tile_x * TILE_SIZE;
        int32_t       ly = y - tile_y * TILE_SIZE;
        if (!dyadic_multiply_integer(sx, (int64_t)(2 * lx + 1), &tx)
            || !dyadic_multiply_integer(sy, (int64_t)(2 * ly + 1), &ty)) {
            return false;
        }
        --tx.exponent;
        --ty.exponent;
        if (!dyadic_add(constant, tx, &exact)
            || !dyadic_add(exact, ty, &exact)) {
            return false;
        }
        static int value_round = -1;
        if (value_round < 0) {
            const char* env = getenv("PROBE_VALUE_ROUND");
            value_round = env != nullptr ? atoi(env) : 0;
        }
        bool ok;
        if (value_round == 1) {
            ok = dyadic_float_bits(exact, &out[channel]);
        } else if (value_round == 2) {
            struct dyadic internal;
            ok = quantize_significand(exact, 28, &internal)
                 && dyadic_toward_zero_float_bits(internal, &out[channel]);
        } else if (value_round == 3) {
            struct dyadic internal;
            ok = quantize_significand(exact, 28, &internal)
                 && dyadic_float_bits(internal, &out[channel]);
        } else {
            ok = dyadic_toward_zero_float_bits(exact, &out[channel]);
        }
        if (!ok)
            return false;
    }
    result[0] = bits_float(out[0]);
    result[1] = bits_float(out[1]);
    return true;
}

/* Resolve the general setup of the pixel's final owner drawn triangle.
 * PROBE_MATCH_MODE: 0 (default) draw-order containment - the last drawn
 * triangle whose exact rasterization covers the pixel wins, mirroring the
 * hardware; 1 raster owner-slot partition. */
static const struct general_child* general_owner_child(const struct scene* scene,
                                                       int32_t             x,
                                                       int32_t             y)
{
    static int match_mode = -1;
    if (match_mode < 0) {
        const char* env = getenv("PROBE_MATCH_MODE");
        match_mode = env != nullptr ? atoi(env) : 0;
    }
    if (match_mode == 0) {
        for (size_t index = scene->general_count; index > 0; --index) {
            const struct general_child* child = &scene->general[index - 1];
            if (child_contains(child, x, y))
                return child;
        }
        return nullptr;
    }
    int code = owner_code(&scene->raster, x, y, scene->raster.owner_count);
    if (code <= 0)
        return nullptr;
    size_t slot      = (size_t)((code - 1) / 2);
    int    primitive = (code - 1) & 1;
    int    ordinal   = scene->slot_ordinal[slot][primitive];
    if (ordinal < 0)
        return nullptr;
    for (size_t index = 0; index < scene->general_count; ++index) {
        if (scene->general[index].ordinal == ordinal)
            return &scene->general[index];
    }
    return nullptr;
}

static bool general_coordinates(const struct scene* scene,
                                int32_t             x,
                                int32_t             y,
                                float               result[static 2])
{
    if (!scene->use_general)
        return false;
    const struct general_child* child = general_owner_child(scene, x, y);
    if (child == nullptr || !general_child_value(child, x, y, result))
        return false;
    g_owner_ordinal = child->ordinal;
    return true;
}

static void sample_coordinates(const struct scene* scene,
                               int32_t             x,
                               int32_t             y,
                               size_t              fallback_slot,
                               int                 fallback_primitive,
                               float               result[static 2])
{
    if (general_coordinates(scene, x, y, result))
        return;
    int code = owner_code(&scene->raster, x, y, scene->raster.owner_count);
    size_t slot = fallback_slot;
    int    primitive = fallback_primitive;
    if (code > 0) {
        slot      = (size_t)((code - 1) / 2);
        primitive = (code - 1) & 1;
    }
    if (scene->use_wide
        && wide_owner_coordinates(scene, x, y, slot, primitive, result)) {
        return;
    }
    owner_coordinates(&scene->raster, x, y, slot, primitive, result);
}

/* LDCF triple: partners are evaluated with the CENTER fragment's setup
 * (the hardware interpolates the quad's helper lanes from the center
 * fragment's own plane, never the partners' owners). */
static void sample_triple(struct scene* scene,
                          int32_t       x,
                          int32_t       y,
                          size_t        fallback_slot,
                          int           fallback_primitive,
                          float         center[static 2],
                          float         horizontal[static 2],
                          float         vertical[static 2])
{
    if (scene->use_general) {
        const struct general_child* child = general_owner_child(scene, x, y);
        if (child != nullptr) {
            g_partner_sample = false;
            bool ok = general_child_value(child, x, y, center);
            g_partner_sample = true;
            ok = ok && general_child_value(child, x ^ 1, y, horizontal)
                 && general_child_value(child, x, y ^ 1, vertical);
            g_partner_sample = false;
            if (ok) {
                g_owner_ordinal = child->ordinal;
                return;
            }
        }
    }
    sample_coordinates(scene, x, y, fallback_slot, fallback_primitive, center);
    sample_coordinates(scene, x ^ 1, y, fallback_slot, fallback_primitive,
                       horizontal);
    sample_coordinates(scene, x, y ^ 1, fallback_slot, fallback_primitive,
                       vertical);
}

static bool evaluate_byte(const uint8_t* sqrt_table,
                          size_t         sqrt_bytes,
                          const float    center[static 2],
                          const float    horizontal[static 2],
                          const float    vertical[static 2],
                          unsigned*      result)
{
    float center_distance, horizontal_distance, vertical_distance;
    if (!walle_lg_reveal_mask_apple_fast_sqrt(
            sqrt_table, sqrt_bytes, fmaf(center[1], center[1], center[0] * center[0]),
            &center_distance)
        || !walle_lg_reveal_mask_apple_fast_sqrt(
            sqrt_table, sqrt_bytes,
            fmaf(horizontal[1], horizontal[1], horizontal[0] * horizontal[0]),
            &horizontal_distance)
        || !walle_lg_reveal_mask_apple_fast_sqrt(
            sqrt_table, sqrt_bytes,
            fmaf(vertical[1], vertical[1], vertical[0] * vertical[0]),
            &vertical_distance)) {
        return false;
    }
    float feather = fabsf(horizontal_distance - center_distance)
                    + fabsf(vertical_distance - center_distance);
    if (feather < 1.0e-4f)
        feather = 1.0e-4f;
    float alpha = (1.0f - center_distance) / feather + 0.5f;
    alpha       = alpha < 0.0f ? 0.0f : (alpha > 1.0f ? 1.0f : alpha);
    float half_alpha = alpha == 0.0f || alpha == 1.0f ? alpha : half_round_trip(alpha);
    float scaled     = half_alpha * 255.0f;
    unsigned truncated = (unsigned)scaled;
    float    remainder = scaled - (float)truncated;
    if (remainder > 0.5f || (remainder == 0.5f && (truncated & 1u) != 0u))
        ++truncated;
    *result = truncated > 255u ? 255u : truncated;
    return true;
}

int main(int argc, char** argv)
{
    bool use_general = argc > 3 && argv[3][0] == '1';

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

    /* The shipped table is nibble packed; the model API consumes one byte per
     * mantissa, so unpack it here. */
    static uint8_t packed_sqrt[1u << 22];
    FILE*          sqrt_file = fopen("parity/apple_fast_sqrt_correction_nibbles.bin", "rb");
    if (sqrt_file == nullptr)
        return 2;
    if (fread(packed_sqrt, 1, sizeof packed_sqrt, sqrt_file) != sizeof packed_sqrt)
        return 2;
    fclose(sqrt_file);
    static uint8_t sqrt_table[WALLE_LG_REVEAL_FAST_SQRT_TABLE_BYTE_COUNT];
    for (size_t mantissa = 0; mantissa < sizeof sqrt_table; ++mantissa) {
        sqrt_table[mantissa]
            = (uint8_t)((packed_sqrt[mantissa >> 1] >> ((mantissa & 1u) * 4u)) & 0x0fu);
    }
    size_t sqrt_bytes = sizeof sqrt_table;

    FILE* residuals = fopen(argv[2], "r");
    if (residuals == nullptr)
        return 2;

    int      state, x, y, walle_byte, apple_byte;
    uint32_t loaded_state = UINT32_MAX;
    struct scene scene    = {.use_general = use_general};
    size_t reproduced = 0, total = 0, changed = 0;

    while (fscanf(residuals, "%d %d %d %d %d", &state, &x, &y, &walle_byte, &apple_byte) == 5) {
        ++total;
        if ((uint32_t)state != loaded_state) {
            g_current_state = state;
            if (loaded_state != UINT32_MAX)
                walle_lg_reveal_raster_destroy(&scene.raster);
            struct walle_lg_reveal_mask_request request = {
                .target_width   = TARGET_EXTENT,
                .target_height  = TARGET_EXTENT,
                .center_x       = 512.0,
                .center_y       = 614.4,
                .maximum_radius = 2164.104505809273,
                .progress       = (double)state / 64.0,
            };
            struct walle_lg_reveal_mask_geometry geometry;
            if (!walle_lg_reveal_mask_geometry_construct(&request, &geometry))
                return 1;
            if (walle_lg_reveal_raster_construct(&geometry,
                                                 TARGET_EXTENT,
                                                 TARGET_EXTENT,
                                                 &calibration,
                                                 &scene.raster)
                != WALLE_LG_REVEAL_RASTER_OK) {
                return 1;
            }
            /* Rebuild every owner's runtime quad in construction order so
             * the wide path can evaluate the unrounded planes per slot. */
            memset(scene.wide, 0, sizeof scene.wide);
            scene.use_wide = getenv("PROBE_WIDE") != nullptr;
            for (size_t s = 0; s < WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT; ++s)
                scene.slot_ordinal[s][0] = scene.slot_ordinal[s][1] = -1;
            for (size_t ordinal = 0;
                 ordinal < scene.raster.original_primitive_count; ++ordinal) {
                uint8_t ps = scene.raster.primitives[ordinal].packed_slot;
                uint8_t gp = scene.raster.primitives[ordinal].geometric_primitive;
                if (ps == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING
                    || gp == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING)
                    continue;
                scene.slot_ordinal[ps][gp] = (int)ordinal;
            }
            {
                size_t slot = 0;
                size_t group_count = geometry.index_count / 6u;
                for (size_t group = 0; group < group_count; ++group) {
                    struct walle_lg_vertex original[6];
                    struct walle_lg_vertex completed[6];
                    if (!reveal_group_vertices(&geometry, group, original))
                        break;
                    bool active[2] = {
                        reveal_triangle_area(original) != 0.0,
                        reveal_triangle_area(original + 3) != 0.0,
                    };
                    if (!active[0] && !active[1])
                        continue;
                    if (active[0] && active[1]) {
                        memcpy(completed, original, sizeof completed);
                    } else {
                        const struct walle_lg_vertex* triangle
                            = active[0] ? original : original + 3;
                        if (!complete_reveal_quad(triangle, completed))
                            break;
                    }
                    if (!reveal_vertices_valid(completed, 6))
                        break;
                    struct walle_lg_reveal_raster_quad metadata;
                    struct prepared_reveal_owner       prepared_owner;
                    enum prepared_reveal_owner_status prepare_status
                        = prepare_reveal_owner(completed, &calibration, false,
                                               &metadata, &prepared_owner);
                    if (prepare_status == PREPARED_REVEAL_OWNER_OFFSCREEN)
                        continue;
                    if (prepare_status != PREPARED_REVEAL_OWNER_READY)
                        break;
                    if (slot < WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT) {
                        scene.wide[slot].quad = prepared_owner.runtime;
                        scene.wide[slot].selector = prepared_owner.selector;
                        scene.wide[slot].selector_exponent
                            = prepared_owner.selector_exponent;
                        scene.wide[slot].valid = true;
                    }
                    ++slot;
                }
                /* postguard children, mirroring the construction loop */
                uint32_t child_target[2] = {TARGET_EXTENT, TARGET_EXTENT};
                struct walle_lg_postguard_children wide_children;
                if (walle_lg_postguard_children_construct(&geometry,
                                                          child_target,
                                                          &wide_children)
                    == WALLE_LG_POSTGUARD_OK) {
                    for (size_t index = 0; index < wide_children.child_count;
                         ++index) {
                        struct walle_lg_vertex triangle[3];
                        struct walle_lg_vertex completed[6];
                        for (size_t vertex = 0; vertex < 3; ++vertex) {
                            postguard_vertex(
                                &wide_children.children[index].vertices[vertex],
                                &triangle[vertex]);
                        }
                        if (!reveal_vertices_valid(triangle, 3))
                            continue;
                        enum prepared_reveal_owner_status target_status
                            = reveal_triangle_target_status(triangle,
                                                            TARGET_EXTENT,
                                                            TARGET_EXTENT);
                        if (target_status != PREPARED_REVEAL_OWNER_READY)
                            continue;
                        if (!complete_reveal_quad(triangle, completed)
                            || !reveal_vertices_valid(completed, 6)) {
                            continue;
                        }
                        struct walle_lg_reveal_raster_quad metadata;
                        struct prepared_reveal_owner       prepared_owner;
                        enum prepared_reveal_owner_status prepare_status
                            = prepare_reveal_owner(completed, &calibration,
                                                   true, &metadata,
                                                   &prepared_owner);
                        if (prepare_status != PREPARED_REVEAL_OWNER_READY)
                            continue;
                        if (slot < WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT) {
                            scene.wide[slot].quad = prepared_owner.runtime;
                            scene.wide[slot].selector = prepared_owner.selector;
                            scene.wide[slot].selector_exponent
                                = prepared_owner.selector_exponent;
                            scene.wide[slot].valid = true;
                            scene.slot_ordinal[slot][0]
                                = scene.slot_ordinal[slot][1]
                                = 100 + (int)index;
                        }
                        ++slot;
                    }
                }
                if (scene.use_wide
                    && slot != scene.raster.owner_count) {
                    fprintf(stderr,
                            "wide owner rebuild mismatch: %zu vs %u\n",
                            slot,
                            scene.raster.owner_count);
                    scene.use_wide = false;
                }
            }
            scene.general_count      = 0;
            uint32_t target[2]       = {TARGET_EXTENT, TARGET_EXTENT};

            /* The hardware sets up every drawn triangle independently, so
             * the base mesh triangles get their own setup too. */
            if (getenv("PROBE_NO_BASE_TRIANGLES") == nullptr) {
                size_t group_count = geometry.index_count / 6;
                for (size_t group = 0; group < group_count; ++group) {
                    struct walle_lg_vertex hexad[6];
                    if (!reveal_group_vertices(&geometry, group, hexad))
                        continue;
                    for (size_t half = 0; half < 2; ++half) {
                        struct walle_lg_vertex triangle[3] = {
                            hexad[half * 3 + 0],
                            hexad[half * 3 + 1],
                            hexad[half * 3 + 2],
                        };
                        if (!reveal_vertices_valid(triangle, 3))
                            continue;
                        if (reveal_triangle_target_status(triangle,
                                                         TARGET_EXTENT,
                                                         TARGET_EXTENT)
                            != PREPARED_REVEAL_OWNER_READY) {
                            continue;
                        }
                        register_general_triangle(&scene, &calibration,
                                                  triangle,
                                                  (int)(group * 2 + half),
                                                  (int)(group * 2 + half),
                                                  false);
                    }
                }
            }
            struct walle_lg_postguard_children children;
            if (walle_lg_postguard_children_construct(&geometry, target, &children)
                != WALLE_LG_POSTGUARD_OK) {
                return 1;
            }
            for (size_t index = 0; index < children.child_count; ++index) {
                struct walle_lg_vertex triangle[3];
                struct walle_lg_vertex completed[6];
                for (size_t vertex = 0; vertex < 3; ++vertex) {
                    postguard_vertex(&children.children[index].vertices[vertex],
                                     &triangle[vertex]);
                }
                if (getenv("PROBE_DUMP_RAW_CHILDREN") != nullptr) {
                    printf("RAWCHILD %d %zu src=%u pos=(%.9g,%.9g)(%.9g,%.9g)"
                           "(%.9g,%.9g) sdf=(%08x,%08x)(%08x,%08x)(%08x,%08x)"
                           " valid=%d status=%d\n",
                           state, index,
                           children.children[index].source_primitive,
                           triangle[0].position[0], triangle[0].position[1],
                           triangle[1].position[0], triangle[1].position[1],
                           triangle[2].position[0], triangle[2].position[1],
                           float_bits(triangle[0].sdf[0]),
                           float_bits(triangle[0].sdf[1]),
                           float_bits(triangle[1].sdf[0]),
                           float_bits(triangle[1].sdf[1]),
                           float_bits(triangle[2].sdf[0]),
                           float_bits(triangle[2].sdf[1]),
                           (int)reveal_vertices_valid(triangle, 3),
                           (int)reveal_triangle_target_status(triangle,
                                                              TARGET_EXTENT,
                                                              TARGET_EXTENT));
                }
                if (!reveal_vertices_valid(triangle, 3))
                    continue;
                if (reveal_triangle_target_status(triangle, TARGET_EXTENT, TARGET_EXTENT)
                    != PREPARED_REVEAL_OWNER_READY) {
                    continue;
                }
                /* A child is representable by the packed path only when it
                 * completes to an axis-aligned quad whose varyings are also
                 * axis separable; otherwise the shipped renderer drops it.
                 * PROBE_ALL_CHILDREN instead gives every child its own
                 * triangle setup, which is what the hardware does. */
                struct runtime_quad packed;
                if (getenv("PROBE_ALL_CHILDREN") == nullptr
                    && complete_reveal_quad(triangle, completed)
                    && reveal_vertices_valid(completed, 6)
                    && runtime_quad_from_vertices(completed, &packed)) {
                    continue;
                }
                if (scene.general_count >= MAX_GENERAL_CHILDREN)
                    continue;
                if (getenv("PROBE_DUMP_RAW_CHILDREN") != nullptr
                    && !child_prepare(triangle, &calibration,
                                      &scene.general[scene.general_count])) {
                    printf("PREPFAIL %d %zu\n", state, index);
                }
                register_general_triangle(&scene, &calibration, triangle,
                                          100 + (int)index,
                                          (int)children.children[index]
                                              .source_primitive,
                                          true);
            }
            loaded_state = (uint32_t)state;
        }

        if (getenv("PROBE_PREDICT") != nullptr) {
            /* Full-region sweep: report every pixel whose byte changes when
             * the general-children path replaces the packed path. */
            int32_t low_x = TARGET_EXTENT, low_y = TARGET_EXTENT;
            int32_t high_x = 0, high_y = 0;
            for (size_t index = 0; index < scene.general_count; ++index) {
                const struct general_child* child = &scene.general[index];
                if (child->visible_bounds[0] < low_x)
                    low_x = child->visible_bounds[0];
                if (child->visible_bounds[1] < low_y)
                    low_y = child->visible_bounds[1];
                if (child->visible_bounds[2] > high_x)
                    high_x = child->visible_bounds[2];
                if (child->visible_bounds[3] > high_y)
                    high_y = child->visible_bounds[3];
            }
            if (low_x < 1) low_x = 1;
            if (low_y < 1) low_y = 1;
            if (high_x > TARGET_EXTENT - 1) high_x = TARGET_EXTENT - 1;
            if (high_y > TARGET_EXTENT - 1) high_y = TARGET_EXTENT - 1;
            size_t sweep_diffs = 0;
            for (int32_t py = low_y - 1; py <= high_y; ++py) {
                for (int32_t px = low_x - 1; px <= high_x; ++px) {
                    int    bc = owner_code(&scene.raster, px, py,
                                           scene.raster.base_owner_count);
                    size_t fs = 0;
                    int    fp = 0;
                    if (bc > 0) {
                        fs = (size_t)((bc - 1) / 2);
                        fp = (bc - 1) & 1;
                    }
                    float pc[2], ph[2], pv[2], gc[2], gh[2], gv[2];
                    scene.use_general = false;
                    sample_coordinates(&scene, px, py, fs, fp, pc);
                    sample_coordinates(&scene, px ^ 1, py, fs, fp, ph);
                    sample_coordinates(&scene, px, py ^ 1, fs, fp, pv);
                    scene.use_general = true;
                    sample_triple(&scene, px, py, fs, fp, gc, gh, gv);
                    if (float_bits(pc[0]) == float_bits(gc[0])
                        && float_bits(pc[1]) == float_bits(gc[1])
                        && float_bits(ph[0]) == float_bits(gh[0])
                        && float_bits(ph[1]) == float_bits(gh[1])
                        && float_bits(pv[0]) == float_bits(gv[0])
                        && float_bits(pv[1]) == float_bits(gv[1])) {
                        continue;
                    }
                    unsigned packed_byte, general_byte;
                    if (!evaluate_byte(sqrt_table, sqrt_bytes,
                                       pc, ph, pv, &packed_byte)
                        || !evaluate_byte(sqrt_table, sqrt_bytes,
                                          gc, gh, gv, &general_byte)) {
                        continue;
                    }
                    if (packed_byte != general_byte) {
                        printf("PRED %d %d %d %u %u\n",
                               state, px, py, packed_byte, general_byte);
                        ++sweep_diffs;
                        if (getenv("PROBE_PRED_VERBOSE") != nullptr
                            && sweep_diffs <= 40) {
                            int fo = owner_code(&scene.raster, px, py,
                                                scene.raster.owner_count);
                            const struct general_child* mc = nullptr;
                            for (size_t ci = scene.general_count; ci > 0; --ci) {
                                const struct general_child* cd
                                    = &scene.general[ci - 1];
                                if (cd->from_postguard
                                    && cd->source_primitive != fo - 1)
                                    continue;
                                if (child_contains(cd, px, py)) {
                                    mc = cd;
                                    break;
                                }
                            }
                            fprintf(stderr,
                                    "VERB s=%d (%d,%d) base=%d full=%d "
                                    "child_ord=%d src=%d "
                                    "packed=(%08x,%08x) general=(%08x,%08x)\n",
                                    state, px, py, bc, fo,
                                    mc != nullptr ? mc->ordinal : -1,
                                    mc != nullptr ? mc->source_primitive : -1,
                                    float_bits(pc[0]), float_bits(pc[1]),
                                    float_bits(gc[0]), float_bits(gc[1]));
                        }
                    }
                }
            }
            fprintf(stderr, "predict state %d: %zu byte diffs\n",
                    state, sweep_diffs);
            scene.use_general = use_general;
            continue;
        }

        /* The fragment's fallback owner comes from the drawn primitive; the
         * residual coordinates are all inside a base owner, so resolve the
         * fallback from the base owner set. */
        int    base_code = owner_code(&scene.raster, x, y, scene.raster.base_owner_count);
        size_t fallback_slot = 0;
        int    fallback_primitive = 0;
        if (base_code > 0) {
            fallback_slot      = (size_t)((base_code - 1) / 2);
            fallback_primitive = (base_code - 1) & 1;
        }

        float center[2], horizontal[2], vertical[2];
        sample_triple(&scene, x, y, fallback_slot, fallback_primitive,
                      center, horizontal, vertical);

        if (getenv("PROBE_RESIDUAL_VERBOSE") != nullptr) {
            int fo = owner_code(&scene.raster, x, y, scene.raster.owner_count);
            int mapped = -2;
            const struct general_child* oc = nullptr;
            if (fo > 0) {
                mapped = scene.slot_ordinal[(fo - 1) / 2][(fo - 1) & 1];
                oc     = general_owner_child(&scene, x, y);
            }
            float pk[2];
            bool saved = scene.use_general;
            scene.use_general = false;
            sample_coordinates(&scene, x, y, fallback_slot,
                               fallback_primitive, pk);
            scene.use_general = saved;
            fprintf(stderr,
                    "RESID %d (%d,%d) base=%d full=%d mapped=o%d found=%d "
                    "packed=(%08x,%08x) gen=(%08x,%08x)\n",
                    state, x, y, base_code, fo, mapped, oc != nullptr,
                    float_bits(pk[0]), float_bits(pk[1]),
                    float_bits(center[0]), float_bits(center[1]));
        }

        if (getenv("PROBE_DUMP_CONSTRUCTION") != nullptr) {
            for (size_t index = 0; index < scene.general_count; ++index) {
                const struct general_child* child = &scene.general[index];
                int32_t tile_x = floor_div_i32(x, TILE_SIZE);
                int32_t tile_y = floor_div_i32(y, TILE_SIZE);
                for (size_t channel = 0; channel < 2; ++channel) {
                    printf("TRICON %d %d %d %d %zu %d %d %08x "
                           "%d %llu %d %d %llu %d %lld %lld %u %d %d\n",
                           state, x, y, child->ordinal, channel,
                           tile_x, tile_y,
                           float_bits(child_component(child, child->anchor,
                                                      channel)),
                           child->numerator_sign[channel][0],
                           (unsigned long long)
                               child->numerator_index[channel][0],
                           child->numerator_exponent[channel][0],
                           child->numerator_sign[channel][1],
                           (unsigned long long)
                               child->numerator_index[channel][1],
                           child->numerator_exponent[channel][1],
                           (long long)((int64_t)tile_x * TILE_SIZE
                                           * SUBPIXEL_SCALE
                                       - child->fixed[child->anchor][0]),
                           (long long)((int64_t)tile_y * TILE_SIZE
                                           * SUBPIXEL_SCALE
                                       - child->fixed[child->anchor][1]),
                           child->selector,
                           child->selector_exponent,
                           child->determinant < 0 ? -1 : 1);
                }
            }
        }

        if (getenv("PROBE_DUMP_TRIANGLES") != nullptr) {
            for (size_t index = 0; index < scene.general_count; ++index) {
                const struct general_child* child = &scene.general[index];
                int32_t tile_x = floor_div_i32(x, TILE_SIZE);
                int32_t tile_y = floor_div_i32(y, TILE_SIZE);
                uint32_t cx_bits, cy_bits;
                if (!child_constant_bits(child, 0, tile_x, tile_y, &cx_bits)
                    || !child_constant_bits(child, 1, tile_x, tile_y,
                                            &cy_bits)) {
                    continue;
                }
                uint32_t out[2] = {0, 0};
                bool     ok = true;
                for (size_t channel = 0; channel < 2 && ok; ++channel) {
                    struct dyadic constant = dyadic_from_float_bits(
                        channel == 0 ? cx_bits : cy_bits);
                    struct dyadic sx = dyadic_from_float_bits(
                        child->slope_bits[channel][0]);
                    struct dyadic sy = dyadic_from_float_bits(
                        child->slope_bits[channel][1]);
                    struct dyadic tx, ty, exact;
                    int32_t lx = x - tile_x * TILE_SIZE;
                    int32_t ly = y - tile_y * TILE_SIZE;
                    if (!dyadic_multiply_integer(sx, (int64_t)(2 * lx + 1), &tx)
                        || !dyadic_multiply_integer(sy, (int64_t)(2 * ly + 1),
                                                    &ty)) {
                        ok = false;
                        break;
                    }
                    --tx.exponent;
                    --ty.exponent;
                    if (!dyadic_add(constant, tx, &exact)
                        || !dyadic_add(exact, ty, &exact)
                        || !dyadic_toward_zero_float_bits(exact,
                                                          &out[channel])) {
                        ok = false;
                    }
                }
                if (!ok)
                    continue;
                printf("TRIVAL %d %d %d %d %08x %08x pg=%d c=%08x,%08x "
                       "s=%08x,%08x,%08x,%08x src=%d\n",
                       state, x, y, child->ordinal >= 0 ? child->ordinal : -1,
                       out[0], out[1], (int)child->from_postguard,
                       cx_bits, cy_bits,
                       child->slope_bits[0][0], child->slope_bits[0][1],
                       child->slope_bits[1][0], child->slope_bits[1][1],
                       child->source_primitive);
            }
        }

        if (getenv("PROBE_DUMP_ANCHOR_VARIANTS") != nullptr) {
            for (size_t slot = 0; slot < scene.raster.owner_count; ++slot) {
                const int32_t* bounds = scene.raster.owner_block.bounds[slot];
                if (x < bounds[0] - 1 || y < bounds[1] - 1
                    || x >= bounds[2] + 1 || y >= bounds[3] + 1) {
                    continue;
                }
                if (!scene.wide[slot].valid)
                    continue;
                for (int primitive = 0; primitive < 2; ++primitive) {
                    for (int override = 0; override < 4; ++override) {
                        g_anchor_override = override;
                        float value[2];
                        bool ok = wide_owner_coordinates(&scene, x, y, slot,
                                                         primitive, value);
                        g_anchor_override = -1;
                        if (!ok)
                            continue;
                        printf("ANCHVAL %d %d %d %zu %d %d %08x %08x asc=%d "
                               "ax0=%u ax1=%u\n",
                               state, x, y, slot, primitive, override,
                               float_bits(value[0]), float_bits(value[1]),
                               scene.wide[slot].quad.ascending_diagonal,
                               scene.wide[slot].quad.channel_axis[0],
                               scene.wide[slot].quad.channel_axis[1]);
                    }
                }
            }
        }

        if (getenv("PROBE_DUMP_OWNERS") != nullptr) {
            /* For every owner slot and primitive whose bounds admit this
             * pixel, dump the evaluated channel pair, plus the primitive
             * mapping table once per state. */
            static uint32_t mapped_state = UINT32_MAX;
            if (mapped_state != (uint32_t)state) {
                mapped_state = (uint32_t)state;
                for (size_t ordinal = 0;
                     ordinal < scene.raster.original_primitive_count * 2;
                     ++ordinal) {
                    printf("PRIMMAP %d %zu %u %u\n",
                           state,
                           ordinal,
                           scene.raster.primitives[ordinal].packed_slot,
                           scene.raster.primitives[ordinal]
                               .geometric_primitive);
                }
            }
            for (size_t slot = 0; slot < scene.raster.owner_count; ++slot) {
                const int32_t* bounds = scene.raster.owner_block.bounds[slot];
                if (x < bounds[0] - 1 || y < bounds[1] - 1
                    || x >= bounds[2] + 1 || y >= bounds[3] + 1) {
                    continue;
                }
                for (int primitive = 0; primitive < 2; ++primitive) {
                    float value[2];
                    if (!scene.wide[slot].valid
                        || !wide_owner_coordinates(&scene, x, y, slot,
                                                   primitive, value)) {
                        continue;
                    }
                    printf("OWNERVAL %d %d %d %zu %d %08x %08x  inb=%d "
                           "prim=%d\n",
                           state, x, y, slot, primitive,
                           float_bits(value[0]), float_bits(value[1]),
                           owner_primitive_at(&scene.raster, slot, x, y)
                               >= 0,
                           owner_primitive(&scene.raster, slot, x, y));
                }
            }
        }

        if (getenv("PROBE_DUMP_CONSTANTS") != nullptr) {
            int code = owner_code(&scene.raster, x, y,
                                  scene.raster.owner_count);
            if (code > 0) {
                size_t slot = (size_t)((code - 1) / 2);
                int    primitive = (code - 1) & 1;
                const struct wide_owner* owner = &scene.wide[slot];
                if (owner->valid) {
                    for (size_t channel = 0; channel < 2; ++channel) {
                        uint8_t axis = owner->quad.channel_axis[channel];
                        int32_t coordinate = axis == 0 ? x : y;
                        int32_t tile = floor_div_i32(coordinate, TILE_SIZE);
                        struct dyadic wide_c;
                        struct dyadic slope;
                        uint32_t      f32_c = 0;
                        if (!wide_constant(owner, channel, (size_t)primitive,
                                           tile, &wide_c)
                            || !wide_slope(owner, channel, &slope)
                            || !coefficient_bits(&owner->quad,
                                                 channel,
                                                 (size_t)primitive,
                                                 tile,
                                                 false,
                                                 owner->selector,
                                                 owner->selector_exponent,
                                                 &f32_c)) {
                            continue;
                        }
                        double slope_value
                            = ldexp((double)(int64_t)slope.numerator,
                                    slope.exponent);
                        double c_value
                            = ldexp((double)(int64_t)wide_c.numerator,
                                    wide_c.exponent);
                        printf("CONST %d %d %d ch%zu axis%u slot%zu prim%d "
                               "tile%d f32c=%08x widec=%.17e slope=%.17e "
                               "slopef32=%08x\n",
                               state, x, y, channel, axis, slot, primitive,
                               tile, f32_c, c_value, slope_value,
                               float_bits(round_f32(slope_value)));
                    }
                }
            }
        }

        if (getenv("PROBE_DUMP_PLANE") != nullptr) {
            int code = owner_code(&scene.raster, x, y,
                                  scene.raster.owner_count);
            if (code > 0) {
                size_t slot = (size_t)((code - 1) / 2);
                int    primitive = (code - 1) & 1;
                /* Recover the owner quad and dump slope/constant words for
                 * both channels at this pixel's tiles. */
                const struct walle_lg_reveal_raster_quad* owner
                    = &scene.raster.owners[slot];
                struct runtime_quad quad = {
                    .raster = {
                        .origin_x_fixed = owner->origin_fixed[0],
                        .origin_y_fixed = owner->origin_fixed[1],
                        .width_fixed    = owner->extent_fixed[0],
                        .height_fixed   = owner->extent_fixed[1],
                    },
                    .ascending_diagonal = owner->ascending_diagonal,
                };
                /* endpoints must be rebuilt: find them from the packed
                 * geometry via the scene construction again. */
                (void)quad;
                printf("PLANE %d %d %d slot=%zu prim=%d start=%d\n",
                       state, x, y, slot, primitive, owner->axis_start);
            }
        }

        if (getenv("PROBE_DUMP_CHILD_WORDS") != nullptr) {
            static uint32_t words_state = UINT32_MAX;
            if (words_state != (uint32_t)state) {
                words_state = (uint32_t)state;
                for (size_t index = 0; index < scene.general_count; ++index) {
                    const struct general_child* child = &scene.general[index];
                    printf("CHILDW %d %d %zu %d %d %d %d %d %d %d %d "
                           "%08x %08x %08x %08x\n",
                           state, child->ordinal, index,
                           child->fixed[0][0], child->fixed[0][1],
                           child->fixed[1][0], child->fixed[1][1],
                           child->fixed[2][0], child->fixed[2][1],
                           child->determinant < 0 ? -1 : 1,
                           (int)child->from_postguard,
                           child->slope_bits[0][0], child->slope_bits[0][1],
                           child->slope_bits[1][0], child->slope_bits[1][1]);
                    int32_t tx0 = floor_div_i32(child->visible_bounds[0], TILE_SIZE);
                    int32_t ty0 = floor_div_i32(child->visible_bounds[1], TILE_SIZE);
                    int32_t tx1 = floor_div_i32(child->visible_bounds[2] - 1, TILE_SIZE);
                    int32_t ty1 = floor_div_i32(child->visible_bounds[3] - 1, TILE_SIZE);
                    for (int32_t ty = ty0; ty <= ty1; ++ty) {
                        for (int32_t tx = tx0; tx <= tx1; ++tx) {
                            uint32_t cx = 0, cy = 0;
                            if (!child_constant_bits(child, 0, tx, ty, &cx)
                                || !child_constant_bits(child, 1, tx, ty, &cy))
                                continue;
                            printf("CHILDC %d %zu %d %d %08x %08x\n",
                                   state, index, tx, ty, cx, cy);
                        }
                    }
                }
            }
        }

        if (getenv("PROBE_DUMP_GEOMETRY") != nullptr) {
            static uint32_t geometry_state = UINT32_MAX;
            if (geometry_state != (uint32_t)state) {
                geometry_state = (uint32_t)state;
                for (size_t index = 0; index < scene.general_count; ++index) {
                    const struct general_child* child = &scene.general[index];
                    printf("CHILDGEO %d %d %d %d %zu %d "
                           "%d %d %d %d %d %d "
                           "%d %d %d %d "
                           "%08x %08x %08x %08x %08x %08x %u %d",
                           state, child->ordinal, child->source_primitive,
                           (int)child->from_postguard, child->anchor,
                           child->determinant < 0 ? -1 : 1,
                           child->fixed[0][0], child->fixed[0][1],
                           child->fixed[1][0], child->fixed[1][1],
                           child->fixed[2][0], child->fixed[2][1],
                           child->visible_bounds[0], child->visible_bounds[1],
                           child->visible_bounds[2], child->visible_bounds[3],
                           child->slope_bits[0][0], child->slope_bits[0][1],
                           child->slope_bits[1][0], child->slope_bits[1][1],
                           float_bits(child_component(child, child->anchor, 0)),
                           float_bits(child_component(child, child->anchor, 1)),
                           child->selector, child->selector_exponent);
                    for (size_t channel = 0; channel < 2; ++channel)
                        for (size_t axis = 0; axis < 2; ++axis)
                            printf(" %d %llu %d",
                                   child->numerator_sign[channel][axis],
                                   (unsigned long long)
                                       child->numerator_index[channel][axis],
                                   child->numerator_exponent[channel][axis]);
                    printf("\n");
                    printf("CHILDSDF %d %d", state, child->ordinal);
                    for (size_t vertex = 0; vertex < 3; ++vertex)
                        printf(" %08x %08x %08x %08x",
                               float_bits(child->vertices[vertex].position[0]),
                               float_bits(child->vertices[vertex].position[1]),
                               float_bits(child->vertices[vertex].sdf[0]),
                               float_bits(child->vertices[vertex].sdf[1]));
                    printf("\n");
                }
            }
        }

        if (getenv("PROBE_DUMP_VALUES") != nullptr) {
            g_owner_ordinal = -1;
            float redo[2];
            sample_coordinates(&scene, x, y, fallback_slot,
                               fallback_primitive, redo);
            printf("VALUES %d %d %d %08x %08x %08x %08x %08x %08x own=%d\n",
                   state, x, y,
                   float_bits(center[0]), float_bits(center[1]),
                   float_bits(horizontal[0]), float_bits(horizontal[1]),
                   float_bits(vertical[0]), float_bits(vertical[1]),
                   g_owner_ordinal);
        }

        if (getenv("PROBE_CONFIG_SEARCH") != nullptr) {
            /* Enumerate candidate center sources and partner policies and
             * report every combination that reproduces Apple's byte. */
            struct candidate
            {
                char  name[48];
                float value[2];
            };
            struct candidate candidates[24];
            size_t           candidate_count = 0;

            int packed_code = owner_code(&scene.raster, x, y,
                                         scene.raster.owner_count);
            if (packed_code > 0) {
                size_t slot = (size_t)((packed_code - 1) / 2);
                int    primitive = (packed_code - 1) & 1;
                struct candidate* candidate = &candidates[candidate_count++];
                snprintf(candidate->name, sizeof candidate->name,
                         "packed s%zu p%d", slot, primitive);
                owner_coordinates(&scene.raster, x, y, slot, primitive,
                                  candidate->value);
                /* The sibling primitive of the same quad. */
                if ((scene.raster.owner_block.control[slot][2]
                     & (1 << (1 - primitive))) != 0) {
                    candidate = &candidates[candidate_count++];
                    snprintf(candidate->name, sizeof candidate->name,
                             "packed s%zu p%d sibling", slot, 1 - primitive);
                    owner_coordinates(&scene.raster, x, y, slot,
                                      1 - primitive, candidate->value);
                }
                /* Lower-slot owners that also contain the pixel. */
                for (size_t other = 0; other < slot
                     && candidate_count + 1 < 24; ++other) {
                    int other_primitive
                        = owner_primitive_at(&scene.raster, other, x, y);
                    if (other_primitive < 0)
                        continue;
                    candidate = &candidates[candidate_count++];
                    snprintf(candidate->name, sizeof candidate->name,
                             "packed s%zu p%d under", other, other_primitive);
                    owner_coordinates(&scene.raster, x, y, other,
                                      other_primitive, candidate->value);
                }
            }
            for (size_t index = 0; index < scene.general_count
                 && candidate_count + 1 < 24; ++index) {
                const struct general_child* child = &scene.general[index];
                if (!child_contains(child, x, y))
                    continue;
                float lanes[2][4];
                if (!child_lane_values(child, 0, x, y, lanes[0])
                    || !child_lane_values(child, 1, x, y, lanes[1])) {
                    continue;
                }
                size_t lane = (size_t)((x & 1) + 2 * (y & 1));
                struct candidate* candidate = &candidates[candidate_count++];
                snprintf(candidate->name, sizeof candidate->name,
                         "general %zu%s", index,
                         child->from_postguard ? " pg" : "");
                candidate->value[0] = lanes[0][lane];
                candidate->value[1] = lanes[1][lane];
            }

            for (size_t index = 0; index < candidate_count; ++index) {
                /* Partner policy A: keep the normally resolved partners. */
                unsigned byte_a;
                if (evaluate_byte(sqrt_table, sqrt_bytes,
                                  candidates[index].value, horizontal,
                                  vertical, &byte_a)
                    && byte_a == (unsigned)apple_byte) {
                    printf("state %2d (%4d,%4d) APPLE via center=%s "
                           "partners=resolved\n",
                           state, x, y, candidates[index].name);
                }
                /* Partner policy B: partners from the same candidate's
                 * source when it is a general child. */
            }
            printf("state %2d (%4d,%4d) walle=%3d apple=%3d "
                   "candidates=%zu\n",
                   state, x, y, walle_byte, apple_byte, candidate_count);
        }

        if (getenv("PROBE_OWNER_DETAIL") != nullptr) {
            for (size_t index = scene.general_count; index > 0; --index) {
                const struct general_child* child = &scene.general[index - 1];
                if (!child_contains(child, x, y))
                    continue;
                int32_t edges[2][3];
                child_edges(child, edges);
                printf("state %2d (%4d,%4d) owner=%zu postguard=%d terms:",
                       state, x, y, index - 1, (int)child->from_postguard);
                for (size_t channel = 0; channel < 2; ++channel) {
                    for (size_t axis = 0; axis < 2; ++axis) {
                        unsigned nonzero = 0;
                        for (size_t vertex = 0; vertex < 3; ++vertex) {
                            if (vertex == child->anchor)
                                continue;
                            float delta = subtract_f32(
                                child_component(child, vertex, channel),
                                child_component(child, child->anchor, channel));
                            float edge = round_f32((double)edges[axis][vertex]
                                                   / SUBPIXEL_SCALE);
                            nonzero += (delta != 0.0f && edge != 0.0f) ? 1u : 0u;
                        }
                        printf(" c%zua%zu=%u", channel, axis, nonzero);
                    }
                }
                float packed_center[2];
                int   code = owner_code(&scene.raster, x, y,
                                        scene.raster.owner_count);
                if (code > 0) {
                    owner_coordinates(&scene.raster, x, y,
                                      (size_t)((code - 1) / 2), (code - 1) & 1,
                                      packed_center);
                    printf("  packed=(%08x,%08x) general=(%08x,%08x)",
                           float_bits(packed_center[0]), float_bits(packed_center[1]),
                           float_bits(center[0]), float_bits(center[1]));
                }
                for (size_t channel = 0; channel < 2; ++channel) {
                    for (size_t axis = 0; axis < 2; ++axis) {
                        printf("\n    c%zua%zu products:", channel, axis);
                        for (size_t vertex = 0; vertex < 3; ++vertex) {
                            if (vertex == child->anchor)
                                continue;
                            float delta = subtract_f32(
                                child_component(child, vertex, channel),
                                child_component(child, child->anchor, channel));
                            float edge = round_f32((double)edges[axis][vertex]
                                                   / SUBPIXEL_SCALE);
                            if (delta == 0.0f || edge == 0.0f)
                                continue;
                            uint64_t di, ei;
                            int      de, ee;
                            (void)positive_float_components(float_bits(fabsf(delta)),
                                                            &di, &de);
                            (void)positive_float_components(float_bits(fabsf(edge)),
                                                            &ei, &ee);
                            uint64_t pi;
                            int      pe;
                            (void)product_stage(di, de, ei, ee, 27, 16, 15, &pi, &pe);
                            printf(" [v%zu %c%llu e%d]", vertex,
                                   (signbit(delta) != signbit(edge)) ? '-' : '+',
                                   (unsigned long long)pi, pe);
                        }
                        printf(" -> sign=%d index=%llu exp=%d slope=%08x",
                               child->numerator_sign[channel][axis],
                               (unsigned long long)child->numerator_index[channel][axis],
                               child->numerator_exponent[channel][axis],
                               child->slope_bits[channel][axis]);
                    }
                }
                printf("\n");
                break;
            }
        }

        if (getenv("PROBE_SOURCE") != nullptr) {
            int owner_index = -1;
            for (size_t index = scene.general_count; index > 0; --index) {
                if (child_contains(&scene.general[index - 1], x, y)) {
                    owner_index = (int)(index - 1);
                    break;
                }
            }
            int postguard_index = -1;
            for (size_t index = scene.general_count; index > 0; --index) {
                if (scene.general[index - 1].from_postguard
                    && child_contains(&scene.general[index - 1], x, y)) {
                    postguard_index = (int)(index - 1);
                    break;
                }
            }
            unsigned model_byte = 0;
            (void)evaluate_byte(sqrt_table, sqrt_bytes, center, horizontal, vertical,
                                &model_byte);
            printf("state %2d (%4d,%4d) walle=%3d apple=%3d model=%3u owner=%d "
                   "postguard=%d\n",
                   state, x, y, walle_byte, apple_byte, model_byte, owner_index,
                   postguard_index);
        }

        if (getenv("PROBE_TRY_CHILDREN") != nullptr) {
            const char* verdict = "no child helps";
            int         helper  = -1;
            for (size_t index = 0; index < scene.general_count; ++index) {
                const struct general_child* child = &scene.general[index];
                float lanes[3][2][4];
                bool  ok = true;
                int32_t coordinates[3][2]
                    = {{x, y}, {x ^ 1, y}, {x, y ^ 1}};
                for (size_t sample = 0; sample < 3 && ok; ++sample) {
                    for (size_t channel = 0; channel < 2 && ok; ++channel) {
                        ok = child_lane_values(child,
                                               channel,
                                               coordinates[sample][0],
                                               coordinates[sample][1],
                                               lanes[sample][channel]);
                    }
                }
                if (!ok)
                    continue;
                float trial[3][2];
                for (size_t sample = 0; sample < 3; ++sample) {
                    size_t lane = (size_t)((coordinates[sample][0] & 1)
                                           + 2 * (coordinates[sample][1] & 1));
                    trial[sample][0] = lanes[sample][0][lane];
                    trial[sample][1] = lanes[sample][1][lane];
                }
                unsigned candidate_byte;
                if (evaluate_byte(sqrt_table, sqrt_bytes, trial[0], trial[1], trial[2],
                                  &candidate_byte)
                    && candidate_byte == (unsigned)apple_byte) {
                    verdict = "child plane reproduces Apple";
                    helper  = (int)index;
                    break;
                }
            }
            printf("state %2d (%4d,%4d) walle=%3d apple=%3d children=%zu %s(%d)\n",
                   state, x, y, walle_byte, apple_byte, scene.general_count,
                   verdict, helper);
        }

        if (getenv("PROBE_PERTURB") != nullptr) {
            float* slots[6]
                = {&center[0], &center[1], &horizontal[0], &horizontal[1],
                   &vertical[0], &vertical[1]};
            static const char* names[6]
                = {"center.x", "center.y", "hpartner.x", "hpartner.y",
                   "vpartner.x", "vpartner.y"};
            char explanation[256] = "none";
            for (size_t slot = 0; slot < 6 && explanation[0] == 'n'; ++slot) {
                for (int step = -2; step <= 2; ++step) {
                    if (step == 0)
                        continue;
                    float saved = *slots[slot];
                    uint32_t bits = float_bits(saved);
                    *slots[slot] = bits_float((uint32_t)((int32_t)bits + step));
                    unsigned trial;
                    if (evaluate_byte(sqrt_table, sqrt_bytes, center, horizontal,
                                      vertical, &trial)
                        && trial == (unsigned)apple_byte) {
                        snprintf(explanation, sizeof explanation, "%s %+d ulp",
                                 names[slot], step);
                        *slots[slot] = saved;
                        break;
                    }
                    *slots[slot] = saved;
                }
            }
            printf("state %2d (%4d,%4d) walle=%3d apple=%3d fix=%s\n",
                   state, x, y, walle_byte, apple_byte, explanation);
        }

        float center_distance, horizontal_distance, vertical_distance;
        if (!walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table, sqrt_bytes, fmaf(center[1], center[1], center[0] * center[0]),
                &center_distance)
            || !walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table, sqrt_bytes,
                fmaf(horizontal[1], horizontal[1], horizontal[0] * horizontal[0]),
                &horizontal_distance)
            || !walle_lg_reveal_mask_apple_fast_sqrt(
                sqrt_table, sqrt_bytes,
                fmaf(vertical[1], vertical[1], vertical[0] * vertical[0]),
                &vertical_distance)) {
            continue;
        }
        float feather = fabsf(horizontal_distance - center_distance)
                        + fabsf(vertical_distance - center_distance);
        if (feather < 1.0e-4f)
            feather = 1.0e-4f;
        float alpha = (1.0f - center_distance) / feather + 0.5f;
        alpha       = alpha < 0.0f ? 0.0f : (alpha > 1.0f ? 1.0f : alpha);

        float half_alpha
            = alpha == 0.0f || alpha == 1.0f ? alpha : half_round_trip(alpha);
        float    scaled    = half_alpha * 255.0f;
        unsigned truncated = (unsigned)scaled;
        float    remainder = scaled - (float)truncated;
        if (remainder > 0.5f || (remainder == 0.5f && (truncated & 1u) != 0u))
            ++truncated;
        unsigned coverage = truncated > 255u ? 255u : truncated;

        reproduced += coverage == (unsigned)(use_general ? apple_byte : walle_byte) ? 1u : 0u;
        changed += coverage != (unsigned)walle_byte ? 1u : 0u;
        if (getenv("PROBE_VERBOSE") != nullptr) {
            unsigned covers = 0;
            for (size_t index = 0; index < scene.general_count; ++index) {
                covers |= child_contains(&scene.general[index], x, y) ? 1u : 0u;
                covers |= child_contains(&scene.general[index], x ^ 1, y) ? 2u : 0u;
                covers |= child_contains(&scene.general[index], x, y ^ 1) ? 4u : 0u;
            }
            printf("state %2d (%4d,%4d) walle=%3d apple=%3d model=%3u general=%zu cover=%u\n",
                   state, x, y, walle_byte, apple_byte, coverage,
                   scene.general_count, covers);
        }
    }
    fclose(residuals);
    printf("%s: residuals %zu, target byte reproduced %zu, differs from Walle %zu\n",
           use_general ? "with general children" : "validation (no general children)",
           total,
           reproduced,
           changed);
    return 0;
}
