# Liquid Glass verification

The accepted product scope is regular/clear, light/dark/portal-resolved automatic
appearance, optional byte RGB(A) tint, and both sweep/lens motions. The optics and
material evaluation follow the retained extraction; movement, image reveal and
settling are Walle behavior. [RESEARCH.md](RESEARCH.md) records that boundary and
the design alternatives. This is not an M1/Vulkan bit-identity or universal
beauty/performance claim.

This record applies to the source hashes in
[provenance/runtime-sources.json](provenance/runtime-sources.json). Source
correspondence, Vulkan correctness, application behavior and measured performance
are separate findings. The original full Apple-host extraction remains paused;
its unimplemented ICC, system-color, display and ownership work is retained at
`/tmp/extract-liquidglass/work/continue/TABLED.md`.

## Build and CPU checks

The locked environment provides GCC15.2, Make4.4.1, Slang2026.12, SPIR-V Tools/
Vulkan headers1.4.341, libvips8.18.3 and Wayland1.25. Actual runtime checks used
Linux6.18.51 and Mesa26.1.8 on RX9070XT, the Ryzen9950X3D integrated GPU, and
lavapipe. Tests run with explicit C23 and source-sensitive contraction disabled.

```sh
nix develop -c make MODE=release -j6
nix develop -c make MODE=release test
nix develop -c make test-sanitize
nix develop -c make MODE=release ANALYZE=1 -j6
nix build path:.
```

`nix build path:.` includes the current working tree, including newly added
files. The package excludes generated build/protocol artifacts, compiles fresh,
and runs the display-free test suite before installation. The clean package's
check phase and installed executable were exercised. GCC's analyzer and the
full ASan/UBSan application build produced no compiler diagnostics.

| Check | Actual coverage/result |
| --- | --- |
| Source host arithmetic | 184 material packets, 288 transformed domains, 236 capture/1416 pyramid plans, 1000 geometry cases, 5000 each powf/trig/half, 13072 tint matrices, 512 byte transfer cases, 24 constructors and 553 literal table words: zero mismatches. |
| Source clipping | 19078 DOD/Gaussian/AA/affine/recipe controls: zero mismatches. |
| Checked bounds follow-up | 69 capture/232 pyramid matches, 5 capture/251 pyramid representation rejections and 8 invalid/overflow failure-state cases. Packet bounds are derived from native field types, not an arbitrary image-size cap. |
| Shipped material fixtures | 32 independently generated cases, 62784 packet bytes and 776 scalar comparisons pass release O3/LTO and ASan/UBSan. No production C output generated their expected values. |
| Shipped transition tests | 144 trajectories/28800 frames, 26 invalid configurations, 12 wide/update cases and 1000 separate Fortify bounds controls. Sanitizer and NDEBUG variants also pass. |
| Shipped app checks | 74 assertions and 11 display-free configuration cases. Three deliberate regression mutants were rejected. |
| Shipped renderer bounds | 40 synthetic boundary/entrypoint checks; invalid graphics extents cause no allocation/destruction/fence wait. Storage-only images retain their separate limits. |

The material fixtures reproduce byte for byte from the retained source using
`tools/generate_material_fixtures.py`. Regeneration is optional research work;
normal builds/tests have no dependency on that workspace or Apple frameworks.
Formatting of the new host modules preserved their C token sequences; the
before/after mapping is retained in `provenance/formatting.json`.

## GPU and real application checks

All21 qualified shader entry points compile and validate. Production embeds18;
all18 final modules are byte-identical to the accepted strict compiler outputs.
Final release O3/LTO source was tested for1188 frames with validation required
and checked through teardown:216 optical frames match the accepted reference
bytes on all three devices;108 tiny-output/endpoint configurations add972 frames.
Near-zero and near-one endpoints are exact. A further24 near-one cases at
1920×1080,2560×2880 and5120×2880 on both AMDs checked193536000 pixels: every
pixel was exact B while the optical path remained active, with zero validation
errors. This tests the Vulkan adaptation,
not equality to Apple's Metal driver. The earlier cross-device corpus found the
two AMD outputs equal; lavapipe had intermediate differences up to14 byte levels
(mean absolute byte difference0.0721).

