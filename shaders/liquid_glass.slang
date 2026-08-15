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
    float4 material; // appearance (<0 = derive), tint rgb (<0 in y = untinted)
};

struct ComposeVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 uv;
};

static const float kFadeStart = 0.66;
// Measured exponent of the materialize ease, k = clock^n (residual 0.018).
static const float kMaterializeEase = 2.36;


// --- Measured refraction, macOS 26.6.1 ------------------------------------
// Decoded by PHASE, not by eye: the rig's four-step sine gratings give the
// local phase of the backdrop at every pixel, and along the element's
// horizontal diameter the grating's axis and the circle's radial direction
// coincide, so the phase shift IS the radial displacement.  Precision 0.02 px,
// unwrapped across periods 1024 down to 64, fitted only where the grating is
// unclipped.  Measured profile, on the 500 pt circle at 2x (radius 500 px):
//
//     r <= 460      0.00 px      r = 480..485   19.84
//     r = 460..465  0.17         r = 485..490   31.54
//     r = 465..470  1.88         r = 490..495   48.11
//     r = 470..475  5.58         r = 495..500   74.66
//     r = 475..480 11.43
//
// which is a power law in the distance from the rim, to 8.5% worst relative
// error, the worst bin being the outermost where the rim itself intrudes.
//
// walle's previous refraction was fitted from Human Interface Guidelines
// photographs and was wrong three ways at once: three times too WIDE (0.25 R
// against the measured 0.081 R, so it displaced content the hardware leaves
// alone), peaked mid-band where the hardware's grows monotonically to the rim,
// and displaced in the OPPOSITE direction - the hardware samples INWARD, which
// is what stretches the interior out to meet the edge.
static const float kLensBandRadiusFraction = 0.0810;
static const float kLensRimDisplacementFraction = 0.1587;
static const float kLensProfilePower = 2.3534;
// Measured ABSENT: red, green and blue displace identically to 0.002 px at
// every radius, so the 3% per-channel spread walle used to carry - 2.2 px of
// fringing at the rim - was invented.  Kept at zero rather than deleted so the
// measurement stays auditable.
static const float kLensDispersion = 0.0;


// Measured on the target M1 from the materialize capture: the specular rim
// sits in the outermost ~10 px of a 1000 px radius element, i.e. ~0.995 R,
// not the 0.973 R fitted from HIG photos.
static const float kRingRadiusFraction = 0.995;
static const float kRingOffsetFraction = 0.021;
static const float kRingEdgeWidthFraction = 0.007;
static const float kRingTopWidthFraction = 0.020;
// The rim measures +7 sRGB codes over the interior, about 0.032 in linear
// light at these levels.  walle's HIG-fitted lobes average roughly 0.13
// azimuthally, so they are scaled to the measured amplitude.  The
// measurement is an azimuthal mean, so the lobe SHAPE is retained and only
// its magnitude is corrected.
static const float kRingGainScale = 0.25;
static const float kRingUpGain = 0.20 * kRingGainScale;
static const float kRingDownGain = 0.13 * kRingGainScale;
static const float kRingBaseGain = 0.04 * kRingGainScale;
static const float kShadowOffsetFraction = 0.012;
static const float kShadowPenumbraFraction = 0.035;
static const float kShadowMinimumPenumbra = 10.0;
// Measured ABSENT on 26.6.1.  Across the whole materialize timeline the
// element interior is flat to within 0.4 codes from centre to rim (no
// glow), and the exterior band just outside the rim reads +0.5 codes
// against a background that never darkens (no contact shadow).  Both were
// fitted from HIG photos rather than measured.  The code paths are kept so
// the measurement stays auditable and either can be restored if a future
// build reintroduces them.
static const float kShadowBase = 0.0;
static const float kGlowIntensity = 0.0;

static const float kDitherLsb = 1.0 / 255.0;
static const float3 kLuma = float3(0.2126, 0.7152, 0.0722);
static const float kHalfPi = 1.57079632679;

// --- Measured Liquid Glass material and tint law -------------------------
// Every constant below is generated straight from the captures by
// analysis/generate_material_law_header.py, so the shader and the measurement
// cannot drift.  See that file's banner for what each law says and how well it
// fits; see analysis/derive_material_matrices.py and analysis/derive_tint_law.py
// for how they were measured.
#include "material_law.slang"

// A tint is neutral when it is EXACTLY gray.  Measured: a tint 0.645 code
// values off the gray axis already blocks the backdrop's chroma as completely
// as a fully saturated one, so this is not a tolerance to tune - it only has
// to sit below the smallest chroma an 8-bit tint can carry, which is 0.845.
static const float kTintNeutralChroma = 0.5;


