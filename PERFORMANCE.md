# Scoped renderer measurements — final-actual-app-horizontal-center

Five fresh runs per case, each with one first-use frame at progress0.5 and119 warm points1..119/120. One complete untimed transition precedes each case. All validation counters including teardown are zero; all output destruction checkpoints report zero owned allocations. No readback. No samples were discarded or repeated.

GPU columns below are pooled warm timestamp intervals in milliseconds. Active/idle memory is owned Vulkan allocation MiB, not total driver VRAM; this offscreen path retains one optimal presentation image. Production dma-buf may retain two modifier images. `RESULTS.csv` adds first-use capture, CPU timing, retry, per-run range and cadence-count columns; per-case JSON retains every run.

## AMD Radeon RX 9070 XT (RADV GFX1201)

### 1920×1080 — current output, comparison interval 4.171ms

| Material | Motion | Median | p95 | p99 | Max | Active MiB | Idle MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| clear light tinted | lens | 0.254 | 0.361 | 0.382 | 0.395 | 47.54 | 8.44 |
| clear light tinted | sweep | 0.235 | 0.360 | 0.369 | 0.373 | 47.54 | 8.44 |
| clear light untinted | lens | 0.166 | 0.265 | 0.273 | 0.283 | 30.66 | 8.44 |
| clear light untinted | sweep | 0.156 | 0.256 | 0.262 | 0.272 | 30.66 | 8.44 |
| regular dark untinted | lens | 0.187 | 0.288 | 0.295 | 0.312 | 27.58 | 8.44 |
| regular dark untinted | sweep | 0.173 | 0.280 | 0.290 | 0.296 | 27.57 | 8.44 |

### 2560×2880 — current output, comparison interval 16.676ms

| Material | Motion | Median | p95 | p99 | Max | Active MiB | Idle MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| clear light tinted | lens | 0.566 | 0.938 | 0.956 | 0.982 | 160.73 | 28.75 |
| clear light tinted | sweep | 0.562 | 0.899 | 0.930 | 0.959 | 160.72 | 28.75 |
| clear light untinted | lens | 0.447 | 0.744 | 0.777 | 0.804 | 103.22 | 28.75 |
| clear light untinted | sweep | 0.422 | 0.708 | 0.777 | 0.786 | 103.22 | 28.75 |
| regular dark untinted | lens | 0.453 | 0.732 | 0.817 | 0.841 | 91.68 | 28.75 |
| regular dark untinted | sweep | 0.446 | 0.701 | 0.799 | 0.816 | 91.67 | 28.75 |

### 5120×2880 — 5K stress, comparison interval 16.667ms

| Material | Motion | Median | p95 | p99 | Max | Active MiB | Idle MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| clear light tinted | lens | 1.006 | 1.306 | 1.417 | 1.442 | 320.96 | 57.50 |
| clear light tinted | sweep | 0.912 | 1.145 | 1.180 | 1.210 | 320.95 | 57.50 |
| clear light untinted | lens | 0.728 | 1.030 | 1.105 | 1.130 | 205.96 | 57.50 |
| clear light untinted | sweep | 0.688 | 0.915 | 0.987 | 1.005 | 205.95 | 57.50 |
| regular dark untinted | lens | 0.789 | 1.098 | 1.173 | 4.177 | 182.73 | 57.50 |
| regular dark untinted | sweep | 0.731 | 0.954 | 1.024 | 1.058 | 182.72 | 57.50 |

## AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO)

### 1920×1080 — current output, comparison interval 4.171ms

| Material | Motion | Median | p95 | p99 | Max | Active MiB | Idle MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| clear light tinted | lens | 6.805 | 9.536 | 10.387 | 10.749 | 48.14 | 8.48 |
| clear light tinted | sweep | 6.211 | 9.753 | 10.104 | 10.494 | 48.13 | 8.48 |
| clear light untinted | lens | 4.383 | 6.596 | 7.086 | 7.101 | 31.16 | 8.48 |
| clear light untinted | sweep | 3.913 | 6.615 | 7.035 | 7.331 | 31.16 | 8.48 |
| regular dark untinted | lens | 5.015 | 7.442 | 7.852 | 7.922 | 28.02 | 8.48 |
| regular dark untinted | sweep | 4.330 | 7.331 | 7.338 | 7.362 | 28.02 | 8.48 |

### 2560×2880 — current output, comparison interval 16.676ms

| Material | Motion | Median | p95 | p99 | Max | Active MiB | Idle MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| clear light tinted | lens | 23.291 | 36.599 | 36.635 | 37.107 | 162.18 | 28.87 |
| clear light tinted | sweep | 22.536 | 33.609 | 33.627 | 33.660 | 162.17 | 28.87 |
| clear light untinted | lens | 14.990 | 24.959 | 25.074 | 25.337 | 104.44 | 28.87 |
| clear light untinted | sweep | 14.383 | 23.247 | 23.278 | 23.475 | 104.43 | 28.87 |
| regular dark untinted | lens | 16.678 | 27.611 | 27.639 | 27.723 | 92.05 | 28.87 |
| regular dark untinted | sweep | 15.976 | 25.897 | 25.927 | 26.146 | 92.04 | 28.87 |

### 5120×2880 — 5K stress, comparison interval 16.667ms

| Material | Motion | Median | p95 | p99 | Max | Active MiB | Idle MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| clear light tinted | lens | 50.171 | 73.043 | 73.142 | 73.527 | 323.18 | 57.73 |
| clear light tinted | sweep | 43.121 | 67.157 | 67.629 | 68.556 | 323.17 | 57.73 |
| clear light untinted | lens | 31.387 | 49.844 | 49.862 | 49.889 | 207.71 | 57.73 |
| clear light untinted | sweep | 27.394 | 46.451 | 46.542 | 46.738 | 207.70 | 57.73 |
| regular dark untinted | lens | 35.005 | 55.140 | 55.182 | 55.265 | 183.48 | 57.73 |
| regular dark untinted | sweep | 30.482 | 51.757 | 51.964 | 52.999 | 183.48 | 57.73 |

These are per-case engineering observations. The 5K case is a stress scenario, not a current-output claim. Cadence comparisons are practical references, not newly imposed limits. There is no averaging across devices/resolutions/materials/motions and no architecture-dominance inference from these timings.
