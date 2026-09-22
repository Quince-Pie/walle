#define _GNU_SOURCE

#include "vulkan_renderer.h"

#include <assert.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/dma-buf.h>
#include <math.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdckdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <unistd.h>
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "protocols/linux-dmabuf-v1.h"

constexpr VkFormat          WALLE_VK_PRESENT_FORMAT       = VK_FORMAT_B8G8R8A8_UNORM;
constexpr VkFormat          WALLE_VK_WALLPAPER_FORMAT     = VK_FORMAT_R8G8B8A8_UNORM;
constexpr uint32_t          WALLE_VK_REQUIRED_API_VERSION = VK_API_VERSION_1_4;
constexpr VkImageUsageFlags PRESENT_USAGE                 = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT
                                                            | VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT
                                                            | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
enum
{
    PIPE_WALLPAPER = WALLE_VK_PASS_COUNT,
    PIPE_COUNT,
    LAYOUT_GRAPHICS = 0,
    LAYOUT_CAPTURE  = 1,
    LAYOUT_COMPUTE  = 2,
    LAYOUT_COUNT    = 3
};
alignas(4) static const uint8_t spv_glassVertex[] = {
#embed "build/shaders/glassVertex.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_revealFragment[] = {
#embed "build/shaders/revealFragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_regularFragment[] = {
#embed "build/shaders/regularFragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_clearFragment[] = {
#embed "build/shaders/clearFragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_faceFragment_local[] = {
#embed "build/shaders/faceFragment_local.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_tintMaskFragment[] = {
#embed "build/shaders/tintMaskFragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_tintGradientFragment_local[] = {
#embed "build/shaders/tintGradientFragment_local.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_tintCompositeFragment[] = {
#embed "build/shaders/tintCompositeFragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_highlightFragment_local[] = {
#embed "build/shaders/highlightFragment_local.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_productFinishFragment_local[] = {
#embed "build/shaders/productFinishFragment_local.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_wallpaperFragment[] = {
#embed "build/shaders/wallpaperFragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_captureCopyFragment[] = {
#embed "build/shaders/captureCopyFragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_captureVertex[] = {
#embed "build/shaders/captureVertex.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_capture4Fragment[] = {
#embed "build/shaders/capture4Fragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_capture6Fragment[] = {
#embed "build/shaders/capture6Fragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_capture8Fragment[] = {
#embed "build/shaders/capture8Fragment.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_lg_blur_copy_base_mip[] = {
#embed "build/shaders/lg_blur_copy_base_mip.spv" if_empty(0)
};
alignas(4) static const uint8_t spv_lg_blur_downsample_agx2[] = {
#embed "build/shaders/lg_blur_downsample_agx2.spv" if_empty(0)
};
struct walle_vk_memory
{
    VkDeviceMemory            handle;
    VkDeviceSize              size;
    VkMemoryPropertyFlags     properties;
    void*                     mapped;
    struct walle_vk_renderer* owner;
};

struct walle_vk_buffer
{
    VkBuffer               handle;
    struct walle_vk_memory memory;
    VkDeviceSize           capacity;
};

struct walle_vk_image
{
    VkImage                handle;
    VkImageView            view;
    struct walle_vk_memory memory;
    uint32_t               width;
    uint32_t               height;
    VkFormat               format;
    uint32_t               mip_count;
    VkImageView            mip_views[32];
};

struct walle_vk_dmabuf_format
{
    uint32_t format;
    uint32_t padding;
    uint64_t modifier;
};

struct walle_vk_dmabuf_candidate
{
    uint32_t format;
    uint64_t modifier;
};

struct walle_vk_dmabuf_feedback
{
    struct zwp_linux_dmabuf_v1*          factory;
    struct zwp_linux_dmabuf_feedback_v1* object;
    struct walle_vk_dmabuf_format*       table;
    size_t                               table_size;
    struct walle_vk_dmabuf_candidate*    candidates;
    size_t                               candidate_count;
    size_t                               candidate_capacity;
    bool                                 ready;
    bool                                 failed;
};

struct walle_vk_output;

struct walle_vk_present_image
{
    struct walle_vk_output* output;
    struct walle_vk_image   image;
    struct wl_buffer*       buffer;
    VkImageLayout           layout;
    bool                    busy;
    bool                    foreign_owned;
    int                     dmabuf_fd;
    bool                    has_dmabuf_fd;
};

struct walle_vk_renderer
{
    struct wl_display*                           display;
    char*                                        device_selector;
    VkInstance                                   instance;
    VkDebugUtilsMessengerEXT                     debug_messenger;
    VkPhysicalDevice                             physical_device;
    VkPhysicalDeviceProperties                   properties;
    VkPhysicalDeviceMemoryProperties             memory_properties;
    VkPhysicalDeviceVulkan14Properties           properties14;
    VkDevice                                     device;
    uint32_t                                     queue_family;
    VkQueue                                      queue;
    PFN_vkGetMemoryFdKHR                         get_memory_fd;
    PFN_vkGetImageDrmFormatModifierPropertiesEXT get_image_drm_format_modifier_properties;
    PFN_vkGetSemaphoreFdKHR                      get_semaphore_fd;
    PFN_vkImportSemaphoreFdKHR                   import_semaphore_fd;
    VkDescriptorSetLayout                        set_layout[LAYOUT_COUNT];
    VkPipelineLayout                             pipeline_layout[LAYOUT_COUNT];
    VkPipeline                   pipelines[PIPE_COUNT], capture_pipelines[4], compute_pipelines[2];
    VkSampler                    linear_sampler;
    VkCommandPool                upload_command_pool;
    VkCommandBuffer              upload_command_buffer;
    VkFence                      upload_fence;
    atomic_uint_fast64_t         validation_error_count;
    struct walle_vk_memory_stats memory_stats;
    struct walle_vk_dmabuf_feedback dmabuf;
    bool                            upload_pending, validation_enabled, device_ready, fatal;
};
struct walle_vk_output
{
    struct walle_vk_renderer*     renderer;
    VkSurfaceKHR                  surface;
    struct wl_surface*            wayland_surface;
    VkExtent2D                    extent;
    struct walle_vk_present_image present_images[2];
    uint32_t                      next_present_image, last_present_image, idle_present_image;
    uint32_t                      present_drm_format, present_plane_count;
    uint64_t                      present_modifier;
    bool                          compact_present;
    VkCommandPool                 command_pool;
    VkCommandBuffer               command_buffer;
    VkFence                       frame_fence;
    bool                          frame_pending, composition_readback_enabled;
    VkSemaphore                   acquire_semaphore, render_semaphore;
    VkDescriptorPool              descriptor_pool;
    uint32_t                      descriptor_capacity;
    struct walle_vk_image       current, incoming, capture, pyramid, blur_scratch, tint, mask, ramp;
    struct wm_capture_plan      capture_plan;
    struct wm_pyramid_plan      pyramid_plan;
    bool                        backdrop_ready, tint_ready, ramp_ready;
    uint8_t                     ramp_bytes[2048];
    struct walle_vk_buffer      frame_buffer, readback_buffer;
    VkDeviceSize                cursor;
    VkQueryPool                 timestamp_pool;
    uint32_t                    timestamp_bits;
    bool                        timing_pending, pending_built_backdrop;
    uint64_t                    timed_frame_id;
    struct walle_vk_diagnostics last_timing;
};
struct shader_blob
{
    const uint8_t* data;
    size_t         size;
    const char*    entry;
};
#define SHADER(n, e)                                                                               \
    (struct shader_blob)                                                                           \
    {                                                                                              \
        spv_##n, sizeof spv_##n, e                                                                 \
    }
struct glass_push
{
    float    resolution[2], edr;
    uint32_t mode;
    float    material_opacity, reserved[3];
};
static_assert(sizeof(struct glass_push) == 32);
static bool device_candidate(struct walle_vk_renderer*,
                             VkPhysicalDevice,
                             VkSurfaceKHR,
                             uint32_t*,
                             VkPhysicalDeviceProperties2*,
                             VkPhysicalDeviceVulkan14Properties*);
static bool create_descriptor_layouts(struct walle_vk_renderer*);
static bool create_pipeline_layouts(struct walle_vk_renderer*);
static bool create_pipelines(struct walle_vk_renderer*);
static bool collect_timing(struct walle_vk_output*);
static void dmabuf_feedback_reset_table(struct walle_vk_dmabuf_feedback* feedback)
{
    if (feedback->table)
        munmap(feedback->table, feedback->table_size);
    feedback->table           = nullptr;
    feedback->table_size      = 0;
    feedback->candidate_count = 0;
    feedback->ready           = false;
}

static void dmabuf_feedback_done(void* data, struct zwp_linux_dmabuf_feedback_v1* object)
{
    (void)object;
    auto feedback   = (struct walle_vk_dmabuf_feedback*)data;
    feedback->ready = !feedback->failed && feedback->table && feedback->candidate_count != 0;
}

static void dmabuf_feedback_format_table(void*                                data,
                                         struct zwp_linux_dmabuf_feedback_v1* object,
                                         int32_t                              fd,
                                         uint32_t                             size)
{
    (void)object;
    auto feedback = (struct walle_vk_dmabuf_feedback*)data;
    dmabuf_feedback_reset_table(feedback);
    if (fd < 0 || size == 0 || size % sizeof(struct walle_vk_dmabuf_format) != 0) {
        feedback->failed = true;
        if (fd >= 0)
            close(fd);
        return;
    }
    void* table = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
    int   saved = errno;
    close(fd);
    if (table == MAP_FAILED) {
        errno            = saved;
        feedback->failed = true;
        return;
    }
    feedback->table      = table;
    feedback->table_size = size;
    feedback->failed     = false;
}

static void dmabuf_feedback_device(void*                                data,
                                   struct zwp_linux_dmabuf_feedback_v1* object,
                                   struct wl_array*                     device)
{
    (void)object;
    auto feedback = (struct walle_vk_dmabuf_feedback*)data;
    if (!device || device->size != sizeof(dev_t))
        feedback->failed = true;
}

static void dmabuf_feedback_tranche_done(void* data, struct zwp_linux_dmabuf_feedback_v1* object)
{
    (void)data;
    (void)object;
}

static bool dmabuf_feedback_append(struct walle_vk_dmabuf_feedback* feedback,
                                   uint32_t                         format,
                                   uint64_t                         modifier)
{
    if ((format != DRM_FORMAT_XRGB8888 && format != DRM_FORMAT_ARGB8888)
        || modifier == DRM_FORMAT_MOD_INVALID)
        return true;
    for (size_t index = 0; index < feedback->candidate_count; ++index) {
        if (feedback->candidates[index].format == format
            && feedback->candidates[index].modifier == modifier)
            return true;
    }
    if (feedback->candidate_count == feedback->candidate_capacity) {
        size_t capacity = feedback->candidate_capacity ? feedback->candidate_capacity * 2 : 16;
        size_t bytes;
        if (capacity < feedback->candidate_capacity
            || ckd_mul(&bytes, capacity, sizeof *feedback->candidates))
            return false;
        void* candidates = realloc(feedback->candidates, bytes);
        if (!candidates)
            return false;
        feedback->candidates         = candidates;
        feedback->candidate_capacity = capacity;
    }
    feedback->candidates[feedback->candidate_count++] = (struct walle_vk_dmabuf_candidate){
        .format   = format,
        .modifier = modifier,
    };
    return true;
}

static void dmabuf_feedback_tranche_formats(void*                                data,
                                            struct zwp_linux_dmabuf_feedback_v1* object,
                                            struct wl_array*                     indices)
{
    (void)object;
    auto feedback = (struct walle_vk_dmabuf_feedback*)data;
    if (!feedback->table || !indices || indices->size % sizeof(uint16_t) != 0) {
        feedback->failed = true;
        return;
    }
    size_t table_count = feedback->table_size / sizeof *feedback->table;
    size_t count       = indices->size / sizeof(uint16_t);
    for (size_t offset = 0; offset < count; ++offset) {
        uint16_t table_index;
        memcpy(&table_index,
               (const uint8_t*)indices->data + offset * sizeof table_index,
               sizeof table_index);
        if (table_index >= table_count
            || !dmabuf_feedback_append(feedback,
                                       feedback->table[table_index].format,
                                       feedback->table[table_index].modifier)) {
            feedback->failed = true;
            return;
        }
    }
}

static void dmabuf_feedback_tranche_flags(void*                                data,
                                          struct zwp_linux_dmabuf_feedback_v1* object,
                                          uint32_t                             flags)
{
    (void)data;
    (void)object;
    (void)flags;
}

static const struct zwp_linux_dmabuf_feedback_v1_listener dmabuf_feedback_listener = {
    .done                  = dmabuf_feedback_done,
    .format_table          = dmabuf_feedback_format_table,
    .main_device           = dmabuf_feedback_device,
    .tranche_done          = dmabuf_feedback_tranche_done,
    .tranche_target_device = dmabuf_feedback_device,
    .tranche_formats       = dmabuf_feedback_tranche_formats,
    .tranche_flags         = dmabuf_feedback_tranche_flags,
};

static const char* vk_result_name(VkResult result)
{
    switch (result) {
        case VK_SUCCESS:
            return "VK_SUCCESS";
        case VK_NOT_READY:
            return "VK_NOT_READY";
        case VK_TIMEOUT:
            return "VK_TIMEOUT";
        case VK_EVENT_SET:
            return "VK_EVENT_SET";
        case VK_EVENT_RESET:
            return "VK_EVENT_RESET";
        case VK_INCOMPLETE:
            return "VK_INCOMPLETE";
        case VK_ERROR_OUT_OF_HOST_MEMORY:
            return "VK_ERROR_OUT_OF_HOST_MEMORY";
        case VK_ERROR_OUT_OF_DEVICE_MEMORY:
            return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
        case VK_ERROR_INITIALIZATION_FAILED:
            return "VK_ERROR_INITIALIZATION_FAILED";
        case VK_ERROR_DEVICE_LOST:
            return "VK_ERROR_DEVICE_LOST";
        case VK_ERROR_MEMORY_MAP_FAILED:
            return "VK_ERROR_MEMORY_MAP_FAILED";
        case VK_ERROR_LAYER_NOT_PRESENT:
            return "VK_ERROR_LAYER_NOT_PRESENT";
        case VK_ERROR_EXTENSION_NOT_PRESENT:
            return "VK_ERROR_EXTENSION_NOT_PRESENT";
        case VK_ERROR_FEATURE_NOT_PRESENT:
            return "VK_ERROR_FEATURE_NOT_PRESENT";
        case VK_ERROR_INCOMPATIBLE_DRIVER:
            return "VK_ERROR_INCOMPATIBLE_DRIVER";
        case VK_ERROR_TOO_MANY_OBJECTS:
            return "VK_ERROR_TOO_MANY_OBJECTS";
        case VK_ERROR_FORMAT_NOT_SUPPORTED:
            return "VK_ERROR_FORMAT_NOT_SUPPORTED";
        case VK_ERROR_SURFACE_LOST_KHR:
            return "VK_ERROR_SURFACE_LOST_KHR";
        case VK_ERROR_NATIVE_WINDOW_IN_USE_KHR:
            return "VK_ERROR_NATIVE_WINDOW_IN_USE_KHR";
        case VK_SUBOPTIMAL_KHR:
            return "VK_SUBOPTIMAL_KHR";
        case VK_ERROR_OUT_OF_DATE_KHR:
            return "VK_ERROR_OUT_OF_DATE_KHR";
        default:
            return "unknown VkResult";
    }
}

static bool vk_check(VkResult result, const char* operation)
{
    if (result == VK_SUCCESS)
        return true;
    fprintf(stderr, "[Vulkan] %s failed: %s (%d)\n", operation, vk_result_name(result), result);
    return false;
}

static VKAPI_ATTR VkBool32 VKAPI_CALL
debug_callback(VkDebugUtilsMessageSeverityFlagBitsEXT      severity,
               VkDebugUtilsMessageTypeFlagsEXT             type,
               const VkDebugUtilsMessengerCallbackDataEXT* data,
               void*                                       user_data)
{
    (void)type;
    struct walle_vk_renderer* renderer = user_data;
    if (renderer && (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT))
        atomic_fetch_add_explicit(&renderer->validation_error_count, 1, memory_order_relaxed);
    const char* level = severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT     ? "ERROR"
                        : severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT ? "WARN"
                                                                                     : "INFO";
    fprintf(stderr,
            "[Vulkan %s] %s\n",
            level,
            data && data->pMessage ? data->pMessage : "validation message without text");
    return VK_FALSE;
}

static bool instance_extension_available(const char* name)
{
    uint32_t count = 0;
    if (vkEnumerateInstanceExtensionProperties(nullptr, &count, nullptr) != VK_SUCCESS)
        return false;
    VkExtensionProperties* properties = calloc(count, sizeof *properties);
    if (!properties)
        return false;
    bool available
        = vkEnumerateInstanceExtensionProperties(nullptr, &count, properties) == VK_SUCCESS;
    for (uint32_t index = 0; available && index < count; ++index) {
        if (strcmp(properties[index].extensionName, name) == 0) {
            free(properties);
            return true;
        }
    }
    free(properties);
    return false;
}

static bool instance_layer_available(const char* name)
{
    uint32_t count = 0;
    if (vkEnumerateInstanceLayerProperties(&count, nullptr) != VK_SUCCESS)
        return false;
    VkLayerProperties* properties = calloc(count, sizeof *properties);
    if (!properties)
        return false;
    bool available = vkEnumerateInstanceLayerProperties(&count, properties) == VK_SUCCESS;
    for (uint32_t index = 0; available && index < count; ++index) {
        if (strcmp(properties[index].layerName, name) == 0) {
            free(properties);
            return true;
        }
    }
    free(properties);
    return false;
}

static bool device_extension_available(VkPhysicalDevice physical_device, const char* name)
{
    uint32_t count = 0;
    if (vkEnumerateDeviceExtensionProperties(physical_device, nullptr, &count, nullptr)
        != VK_SUCCESS)
        return false;
    VkExtensionProperties* properties = calloc(count, sizeof *properties);
    if (!properties)
        return false;
    bool available
        = vkEnumerateDeviceExtensionProperties(physical_device, nullptr, &count, properties)
          == VK_SUCCESS;
    for (uint32_t index = 0; available && index < count; ++index) {
        if (strcmp(properties[index].extensionName, name) == 0) {
            free(properties);
            return true;
        }
    }
    free(properties);
    return false;
}

