implementing liquidglass;

// Core Animation SDF layers (CASDFLayer, CASDFElementLayer, CASDF*Effect) and the blends drawn
// with them. Each function is QuartzCore's UberShader<T, float> helper of the same name
// (fixed_frag_{lph,lpf}_cpf). T is the pixel type: half for 8-bit targets, float otherwise.
//
// Element SDFs are (distance, gradient.xy, alpha) with the distance in points, negative inside.
// A CASDFLayer draws its elements as SDF "images" (see sdfElementImage). Elements after the first are
// merged into the destination with sdfUnion / sdfSubtraction. The effect then turns the merged SDF
// into color (sdfFill, sdfKeyFillHighlight, ...).

// ---------------------------------------------------------------------------------------------
// Shared pieces
// ---------------------------------------------------------------------------------------------

/// Antialiased coverage of the region where `nd` (negated distance) is positive. Used by
/// sdf_fill, sdf_gradient and sdf_gradient_contour.
float edgeCoverage(float nd)
{
    let fw = saturate(max(glassFwidth(nd), 0.0001f));
    return saturate(nd / fw + 0.5f);
}

/// Gaussian-profile falloff, evaluated in half precision in both pixel variants.
/// `s` is the distance in units of the blur sigma; returns about 1 well inside and 0 well outside.
half gaussianFalloff(half s)
{
    let x = saturate(s * 0.25h + 0.5h);
    let y = x * 4.0h + -2.0h;
    let y2 = y * y;
    return fma(fma(fma(fma(half(0.002954483), y2, half(-0.034454346)), y2, half(0.16821289)), y2, half(-0.56054688)), y, 0.5h);
}

// ---------------------------------------------------------------------------------------------
// Element images
// ---------------------------------------------------------------------------------------------

/// Shape of a CASDFElementLayer quad section, selecting QuartzCore's image function.
public enum SdfElementShape
{
    /// rect_sdf_image (uber image function 20): straight edges and interior.
    Rect = 0,
    /// supercircle_sdf_image<false> (21): corners sharing one radius.
    Supercircle = 4,
    /// supercircle_sdf_image<true> (22): corners with individual radii.
    SupercirclePerCorner = 5,
}

/// SDF of one element at `p` (points from the element center). `u` is Uniform::image_arg[0..2].
[ForceInline]
public vector<T, 4> sdfElementImage<T : __BuiltinFloatingPointType>(float2 p, SdfElementShape shape, SdfFragmentUniforms u)
{
    switch (shape)
    {
    case SdfElementShape.Rect: return rectSDFImage<T>(p, u);
    case SdfElementShape.Supercircle: return supercircleSDFImage<T>(p, u);
    default: return supercircleSDFImagePerCorner<T>(p, u);
    }
}

/// sdf_rgba_image (46): an SDF stored in a texture, with `arg` = (distance scale, distance bias,
/// gradient scale, gradient bias) from contentsZeroValueDistance/contentsOneValueDistance.
public vector<T, 4> sdfTextureImage<T : __BuiltinFloatingPointType>(vector<T, 4> texel, float4 arg)
{
    let d = T(arg.x * texel.x.toFloat() + arg.y);
    let g = vector<T, 2>(T(arg.z)) * texel.yz + vector<T, 2>(T(arg.w));
    return vector<T, 4>(d, g, T(1.0));
}

/// supercircle_image (11): coverage of a continuous (squircle) corner, used for the corners of
/// the glass face. `p` is in corner-cell units, |p| = 1 on the curve; `circular` picks per axis
/// between the continuous curve (0) and a circular arc (1).
public T supercircleCoverage<T : __BuiltinFloatingPointType>(float2 p, float2 circular)
{
    let a = abs(p);
    let len = sqrt(dot(a, a));
    let hi = max(a.x, a.y);
    let lo = min(a.x, a.y);
    let t = hi == 0.0f ? 0.0f : saturate(lo / hi);
    let poly = (((3.15601f - t * 0.926054f) * t + -3.64122f) * t + 1.26803f) * t + 0.268531f;
    let continuous = (len + 1.0f) + -1.0f / (1.0f - t * t * poly);
    let m = max(float2(0.0f), a * kContinuousCornerExtent + float2(-0.528665f));
    let circle = sqrt(dot(m, m)) * 0.65416557f + 0.34583443f;
    // QuartzCore rounds the curve distance to half in both pixel variants.
    let d = float(half(lerp(continuous, circle, a.y > a.x ? circular.x : circular.y) + -1.0f));
    return T(saturate(0.5f - d / max(glassFwidth(d + 1.0f), 0.0001f)));
}

