#include "liquid_glass_transition_profile.h"

#include <float.h>
#include <math.h>
#include <stddef.h>
#include <string.h>

#include "liquid_glass_darwin_powf.h"
#include "liquid_glass_resolved_color.h"

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53);
static_assert(sizeof(_Float16) == 2);

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#    error "The Liquid Glass profile encoding is little-endian"
#endif

struct matrix_attributes
{
    float white;
    float black;
    float saturation;
    float fill[4];
};

struct public_color
{
    float component[4];
};

static const float rgb_to_ycbcr[20] = {
    0.2126f, 0.7152f,  0.0722f,  0.0f, 0.0f, -0.1146f, -0.3854f, 0.5f, 0.0f, 0.5f,
    0.5f,    -0.4542f, -0.0458f, 0.0f, 0.5f, 0.0f,     0.0f,     0.0f, 1.0f, 0.0f,
};

static const float ycbcr_to_rgb[20] = {
    1.0f, 0.0f,    1.5748f, 0.0f, -0.7874f, 1.0f, -0.187324f, -0.468124f, 0.0f, 0.327724f,
    1.0f, 1.8556f, 0.0f,    0.0f, -0.9278f, 0.0f, 0.0f,       0.0f,       1.0f, 0.0f,
};

static float rounded_add(float left, float right)
{
    volatile float result = left + right;
    return result;
}

static float rounded_subtract(float left, float right)
{
    volatile float result = left - right;
    return result;
}

static float rounded_multiply(float left, float right)
{
    volatile float result = left * right;
    return result;
}

static float rounded_divide(float left, float right)
{
    volatile float result = left / right;
    return result;
}

static float rounded_mix(float start, float end, float fraction)
{
    float start_weight  = rounded_subtract(1.0f, fraction);
    float start_product = rounded_multiply(start_weight, start);
    float end_product   = rounded_multiply(fraction, end);
    return rounded_add(start_product, end_product);
}

static float reciprocal64(float value)
{
    volatile double result  = 1.0 / (double)value;
    volatile float  rounded = (float)result;
    return rounded;
}

static float reciprocal_from_double(double value)
{
    if (value == 0.0) {
        return copysignf(INFINITY, (float)value);
    }
    volatile double result  = 1.0 / value;
    volatile float  rounded = (float)result;
    return rounded;
}

static float fused_multiply_add(float left, float right, float addend)
{
    volatile float result = fmaf(left, right, addend);
    return result;
}

static float dynamic_scale(float start, float end, float fraction)
{
    volatile double delta  = (double)end - (double)start;
    volatile double scaled = delta * (double)fraction;
    volatile double sum    = (double)start + scaled;
    volatile float  result = (float)sum;
    return result;
}

static void multiply_color_matrices(float       result[static 20],
                                    const float left[static 20],
                                    const float right[static 20])
{
    for (size_t row = 0; row < 4; ++row) {
        size_t row_offset = row * 5;
        for (size_t column = 0; column < 4; ++column) {
            float accumulator = rounded_multiply(left[row_offset + 3], right[15 + column]);
            for (size_t index = 3; index-- > 0;) {
                accumulator = fused_multiply_add(
                    left[row_offset + index], right[index * 5 + column], accumulator);
            }
            result[row_offset + column] = accumulator;
        }

        float accumulator = rounded_multiply(left[row_offset + 3], right[19]);
        for (size_t index = 3; index-- > 0;) {
            accumulator
                = fused_multiply_add(left[row_offset + index], right[index * 5 + 4], accumulator);
        }
        result[row_offset + 4] = rounded_add(accumulator, left[row_offset + 4]);
    }
}

static void multiply_color_matrices_ascending(float       result[static 20],
                                              const float left[static 20],
                                              const float right[static 20])
{
    for (size_t row = 0; row < 4; ++row) {
        size_t row_offset = row * 5;
        for (size_t column = 0; column < 4; ++column) {
            float accumulator = rounded_multiply(left[row_offset], right[column]);
            for (size_t index = 1; index < 4; ++index) {
                accumulator = fused_multiply_add(
                    left[row_offset + index], right[index * 5 + column], accumulator);
            }
            result[row_offset + column] = accumulator;
        }

        float accumulator = rounded_multiply(left[row_offset], right[4]);
        for (size_t index = 1; index < 4; ++index) {
            accumulator
                = fused_multiply_add(left[row_offset + index], right[index * 5 + 4], accumulator);
        }
        result[row_offset + 4] = rounded_add(accumulator, left[row_offset + 4]);
    }
}

