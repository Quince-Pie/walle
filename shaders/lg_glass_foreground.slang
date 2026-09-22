implementing liquidglass;

// CAFilter "glassForeground" (QuartzCore glass_foreground_base / glass_foreground{,_sdf}_{lph,lpf}) and the
// lens helpers "displacementMap", "chromaticAberration" and "chromaticAberrationMap".

/// `GlassForegroundUniforms` (80 bytes).
public struct GlassForegroundUniforms
{
    /// 2x2 matrix taking a (rotated) SDF gradient to a source-texture UV offset.
    public float4 displacement_mat;
    public float refraction_amount;
    public float inv_refraction_height;
    public float refraction_offset;
    public float aberration_amount;
    public float inv_aberration_height;
    public float aberration_offset;
    /// (cos, sin) of the dispersion direction relative to the gradient.
    public float aberration_angle_x;
    public float aberration_angle_y;
    public float edge_start;
    public float edge_end;
    public float edge_opacity_start;
    public float edge_opacity_end;
    /// (cos, sin) of the refraction direction relative to the gradient.
    public float refraction_angle_x;
    public float refraction_angle_y;
}

/// `GlassForegroundSdfUniforms`: element SDF followed by the foreground uniforms (128 bytes).
public struct GlassForegroundSdfUniforms
{
    public SdfFragmentUniforms sdf;
    public GlassForegroundUniforms glass;
}

/// Chromatic-dispersion lens drawn over the glass. `sdf` = (distance, gradient.xy, alpha) of the shape.
/// Seven bilinear taps are spread along the dispersion vector: red integrates the outer three,
/// blue the inner four, green all seven with a tent weight; the result is premultiplied by the
/// average tap alpha, the shape coverage and the SDF alpha, then faded by distance.
public vector<T, 4> glassForeground<T : __BuiltinFloatingPointType>(
    float2 srcUV, vector<T, 4> sdf, Texture2D<vector<T, 4>> source, SamplerState sourceSampler,
    GlassForegroundUniforms u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let dist = sdf.x.toFloat();
    let coverage = saturate(0.5f - dist / max(fwidth(dist), 0.0001f));
    let sdfAlpha = sdf.w.toFloat();
    if (sdfAlpha < 0.001f)
        return vector<T, 4>(T(0.0));

    let grad = float2(sdf.yz);
    let m = u.displacement_mat;

    // Refraction: gradient rotated by the refraction angle, scaled by the bevel height.
    let rg = float2(dot(grad, float2(u.refraction_angle_x, -u.refraction_angle_y)), dot(grad, float2(u.refraction_angle_y, u.refraction_angle_x)));
    let refrDir = float2(dot(rg, m.xy), dot(rg, m.zw));
    let refr = bevelProfile(T(-dist - u.refraction_offset) * T(u.inv_refraction_height), T(u.refraction_amount)).toFloat();
    let refrOffset = float2(refr) * refrDir;

    // Dispersion: gradient rotated by the aberration angle; note QuartzCore swaps the matrix rows here.
    let ag = float2(dot(grad, float2(u.aberration_angle_x, -u.aberration_angle_y)), dot(grad, float2(u.aberration_angle_y, u.aberration_angle_x)));
    let aberDir = float2(dot(ag, m.zw), dot(ag, m.xy));
    let aber = bevelProfile(T(-dist - u.aberration_offset) * T(u.inv_aberration_height), T(u.aberration_amount)).toFloat();
    let aberOffset = float2(aber) * aberDir;

    let center = refrOffset + srcUV;
    vector<T, 4> acc = vector<T, 4>(T(0.0));

    // Outer half: t = 1, 2/3, 1/3 along +aberOffset.
    float t = 1.0f;
    [loop]
    for (int i = 0; i < 3; i++)
    {
        let tap = source.Sample(sourceSampler, float2(t) * aberOffset + center);
        let c = tap.xy / vector<T, 2>(max(tap.w, T.glassEps()));
        acc.x = T(t * c.x.toFloat() + acc.x.toFloat());
        acc.y = T((1.0f - t) * c.y.toFloat() + acc.y.toFloat());
        acc.w = tap.w + acc.w;
        t = t + -0.333333343f;
    }
    // Inner half: t = 0, 1/3, 2/3, 1 along -aberOffset.
    t = 0.0f;
    [loop]
    for (int i = 0; i < 4; i++)
    {
        let tap = source.Sample(sourceSampler, center - float2(t) * aberOffset);
        let c = tap.yz / vector<T, 2>(max(tap.w, T.glassEps()));
        acc.y = T((1.0f - t) * c.x.toFloat() + acc.y.toFloat());
        acc.z = T(t * c.y.toFloat() + acc.z.toFloat());
        acc.w = tap.w + acc.w;
        t = t + 0.333333343f;
    }

    let alpha = T(sdfAlpha * 0.142857149f * coverage * acc.w.toFloat());
    let rgb = acc.xyz * vector<T, 3>(T(0.5f), T(0.333333343f), T(0.5f)) * vector<T, 3>(edrScale * alpha);
    let fade = lerp(u.edge_opacity_start, u.edge_opacity_end, glassRamp(float1(dist), float1(u.edge_start), float1(u.edge_end)).x);
    return vector<T, 4>(rgb, alpha) * vector<T, 4>(T(1.0f - fade));
}

