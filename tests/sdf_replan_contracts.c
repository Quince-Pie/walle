#define _GNU_SOURCE
#include "../vulkan_renderer.c"
#include "transition_check.h"

static VkResult image_result, memory_result, bind_result, view_result;
static VkFormat expected_image_format;
static unsigned images, allocations, binds, views, image_destroys, frees;
static unsigned command_resets, command_begins, submissions, fences;

VkResult __wrap_vkCreateImage(VkDevice device, const VkImageCreateInfo* info,
                              const VkAllocationCallbacks* allocator, VkImage* image)
{
    (void)device; (void)allocator;
    CHECK(info->format == expected_image_format);
    ++images;
    *image = image_result == VK_SUCCESS ? (VkImage)(uintptr_t)10 : VK_NULL_HANDLE;
    return image_result;
}
void __wrap_vkGetImageMemoryRequirements(VkDevice device, VkImage image, VkMemoryRequirements* requirements)
{
    (void)device; (void)image;
    *requirements = (VkMemoryRequirements){.size=131072,.alignment=256,.memoryTypeBits=1};
}
VkResult __wrap_vkAllocateMemory(VkDevice device, const VkMemoryAllocateInfo* info,
                                 const VkAllocationCallbacks* allocator, VkDeviceMemory* memory)
{
    (void)device; (void)info; (void)allocator;
    ++allocations;
    *memory = memory_result == VK_SUCCESS ? (VkDeviceMemory)(uintptr_t)11 : VK_NULL_HANDLE;
    return memory_result;
}
VkResult __wrap_vkBindImageMemory(VkDevice device, VkImage image, VkDeviceMemory memory, VkDeviceSize offset)
{
    (void)device; (void)image; (void)memory; (void)offset;
    ++binds;
    return bind_result;
}
VkResult __wrap_vkCreateImageView(VkDevice device, const VkImageViewCreateInfo* info,
                                 const VkAllocationCallbacks* allocator, VkImageView* view)
{
    (void)device; (void)info; (void)allocator;
    ++views;
    *view = view_result == VK_SUCCESS ? (VkImageView)(uintptr_t)12 : VK_NULL_HANDLE;
    return view_result;
}
void __wrap_vkDestroyImage(VkDevice device, VkImage image, const VkAllocationCallbacks* allocator)
{
    (void)device; (void)image; (void)allocator;
    ++image_destroys;
}
void __wrap_vkDestroyImageView(VkDevice device, VkImageView view, const VkAllocationCallbacks* allocator)
{
    (void)device; (void)view; (void)allocator;
}
void __wrap_vkFreeMemory(VkDevice device, VkDeviceMemory memory, const VkAllocationCallbacks* allocator)
{
    (void)device; (void)memory; (void)allocator;
    ++frees;
}
VkResult __wrap_vkResetCommandBuffer(VkCommandBuffer command, VkCommandBufferResetFlags flags)
{
    (void)command; (void)flags;
    ++command_resets;
    return VK_ERROR_UNKNOWN;
}
VkResult __wrap_vkBeginCommandBuffer(VkCommandBuffer command, const VkCommandBufferBeginInfo* info)
{
    (void)command; (void)info;
    ++command_begins;
    return VK_ERROR_UNKNOWN;
}
VkResult __wrap_vkQueueSubmit2(VkQueue queue, uint32_t count, const VkSubmitInfo2* submissions_info, VkFence fence)
{
    (void)queue; (void)count; (void)submissions_info; (void)fence;
    ++submissions;
    return VK_ERROR_UNKNOWN;
}
VkResult __wrap_vkGetFenceStatus(VkDevice device, VkFence fence)
{
    (void)device; (void)fence;
    ++fences;
    return VK_NOT_READY;
}

