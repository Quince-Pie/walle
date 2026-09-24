# Preserve native half boundaries

The native half GB path has half displacement-normal registers (clear0x17b0/0x17b8, mixedFMA1438), half refracted-radius results (0x1a46/0x1b0a, mixedMUL1796), and a half radius remap before half log2 (1446→1622). Their Float32 consumers must see the rounded half values. A plain Slang cast is insufficient in the current backend: the emitted SPIR-V contains narrowing/widening conversions, but Mesa26.1.8's inexact conversion-chain rule can remove them. `roundToHalf` applies FloatControls2 FPFastMathModeNone at the required boundary.

The same issue can invalidate the portable half-FMA proof: its half arguments are widened to Float32 before the exact-product calculation. `nativeHalfToFloat` preserves each incoming half boundary. It introduces no arithmetic adjustment or new device feature. The qualified native-half-FMA specialization is unchanged. Float public entrypoints remain byte-identical.

Decisive evidence under `/tmp/walle-work/fidelity-completion/resume_1400/arithmetic`:

- `source_carrier/ANCHOR.json`: original GB into RGBA16F without blending repeats exactly; applying the extracted half attachment epilogue reproduces every original BGRA8 GB pixel. This anchors pre-blend comparison.
- `normal_narrow/RESULTS.json`: protecting the displacement-normal halves fixes the first differing native operation and makes the specific0,76 source value exact onall3GPUs.
- `radius_narrow/RESULTS.json`: after radius/remap preservation, both AMDs match every native pre-blend component across36,864 fully observed quads when supplied native coordinates. CPU retains16 differingpixels, outside this patch's established closure.
- `BOUNDARY_RESULTS.json`: all31,744 nonnegative finite half radii yield the same sampled LOD weights as the complete original log2 extraction. Its two intermediate log2 differences never change a samplerweight; no new log2table is required for these callers.
- `fma_input_boundary/INPUTS.json`:4,194,304 Float32 input triples lie inside the rounding bins of previously captured native half-FMA triples. Without protected half arguments,559,914CPU and559,908AMD results differ. With protected widening, all three devices match every native result.
- `boundary_runtime/vcm/RESULTS.json`:24 fresh VCM outputs match the previously native-qualified originals byte-for-byte, covering two batches, both matrices, both outputformats and all3devices.
- `boundary_runtime/float_controls/results.json`, `shader_build.json`, `scenes_v1/results.json`: five float entrypoints unchanged; all27shaderentries compile/validate;21scene runs passvalidation/teardown. Raster and other separately recorded residuals remain.

Primary sources: the original clear GB decoded stream at `fidelity-fix/gpu-precision/gb_code/clear_original_212_fragment.code.pretty`; original AIR `work/mac/qc_ll/glass_background_sdf_no_bleed_lph.ll`; Mesa26.1.8 `src/compiler/nir/nir_opt_algebraic.py` rule aroundline1200. The local Slang emitter's precise-mode logic decorates arithmetic with NoContraction, which does not by itself preserve these conversion chains. These are native rounding boundaries, not fitted image corrections.