static void color_matrix(float result[static 12], const struct matrix_attributes* attributes)
{
    float luminance[20] = {0};
    luminance[0]        = rounded_subtract(attributes->white, attributes->black);
    luminance[4]        = attributes->black;
    luminance[6]        = 1.0f;
    luminance[12]       = 1.0f;
    luminance[18]       = 1.0f;

    float saturation[20]              = {0};
    saturation[0]                     = 1.0f;
    saturation[6]                     = attributes->saturation;
    saturation[12]                    = attributes->saturation;
    saturation[18]                    = 1.0f;
    volatile double saturation_offset = 0.5 - (double)attributes->saturation * 0.5;
    saturation[9]                     = (float)saturation_offset;
    saturation[14]                    = (float)saturation_offset;

    float first[20];
    float second[20];
    float matrix[20];
    multiply_color_matrices(first, luminance, rgb_to_ycbcr);
    multiply_color_matrices(second, saturation, first);
    multiply_color_matrices(matrix, ycbcr_to_rgb, second);

    float scale = rounded_subtract(1.0f, attributes->fill[3]);
    for (size_t index = 0; index < 20; ++index) {
        matrix[index] = rounded_multiply(matrix[index], scale);
    }
    for (size_t row = 0; row < 4; ++row) {
        matrix[row * 5 + 4] = rounded_add(matrix[row * 5 + 4], attributes->fill[row]);
    }

    size_t output = 0;
    for (size_t row = 0; row < 3; ++row) {
        for (size_t column = 0; column < 3; ++column) {
            result[output++] = matrix[row * 5 + column];
        }
        result[output++] = matrix[row * 5 + 4];
    }
}

static void color_matrix_ascending(float                           result[static 12],
                                   const struct matrix_attributes* attributes)
{
    float luminance[20] = {0};
    luminance[0]        = rounded_subtract(attributes->white, attributes->black);
    luminance[4]        = attributes->black;
    luminance[6]        = 1.0f;
    luminance[12]       = 1.0f;
    luminance[18]       = 1.0f;

    float saturation[20]              = {0};
    saturation[0]                     = 1.0f;
    saturation[6]                     = attributes->saturation;
    saturation[12]                    = attributes->saturation;
    saturation[18]                    = 1.0f;
    volatile double saturation_offset = 0.5 - (double)attributes->saturation * 0.5;
    saturation[9]                     = (float)saturation_offset;
    saturation[14]                    = (float)saturation_offset;

    float first[20];
    float second[20];
    float matrix[20];
    multiply_color_matrices_ascending(first, luminance, rgb_to_ycbcr);
    multiply_color_matrices_ascending(second, saturation, first);
    multiply_color_matrices_ascending(matrix, ycbcr_to_rgb, second);

    float scale = rounded_subtract(1.0f, attributes->fill[3]);
    for (size_t index = 0; index < 20; ++index) {
        matrix[index] = rounded_multiply(matrix[index], scale);
    }
    for (size_t row = 0; row < 4; ++row) {
        matrix[row * 5 + 4] = rounded_add(matrix[row * 5 + 4], attributes->fill[row]);
    }

    size_t output = 0;
    for (size_t row = 0; row < 3; ++row) {
        for (size_t column = 0; column < 3; ++column) {
            result[output++] = matrix[row * 5 + column];
        }
        result[output++] = matrix[row * 5 + 4];
    }
}

static bool
mixed_public_color(const float endpoint[static 4], float fraction, struct public_color* result)
{
    float transparent[4] = {
        endpoint[0],
        endpoint[1],
        endpoint[2],
        0.0f,
    };
    struct walle_lg_resolved_color from;
    struct walle_lg_resolved_color to;
    struct walle_lg_resolved_color mixed;
    return walle_lg_resolved_color_from_public_components(transparent, &from)
           && walle_lg_resolved_color_from_public_components(endpoint, &to)
           && walle_lg_mix_resolved_color(&from, &to, (double)fraction, &mixed)
           && walle_lg_resolved_color_public_components(&mixed, result->component);
}

