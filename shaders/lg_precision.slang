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
/// The compiled operation is fma(t, b, fma(-t, a, a)), with both T-rounding
/// boundaries retained in the default path (original clear GB 0x1bae..0x1bc0).
public vector<T, N> glassMix<T : __BuiltinFloatingPointType, let N : int>(vector<T, N> a, vector<T, N> b, T t, bool x86)
    where T : IGlassReal
{
    if (T is float)
        return lerp(a, b, vector<T, N>(t));
    if (x86)
        return vector<T, N>(glassFma(vector<float, N>(t.toFloat()), vector<float, N>(b),
            glassFma(-vector<float, N>(t.toFloat()), vector<float, N>(a), vector<float, N>(a))));
    return glassFma(vector<T, N>(t), b, glassFma(-vector<T, N>(t), a, a));
}

/// Scalar `mix` whose operands come straight from `float` uniform fields: the x86 path mixes the
/// raw floats, the default path converts each operand to `T` first.
public T glassMixF<T : __BuiltinFloatingPointType>(float a, float b, float t, bool x86)
    where T : IGlassReal
{
    if (T is float)
        return lerp(T(a), T(b), T(t));
    if (x86)
        return T(glassFma(t, b, glassFma(-t, a, a)));
    return glassFma(T(t), T(b), glassFma(-T(t), T(a), T(a)));
}

/// Scalar `mix` of `T` operands.
public T glassMixS<T : __BuiltinFloatingPointType>(T a, T b, T t, bool x86)
    where T : IGlassReal
{
    if (T is float)
        return lerp(a, b, t);
    if (x86)
        return T(glassFma(t.toFloat(), b.toFloat(), glassFma(-t.toFloat(), a.toFloat(), a.toFloat())));
    return glassFma(t, b, glassFma(-t, a, a));
}

/// Zeroes colour channels whose magnitude is below `glassEps` (applied to every backdrop tap).
public vector<T, 3> flushNegligible<T : __BuiltinFloatingPointType>(vector<T, 3> c)
    where T : IGlassReal
{
    // A generic vector comparison/select can resolve to a scalar condition in
    // Slang. Explicit components preserve the original AIR's three comparisons.
    return vector<T, 3>(abs(c.x) < T.glassEps() ? T(0.0) : c.x,
                        abs(c.y) < T.glassEps() ? T(0.0) : c.y,
                        abs(c.z) < T.glassEps() ? T(0.0) : c.z);
}

/// Un-premultiplies with the alpha guard QuartzCore uses.
public vector<T, 3> unpremultiply<T : __BuiltinFloatingPointType>(vector<T, 4> c)
    where T : IGlassReal
{
    return glassDivide(c.rgb,vector<T,3>(max(c.a,T.glassEps())));
}

/// Original UNORM8 attachment epilogue only: clamp the half shader source,
/// decode the stored destination bytes to RNE-half, then evaluate the half
/// alpha complement and four fused RNE-half sums. This is not an RGBA16F blend.
/// Uint32-pair arithmetic retains those boundaries on implementations whose
/// default half conversions or arithmetic use different rounding. No Int16,
/// Int64 or additional optional floating-point mode is required.
/// Native GB RGB clamp/multiply is opcode1015 at0x21b0/0x21bc/0x21c2;
/// complement928 and FMA967 follow. Independent full-half RGB/alpha controls
/// and byte-source/mask controls are exact on all three qualified devices.
public half4 sourceOverHalf(half4 source, half4 destination)
{
    source=saturate(source);
    uint4 sourceBits=uint4(packHalf2x16(float2(source.x.toFloat(),0)),
        packHalf2x16(float2(source.y.toFloat(),0)),
        packHalf2x16(float2(source.z.toFloat(),0)),
        packHalf2x16(float2(source.w.toFloat(),0)))&0xffffu;
    uint4 bytes=uint4(float4(destination)*255.0f+0.5f);
    uint4 destinationBits=uint4(unormByteHalfBits(bytes.x),unormByteHalfBits(bytes.y),
        unormByteHalfBits(bytes.z),unormByteHalfBits(bytes.w));
#if LG_M1_SF_EMULATION
    // Reuse the qualified fused-half implementation only for proven positive
    // unit-range words. The original clamp/byte conversion and fallback stay.
    if(all(sourceBits<=uint4(0x3c00u)) && all(destinationBits<=uint4(0x3c00u))){
        half4 s=half4(nativeHalfFromWord(sourceBits.x),nativeHalfFromWord(sourceBits.y),
                     nativeHalfFromWord(sourceBits.z),nativeHalfFromWord(sourceBits.w));
        half4 d=half4(nativeHalfFromWord(destinationBits.x),nativeHalfFromWord(destinationBits.y),
                     nativeHalfFromWord(destinationBits.z),nativeHalfFromWord(destinationBits.w));
        half remaining=glassFma(-s.w,1.0h,1.0h);
        return glassFma(d,half4(remaining),s);
    }
#endif
    uint remainingBits=positiveQ24HalfBits(0x01000000u-positiveHalfQ24(sourceBits.w));
    uint4 result=uint4(positiveHalfFmaBits(destinationBits.x,remainingBits,sourceBits.x),
        positiveHalfFmaBits(destinationBits.y,remainingBits,sourceBits.y),
        positiveHalfFmaBits(destinationBits.z,remainingBits,sourceBits.z),
        positiveHalfFmaBits(destinationBits.w,remainingBits,sourceBits.w));
    return half4(half(unpackHalf2x16ToFloat(result.x).x),half(unpackHalf2x16ToFloat(result.y).x),
        half(unpackHalf2x16ToFloat(result.z).x),half(unpackHalf2x16ToFloat(result.w).x));
}