static bool track_memory(struct walle_vk_renderer* renderer, struct walle_vk_memory* memory)
{
    uint64_t total;
    if (ckd_add(&total, renderer->memory_stats.allocated_bytes, memory->size)
        || renderer->memory_stats.allocation_count == UINT32_MAX)
        return false;
    memory->owner                          = renderer;
    renderer->memory_stats.allocated_bytes = total;
    renderer->memory_stats.allocation_count++;
    if (total > renderer->memory_stats.peak_bytes)
        renderer->memory_stats.peak_bytes = total;
    if (renderer->memory_stats.allocation_count > renderer->memory_stats.peak_allocation_count)
        renderer->memory_stats.peak_allocation_count = renderer->memory_stats.allocation_count;
    return true;
}
static void destroy_memory(VkDevice device, struct walle_vk_memory* memory)
{
    if (memory->mapped)
        vkUnmapMemory(device, memory->handle);
    if (memory->handle) {
        vkFreeMemory(device, memory->handle, nullptr);
        if (memory->owner) {
            assert(memory->owner->memory_stats.allocated_bytes >= memory->size);
            assert(memory->owner->memory_stats.allocation_count != 0);
            memory->owner->memory_stats.allocated_bytes -= memory->size;
            memory->owner->memory_stats.allocation_count--;
        }
    }
    *memory = (struct walle_vk_memory){};
}

static void destroy_buffer(VkDevice device, struct walle_vk_buffer* buffer)
{
    if (buffer->handle)
        vkDestroyBuffer(device, buffer->handle, nullptr);
    destroy_memory(device, &buffer->memory);
    *buffer = (struct walle_vk_buffer){};
}

static void destroy_image(VkDevice device, struct walle_vk_image* image)
{
    for (uint32_t i = 0; i < image->mip_count; ++i)
        if (image->mip_views[i])
            vkDestroyImageView(device, image->mip_views[i], nullptr);
    if (image->view)
        vkDestroyImageView(device, image->view, nullptr);
    if (image->handle)
        vkDestroyImage(device, image->handle, nullptr);
    destroy_memory(device, &image->memory);
    *image = (struct walle_vk_image){};
}

static bool find_memory_type(const struct walle_vk_renderer* renderer,
                             uint32_t                        type_bits,
                             VkMemoryPropertyFlags           required,
                             VkMemoryPropertyFlags           preferred,
                             uint32_t*                       result)
{
    int      best_score = -1;
    uint32_t best       = UINT32_MAX;
    for (uint32_t index = 0; index < renderer->memory_properties.memoryTypeCount; ++index) {
        VkMemoryPropertyFlags flags = renderer->memory_properties.memoryTypes[index].propertyFlags;
        if ((type_bits & (1u << index)) == 0 || (flags & required) != required)
            continue;
        int score = (int)__builtin_popcount(flags & preferred);
        if (score > best_score) {
            best_score = score;
            best       = index;
        }
    }
    if (best == UINT32_MAX)
        return false;
    *result = best;
    return true;
}

static bool allocate_memory(struct walle_vk_renderer*   renderer,
                            const VkMemoryRequirements* requirements,
                            VkMemoryPropertyFlags       required,
                            VkMemoryPropertyFlags       preferred,
                            bool                        map,
                            struct walle_vk_memory*     result)
{
    uint32_t type_index;
    if (!find_memory_type(renderer, requirements->memoryTypeBits, required, preferred, &type_index))
        return false;

    VkMemoryAllocateInfo allocate_info = {
        .sType           = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize  = requirements->size,
        .memoryTypeIndex = type_index,
    };
    struct walle_vk_memory memory = {
        .size       = requirements->size,
        .properties = renderer->memory_properties.memoryTypes[type_index].propertyFlags,
    };
    if (!vk_check(vkAllocateMemory(renderer->device, &allocate_info, nullptr, &memory.handle),
                  "vkAllocateMemory"))
        return false;
    if (!track_memory(renderer, &memory)) {
        destroy_memory(renderer->device, &memory);
        return false;
    }
    if (map
        && !vk_check(
            vkMapMemory(renderer->device, memory.handle, 0, memory.size, 0, &memory.mapped),
            "vkMapMemory")) {
        destroy_memory(renderer->device, &memory);
        return false;
    }
    *result = memory;
    return true;
}

static bool create_buffer(struct walle_vk_renderer* renderer,
                          VkDeviceSize              size,
                          VkBufferUsageFlags        usage,
                          VkMemoryPropertyFlags     required,
                          VkMemoryPropertyFlags     preferred,
                          bool                      map,
                          struct walle_vk_buffer*   result)
{
    if (size == 0)
        return false;
    VkBufferCreateInfo create_info = {
        .sType       = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size        = size,
        .usage       = usage,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    struct walle_vk_buffer buffer = {.capacity = size};
    if (!vk_check(vkCreateBuffer(renderer->device, &create_info, nullptr, &buffer.handle),
                  "vkCreateBuffer"))
        return false;

    VkMemoryRequirements requirements;
    vkGetBufferMemoryRequirements(renderer->device, buffer.handle, &requirements);
    if (!allocate_memory(renderer, &requirements, required, preferred, map, &buffer.memory)
        || !vk_check(vkBindBufferMemory(renderer->device, buffer.handle, buffer.memory.handle, 0),
                     "vkBindBufferMemory")) {
        destroy_buffer(renderer->device, &buffer);
        return false;
    }
    *result = buffer;
    return true;
}

/* Our graphics paths render over the complete attachment with viewport
 * x=0, y=height, width=width, height=-height. Check exactly those endpoints.
 * Image-only limits remain separate for sampled/storage compute textures. */
static bool
image_extent_valid(const struct walle_vk_renderer* renderer, uint32_t width, uint32_t height)
{
    return width && height && width <= renderer->properties.limits.maxImageDimension2D
           && height <= renderer->properties.limits.maxImageDimension2D;
}
static bool
graphics_extent_valid(const struct walle_vk_renderer* renderer, uint32_t width, uint32_t height)
{
    const VkPhysicalDeviceLimits* limits          = &renderer->properties.limits;
    double                        viewport_width  = (double)(float)width;
    double                        viewport_height = (double)(float)height;
    double                        minimum         = (double)limits->viewportBoundsRange[0];
    double                        maximum         = (double)limits->viewportBoundsRange[1];
    return image_extent_valid(renderer, width, height) && width <= limits->maxFramebufferWidth
           && height <= limits->maxFramebufferHeight
           && viewport_width <= (double)limits->maxViewportDimensions[0]
           && viewport_height <= (double)limits->maxViewportDimensions[1] && !isnan(minimum)
           && !isnan(maximum) && minimum <= 0 && maximum >= 0 && viewport_width <= maximum
           && viewport_height <= maximum;
}
static bool create_image(struct walle_vk_renderer* renderer,
                         uint32_t                  width,
                         uint32_t                  height,
                         VkFormat                  format,
                         VkImageUsageFlags         usage,
                         struct walle_vk_image*    result)
{
    constexpr VkImageUsageFlags attachment_usage
        = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT
          | VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSIENT_ATTACHMENT_BIT;
    if (!image_extent_valid(renderer, width, height)
        || ((usage & attachment_usage) && !graphics_extent_valid(renderer, width, height)))
        return false;
    VkImageCreateInfo create_info = {
        .sType         = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType     = VK_IMAGE_TYPE_2D,
        .format        = format,
        .extent        = {.width = width, .height = height, .depth = 1},
        .mipLevels     = 1,
        .arrayLayers   = 1,
        .samples       = VK_SAMPLE_COUNT_1_BIT,
        .tiling        = VK_IMAGE_TILING_OPTIMAL,
        .usage         = usage,
        .sharingMode   = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    struct walle_vk_image image = {.width = width, .height = height, .format = format};
    if (!vk_check(vkCreateImage(renderer->device, &create_info, nullptr, &image.handle),
                  "vkCreateImage"))
        return false;

    VkMemoryRequirements requirements;
    vkGetImageMemoryRequirements(renderer->device, image.handle, &requirements);
    if (!allocate_memory(renderer,
                         &requirements,
                         VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                         VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                         false,
                         &image.memory)
        || !vk_check(vkBindImageMemory(renderer->device, image.handle, image.memory.handle, 0),
                     "vkBindImageMemory")) {
        destroy_image(renderer->device, &image);
        return false;
    }

    VkImageViewCreateInfo view_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .image = image.handle,
        .viewType = VK_IMAGE_VIEW_TYPE_2D,
        .format = format,
        .components = {
            VK_COMPONENT_SWIZZLE_IDENTITY,
            VK_COMPONENT_SWIZZLE_IDENTITY,
            VK_COMPONENT_SWIZZLE_IDENTITY,
            VK_COMPONENT_SWIZZLE_IDENTITY,
        },
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    if (!vk_check(vkCreateImageView(renderer->device, &view_info, nullptr, &image.view),
                  "vkCreateImageView")) {
        destroy_image(renderer->device, &image);
        return false;
    }
    *result = image;
    return true;
}

static bool present_modifier_exportable(struct walle_vk_renderer* renderer,
                                        uint64_t                  modifier,
                                        VkImageUsageFlags         usage)
{
    VkPhysicalDeviceExternalImageFormatInfo external = {
        .sType      = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    };
    VkPhysicalDeviceImageDrmFormatModifierInfoEXT drm = {
        .sType             = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT,
        .pNext             = &external,
        .drmFormatModifier = modifier,
        .sharingMode       = VK_SHARING_MODE_EXCLUSIVE,
        .queueFamilyIndexCount = 0,
    };
    VkPhysicalDeviceImageFormatInfo2 info = {
        .sType  = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
        .pNext  = &drm,
        .format = WALLE_VK_PRESENT_FORMAT,
        .type   = VK_IMAGE_TYPE_2D,
        .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage  = usage,
    };
    VkExternalImageFormatProperties external_properties = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES,
    };
    VkImageFormatProperties2 properties = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
        .pNext = &external_properties,
    };
    return vkGetPhysicalDeviceImageFormatProperties2(renderer->physical_device, &info, &properties)
               == VK_SUCCESS
           && (external_properties.externalMemoryProperties.externalMemoryFeatures
               & VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT)
                  != 0;
}

static bool select_present_modifier(struct walle_vk_renderer* renderer,
                                    VkImageUsageFlags         usage,
                                    uint32_t*                 drm_format,
                                    uint64_t*                 modifier,
                                    uint32_t*                 plane_count)
{
    VkDrmFormatModifierPropertiesList2EXT list = {
        .sType = VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_2_EXT,
    };
    VkFormatProperties2 properties = {
        .sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2,
        .pNext = &list,
    };
    vkGetPhysicalDeviceFormatProperties2(
        renderer->physical_device, WALLE_VK_PRESENT_FORMAT, &properties);
    if (list.drmFormatModifierCount == 0)
        return false;
    list.pDrmFormatModifierProperties
        = calloc(list.drmFormatModifierCount, sizeof *list.pDrmFormatModifierProperties);
    if (!list.pDrmFormatModifierProperties)
        return false;
    vkGetPhysicalDeviceFormatProperties2(
        renderer->physical_device, WALLE_VK_PRESENT_FORMAT, &properties);

    const VkFormatFeatureFlags2 required
        = VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT | VK_FORMAT_FEATURE_2_TRANSFER_SRC_BIT;
    bool found = false;
    for (unsigned format_pass = 0; format_pass < 2 && !found; ++format_pass) {
        uint32_t preferred_format = format_pass == 0 ? DRM_FORMAT_XRGB8888 : DRM_FORMAT_ARGB8888;
        for (size_t candidate_index = 0;
             candidate_index < renderer->dmabuf.candidate_count && !found;
             ++candidate_index) {
            const struct walle_vk_dmabuf_candidate* candidate
                = &renderer->dmabuf.candidates[candidate_index];
            if (candidate->format != preferred_format)
                continue;
            for (uint32_t property_index = 0; property_index < list.drmFormatModifierCount;
                 ++property_index) {
                const VkDrmFormatModifierProperties2EXT* property
                    = &list.pDrmFormatModifierProperties[property_index];
                if (property->drmFormatModifier == candidate->modifier
                    && (property->drmFormatModifierTilingFeatures & required) == required
                    && property->drmFormatModifierPlaneCount != 0
                    && property->drmFormatModifierPlaneCount <= 4
                    && present_modifier_exportable(renderer, candidate->modifier, usage)) {
                    *drm_format  = preferred_format;
                    *modifier    = candidate->modifier;
                    *plane_count = property->drmFormatModifierPlaneCount;
                    found        = true;
                    break;
                }
            }
        }
    }
    free(list.pDrmFormatModifierProperties);
    return found;
}

static VkImageAspectFlagBits memory_plane_aspect(uint32_t plane)
{
    switch (plane) {
        case 0:
            return VK_IMAGE_ASPECT_MEMORY_PLANE_0_BIT_EXT;
        case 1:
            return VK_IMAGE_ASPECT_MEMORY_PLANE_1_BIT_EXT;
        case 2:
            return VK_IMAGE_ASPECT_MEMORY_PLANE_2_BIT_EXT;
        case 3:
            return VK_IMAGE_ASPECT_MEMORY_PLANE_3_BIT_EXT;
        default:
            return 0;
    }
}

static void destroy_present_image(VkDevice device, struct walle_vk_present_image* image);

static void present_buffer_released(void* data, struct wl_buffer* buffer)
{
    (void)buffer;
    auto image  = (struct walle_vk_present_image*)data;
    image->busy = false;
    if (image->output && image->output->compact_present
        && image != &image->output->present_images[image->output->idle_present_image])
        destroy_present_image(image->output->renderer->device, image);
}

static const struct wl_buffer_listener present_buffer_listener = {
    .release = present_buffer_released,
};

static void destroy_present_image(VkDevice device, struct walle_vk_present_image* image)
{
    if (image->buffer)
        wl_buffer_destroy(image->buffer);
    if (image->has_dmabuf_fd)
        close(image->dmabuf_fd);
    destroy_image(device, &image->image);
    *image = (struct walle_vk_present_image){};
}

static bool create_present_image(struct walle_vk_output*        output,
                                 uint32_t                       drm_format,
                                 uint64_t                       modifier,
                                 uint32_t                       plane_count,
                                 VkImageUsageFlags              usage,
                                 struct walle_vk_present_image* result)
{
    struct walle_vk_renderer* renderer = output->renderer;
    if (!graphics_extent_valid(renderer, output->extent.width, output->extent.height))
        return false;
    *result                                  = (struct walle_vk_present_image){};
    result->output                           = output;
    VkExternalMemoryImageCreateInfo external = {
        .sType       = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
        .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    };
    VkImageDrmFormatModifierListCreateInfoEXT modifiers = {
        .sType                  = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_LIST_CREATE_INFO_EXT,
        .pNext                  = &external,
        .drmFormatModifierCount = 1,
        .pDrmFormatModifiers    = &modifier,
    };
    VkImageCreateInfo create_info = {
        .sType       = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .pNext       = &modifiers,
        .imageType   = VK_IMAGE_TYPE_2D,
        .format      = WALLE_VK_PRESENT_FORMAT,
        .extent      = {.width = output->extent.width, .height = output->extent.height, .depth = 1},
        .mipLevels   = 1,
        .arrayLayers = 1,
        .samples     = VK_SAMPLE_COUNT_1_BIT,
        .tiling      = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage       = usage,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    result->image.width  = output->extent.width;
    result->image.height = output->extent.height;
    result->image.format = WALLE_VK_PRESENT_FORMAT;
    if (!vk_check(vkCreateImage(renderer->device, &create_info, nullptr, &result->image.handle),
                  "vkCreateImage(dma-buf present)"))
        return false;

    VkMemoryRequirements requirements;
    vkGetImageMemoryRequirements(renderer->device, result->image.handle, &requirements);
    uint32_t memory_type;
    if (!find_memory_type(renderer,
                          requirements.memoryTypeBits,
                          VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                          VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                          &memory_type))
        goto failed;
    VkExportMemoryAllocateInfo export_info = {
        .sType       = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
        .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    };
    VkMemoryDedicatedAllocateInfo dedicated = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
        .pNext = &export_info,
        .image = result->image.handle,
    };
    VkMemoryAllocateInfo allocation = {
        .sType           = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext           = &dedicated,
        .allocationSize  = requirements.size,
        .memoryTypeIndex = memory_type,
    };
    result->image.memory.size = requirements.size;
    result->image.memory.properties
        = renderer->memory_properties.memoryTypes[memory_type].propertyFlags;
    if (!vk_check(
            vkAllocateMemory(renderer->device, &allocation, nullptr, &result->image.memory.handle),
            "vkAllocateMemory(dma-buf present)"))
        goto failed;
    if (!track_memory(renderer, &result->image.memory)
        || !vk_check(vkBindImageMemory(
                         renderer->device, result->image.handle, result->image.memory.handle, 0),
                     "vkBindImageMemory(dma-buf present)"))
        goto failed;

