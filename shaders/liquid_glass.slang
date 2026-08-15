#language slang 2026

// Liquid Glass composition. The mask itself is produced by reveal_mask.slang.
//
// Two composition paths share this shader:
//
//   * the Liquid Glass material (DEFAULT, what users see) - the blurred
//     backdrop, veil/platter, lens refraction, ring light, glow and contact
//     shadow, all driven by the reveal mask;
//   * Apple's measured reveal blend, selected by push.geometry.w != 0 and
//     used only by the process-capture gate.  The hardware corpus composes
//     exactly round((mask*incoming + (255-mask)*current) / 255) per channel
//     in code-value space, so scoring that path keeps the blend law verified
//     WITHOUT displacing the material look.
//
// Scope of the corpus evidence: it reveals opaque white over opaque black.
// Uniform sources cannot see a blur or a lens displacement, so its
// byte-exactness constrains the value-changing terms (veil, tint, ring,
// glow, shadow, dither); the colour-field corpus is what bears on the
// spatial ones.

[[vk::binding(0, 0)]] Texture2D<float4> texA;
[[vk::binding(1, 0)]] Texture2D<float4> texGlassA;
[[vk::binding(2, 0)]] Texture2D<float4> texB;
[[vk::binding(3, 0)]] Texture2D<float4> texGlassB;
[[vk::binding(4, 0)]] Texture2D<uint> revealMask;
[[vk::binding(5, 0)]] SamplerState linearSampler;

struct ComposePush
{
    // Two float4 lanes keep the Vulkan and C layouts identical without
    // relying on scalar-versus-std430 member alignment.
    float4 timeline; // time, resolution.x, resolution.y, variant
    float4 geometry; // centerTopLeft.x, centerTopLeft.y, radius, appleBlend
};

struct ComposeVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 uv;
};

static const float kFadeStart = 0.66;
static const float kMaterialize = 0.12;

static const float kVeilMix = 0.494;
static const float kVeilAddLight = 0.267;
static const float kVeilAddDark = 0.016;
static const float kVeilSaturationLight = 1.102;
static const float kVeilSaturationDark = 0.85;

static const float kLensBandRadiusFraction = 0.44;
static const float kLensBandDiagonalFraction = 0.033;
static const float kLensMaximumRadiusFraction = 0.21;
static const float kLensMaximumBandFraction = 1.09;
static const float kLensCopyLight = 0.36;
static const float kLensCopyDark = 0.19;
static const float kLensProfilePower = 5.2;
static const float kLensProfileKnee = 0.508;
static const float kLensProfileRim = 0.52;
static const float kLensDispersion = 0.030;

static const float kPlatterLight = 0.980;
static const float kPlatterDark = 0.078;
static const float kWashLight = 0.18;
static const float kWashDark = 0.28;

static const float kRingRadiusFraction = 0.973;
static const float kRingOffsetFraction = 0.021;
static const float kRingEdgeWidthFraction = 0.007;
static const float kRingTopWidthFraction = 0.020;
static const float kRingUpGain = 0.20;
static const float kRingDownGain = 0.13;
static const float kRingBaseGain = 0.04;
static const float kShadowOffsetFraction = 0.012;
static const float kShadowPenumbraFraction = 0.035;
static const float kShadowMinimumPenumbra = 10.0;
static const float kShadowBase = 0.16;
static const float kGlowIntensity = 0.20;

static const float kDitherLsb = 1.0 / 255.0;
static const float3 kLuma = float3(0.2126, 0.7152, 0.0722);
static const float kHalfPi = 1.57079632679;

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

float srgbToLinear(float value)
{
    return value <= 0.04045
        ? value / 12.92
        : pow((value + 0.055) / 1.055, 2.4);
}

float3 srgbToLinear(float3 value)
{
    return float3(
        srgbToLinear(value.x),
        srgbToLinear(value.y),
        srgbToLinear(value.z));
}

float hash12(float2 value)
{
    float3 mixed = frac(float3(value.x, value.y, value.x) * 0.1031);
    mixed += dot(mixed, mixed.yzx + 33.33);
    return frac((mixed.x + mixed.y) * mixed.z);
}

float triangularDither(float2 value)
{
    return hash12(value) + hash12(value + 19.19) - 1.0;
}

