#ifndef WALLE_LIQUID_GLASS_RESOLVED_COLOR_H
#define WALLE_LIQUID_GLASS_RESOLVED_COLOR_H

#include <stdbool.h>

struct walle_lg_resolved_color {
    float linear_rgba[4];
};

[[nodiscard]] bool walle_lg_resolved_color_public_components(
    const struct walle_lg_resolved_color *color,
    float *components
);

[[nodiscard]] bool walle_lg_resolved_color_from_public_components(
    const float *components,
    struct walle_lg_resolved_color *color
);

[[nodiscard]] bool walle_lg_mix_resolved_color(
    const struct walle_lg_resolved_color *from,
    const struct walle_lg_resolved_color *to,
    double fraction,
    struct walle_lg_resolved_color *result
);

#endif
