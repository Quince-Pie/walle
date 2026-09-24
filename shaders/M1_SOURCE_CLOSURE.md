# Extracted SDF and displacement operation closure

Evidence filenames and `../` references below are relative to the retained
record directory `/tmp/walle-work/fidelity-completion/resume_1400/arithmetic/source_closure`,
where the complete manifests, controls and reproduction scripts reside.

Scope: the native-half Liquid Glass path on the supplied M1 Max, macOS 26.6.1 build 25G76, QuartzCore 1195.17. This closes explicit operation/order/precision mappings; it does not claim that a different GPU's floating-point units or rasterizer produce identical bits. The user's clarified Walle acceptance permits identified platform operation implementations, while requiring the extracted algorithm, constants, boundaries and pass/state behavior.

`SOURCE_CLOSURE.patch` changes only `lg_sdf.slang` and `lg_glass_background.slang`. Exact source hashes are in `SOURCE_PINS.json`. The patch retains all previously integrated division, half narrowing, portable half FMA input protection and cached-SDF work. It adds no resource, feature, fitted value or approximation table. Public Float32 controls remain unchanged.

## Primary mechanism mapping

The original clear shader is `/tmp/walle-work/fidelity-fix/gpu-precision/gb_code/clear_original_212_fragment.code.pretty`, backed by `gb_archives/clear_original.metalar` (SHA256 `5c488dda84d93ea9c747e40897ddd777a2f5d2d58b03f145815c1aa126fe57d0`). The decoded binary SHA256 is `b3dca1d5c426109811ef49a823d89d8069278fa01f51f87754cbc0cabc179485`. The corresponding original AIR is `/tmp/extract-liquidglass/work/mac/qc_ll/glass_background_sdf_no_bleed_lph.ll`. Inline constants were decoded into `../SDF_CONSTANT_BANK.json`; values below retain those exact Float32 encodings.

| Mechanism | Original compiled schedule | Delivered mapping |
| --- | --- | --- |
| Extent, axis and final corner interpolation | Paired FMA1402: extent 0x7c/0x8e; axes 0x7d8/0x7e4 and 0x7ec/0x7fa; corner 0x808/0x810 | Previously integrated `sdfMixF32`: `fma(t,b,fma(-t,a,a))`. Register stores 0x368/0x370 map u7 to circular.y and u8 to circular.x; final weight is w, with dy as second operand. |
| Float32 squared norm | x product then y FMA at 0x678/0x684 and 0x6ba/0x6c6 | Previously integrated separately rounded x*x plus protected y*y+x2 FMA. |
| Circular-cell affine | FMA1402 at 0x69a/0x6a2, then max with zero | Previously integrated `fma(c,1.528665,-0.528665)`. Circle sqrt affine uses the FMA at 0x7ca with 0.65416557 and 0.34583443. |
| Quartic | 0x6fc, 0x70a, 0x71a, 0x726 | Four explicit FMAs preserve the negative-polynomial schedule: t*(-0.926054)+3.15601; stage*t-3.64122; stage*t+1.26803; stage*(-t)-0.268531. |
| Continuous denominator | t*t at 0x6dc; multiply saturated length at 0x6e8; FMA with 1 at 0x734 | Ordered t*t, ordered multiply with saturate(len), then explicit negativePolynomial*factor+1 FMA. Original AIR division remains division. |
| Continuous length plus one | AIR sqrt(norm)+1; M1 fast-sqrt opcode1994 at 0x696, product contracted into FMA1404 at 0x75a | Preserve AIR sqrt(norm), then explicit add 1. The machine contraction is an identified backend lowering difference, discussed below. |
| Blend weight w | Sign comparison 0x68c; 0.5-s at 0x6e2; saturated mixed FMA at 0x6ee | Explicit protected subtraction and FMA, retaining saturation and sign selection. |
| Gradient normalization | Half interpolation FMAs, half x*x then y FMA, half rsqrt and half products | Existing extracted `glassFma`, `glassRsqrt` and half precision boundaries are retained. |
| Final SDF gradient transform | y products at 0x840/0x846 then x FMAs at 0x84c/0x854; alternate path 0x100c/0x1012 then 0x1018/0x1020; half conversion 0x1056/0x105c | Protect half gradient widening, ordered y product then explicit x FMA for each matrix row, then protected half narrowing. |
| GB displacement transform | x products opcode1789 at 0x17a4/0x17aa; y FMA1438 to half at 0x17b0/0x17b8 | Explicit x product then y FMA and the already established protected half narrowing. |
| Displaced UV | Mixed FMA1414 at 0x187a/0x1882, 0x1a60/0x1a68, 0x1b24/0x1b2c | Previously integrated half lift/half normal with Float32 source UV and Float32 result. UV is not narrowed to half. |

