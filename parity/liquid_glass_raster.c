#include "liquid_glass_raster.h"

#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdckdint.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);

constexpr int32_t  SUBPIXEL_SCALE        = 256;
constexpr int32_t  TILE_SIZE             = 32;
constexpr unsigned CENTER_PRECISION_BITS = 36;
constexpr uint64_t P25_KEY_LOWER         = UINT64_C(1) << 24;
constexpr uint64_t P25_KEY_UPPER         = UINT64_C(1) << 25;
constexpr uint64_t P25_RECIPROCAL        = UINT64_C(1) << 49;

typedef signed _BitInt(128) i128;
typedef unsigned _BitInt(128) u128;

struct dyadic
{
    i128 numerator;
    int  exponent;
};

struct raster_case
{
    int32_t origin_x_fixed;
    int32_t origin_y_fixed;
    int32_t width_fixed;
    int32_t height_fixed;
};

struct endpoint
{
    uint32_t low_bits;
    uint32_t high_bits;
};

struct runtime_quad
{
    struct raster_case raster;
    struct endpoint    endpoint[WALLE_LG_RASTER_CHANNEL_COUNT];
    uint8_t            channel_axis[WALLE_LG_RASTER_CHANNEL_COUNT];
    bool               ascending_diagonal;
};

static float vertex_component(const struct walle_lg_vertex* vertex, size_t component);

static const int32_t near_square_height_deltas[] = {
    -256,
    -128,
    -64,
    -32,
    -16,
    -8,
    -4,
    -2,
    -1,
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
};

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof bits);
    return bits;
}