// ---------------------------------------------------------------------------------------------
// Effects (CASDF*Effect). Parameter blocks are the members of QuartzCore's Uniform<T> union.
// ---------------------------------------------------------------------------------------------

/// CASDFFillEffect: params0.x = distance offset.
public struct SdfFillParams<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> params0;
    public vector<T, 4> color;
}

/// `d > 1 ? 0 : covered` with `covered` evaluated unconditionally first (QuartzCore computes the
/// coverage, then selects); a separate function so the argument is computed before the select.
T zeroBeyondOne<T : __BuiltinFloatingPointType>(T d, T covered)
{
    return selectFast(d > T(1.0), T(0.0), covered);
}

public vector<T, 4> sdfFill<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, SdfFillParams<T> p)
{
    let d = p.params0.x + sdf.x;
    return vector<T, 4>(zeroBeyondOne(d, sdf.w * T(edgeCoverage((-d).toFloat())))) * p.color;
}

/// CASDFGlassHighlightEffect: params0 = (height, spread, direction.xy), params1 = (amount,
/// curvature, distance offset, -).
public struct SdfGlassHighlightParams<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> params0;
    public vector<T, 4> params1;
    public vector<T, 4> color;
}

/// Preserve the separate per-light fwidth calls present in QuartzCore's AIR through Slang lowering.
/// Their pre-backend structure affects later arithmetic lowering. Apple's final G13S-B0 code then
/// merges them to one dfdx/dfdy pair in every inspected key/fill variant; this helper does not imply
/// two physical derivative evaluations (work/finish/provenance/ARITHMETIC.md).
float fwidthPerLight(float x)
{
    __target_switch
    {
    case metal:
        __intrinsic_asm "fwidth($0)";
    default:
        return glassFwidth(x);
    }
}

/// One highlight light: a rim band `height` deep with a rounded (curvature) profile, times how much
/// the surface normal `n` faces `dir` (0 at cosine `spread` and below, 1 head on). `ds` is the
/// depth inside the shape; zero beyond 5 points outside.
[ForceInline]
T highlightLight<T : __BuiltinFloatingPointType>(float ds, float height, float curvature, vector<T, 2> dir, vector<T, 2> n, T spread, bool glassHighlight = false)
    where T : IGlassReal
{
    let x = saturate(ds / height);
    let profile = lerp(stepBelow(x, 1.0f), 1.0f - x, curvature);
    let fw = max(fwidthPerLight(ds), 0.0001f);
    let inner = saturate(ds / fw + 0.5f);
    let outer = saturate((height - ds) / fw + 0.5f);
    let facing = saturate((dot(dir, n).toFloat() - spread.toFloat()) / max(T(1.0) - spread, T.glassEps()).toFloat());
    // G13S-B0 compiled schedules, directly decoded through Apple's LLVM: half glass-highlight
    // uses ((inner*facing)*outer)*profile; key/fill and float glass-highlight use
    // ((profile*facing)*inner)*outer. See work/finish/provenance/results/dgh_code_1750
    // and generic_bgra's corresponding product at0x1d4c6. The distinction is observable
    // with fractional inner/outer coverage at a half-rounding boundary.
    let v = glassHighlight && sizeof(T) == 2
        ? orderedProduct(inner, facing, outer, profile)
        : orderedProduct(profile, facing, inner, outer);
    return T(select(ds < -5.0f, 0.0f, v));
}

/// Compresses a highlight value v in [0, 1] with `amount` (0 = linear).
[ForceInline]
T highlightTone<T : __BuiltinFloatingPointType>(T v, T amount) where T : IGlassReal
{
    return v / max(T((1.0f - v.toFloat()) * amount.toFloat() + 1.0f), T.glassEps());
}

