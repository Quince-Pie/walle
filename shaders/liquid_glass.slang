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
// The two wallpapers' WIDE ("bleed") fields at the bake's 8x-reduced grid, in
// PANEL CODE space (session 202).
[[vk::binding(6, 0)]] Texture2D<float4> texWideA;
[[vk::binding(7, 0)]] Texture2D<float4> texWideB;

struct ComposePush
{
    // Two float4 lanes keep the Vulkan and C layouts identical without
    // relying on scalar-versus-std430 member alignment.
    float4 timeline; // time, resolution.x, resolution.y, variant
    float4 geometry; // centerTopLeft.x, centerTopLeft.y, radius, appleBlend
    float4 material; // appearance (0 dark, 1 light), tint rgb (<0 in y = untinted)
    float4 scaling;  // device pixels per point, then three unused lanes
    float4 wide;     // enable, wide sigma in output px, two unused lanes
};

struct ComposeVertexOutput
{
    float4 position : SV_Position;
    [[vk::location(0)]] float2 uv;
};

// --- Measured wallpaper-transition arrival, macOS 26.6.1 (25G76) -----------
//
// Captured on the authorized M1 (MacBookPro18,2, Retina at backing scale 2,
// Reduce Transparency and Reduce Motion off, no preflight errors) with the
// rig's own `wallpaper-transition` mode - probeRole
// "walle-two-wallpaper-expansion", the one probe that animates exactly what
// walle animates.  Four sequences, 61 frames each, read as the deep-interior
// code-space alpha against that sequence's own settled material.
//
// THE MATERIAL DOES NOT ARRIVE.  It is already there.  Apple reads alpha 1.01
// in the FIRST frame that clears the lens band - 43 to 54 ms into a one second
// animation - and holds 0.98 to 1.02 until the exit begins.  The rig's
// exact-state sweeps say the same thing from the other side: their interior is
// 222.0 at progress 1/16 and 220.0 at 1, flat to two code values across every
// radius, so a settled element carries its full material at any size.
//
// This retires the materialize ease - kMaterialize{Clear,Regular}, still in
// material_law.slang and no longer read from here - that used to be replayed
// over [0, 0.66).  That ease is real, but it belongs to Apple's `materialize`
// probe - a glass element being INSERTED - and a wallpaper reveal is a
// different animation.  Replaying it here left the glass at thickness 0.03
// where the hardware reads 1.00, which over an eight second transition is a
// bare circle of raw wallpaper for the first three seconds.
//
// Only the EXIT is animated, and it is a plain power of the remaining window.
// Fitted over all 224 dynamic frames: 0.037 rms, against 0.564 for the curve
// this replaces - and the old curve's worst frame was off by 1.148 of full
// scale, i.e. the entire material.
//
//     x = dematerialize start, y = exponent
// The exit start is a SCHEDULING LOTTERY: ten repeat measurements of the
// same animation land in two clusters, 0.691 +-0.003 and 0.732 +-0.004 -
// ~2.4 frames apart at 61 Hz, drawn per sequence (one capture run held one
// of each).  The un-delayed schedule is the law; regular's fitted start
// already sits on it, and clear's 0.7030 was the average of a
// cluster-contaminated fit - its early-cluster mean is 0.6908.  Both
// variants share one start.  Scoring against any single capture still
// inherits that run's draw; the exponent awaits a cluster-aligned refit.
// Exponents refit on cluster-ALIGNED curves (each sequence shifted to its
// own measured start, 372 pooled points across six capture runs): clear's
// 1.720 was fit on the same contaminated mix as its start and is really
// 2.075 (aligned |resid| 0.051 -> 0.017); regular's 1.585 is confirmed
// within noise and stands.
static const float2 kDematerializeClear   = float2(0.6908, 2.075);
static const float2 kDematerializeRegular = float2(0.6910, 1.585);


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
// sRGB-linear <-> panel-linear colorants, the same pair the bake uses
// (walle.c kGlassToPanelLinear / kGlassFromPanelLinear).
static const float3x3 kToPanelLinear = float3x3(
    0.8225172, 0.1774401, -0.0000221,
    0.0331941, 0.9667933, -0.0000244,
    0.0171003, 0.0724382,  0.9108519);
