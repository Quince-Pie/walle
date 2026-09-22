# Walle C23 material implementation

This library implements the declared Walle shader boundary using the
retained macOS 26.6.1 (25G76) extraction. It does not resume or complete the paused
full Apple extraction. No Walle logs, deleted implementation, web sources, new
native process, or GPU captures were used for this implementation.

## Inputs and lifetime

Include `material.h`. Link `material.c`, `material_math.c`, `applelog.c`,
`capture.c`, `geometry.c`, and `scissor.c` with `-lm`. Required compiler semantics are C23,
IEEE binary32/binary64, little endian, round to nearest/even, no reassociation,
and `-ffp-contract=off`; explicit `fma`/`fmaf` calls implement source fused sites.
The tested compiler is GCC 15.2 with `-O2 -Wall -Wextra -Werror`.

The supported material inputs are public regular/clear, incoming light/dark,
active context, source tuning defaults, fixed appearance, positive logical
layout bounds/backing scale, and optional straight sRGB RGBA bytes. `active=false`
is rejected. Portal `auto` must be resolved to incoming light/dark outside this
library. The optional adaptive luma owner is disabled, not approximated.

`wm_recipe_create/update` evaluates `D=min(width_points,height_points)` and all
source size maps. Four typed SpecV1 definitions are generated from
`source_spec_templates.json`; these contain unevaluated maps, not sampled output
grids. `generate_specs.py` emits `material_specs.h`. `check_specs.py` independently
reconstructs the definitions through the retained source dispatch.

`wm_recipe_pack` produces the glass216, face/tint/highlight matrices48,
key/fill40, gradient24, fill16 and tint ramp2048 bytes. The domain specifies
source extent/scale, root transform, headroom/gamma and optional explicit light.
Use headroom1/gamma2.2 for the declared SDR boundary. The source image contains
encoded sRGB values in UNORM storage; an automatic hardware sRGB transfer would
change this boundary. The packet supports a shared current/stored SDF transform;
it does not implement a separately retained SDF transform lifecycle.

Tint presence is distinct from present alpha0. Tint alpha feeds the source color
matrix; it is not a blur multiplier. The full HSL/endpoints/matrix arithmetic is
ported, including the original Apple Float powf/trig implementations. There is
no platform `powf`, fitted RGB grid, or captured color-output table. The three-stop
tint ramp uses the source cubic timing solver and half packing.

Effect opacity is already included before half conversion in the key/fill
colors. These public presets emit face/tint-group opacity1 and highlight0/1;
skip a zero-opacity highlight. Product reveal opacity belongs to a separate
final composition stage, not to a postmultiply of destination-aware VCM output.

## Stable capture, moving geometry

Build `wm_capture_full` with physical canvas size, `plan.backdrop_scale` (.25
regular/.5 clear), and `plan.maximum_refraction>0` for edge replication. This is
an explicit full-image input boundary with zero capture margin/origin. It does
not reproduce QuartzCore's moving crop lattice. The API checks computed signed32 surface bounds and rounded allocations before
narrowing, and rejects an empty scaled interior. Live blur plans must also fit
the original signed16 coordinate and unsigned16 destination packet fields.
The Vulkan backend separately checks actual image, framebuffer and viewport
limits. Failed constructors leave a zero output; use distinct input/output objects.

Pass the resulting capture and `plan.blur_min/max` to `wm_pyramid_build`, with
the fixed root transform scale. Both source allocations round to64 texels.
Copy dispatches cover32×32 with20×20 threads; later downsample dispatches cover
16×32 with16×16 threads. Use the returned extents, clamp, origin, dimensions,
`no_base`, sample scale and mip indices rather than deriving replacement values.
A zero mip count means there is no filtered pyramid. Plans must originate from
these constructors, not unchecked external structs.

For static incoming wallpaper B, capture/pyramid and material packets can be
prepared once. Translation changes mesh positions/source UV. A lens changes
the element-to-SDF-root matrix; keep its local layout bounds and the outer
backing-scale transform fixed. Compute the grid scale with `wm_sdf_scale`, whose
Float rounding can differ from the requested Double scale. Zero scale bypasses
the SDF path.

