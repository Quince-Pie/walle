# Analytic recovery CPU contracts

Run `python3 tests/run_sdf_replan.py --profile fortify` and the same command
with `--profile sanitize`. This control calls the real renderer's public frame
API. Vulkan resource functions are replaced by deterministic CPU stubs; no
instance, device, GPU, Wayland connection or rendering command is created.

Ten graphics-limit or allocation failures must return REPLAN. Four injected
device-lost results remain fatal. Eight invalid metadata/ownership cases are
fatal without requesting recovery. A pending prior-frame fence returns RETRY.
An allocation failure for a reveal surface stays fatal, so recovery is scoped
to the SDF cache resource.
Every case asserts zero command-buffer reset, begin and queue-submit calls.
Partial allocations must be destroyed and tracked memory must return to zero.

The existing `run_cache_controller.py` additionally checks20 recovery cases.
Four exercise native sequence checkpoints, including first allocation,
retained ownership, the large retained surface and the portrait retention
budget miss. Sixteen cover regular/clear, light/dark, active/inactive and tint
presence. Recovery's full analytic packets, vertices, indices, scissors,
surfaces and tint ramp must equal a separate never-cached controller owner at
the exact same pose/time. That comparison is a recovery invariant, not an
independent GPU pixel oracle; the existing native corpus still qualifies the
normal host path without relaxing any expected values.

The observation wrapper verifies the native eligibility ring after recovery
and subsequent builds. Rebuilding an uncommitted recovery does not advance
that history twice. The failed renderer resource is invalidated immediately,
so a new cache build must redraw even when the recovery did not commit. A
second recovery of the same build is rejected. Commit, new build and context
reset behavior are checked separately.

This implements the user-approved analytic exception for SDF cache limit or
allocation failure. A retention-budget miss remains a texture-SDF frame; the
unchanged portrait native fixture explicitly checks that case.
