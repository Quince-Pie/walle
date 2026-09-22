implementing liquidglass;

// Precision policy shared by every Liquid Glass shader.
//
// QuartzCore compiles each glass shader twice from one template: `_lph` with a `half` pixel type
// (used for 8-bit render targets) and `_lpf` with a `float` pixel type (extended-range targets).
// `T` below is that pixel type. Values the template spells as explicit `half` stay `half` in both
// instantiations; everything typed `T` changes with the variant. Uniform fields stored as `float`
// are converted with `T(...)` exactly where the compiled AIR converts them.

/// Pixel precision for a Liquid Glass shader instantiation (`half` or `float`).
public interface IGlassReal
{
    /// Threshold QuartzCore uses for divide-by-alpha guards, "negligible" colour flushes and
    /// "effectively zero" tests: 1e-4 in half (0x068E), 1e-6 in float (0x358637bd).
    static This glassEps();
    /// True when the exponent bits are all ones (infinity or NaN). An integer test on the bit pattern, as
    /// QuartzCore compiles it (`and i16 %x, 0x7C00` / `and i32 %x, 0x7F800000`), so fast math cannot remove it.
    static bool exponentAllOnes(This x);
}

public extension half : IGlassReal
{
    public static half glassEps() { return 0.0001h; }
    public static bool exponentAllOnes(half x) { return (asuint16(x) & uint16_t(0x7C00)) == uint16_t(0x7C00); }
}

public extension float : IGlassReal
{
    public static float glassEps() { return 0.000001f; }
    public static bool exponentAllOnes(float x) { return (asuint(x) & 0x7F800000u) == 0x7F800000u; }
}

/// fc_unbounded_range (uber pipelines whose label ends in `u`, drawn into float targets): after each image input's
/// colour-management stage QuartzCore replaces the image value by (0, 0, 0, a) when the sum ((r + g) + a) + b is
/// infinite or NaN, with an infinite or NaN alpha replaced by 1 (fixed_frag_lph_cpf.ll blocks %942 / %1462,
/// fixed_frag_lpf_cpf.ll block %960).
public vector<T, 4> unboundedRangeImage<T : __BuiltinFloatingPointType>(vector<T, 4> v) where T : IGlassReal
{
    let sum = ((v.x + v.y) + v.w) + v.z;
    let a = T.exponentAllOnes(v.w) ? T(1.0) : v.w;
    return T.exponentAllOnes(sum) ? vector<T, 4>(T(0.0), T(0.0), T(0.0), a) : v;
}

/// `mix` as QuartzCore's templates emit it. When the uniform `x86_workaround` flag is set the
/// interpolation runs in float and is rounded back to `T`; otherwise it runs in `T`.
public vector<T, N> glassMix<T : __BuiltinFloatingPointType, let N : int>(vector<T, N> a, vector<T, N> b, T t, bool x86)
    where T : IGlassReal
{
    if (x86)
        return vector<T, N>(lerp(vector<float, N>(a), vector<float, N>(b), vector<float, N>(t.toFloat())));
    return lerp(a, b, vector<T, N>(t));
}

/// Scalar `mix` whose operands come straight from `float` uniform fields: the x86 path mixes the
/// raw floats, the default path converts each operand to `T` first.
public T glassMixF<T : __BuiltinFloatingPointType>(float a, float b, float t, bool x86)
    where T : IGlassReal
{
    if (x86)
        return T(lerp(a, b, t));
    return lerp(T(a), T(b), T(t));
}

/// Scalar `mix` of `T` operands.
public T glassMixS<T : __BuiltinFloatingPointType>(T a, T b, T t, bool x86)
    where T : IGlassReal
{
    if (x86)
        return T(lerp(a.toFloat(), b.toFloat(), t.toFloat()));
    return lerp(a, b, t);
}

/// Zeroes colour channels whose magnitude is below `glassEps` (applied to every backdrop tap).
public vector<T, 3> flushNegligible<T : __BuiltinFloatingPointType>(vector<T, 3> c)
    where T : IGlassReal
{
    return select(abs(c) < vector<T, 3>(T.glassEps()), vector<T, 3>(T(0.0)), c);
}

/// Un-premultiplies with the alpha guard QuartzCore uses.
public vector<T, 3> unpremultiply<T : __BuiltinFloatingPointType>(vector<T, 4> c)
    where T : IGlassReal
{
    return c.rgb / vector<T, 3>(max(c.a, T.glassEps()));
}

