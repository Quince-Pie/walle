#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>

#include <ctype.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *const keywords[] = {
    "clip",       "barycentric", "transformfeedback", "openglmode",
    "raster",     "varying",     "primitive",         "guard",
    "coefficient", "feedback",    "viewport",
};

static bool
contains_keyword(const char *name)
{
    if (name == NULL)
        return false;

    size_t length = strlen(name);
    char *lower = malloc(length + 1);
    if (lower == NULL)
        abort();

    for (size_t index = 0; index < length; ++index)
        lower[index] = (char)tolower((unsigned char)name[index]);
    lower[length] = '\0';

    bool found = false;
    for (size_t index = 0; index < sizeof(keywords) / sizeof(keywords[0]); ++index) {
        if (strstr(lower, keywords[index]) != NULL) {
            found = true;
            break;
        }
    }

    free(lower);
    return found;
}

static void
print_method_list(Class cls, const char *kind)
{
    unsigned int count = 0;
    Method *methods = class_copyMethodList(cls, &count);

    for (unsigned int index = 0; index < count; ++index) {
        Method method = methods[index];
        SEL selector = method_getName(method);
        const char *name = sel_getName(selector);
        if (!contains_keyword(name))
            continue;

        const char *encoding = method_getTypeEncoding(method);
        printf("METHOD\t%s\t%s\t%s\t%s\n", class_getName(cls), kind, name,
               encoding != NULL ? encoding : "<null>");
    }

    free(methods);
}

static void
print_object(const char *label, id object)
{
    if (object == nil) {
        printf("OBJECT\t%s\t<nil>\n", label);
        return;
    }

    printf("OBJECT\t%s\t%s\n", label, object_getClassName(object));
}

static Class
defining_class_for_selector(Class cls, SEL selector)
{
    for (Class cursor = cls; cursor != Nil; cursor = class_getSuperclass(cursor)) {
        unsigned int count = 0;
        Method *methods = class_copyMethodList(cursor, &count);
        for (unsigned int index = 0; index < count; ++index) {
            if (method_getName(methods[index]) == selector) {
                free(methods);
                return cursor;
            }
        }
        free(methods);
    }

    return Nil;
}

static void
probe_selector(const char *label, id object, const char *selector_name)
{
    SEL selector = sel_registerName(selector_name);
    bool responds = object != nil && [object respondsToSelector:selector];
    Class defining_class =
        responds ? defining_class_for_selector(object_getClass(object), selector) : Nil;
    Method method =
        object != nil ? class_getInstanceMethod(object_getClass(object), selector) : NULL;
    const char *encoding = method != NULL ? method_getTypeEncoding(method) : NULL;
    IMP implementation = method != NULL ? method_getImplementation(method) : NULL;
    Dl_info image = {0};
    bool has_image = implementation != NULL && dladdr((const void *)implementation, &image);

    printf("RESPONDS\t%s\t%s\t%d\t%s\t%s\t%s\n", label, selector_name,
           responds ? 1 : 0,
           defining_class != Nil ? class_getName(defining_class) : "<none>",
           encoding != NULL ? encoding : "<none>",
           has_image && image.dli_fname != NULL ? image.dli_fname : "<none>");
}

int
main(void)
{
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            fputs("Metal device unavailable\n", stderr);
            return EXIT_FAILURE;
        }

        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command_buffer = [queue commandBuffer];
        MTLRenderPassDescriptor *render_pass =
            [MTLRenderPassDescriptor renderPassDescriptor];
        MTLTextureDescriptor *texture_descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                              width:4
                                                             height:4
                                                          mipmapped:NO];
        texture_descriptor.usage = MTLTextureUsageRenderTarget;
        id<MTLTexture> texture = [device newTextureWithDescriptor:texture_descriptor];
        render_pass.colorAttachments[0].texture = texture;
        render_pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        render_pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder =
            [command_buffer renderCommandEncoderWithDescriptor:render_pass];
        MTLRenderPipelineDescriptor *pipeline = [MTLRenderPipelineDescriptor new];

        print_object("device", device);
        print_object("queue", queue);
        print_object("command-buffer", command_buffer);
        print_object("render-pass", render_pass);
        print_object("texture", texture);
        print_object("encoder", encoder);
        print_object("pipeline-descriptor", pipeline);

        static const char *const selectors[] = {
            "setClipPlane:p2:p3:p4:atIndex:",
            "setTransformFeedbackState:",
            "setDepthClipMode:",
            "setDepthClipModeSPI:",
            "setViewportTransformEnabled:",
            "setOpenGLModeEnabled:",
            "setClipDistanceEnableMask:",
            "setRasterizationEnabled:",
        };
        const struct {
            const char *label;
            __unsafe_unretained id object;
        } objects[] = {
            {"device", device},
            {"queue", queue},
            {"command-buffer", command_buffer},
            {"render-pass", render_pass},
            {"encoder", encoder},
            {"pipeline-descriptor", pipeline},
        };
        for (size_t object_index = 0;
             object_index < sizeof(objects) / sizeof(objects[0]); ++object_index) {
            for (size_t selector_index = 0;
                 selector_index < sizeof(selectors) / sizeof(selectors[0]);
                 ++selector_index) {
                probe_selector(objects[object_index].label,
                               objects[object_index].object,
                               selectors[selector_index]);
            }
        }

        int class_count = objc_getClassList(NULL, 0);
        if (class_count <= 0)
            return EXIT_FAILURE;

        Class __unsafe_unretained *classes =
            (Class __unsafe_unretained *)calloc((size_t)class_count,
                                                sizeof(*classes));
        if (classes == NULL)
            abort();
        class_count = objc_getClassList(classes, class_count);

        for (int index = 0; index < class_count; ++index) {
            Class cls = classes[index];
            const char *class_name = class_getName(cls);
            if (class_name == NULL ||
                (strstr(class_name, "AGX") == NULL &&
                 strstr(class_name, "MTL") == NULL &&
                 strstr(class_name, "IOGPU") == NULL))
                continue;

            print_method_list(cls, "instance");
            Class metaclass = object_getClass((id)cls);
            if (metaclass != Nil)
                print_method_list(metaclass, "class");
        }

        free(classes);
        [encoder endEncoding];
        [command_buffer commit];
        [command_buffer waitUntilCompleted];
    }

    return EXIT_SUCCESS;
}