/// Mip level for a blur radius measured in backdrop texels. Radii below 2 are remapped to
/// r/2 + 1 so the level ramps continuously to 0 at r = 0.
public T blurLevel<T : __BuiltinFloatingPointType>(T radius)
{
    if(T is half){
        // Native radius/remap/log operands are half (1446 then1622).
        // Preserve both half boundaries when the backend widens log2.
        half r=roundToHalf(radius.toFloat());
        if(r<2.0h)r=roundToHalf(fmaSafeFused(r.toFloat(),0.5f,1.0f));
        return T(max(0.0h,log2(r)));
    }
    let r = radius < T(2.0) ? T(radius.toFloat() * 0.5f + 1.0f) : radius;
    return max(T(0.0), log2(r));
}

/// The glass "bevel" height profile: `amount * (1 - sqrt(x * (2 - x)))` with
/// `x = saturate(scaledDepth)`, i.e. a quarter-circle falloff from `amount` at the rim to 0 at
/// depth `1 / invHeight` inside the shape.
public T bevelProfile<T : __BuiltinFloatingPointType>(T scaledDepth, T amount)
{
    let x = saturate(scaledDepth);
    let s = saturate(glassSqrt((T(2.0) - x) * x));
    // Original half GB shadow/inner/outer/bleed epilogues use FMA967.
    if (T is half) return glassFma(-amount,s,amount);
    return amount - s * amount;
}

/// Linear ramp `saturate((x - x0) / (x1 - x0))` evaluated the way the AIR does it:
/// `fma(x, 1/(x1-x0), -x0/(x1-x0))` in float.
public vector<float, N> glassRamp<let N : int>(vector<float, N> x, vector<float, N> x0, vector<float, N> x1)
{
    let span = x1 - x0;
    return saturate(glassFma(x, vector<float, N>(1.0f) / span, -x0 / span));
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

// Preserve the first multiply and final bias-add of the original color-matrix
// schedule. SPIR-V NoContraction prevents the driver from moving the bias into
// the first FMA. Explicit fma calls between these boundaries remain fused.
public T orderedMultiply<T : __BuiltinFloatingPointType>(T a, T b)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
            %value:$$T = OpFMul $a $b;
            OpDecorate %value NoContraction;
            result:$$T = OpCopyObject %value;
        };
    default:
        return a * b;
    }
}
public vector<T, N> orderedAdd<T : __BuiltinFloatingPointType, let N : int>(
    vector<T, N> a, vector<T, N> b)
{
    // This boundary is established for the half material path. Keep the
    // previously qualified float expression unchanged.
    if (T is float)
        return a + b;
    __target_switch
    {
    case spirv:
        return spirv_asm {
            %value:$$vector<T,N> = OpFAdd $a $b;
            OpDecorate %value NoContraction;
            result:$$vector<T,N> = OpCopyObject %value;
        };
    default:
        return a + b;
    }
}

// The attachment blend's half alpha complement is a separate native rounding.
public T orderedSubtract<T : __BuiltinFloatingPointType>(T a, T b)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
            %value:$$T = OpFSub $a $b;
            OpDecorate %value NoContraction;
            result:$$T = OpCopyObject %value;
        };
    default:
        return a - b;
    }
}

