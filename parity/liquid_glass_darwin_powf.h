#ifndef WALLE_LIQUID_GLASS_DARWIN_POWF_H
#define WALLE_LIQUID_GLASS_DARWIN_POWF_H

#include <stdbool.h>

/* This is the measured macOS 26.6.1 positive-normal fast path, not a
 * general-purpose powf. Inputs outside that path are rejected. */
[[nodiscard]] bool walle_lg_darwin_powf_positive_normal(
    float base,
    float exponent,
    float *result
);

[[nodiscard]] bool walle_lg_darwin_powf_2_4(float base, float *result);

[[nodiscard]] bool walle_lg_darwin_powf_1_over_2_2(float base, float* result);

#endif
