implementing liquidglass;

// Analytic signed distance fields for CASDFElementLayer shapes
// (QuartzCore `CA::OGL::Metal::ShaderUtils<T>::supercircle_sdf` and `compute_sdf_with_mode`).
// Distances are in points, negative inside. Gradients point away from the shape.

/// Distance along each edge covered by a continuous ("squircle") corner of radius 1.
public static const float kContinuousCornerExtent = 1.528665f;

/// Per-element SDF parameters (`SdfFragmentUniforms`, 48 bytes).
public struct SdfFragmentUniforms
{
    /// xy: half size of the element; z: mode (as float; negative = region outside the shape);
    /// w: gradient ovalization (0 = box normals, 1 = elliptical normals).
    public float4 arg;
    /// 2x2 transform applied to the gradient: grad' = (dot(g, tr.xy), dot(g, tr.zw)).
    public float4 tr;
    /// mode 4: xy = per-axis circular-corner selector (0 continuous, 1 circular), z = corner radius.
    /// mode > 4: per-corner radii.
    public float4 arg2;
}

/// `hi == 0 ? 0 : v` with `v` evaluated first (QuartzCore divides unconditionally, then selects).
float zeroIfZero(float hi, float v)
{
    return selectFast(hi == 0.0f, 0.0f, v);
}

// The original half-template Float32 mix uses two separately rounded
// FMA1402 operations. Public float and non-native compilation retain lerp.
float sdfMixF32<T : __BuiltinFloatingPointType>(float a,float b,float t)
{
#if LG_M1_SF_EMULATION
    if(T is half)return fmaSafeFused(t,b,fmaSafeFused(-t,a,a));
#endif
    return lerp(a,b,t);
}

/// SDF of the first-quadrant corner of a rounded box. `circular` blends each axis between a
/// continuous corner (0) and a circular arc (1). Returns (distance, gradient).
///
/// Shape of the compiled code: QuartzCore's specialized pipelines inline the corner into the
/// single-radius image function but keep it a separate function (called four times) in the
/// per-corner one, and the GPU compiler optimizes the two shapes differently. The port mirrors that:
/// supercircleSDF is the out-of-line function, supercircleSDFBody the force-inlined body.
public vector<T, 3> supercircleSDF<T : __BuiltinFloatingPointType>(float2 p, float2 halfSize, float radius, float2 circular)
{
    return supercircleSDFBody<T>(p, halfSize, radius, circular);
}

