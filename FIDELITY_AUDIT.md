# Liquid Glass fidelity audit — 2026-09-22

Historical pre-repair assessment. [FIDELITY_REPAIR.md](FIDELITY_REPAIR.md)
records the subsequent fixes and final scoped acceptance.

**The current transition is not qualified as an end-to-end native-equivalent
Liquid Glass implementation.** Its extracted material calculations have strong
independent evidence, but the integration contains behavioral substitutions.
The earlier component tests and Vulkan self-regressions did not close that gap.
Documenting a substitution as an application boundary did not establish its
equivalence to the requested native behavior.

This assessment follows the user's clarified symptom: a broad blue-ish glare
in regular with automatic appearance. The user accepts the blue response when
the original M1 programs also produce it and does not authorize changing native
coefficients merely to suppress it.

The retained evidence and diagnostic programs are under
`/tmp/walle-work/regular-assessment`. Primary sources remain in
`/tmp/extract-liquidglass`. No web search, Walle history/reflog inspection,
production shader changes, live configuration edits, or service restart were
used for this assessment. The original full extraction goal remains paused.

## Confirmed integration departures

| ID | Current Walle behavior | Extracted reference behavior | Consequence |
|---|---|---|---|
| D1 | Captures the complete incoming B image before rendering the visible reveal, and reuses its pyramid | Captures the already-composited lower scene at the material's position in the rendering sequence | If the intended scene is A outside the reveal and B inside, glass samples different pixels. Caching B is not equivalent to caching that changing scene. |
| D2 | Constructs capture/pyramid geometry once from the complete canvas with a zero capture origin, without deriving backdrop-layer ownership/bounds | Derives the region from the declared backdrop layer bounds/transform, margin, clipping and rounding; source origin/size follow that capture | Moving or partial captures can have different sampling lattices, padding and edge behavior. SDF-element movement alone does not require backdrop bounds to move. A native plan can legitimately remain full-canvas; that equivalence must be established for the chosen graph. |
| D3 | Multiplies tint and mask and immediately source-overs the result into the scene | Stores the masked tint group into an 8-bit attachment, then composites that stored group | A native quantization boundary is omitted. This affects the tinted path; the user's untinted blue-glare case does not execute it. |
| D4 | Pyramid planning uses double `log2` and unfused expanded-width arithmetic, following the retained Python helper | The original function uses `log2f` and specific double FMADD instructions | A fresh original-function counterexample changes mip count and aligned pyramid bounds. C-versus-Python agreement did not validate this shared transcription error. |

Source locations: `vulkan_renderer.c:record_backdrop` and
`walle_vk_output_render`; `transition.c:prepare` and
`material/capture.c:wm_capture_full`; `shaders/walle_glass.slang` and
`walle_glass_local.slang:tintCompositeFragment`. Reference mechanisms:
`work/reports/cpu_backdrop.md`, `liquidglass/host/lg_host.py:capture_plan`, and
the tint-group construction in `liquidglass/host/lg_multitint.py`.

These are separate changes to content, capture geometry, pass quantization, and
pyramid arithmetic.
They must be corrected and tested together against an independently constructed
native scene before the complete optical sequence can be accepted. Changing
only D1 would leave D2, D3 and D4 unresolved.

D3 now has a direct GPU counterexample: 154 of 256 deterministic, valid
premultiplied RGBA8 input pixels differ between original M1 dest-in/store/
source-over and Walle's fused operation, by at most one byte. Supplying Apple's
stored masked group to the same Vulkan composite closes 132 of those
counterexamples exactly, isolating the omitted earlier store. That second
control still has 30 separate one-byte residuals; their cause is not assigned
specifically to hardware blending. The input group, mask and destination are
held equal. This is an isolated composition control, not a claim that every
generated tint-gradient pixel differs. See
[tint-store evidence](/tmp/walle-work/regular-assessment/shader/tint_counter/comparison.json).

For D4, the original QuartzCore function at `0x18a72126c`, under a verified
library UUID, receives a 512x512 capture, minimum radius 1 and maximum radius
`10.000000953674316` (the next binary32 value above 10). Apple returns 5 levels,
alignment 32 and bounds `(-32,-32,576,576)`. The retained double-log helper
selects 6 levels, alignment 64 and bounds `(-64,-64,640,640)`. This is a helper
input counterexample; it is not claimed as the cause of the user's current
blue glare or as a demonstrated current-monitor pose. The original call source,
inputs, library identity and raw bytes are in
[native blur evidence](/tmp/walle-work/regular-assessment/native/native_blur_results.json).

