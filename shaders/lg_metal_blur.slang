#language slang 2026

/// Blur-pyramid entry points with QuartzCore's binding layout. One compile per kernel:
///   -DLG_KERNEL=1   base-mip copy + first blurred level (variable_blur_copy_base_mip_compute)
///   -DLG_KERNEL=2   one blurred level from the previous one (variable_blur_downsample_compute): the kernel
///                   QuartzCore binds on GPUs that support MTLGPUFamilyApple9, dispatched with 16x32 threads
///                   (-DLG_TG_H=16 for 16x16)
///   -DLG_KERNEL=4   the same, as variable_blur_downsample_compute_agx2 (16x16 threads per 16x32 tile): the
///                   kernel QuartzCore binds when MTLGPUFamilyApple9 is not supported (the M1 Max)
///   (new_metal_context @0x18a832ea4 sets MetalContext+0xe35 bit 0x40 = supportsFamily(MTLGPUFamilyApple9);
///    create_variable_blur_mip_surface 0x18a81fa5c..68 picks ComputeShaderType 6 (non-agx2) or 7 (agx2) from it)
///   -DLG_KERNEL=3   backdrop capture (downsample_blur_vert + downsample_4_frag), with -DLG_HALF=1|0; -DLG_TAPS=6|8
///                   selects downsample_6_frag / downsample_8_frag (texture functions 28 / 29) instead of 4 (27)
/// Compute outputs are single-mip views of the pyramid texture (QuartzCore writes the level through
/// an imageblock slice instead); texture(0) is the input, texture(1)/(2) the output views, buffer(0)
/// the uniforms, sampler(0) linear filtering / nearest mip / clamp to edge.
module lg_metal_blur;

import liquidglass;
#ifndef LG_NATIVE_UNORM8_INPUT
#define LG_NATIVE_UNORM8_INPUT 0
#endif


// Vulkan transport ABI widens narrow host integers; the original kernels
// perform their addressing in 32-bit integers. Host constructors retain the
// native signed16 base/clamp and unsigned16 destination ranges; renderer limits
// come from actual device image, framebuffer and viewport capabilities.
struct VulkanBlurCopyBaseMipUniforms {
 int2 tex_coord_base; int2 pad0; int4 tex_coord_clamp;
 uint2 dst_0_size; uint2 dst_1_size; uint dst_1_level; uint no_base_mip;
}
struct VulkanBlurDownsampleUniforms {
 uint src_level; uint dst_level; uint dst_w; uint dst_h;
 float dst_dx; float dst_dy;
}

#if LG_KERNEL == 1

[[vk::image_format("rgba8")]] Texture2D<half4> textureIn : register(t0);
[[vk::image_format("rgba8")]] WTexture2D<half4> baseOut : register(u1);
[[vk::image_format("rgba8")]] WTexture2D<half4> blurOut : register(u2);
ConstantBuffer<VulkanBlurCopyBaseMipUniforms> u : register(b0);
groupshared half4 boxes[400];
half4 readBlurInput(int2 coordinate)
{
    half4 value=textureIn.Load(int3(coordinate,0));
#if LG_NATIVE_UNORM8_INPUT
    return unorm8TexelHalf(value);
#else
    return value;
#endif
}


/// Each 20x20 group covers a 32x32 tile of mip 0 plus a 4-texel apron (as 2x2 boxes), and
/// writes the 16x16 tile of the blurred level.
[shader("compute")]
[numthreads(20, 20, 1)]
void lg_blur_copy_base_mip(uint3 tid3 : SV_GroupThreadID, uint3 tg3 : SV_GroupID)
{
    let tid = int2(tid3.xy);
    let p = tid * 2 - 4;
    let src = p + int2(tg3.xy) * 32 + int2(u.tex_coord_base);
    let lo = int2(u.tex_coord_clamp.xy);
    let hi = int2(u.tex_coord_clamp.zw);
    let a = readBlurInput(clamp(src, lo, hi));
    let b = readBlurInput(clamp(src + int2(1, 0), lo, hi));
    let c = readBlurInput(clamp(src + int2(0, 1), lo, hi));
    let d = readBlurInput(clamp(src + int2(1, 1), lo, hi));
    let row = tid.y * 20 + tid.x;
    boxes[row] = box2x2(a, b, c, d);

    let extent = min(uint2(32), uint2(u.dst_0_size) - tg3.xy * 32);
    if (!u.no_base_mip && uint(p.x) < extent.x && uint(p.y) < extent.y)
    {
        let o = int2(tg3.xy) * 32 + p;
        baseOut.Store(uint2(o), a);
        baseOut.Store(uint2(o + int2(1, 0)), b);
        baseOut.Store(uint2(o + int2(0, 1)), c);
        baseOut.Store(uint2(o + int2(1, 1)), d);
    }
    GroupMemoryBarrierWithGroupSync();

    let half_extent = extent >> 1;
    if (uint(tid.x) < half_extent.x && uint(tid.y) < half_extent.y)
    {
        let i = row;
        let v = diamond13(boxes[i + 42], boxes[i + 41], boxes[i + 22], boxes[i + 43], boxes[i + 62],
                          boxes[i + 21], boxes[i + 23], boxes[i + 61], boxes[i + 63],
                          boxes[i + 40], boxes[i + 2], boxes[i + 44], boxes[i + 82]);
        blurOut.Store(tg3.xy * 16 + uint2(tid), v);
    }
}

