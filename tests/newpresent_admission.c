#define _GNU_SOURCE
#include "../vulkan_renderer.c"

/* CPU-only driver controls. Every Vulkan operation reached by these tests is
 * wrapped; no instance, device, GPU, compositor or allocation is created. */
enum scenario { DIRECT, SAMPLED_TOO_SMALL, SAMPLED_CREATE_FAIL, SAMPLED_QUERY_FAIL, BOTH_TOO_SMALL };
static enum scenario scenario;
static unsigned sampled_queries, plain_queries, sampled_creates, plain_creates;
static unsigned enumeration_calls, wayland_surface_calls;
static bool exportable = true;
static VkDeviceSize image_bytes;
static int injected_stage = -1;
static VkResult injected_result;
static unsigned resource_calls[6], image_destroys, memory_frees, view_destroys;
static unsigned wayland_requests, wayland_version_queries;

static VkResult resource_result(unsigned stage)
{
    if (injected_stage < 0) return VK_SUCCESS;
    ++resource_calls[stage];
    return (unsigned)injected_stage == stage ? injected_result : VK_SUCCESS;
}

uint32_t __wrap_wl_proxy_get_version(struct wl_proxy* proxy)
{ (void)proxy; ++wayland_version_queries; return 5; }
struct wl_proxy* __wrap_wl_proxy_marshal_flags(struct wl_proxy* proxy, uint32_t opcode,
    const struct wl_interface* interface, uint32_t version, uint32_t flags, ...)
{
    (void)proxy; (void)opcode; (void)interface; (void)version; (void)flags;
    ++wayland_requests; return nullptr;
}

void __wrap_vkGetPhysicalDeviceFormatProperties2(VkPhysicalDevice device,
    VkFormat format, VkFormatProperties2* properties)
{
    (void)device; (void)format;
    VkDrmFormatModifierPropertiesList2EXT* list=properties->pNext;
    if (list->pDrmFormatModifierProperties)
        list->pDrmFormatModifierProperties[0]=(VkDrmFormatModifierProperties2EXT){
            .drmFormatModifier=0,.drmFormatModifierPlaneCount=1,
            .drmFormatModifierTilingFeatures=VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT
                | VK_FORMAT_FEATURE_2_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_BIT
                | VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_FILTER_LINEAR_BIT,
        };
    list->drmFormatModifierCount=1;
}
static VkResult mock_modifier_properties(VkDevice device, VkImage image,
    VkImageDrmFormatModifierPropertiesEXT* properties)
{
    (void)device; (void)image;
    properties->drmFormatModifier=0;
    return resource_result(4);
}
static VkResult mock_memory_fd(VkDevice device, const VkMemoryGetFdInfoKHR* info, int* fd)
{
    (void)device; (void)info;
    *fd=-1;
    return resource_result(5);
}

VkResult __wrap_vkEnumeratePhysicalDevices(VkInstance instance, uint32_t* count,
    VkPhysicalDevice* devices)
{
    (void)instance; (void)count; (void)devices;
    ++enumeration_calls; return VK_ERROR_INITIALIZATION_FAILED;
}
VkResult __wrap_vkCreateWaylandSurfaceKHR(VkInstance instance,
    const VkWaylandSurfaceCreateInfoKHR* info, const VkAllocationCallbacks* callbacks,
    VkSurfaceKHR* surface)
{
    (void)instance; (void)info; (void)callbacks;
    ++wayland_surface_calls; *surface=VK_NULL_HANDLE; return VK_ERROR_INITIALIZATION_FAILED;
}