## Additional policies and unresolved correspondence

The four entries above are not a claim that only four differences exist. The
following current choices must also be visible in the acceptance contract.

| ID | Choice or gap | Established scope and required follow-up |
|---|---|---|
| E1 | Tiny-output capture-scale override | Walle raises the recipe scale to retain at least one interior texel: below four pixels for regular, below two for clear. The source uses its ordinary scale/rounding and can produce an empty capture. The override is an additional policy; native empty/no-source behavior must be implemented or explicitly resolved. |
| E2 | Subpixel lens cull | Walle skips all draws below a physical radius of 0.001 pixels. This threshold was not derived from the native evaluator, and equivalence to native subpixel coverage is unverified. |
| I1 | Always-active material | The app forces active=true and the specialized recipe API requires it. Native active/inactive recipes differ. The active recipe is checked; native activity selection is not implemented. This is an explicit scene-input choice, not evidence that wallpaper should automatically use inactive glass. |
| I2 | Fixed appearance and state ownership | Walle snapshots appearance per transition and lacks Apple's material/foreground owners, history and springs. Fresh large-D states match because native luma tracking is disabled there. This does not establish small/adaptive/history-driven equivalence. Portal auto is a desktop setting, not that native feedback loop. |
| I3 | Fixed SDR/environment boundary | Headroom=1, gamma=2.2 and EDR scale=1 match the selected SDR controls. General Apple display/ICC/accessibility/environment producers are not supplied by the wallpaper implementation. The broader extraction of those producers remains tabled. |
| I4 | Geometry and backdrop ownership | Fixed giant path metrics plus a separately scaled SDF element are not automatically equivalent to resizing an NSGlassEffectView or scaling its entire material root. Backdrop-layer bounds must be declared separately. Large D given the giant path is native-confirmed; choosing an arbitrary smaller D would not fix this correspondence. |
| I5 | Primitive and disappearance choreography | The chosen circular primitive is native-supported; it is not proven byte-equivalent to AppKit's continuous rounded-square. The product's final lerp toward B is custom choreography, not extracted native layer-opacity/spring behavior. Custom sweep/lens motion was requested; these distinctions still need explicit scene qualification. |
| N1 | Remaining GPU numerical differences | Photo capture and pyramid pixels match on the tested M1/AMD pair; residual differences first arise in the glass-background draw. Their full cause is still under investigation. |
| N2 | Raw YCC helper precision | One Float32 coefficient differs by one ULP in a fresh original-function control. It rounds to the same stored half coefficient, so it is not an observed visible blue-bias error in this path. |
| N3 | Capture scale/tap precision outside default scales | Current full-capture code clamps scale in double and computes tap setup in double; the original path narrows/clamps and performs those operations in Float32. Default 0.25/0.5/1 controls coincide. Non-default scales, including the tiny-output 1/3 override, require the original arithmetic and independent controls. This is separate from the demonstrated D4 pyramid counterexample. |
| N4 | Tint-specific offscreen and destination-read adaptation | Native mask/group targets have independent cropped origins, allocations, extents and final resampling; native backdrop-aware tint uses an RGBA16F secondary attachment and copy/accumulation sequence. Walle uses full-canvas scratch targets and directly reads the current BGRA8 scene. Narrow pointwise equivalence is plausible for a single opaque-SDR group, but whole-tint equivalence, derivative placement and intermediate precision are not established. This is distinct from backdrop D2 and the proven omitted-store D3. |

This is **four confirmed integration/arithmetic problem families, two additional
edge-case substitutions, and separately identified input/ownership/numerical
qualification gaps**. These categories avoid counting every consequence of a
wrong capture origin as a new defect, or counting every omitted private API as
a rendering bug. They do not prove absence of further defects.

The byte-tint input conversion is a checked source fast path: all 256 channel
values and 7,120 additional active tint matrices agree with the retained source.
It does not require the Cocoa/system-palette bridge. Default global-light
disablement also matches the fresh native effect's `global=0`. Neither is an
extra defect in the selected route. The user's explicit exclusion of the 21
private AppKit variants is preserved.