static struct public_color
endpoint_color(enum walle_lg_material material, enum walle_lg_appearance appearance, bool shadow)
{
    if (shadow) {
        float alpha = material == WALLE_LG_MATERIAL_CLEAR       ? 0.1f
                      : appearance == WALLE_LG_APPEARANCE_LIGHT ? 0.12f
                                                                : 0.0f;
        return (struct public_color){.component = {0.0f, 0.0f, 0.0f, alpha}};
    }
    if (material == WALLE_LG_MATERIAL_REGULAR && appearance == WALLE_LG_APPEARANCE_LIGHT) {
        return (struct public_color){.component = {1.0f, 1.0f, 1.0f, 0.4f}};
    }
    if (material == WALLE_LG_MATERIAL_REGULAR) {
        return (struct public_color){.component = {0.0f, 0.0f, 0.0f, 0.4f}};
    }
    return (struct public_color){.component = {1.0f, 1.0f, 1.0f, 0.0f}};
}

static bool matrix_attributes(struct matrix_attributes*  result,
                              float                      white,
                              float                      black,
                              float                      saturation,
                              const struct public_color* color,
                              float                      alpha_addend)
{
    if (result == nullptr || color == nullptr) {
        return false;
    }
    *result = (struct matrix_attributes){
        .white      = white,
        .black      = black,
        .saturation = saturation,
    };
    float alpha = color->component[3];
    for (size_t index = 0; index < 3; ++index) {
        result->fill[index] = rounded_multiply(color->component[index], alpha);
    }
    result->fill[3] = rounded_add(alpha, alpha_addend);
    return true;
}

static void store_float(struct walle_lg_profile_payload* payload, size_t offset, float value)
{
    memcpy(&payload->byte[offset], &value, sizeof value);
}

static void store_half(struct walle_lg_profile_payload* payload, size_t offset, float value)
{
    volatile _Float16 converted = (_Float16)value;
    _Float16          stored    = converted;
    memcpy(&payload->byte[offset], &stored, sizeof stored);
}

static void store_matrix(struct walle_lg_profile_payload* payload,
                         size_t                           offset,
                         const struct matrix_attributes*  attributes)
{
    float matrix[12];
    color_matrix(matrix, attributes);
    for (size_t index = 0; index < 12; ++index) {
        store_half(payload, offset + index * 2, matrix[index]);
    }
}

static void store_matrix_ascending(struct walle_lg_profile_payload* payload,
                                   size_t                           offset,
                                   const struct matrix_attributes*  attributes)
{
    float matrix[12];
    color_matrix_ascending(matrix, attributes);
    for (size_t index = 0; index < 12; ++index) {
        store_half(payload, offset + index * 2, matrix[index]);
    }
}