/// Preserve the original corner-distance narrowing before its float-radius
/// multiply (M1 clear GB opcode1104 at0x1200, feeding opcode1433 at0x120e).
/// FloatControls2 is required by Vulkan1.4. Only this conversion disables fast
/// transformations; the rest of the shader retains its selected math modes.
/// Unlike OpQuantizeToF16, this keeps half subnormal values.
public half roundToHalf(float value)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
            OpCapability FloatControls2;
            OpExtension "SPV_KHR_float_controls2";
            %rounded:$$half = OpFConvert $value;
            OpDecorate %rounded FPFastMathMode 0; // None.
            result:$$half = OpCopyObject %rounded;
        };
    default:
        return half(value);
    }
}


/// The original native half-LOD sampler uses six fractional mip-weight bits.
/// Exhaustive finite-half input control: lod_calibration/exhaustive. Preserve
/// the half input before converting the mip interpolation weight. The float
/// source variant retains its previously qualified path.
public float nativeSamplerLevel<T : __BuiltinFloatingPointType>(T level)
{
    if (T is half)
        return floor(roundToHalf(level.toFloat()).toFloat() * 64.0f) * 0.015625f;
    return level.toFloat();
}


// Native normalized, clamp-to-edge UNORM8 filtering. The texture unit uses
// 8 fractional coordinate bits, 6 fractional LOD bits, 16-bit interpolation
// coefficients and a result with 4 fractional byte bits. Direct original
// sample/store controls cover the complete one-dimensional byte domain and
// all spatial/LOD phases over independently selected two-dimensional cells.
// This opt-in path is only for the product's RGBA8 backdrop pyramid; the
// generic half/float texture path retains ordinary resource sampling.
uint4 nativeBackdropBytes<T : __BuiltinFloatingPointType>(
    Texture2D<vector<T,4>> source, int2 pixel, uint level, uint2 size)
    where vector<T,4> : ITexelElement
{
    pixel=clamp(pixel,int2(0),int2(size)-1);
    // A half texel retains every UNORM8 code: error is below1/16 byte with
    // RNE and below1/8 byte with RTZ. Rounding after multiplication by255
    // therefore recovers the original byte exactly in either case.
    return uint4(float4(source.Load(int3(pixel,int(level))))*255.0f+0.5f);
}

uint4 nativeBackdropMip<T : __BuiltinFloatingPointType>(
    Texture2D<vector<T,4>> source,float2 uv,uint level,uint mipWeight)
    where vector<T,4> : ITexelElement
{
    uint width,height,levels;
    source.GetDimensions(level,width,height,levels);
    float2 scaled=float2(orderedMultiply(uv.x,float(width)),
                         orderedMultiply(uv.y,float(height)));
    float2 position=float2(orderedSubtract(scaled.x,0.5f),
                           orderedSubtract(scaled.y,0.5f));
    position=clamp(position,float2(0),float2(width-1,height-1));
    int2 base=int2(floor(position));
    uint2 phase=uint2(floor((position-float2(base))*256.0f+0.5f));
    // The upper coefficients round upward at ties. Lower coefficients are
    // column complements, retaining each exact mip-weight column total.
    uint left=(256-phase.x)*mipWeight*4;
    uint right=phase.x*mipWeight*4;
    uint a=((256-phase.x)*(256-phase.y)*mipWeight+32)>>6;
    uint b=(phase.x*(256-phase.y)*mipWeight+32)>>6;
    uint c=left-a;
    uint d=right-b;
    uint2 size=uint2(width,height);
    return nativeBackdropBytes(source,base,level,size)*a
        +nativeBackdropBytes(source,base+int2(1,0),level,size)*b
        +nativeBackdropBytes(source,base+int2(0,1),level,size)*c
        +nativeBackdropBytes(source,base+int2(1,1),level,size)*d;
}

// Native M1 sign-magnitude clamp, qualified across all nonfinite coordinates.
float nativeClampSamplerCoordinate(float value)
{
    uint bits=asuint(value);
    if ((bits & 0x80000000u)!=0) return 0.0f;
    return bits>=0x3f800000u ? 1.0f : value;
}

