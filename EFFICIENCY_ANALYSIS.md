# walle efficiency analysis

Measured through 2026-07-27 with Linux 6.18, Mesa 26.1.5/amdgpu, niri, an
AMD Radeon RX 9070 XT, and the active
outputs below:

| Output | Buffer size | RGBA8 wallpaper | 1/8 glass layer | One texture pair |
|---|---:|---:|---:|---:|
| DP-1 | 5120×2880 | 56.250 MiB | 0.879 MiB | 57.129 MiB |
| HDMI-A-1 | 2560×2880 | 28.125 MiB | 0.439 MiB | 28.564 MiB |
| Total | 22,118,400 px | 84.375 MiB | 1.318 MiB | 85.693 MiB |

## Visual-equivalence gate

Image quality is a hard constraint, not a benchmark tradeoff:

- `shaders/frag.glsl` is byte-for-byte identical to the user's
  pre-optimization worktree version. Its SHA-256 is
  `11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.
  The committed blob with SHA-256
  `6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d`
  was fitted to the now-rejected Reduce Transparency artifact and is
  intentionally not treated as the quality baseline.
- Wallpaper and glass textures remain full-resolution/source-resolution
  lossless `GL_SRGB8_ALPHA8`, uploaded as the same RGBA8 pixels.
- Dimensions, sRGB decoding, linear filtering, and clamp-to-edge sampling are
  unchanged.
- The four sampler uniforms were moved from every frame to program
  initialization. Their values are identical; this only removes redundant
  driver calls.

A BC7 experiment did reduce memory, but it measured only 36.29 dB PSNR with
2.66 mean absolute 8-bit error on the 5K output. It was rejected and removed.
The final source has no compressed-texture render path. An exact-quality gate
means an attractive VRAM result is irrelevant if even one rendered pixel can
change.

## Finding: the reported ~1100 MiB is almost certainly triple-counted

amdgpu exposes the same client accounting record through every DRM fd owned by
that client. walle had three such fdinfo records during the test. All three
reported the same `drm-client-id` and the same `drm-memory-vram` value.

After multiple transitions, the stable value for that one client was:

```text
drm-client-id:    7628
drm-memory-vram:  382544 KiB
```

Summing the three fd records produces:

```text
3 × 382544 KiB = 1120.734 MiB
```

That matches the reported ~1100 MiB unusually closely. VRAM tooling must
deduplicate fdinfo records by `drm-client-id`; selecting any one record for
each client is sufficient.

## Live results after this refactor

Controlled A/B runs used the same binary, wallpapers, output modes, and three
dual-output rotation cycles. `WALLE_KEEP_EGL_SURFACE=1` is the measurement
control; the default build retires an output's EGL window surface after its
final frame.

| State | Keep EGL surfaces | Retire while idle | Change |
|---|---:|---:|---:|
| Cold idle | 201 MiB | 172 MiB | -29 MiB (-14.4%) |
| Repeated-cycle idle | 373–375 MiB | 258–260 MiB | -115 MiB (-30.7%) |
| Later transition peak | 433 MiB | 433 MiB | unchanged |

The user's 373 MiB idle observation is reproduced by the control. The final
steady idle is 258–260 MiB, a 113–115 MiB reduction. The transition peak is
intentionally not claimed as improved: the EGL surfaces and both old/new
texture pairs must exist while the effect is being drawn.

The difference between logical and driver accounting is expected:

- EGL allocates presentation buffers for both Wayland surfaces. Keeping those
  surfaces alive preserves their Mesa/Wayland swap-chain high-water
  allocation even though no frame is being drawn.
- A transition necessarily reaches two wallpaper texture pairs, or
  171.387 MiB, while old and new images coexist.
- Mesa retains recently deleted BOs for reuse. `glDeleteTextures` removes the
  GL objects and walle's live-storage ledger returns to 85.693 MiB, while the
  driver's allocation cache remains near its first-transition high-water
  mark. Later transitions reuse that storage instead of growing it.
- Driver command buffers and other context allocations account for the
  remainder.

The texture layout creates only A at boot, creates B for a transition, deletes
the outgoing A at completion, and promotes B. At the same point, the final
buffer has already been committed, so walle destroys only the idle
`EGLSurface` and `wl_egl_window`; it keeps the Wayland layer surface, GL
context, program, and current lossless texture pair. The compositor continues
displaying the last attached buffer. A new EGL surface is created before the
next prepared wallpaper is activated.

This lifecycle change is why the stable number now falls below the old Mesa/EGL
high-water without re-decoding, re-uploading, or changing a pixel. A more
aggressive exact-quality experiment evicted idle textures too: it reached
229 MiB idle but raised the measured transition peak to 490 MiB and required
re-uploading the outgoing wallpaper. It was rejected because it harms
transition latency and throughput.

Host-side steady state was also bounded. After 82 seconds, including cache-hit
rotations on both outputs, `ps` reported 110.8 MiB RSS, 11 threads, and 0.1%
lifetime CPU. libvips' operation, memory, and file caches are disabled and its
worker concurrency is one. A render mapping exists only until
`glTexSubImage2D` returns, so decoded/cache pixels do not become permanent RSS.

## Texture/upload path

The previous path had three copies or allocations in play:

1. the raw cache file;
2. a `pread` into an orphaned pixel-unpack buffer;
3. texture storage allocated by `glTexImage2D`.

It also uploaded the first image twice, once into A and once into B.

The new path is:

```text
render worker mmap
    -> MADV_POPULATE_READ on a cache hit
    -> direct glTexSubImage2D on a private shared EGL context
    -> glFinish before publishing the shared texture names
    -> immediate munmap after GL consumes the client bytes
