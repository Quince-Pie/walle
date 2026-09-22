# Checks

`make MODE=release test` builds the current application and runs display-free
renderer-bound, independent material-fixture, transition/geometry, app-helper
and configuration checks. `make test-sanitize` instruments the host tests with
ASan/UBSan. Reports and compiled helpers go under `build/tests`.

`MODE=debug SANITIZER=1` builds the full instrumented app; use its explicit
`build/bin/debug-sanitized/walle` path because the active symlink follows the
last build profile. `MODE=release ANALYZE=1` runs GCC's analyzer in a separate
object directory. See `../VERIFICATION.md` for actual coverage and the installed
Vulkan driver's test-only unload workaround.

Optional actual-compositor checks are `run_walle_preview.py`, `lifecycle/`, and
`upload_faults/`. These create private headless sessions, require Labwc and the
listed helper dependencies, check child exit codes, and never need a live
wallpaper process. Run these separately from GPU measurements. Use
`prepare_image.c` plus `check_app_endpoints.py` for an independent libvips memory
oracle against captured BGRA endpoints.

Material fixtures come from the retained reference, not the implementation
under test. `MATERIAL_FIXTURES.md` explains their provenance and regeneration.
