# Native cache/controller fixtures

Run `python3 tests/run_cache_controller.py --profile fortify --require-matched-inputs`
and `python3 tests/run_cache_controller.py --profile sanitize --require-matched-inputs`.
The runner builds
repository C sources with explicit floating-point contraction disabled and
records source/binary hashes, commands and diagnostics under
`build/tests/cache-controller/`. No external extraction, Mac, GPU, network or
native library is needed to run the shipped regression.

`cache_native_fixture.json` contains original QuartzCore entry/return records
under UUID `f1ba3189e95a3ecab59a5a6872754484`, with hashes of the original scene
inputs, exact-input manifests, framework identities and raw CPU traces.
Expected state, eligibility, allocation, retention
and surface-origin values were copied from those records. Candidate C output
was not used to generate expected results. The four declared scenes contain
100 frames and 92 shared-state updates; the complete original update corpus
contains250 calls including separate single-copy owners. Every case uses the
fresh v9 native transport:964 numeric input leaves are verified against the
declared binary64 bits before the records are exported.

The regression calls the public create/build/update/commit APIs. A linker
wrapper observes the controller's actual cache-helper inputs and results, with
no private-layout access or substitute cache implementation. It verifies the
clipped element contribution, exact clocks, copied-effect counts, state ring,
changed/eligible decisions, cached/retained/redraw frame flags, creation
allocation and translated sampling origin. It repeats all 100 frames without
committing the first build, discards an initial cache allocation before a
different frame, tests same-context and changed-motion resets, confirms failed
update preserves the owner, and rejects 18 nonfinite-clock calls. Twelve new
texture-packer invalid-input cases require zeroed nonnull outputs.

The portrait case exceeds the native192 MiB retention budget and still uses
a rendered texture SDF. Its original three copied-effect renders are retained
in `native_copy_render_count`; the CPU contract checks one frame's redraw and
nonretention decisions. Equivalence of coalescing identical transient copies
within one frame remains a renderer/GPU qualification, as do device-cap and
allocation-failure rendering. The user-approved analytic replan is covered by
the additional CPU controls in `SDF_REPLAN_CONTRACTS.md`.

An earlier v8 run exposed Foundation JSON rounding in two lens frames. Those
failed checks and their inputs remain archived in the qualification evidence.
The v9 fixture replaces each whole case with fresh observed clocks and state;
it does not patch expected output bits or mix old and new epochs. The public
controller's transform must now equal both the declaration and the original
function's actual input, bit for bit. All250 direct native-state calls likewise
use the original input bytes. The strict input gate passes with no tolerance.

Endpoint frames contain no native eligibility calls. Their finite scene clocks
are reconstructed from the observed sequence epoch solely to exercise the
public hidden-endpoint path; `clock_observed` records this distinction. Every
optical frame uses the exact observed native absolute timestamp.