static bool store_profile_tail(struct walle_lg_profile_payload*      result,
                               const struct walle_lg_numeric_inputs* inputs,
                               enum walle_lg_material                material,
                               enum walle_lg_appearance              appearance,
                               float                                 fraction,
                               const float                           displacement[static 4],
                               double                                inner_height,
                               double                                outer_height,
                               double                                bleed_height,
                               double                                shadow_height,
                               float                                 shadow_offset_y,
                               bool                                  ascending_shadow_matrix)
{
    bool         regular = material == WALLE_LG_MATERIAL_REGULAR;
    const float* value   = inputs->value;

    struct public_color face_endpoint   = endpoint_color(material, appearance, false);
    struct public_color shadow_endpoint = endpoint_color(material, appearance, true);
    struct public_color face;
    struct public_color shadow;
    struct public_color bleed = {.component = {0}};
    if (!mixed_public_color(face_endpoint.component, fraction, &face)
        || !mixed_public_color(shadow_endpoint.component, fraction, &shadow)) {
        return false;
    }

    struct matrix_attributes face_matrix;
    struct matrix_attributes bleed_matrix;
    struct matrix_attributes shadow_matrix;
    if (!matrix_attributes(&face_matrix,
                           value[WALLE_LG_INPUT_FACE_COLOR_MATRIX_WHITE],
                           value[WALLE_LG_INPUT_FACE_COLOR_MATRIX_BLACK],
                           value[WALLE_LG_INPUT_FACE_COLOR_MATRIX_SATURATION],
                           &face,
                           0.0f)
        || !matrix_attributes(&bleed_matrix,
                              value[WALLE_LG_INPUT_BLEED_COLOR_MATRIX_WHITE],
                              value[WALLE_LG_INPUT_BLEED_COLOR_MATRIX_BLACK],
                              value[WALLE_LG_INPUT_BLEED_COLOR_MATRIX_SATURATION],
                              &bleed,
                              0.0f)
        || !matrix_attributes(&shadow_matrix,
                              value[WALLE_LG_INPUT_SHADOW_COLOR_MATRIX_WHITE],
                              value[WALLE_LG_INPUT_SHADOW_COLOR_MATRIX_BLACK],
                              value[WALLE_LG_INPUT_SHADOW_COLOR_MATRIX_SATURATION],
                              &shadow,
                              value[WALLE_LG_INPUT_SDR_SHADOW_OPACITY])) {
        return false;
    }

    for (size_t index = 0; index < 4; ++index) {
        store_float(result, 48 + index * 4, displacement[index]);
    }

    float main_blur_scale      = dynamic_scale(1.6f, regular ? 0.4f : 0.8f, fraction);
    float auxiliary_blur_scale = dynamic_scale(0.8f, 0.2f, fraction);
    store_float(result, 64, value[WALLE_LG_INPUT_INNER_REFRACTION_AMOUNT]);
    store_float(result, 68, reciprocal_from_double(inner_height));
    store_float(result, 72, value[WALLE_LG_INPUT_OUTER_REFRACTION_AMOUNT]);
    store_float(result, 76, reciprocal_from_double(outer_height));
    store_float(result, 80, value[WALLE_LG_INPUT_REFRACTION_DISTANCE_0]);
    store_float(result, 84, value[WALLE_LG_INPUT_REFRACTION_DISTANCE_1]);
    store_float(result, 88, rounded_multiply(value[WALLE_LG_INPUT_BLUR_RADIUS], main_blur_scale));
    store_float(result,
                92,
                rounded_multiply(value[WALLE_LG_INPUT_BLEED_BLUR_RADIUS], auxiliary_blur_scale));
    store_float(result, 96, value[WALLE_LG_INPUT_BLEED_AMOUNT]);
    store_float(result, 100, reciprocal_from_double(bleed_height));
    store_float(result, 104, value[WALLE_LG_INPUT_SHADOW_AMOUNT]);
    store_float(result, 108, reciprocal_from_double(shadow_height));
    store_float(result, 112, 0.0f);
    store_float(result, 116, shadow_offset_y);
    store_float(result,
                120,
                rounded_multiply(value[WALLE_LG_INPUT_SHADOW_BLUR_RADIUS], auxiliary_blur_scale));
    store_float(result, 124, reciprocal64(value[WALLE_LG_INPUT_SHADOW_RADIUS]));
    store_matrix(result, 128, &face_matrix);
    store_matrix(result, 152, &bleed_matrix);
    if (ascending_shadow_matrix) {
        store_matrix_ascending(result, 176, &shadow_matrix);
    } else {
        store_matrix(result, 176, &shadow_matrix);
    }
    store_float(result, 200, value[WALLE_LG_INPUT_SHADOW_VIBRANCY_CONTRIBUTION]);
    store_float(
        result, 204, rounded_add(shadow.component[3], value[WALLE_LG_INPUT_SDR_SHADOW_OPACITY]));

    float       opacity0      = value[WALLE_LG_INPUT_BLUR_OPACITY_0];
    float       opacity1      = value[WALLE_LG_INPUT_BLUR_OPACITY_1];
    float       opacity2      = value[WALLE_LG_INPUT_BLUR_OPACITY_2];
    float       opacity3      = value[WALLE_LG_INPUT_BLUR_OPACITY_3];
    const float blur_alpha[4] = {
        opacity0,
        rounded_subtract(opacity0, opacity1),
        rounded_subtract(opacity1, opacity2),
        rounded_subtract(opacity2, opacity3),
    };
    for (size_t index = 0; index < 4; ++index) {
        store_half(result, 208 + index * 2, blur_alpha[index]);
        store_half(result, 216 + index * 2, value[WALLE_LG_INPUT_BLUR_DISTANCE_0 + index]);
    }
    store_half(result, 224, value[WALLE_LG_INPUT_BLEED_DISTANCE_0]);
    store_half(result, 226, value[WALLE_LG_INPUT_BLEED_DISTANCE_1]);
    store_half(result, 228, value[WALLE_LG_INPUT_BLEED_OPACITY]);
    store_half(result, 230, value[WALLE_LG_INPUT_FACE_OPACITY]);

    bool bleed_darken = appearance == WALLE_LG_APPEARANCE_LIGHT
                        || (material == WALLE_LG_MATERIAL_CLEAR && fraction >= 0.5f);
    store_half(result, 232, bleed_darken ? 1.0f : -1.0f);
    store_half(result, 234, bleed_darken ? 0.0f : 1.0f);
    store_half(result, 236, value[WALLE_LG_INPUT_SHADOW_DISTANCE_OFFSET]);
    store_half(result, 238, value[WALLE_LG_INPUT_SHADOW_OPACITY]);
    store_half(result, 240, value[WALLE_LG_INPUT_REFRACTION_OPACITY]);
    store_half(result, 242, 1.0f);
    store_half(result, 244, value[WALLE_LG_INPUT_SDR_GRADIENT_DISTANCE_0]);
    store_half(result, 246, value[WALLE_LG_INPUT_SDR_GRADIENT_DISTANCE_1]);

    float clamp_limit;
    if (!walle_lg_darwin_powf_1_over_2_2(value[WALLE_LG_INPUT_CLAMP], &clamp_limit)) {
        return false;
    }
    store_half(result, 248, clamp_limit);
    store_half(result, 250, 0.0f);
    store_half(result, 252, value[WALLE_LG_INPUT_SDR_HOLDING_TONE_WHITE]);
    store_half(result, 254, 0.0f);
    store_half(result, 256, 1.0f);
    return true;
}

