#include "liquid_glass_postguard.h"

#include <float.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* A rounded intersection needs at most 857 bits; exact area cancellation can
 * need 1,063.  Forty limbs leave both paths bounded without heap arithmetic. */
enum
{
    NATURAL_WORD_COUNT         = 40,
    TEMPORARY_POLYGON_CAPACITY = WALLE_LG_POSTGUARD_MAX_POLYGON_VERTEX_COUNT + 1,
};

struct natural
{
    uint32_t words[NATURAL_WORD_COUNT];
};

struct signed_natural
{
    struct natural magnitude;
    bool           negative;
};

struct binary32_parts
{
    uint32_t significand;
    int      exponent;
    bool     negative;
};

static_assert(sizeof(float) == sizeof(uint32_t));
static_assert(FLT_RADIX == 2);
static_assert(FLT_MANT_DIG == 24);
static_assert(FLT_MAX_EXP == 128);
static_assert(WALLE_LG_POSTGUARD_MAX_POLYGON_VERTEX_COUNT == 7);
static_assert(WALLE_LG_POSTGUARD_MAX_SOURCE_PRIMITIVE_COUNT <= UINT8_MAX);

static size_t natural_size(const struct natural* value)
{
    for (size_t index = NATURAL_WORD_COUNT; index > 0; --index) {
        if (value->words[index - 1] != 0)
            return index;
    }
    return 0;
}
static bool natural_is_zero(const struct natural* value)
{
    return natural_size(value) == 0;
}

static unsigned word_bit_length(uint32_t value)
{
    unsigned result = 0;
    while (value != 0) {
        ++result;
        value >>= 1;
    }
    return result;
}

static unsigned natural_bit_length(const struct natural* value)
{
    size_t size = natural_size(value);
    if (size == 0)
        return 0;
    return (unsigned)((size - 1) * 32) + word_bit_length(value->words[size - 1]);
}

static int natural_compare(const struct natural* left, const struct natural* right)
{
    for (size_t index = NATURAL_WORD_COUNT; index > 0; --index) {
        uint32_t left_word  = left->words[index - 1];
        uint32_t right_word = right->words[index - 1];
        if (left_word != right_word)
            return left_word < right_word ? -1 : 1;
    }
    return 0;
}

static bool natural_shift_left(const struct natural* source, unsigned shift, struct natural* result)
{
    *result = (struct natural){};
    if (natural_is_zero(source))
        return true;
    size_t   word_shift  = shift / 32;
    unsigned bit_shift   = shift % 32;
    size_t   source_size = natural_size(source);
    if (word_shift >= NATURAL_WORD_COUNT || source_size > NATURAL_WORD_COUNT - word_shift)
        return false;
    for (size_t index = 0; index < source_size; ++index) {
        size_t destination = index + word_shift;
        result->words[destination] |= source->words[index] << bit_shift;
        if (bit_shift != 0) {
            uint32_t high = source->words[index] >> (32 - bit_shift);
            if (high != 0) {
                if (destination + 1 >= NATURAL_WORD_COUNT)
                    return false;
                result->words[destination + 1] |= high;
            }
        }
    }
    return true;
}

static bool natural_from_shifted_u32(uint32_t value, unsigned shift, struct natural* result)
{
    struct natural source = {.words = {value}};
    return natural_shift_left(&source, shift, result);
}

static bool
natural_add(const struct natural* left, const struct natural* right, struct natural* result)
{
    struct natural sum   = {};
    uint64_t       carry = 0;
    for (size_t index = 0; index < NATURAL_WORD_COUNT; ++index) {
        uint64_t word    = (uint64_t)left->words[index] + right->words[index] + carry;
        sum.words[index] = (uint32_t)word;
        carry            = word >> 32;
    }
    if (carry != 0)
        return false;
    *result = sum;
    return true;
}

static void
natural_subtract(const struct natural* left, const struct natural* right, struct natural* result)
{
    struct natural difference = {};
    uint64_t       borrow     = 0;
    for (size_t index = 0; index < NATURAL_WORD_COUNT; ++index) {
        uint64_t subtrahend     = (uint64_t)right->words[index] + borrow;
        uint64_t left_word      = left->words[index];
        difference.words[index] = (uint32_t)(left_word - subtrahend);
        borrow                  = left_word < subtrahend;
    }
    *result = difference;
}