public vector<T, 4> sdfGlassHighlight<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, SdfGlassHighlightParams<T> p) where T : IGlassReal
{
    let depth = (-(p.params1.z + sdf.x)).toFloat();
    let invLen = T(1.0) / sqrt(dot(sdf.yz, sdf.yz));
    let n = vector<T, 2>(invLen) * sdf.yz;
    let ds = invLen.toFloat() * depth;
    let v = highlightLight<T>(ds, p.params0.x.toFloat(), p.params1.y.toFloat(), p.params0.zw, n, p.params0.y, true);
    if (v < T.glassEps())
        discard;
    return vector<T, 4>(highlightTone(v, p.params1.x)) * p.color;
}

/// CASDFKeyFillHighlightEffect (the Liquid Glass specular rim). params[0] = (key height, key
/// spread, key amount, key direction.x), params[1] = (key direction.y, fill height, fill spread,
/// fill amount), params[2] = (fill direction.xy, curvature, distance offset).
public struct SdfKeyFillHighlightParams<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> params[3];
    public vector<T, 4> color0;
    public vector<T, 4> color1;
}

public vector<T, 4> sdfKeyFillHighlight<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, SdfKeyFillHighlightParams<T> p) where T : IGlassReal
{
    let p0 = p.params[0];
    let p1 = p.params[1];
    let p2 = p.params[2];
    let depth = (-(p2.w + sdf.x)).toFloat();
    T invLen;
#if LG_M1_SF_EMULATION
    if (T is half)
        invLen=glassRsqrt(glassFma(sdf.z,sdf.z,orderedMultiply(sdf.y,sdf.y)));
    else
#endif
        invLen=T(1.0)/sqrt(dot(sdf.yz,sdf.yz));
    let n = vector<T, 2>(invLen) * sdf.yz;
    let ds = invLen.toFloat() * depth;
    let curvature = p2.z.toFloat();
    let key = highlightLight<T>(ds, p0.x.toFloat(), curvature, vector<T, 2>(p0.w, p1.x), n, p0.y);
    let fill = highlightLight<T>(ds, p1.y.toFloat(), curvature, p2.xy, n, p1.z);
    if (fill + key < T.glassEps())
        discard;
    // QuartzCore's expression, left to fast math: the GPU compiler fuses it per pipeline (fma(fill, color1,
    // key * color0) when all four lanes are used; when a vcm blend keeps only alpha it folds each tone's
    // division into its product instead), so pinning either form breaks the other pipelines (measured).
    return vector<T, 4>(highlightTone(key, p0.z)) * p.color0 + vector<T, 4>(highlightTone(fill, p1.w)) * p.color1;
}

/// CASDFGlassDisplacementEffect: writes a refraction normal map (xy = displacement, z = coverage).
/// params0 = (height, curvature, distance offset, coverage offset), params1 = (cos, sin) rotation.
public struct SdfGlassDisplacementParams<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> params0;
    public vector<T, 4> params1;
}

/// `e < -5 ? 0 : covered` as QuartzCore's plain (flagless) select, `covered` evaluated first.
float zeroBelowMinusFive(float e, float covered)
{
    return select(e < -5.0f, 0.0f, covered);
}

public vector<T, 4> sdfGlassDisplacement<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, SdfGlassDisplacementParams<T> p)
{
    let depth = (-(p.params0.z + sdf.x)).toFloat();
    let e = depth - p.params0.w.toFloat();
    let fw = max(glassFwidth(e), 0.0001f);
    let cov = zeroBelowMinusFive(e, saturate(e / fw + 0.5f) * sdf.w.toFloat());
    let h = p.params0.x.toFloat();
    let x = saturate(depth / h);
    let flat = 1.0f - float(int(x < 1.0f)) * 0.2929f;
    let profile = lerp(flat, sqrt(1.0f - (1.0f - x) * (1.0f - x)), p.params0.y.toFloat());
    let r = p.params1;
    let g = vector<T, 2>(dot(sdf.yz, vector<T, 2>(r.x, -r.y)), dot(sdf.yz, vector<T, 2>(r.y, r.x)));
    // (g * (1 - profile)) * rim, in this order (QuartzCore's expression order).
    let bent = vector<T, 2>(T(1.0f - profile)) * g;
    let rim = T(saturate((h - depth) / fw + 0.5f));
    return vector<T, 4>(bent * vector<T, 2>(rim), T(cov), T(1.0));
}

/// CASDFShadowEffect: params0 = (sigma scale, bias, distance offset, 1 / radius).
public struct SdfShadowParams<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> params0;
    public vector<T, 4> color;
}