bool walle_lg_transition_profile(const struct walle_lg_transition_profile_request* request,
                                 struct walle_lg_profile_payload*                  result)
{
    if (request == nullptr || result == nullptr || !isfinite(request->sdf_half_width)
        || !isfinite(request->sdf_half_height) || request->sdf_half_width <= 0.0f
        || request->sdf_half_height <= 0.0f || !isfinite(request->source_texel_step_x)
        || !isfinite(request->source_texel_step_y) || request->source_texel_step_x <= 0.0f
        || request->source_texel_step_y <= 0.0f) {
        return false;
    }

    struct walle_lg_numeric_inputs inputs;
    if (!walle_lg_transition_numeric_inputs(&request->transition, &inputs)) {
        return false;
    }

    enum walle_lg_material   material   = request->transition.material;
    enum walle_lg_appearance appearance = request->transition.appearance;
    float                    fraction   = request->transition.visible_fraction;
    bool                     regular    = material == WALLE_LG_MATERIAL_REGULAR;

    volatile double geometry_tail = 16.0 * (1.0 - (double)fraction);
    volatile double geometry_sum  = (double)request->transition.diameter + geometry_tail;
    volatile double geometry      = (double)fraction * geometry_sum;
    volatile double outer_height  = geometry / 8.0;
    volatile double bleed_height  = regular ? 0.35 * geometry : 0.0;
    volatile double shadow_height = 2.0 * geometry / 5.0;

    memset(result, 0, sizeof *result);
    const float sdf_arg[4] = {
        request->sdf_half_width,
        request->sdf_half_height,
        4.0f,
        regular ? 0.5f : 0.0f,
    };
    const float sdf_transform[4] = {1.0f, 0.0f, 0.0f, 1.0f};
    const float sdf_arg2[4]      = {
        1.0f,
        1.0f,
        fminf(request->sdf_half_width, request->sdf_half_height),
        0.0f,
    };
    const float displacement[4] = {
        request->source_texel_step_x,
        0.0f,
        0.0f,
        -request->source_texel_step_y,
    };
    for (size_t index = 0; index < 4; ++index) {
        store_float(result, index * 4, sdf_arg[index]);
        store_float(result, 16 + index * 4, sdf_transform[index]);
        store_float(result, 32 + index * 4, sdf_arg2[index]);
    }
    return store_profile_tail(result,
                              &inputs,
                              material,
                              appearance,
                              fraction,
                              displacement,
                              (double)inputs.value[WALLE_LG_INPUT_INNER_REFRACTION_HEIGHT],
                              outer_height,
                              bleed_height,
                              shadow_height,
                              -8.0f,
                              false);
}

bool walle_lg_small_clear_background_profile(
    const struct walle_lg_small_clear_background_profile_request* request,
    struct walle_lg_small_clear_background_profile_payload*       result)
{
    if (request == nullptr || result == nullptr || request->appearance < WALLE_LG_APPEARANCE_LIGHT
        || request->appearance > WALLE_LG_APPEARANCE_DARK || request->diameter == 0
        || !(request->visible_fraction > 0.0f && request->visible_fraction <= 1.0f)
        || !isfinite(request->element_extent) || request->element_extent <= 0.0
        || !isfinite(request->backdrop_scale) || request->backdrop_scale <= 0.0f) {
        return false;
    }

