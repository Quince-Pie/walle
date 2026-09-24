# Walle

**Final accepted scoped choice: `hw_circle`.** The user selected hardware blending and retained the documented portrait exception ([final user choice](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_DEFAULT.json)). [final delivery acceptance](/tmp/walle-work/fidelity-completion/resume_1400/FINAL_DELIVERY.json) supersedes earlier provisional/pending status. [retained-source verification](/tmp/walle-work/fidelity-completion/resume_1400/final-promotion/retained-hw-circle/ORCHESTRATION.json) confirms unchanged behavior/source and reuses the completed checks. This is a scoped engineering selection, not universal dominance or global optimality.

A C23/Wayland wallpaper engine with source-derived Liquid Glass materials and
Slang shaders compiled offline for Vulkan 1.4.

The provisional `hw_circle` implementation is integrated. Release, normal and
sanitizer tests, analyzer, package, native execution and actual-application
checks pass for this snapshot. Final scoped selection is complete;
see [current acceptance status](REMAINING_WORK.md). The user permits identified
platform numerical differences while requiring the extracted algorithm and
its source-defined precision boundaries.

The glass implementation comes from the macOS 26.6.1/M1 Max extraction: analytic
SDFs, refraction, variable blur, native blur-pyramid kernels, shadows, vibrant
color matrices, highlights and the complete tint-gradient/mask composition.
Walle supplies its own transition movement and final settle into the incoming
wallpaper. This is not a claim that Apple implements these wallpaper transitions
or that Vulkan produces bit-identical pixels to the M1 Metal implementation.

## Build

The flake and lock file define the dependencies and compiler versions.

```sh
nix develop path:. -c make MODE=release -j
nix develop path:. -c make MODE=release test
nix build path:.
```

CPU contract tests run without a display; `make test-sanitize` adds ASan/UBSan.
`MODE=debug SANITIZER=1` builds the complete instrumented application in a separate
profile. See [VERIFICATION.md](VERIFICATION.md) for GPU and compositor coverage.

The tested toolchain is GCC15.2, GNU Make4.4.1, Slang2026.12 and SPIR-V Tools for
Vulkan1.4. Shader compilation uses language2026, SPIR-V1.6, the Vulkan memory
model and explicit precision settings. Each generated module is validated before
C23 `#embed` includes it. There is no runtime shader compiler or OpenGL fallback.

The Vulkan device must support half arithmetic, 16-bit uniform/storage access,
scalar block layout, dynamic rendering/local read, synchronization2,
maintenance5/6, demote-to-helper and Linux external-memory/sync-file interfaces.
The compositor needs layer-shell and linux-dmabuf feedback version4 or newer;
the kernel needs dma-buf reservation-fence import/export ioctls. Actual validation
covers the installed Linux6.18/Mesa26.1.8 environment. See the verification record
for the tested devices and limits. [PERFORMANCE.md](PERFORMANCE.md) keeps each
material, motion, resolution and GPU measurement separate.

## Configure

Use `$XDG_CONFIG_HOME/walle/config.ini`, `~/.config/walle/config.ini`, or `-c`.
A `[default]` section applies to outputs without a named section; a named output
section replaces it rather than merging fields. See [config.ini](config.ini).

```ini
[default]
files =
    fill:~/.config/bg
timeout = 60
randomize = true
gamemode = true
transition = true
transition_duration = 2.4
transition_variant = clear
transition_appearance = auto
transition_motion = sweep
transition_tint = none
```

| Setting | Values and meaning |
| --- | --- |
| `transition_variant` | `clear` preserves more image detail; `regular` gives the stronger extracted frosted material. |
| `transition_motion` | `sweep` moves a broad curved glass front; `lens` expands a glass lens from a varying origin. Both use the same extracted optics. |
| `transition_appearance` | `light`, `dark`, or `auto` from the desktop portal color scheme. No preference/unavailable portal defaults to light. This is desktop appearance policy, not an invented image-luminance threshold. |
| `transition_tint` | `none`, `#RRGGBB`, or `#RRGGBBAA`. A present transparent tint is distinct from absent tint. The renderer uses the extracted matrix/ramp/mask pipeline, not an RGB overlay. |
| `transition_duration` | Positive finite seconds, up to600. The default is2.4. |
| `transition` | `false` presents the incoming wallpaper directly. First boot is also a direct presentation. |

