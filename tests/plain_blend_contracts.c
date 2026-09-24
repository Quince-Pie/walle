#define _GNU_SOURCE
#include "../vulkan_renderer.c"
#include "transition_check.h"

static bool expected_fma, expected_blend, fail_gate_allocation;
static int fail_module, fail_pipeline;
static unsigned module_calls, pipeline_calls, live_modules, live_pipelines, graphics_count, compute_count;
static unsigned gate_mode, gate_queries, gate_allocations, gate_frees;
static VkFormatFeatureFlags2 optimal_flags;
static VkDrmFormatModifierProperties2EXT modifier_flags[3];
static uint32_t modifier_count;
static bool module_live[64],pipeline_live[64];
void* __real_calloc(size_t,size_t);
void __real_free(void*);
static void* gate_allocation;
void* __wrap_calloc(size_t n,size_t s)
{
    if(s==sizeof(VkDrmFormatModifierProperties2EXT)){
        ++gate_allocations;if(fail_gate_allocation)return nullptr;
        gate_allocation=__real_calloc(n,s);return gate_allocation;
    }
    return __real_calloc(n,s);
}
void __wrap_free(void* p){if(p && p==gate_allocation){++gate_frees;gate_allocation=nullptr;}__real_free(p);}
void __wrap_vkGetPhysicalDeviceFormatProperties2(VkPhysicalDevice d,VkFormat f,VkFormatProperties2* p)
{
    (void)d;CHECK(f==WALLE_VK_PRESENT_FORMAT);VkBaseOutStructure* next=p->pNext;
    if(next->sType==VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3){((VkFormatProperties3*)next)->optimalTilingFeatures=optimal_flags;return;}
    CHECK(next->sType==VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_2_EXT);
    VkDrmFormatModifierPropertiesList2EXT* list=(void*)next;++gate_queries;
    if(list->pDrmFormatModifierProperties){
        CHECK(list->drmFormatModifierCount==modifier_count);
        memcpy(list->pDrmFormatModifierProperties,modifier_flags,modifier_count*sizeof modifier_flags[0]);
        list->drmFormatModifierCount=gate_mode==1 ? modifier_count-1 : modifier_count;
    }else list->drmFormatModifierCount=gate_mode==2 && gate_queries==3 ? modifier_count+1 : modifier_count;
}
static bool plain(const char* name)
{
    const char* names[]={"regularFragment","clearFragment","regularCachedFragment","clearCachedFragment",
        "tintCompositeFragment","revealFragment","productFinishFragment"};
    for(unsigned i=0;i<7;++i)if(!strcmp(name,names[i]))return true;
    return false;
}
static bool local(const char* n)
{
    return plain(n)||!strcmp(n,"faceFragment")||!strcmp(n,"highlightFragment")
        ||!strcmp(n,"highlightCachedFragment")||!strcmp(n,"tintApplyMaskFragment");
}
VkResult __wrap_vkCreateShaderModule(VkDevice d,const VkShaderModuleCreateInfo* i,const VkAllocationCallbacks* a,VkShaderModule* m)
{
    (void)d;(void)a;CHECK(i->codeSize>4 && i->codeSize%4==0);unsigned index=module_calls++;
    CHECK(index<64);*m=VK_NULL_HANDLE;if((int)index==fail_module)return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    module_live[index]=true;++live_modules;*m=(VkShaderModule)(uintptr_t)(100+index);return VK_SUCCESS;
}
void __wrap_vkDestroyShaderModule(VkDevice d,VkShaderModule m,const VkAllocationCallbacks* a)
{(void)d;(void)a;unsigned i=(unsigned)((uintptr_t)m-100);CHECK(i<64 && module_live[i]);module_live[i]=false;--live_modules;}
static void specialization(const VkPipelineShaderStageCreateInfo* stage,bool graphics)
{
    const VkSpecializationInfo* s=stage->pSpecializationInfo;CHECK(s && s->mapEntryCount==(graphics ? 2u : 1u));
    CHECK(s->dataSize==(graphics ? 2u : 1u)*sizeof(VkBool32));
    for(unsigned i=0;i<s->mapEntryCount;++i){
        CHECK(s->pMapEntries[i].constantID==100+i && s->pMapEntries[i].offset==i*sizeof(VkBool32)
            && s->pMapEntries[i].size==sizeof(VkBool32));
        VkBool32 value;memcpy(&value,(const char*)s->pData+i*sizeof value,sizeof value);
        CHECK(value==(i==0 ? expected_fma : expected_blend));
    }
}
static VkResult new_pipeline(VkPipeline* pipeline)
{
    unsigned i=pipeline_calls++;CHECK(i<64);*pipeline=VK_NULL_HANDLE;
    if((int)i==fail_pipeline)return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    pipeline_live[i]=true;++live_pipelines;*pipeline=(VkPipeline)(uintptr_t)(1000+i);return VK_SUCCESS;
}
VkResult __wrap_vkCreateGraphicsPipelines(VkDevice d,VkPipelineCache c,uint32_t n,const VkGraphicsPipelineCreateInfo* i,const VkAllocationCallbacks* a,VkPipeline* p)
{
    (void)d;(void)c;(void)a;CHECK(n==1);specialization(&i->pStages[1],true);
    const char* name=i->pStages[1].pName;bool tint=!strcmp(name,"tintGradientFragment");
    const VkPipelineColorBlendAttachmentState* blend=i->pColorBlendState->pAttachments;
    CHECK(blend->blendEnable==(expected_blend && plain(name)));
    CHECK(blend->srcColorBlendFactor==VK_BLEND_FACTOR_ONE && blend->srcAlphaBlendFactor==VK_BLEND_FACTOR_ONE);
    CHECK(blend->dstColorBlendFactor==VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA && blend->dstAlphaBlendFactor==VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA);
    CHECK(blend->colorBlendOp==VK_BLEND_OP_ADD && blend->alphaBlendOp==VK_BLEND_OP_ADD && blend->colorWriteMask==15);
    const VkPipelineRenderingCreateInfo* render=i->pNext;
    const VkRenderingAttachmentLocationInfo* locations=render->pNext;
    const VkRenderingInputAttachmentIndexInfo* inputs=locations->pNext;
    unsigned count=tint || RETAIN_DISCARD_ATTACHMENT ? 2u : 1u;
    CHECK(render->colorAttachmentCount==count && i->pColorBlendState->attachmentCount==count);
    CHECK(locations->pColorAttachmentLocations[0]==0);
    CHECK(inputs->pColorAttachmentInputIndices[0]==(!tint && local(name) ? 0u : VK_ATTACHMENT_UNUSED));
    if(count==2){CHECK(locations->pColorAttachmentLocations[1]==VK_ATTACHMENT_UNUSED);
        CHECK(inputs->pColorAttachmentInputIndices[1]==(tint ? 0u : VK_ATTACHMENT_UNUSED));
        CHECK(!blend[1].blendEnable && !blend[1].colorWriteMask);}
    if(plain(name))CHECK(render->pColorAttachmentFormats[0]==VK_FORMAT_B8G8R8A8_UNORM);
    ++graphics_count;return new_pipeline(p);
}
VkResult __wrap_vkCreateComputePipelines(VkDevice d,VkPipelineCache c,uint32_t n,const VkComputePipelineCreateInfo* i,const VkAllocationCallbacks* a,VkPipeline* p)
{(void)d;(void)c;(void)a;CHECK(n==1);specialization(&i->stage,false);++compute_count;return new_pipeline(p);}
void __wrap_vkDestroyPipeline(VkDevice d,VkPipeline p,const VkAllocationCallbacks* a)
{(void)d;(void)a;unsigned i=(unsigned)((uintptr_t)p-1000);CHECK(i<64 && pipeline_live[i]);pipeline_live[i]=false;--live_pipelines;}
VkResult __wrap_vkDeviceWaitIdle(VkDevice d){(void)d;return VK_SUCCESS;}
void __wrap_vkDestroyDevice(VkDevice d,const VkAllocationCallbacks* a){(void)d;(void)a;}
static void pipeline_case(int module,int pipeline)
{
    struct walle_vk_renderer* r=calloc(1,sizeof *r);CHECK(r);atomic_init(&r->validation_error_count,0);
    r->device=(VkDevice)(uintptr_t)1;r->native_half_fma=expected_fma;r->hardware_source_over=expected_blend;
    module_calls=pipeline_calls=live_modules=live_pipelines=graphics_count=compute_count=0;
    memset(module_live,0,sizeof module_live);memset(pipeline_live,0,sizeof pipeline_live);
    fail_module=module;fail_pipeline=pipeline;
    CHECK(create_pipelines(r)==(module<0 && pipeline<0));CHECK(!live_modules);
    if(module<0 && pipeline<0)CHECK(graphics_count==24 && compute_count==2 && module_calls==50);
    struct walle_vk_memory_stats stats={};CHECK(!walle_vk_renderer_destroy_report(r,&stats));
    CHECK(!live_modules && !live_pipelines && !stats.allocated_bytes && !stats.allocation_count);
}
int main(void)
{
    unsigned gates=0,pipelines=0;
    for(unsigned mode=0;mode<9;++mode){
        struct walle_vk_renderer r={.physical_device=(VkPhysicalDevice)(uintptr_t)1,.display=(void*)(uintptr_t)1};
        optimal_flags=VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT|VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BLEND_BIT;
        modifier_count=3;gate_mode=0;gate_queries=gate_allocations=gate_frees=0;fail_gate_allocation=false;
        for(unsigned i=0;i<3;++i)modifier_flags[i]=(VkDrmFormatModifierProperties2EXT){.drmFormatModifier=i,
            .drmFormatModifierTilingFeatures=optimal_flags,.drmFormatModifierPlaneCount=1};
        bool expected=true;
        switch(mode){case 0:break;case 1:r.display=nullptr;break;
            case 2:optimal_flags=VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT;expected=false;break;
            case 3:modifier_flags[2].drmFormatModifierTilingFeatures=VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BIT;expected=false;break;
            case 4:modifier_flags[2].drmFormatModifierTilingFeatures=VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_BIT;break;
            case 5:modifier_count=0;expected=false;break;case 6:fail_gate_allocation=true;expected=false;break;
            case 7:gate_mode=1;expected=false;break;case 8:gate_mode=2;expected=false;break;}
        CHECK(hardware_source_over_supported(&r)==expected);CHECK(!gate_allocation && !r.fatal);
        CHECK(gate_frees==gate_allocations-(fail_gate_allocation ? 1u : 0u));++gates;
    }
    fail_gate_allocation=false;
    for(unsigned blend=0;blend<2;++blend)for(unsigned fma=0;fma<2;++fma){
        expected_blend=blend;expected_fma=fma;pipeline_case(-1,-1);++pipelines;
        for(int i=0;i<50;++i){pipeline_case(i,-1);++pipelines;}
        for(int i=0;i<26;++i){pipeline_case(-1,i);++pipelines;}
    }
    printf("{\"capability_gate_cases\":%u,\"pipeline_success_failure_cases\":%u,\"graphics_pipelines\":24,\"blend_and_fma_states\":4,\"gpu_execution\":false}\n",gates,pipelines);
    return 0;
}
