#define _GNU_SOURCE

#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

VKAPI_ATTR VkResult VKAPI_CALL vkAcquireNextImageKHR(VkDevice       device,
                                                     VkSwapchainKHR swapchain,
                                                     uint64_t       timeout,
                                                     VkSemaphore    semaphore,
                                                     VkFence        fence,
                                                     uint32_t*      image_index)
{
    static PFN_vkAcquireNextImageKHR acquire;
    static bool                      injected;
    if (!acquire) {
        void* symbol = dlsym(RTLD_NEXT, "vkAcquireNextImageKHR");
        static_assert(sizeof symbol == sizeof acquire);
        memcpy(&acquire, &symbol, sizeof acquire);
    }
    if (!acquire)
        return VK_ERROR_INITIALIZATION_FAILED;

    const char* forced_result = getenv("WALLE_TEST_VK_ACQUIRE_RESULT");
    if (!injected && forced_result && strcmp(forced_result, "OUT_OF_DATE") == 0) {
        injected = true;
        return VK_ERROR_OUT_OF_DATE_KHR;
    }

    VkResult result = acquire(device, swapchain, timeout, semaphore, fence, image_index);
    if (!injected && result == VK_SUCCESS && forced_result
        && strcmp(forced_result, "SUBOPTIMAL") == 0) {
        injected = true;
        return VK_SUBOPTIMAL_KHR;
    }
    return result;
}