In-flight material/appearance/duration are snapshots. Timer and configuration
requests coalesce while a transition is active, so short cycling intervals do
not repeatedly jump back to an older image. GameMode pauses queued cycling.
Resize and scale changes cancel the old callback/resources and present a freshly
prepared image at the new dimensions. Failed reload parsing retains the old
configuration. Images, directories, PDF decoding and fill/stretch/fit/attention/
entropy crop modes continue through libvips. Transparent images are composited
over black into the explicitly opaque wallpaper surface.

`vulkan_device` belongs in `[walle]`. It accepts `auto`, `discrete`, `integrated`,
a device index, or a case-insensitive device-name substring. Command-line
`--vulkan-device` overrides `WALLE_VK_DEVICE`, which overrides configuration.
Changing the selected device requires restarting the process. Automatic selection
never chooses a CPU Vulkan implementation.

## Run and inspect

```sh
build/bin/walle --check-config -c config.ini
build/bin/walle -c config.ini
```

A deterministic diagnostic uses the same layer-shell renderer, records61 full
BGRA8 frames at1280×720 into an existing empty directory, then exits. Its config
must list exactly two images. It does not need the old mask corpus or calibration
files. Run it under an isolated compositor when testing rather than replacing a
live wallpaper process:

```sh
build/bin/walle -c preview.ini --preview /path/to/empty-directory
```

Set `WALLE_VULKAN_VALIDATION=1` to require the validation layer. Validation errors
fail renderer operations and the process's checked teardown. Synchronization
validation can be enabled with `VK_LAYER_VALIDATE_SYNC=1`.

## Resources and provenance

Wallpapers are decoded once into opaque encoded-sRGB RGBA8. Each optical frame
captures the composed current scene after the masked next-image reveal and
rebuilds its native blur pyramid. Allocation reuse does not cache changing pixels.
Automatic sRGB texture conversion is
intentionally disabled because the extracted shader expects encoded values.
The final image is the unchanged incoming wallpaper, with no idle veil.

Local attachment reads preserve the intermediate 8-bit stores between optical
stages. Direct dma-buf presentation uses at most two images and returns to one idle
image after the compositor releases the previous one. Compositor reservation fences are bridged to Vulkan
sync-file semaphores; a compositor-owned image is never overwritten. Sampled
images and transition resources are released after promotion/abort. The current native-operation path has no shared scalar lookup buffer or
discard-only secondary attachment; the readable tint backdrop remains.
Plain source-over uses hardware blending only when the conservative capability
gate permits it, with the existing shader operation as fallback. Capture uses
the composed image directly when supported, otherwise its copy fallback. Timing
queries are opt-in; allocation diagnostics count owned Vulkan allocations,
not opaque driver/compositor memory.

The currently integrated implementation is **accepted `hw_circle`**: native sqrt/rsqrt and filtering, promoted-Float32 half division, guarded circular work removal enabled, coverage work removal disabled, no shared scalar buffer or discard-only attachment, hardware plain source-over with its shader fallback, and adaptive direct capture with copy fallback. These are the final measured choices within the documented scope and exceptions.

The native SDF retention budget is 192 MiB, not a transient-allocation cap. The
recorded confirmation scope reached 1,692,133,936 owned Vulkan bytes (1.576 GiB);
see [memory scope](PERFORMANCE.md#resource-scope) before treating that budget as
a total-memory limit.

[RESEARCH.md](RESEARCH.md) separates extracted mechanisms from Walle choices and
records the qualification boundaries. The full Apple system-host extraction is
paused, with its remaining ICC, system-color, display and lifecycle work retained
in the research workspace. Those missing Apple services are not runtime fallbacks
in this image-input implementation.
