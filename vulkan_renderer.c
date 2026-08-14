#define _GNU_SOURCE

#include "vulkan_renderer.h"

#include <assert.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <stdarg.h>
#include <stdckdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <unistd.h>
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "parity/liquid_glass_raster.h"
#include "protocols/linux-dmabuf-v1.h"

enum
{
    WALLE_VK_MASK_BINDING_AXIS       = 0,
    WALLE_VK_MASK_BINDING_SQRT       = 1,
    WALLE_VK_MASK_BINDING_OWNER      = 2,
    WALLE_VK_MASK_BINDING_MAPPING    = 3,
    WALLE_VK_COMPOSE_BINDING_TEX_A   = 0,
    WALLE_VK_COMPOSE_BINDING_GLASS_A = 1,
    WALLE_VK_COMPOSE_BINDING_TEX_B   = 2,
    WALLE_VK_COMPOSE_BINDING_GLASS_B = 3,
    WALLE_VK_COMPOSE_BINDING_MASK    = 4,
    WALLE_VK_COMPOSE_BINDING_SAMPLER = 5,
};

constexpr uint32_t WALLE_VK_OWNER_VECTOR_COUNT
    = sizeof(struct walle_lg_reveal_owner_block) / sizeof(int32_t[4]);
constexpr VkFormat WALLE_VK_PRESENT_FORMAT       = VK_FORMAT_B8G8R8A8_UNORM;
constexpr VkFormat WALLE_VK_WALLPAPER_FORMAT     = VK_FORMAT_R8G8B8A8_SRGB;
constexpr VkFormat WALLE_VK_MASK_FORMAT          = VK_FORMAT_R8_UINT;
constexpr uint32_t WALLE_VK_REQUIRED_API_VERSION = VK_API_VERSION_1_4;

alignas(4) static const uint8_t WALLE_VK_MASK_VERTEX_SPIRV[] = {
#embed "build/shaders/maskVertex.spv" if_empty(0)
};

alignas(4) static const uint8_t WALLE_VK_MASK_FRAGMENT_SPIRV[] = {
#embed "build/shaders/maskFragment.spv" if_empty(0)
};

alignas(4) static const uint8_t WALLE_VK_COMPOSE_VERTEX_SPIRV[] = {
#embed "build/shaders/composeVertex.spv" if_empty(0)
};

alignas(4) static const uint8_t WALLE_VK_COMPOSE_FRAGMENT_SPIRV[] = {
#embed "build/shaders/composeFragment.spv" if_empty(0)
};

static const uint8_t WALLE_VK_REVEAL_RASTER_P25[] = {
#embed "parity/raster_p25_selector_ceil_bits.bin" limit(2097152) if_empty(0)
};

static const uint8_t WALLE_VK_APPLE_FAST_SQRT[] = {
#embed "parity/apple_fast_sqrt_correction_nibbles.bin" limit(4194304) if_empty(0)
};

static_assert(sizeof WALLE_VK_REVEAL_RASTER_P25 == 2u * 1024u * 1024u);
static_assert(sizeof WALLE_VK_APPLE_FAST_SQRT == 4u * 1024u * 1024u);
static_assert(sizeof WALLE_VK_MASK_VERTEX_SPIRV % sizeof(uint32_t) == 0);
static_assert(sizeof WALLE_VK_MASK_FRAGMENT_SPIRV % sizeof(uint32_t) == 0);
static_assert(sizeof WALLE_VK_COMPOSE_VERTEX_SPIRV % sizeof(uint32_t) == 0);
static_assert(sizeof WALLE_VK_COMPOSE_FRAGMENT_SPIRV % sizeof(uint32_t) == 0);
static_assert(WALLE_VK_OWNER_VECTOR_COUNT == 283);

struct walle_vk_memory
{
    VkDeviceMemory        handle;
    VkDeviceSize          size;
    VkMemoryPropertyFlags properties;
    void*                 mapped;
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
};

struct walle_vk_texture_pair
{
    struct walle_vk_image standard;
    struct walle_vk_image glass;
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
};

struct walle_vk_mask_push
{
    float    resolution[2];
    float    compact_family;
    uint32_t general_child_count;
    uint32_t owner_count;
    uint32_t base_owner_count;
    uint32_t packed_width;
    uint32_t primitive_count;
    /* Hardware-measured A2 presentation deficit band (later-78):
       x0, x1, yMax, enable. */
    float secondary_band[4];
};

struct walle_vk_compose_push
{
    float timeline[4];
    float geometry[4];
};

static_assert(sizeof(struct walle_vk_mask_push) == 48);
static_assert(sizeof(struct walle_vk_compose_push) == 32);
static_assert(offsetof(struct walle_vk_compose_push, timeline) == 0);
static_assert(offsetof(struct walle_vk_compose_push, geometry) == 16);

struct walle_vk_renderer
{
    struct wl_display*       display;
    char*                    device_selector;
    VkInstance               instance;
    VkDebugUtilsMessengerEXT debug_messenger;

    VkPhysicalDevice                             physical_device;
    VkPhysicalDeviceProperties                   properties;
    VkPhysicalDeviceMemoryProperties             memory_properties;
    VkPhysicalDeviceVulkan14Properties           properties14;
    VkDevice                                     device;
    uint32_t                                     queue_family;
    VkQueue                                      queue;
    PFN_vkGetMemoryFdKHR                         get_memory_fd;
    PFN_vkGetImageDrmFormatModifierPropertiesEXT get_image_drm_format_modifier_properties;

    VkDescriptorSetLayout mask_set_layout;
    VkDescriptorSetLayout compose_set_layout;
    VkPipelineLayout      mask_pipeline_layout;
    VkPipelineLayout      compose_pipeline_layout;
    VkPipeline            mask_pipeline;
    VkPipeline            compose_pipeline;
    VkSampler             linear_sampler;

    struct walle_vk_buffer sqrt_buffer;
    uint32_t               transition_resource_users;
    VkCommandPool          upload_command_pool;
    VkCommandBuffer        upload_command_buffer;
    VkFence                upload_fence;

    struct walle_vk_dmabuf_feedback dmabuf;

    bool validation_enabled;
    bool device_ready;
    bool fatal;
};

struct walle_vk_output
{
    struct walle_vk_renderer* renderer;
    VkSurfaceKHR              surface;
    struct wl_surface*        wayland_surface;
    VkExtent2D                extent;

    struct walle_vk_present_image present_images[2];
    uint32_t                      next_present_image;
    uint32_t                      last_present_image;
    uint32_t                      idle_present_image;
    uint32_t                      present_drm_format;
    uint32_t                      present_plane_count;
    uint64_t                      present_modifier;
    bool                          compact_present;

    VkCommandPool   command_pool;
    VkCommandBuffer command_buffer;
    VkFence         frame_fence;

    VkDescriptorPool descriptor_pool;
    VkDescriptorSet  mask_set;
    VkDescriptorSet  compose_set;

    struct walle_vk_texture_pair current;
    struct walle_vk_texture_pair incoming;
    struct walle_vk_image        mask;
    VkImageLayout                mask_layout;

    struct walle_vk_buffer transition_buffer;
    struct walle_vk_buffer staging_buffer;
    struct walle_vk_buffer readback_buffer;

    VkDeviceSize vertex_offset;
    VkDeviceSize index_offset;
    VkDeviceSize owner_offset;
    VkDeviceSize mapping_offset;
    VkDeviceSize axis_offset;
    VkDeviceSize general_offset;
    VkDeviceSize general_constant_offset;
    VkDeviceSize general_constant_capacity;

    uint32_t axis_packed_width;
    bool     transition_resources_ready;
    bool     mask_descriptors_ready;
    bool     compose_descriptors_ready;
    bool     compose_descriptors_first_boot;
    bool     composition_readback_enabled;
};

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
    (void)user_data;
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

static void destroy_memory(VkDevice device, struct walle_vk_memory* memory)
{
    if (memory->mapped)
        vkUnmapMemory(device, memory->handle);
    if (memory->handle)
        vkFreeMemory(device, memory->handle, nullptr);
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

static bool create_image(struct walle_vk_renderer* renderer,
                         uint32_t                  width,
                         uint32_t                  height,
                         VkFormat                  format,
                         VkImageUsageFlags         usage,
                         struct walle_vk_image*    result)
{
    if (width == 0 || height == 0)
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
    struct walle_vk_renderer* renderer       = output->renderer;
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
            "vkAllocateMemory(dma-buf present)")
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
    close(memory_fd);
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
    constexpr VkImageUsageFlags usage
        = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    if (index >= 2 || output->present_images[index].image.handle)
        return false;
    return create_present_image(output,
                                output->present_drm_format,
                                output->present_modifier,
                                output->present_plane_count,
                                usage,
                                &output->present_images[index]);
}

