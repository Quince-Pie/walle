#ifndef WM_MATERIAL_MATH_H
#define WM_MATERIAL_MATH_H
#include <stdint.h>
float    wm_float(uint32_t bits);
uint32_t wm_float_bits(float value);
double   wm_double(uint64_t bits);
uint16_t wm_half(float value);
float    wm_powf(float x, float y);
void     wm_sincosf(float angle, float* sine, float* cosine);
float    wm_cosf(float angle);
float    wm_srgb_decode(float value);
float    wm_srgb_encode(float value);
void wm_tint_matrix(const uint8_t rgba[4], bool dark, bool active, float opacity, float out[20]);
void wm_ycc_adjust(int inverse, float black, float white, float saturation, float out[20]);
void wm_matrix_multiply(const float a[20], const float b[20], float out[20]);
#endif