public vector<T,4> sampleGlassBackdrop<T : __BuiltinFloatingPointType,let nativeUnorm8 : bool>(
    Texture2D<vector<T,4>> source,SamplerState sampler,float2 uv,T level)
    where vector<T,4> : ITexelElement
{
    if (!nativeUnorm8 || T is float)
        return source.SampleLevel(sampler,uv,nativeSamplerLevel(level));
    // AIR passes the rounded half LOD directly. The six-bit phase belongs
    // to M1's texture unit, not the source algorithm.
    float lod=roundToHalf(level.toFloat()).toFloat();
    uint lodWord=asuint(lod),lodMagnitude=lodWord&0x7fffffffu;
    if(lodMagnitude>0x7f800000u)lod=0.0f;
    else if(lodMagnitude==0x7f800000u){
        if((lodWord&0x80000000u)!=0u)lod=0.0f;
        else{uint width,height,levels;source.GetDimensions(0,width,height,levels);lod=float(levels-1u);}
    }
    float2 position=float2(nativeClampSamplerCoordinate(uv.x),nativeClampSamplerCoordinate(uv.y));
    let value=source.SampleLevel(sampler,position,lod);
    return vector<T,4>(T(roundToHalf(value.x.toFloat())),T(roundToHalf(value.y.toFloat())),
        T(roundToHalf(value.z.toFloat())),T(roundToHalf(value.w.toFloat())));
}


#ifndef LG_M1_SF_EMULATION
#define LG_M1_SF_EMULATION 0
#endif
#if LG_M1_SF_EMULATION
// Preserve every binary16 word without requiring shaderInt16.
public uint nativeHalfWord(half value)
{
    half2 pair=half2(value,value);
    return spirv_asm { result:$$uint = OpBitcast $pair; } & 0xffffu;
}
public half nativeHalfFromWord(uint word)
{
    uint pair=(word&0xffffu)*0x00010001u;
    half2 result=spirv_asm { result:$$half2 = OpBitcast $pair; };
    return result.x;
}
// Original opcode1863 followed by opcode1798 destination modifier0x1000.
// The complete2^32 raw-binary16 operand domain matches exact rational RNE
// division. Integer significands make this independent of backend FDiv
// accuracy and require no Int16, Int64 or Float64 capability.
uint nativeHalfDivideBits(uint a,uint b)
{
    uint am=a&0x7fffu,bm=b&0x7fffu,sign=(a^b)&0x8000u;
    if(am>0x7c00u||bm>0x7c00u||(am==0u&&bm==0u)||(am==0x7c00u&&bm==0x7c00u))return 0x7e00u;
    if(am==0x7c00u||bm==0u)return sign|0x7c00u;
    if(am==0u||bm==0x7c00u)return sign;
    if(bm==0x3c00u)return sign|am;
    uint ae=am>>10,be=bm>>10,ma=(am&1023u)|(ae!=0?1024u:0u),mb=(bm&1023u)|(be!=0?1024u:0u);
    int e=(ae!=0?int(ae)-25:-24)-(be!=0?int(be)-25:-24);
    int p=firstbithigh(ma)-firstbithigh(mb);
    if(p>=0){if(ma<(mb<<uint(p)))p--;}
    else if((ma<<uint(-p))<mb)p--;
    int top=e+p;
    if(top>15)return sign|0x7c00u;
    if(top<-25)return sign;
    int shift=top>=-14?10-p:e+24;
    uint numerator=ma,denominator=mb;
    if(shift>=0)numerator<<=uint(shift);
    else denominator<<=uint(-shift);
    uint quotient=numerator/denominator;
    uint remainder=numerator-quotient*denominator;
    quotient+=uint(remainder>denominator-remainder ||
        (remainder==denominator-remainder&&(quotient&1u)!=0));
    return sign|(top>=-14 ? (uint(top+15)<<10)+(quotient-1024u) : quotient);
}
public half nativeHalfDivide(half numerator,half denominator)
{
    uint a=nativeHalfWord(roundToHalf(numerator.toFloat()));
    uint b=nativeHalfWord(roundToHalf(denominator.toFloat()));
    uint am=a&0x7fffu,bm=b&0x7fffu,sign=(a^b)&0x8000u;
    if(am>0x7c00u||bm>0x7c00u||(am==0u&&bm==0u)||(am==0x7c00u&&bm==0x7c00u))return nativeHalfFromWord(0x7e00u);
    if(am==0x7c00u||bm==0u)return nativeHalfFromWord(sign|0x7c00u);
    if(am==0u||bm==0x7c00u)return nativeHalfFromWord(sign);
    float x=nativeHalfFromWord(a).toFloat(),y=nativeHalfFromWord(b).toFloat();
    float quotient=spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$float = OpFDiv $x $y;
        OpDecorate %v FPFastMathMode 0;
        result:$$float = OpCopyObject %v;
    };
    return roundToHalf(quotient);
}


