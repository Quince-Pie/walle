implementing liquidglass;

// CAFilter "glassBackground" — QuartzCore `glass_background_base<bleed>` and its entry points
// glass_background{,_no_bleed}{,_sdf}_{lph,lpf}.

/// `GlassBackgroundUniforms` (224 bytes for T = half, 272 bytes for T = float). Field names and
/// offsets are QuartzCore's; the CPU side that fills them is `glassBackgroundUniforms` in lg_params.slang.
public struct GlassBackgroundUniforms<T : __BuiltinFloatingPointType>
{
    /// 2x2 matrix taking an SDF gradient (points) to a backdrop-texture UV offset.
    public float4 displacement_mat;
    public float inner_refraction_amount;
    public float inner_refraction_inv_height;
    public float outer_refraction_amount;
    public float outer_refraction_inv_height;
    public float refraction_threshold0;
    public float refraction_threshold1;
    /// Blur radius in backdrop texels.
    public float blur_radius;
    public float edge_bleed_blur_radius;
    public float edge_bleed_amount;
    public float edge_bleed_inv_height;
    public float shadow_amount;
    public float shadow_inv_height;
    /// Offset added to the SDF lookup position for the shadow (points, or SDF-texture UV).
    public float2 shadow_offset;
    public float shadow_blur_radius;
    public float shadow_inv_radius;
    /// Colour matrices as rows (m0, m1, m2, bias).
    public half4 face_cm0;
    public half4 face_cm1;
    public half4 face_cm2;
    public half4 bleed_cm0;
    public half4 bleed_cm1;
    public half4 bleed_cm2;
    public half4 shadow_cm0;
    public half4 shadow_cm1;
    public half4 shadow_cm2;
    public float shadow_contribution;
    public float shadow_face_opacity;
    public T blur_alpha0;
    public T blur_alpha1;
    public T blur_alpha2;
    public T blur_alpha3;
    public T blur_dist0;
    public T blur_dist1;
    public T blur_dist2;
    public T blur_dist3;
    public T edge_bleed_dist0;
    public T edge_bleed_dist1;
    public T edge_bleed_opacity;
    public T face_opacity;
    public vector<T, 2> bleed_darken;
    public T shadow_dist_offset;
    public T shadow_opacity;
    public T refraction_opacity;
    public T holding_tone_opacity;
    public T sdr_shadow_dist0;
    public T sdr_shadow_dist1;
    public T clamp_limit;
    public T preserve_hue;
    public T sdr_white_value;
    public T x86_workaround;
    public T complex_refraction;
}

/// `GlassBackgroundUniformsSdf`: element SDF followed by the glass uniforms.
public struct GlassBackgroundUniformsSdf<T : __BuiltinFloatingPointType>
{
    public SdfFragmentUniforms sdf;
    public GlassBackgroundUniforms<T> glass;
}

vector<T, 3> applyColorMatrix<T : __BuiltinFloatingPointType>(vector<T, 3> c, half4 m0, half4 m1, half4 m2)
{
    return vector<T, 3>(dot(c, vector<T, 3>(m0.xyz)), dot(c, vector<T, 3>(m1.xyz)), dot(c, vector<T, 3>(m2.xyz)));
}

vector<T, 3> colorMatrixBias<T : __BuiltinFloatingPointType>(half4 m0, half4 m1, half4 m2)
{
    return vector<T, 3>(T(m0.w), T(m1.w), T(m2.w));
}

/// Blur-radius modulation for a refracted tap: the base radius is attenuated by up to three ramps
/// over the refracted depth (`blur_dist*` breakpoints, `blur_alpha*` weights).
T refractedBlurRadius<T : __BuiltinFloatingPointType>(T depth, float3 invSpan, float3 bias, GlassBackgroundUniforms<T> u)
{
    let w = vector<T, 3>(saturate(fma(float3(depth.toFloat()), invSpan, bias)));
    let a = vector<T, 3>(u.blur_alpha1, u.blur_alpha2, u.blur_alpha3) * w;
    let alpha = u.blur_alpha0 - (a.x + a.y + a.z);
    return T(u.blur_radius * alpha.toFloat());
}

