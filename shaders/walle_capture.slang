#language slang 2026
module walle_capture;
import liquidglass;
struct CaptureGeometry { float2 resolution; float edrScale; uint unused; }
[[vk::push_constant]] ConstantBuffer<CaptureGeometry> geometry;
struct CaptureUniforms
{
    DownsampleBlurUniforms<half> filter;
    float4 projection0;
    float4 projection1;
    float4 projection2;
    float4 projection3;
    float4 textureMatrix;
}
[[vk::binding(0,0)]] ConstantBuffer<CaptureUniforms> parameters;
[[vk::binding(1,0)]] Texture2D<half4> image;
[[vk::binding(2,0)]] SamplerState captureSampler;
struct Vertex { [[vk::location(0)]] float2 position; [[vk::location(2)]] float2 sourceUV; }
struct Varying { float4 position:SV_Position; float2 sourceUV:TEXCOORD0; }
[shader("vertex")]
Varying captureVertex(Vertex input)
{
    Varying output;
    precise float4 first=parameters.projection0*input.position.x;
    output.position=fma(parameters.projection3,1.0f,
        fma(parameters.projection2,0.0f,fma(parameters.projection1,input.position.y,first)));
    output.sourceUV=parameters.textureMatrix.xy*input.sourceUV+parameters.textureMatrix.zw;
    return output;
}
[shader("fragment")]
float4 capture4Fragment(Varying input):SV_Target
{
    return float4(downsample4<half,true>(image,captureSampler,input.sourceUV,parameters.filter,half(geometry.edrScale)));
}
[shader("fragment")]
float4 capture6Fragment(Varying input):SV_Target
{
    return float4(downsample6<half,true>(image,captureSampler,input.sourceUV,parameters.filter,half(geometry.edrScale)));
}
[shader("fragment")]
float4 capture8Fragment(Varying input):SV_Target
{
    return float4(downsample8<half,true>(image,captureSampler,input.sourceUV,parameters.filter,half(geometry.edrScale)));
}

[shader("fragment")]
float4 captureCopyFragment(Varying input):SV_Target
{
    // Native img-style capture path when no downsample taps are requested.
    half4 sample = sampleGlassBackdrop<half,true>(image,captureSampler,input.sourceUV,half(0));
    return float4(half4(sample.rgb * half(geometry.edrScale), sample.a));
}
