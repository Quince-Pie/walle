# Evidence index

`runtime-sources.json` identifies the delivered runtime/build revision. The
other records distinguish independent source-oracle checks, compiler/ABI checks,
actual device/frame comparisons, application lifecycle/failure observations,
and measured timing/allocation results. Historical absolute paths identify
retained research artifacts; they are not runtime dependencies.

`formatting.json` maps qualified host sources to the delivered formatting with
unchanged C tokens. `shader-source_port.diff` records the derivative/transport
adaptation against the retained Slang extraction. Material definitions and their
generator/source identities are in `../material/`.

The final GPU regression and packaged frame comparisons use existing accepted
images; they did not generate replacement goldens from the candidate. Earlier
failed demote validation and failed preview shutdown controls remain under
`/tmp/walle-work`, with the failure/correction described in `../VERIFICATION.md`.
The unsuppressed driver-unload leak controls are also retained there.

`performance.json`/CSV summarize the final horizontal-center scenario. Raw
samples, every run, input bytes, frozen source/compiler records and tools remain
under `/tmp/walle-work/benchmark-final`. Initial slanted-center measurements are
separate exploratory data, not a matched performance comparison.