static bool initialize_present_images(struct walle_vk_output* output)
{
    constexpr VkImageUsageFlags usage
        = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
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

static bool begin_upload(struct walle_vk_renderer* renderer)
{
    if (!vk_check(
            vkWaitForFences(renderer->device, 1, &renderer->upload_fence, VK_TRUE, UINT64_MAX),
            "vkWaitForFences(upload)"))
        return false;
    if (!vk_check(vkResetFences(renderer->device, 1, &renderer->upload_fence),
                  "vkResetFences(upload)"))
        return false;
    if (!vk_check(vkResetCommandBuffer(renderer->upload_command_buffer, 0),
                  "vkResetCommandBuffer(upload)"))
        return false;
    VkCommandBufferBeginInfo begin_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    return vk_check(vkBeginCommandBuffer(renderer->upload_command_buffer, &begin_info),
                    "vkBeginCommandBuffer(upload)");
}

static bool end_upload(struct walle_vk_renderer* renderer)
{
    if (!vk_check(vkEndCommandBuffer(renderer->upload_command_buffer),
                  "vkEndCommandBuffer(upload)"))
        return false;
    VkCommandBufferSubmitInfo command_info = {
        .sType         = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO,
        .commandBuffer = renderer->upload_command_buffer,
    };
    VkSubmitInfo2 submit_info = {
        .sType                  = VK_STRUCTURE_TYPE_SUBMIT_INFO_2,
        .commandBufferInfoCount = 1,
        .pCommandBufferInfos    = &command_info,
    };
    if (!vk_check(vkQueueSubmit2(renderer->queue, 1, &submit_info, renderer->upload_fence),
                  "vkQueueSubmit2(upload)"))
        return false;
    return vk_check(
        vkWaitForFences(renderer->device, 1, &renderer->upload_fence, VK_TRUE, UINT64_MAX),
        "vkWaitForFences(upload completion)");
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
        .messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT
                       | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT
                       | VK_DEBUG_UTILS_MESSAGE_TYPE_PERFORMANCE_BIT_EXT,
        .pfnUserCallback = debug_callback,
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
    uint32_t extension_count = 2;
    if (!instance_extension_available(extensions[0])
        || !instance_extension_available(extensions[1])) {
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
    }

    VkApplicationInfo application_info = {
        .sType              = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName   = "walle",
        .applicationVersion = VK_MAKE_API_VERSION(0, 0, 0, 1),
        .pEngineName        = "walle-vulkan",
        .engineVersion      = VK_MAKE_API_VERSION(0, 0, 0, 1),
        .apiVersion         = WALLE_VK_REQUIRED_API_VERSION,
    };
    VkInstanceCreateInfo create_info = {
        .sType                   = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
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

static bool select_queue_family(VkPhysicalDevice   physical_device,
                                VkSurfaceKHR       surface,
                                struct wl_display* display,
                                uint32_t*          result)
{
    uint32_t count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical_device, &count, nullptr);
    if (count == 0)
        return false;
    VkQueueFamilyProperties* properties = calloc(count, sizeof *properties);
    if (!properties)
        return false;
    vkGetPhysicalDeviceQueueFamilyProperties(physical_device, &count, properties);
    for (uint32_t index = 0; index < count; ++index) {
        VkBool32 surface_support = VK_FALSE;
        if ((properties[index].queueFlags & VK_QUEUE_GRAPHICS_BIT) != 0
            && vkGetPhysicalDeviceSurfaceSupportKHR(
                   physical_device, index, surface, &surface_support)
                   == VK_SUCCESS
            && surface_support
            && vkGetPhysicalDeviceWaylandPresentationSupportKHR(physical_device, index, display)) {
            free(properties);
            *result = index;
            return true;
        }
    }
    free(properties);
    return false;
}

static bool device_candidate(struct walle_vk_renderer*           renderer,
                             VkPhysicalDevice                    physical_device,
                             VkSurfaceKHR                        surface,
                             uint32_t*                           queue_family,
                             VkPhysicalDeviceProperties2*        properties2,
                             VkPhysicalDeviceVulkan14Properties* properties14)
{
    *properties14 = (VkPhysicalDeviceVulkan14Properties){
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_PROPERTIES,
    };
    *properties2 = (VkPhysicalDeviceProperties2){
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
        .pNext = properties14,
    };
    vkGetPhysicalDeviceProperties2(physical_device, properties2);
    if (properties2->properties.apiVersion < WALLE_VK_REQUIRED_API_VERSION
        || !device_extension_available(physical_device, VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME)
        || !device_extension_available(physical_device,
                                       VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME)
        || !device_extension_available(physical_device,
                                       VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME)
        || !device_extension_available(physical_device, VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME)
        || !select_queue_family(physical_device, surface, renderer->display, queue_family))
        return false;

    VkPhysicalDeviceVulkan14Features features14 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES,
    };
    VkPhysicalDeviceVulkan13Features features13 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &features14,
    };
    VkPhysicalDeviceVulkan12Features features12 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &features13,
    };
    VkPhysicalDeviceVulkan11Features features11 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,
        .pNext = &features12,
    };
    VkPhysicalDeviceFeatures2 features2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &features11,
    };
    vkGetPhysicalDeviceFeatures2(physical_device, &features2);
    if (!features2.features.shaderInt64 || !features2.features.geometryShader
        || !features11.shaderDrawParameters || !features12.vulkanMemoryModel
        || !features12.vulkanMemoryModelDeviceScope || !features13.dynamicRendering
        || !features13.synchronization2 || !features14.maintenance5 || !features14.maintenance6)
        return false;

    if (!format_supports(physical_device,
                         WALLE_VK_WALLPAPER_FORMAT,
                         VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_BIT
                             | VK_FORMAT_FEATURE_2_TRANSFER_DST_BIT)
        || !format_supports(physical_device,
                            WALLE_VK_MASK_FORMAT,
                            VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT
                                | VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_BIT
                                | VK_FORMAT_FEATURE_2_TRANSFER_SRC_BIT)
        || !format_supports(
            physical_device, WALLE_VK_PRESENT_FORMAT, VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT))
        return false;
    return true;
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

static bool create_descriptor_layouts(struct walle_vk_renderer* renderer)
{
    VkDescriptorSetLayoutBinding mask_bindings[6] = {};
    for (uint32_t index = 0; index < 6; ++index) {
        mask_bindings[index] = (VkDescriptorSetLayoutBinding){
            .binding         = index,
            .descriptorType  = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .descriptorCount = 1,
            .stageFlags      = VK_SHADER_STAGE_FRAGMENT_BIT,
        };
    }
    VkDescriptorSetLayoutCreateInfo mask_info = {
        .sType        = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 6,
        .pBindings    = mask_bindings,
    };
    if (!vk_check(vkCreateDescriptorSetLayout(
                      renderer->device, &mask_info, nullptr, &renderer->mask_set_layout),
                  "vkCreateDescriptorSetLayout(mask)"))
        return false;

    VkDescriptorSetLayoutBinding compose_bindings[6] = {};
    for (uint32_t index = 0; index < 5; ++index) {
        compose_bindings[index] = (VkDescriptorSetLayoutBinding){
            .binding         = index,
            .descriptorType  = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
            .descriptorCount = 1,
            .stageFlags      = VK_SHADER_STAGE_FRAGMENT_BIT,
        };
    }
    compose_bindings[5] = (VkDescriptorSetLayoutBinding){
        .binding         = WALLE_VK_COMPOSE_BINDING_SAMPLER,
        .descriptorType  = VK_DESCRIPTOR_TYPE_SAMPLER,
        .descriptorCount = 1,
        .stageFlags      = VK_SHADER_STAGE_FRAGMENT_BIT,
    };
    VkDescriptorSetLayoutCreateInfo compose_info = {
        .sType        = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 6,
        .pBindings    = compose_bindings,
    };
    return vk_check(vkCreateDescriptorSetLayout(
                        renderer->device, &compose_info, nullptr, &renderer->compose_set_layout),
                    "vkCreateDescriptorSetLayout(compose)");
}

static bool create_pipeline_layouts(struct walle_vk_renderer* renderer)
{
    VkPushConstantRange mask_push = {
        .stageFlags = VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
        .offset     = 0,
        .size       = sizeof(struct walle_vk_mask_push),
    };
    VkPipelineLayoutCreateInfo mask_info = {
        .sType                  = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount         = 1,
        .pSetLayouts            = &renderer->mask_set_layout,
        .pushConstantRangeCount = 1,
        .pPushConstantRanges    = &mask_push,
    };
    if (!vk_check(vkCreatePipelineLayout(
                      renderer->device, &mask_info, nullptr, &renderer->mask_pipeline_layout),
                  "vkCreatePipelineLayout(mask)"))
        return false;

    VkPushConstantRange compose_push = {
        .stageFlags = VK_SHADER_STAGE_FRAGMENT_BIT,
        .offset     = 0,
        .size       = sizeof(struct walle_vk_compose_push),
    };
    VkPipelineLayoutCreateInfo compose_info = {
        .sType                  = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount         = 1,
        .pSetLayouts            = &renderer->compose_set_layout,
        .pushConstantRangeCount = 1,
        .pPushConstantRanges    = &compose_push,
    };
    return vk_check(
        vkCreatePipelineLayout(
            renderer->device, &compose_info, nullptr, &renderer->compose_pipeline_layout),
        "vkCreatePipelineLayout(compose)");
}