[ForceInline]
vector<T, 3> supercircleSDFBody<T : __BuiltinFloatingPointType>(float2 p, float2 halfSize, float radius, float2 circular)
{
    let r = abs(radius);
    let rk = r * kContinuousCornerExtent;
    let extent = sdfMixF32<T>(rk,r,max(circular.x,circular.y));
    let pb = p - halfSize;
    let q = float2(extent) + pb;

    // Position inside the corner cell, 0 at the corner-cell origin, 1 at the box edge.
    let c = abs(max(float2(0.0f), (pb + float2(rk)) / float2(rk)));
    bool circularOnly=false;
#if LG_M1_SF_EMULATION
    if(T is half){
        uint2 words=asuint(c);
        circularOnly=asuint(circular.x)==0x3f800000u && asuint(circular.y)==0x3f800000u
            && words.x<=0x5d800000u && words.y<=0x5d800000u
            && max(words.x,words.y)>=0x30800000u;
    }
#endif
    // This guard proves continuous finite, so both selector-1 axis mixes
    // reduce to circle. Preserve the later weight-dependent paired mix.
    float len=0.0f;
    [branch]if(!circularOnly)len=sdfSqrtF32<T>(sdfDotF32<T>(c));
    let hi = max(c.x, c.y);
    let lo = min(c.x, c.y);
    let t = zeroIfZero(hi, saturate(lo / hi));

    // Continuous-corner distance: radial length corrected by a quartic in the aspect ratio t.
    float continuous=0.0f;
    [branch]if(!circularOnly){
#if LG_M1_SF_EMULATION
    if(T is half){
        // Original0x6fc/70a/71a/726 evaluates the negative polynomial.
        float stage=fmaSafeFused(t,-0.926054f,3.15601f);
        stage=fmaSafeFused(stage,t,-3.64122f);
        stage=fmaSafeFused(stage,t,1.26803f);
        float negativePolynomial=fmaSafeFused(stage,-t,-0.268531f);
        float factor=orderedMultiply(orderedMultiply(t,t),saturate(len));
        float denominator=fmaSafeFused(negativePolynomial,factor,1.0f);
        // Preserve AIR sqrt(norm)+1. M1's fast-sqrt product contracts
        // at0x75a; that backend lowering has different special handling.
        float lengthPlusOne=fmaSafeAdd(len,1.0f);
        continuous=fmaSafeSubtract(lengthPlusOne,1.0f/denominator);
    }else
#endif
    {
        let poly=(((3.15601f-t*0.926054f)*t+-3.64122f)*t+1.26803f)*t+0.268531f;
        continuous=(len+1.0f)+-1.0f/(1.0f-t*t*saturate(len)*poly);
    }

    }

    // Circular-arc distance in the same normalized cell.
    float2 a;
#if LG_M1_SF_EMULATION
    if(T is half)a=max(float2(0.0f),float2(
        fmaSafeFused(c.x,kContinuousCornerExtent,-0.528665f),
        fmaSafeFused(c.y,kContinuousCornerExtent,-0.528665f)));
    else
#endif
        a=max(float2(0.0f),c*kContinuousCornerExtent+float2(-0.528665f));
    float circle;
#if LG_M1_SF_EMULATION
    if(T is half)circle=fmaSafeFused(sdfSqrtF32<T>(sdfDotF32<T>(a)),0.65416557f,0.34583443f);
    else
#endif
        circle=sdfSqrtF32<T>(sdfDotF32<T>(a))*0.65416557f+0.34583443f;

    let dx = circularOnly ? circle : sdfMixF32<T>(continuous,circle,circular.x);
    let dy = circularOnly ? circle : sdfMixF32<T>(continuous,circle,circular.y);
    let s = float(c.y > c.x ? 1 : -1);
    float w;
#if LG_M1_SF_EMULATION
    if(T is half)w=saturate(fmaSafeFused(t,s,fmaSafeSubtract(0.5f,s)));
    else
#endif
        w=saturate((0.5f-s)+s*t);
    half cornerDist;
    if (T is half)
        cornerDist = roundToHalf(sdfMixF32<T>(dx,dy,w) + -1.0f);
    else
        cornerDist = half(sdfMixF32<T>(dx,dy,w) + -1.0f);

    let m = max(float2(0.0f), q);
    let n = vector<T, 2>(m * sdfRsqrtF32<T>(sdfDotF32<T>(m)));
    let axis = selectFast(q.x > q.y, vector<T, 2>(T(1.0), T(0.0)), vector<T, 2>(T(0.0), T(1.0)));
    let d = min(max(T(q.x), T(q.y)), T(0.0)) + T(rk * float(cornerDist));
    let g = selectFast((n.x + n.y) > T(0.0), n, axis);
    return vector<T, 3>(d, g.x, g.y);
}

