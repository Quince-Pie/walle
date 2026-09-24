# Walle C23 material implementation

**Final accepted scoped choice: `hw_circle`.** The user selected hardware blending and retained the documented portrait exception ([final user choice](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_DEFAULT.json)). [final delivery acceptance](/tmp/walle-work/fidelity-completion/resume_1400/FINAL_DELIVERY.json) supersedes earlier provisional/pending status. [retained-source verification](/tmp/walle-work/fidelity-completion/resume_1400/final-promotion/retained-hw-circle/ORCHESTRATION.json) confirms unchanged behavior/source and reuses the completed checks. This is a scoped engineering selection, not universal dominance or global optimality.

This library implements the declared regular/clear wallpaper scene from the
retained macOS 26.6.1(25G76) extraction. Its host mechanisms have independent
original-function and original-scene controls. The provisional `hw_circle` integration has passed its recorded build, CPU,
native-execution and application checks; final scoped selection is complete. Native image comparisons are diagnostic under the extracted-algorithm
contract, not a whole-image equality gate. The broader Apple
environment/owner extraction remains paused.

## Inputs and ownership

Recipes accept regular/clear, resolved light/dark, **active or inactive**,
positive logical bounds/backing scale, and optional straight encoded-sRGB
RGBA byte tint. `D=min(width_points,height_points)` drives the source size
maps. Tint presence differs from present alpha0; tint alpha is a source
color-matrix input, not a blur multiplier. Inactive recipes preserve their
own blur/refraction/tint branches. `has_highlight` describes topology and is
distinct from `highlight_opacity`: an inactive copied owner can count toward
cache eligibility even when no highlight draw is emitted.

Activity is supplied by the caller. The application currently declares
`active=true`; this library does not acquire desktop/window activity. Portal
`auto` resolves the incoming light/dark value outside the library, and the
application snapshots that choice for a transition.

The selected fixed-appearance route corresponds to original
`set_adaptiveAppearance:1`: configuration option0x4000 is omitted, environment
bit16 is clear, and the adaptive small-glass/luminance observer is disabled
at **every D**. Separately, the default adaptive route clears tracksLuminance
above64. Large-D agreement alone would not justify omitting small-D feedback;
the explicit fixed-appearance counterpart does. General adaptive history,
foreground ownership and Apple animation springs are not implemented here.

The image boundary is opaque encoded-sRGB RGBA8 in UNORM storage, headroom1,
gamma2.2, draw EDR scale1 and default global-light=false. Hardware sRGB decoding
would change the shader input. Display/ICC, HDR/headroom acquisition, dynamic
system colors, accessibility and other environment producers remain outside
this selected boundary and are not declared complete.

## APIs and arithmetic

[material.h](material.h) exposes recipe/capture/geometry;
[scissor.h](scissor.h), [sdf_cache.h](sdf_cache.h) and [clip.h](clip.h) expose
the additional host helpers. Link `material.c`, `material_math.c`, `applelog.c`,
`capture.c`, `geometry.c`, `scissor.c`, `sdf_cache.c` and `clip.c` with `-lm`.
Required semantics are C23, IEEE binary32/binary64, little endian,
round-to-nearest/even, no reassociation and `-ffp-contract=off`. Explicit
`fma`/`fmaf` calls retain original fused sites. Controls use GCC15 and
UUID-gated original Apple functions.

`wm_recipe_create/update` owns typed source parameters. `wm_recipe_pack`
emits glass216, face/tint/highlight matrices48, key/fill40, gradient24, fill16
and tint-ramp2048 bytes. `wm_recipe_pack_glass` refreshes only the domain-dependent
216 bytes and selector. `wm_recipe_pack_glass_texture` emits the cached
texture-SDF glass variant. Nonnull narrow-packer outputs are zeroed on failure.

Source coefficient maps, byte-tint conversion, Apple powf/trig, per-lane matrix
FMA order and half packing are retained. Pyramid planning uses the original
Apple log2f instructions/table, Float narrowing and Double fused expansion;
capture clamp/tap setup follows the original Float schedule.

## Capture and geometry

One fixed-local-size circular material root owns element and backdrop. Sweep
translates it; lens scales the **whole root**, including optical distances.
Clipping does not redefine recipe dimension D. Each optical frame derives
capture from opaque A plus the masked B reveal, before glass and later effects.

`wm_backdrop_bounds` narrows margin to Float, expands local G with the original
width/height arithmetic, then transforms it. Pass expanded world G to
`wm_capture_clipped` with margin0. Group ROI has separate expanded-backdrop
and padded-element contributors; the first uses G, not the unexpanded element.
The renderer honors returned origin, projection, extent, allocation, clamp
and quads. Contents are recomputed per frame. `wm_capture_full` remains an
explicit full-canvas helper; it is not the moving controller's capture policy.