half shadowFalloff<T : __BuiltinFloatingPointType>(T dist, vector<T, 4> p)
{
    return gaussianFalloff(half(((p.z + dist) * p.w).toFloat() * p.x.toFloat() - p.y.toFloat()));
}

/// `sdf` is the shape's SDF sampled at the shadow offset.
public vector<T, 4> sdfShadow<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, SdfShadowParams<T> p)
{
    return vector<T, 4>(T(shadowFalloff(sdf.x, p.params0)) * sdf.w) * p.color;
}

/// Shadow with the shape itself cut out: `shape` is the unshifted SDF.
public vector<T, 4> sdfShadowPunchout<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, vector<T, 4> shape, SdfShadowParams<T> p)
{
    let p0 = p.params0;
    let shadow = T(shadowFalloff(sdf.x, p0)) * sdf.w;
    let e = ((p0.z + shape.x) * p0.w).toFloat();
    let cut = T(saturate((1.0f - e) / max(glassFwidth(e), 0.0001f) + 0.5f) * shape.w.toFloat());
    return vector<T, 4>(shadow * cut) * p.color;
}

half4 genericPunchoutProduct(half cut, half coverage, half falloff, half4 color)
{
    __target_switch
    {
    case metal:
        __requirePrelude(R"(
static inline half4 lg_generic_punchout_product(half cut, half coverage, half falloff, half4 color)
{
#pragma METAL fp math_mode(safe)
    return ((cut * coverage) * falloff) * color;
}
)");
        __intrinsic_asm "lg_generic_punchout_product($0, $1, $2, $3)";
    default:
        return half4((cut * coverage) * falloff) * color;
    }
}

/// Runtime generic's compiled half schedule differs from the specialized helper.
/// Original G13S-B0 generic: cut*sdf.w at 0x1d3e2, *falloff at 0x1d3e8,
/// then the four colour products at 0x1d3ee..0x1d400.
public half4 sdfShadowPunchoutGeneric(half4 sdf, half4 shape, SdfShadowParams<half> p)
{
    let e = float((p.params0.z + shape.x) * p.params0.w);
    let cut = half(saturate((1.0f - e) / max(glassFwidth(e), 0.0001f) + 0.5f) * float(shape.w));
    return genericPunchoutProduct(cut, sdf.w, shadowFalloff<half>(sdf.x, p.params0), p.color);
}

/// CASDFGradientEffect: color from a 1-D ramp texture indexed by distance.
/// params0 = (distance offset, ramp offset, coverage offset, ramp scale).
public struct SdfGradientParams<T : __BuiltinFloatingPointType>
{
    public float4 params0;
    public vector<T, 4> color;
}

public vector<T, 4> sdfGradient<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, Texture2D<vector<T, 4>> ramp, SamplerState rampSampler, SdfGradientParams<T> p)
    where vector<T, 4> : ITexelElement
{
    let depth = (-sdf.x).toFloat() - p.params0.x;
    let c = ramp.Sample(rampSampler, float2((depth + p.params0.y) * p.params0.w, 0.5f)) * p.color;
    let e = depth + p.params0.z;
    return c * vector<T, 4>(T(edgeCoverage(e) * sdf.w.toFloat()));
}

#if LG_M1_SF_EMULATION
// The invariant texture/coordinate gate preserves the measured nonfinite
// coordinate policy. Filtering uses the source linear/no-mip/clamp sampler.
// Coverage, SDF alpha and the current gradient color remain live.
public half4 sdfGradientHardwareFixedRamp(half4 sdf,Texture2D<half4> ramp,
    SamplerState rampSampler,SdfGradientParams<half> p)
{
    float depth=fmaSafeSubtract(nativeHalfToFloat(-sdf.x),p.params0.x);
    float coordinate=orderedMultiply(fmaSafeAdd(depth,p.params0.y),p.params0.w);
    // Every NaN distance in this extracted fixed profile selects the high edge.
    if((nativeHalfWord(sdf.x)&0x7fffu)>0x7c00u)coordinate=1.0f;
    float2 position=float2(nativeClampSamplerCoordinate(coordinate),0.5f);
    let sampled=ramp.Sample(rampSampler,position);
    let value=half4(roundToHalf(sampled.x.toFloat()),roundToHalf(sampled.y.toFloat()),
        roundToHalf(sampled.z.toFloat()),roundToHalf(sampled.w.toFloat()));
    let c=value*p.color;
    let e=fmaSafeAdd(depth,p.params0.z);
    return c*half4(half(edgeCoverage(e)*sdf.w.toFloat()));
}
#endif

