# Cached SDF sampling at zero spatial weight

**Archived M1 zero-spatial-weight emulation.** The current accepted runtime uses the extracted linear sampler state and protected half boundary, without the quantized center-`Load` substitution described below. These controls establish that earlier M1 emulation, not identical filtering on every GPU. See [PLATFORM_OPERATIONS.md](PLATFORM_OPERATIONS.md).

Walle's cached SDF is a one-level RGBA16F texture. Its two cached glass
fragments explicitly select `glassBackgroundTexture<half, bleed, true, true>`.
The fourth parameter, `kNativeHalfSdf`, is independent of the third parameter
that describes the UNORM8 backdrop. Its default is false. Generic callers and
Float32 glass retain their original sampling code.

Apple's M1 linear sampler returns the selected texel when both quantized
eight-bit spatial weights are zero or full. In that case stored negative zero
becomes positive zero, and every stored NaN becomes positive canonical half
NaN `0x7e00`. All other finite values and infinities retain their half words.
The helper implements precisely this case with `Load`. Other coordinates and
multi-level textures retain the same linear `Sample` operation.

For each axis the helper applies the previously extracted coordinate clamp,
then separately rounded Float32 multiplication by the texture dimension and
subtraction of `0.5`. It clamps this position to the valid texel interval,
computes the integer base and the nearest eight-bit phase, and selects a texel
only when each phase is either 0 or 256. This is a quantized sampler condition,
not an optical epsilon or a replacement of general linear filtering by nearest
filtering. Coordinates and integer indices remain bounded by the actual
texture dimensions. The one-level guard prevents this substitution from
changing implicit mip selection.

Evidence is retained under
`/tmp/walle-work/fidelity-completion/resume_1400/cached-sampler`:

- `native/RESULTS.json` pins the original frame 17 sampler operands and result.
  The original GB replay exactly reproduces the captured GB image.
- `center-control` covers all 65,536 raw half center values at nine coordinates
  within their zero-weight cells, with five finite/nonfinite neighbor classes.
- `center-width-domain` processes 274,877,906,944 x-coordinate cases: every
  positive Float32 word below 2 for each 64-aligned width from 64 to 16,384.
  Of these, 245,717,553,225 satisfy the tested center/clamp predicate; only
  117,259,745 are interior cases. All comparisons pass. This is a one-axis
  classifier check, not a proof of arbitrary two-dimensional filtering.
  Boundary and interior corruption controls each detect their injected error.
- `qualification_v3/CENTER_RUN.json` checks the full center-control corpus on
  both AMD devices and the CPU Vulkan device: all 15 runs match native words,
  with zero validation errors and zero owned allocations after teardown.
- `qualification_v3/ACTUAL_FRAME17_RUN.json` reproduces every 147,456 retained
  sampled half word on all three devices. The prior CPU sampling path differed
  at 25,682 pixels because it retained tiny Float32 coordinate residues.
- `qualification_v3/GENERIC_PRESERVATION_FINAL.json` records byte-identical
  SPIR-V for both existing three-parameter half cached entrypoints and the
  default Float32 cached entrypoint. The explicit Walle opt-in emits the same
  SPIR-V as the tested prototype, recorded separately in
  `SPECIALIZATION_PRESERVATION_FINAL.json`.
- `qualification_v3/SEQUENCE_RUN.json` compares 25 cached-transition frames
  before and after the helper on all three devices. Both AMD sequences remain
  identical. CPU frames 16, 17 and 18 change 11, 4 and 2 pixels respectively;
  the other 22 frames remain identical. Validation and teardown pass throughout.

This corrects the qualified sampler case; it does not complete the general
RGBA16F sampler extraction or establish full-frame Apple equality. In the
frozen comparison, CPU frame 17's remaining native mismatch count changes from
230 to 232 while its isolated sampler words become exact. Other arithmetic and
raster differences remain separate gates. Full common-source native comparison
and performance confirmation are required after integration. No performance
claim is based on these correctness probes.