// The material's colour transfer: an affine 3x3, applied in the space the
// measurement says it lives in.  `clear` uses exponent 1, which makes both pow
// calls the identity; `regular` uses the measured 0.795, without which its gray
// response is 4 code values out at mid-scale in both appearances.
float3 materialTransfer(float3 backdropSrgb, WalleMaterialTransfer transfer)
{
    float3 raised = pow(saturate(backdropSrgb), transfer.exponent);
    float3 mixed = raised.r * transfer.fromR + raised.g * transfer.fromG
        + raised.b * transfer.fromB + transfer.offset;
    return saturate(pow(max(mixed, 0.0), 1.0 / transfer.exponent));
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

// Apply the measured tint to one material's own untinted output.
//
// Everything here is in sRGB CODE values, which is the space the law was fitted
// in, so the only conversion is the one at the end.
//
// The tint's two functions are expanded in a basis of its own colour: a
// quadratic in all three components when the tint is chromatic, and a quadratic
// in its single level when it is gray.  An affine basis is not enough - held
// out one tint at a time it scores 6.43 code values rms against the quadratic's
// 4.88 - because a strongly chromatic tint drives the base NEGATIVE in the
// channel opposite its hue, and a plane through the rest of the colour cube
// cannot reach that.
float3 applyTint(float3 substrateSrgb, float3 tint, float lightness,
                 WalleTintRegime dark, WalleTintRegime light)
{
    if (tint.r < 0.0)
        return substrateSrgb;

    float3 unit = saturate(tint);
    float level = dot(unit, kLuma);
    bool haveNeutral = dark.neutralBase[0] != 0.0 || dark.neutralBase[1] != 0.0;
    bool neutral = length(unit * 255.0 - level * 255.0) < kTintNeutralChroma
        && haveNeutral;

    float terms[10];
    int width;
    if (neutral)
    {
        terms[0] = level;
        terms[1] = level * level;
        terms[2] = 1.0;
        width = 3;
    }
    else
    {
        terms[0] = unit.r;
        terms[1] = unit.g;
        terms[2] = unit.b;
        terms[3] = 1.0;
        terms[4] = unit.r * unit.r;
        terms[5] = unit.g * unit.g;
        terms[6] = unit.b * unit.b;
        terms[7] = unit.r * unit.g;
        terms[8] = unit.r * unit.b;
        terms[9] = unit.g * unit.b;
        width = 10;
    }

    float3 substrateCodes = substrateSrgb * 255.0;
    float substrateLuma = dot(substrateCodes, kLuma);
    float3 substrateChroma = substrateCodes - substrateLuma;

    float3 base = float3(0.0);
    float3 beta = float3(0.0);
    for (int channel = 0; channel < 3; ++channel)
    {
        for (int term = 0; term < width; ++term)
        {
            int index = channel * width + term;
            float b = neutral
                ? lerp(dark.neutralBase[index], light.neutralBase[index], lightness)
                : lerp(dark.chromaticBase[index], light.chromaticBase[index],
                       lightness);
            float g = neutral
                ? lerp(dark.neutralBeta[index], light.neutralBeta[index], lightness)
                : lerp(dark.chromaticBeta[index], light.chromaticBeta[index],
                       lightness);
            base[channel] += b * terms[term];
            beta[channel] += g * terms[term];
        }
    }

    // gamma: a neutral tint lets the backdrop's colour through untouched, a
    // chromatic one replaces it.  Measured with nothing in between.
    float3 chroma = neutral ? substrateChroma : float3(0.0);
    return saturate((base + beta * substrateLuma + chroma) / 255.0);
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

    // Two separate laws, both measured, and they compose:
    //
    //   inputFaceOpacity == the visible fraction k, EXACTLY (deviation 0 over
    //   33 states, both materials, both appearances) - so the material's
    //   presence is linear in k;
    //
    //   k itself is a strongly eased function of the animation clock.
    //   Decoding the rendered pixels of Apple's materialize capture (12
    //   frames, clear/light, coded-field backdrop) gives k = clock^2.36 with
    //   a maximum residual of 0.018 of full scale.  Linear-in-clock misses by
    //   0.32 and walle's original smoothstep(0, 0.12) by 0.36 - it was the
    //   worst of every candidate tried.
    //
    // walle's `time` is a linear clock, so the ease belongs here.
    float tFade = saturate((time - kFadeStart) / (1.0 - kFadeStart));
    // The ease spans the MATERIALIZE WINDOW, not the whole timeline: Apple's
    // clock runs 0..1 across its materialize, whereas walle materializes over
    // [0, kFadeStart) and then fades.  Easing over the full timeline instead
    // caps the material at 0.37 thickness before the fade begins.
    float thickness = pow(saturate(time / kFadeStart), kMaterializeEase)
                      * (1.0 - tFade);

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


    // The measured blur radius ramps linearly with materialize progress
    // (BLUR_RADIUS reads 0.5 at visible_fraction 0.5 and 1.0 at 1.0, with
    // regular's maximum exactly 4x clear's).  texGlassB already carries the
    // fully materialized backdrop at the measured radii, so the ramp is the
    // opening from the sharp source to it as the material thickens.
    float3 sharpIncoming = texB.Sample(linearSampler, uv).rgb;
    float3 blurredIncoming = lerp(
        sharpIncoming, texGlassB.Sample(linearSampler, uv).rgb, thickness);
    // Apple selects the material's appearance pole from the SYSTEM
    // appearance, and so does walle: config, then the desktop portal, then
    // dark.  The nine-tap content-luminance wash that used to stand in for it
    // is gone.  It was never measured, and it cannot survive the backdrop
    // being baked on the CPU - the blend between the material's narrow and
    // wide layers depends on the appearance, so a per-pixel appearance means
    // a per-pixel backdrop, and the baked backdrop and the matrices chosen
    // here would disagree on the same pixel.  Measured cost of getting the
    // pole wrong: regular over black reads 179 (light) against 15 (dark).
    float lightness = saturate(push.material.x);

    float shadowSdf = distance(
        fragmentPixels - float2(0.0, -shadowOffset),
        centerPixels) - radiusPixels;
    float penumbra = 1.0 - smoothstep(0.0, shadowPenumbra, shadowSdf);
    float backgroundLuma = dot(texGlassA.Sample(linearSampler, input.uv).rgb, kLuma);
    float shadowAdaptation = lerp(1.25, 0.60, smoothstep(0.03, 0.35, backgroundLuma));
    float shadowAlpha = penumbra * kShadowBase * shadowAdaptation
        * regular * (1.0 - lightness) * (1.0 - mask) * thickness;

    // Measured on 26.6.1: inputOuterRefractionHeight is exactly 0.125 of the
    // element diameter (0.25 R) at every size probed from 455 to 4328, with
    // no dependence on the window diagonal.  walle used 0.44 R capped by
    // 0.033 * diagonal - nearly twice as wide, and capped where the hardware
    // is not.  The displacement profile below is unchanged; only the band
    // width is corrected.
    float lensBand = kLensBandRadiusFraction * radiusPixels;
    float bandPosition = (radiusPixels - distancePixels) / max(lensBand, 1.0);
    float bandUnit = clamp(bandPosition, 0.0, 1.0);

    float3 glassColor;
    if (regular > 0.5)
    {
        // Measured 26.6.1 regular: an affine colour matrix on the blurred
        // backdrop in sRGB space, still selected between the two measured
        // appearance poles by content luminance.
        // The transfer input is the measured backdrop, and only that: an
        // earlier version fed it a nine-tap screen-wide spread and measured
        // MTF 0.11 at period 64 where the hardware transmits 0.50.
        float3 washSrgb = linearToSrgb(blurredIncoming);
        float3 platterDark = materialTransfer(washSrgb, kMaterialRegularDark);
        float3 platterLight = materialTransfer(washSrgb, kMaterialRegularLight);
        float3 platterSrgb = lerp(platterDark, platterLight, lightness);
        platterSrgb = applyTint(platterSrgb, push.material.yzw, lightness,
                                kTintRegularDark, kTintRegularLight);
        glassColor = lerp(blurredIncoming, srgbToLinear(platterSrgb), thickness);
    }
    else
    {
        // bandPosition is 0 at the rim and 1 at the band's inner edge, so the
        // measured profile is a power of its complement.  The displacement is
        // INWARD, hence the negation.
        float rimUnit = 1.0 - bandUnit;
        float bend = kLensRimDisplacementFraction * radiusPixels
            * pow(rimUnit, kLensProfilePower) * thickness * step(0.0, bandPosition);
        float2 displacementUv = -float2(normalOut.x, -normalOut.y) * bend * pixelToUv;

        // The refraction displaces the backdrop the material samples, which is
        // the measured blur - not the sharp source.  Outside the band the
        // displacement is zero and this is exactly `blurredIncoming`, so the
        // band needs no separate blend to fade into.
        float3 lensed;
        lensed.x = texGlassB.Sample(
            linearSampler, uv + displacementUv * (1.0 + kLensDispersion)).r;
        lensed.y = texGlassB.Sample(linearSampler, uv + displacementUv).g;
        lensed.z = texGlassB.Sample(
            linearSampler, uv + displacementUv * (1.0 - kLensDispersion)).b;
        float3 pre = lerp(sharpIncoming, lensed, thickness);
        float3 preSrgb = linearToSrgb(pre);
        // Measured 26.6.1 clear: one colour matrix, IDENTICAL in both
        // appearances (the gray ladder matches to the code across light and
        // dark), so no appearance interpolation here.  `thickness` still
        // ramps it in over the materialize window.
        float3 veiled = materialTransfer(preSrgb, kMaterialClearDark);
        veiled = applyTint(veiled, push.material.yzw, lightness,
                           kTintClearDark, kTintClearLight);
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