/// Liquid Glass backdrop shading for one fragment.
///  srcUV:     backdrop (blur pyramid) UV of this fragment.
///  shadowSDF: (distance, alpha) of the shape sampled at position + shadow_offset.
///  dist:      signed distance to the shape (points, negative inside).
///  grad:      SDF gradient (outward normal) in points.
///  coverage:  antialiased shape coverage.
/// Returns premultiplied colour: the drop shadow outside the shape, the refracted/blurred/tinted
/// backdrop inside, blended by coverage.
public vector<T, 4> glassBackground<T : __BuiltinFloatingPointType, let kBleed : bool>(
    float2 srcUV, vector<T, 2> shadowSDF, T dist, float2 grad, T coverage,
    Texture2D<vector<T, 4>> source, SamplerState sourceSampler, GlassBackgroundUniforms<T> u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let x86 = u.x86_workaround.toFloat() != 0.0f;
    let n = vector<T, 2>(T(dot(grad, u.displacement_mat.xy)), T(dot(grad, u.displacement_mat.zw)));
    let uv = vector<T, 2>(srcUV);

    // Drop shadow, drawn where the shape is not fully opaque.
    vector<T, 4> shadow = vector<T, 4>(T(0.0));
    if (coverage < T(1.0))
    {
        let depth = -(u.shadow_dist_offset + dist);
        let lift = bevelProfile(depth * T(u.shadow_inv_height), T(u.shadow_amount));
        let shadowUV = vector<T, 2>(lift) * n + uv;

        // Gaussian-like falloff of the shadow edge: an odd polynomial in half precision.
        let x = half(u.shadow_inv_radius * shadowSDF.x.toFloat());
        let s = saturate(x * 0.25h + 0.5h) * 4.0h + -2.0h;
        let s2 = s * s;
        let falloff = fma(fma(fma(fma(0.002954h, s2, -0.03445h), s2, 0.1682h), s2, -0.5605h), s, 0.5h);
        let alpha = T(falloff) * shadowSDF.y * u.shadow_opacity;
        if (alpha < T.glassEps() && coverage == T(0.0))
            discard;

        vector<T, 4> c;
        if (u.shadow_contribution > T.glassEps().toFloat())
        {
            let tap = source.SampleLevel(sourceSampler, float2(shadowUV), blurLevel(T(u.shadow_blur_radius)).toFloat());
            let rgb = flushNegligible(unpremultiply(tap));
            let tinted = applyColorMatrix(rgb, u.shadow_cm0, u.shadow_cm1, u.shadow_cm2) * T(u.shadow_contribution)
                + colorMatrixBias<T>(u.shadow_cm0, u.shadow_cm1, u.shadow_cm2);
            c = vector<T, 4>(tinted, glassMixF<T>(u.shadow_face_opacity, 1.0f, u.shadow_contribution, x86));
        }
        else
        {
            c = vector<T, 4>(colorMatrixBias<T>(u.shadow_cm0, u.shadow_cm1, u.shadow_cm2), T(u.shadow_face_opacity));
        }
        shadow = c * vector<T, 4>(alpha);
    }

    // Glass body.
    vector<T, 4> body = vector<T, 4>(T(0.0));
    if (coverage > T(0.0))
    {
        vector<T, 4> c;
        if (u.complex_refraction.toFloat() != 0.0f)
        {
            // Inner refraction: displace along the normal by the bevel height at this depth.
            let innerAmount = T(u.inner_refraction_amount);
            let inner = bevelProfile(T(u.inner_refraction_inv_height) * -dist, innerAmount);
            let d0 = float3(u.blur_dist0.toFloat(), u.blur_dist1.toFloat(), u.blur_dist2.toFloat());
            let d1 = float3(u.blur_dist1.toFloat(), u.blur_dist2.toFloat(), u.blur_dist3.toFloat());
            let span = d1 - d0;
            let invSpan = float3(1.0f) / span;
            let bias = -d0 / span;
            let innerRadius = refractedBlurRadius(inner + dist, invSpan, bias, u);
            let innerUV = uv + vector<T, 2>(inner) * n;
            var tap = source.SampleLevel(sourceSampler, float2(innerUV), blurLevel(innerRadius).toFloat());
            c = vector<T, 4>(flushNegligible(tap.rgb), tap.a);

            if (u.refraction_opacity > T(0.0))
            {
                // Outer refraction, faded in between refraction_threshold0 and refraction_threshold1.
                let outerAmount = T(u.outer_refraction_amount);
                let outer = bevelProfile(T(u.outer_refraction_inv_height) * -dist, outerAmount);
                let outerRadius = refractedBlurRadius(outer + dist, invSpan, bias, u);
                let outerUV = vector<T, 2>(outer) * n + uv;
                let tap2 = source.SampleLevel(sourceSampler, float2(outerUV), blurLevel(outerRadius).toFloat());
                let c2 = vector<T, 4>(flushNegligible(tap2.rgb), tap2.a);
                let fade = T(glassRamp(float1(dist.toFloat()), float1(u.refraction_threshold0), float1(u.refraction_threshold1)).x);
                c = glassMix(c, c2, u.refraction_opacity * fade, x86);
            }
        }
        else
        {
            let tap = source.SampleLevel(sourceSampler, srcUV, blurLevel(T(u.blur_radius)).toFloat());
            c = vector<T, 4>(flushNegligible(tap.rgb), tap.a);
        }

        // Face tint.
        let rgb = unpremultiply(c);
        vector<T, 4> col;
        if (u.face_opacity > T(0.0))
        {
            let face = applyColorMatrix(rgb, u.face_cm0, u.face_cm1, u.face_cm2) + colorMatrixBias<T>(u.face_cm0, u.face_cm1, u.face_cm2);
            col = vector<T, 4>(glassMix(rgb, face, u.face_opacity, x86), T(1.0));
        }
        else
        {
            col = vector<T, 4>(rgb, T(1.0));
        }

        // Edge bleed: light from further out, pulled in along the normal near the rim.
        if (kBleed)
        {
            let bleedAmount = T(u.edge_bleed_amount);
            let bleed = bevelProfile(-dist * T(u.edge_bleed_inv_height), bleedAmount);
            let bleedUV = vector<T, 2>(bleed) * n + uv;
            let tap = source.SampleLevel(sourceSampler, float2(bleedUV), blurLevel(T(u.edge_bleed_blur_radius)).toFloat());
            let brgb = flushNegligible(unpremultiply(tap));
            let tint = applyColorMatrix(brgb, u.bleed_cm0, u.bleed_cm1, u.bleed_cm2) + colorMatrixBias<T>(u.bleed_cm0, u.bleed_cm1, u.bleed_cm2);
            let ramp = T(glassRamp(float1(dist.toFloat()), float1(u.edge_bleed_dist0.toFloat()), float1(u.edge_bleed_dist1.toFloat())).x);
            let luma = saturate(dot(col.rgb, vector<T, 3>(T(0.2125f), T(0.7154f), T(0.0721f))));
            var k = u.bleed_darken.x * luma + u.bleed_darken.y;
            k = k * k;
            k = k * ramp;
            k = k * k;
            k = k * u.edge_bleed_opacity;
            col = vector<T, 4>(glassMix(col.rgb, tint, k, x86), col.a);
        }
        body = col;
    }

    var o = glassMix(shadow, body, coverage, x86);

    // SDR holding tone: remap towards sdr_white_value near the rim.
    if (u.holding_tone_opacity > T(0.0))
    {
        T h;
        if (u.sdr_shadow_dist0 > dist)
            h = T(1.0);
        else if (!(u.sdr_shadow_dist1 <= dist))
            h = glassMixS(T(1.0), T(0.0), (dist - u.sdr_shadow_dist0) / (u.sdr_shadow_dist1 - u.sdr_shadow_dist0), x86);
        else
            h = T(0.0);
        let a = saturate(o.a);
        let held = vector<T, 4>(vector<T, 3>(u.sdr_white_value) * o.rgb * vector<T, 3>(a) / vector<T, 3>(max(o.a, T.glassEps())), a);
        o = glassMix(o, held, h * u.holding_tone_opacity, x86);
    }

    // Clamp un-premultiplied colour to clamp_limit (optionally preserving hue).
    if (u.clamp_limit > T(0.0))
    {
        var rgb = o.rgb / vector<T, 3>(max(o.a, T.glassEps()));
        if (u.preserve_hue > T(0.0))
        {
            let m = max(max(rgb.x, rgb.y), rgb.z);
            if (m > u.clamp_limit)
                rgb = vector<T, 3>(u.clamp_limit / m) * rgb;
        }
        else
        {
            rgb = clamp(rgb, vector<T, 3>(T(-0.75)), vector<T, 3>(u.clamp_limit));
        }
        o = vector<T, 4>(rgb * vector<T, 3>(o.a), o.a);
    }

    return vector<T, 4>(vector<T, 3>(edrScale) * o.rgb, o.a);
}