/// Entry-point body for glass_foreground_sdf_*: the SDF is evaluated analytically at `sdfPos`.
public vector<T, 4> glassForegroundSDF<T : __BuiltinFloatingPointType>(
    float2 sdfPos, float2 srcUV, Texture2D<vector<T, 4>> source, SamplerState sourceSampler,
    GlassForegroundSdfUniforms u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let sdf = computeSDFWithMode<T>(sdfPos, int(abs(u.sdf.arg.z)), u.sdf);
    return glassForeground<T>(srcUV, sdf, source, sourceSampler, u.glass, edrScale);
}

/// Entry-point body for glass_foreground_*: the SDF comes from a texture.
public vector<T, 4> glassForegroundTexture<T : __BuiltinFloatingPointType>(
    float2 srcUV, float2 sdfUV, Texture2D<vector<T, 4>> source, SamplerState sourceSampler,
    Texture2D<vector<T, 4>> sdf, SamplerState sdfSampler, GlassForegroundUniforms u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    return glassForeground<T>(srcUV, sdf.Sample(sdfSampler, sdfUV), source, sourceSampler, u, edrScale);
}

/// `DisplacementMapUniforms` (32 bytes), shared by displacementMap and chromaticAberrationMap.
public struct DisplacementMapUniforms
{
    /// 2x2 matrix applied to (mask.xy - offset).
    public float4 mat;
    /// Upper bound applied to the displaced UV (displacementMap only).
    public float2 clamp;
    public float2 offset;
}

/// CAFilter "displacementMap": the mask's rg (minus `offset`) through `mat` displaces the source
/// lookup; the result is scaled by the mask's b channel.
public vector<T, 4> displacementMap<T : __BuiltinFloatingPointType>(
    float2 srcUV, float2 maskUV, Texture2D<vector<T, 4>> source, Texture2D<vector<T, 4>> mask, SamplerState smp,
    DisplacementMapUniforms u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let m = mask.Sample(smp, maskUV);
    let d = float2(m.xy) - u.offset;
    let uv = min(u.clamp, float2(dot(d, u.mat.xy), dot(d, u.mat.zw)) + srcUV);
    let c = source.Sample(smp, uv) * vector<T, 4>(m.z);
    return vector<T, 4>(c.rgb * vector<T, 3>(edrScale), c.a);
}

/// CAFilter "chromaticAberrationMap": like displacementMap but red is taken at +offset, green at the
/// centre and blue at -offset; alpha is the mean tap alpha times the mask's b channel.
public vector<T, 4> chromaticAberrationMap<T : __BuiltinFloatingPointType>(
    float2 srcUV, float2 maskUV, Texture2D<vector<T, 4>> source, Texture2D<vector<T, 4>> mask, SamplerState smp,
    DisplacementMapUniforms u, T edrScale)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let m = mask.Sample(smp, maskUV);
    let d = float2(m.xy) - u.offset;
    let off = float2(dot(d, u.mat.xy), dot(d, u.mat.zw));
    let r = source.Sample(smp, off + srcUV);
    let g = source.Sample(smp, srcUV);
    let b = source.Sample(smp, srcUV - off);
    let rgb = vector<T, 3>(r.x / max(r.w, T.glassEps()), g.y / max(g.w, T.glassEps()), b.z / max(b.w, T.glassEps()));
    let alpha = T(m.z.toFloat() * 0.333333343f * ((g.w + r.w) + b.w).toFloat());
    return vector<T, 4>(vector<T, 3>(edrScale * alpha) * rgb, alpha);
}

/// `ChromaticAberrationUniforms` (32 bytes for half, 40 for float).
public struct ChromaticAberrationUniforms<T : __BuiltinFloatingPointType>
{
    public vector<T, 2> r_offset;
    public vector<T, 2> g_offset;
    public vector<T, 2> b_offset;
    public float2 uv_min;
    public float2 uv_max;
}

/// CAFilter "chromaticAberration": constant per-channel UV offsets; taps that land outside
/// [uv_min, uv_max] contribute nothing.
public vector<T, 4> chromaticAberration<T : __BuiltinFloatingPointType>(
    float2 uv, Texture2D<vector<T, 4>> source, SamplerState smp, ChromaticAberrationUniforms<T> u)
    where T : IGlassReal
    where vector<T, 4> : ITexelElement
{
    let uvR = float2(u.r_offset) + uv;
    let uvG = float2(u.g_offset) + uv;
    let uvB = float2(u.b_offset) + uv;
    let r = source.Sample(smp, uvR);
    let g = source.Sample(smp, uvG);
    let b = source.Sample(smp, uvB);
    vector<T, 4> acc = vector<T, 4>(T(0.0));
    if (all(uvR >= u.uv_min) && all(uvR <= u.uv_max))
        acc = vector<T, 4>(r.x / max(r.w, T.glassEps()), T(0.0), T(0.0), r.w);
    if (all(uvG >= u.uv_min) && all(uvG <= u.uv_max))
    {
        acc.w = acc.w + g.w;
        acc.y = g.y / max(g.w, T.glassEps());
    }
    if (all(uvB >= u.uv_min) && all(uvB <= u.uv_max))
    {
        acc.w = acc.w + b.w;
        acc.z = b.z / max(b.w, T.glassEps());
    }
    let a = T(acc.w.toFloat() * 0.333333343f);
    return vector<T, 4>(vector<T, 3>(a) * acc.xyz, a);
}