static float bits_float(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static float round_f32(double value)
{
    volatile float result = (float)value;
    return result;
}

static float subtract_f32(float left, float right)
{
    volatile float result = left - right;
    return result;
}

static float multiply_f32(float left, float right)
{
    volatile float result = left * right;
    return result;
}

static unsigned bit_length_u64(uint64_t value)
{
    return value == 0 ? 0u : 64u - (unsigned)__builtin_clzll(value);
}

static unsigned bit_length_u128(u128 value)
{
    uint64_t high = (uint64_t)(value >> 64);
    return high != 0 ? 64u + bit_length_u64(high) : bit_length_u64((uint64_t)value);
}

static u128 magnitude_i128(i128 value)
{
    u128 unsigned_value = (u128)value;
    return value < 0 ? (u128)0 - unsigned_value : unsigned_value;
}

static struct dyadic normalize(struct dyadic value)
{
    if (value.numerator == 0)
        return (struct dyadic){};
    u128     magnitude = magnitude_i128(value.numerator);
    unsigned zeros     = 0;
    uint64_t low       = (uint64_t)magnitude;
    if (low == 0) {
        zeros = 64;
        low   = (uint64_t)(magnitude >> 64);
    }
    zeros += (unsigned)__builtin_ctzll(low);
    value.numerator >>= zeros;
    value.exponent += (int)zeros;
    return value;
}

static struct dyadic dyadic_from_float_bits(uint32_t bits)
{
    uint32_t exponent    = (bits >> 23) & 0xffu;
    uint32_t significand = bits & UINT32_C(0x7fffff);
    if (exponent == 0xffu)
        return (struct dyadic){.exponent = INT_MIN};
    if (exponent != 0) {
        significand |= UINT32_C(1) << 23;
    } else if (significand == 0) {
        return (struct dyadic){};
    }
    i128 numerator = significand;
    if ((bits >> 31) != 0)
        numerator = -numerator;
    return normalize((struct dyadic){
        .numerator = numerator,
        .exponent  = exponent == 0 ? -149 : (int)exponent - 150,
    });
}

static bool shift_left_i128(i128 value, unsigned shift, i128* result)
{
    u128 magnitude = magnitude_i128(value);
    if (shift >= 127 || bit_length_u128(magnitude) + shift >= 127)
        return false;
    i128 shifted = (i128)(magnitude << shift);
    *result      = value < 0 ? -shifted : shifted;
    return true;
}

static bool dyadic_add(struct dyadic left, struct dyadic right, struct dyadic* result)
{
    if (left.numerator == 0) {
        *result = right;
        return true;
    }
    if (right.numerator == 0) {
        *result = left;
        return true;
    }
    int  exponent = left.exponent < right.exponent ? left.exponent : right.exponent;
    i128 left_numerator;
    i128 right_numerator;
    if (!shift_left_i128(left.numerator, (unsigned)(left.exponent - exponent), &left_numerator)
        || !shift_left_i128(
            right.numerator, (unsigned)(right.exponent - exponent), &right_numerator)) {
        return false;
    }
    *result = normalize((struct dyadic){
        .numerator = left_numerator + right_numerator,
        .exponent  = exponent,
    });
    return true;
}

static bool dyadic_multiply_integer(struct dyadic value, int64_t multiplier, struct dyadic* result)
{
    if (multiplier == 0 || value.numerator == 0) {
        *result = (struct dyadic){};
        return true;
    }
    unsigned multiplier_bits = bit_length_u64(multiplier < 0 ? (uint64_t)(-(multiplier + 1)) + 1u
                                                             : (uint64_t)multiplier);
    if (bit_length_u128(magnitude_i128(value.numerator)) + multiplier_bits >= 127)
        return false;
    *result = normalize((struct dyadic){
        .numerator = value.numerator * multiplier,
        .exponent  = value.exponent,
    });
    return true;
}

static int floor_binary_exponent(struct dyadic value)
{
    return (int)bit_length_u128(magnitude_i128(value.numerator)) - 1 + value.exponent;
}

static u128 round_right_nearest_even(u128 value, unsigned shift)
{
    if (shift == 0)
        return value;
    u128 quotient  = value >> shift;
    u128 remainder = value - (quotient << shift);
    u128 half      = (u128)1 << (shift - 1);
    if (remainder > half || (remainder == half && (quotient & 1u) != 0))
        ++quotient;
    return quotient;
}

static bool
quantize_significand(struct dyadic value, unsigned precision_bits, struct dyadic* result)
{
    if (value.numerator == 0) {
        *result = value;
        return true;
    }
    int  target_exponent = floor_binary_exponent(value) - (int)precision_bits + 1;
    int  shift           = target_exponent - value.exponent;
    u128 magnitude       = magnitude_i128(value.numerator);
    if (shift > 0) {
        if (shift >= 127)
            return false;
        magnitude = round_right_nearest_even(magnitude, (unsigned)shift);
    } else if (shift < 0) {
        if ((unsigned)-shift >= 127 || bit_length_u128(magnitude) + (unsigned)-shift >= 127)
            return false;
        magnitude <<= (unsigned)-shift;
    }
    i128 numerator = (i128)magnitude;
    if (value.numerator < 0)
        numerator = -numerator;
    *result = normalize((struct dyadic){.numerator = numerator, .exponent = target_exponent});
    return true;
}

static bool dyadic_compare(struct dyadic left, struct dyadic right, int* result)
{
    struct dyadic difference;
    right.numerator = -right.numerator;
    if (!dyadic_add(left, right, &difference))
        return false;
    *result = (difference.numerator > 0) - (difference.numerator < 0);
    return true;
}

static bool dyadic_float_bits(struct dyadic value, uint32_t* result)
{
    if (value.numerator == 0) {
        *result = 0;
        return true;
    }
    bool negative   = value.numerator < 0;
    value.numerator = (i128)magnitude_i128(value.numerator);
    struct dyadic rounded;
    if (!quantize_significand(value, 24, &rounded))
        return false;
    int exponent = floor_binary_exponent(rounded);
    if (exponent < -126 || exponent > 127)
        return false;
    int  shift       = rounded.exponent - (exponent - 23);
    u128 significand = magnitude_i128(rounded.numerator);
    if (shift >= 0) {
        if (shift >= 127)
            return false;
        significand <<= (unsigned)shift;
    } else {
        significand >>= (unsigned)-shift;
    }
    if (significand == ((u128)1 << 24)) {
        significand >>= 1;
        ++exponent;
    }
    if (significand < ((u128)1 << 23) || significand >= ((u128)1 << 24) || exponent > 127) {
        return false;
    }
    *result = (negative ? UINT32_C(0x80000000) : 0u) | ((uint32_t)(exponent + 127) << 23)
              | ((uint32_t)significand - (UINT32_C(1) << 23));
    return true;
}

static bool quantize_composite_constant(struct dyadic value, uint32_t* result)
{
    struct dyadic internal;
    return quantize_significand(value, 28, &internal) && dyadic_float_bits(internal, result);
}

static bool dyadic_toward_zero_float_bits(struct dyadic value, uint32_t* result)
{
    uint32_t bits;
    if (!dyadic_float_bits(value, &bits))
        return false;
    struct dyadic rounded = dyadic_from_float_bits(bits);
    int           comparison;
    if (!dyadic_compare(rounded, value, &comparison))
        return false;
    if ((value.numerator > 0 && comparison > 0) || (value.numerator < 0 && comparison < 0))
        --bits;
    *result = bits;
    return true;
}

static int64_t floor_div_power_two(i128 numerator, unsigned shift)
{
    if (shift == 0)
        return (int64_t)numerator;
    if (numerator >= 0)
        return (int64_t)(numerator >> shift);
    u128 magnitude = magnitude_i128(numerator);
    return -(int64_t)((magnitude + (((u128)1 << shift) - 1)) >> shift);
}

static bool dyadic_floor_ratio_power_two(struct dyadic value, int step_exponent, int64_t* result)
{
    int shift = step_exponent - value.exponent;
    if (shift >= 0) {
        if (shift >= 127)
            return false;
        *result = floor_div_power_two(value.numerator, (unsigned)shift);
        return true;
    }
    i128 shifted;
    if (!shift_left_i128(value.numerator, (unsigned)-shift, &shifted) || shifted < INT64_MIN
        || shifted > INT64_MAX) {
        return false;
    }
    *result = (int64_t)shifted;
    return true;
}

static uint64_t
partial_product_sum(uint64_t multiplicand, uint64_t multiplier, unsigned truncation_bits)
{
    uint64_t result = 0;
    for (unsigned bit = 0; bit < bit_length_u64(multiplier); ++bit) {
        if ((multiplier & (UINT64_C(1) << bit)) != 0) {
            uint64_t partial = multiplicand << bit;
            result += (partial >> truncation_bits) << truncation_bits;
        }
    }
    return result;
}

static bool product_stage(uint64_t  multiplicand,
                          int       multiplicand_exponent,
                          uint64_t  multiplier,
                          int       multiplier_exponent,
                          unsigned  output_bits,
                          unsigned  truncation_bits,
                          uint64_t  bias_units,
                          uint64_t* result_index,
                          int*      result_exponent)
{
    uint64_t product = multiplicand * multiplier;
    int      shift   = (int)bit_length_u64(product) - (int)output_bits;
    if (shift < 0)
        return false;
    uint64_t partial = partial_product_sum(multiplicand, multiplier, truncation_bits);
    *result_index    = (partial + (bias_units << truncation_bits)) >> shift;
    *result_exponent = multiplicand_exponent + multiplier_exponent + shift;
    return true;
}

/* M1-measured general-path stage (production-children + cancelled-numerator
 * captures, plan shas in the ledger): products of 32 bits or fewer bypass
 * the truncating partial-product array and emerge exact, with no bias.  The
 * shipped packed path keeps the historical product_stage behaviour above. */
static bool general_product_stage(uint64_t  multiplicand,
                                  int       multiplicand_exponent,
                                  uint64_t  multiplier,
                                  int       multiplier_exponent,
                                  unsigned  output_bits,
                                  unsigned  truncation_bits,
                                  uint64_t  bias_units,
                                  uint64_t* result_index,
                                  int*      result_exponent)
{
    uint64_t product = multiplicand * multiplier;
    int      bits    = (int)bit_length_u64(product);
    int      shift   = bits - (int)output_bits;
    if (bits <= 32) {
        if (shift <= 0) {
            *result_index    = product;
            *result_exponent = multiplicand_exponent + multiplier_exponent;
            return true;
        }
        uint64_t rounded = (product + (UINT64_C(1) << (shift - 1))) >> shift;
        if (bit_length_u64(rounded) > output_bits) {
            rounded >>= 1;
            ++shift;
        }
        *result_index    = rounded;
        *result_exponent = multiplicand_exponent + multiplier_exponent + shift;
        return true;
    }
    uint64_t partial = partial_product_sum(multiplicand, multiplier, truncation_bits);
    *result_index    = (partial + (bias_units << truncation_bits)) >> shift;
    *result_exponent = multiplicand_exponent + multiplier_exponent + shift;
    return true;
}

static uint64_t
propagated_discarded_carry(uint64_t multiplicand, uint64_t multiplier, unsigned truncation_bits)
{
    unsigned column = truncation_bits - 1;
    uint64_t carry  = 0;
    for (unsigned bit = 0; bit < bit_length_u64(multiplier); ++bit) {
        if ((multiplier & (UINT64_C(1) << bit)) != 0)
            carry += ((multiplicand << bit) >> column) & 1u;
    }
    return carry >> 1;
}

static bool column_product_stage(uint64_t  multiplicand,
                                 int       multiplicand_exponent,
                                 uint64_t  multiplier,
                                 int       multiplier_exponent,
                                 uint64_t* result_index,
                                 int*      result_exponent)
{
    constexpr unsigned output_bits     = 27;
    constexpr unsigned truncation_bits = 19;
    constexpr uint64_t bias_units      = 10;
    uint64_t           product         = multiplicand * multiplier;
    int                shift           = (int)bit_length_u64(product) - (int)output_bits;
    if (shift < 0)
        return false;
    uint64_t partial  = partial_product_sum(multiplicand, multiplier, truncation_bits);
    uint64_t carry    = propagated_discarded_carry(multiplicand, multiplier, truncation_bits);
    uint64_t adjusted = partial + ((carry + bias_units) << truncation_bits);
    *result_index     = adjusted >> shift;
    *result_exponent  = multiplicand_exponent + multiplier_exponent + shift;
    return true;
}

/* General-path middle product with the exact-narrow-product bypass. */
static bool general_column_product_stage(uint64_t  multiplicand,
                                         int       multiplicand_exponent,
                                         uint64_t  multiplier,
                                         int       multiplier_exponent,
                                         uint64_t* result_index,
                                         int*      result_exponent)
{
    constexpr unsigned output_bits = 27;
    constexpr uint64_t bias_units  = 10;
    uint64_t           product     = multiplicand * multiplier;
    int                bits        = (int)bit_length_u64(product);
    int                shift       = bits - (int)output_bits;
    if (bits <= 32) {
        if (shift <= 0) {
            *result_index    = product;
            *result_exponent = multiplicand_exponent + multiplier_exponent;
            return true;
        }
        uint64_t rounded = (product + (UINT64_C(1) << (shift - 1))) >> shift;
        if (bit_length_u64(rounded) > output_bits) {
            rounded >>= 1;
            ++shift;
        }
        *result_index    = rounded;
        *result_exponent = multiplicand_exponent + multiplier_exponent + shift;
        return true;
    }
    /* Operand-anchored column, like the selector stages (dense capture
     * 74610b36... all 36 contexts). */
    unsigned truncation_bits = bit_length_u64(multiplicand) - 8;
    uint64_t partial  = partial_product_sum(multiplicand, multiplier, truncation_bits);
    uint64_t carry    = propagated_discarded_carry(multiplicand, multiplier, truncation_bits);
    uint64_t adjusted = partial + ((carry + bias_units) << truncation_bits);
    *result_index     = adjusted >> shift;
    *result_exponent  = multiplicand_exponent + multiplier_exponent + shift;
    return true;
}

static bool selector_product_stage(uint64_t  multiplicand,
                                   int       multiplicand_exponent,
                                   uint64_t  selector,
                                   int       selector_exponent,
                                   uint64_t* result_index,
                                   int*      result_exponent)
{
    /* M1-measured law (first-cancellation capture 5d9ee7b9..., production
     * children captures c79c9d68... / 1e7fd8c2...): the selector product
     * truncates the partial-product columns below product_bitlen - 32 with
     * a +20 compensation at the truncated column scale; products of 32 bits
     * or fewer bypass the array and are exact, rounded half-up to 27 bits
     * when wider than 27. */
    uint64_t product = multiplicand * selector;
    int      bits    = (int)bit_length_u64(product);
    int      shift   = bits - 27;
    if (bits <= 32) {
        if (shift <= 0) {
            *result_index    = product;
            *result_exponent = multiplicand_exponent + selector_exponent;
            return true;
        }
        uint64_t rounded = (product + (UINT64_C(1) << (shift - 1))) >> shift;
        if (bit_length_u64(rounded) > 27) {
            rounded >>= 1;
            ++shift;
        }
        *result_index    = rounded;
        *result_exponent = multiplicand_exponent + selector_exponent + shift;
        return true;
    }
    /* Operand-anchored truncation column (sel-isolation capture
     * 3e7a7549..., joint slope fits 2,696 rows): the array truncates at
     * multiplicand_bits - 8 regardless of the product width. */
    unsigned truncation = bit_length_u64(multiplicand) - 8;
    uint64_t partial    = partial_product_sum(multiplicand, selector, truncation);
    *result_index       = (partial + (UINT64_C(20) << truncation)) >> shift;
    *result_exponent    = multiplicand_exponent + selector_exponent + shift;
    return true;
}

static bool constant_selector_product_stage(uint64_t  multiplicand,
                                            int       multiplicand_exponent,
                                            uint64_t  selector,
                                            int       selector_exponent,
                                            uint64_t* result_index,
                                            int*      result_exponent)
{
    /* M1-measured: the tile-constant reciprocal keeps a fixed truncation
     * column of 20 for wide products (state-31 production capture, 408/408
     * tiles), while narrow products bypass the array exactly. */
    uint64_t product = multiplicand * selector;
    int      bits    = (int)bit_length_u64(product);
    int      shift   = bits - 27;
    if (bits <= 32) {
        if (shift <= 0) {
            *result_index    = product;
            *result_exponent = multiplicand_exponent + selector_exponent;
            return true;
        }
        uint64_t rounded = (product + (UINT64_C(1) << (shift - 1))) >> shift;
        if (bit_length_u64(rounded) > 27) {
            rounded >>= 1;
            ++shift;
        }
        *result_index    = rounded;
        *result_exponent = multiplicand_exponent + selector_exponent + shift;
        return true;
    }
    unsigned truncation = bit_length_u64(multiplicand) - 8;
    uint64_t partial    = partial_product_sum(multiplicand, selector, truncation);
    *result_index       = (partial + (UINT64_C(20) << truncation)) >> shift;
    *result_exponent    = multiplicand_exponent + selector_exponent + shift;
    return true;
}

static bool positive_float_components(uint32_t bits, uint64_t* significand, int* exponent)
{
    uint32_t encoded_exponent = (bits >> 23) & 0xffu;
    if ((bits >> 31) != 0 || encoded_exponent == 0 || encoded_exponent == 0xffu)
        return false;
    *significand = (UINT32_C(1) << 23) | (bits & UINT32_C(0x7fffff));
    *exponent    = (int)encoded_exponent - 150;
    return true;
}

static uint64_t round_integer_right_nearest_even(uint64_t value, unsigned shift)
{
    if (shift == 0)
        return value;
    uint64_t quotient  = value >> shift;
    uint64_t remainder = value - (quotient << shift);
    uint64_t half      = UINT64_C(1) << (shift - 1);
    return quotient + (remainder > half || (remainder == half && (quotient & 1u) != 0) ? 1u : 0u);
}

static bool selector_index(const struct raster_case* raster,
                           size_t                    selector_count,
                           size_t*                   result_index,
                           int*                      result_exponent)
{
    uint64_t determinant = (uint64_t)raster->width_fixed * (uint64_t)raster->height_fixed;
    if (determinant == 0)
        return false;
    unsigned exponent = bit_length_u64(determinant) - 1;
    uint64_t normalized;
    if (exponent <= 23) {
        normalized = determinant << (23 - exponent);
    } else {
        normalized = round_integer_right_nearest_even(determinant, exponent - 23);
    }
    if (normalized == (UINT64_C(1) << 24))
        normalized >>= 1;
    uint64_t mantissa  = normalized - (UINT64_C(1) << 23);
    uint64_t quantized = ((mantissa + 2u) / 4u) * 4u;
    size_t   index     = (size_t)(quantized / 4u);
    if (index >= selector_count)
        return false;
    *result_index    = index;
    *result_exponent = -(int)bit_length_u64(determinant - 1) - 24 + 16;
    return true;
}

static bool p25_selector(const struct runtime_quad*                quad,
                         const struct walle_lg_raster_calibration* calibration,
                         uint32_t*                                 selector,
                         int*                                      exponent)
{
    if (quad->raster.width_fixed <= 0 || quad->raster.height_fixed <= 0
        || calibration->p25_ceil_bits == nullptr
        || calibration->p25_selector_bit_count != P25_KEY_UPPER - P25_KEY_LOWER) {
        return false;
    }
    uint64_t determinant
        = (uint64_t)quad->raster.width_fixed * (uint64_t)quad->raster.height_fixed;
    unsigned determinant_exponent = bit_length_u64(determinant) - 1;
    uint64_t key;
    if (determinant_exponent <= 24) {
        key = determinant << (24 - determinant_exponent);
    } else {
        unsigned shift = determinant_exponent - 24;
        uint64_t quotient = determinant >> shift;
        uint64_t remainder = determinant - (quotient << shift);
        key = quotient + (remainder >= (UINT64_C(1) << (shift - 1)) ? 1u : 0u);
    }
    *exponent = -(int)bit_length_u64(determinant - 1) - 8;
    if ((determinant & (determinant - 1)) == 0 || key == P25_KEY_UPPER) {
        *selector = UINT32_C(1) << 24;
        return true;
    }
    if (key < P25_KEY_LOWER || key >= P25_KEY_UPPER)
        return false;
    uint64_t bit_index = key - P25_KEY_LOWER;
    bool ceil = (((uint32_t)calibration->p25_ceil_bits[bit_index >> 3] >> (bit_index & 7u)) & 1u)
                != 0;
    uint64_t floor = P25_RECIPROCAL / key;
    *selector = (uint32_t)(floor + (ceil && P25_RECIPROCAL % key != 0 ? 1u : 0u));
    return true;
}

static int32_t subpixel_fixed(float value)
{
    double exact = (double)value * SUBPIXEL_SCALE;
    return (int32_t)floor(exact + 0.5);
}

static bool fixed_position_equal(const int32_t left[static 2], const int32_t right[static 2])
{
    return left[0] == right[0] && left[1] == right[1];
}

static bool runtime_quad_from_vertices(const struct walle_lg_vertex vertices[static 6],
                                       struct runtime_quad*         result)
{
    int32_t fixed[6][2];
    int32_t left = INT32_MAX, right = INT32_MIN, bottom = INT32_MAX, top = INT32_MIN;
    for (size_t index = 0; index < 6; ++index) {
        fixed[index][0] = subpixel_fixed(vertices[index].position[0]);
        fixed[index][1] = subpixel_fixed(vertices[index].position[1]);
        left            = fixed[index][0] < left ? fixed[index][0] : left;
        right           = fixed[index][0] > right ? fixed[index][0] : right;
        bottom          = fixed[index][1] < bottom ? fixed[index][1] : bottom;
        top             = fixed[index][1] > top ? fixed[index][1] : top;
    }
    if (right <= left || top <= bottom)
        return false;

    struct runtime_quad quad = {
        .raster = {
            .origin_x_fixed = left,
            .origin_y_fixed = bottom,
            .width_fixed    = right - left,
            .height_fixed   = top - bottom,
        },
    };
    static const uint8_t preferred_axes[WALLE_LG_RASTER_CHANNEL_COUNT] = {0, 1, 0, 1};
    for (size_t channel = 0; channel < WALLE_LG_RASTER_CHANNEL_COUNT; ++channel) {
        size_t component = channel + 4;
        bool   found     = false;
        for (size_t attempt = 0; attempt < 2 && !found; ++attempt) {
            uint8_t  axis = attempt == 0 ? preferred_axes[channel] : 1u - preferred_axes[channel];
            int32_t  low_position  = axis == 0 ? left : bottom;
            int32_t  high_position = axis == 0 ? right : top;
            uint32_t low_bits = 0, high_bits = 0;
            bool     low_set = false, high_set = false, consistent = true;
            for (size_t index = 0; index < 6; ++index) {
                uint32_t bits = float_bits(vertex_component(&vertices[index], component));
                if (fixed[index][axis] == low_position) {
                    if (low_set && bits != low_bits)
                        consistent = false;
                    low_bits = bits;
                    low_set  = true;
                }
                if (fixed[index][axis] == high_position) {
                    if (high_set && bits != high_bits)
                        consistent = false;
                    high_bits = bits;
                    high_set  = true;
                }
            }
            if (consistent && low_set && high_set) {
                quad.channel_axis[channel] = axis;
                quad.endpoint[channel]
                    = (struct endpoint){.low_bits = low_bits, .high_bits = high_bits};
                found = true;
            }
        }
        if (!found)
            return false;
    }

    int32_t shared[2][2];
    size_t  shared_count = 0;
    for (size_t first = 0; first < 3; ++first) {
        for (size_t second = 3; second < 6; ++second) {
            if (!fixed_position_equal(fixed[first], fixed[second]))
                continue;
            bool duplicate = false;
            for (size_t index = 0; index < shared_count; ++index)
                duplicate |= fixed_position_equal(shared[index], fixed[first]);
            if (!duplicate && shared_count < 2) {
                memcpy(shared[shared_count], fixed[first], sizeof shared[shared_count]);
                ++shared_count;
            }
        }
    }
    if (shared_count != 2 || shared[0][0] == shared[1][0])
        return false;
    size_t lower_x          = shared[0][0] < shared[1][0] ? 0 : 1;
    quad.ascending_diagonal = shared[1 - lower_x][1] > shared[lower_x][1];
    *result                 = quad;
    return true;
}

static bool first_stage_numerator(const struct runtime_quad* quad,
                                  size_t                     channel,
                                  int*                       result_sign,
                                  uint64_t*                  result_index,
                                  int*                       result_exponent)
{
    const struct endpoint* endpoint = &quad->endpoint[channel];
    uint8_t                axis     = quad->channel_axis[channel];
    int32_t opposite_fixed = axis == 0 ? quad->raster.height_fixed : quad->raster.width_fixed;
    float   delta = subtract_f32(bits_float(endpoint->high_bits), bits_float(endpoint->low_bits));
    if (delta == 0.0f) {
        *result_sign     = 0;
        *result_index    = 0;
        *result_exponent = 0;
        return true;
    }
    uint64_t delta_index, opposite_index;
    int      delta_exponent, opposite_exponent;
    if (!positive_float_components(float_bits(fabsf(delta)), &delta_index, &delta_exponent))
        return false;
    float opposite = round_f32((double)opposite_fixed / SUBPIXEL_SCALE);
    if (!positive_float_components(float_bits(opposite), &opposite_index, &opposite_exponent)
        || !product_stage(delta_index,
                          delta_exponent,
                          opposite_index,
                          opposite_exponent,
                          27,
                          16,
                          15,
                          result_index,
                          result_exponent)) {
        return false;
    }
    *result_sign = signbit(delta) ? -1 : 1;
    return true;
}

static bool reciprocal_stage(const struct runtime_quad* quad,
                             uint32_t                   selector,
                             int                        selector_exponent,
                             uint64_t                   input_index,
                             int                        input_exponent,
                             uint64_t*                  result_index,
                             int*                       result_exponent)
{
    (void)quad;
    return product_stage(input_index,
                         input_exponent,
                         selector,
                         selector_exponent,
                         27,
                         19,
                         20,
                         result_index,
                         result_exponent);
}

static bool determinant_slope_bits(const struct runtime_quad* quad,
                                   size_t                     channel,
                                   uint32_t                   selector,
                                   int                        selector_exponent,
                                   uint32_t*                  result)
{
    int      sign;
    uint64_t numerator;
    int      numerator_exponent;
    if (!first_stage_numerator(quad, channel, &sign, &numerator, &numerator_exponent)) {
        return false;
    }
    if (sign == 0) {
        *result = 0;
        return true;
    }
    uint64_t coefficient;
    int      coefficient_exponent;
    if (!reciprocal_stage(quad,
                          selector,
                          selector_exponent,
                          numerator,
                          numerator_exponent,
                          &coefficient,
                          &coefficient_exponent)) {
        return false;
    }
    double value = ldexp((double)(sign * (int64_t)coefficient), coefficient_exponent);
    *result      = float_bits(round_f32(value));
    return true;
}

static bool coefficient_bits(const struct runtime_quad* quad,
                             size_t                     channel,
                             size_t                     primitive,
                             int32_t                    tile,
                             bool                       force_low_anchor,
                             uint32_t                   selector,
                             int                        selector_exponent,
                             uint32_t*                  result)
{
    const struct endpoint* endpoint = &quad->endpoint[channel];
    uint8_t                axis     = quad->channel_axis[channel];
    bool                   anchor_high
        = !force_low_anchor && axis == 0 && primitive == 0 && !quad->ascending_diagonal;
    uint32_t anchor_bits = anchor_high ? endpoint->high_bits : endpoint->low_bits;
    int32_t  anchor_fixed;
    if (axis == 0) {
        anchor_fixed = quad->raster.origin_x_fixed + (anchor_high ? quad->raster.width_fixed : 0);
    } else {
        anchor_fixed = quad->raster.origin_y_fixed + (anchor_high ? quad->raster.height_fixed : 0);
    }
    int64_t displacement64 = (int64_t)tile * TILE_SIZE * SUBPIXEL_SCALE - anchor_fixed;
    if (displacement64 < INT32_MIN || displacement64 > INT32_MAX)
        return false;
    int32_t       displacement = (int32_t)displacement64;
    struct dyadic value        = dyadic_from_float_bits(anchor_bits);

    int      sign;
    uint64_t numerator;
    int      numerator_exponent;
    if (!first_stage_numerator(quad, channel, &sign, &numerator, &numerator_exponent)) {
        return false;
    }
    if (sign != 0 && displacement != 0) {
        uint64_t distance_index;
        int      distance_exponent;
        float    distance = round_f32((double)llabs(displacement) / SUBPIXEL_SCALE);
        if (!positive_float_components(float_bits(distance), &distance_index, &distance_exponent)) {
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
        if (!reciprocal_stage(quad,
                              selector,
                              selector_exponent,
                              middle,
                              middle_exponent,
                              &coefficient,
                              &coefficient_exponent)) {
            return false;
        }
        struct dyadic term = {
            .numerator = sign * (displacement < 0 ? -(i128)coefficient : (i128)coefficient),
            .exponent  = coefficient_exponent,
        };
        if (!dyadic_add(value, term, &value))
            return false;
    }
    return quantize_composite_constant(value, result);
}

static bool base_selector(const struct runtime_quad*                quad,
                          const struct walle_lg_raster_calibration* calibration,
                          uint32_t*                                 selector,
                          int*                                      exponent)
{
    if (calibration->p25_ceil_bits != nullptr)
        return p25_selector(quad, calibration, selector, exponent);
    size_t index;
    if (calibration->base_selectors == nullptr
        || !selector_index(&quad->raster, calibration->base_selector_count, &index, exponent)) {
        return false;
    }
    *selector = calibration->base_selectors[index];
    return true;
}

static bool square_selector(const struct runtime_quad*                quad,
                            const struct walle_lg_raster_calibration* calibration,
                            uint32_t*                                 selector,
                            int*                                      exponent)
{
    if (calibration->p25_ceil_bits != nullptr)
        return p25_selector(quad, calibration, selector, exponent);
    if (quad->raster.width_fixed != quad->raster.height_fixed || quad->raster.width_fixed < 0
        || (uint32_t)quad->raster.width_fixed < calibration->square_width_fixed_lower) {
        return false;
    }
    size_t offset = (uint32_t)quad->raster.width_fixed - calibration->square_width_fixed_lower;
    size_t ignored;
    if (calibration->square_selectors == nullptr || offset >= calibration->square_selector_count
        || !selector_index(&quad->raster, calibration->base_selector_count, &ignored, exponent)) {
        return false;
    }
    *selector = calibration->square_selectors[offset];
    return true;
}

static bool
calibrated_square_or_near_selector(const struct runtime_quad*                quad,
                                   const struct walle_lg_raster_calibration* calibration,
                                   uint32_t*                                 selector,
                                   int*                                      exponent)
{
    if (calibration->p25_ceil_bits != nullptr)
        return p25_selector(quad, calibration, selector, exponent);
    if (quad->raster.width_fixed == quad->raster.height_fixed)
        return square_selector(quad, calibration, selector, exponent);
    if (quad->raster.width_fixed < 0
        || (uint32_t)quad->raster.width_fixed < calibration->square_width_fixed_lower) {
        return false;
    }
    size_t width_offset
        = (uint32_t)quad->raster.width_fixed - calibration->square_width_fixed_lower;
    if (width_offset >= calibration->square_selector_count)
        return false;
    int32_t delta       = quad->raster.height_fixed - quad->raster.width_fixed;
    size_t  delta_index = SIZE_MAX;
    for (size_t index = 0;
         index < sizeof near_square_height_deltas / sizeof near_square_height_deltas[0];
         ++index) {
        if (near_square_height_deltas[index] == delta) {
            delta_index = index;
            break;
        }
    }
    size_t expected = calibration->square_selector_count
                      * (sizeof near_square_height_deltas / sizeof near_square_height_deltas[0]);
    size_t ignored;
    if (delta_index == SIZE_MAX || calibration->near_square_selectors == nullptr
        || calibration->near_square_selector_count != expected
        || !selector_index(&quad->raster, calibration->base_selector_count, &ignored, exponent)) {
        return false;
    }
    *selector = calibration->near_square_selectors[delta_index * calibration->square_selector_count
                                                   + width_offset];
    return true;
}

static bool natural_shadow_selector(const struct runtime_quad*                quad,
                                    const struct walle_lg_raster_calibration* calibration,
                                    uint32_t*                                 selector,
                                    int*                                      exponent)
{
    if (calibration->p25_ceil_bits != nullptr)
        return p25_selector(quad, calibration, selector, exponent);
    if (quad->raster.width_fixed <= 0 || quad->raster.height_fixed <= 0
        || calibration->natural_shadow_cases == nullptr
        || calibration->natural_shadow_selectors == nullptr) {
        return false;
    }
    struct walle_lg_raster_case_selector key = {
        .width_fixed  = (uint32_t)quad->raster.width_fixed,
        .height_fixed = (uint32_t)quad->raster.height_fixed,
    };
    size_t lower = 0, upper = calibration->natural_shadow_count;
    while (lower < upper) {
        size_t                               middle    = lower + (upper - lower) / 2;
        struct walle_lg_raster_case_selector candidate = calibration->natural_shadow_cases[middle];
        if (candidate.width_fixed < key.width_fixed
            || (candidate.width_fixed == key.width_fixed
                && candidate.height_fixed < key.height_fixed)) {
            lower = middle + 1;
        } else {
            upper = middle;
        }
    }
    size_t ignored;
    if (lower >= calibration->natural_shadow_count
        || calibration->natural_shadow_cases[lower].width_fixed != key.width_fixed
        || calibration->natural_shadow_cases[lower].height_fixed != key.height_fixed
        || !selector_index(&quad->raster, calibration->base_selector_count, &ignored, exponent)) {
        return false;
    }
    *selector = calibration->natural_shadow_selectors[lower];
    return true;
}

static int32_t floor_div_i32(int32_t numerator, int32_t denominator)
{
    int32_t quotient  = numerator / denominator;
    int32_t remainder = numerator % denominator;
    return quotient - (remainder != 0 && ((remainder < 0) != (denominator < 0)) ? 1 : 0);
}

static int64_t ceil_div_i64(int64_t numerator, int64_t denominator)
{
    int64_t quotient  = numerator / denominator;
    int64_t remainder = numerator % denominator;
    return quotient + (remainder != 0 && ((remainder < 0) == (denominator < 0)) ? 1 : 0);
}

static bool visible_bounds(const struct raster_case* raster, int32_t result[static 4])
{
    constexpr int32_t half      = SUBPIXEL_SCALE / 2;
    int64_t           bounds[4] = {
        ceil_div_i64((int64_t)raster->origin_x_fixed - half, SUBPIXEL_SCALE),
        ceil_div_i64((int64_t)raster->origin_y_fixed - half, SUBPIXEL_SCALE),
        ceil_div_i64((int64_t)raster->origin_x_fixed + raster->width_fixed - half, SUBPIXEL_SCALE),
        ceil_div_i64((int64_t)raster->origin_y_fixed + raster->height_fixed - half, SUBPIXEL_SCALE),
    };
    for (size_t index = 0; index < 4; ++index) {
        if (bounds[index] < INT32_MIN || bounds[index] > INT32_MAX)
            return false;
        result[index] = (int32_t)bounds[index];
    }
    return true;
}

static bool coefficient_table(const struct runtime_quad* quad,
                              uint32_t                   selector,
                              int                        selector_exponent,
                              int32_t                    requested_start,
                              uint32_t                   requested_count,
                              bool                       force_low_anchor,
                              int32_t*                   result_start,
                              uint32_t*                  result_count,
                              uint32_t*                  output)
{
    constexpr int32_t tile_fixed = TILE_SIZE * SUBPIXEL_SCALE;
    int32_t required_start = floor_div_i32(quad->raster.origin_x_fixed < quad->raster.origin_y_fixed
                                               ? quad->raster.origin_x_fixed
                                               : quad->raster.origin_y_fixed,
                                           tile_fixed);
    int32_t horizontal_end
        = floor_div_i32(quad->raster.origin_x_fixed + quad->raster.width_fixed - 1, tile_fixed);
    int32_t vertical_end
        = floor_div_i32(quad->raster.origin_y_fixed + quad->raster.height_fixed - 1, tile_fixed);
    int32_t  required_end = horizontal_end > vertical_end ? horizontal_end : vertical_end;
    int32_t  first        = requested_count == 0 ? required_start : requested_start;
    uint32_t count = requested_count == 0 ? (uint32_t)(required_end - first + 1) : requested_count;
    if (count == 0 || first > required_start || first + (int32_t)count - 1 < required_end)
        return false;
    for (size_t primitive = 0; primitive < WALLE_LG_RASTER_PRIMITIVE_COUNT; ++primitive) {
        for (uint32_t offset = 0; offset < count; ++offset) {
            int32_t tile = first + (int32_t)offset;
            for (size_t channel = 0; channel < WALLE_LG_RASTER_CHANNEL_COUNT; ++channel) {
                size_t index
                    = (primitive * count + offset) * WALLE_LG_RASTER_CHANNEL_COUNT + channel;
                if (!coefficient_bits(quad,
                                      channel,
                                      primitive,
                                      tile,
                                      force_low_anchor,
                                      selector,
                                      selector_exponent,
                                      &output[index])) {
                    return false;
                }
            }
        }
    }
    *result_start = first;
    *result_count = count;
    return true;
}

static bool center_pair_bits(int32_t       local_pixel,
                             struct dyadic slope,
                             struct dyadic constant,
                             int           step_exponent,
                             uint32_t      result[static 2])
{
    int64_t       odd = (int64_t)(2 * (local_pixel & ~1) + 1);
    struct dyadic product;
    if (!dyadic_multiply_integer(slope, odd, &product))
        return false;
    --product.exponent;
    struct dyadic exact;
    if (!dyadic_add(constant, product, &exact))
        return false;
    int64_t index;
    if (!dyadic_floor_ratio_power_two(exact, step_exponent, &index))
        return false;
    struct dyadic left = {.numerator = index, .exponent = step_exponent};
    struct dyadic right;
    if (!dyadic_add(left, slope, &right) || !dyadic_toward_zero_float_bits(left, &result[0])
        || !dyadic_toward_zero_float_bits(right, &result[1])) {
        return false;
    }
    return true;
}

static bool axis_values(const struct runtime_quad* quad,
                        size_t                     channel,
                        size_t                     primitive,
                        int32_t                    first,
                        uint32_t                   count,
                        bool                       force_low_anchor,
                        uint32_t                   selector,
                        int                        selector_exponent,
                        uint32_t*                  output)
{
    uint32_t slope_bits;
    if (!determinant_slope_bits(quad, channel, selector, selector_exponent, &slope_bits)) {
        return false;
    }
    struct dyadic          slope    = dyadic_from_float_bits(slope_bits);
    const struct endpoint* endpoint = &quad->endpoint[channel];
    struct dyadic          low      = dyadic_from_float_bits(endpoint->low_bits);
    struct dyadic          high     = dyadic_from_float_bits(endpoint->high_bits);
    low.numerator                   = (i128)magnitude_i128(low.numerator);
    high.numerator                  = (i128)magnitude_i128(high.numerator);
    int endpoint_comparison;
    if (!dyadic_compare(low, high, &endpoint_comparison))
        return false;
    int endpoint_step = floor_binary_exponent(endpoint_comparison >= 0 ? low : high)
                        - (int)CENTER_PRECISION_BITS + 1;
    int32_t       cached_tile   = INT32_MIN;
    struct dyadic constant      = {};
    int           step_exponent = 0;
    for (uint32_t offset = 0; offset < count; ++offset) {
        int32_t coordinate = first + (int32_t)offset;
        int32_t tile       = floor_div_i32(coordinate, TILE_SIZE);
        if (tile != cached_tile) {
            uint32_t constant_bits;
            if (!coefficient_bits(quad,
                                  channel,
                                  primitive,
                                  tile,
                                  force_low_anchor,
                                  selector,
                                  selector_exponent,
                                  &constant_bits)) {
                return false;
            }
            constant      = dyadic_from_float_bits(constant_bits);
            step_exponent = constant.numerator == 0
                                ? endpoint_step
                                : floor_binary_exponent(constant) - (int)CENTER_PRECISION_BITS + 1;
            cached_tile   = tile;
        }
        int32_t  local_pixel = coordinate - tile * TILE_SIZE;
        uint32_t pair[2];
        if (!center_pair_bits(local_pixel, slope, constant, step_exponent, pair))
            return false;
        output[offset] = pair[(uint32_t)local_pixel & 1u];
    }
    return true;
}

static bool axis_table(const struct runtime_quad* quad,
                       uint32_t                   selector,
                       int                        selector_exponent,
                       bool                       force_low_anchor,
                       uint32_t                   halo,
                       size_t                     channel_count,
                       int32_t*                   result_start,
                       uint32_t*                  result_count,
                       uint32_t*                  output)
{
    if (channel_count == 0 || channel_count > WALLE_LG_RASTER_CHANNEL_COUNT)
        return false;
    int32_t bounds[4];
    if (!visible_bounds(&quad->raster, bounds))
        return false;
    int64_t first64 = (int64_t)(bounds[0] < bounds[1] ? bounds[0] : bounds[1]) - halo;
    int64_t end64   = (int64_t)(bounds[2] > bounds[3] ? bounds[2] : bounds[3]) + halo;
    if (first64 < INT32_MIN || end64 > INT32_MAX || end64 <= first64
        || end64 - first64 > UINT32_MAX) {
        return false;
    }
    int32_t  first = (int32_t)first64;
    uint32_t count = (uint32_t)(end64 - first64);
    for (size_t primitive = 0; primitive < WALLE_LG_RASTER_PRIMITIVE_COUNT; ++primitive) {
        for (size_t channel = 0; channel < channel_count; ++channel) {
            uint32_t* temporary = malloc((size_t)count * sizeof(uint32_t));
            if (temporary == nullptr
                || !axis_values(quad,
                                channel,
                                primitive,
                                first,
                                count,
                                force_low_anchor,
                                selector,
                                selector_exponent,
                                temporary)) {
                free(temporary);
                return false;
            }
            for (uint32_t offset = 0; offset < count; ++offset) {
                output[(primitive * count + offset) * channel_count + channel] = temporary[offset];
            }
            free(temporary);
        }
    }
    *result_start = first;
    *result_count = count;
    return true;
}

static bool word_count(size_t first, size_t second, size_t third, size_t* result)
{
    size_t intermediate;
    return !ckd_mul(&intermediate, first, second) && !ckd_mul(result, intermediate, third);
}

static bool allocate_words(size_t count, uint32_t** result)
{
    size_t bytes;
    if (count == 0 || ckd_mul(&bytes, count, sizeof(uint32_t)))
        return false;
    *result = calloc(1, bytes);
    return *result != nullptr;
}

static bool tile_span(const struct runtime_quad* quad, int32_t* start, uint32_t* count)
{
    constexpr int32_t tile_fixed = TILE_SIZE * SUBPIXEL_SCALE;
    int32_t first = floor_div_i32(quad->raster.origin_x_fixed < quad->raster.origin_y_fixed
                                      ? quad->raster.origin_x_fixed
                                      : quad->raster.origin_y_fixed,
                                  tile_fixed);
    int32_t horizontal_end
        = floor_div_i32(quad->raster.origin_x_fixed + quad->raster.width_fixed - 1, tile_fixed);
    int32_t vertical_end
        = floor_div_i32(quad->raster.origin_y_fixed + quad->raster.height_fixed - 1, tile_fixed);
    int32_t last = horizontal_end > vertical_end ? horizontal_end : vertical_end;
    if (last < first)
        return false;
    *start = first;
    *count = (uint32_t)(last - first + 1);
    return true;
}

static bool construct_main(const struct walle_lg_transition_frame*   frame,
                           uint32_t                                  axis_extent,
                           const struct walle_lg_raster_calibration* calibration,
                           struct walle_lg_raster_tables*            tables)
{
    struct runtime_quad quad;
    if (!runtime_quad_from_vertices(frame->main_vertices, &quad))
        return false;
    uint32_t selector;
    int      selector_exponent;
    if (!square_selector(&quad, calibration, &selector, &selector_exponent))
        return false;
    int32_t  tile_start;
    uint32_t coefficient_width;
    if (!tile_span(&quad, &tile_start, &coefficient_width)
        || !word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                       coefficient_width,
                       WALLE_LG_RASTER_CHANNEL_COUNT,
                       &tables->coefficient_word_count)
        || !allocate_words(tables->coefficient_word_count, &tables->coefficients)) {
        return false;
    }
    int32_t  generated_start;
    uint32_t generated_count;
    if (!coefficient_table(&quad,
                           selector,
                           selector_exponent,
                           tile_start,
                           coefficient_width,
                           false,
                           &generated_start,
                           &generated_count,
                           tables->coefficients)
        || generated_start != tile_start || generated_count != coefficient_width) {
        return false;
    }
    tables->tile_start        = (uint32_t)tile_start;
    tables->coefficient_width = coefficient_width;
    for (size_t channel = 0; channel < WALLE_LG_RASTER_CHANNEL_COUNT; ++channel) {
        if (!determinant_slope_bits(
                &quad, channel, selector, selector_exponent, &tables->slopes[channel])) {
            return false;
        }
    }

    size_t full_words;
    if (!word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                    axis_extent,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &full_words)
        || !allocate_words(full_words, &tables->main_axis)) {
        return false;
    }
    tables->main_axis_word_count = full_words;
    int32_t  compact_start;
    uint32_t compact_count;
    int32_t bounds[4];
    if (!visible_bounds(&quad.raster, bounds))
        return false;
    int32_t compact_lower = bounds[0] < bounds[1] ? bounds[0] : bounds[1];
    int32_t compact_upper = bounds[2] > bounds[3] ? bounds[2] : bounds[3];
    if (compact_lower <= 0 || compact_upper == INT32_MAX)
        return false;
    compact_start       = compact_lower - 1;
    int32_t compact_end = compact_upper + 1;
    if (compact_end <= compact_start || (uint32_t)compact_end > axis_extent) {
        return false;
    }
    compact_count = (uint32_t)(compact_end - compact_start);
    size_t    compact_words;
    uint32_t* compact = nullptr;
    if (!word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                    compact_count,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &compact_words)
        || !allocate_words(compact_words, &compact)
        || !axis_table(&quad,
                       selector,
                       selector_exponent,
                       false,
                       1,
                       WALLE_LG_RASTER_CHANNEL_COUNT,
                       &compact_start,
                       &compact_count,
                       compact)) {
        free(compact);
        return false;
    }
    for (size_t primitive = 0; primitive < WALLE_LG_RASTER_PRIMITIVE_COUNT; ++primitive) {
        size_t source      = primitive * (size_t)compact_count * WALLE_LG_RASTER_CHANNEL_COUNT;
        size_t destination = (primitive * (size_t)axis_extent + (uint32_t)compact_start)
                             * WALLE_LG_RASTER_CHANNEL_COUNT;
        memcpy(&tables->main_axis[destination],
               &compact[source],
               (size_t)compact_count * WALLE_LG_RASTER_CHANNEL_COUNT * sizeof(uint32_t));
    }
    free(compact);
    return true;
}

