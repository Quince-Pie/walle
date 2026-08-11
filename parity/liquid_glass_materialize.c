#include "liquid_glass_materialize.h"
#include "liquid_glass_darwin_powf.h"

#include <float.h>
#include <math.h>
#include <stddef.h>

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53);
static_assert(WALLE_LG_NUMERIC_FIELD_COUNT == 47);

struct profile {
    float face_black;
    float face_saturation;
    float face_white;
    float bleed_black;
    float bleed_saturation;
    float bleed_white;
    float shadow_black;
    float shadow_saturation;
    float shadow_white;
    float bleed_opacity;
};

static const struct profile profiles[2][2] = {
    [WALLE_LG_MATERIAL_CLEAR] = {
        [WALLE_LG_APPEARANCE_LIGHT] = {
            .face_black = 0.075f,
            .face_saturation = 1.06f,
            .face_white = 1.15f,
            .bleed_black = 0.75f,
            .bleed_saturation = 1.2f,
            .bleed_white = 1.0f,
            .shadow_black = 0.0f,
            .shadow_saturation = 1.2f,
            .shadow_white = 1.0f,
            .bleed_opacity = 0.0f,
        },
        [WALLE_LG_APPEARANCE_DARK] = {
            .face_black = 0.075f,
            .face_saturation = 1.06f,
            .face_white = 1.15f,
            .bleed_black = 0.75f,
            .bleed_saturation = 1.2f,
            .bleed_white = 1.0f,
            .shadow_black = 0.0f,
            .shadow_saturation = 1.2f,
            .shadow_white = 1.0f,
            .bleed_opacity = 0.0f,
        },
    },
    [WALLE_LG_MATERIAL_REGULAR] = {
        [WALLE_LG_APPEARANCE_LIGHT] = {
            .face_black = 0.5f,
            .face_saturation = 1.0f,
            .face_white = 1.03f,
            .bleed_black = 0.9f,
            .bleed_saturation = 1.2f,
            .bleed_white = 1.0f,
            .shadow_black = 0.0f,
            .shadow_saturation = 1.8f,
            .shadow_white = 1.0f,
            .bleed_opacity = 0.5f,
        },
        [WALLE_LG_APPEARANCE_DARK] = {
            .face_black = 0.2f,
            .face_saturation = 1.0f,
            .face_white = 0.6f,
            .bleed_black = 0.0f,
            .bleed_saturation = 1.0f,
            .bleed_white = 0.5f,
            .shadow_black = 0.0f,
            .shadow_saturation = 1.0f,
            .shadow_white = 0.5f,
            .bleed_opacity = 0.8f,
        },
    },
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

static float mix32(float start, float end, float fraction)
{
    float start_weight = rounded_subtract(1.0f, fraction);
    float start_product = rounded_multiply(start_weight, start);
    float end_product = rounded_multiply(fraction, end);
    return rounded_add(start_product, end_product);
}

static bool input_clamp(float fraction, float face_white, float *result)
{
    float from_weight = rounded_subtract(1.0f, fraction);
    float encoded = rounded_add(
        rounded_multiply(from_weight, 1.0f),
        rounded_multiply(fraction, face_white)
    );
    float divisor = 1.055f;
    float inverse = rounded_divide(1.0f, divisor);
    float offset = rounded_divide(0.055f, divisor);
    float base = rounded_add(rounded_multiply(encoded, inverse), offset);
    float decoded;
    if (!walle_lg_darwin_powf_2_4(base, &decoded)) {
        return false;
    }
    *result = decoded > 1.0f ? decoded : 1.0f;
    return true;
}

bool walle_lg_transition_numeric_inputs(
    const struct walle_lg_transition_request *request,
    struct walle_lg_numeric_inputs *result
)
{
    if (request == nullptr || result == nullptr
        || request->material < WALLE_LG_MATERIAL_CLEAR
        || request->material > WALLE_LG_MATERIAL_REGULAR
        || request->appearance < WALLE_LG_APPEARANCE_LIGHT
        || request->appearance > WALLE_LG_APPEARANCE_DARK
        || request->diameter == 0
        || !(request->visible_fraction >= 0.0f
             && request->visible_fraction <= 1.0f)) {
        return false;
    }

    const struct profile *profile = &profiles[request->material][request->appearance];
    float fraction = request->visible_fraction;
    bool regular = request->material == WALLE_LG_MATERIAL_REGULAR;
    float clamp;
    if (!input_clamp(fraction, profile->face_white, &clamp)) {
        return false;
    }

    /* These stores preserve Apple's mixed-precision law. Replacing them with
     * float arithmetic changes observed words for otherwise equivalent math. */
    volatile double geometry_tail = 16.0 * (1.0 - (double)fraction);
    volatile double geometry_sum = (double)request->diameter + geometry_tail;
    volatile double geometry = (double)fraction * geometry_sum;
    volatile double doubled_geometry = 2.0 * geometry;

    float blur_weight = rounded_multiply(fraction, mix32(0.2f, 0.5f, fraction));
    float doubled_blur_weight = rounded_multiply(2.0f, blur_weight);
    float *value = result->value;

    value[WALLE_LG_INPUT_BLEED_AMOUNT] = regular ? (float)(0.35 * geometry) : 0.0f;
    value[WALLE_LG_INPUT_BLEED_BLUR_RADIUS] = regular
        ? rounded_multiply(160.0f, fraction)
        : 0.0f;
    value[WALLE_LG_INPUT_BLEED_COLOR_MATRIX_BLACK] = mix32(
        0.0f, profile->bleed_black, fraction
    );
    value[WALLE_LG_INPUT_BLEED_COLOR_MATRIX_SATURATION] = mix32(
        1.0f, profile->bleed_saturation, fraction
    );
    value[WALLE_LG_INPUT_BLEED_COLOR_MATRIX_WHITE] = mix32(
        1.0f, profile->bleed_white, fraction
    );
    value[WALLE_LG_INPUT_BLEED_DISTANCE_0] = fraction;
    value[WALLE_LG_INPUT_BLEED_DISTANCE_1] = 0.0f;
    value[WALLE_LG_INPUT_BLEED_HEIGHT] = regular ? (float)(0.35 * geometry) : 0.0f;
    value[WALLE_LG_INPUT_BLEED_OPACITY] = mix32(
        0.0f, profile->bleed_opacity, fraction
    );
    value[WALLE_LG_INPUT_BLUR_DISTANCE_0] = (float)(-geometry / 2.0);
    value[WALLE_LG_INPUT_BLUR_DISTANCE_1] = -fraction;
    value[WALLE_LG_INPUT_BLUR_DISTANCE_2] = 0.0f;
    value[WALLE_LG_INPUT_BLUR_DISTANCE_3] = 0.0f;
    value[WALLE_LG_INPUT_BLUR_DISTANCE_4] = regular ? (float)(geometry / 5.0) : 0.0f;
    value[WALLE_LG_INPUT_BLUR_OPACITY_0] = fraction;
    value[WALLE_LG_INPUT_BLUR_OPACITY_1] = blur_weight;
    value[WALLE_LG_INPUT_BLUR_OPACITY_2] = blur_weight;
    value[WALLE_LG_INPUT_BLUR_OPACITY_3] = doubled_blur_weight;
    value[WALLE_LG_INPUT_BLUR_OPACITY_4] = doubled_blur_weight;
    value[WALLE_LG_INPUT_BLUR_RADIUS] = rounded_multiply(
        regular ? 4.0f : 1.0f, fraction
    );
    value[WALLE_LG_INPUT_CLAMP] = clamp;
    value[WALLE_LG_INPUT_FACE_COLOR_MATRIX_BLACK] = mix32(
        0.0f, profile->face_black, fraction
    );
    value[WALLE_LG_INPUT_FACE_COLOR_MATRIX_SATURATION] = mix32(
        1.0f, profile->face_saturation, fraction
    );
    value[WALLE_LG_INPUT_FACE_COLOR_MATRIX_WHITE] = mix32(
        1.0f, profile->face_white, fraction
    );
    value[WALLE_LG_INPUT_FACE_OPACITY] = fraction;
    value[WALLE_LG_INPUT_INNER_REFRACTION_AMOUNT] = rounded_multiply(-60.0f, fraction);
    value[WALLE_LG_INPUT_INNER_REFRACTION_HEIGHT] = rounded_multiply(20.0f, fraction);
    value[WALLE_LG_INPUT_MAX_HEADROOM] = mix32(1.2f, 9999.0f, fraction);
    value[WALLE_LG_INPUT_OUTER_REFRACTION_AMOUNT] = (float)(geometry / 5.0);
    value[WALLE_LG_INPUT_OUTER_REFRACTION_HEIGHT] = (float)(geometry / 8.0);
    value[WALLE_LG_INPUT_REFRACTION_DISTANCE_0] = -fraction;
    value[WALLE_LG_INPUT_REFRACTION_DISTANCE_1] = (float)(-(double)fraction / 2.0);
    value[WALLE_LG_INPUT_REFRACTION_OPACITY] = regular
        ? mix32(0.0f, 0.3f, fraction)
        : 0.0f;
    value[WALLE_LG_INPUT_SDR_GRADIENT_DISTANCE_0] = -fraction;
    value[WALLE_LG_INPUT_SDR_GRADIENT_DISTANCE_1] = (float)(-(double)fraction / 2.0);
    value[WALLE_LG_INPUT_SDR_HOLDING_TONE_WHITE] = mix32(1.0f, 0.97f, fraction);
    value[WALLE_LG_INPUT_SDR_SHADOW_OPACITY] = mix32(0.0f, 0.24f, fraction);
    value[WALLE_LG_INPUT_SHADOW_AMOUNT] = rounded_multiply(75.0f, fraction);
    value[WALLE_LG_INPUT_SHADOW_BLUR_RADIUS] = regular
        ? rounded_multiply(40.0f, fraction)
        : 0.0f;
    value[WALLE_LG_INPUT_SHADOW_COLOR_MATRIX_BLACK] = mix32(
        0.0f, profile->shadow_black, fraction
    );
    value[WALLE_LG_INPUT_SHADOW_COLOR_MATRIX_SATURATION] = mix32(
        1.0f, profile->shadow_saturation, fraction
    );
    value[WALLE_LG_INPUT_SHADOW_COLOR_MATRIX_WHITE] = mix32(
        1.0f, profile->shadow_white, fraction
    );
    value[WALLE_LG_INPUT_SHADOW_DISTANCE_OFFSET] = 0.0f;
    value[WALLE_LG_INPUT_SHADOW_HEIGHT] = (float)(doubled_geometry / 5.0);
    value[WALLE_LG_INPUT_SHADOW_OPACITY] = regular
        ? mix32(0.0f, 0.25f, fraction)
        : 0.0f;
    value[WALLE_LG_INPUT_SHADOW_RADIUS] = regular
        ? rounded_multiply(24.0f, fraction)
        : 0.0f;
    value[WALLE_LG_INPUT_SHADOW_VIBRANCY_CONTRIBUTION] = regular ? fraction : 0.0f;
    return true;
}