// Source AIR operations mapped to the platform's typed SFU operations.
// Special cases remain the retained native policy; finite normal approximation
// is a permitted hardware difference under the user's revised contract.
float platformSqrtF32(float value) {
    return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$float = OpExtInst glsl450 Sqrt $value;
        OpDecorate %v FPFastMathMode 0;
        result:$$float = OpCopyObject %v;
    };
}
float platformRsqrtF32(float value) {
    return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$float = OpExtInst glsl450 InverseSqrt $value;
        OpDecorate %v FPFastMathMode 0;
        result:$$float = OpCopyObject %v;
    };
}
half platformSqrtHalf(half value) {
    uint word=nativeHalfWord(value),magnitude=word&0x7fffu;
    if(magnitude==0u)return nativeHalfFromWord(word);
    if((word&0x8000u)!=0u || magnitude>0x7c00u)return nativeHalfFromWord(0x7e00u);
    if(magnitude==0x7c00u)return nativeHalfFromWord(0x7c00u);
    half operand=nativeHalfFromWord(word);
    return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$half = OpExtInst glsl450 Sqrt $operand;
        OpDecorate %v FPFastMathMode 0;
        result:$$half = OpCopyObject %v;
    };
}
half platformRsqrtHalf(half value) {
    uint word=nativeHalfWord(value),magnitude=word&0x7fffu;
    if(magnitude==0u)return nativeHalfFromWord((word&0x8000u)|0x7c00u);
    if((word&0x8000u)!=0u || magnitude>0x7c00u)return nativeHalfFromWord(0x7e00u);
    if(magnitude==0x7c00u)return nativeHalfFromWord(0u);
    half operand=nativeHalfFromWord(word);
    return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$half = OpExtInst glsl450 InverseSqrt $operand;
        OpDecorate %v FPFastMathMode 0;
        result:$$half = OpCopyObject %v;
    };
}

public float m1RsqrtF32(float value)
{
    uint bits=asuint(value),exponent=(bits>>23)&255u,sign=bits&0x80000000u;
    if(exponent==0u)return asfloat(sign|0x7f800000u);
    if(sign!=0u)return asfloat(0x7fc00000u);
    if(exponent==255u)return (bits&0x7fffffu)==0u ? 0.0f : asfloat(0x7fc00000u);
    return platformRsqrtF32(value);
}
public float m1SqrtF32(float value)
{
    uint bits=asuint(value),exponent=(bits>>23)&255u;
    if(exponent==0u)return asfloat(bits&0x80000000u);
    if((bits&0x80000000u)!=0u)return asfloat(0x7fc00000u);
    if(exponent==255u)return (bits&0x7fffffu)==0u ? value : asfloat(0x7fc00000u);
    return platformSqrtF32(value);
}
#endif

public float sdfSqrtF32<T : __BuiltinFloatingPointType>(float value)
{
#if LG_M1_SF_EMULATION
    if (T is half) return m1SqrtF32(value);
#endif
    return sqrt(value);
}
public float sdfRsqrtF32<T : __BuiltinFloatingPointType>(float value)
{
#if LG_M1_SF_EMULATION
    if (T is half) return m1RsqrtF32(value);
#endif
    return rsqrt(value);
}
public T glassSqrt<T : __BuiltinFloatingPointType>(T value)
{
#if LG_M1_SF_EMULATION
    if (T is half) return T(platformSqrtHalf(roundToHalf(value.toFloat())));
#endif
    return sqrt(value);
}
public T glassRsqrt<T : __BuiltinFloatingPointType>(T value)
{
#if LG_M1_SF_EMULATION
    if (T is half) return T(platformRsqrtHalf(roundToHalf(value.toFloat())));
#endif
    return rsqrt(value);
}