static bool
natural_multiply(const struct natural* left, const struct natural* right, struct natural* result)
{
    struct natural product    = {};
    size_t         left_size  = natural_size(left);
    size_t         right_size = natural_size(right);
    if (left_size == 0 || right_size == 0) {
        *result = product;
        return true;
    }
    if (left_size + right_size > NATURAL_WORD_COUNT + 1)
        return false;
    for (size_t left_index = 0; left_index < left_size; ++left_index) {
        uint64_t carry = 0;
        for (size_t right_index = 0; right_index < right_size; ++right_index) {
            size_t destination = left_index + right_index;
            if (destination >= NATURAL_WORD_COUNT)
                return false;
            uint64_t word = (uint64_t)left->words[left_index] * right->words[right_index]
                            + product.words[destination] + carry;
            product.words[destination] = (uint32_t)word;
            carry                      = word >> 32;
        }
        size_t destination = left_index + right_size;
        if (carry != 0) {
            if (destination >= NATURAL_WORD_COUNT || product.words[destination] != 0)
                return false;
            product.words[destination] = (uint32_t)carry;
        }
    }
    *result = product;
    return true;
}

static struct signed_natural signed_negate(struct signed_natural value)
{
    if (!natural_is_zero(&value.magnitude))
        value.negative = !value.negative;
    return value;
}

static bool
signed_add(struct signed_natural left, struct signed_natural right, struct signed_natural* result)
{
    struct signed_natural sum = {};
    if (left.negative == right.negative) {
        if (!natural_add(&left.magnitude, &right.magnitude, &sum.magnitude))
            return false;
        sum.negative = left.negative;
    } else {
        int comparison = natural_compare(&left.magnitude, &right.magnitude);
        if (comparison >= 0) {
            natural_subtract(&left.magnitude, &right.magnitude, &sum.magnitude);
            sum.negative = left.negative;
        } else {
            natural_subtract(&right.magnitude, &left.magnitude, &sum.magnitude);
            sum.negative = right.negative;
        }
    }
    if (natural_is_zero(&sum.magnitude))
        sum.negative = false;
    *result = sum;
    return true;
}

static bool signed_subtract(struct signed_natural  left,
                            struct signed_natural  right,
                            struct signed_natural* result)
{
    return signed_add(left, signed_negate(right), result);
}

static bool signed_multiply(struct signed_natural  left,
                            struct signed_natural  right,
                            struct signed_natural* result)
{
    struct signed_natural product = {.negative = left.negative != right.negative};
    if (!natural_multiply(&left.magnitude, &right.magnitude, &product.magnitude))
        return false;
    if (natural_is_zero(&product.magnitude))
        product.negative = false;
    *result = product;
    return true;
}

static bool
signed_shift_left(struct signed_natural source, unsigned shift, struct signed_natural* result)
{
    struct signed_natural shifted = {.negative = source.negative};
    if (!natural_shift_left(&source.magnitude, shift, &shifted.magnitude))
        return false;
    *result = shifted;
    return true;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof bits);
    return bits;
}

static bool binary32_decompose(uint32_t bits, struct binary32_parts* result)
{
    uint32_t encoded_exponent = (bits >> 23) & UINT32_C(0xff);
    if (encoded_exponent == UINT32_C(0xff))
        return false;
    *result = (struct binary32_parts){
        .significand = encoded_exponent == 0 ? bits & UINT32_C(0x7fffff)
                                             : (bits & UINT32_C(0x7fffff)) | UINT32_C(0x800000),
        .exponent    = encoded_exponent == 0 ? -149 : (int)encoded_exponent - 150,
        .negative    = (bits >> 31) != 0,
    };
    return true;
}

static bool scaled_binary32(uint32_t bits, int common_exponent, struct signed_natural* result)
{
    struct binary32_parts parts;
    if (!binary32_decompose(bits, &parts)
        || (parts.significand != 0 && parts.exponent < common_exponent)) {
        return false;
    }
    struct signed_natural value = {.negative = parts.negative};
    if (!natural_from_shifted_u32(
            parts.significand,
            parts.significand == 0 ? 0 : (unsigned)(parts.exponent - common_exponent),
            &value.magnitude)) {
        return false;
    }
    if (parts.significand == 0)
        value.negative = false;
    *result = value;
    return true;
}