An empty rounded capture does not automatically remove glass.
`wm_capture_filter_fallback` derives a cleared offscreen source from native
source DOD/filter ROI, using scale1, no capture quads and no pyramid. Its
derived allocation can exceed64². This is distinct from SDF cache allocation
failure. `wm_glass_filter_dod` retains the native transform/raster order and
bias255/512. Empty integer DOD suppresses only glass background. Other effects
retain their own bounds; there is no invented subpixel-radius cutoff.
Exactly zero root scale is a collapsed shape.

`wm_clip_rect_quad` preserves the native CPU clip for the one-part,
no-source-surface analytic emitter. Identity/translation narrows positions
before clipping; scale/reflection retains Double endpoints until emission.
Each branch preserves its Float FMA schedule. Nine-part and surface-backed
emitters retain their separate source paths; this is not a universal GPU-space
clipping rule. The selected idle face draw is omitted under its
[zero-alpha covering argument](IDLE_FACE.md), preserving the native packet.

## Cache lifecycle and resource recovery

`sdf_cache.[ch]` models actual scene time, clipped prepared element bounds,
root transform, the four-duration ring, changed/eligible predicates, shared
padding, copied-effect DOD, allocation/containment and translated reuse origin.
The original anchor rounds ties away. Cache-item bounds, creation bounds and
mutable surface origins are distinct. Two source copies are required, with
an optional third tint-fill copy; gradient/reveal owners each have one.

The controller separates pending and committed history. Only successful
rendering commits a frame; RETRY rebuilds from committed history. Successful
context updates reset ownership; failed updates preserve the old object.
The native 192 MiB budget controls **retention**: a larger successfully
allocated SDF remains a transient texture-SDF frame.

The user approved a separate resource exception: if cached SDF storage exceeds
device limits or cannot be allocated, request one analytic replan at the same
scene/material/pose/time. `walle_transition_recover_analytic` rejects a second
recovery of that build and preserves one committed eligibility update.
Failed retained storage is invalidated immediately. Invalid metadata,
unrelated resource failures and device loss remain fatal. Apple's original
child/constant allocation fallback is preserved as reference evidence outside
runtime; it is not the selected production policy.

Reveal/departure uses separately stored B and mask surfaces, native byte/half
layer opacity and multiply-alpha/source-over. Tint has its own cropped
mask/group, RGBA16F backdrop attachment, dest-in RGBA8 store and composition.
Product timing, circle geometry and exact A/B endpoints remain wallpaper
choreography, not Apple springs or a continuous rounded-square.

## Qualification and references

Shipped controls are [run_material.py](../tests/run_material.py),
[run_cache_controller.py](../tests/run_cache_controller.py),
[run_quad_clip.py](../tests/run_quad_clip.py) and
[run_sdf_replan.py](../tests/run_sdf_replan.py). Their fixture documents state
inputs and scope. Renderer tests require generated shader/protocol inputs.
Qualified normal and ASan/UBSan runs pass; CPU success implies no pixel tolerance.

| Independent host control | Scope |
|---|---|
| Original log2f / blur |1,097,551 log2f inputs;36,810 complete blur outputs |
| YCC / concat |20,032 original YCC calls;20,000 original matrix products |
| Capture / backdrop |20,346 arithmetic-block controls;20,480 backdrop/transform calls |
| Activity recipes |160 original cases;416 full C/source packet comparisons |
| Cache lifecycle |6,000 original state calls;100 exact-input native frames /250 state updates |
| CPU quad clipping |3,109 original enabled-clipping cases, including21 scene calls |
| Analytic recovery |20 controller cases plus CPU-injected renderer failure controls |

Task evidence is retained outside the installed library at
`/tmp/walle-work/fidelity-fix`: `material-evidence/NUMERIC_REPAIR.md` and
`GLASS_PACK_ADDENDUM.md`, `cache-evidence/CONTROLLER_V9_REVIEW.md`,
`clip-evidence/README.md`, `recovery-evidence/README.md` and
`fallback-evidence/README.md`. Their manifests retain identities, commands,
hashes and failed evaluators. Primary extraction maps remain under
`/tmp/extract-liquidglass/work/reports/`: `material_model.md`,
`cpu_backdrop*.md`, `cpu_sdf_effects*.md` and `tint_dod.md`.

Current integration and remaining decisions are recorded in [REMAINING_WORK.md](../REMAINING_WORK.md), [VERIFICATION.md](../VERIFICATION.md), and [PLATFORM_OPERATIONS.md](../shaders/PLATFORM_OPERATIONS.md). The provisional runtime uses native functions/filtering, protected promoted-Float32 half division and guarded circular removal; coverage removal is off. It has no shared scalar buffer or discard-only attachment, preserves READ_BACKDROP and uses capability-gated hardware plain blending with the shader fallback. Direct capture retains copy fallback.

[M1_MATH.md](../shaders/M1_MATH.md) preserves the earlier exact scalar reconstruction, not a claim that the current runtime allocates its table. The 192 MiB retention budget does not cap transient/native-field allocation; the measured peak scope is documented in [PERFORMANCE.md](../PERFORMANCE.md). Private variants, adaptive owners and display/ICC producers remain in the paused broader extraction.
