#define _GNU_SOURCE
#include "../vulkan_renderer.c"
#include "transition_check.h"

/* These controls execute the renderer against a CPU driver double. The real
 * native descriptor inventory supplies the two-attachment/read-role contract. */
struct mock_image { uint32_t width, height; VkFormat format; VkImageUsageFlags usage; };
static struct mock_image mock_images[64];
static unsigned image_count, allocation_count, image_destroys, memory_frees, view_destroys;
static unsigned format_queries, command_resets, command_begins, submissions, barriers;
static unsigned selected_types[64], current_image;
static int failure_stage, failure_type;
static uint32_t failure_width;
static bool transient_failure_only;
static VkResult failure_result;
static VkImageFormatProperties image_properties;
static VkRenderingAttachmentInfo observed_attachments[2];
static VkRenderingInfo observed_rendering;
static uint32_t observed_locations[2], observed_inputs[2], observed_count;
static unsigned graphics_pipelines, compute_pipelines;
static bool production_pipelines;
static enum auxiliary_role expected_role;
static bool expected_local;
static VkFormat expected_format;
static VkBool32 expected_native_half_fma;

static void check_arithmetic_specialization(const VkPipelineShaderStageCreateInfo* stage)
{
    const VkSpecializationInfo* spec=stage->pSpecializationInfo;
    bool graphics=stage->stage==VK_SHADER_STAGE_FRAGMENT_BIT;
    CHECK(spec && spec->mapEntryCount==(graphics ? 2u : 1u)
          && spec->dataSize==(graphics ? 2u : 1u)*sizeof(VkBool32));
    CHECK(spec->pMapEntries[0].constantID==100 && spec->pMapEntries[0].offset==0
          && spec->pMapEntries[0].size==sizeof(VkBool32));
    VkBool32 selected;
    memcpy(&selected,spec->pData,sizeof selected);
    CHECK(selected==expected_native_half_fma);
    if(graphics){
        CHECK(spec->pMapEntries[1].constantID==101
              && spec->pMapEntries[1].offset==sizeof(VkBool32)
              && spec->pMapEntries[1].size==sizeof(VkBool32));
        VkBool32 blend;
        memcpy(&blend,(const uint8_t*)spec->pData+sizeof(VkBool32),sizeof blend);
        CHECK(blend==VK_FALSE); /* Existing fixtures use the shader blend path. */
    }
}

static unsigned check_native_half_fma_profiles(void)
{
    unsigned cases=0;
    VkPhysicalDeviceProperties properties={.vendorID=0x1002,.deviceID=0x7550,
        .pipelineCacheUUID={0x36,0x53,0xc5,0xb4,0x95,0xfa,0x5b,0x49,0xff,0xc1,0xd0,0x0b,0x65,0x1e,0x8a,0x06}};
    VkPhysicalDeviceVulkan12Properties arithmetic={.driverID=VK_DRIVER_ID_MESA_RADV,
        .shaderDenormPreserveFloat16=VK_TRUE,.shaderRoundingModeRTEFloat16=VK_TRUE,
        .shaderSignedZeroInfNanPreserveFloat16=VK_TRUE,.shaderRoundingModeRTZFloat16=VK_TRUE};
    snprintf(test_case,sizeof test_case,"qualified FMA profile and conservative fallback");
    CHECK(native_half_fma_profile(&properties,&arithmetic)); ++cases;
    properties.deviceID=0x13c0;
    CHECK(native_half_fma_profile(&properties,&arithmetic)); ++cases;
    /* Every bit of the backend build identity participates in qualification. */
    for(unsigned byte=0;byte<VK_UUID_SIZE;++byte)
        for(unsigned bit=0;bit<8;++bit) {
            properties.pipelineCacheUUID[byte]^=(uint8_t)(1u<<bit);
            CHECK(!native_half_fma_profile(&properties,&arithmetic)); ++cases;
            properties.pipelineCacheUUID[byte]^=(uint8_t)(1u<<bit);
        }
    for(unsigned input=0;input<8;++input) {
        VkPhysicalDeviceProperties p=properties;
        VkPhysicalDeviceVulkan12Properties a=arithmetic;
        switch(input) {
            case 0: p.vendorID=0x1003; break;
            case 1: p.deviceID=0x7300; break; /* GFX8: nativeFMA lowering is split. */
            case 2: p.deviceID=0x7551; break; /* unqualified, nearby PCI identity */
            case 3: a.driverID=VK_DRIVER_ID_MESA_LLVMPIPE; break;
            case 4: a.shaderDenormPreserveFloat16=VK_FALSE; break;
            case 5: a.shaderRoundingModeRTEFloat16=VK_FALSE; break;
            case 6: a.shaderSignedZeroInfNanPreserveFloat16=VK_FALSE; break;
            case 7: a.shaderRoundingModeRTZFloat16=VK_FALSE; break; /* LLVM backend */
        }
        CHECK(!native_half_fma_profile(&p,&a)); ++cases;
    }
    return cases;
}