    VkImageViewCreateInfo view_info = {
        .sType    = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .image    = result->image.handle,
        .viewType = VK_IMAGE_VIEW_TYPE_2D,
        .format   = WALLE_VK_PRESENT_FORMAT,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .levelCount = 1,
            .layerCount = 1,
        },
    };
    if (!vk_check(vkCreateImageView(renderer->device, &view_info, nullptr, &result->image.view),
                  "vkCreateImageView(dma-buf present)"))
        goto failed;

    VkImageDrmFormatModifierPropertiesEXT modifier_properties = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_PROPERTIES_EXT,
    };
    if (!vk_check(renderer->get_image_drm_format_modifier_properties(
                      renderer->device, result->image.handle, &modifier_properties),
                  "vkGetImageDrmFormatModifierPropertiesEXT")
        || modifier_properties.drmFormatModifier != modifier)
        goto failed;
    VkMemoryGetFdInfoKHR fd_info = {
        .sType      = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
        .memory     = result->image.memory.handle,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    };
    int memory_fd = -1;
    if (!vk_check(renderer->get_memory_fd(renderer->device, &fd_info, &memory_fd),
                  "vkGetMemoryFdKHR"))
        goto failed;

    struct zwp_linux_buffer_params_v1* params
        = zwp_linux_dmabuf_v1_create_params(renderer->dmabuf.factory);
    bool planes_valid = params != nullptr;
    for (uint32_t plane = 0; planes_valid && plane < plane_count; ++plane) {
        VkImageSubresource subresource = {
            .aspectMask = memory_plane_aspect(plane),
        };
        VkSubresourceLayout layout;
        vkGetImageSubresourceLayout(renderer->device, result->image.handle, &subresource, &layout);
        int plane_fd = fcntl(memory_fd, F_DUPFD_CLOEXEC, 0);
        if (subresource.aspectMask == 0 || layout.offset > UINT32_MAX
            || layout.rowPitch > UINT32_MAX || plane_fd < 0) {
            if (plane_fd >= 0)
                close(plane_fd);
            planes_valid = false;
            break;
        }
        zwp_linux_buffer_params_v1_add(params,
                                       plane_fd,
                                       plane,
                                       (uint32_t)layout.offset,
                                       (uint32_t)layout.rowPitch,
                                       (uint32_t)(modifier >> 32),
                                       (uint32_t)modifier);
        close(plane_fd);
    }
    result->dmabuf_fd     = memory_fd;
    result->has_dmabuf_fd = true;
    if (!planes_valid) {
        if (params)
            zwp_linux_buffer_params_v1_destroy(params);
        goto failed;
    }
    result->buffer = zwp_linux_buffer_params_v1_create_immed(
        params, (int32_t)output->extent.width, (int32_t)output->extent.height, drm_format, 0);
    zwp_linux_buffer_params_v1_destroy(params);
    if (!result->buffer
        || wl_buffer_add_listener(result->buffer, &present_buffer_listener, result) != 0)
        goto failed;
    return true;

failed:
    destroy_present_image(renderer->device, result);
    return false;
}

static bool create_present_slot(struct walle_vk_output* output, uint32_t index)
{
    constexpr VkImageUsageFlags usage = PRESENT_USAGE;
    if (index >= 2 || output->present_images[index].image.handle)
        return false;
    if (!output->wayland_surface) {
        auto p    = &output->present_images[index];
        p->output = output;
        return create_image(output->renderer,
                            output->extent.width,
                            output->extent.height,
                            WALLE_VK_PRESENT_FORMAT,
                            PRESENT_USAGE,
                            &p->image);
    }
    return create_present_image(output,
                                output->present_drm_format,
                                output->present_modifier,
                                output->present_plane_count,
                                usage,
                                &output->present_images[index]);
}

static bool initialize_present_images(struct walle_vk_output* output)
{
    constexpr VkImageUsageFlags usage = PRESENT_USAGE;
    if (!output->wayland_surface)
        return create_present_slot(output, 0);
    if (!select_present_modifier(output->renderer,
                                 usage,
                                 &output->present_drm_format,
                                 &output->present_modifier,
                                 &output->present_plane_count)) {
        fprintf(stderr, "FATAL: no shared Vulkan/linux-dmabuf presentation modifier.\n");
        return false;
    }
    if (!create_present_slot(output, 0)) {
        return false;
    }
    fprintf(stderr,
            "[Vulkan] Adaptive direct buffer: DRM format 0x%08" PRIx32 ", modifier 0x%016" PRIx64
            ", %u plane%s.\n",
            output->present_drm_format,
            output->present_modifier,
            output->present_plane_count,
            output->present_plane_count == 1 ? "" : "s");
    return true;
}

static void image_barrier_queues(VkCommandBuffer       command_buffer,
                                 VkImage               image,
                                 VkPipelineStageFlags2 source_stage,
                                 VkAccessFlags2        source_access,
                                 VkPipelineStageFlags2 destination_stage,
                                 VkAccessFlags2        destination_access,
                                 VkImageLayout         old_layout,
                                 VkImageLayout         new_layout,
                                 uint32_t              source_queue,
                                 uint32_t              destination_queue)
{
    VkImageMemoryBarrier2 barrier = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2,
        .srcStageMask = source_stage,
        .srcAccessMask = source_access,
        .dstStageMask = destination_stage,
        .dstAccessMask = destination_access,
        .oldLayout = old_layout,
        .newLayout = new_layout,
        .srcQueueFamilyIndex = source_queue,
        .dstQueueFamilyIndex = destination_queue,
        .image = image,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    VkDependencyInfo dependency = {
        .sType                   = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
        .imageMemoryBarrierCount = 1,
        .pImageMemoryBarriers    = &barrier,
    };
    vkCmdPipelineBarrier2(command_buffer, &dependency);
}

static void image_barrier(VkCommandBuffer       command_buffer,
                          VkImage               image,
                          VkPipelineStageFlags2 source_stage,
                          VkAccessFlags2        source_access,
                          VkPipelineStageFlags2 destination_stage,
                          VkAccessFlags2        destination_access,
                          VkImageLayout         old_layout,
                          VkImageLayout         new_layout)
{
    image_barrier_queues(command_buffer,
                         image,
                         source_stage,
                         source_access,
                         destination_stage,
                         destination_access,
                         old_layout,
                         new_layout,
                         VK_QUEUE_FAMILY_IGNORED,
                         VK_QUEUE_FAMILY_IGNORED);
}

static void buffer_barrier(VkCommandBuffer       command_buffer,
                           VkPipelineStageFlags2 source_stage,
                           VkAccessFlags2        source_access,
                           VkPipelineStageFlags2 destination_stage,
                           VkAccessFlags2        destination_access)
{
    VkMemoryBarrier2 barrier = {
        .sType         = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2,
        .srcStageMask  = source_stage,
        .srcAccessMask = source_access,
        .dstStageMask  = destination_stage,
        .dstAccessMask = destination_access,
    };
    VkDependencyInfo dependency = {
        .sType              = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
        .memoryBarrierCount = 1,
        .pMemoryBarriers    = &barrier,
    };
    vkCmdPipelineBarrier2(command_buffer, &dependency);
}

static bool string_is_true(const char* value)
{
    return value
           && (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0
               || strcasecmp(value, "yes") == 0);
}

static bool create_debug_messenger(struct walle_vk_renderer* renderer)
{
    PFN_vkCreateDebugUtilsMessengerEXT create_messenger
        = (PFN_vkCreateDebugUtilsMessengerEXT)vkGetInstanceProcAddr(
            renderer->instance, "vkCreateDebugUtilsMessengerEXT");
    if (!create_messenger)
        return false;
    VkDebugUtilsMessengerCreateInfoEXT create_info = {
        .sType           = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT,
        .messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT
                           | VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT,
        .messageType     = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT
                           | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT
                           | VK_DEBUG_UTILS_MESSAGE_TYPE_PERFORMANCE_BIT_EXT,
        .pfnUserCallback = debug_callback,
        .pUserData       = renderer,
    };
    return vk_check(
        create_messenger(renderer->instance, &create_info, nullptr, &renderer->debug_messenger),
        "vkCreateDebugUtilsMessengerEXT");
}

static void destroy_debug_messenger(struct walle_vk_renderer* renderer)
{
    if (!renderer->debug_messenger)
        return;
    PFN_vkDestroyDebugUtilsMessengerEXT destroy_messenger
        = (PFN_vkDestroyDebugUtilsMessengerEXT)vkGetInstanceProcAddr(
            renderer->instance, "vkDestroyDebugUtilsMessengerEXT");
    if (destroy_messenger)
        destroy_messenger(renderer->instance, renderer->debug_messenger, nullptr);
    renderer->debug_messenger = VK_NULL_HANDLE;
}

static bool create_instance(struct walle_vk_renderer* renderer)
{
    uint32_t loader_version = VK_API_VERSION_1_0;
    if (!vk_check(vkEnumerateInstanceVersion(&loader_version), "vkEnumerateInstanceVersion")
        || loader_version < WALLE_VK_REQUIRED_API_VERSION) {
        fprintf(stderr,
                "FATAL: Walle requires Vulkan 1.4; loader exposes %u.%u.%u.\n",
                VK_API_VERSION_MAJOR(loader_version),
                VK_API_VERSION_MINOR(loader_version),
                VK_API_VERSION_PATCH(loader_version));
        return false;
    }

    const char* extensions[3] = {
        VK_KHR_SURFACE_EXTENSION_NAME,
        VK_KHR_WAYLAND_SURFACE_EXTENSION_NAME,
    };
    uint32_t extension_count = renderer->display ? 2u : 0u;
    if (renderer->display
        && (!instance_extension_available(extensions[0])
            || !instance_extension_available(extensions[1]))) {
        fprintf(stderr, "FATAL: Vulkan Wayland WSI extensions are unavailable.\n");
        return false;
    }

#if defined(NDEBUG)
    bool validation_requested = string_is_true(getenv("WALLE_VULKAN_VALIDATION"));
#else
    bool validation_requested
        = !getenv("WALLE_VULKAN_VALIDATION") || string_is_true(getenv("WALLE_VULKAN_VALIDATION"));
#endif
    const char* layers[] = {"VK_LAYER_KHRONOS_validation"};
    if (validation_requested && instance_layer_available(layers[0])
        && instance_extension_available(VK_EXT_DEBUG_UTILS_EXTENSION_NAME)) {
        extensions[extension_count++] = VK_EXT_DEBUG_UTILS_EXTENSION_NAME;
        renderer->validation_enabled  = true;
    } else if (validation_requested) {
        fprintf(
            stderr,
            "[Vulkan] Validation was requested but VK_LAYER_KHRONOS_validation is unavailable.\n");
        if (string_is_true(getenv("WALLE_VULKAN_VALIDATION")))
            return false;
    }

    VkApplicationInfo application_info = {
        .sType              = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName   = "walle",
        .applicationVersion = VK_MAKE_API_VERSION(0, 0, 0, 1),
        .pEngineName        = "walle-vulkan",
        .engineVersion      = VK_MAKE_API_VERSION(0, 0, 0, 1),
        .apiVersion         = WALLE_VK_REQUIRED_API_VERSION,
    };
    VkDebugUtilsMessengerCreateInfoEXT creation_debug = {
        .sType           = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT,
        .messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT
                           | VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT,
        .messageType     = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT
                           | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT
                           | VK_DEBUG_UTILS_MESSAGE_TYPE_PERFORMANCE_BIT_EXT,
        .pfnUserCallback = debug_callback,
        .pUserData       = renderer,
    };
    VkInstanceCreateInfo create_info = {
        .sType                   = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pNext                   = renderer->validation_enabled ? &creation_debug : nullptr,
        .pApplicationInfo        = &application_info,
        .enabledLayerCount       = renderer->validation_enabled ? 1u : 0u,
        .ppEnabledLayerNames     = renderer->validation_enabled ? layers : nullptr,
        .enabledExtensionCount   = extension_count,
        .ppEnabledExtensionNames = extensions,
    };
    if (!vk_check(vkCreateInstance(&create_info, nullptr, &renderer->instance), "vkCreateInstance"))
        return false;
    if (renderer->validation_enabled && !create_debug_messenger(renderer))
        return false;
    return true;
}

static bool
format_supports(VkPhysicalDevice physical_device, VkFormat format, VkFormatFeatureFlags2 required)
{
    VkFormatProperties3 properties3 = {.sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3};
    VkFormatProperties2 properties2 = {
        .sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2,
        .pNext = &properties3,
    };
    vkGetPhysicalDeviceFormatProperties2(physical_device, format, &properties2);
    return (properties3.optimalTilingFeatures & required) == required;
}

static uint32_t wallpaper_device_preference(VkPhysicalDeviceType type)
{
    switch (type) {
        case VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU:
            return 5;
        case VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU:
            return 4;
        case VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU:
            return 3;
        case VK_PHYSICAL_DEVICE_TYPE_OTHER:
            return 2;
        case VK_PHYSICAL_DEVICE_TYPE_CPU:
            return 0;
        default:
            return 0;
    }
}

static const char* physical_device_type_name(VkPhysicalDeviceType type)
{
    switch (type) {
        case VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU:
            return "discrete";
        case VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU:
            return "integrated";
        case VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU:
            return "virtual";
        case VK_PHYSICAL_DEVICE_TYPE_CPU:
            return "cpu";
        case VK_PHYSICAL_DEVICE_TYPE_OTHER:
        default:
            return "other";
    }
}

static bool parse_device_index(const char* selector, uint32_t* result)
{
    if (!selector || !*selector)
        return false;
    errno               = 0;
    char*         end   = nullptr;
    unsigned long index = strtoul(selector, &end, 10);
    if (errno || end == selector || *end || index > UINT32_MAX)
        return false;
    *result = (uint32_t)index;
    return true;
}

static bool device_selector_matches(const char*                       selector,
                                    uint32_t                          index,
                                    const VkPhysicalDeviceProperties* properties)
{
    if (!selector || !*selector || strcasecmp(selector, "auto") == 0)
        return properties->deviceType != VK_PHYSICAL_DEVICE_TYPE_CPU;

    uint32_t requested_index;
    if (parse_device_index(selector, &requested_index))
        return requested_index == index;

    const char* type = physical_device_type_name(properties->deviceType);
    if (strcasecmp(selector, "discrete") == 0 || strcasecmp(selector, "integrated") == 0
        || strcasecmp(selector, "virtual") == 0 || strcasecmp(selector, "cpu") == 0
        || strcasecmp(selector, "other") == 0)
        return strcasecmp(selector, type) == 0;

    return strcasestr(properties->deviceName, selector) != nullptr;
}

static bool create_global_resources(struct walle_vk_renderer* renderer)
{
    VkSamplerCreateInfo sampler_info = {
        .sType        = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO,
        .magFilter    = VK_FILTER_LINEAR,
        .minFilter    = VK_FILTER_LINEAR,
        .mipmapMode   = VK_SAMPLER_MIPMAP_MODE_LINEAR,
        .addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .maxLod       = VK_LOD_CLAMP_NONE,
    };
    if (!vk_check(
            vkCreateSampler(renderer->device, &sampler_info, nullptr, &renderer->linear_sampler),
            "vkCreateSampler"))
        return false;

    VkCommandPoolCreateInfo pool_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags
        = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT | VK_COMMAND_POOL_CREATE_TRANSIENT_BIT,
        .queueFamilyIndex = renderer->queue_family,
    };
    if (!vk_check(vkCreateCommandPool(
                      renderer->device, &pool_info, nullptr, &renderer->upload_command_pool),
                  "vkCreateCommandPool(upload)"))
        return false;
    VkCommandBufferAllocateInfo allocate_info = {
        .sType              = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool        = renderer->upload_command_pool,
        .level              = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    if (!vk_check(vkAllocateCommandBuffers(
                      renderer->device, &allocate_info, &renderer->upload_command_buffer),
                  "vkAllocateCommandBuffers(upload)"))
        return false;
    VkFenceCreateInfo fence_info = {
        .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
        .flags = VK_FENCE_CREATE_SIGNALED_BIT,
    };
    if (!vk_check(vkCreateFence(renderer->device, &fence_info, nullptr, &renderer->upload_fence),
                  "vkCreateFence(upload)"))
        return false;

    return true;
}

