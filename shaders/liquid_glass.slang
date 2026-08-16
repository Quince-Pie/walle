#language slang 2026

// Liquid Glass composition. The mask itself is produced by reveal_mask.slang.
//
// Two composition paths share this shader:
//
//   * the Liquid Glass material (DEFAULT, what users see) - the blurred
//     backdrop, veil/platter, lens refraction, the rim inside the boundary and
//     the shadow outside it, all driven by the reveal mask;
//   * Apple's measured reveal blend, selected by push.geometry.w != 0 and
//     used only by the process-capture gate.  The hardware corpus composes
//     exactly round((mask*incoming + (255-mask)*current) / 255) per channel
//     in code-value space, so scoring that path keeps the blend law verified
//     WITHOUT displacing the material look.
//
// Scope of the corpus evidence: it reveals opaque white over opaque black.
// Uniform sources cannot see a blur or a lens displacement, so its
// byte-exactness constrains the value-changing terms (veil, tint, rim, shadow,
// dither); the colour-field corpus is what bears on the spatial ones, and the
// step-edge check (analysis/verify_structured_background.py) is what scores
// them end to end.

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
    float4 material; // appearance (0 dark, 1 light), tint rgb (<0 in y = untinted)
    float4 scaling;  // device pixels per point, then three unused lanes
};

struct ComposeVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 uv;
};

static const float kFadeStart = 0.66;
// The materialize ease itself is measured per variant and lives in
// material_law.slang: it is a delayed power, not a bare one, and `clear` and
// `regular` do not share it.


// --- Measured refraction, macOS 26.6.1 ------------------------------------
// Decoded by PHASE, not by eye: the rig's four-step sine gratings give the
// local phase of the backdrop at every pixel, and along the element's
// horizontal diameter the grating's axis and the circle's radial direction
// coincide, so the phase shift IS the radial displacement.  Precision 0.02 px,
// unwrapped across four periods, fitted only where the grating is unclipped.
//
// The band is ABSOLUTE.  Measured on circles of radius 128, 256 and 500 capture
// pixels, the profile against DISTANCE INSIDE THE RIM is the same curve to
// within 0.2 px - a fourfold range of element size with no scaling at all - and
// it is the same for both variants and both appearances.  An earlier reading
// called it 0.081 R, which was only true of the one element it was measured on.
//
//     px inside the rim    1      3      6     12     18     25     32    38
//     displacement      94.8   74.8   57.0   34.4   19.9    8.8    2.5   0.2
//
// fitted to 1.9 px worst on a 94.8 px peak by
//
//     d(u) = A (W - u)^k / (u + c)          u, d in CAPTURE pixels at 2x
//
// walle's previous refraction was fitted from Human Interface Guidelines
// photographs and was wrong four ways: three times too WIDE, scaled with the
// element instead of being absolute, peaked mid-band where the hardware's grows
// monotonically to the rim, and displaced OUTWARD where the hardware samples
// inward - which is what stretches the interior out to meet the edge.
static const float kLensCaptureScale = 2.0;
static const float kLensBandCapturePixels = 35.5796;
static const float kLensAmplitude = 26.48219;
static const float kLensProfilePower = 1.09134;
static const float kLensProfileOffset = 12.6207;
// Measured ABSENT: red, green and blue displace identically to 0.002 px at
// every radius, so the 3% per-channel spread walle used to carry - 2.2 px of
// fringing at the rim - was invented.  Kept at zero rather than deleted so the
// measurement stays auditable.
static const float kLensDispersion = 0.0;


// The specular ring, the glow and the contact shadow that used to sit here
// were all fitted from Human Interface Guidelines photographs, and all three
// are gone.  The ring's directional lobes describe an angular structure the
// hardware does not have - measured across twelve sectors and 1677 frames, the
// rim varies by a median 2.7% of its own excess - and the glow and the shadow
// had been zeroed after a materialize capture read them as absent.  Rendering
// walle back against a step edge is what showed the shadow is NOT absent: it
// is a 4.3% darkening in light and 7.6% in dark, reaching 80 capture pixels.
// The rim and the shadow are now measured laws in material_law.slang; see
// analysis/measure_rim_light.py.

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


// The material's colour transfer: a polynomial in sRGB code space, whose order
// each material chose by cross-validation and whose terms the generated header
// pads to one width.  An affine 3x3 is not enough - `regular`'s gray response
// bows above the chord by 4.4 code values at mid-scale, in both appearances -
// and the higher terms beat it on held-out backgrounds everywhere.  They also
// removed the need for the x**0.795 space this used to apply, which was an
// artefact of fitting sixteen backgrounds rather than a property of the
// material.
float3 materialTransfer(float3 backdropSrgb, WalleMaterialTransfer transfer)
{
    float terms[kTransferTermCount];
    walleTransferBasis(saturate(backdropSrgb), terms);

    float3 mixed = float3(0.0);
    for (int index = 0; index < kTransferTermCount; ++index)
        mixed += transfer.terms[index] * terms[index];
    return saturate(mixed);
}

