# Extracted operations and platform implementations

**Final accepted scoped choice: `hw_circle`.** The user selected hardware blending and retained the documented portrait exception ([final user choice](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_DEFAULT.json)). [final delivery acceptance](/tmp/walle-work/fidelity-completion/resume_1400/FINAL_DELIVERY.json) supersedes earlier provisional/pending status. [retained-source verification](/tmp/walle-work/fidelity-completion/resume_1400/final-promotion/retained-hw-circle/ORCHESTRATION.json) confirms unchanged behavior/source and reuses the completed checks. This is a scoped engineering selection, not universal dominance or global optimality.

**Current runtime: accepted `hw_circle`; scope and exceptions remain below.** The current source and its completed checks are pinned below. The contract is the extracted Apple algorithm: operations, order, constants, precision boundaries, texture/pass state and host behavior. Identified OS/GPU implementations may differ; a missing source operation or unexplained residual is not waived.

| Current implementation field | Provisional value |
| --- | --- |
| Source integration | [hw_circle record](/tmp/walle-work/fidelity-completion/resume_1400/provisional-integration-2305/INTEGRATION.json) |
| Scalar functions/filtering | Platform sqrt/rsqrt and original-state hardware filtering; no scalar buffer |
| Half divider | Protected promoted-Float32 division, then half rounding |
| Circular work removal | Enabled under the proved finite guard; original fallback retained |
| Coverage work removal | Disabled; complete coverage expression retained |
| Attachments | Discard-only auxiliary omitted; READ_BACKDROP retained |
| Plain source-over | Hardware operation under format/modifier gate; shader fallback retained |
| Scene capture | Adaptive direct sampling, with the original copy fallback |
| Verification | [Release/tests/sanitizers/analyzer/package PASS](/tmp/walle-work/fidelity-completion/resume_1400/final-verification/provisional-circle/RESULTS.json); [492 native diagnostics](/tmp/walle-work/fidelity-completion/resume_1400/final-native-confirmation-circle/CONFIRMATION.json); [app receipts](/tmp/walle-work/fidelity-completion/resume_1400/final-app/run_records) |
| Final performance decision | Accepted hardware default with portrait exception; all180 additional confirmation cells complete |


The shared native candidate is pinned by `native-instructions/COMPOSED_BASE.json`; later attachment and work-removal overlays have separate handoffs. Continuation evidence paths below are relative to `/tmp/walle-work/fidelity-completion/resume_1400/`; original AIR paths beginning `work/` are relative to `/tmp/extract-liquidglass/`. Historical `m1*` helper names and `LG_M1_SF_EMULATION=1` select extracted precision/policy code; those names alone do not establish that a lookup table is used.

## Scalar operations and division

Original AIR `work/mac/qc_ll/glass_background_sdf_lph.ll` contains `air.fast_sqrt.f32`, `air.fast_rsqrt.f32`, `air.sqrt.f16` and `air.rsqrt.f16`; `work/uber/supercircle_sdf*.ll` retains the surrounding SDF mechanism. The local Slang typed operations map to SPIR-V GLSL.std.450 `Sqrt` and `InverseSqrt`. The provisional native runtime uses those operations with protected actual half inputs/results where the AIR is half, preserving the existing special-value policy. Float32 zero/subnormal inputs retain the extracted signed-zero sqrt and signed-infinity rsqrt rules; negative normal inputs and NaNs retain canonical-NaN handling. Half subnormal inputs remain live half values. There is no implicit promotion of the entire material to Float32.

M1 fast Float32 sqrt lowering uses opcode1994 plus a product; its positive-normal identity with reciprocal-square-root reconstruction is a device implementation fact. It does not authorize replacing every AIR sqrt by ordinary rsqrt times its input at zero or subnormals. The rejected `rsqrt(norm)*norm+1` prototype is retained under `arithmetic/source_closure_rejected_sqrt_product/`. The shared source preserves AIR `sqrt(norm)+1`, together with the explicit quartic, affine, norm, interpolation and transform schedule. See the [final integrated source mapping](M1_SOURCE_CLOSURE.md).

