#language slang 2026

// GPU glass bake: the measured backdrop mixture, computed where Apple
// computes theirs.  Replaces the CPU (vips) bake of texGlassB; the CPU path
// remains behind WALLE_GLASS_BAKE=cpu as the A/B referee.
//
// The law (walle.c:106..173, measured on macOS 26.6.1): blur the wallpaper in
// PANEL CODE SPACE - sRGB-decode, colorant matrix to panel primaries, panel
// re-encode - with a two-Gaussian mixture whose luma and chroma weights
// differ, then convert back.  Radii are absolute in capture pixels at 2x.
//
//   passes: convertForward -> blur(narrow,H) -> blur(narrow,V)
//           downsample8 -> blur(wide/8,H) -> blur(wide/8,V)
//           mixFinal (bilinear-upsamples the wide field in place)
//
// Precision: intermediates are RGBA32F carrying code values normalized to
// [0,1]; the final pass writes LINEAR light into an sRGB attachment so the
// hardware performs the terminal encode+quantize.  Bit-identity with the CPU
// bake is not claimed - the referee is the measured-law score gate.

[[vk::binding(0, 0)]] Texture2D<float4> sourceA;
[[vk::binding(1, 0)]] Texture2D<float4> sourceB;
[[vk::binding(2, 0)]] SamplerState linearSampler;

struct BakePush
{
    // rows of the 3x3 colorant matrix (to-panel for the forward pass,
    // from-panel for the final); w lanes unused
    float4 matRow0;
    float4 matRow1;
    float4 matRow2;
    // x: sigma in output pixels for blur passes; y: kernel radius (taps each
    // side); z: 1 = horizontal, 0 = vertical; w: 1 = panel space enabled
    float4 blur;
    // x: narrow (luma) weight  y: narrow chroma weight
    // z,w: inverse extent of the pass's OUTPUT target
    float4 mix;
};

struct BakeVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 uv;
};

[shader("vertex")]
BakeVertexOutput bakeVertex(uint vertexId : SV_VertexID)
{
    BakeVertexOutput output;
    float2 uv = float2(float((vertexId << 1u) & 2u), float(vertexId & 2u));
    output.position = float4(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0, 0.0, 1.0);
    output.uv = uv;
    return output;
}

float srgbEncode(float value)
{
    return value <= 0.0031308
        ? 12.92 * value
        : 1.055 * pow(value, 1.0 / 2.4) - 0.055;
}

float3 srgbEncode(float3 v)
{
    return float3(srgbEncode(v.x), srgbEncode(v.y), srgbEncode(v.z));
}

float srgbDecode(float value)
{
    return value <= 0.04045
        ? value / 12.92
        : pow((value + 0.055) / 1.055, 2.4);
}

float3 srgbDecode(float3 v)
{
    return float3(srgbDecode(v.x), srgbDecode(v.y), srgbDecode(v.z));
}

// Pass 1: standard texture (sRGB image: hardware sample = exact per-code
// decode) -> panel primaries -> panel code space.  Output: codes in [0,1].
[shader("fragment")]
float4 bakeConvertForward(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    float3 lin = sourceA.Sample(linearSampler, input.uv).rgb;
    if (push.blur.w > 0.5)
    {
        float3 mixed = float3(
            dot(push.matRow0.xyz, lin),
            dot(push.matRow1.xyz, lin),
            dot(push.matRow2.xyz, lin));
        lin = max(mixed, 0.0);
    }
    return float4(srgbEncode(lin), 1.0);
}

// Separable Gaussian in code space.  Weights follow vips's discrete mask:
// w_i = exp(-i^2 / (2 sigma^2)), truncated at the pushed radius (min_ampl
// 0.001), normalized by the computed sum.
[shader("fragment")]
float4 bakeBlur(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    float sigma = push.blur.x;
    int radius = int(push.blur.y);
    float2 axis = push.blur.z > 0.5 ? float2(push.mix.z, 0.0) : float2(0.0, push.mix.w);
    float denom = -1.0 / (2.0 * sigma * sigma);
    float4 accum = sourceA.Sample(linearSampler, input.uv);
    float total = 1.0;
    for (int tap = 1; tap <= radius; ++tap)
    {
        float weight = exp(float(tap * tap) * denom);
        float2 offset = axis * float(tap);
        accum += weight * (sourceA.Sample(linearSampler, input.uv + offset)
                           + sourceA.Sample(linearSampler, input.uv - offset));
        total += 2.0 * weight;
    }
    return accum / total;
}

// 8x box downsample for the wide field's reduced grid: the wide Gaussian is
// an approximation stack on both implementations (the CPU uses
// reduce/blur/resize); the referee is the measured-law score.
[shader("fragment")]
float4 bakeDownsample8(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    // Output texel spans 8x8 source texels; sample its 4x4 bilinear centers.
    float2 sourceTexel = float2(push.mix.z, push.mix.w); // 1/source extent
    float4 accum = float4(0.0);
    [unroll]
    for (int y = 0; y < 4; ++y)
    {
        [unroll]
        for (int x = 0; x < 4; ++x)
        {
            float2 offset = (float2(float(x), float(y)) * 2.0 - 3.0) * sourceTexel;
            accum += sourceA.Sample(linearSampler, input.uv + offset);
        }
    }
    return accum * (1.0 / 16.0);
}

