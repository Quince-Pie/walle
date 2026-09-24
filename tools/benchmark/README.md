# Renderer benchmark reproduction

Run these tools inside the selected repository's authoritative Nix development
shell. `--repo-root` is explicit, and `--work-dir` must be outside the repository
so raw samples, logs, source snapshots and binaries are not committed accidentally.
No script assumes a particular checkout path or username.

```sh
nix develop "$repo_root#default" --offline --no-write-lock-file --command \
  python3 "$repo_root/tools/benchmark/build.py" \
  --repo-root "$repo_root" --work-dir "$benchmark_work"
nix develop "$repo_root#default" --offline --no-write-lock-file --command \
  python3 "$repo_root/tools/benchmark/prepare_inputs.py" \
  --repo-root "$repo_root" --work-dir "$benchmark_work" \
  --image-a "$image_a" --image-b "$image_b"
```

Set the shell variables to explicit paths first. Image defaults are the current
user's `~/.config/bg/0008.jpg` and `0010.jpg`; override them when reproducing on
another machine. Preparation occurs before measurements: libvips centered cover,
no rotation, sRGB, black flattening if alpha, opaque alpha and uchar. Prepared
bytes and original image files are hashed.

`build.py` invokes the repository's shader/protocol Make rules, snapshots its
current renderer/controller/material source plus the actual embedded SPIR-V,
and compiles a release C23/O3/LTO harness. Explicit `-ffp-contract=off` preserves
host arithmetic. Source, shader, compiler, flake and binary hashes are retained
in the external work directory. The same actual renderer frame path is used;
no readback buffer is requested for measurement.

Print the declared commands without touching a GPU:

```sh
python3 "$repo_root/tools/benchmark/run_plan.py" \
  --repo-root "$repo_root" --work-dir "$benchmark_work" --device discrete
```

Coordinate an exclusive device lease and run GPUs serially:

```sh
nix develop "$repo_root#default" --offline --no-write-lock-file --command \
  python3 "$repo_root/tools/benchmark/run_plan.py" \
  --repo-root "$repo_root" --work-dir "$benchmark_work" --device discrete --execute
nix develop "$repo_root#default" --offline --no-write-lock-file --command \
  python3 "$repo_root/tools/benchmark/run_plan.py" \
  --repo-root "$repo_root" --work-dir "$benchmark_work" --device integrated --execute
python3 "$repo_root/tools/benchmark/report.py" \
  --work-dir "$benchmark_work" --label final-confirmation
```

The declared matrix uses current-output cases 1920×1080/4.171ms and
2560×2880/16.676ms, plus explicitly labeled 5120×2880/16.667ms stress. These are
scoped cadence references, not asserted limits on other hardware. Materials:
clear/light/untinted, regular/dark/untinted and clear/light/#20bc9b96 tint, each
with sweep and lens; origin(.5,.5), direction(1,0), scale1. This is the actual-app
horizontal-center scenario. The earlier direction(1,.12) measurements remain
an exploratory slanted-center scenario and are not a matched performance
comparison with this final confirmation.

Each case has one untimed complete transition, then five fresh renderer/output/
controller runs. Each run records a first-use frame at progress0.5 separately,
then all119 warm progress points1..119/120. Each frame containing a glass
background draw must capture the current pre-glass composition and rebuild its
pyramid; allocation reuse does not make those changing pixels cacheable.
The final endpoint, promote, abort and destruction checks run with
timestamp diagnostics disabled. Source preparation and upload are outside the
sample timers. Validation must be active, every error is fatal, and checked
teardown is included in the result gate.

Cache decisions receive an explicit scene clock at a positive monotonic epoch
plus progress×Float(2.4), matching the current configured duration. Submission
remains unpaced. Controller history is reset after the separate midpoint
first-use probe; GPU allocations remain available for the warm trajectory.
Run-end records separately include startup (device/output, upload, controller)
and teardown wall time. They are lifecycle costs, not frame intervals.

`schema.json` describes the JSONL records. All samples/retries are retained.
The evaluator requires five complete runs,595 warm samples, five first-use
samples, zero validation errors and zero owned allocations after full renderer
destruction. Output teardown retains only renderer-shared resources. Shared
function-buffer bytes and memory flags are reported separately when present
in a reference control; the delivered native-operation path reports zero.
Device records include the qualified native-half-FMA
selection; unknown backend builds retain the exact portable helper.
Quantiles linearly interpolate at(n−1)q. Reports keep per-run and pooled
median/p95/p99/max, run ranges, first-use GPU/capture, CPU API/wait timing, retries,
and active/idle/abort memory separately. No averaging across cases occurs.

GPU time comes from five command-buffer timestamps. The scene interval includes
the resource prelude, wallpaper and reveal; the capture interval includes scene
sampling/copy, capture and pyramid; the draw interval includes later material
passes. Frame is their sum; the tail contains optional readback/release. Even
an empty interval retains its measured timestamp overhead.
CPU submission/completion time
is reported separately and includes validation overhead. Memory means owned
VkDeviceMemory allocations, not total driver VRAM. This offscreen path retains
one optimal presentation image; real dma-buf presentation can retain two
modifier images. These observations alone do not establish architecture
superiority or universal equivalence.

Existing result directories are not overwritten. A demonstrated implementation
or evaluator change requires a new work/result directory and invalidating its
affected earlier results. Stop after five runs; do not discard slow samples or
rerun merely to improve a number. Each owned case has a480-second timeout.
The earlier180-second records remain historical; the completion comparison
declared the larger common cap before new measurements.

The executable's optional final `--pilot` argument keeps the same untimed
transition and runs one measured lifecycle. Its JSON declares `runs:1`; the
ordinary five-run evaluator deliberately rejects that as confirmation data.
The two fixed completion pilots are scheduling diagnostics and never replace
any of the216 required variant/workload comparisons.