// For a proven point/texel fetch from an UNORM8 image. Recover the stored
// bytes before rebuilding native RNE-half values; do not apply to filtered
// samples or computed colors.
public half4 unorm8TexelHalf(half4 value)
{
    uint4 bytes=uint4(float4(value)*255.0f+0.5f);
    uint4 bits=uint4(unormByteHalfBits(bytes.x),unormByteHalfBits(bytes.y),
        unormByteHalfBits(bytes.z),unormByteHalfBits(bytes.w));
    return half4(half(unpackHalf2x16ToFloat(bits.x).x),half(unpackHalf2x16ToFloat(bits.y).x),
        half(unpackHalf2x16ToFloat(bits.z).x),half(unpackHalf2x16ToFloat(bits.w).x));
}
public half unorm8ScalarHalf(float value)
{
    uint byte=uint(saturate(value)*255.0f+0.5f);
    return half(unpackHalf2x16ToFloat(unormByteHalfBits(byte)).x);
}
// Native RNE-half product for known nonnegative unit-range inputs.
public half4 multiplyUnormHalf(half4 value,half factor)
{
    uint f=packHalf2x16(float2(factor.toFloat(),0))&0xffffu;
    uint4 bits=uint4(packHalf2x16(float2(value.x.toFloat(),0)),
        packHalf2x16(float2(value.y.toFloat(),0)),
        packHalf2x16(float2(value.z.toFloat(),0)),
        packHalf2x16(float2(value.w.toFloat(),0)))&0xffffu;
#if LG_M1_SF_EMULATION
    // Positive finite [0,1] products plus +0 have the same RNE-half result.
    // Negative zero, negative values, nonfinite values and other ranges keep
    // the original bit-arithmetic path below.
    if(f<=0x3c00u && all(bits<=uint4(0x3c00u))){
        half4 v=half4(nativeHalfFromWord(bits.x),nativeHalfFromWord(bits.y),
                     nativeHalfFromWord(bits.z),nativeHalfFromWord(bits.w));
        return glassFma(v,half4(nativeHalfFromWord(f)),half4(0.0h));
    }
#endif
    uint4 product=uint4(positiveHalfFmaBits(bits.x,f,0),positiveHalfFmaBits(bits.y,f,0),
        positiveHalfFmaBits(bits.z,f,0),positiveHalfFmaBits(bits.w,f,0));
    return half4(half(unpackHalf2x16ToFloat(product.x).x),half(unpackHalf2x16ToFloat(product.y).x),
        half(unpackHalf2x16ToFloat(product.z).x),half(unpackHalf2x16ToFloat(product.w).x));
}

// Exact positive binary16 attachment arithmetic, using only uint32 pairs.
// All finite values in[0,1] are integer multiples of2^-24. Their product and
// sum are represented exactly in Q48 before one ties-to-even half narrowing.
uint positiveHalfQ24(uint bits)
{
    uint exponent=(bits>>10)&31u;
    uint mantissa=bits&1023u;
    return exponent==0 ? mantissa : (mantissa|1024u)<<(exponent-1u);
}
uint positiveQ24HalfBits(uint value)
{
    if(value<1024u)return value;
    uint top=uint(firstbithigh(value));
    uint shift=top-10u;
    uint sig=value>>shift;
    if(shift!=0) {
        uint rest=value&((1u<<shift)-1u),middle=1u<<(shift-1u);
        if(rest>middle || (rest==middle && (sig&1u)!=0))sig++;
    }
    return ((top-9u)<<10)+(sig-1024u);
}
uint unormByteHalfBits(uint value)
{
    if(value==0)return 0;
    uint top=uint(firstbithigh(value));
    uint sig=((value<<(18u-top))+127u)/255u;
    return ((top+7u)<<10)+(sig-1024u);
}
uint positiveHalfFmaBits(uint a,uint b,uint c)
{
    uint av=positiveHalfQ24(a),bv=positiveHalfQ24(b),cv=positiveHalfQ24(c);
    uint low0=(av&65535u)*(bv&65535u);
    uint middle=(av>>16)*(bv&65535u)+(av&65535u)*(bv>>16);
    uint low=low0+(middle<<16);
    uint high=(av>>16)*(bv>>16)+(middle>>16)+(low<low0?1u:0u);
    uint add=cv<<24;
    uint sum=low+add;
    high+=(cv>>8)+(sum<low?1u:0u);
    low=sum;
    if((high|low)==0)return 0;
    uint top=high!=0 ? 32u+uint(firstbithigh(high)) : uint(firstbithigh(low));
    uint shift=max(24u,top>10u?top-10u:0u);
    uint sig,guard,sticky;
    if(shift>32u) {
        uint s=shift-32u;
        sig=high>>s;
        guard=(high>>(s-1u))&1u;
        sticky=((high&((1u<<(s-1u))-1u))|low)!=0?1u:0u;
    } else if(shift==32u) {
        sig=high;guard=low>>31;sticky=(low&0x7fffffffu)!=0?1u:0u;
    } else {
        sig=(low>>shift)|(high<<(32u-shift));
        guard=(low>>(shift-1u))&1u;
        sticky=(low&((1u<<(shift-1u))-1u))!=0?1u:0u;
    }
    if(guard!=0 && (sticky!=0 || (sig&1u)!=0))sig++;
    return top<34u ? sig : ((top-33u)<<10)+(sig-1024u);
}


