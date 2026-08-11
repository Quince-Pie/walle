#include "liquid_glass_resolved_color.h"

#include "liquid_glass_darwin_powf.h"

#include <float.h>
#include <math.h>
#include <stddef.h>

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53);
static_assert(FLT_EVAL_METHOD == 0);

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

static bool linear_to_srgb(float linear, float *result)
{
    float magnitude = fabsf(linear);
    if (magnitude <= 0.0031308f) {
        *result = rounded_multiply(12.92f, linear);
        return true;
    }
    if (magnitude == 1.0f) {
        *result = linear;
        return true;
    }

    float powered;
    if (!walle_lg_darwin_powf_positive_normal(
            magnitude,
            0.4166666567325592f,
            &powered)) {
        return false;
    }
    float encoded = rounded_subtract(
        rounded_multiply(1.055f, powered),
        0.055f
    );
    *result = copysignf(encoded, linear);
    return true;
}

static bool srgb_to_linear(float encoded, float *result)
{
    float magnitude = fabsf(encoded);
    if (magnitude <= 0.04045f) {
        *result = rounded_divide(encoded, 12.92f);
        return true;
    }

    float inverse = rounded_divide(1.0f, 1.055f);
    float offset = rounded_divide(0.055f, 1.055f);
    float base = rounded_add(
        rounded_multiply(magnitude, inverse),
        offset
    );
    float decoded;
    if (!walle_lg_darwin_powf_2_4(base, &decoded)) {
        return false;
    }
    *result = copysignf(decoded, encoded);
    return true;
}

bool walle_lg_resolved_color_public_components(
    const struct walle_lg_resolved_color *color,
    float *components
)
{
    if (color == nullptr || components == nullptr) {
        return false;
    }
    for (size_t index = 0; index < 3; ++index) {
        if (!isfinite(color->linear_rgba[index])
            || !linear_to_srgb(color->linear_rgba[index], &components[index])) {
            return false;
        }
    }
    if (!isfinite(color->linear_rgba[3])) {
        return false;
    }
    components[3] = color->linear_rgba[3];
    return true;
}

bool walle_lg_resolved_color_from_public_components(
    const float *components,
    struct walle_lg_resolved_color *color
)
{
    if (components == nullptr || color == nullptr) {
        return false;
    }
    for (size_t index = 0; index < 3; ++index) {
        if (!isfinite(components[index])
            || !srgb_to_linear(components[index], &color->linear_rgba[index])) {
            return false;
        }
    }
    if (!isfinite(components[3])) {
        return false;
    }
    color->linear_rgba[3] = components[3];
    return true;
}

bool walle_lg_mix_resolved_color(
    const struct walle_lg_resolved_color *from,
    const struct walle_lg_resolved_color *to,
    double fraction,
    struct walle_lg_resolved_color *result
)
{
    if (from == nullptr || to == nullptr || result == nullptr || !isfinite(fraction)) {
        return false;
    }

    float from_components[4];
    float to_components[4];
    if (!walle_lg_resolved_color_public_components(from, from_components)
        || !walle_lg_resolved_color_public_components(to, to_components)) {
        return false;
    }

    volatile double complement = 1.0 - fraction;
    volatile float from_weight = (float)complement;
    volatile float to_weight = (float)fraction;
    float mixed[4];
    for (size_t index = 0; index < 4; ++index) {
        float from_product = rounded_multiply(from_components[index], from_weight);
        float to_product = rounded_multiply(to_components[index], to_weight);
        mixed[index] = rounded_add(from_product, to_product);
    }
    return walle_lg_resolved_color_from_public_components(mixed, result);
}