// Apple's measured reveal blend.  The wallpapers are sampled 1:1 from sRGB
// textures, so round() of the re-encoded value recovers the stored code
// exactly; the integer numerator is never a rounding tie (255 * (2k + 1) / 2
// is not an integer), so the UNORM8 result is bit-deterministic.
float3 appleRevealBlend(int3 coordinate, float mask)
{
    float3 currentCode = round(linearToSrgb(texA.Load(coordinate).rgb) * 255.0);
    float3 incomingCode = round(linearToSrgb(texB.Load(coordinate).rgb) * 255.0);
    return (incomingCode * mask + currentCode * (255.0 - mask)) * (1.0 / 65025.0);
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
float4 composeFragment(
    ComposeVertexOutput input,
    uniform ComposePush push) : SV_Target0
{
    if (push.geometry.w != 0.0)
    {
        int3 texel = int3(int2(input.position.xy), 0);
        return float4(appleRevealBlend(texel, float(revealMask.Load(texel))), 1.0);
    }

    float time = push.timeline.x;
    float2 resolution = push.timeline.yz;
    float regular = push.timeline.w;
    float2 centerTopLeft = push.geometry.xy;
    float radiusPixels = push.geometry.z;

    float tFade = smoothstep(kFadeStart, 1.0, time);
    float thickness = smoothstep(0.0, kMaterialize, time) * (1.0 - tFade);

    // Express the material in the original bottom-left pixel coordinates while
    // Vulkan resources and mask storage remain naturally top-left.
    float2 fragmentTopLeft = input.position.xy;
    float2 fragmentPixels = float2(fragmentTopLeft.x, resolution.y - fragmentTopLeft.y);
    float2 centerPixels = float2(
        centerTopLeft.x,
        resolution.y - centerTopLeft.y);
    float2 delta = fragmentPixels - centerPixels;
    float distancePixels = length(delta);
    float sdf = distancePixels - radiusPixels;
    float2 normalOut = distancePixels > 0.5 ? delta / distancePixels : float2(0.0, 1.0);
    float mask = float(revealMask.Load(int3(int2(fragmentTopLeft), 0))) / 255.0;

    float shadowOffset = kShadowOffsetFraction * radiusPixels;
    float shadowPenumbra = max(kShadowPenumbraFraction * radiusPixels, kShadowMinimumPenumbra);

    if (mask <= 0.0 && sdf > shadowOffset + shadowPenumbra + 8.0)
        return float4(linearToSrgb(texA.Sample(linearSampler, input.uv).rgb), 1.0);

    float2 uv = input.uv;
    float2 pixelToUv = 1.0 / resolution;
    float diagonal = length(resolution);

    float2 e1 = float2(0.30, 0.0);
    float2 e2 = float2(0.0, 0.30);
    float2 e3 = float2(0.21, 0.21);
    float2 e4 = float2(0.21, -0.21);
    float3 blurredIncoming = texGlassB.Sample(linearSampler, uv).rgb;
    float3 wash = (
        blurredIncoming
        + texGlassB.Sample(linearSampler, uv + e1).rgb
        + texGlassB.Sample(linearSampler, uv - e1).rgb
        + texGlassB.Sample(linearSampler, uv + e2).rgb
        + texGlassB.Sample(linearSampler, uv - e2).rgb
        + texGlassB.Sample(linearSampler, uv + e3).rgb
        + texGlassB.Sample(linearSampler, uv - e3).rgb
        + texGlassB.Sample(linearSampler, uv + e4).rgb
        + texGlassB.Sample(linearSampler, uv - e4).rgb) / 9.0;
    float washLuma = dot(wash, kLuma);
    float lightness = smoothstep(0.04, 0.10, washLuma);

    float shadowSdf = distance(
        fragmentPixels - float2(0.0, -shadowOffset),
        centerPixels) - radiusPixels;
    float penumbra = 1.0 - smoothstep(0.0, shadowPenumbra, shadowSdf);
    float backgroundLuma = dot(texGlassA.Sample(linearSampler, input.uv).rgb, kLuma);
    float shadowAdaptation = lerp(1.25, 0.60, smoothstep(0.03, 0.35, backgroundLuma));
    float shadowAlpha = penumbra * kShadowBase * shadowAdaptation
        * regular * (1.0 - lightness) * (1.0 - mask) * thickness;

    float lensBand = min(
        kLensBandRadiusFraction * radiusPixels,
        kLensBandDiagonalFraction * diagonal);
    float bandPosition = (radiusPixels - distancePixels) / max(lensBand, 1.0);
    float bandUnit = clamp(bandPosition, 0.0, 1.0);

    float3 glassColor;
    if (regular > 0.5)
    {
        float3 washSrgb = linearToSrgb(wash);
        float washSrgbLuma = dot(washSrgb, kLuma);
        float platter = lerp(kPlatterDark, kPlatterLight, lightness);
        float washStrength = lerp(kWashDark, kWashLight, lightness);
        float3 platterSrgb = clamp(
            float3(platter) + (washSrgb - float3(washSrgbLuma)) * washStrength,
            0.0,
            1.0);
        glassColor = lerp(blurredIncoming, srgbToLinear(platterSrgb), thickness);
    }
    else
    {
        float profile = (1.0 - pow(bandUnit, kLensProfilePower))
            * (kLensProfileRim + (1.0 - kLensProfileRim)
                * sin(kHalfPi * min(bandUnit / kLensProfileKnee, 1.0)));
        float maximumDisplacement = min(
            kLensMaximumRadiusFraction * radiusPixels,
            kLensMaximumBandFraction * lensBand);
        float bend = maximumDisplacement * profile * thickness * step(0.0, bandPosition);
        float2 displacementUv = float2(normalOut.x, -normalOut.y) * bend * pixelToUv;

        float3 lensed;
        lensed.x = texB.Sample(
            linearSampler,
            uv + displacementUv * (1.0 + kLensDispersion)).r;
        lensed.y = texB.Sample(linearSampler, uv + displacementUv).g;
        lensed.z = texB.Sample(
            linearSampler,
            uv + displacementUv * (1.0 - kLensDispersion)).b;

        float copyAlpha = lerp(kLensCopyDark, kLensCopyLight, lightness)
            * exp(-max(bandPosition, 0.0)) * thickness * step(0.0, bandPosition);
        float3 pre = lerp(blurredIncoming, lensed, copyAlpha);
        float3 preSrgb = linearToSrgb(pre);
        float preLuma = dot(preSrgb, kLuma);
        float veilSaturation = lerp(kVeilSaturationDark, kVeilSaturationLight, lightness);
        preSrgb = float3(preLuma)
            + (preSrgb - float3(preLuma)) * lerp(1.0, veilSaturation, thickness);
        float veilMix = lerp(1.0, kVeilMix, thickness);
        float veilAdd = lerp(kVeilAddDark, kVeilAddLight, lightness) * thickness;
        glassColor = srgbToLinear(clamp(preSrgb * veilMix + float3(veilAdd), 0.0, 1.0));
    }

    float2 ringCenter = centerPixels - float2(0.0, kRingOffsetFraction * radiusPixels);
    float2 ringDelta = fragmentPixels - ringCenter;
    float ringDistance = length(ringDelta);
    float2 ringNormal = ringDistance > 0.5 ? ringDelta / ringDistance : float2(0.0, 1.0);
    float ringSdf = ringDistance - kRingRadiusFraction * radiusPixels;
    float edgeWidth = clamp(kRingEdgeWidthFraction * radiusPixels, 1.25, 5.0);
    float topWidth = clamp(kRingTopWidthFraction * radiusPixels, 3.0, 24.0);
    float ringWidth = lerp(edgeWidth, topWidth, smoothstep(0.0, 1.0, max(ringNormal.y, 0.0)));
    float ringProfile = exp(-(ringSdf * ringSdf) / (ringWidth * ringWidth));

    float angle = radians(125.0) - radians(30.0) * time;
    float2 light = float2(cos(angle), sin(angle));
    float normalDot = dot(ringNormal, light);
    float lobes = kRingBaseGain
        + kRingUpGain * pow(max(normalDot, 0.0), 1.6)
        + kRingDownGain * pow(max(-normalDot, 0.0), 2.0);
    float3 ringLight = float3(ringProfile * lobes * thickness * mask);

    float glowTime = thickness * (1.0 - smoothstep(0.18, 0.55, time));
    float glow = exp(
        -(distancePixels * distancePixels)
        / max(radiusPixels * radiusPixels * 0.45, 1.0));
    glassColor += float3(glow * glowTime * kGlowIntensity * lerp(0.5, 1.0, regular));

    float3 incoming = texB.Sample(linearSampler, input.uv).rgb;
    float3 insideColor = lerp(glassColor + ringLight, incoming, tFade);
    float3 background = texA.Sample(linearSampler, input.uv).rgb;
    float3 shadowedBackground = background * (1.0 - shadowAlpha);
    float3 result = lerp(shadowedBackground, insideColor, mask);

    float3 encoded = linearToSrgb(clamp(result, 0.0, 1.0));
    encoded += triangularDither(fragmentPixels + time * 61.0)
        * kDitherLsb * (1.0 - tFade);
    return float4(encoded, 1.0);
}
