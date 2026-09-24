# Retained M1 scalar functions

**Archived M1 SFU reconstruction/control.** The exhaustive extraction and decoder proof below remain reference evidence. The current accepted `hw_circle` runtime uses platform sqrt/rsqrt and has no scalar lookup buffer. [PLATFORM_OPERATIONS.md](PLATFORM_OPERATIONS.md) records the current operation and resource boundary; historical selected/accepted wording below concerns the retained table control.

`m1_rsqrt.bin` is a lossless representation of the retained native scalar
reciprocal-square-root function. It is algorithm data, independent of scene
geometry, images, colors or residual pixels. No optical coefficients or
per-image correction entries are present.

Target identity: Apple M1 Max, macOS26.6.1 build25G76. The original QuartzCore
UUID is `F1BA3189-E95A-3ECA-B59A-5A6872754484`; identity is retained at
`/tmp/walle-work/fidelity-fix/native-scene/v9/portrait/regular/scene/identity.json`. Original compiled
fragment programs and independent scalar calibration kernels use AGX code
subtype530 (`212`), decoded through the retained Apple LLVM decoder.

Original operation mapping:

- Float reciprocal square root is opcode1886.
- Default/fast float square root is opcode1994 followed by float multiply1786.
  Its result equals `x * native_rsqrt(x)` for every positive-normal Float32
  input, with the native special rules below.
- The original half SDF contains float square roots and reciprocal square
  roots internally. Its final gradient normalization and bevel/keyfill
  operations also contain half versions. All65,536 half input bit patterns
  reproduce the native half sqrt/rsqrt outputs by rounding the reconstructed
  Float32 operation to half.

The independent default and fast rsqrt calibration code has SHA256
`354ca4f1d2c06e42efa0eb24a53a1ce5bd77a533f2c2445718f0661ee28a7400`.
Complete archive/code hashes are in
`/tmp/walle-work/fidelity-fix/gpu-precision/sqrt_calibration/code/manifest.json`. Relevant original
SDF instruction schedules are retained in the original GB and specialized
key/fill archives, not inferred from images.

## Encoding and reconstruction

File length:6,291,456 bytes. SHA256:
`df3c6e6cbcf0e5434dd951f936950d604d3c1e27fdbf8c5a6d3d1ff6c5efa9f6`.

There are16,777,216 Float32 outputs for every input bit pattern in[.5,2), ordered
by input bits. Each block of32 entries occupies three little-endian uint32
words: an output-bit anchor, a low bitplane, and a high bitplane. Bit i of
the planes encodes `delta[i]+1`, where `delta[i]=output[i]-output[i+1]` as
integer bit patterns. Every delta is in[-1,2]. Bit31 of each plane is unused.

For offset j in0..31, mask the first j bits. Subtract
`popcount(low) + 2*popcount(high) - j` from the anchor. This exactly recovers
the output bits. The independent compressor roundtrip checks all entries.

For positive-normal input with biased exponent E, the table index is
`((E&1)<<23) | mantissa`. The normalized input exponent is126+(E&1).
Subtract `(E - 126 - (E&1))/2` from the reconstructed output's exponent.
All reciprocal-square-root results in this domain remain normal, so that
power-of-two scaling is an exact bit adjustment.

The graphics shader uses a read-only `StructuredBuffer<uint>` at set0,
binding9. The [fixed tint-ramp response](M1_RAMP.md) occupies a separate128KiB
tail; the reciprocal-square-root data begins at word0 unchanged. Word stride
is4, block word index is `block*3`; there is no uint3
alignment ambiguity. It requires no new optional Vulkan feature. The already
qualified FloatControls2 conversion preserves half narrowing. The table is
one renderer-owned resource shared by outputs; it is not allocated per draw.

## Domain verification

The original GPU verified the reconstruction over all4,294,967,296 Float32
bit patterns, including both signs of normal/subnormal numbers, signed zeros,
both infinities and every NaN payload. There were zero mismatches.

The verified rules are:

| Input | sqrt result | rsqrt result |
|---|---|---|
| Positive normal | rounded float product of input and reconstructed rsqrt | reconstructed table result |
| Positive zero/subnormal | +0 | +infinity |
| Negative zero/subnormal | -0 | -infinity |
| Negative normal or -infinity | canonical quiet NaN0x7fc00000 | canonical quiet NaN0x7fc00000 |
| +infinity | +infinity | +0 |
| Any NaN | canonical quiet NaN0x7fc00000 | canonical quiet NaN0x7fc00000 |

Evidence is under `/tmp/walle-work/fidelity-fix/gpu-precision/sqrt_calibration/`:

- `full_all_bits/comparison.json`: complete signed Float32 domain, with
  mismatch counters and bounded failure records.
- `full_domain_corrupt/comparison.json`: flipping one base entry causes
  exactly127 rsqrt and127 sqrt failures at the expected exponents. This
  positive control checks that the evaluator detects incorrect reconstruction.
- `half_domain/full_comparison.json`: every half bit pattern, including
  special values and half subnormals, matches exactly.
- `compressed/comparison.json`: independent lossless block decoding.

The Vulkan decoder was then checked over all16,777,216 base inputs, for both
sqrt and rsqrt, on the discrete AMD GPU, integrated AMD GPU and llvmpipe.
All three produce exact native output bits with zero validation errors;
see `/tmp/walle-work/fidelity-fix/gpu-precision/native_math_candidate/decoder/comparison.json`.
The remaining exponent reconstruction is integer scaling whose complete
native-domain correspondence is tested above.

Reproduction uses the retained local scripts under `/tmp/walle-work/fidelity-fix/gpu-precision/`:
`archive_sqrt_calibration.py`, `make_sqrt_range.py`, `make_sqrt_domain.py`,
`check_sqrt_range.py`, `compress_sqrt.py`, and
`check_native_math_decoder.py`. Generated plans, source, native remote run
identity, result sizes and hashes are retained alongside every comparison.
`full_all_bits` adds the signed and NaN-domain checks to the normal-domain
plan; its exact source and descriptor are retained there.

## Source scope

`LG_M1_SF_EMULATION=1` enables the binding and functions. Public typed helpers
preserve the existing T=float path. Walle's T=half SDF uses the reconstruction
for its internal Float32 operations and for half normalization, bevel and
key/fill operations. Integer coefficient and half rounding boundaries remain
explicit; no global precision mode or postcompiled binary patch is used.

Circle_image10's original float dot is a rounded x*x followed by FMA(y,y,x²),
then the native square root. A freshly specialized original archive establishes
PCs0x08,0x0e,0x16 and0x1a respectively. Wrapper spelling is:

```
m1SqrtF32(fma(p.y, p.y, orderedMultiply(p.x, p.x)))
```

Reference: original `fixed_frag_lph_cpf.ll:9993` and
`/tmp/walle-work/fidelity-fix/gpu-precision/circle_archive/code/circle_image10_212_fragment.code.pretty`.
The circle epilogue's separate reciprocal1862 and mixed coverage FMA are
distinct operations; this file does not claim they are emulated by the rsqrt
table.

This qualification establishes scalar-function identity for the retained M1
target, not whole-frame identity. Native/Vulkan varying interpolation and
remaining source arithmetic still require their own controls. The frame9
constant-coordinate SDF and both previously missing highlight-edge values are
exact after this primitive replacement. Whole-scene residuals are reported
separately. Resource, startup and complete-frame costs are measured by the
renderer integration evaluation rather than inferred from primitive tests.
