#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

@interface MTLRenderPipelineDescriptor (WallePrivateClipDistance)
- (void)setClipDistanceEnableMask:(uint8_t)mask;
@end

static id<MTLRenderPipelineState>
make_pipeline(id<MTLDevice> device, uint8_t clip_mask, NSError **error)
{
    static NSString *const source =
        @"#include <metal_stdlib>\n"
         "using namespace metal;\n"
         "struct VertexOut {\n"
         "    float4 position [[position]];\n"
         "    float clip [[clip_distance]];\n"
         "};\n"
         "vertex VertexOut vertex_main(uint id [[vertex_id]],\n"
         "                             constant float *distances [[buffer(0)]])\n"
         "{\n"
         "    const float2 positions[3] = {\n"
         "        float2(-1.0f, -1.0f),\n"
         "        float2( 3.0f, -1.0f),\n"
         "        float2(-1.0f,  3.0f)\n"
         "    };\n"
         "    VertexOut out;\n"
         "    out.position = float4(positions[id], 0.5f, 1.0f);\n"
         "    out.clip = distances[id];\n"
         "    return out;\n"
         "}\n"
         "fragment uint fragment_main() { return 0xffffffffu; }\n";

    id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:error];
    if (library == nil)
        return nil;

    MTLRenderPipelineDescriptor *descriptor = [MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction = [library newFunctionWithName:@"vertex_main"];
    descriptor.fragmentFunction = [library newFunctionWithName:@"fragment_main"];
    descriptor.colorAttachments[0].pixelFormat = MTLPixelFormatR32Uint;
    [descriptor setClipDistanceEnableMask:clip_mask];
    return [device newRenderPipelineStateWithDescriptor:descriptor error:error];
}

static size_t
render_and_count(id<MTLDevice> device, id<MTLCommandQueue> queue,
                 id<MTLRenderPipelineState> pipeline,
                 const float distances[static 3])
{
    enum { width = 64, height = 64 };
    MTLTextureDescriptor *texture_descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR32Uint
                                                          width:width
                                                         height:height
                                                      mipmapped:NO];
    texture_descriptor.usage = MTLTextureUsageRenderTarget;
    texture_descriptor.storageMode = MTLStorageModeShared;
    id<MTLTexture> texture = [device newTextureWithDescriptor:texture_descriptor];
    id<MTLBuffer> distance_buffer =
        [device newBufferWithBytes:distances
                           length:3 * sizeof(*distances)
                          options:MTLResourceStorageModeShared];

    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = texture;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0.0, 0.0, 0.0, 0.0);

    id<MTLCommandBuffer> command_buffer = [queue commandBuffer];
    id<MTLRenderCommandEncoder> encoder =
        [command_buffer renderCommandEncoderWithDescriptor:pass];
    [encoder setRenderPipelineState:pipeline];
    [encoder setVertexBuffer:distance_buffer offset:0 atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [encoder endEncoding];
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    if (command_buffer.status != MTLCommandBufferStatusCompleted) {
        fprintf(stderr, "command buffer failed: %s\n",
                command_buffer.error.localizedDescription.UTF8String);
        exit(EXIT_FAILURE);
    }

    uint32_t pixels[width * height];
    [texture getBytes:pixels
          bytesPerRow:width * sizeof(*pixels)
           fromRegion:MTLRegionMake2D(0, 0, width, height)
          mipmapLevel:0];

    size_t covered = 0;
    for (size_t index = 0; index < width * height; ++index)
        covered += pixels[index] != 0;
    return covered;
}

int
main(void)
{
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> queue = [device newCommandQueue];
        if (device == nil || queue == nil)
            return EXIT_FAILURE;

        NSError *error = nil;
        id<MTLRenderPipelineState> disabled = make_pipeline(device, 0, &error);
        if (disabled == nil) {
            fprintf(stderr, "disabled pipeline: %s\n",
                    error.localizedDescription.UTF8String);
            return EXIT_FAILURE;
        }
        id<MTLRenderPipelineState> enabled = make_pipeline(device, 1, &error);
        if (enabled == nil) {
            fprintf(stderr, "enabled pipeline: %s\n",
                    error.localizedDescription.UTF8String);
            return EXIT_FAILURE;
        }

        const float all_inside[3] = {1.0f, 1.0f, 1.0f};
        const float one_outside[3] = {-1.0f, 1.0f, 1.0f};
        const float all_outside[3] = {-1.0f, -1.0f, -1.0f};
        printf("disabled-one-outside\t%zu\n",
               render_and_count(device, queue, disabled, one_outside));
        printf("enabled-all-inside\t%zu\n",
               render_and_count(device, queue, enabled, all_inside));
        printf("enabled-one-outside\t%zu\n",
               render_and_count(device, queue, enabled, one_outside));
        printf("enabled-all-outside\t%zu\n",
               render_and_count(device, queue, enabled, all_outside));
    }

    return EXIT_SUCCESS;
}