static bool positive_raster_extent(const struct walle_lg_vertex vertices[static 6])
{
    int32_t left = INT32_MAX, right = INT32_MIN, bottom = INT32_MAX, top = INT32_MIN;
    for (size_t index = 0; index < 6; ++index) {
        int32_t x = subpixel_fixed(vertices[index].position[0]);
        int32_t y = subpixel_fixed(vertices[index].position[1]);
        left      = x < left ? x : left;
        right     = x > right ? x : right;
        bottom    = y < bottom ? y : bottom;
        top       = y > top ? y : top;
    }
    return right > left && top > bottom;
}

static bool construct_shadow(const struct walle_lg_transition_frame*   frame,
                             const struct walle_lg_raster_calibration* calibration,
                             struct walle_lg_raster_tables*            tables)
{
    if (!word_count(2 * WALLE_LG_SHADOW_QUAD_COUNT,
                    WALLE_LG_SHADOW_COEFFICIENT_TILE_COUNT,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &tables->shadow_coefficient_word_count)
        || !allocate_words(tables->shadow_coefficient_word_count, &tables->shadow_coefficients)
        || !word_count(WALLE_LG_SHADOW_QUAD_COUNT,
                       1,
                       WALLE_LG_RASTER_CHANNEL_COUNT,
                       &tables->shadow_slope_word_count)
        || !allocate_words(tables->shadow_slope_word_count, &tables->shadow_slopes)) {
        return false;
    }
    for (size_t quad_index = 0; quad_index < WALLE_LG_SHADOW_QUAD_COUNT; ++quad_index) {
        struct walle_lg_vertex expanded[6];
        for (size_t index = 0; index < 6; ++index) {
            uint16_t vertex_index = frame->shadow_indices[quad_index * 6 + index];
            if (vertex_index >= WALLE_LG_SHADOW_VERTEX_COUNT)
                return false;
            expanded[index] = frame->shadow_vertices[vertex_index];
        }
        if (!positive_raster_extent(expanded))
            continue;
        struct runtime_quad quad;
        uint32_t            selector;
        int                 selector_exponent;
        if (!runtime_quad_from_vertices(expanded, &quad)
            || !natural_shadow_selector(&quad, calibration, &selector, &selector_exponent)) {
            return false;
        }
        size_t table_offset = quad_index * 2u * WALLE_LG_SHADOW_COEFFICIENT_TILE_COUNT
                              * WALLE_LG_RASTER_CHANNEL_COUNT;
        int32_t  generated_start;
        uint32_t generated_count;
        if (!coefficient_table(&quad,
                               selector,
                               selector_exponent,
                               0,
                               WALLE_LG_SHADOW_COEFFICIENT_TILE_COUNT,
                               false,
                               &generated_start,
                               &generated_count,
                               &tables->shadow_coefficients[table_offset])
            || generated_start != 0 || generated_count != WALLE_LG_SHADOW_COEFFICIENT_TILE_COUNT) {
            return false;
        }
        for (size_t channel = 0; channel < WALLE_LG_RASTER_CHANNEL_COUNT; ++channel) {
            if (!determinant_slope_bits(
                    &quad,
                    channel,
                    selector,
                    selector_exponent,
                    &tables->shadow_slopes[quad_index * WALLE_LG_RASTER_CHANNEL_COUNT + channel])) {
                return false;
            }
        }
    }
    return true;
}