/// CASDFGradientContourEffect. params[0] = (distance offset, 1 / radius, gradient start, gradient
/// end), params[1] = (contour offset, opacity, -, midpoint), params[2].xy = gradient direction.
/// `uv` is the effect quad's texcoord (0..1 over the layer).
public struct SdfGradientContourParams<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> params[3];
    public vector<T, 4> color0;
    public vector<T, 4> color1;
}

public vector<T, 4> sdfGradientContour<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, float2 uv, SdfGradientContourParams<T> p)
{
    let p0 = p.params[0];
    let p1 = p.params[1];
    let dir = p.params[2];
    let t = T((uv.x + -0.5f) * dir.x.toFloat() + (uv.y + -0.5f) * dir.y.toFloat());
    let pos = T((t * T(2.0)).toFloat() * 0.5f + 0.5f);
    let a = saturate((pos - p1.w) / (T(1.0) - p1.w * T(2.0)));
    let b = saturate((pos - p0.z) / (p0.w - p0.z));
    let cA = lerp(p.color0, p.color1, vector<T, 4>(a));
    let cB = lerp(p.color1, p.color0, vector<T, 4>(b));
    let k = p1.y * sdf.w * T(gaussianFalloff(half(((p1.x + sdf.x) * p0.y).toFloat())));
    let over = vector<T, 4>(k) * cB;
    let c = vector<T, 4>(T(1.0) - over.w) * cA + over;
    let e = p0.x + sdf.x;
    let cov = e > T(1.0) ? T(0.0) : T(edgeCoverage((-e).toFloat()) * sdf.w.toFloat());
    return c * vector<T, 4>(cov);
}

/// CASDFVisualizationEffect: debug stripes. params.x = stripe frequency, params.y = distance offset.
public vector<T, 4> sdfVisualization<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, vector<T, 4> params)
{
    let d = params.y + sdf.x;
    let g = sdf.yz * vector<T, 2>(T(0.5)) + vector<T, 2>(T(0.5));
    let s = T(frac(abs(d.toFloat()) * params.x.toFloat()) * 0.5f + 0.5f);
    return vector<T, 4>(vector<T, 2>(s) * g, d < T(0.0) ? T(0.5) : T(1.0), T(1.0));
}

/// Layer rim (CALayer rimWidth / softRim) drawn from an SDF.
public vector<T, 4> sdfRim<T : __BuiltinFloatingPointType>(vector<T, 4> sdf, vector<T, 4> color, T width, bool soft)
{
    let d = sdf.x.toFloat();
    let fw = T(max(glassFwidth(d), 0.0001f)).toFloat();
    let w = width.toFloat();
    var r = T(saturate(d / fw + 0.5f)) * T(saturate((w - d) / fw + 0.5f));
    if (soft)
        r = r * T(half(2.0) * gaussianFalloff(half(saturate((d - fw * 0.25f) / w)) * 2.0h));
    return vector<T, 4>(r) * color;
}

/// Resolves a Gaussian-blurred SDF (CASDFLayer gaussianRadius): dist / weight, renormalized gradient.
public vector<T, 4> sdfGaussianResolve<T : __BuiltinFloatingPointType>(vector<T, 4> sdf)
{
    if (sdf.w < T(0.5))
        discard;
    return vector<T, 4>(sdf.x / sdf.w, normalize(sdf.yz), T(1.0));
}

// ---------------------------------------------------------------------------------------------
// Blends (fragment-fetch of the destination)
// ---------------------------------------------------------------------------------------------

/// Destination for the SDF blends; an empty (alpha 0) pixel acts as "very far outside".
vector<T, 4> sdfDestination<T : __BuiltinFloatingPointType>(vector<T, 4> dst)
{
    return dst.w == T(0.0) ? vector<T, 4>(T(10000.0), T(0.0), T(0.0), dst.w) : dst;
}