The actual layer-shell program was also run under a private headless Labwc:

- Both packaged clear/sweep and regular/dark/tinted/lens runs produced61 full
  frames and exited successfully. Every frame matches the earlier accepted
  application run. An instrumented run matches those61 frames as well.
- First and last frames match an independent libvips memory-render oracle
  byte for byte. Transparent alpha0/128/255 fixtures additionally match analytic
  black compositing and finish with opaque alpha.
- Reload during visible motion changes all four material controls on the next
  transition. Repeated timer expirations coalesce rather than restart motion.
  Resize and scale changes produce fresh images. SIGTERM during motion exits
  cleanly. A two-output run independently progresses both outputs.
- All three application upload-failure stages terminate automatically with the
  expected nonzero status, no retained observed source fd, and no validation
  error. The first test exposed a shutdown wait bug; the fixed path reaps dead
  outputs before blocking again. The failed control remains retained.
- Lower-level queue-submit and sync-file import/export failures, a legitimate
  signaled fd=-1, resource promotion/abort/resize/destruction and120-frame dma-buf
  transport controls pass. The transport control retained a stable17→17 fd count.
- Actual graphics-limit rejection leaves allocation counts unchanged on all
  three devices. On the discrete GPU, a16385×1 storage-only image succeeds even
  though graphics framebuffer dimensions stop at16384. Per-axis viewport and
  framebuffer checks avoid rejecting valid asymmetric rectangles.

Every private process is bounded and its actual child exit status is checked;
Labwc's own exit status alone is insufficient. No live desktop process or
configuration was used for these integration runs. Optional reproducers are in
`tests/lifecycle`, `tests/upload_faults` and `tests/run_walle_preview.py`.
Labwc/grim and NumPy/Pillow are additional tools for those opt-in GPU checks.

The full sanitizer run initially reported512 bytes during Vulkan driver
unloading. A standalone create/enumerate/destroy program reproduced the same
report, and RADV-only enumeration reproduced256 bytes. Keeping the RADV library
loaded for that test makes both the minimal control and full application pass
ASan/UBSan/LSan without suppressions or disabled checks. This test-only preload
is recorded in the receipts; production has no preload or leak suppression.
The original unsuppressed failures remain evidence of the installed driver
unload limitation, not a passing leak test.

An early GPU evaluator also missed a missing demote-feature enablement because
it searched the wrong diagnostic spelling. Those v1/v2 validation-clean claims
were invalidated. Feature querying/enabling, atomic validation-error accounting,
checked teardown, and subsequent clean reruns replace them. Pixel agreement
alone was never used to waive the Vulkan error.

## Measurements and limits

Final timing results are in [PERFORMANCE.md](PERFORMANCE.md), with a machine-
readable table and reproduction tools under `tools/benchmark`. The fixed matrix
keeps all36 device/resolution/material/motion cases separate: current1920×1080
at239.760Hz and2560×2880 at59.967Hz, plus a labeled5120×2880 stress scenario.
Each case has one warmup, five first-use samples and595 warm samples; all samples,
run dispersion and tails are retained. Final measurements use the application's
horizontal direction and a fixed center origin. Random origins are not assigned
invented workload weights. Initial slanted-center data remains exploratory and
is not presented as a matched before/after comparison.

Samples submit and wait sequentially without display-rate pacing. These are
renderer GPU intervals, separate CPU API/completion measurements and owned Vulkan
allocations. Decode, upload, device startup, driver-internal memory
and compositor scheduling are outside those frame intervals. Offscreen timing
uses one optimal presentation image; production may retain a second modifier image until the compositor
releases it. No full-desktop frame-rate guarantee or architecture dominance is
inferred. The integrated GPU's observed tails exceed some current-output cadence
references; automatic device selection prefers a suitable discrete GPU.

The selected contract and source/implementation qualification are supported by
the checks above. General Apple-host completion, M1 pixel identity on Vulkan,
and literal universal optimality are not established or claimed. Full raw
qualification records, retained failed controls and image frames remain under
`/tmp/walle-work`; compact source/evidence records are shipped in `provenance/`.