static bool common_binary32_exponent(const uint32_t* bits, size_t count, int* result)
{
    int minimum = INT_MAX;
    for (size_t index = 0; index < count; ++index) {
        struct binary32_parts parts;
        if (!binary32_decompose(bits[index], &parts))
            return false;
        if (parts.significand != 0 && parts.exponent < minimum)
            minimum = parts.exponent;
    }
    *result = minimum == INT_MAX ? 0 : minimum;
    return true;
}

static bool dyadic_difference(uint32_t               left_bits,
                              uint32_t               right_bits,
                              struct signed_natural* difference,
                              int*                   exponent)
{
    uint32_t values[2] = {left_bits, right_bits};
    if (!common_binary32_exponent(values, 2, exponent))
        return false;
    struct signed_natural left;
    struct signed_natural right;
    return scaled_binary32(left_bits, *exponent, &left)
           && scaled_binary32(right_bits, *exponent, &right)
           && signed_subtract(left, right, difference);
}

static bool natural_compare_shifted(const struct natural* left,
                                    unsigned              left_shift,
                                    const struct natural* right,
                                    unsigned              right_shift,
                                    int*                  result)
{
    struct natural shifted_left;
    struct natural shifted_right;
    if (!natural_shift_left(left, left_shift, &shifted_left)
        || !natural_shift_left(right, right_shift, &shifted_right)) {
        return false;
    }
    *result = natural_compare(&shifted_left, &shifted_right);
    return true;
}

static bool rounded_scaled_ratio(const struct natural* numerator,
                                 const struct natural* denominator,
                                 int                   scale,
                                 uint32_t*             result)
{
    struct natural dividend;
    struct natural divisor;
    if (!natural_shift_left(numerator, scale > 0 ? (unsigned)scale : 0, &dividend)
        || !natural_shift_left(denominator, scale < 0 ? (unsigned)-scale : 0, &divisor)
        || natural_is_zero(&divisor)) {
        return false;
    }

    unsigned dividend_bits = natural_bit_length(&dividend);
    unsigned divisor_bits  = natural_bit_length(&divisor);
    uint32_t quotient      = 0;
    if (dividend_bits >= divisor_bits) {
        unsigned highest_bit = dividend_bits - divisor_bits;
        if (highest_bit >= 32)
            return false;
        for (unsigned ordinal = highest_bit + 1; ordinal > 0; --ordinal) {
            unsigned       shift = ordinal - 1;
            struct natural shifted_divisor;
            if (!natural_shift_left(&divisor, shift, &shifted_divisor))
                return false;
            if (natural_compare(&dividend, &shifted_divisor) >= 0) {
                natural_subtract(&dividend, &shifted_divisor, &dividend);
                quotient |= UINT32_C(1) << shift;
            }
        }
    }

    struct natural twice_remainder;
    if (!natural_shift_left(&dividend, 1, &twice_remainder))
        return false;
    int comparison = natural_compare(&twice_remainder, &divisor);
    if (comparison > 0 || (comparison == 0 && (quotient & 1) != 0)) {
        if (quotient == UINT32_MAX)
            return false;
        ++quotient;
    }
    *result = quotient;
    return true;
}

