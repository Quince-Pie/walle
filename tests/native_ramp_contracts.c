#define _GNU_SOURCE
#include "../vulkan_renderer.c"
#include "transition_check.h"

static unsigned buffer_creates, samplers, sampler_destroys, pools, pool_destroys;
static unsigned commands, fences, fence_destroys, devices, steps;
static unsigned layouts, layout_destroys, descriptor_pools, descriptor_destroys, descriptor_resets;
static unsigned writes[9], copies, barriers, original_compares, cache_compares;
static int fail_stage, fail_layout;
static bool fail_descriptor_pool;
static VkResult failure;
static const void* cached_bytes;

int __real_memcmp(const void*,const void*,size_t);
int __wrap_memcmp(const void* a,const void* b,size_t bytes)
{
    if(bytes==sizeof native_tint_ramp){
        if(b==native_tint_ramp && a!=cached_bytes)++original_compares;
        if(a==cached_bytes)++cache_compares;
    }
    return __real_memcmp(a,b,bytes);
}
static bool fail_next(void){return (int)steps++==fail_stage;}
VkResult __wrap_vkCreateSampler(VkDevice d,const VkSamplerCreateInfo* i,const VkAllocationCallbacks* a,VkSampler* s)
{
    (void)d;(void)a;*s=VK_NULL_HANDLE;
    CHECK(i->addressModeU==VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE
          && i->addressModeV==VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE
          && i->addressModeW==VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE);
    CHECK(!i->anisotropyEnable && !i->compareEnable && !i->unnormalizedCoordinates && i->minLod==0);
    CHECK(i->magFilter==(steps==1 ? VK_FILTER_NEAREST : VK_FILTER_LINEAR));
    CHECK(i->minFilter==i->magFilter);
    CHECK(i->mipmapMode==(steps==0 ? VK_SAMPLER_MIPMAP_MODE_LINEAR : VK_SAMPLER_MIPMAP_MODE_NEAREST));
    CHECK(i->maxLod==(steps==2 ? 0.0f : VK_LOD_CLAMP_NONE));
    if(fail_next())return failure;
    *s=(VkSampler)(uintptr_t)(10+ ++samplers);return VK_SUCCESS;
}
void __wrap_vkDestroySampler(VkDevice d,VkSampler s,const VkAllocationCallbacks* a)
{(void)d;(void)s;(void)a;++sampler_destroys;}
VkResult __wrap_vkCreateCommandPool(VkDevice d,const VkCommandPoolCreateInfo* i,const VkAllocationCallbacks* a,VkCommandPool* p)
{
    (void)d;(void)a;CHECK(i->flags==(VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT|VK_COMMAND_POOL_CREATE_TRANSIENT_BIT));
    *p=VK_NULL_HANDLE;if(fail_next())return failure;
    *p=(VkCommandPool)(uintptr_t)20;++pools;return VK_SUCCESS;
}
void __wrap_vkDestroyCommandPool(VkDevice d,VkCommandPool p,const VkAllocationCallbacks* a)
{(void)d;(void)p;(void)a;++pool_destroys;}
VkResult __wrap_vkAllocateCommandBuffers(VkDevice d,const VkCommandBufferAllocateInfo* i,VkCommandBuffer* b)
{
    (void)d;CHECK(i->commandBufferCount==1 && i->level==VK_COMMAND_BUFFER_LEVEL_PRIMARY);
    *b=VK_NULL_HANDLE;if(fail_next())return failure;
    *b=(VkCommandBuffer)(uintptr_t)21;++commands;return VK_SUCCESS;
}
VkResult __wrap_vkCreateFence(VkDevice d,const VkFenceCreateInfo* i,const VkAllocationCallbacks* a,VkFence* f)
{
    (void)d;(void)a;CHECK(i->flags==VK_FENCE_CREATE_SIGNALED_BIT);*f=VK_NULL_HANDLE;
    if(fail_next())return failure;
    *f=(VkFence)(uintptr_t)22;++fences;return VK_SUCCESS;
}
void __wrap_vkDestroyFence(VkDevice d,VkFence f,const VkAllocationCallbacks* a)
{(void)d;(void)f;(void)a;++fence_destroys;}
VkResult __wrap_vkCreateBuffer(VkDevice d,const VkBufferCreateInfo* i,const VkAllocationCallbacks* a,VkBuffer* b)
{(void)d;(void)i;(void)a;*b=VK_NULL_HANDLE;++buffer_creates;return VK_ERROR_OUT_OF_DEVICE_MEMORY;}
VkResult __wrap_vkDeviceWaitIdle(VkDevice d){(void)d;return VK_SUCCESS;}
void __wrap_vkDestroyDevice(VkDevice d,const VkAllocationCallbacks* a){(void)d;(void)a;++devices;}
VkResult __wrap_vkCreateDescriptorSetLayout(VkDevice d,const VkDescriptorSetLayoutCreateInfo* i,const VkAllocationCallbacks* a,VkDescriptorSetLayout* l)
{
    (void)d;(void)a;unsigned index=layouts++;
    const unsigned counts[]={9,3,5};CHECK(index<3 && i->bindingCount==counts[index]);
    for(unsigned n=0;n<i->bindingCount;++n){
        CHECK(i->pBindings[n].descriptorType!=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER);
        CHECK(i->pBindings[n].binding!=9 && i->pBindings[n].descriptorCount==1);
    }
    *l=VK_NULL_HANDLE;if((int)index==fail_layout)return failure;
    *l=(VkDescriptorSetLayout)(uintptr_t)(60+index);return VK_SUCCESS;
}
void __wrap_vkDestroyDescriptorSetLayout(VkDevice d,VkDescriptorSetLayout l,const VkAllocationCallbacks* a)
{(void)d;(void)l;(void)a;++layout_destroys;}
VkResult __wrap_vkCreateDescriptorPool(VkDevice d,const VkDescriptorPoolCreateInfo* i,const VkAllocationCallbacks* a,VkDescriptorPool* p)
{
    (void)d;(void)a;++descriptor_pools;CHECK(i->poolSizeCount==5 && i->maxSets==41);
    for(unsigned n=0;n<i->poolSizeCount;++n)CHECK(i->pPoolSizes[n].type!=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER);
    *p=VK_NULL_HANDLE;if(fail_descriptor_pool)return failure;
    *p=(VkDescriptorPool)(uintptr_t)80;return VK_SUCCESS;
}
void __wrap_vkDestroyDescriptorPool(VkDevice d,VkDescriptorPool p,const VkAllocationCallbacks* a)
{(void)d;(void)p;(void)a;++descriptor_destroys;}
VkResult __wrap_vkResetDescriptorPool(VkDevice d,VkDescriptorPool p,VkDescriptorPoolResetFlags f)
{(void)d;(void)p;CHECK(!f);++descriptor_resets;return VK_SUCCESS;}
VkResult __wrap_vkAllocateDescriptorSets(VkDevice d,const VkDescriptorSetAllocateInfo* i,VkDescriptorSet* s)
{(void)d;CHECK(i->descriptorSetCount==1);*s=(VkDescriptorSet)(uintptr_t)40;return VK_SUCCESS;}
void __wrap_vkUpdateDescriptorSets(VkDevice d,uint32_t n,const VkWriteDescriptorSet* w,uint32_t c,const VkCopyDescriptorSet* p)
{
    (void)d;(void)c;(void)p;
    for(uint32_t j=0;j<n;++j){
        CHECK(w[j].dstBinding<9 && w[j].descriptorCount==1);
        CHECK(w[j].descriptorType!=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER);++writes[w[j].dstBinding];
        if(w[j].dstBinding==6)CHECK(w[j].pImageInfo->sampler==(VkSampler)(uintptr_t)13);
    }
}
void __wrap_vkCmdPipelineBarrier2(VkCommandBuffer c,const VkDependencyInfo* i)
{(void)c;(void)i;++barriers;}
void __wrap_vkCmdCopyBufferToImage(VkCommandBuffer c,VkBuffer b,VkImage i,VkImageLayout l,uint32_t n,const VkBufferImageCopy* p)
{
    (void)c;(void)b;(void)i;CHECK(l==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL && n==1);
    CHECK(p->imageExtent.width==256 && p->imageExtent.height==1);++copies;
}
static struct walle_vk_renderer* renderer(void)
{
    struct walle_vk_renderer* r=calloc(1,sizeof *r);CHECK(r);
    r->device=(VkDevice)(uintptr_t)1;r->fatal=true;
    atomic_init(&r->validation_error_count,0);
    r->properties.limits.minUniformBufferOffsetAlignment=256;
    buffer_creates=samplers=sampler_destroys=pools=pool_destroys=commands=0;
    fences=fence_destroys=devices=steps=layouts=layout_destroys=0;
    descriptor_pools=descriptor_destroys=descriptor_resets=0;
    memset(writes,0,sizeof writes);return r;
}
static void destroy_checked(struct walle_vk_renderer* r)
{
    struct walle_vk_memory_stats final={};
    CHECK(walle_vk_renderer_destroy_report(r,&final)==0);
    CHECK(!final.allocated_bytes && !final.allocation_count);
    CHECK(samplers==sampler_destroys && pools==pool_destroys && fences==fence_destroys && devices==1);
    CHECK(buffer_creates==0);
}
int main(void)
{
    unsigned resource_cases=0,coordinate_cases=0,layout_cases=0;
    for(int stage=-1;stage<6;++stage)
        for(unsigned lost=0;lost<(stage<0 ? 1u : 2u);++lost){
            snprintf(test_case,sizeof test_case,"allocation-free initialization stage%d lost%u",stage,lost);
            struct walle_vk_renderer* r=renderer();fail_stage=stage;
            failure=lost ? VK_ERROR_DEVICE_LOST : VK_ERROR_OUT_OF_DEVICE_MEMORY;
            bool ok=create_global_resources(r);CHECK(ok==(stage<0));CHECK(r->fatal);
            CHECK(steps==(stage<0 ? 6u : (unsigned)stage+1u));
            if(ok){
                CHECK(samplers==3 && pools==1 && commands==1 && fences==1);
                struct walle_vk_output o={.renderer=r};
                o.frame_buffer.capacity=8192;o.frame_buffer.memory.mapped=calloc(1,8192);CHECK(o.frame_buffer.memory.mapped);
                CHECK(graphics_set(&o,nullptr,(VkImageView)(uintptr_t)1,(VkImageView)(uintptr_t)2));
                for(unsigned n=0;n<9;++n)CHECK(writes[n]==1);
                struct walle_vk_diagnostics diagnostics={};
                CHECK(walle_vk_output_diagnostics(&o,false,&diagnostics));
                CHECK(!diagnostics.shared_math_bytes && !diagnostics.shared_math_memory_flags);
                free(o.frame_buffer.memory.mapped);
            }
            destroy_checked(r);++resource_cases;
        }
    for(int stage=-1;stage<3;++stage){
        snprintf(test_case,sizeof test_case,"descriptor layout stage%d",stage);
        struct walle_vk_renderer* r=renderer();fail_layout=stage;
        CHECK(create_descriptor_layouts(r)==(stage<0));
        CHECK(layouts==(stage<0 ? 3u : (unsigned)stage+1u));
        destroy_checked(r);CHECK(layout_destroys==(stage<0 ? 3u : (unsigned)stage));++layout_cases;
    }
    for(unsigned failure_case=0;failure_case<2;++failure_case){
        snprintf(test_case,sizeof test_case,"descriptor pool failure%u",failure_case);
        struct walle_vk_renderer* r=renderer();struct walle_vk_output o={.renderer=r};
        fail_descriptor_pool=failure_case;CHECK(prepare_pool(&o,41)==!failure_case);
        CHECK(descriptor_pools==1 && o.descriptor_capacity==(failure_case ? 0u : 41u));
        if(!failure_case){
            CHECK(prepare_pool(&o,40) && descriptor_pools==1 && descriptor_resets==1);
            vkDestroyDescriptorPool(r->device,o.descriptor_pool,nullptr);CHECK(descriptor_destroys==1);
        }else CHECK(!o.descriptor_pool);
        destroy_checked(r);++resource_cases;
    }
    snprintf(test_case,sizeof test_case,"canonical ramp cache and exact coordinate gate");
    struct walle_vk_renderer r={};struct walle_vk_output o={.renderer=&r};
    o.ramp.handle=(VkImage)(uintptr_t)50;o.frame_buffer.capacity=8192;
    o.frame_buffer.memory.mapped=calloc(1,8192);CHECK(o.frame_buffer.memory.mapped);
    cached_bytes=o.ramp_bytes;copies=barriers=original_compares=cache_compares=0;
    CHECK(record_ramp(&o,native_tint_ramp));CHECK(o.ramp_native && copies==1 && original_compares==1);
    o.ramp_ready=true;size_t cursor=o.cursor;
    CHECK(record_ramp(&o,native_tint_ramp));
    CHECK(o.cursor==cursor && copies==1 && original_compares==1 && cache_compares==1);
    struct walle_vk_draw d={.pass=WALLE_VK_TINT_GRADIENT};
    memcpy(d.effect+136,native_tint_gradient,sizeof native_tint_gradient);
    CHECK(native_ramp_draw(&o,&d));++coordinate_cases;
    const size_t offsets[]={136,140,148};
    for(unsigned i=0;i<3;++i) {
        d.effect[offsets[i]]^=1;CHECK(!native_ramp_draw(&o,&d));d.effect[offsets[i]]^=1;++coordinate_cases;
    }
    d.effect[139]^=0x80;CHECK(!native_ramp_draw(&o,&d));d.effect[139]^=0x80;++coordinate_cases; /* -0 x */
    memset(d.effect+144,0xff,4);memset(d.effect+152,0xff,8);
    CHECK(native_ramp_draw(&o,&d));++coordinate_cases; /* coverage/colour are independent */
    for(unsigned pass=0;pass<WALLE_VK_PASS_COUNT;++pass) {
        d.pass=(enum walle_vk_pass)pass;
        CHECK(native_ramp_draw(&o,&d)==(pass==WALLE_VK_TINT_GRADIENT));++coordinate_cases;
    }
    d.pass=WALLE_VK_TINT_GRADIENT;
    uint8_t custom[2048];memcpy(custom,native_tint_ramp,sizeof custom);
    for(size_t i=0;i<sizeof custom;++i) {
        custom[i]^=1;o.cursor=0;
        CHECK(record_ramp(&o,custom) && !o.ramp_native && !native_ramp_draw(&o,&d));
        unsigned comparisons=original_compares, uploaded=copies;
        CHECK(record_ramp(&o,custom) && original_compares==comparisons && copies==uploaded);
        custom[i]^=1;o.cursor=0;
        CHECK(record_ramp(&o,custom) && o.ramp_native && native_ramp_draw(&o,&d));
    }
    CHECK(original_compares==1+2*sizeof custom);
    custom[0]^=1;o.cursor=0;o.frame_buffer.capacity=16;
    CHECK(!record_ramp(&o,custom) && o.ramp_native);
    CHECK(!__real_memcmp(o.ramp_bytes,native_tint_ramp,sizeof native_tint_ramp));
    struct glass_push push=graphics_push(257,145,0,0);
    CHECK(push.native_ramp==0 && offsetof(struct glass_push,native_ramp)==20
          && offsetof(struct glass_push,projection_offset)==24 && sizeof push==32);
    free(o.frame_buffer.memory.mapped);
    printf("{\"resource_init_cleanup_cases\":%u,\"descriptor_layout_cases\":%u,\"coordinate_and_pass_cases\":%u,"
           "\"single_byte_custom_ramps\":2048,\"shared_logical_bytes\":0,\"storage_buffer_calls\":0,"
           "\"sampler_contracts\":3,\"descriptor_write_bindings\":9,\"gpu_execution\":false}\n",
           resource_cases,layout_cases,coordinate_cases);
    return 0;
}
