# Verification of the accepted hw_circle integration

**Final accepted scoped choice: `hw_circle`.** The user selected hardware blending and retained the documented portrait exception ([final user choice](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_DEFAULT.json)). [final delivery acceptance](/tmp/walle-work/fidelity-completion/resume_1400/FINAL_DELIVERY.json) supersedes earlier provisional/pending status. [retained-source verification](/tmp/walle-work/fidelity-completion/resume_1400/final-promotion/retained-hw-circle/ORCHESTRATION.json) confirms unchanged behavior/source and reuses the completed checks. This is a scoped engineering selection, not universal dominance or global optimality.

The currently integrated `hw_circle` snapshot passes the checks below. **Final scoped selection is complete.** The fidelity contract is the extracted Apple algorithm and its graph/precision/ownership semantics; identified GPU/OS opcode and raster differences are allowed. Native image equality is not the acceptance gate, and no image tolerance or fitted correction has been introduced.

## Source and build identity

The [integration record](/tmp/walle-work/fidelity-completion/resume_1400/provisional-integration-2305/INTEGRATION.json) pins the provisional changes. Its older verification-pending label is superseded, for these checks, by the [completed verification receipt](/tmp/walle-work/fidelity-completion/resume_1400/final-verification/provisional-circle/RESULTS.json): release, normal tests, ASan/UBSan tests, analyzer and Nix package all PASS, with checked sources unchanged during that run.

| Artifact | Recorded identity |
| --- | --- |
| Release executable SHA256 | `8b303a297a356a69e40d83fbbc9e05463ef125e9069d57fc2f6c23dbb7ee1c45` |
| Package | `/nix/store/5mf4zw2nn4yv7r585zx66zvdl77kx4pn-walle-0.0.1` |
| Package executable SHA256 | `87d30f174509ab97eb0451e9509c8582e48728b00e6b9496d8153b10d52a03e9` |

These identify the verified provisional build, not a future selection. Documentation integration does not replace those pinned receipts. A later behavior change requires its affected checks and identities to be refreshed.

```sh
nix develop --offline path:. -c make MODE=release -j8
nix develop --offline path:. -c make MODE=release test test-sanitize
nix develop --offline path:. -c make MODE=release ANALYZE=1 -j4
nix build --offline --no-link --print-out-paths path:.
```

The flake pins GCC15.2, Make4.4.1, Slang2026.12, Python3.14.4 and Vulkan headers1.4.341. All 27 generated shader entries are validated before C23 `#embed` consumes them. CPU fixtures need neither a Mac nor a live compositor. They cover renderer limits, retained/elided auxiliary attachment policies, scalar-buffer absence, both blend/FMA selectors, cache recovery, material/transition/cache/clip, application contracts and configuration.

## Independent mechanism evidence

The reference is M1 Max, macOS 26.6.1 build 25G76, QuartzCore 1195.17. Retained original-function controls cover1,097,551 log2f inputs,36,810 blur plans,20,032 YCC calls,20,000 matrix products,20,346 capture arithmetic controls,20,480 backdrop/transform calls,3,109 clipping calls,6,000 cache-state calls and100 exact-input cache frames/250 updates. Their source mappings and premises are linked by [RESEARCH.md](RESEARCH.md) and the [D/E/I/N closure map](/tmp/walle-work/fidelity-completion/resume_1400/final-audit/D_E_I_N_CLOSURE_MAP.md).

[PLATFORM_OPERATIONS.md](shaders/PLATFORM_OPERATIONS.md) records the active scalar/division/sampler/blend paths and archived exact controls. Source FMA/order/narrowing remain required. The hardware-blend source-boundary test covers every binary16 word in each of four lanes on all three Vulkan devices; the original clamp/widen result is exact in that control. The actual DISPLAY gate is true on all nine advertised attachable modifiers of the 9070XT and all five of Raphael/Mendocino. These facts are separately scoped from complete-frame correspondence.

## Native diagnostic corpus

The [provisional-circle corpus receipt](/tmp/walle-work/fidelity-completion/resume_1400/final-native-confirmation-circle/CONFIRMATION.json) completes492 comparisons:164 per device, covering54 static scenes, four25-frame trajectories, five endpoints and five visible cached interiors. Checked sources remained unchanged; executions, validation and owned-resource teardown were clean. Original native scenes derive their own graph/capture from exact inputs rather than accepting candidate packets as an oracle.

| Device | Comparisons | Byte-identical frames | Largest recorded `max_byte` difference |
| --- | ---: | ---: | ---: |
| RX 9070 XT |164|49|16|
| Raphael/Mendocino |164|49|16|
| llvmpipe CPU |164|39|107|

These are raw diagnostic counts, not quality percentages, tolerances or algorithm certification. The receipt explicitly leaves algorithm acceptance separate. The [platform classification](/tmp/walle-work/fidelity-completion/resume_1400/final-audit/RESIDUAL_CLASSIFICATION.md) does not assert every differing byte has been individually attributed. The matched-coordinate16-pixel CPU FMA residual has its own causal intervention; that result does not explain all complete-frame differences.

## Actual application and failures

The [actual-app records](/tmp/walle-work/fidelity-completion/resume_1400/final-app/run_records) contain nine passing release controls: clear preview, regular/tinted preview, SDF allocation recovery, scalar-storage absence, lifecycle/reload/resize/cancellation, multiple outputs, and three upload-failure stages. They use private compositor sockets/configuration and owned artifacts. Live services and configuration remain unchanged.

Independent preparation confirms exact opaque A/B endpoints in all five recorded preview directories: [clear](/tmp/walle-work/fidelity-completion/resume_1400/final-app/endpoints/preview_clear/result.json), [regular/tinted](/tmp/walle-work/fidelity-completion/resume_1400/final-app/endpoints/p/result.json), [cache recovery](/tmp/walle-work/fidelity-completion/resume_1400/final-app/endpoints/c/result.json), [storage absence](/tmp/walle-work/fidelity-completion/resume_1400/final-app/endpoints/a/result.json), and [package](/tmp/walle-work/fidelity-completion/resume_1400/final-app/endpoints/q/result.json). The [package preview](/tmp/walle-work/fidelity-completion/resume_1400/final-app/run_records/package_preview.json) passes and all 61 frames equal the release preview byte-for-byte. Different executable hashes are not a frame-equivalence failure.

Expected injected failures follow their declared recovery/fatal paths; successful checked teardown has zero owned allocations. Driver/compositor-private allocations are outside those counters. The absence control expects zero obsolete scalar-buffer allocations, not failure of a resource the implementation no longer creates.

## Final acceptance

The source is unchanged from the verified `hw_circle` baseline. [retained-source verification](/tmp/walle-work/fidelity-completion/resume_1400/final-promotion/retained-hw-circle/ORCHESTRATION.json) verifies147 CPU and152 application source identities and retains17 completed receipts. [final user choice](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_DEFAULT.json) selects hardware while keeping the portrait exception; [final delivery acceptance](/tmp/walle-work/fidelity-completion/resume_1400/FINAL_DELIVERY.json) records final scoped acceptance. No global optimality, universal dominance or universal aesthetic claim follows. The broad standalone host extraction remains paused.