static float vertex_component(const struct walle_lg_vertex* vertex, size_t component)
{
    if (component < 4)
        return vertex->position[component];
    if (component < 6)
        return vertex->sdf[component - 4];
    return vertex->source[component - 6];
}

static void replace_source_with_sdf(struct walle_lg_vertex* vertex)
{
    vertex->source[0] = vertex->sdf[0];
    vertex->source[1] = vertex->sdf[1];
}

static bool highlight_back_facing(const struct walle_lg_transition_frame* frame, bool* result)
{
    bool negative = true, positive = true;
    for (uint32_t triangle = 0; triangle < frame->highlight_index_count / 3; ++triangle) {
        const struct walle_lg_vertex* first
            = &frame->highlight_vertices[frame->highlight_indices[triangle * 3]];
        const struct walle_lg_vertex* second
            = &frame->highlight_vertices[frame->highlight_indices[triangle * 3 + 1]];
        const struct walle_lg_vertex* third
            = &frame->highlight_vertices[frame->highlight_indices[triangle * 3 + 2]];
        float left_x  = subtract_f32(second->position[0], first->position[0]);
        float left_y  = subtract_f32(second->position[1], first->position[1]);
        float right_x = subtract_f32(third->position[0], first->position[0]);
        float right_y = subtract_f32(third->position[1], first->position[1]);
        float area    = subtract_f32(multiply_f32(left_x, right_y), multiply_f32(left_y, right_x));
        negative &= area < 0.0f;
        positive &= area > 0.0f;
    }
    if (!negative && !positive)
        return false;
    *result = negative;
    return true;
}

static bool copy_compact_axis(uint32_t*       destination,
                              uint32_t        axis_extent,
                              const uint32_t* source,
                              int32_t         source_start,
                              uint32_t        source_count)
{
    if (source_start < 0 || (uint64_t)(uint32_t)source_start + source_count > axis_extent)
        return false;
    for (size_t primitive = 0; primitive < WALLE_LG_RASTER_PRIMITIVE_COUNT; ++primitive) {
        size_t source_offset = primitive * (size_t)source_count * WALLE_LG_RASTER_CHANNEL_COUNT;
        size_t destination_offset = (primitive * (size_t)axis_extent + (uint32_t)source_start)
                                    * WALLE_LG_RASTER_CHANNEL_COUNT;
        memcpy(&destination[destination_offset],
               &source[source_offset],
               (size_t)source_count * WALLE_LG_RASTER_CHANNEL_COUNT * sizeof(uint32_t));
    }
    return true;
}

static bool construct_compact_highlight(const struct walle_lg_transition_frame*   frame,
                                        uint32_t                                  axis_extent,
                                        const struct walle_lg_raster_calibration* calibration,
                                        struct walle_lg_raster_tables*            tables)
{
    if (tables->highlight_back_facing)
        return true;
    struct walle_lg_vertex expanded[6];
    for (size_t index = 0; index < 6; ++index) {
        uint16_t source_index = frame->highlight_indices[index];
        if (source_index >= frame->highlight_vertex_count)
            return false;
        expanded[index] = frame->highlight_vertices[source_index];
        replace_source_with_sdf(&expanded[index]);
    }
    struct runtime_quad quad;
    uint32_t            selector;
    int                 selector_exponent;
    if (!runtime_quad_from_vertices(expanded, &quad)
        || !calibrated_square_or_near_selector(&quad, calibration, &selector, &selector_exponent)) {
        return false;
    }
    int32_t bounds[4];
    if (!visible_bounds(&quad.raster, bounds))
        return false;
    int32_t start = bounds[0] < bounds[1] ? bounds[0] : bounds[1];
    int32_t end   = bounds[2] > bounds[3] ? bounds[2] : bounds[3];
    if (end <= start)
        return false;
    uint32_t  count = (uint32_t)(end - start);
    size_t    word_count_value;
    uint32_t* compact = nullptr;
    if (!word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                    count,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &word_count_value)
        || !allocate_words(word_count_value, &compact)
        || !axis_table(&quad,
                       selector,
                       selector_exponent,
                       true,
                       0,
                       WALLE_LG_RASTER_CHANNEL_COUNT,
                       &start,
                       &count,
                       compact)
        || !copy_compact_axis(tables->highlight_axis, axis_extent, compact, start, count)) {
        free(compact);
        return false;
    }
    free(compact);
    return true;
}

