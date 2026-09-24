# Fixed native ramp boundary

The delivered renderer samples the original ramp texture directly and has no
shared scalar or ramp-response buffer. The earlier 6,422,528-byte combined
resource remains an archived comparison control, described in
[M1_RAMP.md](../shaders/M1_RAMP.md). Its byte identity does not describe the
current resource layout.

`glass_push.native_ramp` is uint32 at offset20; projection_offset remains24 and
the push structure remains32 bytes. Only TINT_GRADIENT sets the flag, when its
current ramp is byte-identical to the original and coordinate x/y/w match the
original Float32 words exactly. Negative zero x is excluded. Coverage z and
the half color remain dynamic. Other/custom data keeps the existing generic
texture and sampler path. The real ramp texture and upload remain present.

The existing ramp-byte cache also retains the canonical classification.
Unchanged warm uploads return before another full comparison to the original.
Cancellation resets that classification with the ramp's ready state.

Run after shaders/protocols exist:

```
python3 tests/run_native_ramp.py --repo-root . --profile fortify
python3 tests/run_native_ramp.py --repo-root . --profile sanitize
```

The CPU controls check the absence of shared-function buffer allocations and
storage-buffer descriptors, all three sampler configurations, initialization
and cleanup success/failure paths, descriptor layouts and pools, exact
coordinates/pass selection, all 2,048 single-byte custom-ramp mutations, warm
comparison counts and independent coverage/color parameters. No GPU is invoked.

`native_function_alloc_fault.c` is an optional own-process allocation-failure
interposer for application checks. It recognizes the earlier pure STORAGE_BUFFER
resource role and logs its actual size; it does not assume a six-MiB buffer.
Build as a shared object with `-std=c23 -fPIC -shared -ldl`. It preserves the
existing WALLE_MATH_FAIL_TRACE interface. The current application absence
control expects zero such buffers and zero injected failures. Historical
table-backed controls used it to check fatal allocation failure separately
from cache analytic recovery.

Original source identities, qualification and integration receipts are under
`/tmp/walle-work/fidelity-completion/native-scene/tint_ramp_sampling/` and
`/tmp/walle-work/fidelity-completion/ramp-integration/`. These CPU controls
qualify the resource/gating boundary, not final scene pixel equality.
