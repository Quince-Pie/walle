#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

static PFN_vkCreateDevice next_device;
static PFN_vkDestroyDevice next_destroy_device;
static PFN_vkCreateBuffer next_buffer;
static PFN_vkAllocateMemory next_allocate;
static _Thread_local bool table_pending;
static _Atomic unsigned devices_created,devices_destroyed,table_buffers,injections;
static int trace_fd=-1;
static void emit(const char* text)
{ if(trace_fd>=0)(void)syscall(SYS_write,trace_fd,text,strlen(text)); }
[[gnu::constructor]] static void initialize(void)
{
    void* symbol=dlsym(RTLD_NEXT,"vkCreateDevice");memcpy(&next_device,&symbol,sizeof symbol);
    symbol=dlsym(RTLD_NEXT,"vkDestroyDevice");memcpy(&next_destroy_device,&symbol,sizeof symbol);
    symbol=dlsym(RTLD_NEXT,"vkCreateBuffer");memcpy(&next_buffer,&symbol,sizeof symbol);
    symbol=dlsym(RTLD_NEXT,"vkAllocateMemory");memcpy(&next_allocate,&symbol,sizeof symbol);
    const char* name=getenv("WALLE_MATH_FAIL_TRACE");
    if(name)trace_fd=open(name,O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC,0600);
}
VKAPI_ATTR VkResult VKAPI_CALL vkCreateDevice(VkPhysicalDevice physical,
    const VkDeviceCreateInfo* info,const VkAllocationCallbacks* callbacks,VkDevice* device)
{
    VkResult status=next_device?next_device(physical,info,callbacks,device):VK_ERROR_INITIALIZATION_FAILED;
    if(status==VK_SUCCESS)atomic_fetch_add(&devices_created,1);
    return status;
}
VKAPI_ATTR void VKAPI_CALL vkDestroyDevice(VkDevice device,const VkAllocationCallbacks* callbacks)
{
    if(next_destroy_device)next_destroy_device(device,callbacks);
    atomic_fetch_add(&devices_destroyed,1);
}
VKAPI_ATTR VkResult VKAPI_CALL vkCreateBuffer(VkDevice device,const VkBufferCreateInfo* info,
    const VkAllocationCallbacks* callbacks,VkBuffer* buffer)
{
    VkResult status=next_buffer?next_buffer(device,info,callbacks,buffer):VK_ERROR_INITIALIZATION_FAILED;
    if(status==VK_SUCCESS && info->usage==VK_BUFFER_USAGE_STORAGE_BUFFER_BIT){
        /* The only pure storage buffer in Walle is the renderer-shared native
         * function resource. Match its role, not a representation-dependent size. */
        table_pending=true;atomic_fetch_add(&table_buffers,1);
        char line[128];snprintf(line,sizeof line,"{\"event\":\"function_buffer\",\"bytes\":%llu}\n",(unsigned long long)info->size);emit(line);
    }
    return status;
}
VKAPI_ATTR VkResult VKAPI_CALL vkAllocateMemory(VkDevice device,const VkMemoryAllocateInfo* info,
    const VkAllocationCallbacks* callbacks,VkDeviceMemory* memory)
{
    bool reject=table_pending;table_pending=false;
    if(reject && atomic_fetch_add(&injections,1)==0){
        char line[160];snprintf(line,sizeof line,
            "{\"event\":\"math_memory_rejected\",\"bytes\":%llu,\"memory_type\":%u}\n",
            (unsigned long long)info->allocationSize,info->memoryTypeIndex);emit(line);
        *memory=VK_NULL_HANDLE;return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    return next_allocate?next_allocate(device,info,callbacks,memory):VK_ERROR_INITIALIZATION_FAILED;
}
[[gnu::destructor]] static void summarize(void)
{
    char line[192];snprintf(line,sizeof line,
        "{\"event\":\"summary\",\"devices_created\":%u,\"devices_destroyed\":%u,\"table_buffers\":%u,\"injections\":%u}\n",
        atomic_load(&devices_created),atomic_load(&devices_destroyed),atomic_load(&table_buffers),atomic_load(&injections));emit(line);
}