/// CASDFElementLayer operation "union": smooth minimum of the element SDF `src` into `dst`.
/// The blend width is the layer smoothness scaled down where the two gradients agree.
public vector<T, 4> sdfUnion<T : __BuiltinFloatingPointType>(vector<T, 4> src, vector<T, 4> dst, T smoothness)
{
    let d = sdfDestination(dst);
    let w = saturate(T(0.5) - dot(src.yz, d.yz) * T(0.5)) * smoothness;
    let h = saturate((d.x - src.x) * T(0.5) / w + T(0.5));
    let dist = T(lerp(d.x.toFloat(), src.x.toFloat(), h.toFloat()) - (w * h * (T(1.0) - h)).toFloat());
    return vector<T, 4>(dist, lerp(d.yz, src.yz, vector<T, 2>(h)), saturate(T(1.0)));
}

/// CASDFElementLayer operation "subtract": smooth maximum of `dst` and the inverted element.
public vector<T, 4> sdfSubtraction<T : __BuiltinFloatingPointType>(vector<T, 4> src, vector<T, 4> dst, T smoothness)
{
    let d = sdfDestination(dst);
    let w = (dot(src.yz, d.yz) * T(0.5) + T(0.5)) * smoothness;
    let h = saturate((d.x + src.x) * T(0.5) / w + T(0.5));
    let dist = T(lerp((-src.x).toFloat(), d.x.toFloat(), h.toFloat()) + ((T(1.0) - h) * h * w).toFloat());
    return vector<T, 4>(dist, lerp(-src.yz, d.yz, vector<T, 2>(h)), T(1.0));
}

/// Blend function 22 (`adi`, compositing filter plusLIgnoreAlpha; also capture_detached's downsample pass): the
/// source color is added to the destination and the destination alpha is kept, `(src.rgb, 0) + dst`. QuartzCore
/// normally lifts it into the fixed-function blend (rgb One + One, alpha Zero + One, blend_function_info
/// @0x18a988a28 entry 22); this is the in-shader form its generic pipeline runs once push_blend_function has moved
/// the blend into the shader. As for every in-shader blend, the blended alpha is saturated afterwards.
public vector<T, 4> addIgnoreAlpha<T : __BuiltinFloatingPointType>(vector<T, 4> src, vector<T, 4> dst)
{
    let o = vector<T, 4>(src.xyz, T(0.0)) + dst;
    return vector<T, 4>(o.xyz, saturate(o.w));
}

/// Uniform::blend_arg for vibrant_color_matrix_sover: columns m[0..3] multiply the unpremultiplied
/// destination r, g, b and its alpha, m[4] is the bias; opts.x = clamp limit (0 = off),
/// opts.y > 0 preserves hue while clamping.
public struct VibrantColorMatrix<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> m[5];
    public vector<T, 4> opts;
}

