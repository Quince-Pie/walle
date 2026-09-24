#language slang 2026
module walle_glass;
import liquidglass;

// Vulkan ABI adapter only. Material values come from the extracted host model.
// Keep the current renderer's negative-height viewport convention.
struct Geometry { float2 resolution; float edrScale; uint mode; float materialOpacity; uint nativeRamp; float2 projectionOffset; }
[[vk::push_constant]] ConstantBuffer<Geometry> geometry;
[[vk::binding(0,0)]] ConstantBuffer<GlassBackgroundUniformsSdf<half>> glass;
[[vk::binding(1,0)]] Texture2D<half4> pyramid;
[[vk::binding(2,0)]] SamplerState pyramidSampler;
[[vk::binding(3,0)]] Texture2D<half4> previousColor;

struct EffectUniforms
{
    SdfFragmentUniforms sdf;
    VibrantColorMatrix<half> matrix;
    SdfKeyFillHighlightParams<half> highlight;
    SdfGradientParams<half> gradient;
    SdfFillParams<half> fill;
    float2 faceCircular;
}
[[vk::binding(4,0)]] ConstantBuffer<EffectUniforms> effect;
[[vk::binding(5,0)]] Texture2D<half4> ramp;
[[vk::binding(6,0)]] SamplerState rampSampler;
[[vk::binding(7,0)]] Texture2D<half4> maskTexture;
[[vk::binding(8,0)]] Texture2D<half4> cachedSdf;

struct Vertex
{
    [[vk::location(0)]] float2 position;
    [[vk::location(1)]] float2 local;
    [[vk::location(2)]] float2 sourceUV;
}
struct Varying
{
    float4 position : SV_Position;
    float2 local : TEXCOORD0;
    float2 sourceUV : TEXCOORD1;
}
struct EffectVarying
{
    float4 position : SV_Position;
    noperspective float2 local : TEXCOORD0;
    noperspective float2 sourceUV : TEXCOORD1;
}
[shader("vertex")]
Varying glassVertex(Vertex input)
{
    Varying output;
    precise float2 first = input.position * geometry.resolution;
    output.position = float4(fma(float2(1), geometry.projectionOffset, first), 0, 1);
    output.local = input.local;
    output.sourceUV = input.sourceUV;
    return output;
}
[shader("vertex")]
EffectVarying effectsVertex(Vertex input)
{
    EffectVarying output;
    precise float2 first = input.position * geometry.resolution;
    output.position = float4(fma(float2(1), geometry.projectionOffset, first), 0, 1);
    output.local=input.local;
    output.sourceUV=input.sourceUV;
    return output;
}
[shader("fragment")]
float4 regularFragment(Varying input) : SV_Target
{
    return float4(glassBackgroundSDF<half,true,true>(input.local,input.sourceUV,
                  pyramid,pyramidSampler,glass,half(geometry.edrScale)));
}
[shader("fragment")]
float4 clearFragment(Varying input) : SV_Target
{
    // No-bleed is a source specialization; select it from the recipe's bleed
    // predicate, not merely from the product's regular/clear label.
    return float4(glassBackgroundSDF<half,false,true>(input.local,input.sourceUV,
                  pyramid,pyramidSampler,glass,half(geometry.edrScale)));
}
[shader("fragment")]
float4 faceFragment(EffectVarying input) : SV_Target
{
    half coverage;
    if (geometry.mode==0) coverage=half(1);
    else if (geometry.mode==10)
    {
        // Native circle_image10: float length/derivatives, final half coverage.
        float radius=m1SqrtF32(fma(input.local.y,input.local.y,
                                  orderedMultiply(input.local.x,input.local.x)));
        coverage=half(saturate((1.0f-radius)/max(glassFwidth(radius),0.0001f)+0.5f));
    }
    else coverage=supercircleCoverage<half>(input.local,effect.faceCircular);
    half4 dst = unorm8TexelHalf(previousColor.Load(int3(int2(input.position.xy),0)));
    return float4(vibrantColorMatrixSourceOver<half>(half4(coverage),dst,effect.matrix));
}
[shader("fragment")]
float4 highlightFragment(EffectVarying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    half4 highlight = sdfKeyFillHighlight<half>(sdf,effect.highlight);
    half4 dst = unorm8TexelHalf(previousColor.Load(int3(int2(input.position.xy),0)));
    return float4(vibrantColorMatrixSourceOver<half>(highlight,dst,effect.matrix));
}
[shader("fragment")]
float4 tintMaskFragment(EffectVarying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    return float4(sdfFill<half>(sdf,effect.fill));
}
[shader("fragment")]
float4 sdfCacheFragment(EffectVarying input) : SV_Target
{
    // Native SDFElementGroupNode stores distance, normal and alpha in RGBA16F.
    return float4(sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf));
}
[shader("fragment")]
float4 tintMaskCachedFragment(EffectVarying input) : SV_Target
{
    half4 sdf = cachedSdf.SampleLevel(pyramidSampler,input.local,0);
    return float4(sdfFill<half>(sdf,effect.fill));
}
[shader("fragment")]
float4 tintGradientFragment(EffectVarying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    half4 gradient = geometry.nativeRamp!=0 ? sdfGradientHardwareFixedRamp(sdf,ramp,rampSampler,effect.gradient) : sdfGradient<half>(sdf,ramp,rampSampler,effect.gradient);
    half4 backdrop = previousColor.Load(int3(int2(input.position.xy),0));
    return float4(backdropAwareVibrantColorMatrixSourceOver<half>(gradient,backdrop,half4(0),effect.matrix));
}
[shader("fragment")]
float4 tintApplyMaskFragment(EffectVarying input) : SV_Target
{
    // The local-read variant performs native half dest-in before the RGBA8
    // group is stored and consumed by the final composite.
    if (geometry.mode == 0) return float4(0);
    return float4(unorm8TexelHalf(pyramid.SampleLevel(pyramidSampler, input.sourceUV, 0)));
}
[shader("fragment")]
float4 tintCompositeFragment(EffectVarying input) : SV_Target
{
    half4 source = unorm8TexelHalf(pyramid.SampleLevel(pyramidSampler, input.sourceUV, 0));
    return float4(sourceOverHalf(source, previousColor.Load(int3(int2(input.position.xy),0))));
}
[shader("fragment")]
float4 tintBackdropFragment(EffectVarying input) : SV_Target
{
    // Source raw-image copy into the explicit RGBA16F backdrop attachment.
    return float4(unorm8TexelHalf(pyramid.SampleLevel(pyramidSampler, input.sourceUV, 0)));
}

