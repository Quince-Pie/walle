#ifndef WALLE_LIQUID_GLASS_STATIC_PROFILE_H
#define WALLE_LIQUID_GLASS_STATIC_PROFILE_H

#include "liquid_glass_materialize.h"

#include <stdbool.h>
#include <stdint.h>

enum { WALLE_LG_PROFILE_PAYLOAD_BYTE_COUNT = 258 };

struct walle_lg_static_profile_request {
    enum walle_lg_material material;
    enum walle_lg_appearance appearance;
    float width;
    float height;
    uint32_t source_virtual_width;
    uint32_t source_virtual_height;
};

struct walle_lg_profile_payload {
    uint8_t byte[WALLE_LG_PROFILE_PAYLOAD_BYTE_COUNT];
};

[[nodiscard]] bool walle_lg_static_profile(
    const struct walle_lg_static_profile_request *request,
    struct walle_lg_profile_payload *result
);

#endif
