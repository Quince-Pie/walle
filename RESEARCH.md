# Liquid Glass source boundary

Reference identity: supplied Apple M1 Max, macOS26.6.1(25G76), QuartzCore1195.17,
DesignLibrary/SwiftUICore/AppKit and associated numeric routines. The retained
extraction workspace is `/tmp/extract-liquidglass`; its `liquidglass/EXTRACTION.md`
and `work/continue/verification/SUMMARY.json` inventory native source identities
and the independently checked mechanisms. No Walle logs, history, removed shader
implementation, fitted optical constants or old calibration tables were used.

## Mechanism map

| Walle mechanism | Retained source |
| --- | --- |
| SDF/distance/normal and shape grids | `lg_sdf.slang`, `lg_host.sdf_element_uniforms`, `sdf_bounds_geometry`, `round_rect_fill`, `sdf_src_uv` |
| Glass refraction, variable blur, bleed and shadow | `lg_glass_background.slang`, `glass_background_uniforms`, `glass_shadow_offset` |
| Face/highlight/tint arithmetic | `lg_sdf_effects.slang`, native VCM/key-fill/fill/gradient parameter builders |
| Regular/clear recipes and size maps | `lg_material` SpecV1 constructors, evaluators, recipe/layer/material adapter |
| Tint matrix/ramp | `lg_tintmatrix.py`, `lg_colormap.py`, original Apple powf/log/trig and half-conversion mechanisms |
| Capture and blur pyramid | `capture_plan`, `blur_pyramid_plan`, `lg_blur.slang`, copy and AGX2 downsample kernels |
| Effect clipping | Original glass DOD, Gaussian expansion and AA rounding helpers |
| Quantization/pass order | Qualified `lg_renderer.py` and `lg_multitint.py` stage composition |

Four immutable recipe definitions retain their unevaluated size maps; runtime C
still evaluates size-dependent behavior. Numeric tables are original algorithm
coefficients/resources, not observed-output correction tables. C23 uses explicit
fused operations and disables implicit contraction where source rounding matters.
The tests identify source inputs and retained expected results independently of
production rendering.

## Application choices and translations

Walle's sweep/lens paths, easing, source-image reveal and final removal of the
material are application choreography. They are not extracted Apple transitions.
The local material bounds stay fixed while its element moves/scales, preserving
recipe size inputs. The active material context is explicit; it does not depend
on the wallpaper window gaining keyboard focus.

Appearance is fixed for each transition. Portal automatic appearance supplies
that incoming light/dark choice. The native adaptive luminance observer and
AppKit owner lifecycle are not replaced by an image-average heuristic.

The backdrop is the complete static incoming image. Native capture scales and
pyramid rules are used under this explicit full-image boundary, rather than
claiming QuartzCore's moving cropped-capture lattice. For tiny canvases, Walle
raises capture scale only enough to retain a nonempty interior, then runs the
same extracted constructors. One-mip plans use an independent discarded scratch
blur output; sampled and writable outputs never alias.

Metal framebuffer fetch becomes Vulkan1.4 dynamic-rendering local read. Native
half derivatives are represented by float derivatives followed by separate half
rounding and half abs/add; this follows retained AIR/AGX evidence rather than a
single final narrowing. Derivative quad behavior, texture interpolation and GPU
transcendentals remain hardware-specific. No M1/Vulkan bit-identity claim is made.

## Acceptance scope

Regular and clear, light/dark/resolved-auto, byte RGB(A) tint, uniform-radius
sweep/lens geometry and SDR opaque image inputs are the application contract.
The other private AppKit variants and complete Apple system-host behavior are
not required for this selected product scope; their extraction remains paused,
not declared finished. Vulkan support boundaries and failure handling are
validated separately from mathematical source correspondence.

The supporting verification record distinguishes source-oracle arithmetic,
SPIR-V/ABI checks, actual GPU images, actual layer-shell transport, injected
failures and performance measurements. Finite matches do not prove universal
optimality or universal visual preference. The source-faithful optical path is
mandatory; motion preference is configurable.

## Design qualification

The retained Apple implementations and the checked local Slang repository are
primary evidence for this task. Online recreations cannot satisfy the required
source identity. The original Walle tree could not build because its referenced
shader/calibration directories were absent; it is preserved as a source baseline,
not presented as a measured performance control.

| Choice | Decisive evidence and cost |
| --- | --- |
| Extracted capture/pyramid versus a CPU Gaussian | A Gaussian does not preserve the extracted filter kernels, LOD selection, edge replication or rounding. It fails the optical contract. The GPU path retains those mechanisms and caches preparation for each incoming image. |
| Runtime recipe evaluation versus sampled parameter tables | Retaining the SpecV1 maps supports continuous material size and byte tint inputs. A sampled table would leave permitted combinations unestablished; exact literal algorithm tables remain valid source resources. |
| Local attachment reads versus scene ping-pong | Both can represent the stage dependency if intermediate quantization is preserved. Local reads keep the same-pixel dependency and RGBA8 stores without an explicit whole-image copy. A copied full canvas would add at least one read and write per copied byte, under that implementation's assumptions. This is a data-movement argument, not a measured universal GPU-speed claim. |
| Sweep versus expanding lens | Both preserve the same native optics. Actual-image previews show different movement/distraction tradeoffs; the user selected both. Sweep is the default, lens is an equal config option. No universal beauty ranking is asserted. |
| Strict optimized Slang versus unoptimized compiler control | All 21 entry points validate, resource layouts match, and 216 paired GPU frames are byte-identical on three tested devices. Production uses the 18 needed entries. This comparison qualifies compiler settings, not identity to Apple's driver. |

No throughput or latency superiority over every rendering architecture is
claimed. The mandatory qualification here is source correspondence and a valid,
usable Vulkan adaptation. Actual GPU timing, memory lifecycle and application
checks are recorded separately in VERIFICATION.md. Changing the optical algorithm,
framebuffer format, precision settings or pass order requires fresh qualification.

## Ownership and progress

The Wayland event-loop thread calls renderer/controller entry points serially;
they are not an API for concurrent host submissions. A decode worker owns its
job's dimensions/items, publishes its result, and is joined before the event
thread consumes or replaces it. Output teardown defers freeing an active
worker's owner until completion and cancellation of its event slots.

A controller owns its borrowed frame data until the next build/update/destroy.
The renderer copies that data before returning and retains GPU buffers until
submission completion. Normal presentation polls the prior frame and returns
RETRY while necessary; upload, explicit readback and teardown may wait for owned
submitted work. No lock-free or wait-free end-to-end claim is made. Compositor
release and dma-buf reservation fences jointly govern reuse of presentation
images. Error paths retain or release objects according to that ownership; the
caller decides whether to stop a preview or keep the last visible wallpaper.