static bool initialize_device(struct walle_vk_renderer* renderer, VkSurfaceKHR surface)
{
    if (renderer->device_ready) {
        if (!surface)
            return true;
        VkBool32 supported = VK_FALSE;
        return vkGetPhysicalDeviceSurfaceSupportKHR(
                   renderer->physical_device, renderer->queue_family, surface, &supported)
                   == VK_SUCCESS
               && supported;
    }

    uint32_t count = 0;
    if (!vk_check(vkEnumeratePhysicalDevices(renderer->instance, &count, nullptr),
                  "vkEnumeratePhysicalDevices")
        || count == 0)
        return false;
    VkPhysicalDevice* devices = calloc(count, sizeof *devices);
    if (!devices)
        return false;
    if (!vk_check(vkEnumeratePhysicalDevices(renderer->instance, &count, devices),
                  "vkEnumeratePhysicalDevices(list)")) {
        free(devices);
        return false;
    }

    const char*      requested = renderer->device_selector;
    bool             automatic = !requested || !*requested || strcasecmp(requested, "auto") == 0;
    VkPhysicalDevice selected  = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties2        selected_properties   = {};
    VkPhysicalDeviceVulkan14Properties selected_properties14 = {};
    uint32_t                           selected_queue        = UINT32_MAX;
    uint32_t                           selected_preference   = 0;
    uint32_t                           selected_index        = UINT32_MAX;
    for (uint32_t index = 0; index < count; ++index) {
        VkPhysicalDeviceProperties2        properties2;
        VkPhysicalDeviceVulkan14Properties properties14;
        uint32_t                           queue_family;
        if (!device_candidate(
                renderer, devices[index], surface, &queue_family, &properties2, &properties14))
            continue;
        fprintf(stderr,
                "[Vulkan] Device %u: %s [%s], API %u.%u.%u.\n",
                index,
                properties2.properties.deviceName,
                physical_device_type_name(properties2.properties.deviceType),
                VK_API_VERSION_MAJOR(properties2.properties.apiVersion),
                VK_API_VERSION_MINOR(properties2.properties.apiVersion),
                VK_API_VERSION_PATCH(properties2.properties.apiVersion));
        if (!device_selector_matches(requested, index, &properties2.properties))
            continue;
        uint32_t preference = wallpaper_device_preference(properties2.properties.deviceType);
        if (!automatic)
            preference = 1;
        if (selected
            && (preference < selected_preference
                || (preference == selected_preference
                    && properties2.properties.driverVersion
                           <= selected_properties.properties.driverVersion)))
            continue;
        selected              = devices[index];
        selected_properties   = properties2;
        selected_properties14 = properties14;
        selected_queue        = queue_family;
        selected_preference   = preference;
        selected_index        = index;
    }
    free(devices);
    if (!selected) {
        fprintf(stderr,
                "FATAL: no Vulkan 1.4 Wayland device satisfies Walle's exact renderer%s%s.\n",
                automatic ? "" : " matching selector ",
                automatic ? "" : requested);
        return false;
    }

    float                   priority   = 1.0f;
    VkDeviceQueueCreateInfo queue_info = {
        .sType            = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = selected_queue,
        .queueCount       = 1,
        .pQueuePriorities = &priority,
    };
    VkPhysicalDeviceVulkan14Features features14 = {
        .sType                     = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES,
        .maintenance5              = VK_TRUE,
        .maintenance6              = VK_TRUE,
        .dynamicRenderingLocalRead = VK_TRUE,
    };
    VkPhysicalDeviceVulkan13Features features13 = {
        .sType                          = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext                          = &features14,
        .synchronization2               = VK_TRUE,
        .dynamicRendering               = VK_TRUE,
        .shaderDemoteToHelperInvocation = VK_TRUE,
    };
    VkPhysicalDeviceVulkan12Features features12 = {
        .sType                        = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext                        = &features13,
        .vulkanMemoryModel            = VK_TRUE,
        .vulkanMemoryModelDeviceScope = VK_TRUE,
        .shaderFloat16                = VK_TRUE,
        .scalarBlockLayout            = VK_TRUE,
    };
    VkPhysicalDeviceVulkan11Features features11 = {
        .sType                              = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,
        .pNext                              = &features12,
        .shaderDrawParameters               = VK_TRUE,
        .uniformAndStorageBuffer16BitAccess = VK_TRUE,
        .storageBuffer16BitAccess           = VK_TRUE,
    };
    VkPhysicalDeviceFeatures features = {
        .shaderStorageImageExtendedFormats = VK_TRUE,
        .independentBlend                  = VK_TRUE,
    };
    const char* extensions[] = {
        VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,
        VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME,
        VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
        VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME,
        VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME,
    };
    VkDeviceCreateInfo create_info = {
        .sType                   = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext                   = &features11,
        .queueCreateInfoCount    = 1,
        .pQueueCreateInfos       = &queue_info,
        .enabledExtensionCount   = renderer->display ? sizeof extensions / sizeof *extensions : 0,
        .ppEnabledExtensionNames = extensions,
        .pEnabledFeatures        = &features,
    };
    if (!vk_check(vkCreateDevice(selected, &create_info, nullptr, &renderer->device),
                  "vkCreateDevice"))
        return false;

    renderer->get_memory_fd
        = (PFN_vkGetMemoryFdKHR)vkGetDeviceProcAddr(renderer->device, "vkGetMemoryFdKHR");
    renderer->get_image_drm_format_modifier_properties
        = (PFN_vkGetImageDrmFormatModifierPropertiesEXT)vkGetDeviceProcAddr(
            renderer->device, "vkGetImageDrmFormatModifierPropertiesEXT");
    if (renderer->display
        && (!renderer->get_memory_fd || !renderer->get_image_drm_format_modifier_properties)) {
        fprintf(stderr, "FATAL: Vulkan dma-buf export entry points are unavailable.\n");
        return false;
    }

    renderer->get_semaphore_fd
        = (PFN_vkGetSemaphoreFdKHR)vkGetDeviceProcAddr(renderer->device, "vkGetSemaphoreFdKHR");
    renderer->import_semaphore_fd = (PFN_vkImportSemaphoreFdKHR)vkGetDeviceProcAddr(
        renderer->device, "vkImportSemaphoreFdKHR");
    if (renderer->display && (!renderer->get_semaphore_fd || !renderer->import_semaphore_fd))
        return false;
    renderer->physical_device = selected;
    renderer->properties      = selected_properties.properties;
    renderer->properties14    = selected_properties14;
    renderer->queue_family    = selected_queue;
    vkGetDeviceQueue(renderer->device, selected_queue, 0, &renderer->queue);
    vkGetPhysicalDeviceMemoryProperties(selected, &renderer->memory_properties);

    if (renderer->properties.limits.maxPushConstantsSize < 32
        || renderer->properties.limits.maxComputeWorkGroupInvocations < 400
        || renderer->properties.limits.maxComputeWorkGroupSize[0] < 20
        || renderer->properties.limits.maxComputeWorkGroupSize[1] < 20) {
        fprintf(stderr, "FATAL: Vulkan device limits are below Walle's exact renderer floor.\n");
        return false;
    }

    if (!create_descriptor_layouts(renderer) || !create_pipeline_layouts(renderer)
        || !create_pipelines(renderer) || !create_global_resources(renderer)
        || walle_vk_renderer_validation_errors(renderer))
        return false;
    renderer->device_ready = true;
    fprintf(stderr,
            "[Vulkan] Selected device %u: %s [%s], API %u.%u.%u, SPIR-V 1.6, dynamic "
            "rendering, synchronization2, adaptive one/two-image linux-dmabuf presentation.\n",
            selected_index,
            renderer->properties.deviceName,
            physical_device_type_name(renderer->properties.deviceType),
            VK_API_VERSION_MAJOR(renderer->properties.apiVersion),
            VK_API_VERSION_MINOR(renderer->properties.apiVersion),
            VK_API_VERSION_PATCH(renderer->properties.apiVersion));
    return true;
}

bool walle_vk_renderer_create(struct wl_display*         display,
                              const char*                device_selector,
                              struct walle_vk_renderer** result)
{
    if (!result)
        return false;
    *result                            = nullptr;
    struct walle_vk_renderer* renderer = calloc(1, sizeof *renderer);
    if (!renderer)
        return false;
    atomic_init(&renderer->validation_error_count, 0);
    renderer->display = display;
    renderer->device_selector
        = strdup(device_selector && *device_selector ? device_selector : "auto");
    if (!renderer->device_selector) {
        free(renderer);
        return false;
    }
    if (!create_instance(renderer) || walle_vk_renderer_validation_errors(renderer)) {
        walle_vk_renderer_destroy(renderer);
        return false;
    }
    *result = renderer;
    return true;
}

bool walle_vk_renderer_bind_linux_dmabuf(struct walle_vk_renderer* renderer,
                                         struct wl_registry*       registry,
                                         uint32_t                  name,
                                         uint32_t                  version)
{
    if (!renderer || !registry || renderer->dmabuf.factory || version < 4)
        return false;
    uint32_t bind_version = version < 5 ? version : 5;
    renderer->dmabuf.factory
        = wl_registry_bind(registry, name, &zwp_linux_dmabuf_v1_interface, bind_version);
    if (!renderer->dmabuf.factory)
        return false;
    renderer->dmabuf.object = zwp_linux_dmabuf_v1_get_default_feedback(renderer->dmabuf.factory);
    if (!renderer->dmabuf.object
        || zwp_linux_dmabuf_feedback_v1_add_listener(
               renderer->dmabuf.object, &dmabuf_feedback_listener, &renderer->dmabuf)
               != 0)
        return false;
    return true;
}

bool walle_vk_renderer_linux_dmabuf_ready(const struct walle_vk_renderer* renderer)
{
    return renderer && renderer->dmabuf.ready && !renderer->dmabuf.failed;
}

uint32_t walle_vk_renderer_max_image_dimension(const struct walle_vk_renderer* renderer)
{
    /* Image ceiling only. Each graphics extent has additional per-axis limits,
     * checked by output/attachment creation and resize before image allocation. */
    return renderer && renderer->device_ready ? renderer->properties.limits.maxImageDimension2D
                                              : UINT32_MAX;
}

static void destroy_present_images(struct walle_vk_output* output)
{
    for (size_t index = 0; index < 2; ++index)
        destroy_present_image(output->renderer->device, &output->present_images[index]);
    output->next_present_image = 0;
    output->last_present_image = 0;
    output->idle_present_image = 0;
    output->compact_present    = false;
}

static void compact_present_images(struct walle_vk_output* output)
{
    output->idle_present_image = output->last_present_image;
    output->compact_present    = true;
    for (uint32_t index = 0; index < 2; ++index) {
        if (index != output->idle_present_image && output->present_images[index].image.handle
            && !output->present_images[index].busy)
            destroy_present_image(output->renderer->device, &output->present_images[index]);
    }
}

static bool create_output_command_resources(struct walle_vk_output* output)
{
    struct walle_vk_renderer* renderer  = output->renderer;
    VkCommandPoolCreateInfo   pool_info = {
        .sType            = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags            = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
        .queueFamilyIndex = renderer->queue_family,
    };
    if (!vk_check(vkCreateCommandPool(renderer->device, &pool_info, nullptr, &output->command_pool),
                  "vkCreateCommandPool(output)"))
        return false;
    VkCommandBufferAllocateInfo allocate_info = {
        .sType              = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool        = output->command_pool,
        .level              = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    if (!vk_check(
            vkAllocateCommandBuffers(renderer->device, &allocate_info, &output->command_buffer),
            "vkAllocateCommandBuffers(output)"))
        return false;
    VkFenceCreateInfo fence_info = {
        .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
        .flags = VK_FENCE_CREATE_SIGNALED_BIT,
    };
    if (!vk_check(vkCreateFence(renderer->device, &fence_info, nullptr, &output->frame_fence),
                  "vkCreateFence(frame)"))
        return false;
    if (!output->wayland_surface)
        return true;
    VkSemaphoreCreateInfo si = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    if (!vk_check(vkCreateSemaphore(renderer->device, &si, nullptr, &output->acquire_semaphore),
                  "vkCreateSemaphore(acquire)"))
        return false;
    VkExportSemaphoreCreateInfo ex = {.sType       = VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO,
                                      .handleTypes = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT};
    si.pNext                       = &ex;
    return vk_check(vkCreateSemaphore(renderer->device, &si, nullptr, &output->render_semaphore),
                    "vkCreateSemaphore(render done)");
}

bool walle_vk_output_create(struct walle_vk_renderer* renderer,
                            struct wl_surface*        surface,
                            uint32_t                  width,
                            uint32_t                  height,
                            bool                      enable_composition_readback,
                            struct walle_vk_output**  result)
{
    if (!renderer || !renderer->instance || !result || width == 0 || height == 0)
        return false;
    *result = nullptr;
    if (renderer->device_ready && !graphics_extent_valid(renderer, width, height))
        return false;
    struct walle_vk_output* output = calloc(1, sizeof *output);
    if (!output)
        return false;
    output->renderer                           = renderer;
    output->wayland_surface                    = surface;
    output->extent                             = (VkExtent2D){.width = width, .height = height};
    output->composition_readback_enabled       = enable_composition_readback;
    VkWaylandSurfaceCreateInfoKHR surface_info = {
        .sType   = VK_STRUCTURE_TYPE_WAYLAND_SURFACE_CREATE_INFO_KHR,
        .display = renderer->display,
        .surface = surface,
    };
    bool success = (!surface
                    || vk_check(vkCreateWaylandSurfaceKHR(
                                    renderer->instance, &surface_info, nullptr, &output->surface),
                                "vkCreateWaylandSurfaceKHR"))
                   && initialize_device(renderer, output->surface)
                   && graphics_extent_valid(renderer, width, height)
                   && initialize_present_images(output) && create_output_command_resources(output);
    if (!success) {
        walle_vk_output_destroy(output);
        return false;
    }
    *result = output;
    return true;
}

static bool read_layer_exact(int fd, const struct walle_vk_image_layer* layer, void* destination)
{
    size_t done = 0;
    while (done < layer->size) {
        if (layer->offset > (size_t)INT64_MAX || done > (size_t)INT64_MAX - layer->offset)
            return false;
        ssize_t count = pread(
            fd, (uint8_t*)destination + done, layer->size - done, (off_t)(layer->offset + done));
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0)
            return false;
        done += (size_t)count;
    }
    return true;
}

static bool layer_valid(const struct walle_vk_renderer*    renderer,
                        const struct walle_vk_image_layer* layer)
{
    size_t pixel_count;
    size_t byte_count;
    return layer && layer->width > 0 && layer->height > 0
           && (uint32_t)layer->width <= renderer->properties.limits.maxImageDimension2D
           && (uint32_t)layer->height <= renderer->properties.limits.maxImageDimension2D
           && !ckd_mul(&pixel_count, (size_t)layer->width, (size_t)layer->height)
           && !ckd_mul(&byte_count, pixel_count, 4u) && layer->size == byte_count;
}

static bool take_present_image(struct walle_vk_output* output, uint32_t* result)
{
    for (uint32_t offset = 0; offset < 2; ++offset) {
        uint32_t index = (output->next_present_image + offset) % 2;
        if (output->present_images[index].image.handle && !output->present_images[index].busy) {
            *result                    = index;
            output->next_present_image = (index + 1) % 2;
            return true;
        }
    }
    for (uint32_t index = 0; index < 2; ++index) {
        if (!output->present_images[index].image.handle) {
            output->compact_present = false;
            if (!create_present_slot(output, index)) {
                output->renderer->fatal = true;
                return false;
            }
            *result                    = index;
            output->next_present_image = (index + 1) % 2;
            return true;
        }
    }
    return false;
}

static bool device_candidate(struct walle_vk_renderer*           r,
                             VkPhysicalDevice                    dev,
                             VkSurfaceKHR                        surface,
                             uint32_t*                           queue_family,
                             VkPhysicalDeviceProperties2*        p,
                             VkPhysicalDeviceVulkan14Properties* p14)
{
    *p14 = (VkPhysicalDeviceVulkan14Properties){
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_PROPERTIES};
    *p = (VkPhysicalDeviceProperties2){.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
                                       .pNext = p14};
    vkGetPhysicalDeviceProperties2(dev, p);
    if (p->properties.apiVersion < WALLE_VK_REQUIRED_API_VERSION)
        return false;
    if (r->display
        && (!device_extension_available(dev, VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME)
            || !device_extension_available(dev, VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME)
            || !device_extension_available(dev, VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME)
            || !device_extension_available(dev, VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME)
            || !device_extension_available(dev, VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME)))
        return false;
    if (r->display) {
        VkPhysicalDeviceExternalSemaphoreInfo si
            = {.sType      = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_SEMAPHORE_INFO,
               .handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT};
        VkExternalSemaphoreProperties sp
            = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES};
        vkGetPhysicalDeviceExternalSemaphoreProperties(dev, &si, &sp);
        if ((sp.externalSemaphoreFeatures
             & (VK_EXTERNAL_SEMAPHORE_FEATURE_IMPORTABLE_BIT
                | VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT))
            != (VK_EXTERNAL_SEMAPHORE_FEATURE_IMPORTABLE_BIT
                | VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT))
            return false;
    }
    uint32_t count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(dev, &count, nullptr);
    VkQueueFamilyProperties* queues = calloc(count, sizeof *queues);
    if (!queues)
        return false;
    vkGetPhysicalDeviceQueueFamilyProperties(dev, &count, queues);
    bool found = false;
    for (uint32_t i = 0; i < count; i++) {
        VkBool32 support = VK_TRUE;
        if (surface
            && (vkGetPhysicalDeviceSurfaceSupportKHR(dev, i, surface, &support) != VK_SUCCESS
                || !vkGetPhysicalDeviceWaylandPresentationSupportKHR(dev, i, r->display)))
            support = VK_FALSE;
        if (support
            && (queues[i].queueFlags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT))
                   == (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) {
            *queue_family = i;
            found         = true;
            break;
        }
    }
    free(queues);
    if (!found)
        return false;
    VkPhysicalDeviceVulkan14Features f14
        = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES};
    VkPhysicalDeviceVulkan13Features f13
        = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &f14};
    VkPhysicalDeviceVulkan12Features f12
        = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &f13};
    VkPhysicalDeviceVulkan11Features f11
        = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &f12};
    VkPhysicalDeviceFeatures2 f
        = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &f11};
    vkGetPhysicalDeviceFeatures2(dev, &f);
    return f14.dynamicRenderingLocalRead && f14.maintenance5 && f14.maintenance6
           && f13.dynamicRendering && f13.synchronization2 && f13.shaderDemoteToHelperInvocation
           && f12.shaderFloat16 && f12.scalarBlockLayout && f12.vulkanMemoryModel
           && f12.vulkanMemoryModelDeviceScope && f11.uniformAndStorageBuffer16BitAccess
           && f11.storageBuffer16BitAccess && f11.shaderDrawParameters
           && f.features.shaderStorageImageExtendedFormats && f.features.independentBlend
           && format_supports(dev,
                              WALLE_VK_WALLPAPER_FORMAT,
                              VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_BIT
                                  | VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_FILTER_LINEAR_BIT
                                  | VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT
                                  | VK_FORMAT_FEATURE_2_STORAGE_IMAGE_BIT
                                  | VK_FORMAT_FEATURE_2_TRANSFER_DST_BIT)
           && format_supports(dev,
                              VK_FORMAT_R16G16B16A16_SFLOAT,
                              VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_BIT
                                  | VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_FILTER_LINEAR_BIT
                                  | VK_FORMAT_FEATURE_2_TRANSFER_DST_BIT)
           && format_supports(
               dev, WALLE_VK_PRESENT_FORMAT, VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT);
}

