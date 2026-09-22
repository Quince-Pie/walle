#ifndef WALLE_VULKAN_RENDERER_H
#define WALLE_VULKAN_RENDERER_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <wayland-client-core.h>

#include "capture.h"

struct wl_registry;
struct wl_surface;
struct walle_vk_renderer;
struct walle_vk_output;

/* Input bytes are opaque, encoded sRGB RGBA8, one full output-sized image. */
struct walle_vk_image_layer
{
    size_t  offset, size;
    int32_t width, height;
};
struct walle_vk_vertex
{
    float position[2], local[2], source_uv[2];
};
static_assert(sizeof(struct walle_vk_vertex) == 24);

enum walle_vk_pass : uint8_t
{
    WALLE_VK_REVEAL,
    WALLE_VK_GLASS_REGULAR,
    WALLE_VK_GLASS_CLEAR,
    WALLE_VK_FACE,
    WALLE_VK_TINT_MASK,
    WALLE_VK_TINT_GRADIENT,
    WALLE_VK_TINT_COMPOSITE,
    WALLE_VK_HIGHLIGHT,
    WALLE_VK_PRODUCT_FINISH,
    WALLE_VK_PASS_COUNT
};

/* All spans are borrowed until render returns, and copied before GPU use.
 * Consecutive tint-mask or tint-gradient draws form one target pass: its first
 * draw clears that target, subsequent draws preserve the accumulated target.
 * Vertices and indices retain caller order. Scissor is x,y,width,height in
 * top-left output pixels. The caller owns source geometry and material rules.
 */
struct walle_vk_draw
{
    enum walle_vk_pass            pass;
    const struct walle_vk_vertex* vertices;
    size_t                        vertex_count;
    const uint32_t*               indices;
    size_t                        index_count;
    int32_t                       scissor[4];
    uint32_t                      shape_mode;
    float                         edr_scale;
    uint8_t                       glass[272];
    uint8_t                       effect[184];
};
struct walle_vk_frame
{
    const struct walle_vk_draw*   draws;
    size_t                        draw_count;
    const struct wm_capture_plan* capture;
    const struct wm_pyramid_plan* pyramid;
    const uint8_t*                tint_ramp_rgba16f; /* 256 * 4 binary16 values, or nullptr */
    float    material_opacity;     /* product finish only; optical packets are unchanged */
    bool     plain_incoming;       /* first boot / completed transition: exact B copy */
    uint8_t* composition_readback; /* optional BGRA8, top-left rows */
    size_t   composition_readback_size;
};
enum walle_vk_frame_status : uint8_t
{
    WALLE_VK_FRAME_OK,
    WALLE_VK_FRAME_RETRY,
    WALLE_VK_FRAME_FATAL
};
[[nodiscard]]
bool walle_vk_renderer_create(struct wl_display*, const char*, struct walle_vk_renderer**);
/* Same device, shaders and frame recorder; no Wayland object or export image. */
[[nodiscard]]
bool walle_vk_renderer_create_offscreen(const char*, struct walle_vk_renderer**);
[[nodiscard]]
bool walle_vk_renderer_bind_linux_dmabuf(struct walle_vk_renderer*,
                                         struct wl_registry*,
                                         uint32_t,
                                         uint32_t);
[[nodiscard]]
bool walle_vk_renderer_linux_dmabuf_ready(const struct walle_vk_renderer*);
/* Validation callbacks can occur on Vulkan implementation threads. The count
 * is atomic and includes ERROR severity only; warnings remain nonfatal. */
[[nodiscard]]
uint64_t walle_vk_renderer_validation_errors(const struct walle_vk_renderer*);
[[nodiscard]]
bool walle_vk_renderer_validation_active(const struct walle_vk_renderer*);
/* Returns the final validation count, including errors emitted during teardown. */
[[nodiscard]]
uint64_t walle_vk_renderer_destroy_checked(struct walle_vk_renderer*);
void     walle_vk_renderer_destroy(struct walle_vk_renderer*);
/* Image ceiling only, not complete graphics admission. Output creation/resize
 * additionally check per-axis framebuffer and viewport limits before allocation.
 * UINT32_MAX before selection means unknown, not guaranteed support. */
[[nodiscard]]
uint32_t walle_vk_renderer_max_image_dimension(const struct walle_vk_renderer*);
[[nodiscard]]
bool walle_vk_output_create(struct walle_vk_renderer*,
                            struct wl_surface*,
                            uint32_t,
                            uint32_t,
                            bool,
                            struct walle_vk_output**);
[[nodiscard]]
bool walle_vk_output_create_offscreen(struct walle_vk_renderer*,
                                      uint32_t,
                                      uint32_t,
                                      struct walle_vk_output**);
[[nodiscard]]
bool walle_vk_output_resize(struct walle_vk_output*, uint32_t, uint32_t);
[[nodiscard]]
bool walle_vk_output_upload(struct walle_vk_output*, int, const struct walle_vk_image_layer*);
[[nodiscard]]
bool walle_vk_output_restore_current(struct walle_vk_output*,
                                     int,
                                     const struct walle_vk_image_layer*);
[[nodiscard]]
enum walle_vk_frame_status walle_vk_output_render(struct walle_vk_output*,
                                                  const struct walle_vk_frame*);
/* Counts only VkDeviceMemory allocations owned by this backend, using actual
 * allocation sizes; driver-internal pipeline/descriptor/query memory is excluded. */
struct walle_vk_memory_stats
{
    uint64_t allocated_bytes, peak_bytes;
    uint32_t allocation_count, peak_allocation_count;
};
struct walle_vk_diagnostics
{
    struct walle_vk_memory_stats renderer_memory;
    uint64_t                     output_bytes, source_bytes, backdrop_bytes, scratch_bytes;
    uint64_t                     effect_bytes, frame_buffer_bytes, readback_bytes, present_bytes;
    uint32_t                     present_image_count;
    bool                         timing_enabled, timing_available, timing_pending, built_backdrop;
    uint64_t                     timed_frame_id;
    double gpu_capture_ns, gpu_draw_ns, gpu_frame_ns, gpu_tail_ns, gpu_total_ns;
};
/* Timing is off by default. Explicit enabling creates four timestamp queries.
 * The frame interval includes capture plus draws, excludes readback/release.
 * The tail reports optional readback and final ownership release separately;
 * gpu_total_ns includes that tail.
 * Timestamp pool reuse occurs only after the owning submission completes. */
[[nodiscard]]
bool walle_vk_output_enable_timing(struct walle_vk_output*, bool enabled);
/* wait=false never blocks. Timing availability describes the latest completed
 * instrumented frame. Memory counters always describe the current allocation state.
 * With timing disabled this call makes no Vulkan query/fence calls. */
[[nodiscard]]
bool walle_vk_output_diagnostics(struct walle_vk_output*, bool wait, struct walle_vk_diagnostics*);
void walle_vk_renderer_memory_stats(const struct walle_vk_renderer*, struct walle_vk_memory_stats*);

/* Keep the last presentation buffer, release both sampled images and all
 * transition allocations. The app retains its CPU source for later restore. */
void walle_vk_output_promote(struct walle_vk_output*);
void walle_vk_output_abort_transition(struct walle_vk_output*);
void walle_vk_output_destroy(struct walle_vk_output*);
#endif