static bool rational_to_binary32(struct signed_natural numerator,
                                 const struct natural* denominator,
                                 int                   binary_exponent,
                                 uint32_t*             result)
{
    if (natural_is_zero(&numerator.magnitude)) {
        *result = 0;
        return true;
    }
    if (natural_is_zero(denominator))
        return false;

    int numerator_bits   = (int)natural_bit_length(&numerator.magnitude);
    int denominator_bits = (int)natural_bit_length(denominator);
    int ratio_exponent   = numerator_bits - denominator_bits;
    int comparison;
    if (ratio_exponent >= 0) {
        if (!natural_compare_shifted(
                &numerator.magnitude, 0, denominator, (unsigned)ratio_exponent, &comparison)) {
            return false;
        }
    } else {
        if (!natural_compare_shifted(
                &numerator.magnitude, (unsigned)-ratio_exponent, denominator, 0, &comparison)) {
            return false;
        }
    }
    if (comparison < 0)
        --ratio_exponent;

    int      encoded_exponent = ratio_exponent + binary_exponent;
    uint32_t significand;
    if (encoded_exponent >= -126) {
        if (!rounded_scaled_ratio(
                &numerator.magnitude, denominator, 23 - ratio_exponent, &significand)) {
            return false;
        }
        if (significand == UINT32_C(0x1000000)) {
            significand >>= 1;
            ++encoded_exponent;
        }
        if (encoded_exponent > 127 || significand < UINT32_C(0x800000)
            || significand >= UINT32_C(0x1000000)) {
            return false;
        }
        *result = (numerator.negative ? UINT32_C(0x80000000) : 0)
                  | (uint32_t)(encoded_exponent + 127) << 23 | (significand & UINT32_C(0x7fffff));
        return true;
    }

    if (!rounded_scaled_ratio(
            &numerator.magnitude, denominator, binary_exponent + 149, &significand)
        || significand > UINT32_C(0x800000)) {
        return false;
    }
    if (significand == 0) {
        *result = numerator.negative ? UINT32_C(0x80000000) : 0;
        return true;
    }
    *result = (numerator.negative ? UINT32_C(0x80000000) : 0) | significand;
    return true;
}

static unsigned u64_bit_length(uint64_t value)
{
    unsigned result = 0;
    while (value != 0) {
        ++result;
        value >>= 1;
    }
    return result;
}

static bool extent_guard_bits(uint32_t extent, uint32_t* low, uint32_t* high)
{
    if (extent == 0)
        return false;
    uint64_t numerators[2] = {extent, UINT64_C(5) * extent};
    uint32_t outputs[2];
    for (size_t index = 0; index < 2; ++index) {
        uint64_t numerator = numerators[index];
        unsigned bit_count = u64_bit_length(numerator);
        int      exponent  = (int)bit_count - 1 - 2;
        uint64_t significand;
        if (bit_count <= 24) {
            significand = numerator << (24 - bit_count);
        } else {
            unsigned shift     = bit_count - 24;
            uint64_t remainder = numerator & ((UINT64_C(1) << shift) - 1);
            uint64_t halfway   = UINT64_C(1) << (shift - 1);
            significand        = numerator >> shift;
            if (remainder > halfway || (remainder == halfway && (significand & 1) != 0))
                ++significand;
            if (significand == UINT64_C(0x1000000)) {
                significand >>= 1;
                ++exponent;
            }
        }
        if (exponent < -126 || exponent > 127)
            return false;
        outputs[index]
            = (uint32_t)(exponent + 127) << 23 | ((uint32_t)significand & UINT32_C(0x7fffff));
    }
    *low  = outputs[0] | UINT32_C(0x80000000);
    *high = outputs[1];
    return true;
}

static uint32_t numeric_zero_canonicalized(uint32_t bits)
{
    return (bits & UINT32_C(0x7fffffff)) == 0 ? 0 : bits;
}

static uint32_t binary32_order_key(uint32_t bits)
{
    bits = numeric_zero_canonicalized(bits);
    return (bits & UINT32_C(0x80000000)) != 0 ? ~bits : bits | UINT32_C(0x80000000);
}

static bool binary32_less(uint32_t left, uint32_t right)
{
    return binary32_order_key(left) < binary32_order_key(right);
}

static bool binary32_equal(uint32_t left, uint32_t right)
{
    return numeric_zero_canonicalized(left) == numeric_zero_canonicalized(right);
}

static bool vertex_position_equal(const struct walle_lg_postguard_vertex* left,
                                  const struct walle_lg_postguard_vertex* right)
{
    return binary32_equal(left->component_bits[0], right->component_bits[0])
           && binary32_equal(left->component_bits[1], right->component_bits[1]);
}