static VkImageFormatProperties mock_properties(VkImageUsageFlags usage)
{
    bool sampled = (usage & VK_IMAGE_USAGE_SAMPLED_BIT) != 0;
    bool small = scenario == BOTH_TOO_SMALL || (sampled && scenario == SAMPLED_TOO_SMALL);
    return (VkImageFormatProperties){
        .maxExtent = {small ? 1024u : 8192u, small ? 1024u : 8192u, 1},
        .maxMipLevels = 1, .maxArrayLayers = 1, .sampleCounts = VK_SAMPLE_COUNT_1_BIT,
        .maxResourceSize = UINT64_C(1) << 32,
    };
}
VkResult __wrap_vkGetPhysicalDeviceImageFormatProperties(VkPhysicalDevice device,
    VkFormat format, VkImageType type, VkImageTiling tiling, VkImageUsageFlags usage,
    VkImageCreateFlags flags, VkImageFormatProperties* properties)
{
    (void)device; (void)format; (void)type; (void)tiling; (void)flags;
    bool sampled = (usage & VK_IMAGE_USAGE_SAMPLED_BIT) != 0;
    if (sampled) ++sampled_queries; else ++plain_queries;
    if (sampled && scenario == SAMPLED_QUERY_FAIL) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    *properties = mock_properties(usage);
    return VK_SUCCESS;
}
VkResult __wrap_vkGetPhysicalDeviceImageFormatProperties2(VkPhysicalDevice device,
    const VkPhysicalDeviceImageFormatInfo2* info, VkImageFormatProperties2* properties)
{
    (void)device;
    if (info->usage & VK_IMAGE_USAGE_SAMPLED_BIT) ++sampled_queries; else ++plain_queries;
    properties->imageFormatProperties = mock_properties(info->usage);
    VkExternalImageFormatProperties* external = properties->pNext;
    external->externalMemoryProperties.externalMemoryFeatures
        = exportable ? VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT : 0;
    return VK_SUCCESS;
}
VkResult __wrap_vkCreateImage(VkDevice device, const VkImageCreateInfo* info,
    const VkAllocationCallbacks* callbacks, VkImage* image)
{
    (void)device; (void)callbacks;
    bool sampled = (info->usage & VK_IMAGE_USAGE_SAMPLED_BIT) != 0;
    if (sampled) ++sampled_creates; else ++plain_creates;
    *image = VK_NULL_HANDLE;
    VkResult status=resource_result(0);
    if (status!=VK_SUCCESS) return status;
    if (sampled && scenario == SAMPLED_CREATE_FAIL) return VK_ERROR_FORMAT_NOT_SUPPORTED;
    image_bytes = (VkDeviceSize)info->extent.width * info->extent.height * 4;
    *image = (VkImage)(uintptr_t)1;
    return VK_SUCCESS;
}
void __wrap_vkGetImageMemoryRequirements(VkDevice device, VkImage image,
    VkMemoryRequirements* requirements)
{
    (void)device; (void)image;
    *requirements = (VkMemoryRequirements){.size=image_bytes,.alignment=1,.memoryTypeBits=1};
}
VkResult __wrap_vkAllocateMemory(VkDevice device, const VkMemoryAllocateInfo* info,
    const VkAllocationCallbacks* callbacks, VkDeviceMemory* memory)
{
    (void)device; (void)info; (void)callbacks;
    *memory=VK_NULL_HANDLE;
    VkResult status=resource_result(1);
    if (status!=VK_SUCCESS) return status;
    *memory=(VkDeviceMemory)(uintptr_t)2; return VK_SUCCESS;
}
VkResult __wrap_vkBindImageMemory(VkDevice device, VkImage image, VkDeviceMemory memory,
    VkDeviceSize offset)
{ (void)device; (void)image; (void)memory; (void)offset; return resource_result(2); }
VkResult __wrap_vkCreateImageView(VkDevice device, const VkImageViewCreateInfo* info,
    const VkAllocationCallbacks* callbacks, VkImageView* view)
{
    (void)device; (void)info; (void)callbacks;
    *view=VK_NULL_HANDLE;
    VkResult status=resource_result(3);
    if (status!=VK_SUCCESS) return status;
    *view=(VkImageView)(uintptr_t)3; return VK_SUCCESS;
}
void __wrap_vkDestroyImage(VkDevice device, VkImage image, const VkAllocationCallbacks* callbacks)
{ (void)device; (void)image; (void)callbacks; ++image_destroys; }
void __wrap_vkDestroyImageView(VkDevice device, VkImageView view, const VkAllocationCallbacks* callbacks)
{ (void)device; (void)view; (void)callbacks; ++view_destroys; }
void __wrap_vkFreeMemory(VkDevice device, VkDeviceMemory memory, const VkAllocationCallbacks* callbacks)
{ (void)device; (void)memory; (void)callbacks; ++memory_frees; }