// One mip-chain level down: 2x decimation behind the [1 4 6 4 1]/16
// prefilter, out[i] = sum_k pf[k] * src[2i + k - 2] per axis - the bleed
// mechanism the fresh step edges identified (session 193).
[shader("fragment")]
float4 bakeChainDown2(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    static const float kPf[5] = {1.0 / 16.0, 4.0 / 16.0, 6.0 / 16.0, 4.0 / 16.0, 1.0 / 16.0};
    float2 sourceTexel = float2(push.mix.z, push.mix.w);
    float2 outPixel = floor(input.position.xy);
    float4 accum = float4(0.0);
    [unroll]
    for (int ky = 0; ky < 5; ++ky)
    {
        [unroll]
        for (int kx = 0; kx < 5; ++kx)
        {
            float2 sourceUv = (2.0 * outPixel
                               + float2(float(kx), float(ky)) - 2.0 + 0.5)
                * sourceTexel;
            accum += (kPf[kx] * kPf[ky]) * sourceA.Sample(linearSampler, sourceUv);
        }
    }
    return accum;
}

// One mip-chain level up: zero-stuff + [1 2 1]/2 tent, which is exactly
// bilinear reconstruction at half-texel positions.
[shader("fragment")]
float4 bakeChainUp2(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    float2 sourceTexel = float2(push.mix.z, push.mix.w);
    float2 outPixel = floor(input.position.xy);
    float2 sourceUv = (outPixel * 0.5 + 0.5) * sourceTexel;
    return sourceA.Sample(linearSampler, sourceUv);
}

// Pointwise tonal warp for the cascade far field (sessions 194-195): the
// wide blur runs in a power-warped copy of panel code space, per channel.
// Exponent in blur.x; blur.y > 0.5 selects the FLIPPED form 1-(1-v)^p -
// the same power law measured on the inverted signal (distance from
// white), which is light regular's identity (p = 3).
[shader("fragment")]
float4 bakeWarpPow(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    float3 v = saturate(sourceA.Sample(linearSampler, input.uv).rgb);
    float3 warped = push.blur.y > 0.5
        ? 1.0 - pow(1.0 - v, push.blur.x)
        : pow(v, push.blur.x);
    return float4(warped, 1.0);
}

// Pointwise prep for the LUMA-ONLY warp variant: rgb passes through
// un-warped (the chroma path), alpha carries the warped luma (the luma
// path).  Exponent in blur.x, flip in blur.y, as in bakeWarpPow.
[shader("fragment")]
float4 bakePrepWarpLuma(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    static const float3 kLuma = float3(0.2126, 0.7152, 0.0722);
    float3 v = saturate(sourceA.Sample(linearSampler, input.uv).rgb);
    float y = dot(kLuma, v);
    float yw = push.blur.y > 0.5
        ? 1.0 - pow(1.0 - y, push.blur.x)
        : pow(y, push.blur.x);
    return float4(v, yw);
}

// Final pass: mixture in code space, then back to linear light for the sRGB
// attachment's hardware encode.
//   out = wC*near + (1-wC)*far + (wL-wC) * luma(near - far)
// blur.x, when positive, is the cascade far field's un-warp exponent (1/p);
// blur.y > 0.5 selects the flipped form's inverse 1-(1-y)^(1/p);
// blur.z > 1.5 selects the LUMA-ONLY variant: far.rgb is the un-warped far
// field (chroma), far.a its warped luma - the luma channel mixes from the
// warped field, chroma from the un-warped one.  On gray content this is
// identical to the full-channel warp.
[shader("fragment")]
float4 bakeMixFinal(BakeVertexOutput input, uniform BakePush push) : SV_Target0
{
    static const float3 kLuma = float3(0.2126, 0.7152, 0.0722);
    float3 near = sourceA.Sample(linearSampler, input.uv).rgb;
    float4 farS = sourceB.Sample(linearSampler, input.uv); // bilinear 8x up
    float3 far = farS.rgb;
    if (push.blur.x > 0.0 && push.blur.z > 1.5)
    {
        float yfw = push.blur.y > 0.5
            ? 1.0 - pow(saturate(1.0 - farS.a), push.blur.x)
            : pow(max(farS.a, 0.0), push.blur.x);
        float wLuma = push.mix.x;
        float wChroma = push.mix.y;
        float3 mixed = wChroma * near + (1.0 - wChroma) * far
            + ((wLuma - wChroma) * dot(kLuma, near)
               + (1.0 - wLuma) * yfw
               - (1.0 - wChroma) * dot(kLuma, far));
        mixed = saturate(mixed);
        float3 lin = srgbDecode(mixed);
        if (push.blur.w > 0.5)
        {
            float3 back = float3(
                dot(push.matRow0.xyz, lin),
                dot(push.matRow1.xyz, lin),
                dot(push.matRow2.xyz, lin));
            lin = max(back, 0.0);
        }
        return float4(lin, 1.0);
    }
    if (push.blur.x > 0.0)
        far = push.blur.y > 0.5
            ? 1.0 - pow(saturate(1.0 - far), push.blur.x)
            : pow(max(far, 0.0), push.blur.x);
    float wLuma = push.mix.x;
    float wChroma = push.mix.y;
    float3 mixed = wChroma * near + (1.0 - wChroma) * far
        + (wLuma - wChroma) * dot(kLuma, near - far);
    mixed = saturate(mixed);
    float3 lin = srgbDecode(mixed);
    if (push.blur.w > 0.5)
    {
        float3 back = float3(
            dot(push.matRow0.xyz, lin),
            dot(push.matRow1.xyz, lin),
            dot(push.matRow2.xyz, lin));
        lin = max(back, 0.0);
    }
    return float4(lin, 1.0); // sRGB attachment encodes + quantizes
}