static bool construct_border_highlight(const struct walle_lg_transition_frame*   frame,
                                       uint32_t                                  axis_extent,
                                       const struct walle_lg_raster_calibration* calibration,
                                       struct walle_lg_raster_tables*            tables)
{
    for (size_t quad_index = 0; quad_index < 4; ++quad_index) {
        struct walle_lg_vertex expanded[6];
        for (size_t index = 0; index < 6; ++index) {
            uint16_t source_index = frame->highlight_indices[quad_index * 6 + index];
            if (source_index >= frame->highlight_vertex_count)
                return false;
            expanded[index] = frame->highlight_vertices[source_index];
            replace_source_with_sdf(&expanded[index]);
        }
        struct runtime_quad quad;
        uint32_t            selector;
        int                 selector_exponent;
        if (!runtime_quad_from_vertices(expanded, &quad)
            || !base_selector(&quad, calibration, &selector, &selector_exponent)) {
            return false;
        }
        int32_t bounds[4];
        if (!visible_bounds(&quad.raster, bounds))
            return false;
        int32_t start = bounds[0] < bounds[1] ? bounds[0] : bounds[1];
        int32_t end   = bounds[2] > bounds[3] ? bounds[2] : bounds[3];
        if (start < 0 || end <= start || (uint32_t)end > axis_extent)
            return false;
        uint32_t  count = (uint32_t)(end - start);
        size_t    compact_word_count;
        uint32_t* compact = nullptr;
        if (!word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                        count,
                        WALLE_LG_RASTER_CHANNEL_COUNT,
                        &compact_word_count)
            || !allocate_words(compact_word_count, &compact)
            || !axis_table(&quad,
                           selector,
                           selector_exponent,
                           true,
                           0,
                           WALLE_LG_RASTER_CHANNEL_COUNT,
                           &start,
                           &count,
                           compact)) {
            free(compact);
            return false;
        }
        size_t row_start = quad_index * WALLE_LG_RASTER_PRIMITIVE_COUNT;
        for (size_t component = 0; component < 2; ++component) {
            uint8_t axis  = quad.channel_axis[component];
            int32_t lower = bounds[axis];
            int32_t upper = bounds[axis + 2];
            if (lower < 0 || upper <= lower || (uint32_t)upper > axis_extent || lower < start
                || upper > start + (int32_t)count) {
                free(compact);
                return false;
            }
            for (size_t primitive = 0; primitive < WALLE_LG_RASTER_PRIMITIVE_COUNT; ++primitive) {
                for (int32_t coordinate = lower; coordinate < upper; ++coordinate) {
                    size_t source_offset
                        = (primitive * (size_t)count + (uint32_t)(coordinate - start))
                              * WALLE_LG_RASTER_CHANNEL_COUNT
                          + component;
                    size_t destination_offset
                        = ((row_start + primitive) * (size_t)axis_extent + (uint32_t)coordinate)
                              * WALLE_LG_RASTER_CHANNEL_COUNT
                          + component;
                    tables->highlight_axis[destination_offset] = compact[source_offset];
                }
            }
        }
        free(compact);
    }
    return true;
}

static bool construct_highlight(const struct walle_lg_transition_frame*   frame,
                                uint32_t                                  axis_extent,
                                const struct walle_lg_raster_calibration* calibration,
                                struct walle_lg_raster_tables*            tables)
{
    if (frame->highlight_index_count != 6 && frame->highlight_index_count != 24)
        return false;
    for (uint32_t index = 0; index < frame->highlight_index_count; ++index) {
        if (frame->highlight_indices[index] >= frame->highlight_vertex_count)
            return false;
    }
    if (!highlight_back_facing(frame, &tables->highlight_back_facing))
        return false;
    tables->highlight_axis_rows = frame->highlight_index_count == 24 ? 8 : 2;
    if (!word_count(tables->highlight_axis_rows,
                    axis_extent,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &tables->highlight_axis_word_count)
        || !allocate_words(tables->highlight_axis_word_count, &tables->highlight_axis)) {
        return false;
    }
    return frame->highlight_index_count == 6
               ? construct_compact_highlight(frame, axis_extent, calibration, tables)
               : construct_border_highlight(frame, axis_extent, calibration, tables);
}

bool walle_lg_raster_tables_construct(const struct walle_lg_transition_frame*   frame,
                                      uint32_t                                  target_width,
                                      uint32_t                                  target_height,
                                      const struct walle_lg_raster_calibration* calibration,
                                      struct walle_lg_raster_tables*            result)
{
    bool p25 = calibration != nullptr && calibration->p25_ceil_bits != nullptr
               && calibration->p25_selector_bit_count == P25_KEY_UPPER - P25_KEY_LOWER;
    bool legacy = calibration != nullptr && calibration->base_selectors != nullptr
                  && calibration->base_selector_count != 0
                  && calibration->square_selectors != nullptr
                  && calibration->square_selector_count != 0
                  && calibration->near_square_selectors != nullptr
                  && calibration->near_square_selector_count != 0
                  && calibration->natural_shadow_cases != nullptr
                  && calibration->natural_shadow_selectors != nullptr
                  && calibration->natural_shadow_count != 0;
    if (frame == nullptr || calibration == nullptr || result == nullptr || target_width == 0
        || target_height == 0 || (!p25 && !legacy)) {
        return false;
    }
    struct walle_lg_raster_tables tables = {
        .axis_extent = target_width > target_height ? target_width : target_height,
    };
    if (!construct_main(frame, tables.axis_extent, calibration, &tables)
        || !construct_shadow(frame, calibration, &tables)
        || !construct_highlight(frame, tables.axis_extent, calibration, &tables)) {
        walle_lg_raster_tables_destroy(&tables);
        return false;
    }
    *result = tables;
    return true;
}

static bool producer_quad_construct(const struct walle_lg_transition_frame*   frame,
                                    uint32_t                                  quad_index,
                                    uint32_t                                  source_width,
                                    uint32_t                                  source_height,
                                    const struct walle_lg_raster_calibration* calibration,
                                    struct walle_lg_producer_raster_quad*     result)
{
    constexpr uint16_t                           local_indices[6] = {0, 1, 2, 2, 3, 0};
    const struct walle_lg_dynamic_producer_mesh* mesh             = &frame->producer_mesh;
    uint32_t                                     base             = 4u * quad_index;
    struct walle_lg_vertex                       vertices[6];
    float                                        normalization[2] = {
        round_f32(1.0 / source_width),
        round_f32(1.0 / source_height),
    };
    float producer_origin[2] = {
        round_f32(frame->producer.origin[0]),
        round_f32(frame->producer.origin[1]),
    };

    for (size_t index = 0; index < 6; ++index) {
        if (mesh->indices[6u * quad_index + index] != base + local_indices[index])
            return false;
        const struct walle_lg_producer_vertex* source
            = &mesh->vertices[base + local_indices[index]];
        float u = multiply_f32(source->source[0], normalization[0]);
        float v = multiply_f32(source->source[1], normalization[1]);
        vertices[index] = (struct walle_lg_vertex){
            .position = {
                subtract_f32(source->position[0], producer_origin[0]),
                subtract_f32(source->position[1], producer_origin[1]),
                0.0f,
                1.0f,
            },
            .sdf    = {u, v},
            .source = {u, v},
        };
    }

    struct runtime_quad quad;
    uint32_t            selector;
    int                 selector_exponent;
    int32_t             axis_start;
    uint32_t            axis_count;
    int32_t             bounds[4];
    if (!runtime_quad_from_vertices(vertices, &quad)
        || !base_selector(&quad, calibration, &selector, &selector_exponent)) {
        return false;
    }
    if (!visible_bounds(&quad.raster, bounds))
        return false;
    int32_t first = bounds[0] < bounds[1] ? bounds[0] : bounds[1];
    int32_t end   = bounds[2] > bounds[3] ? bounds[2] : bounds[3];
    if (end <= first)
        return false;
    size_t    axis_word_count;
    uint32_t* axis_bits = nullptr;
    if (!word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                    (uint32_t)(end - first),
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &axis_word_count)
        || !allocate_words(axis_word_count, &axis_bits)
        || !axis_table(
            &quad,
            selector,
            selector_exponent,
            false,
            0,
            WALLE_LG_RASTER_CHANNEL_COUNT,
            &axis_start,
            &axis_count,
            axis_bits)) {
        free(axis_bits);
        return false;
    }
    *result = (struct walle_lg_producer_raster_quad){
        .origin_fixed       = {quad.raster.origin_x_fixed, quad.raster.origin_y_fixed},
        .extent_fixed       = {quad.raster.width_fixed, quad.raster.height_fixed},
        .visible_bounds     = {bounds[0], bounds[1], bounds[2], bounds[3]},
        .axis_start         = axis_start,
        .axis_count         = axis_count,
        .axis_bits          = axis_bits,
        .ascending_diagonal = quad.ascending_diagonal,
    };
    return true;
}

bool walle_lg_producer_raster_construct(const struct walle_lg_transition_frame*   frame,
                                        uint32_t                                  source_width,
                                        uint32_t                                  source_height,
                                        const struct walle_lg_raster_calibration* calibration,
                                        struct walle_lg_producer_raster*          result)
{
    bool calibrated = calibration != nullptr
                      && ((calibration->p25_ceil_bits != nullptr
                           && calibration->p25_selector_bit_count
                                  == P25_KEY_UPPER - P25_KEY_LOWER)
                          || (calibration->base_selectors != nullptr
                              && calibration->base_selector_count != 0));
    if (frame == nullptr || calibration == nullptr || result == nullptr || source_width == 0
        || source_height == 0 || !calibrated || frame->material != WALLE_LG_MATERIAL_REGULAR
        || frame->producer.storage_extent[0] == 0 || frame->producer.storage_extent[1] == 0
        || frame->producer_mesh.vertex_count == 0 || frame->producer_mesh.vertex_count % 4u != 0
        || frame->producer_mesh.vertex_count > WALLE_LG_PRODUCER_MAX_VERTEX_COUNT
        || frame->producer_mesh.index_count != 6u * (frame->producer_mesh.vertex_count / 4u)) {
        return false;
    }
    struct walle_lg_producer_raster raster = {
        .quad_count = frame->producer_mesh.vertex_count / 4u,
    };
    if (raster.quad_count > WALLE_LG_PRODUCER_MAX_QUAD_COUNT)
        return false;
    for (uint32_t quad = 0; quad < raster.quad_count; ++quad) {
        if (!producer_quad_construct(
                frame, quad, source_width, source_height, calibration, &raster.quads[quad])) {
            walle_lg_producer_raster_destroy(&raster);
            return false;
        }
    }
    *result = raster;
    return true;
}

static double reveal_triangle_area(const struct walle_lg_vertex triangle[static 3])
{
    double ab_x = (double)triangle[1].position[0] - triangle[0].position[0];
    double ab_y = (double)triangle[1].position[1] - triangle[0].position[1];
    double ac_x = (double)triangle[2].position[0] - triangle[0].position[0];
    double ac_y = (double)triangle[2].position[1] - triangle[0].position[1];
    return ab_x * ac_y - ab_y * ac_x;
}

static bool reveal_vertices_valid(const struct walle_lg_vertex* vertices, size_t vertex_count)
{
    int32_t low[2]  = {INT32_MAX, INT32_MAX};
    int32_t high[2] = {INT32_MIN, INT32_MIN};
    for (size_t vertex = 0; vertex < vertex_count; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis) {
            float position = vertices[vertex].position[axis];
            double fixed   = floor((double)position * SUBPIXEL_SCALE + 0.5);
            if (!isfinite(position) || fixed < INT32_MIN || fixed > INT32_MAX
                || !isfinite(vertices[vertex].sdf[axis])) {
                return false;
            }
            int32_t value = (int32_t)fixed;
            if (value < low[axis])
                low[axis] = value;
            if (value > high[axis])
                high[axis] = value;
        }
    }
    return (int64_t)high[0] - low[0] <= INT32_MAX
           && (int64_t)high[1] - low[1] <= INT32_MAX;
}

static bool reveal_group_vertices(const struct walle_lg_reveal_mask_geometry* geometry,
                                  size_t                                      group,
                                  struct walle_lg_vertex                      output[static 6])
{
    bool compact = geometry->family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS;
    for (size_t local = 0; local < 6; ++local) {
        size_t   index = group * 6 + local;
        uint16_t source_index = geometry->indices[index];
        if (source_index >= geometry->vertex_count)
            return false;
        const struct walle_lg_reveal_mask_vertex* source = &geometry->vertices[source_index];
        const float* coordinates
            = compact ? source->first_coordinates : source->second_coordinates;
        memcpy(output[local].position, source->position, sizeof output[local].position);
        memcpy(output[local].sdf, coordinates, sizeof output[local].sdf);
        memcpy(output[local].source, coordinates, sizeof output[local].source);
    }
    return reveal_vertices_valid(output, 6);
}

static bool complete_reveal_quad(const struct walle_lg_vertex triangle[static 3],
                                 struct walle_lg_vertex       output[static 6])
{
    float low[2]  = {triangle[0].position[0], triangle[0].position[1]};
    float high[2] = {triangle[0].position[0], triangle[0].position[1]};
    for (size_t vertex = 1; vertex < 3; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis) {
            if (triangle[vertex].position[axis] < low[axis])
                low[axis] = triangle[vertex].position[axis];
            if (triangle[vertex].position[axis] > high[axis])
                high[axis] = triangle[vertex].position[axis];
        }
    }
    if (low[0] == high[0] || low[1] == high[1])
        return false;

    bool present[2][2] = {};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        size_t x = triangle[vertex].position[0] == high[0] ? 1u : 0u;
        size_t y = triangle[vertex].position[1] == high[1] ? 1u : 0u;
        if ((triangle[vertex].position[0] != low[0] && x == 0)
            || (triangle[vertex].position[1] != low[1] && y == 0) || present[x][y]) {
            return false;
        }
        present[x][y] = true;
    }
    size_t missing_x = 2;
    size_t missing_y = 2;
    for (size_t x = 0; x < 2; ++x) {
        for (size_t y = 0; y < 2; ++y) {
            if (!present[x][y]) {
                missing_x = x;
                missing_y = y;
            }
        }
    }
    if (missing_x > 1 || missing_y > 1)
        return false;

    struct walle_lg_vertex missing = triangle[0];
    missing.position[0]            = missing_x == 0 ? low[0] : high[0];
    missing.position[1]            = missing_y == 0 ? low[1] : high[1];
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        if (triangle[vertex].position[0] == missing.position[0]) {
            missing.sdf[0]    = triangle[vertex].sdf[0];
            missing.source[0] = triangle[vertex].source[0];
        }
        if (triangle[vertex].position[1] == missing.position[1]) {
            missing.sdf[1]    = triangle[vertex].sdf[1];
            missing.source[1] = triangle[vertex].source[1];
        }
    }
    size_t diagonal[2] = {SIZE_MAX, SIZE_MAX};
    for (size_t left = 0; left < 3; ++left) {
        for (size_t right = left + 1; right < 3; ++right) {
            if (triangle[left].position[0] != triangle[right].position[0]
                && triangle[left].position[1] != triangle[right].position[1]) {
                diagonal[0] = left;
                diagonal[1] = right;
            }
        }
    }
    if (diagonal[0] == SIZE_MAX)
        return false;
    memcpy(output, triangle, 3 * sizeof *output);
    output[3] = triangle[diagonal[0]];
    output[4] = missing;
    output[5] = triangle[diagonal[1]];
    return true;
}

