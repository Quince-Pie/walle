#ifndef WALLE_VULKAN_RENDERER_H
#define WALLE_VULKAN_RENDERER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <wayland-client-core.h>

struct wl_registry;
struct wl_surface;

#include "parity/liquid_glass_reveal_mask_model.h"

struct walle_vk_renderer;
struct walle_vk_output;

struct walle_vk_image_layer
{
    size_t  offset;
    size_t  size;
    int32_t width;
    int32_t height;
};

struct walle_vk_frame
{
    const struct walle_lg_reveal_mask_geometry* geometry;
    float                                       progress;
    float                                       variant;
    float                                       center_top_left_x;
    float                                       center_top_left_y;
    float                                       radius;
    bool                                        first_boot;
    /* Compose with Apple's measured reveal blend instead of the Liquid Glass
     * material.  Only the process-capture gate sets this; ordinary
     * transitions always keep the material. */
    bool                                        apple_reveal_blend;
    /* Apple takes the system appearance as an input: 0 dark, 1 light. */
    float                                       appearance;
    /* Device pixels per point.  The refraction band is an ABSOLUTE width -
     * measured identical to 0.2 px across a fourfold range of element sizes -
     * so the shader needs the scale to convert it, unlike everything else here
     * which is already in the output's own pixels. */
    float                                       output_scale;
    /* Glass.tint(Color?) in sRGB 0..1; negative red means untinted. */
    float                                       tint[3];

    /* Optional diagnostic destination, tightly packed in top-left row order. */
    uint8_t* mask_readback;
    size_t   mask_readback_size;
    uint8_t* composition_readback;
    size_t   composition_readback_size;
};

enum walle_vk_frame_status : uint8_t
{
    WALLE_VK_FRAME_OK = 0,
    WALLE_VK_FRAME_RETRY,
    WALLE_VK_FRAME_FATAL,
};

[[nodiscard]]
bool walle_vk_renderer_create(struct wl_display*         display,
                              const char*                device_selector,
                              struct walle_vk_renderer** result);

[[nodiscard]]
bool walle_vk_renderer_bind_linux_dmabuf(struct walle_vk_renderer* renderer,
                                         struct wl_registry*       registry,
                                         uint32_t                  name,
                                         uint32_t                  version);

[[nodiscard]]
bool walle_vk_renderer_linux_dmabuf_ready(const struct walle_vk_renderer* renderer);

void walle_vk_renderer_destroy(struct walle_vk_renderer* renderer);

[[nodiscard]]
uint32_t walle_vk_renderer_max_image_dimension(const struct walle_vk_renderer* renderer);

[[nodiscard]]
bool walle_vk_output_create(struct walle_vk_renderer* renderer,
                            struct wl_surface*        surface,
                            uint32_t                  width,
                            uint32_t                  height,
                            bool                      enable_composition_readback,
                            struct walle_vk_output**  result);

[[nodiscard]]
bool walle_vk_output_resize(struct walle_vk_output* output, uint32_t width, uint32_t height);

/* The measured backdrop mixture, computed on the GPU at upload time.  All
 * sigmas are in OUTPUT pixels; matrices are row-major colorant transforms.
 * Pass nullptr to copy the glass bytes from glass_fd instead (identity, and
 * the WALLE_GLASS_BAKE=cpu replay path). */
struct walle_vk_glass_bake
{
    float narrow_sigma;
    float wide_sigma;
    float narrow_weight;
    float narrow_chroma_weight;
    float to_panel[9];
    float from_panel[9];
    bool  panel_space;
    /* Wide-field mechanism: 0 = Gaussian on the 8x-reduced grid; N > 0 = the
     * measured mip chain, N gauss5-prefiltered 2x rounds down and tent
     * rounds back up, with an optional Gaussian on the coarsest grid. */
    int   chain_levels;
    float chain_coarse_sigma;
    /* > 0: the cascade-warp mechanism (session 194) - the wide Gaussian runs
     * on the NARROW-blurred field power-warped per channel by this exponent,
     * and the far sample is un-warped in the mix.  Uniform fields are fixed
     * points, so the flat calibration is untouched. */
    float cascade_exponent;
    /* With cascade_exponent > 0: apply the power to the INVERTED signal,
     * warp(v) = 1 - (1-v)^p - light regular's measured identity (p = 3,
     * session 195). */
    bool  cascade_flip;
    /* With cascade_exponent > 0: warp the far field's LUMA only; chroma
     * mixes from the un-warped far field.  Identical on gray content. */
    bool  cascade_luma;
    /* > 0: the LUMA-GATED warp (session 196), U(v, Y) = lerp(v, v^p,
     * A*Y^q) with p = cascade_exponent, A = this gain and q =
     * cascade_gate_power; the un-warp is self-consistent (gate keyed on the
     * un-warped field's own luma) so uniform fields are exact fixed points.
     * Overrides flip/luma modes. */
    float cascade_gate;
    float cascade_gate_power;
};

[[nodiscard]]
bool walle_vk_output_upload(struct walle_vk_output*            output,
                            int                                standard_fd,
                            const struct walle_vk_image_layer* standard,
                            int                                glass_fd,
                            const struct walle_vk_image_layer* glass,
                            const struct walle_vk_glass_bake*  bake);

[[nodiscard]]
bool walle_vk_output_restore_current(struct walle_vk_output*            output,
                                     int                                standard_fd,
                                     const struct walle_vk_image_layer* standard,
                                     int                                glass_fd,
                                     const struct walle_vk_image_layer* glass);

[[nodiscard]]
enum walle_vk_frame_status walle_vk_output_render(struct walle_vk_output*      output,
                                                  const struct walle_vk_frame* frame);

/* Retain the incoming wallpaper as the sole idle image and release all
 * outgoing and transition-only GPU allocations. */
void walle_vk_output_promote(struct walle_vk_output* output);

void walle_vk_output_abort_transition(struct walle_vk_output* output);

void walle_vk_output_destroy(struct walle_vk_output* output);

#endif