static unsigned image_index(VkImage image)
{
    uintptr_t value = (uintptr_t)image;
    CHECK(value >= 100 && value < 100 + image_count);
    return (unsigned)(value - 100);
}
static bool inject(unsigned stage)
{
    const struct mock_image* image = &mock_images[current_image];
    return failure_stage == (int)stage && (!failure_width || image->width == failure_width)
        && (!transient_failure_only || (image->usage & VK_IMAGE_USAGE_TRANSIENT_ATTACHMENT_BIT));
}
VkResult __wrap_vkGetPhysicalDeviceImageFormatProperties(VkPhysicalDevice device, VkFormat format,
    VkImageType type, VkImageTiling tiling, VkImageUsageFlags usage, VkImageCreateFlags flags,
    VkImageFormatProperties* properties)
{
    (void)device; (void)flags;
    CHECK(type == VK_IMAGE_TYPE_2D && tiling == VK_IMAGE_TILING_OPTIMAL);
    CHECK(format == VK_FORMAT_R16G16B16A16_SFLOAT || format == WALLE_VK_PRESENT_FORMAT);
    if (usage & VK_IMAGE_USAGE_TRANSIENT_ATTACHMENT_BIT)
        CHECK(usage == (VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSIENT_ATTACHMENT_BIT));
    ++format_queries; *properties = image_properties; return VK_SUCCESS;
}
VkResult __wrap_vkCreateImage(VkDevice device, const VkImageCreateInfo* info,
    const VkAllocationCallbacks* callbacks, VkImage* image)
{
    (void)device; (void)callbacks;
    CHECK(image_count < 64 && info->mipLevels == 1 && info->arrayLayers == 1);
    current_image = image_count++;
    mock_images[current_image] = (struct mock_image){info->extent.width, info->extent.height,
                                                    info->format, info->usage};
    if (info->usage & VK_IMAGE_USAGE_TRANSIENT_ATTACHMENT_BIT)
        CHECK(info->format == VK_FORMAT_R16G16B16A16_SFLOAT);
    *image = VK_NULL_HANDLE;
    if (inject(0)) return failure_result;
    *image = (VkImage)(uintptr_t)(100 + current_image); return VK_SUCCESS;
}
void __wrap_vkGetImageMemoryRequirements(VkDevice device, VkImage image, VkMemoryRequirements* req)
{
    (void)device; current_image = image_index(image);
    const struct mock_image* m = &mock_images[current_image];
    uint32_t bytes = m->format == VK_FORMAT_R16G16B16A16_SFLOAT ? 8u : 4u;
    *req = (VkMemoryRequirements){.size=(VkDeviceSize)m->width*m->height*bytes,
                                 .alignment=256,.memoryTypeBits=3};
}
VkResult __wrap_vkAllocateMemory(VkDevice device, const VkMemoryAllocateInfo* info,
    const VkAllocationCallbacks* callbacks, VkDeviceMemory* memory)
{
    (void)device; (void)callbacks;
    CHECK(allocation_count < 64);
    selected_types[allocation_count++] = info->memoryTypeIndex;
    *memory = VK_NULL_HANDLE;
    if (inject(1) && (failure_type < 0 || info->memoryTypeIndex == (uint32_t)failure_type))
        return failure_result;
    *memory = (VkDeviceMemory)(uintptr_t)(1000 + allocation_count); return VK_SUCCESS;
}
VkResult __wrap_vkBindImageMemory(VkDevice device, VkImage image, VkDeviceMemory memory, VkDeviceSize offset)
{
    (void)device; (void)memory; CHECK(offset == 0); current_image = image_index(image);
    return inject(2) ? failure_result : VK_SUCCESS;
}
VkResult __wrap_vkCreateImageView(VkDevice device, const VkImageViewCreateInfo* info,
    const VkAllocationCallbacks* callbacks, VkImageView* view)
{
    (void)device; (void)callbacks; current_image = image_index(info->image);
    CHECK(info->format == mock_images[current_image].format);
    *view = VK_NULL_HANDLE;
    if (inject(3)) return failure_result;
    *view = (VkImageView)(uintptr_t)(2000 + current_image); return VK_SUCCESS;
}
void __wrap_vkDestroyImage(VkDevice device, VkImage image, const VkAllocationCallbacks* callbacks)
{ (void)device; (void)image; (void)callbacks; ++image_destroys; }
void __wrap_vkDestroyImageView(VkDevice device, VkImageView view, const VkAllocationCallbacks* callbacks)
{ (void)device; (void)view; (void)callbacks; ++view_destroys; }
void __wrap_vkFreeMemory(VkDevice device, VkDeviceMemory memory, const VkAllocationCallbacks* callbacks)
{ (void)device; (void)memory; (void)callbacks; ++memory_frees; }
VkResult __wrap_vkGetFenceStatus(VkDevice device, VkFence fence)
{ (void)device; (void)fence; return VK_NOT_READY; }
VkResult __wrap_vkResetCommandBuffer(VkCommandBuffer command, VkCommandBufferResetFlags flags)
{ (void)command; (void)flags; ++command_resets; return VK_ERROR_UNKNOWN; }
VkResult __wrap_vkBeginCommandBuffer(VkCommandBuffer command, const VkCommandBufferBeginInfo* info)
{ (void)command; (void)info; ++command_begins; return VK_ERROR_UNKNOWN; }
VkResult __wrap_vkQueueSubmit2(VkQueue queue, uint32_t count, const VkSubmitInfo2* info, VkFence fence)
{ (void)queue; (void)count; (void)info; (void)fence; ++submissions; return VK_ERROR_UNKNOWN; }
void __wrap_vkCmdPipelineBarrier2(VkCommandBuffer command, const VkDependencyInfo* info)
{
    (void)command; CHECK(info->imageMemoryBarrierCount == 1);
    const VkImageMemoryBarrier2* barrier = info->pImageMemoryBarriers;
    CHECK(barrier->oldLayout == VK_IMAGE_LAYOUT_UNDEFINED);
    CHECK(barrier->newLayout == VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL);
    CHECK(barrier->srcStageMask == VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT);
    CHECK(barrier->dstStageMask == VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT);
    ++barriers;
}
void __wrap_vkCmdBeginRendering(VkCommandBuffer command, const VkRenderingInfo* info)
{
    (void)command; CHECK(info->colorAttachmentCount >= 1 && info->colorAttachmentCount <= 2);
    observed_rendering = *info;
    memcpy(observed_attachments, info->pColorAttachments,
           info->colorAttachmentCount * sizeof *info->pColorAttachments);
}
void __wrap_vkCmdSetRenderingAttachmentLocations(VkCommandBuffer command,
    const VkRenderingAttachmentLocationInfo* info)
{
    (void)command; observed_count = info->colorAttachmentCount;
    memcpy(observed_locations, info->pColorAttachmentLocations, observed_count * sizeof(uint32_t));
}
void __wrap_vkCmdSetRenderingInputAttachmentIndices(VkCommandBuffer command,
    const VkRenderingInputAttachmentIndexInfo* info)
{
    (void)command; CHECK(info->colorAttachmentCount == observed_count);
    memcpy(observed_inputs, info->pColorAttachmentInputIndices, observed_count * sizeof(uint32_t));
}
VkResult __wrap_vkCreateShaderModule(VkDevice device, const VkShaderModuleCreateInfo* info,
    const VkAllocationCallbacks* callbacks, VkShaderModule* module)
{ (void)device; (void)info; (void)callbacks; *module=(VkShaderModule)(uintptr_t)3000; return VK_SUCCESS; }
void __wrap_vkDestroyShaderModule(VkDevice device, VkShaderModule module, const VkAllocationCallbacks* callbacks)
{ (void)device; (void)module; (void)callbacks; }
VkResult __wrap_vkCreateGraphicsPipelines(VkDevice device, VkPipelineCache cache, uint32_t count,
    const VkGraphicsPipelineCreateInfo* info, const VkAllocationCallbacks* callbacks, VkPipeline* pipeline)
{
    (void)device; (void)cache; (void)callbacks; CHECK(count == 1);
    CHECK(info->stageCount==2 && info->pStages[0].pSpecializationInfo==nullptr);
    check_arithmetic_specialization(&info->pStages[1]);
    const VkPipelineRenderingCreateInfo* render = info->pNext;
    const VkRenderingAttachmentLocationInfo* locations = render->pNext;
    const VkRenderingInputAttachmentIndexInfo* inputs = locations->pNext;
    enum auxiliary_role role = expected_role;
    if (production_pipelines)
        role = !strcmp(info->pStages[1].pName,"tintGradientFragment")
            ? AUXILIARY_READ_BACKDROP : AUXILIARY_DISCARD;
    uint32_t attachments = role == AUXILIARY_NONE ? 1u : 2u;
    CHECK(render->colorAttachmentCount == attachments);
    CHECK(locations->colorAttachmentCount == attachments && inputs->colorAttachmentCount == attachments);
    CHECK(info->pColorBlendState->attachmentCount == attachments);
    CHECK(locations->pColorAttachmentLocations[0] == 0);
    if (!production_pipelines) CHECK(render->pColorAttachmentFormats[0] == expected_format);
    CHECK(info->pColorBlendState->pAttachments[0].colorWriteMask == 15);
    if (attachments == 2) {
        CHECK(render->pColorAttachmentFormats[1] == VK_FORMAT_R16G16B16A16_SFLOAT);
        CHECK(locations->pColorAttachmentLocations[1] == VK_ATTACHMENT_UNUSED);
        CHECK(info->pColorBlendState->pAttachments[1].colorWriteMask == 0);
        CHECK(!info->pColorBlendState->pAttachments[1].blendEnable);
        CHECK(inputs->pColorAttachmentInputIndices[1] == (role == AUXILIARY_READ_BACKDROP ? 0u : VK_ATTACHMENT_UNUSED));
    }
    if (role == AUXILIARY_READ_BACKDROP)
        CHECK(inputs->pColorAttachmentInputIndices[0] == VK_ATTACHMENT_UNUSED);
    else if (!production_pipelines)
        CHECK(inputs->pColorAttachmentInputIndices[0] == (expected_local ? 0u : VK_ATTACHMENT_UNUSED));
    ++graphics_pipelines; *pipeline=(VkPipeline)(uintptr_t)(4000+graphics_pipelines); return VK_SUCCESS;
}
VkResult __wrap_vkCreateComputePipelines(VkDevice device, VkPipelineCache cache, uint32_t count,
    const VkComputePipelineCreateInfo* info, const VkAllocationCallbacks* callbacks, VkPipeline* pipeline)
{
    (void)device; (void)cache; (void)callbacks; CHECK(count == 1);
    check_arithmetic_specialization(&info->stage);
    ++compute_pipelines; *pipeline=(VkPipeline)(uintptr_t)(5000+compute_pipelines); return VK_SUCCESS;
}