| Divider contender | Mechanism and established scope |
| --- | --- |
| Promoted native Float32 | Reconstruct the actual half operand words; handle original NaN/Inf/zero classes; widen finite operands; execute protected Float32 `OpFDiv`; round once to half. Finite half inputs and their Float32 quotient cannot underflow or overflow Float32. This avoids the range failure of the rejected direct-half backend lowering. It preserves the half interface, not universal M1 result identity. |
| Exact integer | Exact significand quotient/remainder with uint32 arithmetic and half RNE. Original M1 opcode1863→flagged1798 equals this result for all2^32 raw half operand pairs, with execution counters and corruption controls. |
| Checked-estimate exact | A Float32 estimate is admitted only after integer inequalities prove its floor; invalid/out-of-range estimates use integer division. Final remainder/rounding is identical to the exact control. Correctness does not depend on approximate division accuracy. |

The promoted-native controls have zero special/range/denormal-policy failures. On each AMD device,48 of4,194,304 broad pairs and464 of16,384 repeated difficult pairs differ from M1 by one half step; CPU matches these controls. These are measured scopes, not a universal error bound. Direct half `OpFDiv` is rejected:37,968 expected finite-normal broad results became zero/nonfinite on each AMD. See `native-instructions/DIVISION_RESULTS.json`, `arithmetic/candidate/M1_DIVISION.md`, and `arithmetic/ESTIMATE_PROOF.md`. Only source-established GB/SDF division sites use the selected helper; VCM/highlight compound schedules remain independent.

## Protected boundaries and FMA

The common source keeps the half displacement-normal result before Float32 UV FMA, the mixed Float32 blur-radius product's half result, the half radius/remap/log2 input boundary, and actual half arguments entering the portable FMA. Generic constructor casts alone previously allowed Mesa to remove these round trips; protected conversions prevent that. Explicit FMA/product order also remains in the SDF polynomial, norm, axis/final mixes, gradient transform, bevel and blur weighting. Public Float32 paths were separately checked.

The existing half-FMA helper selects the qualified native half instruction only for its pinned AMD profile; other backends use the proved portable half implementation. This is distinct from Float32 FMA. The pinned llvmpipe backend lowers requested Float32 FMA through LLVM `fmuladd` and executes a split result on the discriminating controls. Replacing only that operation in a **diagnostic-only** Float64 implementation closes all16 held-coordinate CPU source pixels; production gains no Float64 requirement. Evidence: `arithmetic/BOUNDARIES_HANDOFF.json`, `arithmetic/SOURCE_CLOSURE_HANDOFF.json`, and `arithmetic/float_fma_diagnostic/CAUSAL_RESULT.md`. That causal classification does not classify unrelated frame differences.

## Complete material sampler boundary

| Caller | Preserved source/state in the provisional native runtime |
| --- | --- |
| GB shadow, inner refraction, outer refraction, unrefracted backdrop and edge bleed | `sampleGlassBackdrop` uses the current backdrop pyramid with normalized linear min/mag and linear mip filtering, clamp-to-edge. The original computed half LOD is protected, widened, and passed directly to `SampleLevel`; output narrows to half. Float32 source UV/displacement order remains. There is no added six-bit LOD floor or manual eight-bit spatial-phase reconstruction in this Walle path. |
| Capture image copy | Read the composed present image or explicit scene-copy view at level0. Both resources have one mip; the captured source state is linear/no-mip/clamp-to-edge. |
| Capture downsample4/downsample8 | Preserve the original four/sixteen level0 taps respectively, including offsets, tap/mirroring order, weights, half FMA chains and EDR scaling. |
| Capture downsample6 | Original AIR uses implicit LOD. Walle's selected composed-present/scene-copy input has exactly one mip, so the existing level0 specialization addresses its only level. Generic `kNativeUnorm8=false` retains implicit sampling; this is not a claim about arbitrary multi-level downsample6 inputs. |
| AGX2 pyramid downsample | Preserve the original unsigned16-to-Float32 level, coordinates, box/diamond tap order, half arithmetic, workgroup layout and stores. Walle's integer mip index is at most31, exactly representable in half; linear-mip and nearest-mip sampling select the same level at that integer LOD. The retained non-AGX2 entry has the same integer-level correspondence but is not the selected M1 path. |
| Cached regular/clear GB SDF | Typed half linear sampling of the one-level RGBA16F cache with the extracted coordinate clamp, nearest mip selection and protected half result. The provisional native runtime removes the M1 zero-weight `Load` substitution. Cache creation/reuse/recovery behavior is separate and unchanged by this sampler choice. |
| Cached mask/highlight | Their distinct nearest sampler state remains; the linear GB SDF sampler is not applied to these callers. |
| Fixed tint gradient | Sample the original256x1 RGBA16F texture. Preserve half negation, protected Float32 extension, separate subtract/add/multiply coordinate schedule, y=0.5, normalized linear/clamp state and the exact texture/x/y/w identity gate. Coverage offset, color, SDF alpha and coverage products remain live. Under that gate, NaN distance selects the measured high edge; other special coordinates keep the extracted clamp. |
| Custom/generic ramp and texture paths | Preserve the generic source helper and supplied parameters/state. Fixed-profile identities and special rules are not imposed on unrelated textures. Proven byte/point-fetch conversion boundaries in copy, mask and composition callers remain independent of filtering. |

