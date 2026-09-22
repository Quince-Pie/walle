# Optional actual-app upload failure controls

Requires a GPU test lease, Labwc, the release app and Vulkan validation layer.
Build the shim with `bash tests/upload_faults/build.sh`, then run
`python tests/upload_faults/run.py --stage 1` (repeat for2 and3).

The unchanged application runs in a private headless compositor/runtime/cache.
`LD_PRELOAD` wraps `vkQueueSubmit2` with `dlsym(RTLD_NEXT)`, returning one
`VK_ERROR_OUT_OF_DEVICE_MEMORY` without submitting that selected operation.
For the current Wayland renderer, uploads contain one command buffer and no
wait/signal semaphores; frames signal the presentation semaphore. Upload1 is
initial A,2 restores A after the first plain frame,3 uploads incoming B.

The shim observes image-cache fd mappings/reads and explicit successful closes.
The test requires a single injected fault, actual app exit1 within25seconds,
the expected preview upload-failure message, all observed source fds closed
before library destruction, zero validation/sanitizer errors, and no watchdog
termination. Labwc's own returncode is insufficient; child status is recorded.

`--multi` instead runs a two-headless-output normal daemon with timer1/duration2.
It needs `bash tests/lifecycle/build.sh` first, plus grim and NumPy/Pillow. Each
output must produce A/B worker jobs and a fresh mixed-color protocol capture,
then SIGTERM must exit0 with clean validation and no forced cleanup.

These are functional failure/lifecycle controls, not performance measurements.
The shim does not simulate physical device loss or claim general Vulkan fault
coverage. Production files and live compositor/session services are untouched.