static bool wait_submission(struct walle_vk_renderer* r, VkFence fence, bool* pending)
{
    if (!*pending)
        return true;
    VkResult status = vkWaitForFences(r->device, 1, &fence, VK_TRUE, UINT64_MAX);
    if (status == VK_SUCCESS || status == VK_ERROR_DEVICE_LOST)
        *pending = false;
    if (!vk_check(status, "vkWaitForFences(submitted work)")) {
        r->fatal = true;
        return false;
    }
    return true;
}
static bool begin_upload(struct walle_vk_renderer* r)
{
    if (!wait_submission(r, r->upload_fence, &r->upload_pending)
        || !vk_check(vkResetCommandBuffer(r->upload_command_buffer, 0),
                     "vkResetCommandBuffer(upload)"))
        return false;
    VkCommandBufferBeginInfo b = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                  .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    return vk_check(vkBeginCommandBuffer(r->upload_command_buffer, &b),
                    "vkBeginCommandBuffer(upload)");
}
static bool
submit_command(struct walle_vk_renderer* r, VkCommandBuffer cmd, VkFence fence, bool* pending)
{
    if (*pending || !vk_check(vkEndCommandBuffer(cmd), "vkEndCommandBuffer")
        || !vk_check(vkResetFences(r->device, 1, &fence), "vkResetFences(before submit)"))
        return false;
    VkCommandBufferSubmitInfo c
        = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO, .commandBuffer = cmd};
    VkSubmitInfo2 s = {.sType                  = VK_STRUCTURE_TYPE_SUBMIT_INFO_2,
                       .commandBufferInfoCount = 1,
                       .pCommandBufferInfos    = &c};
    if (!vk_check(vkQueueSubmit2(r->queue, 1, &s, fence), "vkQueueSubmit2")) {
        r->fatal = true;
        return false;
    }
    *pending = true;
    return true;
}
static bool end_upload(struct walle_vk_renderer* r)
{
    return submit_command(r, r->upload_command_buffer, r->upload_fence, &r->upload_pending)
           && wait_submission(r, r->upload_fence, &r->upload_pending);
}
static bool create_descriptor_layouts(struct walle_vk_renderer* r)
{
    const VkDescriptorType       types[]     = {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,
                                                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                                                VK_DESCRIPTOR_TYPE_SAMPLER,
                                                VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT,
                                                VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,
                                                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                                                VK_DESCRIPTOR_TYPE_SAMPLER,
                                                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                                                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE};
    VkDescriptorSetLayoutBinding bindings[9] = {};
    for (uint32_t i = 0; i < 9; i++)
        bindings[i] = (VkDescriptorSetLayoutBinding){.binding         = i,
                                                     .descriptorType  = types[i],
                                                     .descriptorCount = 1,
                                                     .stageFlags = VK_SHADER_STAGE_FRAGMENT_BIT};
    VkDescriptorSetLayoutCreateInfo ci
        = {.sType        = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
           .bindingCount = 9,
           .pBindings    = bindings};
    if (!vk_check(
            vkCreateDescriptorSetLayout(r->device, &ci, nullptr, &r->set_layout[LAYOUT_GRAPHICS]),
            "vkCreateDescriptorSetLayout(graphics)"))
        return false;
    ci.bindingCount = 3;
    if (!vk_check(
            vkCreateDescriptorSetLayout(r->device, &ci, nullptr, &r->set_layout[LAYOUT_CAPTURE]),
            "vkCreateDescriptorSetLayout(capture)"))
        return false;
    const uint32_t         slots[] = {0, 16, 33, 34, 48};
    const VkDescriptorType ct[]    = {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,
                                      VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                                      VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                                      VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                                      VK_DESCRIPTOR_TYPE_SAMPLER};
    for (uint32_t i = 0; i < 5; i++)
        bindings[i] = (VkDescriptorSetLayoutBinding){.binding         = slots[i],
                                                     .descriptorType  = ct[i],
                                                     .descriptorCount = 1,
                                                     .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
    ci.bindingCount = 5;
    return vk_check(
        vkCreateDescriptorSetLayout(r->device, &ci, nullptr, &r->set_layout[LAYOUT_COMPUTE]),
        "vkCreateDescriptorSetLayout(compute)");
}
static bool create_pipeline_layouts(struct walle_vk_renderer* r)
{
    for (uint32_t i = 0; i < LAYOUT_COUNT; i++) {
        VkPushConstantRange push
            = {.stageFlags = VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
               .size       = i == LAYOUT_GRAPHICS ? 32u : 16u};
        VkPipelineLayoutCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
                                         .setLayoutCount         = 1,
                                         .pSetLayouts            = &r->set_layout[i],
                                         .pushConstantRangeCount = i == LAYOUT_COMPUTE ? 0u : 1u,
                                         .pPushConstantRanges    = &push};
        if (!vk_check(vkCreatePipelineLayout(r->device, &ci, nullptr, &r->pipeline_layout[i]),
                      "vkCreatePipelineLayout"))
            return false;
    }
    return true;
}
static bool shader_module(struct walle_vk_renderer* r, struct shader_blob blob, VkShaderModule* out)
{
    VkShaderModuleCreateInfo ci = {.sType    = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                                   .codeSize = blob.size,
                                   .pCode    = (const uint32_t*)blob.data};
    return blob.size > 4 && blob.size % 4 == 0
           && vk_check(vkCreateShaderModule(r->device, &ci, nullptr, out), blob.entry);
}
static bool create_graphics_pipeline(struct walle_vk_renderer* r,
                                     struct shader_blob        vs,
                                     struct shader_blob        fs,
                                     uint32_t                  layout,
                                     bool                      blend,
                                     bool                      local,
                                     bool                      tint,
                                     VkFormat                  format,
                                     VkPipeline*               out)
{
    VkShaderModule vert = VK_NULL_HANDLE, frag = VK_NULL_HANDLE;
    if (!shader_module(r, vs, &vert) || !shader_module(r, fs, &frag))
        goto fail;
    VkPipelineShaderStageCreateInfo stages[2]
        = {{.sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage  = VK_SHADER_STAGE_VERTEX_BIT,
            .module = vert,
            .pName  = vs.entry},
           {.sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage  = VK_SHADER_STAGE_FRAGMENT_BIT,
            .module = frag,
            .pName  = fs.entry}};
    VkVertexInputBindingDescription   vb = {.binding   = 0,
                                            .stride    = sizeof(struct walle_vk_vertex),
                                            .inputRate = VK_VERTEX_INPUT_RATE_VERTEX};
    VkVertexInputAttributeDescription va[3]
        = {{.location = 0, .binding = 0, .format = VK_FORMAT_R32G32_SFLOAT, .offset = 0},
           {.location = 1, .binding = 0, .format = VK_FORMAT_R32G32_SFLOAT, .offset = 8},
           {.location = 2, .binding = 0, .format = VK_FORMAT_R32G32_SFLOAT, .offset = 16}};
    if (layout == LAYOUT_CAPTURE)
        va[1] = va[2];
    VkPipelineVertexInputStateCreateInfo vi
        = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO,
           .vertexBindingDescriptionCount   = 1,
           .pVertexBindingDescriptions      = &vb,
           .vertexAttributeDescriptionCount = layout == LAYOUT_CAPTURE ? 2u : 3u,
           .pVertexAttributeDescriptions    = va};
    VkPipelineInputAssemblyStateCreateInfo ia
        = {.sType    = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
           .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
    VkPipelineViewportStateCreateInfo vp
        = {.sType         = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
           .viewportCount = 1,
           .scissorCount  = 1};
    VkPipelineRasterizationStateCreateInfo rs
        = {.sType       = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
           .polygonMode = VK_POLYGON_MODE_FILL,
           .cullMode    = VK_CULL_MODE_NONE,
           .frontFace   = VK_FRONT_FACE_COUNTER_CLOCKWISE,
           .lineWidth   = 1.f};
    VkPipelineMultisampleStateCreateInfo ms
        = {.sType                = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
           .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT};
    VkPipelineColorBlendAttachmentState ba[2]
        = {{.blendEnable         = blend,
            .srcColorBlendFactor = VK_BLEND_FACTOR_ONE,
            .dstColorBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,
            .colorBlendOp        = VK_BLEND_OP_ADD,
            .srcAlphaBlendFactor = VK_BLEND_FACTOR_ONE,
            .dstAlphaBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,
            .alphaBlendOp        = VK_BLEND_OP_ADD,
            .colorWriteMask      = 15},
           {.colorWriteMask = 0}};
    VkPipelineColorBlendStateCreateInfo bs
        = {.sType           = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
           .attachmentCount = tint ? 2u : 1u,
           .pAttachments    = ba};
    VkDynamicState states[] = {VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR};
    VkPipelineDynamicStateCreateInfo ds
        = {.sType             = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO,
           .dynamicStateCount = 2,
           .pDynamicStates    = states};
    uint32_t locations[2] = {0, VK_ATTACHMENT_UNUSED},
             inputs[2]    = {local ? 0 : VK_ATTACHMENT_UNUSED, VK_ATTACHMENT_UNUSED};
    if (tint) {
        inputs[0] = VK_ATTACHMENT_UNUSED;
        inputs[1] = 0;
    }
    VkRenderingInputAttachmentIndexInfo ii
        = {.sType                        = VK_STRUCTURE_TYPE_RENDERING_INPUT_ATTACHMENT_INDEX_INFO,
           .colorAttachmentCount         = tint ? 2u : 1u,
           .pColorAttachmentInputIndices = inputs};
    VkRenderingAttachmentLocationInfo li
        = {.sType                     = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_LOCATION_INFO,
           .pNext                     = &ii,
           .colorAttachmentCount      = tint ? 2u : 1u,
           .pColorAttachmentLocations = locations};
    VkFormat                      formats[] = {format, WALLE_VK_PRESENT_FORMAT};
    VkPipelineRenderingCreateInfo rendering
        = {.sType                   = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,
           .pNext                   = &li,
           .colorAttachmentCount    = tint ? 2u : 1u,
           .pColorAttachmentFormats = formats};
    VkGraphicsPipelineCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
                                       .pNext = &rendering,
                                       .stageCount          = 2,
                                       .pStages             = stages,
                                       .pVertexInputState   = &vi,
                                       .pInputAssemblyState = &ia,
                                       .pViewportState      = &vp,
                                       .pRasterizationState = &rs,
                                       .pMultisampleState   = &ms,
                                       .pColorBlendState    = &bs,
                                       .pDynamicState       = &ds,
                                       .layout              = r->pipeline_layout[layout]};
    bool ok = vk_check(vkCreateGraphicsPipelines(r->device, VK_NULL_HANDLE, 1, &ci, nullptr, out),
                       fs.entry);
    vkDestroyShaderModule(r->device, vert, nullptr);
    vkDestroyShaderModule(r->device, frag, nullptr);
    return ok;
fail:
    if (vert)
        vkDestroyShaderModule(r->device, vert, nullptr);
    if (frag)
        vkDestroyShaderModule(r->device, frag, nullptr);
    return false;
}
static bool create_pipelines(struct walle_vk_renderer* r)
{
    const struct shader_blob fragments[PIPE_COUNT]
        = {SHADER(revealFragment, "revealFragment"),
           SHADER(regularFragment, "regularFragment"),
           SHADER(clearFragment, "clearFragment"),
           SHADER(faceFragment_local, "faceFragment"),
           SHADER(tintMaskFragment, "tintMaskFragment"),
           SHADER(tintGradientFragment_local, "tintGradientFragment"),
           SHADER(tintCompositeFragment, "tintCompositeFragment"),
           SHADER(highlightFragment_local, "highlightFragment"),
           SHADER(productFinishFragment_local, "productFinishFragment"),
           SHADER(wallpaperFragment, "wallpaperFragment")};
    for (uint32_t i = 0; i < PIPE_COUNT; i++) {
        bool blend = i == WALLE_VK_REVEAL || i == WALLE_VK_GLASS_REGULAR
                     || i == WALLE_VK_GLASS_CLEAR || i == WALLE_VK_TINT_COMPOSITE;
        bool local = i == WALLE_VK_FACE || i == WALLE_VK_HIGHLIGHT || i == WALLE_VK_PRODUCT_FINISH;
        bool tint  = i == WALLE_VK_TINT_GRADIENT;
        VkFormat format = (tint || i == WALLE_VK_TINT_MASK) ? WALLE_VK_WALLPAPER_FORMAT
                                                            : WALLE_VK_PRESENT_FORMAT;
        if (!create_graphics_pipeline(r,
                                      SHADER(glassVertex, "glassVertex"),
                                      fragments[i],
                                      LAYOUT_GRAPHICS,
                                      blend,
                                      local,
                                      tint,
                                      format,
                                      &r->pipelines[i]))
            return false;
    }
    const struct shader_blob caps[] = {SHADER(capture4Fragment, "capture4Fragment"),
                                       SHADER(capture6Fragment, "capture6Fragment"),
                                       SHADER(capture8Fragment, "capture8Fragment"),
                                       SHADER(captureCopyFragment, "captureCopyFragment")};
    for (uint32_t i = 0; i < 4; i++)
        if (!create_graphics_pipeline(r,
                                      SHADER(captureVertex, "captureVertex"),
                                      caps[i],
                                      LAYOUT_CAPTURE,
                                      false,
                                      false,
                                      false,
                                      WALLE_VK_WALLPAPER_FORMAT,
                                      &r->capture_pipelines[i]))
            return false;
    const struct shader_blob computes[]
        = {SHADER(lg_blur_copy_base_mip, "lg_blur_copy_base_mip"),
           SHADER(lg_blur_downsample_agx2, "lg_blur_downsample_agx2")};
    for (uint32_t i = 0; i < 2; i++) {
        VkShaderModule m;
        if (!shader_module(r, computes[i], &m))
            return false;
        VkComputePipelineCreateInfo ci
            = {.sType  = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
               .stage  = {.sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                          .stage  = VK_SHADER_STAGE_COMPUTE_BIT,
                          .module = m,
                          .pName  = computes[i].entry},
               .layout = r->pipeline_layout[LAYOUT_COMPUTE]};
        bool ok
            = vk_check(vkCreateComputePipelines(
                           r->device, VK_NULL_HANDLE, 1, &ci, nullptr, &r->compute_pipelines[i]),
                       computes[i].entry);
        vkDestroyShaderModule(r->device, m, nullptr);
        if (!ok)
            return false;
    }
    return true;
}

