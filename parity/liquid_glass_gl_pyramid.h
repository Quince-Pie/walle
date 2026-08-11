#ifndef WALLE_LIQUID_GLASS_GL_PYRAMID_H
#define WALLE_LIQUID_GLASS_GL_PYRAMID_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <GL/glcorearb.h>

#include "liquid_glass_raster.h"

struct walle_lg_gl_pyramid_builder;

[[nodiscard]]
struct walle_lg_gl_pyramid_builder* walle_lg_gl_pyramid_builder_create(
    const char* compute_shader);

[[nodiscard]]
bool walle_lg_gl_pyramid_builder_build(
    struct walle_lg_gl_pyramid_builder*      builder,
    GLuint                                   source_texture,
    uint32_t                                 source_width,
    uint32_t                                 source_height,
    const struct walle_lg_transition_frame* frame,
    const struct walle_lg_raster_calibration* calibration);

[[nodiscard]]
GLuint walle_lg_gl_pyramid_builder_texture(
    const struct walle_lg_gl_pyramid_builder* builder);

[[nodiscard]]
bool walle_lg_gl_pyramid_builder_read_rgba8(
    const struct walle_lg_gl_pyramid_builder* builder,
    uint32_t                                  level,
    void*                                     pixels,
    size_t                                    byte_count);

void walle_lg_gl_pyramid_builder_destroy(
    struct walle_lg_gl_pyramid_builder* builder);

#endif
