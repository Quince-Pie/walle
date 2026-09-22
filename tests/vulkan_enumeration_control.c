#include <stdio.h>
#include <stdlib.h>
#include <vulkan/vulkan.h>

int main(int argc, char** argv)
{
    unsigned repeats = argc > 1 ? (unsigned)strtoul(argv[1], nullptr, 10) : 1;
    if (!repeats || repeats > 100)
        return 2;
    for (unsigned i = 0; i < repeats; ++i) {
        const char* layer = "VK_LAYER_KHRONOS_validation";
        const VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                                        .apiVersion = VK_API_VERSION_1_4};
        const VkInstanceCreateInfo info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                                           .pApplicationInfo = &app,
                                           .enabledLayerCount = 1,
                                           .ppEnabledLayerNames = &layer};
        VkInstance instance = VK_NULL_HANDLE;
        if (vkCreateInstance(&info, nullptr, &instance) != VK_SUCCESS)
            return 1;
        uint32_t count = 0;
        VkResult result = vkEnumeratePhysicalDevices(instance, &count, nullptr);
        vkDestroyInstance(instance, nullptr);
        if (result != VK_SUCCESS || count == 0)
            return 1;
    }
    printf("Created, enumerated and destroyed %u Vulkan instances.\n", repeats);
    return 0;
}