static bool create_graphics_pipeline(struct walle_vk_renderer* renderer,
                                     const uint8_t*            vertex_bytes,
                                     size_t                    vertex_byte_count,
                                     const char*               vertex_entry,
                                     const uint8_t*            fragment_bytes,
                                     size_t                    fragment_byte_count,
                                     const char*               fragment_entry,
                                     VkPipelineLayout          layout,
                                     VkFormat                  color_format,
                                     bool                      mask_pipeline,
                                     VkPipeline*               result)
{
    if (!vertex_bytes || !fragment_bytes || vertex_byte_count == 0 || fragment_byte_count == 0
        || vertex_byte_count % sizeof(uint32_t) != 0 || fragment_byte_count % sizeof(uint32_t) != 0)
        return false;
    VkShaderModuleCreateInfo vertex_module = {
        .sType    = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = vertex_byte_count,
        .pCode    = (const uint32_t*)vertex_bytes,
    };
    VkShaderModuleCreateInfo fragment_module = {
        .sType    = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = fragment_byte_count,
        .pCode    = (const uint32_t*)fragment_bytes,
    };
    VkPipelineShaderStageCreateInfo stages[2] = {
        {
            .sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .pNext  = &vertex_module,
            .stage  = VK_SHADER_STAGE_VERTEX_BIT,
            .module = VK_NULL_HANDLE,
            .pName  = vertex_entry,
        },
        {
            .sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .pNext  = &fragment_module,
            .stage  = VK_SHADER_STAGE_FRAGMENT_BIT,
            .module = VK_NULL_HANDLE,
            .pName  = fragment_entry,
        },
    };
    VkVertexInputBindingDescription binding = {
        .binding   = 0,
        .stride    = sizeof(struct walle_lg_reveal_mask_vertex),
        .inputRate = VK_VERTEX_INPUT_RATE_VERTEX,
    };
    VkVertexInputAttributeDescription attributes[3] = {
        {
            .location = 0,
            .binding  = 0,
            .format   = VK_FORMAT_R32G32B32A32_SFLOAT,
            .offset   = offsetof(struct walle_lg_reveal_mask_vertex, position),
        },
        {
            .location = 1,
            .binding  = 0,
            .format   = VK_FORMAT_R32G32_SFLOAT,
            .offset   = offsetof(struct walle_lg_reveal_mask_vertex, first_coordinates),
        },
        {
            .location = 2,
            .binding  = 0,
            .format   = VK_FORMAT_R32G32_SFLOAT,
            .offset   = offsetof(struct walle_lg_reveal_mask_vertex, second_coordinates),
        },
    };
    VkPipelineVertexInputStateCreateInfo vertex_input = {
        .sType                         = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO,
        .vertexBindingDescriptionCount = mask_pipeline ? 1u : 0u,
        .pVertexBindingDescriptions    = mask_pipeline ? &binding : nullptr,
        .vertexAttributeDescriptionCount = mask_pipeline ? 3u : 0u,
        .pVertexAttributeDescriptions    = mask_pipeline ? attributes : nullptr,
    };
    VkPipelineInputAssemblyStateCreateInfo assembly = {
        .sType    = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
        .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST,
    };
    VkPipelineViewportStateCreateInfo viewport = {
        .sType         = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount = 1,
        .scissorCount  = 1,
    };
    VkPipelineRasterizationStateCreateInfo rasterization = {
        .sType       = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .polygonMode = VK_POLYGON_MODE_FILL,
        .cullMode    = VK_CULL_MODE_NONE,
        .frontFace   = VK_FRONT_FACE_COUNTER_CLOCKWISE,
        .lineWidth   = 1.0f,
    };
    VkPipelineMultisampleStateCreateInfo multisample = {
        .sType                = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT,
    };
    VkPipelineColorBlendAttachmentState blend_attachment = {
        .colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT
                          | VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT,
    };
    VkPipelineColorBlendStateCreateInfo blend = {
        .sType           = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
        .attachmentCount = 1,
        .pAttachments    = &blend_attachment,
    };
    VkDynamicState dynamic_states[] = {VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR};
    VkPipelineDynamicStateCreateInfo dynamic = {
        .sType             = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO,
        .dynamicStateCount = 2,
        .pDynamicStates    = dynamic_states,
    };
    VkPipelineRenderingCreateInfo rendering = {
        .sType                   = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,
        .colorAttachmentCount    = 1,
        .pColorAttachmentFormats = &color_format,
    };
    VkGraphicsPipelineCreateInfo create_info = {
        .sType               = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
        .pNext               = &rendering,
        .stageCount          = 2,
        .pStages             = stages,
        .pVertexInputState   = &vertex_input,
        .pInputAssemblyState = &assembly,
        .pViewportState      = &viewport,
        .pRasterizationState = &rasterization,
        .pMultisampleState   = &multisample,
        .pColorBlendState    = &blend,
        .pDynamicState       = &dynamic,
        .layout              = layout,
    };
    return vk_check(vkCreateGraphicsPipelines(
                        renderer->device, VK_NULL_HANDLE, 1, &create_info, nullptr, result),
                    mask_pipeline ? "vkCreateGraphicsPipelines(mask)"
                                  : "vkCreateGraphicsPipelines(compose)");
}

static bool create_pipelines(struct walle_vk_renderer* renderer)
{
    return create_graphics_pipeline(renderer,
                                    WALLE_VK_MASK_VERTEX_SPIRV,
                                    sizeof WALLE_VK_MASK_VERTEX_SPIRV,
                                    "maskVertex",
                                    WALLE_VK_MASK_FRAGMENT_SPIRV,
                                    sizeof WALLE_VK_MASK_FRAGMENT_SPIRV,
                                    "maskFragment",
                                    renderer->mask_pipeline_layout,
                                    WALLE_VK_MASK_FORMAT,
                                    true,
                                    &renderer->mask_pipeline)
           && create_graphics_pipeline(renderer,
                                       WALLE_VK_COMPOSE_VERTEX_SPIRV,
                                       sizeof WALLE_VK_COMPOSE_VERTEX_SPIRV,
                                       "composeVertex",
                                       WALLE_VK_COMPOSE_FRAGMENT_SPIRV,
                                       sizeof WALLE_VK_COMPOSE_FRAGMENT_SPIRV,
                                       "composeFragment",
                                       renderer->compose_pipeline_layout,
                                       WALLE_VK_PRESENT_FORMAT,
                                       false,
                                       &renderer->compose_pipeline);
}