`wm_sdf_arguments` produces48 bytes. `wm_sdf_grid_build` returns the source
partition groups with already triangle-expanded indices. `wm_sdf_vertices`
maps the grid through an explicit application affine and evaluates source UV.
The affine is product geometry, not an emulated AppKit owner transform history.
The inline image21/20 partitions are normalized to shader mode4/0; glass also
uses outside/shadow mode−4. Positive infinity is valid for tint maximum distance.
Only uniform-radius geometry is exported; per-corner radii are not implemented.

For a circle, use equal width/height, radius=width/2, continuous=false. The SDF
uses mode4 with circular selectors(1,1). Its face is native image10 with one
quad, not image11 with zero circularity. Local SDF Y points upward; reflect Y
in both application screen geometry and the glass displacement domain.

The packet's plan exposes output, highlight, tint-mask and tint-gradient bounds,
shadow expansion/offset, and maximum refraction. Source DOD and AA scissor helpers are ported in `scissor.c`: `glass_dod`
(18a78c65c), `gaussian_expansion_factor` (18a78adc4), and `aa_round` (18a7a621c).
The controller transforms those root-coordinate bounds and intersects them with
the glass mesh scissor. Face, tint and highlight retain their own geometry.
The full-image capture boundary does not emulate a moving cropped-capture graph.

## Source mapping

| C implementation | Retained source |
| --- | --- |
| Spec definitions and evaluator/finishing | `lg_material/specs.py`, `evalspec.py`, `evalfull.py`, `recipes.py`, `post.py`, `resolve.py`; regular240965348/spec240963eb8, clear240951e38, evaluator240946344 |
| Layer/filter fields and bounds | `lg_material/layers.py`240922488; `material_adapter.render_inputs` |
| Byte tint and matrices | `lg_material/lg_tintmatrix.py`, `lg_applepowf.py`; source literals and original scalar instruction order |
| Scalar log/trig | original `applelog.c` retained under `work/agents/gpu_tuning/applelog`; `lg_trig.py` and `lg_trig_tables.json` |
| Glass216 normalization | `lg_host.glass_background_uniforms`18a78ae94 and `pack_glass_background_lph` |
| Effect packets/ramp | `lg_host.key_fill_highlight_params`, `gradient_params`, `fill_params`, `vibrant_color_matrix_uniforms`; `lg_colormap.py` |
| Capture/pyramid | `lg_host.capture_plan`, `capture_quads`, `blur_pyramid_plan` |
| SDF and face geometry | `lg_host.sdf_element_uniforms`18a85e900, `sdf_bounds_geometry`, `sdf_src_uv`, `round_rect_fill`18a8a27cc |

## Qualification

Local controls compare C output against the separately retained Python source
implementation; they do not compare the new Vulkan backend to native pixels.

| Control | Cases |
| --- | ---: |
| Unevaluated source constructor reconstruction |24|
| Complete material packets, both styles/appearances and size edges |184|
| Transformed domains, headroom and explicit lighting |288|
| Capture plans |236|
| Blur pyramid plans |1416|
| SDF/mesh/face/UV and element-scale controls |1000|
| Source powf, trig, half controls |5000 each|
| Tint matrices including grayscale/inactive branches |13072|
| Every byte-channel decode and source reencode |512|
| Literal source arithmetic table words |553|

The checked-bounds follow-up adds 69 capture/232 pyramid matches and explicit
source-packet rejection/failure-state controls. The scissor follow-up adds 19,078
source-oracle controls. All reported controls have zero mismatches; their JSON results and checkers are
retained beside the source. These finite corpora support the source port, and are
not an exhaustive proof of every RGB matrix or cross-GPU floating behavior.
`source_manifest.json` identifies the extraction inputs; `../provenance/runtime-sources.json`
identifies the delivered implementation; `../VERIFICATION.md` records its checks.

The application still owns image decoding/profile policy, portal appearance,
motion/reveal/terminal-frame policy, pass ordering/composition and actual GPU
validation. Arbitrary ICC, dynamic system colors, hardware headroom/lighting,
adaptive owner state, and native capture/topology lifecycle are not claimed.
