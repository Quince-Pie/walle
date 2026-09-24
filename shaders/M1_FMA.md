# Native binary16 FMA

The selected original half operations use M1 opcode967. The retained source
calls a fused operation. Mesa26.1.8's llvmpipe `nir_op_ffma` lowering calls
`lp_build_fmuladd`, which emits `llvm.fmuladd`; the observed half path rounds
the product separately. On the first VCM row, every one of the 11,782 differing
CPU components equals that unfused result. Both AMD controls use the fused
result. This is a lowering difference, not a change to the material equations.

The portable helper keeps the original half FMA boundaries. Finite binary16
operands have an exact Float32 product: at most22 significant bits, magnitude
from2^-48 through less than2^32. Thus Float32 `fma(a,b,c)` and a separately
rounded Float32 product plus sum give the same Float32 sum. Neither Float32
overflow nor subnormal arithmetic is reachable in this calculation.

Integer encoding rounds that sum to binary16, including subnormals, signed
zero, cancellation and overflow. A Float32 rounding error can affect the
binary16 result only when the Float32 sum is exactly a half midpoint. Only in
that branch, the helper computes the exact product and sorts the two addends
by magnitude. FastTwoSum recovers the signed residual with two separate
Float32 subtractions. Its sign determines which side of the midpoint the
exact result occupies. A zero residual retains ties-to-even encoding.

The basic operations and Float32 FMA carry standard SPIR-V FloatControls2
FPFastMathMode None, preventing contraction and unsafe assumptions where the
residual relies on source order. This uses the renderer's already required
Vulkan1.4 shaderFloatControls2. It adds no Int16, Int64 or Float64 capability.
The portable helper requires no device-specific assumption or sampled startup
test. A separately qualified native instruction path is described below.

NaN and infinity operands are classified before arithmetic. Original opcode967
controls show positive canonical half NaN0x7e00 for a NaN operand, zero times
infinity, and opposite-sign infinity cancellation. Signed infinity and finite
signed-zero cases retain the measured IEEE results. The helper implements
those classes explicitly. This is reachable behavior in analytic SDF paths.

Evidence paths below are relative to the retained record at
`/tmp/walle-work/fidelity-completion/gpu-precision/`:

- `half_fma_reference.c`: independent exact C23 `_BitInt(96)` Q48 oracle;
  `half_fma/reference_result.txt` records116,306,048 finite triples, zero errors.
- `half_fma/native` and `half_fma/archive`:4,194,304 original M1 finite results
  and opcode967 provenance. The same set contains22,703 cases where a naive
  Float32 sum followed by half conversion double-rounds incorrectly.
- `half_fma_special`:1,048,576 original controls including every raw half word
  in each operand role against zero, one, infinity and NaN representatives.
- `half_fma_midpoint/results.json`: all finite and special controls exact on
  discrete AMD, integrated AMD and llvmpipe with the promoted midpoint-only
  residual implementation. The prior eager implementation is retained.
- `fused_candidate/vcm/comparison.json` and `midpoint_confirmation.json`:
  both actual extracted matrices, two independent65,536-pixel input batches,
  RGBA8 and RGBA16F stores, all three devices, zero differing components.
- `fused_candidate/float_controls/results.json`: public float regular, clear,
  gradient, highlight and tint entrypoints compile byte-identically to the
  previous source. The generic gradient API remains unchanged.

The finite theorem covers the arithmetic domain; the listed GPU samples are
independent confirmation, not an exhaustive enumeration of all2^48 triples.
These proofs do not certify whole-frame equality or other half operations.

No stronger portable SPIR-V FMA control was found that changes llvmpipe's
choice of `llvm.fmuladd` to `llvm.fma`. An unrestricted native-half operation
therefore fails the supported-device contract.

## Qualified native instruction path

Specialization constant100 is a4-byte VkBool32, default false. The renderer
enables it only after its existing Float16 admission, for all of:

- RADV with pipeline-cache UUID `3653c5b4-95fa-5b49-ffc1-d00b651e8a06`;
- PCI vendor/device `1002:7550` or `1002:13c0`;
- half denormal preservation, RNE, signed-zero/Inf/NaN preservation and RTZ
  properties all true. RTZ distinguishes ACO from LLVM in this pinned source.

Every other tuple retains the exact portable helper, including a future
driver build on the same GPU. This predicate changes the lowering, never
device admission. It introduces no global shader rounding-mode change.

The installed Mesa26.1.8 primary implementation maps fragment/compute half
FMA to fused GFX9+ instructions. ACO selects half RNE and preserved subnormals
and restores those modes around conversions. The two emitted instruction
streams were inspected. GFX8 is a concrete negative case: RADV can expose
Float16 there while deliberately splitting FMA. Vendor or Float16 support
alone cannot select this path.

The native helper executes the protected half FMA and canonicalizes any
resulting NaN to0x7e00. Independent finite/special controls agree with the
original M1 on both qualified AMDs. Forcing this path on llvmpipe produces
372,671 finite mismatches, confirming why its runtime profile stays false.
The actual renderer's126 stage/capture/pyramid planes match the portable
helper byte-for-byte on the three tested devices; this is a comparison
between arithmetic implementations, not a whole-frame Apple-equivalence
claim.

Primary-source locations, device properties and negative GFX8 case are in
[`radv_fma_contract`](/tmp/walle-work/fidelity-completion/gpu-precision/radv_fma_contract/README.md).
Fresh original-result comparisons, emitted ISA and full-stage receipts are in
[`native_fma_profile`](/tmp/walle-work/fidelity-completion/gpu-precision/native_fma_profile/README.md).
The CPU suite checks138 profile/near-miss controls and both specialization
states on every graphics and compute pipeline. Complete-frame timing and
lifecycle costs are evaluated separately from these correctness receipts.