/// Applies gradient ovalization and the gradient transform shared by every SDF mode.
vector<T, 4> finishSDF<T : __BuiltinFloatingPointType>(float2 p, T dist, vector<T, 2> g0, SdfFragmentUniforms u)
{
    let pe = float2(p.x, u.arg.x * p.y / u.arg.y);
    let ne = vector<T, 2>(pe * sdfRsqrtF32<T>(sdfDotF32<T>(pe)));
    vector<T, 2> g;
    if (T is half)
    {
        // Original half SDF: two half mix FMAs, rounded x*x, y FMA,
        // half rsqrt, then two half products (clear GB0x07c4..0x0834;
        // specialized key/fill0x0236..0x0354). Keep the float path unchanged.
        let blend = vector<T, 2>(T(u.arg.w));
        let mixed = glassFma(blend, ne, glassFma(-blend, g0, g0));
        let norm2 = glassFma(mixed.y, mixed.y, orderedMultiply(mixed.x, mixed.x));
        let inverseLength = glassRsqrt(norm2);
        g = vector<T, 2>(orderedMultiply(mixed.x, inverseLength),
                          orderedMultiply(mixed.y, inverseLength));
    }
    else
        g = normalize(lerp(g0, ne, vector<T, 2>(T(u.arg.w))));
#if LG_M1_SF_EMULATION
    if(T is half){
        // Native half products feed y-product then x-FMA transforms;
        // their results narrow at0x1056/105c before GB consumes them.
        float gx=nativeHalfToFloat(half(g.x.toFloat()));
        float gy=nativeHalfToFloat(half(g.y.toFloat()));
        float x=fmaSafeFused(u.tr.x,gx,orderedMultiply(u.tr.y,gy));
        float y=fmaSafeFused(u.tr.z,gx,orderedMultiply(u.tr.w,gy));
        return vector<T,4>(dist,T(roundToHalf(x)),T(roundToHalf(y)),T(1.0));
    }
#endif
    let gx = g.x.toFloat();
    let gy = g.y.toFloat();
    return vector<T, 4>(dist, T(u.tr.x * gx + u.tr.y * gy), T(u.tr.z * gx + u.tr.w * gy), T(1.0));
}

/// Evaluates an element SDF. Returns (distance, gradient.xy, alpha).
/// mode 0-3: box (Chebyshev distance); mode 4: rounded box with one radius (arg2.z);
/// mode > 4: rounded box with per-corner radii in arg2 (negative radius = circular corner).
public vector<T, 4> computeSDFWithMode<T : __BuiltinFloatingPointType>(float2 p, int mode, SdfFragmentUniforms u)
{
    if (mode < 4)
        return rectSDFImage<T>(p, u);
    if (mode == 4)
        return supercircleSDFImage<T>(p, u);
    return supercircleSDFImagePerCorner<T>(p, u);
}

/// rect_sdf_image (uber image function 20).
[ForceInline]
public vector<T, 4> rectSDFImage<T : __BuiltinFloatingPointType>(float2 p, SdfFragmentUniforms u)
{
    let q = abs(p) - u.arg.xy;
    let qx = T(q.x);
    let qy = T(q.y);
    let s = vector<T, 2>(T(selectFast(p.x >= 0.0f, 1.0f, -1.0f)), T(selectFast(p.y >= 0.0f, 1.0f, -1.0f)));
    let axis = selectFast(qx > qy, vector<T, 2>(T(1.0), T(0.0)), vector<T, 2>(T(0.0), T(1.0)));
    return finishSDF<T>(p, max(qx, qy), axis * s, u);
}

/// supercircle_sdf_image<false> (21): one radius, one corner evaluation (QuartzCore's specialized
/// pipeline has the corner function inlined here).
[ForceInline]
public vector<T, 4> supercircleSDFImage<T : __BuiltinFloatingPointType>(float2 p, SdfFragmentUniforms u)
{
    let r = supercircleSDFBody<T>(abs(p), u.arg.xy, u.arg2.z, u.arg2.xy);
    let s = vector<T, 2>(T(selectFast(p.x >= 0.0f, 1.0f, -1.0f)), T(selectFast(p.y >= 0.0f, 1.0f, -1.0f)));
    return finishSDF<T>(p, r.x, r.yz * s, u);
}