/// Entry-point body for the analytic-SDF variants (`glass_background_sdf*`).
public vector<T, 4> glassBackgroundSDF<T : __BuiltinFloatingPointType, let kBleed : bool>(
    float2 sdfPos, float2 srcUV, Texture2D<vector<T, 4>> source, SamplerState sourceSampler,
    GlassBackgroundUniformsSdf<T> u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let mode = int(u.sdf.arg.z);
    T coverage = T(0.0);
    float2 grad = float2(0.0f);
    T dist = T(0.0);
    if (mode > -1)
    {
        let s = computeSDFWithMode<T>(sdfPos, mode, u.sdf);
        dist = s.x;
        grad = float2(s.yz);
        coverage = sdfCoverage(s.x, s.w);
    }
    vector<T, 2> shadowSDF = vector<T, 2>(T(0.0));
    if (coverage < T(1.0))
    {
        let s = computeSDFWithMode<T>(u.glass.shadow_offset + sdfPos, abs(mode) | 4, u.sdf);
        shadowSDF = s.xw;
    }
    return glassBackground<T, kBleed>(srcUV, shadowSDF, dist, grad, coverage, source, sourceSampler, u.glass, edrScale);
}

/// Entry-point body for the SDF-texture variants (`glass_background`, `glass_background_no_bleed`).
/// The SDF texture holds (distance, gradient.x, gradient.y, alpha).
public vector<T, 4> glassBackgroundTexture<T : __BuiltinFloatingPointType, let kBleed : bool>(
    float2 srcUV, float2 sdfUV, Texture2D<vector<T, 4>> source, SamplerState sourceSampler,
    Texture2D<vector<T, 4>> sdf, SamplerState sdfSampler, GlassBackgroundUniforms<T> u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let s = sdf.Sample(sdfSampler, sdfUV);
    let coverage = sdfCoverage(s.x, s.w);
    vector<T, 2> shadowSDF = vector<T, 2>(T(0.0));
    if (coverage < T(1.0))
        shadowSDF = sdf.Sample(sdfSampler, u.shadow_offset + sdfUV).xw;
    return glassBackground<T, kBleed>(srcUV, shadowSDF, s.x, float2(s.yz), coverage, source, sourceSampler, u, edrScale);
}