/// Metal's per-row/per-column derivative stencil. Unqualified SPIR-V
/// derivatives permit a coarser stencil; both AMD devices select it by default.
/// Original float/half x*y controls establish the native fine result exactly.
public float glassFwidth(float value)
{
    __target_switch
    {
    case metal:
        __intrinsic_asm "fwidth($0)";
    default:
        return abs(ddx_fine(value))+abs(ddy_fine(value));
    }
}

float fmaSafeFused(float a,float b,float c)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$float = OpExtInst glsl450 Fma $a $b $c;
        OpDecorate %v FPFastMathMode 0;
        result:$$float = OpCopyObject %v;
    };
    default: return fma(a,b,c);
    }
}
// Exact finite binary16 FMA through an exact binary32 product, FastTwoSum,
// and correction only when binary32 rounding lands on a binary16 midpoint.
// No Float64 or Int64 capability. Special inputs use the original canonical-NaN and IEEE infinity rules.
float fmaSafeAdd(float a,float b)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$float = OpFAdd $a $b;
        OpDecorate %v FPFastMathMode 0;
        result:$$float = OpCopyObject %v;
    };
    default: return a+b;
    }
}
float fmaSafeSubtract(float a,float b)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$float = OpFSub $a $b;
        OpDecorate %v FPFastMathMode 0;
        result:$$float = OpCopyObject %v;
    };
    default: return a-b;
    }
}
float fmaSafeMultiply(float a,float b)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
        OpCapability FloatControls2;
        OpExtension "SPV_KHR_float_controls2";
        %v:$$float = OpFMul $a $b;
        OpDecorate %v FPFastMathMode 0;
        result:$$float = OpCopyObject %v;
    };
    default: return a*b;
    }
}
uint fmaRoundHalfBits(float x)
{
    uint bits=asuint(x),sign=(bits>>16)&0x8000u;
    uint v=bits&0x7fffffffu,e=v>>23;
    if(e>=143u)return sign|((e==255u&&(v&0x7fffffu)!=0)?0x7e00u:0x7c00u);
    if(e<102u)return sign;
    if(e>=113u)return sign|(((v+0xfffu+((v>>13)&1u))>>13)-0x1c000u);
    uint m=(v&0x7fffffu)|0x800000u,shift=126u-e;
    uint q=m>>shift,rem=m&((1u<<shift)-1u),tie=1u<<(shift-1u);
    return sign|(q+uint(rem>tie||(rem==tie&&(q&1u)!=0)));
}
// Default false: only an externally qualified exact backend profile may
// select the fused native instruction. Every other device keeps the portable
// midpoint-corrected implementation below. This is a VkBool32 at SpecId100.
[[vk::constant_id(100)]] const bool qualifiedNativeHalfFma=false;
half qualifiedFusedHalf(half a,half b,half c)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
            OpCapability FloatControls2;
            OpExtension "SPV_KHR_float_controls2";
            %v:$$half = OpExtInst glsl450 Fma $a $b $c;
            OpDecorate %v FPFastMathMode 0;
            result:$$half = OpCopyObject %v;
        };
    default: return fma(a,b,c);
    }
}

public float nativeHalfToFloat(half value)
{
    __target_switch
    {
    case spirv:
        return spirv_asm {
            OpCapability FloatControls2;
            OpExtension "SPV_KHR_float_controls2";
            %wide:$$float = OpFConvert $value;
            OpDecorate %wide FPFastMathMode 0;
            result:$$float = OpCopyObject %wide;
        };
    default: return value.toFloat();
    }
}