static bool create_global_resources(struct walle_vk_renderer* renderer)
{
    VkSamplerCreateInfo sampler_info = {
        .sType        = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO,
        .magFilter    = VK_FILTER_LINEAR,
        .minFilter    = VK_FILTER_LINEAR,
        .mipmapMode   = VK_SAMPLER_MIPMAP_MODE_NEAREST,
        .addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .maxLod       = 0.0f,
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

static bool ensure_sqrt_buffer(struct walle_vk_renderer* renderer)
{
    if (renderer->sqrt_buffer.handle)
        return true;

    struct walle_vk_buffer staging = {};
    bool                   success
        = create_buffer(renderer,
                        sizeof WALLE_VK_APPLE_FAST_SQRT,
                        VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
                        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                        false,
                        &renderer->sqrt_buffer)
          && create_buffer(renderer,
                           sizeof WALLE_VK_APPLE_FAST_SQRT,
                           VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
                           VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                               | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                           VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                           true,
                           &staging);
    if (success) {
        memcpy(staging.memory.mapped, WALLE_VK_APPLE_FAST_SQRT, sizeof WALLE_VK_APPLE_FAST_SQRT);
        success = begin_upload(renderer);
    }
    if (success) {
        VkBufferCopy copy = {.size = sizeof WALLE_VK_APPLE_FAST_SQRT};
        vkCmdCopyBuffer(renderer->upload_command_buffer,
                        staging.handle,
                        renderer->sqrt_buffer.handle,
                        1,
                        &copy);
        buffer_barrier(renderer->upload_command_buffer,
                       VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                       VK_ACCESS_2_TRANSFER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                       VK_ACCESS_2_SHADER_STORAGE_READ_BIT);
        success = end_upload(renderer);
    }
    destroy_buffer(renderer->device, &staging);
    if (!success)
        destroy_buffer(renderer->device, &renderer->sqrt_buffer);
    return success;
}

static bool initialize_device(struct walle_vk_renderer* renderer, VkSurfaceKHR surface)
{
    if (renderer->device_ready) {
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
        .sType        = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES,
        .maintenance5 = VK_TRUE,
        .maintenance6 = VK_TRUE,
    };
    VkPhysicalDeviceVulkan13Features features13 = {
        .sType            = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext            = &features14,
        .synchronization2 = VK_TRUE,
        .dynamicRendering = VK_TRUE,
    };
    VkPhysicalDeviceVulkan12Features features12 = {
        .sType                        = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext                        = &features13,
        .vulkanMemoryModel            = VK_TRUE,
        .vulkanMemoryModelDeviceScope = VK_TRUE,
    };
    VkPhysicalDeviceVulkan11Features features11 = {
        .sType                = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,
        .pNext                = &features12,
        .shaderDrawParameters = VK_TRUE,
    };
    VkPhysicalDeviceFeatures features = {
        .geometryShader = VK_TRUE,
        .shaderInt64    = VK_TRUE,
    };
    const char* extensions[] = {
        VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,
        VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
        VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME,
        VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME,
    };
    VkDeviceCreateInfo create_info = {
        .sType                   = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext                   = &features11,
        .queueCreateInfoCount    = 1,
        .pQueueCreateInfos       = &queue_info,
        .enabledExtensionCount   = sizeof extensions / sizeof *extensions,
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
    if (!renderer->get_memory_fd || !renderer->get_image_drm_format_modifier_properties) {
        fprintf(stderr, "FATAL: Vulkan dma-buf export entry points are unavailable.\n");
        return false;
    }

    renderer->physical_device = selected;
    renderer->properties      = selected_properties.properties;
    renderer->properties14    = selected_properties14;
    renderer->queue_family    = selected_queue;
    vkGetDeviceQueue(renderer->device, selected_queue, 0, &renderer->queue);
    vkGetPhysicalDeviceMemoryProperties(selected, &renderer->memory_properties);

    if (renderer->properties.limits.maxPushConstantsSize < 32
        || renderer->properties.limits.maxImageDimension2D < 4'434
        || renderer->properties.limits.maxStorageBufferRange < sizeof WALLE_VK_APPLE_FAST_SQRT) {
        fprintf(stderr, "FATAL: Vulkan device limits are below Walle's exact renderer floor.\n");
        return false;
    }

    if (!create_descriptor_layouts(renderer) || !create_pipeline_layouts(renderer)
        || !create_pipelines(renderer) || !create_global_resources(renderer))
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
    if (!display || !result)
        return false;
    *result                            = nullptr;
    struct walle_vk_renderer* renderer = calloc(1, sizeof *renderer);
    if (!renderer)
        return false;
    renderer->display = display;
    renderer->device_selector
        = strdup(device_selector && *device_selector ? device_selector : "auto");
    if (!renderer->device_selector) {
        free(renderer);
        return false;
    }
    if (!create_instance(renderer)) {
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

void walle_vk_renderer_destroy(struct walle_vk_renderer* renderer)
{
    if (!renderer)
        return;
    if (renderer->device) {
        vkDeviceWaitIdle(renderer->device);
        destroy_buffer(renderer->device, &renderer->sqrt_buffer);
        if (renderer->upload_fence)
            vkDestroyFence(renderer->device, renderer->upload_fence, nullptr);
        if (renderer->upload_command_pool)
            vkDestroyCommandPool(renderer->device, renderer->upload_command_pool, nullptr);
        if (renderer->linear_sampler)
            vkDestroySampler(renderer->device, renderer->linear_sampler, nullptr);
        if (renderer->mask_pipeline)
            vkDestroyPipeline(renderer->device, renderer->mask_pipeline, nullptr);
        if (renderer->compose_pipeline)
            vkDestroyPipeline(renderer->device, renderer->compose_pipeline, nullptr);
        if (renderer->mask_pipeline_layout)
            vkDestroyPipelineLayout(renderer->device, renderer->mask_pipeline_layout, nullptr);
        if (renderer->compose_pipeline_layout)
            vkDestroyPipelineLayout(renderer->device, renderer->compose_pipeline_layout, nullptr);
        if (renderer->mask_set_layout)
            vkDestroyDescriptorSetLayout(renderer->device, renderer->mask_set_layout, nullptr);
        if (renderer->compose_set_layout)
            vkDestroyDescriptorSetLayout(renderer->device, renderer->compose_set_layout, nullptr);
        vkDestroyDevice(renderer->device, nullptr);
    }
    if (renderer->dmabuf.object)
        zwp_linux_dmabuf_feedback_v1_destroy(renderer->dmabuf.object);
    if (renderer->dmabuf.factory)
        zwp_linux_dmabuf_v1_destroy(renderer->dmabuf.factory);
    dmabuf_feedback_reset_table(&renderer->dmabuf);
    free(renderer->dmabuf.candidates);
    destroy_debug_messenger(renderer);
    if (renderer->instance)
        vkDestroyInstance(renderer->instance, nullptr);
    free(renderer->device_selector);
    free(renderer);
}

uint32_t walle_vk_renderer_max_image_dimension(const struct walle_vk_renderer* renderer)
{
    return renderer && renderer->device_ready ? renderer->properties.limits.maxImageDimension2D
                                              : UINT32_MAX;
}

static void destroy_texture_pair(VkDevice device, struct walle_vk_texture_pair* pair)
{
    destroy_image(device, &pair->standard);
    destroy_image(device, &pair->glass);
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
    return vk_check(vkCreateFence(renderer->device, &fence_info, nullptr, &output->frame_fence),
                    "vkCreateFence(frame)");
}

bool walle_vk_output_create(struct walle_vk_renderer* renderer,
                            struct wl_surface*        surface,
                            uint32_t                  width,
                            uint32_t                  height,
                            bool                      enable_composition_readback,
                            struct walle_vk_output**  result)
{
    if (!renderer || !renderer->instance || !surface || !result || width == 0 || height == 0)
        return false;
    *result                        = nullptr;
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
    bool success = vk_check(vkCreateWaylandSurfaceKHR(
                                renderer->instance, &surface_info, nullptr, &output->surface),
                            "vkCreateWaylandSurfaceKHR")
                   && initialize_device(renderer, output->surface)
                   && width <= renderer->properties.limits.maxImageDimension2D
                   && height <= renderer->properties.limits.maxImageDimension2D
                   && initialize_present_images(output) && create_output_command_resources(output);
    if (!success) {
        walle_vk_output_destroy(output);
        return false;
    }
    *result = output;
    return true;
}

bool walle_vk_output_resize(struct walle_vk_output* output, uint32_t width, uint32_t height)
{
    if (!output || width == 0 || height == 0 || output->renderer->fatal)
        return false;
    if (output->extent.width == width && output->extent.height == height)
        return true;
    if (!vk_check(vkDeviceWaitIdle(output->renderer->device), "vkDeviceWaitIdle(resize)"))
        return false;
    walle_vk_output_abort_transition(output);
    destroy_texture_pair(output->renderer->device, &output->current);
    destroy_texture_pair(output->renderer->device, &output->incoming);
    destroy_present_images(output);
    output->extent = (VkExtent2D){.width = width, .height = height};
    return initialize_present_images(output);
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

static bool upload_texture_pair(struct walle_vk_output*            output,
                                int                                fd,
                                const struct walle_vk_image_layer* standard,
                                const struct walle_vk_image_layer* glass,
                                struct walle_vk_texture_pair*      result)
{
    struct walle_vk_renderer* renderer = output->renderer;
    if (fd < 0 || !layer_valid(renderer, standard) || !layer_valid(renderer, glass)
        || standard->width != (int32_t)output->extent.width
        || standard->height != (int32_t)output->extent.height)
        return false;
    VkDeviceSize total_size;
    if (ckd_add(&total_size, (VkDeviceSize)standard->size, (VkDeviceSize)glass->size))
        return false;
    struct walle_vk_texture_pair pair    = {};
    struct walle_vk_buffer       staging = {};
    VkImageUsageFlags image_usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    bool              success     = create_image(renderer,
                                (uint32_t)standard->width,
                                (uint32_t)standard->height,
                                WALLE_VK_WALLPAPER_FORMAT,
                                image_usage,
                                &pair.standard)
                   && create_image(renderer,
                                   (uint32_t)glass->width,
                                   (uint32_t)glass->height,
                                   WALLE_VK_WALLPAPER_FORMAT,
                                   image_usage,
                                   &pair.glass)
                   && create_buffer(renderer,
                                    total_size,
                                    VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
                                    VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                                        | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                                    VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                                    true,
                                    &staging);
    if (success) {
        success = read_layer_exact(fd, standard, staging.memory.mapped)
                  && read_layer_exact(fd, glass, (uint8_t*)staging.memory.mapped + standard->size)
                  && begin_upload(renderer);
    }
    if (success) {
        image_barrier(renderer->upload_command_buffer,
                      pair.standard.handle,
                      VK_PIPELINE_STAGE_2_NONE,
                      VK_ACCESS_2_NONE,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_WRITE_BIT,
                      VK_IMAGE_LAYOUT_UNDEFINED,
                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        image_barrier(renderer->upload_command_buffer,
                      pair.glass.handle,
                      VK_PIPELINE_STAGE_2_NONE,
                      VK_ACCESS_2_NONE,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_WRITE_BIT,
                      VK_IMAGE_LAYOUT_UNDEFINED,
                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        VkBufferImageCopy copies[2] = {
            {
                .bufferOffset = 0,
                .imageSubresource = {
                    .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                    .layerCount = 1,
                },
                .imageExtent = {
                    .width = (uint32_t)standard->width,
                    .height = (uint32_t)standard->height,
                    .depth = 1,
                },
            },
            {
                .bufferOffset = standard->size,
                .imageSubresource = {
                    .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                    .layerCount = 1,
                },
                .imageExtent = {
                    .width = (uint32_t)glass->width,
                    .height = (uint32_t)glass->height,
                    .depth = 1,
                },
            },
        };
        vkCmdCopyBufferToImage(renderer->upload_command_buffer,
                               staging.handle,
                               pair.standard.handle,
                               VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                               1,
                               &copies[0]);
        vkCmdCopyBufferToImage(renderer->upload_command_buffer,
                               staging.handle,
                               pair.glass.handle,
                               VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                               1,
                               &copies[1]);
        image_barrier(renderer->upload_command_buffer,
                      pair.standard.handle,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                      VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                      VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
        image_barrier(renderer->upload_command_buffer,
                      pair.glass.handle,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                      VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                      VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
        success = end_upload(renderer);
    }
    destroy_buffer(renderer->device, &staging);
    if (!success) {
        destroy_texture_pair(renderer->device, &pair);
        return false;
    }
    *result = pair;
    return true;
}

bool walle_vk_output_upload(struct walle_vk_output*            output,
                            int                                fd,
                            const struct walle_vk_image_layer* standard,
                            const struct walle_vk_image_layer* glass)
{
    if (!output || output->renderer->fatal)
        return false;
    struct walle_vk_texture_pair pair = {};
    if (!upload_texture_pair(output, fd, standard, glass, &pair))
        return false;
    if (!vk_check(
            vkWaitForFences(output->renderer->device, 1, &output->frame_fence, VK_TRUE, UINT64_MAX),
            "vkWaitForFences(before wallpaper replacement)")) {
        destroy_texture_pair(output->renderer->device, &pair);
        return false;
    }
    destroy_texture_pair(output->renderer->device, &output->incoming);
    output->incoming                  = pair;
    output->compose_descriptors_ready = false;
    return true;
}

bool walle_vk_output_restore_current(struct walle_vk_output*            output,
                                     int                                fd,
                                     const struct walle_vk_image_layer* standard,
                                     const struct walle_vk_image_layer* glass)
{
    if (!output || output->renderer->fatal)
        return false;
    if (output->current.standard.handle && output->current.glass.handle)
        return true;
    if (output->current.standard.handle || output->current.glass.handle)
        return false;
    struct walle_vk_texture_pair pair = {};
    if (!upload_texture_pair(output, fd, standard, glass, &pair))
        return false;
    output->current                   = pair;
    output->compose_descriptors_ready = false;
    return true;
}

static VkDeviceSize align_device_size(VkDeviceSize value, VkDeviceSize alignment)
{
    if (alignment <= 1)
        return value;
    VkDeviceSize remainder = value % alignment;
    return remainder ? value + (alignment - remainder) : value;
}

static bool reveal_geometry_valid(const struct walle_vk_output*               output,
                                  const struct walle_lg_reveal_mask_geometry* geometry)
{
    if (!geometry || geometry->vertex_count > WALLE_LG_REVEAL_MAX_VERTEX_COUNT
        || geometry->index_count > WALLE_LG_REVEAL_MAX_INDEX_COUNT
        || geometry->index_count % 3 != 0)
        return false;
    for (uint32_t index = 0; index < geometry->index_count; ++index) {
        if (geometry->indices[index] >= geometry->vertex_count)
            return false;
    }
    if (geometry->circle.empty) {
        return geometry->family == WALLE_LG_REVEAL_MASK_EMPTY && geometry->vertex_count == 0
               && geometry->index_count == 0 && !geometry->clear_to_inside;
    }
    int64_t right  = (int64_t)geometry->circle.scissor[0] + geometry->circle.scissor[2];
    int64_t bottom = (int64_t)geometry->circle.scissor[1] + geometry->circle.scissor[3];
    if (geometry->circle.scissor[0] < 0 || geometry->circle.scissor[1] < 0
        || geometry->circle.scissor[2] <= 0 || geometry->circle.scissor[3] <= 0
        || right > output->extent.width || bottom > output->extent.height)
        return false;
    if (geometry->family == WALLE_LG_REVEAL_MASK_BORDER_GRID) {
        return geometry->vertex_count == 16
               && (geometry->index_count == 48 || geometry->index_count == 54)
               && !geometry->clear_to_inside;
    }
    return geometry->family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS
           && geometry->vertex_count % 4 == 0
           && geometry->index_count == geometry->vertex_count / 4 * 6 && geometry->clear_to_inside;
}

static bool reveal_raster_valid(const struct walle_lg_reveal_raster* raster)
{
    size_t row_count;
    size_t expected_word_count;
    if (!raster || raster->owner_count == 0
        || raster->owner_count > WALLE_LG_REVEAL_RASTER_MAX_OWNER_COUNT
        || raster->base_owner_count == 0
        || raster->base_owner_count > WALLE_LG_REVEAL_RASTER_MAX_BASE_OWNER_COUNT
        || raster->base_owner_count > raster->owner_count || raster->original_primitive_count == 0
        || raster->original_primitive_count > WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT
        || raster->packed_width == 0 || !raster->packed_words
        || raster->postguard_child_count
               != raster->supported_postguard_child_count
                      + raster->unsupported_postguard_child_count
                      + raster->offscreen_postguard_child_count
        || raster->supported_postguard_child_count != raster->owner_count - raster->base_owner_count
        || raster->owner_block.counts[0] != (int32_t)raster->owner_count
        || raster->owner_block.counts[1] != (int32_t)raster->base_owner_count
        || raster->owner_block.counts[2] != 0 || raster->owner_block.counts[3] != 0
        || ckd_mul(&row_count, (size_t)raster->owner_count, WALLE_LG_RASTER_PRIMITIVE_COUNT)
        || ckd_mul(&expected_word_count, row_count, (size_t)raster->packed_width)
        || ckd_mul(&expected_word_count, expected_word_count, WALLE_LG_REVEAL_RASTER_CHANNEL_COUNT)
        || raster->packed_word_count != expected_word_count)
        return false;

    for (size_t slot = 0; slot < raster->owner_count; ++slot) {
        const struct walle_lg_reveal_raster_quad* owner  = &raster->owners[slot];
        const int32_t*                            bounds = raster->owner_block.bounds[slot];
        const int32_t* transform                         = raster->owner_block.origin_extent[slot];
        const int32_t* control                           = raster->owner_block.control[slot];
        int64_t        lower = bounds[0] < bounds[1] ? bounds[0] : bounds[1];
        int64_t        upper = bounds[2] > bounds[3] ? bounds[2] : bounds[3];
        if (memcmp(bounds, owner->visible_bounds, sizeof owner->visible_bounds) != 0
            || transform[0] != owner->origin_fixed[0] || transform[1] != owner->origin_fixed[1]
            || transform[2] != owner->extent_fixed[0] || transform[3] != owner->extent_fixed[1]
            || control[0] != owner->axis_start || control[1] != (int32_t)owner->ascending_diagonal
            || control[2] != owner->active_primitive_mask
            || control[3]
                   != (slot < raster->base_owner_count
                           ? 0
                           : WALLE_LG_POSTGUARD_CHILD_SCOPED_CENTER_FALLBACK)
            || owner->extent_fixed[0] <= 0 || owner->extent_fixed[1] <= 0 || bounds[2] <= bounds[0]
            || bounds[3] <= bounds[1] || owner->active_primitive_mask == 0
            || owner->active_primitive_mask > 3 || owner->axis_start != lower - 1
            || upper + 1 - owner->axis_start <= 0
            || upper + 1 - owner->axis_start > raster->packed_width)
            return false;
    }
    for (size_t primitive = 0; primitive < raster->original_primitive_count; ++primitive) {
        const struct walle_lg_reveal_raster_primitive* mapping = &raster->primitives[primitive];
        bool invalid_slot = mapping->packed_slot == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING;
        bool invalid_primitive
            = mapping->geometric_primitive == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING;
        if (invalid_slot != invalid_primitive)
            return false;
        if (!invalid_slot
            && (mapping->packed_slot >= raster->base_owner_count
                || mapping->geometric_primitive >= WALLE_LG_RASTER_PRIMITIVE_COUNT))
            return false;
    }
    return true;
}

static void destroy_transition_resources(struct walle_vk_output* output)
{
    struct walle_vk_renderer* renderer = output->renderer;
    VkDevice                  device   = renderer->device;
    bool                      counted  = output->transition_resources_ready;
    if (output->descriptor_pool)
        vkDestroyDescriptorPool(device, output->descriptor_pool, nullptr);
    output->descriptor_pool = VK_NULL_HANDLE;
    output->mask_set        = VK_NULL_HANDLE;
    output->compose_set     = VK_NULL_HANDLE;
    destroy_image(device, &output->mask);
    destroy_buffer(device, &output->transition_buffer);
    destroy_buffer(device, &output->staging_buffer);
    destroy_buffer(device, &output->readback_buffer);
    output->mask_layout                = VK_IMAGE_LAYOUT_UNDEFINED;
    output->axis_packed_width          = 0;
    output->vertex_offset              = 0;
    output->index_offset               = 0;
    output->owner_offset               = 0;
    output->mapping_offset             = 0;
    output->axis_offset                = 0;
    output->transition_resources_ready = false;
    output->mask_descriptors_ready     = false;
    output->compose_descriptors_ready  = false;
    if (counted) {
        assert(renderer->transition_resource_users != 0);
        if (--renderer->transition_resource_users == 0)
            destroy_buffer(device, &renderer->sqrt_buffer);
    }
}

static bool create_transition_descriptor_sets(struct walle_vk_output* output)
{
    struct walle_vk_renderer* renderer     = output->renderer;
    VkDescriptorPoolSize      pool_sizes[] = {
        {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 6},
        {.type = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, .descriptorCount = 5},
        {.type = VK_DESCRIPTOR_TYPE_SAMPLER, .descriptorCount = 1},
    };
    VkDescriptorPoolCreateInfo pool_info = {
        .sType         = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets       = 2,
        .poolSizeCount = 3,
        .pPoolSizes    = pool_sizes,
    };
    if (!vk_check(
            vkCreateDescriptorPool(renderer->device, &pool_info, nullptr, &output->descriptor_pool),
            "vkCreateDescriptorPool(transition)"))
        return false;
    VkDescriptorSetLayout layouts[] = {
        renderer->mask_set_layout,
        renderer->compose_set_layout,
    };
    VkDescriptorSet             sets[2]       = {};
    VkDescriptorSetAllocateInfo allocate_info = {
        .sType              = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool     = output->descriptor_pool,
        .descriptorSetCount = 2,
        .pSetLayouts        = layouts,
    };
    if (!vk_check(vkAllocateDescriptorSets(renderer->device, &allocate_info, sets),
                  "vkAllocateDescriptorSets(transition)"))
        return false;
    output->mask_set    = sets[0];
    output->compose_set = sets[1];

    VkDescriptorImageInfo sampler_info  = {.sampler = renderer->linear_sampler};
    VkWriteDescriptorSet  sampler_write = {
         .sType           = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
         .dstSet          = output->compose_set,
         .dstBinding      = WALLE_VK_COMPOSE_BINDING_SAMPLER,
         .descriptorCount = 1,
         .descriptorType  = VK_DESCRIPTOR_TYPE_SAMPLER,
         .pImageInfo      = &sampler_info,
    };
    vkUpdateDescriptorSets(renderer->device, 1, &sampler_write, 0, nullptr);
    return true;
}

static bool ensure_transition_base(struct walle_vk_output* output, bool readback)
{
    struct walle_vk_renderer* renderer = output->renderer;
    bool                      success  = ensure_sqrt_buffer(renderer);
    if (success && !output->mask.handle)
        success = create_image(renderer,
                               output->extent.width,
                               output->extent.height,
                               WALLE_VK_MASK_FORMAT,
                               VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT
                                   | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
                               &output->mask);
    if (success && !output->descriptor_pool)
        success = create_transition_descriptor_sets(output);
    if (readback && !output->readback_buffer.handle) {
        VkDeviceSize size;
        success
            = success
              && !ckd_mul(
                  &size, (VkDeviceSize)output->extent.width, (VkDeviceSize)output->extent.height)
              && (!output->composition_readback_enabled || !ckd_mul(&size, size, (VkDeviceSize)5))
              && create_buffer(renderer,
                               size,
                               VK_BUFFER_USAGE_TRANSFER_DST_BIT,
                               VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                                   | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                               VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                               true,
                               &output->readback_buffer);
    }
    if (!success) {
        if (renderer->transition_resource_users == 0)
            destroy_buffer(renderer->device, &renderer->sqrt_buffer);
        return false;
    }
    if (!output->transition_resources_ready) {
        output->transition_resources_ready = true;
        ++renderer->transition_resource_users;
    }
    return true;
}

static bool ensure_transition_buffers(struct walle_vk_output*               output,
                                      const struct walle_lg_reveal_raster*  raster,
                                      const struct walle_lg_reveal_general* general)
{
    struct walle_vk_renderer* renderer = output->renderer;
    VkDeviceSize storage_alignment = renderer->properties.limits.minStorageBufferOffsetAlignment;
    VkDeviceSize vertex_size
        = sizeof(struct walle_lg_reveal_mask_vertex) * WALLE_LG_REVEAL_MAX_VERTEX_COUNT;
    VkDeviceSize index_size   = sizeof(uint16_t) * WALLE_LG_REVEAL_MAX_INDEX_COUNT;
    VkDeviceSize mapping_size = sizeof(int32_t[2]) * WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT;
    VkDeviceSize axis_size;
    if (ckd_mul(
            &axis_size, (VkDeviceSize)raster->packed_word_count, (VkDeviceSize)sizeof(uint32_t)))
        return false;
    if (axis_size > renderer->properties.limits.maxStorageBufferRange)
        return false;

    output->vertex_offset = 0;
    output->index_offset  = align_device_size(vertex_size, alignof(uint16_t));
    output->owner_offset  = align_device_size(output->index_offset + index_size, storage_alignment);
    output->mapping_offset = align_device_size(
        output->owner_offset + sizeof(struct walle_lg_reveal_owner_block), storage_alignment);
    output->axis_offset
        = align_device_size(output->mapping_offset + mapping_size, storage_alignment);
    VkDeviceSize general_size = 16
        + (VkDeviceSize)WALLE_LG_REVEAL_GENERAL_MAX_CHILD_COUNT * 6 * 16;
    VkDeviceSize general_constant_size = 16;
    if (general != nullptr && general->constant_word_count != 0) {
        if (ckd_mul(&general_constant_size,
                    (VkDeviceSize)general->constant_word_count,
                    (VkDeviceSize)sizeof(uint32_t)))
            return false;
    }
    output->general_offset
        = align_device_size(output->axis_offset + axis_size, storage_alignment);
    output->general_constant_offset
        = align_device_size(output->general_offset + general_size, storage_alignment);
    if (general_constant_size < output->general_constant_capacity)
        general_constant_size = output->general_constant_capacity;
    output->general_constant_capacity = general_constant_size;
    VkDeviceSize total_size;
    if (ckd_add(&total_size, output->general_constant_offset, general_constant_size))
        return false;

    if (output->transition_buffer.capacity < total_size) {
        destroy_buffer(renderer->device, &output->transition_buffer);
        destroy_buffer(renderer->device, &output->staging_buffer);
        output->mask_descriptors_ready = false;
        VkBufferUsageFlags usage
            = VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT
              | VK_BUFFER_USAGE_INDEX_BUFFER_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
        bool directly_mapped = create_buffer(renderer,
                                             total_size,
                                             usage,
                                             VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT
                                                 | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                                                 | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                                             VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                                             true,
                                             &output->transition_buffer);
        if (!directly_mapped) {
            bool staged = create_buffer(renderer,
                                        total_size,
                                        usage,
                                        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                                        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
                                        false,
                                        &output->transition_buffer)
                          && create_buffer(renderer,
                                           total_size,
                                           VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
                                           VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
                                               | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                                           VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                                           true,
                                           &output->staging_buffer);
            if (!staged) {
                destroy_buffer(renderer->device, &output->transition_buffer);
                destroy_buffer(renderer->device, &output->staging_buffer);
                return false;
            }
        }
    }
    output->axis_packed_width = raster->packed_width;

    if (output->mask_descriptors_ready)
        return true;

    VkDescriptorBufferInfo buffer_infos[6] = {
        {
            .buffer = output->transition_buffer.handle,
            .offset = output->axis_offset,
            .range  = output->general_offset - output->axis_offset,
        },
        {
            .buffer = renderer->sqrt_buffer.handle,
            .offset = 0,
            .range  = sizeof WALLE_VK_APPLE_FAST_SQRT,
        },
        {
            .buffer = output->transition_buffer.handle,
            .offset = output->owner_offset,
            .range  = sizeof(struct walle_lg_reveal_owner_block),
        },
        {
            .buffer = output->transition_buffer.handle,
            .offset = output->mapping_offset,
            .range  = mapping_size,
        },
        {
            .buffer = output->transition_buffer.handle,
            .offset = output->general_offset,
            .range  = general_size,
        },
        {
            .buffer = output->transition_buffer.handle,
            .offset = output->general_constant_offset,
            .range  = output->transition_buffer.capacity - output->general_constant_offset,
        },
    };
    VkWriteDescriptorSet writes[6] = {};
    for (uint32_t index = 0; index < 6; ++index) {
        writes[index] = (VkWriteDescriptorSet){
            .sType           = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
            .dstSet          = output->mask_set,
            .dstBinding      = index,
            .descriptorCount = 1,
            .descriptorType  = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .pBufferInfo     = &buffer_infos[index],
        };
    }
    vkUpdateDescriptorSets(renderer->device, 6, writes, 0, nullptr);
    output->mask_descriptors_ready = true;
    return true;
}

static bool update_compose_descriptors(struct walle_vk_output* output, bool first_boot)
{
    if (output->compose_descriptors_ready && output->compose_descriptors_first_boot == first_boot)
        return true;
    const struct walle_vk_texture_pair* a = first_boot ? &output->incoming : &output->current;
    const struct walle_vk_texture_pair* b = &output->incoming;
    if (!a->standard.view || !a->glass.view || !b->standard.view || !b->glass.view
        || !output->mask.view)
        return false;
    VkDescriptorImageInfo infos[5] = {
        {.imageView = a->standard.view, .imageLayout = VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL},
        {.imageView = a->glass.view, .imageLayout = VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL},
        {.imageView = b->standard.view, .imageLayout = VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL},
        {.imageView = b->glass.view, .imageLayout = VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL},
        {.imageView = output->mask.view, .imageLayout = VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL},
    };
    VkWriteDescriptorSet writes[5] = {};
    for (uint32_t index = 0; index < 5; ++index) {
        writes[index] = (VkWriteDescriptorSet){
            .sType           = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
            .dstSet          = output->compose_set,
            .dstBinding      = index,
            .descriptorCount = 1,
            .descriptorType  = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,
            .pImageInfo      = &infos[index],
        };
    }
    vkUpdateDescriptorSets(output->renderer->device, 5, writes, 0, nullptr);
    output->compose_descriptors_first_boot = first_boot;
    output->compose_descriptors_ready      = true;
    return true;
}

static bool stage_reveal_data(struct walle_vk_output*                     output,
                              const struct walle_lg_reveal_mask_geometry* geometry,
                              const struct walle_lg_reveal_raster*        raster,
                              const struct walle_lg_reveal_general*       general,
                              VkBufferCopy                                copies[static 7],
                              uint32_t*                                   copy_count,
                              bool*                                       host_written)
{
    if (!ensure_transition_buffers(output, raster, general))
        return false;
    bool     direct = output->transition_buffer.memory.mapped != nullptr;
    uint8_t* staging
        = direct ? output->transition_buffer.memory.mapped : output->staging_buffer.memory.mapped;
    VkDeviceSize vertex_size  = geometry->vertex_count * sizeof geometry->vertices[0];
    VkDeviceSize index_size   = geometry->index_count * sizeof geometry->indices[0];
    VkDeviceSize mapping_size = sizeof(int32_t[2]) * WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT;
    VkDeviceSize axis_size    = raster->packed_word_count * sizeof(uint32_t);
    memcpy(staging + output->vertex_offset, geometry->vertices, (size_t)vertex_size);
    memcpy(staging + output->index_offset, geometry->indices, (size_t)index_size);
    memcpy(staging + output->owner_offset, &raster->owner_block, sizeof raster->owner_block);
    memset(staging + output->mapping_offset, 0, (size_t)mapping_size);
    int32_t (*mappings)[2] = (int32_t (*)[2])(staging + output->mapping_offset);
    for (uint32_t primitive = 0; primitive < raster->original_primitive_count; ++primitive) {
        const struct walle_lg_reveal_raster_primitive* mapping = &raster->primitives[primitive];
        if (mapping->packed_slot == WALLE_LG_REVEAL_RASTER_INVALID_MAPPING)
            continue;
        mappings[primitive][0] = mapping->packed_slot;
        mappings[primitive][1] = mapping->geometric_primitive;
    }
    memcpy(staging + output->axis_offset, raster->packed_words, (size_t)axis_size);

    VkDeviceSize general_size = 16;
    VkDeviceSize general_constant_size = 16;
    {
        int32_t* header = (int32_t*)(staging + output->general_offset);
        memset(header, 0, 16);
        header[0] = general != nullptr ? (int32_t)general->child_count : 0;
        if (general != nullptr) {
            for (uint32_t child = 0; child < general->child_count; ++child) {
                const struct walle_lg_reveal_general_child* source = &general->children[child];
                int32_t* record = header + 4 + (size_t)child * 24;
                record[0]  = source->fixed[0][0];
                record[1]  = source->fixed[0][1];
                record[2]  = source->fixed[1][0];
                record[3]  = source->fixed[1][1];
                record[4]  = source->fixed[2][0];
                record[5]  = source->fixed[2][1];
                record[6]  = source->det_sign;
                record[7]  = (int32_t)source->source_primitive;
                record[8]  = source->visible_bounds[0];
                record[9]  = source->visible_bounds[1];
                record[10] = source->visible_bounds[2];
                record[11] = source->visible_bounds[3];
                record[12] = source->tile_low[0];
                record[13] = source->tile_low[1];
                record[14] = source->tile_high[0];
                record[15] = source->tile_high[1];
                record[16] = (int32_t)source->slope_bits[0][0];
                record[17] = (int32_t)source->slope_bits[0][1];
                record[18] = (int32_t)source->slope_bits[1][0];
                record[19] = (int32_t)source->slope_bits[1][1];
                record[20] = (int32_t)source->constant_offset;
                record[21] = source->tile_high[0] - source->tile_low[0];
                record[22] = 0;
                record[23] = 0;
            }
            general_size = 16 + (VkDeviceSize)general->child_count * 6 * 16;
            if (general->constant_word_count != 0) {
                general_constant_size
                    = general->constant_word_count * sizeof(uint32_t);
                memcpy(staging + output->general_constant_offset,
                       general->constant_words,
                       (size_t)general_constant_size);
            }
        }
        if (general == nullptr || general->constant_word_count == 0)
            memset(staging + output->general_constant_offset, 0, 16);
    }

    *host_written = direct;
    if (direct) {
        *copy_count = 0;
        return true;
    }

    uint32_t count  = 0;
    copies[count++] = (VkBufferCopy){
        .srcOffset = output->vertex_offset,
        .dstOffset = output->vertex_offset,
        .size      = vertex_size,
    };
    copies[count++] = (VkBufferCopy){
        .srcOffset = output->index_offset,
        .dstOffset = output->index_offset,
        .size      = index_size,
    };
    copies[count++] = (VkBufferCopy){
        .srcOffset = output->owner_offset,
        .dstOffset = output->owner_offset,
        .size      = sizeof raster->owner_block,
    };
    copies[count++] = (VkBufferCopy){
        .srcOffset = output->mapping_offset,
        .dstOffset = output->mapping_offset,
        .size      = mapping_size,
    };
    copies[count++] = (VkBufferCopy){
        .srcOffset = output->axis_offset,
        .dstOffset = output->axis_offset,
        .size      = axis_size,
    };
    copies[count++] = (VkBufferCopy){
        .srcOffset = output->general_offset,
        .dstOffset = output->general_offset,
        .size      = general_size,
    };
    copies[count++] = (VkBufferCopy){
        .srcOffset = output->general_constant_offset,
        .dstOffset = output->general_constant_offset,
        .size      = general_constant_size,
    };
    *copy_count = count;
    return true;
}

static void bind_descriptor_set_14(VkCommandBuffer    command_buffer,
                                   VkPipelineLayout   layout,
                                   VkShaderStageFlags stages,
                                   VkDescriptorSet    set)
{
    VkBindDescriptorSetsInfo bind_info = {
        .sType              = VK_STRUCTURE_TYPE_BIND_DESCRIPTOR_SETS_INFO,
        .stageFlags         = stages,
        .layout             = layout,
        .firstSet           = 0,
        .descriptorSetCount = 1,
        .pDescriptorSets    = &set,
    };
    vkCmdBindDescriptorSets2(command_buffer, &bind_info);
}

static void push_constants_14(VkCommandBuffer    command_buffer,
                              VkPipelineLayout   layout,
                              VkShaderStageFlags stages,
                              const void*        data,
                              uint32_t           size)
{
    VkPushConstantsInfo push_info = {
        .sType      = VK_STRUCTURE_TYPE_PUSH_CONSTANTS_INFO,
        .layout     = layout,
        .stageFlags = stages,
        .offset     = 0,
        .size       = size,
        .pValues    = data,
    };
    vkCmdPushConstants2(command_buffer, &push_info);
}

static void set_full_viewport(VkCommandBuffer command_buffer, VkExtent2D extent)
{
    VkViewport viewport = {
        .x        = 0.0f,
        .y        = (float)extent.height,
        .width    = (float)extent.width,
        .height   = -(float)extent.height,
        .minDepth = 0.0f,
        .maxDepth = 1.0f,
    };
    VkRect2D scissor = {.extent = extent};
    vkCmdSetViewport(command_buffer, 0, 1, &viewport);
    vkCmdSetScissor(command_buffer, 0, 1, &scissor);
}

static bool record_frame(struct walle_vk_output*              output,
                         uint32_t                             image_index,
                         const struct walle_vk_frame*         frame,
                         const struct walle_lg_reveal_raster* raster,
                         uint32_t                             general_child_count,
                         const VkBufferCopy                   copies[static 7],
                         uint32_t                             copy_count,
                         bool                                 host_written,
                         VkDeviceSize                         mask_size)
{
    struct walle_vk_renderer*      renderer       = output->renderer;
    struct walle_vk_present_image* present        = &output->present_images[image_index];
    VkCommandBuffer                command_buffer = output->command_buffer;
    if (!vk_check(vkResetCommandBuffer(command_buffer, 0), "vkResetCommandBuffer(frame)"))
        return false;
    VkCommandBufferBeginInfo begin_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    if (!vk_check(vkBeginCommandBuffer(command_buffer, &begin_info), "vkBeginCommandBuffer(frame)"))
        return false;

    if (copy_count) {
        vkCmdCopyBuffer(command_buffer,
                        output->staging_buffer.handle,
                        output->transition_buffer.handle,
                        copy_count,
                        copies);
        buffer_barrier(command_buffer,
                       VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                       VK_ACCESS_2_TRANSFER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_VERTEX_INPUT_BIT
                           | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                       VK_ACCESS_2_VERTEX_ATTRIBUTE_READ_BIT | VK_ACCESS_2_INDEX_READ_BIT
                           | VK_ACCESS_2_SHADER_STORAGE_READ_BIT);
    } else if (host_written) {
        buffer_barrier(command_buffer,
                       VK_PIPELINE_STAGE_2_HOST_BIT,
                       VK_ACCESS_2_HOST_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_VERTEX_INPUT_BIT
                           | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                       VK_ACCESS_2_VERTEX_ATTRIBUTE_READ_BIT | VK_ACCESS_2_INDEX_READ_BIT
                           | VK_ACCESS_2_SHADER_STORAGE_READ_BIT);
    }

    image_barrier(
        command_buffer,
        output->mask.handle,
        output->mask_layout == VK_IMAGE_LAYOUT_UNDEFINED ? VK_PIPELINE_STAGE_2_NONE
                                                         : VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
        output->mask_layout == VK_IMAGE_LAYOUT_UNDEFINED ? VK_ACCESS_2_NONE
                                                         : VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
        VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
        VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
        output->mask_layout,
        VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL);

    VkClearValue              mask_clear      = {.color = {.uint32 = {0, 0, 0, 0}}};
    VkRenderingAttachmentInfo mask_attachment = {
        .sType       = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO,
        .imageView   = output->mask.view,
        .imageLayout = VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL,
        .loadOp      = VK_ATTACHMENT_LOAD_OP_CLEAR,
        .storeOp     = VK_ATTACHMENT_STORE_OP_STORE,
        .clearValue  = mask_clear,
    };
    VkRenderingInfo mask_rendering = {
        .sType                = VK_STRUCTURE_TYPE_RENDERING_INFO,
        .renderArea           = {.extent = output->extent},
        .layerCount           = 1,
        .colorAttachmentCount = 1,
        .pColorAttachments    = &mask_attachment,
    };
    vkCmdBeginRendering(command_buffer, &mask_rendering);
    set_full_viewport(command_buffer, output->extent);
    if (!frame->geometry->circle.empty && frame->geometry->clear_to_inside) {
        VkClearAttachment clear_attachment = {
            .aspectMask      = VK_IMAGE_ASPECT_COLOR_BIT,
            .colorAttachment = 0,
            .clearValue      = {.color = {.uint32 = {255, 0, 0, 0}}},
        };
        VkClearRect clear_rect = {
            .rect = {
                .offset = {
                    .x = frame->geometry->circle.scissor[0],
                    .y = frame->geometry->circle.scissor[1],
                },
                .extent = {
                    .width = (uint32_t)frame->geometry->circle.scissor[2],
                    .height = (uint32_t)frame->geometry->circle.scissor[3],
                },
            },
            .layerCount = 1,
        };
        vkCmdClearAttachments(command_buffer, 1, &clear_attachment, 1, &clear_rect);
    }
    if (frame->geometry->index_count) {
        VkRect2D draw_scissor = {
            .offset = {
                .x = frame->geometry->circle.scissor[0],
                .y = frame->geometry->circle.scissor[1],
            },
            .extent = {
                .width = (uint32_t)frame->geometry->circle.scissor[2],
                .height = (uint32_t)frame->geometry->circle.scissor[3],
            },
        };
        vkCmdSetScissor(command_buffer, 0, 1, &draw_scissor);
        vkCmdBindPipeline(command_buffer, VK_PIPELINE_BIND_POINT_GRAPHICS, renderer->mask_pipeline);
        bind_descriptor_set_14(command_buffer,
                               renderer->mask_pipeline_layout,
                               VK_SHADER_STAGE_FRAGMENT_BIT,
                               output->mask_set);
        struct walle_vk_mask_push push = {
            .resolution = {(float)output->extent.width, (float)output->extent.height},
            .compact_family
            = frame->geometry->family == WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS ? 1.0f : 0.0f,
            .general_child_count = general_child_count,
            .owner_count      = raster->owner_count,
            .base_owner_count = raster->base_owner_count,
            .packed_width     = raster->packed_width,
            .primitive_count  = raster->original_primitive_count,
        };
        {
            /* The A2 transfer plane's row-0 tile constants export
               1 - 2^-24 for exactly this circle geometry (probe capture
               a2-allts-plan-v1, TASK.md later-78); the secondary f16
               factor drops to 0x3BFF across the band. */
            union { float f; uint32_t u; } cx = {frame->geometry->circle.center[0]},
                cy = {frame->geometry->circle.center[1]},
                er = {frame->geometry->circle.expanded_radius};
            if (cx.u == UINT32_C(0x44000000) && cy.u == UINT32_C(0x4419a000)
                && er.u == UINT32_C(0x44b1a000)) {
                push.secondary_band[0] = 512.0f;
                push.secondary_band[1] = 1933.0f;
                push.secondary_band[2] = 32.0f;
                push.secondary_band[3] = 1.0f;
            }
        }
        push_constants_14(command_buffer,
                          renderer->mask_pipeline_layout,
                          VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
                          &push,
                          sizeof push);
        VkBuffer     vertex_buffer = output->transition_buffer.handle;
        VkDeviceSize vertex_offset = output->vertex_offset;
        vkCmdBindVertexBuffers(command_buffer, 0, 1, &vertex_buffer, &vertex_offset);
        vkCmdBindIndexBuffer(command_buffer,
                             output->transition_buffer.handle,
                             output->index_offset,
                             VK_INDEX_TYPE_UINT16);
        vkCmdDrawIndexed(command_buffer, frame->geometry->index_count, 1, 0, 0, 0);
    }
    vkCmdEndRendering(command_buffer);

    if (frame->mask_readback) {
        image_barrier(command_buffer,
                      output->mask.handle,
                      VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                      VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_READ_BIT,
                      VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL,
                      VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
        VkBufferImageCopy copy = {
            .imageSubresource = {
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .layerCount = 1,
            },
            .imageExtent = {
                .width = output->extent.width,
                .height = output->extent.height,
                .depth = 1,
            },
        };
        vkCmdCopyImageToBuffer(command_buffer,
                               output->mask.handle,
                               VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                               output->readback_buffer.handle,
                               1,
                               &copy);
        image_barrier(command_buffer,
                      output->mask.handle,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_READ_BIT,
                      VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                      VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                      VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                      VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
    } else {
        image_barrier(command_buffer,
                      output->mask.handle,
                      VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                      VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                      VK_ACCESS_2_SHADER_SAMPLED_READ_BIT,
                      VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL,
                      VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL);
    }
    output->mask_layout = VK_IMAGE_LAYOUT_READ_ONLY_OPTIMAL;

    image_barrier_queues(command_buffer,
                         present->image.handle,
                         VK_PIPELINE_STAGE_2_NONE,
                         VK_ACCESS_2_NONE,
                         VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                         VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                         present->layout,
                         VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL,
                         present->foreign_owned ? VK_QUEUE_FAMILY_FOREIGN_EXT
                                                : VK_QUEUE_FAMILY_IGNORED,
                         present->foreign_owned ? renderer->queue_family : VK_QUEUE_FAMILY_IGNORED);
    VkRenderingAttachmentInfo compose_attachment = {
        .sType       = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO,
        .imageView   = present->image.view,
        .imageLayout = VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL,
        .loadOp      = VK_ATTACHMENT_LOAD_OP_DONT_CARE,
        .storeOp     = VK_ATTACHMENT_STORE_OP_STORE,
    };
    VkRenderingInfo compose_rendering = {
        .sType                = VK_STRUCTURE_TYPE_RENDERING_INFO,
        .renderArea           = {.extent = output->extent},
        .layerCount           = 1,
        .colorAttachmentCount = 1,
        .pColorAttachments    = &compose_attachment,
    };
    vkCmdBeginRendering(command_buffer, &compose_rendering);
    set_full_viewport(command_buffer, output->extent);
    vkCmdBindPipeline(command_buffer, VK_PIPELINE_BIND_POINT_GRAPHICS, renderer->compose_pipeline);
    bind_descriptor_set_14(command_buffer,
                           renderer->compose_pipeline_layout,
                           VK_SHADER_STAGE_FRAGMENT_BIT,
                           output->compose_set);
    struct walle_vk_compose_push compose_push = {
        .timeline = {frame->progress,
                     (float)output->extent.width,
                     (float)output->extent.height,
                     frame->variant},
        .geometry = {frame->center_top_left_x, frame->center_top_left_y, frame->radius, 0.0f},
    };
    push_constants_14(command_buffer,
                      renderer->compose_pipeline_layout,
                      VK_SHADER_STAGE_FRAGMENT_BIT,
                      &compose_push,
                      sizeof compose_push);
    vkCmdDraw(command_buffer, 3, 1, 0, 0);
    vkCmdEndRendering(command_buffer);
    if (frame->composition_readback) {
        image_barrier(command_buffer,
                      present->image.handle,
                      VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                      VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                      VK_PIPELINE_STAGE_2_COPY_BIT,
                      VK_ACCESS_2_TRANSFER_READ_BIT,
                      VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL,
                      VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
        VkBufferImageCopy copy = {
            .bufferOffset = mask_size,
            .imageSubresource = {
                .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                .layerCount = 1,
            },
            .imageExtent = {
                .width = output->extent.width,
                .height = output->extent.height,
                .depth = 1,
            },
        };
        vkCmdCopyImageToBuffer(command_buffer,
                               present->image.handle,
                               VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                               output->readback_buffer.handle,
                               1,
                               &copy);
        image_barrier_queues(command_buffer,
                             present->image.handle,
                             VK_PIPELINE_STAGE_2_COPY_BIT,
                             VK_ACCESS_2_TRANSFER_READ_BIT,
                             VK_PIPELINE_STAGE_2_NONE,
                             VK_ACCESS_2_NONE,
                             VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                             VK_IMAGE_LAYOUT_GENERAL,
                             renderer->queue_family,
                             VK_QUEUE_FAMILY_FOREIGN_EXT);
    } else {
        image_barrier_queues(command_buffer,
                             present->image.handle,
                             VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT,
                             VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT,
                             VK_PIPELINE_STAGE_2_NONE,
                             VK_ACCESS_2_NONE,
                             VK_IMAGE_LAYOUT_ATTACHMENT_OPTIMAL,
                             VK_IMAGE_LAYOUT_GENERAL,
                             renderer->queue_family,
                             VK_QUEUE_FAMILY_FOREIGN_EXT);
    }
    present->layout        = VK_IMAGE_LAYOUT_GENERAL;
    present->foreign_owned = true;
    return vk_check(vkEndCommandBuffer(command_buffer), "vkEndCommandBuffer(frame)");
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

enum walle_vk_frame_status walle_vk_output_render(struct walle_vk_output*      output,
                                                  const struct walle_vk_frame* frame)
{
    if (!output || !frame || output->renderer->fatal
        || !reveal_geometry_valid(output, frame->geometry) || !isfinite(frame->progress)
        || !isfinite(frame->variant) || !isfinite(frame->center_top_left_x)
        || !isfinite(frame->center_top_left_y) || !isfinite(frame->radius) || frame->radius < 0.0f)
        return WALLE_VK_FRAME_FATAL;
    VkDeviceSize mask_size;
    bool readback = frame->mask_readback != nullptr || frame->composition_readback != nullptr;
    VkDeviceSize composition_size;
    if (ckd_mul(&mask_size, (VkDeviceSize)output->extent.width, (VkDeviceSize)output->extent.height)
        || ckd_mul(&composition_size, mask_size, (VkDeviceSize)4)
        || (frame->mask_readback && frame->mask_readback_size != mask_size)
        || (frame->composition_readback
            && (!output->composition_readback_enabled
                || frame->composition_readback_size != composition_size)))
        return WALLE_VK_FRAME_FATAL;
    if (!ensure_transition_base(output, readback))
        return WALLE_VK_FRAME_FATAL;

    VkDevice device = output->renderer->device;
    if (!vk_check(vkWaitForFences(device, 1, &output->frame_fence, VK_TRUE, UINT64_MAX),
                  "vkWaitForFences(frame)"))
        return WALLE_VK_FRAME_FATAL;

    uint32_t image_index;
    if (!take_present_image(output, &image_index))
        return output->renderer->fatal ? WALLE_VK_FRAME_FATAL : WALLE_VK_FRAME_RETRY;

    struct walle_lg_reveal_raster  raster       = {};
    struct walle_lg_reveal_general general      = {};
    VkBufferCopy                   copies[7]    = {};
    uint32_t                       copy_count   = 0;
    bool                           host_written = false;
    static int general_enabled = -1;
    if (general_enabled < 0) {
        /* The measured per-tile general path outperforms the analytic
           fallback on the parity gate (70 vs 80 mismatched bytes,
           TASK.md later-133); it is now the default.  Set
           WALLE_REVEAL_GENERAL=0 to force the fallback. */
        const char* env = getenv("WALLE_REVEAL_GENERAL");
        general_enabled = env == nullptr || env[0] != '0' ? 1 : 0;
    }
    if (frame->geometry->index_count) {
        const struct walle_lg_raster_calibration calibration = {
            .p25_ceil_bits          = WALLE_VK_REVEAL_RASTER_P25,
            .p25_selector_bit_count = UINT64_C(1) << 24,
        };
        enum walle_lg_reveal_raster_status status = walle_lg_reveal_raster_construct(
            frame->geometry, output->extent.width, output->extent.height, &calibration, &raster);
        if (status == WALLE_LG_REVEAL_RASTER_OK && general_enabled
            && walle_lg_reveal_general_construct(frame->geometry,
                                                 output->extent.width,
                                                 output->extent.height,
                                                 &calibration,
                                                 &general)
                   != WALLE_LG_REVEAL_RASTER_OK) {
            walle_lg_reveal_general_destroy(&general);
        }
        if (status != WALLE_LG_REVEAL_RASTER_OK || !reveal_raster_valid(&raster)
            || !stage_reveal_data(output,
                                  frame->geometry,
                                  &raster,
                                  general_enabled ? &general : nullptr,
                                  copies,
                                  &copy_count,
                                  &host_written)) {
            walle_lg_reveal_general_destroy(&general);
            walle_lg_reveal_raster_destroy(&raster);
            return WALLE_VK_FRAME_FATAL;
        }
    }

    if (!update_compose_descriptors(output, frame->first_boot)) {
        walle_lg_reveal_general_destroy(&general);
        walle_lg_reveal_raster_destroy(&raster);
        return WALLE_VK_FRAME_FATAL;
    }

    if (!record_frame(output,
                      image_index,
                      frame,
                      &raster,
                      general_enabled ? general.child_count : 0u,
                      copies,
                      copy_count,
                      host_written,
                      mask_size)
        || !vk_check(vkResetFences(device, 1, &output->frame_fence), "vkResetFences(frame)")) {
        output->renderer->fatal = true;
        walle_lg_reveal_general_destroy(&general);
        walle_lg_reveal_raster_destroy(&raster);
        return WALLE_VK_FRAME_FATAL;
    }
    walle_lg_reveal_general_destroy(&general);
    walle_lg_reveal_raster_destroy(&raster);

    VkCommandBufferSubmitInfo command_info = {
        .sType         = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO,
        .commandBuffer = output->command_buffer,
    };
    VkSubmitInfo2 submit_info = {
        .sType                  = VK_STRUCTURE_TYPE_SUBMIT_INFO_2,
        .commandBufferInfoCount = 1,
        .pCommandBufferInfos    = &command_info,
    };
    if (!vk_check(vkQueueSubmit2(output->renderer->queue, 1, &submit_info, output->frame_fence),
                  "vkQueueSubmit2(frame)")) {
        output->renderer->fatal = true;
        return WALLE_VK_FRAME_FATAL;
    }
    struct walle_vk_present_image* present = &output->present_images[image_index];
    if (readback) {
        if (!vk_check(vkWaitForFences(device, 1, &output->frame_fence, VK_TRUE, UINT64_MAX),
                      "vkWaitForFences(mask readback)")) {
            output->renderer->fatal = true;
            return WALLE_VK_FRAME_FATAL;
        }
        if (frame->mask_readback)
            memcpy(frame->mask_readback, output->readback_buffer.memory.mapped, (size_t)mask_size);
        if (frame->composition_readback)
            memcpy(frame->composition_readback,
                   (uint8_t*)output->readback_buffer.memory.mapped + (size_t)mask_size,
                   (size_t)composition_size);
    }
    wl_surface_attach(output->wayland_surface, present->buffer, 0, 0);
    wl_surface_damage_buffer(output->wayland_surface,
                             0,
                             0,
                             (int32_t)output->extent.width,
                             (int32_t)output->extent.height);
    present->busy              = true;
    output->last_present_image = image_index;
    return WALLE_VK_FRAME_OK;
}

void walle_vk_output_promote(struct walle_vk_output* output)
{
    if (!output)
        return;
    VkDevice device = output->renderer->device;
    if (output->frame_fence)
        vkWaitForFences(device, 1, &output->frame_fence, VK_TRUE, UINT64_MAX);
    destroy_texture_pair(device, &output->current);
    destroy_texture_pair(device, &output->incoming);
    destroy_transition_resources(output);
    compact_present_images(output);
}

void walle_vk_output_abort_transition(struct walle_vk_output* output)
{
    if (!output || !output->renderer->device)
        return;
    VkDevice device = output->renderer->device;
    if (output->frame_fence)
        vkWaitForFences(device, 1, &output->frame_fence, VK_TRUE, UINT64_MAX);
    destroy_texture_pair(device, &output->current);
    destroy_texture_pair(device, &output->incoming);
    destroy_transition_resources(output);
    compact_present_images(output);
}

void walle_vk_output_destroy(struct walle_vk_output* output)
{
    if (!output)
        return;
    struct walle_vk_renderer* renderer = output->renderer;
    if (renderer && renderer->device) {
        vkDeviceWaitIdle(renderer->device);
        destroy_texture_pair(renderer->device, &output->current);
        destroy_texture_pair(renderer->device, &output->incoming);
        destroy_transition_resources(output);
        if (output->frame_fence)
            vkDestroyFence(renderer->device, output->frame_fence, nullptr);
        if (output->command_pool)
            vkDestroyCommandPool(renderer->device, output->command_pool, nullptr);
        destroy_present_images(output);
    }
    if (renderer && renderer->instance && output->surface)
        vkDestroySurfaceKHR(renderer->instance, output->surface, nullptr);
    free(output);
}
