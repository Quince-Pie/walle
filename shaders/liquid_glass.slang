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
    float4 material; // appearance (<0 = derive from content), reserved x3
};

struct ComposeVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 uv;
};

static const float kFadeStart = 0.66;
static const float kMaterialize = 0.12;


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

// --- Measured Liquid Glass material, macOS 26.6.1 (25G76) -----------------
// Re-measured on the target M1 with the capture harness; see
// analysis/derive_reveal_tint_law.py and its result JSON.  The 26.4 (25E246)
// law this shader previously carried no longer holds on that build: `regular`
// stopped being an opaque platter (it now transmits ~28% light / ~31% dark)
// and `clear` became appearance-independent while passing white through.
//
// The material is affine in sRGB CODE space applied to the mega-blurred
// backdrop - fitting the gray ladder to 0.44 codes there versus ~26 in linear
// light, so the sRGB-space application the 26.4 note recorded still stands.
// Saturated primaries need the full 3x3: a per-channel diagonal misses by up
// to 105 codes, the matrix below by 1.2-2.2 mean (3.4-9.5 max) over 17-21
// unclipped backgrounds spanning grays, primaries and secondaries.
//
// Rows are the contribution of one INPUT channel to (R, G, B) out.
static const float3 kClearFromR = float3( 0.9472,  0.0301,  0.0124);
static const float3 kClearFromG = float3( 0.0800,  1.0066,  0.0478);
static const float3 kClearFromB = float3(-0.0016, -0.0010,  0.9697);
static const float3 kClearOffset = float3(22.49, 20.11, 21.50) / 255.0;

static const float3 kRegularLightFromR = float3( 0.4942, -0.0616, -0.0488);
static const float3 kRegularLightFromG = float3(-0.2100,  0.3697, -0.2347);
static const float3 kRegularLightFromB = float3(-0.0052, -0.0224,  0.5685);
static const float3 kRegularLightOffset = float3(183.03, 181.51, 181.69) / 255.0;

static const float3 kRegularDarkFromR = float3( 0.6138, -0.1128, -0.1206);
static const float3 kRegularDarkFromG = float3(-0.2501,  0.4764, -0.2892);
static const float3 kRegularDarkFromB = float3(-0.0503, -0.0503,  0.7232);
static const float3 kRegularDarkOffset = float3(18.43, 18.43, 18.43) / 255.0;

float3 materialTransfer(float3 backdropSrgb, float3 fromR, float3 fromG,
                        float3 fromB, float3 offset)
{
    return clamp(
        backdropSrgb.r * fromR + backdropSrgb.g * fromG + backdropSrgb.b * fromB
            + offset,
        0.0,
        1.0);
}

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

    // Apple's materialize timeline is EXACTLY linear: inputFaceOpacity
    // equals the visible fraction to the bit, across both materials and both
    // appearances (deviation 0 over 33 sampled states).  walle previously
    // eased both ends with smoothstep, which is the wrong shape; the ramps
    // below are linear to match, keeping walle's two-phase structure of a
    // materialize window followed by a fade to the revealed wallpaper.
    float tFade = saturate((time - kFadeStart) / (1.0 - kFadeStart));
    float thickness = saturate(time / kMaterialize) * (1.0 - tFade);

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
    // The measured blur radius ramps linearly with materialize progress
    // (BLUR_RADIUS reads 0.5 at visible_fraction 0.5 and 1.0 at 1.0, with
    // regular's maximum exactly 4x clear's).  walle holds a single
    // pre-blurred backdrop, so approximate the ramp by opening from the
    // sharp source to it as the material thickens.
    float3 sharpIncoming = texB.Sample(linearSampler, uv).rgb;
    float3 blurredIncoming = lerp(
        sharpIncoming, texGlassB.Sample(linearSampler, uv).rgb, thickness);
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
    // Apple selects the material's appearance pole from the SYSTEM
    // appearance.  Use it when the desktop exposes one; otherwise fall back
    // to the content-luminance stand-in.  Measured cost of guessing wrong:
    // regular over black reads 179 (light) against 15 (dark).
    float washLuma = dot(wash, kLuma);
    float lightness = push.material.x >= 0.0
        ? push.material.x
        : smoothstep(0.04, 0.10, washLuma);

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
        // Measured 26.6.1 regular: an affine colour matrix on the blurred
        // backdrop in sRGB space, still selected between the two measured
        // appearance poles by content luminance.
        float3 washSrgb = linearToSrgb(wash);
        float3 platterDark = materialTransfer(
            washSrgb, kRegularDarkFromR, kRegularDarkFromG,
            kRegularDarkFromB, kRegularDarkOffset);
        float3 platterLight = materialTransfer(
            washSrgb, kRegularLightFromR, kRegularLightFromG,
            kRegularLightFromB, kRegularLightOffset);
        float3 platterSrgb = lerp(platterDark, platterLight, lightness);
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
        // Measured 26.6.1 clear: one colour matrix, IDENTICAL in both
        // appearances (the gray ladder matches to the code across light and
        // dark), so no appearance interpolation here.  `thickness` still
        // ramps it in over the materialize window.
        float3 veiled = materialTransfer(
            preSrgb, kClearFromR, kClearFromG, kClearFromB, kClearOffset);
        glassColor = srgbToLinear(lerp(preSrgb, veiled, thickness));
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