/// CAFilter "vibrantColorMatrix" applied as a blend: the destination is recolored through the
/// matrix, masked by the source alpha, and composited source-over onto the destination.
public vector<T, 4> vibrantColorMatrixSourceOver<T : __BuiltinFloatingPointType>(vector<T, 4> src, vector<T, 4> dst, VibrantColorMatrix<T> vcm) where T : IGlassReal
{
    let u = dst.xyz / vector<T, 3>(max(dst.w, T.glassEps()));
    var lin = vector<T, 4>(u.x) * vcm.m[0] + vector<T, 4>(u.y) * vcm.m[1] + vector<T, 4>(u.z) * vcm.m[2] + vcm.m[3] * vector<T, 4>(dst.w) + vcm.m[4];
    // Original half VCM: G+bias, R, B, A are four distinct FMAs.
    // Preserve the prior float specialization until its schedule is qualified.
    if (T is half)
        lin = glassFma(vcm.m[3], vector<T, 4>(dst.w),
                  glassFma(vcm.m[2], vector<T, 4>(u.z),
                      glassFma(vcm.m[0], vector<T, 4>(u.x),
                          glassFma(vcm.m[1], vector<T, 4>(u.y), vcm.m[4]))));
    let a = saturate(lin.w);
    var c = vector<T, 4>(lin.xyz * vector<T, 3>(a), a) * vector<T, 4>(src.w);
    if (T is half)
    {
        let alpha = orderedMultiply(a, src.w);
        c = vector<T, 4>(orderedMultiply(lin.x, alpha),
                         orderedMultiply(lin.y, alpha),
                         orderedMultiply(lin.z, alpha), alpha);
    }
    let limit = vcm.opts.x;
    if (limit > T(0.0))
    {
        if (vcm.opts.y > T(0.0))
        {
            var u = vector<T, 4>(c.xyz / vector<T, 3>(max(c.w, T.glassEps())), c.w);
            let peak = max(max(u.x, u.y), u.z);
            if (peak > limit)
                u = vector<T, 4>(vector<T, 3>(limit / peak) * u.xyz, u.w);
            c = vector<T, 4>(u.xyz * vector<T, 3>(c.w), u.w);
        }
        else
        {
            var unpremultiplied = c.xyz / vector<T, 3>(max(c.w, T.glassEps()));
            if (T is half)
            {
                // Native reciprocal returns float; each mixed product narrows
                // only after multiplication (opcodes1863 and1798).
                let reciprocal = 1.0f / max(c.w, T.glassEps()).toFloat();
                unpremultiplied = vector<T, 3>(
                    T(roundToHalf(c.x.toFloat() * reciprocal)),
                    T(roundToHalf(c.y.toFloat() * reciprocal)),
                    T(roundToHalf(c.z.toFloat() * reciprocal)));
            }
            let rgb = clamp(unpremultiplied, vector<T, 3>(T(-0.75)), vector<T, 3>(limit));
            c = vector<T, 4>(rgb * vector<T, 3>(c.w), c.w);
        }
    }
    var o = vector<T, 4>(T(1.0) - c.w) * dst + c;
    if (T is half)
        o = glassFma(vector<T, 4>(orderedSubtract(T(1.0), c.w)), dst, c);
    return vector<T, 4>(o.xyz, saturate(o.w));
}

/// Backdrop-aware variant (CAFilter vibrantColorMatrix with inputBackdropAware, blend "bcm"): the
/// matrix recolors `backdrop` (color attachment 1 in QuartzCore's pass) instead of the destination,
/// and the result is composited source-over onto `dst`. As for every in-shader blend, the blended
/// alpha is saturated (QuartzCore does this once after its blend-function switch).
public vector<T, 4> backdropAwareVibrantColorMatrixSourceOver<T : __BuiltinFloatingPointType>(vector<T, 4> src, vector<T, 4> backdrop, vector<T, 4> dst, VibrantColorMatrix<T> vcm) where T : IGlassReal
{
    let u = backdrop.xyz / vector<T, 3>(max(backdrop.w, T.glassEps()));
    var lin = vector<T, 4>(u.x) * vcm.m[0] + vector<T, 4>(u.y) * vcm.m[1] + vector<T, 4>(u.z) * vcm.m[2] + vcm.m[3] * vector<T, 4>(backdrop.w) + vcm.m[4];
    // Original half VCM: G+bias, R, B, A are four distinct FMAs.
    // Preserve the prior float specialization until its schedule is qualified.
    if (T is half)
        lin = glassFma(vcm.m[3], vector<T, 4>(backdrop.w),
                  glassFma(vcm.m[2], vector<T, 4>(u.z),
                      glassFma(vcm.m[0], vector<T, 4>(u.x),
                          glassFma(vcm.m[1], vector<T, 4>(u.y), vcm.m[4]))));
    let a = saturate(lin.w);
    var c = vector<T, 4>(lin.xyz * vector<T, 3>(a), a) * vector<T, 4>(src.w);
    if (T is half)
    {
        let alpha = orderedMultiply(a, src.w);
        c = vector<T, 4>(orderedMultiply(lin.x, alpha),
                         orderedMultiply(lin.y, alpha),
                         orderedMultiply(lin.z, alpha), alpha);
    }
    let limit = vcm.opts.x;
    if (limit > T(0.0))
    {
        if (vcm.opts.y > T(0.0))
        {
            var v = vector<T, 4>(c.xyz / vector<T, 3>(max(c.w, T.glassEps())), c.w);
            let peak = max(max(v.x, v.y), v.z);
            if (peak > limit)
                v = vector<T, 4>(vector<T, 3>(limit / peak) * v.xyz, v.w);
            c = vector<T, 4>(v.xyz * vector<T, 3>(c.w), v.w);
        }
        else
        {
            var unpremultiplied = c.xyz / vector<T, 3>(max(c.w, T.glassEps()));
            if (T is half)
            {
                // Native reciprocal returns float; each mixed product narrows
                // only after multiplication (opcodes1863 and1798).
                let reciprocal = 1.0f / max(c.w, T.glassEps()).toFloat();
                unpremultiplied = vector<T, 3>(
                    T(roundToHalf(c.x.toFloat() * reciprocal)),
                    T(roundToHalf(c.y.toFloat() * reciprocal)),
                    T(roundToHalf(c.z.toFloat() * reciprocal)));
            }
            let rgb = clamp(unpremultiplied, vector<T, 3>(T(-0.75)), vector<T, 3>(limit));
            c = vector<T, 4>(rgb * vector<T, 3>(c.w), c.w);
        }
    }
    var o = vector<T, 4>(T(1.0) - c.w) * dst + c;
    if (T is half)
        o = glassFma(vector<T, 4>(orderedSubtract(T(1.0), c.w)), dst, c);
    return vector<T, 4>(o.xyz, saturate(o.w));
}