static uint8_t reveal_geometric_primitive(const struct runtime_quad*    quad,
                                          const struct walle_lg_vertex triangle[static 3])
{
    int32_t sample_x = (int32_t)(((double)triangle[0].position[0]
                                  + triangle[1].position[0] + triangle[2].position[0])
                                 / 3.0);
    int32_t sample_y = (int32_t)(((double)triangle[0].position[1]
                                  + triangle[1].position[1] + triangle[2].position[1])
                                 / 3.0);
    i128 relative_x = (i128)sample_x * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2
                      - quad->raster.origin_x_fixed;
    i128 relative_y = (i128)sample_y * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2
                      - quad->raster.origin_y_fixed;
    if (quad->ascending_diagonal) {
        return relative_y * quad->raster.width_fixed > relative_x * quad->raster.height_fixed ? 1u
                                                                                              : 0u;
    }
    i128 diagonal = relative_x * quad->raster.height_fixed
                    + relative_y * quad->raster.width_fixed;
    i128 area = (i128)quad->raster.width_fixed * quad->raster.height_fixed;
    return diagonal < area ? 1u : 0u;
}

enum prepared_reveal_owner_status : uint8_t
{
    PREPARED_REVEAL_OWNER_READY = 0,
    PREPARED_REVEAL_OWNER_OFFSCREEN,
    PREPARED_REVEAL_OWNER_UNSUPPORTED,
    PREPARED_REVEAL_OWNER_ARITHMETIC_RANGE,
    PREPARED_REVEAL_OWNER_SETUP_FAILED,
};

struct prepared_reveal_owner
{
    struct runtime_quad runtime;
    uint32_t            selector;
    int                 selector_exponent;
    uint32_t            axis_count;
};

static enum prepared_reveal_owner_status prepare_reveal_owner(
    const struct walle_lg_vertex              vertices[static 6],
    const struct walle_lg_raster_calibration* calibration,
    bool                                      unsupported_runtime_is_skipped,
    struct walle_lg_reveal_raster_quad*       metadata,
    struct prepared_reveal_owner*             prepared)
{
    struct runtime_quad runtime;
    if (!runtime_quad_from_vertices(vertices, &runtime)) {
        return unsupported_runtime_is_skipped ? PREPARED_REVEAL_OWNER_UNSUPPORTED
                                              : PREPARED_REVEAL_OWNER_SETUP_FAILED;
    }
    int32_t bounds[4];
    if (!visible_bounds(&runtime.raster, bounds))
        return PREPARED_REVEAL_OWNER_ARITHMETIC_RANGE;
    if (bounds[2] <= bounds[0] || bounds[3] <= bounds[1])
        return PREPARED_REVEAL_OWNER_OFFSCREEN;

    int32_t lower = bounds[0] < bounds[1] ? bounds[0] : bounds[1];
    int32_t upper = bounds[2] > bounds[3] ? bounds[2] : bounds[3];
    int64_t first = (int64_t)lower - 1;
    int64_t end   = (int64_t)upper + 1;
    if (first < INT32_MIN || end > INT32_MAX || end <= first || end - first > UINT32_MAX)
        return PREPARED_REVEAL_OWNER_ARITHMETIC_RANGE;

    uint32_t selector;
    int      selector_exponent;
    if (!base_selector(&runtime, calibration, &selector, &selector_exponent))
        return PREPARED_REVEAL_OWNER_SETUP_FAILED;

    *metadata = (struct walle_lg_reveal_raster_quad){
        .origin_fixed          = {runtime.raster.origin_x_fixed, runtime.raster.origin_y_fixed},
        .extent_fixed          = {runtime.raster.width_fixed, runtime.raster.height_fixed},
        .visible_bounds        = {bounds[0], bounds[1], bounds[2], bounds[3]},
        .axis_start            = (int32_t)first,
        .ascending_diagonal    = runtime.ascending_diagonal,
        .active_primitive_mask = 0,
    };
    *prepared = (struct prepared_reveal_owner){
        .runtime           = runtime,
        .selector          = selector,
        .selector_exponent = selector_exponent,
        .axis_count        = (uint32_t)(end - first),
    };
    return PREPARED_REVEAL_OWNER_READY;
}

static enum walle_lg_reveal_raster_status
postguard_raster_status(enum walle_lg_postguard_status status)
{
    switch (status) {
        case WALLE_LG_POSTGUARD_OK:
            return WALLE_LG_REVEAL_RASTER_OK;
        case WALLE_LG_POSTGUARD_INVALID_ARGUMENT:
            return WALLE_LG_REVEAL_RASTER_INVALID_ARGUMENT;
        case WALLE_LG_POSTGUARD_INVALID_GEOMETRY:
            return WALLE_LG_REVEAL_RASTER_INVALID_GEOMETRY;
        case WALLE_LG_POSTGUARD_ARITHMETIC_RANGE:
            return WALLE_LG_REVEAL_RASTER_ARITHMETIC_RANGE;
        case WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED:
            return WALLE_LG_REVEAL_RASTER_CAPACITY_EXCEEDED;
    }
    return WALLE_LG_REVEAL_RASTER_SETUP_FAILED;
}

static void postguard_vertex(const struct walle_lg_postguard_vertex* source,
                             struct walle_lg_vertex*                 result)
{
    *result = (struct walle_lg_vertex){
        .position = {bits_float(source->component_bits[0]), bits_float(source->component_bits[1])},
        .sdf      = {bits_float(source->component_bits[4]), bits_float(source->component_bits[5])},
        .source   = {bits_float(source->component_bits[4]), bits_float(source->component_bits[5])},
    };
}

static enum prepared_reveal_owner_status reveal_triangle_target_status(
    const struct walle_lg_vertex triangle[static 3],
    uint32_t                     target_width,
    uint32_t                     target_height)
{
    int32_t low[2]  = {INT32_MAX, INT32_MAX};
    int32_t high[2] = {INT32_MIN, INT32_MIN};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis) {
            int32_t fixed = subpixel_fixed(triangle[vertex].position[axis]);
            if (fixed < low[axis])
                low[axis] = fixed;
            if (fixed > high[axis])
                high[axis] = fixed;
        }
    }
    if (high[0] <= low[0] || high[1] <= low[1])
        return PREPARED_REVEAL_OWNER_UNSUPPORTED;
    struct raster_case bounds_case = {
        .origin_x_fixed = low[0],
        .origin_y_fixed = low[1],
        .width_fixed    = high[0] - low[0],
        .height_fixed   = high[1] - low[1],
    };
    int32_t bounds[4];
    if (!visible_bounds(&bounds_case, bounds))
        return PREPARED_REVEAL_OWNER_ARITHMETIC_RANGE;
    if (bounds[2] <= bounds[0] || bounds[3] <= bounds[1] || bounds[2] <= 0 || bounds[3] <= 0
        || (int64_t)bounds[0] >= target_width || (int64_t)bounds[1] >= target_height) {
        return PREPARED_REVEAL_OWNER_OFFSCREEN;
    }
    return PREPARED_REVEAL_OWNER_READY;
}

static void reveal_owner_block_store(struct walle_lg_reveal_raster* raster, size_t slot)
{
    const struct walle_lg_reveal_raster_quad* owner = &raster->owners[slot];
    memcpy(raster->owner_block.bounds[slot], owner->visible_bounds, sizeof owner->visible_bounds);
    raster->owner_block.origin_extent[slot][0] = owner->origin_fixed[0];
    raster->owner_block.origin_extent[slot][1] = owner->origin_fixed[1];
    raster->owner_block.origin_extent[slot][2] = owner->extent_fixed[0];
    raster->owner_block.origin_extent[slot][3] = owner->extent_fixed[1];
    raster->owner_block.control[slot][0]       = owner->axis_start;
    raster->owner_block.control[slot][1]       = owner->ascending_diagonal;
    raster->owner_block.control[slot][2]       = owner->active_primitive_mask;
    raster->owner_block.control[slot][3]
        = slot < raster->base_owner_count ? 0 : WALLE_LG_POSTGUARD_CHILD_SCOPED_CENTER_FALLBACK;
}

enum walle_lg_reveal_raster_status
walle_lg_reveal_raster_construct(const struct walle_lg_reveal_mask_geometry* geometry,
                                 uint32_t                                    target_width,
                                 uint32_t                                    target_height,
                                 const struct walle_lg_raster_calibration*   calibration,
                                 struct walle_lg_reveal_raster*              result)
{
    static_assert(WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT <= UINT8_MAX);
    static_assert(WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT <= UINT8_MAX);
    if (geometry == nullptr || calibration == nullptr || result == nullptr || target_width == 0
        || target_height == 0 || calibration->p25_ceil_bits == nullptr
        || calibration->p25_selector_bit_count != P25_KEY_UPPER - P25_KEY_LOWER) {
        return WALLE_LG_REVEAL_RASTER_INVALID_ARGUMENT;
    }
    if (geometry->vertex_count > WALLE_LG_REVEAL_MAX_VERTEX_COUNT
        || geometry->index_count > WALLE_LG_REVEAL_MAX_INDEX_COUNT
        || geometry->index_count % 6u != 0
        || (geometry->family != WALLE_LG_REVEAL_MASK_EMPTY
            && geometry->family != WALLE_LG_REVEAL_MASK_BORDER_GRID
            && geometry->family != WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS)
        || (geometry->family == WALLE_LG_REVEAL_MASK_EMPTY
            && (geometry->vertex_count != 0 || geometry->index_count != 0))) {
        return WALLE_LG_REVEAL_RASTER_INVALID_GEOMETRY;
    }

    enum walle_lg_reveal_raster_status status = WALLE_LG_REVEAL_RASTER_OK;
    struct walle_lg_reveal_raster      raster = {
        .original_primitive_count = geometry->index_count / 3u,
    };
    struct prepared_reveal_owner prepared[WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT] = {};
    uint32_t*                    scratch = nullptr;
    for (size_t primitive = 0; primitive < WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT;
         ++primitive) {
        raster.primitives[primitive] = (struct walle_lg_reveal_raster_primitive){
            .packed_slot         = WALLE_LG_REVEAL_RASTER_INVALID_MAPPING,
            .geometric_primitive = WALLE_LG_REVEAL_RASTER_INVALID_MAPPING,
        };
    }

    size_t group_count = geometry->index_count / 6u;
    for (size_t group = 0; group < group_count; ++group) {
        struct walle_lg_vertex original[6];
        struct walle_lg_vertex completed[6];
        if (!reveal_group_vertices(geometry, group, original)) {
            status = WALLE_LG_REVEAL_RASTER_INVALID_GEOMETRY;
            goto failure;
        }
        bool active[2] = {
            reveal_triangle_area(original) != 0.0,
            reveal_triangle_area(original + 3) != 0.0,
        };
        if (!active[0] && !active[1])
            continue;
        if (active[0] && active[1]) {
            memcpy(completed, original, sizeof completed);
        } else {
            const struct walle_lg_vertex* triangle = active[0] ? original : original + 3;
            if (!complete_reveal_quad(triangle, completed)) {
                status = WALLE_LG_REVEAL_RASTER_SETUP_FAILED;
                goto failure;
            }
        }
        if (!reveal_vertices_valid(completed, 6)) {
            status = WALLE_LG_REVEAL_RASTER_INVALID_GEOMETRY;
            goto failure;
        }
        struct walle_lg_reveal_raster_quad* metadata = &raster.owners[raster.owner_count];
        enum prepared_reveal_owner_status prepare_status = prepare_reveal_owner(
            completed, calibration, false, metadata, &prepared[raster.owner_count]);
        if (prepare_status == PREPARED_REVEAL_OWNER_OFFSCREEN)
            continue;
        if (raster.owner_count >= WALLE_LG_REVEAL_RASTER_MAX_BASE_OWNER_COUNT) {
            status = WALLE_LG_REVEAL_RASTER_CAPACITY_EXCEEDED;
            goto failure;
        }
        if (prepare_status != PREPARED_REVEAL_OWNER_READY) {
            status = prepare_status == PREPARED_REVEAL_OWNER_ARITHMETIC_RANGE
                         ? WALLE_LG_REVEAL_RASTER_ARITHMETIC_RANGE
                         : WALLE_LG_REVEAL_RASTER_SETUP_FAILED;
            goto failure;
        }
        for (size_t ordinal = 0; ordinal < 2; ++ordinal) {
            if (!active[ordinal])
                continue;
            uint8_t geometric
                = reveal_geometric_primitive(&prepared[raster.owner_count].runtime,
                                             original + ordinal * 3);
            metadata->active_primitive_mask |= (uint8_t)(1u << geometric);
            raster.primitives[group * 2 + ordinal]
                = (struct walle_lg_reveal_raster_primitive){
                    .packed_slot         = (uint8_t)raster.owner_count,
                    .geometric_primitive = geometric,
                };
        }
        if (prepared[raster.owner_count].axis_count > raster.packed_width)
            raster.packed_width = prepared[raster.owner_count].axis_count;
        ++raster.owner_count;
    }
    raster.base_owner_count = raster.owner_count;

