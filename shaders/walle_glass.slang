#language slang 2026
module walle_glass;
import liquidglass;

// Vulkan ABI adapter only. Material values come from the extracted host model.
// Keep the current renderer's negative-height viewport convention.
struct Geometry { float2 resolution; float edrScale; uint mode; float materialOpacity; float3 reserved; }
[[vk::push_constant]] ConstantBuffer<Geometry> geometry;
[[vk::binding(0,0)]] ConstantBuffer<GlassBackgroundUniformsSdf<half>> glass;
[[vk::binding(1,0)]] [[vk::image_format("rgba8")]] Texture2D<half4> pyramid;
[[vk::binding(2,0)]] SamplerState pyramidSampler;
[[vk::binding(3,0)]] [[vk::image_format("rgba8")]] Texture2D<half4> previousColor;

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
[[vk::binding(7,0)]] [[vk::image_format("rgba8")]] Texture2D<half4> maskTexture;
[[vk::binding(8,0)]] [[vk::image_format("rgba8")]] Texture2D<half4> tintTexture;

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
[shader("vertex")]
Varying glassVertex(Vertex input)
{
    Varying output;
    output.position = float4(input.position.x * (2.0f/geometry.resolution.x)-1.0f,
                            1.0f-input.position.y * (2.0f/geometry.resolution.y),0,1);
    output.local = input.local;
    output.sourceUV = input.sourceUV;
    return output;
}
[shader("fragment")]
float4 regularFragment(Varying input) : SV_Target
{
    return float4(glassBackgroundSDF<half,true>(input.local,input.sourceUV,
                  pyramid,pyramidSampler,glass,half(geometry.edrScale)));
}
[shader("fragment")]
float4 clearFragment(Varying input) : SV_Target
{
    // No-bleed is a source specialization; select it from the recipe's bleed
    // predicate, not merely from the product's regular/clear label.
    return float4(glassBackgroundSDF<half,false>(input.local,input.sourceUV,
                  pyramid,pyramidSampler,glass,half(geometry.edrScale)));
}
[shader("fragment")]
float4 faceFragment(Varying input) : SV_Target
{
    half coverage;
    if (geometry.mode==0) coverage=half(1);
    else if (geometry.mode==10)
    {
        // Native circle_image10: float length/derivatives, final half coverage.
        float radius=sqrt(dot(input.local,input.local));
        coverage=half(saturate((1.0f-radius)/max(fwidth(radius),0.0001f)+0.5f));
    }
    else coverage=supercircleCoverage<half>(input.local,effect.faceCircular);
    half4 dst = previousColor.Load(int3(int2(input.position.xy),0));
    return float4(vibrantColorMatrixSourceOver<half>(half4(coverage),dst,effect.matrix));
}
[shader("fragment")]
float4 highlightFragment(Varying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    half4 highlight = sdfKeyFillHighlight<half>(sdf,effect.highlight);
    half4 dst = previousColor.Load(int3(int2(input.position.xy),0));
    return float4(vibrantColorMatrixSourceOver<half>(highlight,dst,effect.matrix));
}
[shader("fragment")]
float4 tintMaskFragment(Varying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    return float4(sdfFill<half>(sdf,effect.fill));
}
[shader("fragment")]
float4 tintGradientFragment(Varying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    half4 gradient = sdfGradient<half>(sdf,ramp,rampSampler,effect.gradient);
    half4 backdrop = previousColor.Load(int3(int2(input.position.xy),0));
    return float4(backdropAwareVibrantColorMatrixSourceOver<half>(gradient,backdrop,half4(0),effect.matrix));
}
[shader("fragment")]
float4 tintCompositeFragment(Varying input) : SV_Target
{
    int3 pixel = int3(int2(input.position.xy),0);
    // Render into the scene with premultiplied source-over hardware blending.
    // The mask and tint must have completed their own UNORM attachment stores.
    return float4(tintTexture.Load(pixel) * maskTexture.Load(pixel));
}

[shader("fragment")]
float4 wallpaperFragment(Varying input) : SV_Target
{
    // Both wallpaper inputs are already sized to the output; exact texel copy.
    return float4(pyramid.Load(int3(int2(input.position.xy),0)));
}

// Product composition. The optical/effect kernels above are unchanged.
[shader("fragment")]
float4 revealFragment(Varying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    half coverage = sdfFill<half>(sdf,effect.fill).a;
    return float4(pyramid.Load(int3(int2(input.position.xy),0)) * coverage);
}
[shader("fragment")]
float4 productFinishFragment(Varying input) : SV_Target
{
    half4 sdf = sdfElementImage<half>(input.local,SdfElementShape(geometry.mode),effect.sdf);
    float coverage = float(sdfFill<half>(sdf,effect.fill).a);
    float4 wallpaper = float4(pyramid.Load(int3(int2(input.position.xy),0)));
    float4 evaluated = float4(previousColor.Load(int3(int2(input.position.xy),0)));
    return lerp(evaluated,wallpaper,coverage * (1.0f-saturate(geometry.materialOpacity)));
}