main Wayland/io_uring thread
    -> adopt two completed texture names
    -> start the transition
```

Textures use one-level immutable `glTexStorage2D`. There is no PBO, no second
file read, no first-boot duplicate, and no CPU mapping held after upload. Each
worker's private context shares objects with the render context and is released
after upload. Transitions that overlap a timer, resize, or reload are coalesced
and deferred, so an in-use B texture cannot be overwritten.

The shader still needs both full-resolution wallpapers during an actual
transition. Removing that 2× peak would require lossy compression, evicting
and re-uploading live inputs, or changing the transition algorithm; none meets
both the exact-quality and latency constraints.

## Tracy and synchronized timing

Tracy is opt-in (`make MODE=release TRACY=1`) and is absent from ordinary and
packaged builds. The Nix development shells provide Tracy's installed headers,
client library, capture tool, and viewer directly through the compiler
wrapper. No Tracy source path or Nix store path is passed through the project
build.
The definitive final-source lossless trace contains twelve per-output
preparations (six dual-output rotations) and 1,211 transition frames.

| Isolated region | Samples | Mean | Min | Max |
|---|---:|---:|---:|---:|
| Worker: prepare wallpaper | 12 | 16.988 ms | 14.712 ms | 18.529 ms |
| Worker: full-resolution upload | 12 | 7.370 ms | 3.207 ms | 11.052 ms |
| Worker: glass upload | 12 | 2.920 ms | 0.152 ms | 7.259 ms |
| Main: finalize prepared wallpaper | 12 | 0.622 ms | 0.099 ms | 2.046 ms |
| Main: transition-frame CPU work | 1,211 | 78.4 us | 21.4 us | 5.204 ms |

The two output workers overlap. Before the change, synchronized lossless
uploads blocked the main thread for 7.147 ms on DP-1 plus 4.575 ms on
HDMI-A-1, or 11.722 ms per dual-output rotation. In the final trace, the six
paired main-thread finalization bursts were 2.145, 0.749, 0.854, 1.577,
1.453, and 0.691 ms (1.245 ms mean). That is 89.4% less issuer-thread time,
or 9.42× transition-activation throughput, while keeping exactly the same
texture bytes.

The fragment shader itself was deliberately not approximated. Synchronized
baseline draws averaged 0.675 ms at 5120×2880 and 0.456 ms at 2560×2880.
Mesa's compiler reported 108 SGPRs, 48 VGPRs, 16-wave occupancy, no scratch,
and no spills. With the exact-output constraint, the measured gain belongs in
resource scheduling and lifecycle rather than changing shader arithmetic.

## Cache necessity and cost

The cache is useful and should remain. It stores the expensive output of:

- image decode;
- orientation/crop/fit/stretch;
- colorspace and RGBA normalization;
- full output-size resampling;
- glass downsampling and Gaussian preprocessing.

Without it, every rotation repeats that pipeline. The glass blur is especially
expensive at 5K. The uncompressed format is large, but it enables validation by
exact size and a direct, conversion-free GPU upload.

It is not GPU memory. A cache entry occupies disk space and may occupy
reclaimable Linux page cache while hot. Its mapping is released immediately
after upload.

At measurement time the cache contained 13 entries totaling 441 MiB. A
512 MiB high watermark and 384 MiB low watermark remain appropriate for these
outputs: a 5K entry is 57.129 MiB and a 2560×2880 entry is 28.564 MiB.

The maintenance policy did need correction. Running GC every 64 renders could
allow more than 3 GiB of 5K entries to be written between scans. Maintenance
is now triggered after 128 MiB of actual newly linked cache data. It retains:

- `O_TMPFILE` plus atomic `linkat` publication;
- exact keying by pipeline schema, source identity, dimensions, transform,
  glass parameters, and crop strategy;
- nanosecond mtime LRU;
- background `SCHED_IDLE` scanning;
- 512→384 MiB hysteresis.

Cache hits now mmap and prefault on the worker. Cache misses build into the
same mapping that is handed to GL, avoiding a write-unmap-read cycle.

## io_uring design

The reactor now uses `<linux/io_uring.h>` directly and follows the 6.18 design
in `IO_URING_SOTA_RESEARCH.md`:

- blind `REGISTER_QUERY` before setup, followed by feature and opcode probes;
- 256 SQ / 512 CQ entries;
- `SINGLE_ISSUER | DEFER_TASKRUN | NO_SQARRAY | SUBMIT_ALL`;
- one shared mapping, direct SQ/CQ atomic access, and zeroed 64-byte SQEs;
- self-registered ring fd for the hot `enter` syscall;
- sparse fixed-file slots for Wayland, inotify, sd-bus, and signalfd;
- one persistent absolute timeout for all rotation and sd-bus deadlines;
- direct inotify/signalfd reads;
- libwayland 1.25 multishot input poll;
- blind cross-thread `REGISTER_SEND_MSG_RING` render completion;
- a Wayland CQ pre-pass before ordinary callbacks;
- one CQ-head publication per batch;
- generation-tagged logical slots and terminal-CQE retirement;
- no SQPOLL thread, eventfd, timerfd, registered data buffers, or recursive
  `enter` from handlers.

SQPOLL would add a permanently scheduled kernel thread to an idle wallpaper
daemon. Registered data buffers and provided-buffer rings do not help these
fixed, low-rate control messages. The selected design minimizes idle work
rather than optimizing an irrelevant high-queue-depth benchmark.

## Data-structure/layout decisions

- `uring_hot` is exactly 128 bytes: one SQ cache line and one CQ cache line.
- The output render block remains exactly 64 bytes.
- Event slots are a fixed direct-index array. The maximum topology is known
  and small, so a heap, tree, hash table, or dynamic chunk pool would add
  allocation and indirection without improving lookup.
- Deadlines are scalar nanoseconds scanned across at most 32 outputs. At this
  cardinality a contiguous scan is cheaper and simpler than a priority queue.
- Cache GC retains a vector plus sort because it needs a complete LRU ordering
  only during rare background scans.
- Debug, release, sanitizer, analyzer, and native builds now use distinct
  object/test directories, preventing stale objects compiled with another
  flag profile from being linked.
- The Nix derivation uses an explicit source fileset. Local Clang/LTO objects,
  generated protocols, captures, and other workspace artifacts cannot leak
  into a clean GCC build.

## Validation performed

- byte-for-byte shader hash comparison against the Git baseline;
- controlled repeated-cycle `amdgpu_top` A/B runs with and without idle EGL
  surface retirement;
- lossless Tracy capture and CSV export of worker upload, main-thread
  finalization, draw submission, swap, and whole-frame zones;
- visual capture after EGL retirement confirming that the compositor retains
  the intended wallpaper buffer;
- GCC 15 C23 release build with LTO and no warnings;
- Clang 21 C23 release build with LTO and no warnings;
- GCC `-fanalyzer` build with no findings;
- ASan plus UBSan live Wayland/EGL runs for both cache-hit transitions and a
  completely empty-cache decode/preprocess/publish/upload path;
- release and sanitizer smoke tests for fixed-file reads, absolute timeout
  expiry, blind cross-thread `MSG_RING`, registered-ring entry/teardown, and
  the unchanged `tilde.c` expansion behavior;
- clean Nix build and Nix formatting check.

## Exact-quality floor

The remaining 85.693 MiB of idle logical texture data is input to the current
shader: one sharp RGBA8 wallpaper and one lossless pre-blurred RGBA8 layer for
each output. The measured alternatives below that floor either changed pixels
or regressed latency/peak memory, so they are intentionally absent. Future
work must pass the same byte/pixel-equivalence gate before a lower number can
replace the current result.