    struct walle_lg_postguard_children children;
    uint32_t target_extent[2] = {target_width, target_height};
    enum walle_lg_postguard_status postguard_status
        = walle_lg_postguard_children_construct(geometry, target_extent, &children);
    if (postguard_status != WALLE_LG_POSTGUARD_OK) {
        status = postguard_raster_status(postguard_status);
        goto failure;
    }
    raster.postguard_child_count = children.child_count;
    for (size_t child_index = 0; child_index < children.child_count; ++child_index) {
        const struct walle_lg_postguard_child* child = &children.children[child_index];
        if (child->source_primitive >= raster.original_primitive_count
            || child->owner_policy != WALLE_LG_POSTGUARD_CHILD_SCOPED_CENTER_FALLBACK) {
            status = WALLE_LG_REVEAL_RASTER_INVALID_GEOMETRY;
            goto failure;
        }
        if (raster.owner_count >= WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT) {
            status = WALLE_LG_REVEAL_RASTER_CAPACITY_EXCEEDED;
            goto failure;
        }

        struct walle_lg_vertex triangle[3];
        struct walle_lg_vertex completed[6];
        for (size_t vertex = 0; vertex < 3; ++vertex)
            postguard_vertex(&child->vertices[vertex], &triangle[vertex]);
        if (!reveal_vertices_valid(triangle, 3)) {
            status = WALLE_LG_REVEAL_RASTER_INVALID_GEOMETRY;
            goto failure;
        }
        enum prepared_reveal_owner_status target_status
            = reveal_triangle_target_status(triangle, target_width, target_height);
        if (target_status == PREPARED_REVEAL_OWNER_OFFSCREEN) {
            ++raster.offscreen_postguard_child_count;
            continue;
        }
        if (target_status == PREPARED_REVEAL_OWNER_UNSUPPORTED) {
            ++raster.unsupported_postguard_child_count;
            continue;
        }
        if (target_status != PREPARED_REVEAL_OWNER_READY) {
            status = WALLE_LG_REVEAL_RASTER_ARITHMETIC_RANGE;
            goto failure;
        }
        if (!complete_reveal_quad(triangle, completed)
            || !reveal_vertices_valid(completed, 6)) {
            ++raster.unsupported_postguard_child_count;
            continue;
        }

        struct walle_lg_reveal_raster_quad* metadata = &raster.owners[raster.owner_count];
        enum prepared_reveal_owner_status prepare_status = prepare_reveal_owner(
            completed, calibration, true, metadata, &prepared[raster.owner_count]);
        if (prepare_status == PREPARED_REVEAL_OWNER_OFFSCREEN) {
            ++raster.offscreen_postguard_child_count;
            continue;
        }
        if (prepare_status == PREPARED_REVEAL_OWNER_UNSUPPORTED) {
            ++raster.unsupported_postguard_child_count;
            continue;
        }
        if (prepare_status != PREPARED_REVEAL_OWNER_READY) {
            status = prepare_status == PREPARED_REVEAL_OWNER_ARITHMETIC_RANGE
                         ? WALLE_LG_REVEAL_RASTER_ARITHMETIC_RANGE
                         : WALLE_LG_REVEAL_RASTER_SETUP_FAILED;
            goto failure;
        }
        uint8_t geometric
            = reveal_geometric_primitive(&prepared[raster.owner_count].runtime, triangle);
        metadata->active_primitive_mask = (uint8_t)(1u << geometric);
        if (prepared[raster.owner_count].axis_count > raster.packed_width)
            raster.packed_width = prepared[raster.owner_count].axis_count;
        ++raster.owner_count;
        ++raster.supported_postguard_child_count;
    }

    raster.owner_block.counts[0] = (int32_t)raster.owner_count;
    raster.owner_block.counts[1] = (int32_t)raster.base_owner_count;
    for (size_t slot = 0; slot < raster.owner_count; ++slot)
        reveal_owner_block_store(&raster, slot);
    if (raster.owner_count == 0) {
        *result = raster;
        return WALLE_LG_REVEAL_RASTER_OK;
    }

    size_t row_count;
    if (ckd_mul(&row_count, (size_t)raster.owner_count, WALLE_LG_RASTER_PRIMITIVE_COUNT)
        || !word_count(row_count,
                       raster.packed_width,
                       WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT,
                       &raster.packed_word_count)) {
        status = WALLE_LG_REVEAL_RASTER_ARITHMETIC_RANGE;
        goto failure;
    }
    if (!allocate_words(raster.packed_word_count, &raster.packed_words)
        || !allocate_words(raster.packed_width, &scratch)) {
        status = WALLE_LG_REVEAL_RASTER_ALLOCATION_FAILED;
        goto failure;
    }
    for (size_t slot = 0; slot < raster.owner_count; ++slot) {
        const struct prepared_reveal_owner* owner = &prepared[slot];
        for (size_t primitive = 0; primitive < WALLE_LG_RASTER_PRIMITIVE_COUNT; ++primitive) {
            if ((raster.owners[slot].active_primitive_mask & (uint8_t)(1u << primitive)) == 0)
                continue;
            size_t row = slot * WALLE_LG_RASTER_PRIMITIVE_COUNT + primitive;
            for (size_t channel = 0; channel < WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT; ++channel) {
                if (!axis_values(&owner->runtime,
                                 channel,
                                 primitive,
                                 raster.owners[slot].axis_start,
                                 owner->axis_count,
                                 false,
                                 owner->selector,
                                 owner->selector_exponent,
                                 scratch)) {
                    status = WALLE_LG_REVEAL_RASTER_SETUP_FAILED;
                    goto failure;
                }
                for (size_t offset = 0; offset < owner->axis_count; ++offset) {
                    size_t destination
                        = (row * (size_t)raster.packed_width + offset)
                              * WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT
                          + channel;
                    raster.packed_words[destination] = scratch[offset];
                }
            }
        }
    }
    free(scratch);
    *result = raster;
    return WALLE_LG_REVEAL_RASTER_OK;

failure:
    free(scratch);
    free(raster.packed_words);
    return status;
}

bool walle_lg_producer_raster_coordinates(const struct walle_lg_producer_raster_quad* quad,
                                          int32_t                                     x,
                                          int32_t                                     y,
                                          float result[static 2])
{
    if (quad == nullptr || quad->axis_bits == nullptr || quad->axis_count == 0
        || x < quad->visible_bounds[0] || x >= quad->visible_bounds[2]
        || y < quad->visible_bounds[1] || y >= quad->visible_bounds[3] || x < quad->axis_start
        || y < quad->axis_start || (uint32_t)(x - quad->axis_start) >= quad->axis_count
        || (uint32_t)(y - quad->axis_start) >= quad->axis_count) {
        return false;
    }
    int64_t  relative_x = (int64_t)x * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2 - quad->origin_fixed[0];
    int64_t  relative_y = (int64_t)y * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2 - quad->origin_fixed[1];
    uint32_t primitive;
    if (quad->ascending_diagonal) {
        primitive = relative_y * quad->extent_fixed[0] > relative_x * quad->extent_fixed[1];
    } else {
        int64_t diagonal = relative_x * quad->extent_fixed[1] + relative_y * quad->extent_fixed[0];
        int64_t area     = (int64_t)quad->extent_fixed[0] * quad->extent_fixed[1];
        primitive        = diagonal < area;
    }
    size_t x_index = (primitive * (size_t)quad->axis_count + (uint32_t)(x - quad->axis_start))
                     * WALLE_LG_RASTER_CHANNEL_COUNT;
    size_t y_index = (primitive * (size_t)quad->axis_count + (uint32_t)(y - quad->axis_start))
                     * WALLE_LG_RASTER_CHANNEL_COUNT;
    result[0] = bits_float(quad->axis_bits[x_index]);
    result[1] = bits_float(quad->axis_bits[y_index + 1u]);
    return true;
}

void walle_lg_producer_raster_destroy(struct walle_lg_producer_raster* raster)
{
    if (raster == nullptr)
        return;
    for (uint32_t quad = 0; quad < raster->quad_count; ++quad)
        free(raster->quads[quad].axis_bits);
    *raster = (struct walle_lg_producer_raster){};
}

void walle_lg_reveal_raster_destroy(struct walle_lg_reveal_raster* raster)
{
    if (raster == nullptr)
        return;
    free(raster->packed_words);
    *raster = (struct walle_lg_reveal_raster){};
}

void walle_lg_raster_tables_destroy(struct walle_lg_raster_tables* tables)
{
    if (tables == nullptr)
        return;
    free(tables->coefficients);
    free(tables->main_axis);
    free(tables->shadow_coefficients);
    free(tables->shadow_slopes);
    free(tables->highlight_axis);
    *tables = (struct walle_lg_raster_tables){};
}

/* ============================================================================
 * General per-triangle post-guard child setup (M1-measured laws; see the
 * lg-test ledger "production-children capture campaign").  The analysis
 * probe carries an identical copy of this pipeline; the process-capture
 * gate plus the probe's hardware-word comparisons keep them honest.
 * ==========================================================================*/

static int64_t wlg_triangle_determinant(const int32_t fixed[3][2])
{
    return (int64_t)(fixed[1][0] - fixed[0][0]) * (fixed[2][1] - fixed[0][1])
           - (int64_t)(fixed[1][1] - fixed[0][1]) * (fixed[2][0] - fixed[0][0]);
}

static size_t wlg_top_left_anchor(const int32_t fixed[3][2])
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

static void wlg_child_edges(const int32_t fixed[3][2], int32_t edges[2][3])
{
    edges[0][0] = fixed[1][1] - fixed[2][1];
    edges[0][1] = fixed[2][1] - fixed[0][1];
    edges[0][2] = fixed[0][1] - fixed[1][1];
    edges[1][0] = fixed[2][0] - fixed[1][0];
    edges[1][1] = fixed[0][0] - fixed[2][0];
    edges[1][2] = fixed[1][0] - fixed[0][0];
}

#include "liquid_glass_reveal_hw_constants.h"

static const struct wlg_hw_child* wlg_hw_lookup(uint32_t      radius_bits,
                                                const int32_t fixed[3][2])
{
    for (size_t i = 0; i < WLG_HW_CHILD_COUNT; ++i) {
        const struct wlg_hw_child* c = &wlg_hw_children[i];
        if (c->radius_bits != radius_bits)
            continue;
        bool match = true;
        for (size_t v = 0; v < 3 && match; ++v)
            match = c->fixed[v][0] == fixed[v][0] && c->fixed[v][1] == fixed[v][1];
        if (match)
            return c;
    }
    return nullptr;
}

struct wlg_child_setup
{
    int32_t  fixed[3][2];
    float    sdf[3][2];
    int64_t  determinant;
    size_t   anchor;
    uint32_t selector;
    int      selector_exponent;
    int      numerator_sign[2][2];
    uint64_t numerator_index[2][2];
    int      numerator_exponent[2][2];
};

static bool wlg_child_numerator(const struct wlg_child_setup* setup,
                                size_t                        channel,
                                size_t                        axis,
                                int*                          result_sign,
                                uint64_t*                     result_index,
                                int*                          result_exponent)
{
    int32_t edges[2][3];
    wlg_child_edges(setup->fixed, edges);
    float anchor_value = setup->sdf[setup->anchor][channel];

    struct dyadic total = {};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        if (vertex == setup->anchor)
            continue;
        float delta = subtract_f32(setup->sdf[vertex][channel], anchor_value);
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
        if (!general_product_stage(delta_index,
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
    }
    if (total.numerator == 0) {
        *result_sign     = 0;
        *result_index    = 0;
        *result_exponent = 0;
        return true;
    }
    /* M1-measured (word-sweep 912ef3eb..., join-isolation ce559c7f...,
     * residual-states 188/188): the joined numerator is kept at 28 bits,
     * rounded to nearest, ties to even.  Every downstream consumer taps
     * this single representation. */
    struct dyadic normalized;
    if (!quantize_significand(total, 28, &normalized))
        return false;
    *result_sign     = normalized.numerator < 0 ? -1 : 1;
    *result_index    = (uint64_t)magnitude_i128(normalized.numerator);
    *result_exponent = normalized.exponent;
    return true;
}

static bool wlg_child_prepare(const struct walle_lg_vertex              vertices[static 3],
                              const struct walle_lg_raster_calibration* calibration,
                              struct wlg_child_setup*                   setup,
                              struct walle_lg_reveal_general_child*     child)
{
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis)
            setup->fixed[vertex][axis] = subpixel_fixed(vertices[vertex].position[axis]);
        setup->sdf[vertex][0] = vertices[vertex].sdf[0];
        setup->sdf[vertex][1] = vertices[vertex].sdf[1];
    }
    setup->determinant = wlg_triangle_determinant(setup->fixed);
    if (setup->determinant == 0)
        return false;
    setup->anchor = wlg_top_left_anchor(setup->fixed);

    uint64_t determinant = setup->determinant < 0 ? (uint64_t)(-setup->determinant)
                                                  : (uint64_t)setup->determinant;
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
    setup->selector_exponent = -(int)bit_length_u64(determinant - 1) - 8;
    if ((determinant & (determinant - 1)) == 0 || key == P25_KEY_UPPER) {
        setup->selector = UINT32_C(1) << 24;
    } else if (key < P25_KEY_LOWER || key >= P25_KEY_UPPER) {
        return false;
    } else {
        uint64_t bit_index = key - P25_KEY_LOWER;
        bool     ceil
            = (((uint32_t)calibration->p25_ceil_bits[bit_index >> 3] >> (bit_index & 7u)) & 1u)
              != 0;
        uint64_t floor  = P25_RECIPROCAL / key;
        setup->selector = (uint32_t)(floor + (ceil && P25_RECIPROCAL % key != 0 ? 1u : 0u));
    }

    for (size_t channel = 0; channel < 2; ++channel) {
        for (size_t axis = 0; axis < 2; ++axis) {
            int      sign;
            uint64_t numerator;
            int      exponent;
            if (!wlg_child_numerator(setup, channel, axis, &sign, &numerator, &exponent))
                return false;
            setup->numerator_sign[channel][axis]     = sign;
            setup->numerator_index[channel][axis]    = numerator;
            setup->numerator_exponent[channel][axis] = exponent;
            if (sign == 0) {
                child->slope_bits[channel][axis] = 0;
                continue;
            }
            uint64_t coefficient;
            int      coefficient_exponent;
            if (!selector_product_stage(numerator,
                                        exponent,
                                        setup->selector,
                                        setup->selector_exponent,
                                        &coefficient,
                                        &coefficient_exponent)) {
                return false;
            }
            int slope_sign = setup->determinant < 0 ? -sign : sign;
            child->slope_bits[channel][axis] = float_bits(
                round_f32(ldexp((double)(slope_sign * (int64_t)coefficient),
                                coefficient_exponent)));
        }
    }

    for (size_t vertex = 0; vertex < 3; ++vertex) {
        child->fixed[vertex][0] = setup->fixed[vertex][0];
        child->fixed[vertex][1] = setup->fixed[vertex][1];
    }
    child->det_sign = setup->determinant < 0 ? -1 : 1;

    int32_t low[2]  = {INT32_MAX, INT32_MAX};
    int32_t high[2] = {INT32_MIN, INT32_MIN};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis) {
            if (setup->fixed[vertex][axis] < low[axis])
                low[axis] = setup->fixed[vertex][axis];
            if (setup->fixed[vertex][axis] > high[axis])
                high[axis] = setup->fixed[vertex][axis];
        }
    }
    struct raster_case bounds_case = {
        .origin_x_fixed = low[0],
        .origin_y_fixed = low[1],
        .width_fixed    = high[0] - low[0],
        .height_fixed   = high[1] - low[1],
    };
    if (!visible_bounds(&bounds_case, child->visible_bounds))
        return false;
    return true;
}