The [complete sampler-caller mapping](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/SAMPLER_CALLERS.md) links GB, capture copy, downsample4/6/8 and AGX2 calls to their original AIR and Walle resource construction. `final-audit/native-all/README.md` supplies the complementary cached-SDF/ramp mapping, including `glass_background_lph.ll:10–14`, `work/uber/sdf_gradient.ll` and the original sampler-state/ramp controls. The native-all candidate samples the original ramp texture instead of the131,072-byte response table; together with native SFUs this removes the shared scalar buffer and binding9. It does not claim M1 filter phase/accumulation identity. Retained table representations remain comparison controls until selection.

## Guarded work removal: circle enabled, coverage disabled

Circular removal is guarded by both selector words equaling`0x3f800000`, both unsigned computed-c words <=`0x5d800000` (nonnegative, <=2^60), and their maximum >=`0x30800000` (>=2^-30). All other inputs keep the complete expression. The Bernstein/Horner bound, including1024*2^-126 for FTZ, leaves denominator >=0.4144548456 and proves continuous finite. Therefore its two selector-1 axis contributions cancel exactly. The final w-dependent paired mix, t, circular affine/sqrt, extent, gradient and half boundaries remain. This is not a replacement with an ordinary geometric circle.

The following coverage mechanism is a qualified comparison control, **not enabled in this provisional runtime**. Coverage removal computes the original fine derivatives and half width **before** branching. It requires positive finite width, non-NaN distance and2*abs(distance)>=width, then preserves the exact native endpoint multiplication including signed zero, NaNs and0*Inf. With the promoted-native divider, it is gated by existing Spec100's exact pinned AMD device/cache-UUID profile; CPU and unknown profiles keep the full division expression. Each of the three tested backends passes1,070,625,790 eligible signed pairs with independently verified per-width counts and a detected one-error corruption control. No general unknown-backend accuracy bound is asserted. Expanding Spec100 requires coverage qualification as well as half-FMA qualification.

`arithmetic/SPECIALIZATION_REVIEW.md` and `arithmetic/work_removal/HANDOFF.json` retain the proofs, guard/fallback/nonfinite checks,4,773,878 real-derivative inputs per device,108 validated shader entries and252 exact stage comparisons. These correctness receipts do not select either optimization on performance.

Attachment selection is independent: the shared hardware-blend contender and fused shader control have separate source/held-input evidence in `final-audit/hardware-blend/` and `arithmetic/attachment_reuse/`. Describing active half-FMA helpers does not imply that every selected attachment blend must run in shader arithmetic.

## Retained reference documents

[M1_MATH.md](M1_MATH.md), [M1_DIVISION.md](M1_DIVISION.md), [M1_RAMP.md](M1_RAMP.md) and [CACHED_SDF_SAMPLING.md](CACHED_SDF_SAMPLING.md) preserve original extraction and earlier exact-control proofs. Their headers identify historical table, integer-divider or center-load descriptions that are not the current runtime selection. [M1_FMA.md](M1_FMA.md), [M1_BOUNDARIES.md](M1_BOUNDARIES.md) and source-schedule records remain active precision obligations; their repairs are not obsolete because scalar/filter emulation was removed.

The [latest user authority](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_SCOPE_AND_BUDGET.json) prioritizes RX 9070 XT and allows work through 00:45 UTC. Final scoped qualification and selection are recorded by the acceptance link above; earlier records that predate that authority remain historical snapshots.
