implementing liquidglass;

// Backdrop blur pyramid used by glassBackground (QuartzCore capture_backdrop + prepare_blur_mipmap):
//   1. downsample_4_frag: the backdrop is captured at 1/4 scale with four bilinear taps (weights 1/4); below 1/4
//      scale capture_backdrop uses downsample_8_frag instead (sixteen taps).
//   2. variable_blur_copy_base_mip_compute: the capture is copied into mip 0 of the blur texture with
//      its edge texels replicated outward, and mip 1 = diamond13(box2x2(mip 0)).
//   3. variable_blur_downsample_compute, per level: mip L+1 = diamond13(box2x2(mip L)), where the 2x2
//      box comes from one bilinear sample at the corner of four source texels.
// glassBackground then reads the pyramid with trilinear filtering at LOD blurLevel(radius).

/// DownsampleBlurUniforms (80 bytes with half pixels, 96 with float): per-tap UV offsets and weights (4-, 6- and 8-tap
/// variants). MetalContext::draw 0x18a8231f8..0x18a823300 fills offset[] from Context+0xb0..0xef and weight[] from the
/// floats at Context+0x100..0x11f (fcvt to half), as the descriptor bytes 0x42 0x94 of texture functions 27..29 say.
public struct DownsampleBlurUniforms<T : __BuiltinFloatingPointType>
{
    public float2 offset[8];
    public vector<T, 4> weight[2];
}

/// downsample_4_frag: four bilinear taps accumulated with fused multiply-adds, color scaled by the EDR
/// factor. QuartzCore uses offsets of +-1 source texel and weights 0.25 (see README).
public vector<T, 4> downsample4<T : __BuiltinFloatingPointType, let kNativeUnorm8 : bool = false>(Texture2D<vector<T, 4>> tex, SamplerState s, float2 uv, DownsampleBlurUniforms<T> u, T edrScale)
    where vector<T, 4> : ITexelElement
{
    var acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,u.offset[0]+uv,T(0)), vector<T, 4>(u.weight[0].x), vector<T, 4>(T(0.0)));
    acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,u.offset[1]+uv,T(0)), vector<T, 4>(u.weight[0].y), acc);
    acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,u.offset[2]+uv,T(0)), vector<T, 4>(u.weight[0].z), acc);
    acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,u.offset[3]+uv,T(0)), vector<T, 4>(u.weight[0].w), acc);
    return vector<T, 4>(vector<T, 3>(edrScale) * acc.xyz, acc.w);
}

/// downsample_6_frag (texture function 28, `ds6`): three symmetric tap pairs uv -+ offset[k], k = 0..2, sampled
/// with the implicit LOD (bias 0; the other downsample shaders use level 0), each pair summed and scaled by
/// weight[0][k]. Color scaled by the EDR factor, alpha kept.
public vector<T, 4> downsample6<T : __BuiltinFloatingPointType, let kNativeUnorm8 : bool = false>(Texture2D<vector<T, 4>> tex, SamplerState s, float2 uv, DownsampleBlurUniforms<T> u, T edrScale)
    where vector<T, 4> : ITexelElement
{
    // The six taps in QuartzCore's order, uv - offset[2] ... uv + offset[2], stored before the weights are read: the
    // order of these loads decides how the Metal compiler orders the commutative operands below (and so which
    // products it fuses). The array keeps Slang from folding the samples into the expression.
    vector<T, 4> tap[6];
    tap[0] = kNativeUnorm8 && T is half ? sampleGlassBackdrop<T,true>(tex,s,uv - u.offset[2],T(0)) : tex.Sample(s, uv - u.offset[2]);
    tap[1] = kNativeUnorm8 && T is half ? sampleGlassBackdrop<T,true>(tex,s,uv - u.offset[1],T(0)) : tex.Sample(s, uv - u.offset[1]);
    tap[2] = kNativeUnorm8 && T is half ? sampleGlassBackdrop<T,true>(tex,s,uv - u.offset[0],T(0)) : tex.Sample(s, uv - u.offset[0]);
    tap[3] = kNativeUnorm8 && T is half ? sampleGlassBackdrop<T,true>(tex,s,u.offset[0] + uv,T(0)) : tex.Sample(s, u.offset[0] + uv);
    tap[4] = kNativeUnorm8 && T is half ? sampleGlassBackdrop<T,true>(tex,s,u.offset[1] + uv,T(0)) : tex.Sample(s, u.offset[1] + uv);
    tap[5] = kNativeUnorm8 && T is half ? sampleGlassBackdrop<T,true>(tex,s,u.offset[2] + uv,T(0)) : tex.Sample(s, u.offset[2] + uv);
    let w = u.weight[0];
    let acc = (vector<T, 4>(w.x) * (tap[3] + tap[2]) + vector<T, 4>(w.y) * (tap[4] + tap[1])) + (tap[5] + tap[0]) * vector<T, 4>(w.z);
    return vector<T, 4>(acc.xyz * vector<T, 3>(edrScale), acc.w);
}