#elif LG_KERNEL == 2

[[vk::image_format("rgba8")]] Texture2D<half4> textureIn : register(t0);
[[vk::image_format("rgba8")]] WTexture2D<half4> textureOut : register(u1);
SamplerState boxSampler : register(s0);
ConstantBuffer<VulkanBlurDownsampleUniforms> u : register(b0);
groupshared half4 boxes[720];

half4 box(float2 uv)
{
    #if LG_NATIVE_UNORM8_INPUT
    return sampleGlassBackdrop<half,true>(textureIn,boxSampler,uv,half(u.src_level));
#else
    return textureIn.SampleLevel(boxSampler,uv,float(u.src_level));
#endif
}

// variable_blur_downsample_compute reads its tile size from threads_per_threadgroup. QuartzCore binds it only on
// GPUs that support MTLGPUFamilyApple9, and dispatches it there with 16x32 threads per group
// (create_variable_blur_mip_surface 0x18a81faf4..b00). -DLG_TG_H=16 gives the 16x16 variant used by the
// earlier differential tests.
#ifndef LG_TG_H
#define LG_TG_H 32
#endif

/// Each 16xLG_TG_H group writes a 16xLG_TG_H tile of level dst_level. A 2x2 box of the source level is one
/// bilinear sample at the corner of four source texels (the centre of a destination texel).
[shader("compute")]
[numthreads(16, LG_TG_H, 1)]
void lg_blur_downsample(uint3 gid3 : SV_DispatchThreadID, uint3 tid3 : SV_GroupThreadID, uint3 tg3 : SV_GroupID)
{
    let tgSize = uint2(16, LG_TG_H);
    let dst = uint2(u.dst_w, u.dst_h);
    let origin = tg3.xy * tgSize;
    let tile = min(tgSize, dst - origin);
    let d = float2(u.dst_dx, u.dst_dy);
    half4 v = half4(0.0h);
    let inside = gid3.x < dst.x && gid3.y < dst.y;
    if (tile.x == 1 && tile.y == 1)
    {
        let c = d * (float2(gid3.xy) + 0.5f);
        v = diamond13Single(box(c), box(c + float2(-d.x, 0.0f)), box(c + float2(0.0f, -d.y)), box(c + float2(d.x, 0.0f)), box(c + float2(0.0f, d.y)),
                            box(c + float2(-d.x, -d.y)), box(c + float2(d.x, -d.y)), box(c + float2(-d.x, d.y)), box(c + d),
                            box(c + float2(d.x * -2.0f, 0.0f)), box(c + float2(0.0f, d.y * -2.0f)), box(c + float2(d.x * 2.0f, 0.0f)), box(c + float2(0.0f, d.y * 2.0f)));
    }
    else
    {
        let stride = tile.x + 4;
        let t2 = tid3.xy * 2;
        if (t2.x < tile.x + 4 && t2.y < tile.y + 4)
        {
            let uv = d * (float2(origin + t2) + -1.5f);
            let uvx = uv + float2(d.x, 0.0f);
            let i = stride * t2.y + t2.x;
            boxes[i] = box(uv);
            boxes[i + 1] = box(uvx);
            boxes[i + stride] = box(uv + float2(0.0f, d.y));
            boxes[i + stride + 1] = box(uvx + float2(0.0f, d.y));
        }
        GroupMemoryBarrierWithGroupSync();
        if (inside)
        {
            let r0 = stride * tid3.y + tid3.x;
            let r1 = r0 + stride;
            let r2 = r1 + stride;
            let r3 = r2 + stride;
            let r4 = r3 + stride;
            v = diamond13(boxes[r2 + 2], boxes[r2 + 1], boxes[r1 + 2], boxes[r2 + 3], boxes[r3 + 2],
                          boxes[r1 + 1], boxes[r1 + 3], boxes[r3 + 1], boxes[r3 + 3],
                          boxes[r2], boxes[r0 + 2], boxes[r2 + 4], boxes[r4 + 2]);
        }
    }
    if (inside)
        textureOut.Store(gid3.xy, v);
}

#elif LG_KERNEL == 4

