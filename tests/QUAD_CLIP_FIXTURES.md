# Original CPU quad-clipping fixture

Run `python3 tests/run_quad_clip.py --profile fortify` and
`python3 tests/run_quad_clip.py --profile sanitize`. These checks need only
repository sources, a C23 compiler and the shipped fixture. They do not use a
Mac, GPU, network or reference implementation at test time.

`quad_clip_fixture.bin` contains3,109 original enabled-clipping controls, with
their source hashes and record layout in `quad_clip_fixture_provenance.json`.
Expected output bytes come from QuartzCore's original `emit_quad` at18a6639f4,
under UUID `f1ba3189e95a3ecab59a5a6872754484`. The native wrapper owns synthetic
CPU context/vertex buffers and calls no Metal/UI API. Twenty-one inputs were
also taken directly from the native frame-eight scene's actual calls.

The controls cover identity/translation, uniform scale, both reflections,
all four clip edges, empty intersections, Float position collapse, null UVs
and signed-zero UVs. They compare every position/primary-UV bit and the exact
emission decision; the exporter checks original triangle order0,1,2,2,3,0.
There are2,402 emitted quads and707 empty results. Ten invalid C API inputs
additionally require the documented failure status and zeroed nonnull output.

The native run contains112 additional controls with CPU clipping disabled.
Their indices and the reason for exclusion are recorded explicitly: the new
helper always receives a clip rectangle. Some native controls provide a second
UV channel; only their position and primary-UV outputs are part of this
one-UV interface. The selected no-surface caller emits no second source UV,
so the helper initializes `source_uv` to zero.

This qualifies the selected single-quad CPU emitter. General rotation,
perspective, nine-part grids and surface-backed SDF paths have distinct source
emitters and must not be redirected to it merely to clip GPU geometry.