static bool wlg_child_constant_bits(const struct wlg_child_setup* setup,
                                    float                         anchor_sdf,
                                    size_t                        channel,
                                    int32_t                       tile_x,
                                    int32_t                       tile_y,
                                    uint32_t*                     result)
{
    struct dyadic value    = dyadic_from_float_bits(float_bits(anchor_sdf));
    int32_t       tiles[2] = {tile_x, tile_y};

    struct dyadic middle_total = {};
    for (size_t axis = 0; axis < 2; ++axis) {
        int      sign      = setup->numerator_sign[channel][axis];
        uint64_t numerator = setup->numerator_index[channel][axis];
        int      exponent  = setup->numerator_exponent[channel][axis];
        int64_t  displacement
            = (int64_t)tiles[axis] * TILE_SIZE * SUBPIXEL_SCALE - setup->fixed[setup->anchor][axis];
        if (sign == 0 || displacement == 0)
            continue;
        uint64_t distance_index;
        int      distance_exponent;
        float    distance = round_f32((double)llabs(displacement) / SUBPIXEL_SCALE);
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
        if (!dyadic_add(middle_total, term, &middle_total))
            return false;
    }
    if (middle_total.numerator != 0) {
        struct dyadic joined;
        if (!quantize_significand(middle_total, 28, &joined))
            return false;
        int      joined_sign  = joined.numerator < 0 ? -1 : 1;
        uint64_t joined_index = (uint64_t)magnitude_i128(joined.numerator);
        uint64_t coefficient;
        int      coefficient_exponent;
        if (!constant_selector_product_stage(joined_index,
                                             joined.exponent,
                                             setup->selector,
                                             setup->selector_exponent,
                                             &coefficient,
                                             &coefficient_exponent)) {
            return false;
        }
        if (setup->determinant < 0)
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

enum walle_lg_reveal_raster_status
walle_lg_reveal_general_construct(const struct walle_lg_reveal_mask_geometry* geometry,
                                  uint32_t                                    target_width,
                                  uint32_t                                    target_height,
                                  const struct walle_lg_raster_calibration*   calibration,
                                  struct walle_lg_reveal_general*             result)
{
    if (geometry == nullptr || calibration == nullptr || result == nullptr
        || calibration->p25_ceil_bits == nullptr) {
        return WALLE_LG_REVEAL_RASTER_INVALID_ARGUMENT;
    }
    *result = (struct walle_lg_reveal_general){};

    struct walle_lg_postguard_children children;
    uint32_t target_extent[2] = {target_width, target_height};
    if (walle_lg_postguard_children_construct(geometry, target_extent, &children)
        != WALLE_LG_POSTGUARD_OK) {
        return WALLE_LG_REVEAL_RASTER_SETUP_FAILED;
    }

    struct wlg_child_setup setups[WALLE_LG_REVEAL_GENERAL_MAX_CHILD_COUNT];
    size_t                 constant_words = 0;
    for (size_t index = 0; index < children.child_count; ++index) {
        struct walle_lg_vertex triangle[3];
        for (size_t vertex = 0; vertex < 3; ++vertex)
            postguard_vertex(&children.children[index].vertices[vertex], &triangle[vertex]);
        if (!reveal_vertices_valid(triangle, 3))
            continue;
        if (reveal_triangle_target_status(triangle, target_width, target_height)
            != PREPARED_REVEAL_OWNER_READY) {
            continue;
        }
        if (result->child_count >= WALLE_LG_REVEAL_GENERAL_MAX_CHILD_COUNT)
            return WALLE_LG_REVEAL_RASTER_CAPACITY_EXCEEDED;
        struct walle_lg_reveal_general_child* child = &result->children[result->child_count];
        struct wlg_child_setup*               setup = &setups[result->child_count];
        *child = (struct walle_lg_reveal_general_child){};
        if (!wlg_child_prepare(triangle, calibration, setup, child))
            continue; /* drop only this child; the packed path still covers it */
        child->source_primitive = children.children[index].source_primitive;
        child->tile_low[0]      = child->visible_bounds[0] >= 0
                                      ? child->visible_bounds[0] / TILE_SIZE
                                      : (child->visible_bounds[0] - TILE_SIZE + 1) / TILE_SIZE;
        child->tile_low[1]      = child->visible_bounds[1] >= 0
                                      ? child->visible_bounds[1] / TILE_SIZE
                                      : (child->visible_bounds[1] - TILE_SIZE + 1) / TILE_SIZE;
        child->tile_high[0]     = (child->visible_bounds[2] + TILE_SIZE - 1) / TILE_SIZE;
        child->tile_high[1]     = (child->visible_bounds[3] + TILE_SIZE - 1) / TILE_SIZE;
        size_t tiles_x          = (size_t)(child->tile_high[0] - child->tile_low[0]);
        size_t tiles_y          = (size_t)(child->tile_high[1] - child->tile_low[1]);
        child->constant_offset  = (uint32_t)constant_words;
        constant_words += tiles_x * tiles_y * 2;
        {
            union { float f; uint32_t u; } radius = { geometry->circle.expanded_radius };
            const struct wlg_hw_child* hw = wlg_hw_lookup(radius.u, child->fixed);
            if (hw != nullptr && hw->ext != nullptr) {
                child->has_ext    = 1;
                child->ext_offset = (uint32_t)constant_words;
                constant_words += 26;
            }
        }
        ++result->child_count;
    }

    if (constant_words == 0)
        return WALLE_LG_REVEAL_RASTER_OK;
    result->constant_words = calloc(constant_words, sizeof(uint32_t));
    if (result->constant_words == nullptr)
        return WALLE_LG_REVEAL_RASTER_ARITHMETIC_RANGE;
    result->constant_word_count = constant_words;

    for (size_t index = 0; index < result->child_count; ++index) {
        struct walle_lg_reveal_general_child* child = &result->children[index];
        struct wlg_child_setup*               setup = &setups[index];
        size_t tiles_x = (size_t)(child->tile_high[0] - child->tile_low[0]);
        for (int32_t tile_y = child->tile_low[1]; tile_y < child->tile_high[1]; ++tile_y) {
            for (int32_t tile_x = child->tile_low[0]; tile_x < child->tile_high[0]; ++tile_x) {
                size_t cell = (size_t)(tile_y - child->tile_low[1]) * tiles_x
                              + (size_t)(tile_x - child->tile_low[0]);
                for (size_t channel = 0; channel < 2; ++channel) {
                    uint32_t word = 0;
                    if (!wlg_child_constant_bits(setup,
                                                 setup->sdf[setup->anchor][channel],
                                                 channel,
                                                 tile_x,
                                                 tile_y,
                                                 &word)) {
                        /* Constant out of binary32 range for this tile
                         * (possible far outside the visible area); the
                         * child never owns pixels there, so store zero. */
                        word = 0;
                    }
                    result->constant_words[child->constant_offset + 2 * cell + channel] = word;
                }
            }
        }
        /* Hardware-measured constants (AGX probe captures) override the
         * computed chain for the residual children; see
         * liquid_glass_reveal_hw_constants.h. */
        union { float f; uint32_t u; } radius = { geometry->circle.expanded_radius };
        const struct wlg_hw_child* hw = wlg_hw_lookup(radius.u, child->fixed);
        child->hw_trusted = hw != nullptr && hw->trusted;
        if (hw != nullptr) {
            for (size_t channel = 0; channel < 2; ++channel) {
                if (hw->slope[channel][0] || hw->slope[channel][1]) {
                    child->slope_bits[channel][0] = hw->slope[channel][0];
                    child->slope_bits[channel][1] = hw->slope[channel][1];
                }
            }
            for (uint32_t ti = 0; ti < hw->tile_count; ++ti) {
                const struct wlg_hw_tile* tile = &hw->tiles[ti];
                if (tile->tx < child->tile_low[0] || tile->tx >= child->tile_high[0]
                    || tile->ty < child->tile_low[1] || tile->ty >= child->tile_high[1]) {
                    continue;
                }
                size_t cell = (size_t)(tile->ty - child->tile_low[1]) * tiles_x
                              + (size_t)(tile->tx - child->tile_low[0]);
                for (size_t channel = 0; channel < 2; ++channel) {
                    if (tile->c[channel])
                        result->constant_words[child->constant_offset + 2 * cell + channel]
                            = tile->c[channel];
                }
            }
            if (child->has_ext && hw->ext != nullptr) {
                const struct wlg_hw_ext* ext   = hw->ext;
                uint32_t*                words = result->constant_words + child->ext_offset;
                words[0] = (uint32_t)(uint16_t)ext->tx
                           | ((uint32_t)(uint16_t)ext->ty << 16);
                words[1] = (uint32_t)ext->e0 | ((uint32_t)ext->e1 << 8)
                           | ((uint32_t)ext->e2 << 16) | ((uint32_t)ext->e3 << 24);
                size_t cursor = 2;
                for (size_t region = 0; region < 2; ++region) {
                    for (size_t channel = 0; channel < 2; ++channel) {
                        for (size_t term = 0; term < 3; ++term) {
                            uint64_t bits = (uint64_t)ext->plane[region][channel][term];
                            words[cursor++] = (uint32_t)bits;
                            words[cursor++] = (uint32_t)(bits >> 32);
                        }
                    }
                }
            }
        }
    }
    return WALLE_LG_REVEAL_RASTER_OK;
}

void walle_lg_reveal_general_destroy(struct walle_lg_reveal_general* general)
{
    if (general == nullptr)
        return;
    free(general->constant_words);
    *general = (struct walle_lg_reveal_general){};
}

bool walle_lg_reveal_general_contains(const struct walle_lg_reveal_general_child* child,
                                      int32_t                                     x,
                                      int32_t                                     y)
{
    if (x < child->visible_bounds[0] || y < child->visible_bounds[1]
        || x >= child->visible_bounds[2] || y >= child->visible_bounds[3]) {
        return false;
    }
    int64_t center_x = (int64_t)x * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2;
    int64_t center_y = (int64_t)y * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2;
    int     expected = child->det_sign;
    for (size_t edge = 0; edge < 3; ++edge) {
        size_t  next   = (edge + 1) % 3;
        int64_t edge_x = child->fixed[next][0] - child->fixed[edge][0];
        int64_t edge_y = child->fixed[next][1] - child->fixed[edge][1];
        int64_t cross  = edge_x * (center_y - child->fixed[edge][1])
                        - edge_y * (center_x - child->fixed[edge][0]);
        if (cross == 0) {
            int64_t oriented_x = expected < 0 ? -edge_x : edge_x;
            int64_t oriented_y = expected < 0 ? -edge_y : edge_y;
            bool    top        = oriented_y == 0 && oriented_x < 0;
            bool    left       = oriented_y > 0;
            if (!(top || left))
                return false;
            continue;
        }
        if ((cross < 0 ? -1 : 1) != expected)
            return false;
    }
    return true;
}

bool walle_lg_reveal_general_value(const struct walle_lg_reveal_general* general,
                                   size_t                                child_index,
                                   int32_t                               x,
                                   int32_t                               y,
                                   float                                 result[static 2])
{
    if (general == nullptr || child_index >= general->child_count)
        return false;
    const struct walle_lg_reveal_general_child* child = &general->children[child_index];
    int32_t tile_x = x >= 0 ? x / TILE_SIZE : (x - TILE_SIZE + 1) / TILE_SIZE;
    int32_t tile_y = y >= 0 ? y / TILE_SIZE : (y - TILE_SIZE + 1) / TILE_SIZE;
    if (tile_x < child->tile_low[0] || tile_x >= child->tile_high[0]
        || tile_y < child->tile_low[1] || tile_y >= child->tile_high[1]) {
        return false;
    }
    if (child->has_ext) {
        const uint32_t* words = general->constant_words + child->ext_offset;
        int32_t         etx   = (int16_t)(words[0] & 0xffffu);
        int32_t         ety   = (int16_t)(words[0] >> 16);
        if (tile_x == etx && tile_y == ety) {
            int32_t local_x = x - tile_x * TILE_SIZE;
            int32_t local_y = y - tile_y * TILE_SIZE;
            int32_t e0 = (int32_t)(words[1] & 0xffu);
            int32_t e1 = (int32_t)((words[1] >> 8) & 0xffu);
            int32_t e2 = (int32_t)((words[1] >> 16) & 0xffu);
            int32_t e3 = (int32_t)(words[1] >> 24);
            size_t  region = e3 != 0 && local_x >= e0 + (e1 * local_y + e2) / e3 ? 1 : 0;
            for (size_t channel = 0; channel < 2; ++channel) {
                int64_t term[3];
                for (size_t t = 0; t < 3; ++t) {
                    size_t o = 2 + (region * 2 + channel) * 6 + t * 2;
                    term[t] = (int64_t)((uint64_t)words[o]
                                        | ((uint64_t)words[o + 1] << 32));
                }
                int64_t  total = term[0] * local_x + term[1] * local_y + term[2];
                uint32_t out   = 0;
                if (total != 0) {
                    uint32_t sign = total < 0 ? UINT32_C(0x80000000) : 0;
                    uint64_t mag  = total < 0 ? (uint64_t)-total : (uint64_t)total;
                    int      high = 63;
                    while (!(mag >> high))
                        --high;
                    int      low  = high - 23;
                    uint32_t mant = low > 0 ? (uint32_t)(mag >> low)
                                            : (uint32_t)(mag << -low);
                    int code = -60 + low + 150;
                    out      = sign | ((uint32_t)code << 23) | (mant & 0x7fffffu);
                }
                result[channel] = bits_float(out);
            }
            return true;
        }
    }
    size_t tiles_x = (size_t)(child->tile_high[0] - child->tile_low[0]);
    size_t cell    = (size_t)(tile_y - child->tile_low[1]) * tiles_x
                  + (size_t)(tile_x - child->tile_low[0]);
    for (size_t channel = 0; channel < 2; ++channel) {
        uint32_t constant_bits
            = general->constant_words[child->constant_offset + 2 * cell + channel];
        struct dyadic constant = dyadic_from_float_bits(constant_bits);
        struct dyadic slope_x  = dyadic_from_float_bits(child->slope_bits[channel][0]);
        struct dyadic slope_y  = dyadic_from_float_bits(child->slope_bits[channel][1]);
        struct dyadic term_x, term_y, exact;
        int32_t       local_x = x - tile_x * TILE_SIZE;
        int32_t       local_y = y - tile_y * TILE_SIZE;
        if (!dyadic_multiply_integer(slope_x, (int64_t)(2 * local_x + 1), &term_x)
            || !dyadic_multiply_integer(slope_y, (int64_t)(2 * local_y + 1), &term_y)) {
            return false;
        }
        --term_x.exponent;
        --term_y.exponent;
        uint32_t out_bits = 0;
        if (!dyadic_add(constant, term_x, &exact) || !dyadic_add(exact, term_y, &exact)
            || !dyadic_toward_zero_float_bits(exact, &out_bits)) {
            return false;
        }
        result[channel] = bits_float(out_bits);
    }
    return true;
}