The actual-pose crop audit contains 144 C-generated poses across the two output
sizes, backing scales 1/2, both materials and motions. Under the explicitly
assumed moving rendered-circle backdrop bounds plus native margin, 112 poses
have a positive glass scissor. Capture surfaces differ from the full-canvas
plan in 4/24 regular sweeps, 0/32 regular lenses, 14/24 clear sweeps and 20/32
clear lenses. Choosing fixed local bounds instead changes the clear-lens result.
This demonstrates why backdrop ownership must be specified; it does not prove
that every current frame should be cropped.

Detailed, deduplicated source maps:
[capture/geometry ledger](/tmp/walle-work/regular-assessment/geometry/CAPTURE_DIVERGENCE_LEDGER.md),
[material/host ledger](/tmp/walle-work/regular-assessment/material/HOST_INVENTORY.md),
[shader/pipeline ledger](/tmp/walle-work/regular-assessment/shader/AUDIT.md).

## What the fresh native checks establish

Reference machine was rechecked as Apple M1 Max / MacBookPro18,2, macOS 26.6.1
(25G76). Native binary/library identities, exact input packets, output hashes,
commands, return codes and timeouts are retained with the runs.

- Current C material packing: 208 cases / 89,856 bytes match the retained
  source-derived host.
- Original DesignLibrary machine-code calls: 38/38 completed and all defined
  recipe fields matched; crashes/missing results were not skipped.
- Fresh native AppKit trees: 24 trees / 4,272 material leaves match. Current C
  independently produces 8,448 bytes matching reference packets constructed
  from those native CA properties under equal sampling inputs. These are not
  fresh captures of the native GPU uniform encoder.
- Ten original Apple glass-background GPU controls reproduce Walle's neutral
  deep-interior values exactly. Full frames differ at 14–1,317 pixels of
  2,073,600, by at most one byte, near the boundary/shadow.
- Three actual-image controls execute original Apple capture, pyramid,
  glass-background, face and highlight programs on the M1. They hold Walle's
  exported packets, capture geometry and pre-glass reveal input fixed. The
  regular/dark output shows the same broad blue treatment. It is not byte-exact:
  the full-frame mean absolute byte difference is 0.0280 and maximum is 6.
  Regular/light has mean 0.0277 / maximum 8. The clear control has mean 0.0895 /
  maximum 63 and requires further numerical localization.

For all three photo cases, separately read-back capture pixels and every blur
pyramid mip match the original Apple programs byte for byte after channel-order
normalization. Transfer-source-only instrumentation leaves Walle's final pixels
unchanged. The residual photo differences first arise in the glass-background
draw. Vertex arithmetic, interpolation, SDF/UV arithmetic and sampling must be
separated before attributing them to unavoidable device behavior. This check
uses the matched substituted capture inputs and does not resolve D1/D2/D4.

An isolated vertex experiment supplied source-rounded projection coefficients
and the native multiply/FMA schedule. Every compared prefix in all three photo
cases remained byte-identical to delivered Vulkan output. It is not a fix for
the observed residual. No per-case coordinate offsets were fitted, and the
remaining GB differences are not classified as unavoidable hardware behavior.

Those photo comparisons show that the original programs reproduce the broad
regular response under the held inputs. They do not establish pixel-equivalent
operators or native scene integration. Because they intentionally retain
Walle's capture geometry and source choice, they do
**not** validate D1 or D2. They are not WindowServer/display screenshots or
complete native AppKit transition lifecycle comparisons.

The primary photo evidence is
[original Apple versus Walle, regular/dark](/tmp/walle-work/regular-assessment/shader/photo_compare_s0_d1.png).
Machine-readable records:
[material](/tmp/walle-work/regular-assessment/material/ASSESSMENT.md),
[geometry](/tmp/walle-work/regular-assessment/geometry/ASSESSMENT.md),
[neutral GPU comparison](/tmp/walle-work/regular-assessment/shader/native_comparison.json),
[photo GPU comparison](/tmp/walle-work/regular-assessment/shader/photo_comparison.json).

## The reported blue glare

The current desktop portal reply was passed through Walle's actual appearance
callback and `prepare_transition` option selection. It selects dark correctly.
This is a current callback/input check, not a reconstruction of the user's
earlier running process.

In the actual regular/dark photo ablation, removing the highlight changes
1,719 rim pixels and zero deep-interior pixels. The separate idle face pass is
also a no-op. The broad response comes through the glass-background stage's
sampling, face color transform and bleed. Removing bleed reduces the blue
excess, but removing its face transform makes the body brighter and more blue;
neither diagnostic is a proposed production change.