// variable_blur_downsample_compute_agx2: the per-level kernel QuartzCore binds on the M1 Max (and the one the
// dispatch geometry ((w+15)>>4, (h+31)>>5) groups of 16x16 threads is sized for). Each group writes a 16x32
// tile of level dst_level from a 20x36 cache of 2x2 boxes (the tile plus a 2-texel apron), filled in two
// phases, and runs the 13-tap diamond twice per thread (rows tid.y and tid.y + 16). A tile smaller than
// 16x32 fills only its valid size plus the apron and writes only texels inside the level.
[[vk::image_format("rgba8")]] Texture2D<half4> textureIn : register(t0);
[[vk::image_format("rgba8")]] WTexture2D<half4> textureOut : register(u1);
SamplerState boxSampler : register(s0);        // linear filtering, nearest mip, clamp to edge (0x7bff0000082a49)
ConstantBuffer<VulkanBlurDownsampleUniforms> u : register(b0);
groupshared half4 cache[720];

half4 boxAt(float2 uv)
{
    #if LG_NATIVE_UNORM8_INPUT
    return sampleGlassBackdrop<half,true>(textureIn,boxSampler,uv,half(u.src_level));
#else
    return textureIn.SampleLevel(boxSampler,uv,float(u.src_level));
#endif
}

half4 diamondAt(uint i)
{
    return diamond13Unflushed(cache[i + 42], cache[i + 41], cache[i + 22], cache[i + 43], cache[i + 62],
                     cache[i + 21], cache[i + 23], cache[i + 61], cache[i + 63],
                     cache[i + 40], cache[i + 2], cache[i + 44], cache[i + 82]);
}

[shader("compute")]
[numthreads(16, 16, 1)]
void lg_blur_downsample_agx2(uint3 tid3 : SV_GroupThreadID, uint3 tg3 : SV_GroupID)
{
    let tid = tid3.xy;
    let dst = uint2(u.dst_w, u.dst_h);
    let origin = tg3.xy * uint2(16, 32);
    let size = min(uint2(16, 32), dst - origin);
    let full = size.x == 16 && size.y == 32;
    let lim = size + 4;
    let e = (tid.y * 16 + tid.x) * 2;             // this thread fills cache entries e and e + 1 (same row)
    let row = e / 20;
    let col = e % 20;
    let d = float2(u.dst_dx, u.dst_dy);
    let uv = d * (float2(uint2(col, row) + origin) + -1.5f);
    let colOk = full || col < lim.x;
    if (e < 400 && colOk && (full || row < lim.y))
    {
        cache[e] = boxAt(uv);
        cache[e + 1] = boxAt(uv + float2(d.x, 0.0f));
    }
    GroupMemoryBarrierWithGroupSync();
    if (e < 320 && colOk && (full || row + 20 < lim.y))
    {
        let dy20 = d.y * 20.0f;
        cache[e + 400] = boxAt(uv + float2(0.0f, dy20));
        cache[e + 401] = boxAt(uv + float2(d.x, dy20));
    }
    let i = tid.y * 20 + tid.x;
    let p0 = origin + tid;
    if (full || (p0.x < dst.x && p0.y < dst.y))
        textureOut.Store(p0, diamondAt(i));
    GroupMemoryBarrierWithGroupSync();
    let p1 = p0 + uint2(0, 16);
    if (full || (p1.x < dst.x && p1.y < dst.y))
        textureOut.Store(p1, diamondAt(i + 320));
}

#elif LG_KERNEL == 3

#if LG_HALF
typealias Pixel = half;
#else
typealias Pixel = float;
#endif

struct CaptureVaryings
{
    float4 position : SV_Position;
    float2 uv : TEXCOORD0;
}

#if LG_VERT
struct Mvp { float4 c0; float4 c1; float4 c2; float4 c3; }
struct CaptureVertexIn
{
    [vk::location(0)] float4 position;
    [vk::location(1)] float2 uv;
}
ConstantBuffer<Mvp> mvp_matrix : register(b2);
ConstantBuffer<float4> texture_matrix : register(b3);

[shader("vertex")]
CaptureVaryings capture_vert(CaptureVertexIn v)
{
    CaptureVaryings o;
    o.position = mvp_matrix.c3 * v.position.w + (mvp_matrix.c2 * v.position.z + (mvp_matrix.c1 * v.position.y + mvp_matrix.c0 * v.position.x));
    o.uv = texture_matrix.xy * v.uv + texture_matrix.zw;
    return o;
}
#else
Texture2D<vector<Pixel, 4>> texture0 : register(t3);
SamplerState sampler0 : register(s0);
ConstantBuffer<DownsampleBlurUniforms<Pixel>> u : register(b1);
ConstantBuffer<Pixel> edr_scale : register(b6);

[shader("fragment")]
vector<Pixel, 4> capture_frag(CaptureVaryings i) : SV_Target
{
#if defined(LG_TAPS) && LG_TAPS == 8
    return downsample8<Pixel>(texture0, sampler0, i.uv, u, edr_scale);
#elif defined(LG_TAPS) && LG_TAPS == 6
    return downsample6<Pixel>(texture0, sampler0, i.uv, u, edr_scale);
#else
    return downsample4<Pixel>(texture0, sampler0, i.uv, u, edr_scale);
#endif
}
#endif

#endif