static void initialize(struct walle_vk_renderer* renderer, struct walle_vk_output* output)
{
    *renderer = (struct walle_vk_renderer){.device_ready=true,.device=(VkDevice)(uintptr_t)1};
    atomic_init(&renderer->validation_error_count, 0);
    renderer->properties.limits = (VkPhysicalDeviceLimits){.maxImageDimension2D=4096,
        .maxFramebufferWidth=1024,.maxFramebufferHeight=1024,.maxViewportDimensions={1024,1024},
        .viewportBoundsRange={-4096,4096},.minUniformBufferOffsetAlignment=256};
    renderer->memory_properties.memoryTypeCount = 1;
    renderer->memory_properties.memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
    *output = (struct walle_vk_output){.renderer=renderer,.extent={256,144}};
    output->current.handle = (VkImage)(uintptr_t)2;
    output->incoming.handle = (VkImage)(uintptr_t)3;
    output->present_images[0].image.handle = (VkImage)(uintptr_t)4;
    /* Keep the mandatory MRT2 attachment available while this fixture injects
     * specifically cache-primary failures. Dedicated MRT2 tests cover its own
     * ordinary and cache-growth allocation failures. */
    output->auxiliary = (struct walle_vk_image){.handle=(VkImage)(uintptr_t)5,
        .view=(VkImageView)(uintptr_t)6,.width=1024,.height=1024,
        .format=VK_FORMAT_R16G16B16A16_SFLOAT};
    image_result = memory_result = bind_result = view_result = VK_SUCCESS;
    expected_image_format = VK_FORMAT_R16G16B16A16_SFLOAT;
    images = allocations = binds = views = image_destroys = frees = 0;
    command_resets = command_begins = submissions = fences = 0;
}
static void stopped_before_gpu(const struct walle_vk_output* output)
{
    CHECK(!command_resets && !command_begins && !submissions);
    CHECK(!output->frame_pending);
    CHECK(output->renderer->memory_stats.allocated_bytes == 0);
    CHECK(output->renderer->memory_stats.allocation_count == 0);
}
int main(void)
{
    struct walle_vk_renderer renderer;
    struct walle_vk_output output;
    const struct walle_vk_vertex vertex = {};
    const uint32_t indices[3] = {0,0,0};
    struct walle_vk_draw draw = {.pass=WALLE_VK_SDF_CACHE,.vertices=&vertex,.vertex_count=1,
        .indices=indices,.index_count=3,.scissor={0,0,1,1},.edr_scale=1};
    const struct walle_vk_surface_plan valid = {.rect={-32,-32,128,128},
        .texture={128,128},.extent={128,128}};
    struct walle_vk_surface_plan plan = valid;
    struct walle_vk_frame frame = {.draws=&draw,.draw_count=1,.sdf_surface=&plan,
        .sdf_redraw=true,.sdf_retain=true,.material_opacity=1};
    unsigned invalid = 0, resource = 0, lost = 0;
    for (unsigned test = 0; test < 7; ++test) {
        snprintf(test_case, sizeof test_case, "invalid SDF metadata %u", test);
        initialize(&renderer, &output);
        plan = valid;
        frame.sdf_surface = &plan;
        switch (test) {
        case 0: frame.sdf_surface = nullptr; break;
        case 1: plan.rect[2] = 0; break;
        case 2: plan.texture[0] = 127; break;
        case 3: plan.extent[1] = 129; break;
        case 4: plan.texture[1] = UINT32_MAX; break;
        case 5: plan.rect[3] = -1; break;
        case 6: plan.extent[0] = 0; break;
        }
        CHECK(walle_vk_output_render(&output, &frame) == WALLE_VK_FRAME_FATAL);
        CHECK(!images && !output.sdf_replan && !renderer.fatal);
        stopped_before_gpu(&output);
        ++invalid;
    }
    frame.sdf_surface = &plan;
    for (unsigned axis = 0; axis < 2; ++axis) {
        snprintf(test_case, sizeof test_case, "SDF graphics limit axis%u", axis);
        initialize(&renderer, &output);
        plan = valid;
        plan.rect[axis + 2] = 1088;
        plan.texture[axis] = plan.extent[axis] = 1088;
        output.sdf.handle = (VkImage)(uintptr_t)8;
        output.sdf_ready = true;
        CHECK(walle_vk_output_render(&output, &frame) == WALLE_VK_FRAME_REPLAN);
        CHECK(!images && image_destroys == 1 && !output.sdf.handle && !output.sdf_ready);
        CHECK(output.sdf_replan && !renderer.fatal);
        stopped_before_gpu(&output);
        ++resource;
    }
    for (unsigned step = 0; step < 4; ++step)
        for (unsigned failure = 0; failure < 3; ++failure) {
            snprintf(test_case, sizeof test_case, "SDF create step%u failure%u", step, failure);
            initialize(&renderer, &output);
            plan = valid;
            VkResult result = failure == 0 ? VK_ERROR_OUT_OF_DEVICE_MEMORY
                : failure == 1 ? VK_ERROR_OUT_OF_HOST_MEMORY : VK_ERROR_DEVICE_LOST;
            VkResult* points[] = {&image_result,&memory_result,&bind_result,&view_result};
            *points[step] = result;
            CHECK(walle_vk_output_render(&output, &frame)
                  == (failure == 2 ? WALLE_VK_FRAME_FATAL : WALLE_VK_FRAME_REPLAN));
            CHECK(renderer.fatal == (failure == 2));
            CHECK(output.sdf_replan == (failure != 2));
            CHECK(images == 1 && allocations == (step >= 1) && binds == (step >= 2)
                  && views == (step >= 3));
            CHECK(image_destroys == (step >= 1) && frees == (step >= 2));
            CHECK(!output.sdf.handle && !output.sdf_ready);
            stopped_before_gpu(&output);
            if (failure == 2) ++lost; else ++resource;
        }
    snprintf(test_case, sizeof test_case, "pending prior frame only retries");
    initialize(&renderer, &output);
    plan = valid;
    output.frame_pending = true;
    CHECK(walle_vk_output_render(&output, &frame) == WALLE_VK_FRAME_RETRY);
    CHECK(fences == 1 && !images && !output.sdf_replan && !renderer.fatal);
    CHECK(!command_resets && !command_begins && !submissions);

    snprintf(test_case, sizeof test_case, "missing retained cache is invalid ownership");
    initialize(&renderer, &output);
    frame.sdf_redraw = false;
    draw.pass = WALLE_VK_HIGHLIGHT;
    draw.cached_sdf = true;
    CHECK(walle_vk_output_render(&output, &frame) == WALLE_VK_FRAME_FATAL);
    CHECK(!images && !output.sdf_replan);
    stopped_before_gpu(&output);
    ++invalid;
    snprintf(test_case, sizeof test_case, "non-SDF allocation failure is not a replan");
    initialize(&renderer, &output);
    expected_image_format = WALLE_VK_WALLPAPER_FORMAT;
    image_result = VK_ERROR_OUT_OF_DEVICE_MEMORY;
    plan.rect[0] = plan.rect[1] = 0;
    frame.sdf_surface = nullptr;
    frame.reveal_surface = &plan;
    draw.pass = WALLE_VK_REVEAL_MASK;
    draw.cached_sdf = false;
    CHECK(walle_vk_output_render(&output, &frame) == WALLE_VK_FRAME_FATAL);
    CHECK(images == 1 && !output.sdf_replan && !renderer.fatal);
    stopped_before_gpu(&output);
    printf("{\"invalid_metadata_or_ownership\":%u,\"resource_replans\":%u,"
           "\"device_lost_fatal\":%u,\"other_resource_fatal\":1,\"fence_retries\":1,\"gpu_recordings\":0}\n",
           invalid, resource, lost);
    return 0;
}