The native dark face transform approximately retains chroma at 0.6 while
compressing neutral luminance to `0.24*x + 0.12`. There is no constant blue bias.
Bleed imports a displaced, blurred sample with an interior weight approximately
`0.8 * (1 - body_luminance)^4`. This is per-fragment arithmetic. It is distinct
from the temporal adaptive-luminance observer/state loop.

For the current giant shape dimensions, native recipe resolution clears luma
tracking above 64 points. Fresh native large-view controls agree. Adding an
invented global brightness correction would not repair this path's fidelity.

The large filled coverage also exposes the color treatment across much of the
wallpaper. The current centered lens covers approximately 99% of the canvas
by progress 0.75 while product opacity remains full until 0.80. Its fixed local
material dimension and separately scaled SDF element leave large bleed
displacements even at small visible lens sizes. Those are consequential scene
and motion choices, not evidence that Apple's recipe coefficients are wrong.

## Direct evidence that D1 changes output

An isolated diagnostic first renders the current A/B reveal, then uses those
pixels as the backdrop while retaining every material coefficient, geometry
packet and full-canvas capture rule. At progress 0.5 on the supplied 1920x1080
wallpaper pair, regular/dark changes by mean absolute RGB 1.91 for sweep and
3.64 for lens, with maxima 51 and 62 respectively. Clear is unchanged in this
particular control. These are source-input comparisons, not beauty scores or
complete native-scene comparisons.

[Sweep comparison](/tmp/walle-work/regular-assessment/backdrop_s0_d1_m0/comparison.png),
[lens comparison](/tmp/walle-work/regular-assessment/backdrop_s0_d1_m1/comparison.png),
[metrics](/tmp/walle-work/regular-assessment/backdrop_comparison.json).
All eight diagnostic combinations returned success with zero Vulkan validation
errors. Their rendered source and output buffers are retained.

## Acceptance and correction order

Preserve native-confirmed color and glare behavior. First declare the backdrop
layer bounds and transform independently of the SDF element, then derive its
capture plan for each frame; it may legitimately remain full-canvas. Capture
the actual pre-glass scene at the source-defined point in the pass sequence.
Then restore the missing tint-group store and correct pyramid arithmetic from
the original instructions, using independent native controls. Qualify the tint
group's own crop/origin/MRT path, native empty/subpixel handling, and remaining
GB numerical behavior. Revalidate complete frames using native scene
construction, including tint, both motions, relevant poses/scales, ordinary and
clipped geometry, endpoints and failure handling. The reference must derive its
own capture and pass graph from the declared scene; feeding Walle's substituted
plan into both renderers would repeat the earlier validation gap. Measure the
corrected implementation's actual per-frame and lifecycle costs afterward.

Any geometric redesign is a separate product decision. A finite moving glass
object can use extracted native mechanisms, but changing a filled disc into a
ring requires a real inner SDF boundary and its normals; simply masking the
already-shaded disc would not supply those optics. No redesign or arbitrary
parameter tuning is accepted by this assessment.

## Delivered assessment and retained work

The selected host/shader/renderer mechanisms have been reviewed, the evidence
above has been checked, and the known departures and unresolved gates are
recorded. This is a bounded assessment, not a proof that no undiscovered defect
exists. End-to-end native fidelity qualification remains **unmet**; no complete
pixel-equivalence or universal aesthetic/optimality claim is established.

The runtime/build source manifest's 40 files remain unchanged. This review
updates the research, verification and performance qualifications and records
the shared planner defect in the extraction workspace. It does not promote a
runtime fix or alter the user's live configuration.

An [isolated capture constructor](/tmp/walle-work/regular-assessment/candidate/README.md)
preserves moving origins, projection and extents, and has 2,100 capture-field
controls plus 65,536 sanitizer calls. It is preparation for correction, not an
integrated fix: the inherited pyramid routine still contains D4 and the current
renderer cannot consume the new projection/extent contract unchanged.

[Evidence index and reproduction records](/tmp/walle-work/regular-assessment/README.md)
include input/output hashes, exact native calls, compiler/platform identity,
failed evaluator attempts, and diagnostic/candidate boundaries. The failed
direct attached-AppKit-layer picture attempt is explicitly excluded. No optical
constants were tuned to make these comparisons look better.
