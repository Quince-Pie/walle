#include "liquid_glass_static_profile.h"

#include <float.h>
#include <math.h>
#include <stddef.h>
#include <string.h>

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(_Float16) == 2);
static_assert(sizeof(struct walle_lg_profile_payload)
              == WALLE_LG_PROFILE_PAYLOAD_BYTE_COUNT);

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "The captured Liquid Glass profile encoding is little-endian"
#endif

struct matrix_attributes {
    float white;
    float black;
    float saturation;
    float fill[4];
};

struct endpoint_profile {
    struct matrix_attributes face;
    struct matrix_attributes bleed;
    struct matrix_attributes shadow;
    float edge_opacity;
    float bleed_darken[2];
    float shadow_face_opacity;
};

static const float rgb_to_ycbcr[20] = {
    0.2126f, 0.7152f, 0.0722f, 0.0f, 0.0f,
    -0.1146f, -0.3854f, 0.5f, 0.0f, 0.5f,
    0.5f, -0.4542f, -0.0458f, 0.0f, 0.5f,
    0.0f, 0.0f, 0.0f, 1.0f, 0.0f,
};

static const float ycbcr_to_rgb[20] = {
    1.0f, 0.0f, 1.5748f, 0.0f, -0.7874f,
    1.0f, -0.187324f, -0.468124f, 0.0f, 0.327724f,
    1.0f, 1.8556f, 0.0f, 0.0f, -0.9278f,
    0.0f, 0.0f, 0.0f, 1.0f, 0.0f,
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

static float fused_multiply_add(float left, float right, float addend)
{
    volatile float result = fmaf(left, right, addend);
    return result;
}

static void multiply_color_matrices(
    float result[static 20],
    const float left[static 20],
    const float right[static 20]
)
{
    for (size_t row = 0; row < 4; ++row) {
        size_t row_offset = row * 5;
        for (size_t column = 0; column < 4; ++column) {
            float accumulator = rounded_multiply(
                left[row_offset + 3],
                right[15 + column]
            );
            for (size_t index = 3; index-- > 0;) {
                accumulator = fused_multiply_add(
                    left[row_offset + index],
                    right[index * 5 + column],
                    accumulator
                );
            }
            result[row_offset + column] = accumulator;
        }

        float accumulator = rounded_multiply(left[row_offset + 3], right[19]);
        for (size_t index = 3; index-- > 0;) {
            accumulator = fused_multiply_add(
                left[row_offset + index],
                right[index * 5 + 4],
                accumulator
            );
        }
        result[row_offset + 4] = rounded_add(
            accumulator,
            left[row_offset + 4]
        );
    }
}

static void color_matrix(
    float result[static 12],
    const struct matrix_attributes *attributes
)
{
    float luminance[20] = { 0 };
    luminance[0] = rounded_subtract(attributes->white, attributes->black);
    luminance[4] = attributes->black;
    luminance[6] = 1.0f;
    luminance[12] = 1.0f;
    luminance[18] = 1.0f;

    float saturation[20] = { 0 };
    saturation[0] = 1.0f;
    saturation[6] = attributes->saturation;
    saturation[12] = attributes->saturation;
    saturation[18] = 1.0f;
    volatile double saturation_offset =
        0.5 - (double)attributes->saturation * 0.5;
    saturation[9] = (float)saturation_offset;
    saturation[14] = (float)saturation_offset;

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
        matrix[row * 5 + 4] = rounded_add(
            matrix[row * 5 + 4],
            attributes->fill[row]
        );
    }

    size_t output = 0;
    for (size_t row = 0; row < 3; ++row) {
        for (size_t column = 0; column < 3; ++column) {
            result[output++] = matrix[row * 5 + column];
        }
        result[output++] = matrix[row * 5 + 4];
    }
}

static struct endpoint_profile endpoint_profile(
    enum walle_lg_material material,
    enum walle_lg_appearance appearance
)
{
    float sdr_shadow = 0.24f;
    if (material == WALLE_LG_MATERIAL_CLEAR) {
        float shadow_face_opacity = rounded_add(0.1f, sdr_shadow);
        return (struct endpoint_profile) {
            .face = { 1.15f, 0.075f, 1.06f, { 0.0f, 0.0f, 0.0f, 0.0f } },
            .bleed = { 1.0f, 0.75f, 1.2f, { 0.0f, 0.0f, 0.0f, 0.0f } },
            .shadow = {
                1.0f, 0.0f, 1.2f,
                { 0.0f, 0.0f, 0.0f, shadow_face_opacity },
            },
            .edge_opacity = 0.0f,
            .bleed_darken = { 1.0f, 0.0f },
            .shadow_face_opacity = shadow_face_opacity,
        };
    }
    if (appearance == WALLE_LG_APPEARANCE_LIGHT) {
        float shadow_face_opacity = rounded_add(0.12f, sdr_shadow);
        return (struct endpoint_profile) {
            .face = { 1.03f, 0.5f, 1.0f, { 0.4f, 0.4f, 0.4f, 0.4f } },
            .bleed = { 1.0f, 0.9f, 1.2f, { 0.0f, 0.0f, 0.0f, 0.0f } },
            .shadow = {
                1.0f, 0.0f, 1.8f,
                { 0.0f, 0.0f, 0.0f, shadow_face_opacity },
            },
            .edge_opacity = 0.5f,
            .bleed_darken = { 1.0f, 0.0f },
            .shadow_face_opacity = shadow_face_opacity,
        };
    }
    return (struct endpoint_profile) {
        .face = { 0.6f, 0.2f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.4f } },
        .bleed = { 0.5f, 0.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f } },
        .shadow = { 0.5f, 0.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.24f } },
        .edge_opacity = 0.8f,
        .bleed_darken = { -1.0f, 1.0f },
        .shadow_face_opacity = 0.24f,
    };
}

