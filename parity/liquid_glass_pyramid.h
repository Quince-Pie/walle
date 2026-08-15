#ifndef WALLE_LIQUID_GLASS_PYRAMID_H
#define WALLE_LIQUID_GLASS_PYRAMID_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "liquid_glass_raster.h"
#include "liquid_glass_static_regular.h"

constexpr uint32_t WALLE_LG_MAX_PYRAMID_LEVELS = 8;

struct walle_lg_pyramid_level
{
    uint32_t       width;
    uint32_t       height;
    size_t         byte_count;
    unsigned char* bgra8;
};

struct walle_lg_pyramid
{
    uint32_t                      level_count;
    struct walle_lg_pyramid_level levels[WALLE_LG_MAX_PYRAMID_LEVELS];
};

struct walle_lg_dynamic_regular_backdrop
{
    struct walle_lg_pyramid_level producer;
    struct walle_lg_pyramid       pyramid;
};

[[nodiscard]]
bool walle_lg_build_static_regular_pyramid(const unsigned char* wallpaper_rgba8,
                                           size_t               wallpaper_byte_count,
                                           const struct walle_lg_static_regular_request* request,
                                           struct walle_lg_pyramid*                      result);

/*
 * Full-frame material backdrop: the DOWNSAMPLE_4 producer kernel followed by
 * the copy-base and AGX2 mip kernels, applied to the whole wallpaper with
 * edge-clamped taps so arbitrary extents are admitted.  Level 0 is the
 * producer (wallpaper / 4, aligned up so every downsample sees even extents);
 * level N is wallpaper / 2^(N + 2).  Levels store BGRA8 rows bottom-up, the
 * same convention as the platter pyramid above.
 */
[[nodiscard]]
bool walle_lg_wallpaper_backdrop_level_extent(uint32_t width,
                                              uint32_t height,
                                              uint32_t level_count,
                                              uint32_t level,
                                              uint32_t extent[static 2]);

[[nodiscard]]
bool walle_lg_build_wallpaper_backdrop(const unsigned char*     wallpaper_rgba8,
                                       uint32_t                 width,
                                       uint32_t                 height,
                                       uint32_t                 level_count,
                                       struct walle_lg_pyramid* result);

[[nodiscard]]
bool walle_lg_build_dynamic_regular_backdrop(const unsigned char* source_bgra8,
                                             size_t               source_byte_count,
                                             uint32_t             source_width,
                                             uint32_t             source_height,
                                             const struct walle_lg_transition_frame*   frame,
                                             const struct walle_lg_raster_calibration* calibration,
                                             struct walle_lg_dynamic_regular_backdrop* result);

void walle_lg_destroy_pyramid(struct walle_lg_pyramid* pyramid);

void walle_lg_destroy_dynamic_regular_backdrop(struct walle_lg_dynamic_regular_backdrop* backdrop);

#endif