static const float3x3 kFromPanelLinear = float3x3(
     1.2248519, -0.2248045, 0.0000237,
    -0.0420549,  1.0420637, 0.0000269,
    -0.0196507, -0.0786527, 1.0978707);

// Standard-normal CDF, tanh form - the fraction of a Gaussian kernel that
// falls on the inside of a locally straight boundary at signed depth z.
float normalCdf(float z)
{
    float t = 0.7978845608 * z * (1.0 + 0.044715 * z * z);
    return 0.5 * (1.0 + tanh(t));
}

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

    // How the material ARRIVES and LEAVES - see kDematerializeClear above for
    // the measurement.  The material is at full thickness from the first frame
    // and only the exit is animated, so there is one curve here, not two.
    //
    // It is a CROSSFADE.  One alpha per frame explains the whole frame to 1.5
    // code values - and that is the discriminator, because a ramping blur
    // radius could not: over a structured backdrop a pixel in fine detail
    // would reach the material sooner than one on a plateau, and none do.
    // So thickness is applied ONCE, at the end, between the sharp backdrop and
    // the finished material.  It used to be threaded through the blur, the
    // refraction and the transfer separately, which is a different function of
    // k with the same endpoints.
    float2 arrival = regular > 0.5 ? kDematerializeRegular : kDematerializeClear;
    float tFade = saturate((time - arrival.x) / (1.0 - arrival.x));
    float thickness = pow(1.0 - tFade, arrival.y);

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

    // Apple's shadow is cast by the element DISPLACED DOWNWARD by
    // shadow.offset.height = 8 pt (DesignLibrary Parameters, 26.6.1;
    // confirmed on the captures - the integrated darkening below a 500 pt
    // circle is 3.5x the darkening above, left and right identical).  The
    // shadow's depth is therefore measured against that displaced circle,
    // not the element's own boundary.  push.scaling.z carries the offset in
    // POINTS (0 = walle's old radially symmetric shadow).
    float shadowOffsetPixels = push.scaling.z * max(push.scaling.x, 1.0);
    float2 shadowCenterPixels = centerPixels - float2(0.0, shadowOffsetPixels);
    float shadowDistancePixels = length(fragmentPixels - shadowCenterPixels);
    float shadowDepth = (shadowDistancePixels - radiusPixels) / lensUnit;

    if (mask <= 0.0 && sdf > shadowReachPixels + shadowOffsetPixels + 2.0)
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
    // The blurred backdrop is read through the lens below, for both variants,
    // so there is no unlensed fetch of it here: outside the band the
    // displacement is zero and the lensed read IS the unlensed one.
    float3 sharpIncoming = texB.Sample(linearSampler, uv).rgb;
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
    // Coverage-centroid sampling at the AA boundary (session 195): walle
    // evaluates the material once at the PIXEL CENTER and blends by mask
    // coverage, so the lens's onset (step at insideRim = 0 below) and the
    // rim's depth switch discontinuously as the center crosses the analytic
    // circle - the R8-keyed blend-curve instrument reads walle jumping at
    // m = 0.5 where Apple moves smoothly.  True rasterization AA evaluates
    // the covered sliver, whose centroid for a straight edge at coverage m
    // sits m/2 DEVICE pixels inside the edge.  Sampling the material's
    // depth-keyed laws there is continuous with the interior (at m = 1 the
    // centroid depth is 0.5 px <= any interior center depth) and leaves
    // full-coverage pixels byte-identical.  No fitted constants.
    bool aaBoundary = mask > 0.0 && mask < 1.0;
    float insideRimMaterial = aaBoundary
        ? max(insideRim, (0.5 * mask) / lensUnit)
        : insideRim;
    float outsideDepthShadow = aaBoundary
        ? max(shadowDepth, (0.5 * (1.0 - mask)) / lensUnit)
        : shadowDepth;

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
    float rimBlend = lerp(rimWeight(insideRimMaterial, edgeDark),
                          rimWeight(insideRimMaterial, edgeLight), lightness);

    // THE RIM IS DIRECTIONAL (session 201).  Apple's Parameters carry a
    // `highlights` layer with a key and a fill lobe, and the captures show it
    // plainly: on gray-128 under a 500 pt circle the outermost 3 capture px
    // carry a cos(theta - phi) modulation with phi = 315 deg (bottom-right)
    // in three of the four cases - clear/light 8.45 codes, clear/dark 7.62,
    // regular/dark 13.76 (residual 0.38 - an almost pure first harmonic) -
    // and regular/light runs the opposite way at 3.84.  Beyond ~3 px the
    // sectors agree to the code, so the term rides the rim's own profile.
    // walle's rim was a function of depth alone, at every angle.
    // push.scaling.w scales it (0 = the old angularly uniform rim).
    float rimDirectional = 0.70710678 * (normalOut.x - normalOut.y);
    float rimDirectionalAmplitude = (regular > 0.5
        ? lerp(13.76, -3.84, lightness)
        : lerp(7.62, 8.45, lightness)) * push.scaling.w * (1.0 / 255.0);

    // The lens, for BOTH variants.  It used to run only for `clear`, on the
    // reasoning - shaders/frag.glsl:41 - that "the regular variant transmits
    // nothing, so it gets no lens at all", which rested on a blur model
    // walle.c:150-153 now retracts as seven to thirty-four times too strong.
    //
    // Measured instead, on the M1 (25G76) wallpaper-transition capture, by ring
    // cross-correlation of the composed frame against the incoming wallpaper at
    // R = 1082: the inward displacement in capture pixels, per depth inside the
    // rim, with the correlation in brackets.
    //
    //     depth            4            8           16           32
    //     clear   light  72.0 (0.97)  50.5 (1.00)  25.5 (1.00)  2.5 (1.00)
    //     regular light  72.0 (0.92)  50.5 (0.99)  25.0 (1.00)  2.5 (1.00)
    //     clear   dark   72.0 (0.98)  50.5 (1.00)  25.5 (1.00)  2.5 (1.00)
    //     regular dark   70.0 (0.63)  47.5 (0.97)  21.5 (0.98) -1.5 (0.98)
    //
    // `regular` carries the same lens as `clear`, to half a pixel in light; it
    // reads softer in dark only because that appearance's wide blur layer leaves
    // less structure to correlate.  The banner at the top of this file says the
    // same from the sine-grating side - "it is the same for both variants and
    // both appearances" - and this branch had simply never been revisited.
    //
    // The displacement is INWARD, hence the negation.  `inside` is clamped
    // rather than merely gated: outside the element the raw value is negative,
    // and it passes through -12.62 exactly, where the denominator is zero.
    // Multiplying that infinity by a zero gate gives a NaN, not a zero.
    // APPLE'S OWN REFRACTION GEOMETRY (session 200, read out of
    // DesignLibrary.framework's GlassMaterialProvider.Parameters on 26.6.1 -
    // not fitted):
    //     refraction.innerHeight  12.00 pt regular / 17.28 pt clear
    //     refraction.innerAmount -38.40    regular / -48.00    clear
    // walle's profile above was pixel-fitted to ONE band and applied to both
    // variants; its 35.58 capture px is clear's band (17.28 pt = 34.56 px) to
    // 3%, so REGULAR has been running a band 44% too wide and an amplitude
    // 25% too strong.  Apple's ratios rescale it: the depth axis compresses by
    // 17.28/12.0 (the band ends at 24.7 px instead of 35.6) and the amplitude
    // scales by 38.4/48.0.  `clear` is left exactly as measured.
    // Session 201: the per-variant reading was an artefact of the DEGENERATE
    // parameter set.  Apple's Parameters are SIZE-DEPENDENT, and the table
    // decoded first (`_nil`, no shape dimensions) is the no-geometry default.
    // At real element sizes - and the wallpaper reveal's disc is enormous -
    // refraction.innerHeight saturates at 20.0 pt for BOTH variants
    // (innerAmount -60), which is exactly walle's own finding that the two
    // variants share one lens.  20.0 pt = 40.0 capture px against walle's
    // pixel-fitted 35.5796: the band is 12.4% wider than fitted.  The depth
    // axis is rescaled so the profile keeps its measured near-edge
    // displacement and reaches zero at 40 px instead of 35.58.
    float lensDepthScale = 1.0;
    float lensAmountScale = 1.0;
    if (push.scaling.y > 0.5)
        lensDepthScale = kLensBandCapturePixels / 40.0;
    float inside = max(insideRimMaterial, 0.0) * lensDepthScale;
    float remaining = max(kLensBandCapturePixels - inside, 0.0);
    float bend = lensUnit * kLensAmplitude * lensAmountScale
        * pow(remaining, kLensProfilePower)
        / (inside + kLensProfileOffset)
        * step(0.0, insideRimMaterial);
    float2 displacementUv = -float2(normalOut.x, -normalOut.y) * bend * pixelToUv;

    // The refraction displaces the backdrop the material samples, which is
    // the measured blur - not the sharp source.  Outside the band the
    // displacement is zero and this is exactly the unlensed backdrop, so the
    // band needs no separate blend to fade into.
    float3 lensed;
    lensed.x = texGlassB.Sample(
        linearSampler, uv + displacementUv * (1.0 + kLensDispersion)).r;
    lensed.y = texGlassB.Sample(linearSampler, uv + displacementUv).g;
    lensed.z = texGlassB.Sample(
        linearSampler, uv + displacementUv * (1.0 - kLensDispersion)).b;
    float3 preSrgb = linearToSrgb(lensed);

    // THE BACKDROP DOES NOT STOP AT THE REVEAL (session 202).  Apple's filter
    // blurs the BACKDROP - the composited screen behind the glass - and
    // outside the growing disc the screen still shows the OUTGOING wallpaper.
    // walle bakes each wallpaper's glass from that wallpaper alone, so its
    // wide field near the boundary averages in incoming content Apple never
    // sees there.  The fix rebuilds the wide field the compositor actually
    // had: the fraction of a Gaussian of sigma `push.wide.y` lying inside the
    // reveal is normalCdf(depth / sigma), and the rest of it sat on the other
    // wallpaper.  The mixture is linear in the wide field, so the correction
    // is exact given both fields:
    //     dMix = (1 - wChroma) * dB - (wLuma - wChroma) * luma(dB)
    // Measured offline against the M1 sweeps, this cuts the interior residual
    // 13-28% mid-transition and exactly 0% at full coverage, where there is
    // no outside left to see.  `clear` carries no wide layer and is skipped.
    if (push.wide.x > 0.5 && regular > 0.5 && mask > 0.0)
    {
        float sigmaWide = max(push.wide.y, 1.0);
        float insideFraction = normalCdf(insideRimMaterial * lensUnit / sigmaWide);
        float3 wideOut = texWideA.Sample(linearSampler, uv).rgb;
        float3 wideIn = texWideB.Sample(linearSampler, uv).rgb;
        float3 deltaWide = (1.0 - insideFraction) * (wideOut - wideIn);
        float wLuma = lerp(0.5164, 0.8846, lightness);
        float wChroma = lerp(0.6120, 0.5420, lightness);
        float3 deltaMix = (1.0 - wChroma) * deltaWide
            - (wLuma - wChroma) * dot(kLuma, deltaWide);
        // The baked glass is sRGB; the mixture lives in panel code space, so
        // the correction is applied there and converted back.
        float3 panelCode = linearToSrgb(mul(kToPanelLinear, srgbToLinear(preSrgb)));
        panelCode = saturate(panelCode + deltaMix);
        preSrgb = linearToSrgb(max(mul(kFromPanelLinear, srgbToLinear(panelCode)), 0.0));
    }

    float3 glassColor;
    if (regular > 0.5)
    {
        // Measured 26.6.1 regular: an affine colour matrix on the blurred
        // backdrop in sRGB space, still selected between the two measured
        // appearance poles by content luminance.
        // The transfer input is the measured backdrop, and only that: an
        // earlier version fed it a nine-tap screen-wide spread and measured
        // MTF 0.11 at period 64 where the hardware transmits 0.50.
        float3 washSrgb = preSrgb;
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
        rimSrgb += rimDirectionalAmplitude * rimDirectional;
        platterSrgb = lerp(platterSrgb, rimSrgb, rimBlend);
        glassColor = srgbToLinear(platterSrgb);
    }
    else
    {
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
        rimSrgb += rimDirectionalAmplitude * rimDirectional;
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
    //
    // ONCE means once.  This used to be followed by a second lerp of the same
    // result back towards `texB.Sample(linearSampler, input.uv)` - byte for
    // byte the same fetch as `sharpIncoming`, because `uv` is `input.uv` and
    // neither is touched between them - weighted by the same tFade that
    // `thickness` already carried.  The exit was therefore quadratic, and its
    // second half ran in LINEAR light, which is precisely the error this
    // comment warns about.
    glassColor = srgbToLinear(lerp(linearToSrgb(sharpIncoming),
                                   linearToSrgb(glassColor), thickness));
    float3 insideColor = glassColor;
    float3 background = texA.Sample(linearSampler, input.uv).rgb;
    // The measured shadow: a colour law on the raw backdrop, weighted by depth
    // outside the boundary and by how far the material has materialized.
    // `clear` casts none, and its fitted law is the identity, so this costs it
    // nothing but the evaluation.
    float3 backgroundSrgb = linearToSrgb(background);
    float3 shadowSrgb = lerp(
        edgeTransfer(backgroundSrgb, edgeDark.shadow),
        edgeTransfer(backgroundSrgb, edgeLight.shadow), lightness);
    float shadowBlend = lerp(shadowWeight(outsideDepthShadow, edgeDark),
                             shadowWeight(outsideDepthShadow, edgeLight), lightness);
    // Amplitude term, measured mask-anchored on the wallpaper sweeps: with
    // both sides profiled in bands at the exact ladder radii, walle's shadow
    // is flat per band (+-0.1 across eleven states - the earlier "decay" was
    // the quiet-ring anchor drifting) and Apple's is flat too, but larger by
    // a ratio that is uniform across the whole penumbra: 1.44/1.46/1.50/1.25
    // (light, bands 0-10/10-25/25-50/50-90) and 1.19/1.23/1.29/1.31 (dark).
    // The colour matrix and the depth profile are right; only the gain is
    // low - the flat-background edge fit under-reads the amplitude on real
    // content.  One factor per appearance, nothing else touched.
    // Session 200: that amplitude deficit was the MISSING SHADOW OFFSET.
    // With the shadow measured against the element displaced down by 8 pt
    // (Apple's shadow.offset), the fitted profile no longer under-reads:
    // regressing Apple's darkening against walle's over the outside band of
    // the coded sweeps returns k = 0.833 (dark) / 0.695 (light) on the old
    // gains, i.e. gains of 1.041 and 1.014 - unity to a few percent.  The
    // 1.25/1.46 fudge is retired and the measured profile stands on its own.
    shadowBlend *= lerp(1.0413, 1.0141, lightness);
    float3 shadowedBackground = srgbToLinear(lerp(
        backgroundSrgb, shadowSrgb, shadowBlend * thickness));
    // EXPERIMENT: the boundary blend happens in sRGB CODE space, like the
    // corpus-proven reveal blend - not in linear light.  R8-keyed rows show
    // walle exact on every full-coverage pixel and 12-15 codes off ONLY on
    // the AA pixel; Apple's AA value sits above even a code-space blend of
    // its neighbours by a symmetric m(1-m) hump.  Step 1 (this change, no
    // fitted constants): blend the material over the background in code
    // space.  Identical to the old path wherever mask is 0 or 255.
    float3 encoded = lerp(linearToSrgb(clamp(shadowedBackground, 0.0, 1.0)),
                          linearToSrgb(clamp(insideColor, 0.0, 1.0)), mask);
    // Step 2: the boundary highlight.  Apple's AA pixel sits ABOVE the
    // code-space blend of its neighbours by a symmetric coverage hump,
    // A * 4m(1-m), zero at full and zero coverage by construction.  A is
    // fitted on the coded sweeps by projection (clear 25.13 dark / 28.7
    // light; regular 19.17 dark / 31.0 light); the natural content is the
    // holdout.  In luma codes, applied to all channels; rides thickness
    // with the material.
    // Refit under the coverage-centroid mechanism (session 195): the
    // centroid sampling supplies the lens/rim share the old constants were
    // absorbing (regular was 19.17/31.0, clear 25.13/28.7).
    float aaHumpA = regular > 0.5
        ? lerp(13.71, 27.47, lightness)
        : lerp(14.62, 17.31, lightness);
    encoded += (aaHumpA / 255.0) * 4.0 * mask * (1.0 - mask) * thickness;
    encoded = saturate(encoded);
    // Dither disabled pending the flat-field verdict: Apple's settled
    // material texture reads 0.08 rms with no periodicity - consistent with
    // plain rounding and NO dither - while this term injected ~0.4 rms the
    // hardware never ships.  The scorer decides (session 193).
    // encoded += triangularDither(fragmentPixels + time * 61.0)
    //     * kDitherLsb * (1.0 - tFade);
    return float4(encoded, 1.0);
}