static bool triangle_area_is_zero(const struct walle_lg_postguard_vertex triangle[static 3],
                                  bool*                                  result)
{
    struct signed_natural ab_x;
    struct signed_natural ac_y;
    struct signed_natural ab_y;
    struct signed_natural ac_x;
    int                   ab_x_exponent;
    int                   ac_y_exponent;
    int                   ab_y_exponent;
    int                   ac_x_exponent;
    if (!dyadic_difference(
            triangle[1].component_bits[0], triangle[0].component_bits[0], &ab_x, &ab_x_exponent)
        || !dyadic_difference(
            triangle[2].component_bits[1], triangle[0].component_bits[1], &ac_y, &ac_y_exponent)
        || !dyadic_difference(
            triangle[1].component_bits[1], triangle[0].component_bits[1], &ab_y, &ab_y_exponent)
        || !dyadic_difference(
            triangle[2].component_bits[0], triangle[0].component_bits[0], &ac_x, &ac_x_exponent)) {
        return false;
    }

    struct signed_natural first;
    struct signed_natural second;
    if (!signed_multiply(ab_x, ac_y, &first) || !signed_multiply(ab_y, ac_x, &second))
        return false;
    int first_exponent  = ab_x_exponent + ac_y_exponent;
    int second_exponent = ab_y_exponent + ac_x_exponent;
    int common_exponent = first_exponent < second_exponent ? first_exponent : second_exponent;
    if (!signed_shift_left(first, (unsigned)(first_exponent - common_exponent), &first)
        || !signed_shift_left(second, (unsigned)(second_exponent - common_exponent), &second)
        || !signed_subtract(first, second, &first)) {
        return false;
    }
    *result = natural_is_zero(&first.magnitude);
    return true;
}

static bool intersection(const struct walle_lg_postguard_vertex* start,
                         const struct walle_lg_postguard_vertex* end,
                         size_t                                  axis,
                         uint32_t                                edge_bits,
                         struct walle_lg_postguard_vertex*       result)
{
    uint32_t axis_values[3] = {
        edge_bits,
        start->component_bits[axis],
        end->component_bits[axis],
    };
    int axis_exponent;
    if (!common_binary32_exponent(axis_values, 3, &axis_exponent))
        return false;
    struct signed_natural edge;
    struct signed_natural start_axis;
    struct signed_natural end_axis;
    struct signed_natural fraction_numerator;
    struct signed_natural fraction_denominator;
    if (!scaled_binary32(edge_bits, axis_exponent, &edge)
        || !scaled_binary32(start->component_bits[axis], axis_exponent, &start_axis)
        || !scaled_binary32(end->component_bits[axis], axis_exponent, &end_axis)
        || !signed_subtract(edge, start_axis, &fraction_numerator)
        || !signed_subtract(end_axis, start_axis, &fraction_denominator)
        || natural_is_zero(&fraction_denominator.magnitude)) {
        return false;
    }

    struct walle_lg_postguard_vertex output = {};
    for (size_t component = 0; component < WALLE_LG_POSTGUARD_VERTEX_COMPONENT_COUNT; ++component) {
        if (component == axis) {
            output.component_bits[component] = edge_bits;
            continue;
        }
        uint32_t component_values[2] = {
            start->component_bits[component],
            end->component_bits[component],
        };
        int component_exponent;
        if (!common_binary32_exponent(component_values, 2, &component_exponent))
            return false;
        struct signed_natural start_component;
        struct signed_natural end_component;
        struct signed_natural component_delta;
        struct signed_natural first_product;
        struct signed_natural second_product;
        struct signed_natural numerator;
        if (!scaled_binary32(start->component_bits[component], component_exponent, &start_component)
            || !scaled_binary32(end->component_bits[component], component_exponent, &end_component)
            || !signed_subtract(end_component, start_component, &component_delta)
            || !signed_multiply(start_component, fraction_denominator, &first_product)
            || !signed_multiply(fraction_numerator, component_delta, &second_product)
            || !signed_add(first_product, second_product, &numerator)) {
            return false;
        }
        if (fraction_denominator.negative)
            numerator = signed_negate(numerator);
        if (!rational_to_binary32(numerator,
                                  &fraction_denominator.magnitude,
                                  component_exponent,
                                  &output.component_bits[component])) {
            return false;
        }
    }
    *result = output;
    return true;
}

static bool vertex_inside(uint32_t coordinate, uint32_t edge, bool keep_greater)
{
    return keep_greater ? !binary32_less(coordinate, edge) : !binary32_less(edge, coordinate);
}