#define CHECK(x) do { if (!(x)) { fprintf(stderr,"present admission line%d: %s\n",__LINE__,#x); return 1; } } while (0)

int main(void)
{
    struct walle_vk_renderer renderer = {.device=(VkDevice)(uintptr_t)1};
    renderer.properties.limits=(VkPhysicalDeviceLimits){
        .maxImageDimension2D=8192,.maxFramebufferWidth=8192,.maxFramebufferHeight=8192,
        .maxViewportDimensions={8192,8192},.viewportBoundsRange={-32768,32767},
    };
    renderer.memory_properties.memoryTypeCount=1;
    renderer.memory_properties.memoryTypes[0].propertyFlags=VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
    unsigned cases=0;
    for (scenario=DIRECT;scenario<=BOTH_TOO_SMALL;scenario++) {
        sampled_queries=plain_queries=sampled_creates=plain_creates=0;
        struct walle_vk_output output={.renderer=&renderer,.extent={1920,1080}};
        bool ok=initialize_present_images(&output);
        CHECK(ok==(scenario!=BOTH_TOO_SMALL));
        CHECK(sampled_queries==1);
        CHECK(plain_queries==(scenario!=DIRECT));
        CHECK(sampled_creates==(scenario==DIRECT || scenario==SAMPLED_CREATE_FAIL));
        CHECK(plain_creates==(scenario==SAMPLED_TOO_SMALL || scenario==SAMPLED_CREATE_FAIL
                              || scenario==SAMPLED_QUERY_FAIL));
        if (ok) CHECK(((output.present_usage&VK_IMAGE_USAGE_SAMPLED_BIT)!=0)==(scenario==DIRECT));
        destroy_present_images(&output);
        CHECK(renderer.memory_stats.allocated_bytes==0 && renderer.memory_stats.allocation_count==0);
        ++cases;
    }
    scenario=DIRECT;
    VkImageFormatProperties properties=mock_properties(PRESENT_USAGE);
    VkExtent2D extent={1920,1080};
    CHECK(present_format_extent(&properties,extent)); ++cases;
    VkImageFormatProperties bad=properties;bad.maxExtent.depth=0;
    CHECK(!present_format_extent(&bad,extent)); ++cases;
    bad=properties;bad.maxMipLevels=0;CHECK(!present_format_extent(&bad,extent)); ++cases;
    bad=properties;bad.maxArrayLayers=0;CHECK(!present_format_extent(&bad,extent)); ++cases;
    bad=properties;bad.sampleCounts=VK_SAMPLE_COUNT_2_BIT;CHECK(!present_format_extent(&bad,extent)); ++cases;
    bad=properties;bad.maxResourceSize=(VkDeviceSize)1920*1080*4-1;
    CHECK(!present_format_extent(&bad,extent)); ++cases;
    CHECK(!present_format_extent(&properties,(VkExtent2D){0,1080})); ++cases;
    scenario=SAMPLED_TOO_SMALL;
    CHECK(!present_modifier_exportable(&renderer,0,PRESENT_USAGE|VK_IMAGE_USAGE_SAMPLED_BIT,extent)); ++cases;
    CHECK(present_modifier_exportable(&renderer,0,PRESENT_USAGE,extent)); ++cases;
    exportable=false;
    CHECK(!present_modifier_exportable(&renderer,0,PRESENT_USAGE,extent)); ++cases;
    /* Fail every resource stage of the actual Wayland presentation path.
     * A lost device must stop before the plain-usage fallback or any Wayland
     * request. Ordinary OOM controls still take both usage attempts. */
    const struct walle_vk_dmabuf_candidate candidate={DRM_FORMAT_XRGB8888,0};
    renderer.instance=(VkInstance)(uintptr_t)4;
    renderer.device_ready=true;
    renderer.dmabuf.candidates=(struct walle_vk_dmabuf_candidate*)&candidate;
    renderer.dmabuf.candidate_count=1;
    renderer.dmabuf.factory=(struct zwp_linux_dmabuf_v1*)(uintptr_t)6;
    renderer.get_image_drm_format_modifier_properties=mock_modifier_properties;
    renderer.get_memory_fd=mock_memory_fd;
    scenario=DIRECT; exportable=true;
    for (injected_stage=0;injected_stage<6;++injected_stage)
        for (unsigned loss=0;loss<2;++loss) {
            renderer.fatal=false;
            injected_result=loss ? VK_ERROR_DEVICE_LOST : VK_ERROR_OUT_OF_HOST_MEMORY;
            memset(resource_calls,0,sizeof resource_calls);
            sampled_queries=plain_queries=sampled_creates=plain_creates=0;
            image_destroys=memory_frees=view_destroys=0;
            wayland_requests=wayland_version_queries=0;
            struct walle_vk_output output={.renderer=&renderer,.extent={1920,1080},
                .wayland_surface=(struct wl_surface*)(uintptr_t)5};
            CHECK(!initialize_present_images(&output));
            unsigned attempts=loss ? 1 : 2;
            CHECK(renderer.fatal==(loss!=0));
            CHECK(sampled_queries==1 && sampled_creates==1);
            CHECK(plain_queries==!loss && plain_creates==!loss);
            for (unsigned stage=0;stage<6;++stage)
                CHECK(resource_calls[stage]==(stage<=(unsigned)injected_stage ? attempts : 0));
            CHECK(image_destroys==(injected_stage>=1 ? attempts : 0));
            CHECK(memory_frees==(injected_stage>=2 ? attempts : 0));
            CHECK(view_destroys==(injected_stage>=4 ? attempts : 0));
            CHECK(!output.present_images[0].image.handle && !output.present_images[0].buffer
                  && !output.present_images[0].has_dmabuf_fd);
            destroy_present_images(&output);
            CHECK(renderer.memory_stats.allocated_bytes==0
                  && renderer.memory_stats.allocation_count==0);
            CHECK(!wayland_requests && !wayland_version_queries);
            if (loss) {
                struct walle_vk_output* rejected=(struct walle_vk_output*)(uintptr_t)7;
                CHECK(!initialize_device(&renderer,VK_NULL_HANDLE));
                CHECK(!walle_vk_output_create(&renderer,(struct wl_surface*)(uintptr_t)5,
                                              64,64,false,&rejected));
                CHECK(!rejected && !enumeration_calls && !wayland_surface_calls);
                CHECK(!wayland_requests && !wayland_version_queries);
            }
            ++cases;
        }
    injected_stage=-1;
    /* A partial bootstrap still owns its device and global objects. Neither a
     * second initialization nor another output may overwrite those handles. */
    renderer.instance=(VkInstance)(uintptr_t)4;
    renderer.device_ready=false;
    renderer.fatal=false;
    VkDevice partial=renderer.device;
    CHECK(!initialize_device(&renderer,VK_NULL_HANDLE));
    CHECK(renderer.fatal && renderer.device==partial && !renderer.device_ready); ++cases;
    CHECK(!initialize_device(&renderer,VK_NULL_HANDLE));
    CHECK(renderer.device==partial && enumeration_calls==0); ++cases;
    struct walle_vk_output* rejected=nullptr;
    CHECK(!walle_vk_output_create(&renderer,(struct wl_surface*)(uintptr_t)5,64,64,false,&rejected));
    CHECK(!rejected && renderer.device==partial && wayland_surface_calls==0); ++cases;
    renderer.device=VK_NULL_HANDLE;
    CHECK(!initialize_device(&renderer,VK_NULL_HANDLE));
    CHECK(renderer.fatal && !renderer.device && enumeration_calls==0); ++cases;
    CHECK(!walle_vk_output_create(&renderer,(struct wl_surface*)(uintptr_t)5,64,64,false,&rejected));
    CHECK(!rejected && wayland_surface_calls==0); ++cases;
    printf("PASS %u presentation/init controls; includes six Wayland device-loss stages, six OOM fallback controls, exact cleanup and zero Wayland requests after resource failure; no GPU used\n",cases);
    return 0;
}
