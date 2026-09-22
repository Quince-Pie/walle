#language slang 2026
module walle_capture;
import liquidglass;
struct CaptureGeometry { float2 resolution; float edrScale; uint unused; }
[[vk::push_constant]] ConstantBuffer<CaptureGeometry> geometry;
[[vk::binding(0,0)]] ConstantBuffer<DownsampleBlurUniforms<half>> parameters;
[[vk::binding(1,0)]] [[vk::image_format("rgba8")]] Texture2D<half4> image;
[[vk::binding(2,0)]] SamplerState captureSampler;
struct Vertex { [[vk::location(0)]] float2 position; [[vk::location(2)]] float2 sourceUV; }
struct Varying { float4 position:SV_Position; float2 sourceUV:TEXCOORD0; }
[shader("vertex")]
Varying captureVertex(Vertex input)
{
    Varying output;
    output.position=float4(input.position.x*(2.0f/geometry.resolution.x)-1.0f,
                           1.0f-input.position.y*(2.0f/geometry.resolution.y),0,1);
    output.sourceUV=input.sourceUV;
    return output;
}
[shader("fragment")]
float4 capture4Fragment(Varying input):SV_Target
{
    return float4(downsample4<half>(image,captureSampler,input.sourceUV,parameters,half(geometry.edrScale)));
}
[shader("fragment")]
float4 capture6Fragment(Varying input):SV_Target
{
    return float4(downsample6<half>(image,captureSampler,input.sourceUV,parameters,half(geometry.edrScale)));
}
[shader("fragment")]
float4 capture8Fragment(Varying input):SV_Target
{
    return float4(downsample8<half>(image,captureSampler,input.sourceUV,parameters,half(geometry.edrScale)));
}

[shader("fragment")]
float4 captureCopyFragment(Varying input):SV_Target
{
    // Native img-style capture path when no downsample taps are requested.
    half4 sample = image.SampleLevel(captureSampler,input.sourceUV,0.0f);
    return float4(half4(sample.rgb * half(geometry.edrScale), sample.a));
}