static bool triangle_outside_guard(const struct walle_lg_postguard_vertex triangle[static 3],
                                   const uint32_t                         guard_bits[static 4])
{
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        uint32_t x = triangle[vertex].component_bits[0];
        uint32_t y = triangle[vertex].component_bits[1];
        if (binary32_less(x, guard_bits[0]) || binary32_less(guard_bits[1], x)
            || binary32_less(y, guard_bits[2]) || binary32_less(guard_bits[3], y)) {
            return true;
        }
    }
    return false;
}

static enum walle_lg_postguard_status clip_triangle(
    const struct walle_lg_postguard_vertex triangle[static 3],
    const uint32_t                         guard_bits[static 4],
    struct walle_lg_postguard_vertex output[static WALLE_LG_POSTGUARD_MAX_POLYGON_VERTEX_COUNT],
    size_t*                          output_count)
{
    struct plane
    {
        size_t   axis;
        uint32_t edge_bits;
        bool     keep_greater;
    };
    const struct plane planes[4] = {
        {0, guard_bits[0], true},
        {0, guard_bits[1], false},
        {1, guard_bits[2], true},
        {1, guard_bits[3], false},
    };
    struct walle_lg_postguard_vertex current[TEMPORARY_POLYGON_CAPACITY] = {
        triangle[0],
        triangle[1],
        triangle[2],
    };
    size_t current_count = 3;
    for (size_t plane_index = 0; plane_index < 4; ++plane_index) {
        if (current_count == 0)
            break;
        struct walle_lg_postguard_vertex next[TEMPORARY_POLYGON_CAPACITY] = {};
        size_t                           next_count                       = 0;
        struct walle_lg_postguard_vertex previous = current[current_count - 1];
        bool previous_inside = vertex_inside(previous.component_bits[planes[plane_index].axis],
                                             planes[plane_index].edge_bits,
                                             planes[plane_index].keep_greater);
        for (size_t index = 0; index < current_count; ++index) {
            struct walle_lg_postguard_vertex vertex = current[index];
            bool inside = vertex_inside(vertex.component_bits[planes[plane_index].axis],
                                        planes[plane_index].edge_bits,
                                        planes[plane_index].keep_greater);
            if (inside) {
                if (!previous_inside) {
                    if (next_count >= TEMPORARY_POLYGON_CAPACITY)
                        return WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED;
                    if (!intersection(&previous,
                                      &vertex,
                                      planes[plane_index].axis,
                                      planes[plane_index].edge_bits,
                                      &next[next_count++])) {
                        return WALLE_LG_POSTGUARD_ARITHMETIC_RANGE;
                    }
                }
                if (next_count >= TEMPORARY_POLYGON_CAPACITY)
                    return WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED;
                next[next_count++] = vertex;
            } else if (previous_inside) {
                if (next_count >= TEMPORARY_POLYGON_CAPACITY)
                    return WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED;
                if (!intersection(&previous,
                                  &vertex,
                                  planes[plane_index].axis,
                                  planes[plane_index].edge_bits,
                                  &next[next_count++])) {
                    return WALLE_LG_POSTGUARD_ARITHMETIC_RANGE;
                }
            }
            previous        = vertex;
            previous_inside = inside;
        }

        current_count = 0;
        for (size_t index = 0; index < next_count; ++index) {
            if (current_count == 0
                || !vertex_position_equal(&current[current_count - 1], &next[index])) {
                if (current_count >= TEMPORARY_POLYGON_CAPACITY)
                    return WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED;
                current[current_count++] = next[index];
            }
        }
        if (current_count > 1 && vertex_position_equal(&current[0], &current[current_count - 1]))
            --current_count;
    }
    if (current_count > WALLE_LG_POSTGUARD_MAX_POLYGON_VERTEX_COUNT)
        return WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED;
    memcpy(output, current, current_count * sizeof *output);
    *output_count = current_count;
    return WALLE_LG_POSTGUARD_OK;
}