// The element's EDGE, measured rather than modelled.
//
// walle used to carry a directional specular ring - an up lobe, a down lobe, a
// base gain and an offset centre, all fitted from Human Interface Guidelines
// photographs - and a shadow whose base had been zeroed.  Rendering both back
// against a capture is what retired them: Apple's rim is ISOTROPIC to a median
// 2.7% of its own excess across twelve sectors and 1677 frames, so there is no
// lobe to describe, and `regular` casts a real shadow that reaches 80 px.
//
// Both are now the same shape as everything else here: a colour law on the
// backdrop, weighted by a measured profile of depth.
float3 edgeTransfer(float3 backdropSrgb, float3 terms[kTransferTermCount])
{
    float basis[kTransferTermCount];
    walleTransferBasis(saturate(backdropSrgb), basis);
    float3 mixed = float3(0.0);
    for (int index = 0; index < kTransferTermCount; ++index)
        mixed += terms[index] * basis[index];
    return saturate(mixed);
}

// depth is measured INSIDE the boundary, in capture pixels at 2x.
float rimWeight(float depth, WalleEdge edge)
{
    if (depth < 0.0)
        return 0.0;
    float position = (depth - kRimProfileStart) / kRimProfileStep;
    if (position <= 0.0)
        return edge.rimWeight[0];
    int index = int(floor(position));
    if (index >= kRimProfileCount - 1)
        return 0.0;
    return lerp(edge.rimWeight[index], edge.rimWeight[index + 1],
                position - float(index));
}