static bool create_mip_image(struct walle_vk_renderer* r,
                             uint32_t                  w,
                             uint32_t                  h,
                             uint32_t                  levels,
                             struct walle_vk_image*    out)
{
    if (!w || !h || !levels || levels > 32 || w > r->properties.limits.maxImageDimension2D
        || h > r->properties.limits.maxImageDimension2D)
        return false;
    struct walle_vk_image image
        = {.width = w, .height = h, .format = WALLE_VK_WALLPAPER_FORMAT, .mip_count = levels};
    VkImageCreateInfo ci = {.sType       = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                            .imageType   = VK_IMAGE_TYPE_2D,
                            .format      = image.format,
                            .extent      = {w, h, 1},
                            .mipLevels   = levels,
                            .arrayLayers = 1,
                            .samples     = VK_SAMPLE_COUNT_1_BIT,
                            .tiling      = VK_IMAGE_TILING_OPTIMAL,
                            .usage       = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT,
                            .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
                            .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
    if (!vk_check(vkCreateImage(r->device, &ci, nullptr, &image.handle), "vkCreateImage(pyramid)"))
        return false;
    VkMemoryRequirements requirements;
    vkGetImageMemoryRequirements(r->device, image.handle, &requirements);
    if (!allocate_memory(r,
                         &requirements,
                         VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                         VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                         false,
                         &image.memory)
        || !vk_check(vkBindImageMemory(r->device, image.handle, image.memory.handle, 0),
                     "vkBindImageMemory(pyramid)"))
        goto fail;
    VkImageViewCreateInfo vi
        = {.sType    = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
           .image    = image.handle,
           .viewType = VK_IMAGE_VIEW_TYPE_2D,
           .format   = image.format,
           .subresourceRange
           = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .levelCount = levels, .layerCount = 1}};
    if (!vk_check(vkCreateImageView(r->device, &vi, nullptr, &image.view),
                  "vkCreateImageView(pyramid)"))
        goto fail;
    vi.subresourceRange.levelCount = 1;
    for (uint32_t i = 0; i < levels; i++) {
        vi.subresourceRange.baseMipLevel = i;
        if (!vk_check(vkCreateImageView(r->device, &vi, nullptr, &image.mip_views[i]),
                      "vkCreateImageView(pyramid mip)"))
            goto fail;
    }
    *out = image;
    return true;
fail:
    destroy_image(r->device, &image);
    return false;
}
static void mip_barrier(VkCommandBuffer        cmd,
                        struct walle_vk_image* image,
                        VkPipelineStageFlags2  src,
                        VkAccessFlags2         sa,
                        VkPipelineStageFlags2  dst,
                        VkAccessFlags2         da,
                        VkImageLayout          old,
                        VkImageLayout          next)
{
    VkImageMemoryBarrier2 b
        = {.sType               = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2,
           .srcStageMask        = src,
           .srcAccessMask       = sa,
           .dstStageMask        = dst,
           .dstAccessMask       = da,
           .oldLayout           = old,
           .newLayout           = next,
           .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
           .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
           .image               = image->handle,
           .subresourceRange    = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                                   .levelCount = image->mip_count ? image->mip_count : 1,
                                   .layerCount = 1}};
    VkDependencyInfo d = {.sType                   = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                          .imageMemoryBarrierCount = 1,
                          .pImageMemoryBarriers    = &b};
    vkCmdPipelineBarrier2(cmd, &d);
}
static void destroy_backdrop(struct walle_vk_output* o)
{
    destroy_image(o->renderer->device, &o->capture);
    destroy_image(o->renderer->device, &o->pyramid);
    destroy_image(o->renderer->device, &o->blur_scratch);
    o->backdrop_ready = false;
}
static void destroy_transition_resources(struct walle_vk_output* o)
{
    VkDevice d = o->renderer->device;
    destroy_backdrop(o);
    destroy_image(d, &o->tint);
    destroy_image(d, &o->mask);
    destroy_image(d, &o->ramp);
    destroy_buffer(d, &o->frame_buffer);
    destroy_buffer(d, &o->readback_buffer);
    if (o->descriptor_pool)
        vkDestroyDescriptorPool(d, o->descriptor_pool, nullptr);
    o->descriptor_pool     = VK_NULL_HANDLE;
    o->descriptor_capacity = 0;
    o->cursor              = 0;
    o->tint_ready          = false;
    o->ramp_ready          = false;
}
static bool upload_image(struct walle_vk_output*            o,
                         int                                fd,
                         const struct walle_vk_image_layer* layer,
                         struct walle_vk_image*             out)
{
    struct walle_vk_renderer* r = o->renderer;
    if (fd < 0 || !layer_valid(r, layer) || (uint32_t)layer->width != o->extent.width
        || (uint32_t)layer->height != o->extent.height)
        return false;
    struct walle_vk_image  image   = {};
    struct walle_vk_buffer staging = {};
    bool ok = create_image(r,
                           (uint32_t)layer->width,
                           (uint32_t)layer->height,
                           WALLE_VK_WALLPAPER_FORMAT,
                           VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT,
                           &image)
              && create_buffer(r,
                               layer->size,
                               VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
                               VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                                   | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                               VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                               true,
                               &staging)
              && read_layer_exact(fd, layer, staging.memory.mapped) && begin_upload(r);
    if (ok) {
        image_barrier(r->upload_command_buffer,
                      image.handle,
                      VK_PIPELINE_STAGE_2_NONE,
                      0,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_WRITE_BIT,
                      VK_IMAGE_LAYOUT_UNDEFINED,
                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        VkBufferImageCopy cp
            = {.imageSubresource = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .layerCount = 1},
               .imageExtent      = {(uint32_t)layer->width, (uint32_t)layer->height, 1}};
        vkCmdCopyBufferToImage(r->upload_command_buffer,
                               staging.handle,
                               image.handle,
                               VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                               1,
                               &cp);
        image_barrier(r->upload_command_buffer,
                      image.handle,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                      VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                      VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
        ok = end_upload(r);
    }
    if (r->upload_pending) {
        if (vkDeviceWaitIdle(r->device) == VK_SUCCESS)
            r->upload_pending = false;
        else
            r->fatal = true;
    }
    destroy_buffer(r->device, &staging);
    if (!ok) {
        destroy_image(r->device, &image);
        return false;
    }
    *out = image;
    return true;
}
bool walle_vk_output_upload(struct walle_vk_output*            o,
                            int                                fd,
                            const struct walle_vk_image_layer* layer)
{
    if (!o || o->renderer->fatal
        || !wait_submission(o->renderer, o->frame_fence, &o->frame_pending))
        return false;
    struct walle_vk_image image = {};
    if (!upload_image(o, fd, layer, &image))
        return false;
    destroy_image(o->renderer->device, &o->incoming);
    o->incoming = image;
    destroy_backdrop(o);
    return true;
}
bool walle_vk_output_restore_current(struct walle_vk_output*            o,
                                     int                                fd,
                                     const struct walle_vk_image_layer* layer)
{
    if (!o || o->renderer->fatal)
        return false;
    return o->current.handle || upload_image(o, fd, layer, &o->current);
}
static bool ensure_buffer(struct walle_vk_renderer* r,
                          struct walle_vk_buffer*   b,
                          VkDeviceSize              size,
                          VkBufferUsageFlags        usage)
{
    if (b->capacity >= size)
        return true;
    struct walle_vk_buffer next = {};
    if (!create_buffer(r,
                       size,
                       usage,
                       VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                       VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                       true,
                       &next))
        return false;
    destroy_buffer(r->device, b);
    *b = next;
    return true;
}
static bool prepare_pool(struct walle_vk_output* o, uint32_t sets)
{
    VkDevice d = o->renderer->device;
    if (o->descriptor_pool && o->descriptor_capacity >= sets)
        return vk_check(vkResetDescriptorPool(d, o->descriptor_pool, 0),
                        "vkResetDescriptorPool(frame)");
    if (o->descriptor_pool)
        vkDestroyDescriptorPool(d, o->descriptor_pool, nullptr);
    o->descriptor_pool                 = VK_NULL_HANDLE;
    o->descriptor_capacity             = 0;
    VkDescriptorPoolSize       sizes[] = {{VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 2 * sets},
                                          {VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, 4 * sets},
                                          {VK_DESCRIPTOR_TYPE_SAMPLER, 2 * sets},
                                          {VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT, sets},
                                          {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 2 * sets}};
    VkDescriptorPoolCreateInfo ci      = {.sType         = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
                                          .maxSets       = sets,
                                          .poolSizeCount = 5,
                                          .pPoolSizes    = sizes};
    if (!vk_check(vkCreateDescriptorPool(d, &ci, nullptr, &o->descriptor_pool),
                  "vkCreateDescriptorPool(frame)"))
        return false;
    o->descriptor_capacity = sets;
    return true;
}
static VkDeviceSize
stage_bytes(struct walle_vk_output* o, const void* data, VkDeviceSize size, VkDeviceSize alignment)
{
    VkDeviceSize offset = o->cursor;
    if (alignment > 1) {
        VkDeviceSize rem = offset % alignment;
        if (rem)
            offset += alignment - rem;
    }
    if (offset > o->frame_buffer.capacity || size > o->frame_buffer.capacity - offset)
        return UINT64_MAX;
    if (data)
        memcpy((uint8_t*)o->frame_buffer.memory.mapped + offset, data, (size_t)size);
    else
        memset((uint8_t*)o->frame_buffer.memory.mapped + offset, 0, (size_t)size);
    o->cursor = offset + size;
    return offset;
}
static VkDescriptorSet allocate_set(struct walle_vk_output* o, uint32_t layout)
{
    VkDescriptorSet             set = VK_NULL_HANDLE;
    VkDescriptorSetAllocateInfo ai  = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
                                       .descriptorPool     = o->descriptor_pool,
                                       .descriptorSetCount = 1,
                                       .pSetLayouts        = &o->renderer->set_layout[layout]};
    if (!vk_check(vkAllocateDescriptorSets(o->renderer->device, &ai, &set),
                  "vkAllocateDescriptorSets"))
        return VK_NULL_HANDLE;
    return set;
}
static void write_image(VkDevice         device,
                        VkDescriptorSet  set,
                        uint32_t         binding,
                        VkDescriptorType type,
                        VkImageView      view,
                        VkImageLayout    layout,
                        VkSampler        sampler)
{
    VkDescriptorImageInfo ii = {.sampler = sampler, .imageView = view, .imageLayout = layout};
    VkWriteDescriptorSet  w  = {.sType           = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
                                .dstSet          = set,
                                .dstBinding      = binding,
                                .descriptorCount = 1,
                                .descriptorType  = type,
                                .pImageInfo      = &ii};
    vkUpdateDescriptorSets(device, 1, &w, 0, nullptr);
}
static void write_uniform(struct walle_vk_output* o,
                          VkDescriptorSet         set,
                          uint32_t                binding,
                          VkDeviceSize            offset,
                          VkDeviceSize            size)
{
    VkDescriptorBufferInfo bi = {.buffer = o->frame_buffer.handle, .offset = offset, .range = size};
    VkWriteDescriptorSet   w  = {.sType           = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
                                 .dstSet          = set,
                                 .dstBinding      = binding,
                                 .descriptorCount = 1,
                                 .descriptorType  = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,
                                 .pBufferInfo     = &bi};
    vkUpdateDescriptorSets(o->renderer->device, 1, &w, 0, nullptr);
}
static void set_viewport(VkCommandBuffer cmd, uint32_t w, uint32_t h, VkRect2D scissor)
{
    VkViewport vp = {.x        = 0,
                     .y        = (float)h,
                     .width    = (float)w,
                     .height   = -(float)h,
                     .minDepth = 0,
                     .maxDepth = 1};
    vkCmdSetViewport(cmd, 0, 1, &vp);
    vkCmdSetScissor(cmd, 0, 1, &scissor);
}
static void begin_render(VkCommandBuffer cmd,
                         VkImageView     target,
                         VkImageView     scene,
                         uint32_t        w,
                         uint32_t        h,
                         bool            clear,
                         bool            local,
                         bool            tint)
{
    VkRenderingAttachmentInfo ai[2]
        = {{.sType       = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO,
            .imageView   = target,
            .imageLayout = VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ,
            .loadOp      = clear ? VK_ATTACHMENT_LOAD_OP_CLEAR : VK_ATTACHMENT_LOAD_OP_LOAD,
            .storeOp     = VK_ATTACHMENT_STORE_OP_STORE},
           {.sType       = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO,
            .imageView   = scene,
            .imageLayout = VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ,
            .loadOp      = VK_ATTACHMENT_LOAD_OP_LOAD,
            .storeOp     = VK_ATTACHMENT_STORE_OP_STORE}};
    VkRenderingInfo ri = {.sType                = VK_STRUCTURE_TYPE_RENDERING_INFO,
                          .renderArea           = {{0, 0}, {w, h}},
                          .layerCount           = 1,
                          .colorAttachmentCount = tint ? 2u : 1u,
                          .pColorAttachments    = ai};
    vkCmdBeginRendering(cmd, &ri);
    uint32_t locations[2] = {0, VK_ATTACHMENT_UNUSED},
             indices[2]   = {local ? 0 : VK_ATTACHMENT_UNUSED, VK_ATTACHMENT_UNUSED};
    if (tint) {
        indices[0] = VK_ATTACHMENT_UNUSED;
        indices[1] = 0;
    }
    VkRenderingAttachmentLocationInfo li
        = {.sType                     = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_LOCATION_INFO,
           .colorAttachmentCount      = tint ? 2u : 1u,
           .pColorAttachmentLocations = locations};
    VkRenderingInputAttachmentIndexInfo ii
        = {.sType                        = VK_STRUCTURE_TYPE_RENDERING_INPUT_ATTACHMENT_INDEX_INFO,
           .colorAttachmentCount         = tint ? 2u : 1u,
           .pColorAttachmentInputIndices = indices};
    vkCmdSetRenderingAttachmentLocations(cmd, &li);
    vkCmdSetRenderingInputAttachmentIndices(cmd, &ii);
}
static void local_read_barrier(VkCommandBuffer cmd)
{
    VkMemoryBarrier2 b  = {.sType         = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2,
                           .srcStageMask  = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                           .srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                           .dstStageMask  = VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT
                                            | VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                           .dstAccessMask = VK_ACCESS_2_INPUT_ATTACHMENT_READ_BIT
                                            | VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT
                                            | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT};
    VkDependencyInfo di = {.sType              = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                           .dependencyFlags    = VK_DEPENDENCY_BY_REGION_BIT,
                           .memoryBarrierCount = 1,
                           .pMemoryBarriers    = &b};
    vkCmdPipelineBarrier2(cmd, &di);
}
static void quad_vertices(struct walle_vk_vertex v[4], const float p[4], const float uv[4])
{
    v[0] = (struct walle_vk_vertex){.position = {p[0], p[1]}, .source_uv = {uv[0], uv[1]}};
    v[1] = (struct walle_vk_vertex){.position = {p[2], p[1]}, .source_uv = {uv[2], uv[1]}};
    v[2] = (struct walle_vk_vertex){.position = {p[2], p[3]}, .source_uv = {uv[2], uv[3]}};
    v[3] = (struct walle_vk_vertex){.position = {p[0], p[3]}, .source_uv = {uv[0], uv[3]}};
}
static bool issue_geometry(struct walle_vk_output*       o,
                           VkPipelineLayout              layout,
                           VkDescriptorSet               set,
                           const struct walle_vk_vertex* vertices,
                           size_t                        nv,
                           const uint32_t*               indices,
                           size_t                        ni,
                           const struct glass_push*      push,
                           uint32_t                      push_size)
{
    VkDeviceSize vo = stage_bytes(o, vertices, nv * sizeof *vertices, 4),
                 io = stage_bytes(o, indices, ni * sizeof *indices, 4);
    if (vo == UINT64_MAX || io == UINT64_MAX)
        return false;
    vkCmdBindDescriptorSets(
        o->command_buffer, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, 1, &set, 0, nullptr);
    vkCmdBindVertexBuffers(o->command_buffer, 0, 1, &o->frame_buffer.handle, &vo);
    vkCmdBindIndexBuffer(o->command_buffer, o->frame_buffer.handle, io, VK_INDEX_TYPE_UINT32);
    vkCmdPushConstants(o->command_buffer,
                       layout,
                       VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
                       0,
                       push_size,
                       push);
    vkCmdDrawIndexed(o->command_buffer, (uint32_t)ni, 1, 0, 0, 0);
    return true;
}