static void initialize(struct walle_vk_renderer* r, struct walle_vk_output* o)
{
    *r=(struct walle_vk_renderer){.device=(VkDevice)(uintptr_t)1,.device_ready=true};
    atomic_init(&r->validation_error_count,0);
    r->properties.limits=(VkPhysicalDeviceLimits){.maxImageDimension2D=4096,
        .maxFramebufferWidth=4096,.maxFramebufferHeight=4096,.maxViewportDimensions={4096,4096},
        .viewportBoundsRange={-8192,8192},.maxColorAttachments=4,.minUniformBufferOffsetAlignment=256};
    r->memory_properties.memoryTypeCount=1;
    r->memory_properties.memoryTypes[0].propertyFlags=VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
    *o=(struct walle_vk_output){.renderer=r,.extent={256,144}};
    o->current.handle=(VkImage)(uintptr_t)2; o->incoming.handle=(VkImage)(uintptr_t)3;
    o->present_images[0].image.handle=(VkImage)(uintptr_t)4;
    memset(mock_images,0,sizeof mock_images); memset(selected_types,0,sizeof selected_types);
    image_count=allocation_count=image_destroys=memory_frees=view_destroys=0;
    format_queries=command_resets=command_begins=submissions=barriers=0;
    failure_stage=failure_type=-1; failure_width=0; transient_failure_only=true;
    failure_result=VK_ERROR_OUT_OF_DEVICE_MEMORY;
    image_properties=(VkImageFormatProperties){.maxExtent={4096,4096,1},.maxMipLevels=1,
        .maxArrayLayers=1,.sampleCounts=VK_SAMPLE_COUNT_1_BIT,.maxResourceSize=UINT64_C(1)<<32};
}
static void stopped_before_gpu(const struct walle_vk_output* output)
{
    CHECK(!command_resets && !command_begins && !submissions && !output->frame_pending);
}
static void clean(struct walle_vk_output* output)
{
    destroy_transition_resources(output);
    CHECK(!output->renderer->memory_stats.allocated_bytes);
    CHECK(!output->renderer->memory_stats.allocation_count);
}
int main(void)
{
    struct walle_vk_renderer r;
    struct walle_vk_output o;
    unsigned states=0, memory_cases=0, failures=0, lifecycle_cases=0;
    unsigned arithmetic_profiles=check_native_half_fma_profiles();
    initialize(&r,&o);
    for (unsigned role=0;role<3;++role)
        for (unsigned local=0;local<2;++local)
            for (unsigned clear=0;clear<2;++clear) {
                snprintf(test_case,sizeof test_case,"render state role%u local%u clear%u",role,local,clear);
                begin_render((VkCommandBuffer)(uintptr_t)8,(VkImageView)(uintptr_t)9,
                    (VkImageView)(uintptr_t)10,73,47,clear!=0,local!=0,(enum auxiliary_role)role);
                CHECK(observed_rendering.renderArea.extent.width==73 && observed_rendering.renderArea.extent.height==47);
                CHECK(observed_count==(role==0 ? 1u : 2u));
                CHECK(observed_attachments[0].imageLayout==VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ);
                CHECK(observed_attachments[0].loadOp==(clear ? VK_ATTACHMENT_LOAD_OP_CLEAR : VK_ATTACHMENT_LOAD_OP_LOAD));
                CHECK(observed_attachments[0].storeOp==VK_ATTACHMENT_STORE_OP_STORE);
                CHECK(observed_inputs[0]==(role==2 ? VK_ATTACHMENT_UNUSED : local ? 0u : VK_ATTACHMENT_UNUSED));
                if (role) {
                    CHECK(observed_locations[1]==VK_ATTACHMENT_UNUSED);
                    CHECK(observed_attachments[1].imageView==(VkImageView)(uintptr_t)10);
                    CHECK(observed_attachments[1].loadOp==(role==1 ? VK_ATTACHMENT_LOAD_OP_DONT_CARE : VK_ATTACHMENT_LOAD_OP_LOAD));
                    CHECK(observed_attachments[1].storeOp==(role==1 ? VK_ATTACHMENT_STORE_OP_DONT_CARE : VK_ATTACHMENT_STORE_OP_STORE));
                    CHECK(observed_attachments[1].imageLayout==(role==1 ? VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL : VK_IMAGE_LAYOUT_RENDERING_LOCAL_READ));
                    CHECK(observed_inputs[1]==(role==2 ? 0u : VK_ATTACHMENT_UNUSED));
                }
                expected_role=(enum auxiliary_role)role; expected_local=local!=0;
                expected_format=VK_FORMAT_B8G8R8A8_UNORM; VkPipeline pipeline;
                CHECK(create_graphics_pipeline(&r,SHADER(effectsVertex,"effectsVertex"),
                    SHADER(wallpaperFragment,"wallpaperFragment"),LAYOUT_GRAPHICS,0,expected_local,
                    expected_role,expected_format,&pipeline));
                ++states;
            }
    production_pipelines=true;
    for(unsigned profile=0;profile<2;++profile) {
        graphics_pipelines=compute_pipelines=0;
        r.native_half_fma=profile!=0; expected_native_half_fma=profile ? VK_TRUE : VK_FALSE;
        CHECK(create_pipelines(&r)); CHECK(graphics_pipelines==PIPE_COUNT+4 && compute_pipelines==2);
    }
    production_pipelines=false;

    snprintf(test_case,sizeof test_case,"lazy memory and device-local fallback");
    for (unsigned mode=0;mode<4;++mode) {
        initialize(&r,&o);
        r.memory_properties.memoryTypeCount=2;
        r.memory_properties.memoryTypes[1].propertyFlags=VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT|VK_MEMORY_PROPERTY_LAZILY_ALLOCATED_BIT;
        if (mode) { failure_stage=1; failure_type=1; failure_result=mode==3 ? VK_ERROR_DEVICE_LOST : VK_ERROR_OUT_OF_DEVICE_MEMORY; }
        if (mode==2) r.memory_properties.memoryTypeCount=1; /* no lazy type */
        bool ok=ensure_auxiliary(&o,256,144);
        CHECK(ok==(mode!=3));
        CHECK(r.fatal==(mode==3));
        if (mode==0) CHECK(allocation_count==1 && selected_types[0]==1);
        if (mode==1) CHECK(allocation_count==2 && selected_types[0]==1 && selected_types[1]==0);
        if (mode==2) CHECK(allocation_count==1 && selected_types[0]==0);
        if (mode==3) CHECK(allocation_count==1 && selected_types[0]==1);
        if (ok) {
            struct walle_vk_diagnostics d;
            CHECK(walle_vk_output_diagnostics(&o,false,&d));
            CHECK(d.auxiliary_bytes==(uint64_t)256*144*8 && d.output_bytes==d.auxiliary_bytes);
            CHECK(d.auxiliary_width==256 && d.auxiliary_height==144);
            CHECK(((d.auxiliary_memory_flags&VK_MEMORY_PROPERTY_LAZILY_ALLOCATED_BIT)!=0)==(mode==0));
            discard_auxiliary_contents(&o); CHECK(barriers==1);
        }
        clean(&o); ++memory_cases;
    }
    const struct walle_vk_frame plain={.plain_incoming=true};
    const struct walle_vk_vertex vertex={}; const uint32_t indices[3]={0,0,0};
    const struct walle_vk_draw cache_draw={.pass=WALLE_VK_SDF_CACHE,.vertices=&vertex,.vertex_count=1,
        .indices=indices,.index_count=3,.scissor={0,0,1,1},.edr_scale=1};
    const struct walle_vk_surface_plan cache_plan={.rect={-32,-32,512,512},.texture={512,512},.extent={512,512}};
    const struct walle_vk_frame cache_frame={.draws=&cache_draw,.draw_count=1,.sdf_surface=&cache_plan,
        .sdf_redraw=true,.sdf_retain=true,.material_opacity=1};
    for (unsigned stage=0;stage<4;++stage)
        for (unsigned lost=0;lost<2;++lost)
            for (unsigned cache=0;cache<2;++cache) {
                snprintf(test_case,sizeof test_case,"resource stage%u lost%u cache%u",stage,lost,cache);
                initialize(&r,&o);
                if (cache) CHECK(ensure_auxiliary(&o,256,144));
                VkImage preserved=o.auxiliary.handle;
                uint64_t bytes=r.memory_stats.allocated_bytes;
                failure_stage=(int)stage; failure_width=cache ? 512u : 256u;
                failure_result=lost ? VK_ERROR_DEVICE_LOST : VK_ERROR_OUT_OF_DEVICE_MEMORY;
                enum walle_vk_frame_status status=walle_vk_output_render(&o,cache ? &cache_frame : &plain);
                CHECK(status==(cache && !lost ? WALLE_VK_FRAME_REPLAN : WALLE_VK_FRAME_FATAL));
                CHECK(o.sdf_replan==(cache && !lost) && r.fatal==(lost!=0));
                CHECK(o.auxiliary.handle==preserved && !o.sdf.handle);
                CHECK(r.memory_stats.allocated_bytes==bytes);
                stopped_before_gpu(&o); clean(&o); ++failures;
            }
    snprintf(test_case,sizeof test_case,"mandatory versus cache format admission");
    for (unsigned cache=0;cache<2;++cache) {
        initialize(&r,&o);
        if (cache) CHECK(ensure_auxiliary(&o,256,144));
        image_properties.maxExtent.width=cache ? 256u : 128u;
        CHECK(walle_vk_output_render(&o,cache ? &cache_frame : &plain)
              ==(cache ? WALLE_VK_FRAME_REPLAN : WALLE_VK_FRAME_FATAL));
        CHECK(o.sdf_replan==(cache!=0) && !r.fatal);
        stopped_before_gpu(&o); clean(&o); ++failures;
    }
    snprintf(test_case,sizeof test_case,"safe retained capacity and exact extent fallback");
    initialize(&r,&o); CHECK(ensure_auxiliary(&o,512,128));
    VkImage original=o.auxiliary.handle; unsigned before=image_count;
    CHECK(ensure_auxiliary(&o,128,64) && image_count==before && o.auxiliary.handle==original);
    o.frame_pending=true;
    CHECK(!ensure_auxiliary(&o,128,512) && image_count==before && o.auxiliary.handle==original);
    o.frame_pending=false;
    image_properties.maxResourceSize=(VkDeviceSize)128*512*8;
    CHECK(ensure_auxiliary(&o,128,512) && o.auxiliary.width==128 && o.auxiliary.height==512);
    CHECK(o.auxiliary.handle!=original && image_destroys==1 && memory_frees==1);
    clean(&o); ++lifecycle_cases;

    snprintf(test_case,sizeof test_case,"output isolation and cancellation");
    initialize(&r,&o); struct walle_vk_output second={.renderer=&r,.extent={256,144}};
    CHECK(ensure_auxiliary(&o,256,144) && ensure_auxiliary(&second,128,64));
    CHECK(o.auxiliary.handle!=second.auxiliary.handle && r.memory_stats.allocation_count==2);
    walle_vk_output_abort_transition(&o);
    CHECK(!o.auxiliary.handle && second.auxiliary.handle && r.memory_stats.allocation_count==1);
    clean(&second); ++lifecycle_cases;

    snprintf(test_case,sizeof test_case,"resize destroys auxiliary before new presentation");
    initialize(&r,&o); CHECK(ensure_auxiliary(&o,256,144));
    CHECK(walle_vk_output_resize(&o,512,256));
    CHECK(!o.auxiliary.handle && o.extent.width==512 && o.extent.height==256);
    destroy_present_images(&o); CHECK(!r.memory_stats.allocated_bytes && !r.memory_stats.allocation_count);
    ++lifecycle_cases;
    printf("{\"render_pipeline_state_controls\":%u,\"production_graphics_pipelines\":%u,"
           "\"memory_preference_controls\":%u,\"resource_failure_controls\":%u,"
           "\"lifecycle_controls\":%u,\"arithmetic_profile_controls\":%u,"
           "\"arithmetic_specialization_states\":2,\"gpu_execution\":false}\n",
           states,graphics_pipelines,memory_cases,failures,lifecycle_cases,arithmetic_profiles);
    return 0;
}