/// Texture function "bvc" (CAFilter vibrantColorMatrix with inputBackdropAware on a layer drawn
/// with its own image, e.g. several vibrant sublayers in one group): the matrix recolors `backdrop`
/// (the in-place backdrop capture in color attachment 1) and the result is masked by the image
/// alpha `src.w`. No in-shader blend and no alpha saturate: the premultiplied result is composited
/// by fixed-function source-over (One, OneMinusSourceAlpha) onto attachment 0.
public vector<T, 4> backdropVibrantColorMatrix<T : __BuiltinFloatingPointType>(vector<T, 4> src, vector<T, 4> backdrop, VibrantColorMatrix<T> vcm) where T : IGlassReal
{
    let u = backdrop.xyz / vector<T, 3>(max(backdrop.w, T.glassEps()));
    let lin = vector<T, 4>(u.x) * vcm.m[0] + vector<T, 4>(u.y) * vcm.m[1] + vector<T, 4>(u.z) * vcm.m[2] + vcm.m[3] * vector<T, 4>(backdrop.w) + vcm.m[4];
    let a = saturate(lin.w);
    var c = vector<T, 4>(lin.xyz * vector<T, 3>(a), a) * vector<T, 4>(src.w);
    let limit = vcm.opts.x;
    if (limit > T(0.0))
    {
        if (vcm.opts.y > T(0.0))
        {
            var v = vector<T, 4>(c.xyz / vector<T, 3>(max(c.w, T.glassEps())), c.w);
            let peak = max(max(v.x, v.y), v.z);
            if (peak > limit)
                v = vector<T, 4>(vector<T, 3>(limit / peak) * v.xyz, v.w);
            c = vector<T, 4>(v.xyz * vector<T, 3>(c.w), v.w);
        }
        else
        {
            let rgb = clamp(c.xyz / vector<T, 3>(max(c.w, T.glassEps())), vector<T, 3>(T(-0.75)), vector<T, 3>(limit));
            c = vector<T, 4>(rgb * vector<T, 3>(c.w), c.w);
        }
    }
    return c;
}

// ---------------------------------------------------------------------------------------------
// QuartzCore's uber-shader uniform block
// ---------------------------------------------------------------------------------------------

/// `CA::OGL::Metal::Uniform<T>`, the uber shader's fragment buffer(1): 256 bytes for half pixels,
/// 368 for float. image_arg[0..2] hold the element SDF parameters (SdfFragmentUniforms). `B` types
/// the blend_arg block (six vector<T, 4>: VibrantColorMatrix<T> or SdfBlendParams<T>) and `P` the
/// member of the trailing union used by the effect (offset 208 / 272).
public struct UberUniforms<T : __BuiltinFloatingPointType, B, P>
{
    public float4 image_arg[6];
    public B blend_arg;
    public T edr_factors[2];
    public T hdr_scale[2];
    public uint8_t premultiply;
    public uint8_t linear_edr;
    public vector<T, 2> clamp_range;
    public float4 filter_arg[2];
    public float4 coord_arg;
    public P fx;
}

/// Uniform::blend_arg for the SDF union / subtraction blends: arg[0].x = CASDFLayer smoothness.
public struct SdfBlendParams<T : __BuiltinFloatingPointType>
{
    public vector<T, 4> arg[6];
}