The production Walle geometry uses circular selectors (1,1), `continuous=false`, and an identity element transform. The general quartic and transform schedules are still mapped rather than omitted because those computations remain in the extracted template. Original AIR divisions, including lo/hi and normalized local coordinates, remain divisions; the M1 compiler's approximate reciprocal lowering is not silently replaced by a newly invented operation or truth table.

## Rejected compound sqrt reconstruction

The initial closure prototype reconstructed 0x75a as `fma(m1RsqrtF32(norm),norm,1)`. This is invalid at zero: the emulated ordinary rsqrt opcode1886 returns infinity, whereas the source of the native multiplication is **fast-sqrt opcode1994**. The positive-normal identity does not transfer its special-case policy. The prototype and build receipts are retained in `../source_closure_rejected_sqrt_product/` with `REJECTED.md`; it was never promoted. The corrected patch keeps the fully extracted AIR `sqrt(norm)+1`, which is valid at zero and preserves the existing sqrt special handling. It makes no claim to emulate the native compound instruction at every Float32 bit pattern.

## Identified CPU FMA implementation difference

`../float_fma_probe/RESULTS.json` uses 256 exact dyadic triples and an integer/Fraction Float32 rounding oracle. It includes SDF affine/dot inputs and cancellation triples. Both AMD devices match the fused oracle. The CPU driver produces the separately rounded multiply/add result for all 256 triples, differing from the fused oracle in the 128 discriminating cases. Adding NoContraction does not change that result (`../float_fma_nocontraction/RESULTS.json`). The invalid combination of NoContraction and FPFastMathMode on one result was rejected by spirv-val and never dispatched.

This has a direct implementation explanation in the installed Mesa 26.1.8 source `/nix/store/dxfn3fqadh1xl8h419h2ckd3giij01fg-source`: GLSL450 Fma becomes `nir_op_ffma`; Gallivm `lp_bld_nir_soa.c` lowers that operation through `lp_build_fmuladd`; `lp_bld_arit.c` emits LLVM `llvm.fmuladd`, whose separate multiply/add lowering is observed in the probe. The emitted source/SPIR-V requests FMA. No source operation has been removed to accommodate this driver, and no production Float64 requirement or software Float32 FMA is added.

A subsequent controlled intervention identifies the 16 matched-coordinate CPU source pixels specifically: replacing only the Float32 FMA implementation with a diagnostic-only, midpoint-corrected Float64 implementation makes every compared source half component exact. That diagnostic first passes the same256 independent Fraction-oracle triples. The source result changes16 differing pixels to0, with an output hash identical to bothAMDs (`../float_fma_diagnostic/CAUSAL_RESULT.md`, `RESULTS.json`). Production gains no Float64 code or feature requirement. This supports classifying those16pixels as the pinned CPU FMA lowering difference; it does not classify unrelated full-scene bytes. Earlier SDF tracing changes six otherwise correct pixels and is retained only as an invalid-context lead (`../sdf_trace/INVALID_CONTEXT.json`).

## Verification and reproduction

`shader_build.json` records compilation and spirv-val validation of all 27 runtime entries. `float_controls/results.json` records byte-identical output for five public Float32 controls. Source-carrier comparison is in `../closure_source/RESULTS.json`, against the original RGBA16F preblend source independently anchored by exact reconstructed native BGRA8 composition in `../source_carrier/ANCHOR.json`. Only the fully observed 256x144 quads are compared; no native helper-lane data exists beyond the odd 257x145 viewport. Scene checks and ownership/validation receipts are in `scenes_v1/`.

Reproduce from the pinned artifacts with `prepare_source_closure.py`, then `LG_RUNTIME_VARIANT=.../source_closure python .../gpu-precision/build_fused_runtime.py`, `qualify_closure_float.py`, `prepare_closure_controls.py`, `run_closure_source.py`, and `LG_RUNTIME_VARIANT=.../source_closure LG_TEST_DEVICES=cpu,discrete,integrated python .../gpu-precision/confirm_fused_scenes.py scenes_v1`. GPU tests must run serially with other qualification jobs. These are correctness checks, not performance claims.

Final closure controls: both AMD source-carrier outputs match every one of147,456 compared half components; CPU retains16 differing pixels (14/16/15/13 components by channel). The21 whole-scene runs all complete with zero validation errors and clean owned-resource teardown. Their raw native differences are retained in `SCENE_SUMMARY.json`; this component check does not reclassify them or replace the parent full-corpus gate.