// depth is measured OUTSIDE the boundary, in capture pixels at 2x.  The
// samples are not evenly spaced - the shadow is read densely where it changes
// and sparsely where it trails off - so this walks them.
float shadowWeight(float depth, WalleEdge edge)
{
    if (depth <= kShadowDepthPixels[0])
        return edge.shadowWeight[0];
    for (int index = 0; index < kShadowProfileCount - 1; ++index)
    {
        if (depth < kShadowDepthPixels[index + 1])
        {
            float span = kShadowDepthPixels[index + 1] - kShadowDepthPixels[index];
            return lerp(edge.shadowWeight[index], edge.shadowWeight[index + 1],
                        (depth - kShadowDepthPixels[index]) / span);
        }
    }
    return 0.0;
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
// polynomial in all three components when the tint is chromatic, and a
// quadratic in its single level when it is gray.  An affine basis is not
// enough - held out one tint at a time it scores 5.27 code values rms against
// a quadratic's 3.07 - because a strongly chromatic tint drives the base
// NEGATIVE in the channel opposite its hue, and a plane through the rest of the
// colour cube cannot reach that.  How far past affine is measured per material,
// and the generated header pads the chosen orders to one width.
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

    float terms[kTintChromaticTermCount];
    int width;
    if (neutral)
    {
        float neutralTerms[kTintNeutralTermCount];
        walleTintNeutralBasis(level, neutralTerms);
        for (int term = 0; term < kTintNeutralTermCount; ++term)
            terms[term] = neutralTerms[term];
        width = kTintNeutralTermCount;
    }
    else
    {
        walleTintBasis(unit, terms);
        width = kTintChromaticTermCount;
    }

    float3 substrateCodes = substrateSrgb * 255.0;
    float substrateLuma = dot(substrateCodes, kLuma);
    float3 substrateChroma = substrateCodes - substrateLuma;

    float3 base = float3(0.0);
    float3 beta = float3(0.0);
    // gamma: how much of the substrate's own chroma survives.  A neutral tint
    // lets the backdrop's colour through and a chromatic one replaces it, with
    // nothing measured in between - but "through" is not exactly one, and
    // against a substrate whose chroma runs to 54 code values the difference
    // is worth nine of them, so it is carried as a fitted function too.
    float3 gamma = float3(0.0);
    for (int channel = 0; channel < 3; ++channel)
    {
        for (int term = 0; term < width; ++term)
        {
            int index = channel * width + term;
            float b = neutral
                ? lerp(dark.neutralBase[index], light.neutralBase[index], lightness)
                : lerp(dark.chromaticBase[index], light.chromaticBase[index],
                       lightness);
            float e = neutral
                ? lerp(dark.neutralBeta[index], light.neutralBeta[index], lightness)
                : lerp(dark.chromaticBeta[index], light.chromaticBeta[index],
                       lightness);
            float c = neutral
                ? lerp(dark.neutralGamma[index], light.neutralGamma[index],
                       lightness)
                : lerp(dark.chromaticGamma[index], light.chromaticGamma[index],
                       lightness);
            base[channel] += b * terms[term];
            beta[channel] += e * terms[term];
            gamma[channel] += c * terms[term];
        }
    }

    return saturate(
        (base + beta * substrateLuma + gamma * substrateChroma) / 255.0);
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

    // How the material ARRIVES, measured across 61 frames of each of the four
    // materials against the rig's own raster clock.
    //
    // It is a CROSSFADE.  One alpha per frame explains the whole frame to 1.5
    // code values - and that is the discriminator, because a ramping blur
    // radius could not: over a structured backdrop a pixel in fine detail
    // would reach the material sooner than one on a plateau, and none do.
    // So thickness is applied ONCE, at the end, between the sharp backdrop and
    // the finished material.  It used to be threaded through the blur, the
    // refraction and the transfer separately, which is a different function of
    // k with the same endpoints.
    //
    // The curve is a delayed power that does not finish when the clock does,
    // and neither part is decoration: with the delay and the overrun the fit
    // is 0.5% of full scale, with a bare power 3.7%.  All three constants are
    // per VARIANT, which the old single exponent could not be.
    //
    // walle's `time` is a linear clock, so the ease belongs here.
    float tFade = saturate((time - kFadeStart) / (1.0 - kFadeStart));
    // The ease spans the MATERIALIZE WINDOW, not the whole timeline: Apple's
    // clock runs 0..1 across its materialize, whereas walle materializes over
    // [0, kFadeStart) and then fades.  Easing over the full timeline instead
    // caps the material at 0.37 thickness before the fade begins.
    WalleMaterialize arrival = kMaterializeClear;
    if (regular > 0.5)
        arrival = kMaterializeRegular;
    // `end` past one means Apple's transition outlives the window the rig
    // clocked, so the curve is played over walle's OWN materialize window
    // rather than cut off where the rig stopped watching: the shape - the
    // delay and the exponent - is what was measured, and the material has to
    // arrive by the time the window ends.  Cutting it off instead leaves the
    // wallpaper permanently 3.5% short of its material for `regular` and 7.2%
    // for `clear`, which the flat-background check catches at once.
    float clock = saturate(time / kFadeStart) * arrival.end;
    float thickness = pow(
        saturate((clock - arrival.delay) / (arrival.end - arrival.delay)),
        arrival.exponent) * (1.0 - tFade);

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

    // The measured shadow reaches kShadowDepthPixels[last] CAPTURE pixels
    // beyond the boundary, and nothing past that is touched.
    float lensUnit = max(push.scaling.x, 1.0) / kLensCaptureScale;
    float shadowReachPixels = kShadowDepthPixels[kShadowProfileCount - 1] * lensUnit;

    if (mask <= 0.0 && sdf > shadowReachPixels + 2.0)
        return float4(linearToSrgb(texA.Sample(linearSampler, input.uv).rgb), 1.0);

    float2 uv = input.uv;
    float2 pixelToUv = 1.0 / resolution;


    // The measured blur radius ramps linearly with materialize progress
    // (BLUR_RADIUS reads 0.5 at visible_fraction 0.5 and 1.0 at 1.0, with
    // regular's maximum exactly 4x clear's).  texGlassB already carries the
    // fully materialized backdrop at the measured radii, so the ramp is the
    // opening from the sharp source to it as the material thickens.
    // At FULL radius: under a crossfade the material's own backdrop is the
    // finished blur, and `thickness` blends the finished material against the
    // sharp source once, at the end.
    float3 sharpIncoming = texB.Sample(linearSampler, uv).rgb;
    float3 blurredIncoming = texGlassB.Sample(linearSampler, uv).rgb;
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

    // Measured on 26.6.1: inputOuterRefractionHeight is exactly 0.125 of the
    // element diameter (0.25 R) at every size probed from 455 to 4328, with
    // no dependence on the window diagonal.  walle used 0.44 R capped by
    // 0.033 * diagonal - nearly twice as wide, and capped where the hardware
    // is not.  The displacement profile below is unchanged; only the band
    // width is corrected.
    // Capture pixels per output pixel: the measurement is in 2x capture pixels
    // and the output is `scale` device pixels per point.  lensUnit is computed
    // with the early-out above, which needs the shadow's reach in the same
    // units.
    float insideRim = (radiusPixels - distancePixels) / lensUnit;

    // The edge's own laws, for this variant.  `clear` casts no shadow and its
    // fitted law is the identity, so the branch is only about which numbers
    // are read, not about which code runs.
    WalleEdge edgeDark = kEdgeClearDark;
    WalleEdge edgeLight = kEdgeClearLight;
    if (regular > 0.5)
    {
        edgeDark = kEdgeRegularDark;
        edgeLight = kEdgeRegularLight;
    }
    float rimBlend = lerp(rimWeight(insideRim, edgeDark),
                          rimWeight(insideRim, edgeLight), lightness);

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
        // The rim is a separate layer with its own colour law AND its own tint
        // law: a tinted rim is nothing like the interior's tint applied to an
        // untinted one, missing it by ninety code values.
        float3 rimSrgb = lerp(edgeTransfer(washSrgb, edgeDark.rim),
                              edgeTransfer(washSrgb, edgeLight.rim), lightness);
        rimSrgb = applyTint(rimSrgb, push.material.yzw, lightness,
                            kRimTintRegularDark, kRimTintRegularLight);
        platterSrgb = lerp(platterSrgb, rimSrgb, rimBlend);
        glassColor = srgbToLinear(platterSrgb);
    }
    else
    {
        // The measured profile, in capture pixels, converted back to the
        // output's own.  The displacement is INWARD, hence the negation below.
        //
        // `inside` is clamped rather than merely gated: outside the element the
        // raw value is negative, and it passes through -12.62 exactly, where
        // the denominator is zero.  Multiplying that infinity by a zero gate
        // gives a NaN, not a zero.
        float inside = max(insideRim, 0.0);
        float remaining = max(kLensBandCapturePixels - inside, 0.0);
        float bend = lensUnit * kLensAmplitude * pow(remaining, kLensProfilePower)
            / (inside + kLensProfileOffset)
            * step(0.0, insideRim);
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
        float3 preSrgb = linearToSrgb(lensed);
        // Measured 26.6.1 clear: one colour matrix, IDENTICAL in both
        // appearances (the gray ladder matches to the code across light and
        // dark), so no appearance interpolation here.
        float3 veiled = materialTransfer(preSrgb, kMaterialClearDark);
        veiled = applyTint(veiled, push.material.yzw, lightness,
                           kTintClearDark, kTintClearLight);
        // `clear`'s rim reaches 130 code values above its interior over black,
        // the largest edge either variant carries.
        float3 rimSrgb = lerp(edgeTransfer(preSrgb, edgeDark.rim),
                              edgeTransfer(preSrgb, edgeLight.rim), lightness);
        rimSrgb = applyTint(rimSrgb, push.material.yzw, lightness,
                            kRimTintClearDark, kRimTintClearLight);
        veiled = lerp(veiled, rimSrgb, rimBlend);
        glassColor = srgbToLinear(veiled);
    }

    // The crossfade, applied once: sharp backdrop to finished material, in
    // sRGB CODE space.  That is where it was measured - alpha is the code-space
    // ratio (frame - backdrop) / (settled - backdrop), and one alpha per frame
    // explains the whole frame to 1.5 code values there - and it is the space
    // the transfer, the blur and the tint were all measured in too.  Fading in
    // linear light instead runs AHEAD of the hardware by up to 0.13 of full
    // scale mid-animation, which is 36 code values over a structured backdrop.
    glassColor = srgbToLinear(lerp(linearToSrgb(sharpIncoming),
                                   linearToSrgb(glassColor), thickness));
    float3 incoming = texB.Sample(linearSampler, input.uv).rgb;
    float3 insideColor = lerp(glassColor, incoming, tFade);
    float3 background = texA.Sample(linearSampler, input.uv).rgb;
    // The measured shadow: a colour law on the raw backdrop, weighted by depth
    // outside the boundary and by how far the material has materialized.
    // `clear` casts none, and its fitted law is the identity, so this costs it
    // nothing but the evaluation.
    float3 backgroundSrgb = linearToSrgb(background);
    float3 shadowSrgb = lerp(
        edgeTransfer(backgroundSrgb, edgeDark.shadow),
        edgeTransfer(backgroundSrgb, edgeLight.shadow), lightness);
    float shadowBlend = lerp(shadowWeight(-insideRim, edgeDark),
                             shadowWeight(-insideRim, edgeLight), lightness);
    float3 shadowedBackground = srgbToLinear(lerp(
        backgroundSrgb, shadowSrgb, shadowBlend * thickness));
    float3 result = lerp(shadowedBackground, insideColor, mask);

    float3 encoded = linearToSrgb(clamp(result, 0.0, 1.0));
    encoded += triangularDither(fragmentPixels + time * 61.0)
        * kDitherLsb * (1.0 - tFade);
    return float4(encoded, 1.0);
}
