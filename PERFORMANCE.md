# Performance qualification: hw_circle

**Final accepted scoped choice: `hw_circle`.** The user selected hardware blending and retained the documented portrait exception ([final user choice](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_DEFAULT.json)). [final delivery acceptance](/tmp/walle-work/fidelity-completion/resume_1400/FINAL_DELIVERY.json) supersedes earlier provisional/pending status. [retained-source verification](/tmp/walle-work/fidelity-completion/resume_1400/final-promotion/retained-hw-circle/ORCHESTRATION.json) confirms unchanged behavior/source and reuses the completed checks. This is a scoped engineering selection, not universal dominance or global optimality.

**Final scoped selection is hardware `hw_circle`, retaining the portrait exception.** The current code is the verified `hw_circle` composition selected under the user's explicit tradeoff preference. The user [prioritizes RX 9070 XT performance and authorizes work through 00:45 UTC](/tmp/walle-work/fidelity-completion/resume_1400/USER_FINAL_SCOPE_AND_BUDGET.json). Integrated GPU support and its results remain. The frame-time-over-one-time-setup priority remains. The user has also set a [conditional GPU-tail priority](/tmp/walle-work/fidelity-completion/resume_1400/USER_TAIL_PRIORITY.json): if matched circle-only confirmation preserves the portrait regular-sweep tradeoff, lower GPU tails take priority over lower GPU median time. Other workload/objective and fidelity requirements remain.

The current composition uses native SFUs/filtering, promoted-Float32 half division, guarded circle removal with coverage removal off, no shared scalar buffer or discard-only attachment, capability-gated hardware plain blending with shader fallback, and adaptive direct capture with copy fallback. Source correctness, supported inputs and recovery remain feasibility constraints.

## Completed comparisons and selected tradeoff

| Measurement | Completed scope | Interpretation |
| --- | --- | --- |
| [Exploration](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/screening-v1/results/STATUS.json) |648/648 cells:18 variants ×36 workloads; one measured lifecycle | Candidate selection data, not fresh confirmation |
| [Scalar mechanisms](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/scalar-controls/results-v2/STATUS.json) |84/84 cells:6 layouts ×7 patterns ×2 GPUs; five lifecycles | Decoder, locality, allocation/setup controls;14,092,861,440 checked output words |
| [Math/work removal](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/confirmation-math-v1/results/STATUS.json) |216/216 cells; five fresh lifecycles | Native/exact/checked division and guarded work-removal comparisons |
| [Blend/copy](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/confirmation-blend-copy-v1/results/STATUS.json) |108/108 cells; five fresh lifecycles | Shader-blend/common, shader-blend/combined and forced-copy/combined controls |
| [Circle/unit composition](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/circle-controls-v1/confirmation-final/STATUS.json) |180/180 cells; five fresh lifecycles | Complete; hardware default selected with portrait exception |

The504 completed fresh renderer cells remain separate from exploration and scalar tests. [DELIVERY_EVIDENCE.md](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/DELIVERY_EVIDENCE.md) and [qualification status](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/FINAL_QUALIFICATION_STATUS.json) preserve their scopes. Their older pending-authority/deadline fields predate the user's later explicit answer and do not supersede it.

Each full renderer cell has an untimed complete121-frame trajectory, then five fresh renderer/device/controller lifecycles with separate setup, first use,119 prescribed warm poses and teardown. Driver/filesystem caches are primed. The119 dependent poses are not independent replicates. Each workload/device retains GPU median/tails/maxima, inclusive CPU intervals, first-use, setup/teardown and memory. No aggregate workload weights, equality margin or statistical dominance claim is invented. Cross-batch comparisons retain timestamps/telemetry and do not assert matched thermal states.

These are unpaced offscreen renderer-service measurements on the pinned Mesa26.1.8 devices, not compositor/display latency or pristine-install startup. Offscreen presentation uses one optimal image; real dma-buf presentation can retain two modifier images. CPU llvmpipe participates in correctness, not this performance matrix.

## Material tradeoff still visible

Hardware blending lowers observed GPU medians in the completed blend comparison, while shader blending preserves a GPU-p99 advantage for9070XT portrait regular/dark sweep. In common math, per-lifecycle shader p99 is2.001–2.283ms versus hardware2.814–3.087ms; inclusive CPU-p99 ranges overlap. Combined math retains the same shader-favorable condition. [Exact tradeoff record](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/confirmation-blend-copy-v1/PERSISTENT_SHADER_TAIL_TRADEOFF.json).

The [pose diagnostic](/tmp/walle-work/fidelity-completion/resume_1400/delivery-docs/SHADER_TAIL_POSE_DIAGNOSTIC.md) localizes that condition to progress0.800–0.817. Most measured excess is in pre-glass scene work, not the post-draw timestamp interval. Hardware still wins most warm poses. Process-endpoint telemetry cannot establish a DVFS/noise/compiler cause. This localization neither erases the tail condition nor supplies a selection preference or a circle-only result.

Forced copy, retained discarded attachment and scalar-table alternatives retain their own favorable observations and lifecycle/memory costs in the reports. Their mechanism arguments and measured scopes do not establish universal dominance. The additional circle/unit confirmation is now complete. [raw-backed final comparisons](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/circle-controls-v1/COMPARISONS.json) and the [scoped review](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/circle-controls-v1/REVIEW.json) retain all15 comparison pairs without a majority-vote or dominance claim. The user's complete-data choice retains the hardware default and documents the portrait exception.

## Resource scope

The [fresh confirmation ownership record](/tmp/walle-work/fidelity-completion/resume_1400/native-instructions/confirmation-math-v1/OWNED_MEMORY_SCOPE.json) records a maximum **1,692,133,936 bytes (1.576 GiB)** across its full-trajectory/teardown scope, on integrated5120×2880 clear/light/tinted sweep. The same workload on9070XT records1,684,490,800 bytes. These are owned Vulkan allocations, not total-process RSS, driver/compositor allocations or a universal cap.

The native 192 MiB SDF budget controls retention only. A larger successful SDF may be a transient texture field. The approved analytic fallback handles unsupported/unallocatable cache storage; it does not silently shrink normal native fields to improve the benchmark. No universal memory-optimality or qualified sparse-backing claim is made.

## Delivery boundary

[VERIFICATION.md](VERIFICATION.md) records passing release/test/sanitizer/analyzer/package,492 native diagnostic comparisons and actual-app checks for the provisional source. Those checks are separate from final performance selection. If selection changes behavior, the affected verification and source/build hashes must be refreshed. Historical incomplete84/108 measurements remain under `/tmp/walle-work/fidelity-fix/architecture-comparison-vcm`; they do not describe the current completed matrices or current implementation.

The complete-data default question is resolved: hardware, retaining the portrait exception. Across all36 workloads, the final circle comparison observes higher shader-blend GPU medians in all36 separated ranges; GPU-p99 is higher in32, lower in the portrait regular-sweep case, and overlapping in3. These are observed lifecycle ranges, not confidence intervals or a universal ranking. The current `hw_circle` is accepted under the user's explicit hardware-default choice, with the portrait exception retained. No prescribed comparison cells or active Walle acceptance gates remain; the user explicitly accepted the documented tradeoff.