/// supercircle_sdf_image<true> (22): per-corner radii, four corner evaluations (the corner function
/// stays a separate function in QuartzCore's specialized pipeline).
[ForceInline]
public vector<T, 4> supercircleSDFImagePerCorner<T : __BuiltinFloatingPointType>(float2 p, SdfFragmentUniforms u)
{
    // Per-corner radii. The circular selector of each axis comes from the average radius of the two
    // corners sharing that edge: negative averages (circular corners) ramp in with how much of the edge
    // the corners consume.
    let radii = u.arg2;
    let edgeRadius = (radii.xxwy + radii.ywzz) * 0.5f;
    let fill = u.arg.xyxy / edgeRadius;
    let ramp = saturate((float4(-kContinuousCornerExtent) - fill) * -1.8915572f);
    let sel = select(edgeRadius < float4(0.0f), ramp, float4(1.0f));

    vector<T, 3> best = supercircleSDF<T>(p, u.arg.xy, radii.y, sel.xw);
    let r1 = supercircleSDF<T>(float2(-p.x, p.y), u.arg.xy, radii.x, sel.xy);
    if (r1.x > best.x)
        best = vector<T, 3>(r1.x, -r1.y, r1.z);
    let r2 = supercircleSDF<T>(float2(p.x, -p.y), u.arg.xy, radii.z, sel.zw);
    if (r2.x > best.x)
        best = vector<T, 3>(r2.x, r2.y, -r2.z);
    let r3 = supercircleSDF<T>(float2(-p.x, -p.y), u.arg.xy, radii.w, sel.zy);
    if (r3.x > best.x)
        best = vector<T, 3>(r3.x, -r3.y, -r3.z);
    return finishSDF<T>(p, best.x, best.yz, u);
}

/// Coverage of the shape from its distance: a one-pixel antialiased edge.
public T sdfCoverage<T : __BuiltinFloatingPointType>(T dist, T alpha)
{
    // SPIR-V derivatives require float32. Native half fwidth lowers to
    // half-result derivatives followed by a half abs/add (retained AGX
    // G.code.pretty @0xf200..0xf208). Keep both narrowing points.
    let dx = T(ddx_fine(dist.toFloat()));
    let dy = T(ddy_fine(dist.toFloat()));
    let fw = max(abs(dx) + abs(dy), T(0.0001f));
    return T(saturate(glassDivide(-dist,fw).toFloat()+0.5f))*alpha;
}

/// Image function 46 `dfr` (CA::OGL::Metal::ShaderUtils<T>::sdf_rgba_image, fixed_frag_lph_cph.ll line 8853): a
/// contents-mode CASDFElementLayer's texel t decoded with image_arg[0] = (one - zero, zero, k, b)
/// (LayerNode::apply 0x18a68c4a8..c568): d = T(x * float(t.r) + y), (nx, ny) = T(z) * t.gb + T(w), alpha 1.
/// (k, b) = (1, 0) for float formats, else ((2^n - 1) / (2^(n-1) - 1), -1) for n-bit formats.
public vector<T, 4> sdfRgbaImage<T : __BuiltinFloatingPointType>(vector<T, 4> t, float4 arg) where T : IGlassReal
{
    let d = T(arg.x * t.x.toFloat() + arg.y);
    let n = vector<T, 2>(T(arg.z)) * t.yz + vector<T, 2>(T(arg.w));
    return vector<T, 4>(d, n.x, n.y, T(1.0));
}

/// Image function 47 `dfa` (alpha formats A8 / Ah / Af): the distance from the alpha channel,
/// d = T(x * float(t.a) + y); t.g and t.b pass through unchanged; alpha 1 (fixed_frag_lph_cph.ll line 1812).
public vector<T, 4> sdfAlphaImage<T : __BuiltinFloatingPointType>(vector<T, 4> t, float4 arg) where T : IGlassReal
{
    let d = T(arg.x * t.w.toFloat() + arg.y);
    return vector<T, 4>(d, t.y, t.z, T(1.0));
}

float sdfDotF32<T : __BuiltinFloatingPointType>(float2 value)
{
#if LG_M1_SF_EMULATION
    if (T is half) return fmaSafeFused(value.y,value.y,orderedMultiply(value.x,value.x));
#endif
    return dot(value,value);
}