/// downsample_8_frag (texture function 29, `ds8`, backdrop captures below 1/4 scale): sixteen bilinear taps, the
/// four offsets offset[0..3] mirrored into all four quadrants (+x +y, +x -y, -x +y, -x -y in that order), each with
/// the fixed weight 1/16 (the weight[] uniforms are not read), accumulated with fused multiply-adds. Color scaled by
/// the EDR factor, alpha kept.
public vector<T, 4> downsample8<T : __BuiltinFloatingPointType, let kNativeUnorm8 : bool = false>(Texture2D<vector<T, 4>> tex, SamplerState s, float2 uv, DownsampleBlurUniforms<T> u, T edrScale)
    where vector<T, 4> : ITexelElement
{
    let w = vector<T, 4>(T(0.0625));
    var acc = vector<T, 4>(T(0.0));
    [ForceUnroll]
    for (int k = 0; k < 4; k++)
        acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,u.offset[k] + uv,T(0)), w, acc);
    [ForceUnroll]
    for (int k = 0; k < 4; k++)
        acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,float2(u.offset[k].x, -u.offset[k].y) + uv,T(0)), w, acc);
    [ForceUnroll]
    for (int k = 0; k < 4; k++)
        acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,float2(-u.offset[k].x, u.offset[k].y) + uv,T(0)), w, acc);
    [ForceUnroll]
    for (int k = 0; k < 4; k++)
        acc = glassFma(sampleGlassBackdrop<T,kNativeUnorm8>(tex,s,float2(-u.offset[k].x, -u.offset[k].y) + uv,T(0)), w, acc);
    return vector<T, 4>(vector<T, 3>(edrScale) * acc.xyz, acc.w);
}

/// Weights of the 13-tap diamond filter (sum 1): center, the 4 axial neighbours, the 4 diagonal
/// neighbours and the 4 texels two steps away along the axes.
public static const half kDiamondCenter = half(0.10546875);
public static const half kDiamondAxial = half(0.090209961);
public static const half kDiamondDiagonal = half(0.077087402);
public static const half kDiamondFar = half(0.056335449);

/// Diamond filter with QuartzCore's summation order (tiled path); colors whose magnitude is below
/// 1e-4 are flushed to zero, alpha is kept.
public half4 diamond13(half4 center, half4 left, half4 up, half4 right, half4 down,
                       half4 upLeft, half4 upRight, half4 downLeft, half4 downRight,
                       half4 left2, half4 up2, half4 right2, half4 down2)
{
    let diag = ((upRight + upLeft) + downLeft) + downRight;
    let axial = ((left + up) + right) + down;
    let far = ((left2 + up2) + right2) + down2;
    // G13S-B0: Apple's decoded half FMA chain is centre, diagonal, axial, far.
    // Direct instruction/constant-bank derivation: work/finish/provenance/ARITHMETIC.md.
    let v = glassFma(far, half4(kDiamondFar), glassFma(axial, half4(kDiamondAxial), glassFma(diag, half4(kDiamondDiagonal), center * kDiamondCenter)));
    return half4(select(abs(v.xyz) < half3(half(0.0001)), half3(0.0h), v.xyz), v.w);
}

/// AGX2 stores the diamond sum directly. Its AIR has no small-colour flush:
/// variable_blur_downsample_compute_agx2.ll:165-174 (and the repeated tile branches).
public half4 diamond13Unflushed(half4 center, half4 left, half4 up, half4 right, half4 down,
                       half4 upLeft, half4 upRight, half4 downLeft, half4 downRight,
                       half4 left2, half4 up2, half4 right2, half4 down2)
{
    let diag = ((upRight + upLeft) + downLeft) + downRight;
    let axial = ((left + up) + right) + down;
    let far = ((left2 + up2) + right2) + down2;
    return glassFma(far, half4(kDiamondFar), glassFma(axial, half4(kDiamondAxial), glassFma(diag, half4(kDiamondDiagonal), center * kDiamondCenter)));
}

/// Diamond filter with the summation order of the per-level kernel's single-texel path.
public half4 diamond13Single(half4 center, half4 left, half4 up, half4 right, half4 down,
                             half4 upLeft, half4 upRight, half4 downLeft, half4 downRight,
                             half4 left2, half4 up2, half4 right2, half4 down2)
{
    let diag = ((downLeft + upLeft) + upRight) + downRight;
    let axial = ((up + left) + down) + right;
    let far = ((up2 + left2) + down2) + right2;
    let v = glassFma(far, half4(kDiamondFar), glassFma(diag, half4(kDiamondDiagonal), glassFma(axial, half4(kDiamondAxial), center * kDiamondCenter)));
    return half4(select(abs(v.xyz) < half3(half(0.0001)), half3(0.0h), v.xyz), v.w);
}

/// 2x2 box of explicitly read texels (base-mip kernel).
public half4 box2x2(half4 topLeft, half4 topRight, half4 bottomLeft, half4 bottomRight)
{
    return (((topRight + topLeft) + bottomLeft) + bottomRight) * 0.25h;
}

/// VarBlurCopyBaseMipUniforms (32 bytes).
public struct BlurCopyBaseMipUniforms
{
    /// Capture texel read for base-mip texel (0, 0).
    public int16_t2 tex_coord_base;
    public int16_t2 pad0;
    /// Inclusive texel clamp (min.xy, max.xy) applied to capture reads: replicates the edges.
    public int16_t4 tex_coord_clamp;
    /// Extent of mip 0 that is written; mip 1 gets half of it.
    public uint16_t2 dst_0_size;
    public uint16_t2 dst_1_size;
    /// Mip level of the blurred output (mip 0 is dst_1_level - 1).
    public uint16_t dst_1_level;
    /// When set, only the blurred level is written.
    public bool no_base_mip;
}

/// VariableBlurDownsampleComputeUniforms (16 bytes).
public struct BlurDownsampleUniforms
{
    public uint16_t src_level;
    public uint16_t dst_level;
    public uint16_t dst_w;
    public uint16_t dst_h;
    /// 1 / dst_w, 1 / dst_h.
    public float dst_dx;
    public float dst_dy;
}
