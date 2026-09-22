#define _GNU_SOURCE
#include "../vulkan_renderer.c"

static unsigned image_calls, destroy_calls, wait_calls;
VkResult        __wrap_vkCreateImage(VkDevice                     device,
                                     const VkImageCreateInfo*     info,
                                     const VkAllocationCallbacks* allocator,
                                     VkImage*                     image)
{
    (void)device;
    (void)info;
    (void)allocator;
    image_calls++;
    *image = VK_NULL_HANDLE;
    return VK_ERROR_FORMAT_NOT_SUPPORTED;
}
void __wrap_vkDestroyImage(VkDevice device, VkImage image, const VkAllocationCallbacks* allocator)
{
    (void)device;
    (void)image;
    (void)allocator;
    destroy_calls++;
}
VkResult __wrap_vkWaitForFences(
    VkDevice device, uint32_t count, const VkFence* fences, VkBool32 all, uint64_t timeout)
{
    (void)device;
    (void)count;
    (void)fences;
    (void)all;
    (void)timeout;
    wait_calls++;
    return VK_ERROR_UNKNOWN;
}
#define CHECK(expression)                                                                          \
    do {                                                                                           \
        if (!(expression)) {                                                                       \
            fprintf(stderr, "bounds check failed line%d: %s\n", __LINE__, #expression);            \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

int main(void)
{
    struct walle_vk_renderer renderer = {.device_ready = true,
                                         .instance     = (VkInstance)(uintptr_t)1,
                                         .device       = (VkDevice)(uintptr_t)1};
    atomic_init(&renderer.validation_error_count, 0);
    renderer.properties.limits = (VkPhysicalDeviceLimits){.maxImageDimension2D   = 32768,
                                                          .maxFramebufferWidth   = 16384,
                                                          .maxFramebufferHeight  = 16384,
                                                          .maxViewportDimensions = {16384, 16384},
                                                          .viewportBoundsRange   = {-32768, 32767}};
    unsigned cases             = 0;
    const struct
    {
        uint32_t width, height;
        bool     valid;
    } device_boundaries[] = {{1, 1, true},
                             {16384, 1, true},
                             {1, 16384, true},
                             {16384, 16384, true},
                             {16385, 1, false},
                             {1, 16385, false},
                             {32768, 1, false},
                             {1, 32768, false},
                             {0, 1, false},
                             {1, 0, false},
                             {UINT32_MAX, 1, false},
                             {1, UINT32_MAX, false}};
    for (size_t i = 0; i < sizeof device_boundaries / sizeof *device_boundaries; i++) {
        CHECK(graphics_extent_valid(
                  &renderer, device_boundaries[i].width, device_boundaries[i].height)
              == device_boundaries[i].valid);
        cases++;
    }
    CHECK(walle_vk_renderer_max_image_dimension(&renderer) == 32768);
    cases++;
    renderer.properties.limits.maxViewportDimensions[0] = 12000;
    renderer.properties.limits.maxFramebufferHeight     = 8192;
    CHECK(graphics_extent_valid(&renderer, 12000, 8192));
    CHECK(!graphics_extent_valid(&renderer, 12001, 8192));
    CHECK(!graphics_extent_valid(&renderer, 12000, 8193));
    CHECK(walle_vk_renderer_max_image_dimension(&renderer) == 32768);
    cases += 4;
    renderer.properties.limits.viewportBoundsRange[1] = 7999.5f;
    CHECK(graphics_extent_valid(&renderer, 7999, 7999));
    CHECK(!graphics_extent_valid(&renderer, 8000, 1));
    CHECK(!graphics_extent_valid(&renderer, 1, 8000));
    CHECK(walle_vk_renderer_max_image_dimension(&renderer) == 32768);
    cases += 4;
    renderer.properties.limits.viewportBoundsRange[0] = .5f;
    CHECK(!graphics_extent_valid(&renderer, 1, 1));
    CHECK(walle_vk_renderer_max_image_dimension(&renderer) == 32768);
    cases += 2;
    renderer.properties.limits.viewportBoundsRange[0] = NAN;
    CHECK(!graphics_extent_valid(&renderer, 1, 1));
    CHECK(walle_vk_renderer_max_image_dimension(&renderer) == 32768);
    cases += 2;
    renderer.properties.limits.viewportBoundsRange[0] = -32768;
    renderer.properties.limits.viewportBoundsRange[1] = INFINITY;
    CHECK(graphics_extent_valid(&renderer, 8192, 8192));
    CHECK(walle_vk_renderer_max_image_dimension(&renderer) == 32768);
    cases += 2;
    renderer.properties.limits.viewportBoundsRange[1]   = 32767;
    renderer.properties.limits.maxFramebufferHeight     = 16384;
    renderer.properties.limits.maxViewportDimensions[0] = 16384;

    struct walle_vk_output output
        = {.renderer = &renderer, .extent = {1920, 1080}, .frame_pending = true};
    struct walle_vk_output* created = (struct walle_vk_output*)(uintptr_t)1;
    CHECK(!walle_vk_output_create(&renderer, nullptr, 16385, 1, false, &created));
    CHECK(!created);
    CHECK(!walle_vk_output_resize(&output, 1, 16385));
    CHECK(output.extent.width == 1920 && output.extent.height == 1080);
    CHECK(output.frame_pending && !renderer.fatal);
    cases += 3;
    struct walle_vk_image   image               = {};
    const VkImageUsageFlags attachment_usages[] = {VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
                                                   VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT,
                                                   VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT,
                                                   VK_IMAGE_USAGE_TRANSIENT_ATTACHMENT_BIT};
    for (size_t i = 0; i < sizeof attachment_usages / sizeof *attachment_usages; i++) {
        CHECK(!create_image(
            &renderer, 16385, 1, WALLE_VK_WALLPAPER_FORMAT, attachment_usages[i], &image));
        cases++;
    }
    struct walle_vk_present_image present = {};
    output.extent                         = (VkExtent2D){16385, 1};
    CHECK(!create_present_image(&output, DRM_FORMAT_XRGB8888, 0, 1, PRESENT_USAGE, &present));
    cases++;
    struct wm_capture_plan capture = {.texture = {16385, 1}, .quad_count = 1};
    struct wm_pyramid_plan pyramid = {};
    struct walle_vk_frame  frame   = {.capture = &capture, .pyramid = &pyramid};
    output.capture.handle          = (VkImage)(uintptr_t)2;
    CHECK(!ensure_backdrop(&output, &frame));
    CHECK(output.capture.handle == (VkImage)(uintptr_t)2);
    cases++;
    CHECK(image_calls == 0 && destroy_calls == 0 && wait_calls == 0);

    /* Source/compute images retain the separate image limit. An injected driver
     * rejection proves these legal dimensions reached creation, without a GPU. */
    CHECK(image_extent_valid(&renderer, 32768, 1));
    CHECK(!image_extent_valid(&renderer, 32769, 1));
    CHECK(!create_image(
        &renderer, 20000, 1, WALLE_VK_WALLPAPER_FORMAT, VK_IMAGE_USAGE_STORAGE_BIT, &image));
    CHECK(image_calls == 1);
    CHECK(!create_mip_image(&renderer, 20000, 1, 1, &image));
    CHECK(image_calls == 2);
    cases += 4;
    CHECK(renderer.memory_stats.allocated_bytes == 0
          && renderer.memory_stats.allocation_count == 0);
    printf(
        "bounds controls PASS cases=%u rejected graphics reached no image "
        "creation/destruction/fence wait; storage-only reached injected creation; no Vulkan "
        "instance/device/GPU used\n",
        cases);
    return 0;
}