[shader("fragment")]
float4 wallpaperFragment(EffectVarying input) : SV_Target
{
    // Both wallpaper inputs are already sized to the output; exact texel copy.
    return float4(unorm8TexelHalf(pyramid.Load(int3(int2(input.position.xy),0))));
}

// Native masked-image composition used by the application reveal/departure.
// The mask and opacity-scaled image have already completed RGBA8 stores.
[shader("fragment")]
float4 maskedImageFragment(EffectVarying input) : SV_Target
{
    uint2 pixel=uint2(input.position.xy)+asuint(effect.sdf.arg.xy);
    half4 value=unorm8TexelHalf(pyramid.Load(int3(pixel,0)));
    return float4(multiplyUnormHalf(value,unorm8ScalarHalf(geometry.materialOpacity)));
}
[shader("fragment")]
float4 revealFragment(EffectVarying input) : SV_Target
{
    half4 value=unorm8TexelHalf(pyramid.SampleLevel(pyramidSampler,input.sourceUV,0));
    half alpha=unorm8TexelHalf(maskTexture.SampleLevel(pyramidSampler,input.sourceUV,0)).a;
    return float4(multiplyUnormHalf(value,alpha));
}
[shader("fragment")]
float4 productFinishFragment(EffectVarying input) : SV_Target
{
    half4 value=unorm8TexelHalf(pyramid.SampleLevel(pyramidSampler,input.sourceUV,0));
    half alpha=unorm8TexelHalf(maskTexture.SampleLevel(pyramidSampler,input.sourceUV,0)).a;
    return float4(multiplyUnormHalf(value,alpha));
}