static bool ensure_backdrop(struct walle_vk_output* o, const struct walle_vk_frame* frame)
{
    const struct wm_capture_plan* c = frame->capture;
    const struct wm_pyramid_plan* p = frame->pyramid;
    if (!c || !p || !graphics_extent_valid(o->renderer, c->texture[0], c->texture[1])
        || c->quad_count > 9 || !c->quad_count
        || (c->tap_count != 0 && c->tap_count != 4 && c->tap_count != 6 && c->tap_count != 8)
        || p->mip_count > 32 || p->down_count > 32)
        return false;
    if (o->capture.handle && !memcmp(&o->capture_plan, c, sizeof *c)
        && !memcmp(&o->pyramid_plan, p, sizeof *p))
        return true;
    destroy_backdrop(o);
    auto r = o->renderer;
    if (!create_image(r,
                      c->texture[0],
                      c->texture[1],
                      WALLE_VK_WALLPAPER_FORMAT,
                      VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT
                          | VK_IMAGE_USAGE_SAMPLED_BIT,
                      &o->capture))
        return false;
    if (p->mip_count
        && (!p->groups[0] || !p->groups[1]
            || !create_mip_image(r, p->texture[0], p->texture[1], p->mip_count, &o->pyramid))) {
        destroy_backdrop(o);
        return false;
    }
    if (p->mip_count && p->dst1_level >= p->mip_count) {
        /* The native base stores are independent of blurOut. A one-mip plan
         * still executes that unchanged kernel; discard its separate blur output. */
        if (p->mip_count != 1 || p->dst1_level != 1 || p->no_base || !p->dst1[0] || !p->dst1[1]
            || !create_image(r,
                             p->dst1[0],
                             p->dst1[1],
                             WALLE_VK_WALLPAPER_FORMAT,
                             VK_IMAGE_USAGE_STORAGE_BIT,
                             &o->blur_scratch)) {
            destroy_backdrop(o);
            return false;
        }
    }
    for (uint32_t i = 0; i < p->down_count; i++)
        if (p->down[i].src_level >= p->mip_count || p->down[i].dst_level >= p->mip_count
            || !p->down[i].groups_x || !p->down[i].groups_y) {
            destroy_backdrop(o);
            return false;
        }
    o->capture_plan = *c;
    o->pyramid_plan = *p;
    return true;
}
static bool record_backdrop(struct walle_vk_output* o)
{
    auto    r            = o->renderer;
    auto    cmd          = o->command_buffer;
    auto    c            = &o->capture_plan;
    auto    p            = &o->pyramid_plan;
    uint8_t uniforms[80] = {};
    memcpy(uniforms, c->taps, sizeof c->taps);
    const uint16_t weights[] = {0x3400, 0x3400, 0x3400, 0x3400, 0, 0, 0, 0};
    memcpy(uniforms + 64, weights, sizeof weights);
    VkDeviceSize u = stage_bytes(
        o, uniforms, sizeof uniforms, r->properties.limits.minUniformBufferOffsetAlignment);
    VkDescriptorSet set = allocate_set(o, LAYOUT_CAPTURE);
    if (!set || u == UINT64_MAX)
        return false;
    write_uniform(o, set, 0, u, 80);
    write_image(r->device,
                set,
                1,
                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                o->incoming.view,
                VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL,
                VK_NULL_HANDLE);
    write_image(r->device,
                set,
                2,
                VK_DESCRIPTOR_TYPE_SAMPLER,
                VK_NULL_HANDLE,
                VK_IMAGE_LAYOUT_UNDEFINED,
                r->linear_sampler);
    image_barrier(cmd,
                  o->capture.handle,
                  VK_PIPELINE_STAGE_2_NONE,
                  0,
                  VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                  VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                  VK_IMAGE_LAYOUT_UNDEFINED,
                  VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ);
    begin_render(
        cmd, o->capture.view, VK_NULL_HANDLE, c->texture[0], c->texture[1], true, false, false);
    uint32_t ci = c->tap_count == 0 ? 3 : c->tap_count == 8 ? 2 : c->tap_count == 6 ? 1 : 0;
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, r->capture_pipelines[ci]);
    set_viewport(
        cmd, c->texture[0], c->texture[1], (VkRect2D){{0, 0}, {c->texture[0], c->texture[1]}});
    struct walle_vk_vertex vertices[36];
    uint32_t               indices[54];
    const uint32_t         quad_indices[] = {0, 1, 2, 0, 2, 3};
    for (uint32_t q = 0; q < c->quad_count; q++) {
        float uv[4] = {c->quads[q].texcoord[0] * c->texture_matrix[0],
                       c->quads[q].texcoord[1] * c->texture_matrix[1],
                       c->quads[q].texcoord[2] * c->texture_matrix[0],
                       c->quads[q].texcoord[3] * c->texture_matrix[1]};
        quad_vertices(vertices + 4 * q, c->quads[q].position, uv);
        for (uint32_t j = 0; j < 6; j++)
            indices[6 * q + j] = 4 * q + quad_indices[j];
    }
    struct glass_push push = {.resolution = {(float)c->texture[0], (float)c->texture[1]}, .edr = 1};
    bool              ok   = issue_geometry(o,
                                            r->pipeline_layout[LAYOUT_CAPTURE],
                                            set,
                                            vertices,
                                            4 * c->quad_count,
                                            indices,
                                            6 * c->quad_count,
                                            &push,
                                            16);
    vkCmdEndRendering(cmd);
    if (!ok)
        return false;
    image_barrier(cmd,
                  o->capture.handle,
                  VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                  VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                  VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                  VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                  VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ,
                  VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
    if (!p->mip_count)
        return true;
    mip_barrier(cmd,
                &o->pyramid,
                VK_PIPELINE_STAGE_2_NONE,
                0,
                VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                VK_IMAGE_LAYOUT_UNDEFINED,
                VK_IMAGE_LAYOUT_GENERAL);
    if (o->blur_scratch.handle)
        image_barrier(cmd,
                      o->blur_scratch.handle,
                      VK_PIPELINE_STAGE_2_NONE,
                      0,
                      VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                      VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                      VK_IMAGE_LAYOUT_UNDEFINED,
                      VK_IMAGE_LAYOUT_GENERAL);
    struct
    {
        int32_t  base[2], pad[2], clamp[4];
        uint32_t dst0[2], dst1[2], level, no_base;
    } base = {};
    static_assert(sizeof base == 56);
    memcpy(base.base, p->coordinate_base, sizeof base.base);
    memcpy(base.clamp, p->coordinate_clamp, sizeof base.clamp);
    memcpy(base.dst0, p->dst0, sizeof base.dst0);
    memcpy(base.dst1, p->dst1, sizeof base.dst1);
    base.level   = p->dst1_level;
    base.no_base = p->no_base;
    u   = stage_bytes(o, &base, sizeof base, r->properties.limits.minUniformBufferOffsetAlignment);
    set = allocate_set(o, LAYOUT_COMPUTE);
    if (!set || u == UINT64_MAX)
        return false;
    write_uniform(o, set, 0, u, sizeof base);
    write_image(r->device,
                set,
                16,
                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                o->capture.view,
                VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL,
                VK_NULL_HANDLE);
    write_image(r->device,
                set,
                33,
                VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                o->pyramid.mip_views[0],
                VK_IMAGE_LAYOUT_GENERAL,
                VK_NULL_HANDLE);
    write_image(r->device,
                set,
                34,
                VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                o->blur_scratch.handle ? o->blur_scratch.view : o->pyramid.mip_views[p->dst1_level],
                VK_IMAGE_LAYOUT_GENERAL,
                VK_NULL_HANDLE);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, r->compute_pipelines[0]);
    vkCmdBindDescriptorSets(cmd,
                            VK_PIPELINE_BIND_POINT_COMPUTE,
                            r->pipeline_layout[LAYOUT_COMPUTE],
                            0,
                            1,
                            &set,
                            0,
                            nullptr);
    vkCmdDispatch(cmd, p->groups[0], p->groups[1], 1);
    for (uint32_t i = 0; i < p->down_count; i++) {
        mip_barrier(cmd,
                    &o->pyramid,
                    VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                    VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                    VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                    VK_ACCESS_2_SHADER_SAMPLED_READ_BIT | VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                    VK_IMAGE_LAYOUT_GENERAL,
                    VK_IMAGE_LAYOUT_GENERAL);
        auto down = &p->down[i];
        struct
        {
            uint32_t src, dst, w, h;
            float    dx, dy;
        } du = {down->src_level, down->dst_level, down->width, down->height, down->dx, down->dy};
        static_assert(sizeof du == 24);
        u   = stage_bytes(o, &du, sizeof du, r->properties.limits.minUniformBufferOffsetAlignment);
        set = allocate_set(o, LAYOUT_COMPUTE);
        if (!set || u == UINT64_MAX)
            return false;
        write_uniform(o, set, 0, u, sizeof du);
        write_image(r->device,
                    set,
                    16,
                    VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                    o->pyramid.view,
                    VK_IMAGE_LAYOUT_GENERAL,
                    VK_NULL_HANDLE);
        write_image(r->device,
                    set,
                    33,
                    VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                    o->pyramid.mip_views[down->dst_level],
                    VK_IMAGE_LAYOUT_GENERAL,
                    VK_NULL_HANDLE);
        write_image(r->device,
                    set,
                    48,
                    VK_DESCRIPTOR_TYPE_SAMPLER,
                    VK_NULL_HANDLE,
                    VK_IMAGE_LAYOUT_UNDEFINED,
                    r->linear_sampler);
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, r->compute_pipelines[1]);
        vkCmdBindDescriptorSets(cmd,
                                VK_PIPELINE_BIND_POINT_COMPUTE,
                                r->pipeline_layout[LAYOUT_COMPUTE],
                                0,
                                1,
                                &set,
                                0,
                                nullptr);
        vkCmdDispatch(cmd, down->groups_x, down->groups_y, 1);
    }
    mip_barrier(cmd,
                &o->pyramid,
                VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                VK_IMAGE_LAYOUT_GENERAL,
                VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
    return true;
}
static bool ensure_tint(struct walle_vk_output* o)
{
    VkImageUsageFlags usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT
                              | VK_IMAGE_USAGE_INPUT_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    if (!o->tint.handle
        && !create_image(o->renderer,
                         o->extent.width,
                         o->extent.height,
                         WALLE_VK_WALLPAPER_FORMAT,
                         usage,
                         &o->tint))
        return false;
    if (!o->mask.handle
        && !create_image(o->renderer,
                         o->extent.width,
                         o->extent.height,
                         WALLE_VK_WALLPAPER_FORMAT,
                         usage,
                         &o->mask))
        return false;
    return true;
}
static bool record_ramp(struct walle_vk_output* o, const uint8_t* bytes)
{
    if (!bytes)
        return true;
    if (o->ramp_ready && !memcmp(o->ramp_bytes, bytes, 2048))
        return true;
    if (!o->ramp.handle
        && !create_image(o->renderer,
                         256,
                         1,
                         VK_FORMAT_R16G16B16A16_SFLOAT,
                         VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT,
                         &o->ramp))
        return false;
    VkDeviceSize offset = stage_bytes(o, bytes, 2048, 8);
    if (offset == UINT64_MAX)
        return false;
    image_barrier(o->command_buffer,
                  o->ramp.handle,
                  o->ramp_ready ? VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT
                                : VK_PIPELINE_STAGE_2_NONE,
                  o->ramp_ready ? VK_ACCESS_2_SHADER_SAMPLED_READ_BIT : 0,
                  VK_PIPELINE_STAGE_2_COPY_BIT,
                  VK_ACCESS_2_TRANSFER_WRITE_BIT,
                  o->ramp_ready ? VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED,
                  VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    VkBufferImageCopy cp
        = {.bufferOffset     = offset,
           .imageSubresource = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .layerCount = 1},
           .imageExtent      = {256, 1, 1}};
    vkCmdCopyBufferToImage(o->command_buffer,
                           o->frame_buffer.handle,
                           o->ramp.handle,
                           VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                           1,
                           &cp);
    image_barrier(o->command_buffer,
                  o->ramp.handle,
                  VK_PIPELINE_STAGE_2_COPY_BIT,
                  VK_ACCESS_2_TRANSFER_WRITE_BIT,
                  VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                  VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                  VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                  VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
    memcpy(o->ramp_bytes, bytes, 2048);
    return true;
}
static VkDescriptorSet graphics_set(struct walle_vk_output*     o,
                                    const struct walle_vk_draw* draw,
                                    VkImageView                 source,
                                    VkImageView                 scene)
{
    auto            r         = o->renderer;
    VkDeviceSize    alignment = r->properties.limits.minUniformBufferOffsetAlignment;
    VkDeviceSize    gu        = stage_bytes(o, draw ? draw->glass : nullptr, 272, alignment),
                    eu        = stage_bytes(o, draw ? draw->effect : nullptr, 184, alignment);
    VkDescriptorSet set       = allocate_set(o, LAYOUT_GRAPHICS);
    if (!set || gu == UINT64_MAX || eu == UINT64_MAX)
        return VK_NULL_HANDLE;
    write_uniform(o, set, 0, gu, 272);
    write_uniform(o, set, 4, eu, 184);
    write_image(r->device,
                set,
                1,
                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                source,
                VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL,
                VK_NULL_HANDLE);
    write_image(r->device,
                set,
                2,
                VK_DESCRIPTOR_TYPE_SAMPLER,
                VK_NULL_HANDLE,
                VK_IMAGE_LAYOUT_UNDEFINED,
                r->linear_sampler);
    write_image(r->device,
                set,
                3,
                VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT,
                scene,
                VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ,
                VK_NULL_HANDLE);
    write_image(r->device,
                set,
                5,
                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                o->ramp.handle ? o->ramp.view : o->incoming.view,
                VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL,
                VK_NULL_HANDLE);
    write_image(r->device,
                set,
                6,
                VK_DESCRIPTOR_TYPE_SAMPLER,
                VK_NULL_HANDLE,
                VK_IMAGE_LAYOUT_UNDEFINED,
                r->linear_sampler);
    write_image(r->device,
                set,
                7,
                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                o->mask.handle ? o->mask.view : o->incoming.view,
                VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL,
                VK_NULL_HANDLE);
    write_image(r->device,
                set,
                8,
                VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
                o->tint.handle ? o->tint.view : o->incoming.view,
                VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL,
                VK_NULL_HANDLE);
    return set;
}
static bool record_wallpaper(struct walle_vk_output* o, VkImageView scene, VkImageView source)
{
    VkDescriptorSet set = graphics_set(o, nullptr, source, scene);
    if (!set)
        return false;
    begin_render(o->command_buffer,
                 scene,
                 VK_NULL_HANDLE,
                 o->extent.width,
                 o->extent.height,
                 true,
                 false,
                 false);
    vkCmdBindPipeline(
        o->command_buffer, VK_PIPELINE_BIND_POINT_GRAPHICS, o->renderer->pipelines[PIPE_WALLPAPER]);
    set_viewport(
        o->command_buffer, o->extent.width, o->extent.height, (VkRect2D){{0, 0}, o->extent});
    struct walle_vk_vertex v[4];
    const float p[] = {0, 0, (float)o->extent.width, (float)o->extent.height}, uv[] = {0, 0, 1, 1};
    quad_vertices(v, p, uv);
    const uint32_t    indices[] = {0, 1, 2, 0, 2, 3};
    struct glass_push push
        = {.resolution = {(float)o->extent.width, (float)o->extent.height}, .edr = 1};
    bool ok = issue_geometry(
        o, o->renderer->pipeline_layout[LAYOUT_GRAPHICS], set, v, 4, indices, 6, &push, 32);
    vkCmdEndRendering(o->command_buffer);
    return ok;
}
static bool record_draw(struct walle_vk_output*     o,
                        VkImageView                 scene,
                        const struct walle_vk_draw* draw,
                        float                       material_opacity,
                        bool                        clear_target)
{
    auto r    = o->renderer;
    auto cmd  = o->command_buffer;
    bool tint = draw->pass == WALLE_VK_TINT_GRADIENT, mask = draw->pass == WALLE_VK_TINT_MASK;
    bool local         = draw->pass == WALLE_VK_FACE || draw->pass == WALLE_VK_HIGHLIGHT
                         || draw->pass == WALLE_VK_PRODUCT_FINISH;
    VkImageView source = o->incoming.view;
    if (draw->pass == WALLE_VK_GLASS_REGULAR || draw->pass == WALLE_VK_GLASS_CLEAR)
        source = o->pyramid.handle ? o->pyramid.view : o->capture.view;
    VkDescriptorSet set = graphics_set(o, draw, source, scene);
    if (!set)
        return false;
    VkImageView target = tint ? o->tint.view : mask ? o->mask.view : scene;
    if (tint || mask) {
        struct walle_vk_image* image = tint ? &o->tint : &o->mask;
        image_barrier(cmd,
                      image->handle,
                      VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT,
                      VK_ACCESS_2_SHADER_SAMPLED_READ_BIT | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                      VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT
                          | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                      clear_target ? VK_IMAGE_LAYOUT_UNDEFINED : VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL,
                      VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ);
    }
    local_read_barrier(cmd);
    begin_render(cmd,
                 target,
                 scene,
                 o->extent.width,
                 o->extent.height,
                 (tint || mask) && clear_target,
                 local,
                 tint);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, r->pipelines[draw->pass]);
    int64_t x0 = draw->scissor[0], y0 = draw->scissor[1], x1 = x0 + draw->scissor[2],
            y1 = y0 + draw->scissor[3];
    if (x0 < 0)
        x0 = 0;
    if (y0 < 0)
        y0 = 0;
    if (x1 > o->extent.width)
        x1 = o->extent.width;
    if (y1 > o->extent.height)
        y1 = o->extent.height;
    bool ok = true;
    if (x1 > x0 && y1 > y0) {
        set_viewport(
            cmd,
            o->extent.width,
            o->extent.height,
            (VkRect2D){{(int32_t)x0, (int32_t)y0}, {(uint32_t)(x1 - x0), (uint32_t)(y1 - y0)}});
        struct glass_push push = {.resolution = {(float)o->extent.width, (float)o->extent.height},
                                  .edr        = draw->edr_scale,
                                  .mode       = draw->shape_mode,
                                  .material_opacity = material_opacity};
        ok                     = issue_geometry(o,
                                                r->pipeline_layout[LAYOUT_GRAPHICS],
                                                set,
                                                draw->vertices,
                                                draw->vertex_count,
                                                draw->indices,
                                                draw->index_count,
                                                &push,
                                                32);
    }
    vkCmdEndRendering(cmd);
    if (tint || mask)
        image_barrier(cmd,
                      tint ? o->tint.handle : o->mask.handle,
                      VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                      VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT,
                      VK_ACCESS_2_SHADER_SAMPLED_READ_BIT | VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                      VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ,
                      VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
    return ok;
}

static int retry_ioctl(int fd, unsigned long operation, void* argument)
{
    int rc;
    do {
        rc = ioctl(fd, operation, argument);
    } while (rc < 0 && errno == EINTR);
    return rc;
}
static bool submit_frame(struct walle_vk_output* o, struct walle_vk_present_image* present)
{
    auto r = o->renderer;
    if (o->frame_pending
        || !vk_check(vkEndCommandBuffer(o->command_buffer), "vkEndCommandBuffer(frame)"))
        return false;
    bool wait_acquire = false;
    if (o->wayland_surface) {
        struct dma_buf_export_sync_file ex = {.flags = DMA_BUF_SYNC_WRITE, .fd = -1};
        if (retry_ioctl(present->dmabuf_fd, DMA_BUF_IOCTL_EXPORT_SYNC_FILE, &ex) < 0) {
            perror("DMA_BUF_IOCTL_EXPORT_SYNC_FILE");
            return false;
        }
        if (ex.fd >= 0) {
            VkImportSemaphoreFdInfoKHR in
                = {.sType      = VK_STRUCTURE_TYPE_IMPORT_SEMAPHORE_FD_INFO_KHR,
                   .semaphore  = o->acquire_semaphore,
                   .flags      = VK_SEMAPHORE_IMPORT_TEMPORARY_BIT,
                   .handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT,
                   .fd         = ex.fd};
            if (!vk_check(r->import_semaphore_fd(r->device, &in),
                          "vkImportSemaphoreFdKHR(acquire)")) {
                close(ex.fd);
                return false;
            }
            /* Successful Vulkan import consumes the descriptor. */
            wait_acquire = true;
        }
    }
    if (!vk_check(vkResetFences(r->device, 1, &o->frame_fence),
                  "vkResetFences(frame before submit)"))
        return false;
    VkCommandBufferSubmitInfo cmd    = {.sType         = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO,
                                        .commandBuffer = o->command_buffer};
    VkSemaphoreSubmitInfo     wait   = {.sType     = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO,
                                        .semaphore = o->acquire_semaphore,
                                        .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT};
    VkSemaphoreSubmitInfo     signal = {.sType     = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO,
                                        .semaphore = o->render_semaphore,
                                        .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT};
    VkSubmitInfo2             si     = {.sType                    = VK_STRUCTURE_TYPE_SUBMIT_INFO_2,
                                        .waitSemaphoreInfoCount   = wait_acquire ? 1u : 0u,
                                        .pWaitSemaphoreInfos      = &wait,
                                        .commandBufferInfoCount   = 1,
                                        .pCommandBufferInfos      = &cmd,
                                        .signalSemaphoreInfoCount = o->wayland_surface ? 1u : 0u,
                                        .pSignalSemaphoreInfos    = &signal};
    if (!vk_check(vkQueueSubmit2(r->queue, 1, &si, o->frame_fence), "vkQueueSubmit2(frame)"))
        return false;
    o->frame_pending = true;
    if (o->wayland_surface) {
        VkSemaphoreGetFdInfoKHR info
            = {.sType      = VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR,
               .semaphore  = o->render_semaphore,
               .handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT};
        int fd = -1;
        if (!vk_check(r->get_semaphore_fd(r->device, &info, &fd),
                      "vkGetSemaphoreFdKHR(render done)"))
            return false;
        if (fd >= 0) {
            struct dma_buf_import_sync_file in = {.flags = DMA_BUF_SYNC_WRITE, .fd = fd};
            int rc    = retry_ioctl(present->dmabuf_fd, DMA_BUF_IOCTL_IMPORT_SYNC_FILE, &in);
            int error = errno;
            close(fd);
            errno = error;
            if (rc < 0) {
                perror("DMA_BUF_IOCTL_IMPORT_SYNC_FILE");
                return false;
            }
        }
        /* fd=-1 is an already-signaled payload; nothing remains to publish. */
    }
    return true;
}
static bool frame_resources(struct walle_vk_output*      o,
                            const struct walle_vk_frame* frame,
                            VkDeviceSize                 readback_size)
{
    if (frame->draw_count > 4096 || (frame->draw_count && !frame->draws))
        return false;
    VkDeviceSize alignment = o->renderer->properties.limits.minUniformBufferOffsetAlignment;
    VkDeviceSize bytes     = (frame->draw_count + 40) * (alignment * 2 + 512) + 8192;
    bool         backdrop = false, tint = false;
    for (size_t i = 0; i < frame->draw_count; i++) {
        auto         d = &frame->draws[i];
        VkDeviceSize vb, ib;
        if (d->pass >= WALLE_VK_PASS_COUNT || !isfinite(d->edr_scale) || d->edr_scale <= 0
            || !d->vertices || !d->indices || !d->vertex_count || !d->index_count
            || d->vertex_count > UINT32_MAX || d->index_count > UINT32_MAX || d->index_count % 3
            || d->scissor[2] < 0 || d->scissor[3] < 0
            || ckd_mul(&vb, (VkDeviceSize)d->vertex_count, (VkDeviceSize)sizeof *d->vertices)
            || ckd_mul(&ib, (VkDeviceSize)d->index_count, (VkDeviceSize)sizeof *d->indices)
            || ckd_add(&bytes, bytes, vb) || ckd_add(&bytes, bytes, ib))
            return false;
        for (size_t v = 0; v < d->vertex_count; v++)
            for (unsigned a = 0; a < 2; a++)
                if (!isfinite(d->vertices[v].position[a]) || !isfinite(d->vertices[v].local[a])
                    || !isfinite(d->vertices[v].source_uv[a]))
                    return false;
        for (size_t j = 0; j < d->index_count; j++)
            if (d->indices[j] >= d->vertex_count)
                return false;
        backdrop |= d->pass == WALLE_VK_GLASS_REGULAR || d->pass == WALLE_VK_GLASS_CLEAR;
        tint |= d->pass == WALLE_VK_TINT_MASK || d->pass == WALLE_VK_TINT_GRADIENT
                || d->pass == WALLE_VK_TINT_COMPOSITE;
    }
    if (backdrop && !ensure_backdrop(o, frame))
        return false;
    if (tint && (!frame->tint_ramp_rgba16f || !ensure_tint(o)))
        return false;
    if (!prepare_pool(o, (uint32_t)frame->draw_count + 40)
        || !ensure_buffer(o->renderer,
                          &o->frame_buffer,
                          bytes,
                          VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT
                              | VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT
                              | VK_BUFFER_USAGE_TRANSFER_SRC_BIT))
        return false;
    if (readback_size
        && !ensure_buffer(
            o->renderer, &o->readback_buffer, readback_size, VK_BUFFER_USAGE_TRANSFER_DST_BIT))
        return false;
    o->cursor = 0;
    return true;
}
enum walle_vk_frame_status walle_vk_output_render(struct walle_vk_output*      o,
                                                  const struct walle_vk_frame* frame)
{
    if (!o || !frame || o->renderer->fatal || !o->incoming.handle
        || (!frame->plain_incoming && !o->current.handle) || !isfinite(frame->material_opacity))
        return WALLE_VK_FRAME_FATAL;
    auto r = o->renderer;
    if (walle_vk_renderer_validation_errors(r)) {
        r->fatal = true;
        return WALLE_VK_FRAME_FATAL;
    }
    if (o->frame_pending) {
        VkResult status = vkGetFenceStatus(r->device, o->frame_fence);
        if (status == VK_NOT_READY)
            return WALLE_VK_FRAME_RETRY;
        if (!vk_check(status, "vkGetFenceStatus(frame)")) {
            r->fatal = true;
            return WALLE_VK_FRAME_FATAL;
        }
        o->frame_pending = false;
    }
    if (!collect_timing(o))
        goto fail;
    VkDeviceSize rb = 0;
    if (frame->composition_readback) {
        if (!o->composition_readback_enabled
            || ckd_mul(&rb, (VkDeviceSize)o->extent.width, (VkDeviceSize)o->extent.height)
            || ckd_mul(&rb, rb, (VkDeviceSize)4) || frame->composition_readback_size != rb)
            return WALLE_VK_FRAME_FATAL;
    }
    uint32_t index;
    if (!take_present_image(o, &index))
        return r->fatal ? WALLE_VK_FRAME_FATAL : WALLE_VK_FRAME_RETRY;
    if (!frame_resources(o, frame, rb))
        return WALLE_VK_FRAME_FATAL;
    if (!vk_check(vkResetCommandBuffer(o->command_buffer, 0), "vkResetCommandBuffer(frame)"))
        goto fail;
    VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                   .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    if (!vk_check(vkBeginCommandBuffer(o->command_buffer, &bi), "vkBeginCommandBuffer(frame)"))
        goto fail;
    if (o->timestamp_pool) {
        vkCmdResetQueryPool(o->command_buffer, o->timestamp_pool, 0, 4);
        vkCmdWriteTimestamp2(
            o->command_buffer, VK_PIPELINE_STAGE_2_TOP_OF_PIPE_BIT, o->timestamp_pool, 0);
    }
    buffer_barrier(o->command_buffer,
                   VK_PIPELINE_STAGE_2_HOST_BIT,
                   VK_ACCESS_2_HOST_WRITE_BIT,
                   VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                   VK_ACCESS_2_UNIFORM_READ_BIT | VK_ACCESS_2_VERTEX_ATTRIBUTE_READ_BIT
                       | VK_ACCESS_2_INDEX_READ_BIT | VK_ACCESS_2_TRANSFER_READ_BIT);
    bool build_backdrop = o->capture.handle && !o->backdrop_ready;
    if (build_backdrop && !record_backdrop(o))
        goto fail;
    if (o->timestamp_pool)
        vkCmdWriteTimestamp2(
            o->command_buffer, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, o->timestamp_pool, 1);
    if (!record_ramp(o, frame->tint_ramp_rgba16f))
        goto fail;
    struct walle_vk_present_image* present = &o->present_images[index];
    image_barrier_queues(
        o->command_buffer,
        present->image.handle,
        VK_PIPELINE_STAGE_2_NONE,
        0,
        VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
        VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_2_INPUT_ATTACHMENT_READ_BIT,
        present->layout,
        VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ,
        present->foreign_owned ? VK_QUEUE_FAMILY_FOREIGN_EXT : VK_QUEUE_FAMILY_IGNORED,
        present->foreign_owned ? r->queue_family : VK_QUEUE_FAMILY_IGNORED);
    if (!record_wallpaper(
            o, present->image.view, frame->plain_incoming ? o->incoming.view : o->current.view))
        goto fail;
    if (!frame->plain_incoming)
        for (size_t i = 0; i < frame->draw_count; i++)
            if (!record_draw(o,
                             present->image.view,
                             &frame->draws[i],
                             frame->material_opacity,
                             i == 0 || frame->draws[i - 1].pass != frame->draws[i].pass))
                goto fail;
    if (o->timestamp_pool)
        vkCmdWriteTimestamp2(
            o->command_buffer, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, o->timestamp_pool, 2);
    VkImageLayout         layout = VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ;
    VkPipelineStageFlags2 stage
        = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT;
    VkAccessFlags2 access
        = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_2_INPUT_ATTACHMENT_READ_BIT;
    if (rb) {
        image_barrier(o->command_buffer,
                      present->image.handle,
                      stage,
                      access,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_READ_BIT,
                      layout,
                      VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
        VkBufferImageCopy cp
            = {.imageSubresource = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .layerCount = 1},
               .imageExtent      = {o->extent.width, o->extent.height, 1}};
        vkCmdCopyImageToBuffer(o->command_buffer,
                               present->image.handle,
                               VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                               o->readback_buffer.handle,
                               1,
                               &cp);
        buffer_barrier(o->command_buffer,
                       VK_PIPELINE_STAGE_2_COPY_BIT,
                       VK_ACCESS_2_TRANSFER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_HOST_BIT,
                       VK_ACCESS_2_HOST_READ_BIT);
        layout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        stage  = VK_PIPELINE_STAGE_2_COPY_BIT;
        access = VK_ACCESS_2_TRANSFER_READ_BIT;
    }
    image_barrier_queues(o->command_buffer,
                         present->image.handle,
                         stage,
                         access,
                         VK_PIPELINE_STAGE_2_NONE,
                         0,
                         layout,
                         VK_IMAGE_LAYOUT_GENERAL,
                         o->wayland_surface ? r->queue_family : VK_QUEUE_FAMILY_IGNORED,
                         o->wayland_surface ? VK_QUEUE_FAMILY_FOREIGN_EXT
                                            : VK_QUEUE_FAMILY_IGNORED);
    if (o->timestamp_pool)
        vkCmdWriteTimestamp2(
            o->command_buffer, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, o->timestamp_pool, 3);
    if (walle_vk_renderer_validation_errors(r) || !submit_frame(o, present)
        || walle_vk_renderer_validation_errors(r))
        goto fail;
    if (o->timestamp_pool) {
        o->timing_pending         = true;
        o->pending_built_backdrop = build_backdrop;
        o->timed_frame_id++;
    }
    present->layout        = VK_IMAGE_LAYOUT_GENERAL;
    present->foreign_owned = o->wayland_surface != nullptr;
    if (build_backdrop)
        o->backdrop_ready = true;
    if (frame->tint_ramp_rgba16f)
        o->ramp_ready = true;
    if (rb) {
        if (!wait_submission(r, o->frame_fence, &o->frame_pending))
            goto fail;
        memcpy(frame->composition_readback, o->readback_buffer.memory.mapped, (size_t)rb);
    }
    if (o->wayland_surface) {
        wl_surface_attach(o->wayland_surface, present->buffer, 0, 0);
        wl_surface_damage_buffer(
            o->wayland_surface, 0, 0, (int32_t)o->extent.width, (int32_t)o->extent.height);
        present->busy = true;
    }
    o->last_present_image = index;
    if (walle_vk_renderer_validation_errors(r))
        goto fail;
    return WALLE_VK_FRAME_OK;
fail:
    r->fatal = true;
    return WALLE_VK_FRAME_FATAL;
}
bool walle_vk_renderer_create_offscreen(const char* selector, struct walle_vk_renderer** result)
{
    return walle_vk_renderer_create(nullptr, selector && *selector ? selector : "cpu", result);
}
bool walle_vk_output_create_offscreen(struct walle_vk_renderer* r,
                                      uint32_t                  w,
                                      uint32_t                  h,
                                      struct walle_vk_output**  result)
{
    if (!r || r->display)
        return false;
    return walle_vk_output_create(r, nullptr, w, h, true, result);
}
void walle_vk_output_promote(struct walle_vk_output* o)
{
    if (!o || !o->renderer->device)
        return;
    if (!wait_submission(o->renderer, o->frame_fence, &o->frame_pending))
        return;
    if (!collect_timing(o))
        return;
    destroy_image(o->renderer->device, &o->current);
    destroy_image(o->renderer->device, &o->incoming);
    destroy_transition_resources(o);
    compact_present_images(o);
}
void walle_vk_output_abort_transition(struct walle_vk_output* o)
{
    walle_vk_output_promote(o);
}
bool walle_vk_output_resize(struct walle_vk_output* o, uint32_t w, uint32_t h)
{
    if (!o || o->renderer->fatal || !graphics_extent_valid(o->renderer, w, h))
        return false;
    if (o->extent.width == w && o->extent.height == h)
        return true;
    if (!wait_submission(o->renderer, o->frame_fence, &o->frame_pending))
        return false;
    walle_vk_output_abort_transition(o);
    destroy_present_images(o);
    o->extent = (VkExtent2D){w, h};
    return initialize_present_images(o);
}
void walle_vk_output_destroy(struct walle_vk_output* o)
{
    if (!o)
        return;
    auto r = o->renderer;
    if (r && r->device) {
        /* Submission failures leave no pending flag and no unsignaled-fence wait. */
        if (o->frame_pending && !wait_submission(r, o->frame_fence, &o->frame_pending))
            vkDeviceWaitIdle(r->device);
        destroy_image(r->device, &o->current);
        destroy_image(r->device, &o->incoming);
        destroy_transition_resources(o);
        if (o->timestamp_pool)
            vkDestroyQueryPool(r->device, o->timestamp_pool, nullptr);
        if (o->acquire_semaphore)
            vkDestroySemaphore(r->device, o->acquire_semaphore, nullptr);
        if (o->render_semaphore)
            vkDestroySemaphore(r->device, o->render_semaphore, nullptr);
        if (o->frame_fence)
            vkDestroyFence(r->device, o->frame_fence, nullptr);
        if (o->command_pool)
            vkDestroyCommandPool(r->device, o->command_pool, nullptr);
        destroy_present_images(o);
    }
    if (r && r->instance && o->surface)
        vkDestroySurfaceKHR(r->instance, o->surface, nullptr);
    free(o);
}
uint64_t walle_vk_renderer_destroy_checked(struct walle_vk_renderer* r)
{
    if (!r)
        return 0;
    if (r->device) {
        vkDeviceWaitIdle(r->device);
        if (r->upload_fence)
            vkDestroyFence(r->device, r->upload_fence, nullptr);
        if (r->upload_command_pool)
            vkDestroyCommandPool(r->device, r->upload_command_pool, nullptr);
        if (r->linear_sampler)
            vkDestroySampler(r->device, r->linear_sampler, nullptr);
        for (uint32_t i = 0; i < PIPE_COUNT; i++)
            if (r->pipelines[i])
                vkDestroyPipeline(r->device, r->pipelines[i], nullptr);
        for (uint32_t i = 0; i < 4; i++)
            if (r->capture_pipelines[i])
                vkDestroyPipeline(r->device, r->capture_pipelines[i], nullptr);
        for (uint32_t i = 0; i < 2; i++)
            if (r->compute_pipelines[i])
                vkDestroyPipeline(r->device, r->compute_pipelines[i], nullptr);
        for (uint32_t i = 0; i < LAYOUT_COUNT; i++) {
            if (r->pipeline_layout[i])
                vkDestroyPipelineLayout(r->device, r->pipeline_layout[i], nullptr);
            if (r->set_layout[i])
                vkDestroyDescriptorSetLayout(r->device, r->set_layout[i], nullptr);
        }
        vkDestroyDevice(r->device, nullptr);
    }
    if (r->dmabuf.object)
        zwp_linux_dmabuf_feedback_v1_destroy(r->dmabuf.object);
    if (r->dmabuf.factory)
        zwp_linux_dmabuf_v1_destroy(r->dmabuf.factory);
    dmabuf_feedback_reset_table(&r->dmabuf);
    free(r->dmabuf.candidates);
    destroy_debug_messenger(r);
    if (r->instance)
        vkDestroyInstance(r->instance, nullptr);
    uint64_t validation_errors = walle_vk_renderer_validation_errors(r);
    free(r->device_selector);
    free(r);
    return validation_errors;
}

void walle_vk_renderer_destroy(struct walle_vk_renderer* renderer)
{
    uint64_t errors = walle_vk_renderer_destroy_checked(renderer);
    (void)errors;
}
uint64_t walle_vk_renderer_validation_errors(const struct walle_vk_renderer* renderer)
{
    return renderer && renderer->validation_enabled
               ? atomic_load_explicit(&renderer->validation_error_count, memory_order_relaxed)
               : 0;
}
bool walle_vk_renderer_validation_active(const struct walle_vk_renderer* renderer)
{
    return renderer && renderer->validation_enabled;
}

static bool collect_timing(struct walle_vk_output* output)
{
    if (!output->timestamp_pool || !output->timing_pending || output->frame_pending)
        return true;
    uint64_t ticks[4];
    if (!vk_check(vkGetQueryPoolResults(output->renderer->device,
                                        output->timestamp_pool,
                                        0,
                                        4,
                                        sizeof ticks,
                                        ticks,
                                        sizeof ticks[0],
                                        VK_QUERY_RESULT_64_BIT),
                  "vkGetQueryPoolResults(completed frame)"))
        return false;
    uint64_t mask
        = output->timestamp_bits == 64 ? UINT64_MAX : (UINT64_C(1) << output->timestamp_bits) - 1;
    double period       = (double)output->renderer->properties.limits.timestampPeriod;
    output->last_timing = (struct walle_vk_diagnostics){
        .timing_enabled   = true,
        .timing_available = true,
        .built_backdrop   = output->pending_built_backdrop,
        .timed_frame_id   = output->timed_frame_id,
        .gpu_capture_ns
        = output->pending_built_backdrop ? (double)((ticks[1] - ticks[0]) & mask) * period : 0,
        .gpu_draw_ns  = (double)((ticks[2] - ticks[1]) & mask) * period,
        .gpu_frame_ns = (double)((ticks[2] - ticks[0]) & mask) * period,
        .gpu_tail_ns  = (double)((ticks[3] - ticks[2]) & mask) * period,
        .gpu_total_ns = (double)((ticks[3] - ticks[0]) & mask) * period,
    };
    output->timing_pending = false;
    return true;
}

bool walle_vk_output_enable_timing(struct walle_vk_output* output, bool enabled)
{
    if (!output || !output->renderer->device || output->renderer->fatal)
        return false;
    if ((output->timestamp_pool != VK_NULL_HANDLE) == enabled)
        return true;
    if (!wait_submission(output->renderer, output->frame_fence, &output->frame_pending)
        || !collect_timing(output))
        return false;
    if (!enabled) {
        vkDestroyQueryPool(output->renderer->device, output->timestamp_pool, nullptr);
        output->timestamp_pool = VK_NULL_HANDLE;
        return true;
    }
    auto renderer = output->renderer;
    if (!renderer->properties.limits.timestampComputeAndGraphics
        || !(renderer->properties.limits.timestampPeriod > 0))
        return false;
    uint32_t count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(renderer->physical_device, &count, nullptr);
    VkQueueFamilyProperties* families = calloc(count, sizeof *families);
    if (!families)
        return false;
    vkGetPhysicalDeviceQueueFamilyProperties(renderer->physical_device, &count, families);
    uint32_t bits
        = renderer->queue_family < count ? families[renderer->queue_family].timestampValidBits : 0;
    free(families);
    if (!bits || bits > 64)
        return false;
    VkQueryPoolCreateInfo create = {
        .sType      = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,
        .queryType  = VK_QUERY_TYPE_TIMESTAMP,
        .queryCount = 4,
    };
    if (!vk_check(vkCreateQueryPool(renderer->device, &create, nullptr, &output->timestamp_pool),
                  "vkCreateQueryPool(frame diagnostics)"))
        return false;
    output->timestamp_bits = bits;
    output->last_timing    = (struct walle_vk_diagnostics){};
    return true;
}

void walle_vk_renderer_memory_stats(const struct walle_vk_renderer* renderer,
                                    struct walle_vk_memory_stats*   result)
{
    if (result)
        *result = renderer ? renderer->memory_stats : (struct walle_vk_memory_stats){};
}

bool walle_vk_output_diagnostics(struct walle_vk_output*      output,
                                 bool                         wait,
                                 struct walle_vk_diagnostics* result)
{
    if (!output || !result)
        return false;
    if (output->timestamp_pool && output->timing_pending) {
        if (wait) {
            if (!wait_submission(output->renderer, output->frame_fence, &output->frame_pending))
                return false;
        } else if (output->frame_pending) {
            VkResult status = vkGetFenceStatus(output->renderer->device, output->frame_fence);
            if (status == VK_SUCCESS)
                output->frame_pending = false;
            else if (status != VK_NOT_READY)
                return vk_check(status, "vkGetFenceStatus(diagnostics)");
        }
        if (!collect_timing(output))
            return false;
    }
    *result                 = output->last_timing;
    result->timing_enabled  = output->timestamp_pool != VK_NULL_HANDLE;
    result->timing_pending  = output->timing_pending;
    result->renderer_memory = output->renderer->memory_stats;
    result->source_bytes    = output->current.memory.size + output->incoming.memory.size;
    result->backdrop_bytes  = output->capture.memory.size + output->pyramid.memory.size;
    result->scratch_bytes   = output->blur_scratch.memory.size;
    result->effect_bytes
        = output->tint.memory.size + output->mask.memory.size + output->ramp.memory.size;
    result->frame_buffer_bytes = output->frame_buffer.memory.size;
    result->readback_bytes     = output->readback_buffer.memory.size;
    for (uint32_t i = 0; i < 2; ++i) {
        if (output->present_images[i].image.memory.handle) {
            result->present_bytes += output->present_images[i].image.memory.size;
            result->present_image_count++;
        }
    }
    result->output_bytes = result->source_bytes + result->backdrop_bytes + result->scratch_bytes
                           + result->effect_bytes + result->frame_buffer_bytes
                           + result->readback_bytes + result->present_bytes;
    return true;
}
