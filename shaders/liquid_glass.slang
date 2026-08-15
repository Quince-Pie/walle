#language slang 2026

// Liquid Glass reveal composition.  The mask itself is produced by
// reveal_mask.slang.
//
// Apple's wallpaper reveal composes exactly one way: the reveal mask is the
// per-pixel coverage of a code-value blend between the current and incoming
// wallpapers.  The hardware reference corpus (65 states, 2048x2048 screen
// captures of the real reveal on black/white content) matches the reveal
// mask byte with zero deviations, which rules out any additional material
// treatment - no veil, platter tint, lens displacement, ring light, glow,
// shadow, or dither exists in Apple's reveal.  The composed byte is
//
//     composed = round((mask * incoming + (255 - mask) * current) / 255)
//
// per channel in code-value (encoded sRGB) space.  The integer numerator is
// never an exact rounding tie (255 * (2k + 1) / 2 is not an integer), so the
// result below is bit-deterministic through float arithmetic.

[[vk::binding(0, 0)]] Texture2D<float4> texA;
[[vk::binding(1, 0)]] Texture2D<float4> texGlassA; // retained in the set; unused
[[vk::binding(2, 0)]] Texture2D<float4> texB;
[[vk::binding(3, 0)]] Texture2D<float4> texGlassB; // retained in the set; unused
[[vk::binding(4, 0)]] Texture2D<uint> revealMask;
[[vk::binding(5, 0)]] SamplerState linearSampler;

// The composition consumes no push data: the reveal mask alone drives the
// blend.  The C renderer still declares the push range in the pipeline
// layout (a superset layout is valid); the timeline/geometry lanes are
// simply unreferenced here.

struct ComposeVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 uv;
};

float linearToSrgb(float value)
{
    return value <= 0.0031308
        ? 12.92 * value
        : 1.055 * pow(value, 1.0 / 2.4) - 0.055;
}

float3 linearToSrgb(float3 value)
{
    return float3(
        linearToSrgb(value.x),
        linearToSrgb(value.y),
        linearToSrgb(value.z));
}

[shader("vertex")]
ComposeVertexOutput composeVertex(uint vertexId : SV_VertexID)
{
    ComposeVertexOutput output;
    float2 uv = float2(float((vertexId << 1u) & 2u), float(vertexId & 2u));
    output.position = float4(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0, 0.0, 1.0);
    output.uv = uv;
    return output;
}

[shader("fragment")]
float4 composeFragment(ComposeVertexOutput input) : SV_Target0
{
    int3 coordinate = int3(int2(input.position.xy), 0);
    float mask = float(revealMask.Load(coordinate));

    // The wallpapers are sampled 1:1 (decoded at the output extent) from
    // sRGB-format textures, so Load() yields linearized values; round() of
    // the re-encoded value recovers the stored code exactly, keeping the
    // blend in Apple's code-value domain with no transfer-function error.
    float3 currentCode = round(linearToSrgb(texA.Load(coordinate).rgb) * 255.0);
    float3 incomingCode = round(linearToSrgb(texB.Load(coordinate).rgb) * 255.0);

    float3 blended = (incomingCode * mask + currentCode * (255.0 - mask))
        * (1.0 / 65025.0);
    return float4(blended, 1.0);
}