static void store_float(struct walle_lg_profile_payload *payload, size_t offset, float value)
{
    memcpy(&payload->byte[offset], &value, sizeof value);
}

static void store_half(struct walle_lg_profile_payload *payload, size_t offset, float value)
{
    volatile _Float16 converted = (_Float16)value;
    _Float16 stored = converted;
    memcpy(&payload->byte[offset], &stored, sizeof stored);
}

static void store_matrix(
    struct walle_lg_profile_payload *payload,
    size_t offset,
    const struct matrix_attributes *attributes
)
{
    float matrix[12];
    color_matrix(matrix, attributes);
    for (size_t index = 0; index < 12; ++index) {
        store_half(payload, offset + index * 2, matrix[index]);
    }
}

bool walle_lg_static_profile(
    const struct walle_lg_static_profile_request *request,
    struct walle_lg_profile_payload *result
)
{
    if (request == nullptr || result == nullptr
        || request->material < WALLE_LG_MATERIAL_CLEAR
        || request->material > WALLE_LG_MATERIAL_REGULAR
        || request->appearance < WALLE_LG_APPEARANCE_LIGHT
        || request->appearance > WALLE_LG_APPEARANCE_DARK
        || !isfinite(request->width) || request->width <= 0.0f
        || !isfinite(request->height) || request->height <= 0.0f
        || request->source_virtual_width == 0
        || request->source_virtual_height == 0) {
        return false;
    }

    bool regular = request->material == WALLE_LG_MATERIAL_REGULAR;
    struct endpoint_profile profile = endpoint_profile(
        request->material,
        request->appearance
    );
    float half_width = rounded_multiply(request->width, 0.5f);
    float half_height = rounded_multiply(request->height, 0.5f);
    float radius = fminf(half_width, half_height);
    float outer_amount = rounded_multiply(radius, 0.4f);
    float bleed_amount = regular ? rounded_multiply(radius, 0.7f) : 0.0f;
    float shadow_height = rounded_multiply(radius, 0.8f);
    memset(result, 0, sizeof *result);

    const float sdf_arg[4] = {
        half_width, half_height, 4.0f, regular ? 0.5f : 0.0f,
    };
    const float sdf_transform[4] = { 1.0f, 0.0f, 0.0f, 1.0f };
    const float sdf_arg2[4] = { 1.0f, 1.0f, radius, 0.0f };
    const float displacement[4] = {
        rounded_divide(1.0f, (float)request->source_virtual_width),
        0.0f,
        0.0f,
        -rounded_divide(1.0f, (float)request->source_virtual_height),
    };
    for (size_t index = 0; index < 4; ++index) {
        store_float(result, index * 4, sdf_arg[index]);
        store_float(result, 16 + index * 4, sdf_transform[index]);
        store_float(result, 32 + index * 4, sdf_arg2[index]);
        store_float(result, 48 + index * 4, displacement[index]);
    }

    store_float(result, 64, -60.0f);
    store_float(result, 68, rounded_divide(1.0f, 20.0f));
    store_float(result, 72, outer_amount);
    store_float(result, 76, rounded_divide(4.0f, radius));
    store_float(result, 80, -1.0f);
    store_float(result, 84, 0.0f);
    store_float(result, 88, regular ? 1.6f : 0.8f);
    store_float(result, 92, regular ? 32.0f : 0.0f);
    store_float(result, 96, bleed_amount);
    store_float(
        result,
        100,
        regular ? rounded_divide(1.0f, bleed_amount) : INFINITY
    );
    store_float(result, 104, 75.0f);
    store_float(result, 108, rounded_divide(1.0f, shadow_height));
    store_float(result, 112, 0.0f);
    store_float(result, 116, -8.0f);
    store_float(result, 120, regular ? 8.0f : 0.0f);
    store_float(
        result,
        124,
        regular ? rounded_divide(1.0f, 24.0f) : INFINITY
    );
    store_matrix(result, 128, &profile.face);
    store_matrix(result, 152, &profile.bleed);
    store_matrix(result, 176, &profile.shadow);
    store_float(result, 200, regular ? 1.0f : 0.0f);
    store_float(result, 204, profile.shadow_face_opacity);

    const float blur_alpha[4] = { 1.0f, 0.5f, 0.0f, -0.5f };
    const float blur_distance[4] = { -radius, -1.0f, 0.0f, 0.0f };
    for (size_t index = 0; index < 4; ++index) {
        store_half(result, 208 + index * 2, blur_alpha[index]);
        store_half(result, 216 + index * 2, blur_distance[index]);
    }
    store_half(result, 224, 1.0f);
    store_half(result, 226, 0.0f);
    store_half(result, 228, profile.edge_opacity);
    store_half(result, 230, 1.0f);
    store_half(result, 232, profile.bleed_darken[0]);
    store_half(result, 234, profile.bleed_darken[1]);
    store_half(result, 236, 0.0f);
    store_half(result, 238, regular ? 0.25f : 0.0f);
    store_half(result, 240, regular ? 0.3f : 0.0f);
    store_half(result, 242, 1.0f);
    store_half(result, 244, -2.0f);
    store_half(result, 246, -1.0f);
    float clamp_limit = fmaxf(
        1.0f,
        ceilf(rounded_multiply(profile.face.white, 32.0f)) / 32.0f
    );
    store_half(result, 248, clamp_limit);
    store_half(result, 250, 0.0f);
    store_half(result, 252, 0.97f);
    store_half(result, 254, 0.0f);
    store_half(result, 256, 1.0f);
    return true;
}