/// Mip level for a blur radius measured in backdrop texels. Radii below 2 are remapped to
/// r/2 + 1 so the level ramps continuously to 0 at r = 0.
public T blurLevel<T : __BuiltinFloatingPointType>(T radius)
{
    let r = radius < T(2.0) ? T(radius.toFloat() * 0.5f + 1.0f) : radius;
    return max(T(0.0), log2(r));
}

/// The glass "bevel" height profile: `amount * (1 - sqrt(x * (2 - x)))` with
/// `x = saturate(scaledDepth)`, i.e. a quarter-circle falloff from `amount` at the rim to 0 at
/// depth `1 / invHeight` inside the shape.
public T bevelProfile<T : __BuiltinFloatingPointType>(T scaledDepth, T amount)
{
    let x = saturate(scaledDepth);
    let s = saturate(sqrt((T(2.0) - x) * x));
    return amount - s * amount;
}

/// Linear ramp `saturate((x - x0) / (x1 - x0))` evaluated the way the AIR does it:
/// `fma(x, 1/(x1-x0), -x0/(x1-x0))` in float.
public vector<float, N> glassRamp<let N : int>(vector<float, N> x, vector<float, N> x0, vector<float, N> x1)
{
    let span = x1 - x0;
    return saturate(fma(x, vector<float, N>(1.0f) / span, -x0 / span));
}

/// `x < edge ? 1 : 0` as a single expression. QuartzCore's C++ ternary compiles to an LLVM `select`
/// carrying the fast-math flags; Slang lowers `?:` to control flow, whose merged select has none,
/// and the GPU backend then folds the surrounding arithmetic differently.
public float stepBelow(float x, float edge)
{
    __target_switch
    {
    case metal:
        __intrinsic_asm "(($0) < ($1) ? 1.0f : 0.0f)";
    default:
        return x < edge ? 1.0f : 0.0f;
    }
}

/// `c ? a : b` as a single C++ conditional expression on Metal, which compiles to an LLVM `select`
/// carrying the fast-math flags (QuartzCore's form). Both operands are evaluated beforehand by the caller.
public T selectFast<T>(bool c, T a, T b)
{
    __target_switch
    {
    case metal:
        __intrinsic_asm "(($0) ? ($1) : ($2))";
    default:
        return c ? a : b;
    }
}

/// Condition of QuartzCore's colour-management stage after an image is sampled,
/// `any(fc_has_color_trc) || any(fc_has_color_cube)`. Liquid Glass pipelines have neither, so it is
/// false, but QuartzCore's specialized pipelines still contain the branch and the phi after it
/// (`air.any(zeroinitializer)` is not folded before the GPU compiler runs), and that boundary decides
/// how the GPU compiler reassociates the effect code that follows. It is emitted verbatim so the port's
/// pipelines have the same shape.
public bool colorConversionEnabled()
{
    __target_switch
    {
    case metal:
        __intrinsic_asm "(any(bool4(false)) || any(bool2(false)))";
    default:
        return false;
    }
}

/// What QuartzCore's colour-management stage reduces to when no transfer curve or cube is present: unpremultiply
/// in float (divisor floor 0.0005), back to the pixel type, premultiply again, alpha unchanged. (For an element SDF
/// image, whose alpha is the constant 1, the specialized pipeline folds this to rgb / max(1, 0.0005) with alpha 1.)
public vector<T, 4> unpremultiplyForConversion<T : __BuiltinFloatingPointType>(vector<T, 4> c)
{
    let rgb = float3(c.x.toFloat(), c.y.toFloat(), c.z.toFloat()) / max(c.w.toFloat(), 0.0005f);
    return vector<T, 4>(vector<T, 3>(rgb) * vector<T, 3>(c.w), c.w);
}

/// ((a * b) * c) * d evaluated in exactly this order. Under fast math the GPU compiler re-sorts
/// multiply chains by its own operand ranking. Apple's decoded G13S-B0 instructions establish the
/// caller-specific order (half glass-highlight differs from key/fill and float glass-highlight).
/// Safe math preserves each extracted rounding tree; see work/finish/provenance/ARITHMETIC.md.
public float orderedProduct(float a, float b, float c, float d)
{
    __target_switch
    {
    case metal:
        __requirePrelude(R"(
static inline float lg_ordered_product(float a, float b, float c, float d)
{
#pragma METAL fp math_mode(safe)
    return ((a * b) * c) * d;
}
)");
        __intrinsic_asm "lg_ordered_product($0, $1, $2, $3)";
    default:
        return ((a * b) * c) * d;
    }
}