half fmaHalfExact(half a,half b,half c)
{
    if(qualifiedNativeHalfFma){
        half result=qualifiedFusedHalf(a,b,c);
        // The qualified IEEE instruction preserves all finite/Inf/zero cases.
        // Canonicalize every NaN payload/sign to the original M1 result.
        uint magnitude=asuint(result.toFloat())&0x7fffffffu;
        return magnitude>0x7f800000u ? half(unpackHalf2x16ToFloat(0x7e00u).x) : result;
    }
    float af=nativeHalfToFloat(a),bf=nativeHalfToFloat(b),cf=nativeHalfToFloat(c);
    uint ax=asuint(af),bx=asuint(bf),cx=asuint(cf);
    uint am=ax&0x7fffffffu,bm=bx&0x7fffffffu,cm=cx&0x7fffffffu;
    if(am>0x7f800000u||bm>0x7f800000u||cm>0x7f800000u)
        return half(unpackHalf2x16ToFloat(0x7e00u).x);
    bool ai=am==0x7f800000u,bi=bm==0x7f800000u,ci=cm==0x7f800000u;
    if((ai&&bm==0u)||(bi&&am==0u))
        return half(unpackHalf2x16ToFloat(0x7e00u).x);
    if(ai||bi){
        uint sign=(ax^bx)&0x80000000u;
        if(ci&&((cx^sign)&0x80000000u)!=0u)
            return half(unpackHalf2x16ToFloat(0x7e00u).x);
        return half(asfloat(sign|0x7f800000u));
    }
    if(ci)return c;
    float sum=fmaSafeFused(af,bf,cf);
    uint packed=fmaRoundHalfBits(sum),word=asuint(sum),mag=word&0x7fffffffu;
    uint e=mag>>23;
    if(e>=102u&&e<=142u){
        uint shift=e>=113u?13u:126u-e;
        uint significand=(mag&0x7fffffu)|0x800000u;
        uint mask=(1u<<shift)-1u,tie=1u<<(shift-1u);
        if((significand&mask)==tie){
            // A rounded Float32 sum can cross a half rounding boundary
            // only when it lands on that exact midpoint. Recover its signed
            // residual only here; all other results already round correctly.
            float product=fmaSafeMultiply(af,bf);
            float hi=product,lo=cf;
            if(abs(hi)<abs(lo)){float t=hi;hi=lo;lo=t;}
            float error=fmaSafeSubtract(lo,fmaSafeSubtract(sum,hi));
            if(error!=0.0f){
                uint down=e>=113u?(mag>>13)-0x1c000u:significand>>shift;
                bool increase=(error>0.0f)==((word&0x80000000u)==0);
                packed=((word>>16)&0x8000u)|(down+uint(increase));
            }
        }
    }
    return half(unpackHalf2x16ToFloat(packed).x);
}

// The public float specialization remains the existing native fma operation.
public T glassFma<T : __BuiltinFloatingPointType>(T a,T b,T c)
{
    if(T is half)
    {
        __target_switch
        {
        case spirv: return T(fmaHalfExact(half(a.toFloat()),half(b.toFloat()),half(c.toFloat())));
        default: return fma(a,b,c);
        }
    }
    return fma(a,b,c);
}
public vector<T,N> glassFma<T : __BuiltinFloatingPointType, let N : int>(
    vector<T,N> a,vector<T,N> b,vector<T,N> c)
{
    if(T is half)
    {
        var r=vector<T,N>(T(0));
        [ForceUnroll] for(int i=0;i<N;i++)r[i]=glassFma<T>(a[i],b[i],c[i]);
        return r;
    }
    return fma(a,b,c);
}

// Only the extracted native-half specialization changes; float and generic
// compilation retain their original division expressions.
public T glassDivide<T : __BuiltinFloatingPointType>(T numerator,T denominator)
{
#if LG_M1_SF_EMULATION
    if(T is half)return T(nativeHalfDivide(half(numerator.toFloat()),half(denominator.toFloat())));
#endif
    return numerator/denominator;
}
public vector<T,N> glassDivide<T : __BuiltinFloatingPointType,let N:int>(vector<T,N> numerator,vector<T,N> denominator)
{
#if LG_M1_SF_EMULATION
    if(T is half){
        var result=vector<T,N>(T(0));
        [ForceUnroll] for(int i=0;i<N;i++)result[i]=glassDivide<T>(numerator[i],denominator[i]);
        return result;
    }
#endif
    return numerator/denominator;
}


// Original cached SDF operation is linear filtering of one RGBA16F level.
// Texture-unit phase/accumulation belongs to the selected Vulkan platform.
public vector<T,4> sampleCachedSdf<T : __BuiltinFloatingPointType,let nativeHalfTexture:bool>(
    Texture2D<vector<T,4>> source,SamplerState sampler,float2 uv)
    where vector<T,4> : ITexelElement
{
#if LG_M1_SF_EMULATION
    if(nativeHalfTexture && T is half){
        float2 position=float2(nativeClampSamplerCoordinate(uv.x),nativeClampSamplerCoordinate(uv.y));
        let value=source.Sample(sampler,position);
        return vector<T,4>(T(roundToHalf(value.x.toFloat())),T(roundToHalf(value.y.toFloat())),
            T(roundToHalf(value.z.toFloat())),T(roundToHalf(value.w.toFloat())));
    }
#endif
    return source.Sample(sampler,uv);
}