    struct walle_lg_transition_request transition = {
        .material         = WALLE_LG_MATERIAL_CLEAR,
        .appearance       = request->appearance,
        .diameter         = request->diameter,
        .visible_fraction = request->visible_fraction,
    };
    struct walle_lg_numeric_inputs inputs;
    if (!walle_lg_transition_numeric_inputs(&transition, &inputs)) {
        return false;
    }

    float           fraction      = request->visible_fraction;
    volatile double geometry      = (double)fraction * request->element_extent;
    volatile double outer_height  = geometry / 8.0;
    volatile double shadow_height = 2.0 * geometry / 5.0;

    volatile float blur_distance                         = (float)(-geometry / 2.0);
    volatile float outer_amount                          = (float)(geometry / 5.0);
    volatile float outer_height_input                    = (float)outer_height;
    volatile float shadow_height_input                   = (float)shadow_height;
    inputs.value[WALLE_LG_INPUT_BLUR_DISTANCE_0]         = blur_distance;
    inputs.value[WALLE_LG_INPUT_OUTER_REFRACTION_AMOUNT] = outer_amount;
    inputs.value[WALLE_LG_INPUT_OUTER_REFRACTION_HEIGHT] = outer_height_input;
    inputs.value[WALLE_LG_INPUT_SHADOW_HEIGHT]           = shadow_height_input;

    float           ordinary_inner_amount = rounded_multiply(60.0f, fraction);
    volatile double inner_amount_magnitude
        = (double)ordinary_inner_amount < geometry ? (double)ordinary_inner_amount : geometry;
    volatile float inner_amount                          = (float)-inner_amount_magnitude;
    inputs.value[WALLE_LG_INPUT_INNER_REFRACTION_AMOUNT] = inner_amount;

    float           ordinary_inner_height = rounded_multiply(20.0f, fraction);
    volatile double inner_height_limit    = 0.36 * geometry;
    volatile double inner_height          = (double)ordinary_inner_height < inner_height_limit
                                                ? (double)ordinary_inner_height
                                                : inner_height_limit;
    volatile float  rounded_inner_height  = (float)inner_height;
    inputs.value[WALLE_LG_INPUT_INNER_REFRACTION_HEIGHT] = rounded_inner_height;

    float           ordinary_shadow_amount = rounded_multiply(75.0f, fraction);
    volatile double shadow_amount_limit    = 0.625 * geometry;
    volatile double shadow_amount          = (double)ordinary_shadow_amount < shadow_amount_limit
                                                 ? (double)ordinary_shadow_amount
                                                 : shadow_amount_limit;
    volatile float  rounded_shadow_amount  = (float)shadow_amount;
    inputs.value[WALLE_LG_INPUT_SHADOW_AMOUNT] = rounded_shadow_amount;

    double clipped_extent = request->element_extent;
    if (clipped_extent < 48.0) {
        clipped_extent = 48.0;
    } else if (clipped_extent > 160.0) {
        clipped_extent = 160.0;
    }
    volatile double endpoint_fraction64 = (clipped_extent - 48.0) / 112.0;
    volatile float  endpoint_fraction   = (float)endpoint_fraction64;
    float           endpoint
        = rounded_add(0.08f, rounded_multiply(rounded_subtract(0.24f, 0.08f), endpoint_fraction));
    inputs.value[WALLE_LG_INPUT_SDR_SHADOW_OPACITY] = rounded_mix(0.0f, endpoint, fraction);

    float                           step = rounded_divide(request->backdrop_scale, 64.0f);
    const float                     displacement[4] = {step, 0.0f, 0.0f, -step};
    struct walle_lg_profile_payload full            = {0};
    if (!store_profile_tail(&full,
                            &inputs,
                            WALLE_LG_MATERIAL_CLEAR,
                            request->appearance,
                            fraction,
                            displacement,
                            inner_height,
                            outer_height,
                            0.0,
                            shadow_height,
                            0.0625f,
                            true)) {
        return false;
    }
    memcpy(result->byte, &full.byte[48], WALLE_LG_SMALL_CLEAR_BACKGROUND_PROFILE_BYTE_COUNT);
    return true;
}