static bool load_source_vertex(const struct walle_lg_reveal_mask_vertex* source,
                               enum walle_lg_reveal_mask_family          family,
                               struct walle_lg_postguard_vertex*         result)
{
    struct walle_lg_postguard_vertex output = {};
    for (size_t component = 0; component < 4; ++component)
        output.component_bits[component] = float_bits(source->position[component]);
    const float* coordinates = family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS
                                   ? source->first_coordinates
                                   : source->second_coordinates;
    for (size_t component = 0; component < 2; ++component)
        output.component_bits[component + 4] = float_bits(coordinates[component]);
    for (size_t component = 0; component < WALLE_LG_POSTGUARD_VERTEX_COMPONENT_COUNT; ++component) {
        struct binary32_parts parts;
        if (!binary32_decompose(output.component_bits[component], &parts))
            return false;
    }
    *result = output;
    return true;
}

enum walle_lg_postguard_status
walle_lg_postguard_children_construct(const struct walle_lg_reveal_mask_geometry* geometry,
                                      const uint32_t                              target_extent[2],
                                      struct walle_lg_postguard_children*         result)
{
    if (geometry == nullptr || target_extent == nullptr || result == nullptr)
        return WALLE_LG_POSTGUARD_INVALID_ARGUMENT;
    *result = (struct walle_lg_postguard_children){};
    if (target_extent[0] == 0 || target_extent[1] == 0
        || geometry->vertex_count > WALLE_LG_REVEAL_MAX_VERTEX_COUNT
        || geometry->index_count > WALLE_LG_REVEAL_MAX_INDEX_COUNT || geometry->index_count % 6 != 0
        || (geometry->family != WALLE_LG_REVEAL_MASK_EMPTY
            && geometry->family != WALLE_LG_REVEAL_MASK_BORDER_GRID
            && geometry->family != WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS)
        || (geometry->family == WALLE_LG_REVEAL_MASK_EMPTY
            && (geometry->vertex_count != 0 || geometry->index_count != 0))) {
        return WALLE_LG_POSTGUARD_INVALID_GEOMETRY;
    }
    if (!extent_guard_bits(target_extent[0], &result->guard_bits[0], &result->guard_bits[1])
        || !extent_guard_bits(target_extent[1], &result->guard_bits[2], &result->guard_bits[3])) {
        return WALLE_LG_POSTGUARD_ARITHMETIC_RANGE;
    }
    if (geometry->family == WALLE_LG_REVEAL_MASK_EMPTY)
        return WALLE_LG_POSTGUARD_OK;

    size_t primitive_count = geometry->index_count / 3;
    for (size_t primitive = 0; primitive < primitive_count; ++primitive) {
        struct walle_lg_postguard_vertex triangle[3];
        for (size_t local = 0; local < 3; ++local) {
            uint16_t source_index = geometry->indices[primitive * 3 + local];
            if (source_index >= geometry->vertex_count
                || !load_source_vertex(
                    &geometry->vertices[source_index], geometry->family, &triangle[local])) {
                return WALLE_LG_POSTGUARD_INVALID_GEOMETRY;
            }
        }
        if (!triangle_outside_guard(triangle, result->guard_bits))
            continue;

        struct walle_lg_postguard_vertex polygon[WALLE_LG_POSTGUARD_MAX_POLYGON_VERTEX_COUNT];
        size_t                           polygon_count;
        enum walle_lg_postguard_status   status
            = clip_triangle(triangle, result->guard_bits, polygon, &polygon_count);
        if (status != WALLE_LG_POSTGUARD_OK)
            return status;
        if (polygon_count < 3)
            continue;
        for (size_t fan = 1; fan + 1 < polygon_count; ++fan) {
            struct walle_lg_postguard_vertex child_vertices[3] = {
                polygon[0],
                polygon[fan],
                polygon[fan + 1],
            };
            bool area_zero;
            if (!triangle_area_is_zero(child_vertices, &area_zero))
                return WALLE_LG_POSTGUARD_ARITHMETIC_RANGE;
            if (area_zero)
                continue;
            if (result->child_count >= WALLE_LG_POSTGUARD_MAX_CHILD_COUNT)
                return WALLE_LG_POSTGUARD_CAPACITY_EXCEEDED;
            struct walle_lg_postguard_child* child = &result->children[result->child_count++];
            memcpy(child->vertices, child_vertices, sizeof child->vertices);
            child->source_primitive = (uint8_t)primitive;
            child->owner_policy     = WALLE_LG_POSTGUARD_CHILD_SCOPED_CENTER_FALLBACK;
        }
    }
    return WALLE_LG_POSTGUARD_OK;
}
