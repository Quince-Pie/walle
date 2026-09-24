# Native unused second attachment

`run_mrt2.py` executes the production renderer with CPU driver controls; it
creates no Vulkan instance/device or GPU work. Run after shaders/protocols
exist:

```
python3 tests/run_mrt2.py --repo-root . --profile fortify
python3 tests/run_mrt2.py --repo-root . --profile sanitize
```

The expected state comes from the original Core Animation attachment
inventory at
`/tmp/walle-work/fidelity-completion/native-scene/ATTACHMENT_INVENTORY.json`
and `CLEAR_ONLY_ATTACHMENTS.json`. Graphics passes bind a real RGBA16F second
target, including clear-only fallback, capture, mask, image, background,
highlight and cached-SDF generation. Its physical allocation covers the
primary. The selected ordinary programs do not read or write it; load/store
are DontCare. The existing tint backdrop input is a distinct role.

The controls check:

- 12 render/pipeline state combinations preserve target0 format/stores and
  target0 local-read mapping, distinguish NONE/DISCARD/READ_BACKDROP, and bind
  the second target with output location UNUSED and write mask0.
- All 24 production graphics pipelines have the expected two-target state;
  compute pipelines remain separate. Both native-FMA specialization states
  are checked on all24 graphics and both compute descriptors, with exact
  SpecId100/VkBool32 data.138 positive/near-miss backend-profile controls
  include every cache-UUID bit and the unsupported GFX8/LLVM cases.
- Four memory-selection cases cover lazy preference, lazy OOM with device-local
  fallback, unavailable lazy memory, and fatal device loss without fallback.
- 18 resource cases distinguish mandatory failure from cache-only growth
  REPLAN at image creation/allocation/bind/view stages and format admission.
  They assert no command reset/begin/submit and exact ownership cleanup.
- Three lifecycle cases cover reuse, in-flight protection, replacement only
  after completion, fallback from retained excess capacity to the exact
  required extent, output isolation, cancellation and resize.

The existing SDF resource fixture supplies an already available mandatory
auxiliary image so its injections continue to target cache-primary failures.
This suite independently exercises the auxiliary failures themselves.

GPU integration evidence is separately retained at
`/tmp/walle-work/fidelity-completion/mrt2-integration/gpu_v3/RESULTS.json`.
Five scenes on each of three devices preserve both AMD output planes, exact
native tiny fallback pixels, validation and zero owned memory after teardown.
Nine 25-frame timelines cover normal cache reuse and injected auxiliary-growth
failure; recovered frames exactly match fresh analytic owners. The complete
native numerical gate remains open: the odd CPU center improves from
BGRA[51,110,56,255] to[163,164,180,255], while native is[163,164,181,255].
That1-byte residual was recorded as a failure of exact native equality, not a
tolerance. The subsequent exact FMA/sampler integration closes the center
residual; other edge residuals remain. See the current completion record.
These tests do not certify every material pixel.
