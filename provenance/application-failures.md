# Actual-app upload failure and two-output checks

The first run on release `f741d832b55cebf5f641adef9e78ee4cc10452fcf46371c6645baacbe7fdb424`
found a real programmatic shutdown hang. Upload1 failed as injected and Walle
reported preview failure, but the child stayed alive for25seconds until the
watchdog's SIGTERM. This remains a failed result in `final-upload-1/`.

Root corrected the main loop by continuing immediately after its top-of-loop
`begin_shutdown`, allowing the reaper to remove newly dead outputs before the
next blocking wait. The correction was made by root in production; these test
files never edited production code.

The unchanged shim and three-stage checks then passed on release
`979a9d1337c32dc5525b896ea80b054dde2438519283bfa552828502080a64b2`:

| Failure | Actual child status | Seconds | Observed source fds opened/closed | Remaining |
|---|---:|---:|---:|---:|
| Initial A upload |1|0.164|1/1|0|
| Restore current A |1|0.164|2/2|0|
| Incoming B upload |1|0.214|2/2|0|

Each injected exactly one `VK_ERROR_OUT_OF_DEVICE_MEMORY`, produced the expected
app upload-failure message, had no validation errors, and terminated without
watchdog signals. Cache-file/memfd tracking observes actual mmap/pread and close
calls; process exit alone is not used as proof of descriptor cleanup. These are
release tests; sanitizer build qualification is recorded separately by root.

The two-output run used two private1280×720 headless outputs, timer1/duration2.
Each output produced initial A and next B worker jobs and a fresh per-output
mixed-color screencopy frame. SIGTERM then returned child0 with clean validation
and no forced cleanup. `fixed-multi/` retains both PNGs and the full event trace.

All processes in the five owned test groups were confirmed absent after the
batch, and the GPU lease was released. Binary hashes were unchanged during all
runs. Portable optional files are in `ready/tests/upload_faults/`; multi-output
mode reuses the portable lifecycle observer. No live session/service was used.
