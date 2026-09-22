# Predeclared Walle performance confirmation

Current Wayland output query: DP-1=1920x1080 at239.760Hz, HDMI-A-1=2560x2880
at59.967Hz, bothscale1. Do not inherit the stale README's5K display assertion.
5120x2880 is an additional stress scenario, not a current-output claim.

Measure on discrete and integrated AMD separately, with no concurrent device
jobs. Use same source0008→0010 images, crop/resize settings and source-derived
material packets for all compared motion cases. Two faithful motion candidates:
sweep andlens. Include clear/light/untinted (default), regular/dark/untinted,
and clear/light/tinted as explicit scenarios rather than inventing workload
weights. Diagnostics disabled outsidemeasurement; benchmark readback disabled.

Record GPU timestamps (actual frame total, capture/pyramid,draws), CPU wall
submission/completion separately, actual memory allocations (not totaldriverVRAM),
and input/source/toolchain/device hashes. First-use includes capture/pyramid;
keep it separate from warmframes. Also report idle andafter-destroyownedmemory.

Use one untimed complete transition percase aswarmup, then five fresh independent
runs percase at fixed progresspoints1..119/120. Preserve all valid samples and
retries; report per-run summaries and pooledmedian,p95,p99,max with runrange.
The current-output frame intervals4.171ms/16.676ms are practical cadence
comparisons, not newly asserted user hardlimits or a universalperformanceproof.
5K stresscomparisonuses16.667ms as labeled60Hzscenario. No averaging between
resolutions,GPUs,materials ormotionstohidearegression. Stopafterfive runs;
repeatonlyfor a demonstratedevaluator/implementationchange andinvalidate
itsaffectedoldresults. These are scoped engineering measurements; no exact
equality/practicalequivalence/dominanceclaimwithoutauthorizedmargins/evidence.

The local-read architecture preserves same-pixel framebufferfetch and eliminates
explicit whole-scene preservation copies/extra storage required by the ordinary
ping-pong control. This is a data-movement argument, not automatically an elapsed-
time superiority claim. If measured behavior or architecture review exposes a
credible performancechallenge that couldreverse selection, investigateit.

Visual selection uses identicalnative optics andcolors: compare stable rim
visibility, legibility ofeachwallpaper, monotonicreveal, endpointcontinuity,
noidleveil, anddistraction overbright/dark/high-detail/portrait assets. The
curve/pacing/settle areWalle design,not extractedApple transitionbehavior.
No universal'beautifulbest'claim. Usercanchooseaesthetic preferenceiftradeoff
remains after actual rendered evidence.


Pre-measurement correction for the final delivered-source confirmation:
use origin(0.5,0.5), direction(1,0), scale1 and label this the actual-app
horizontal-center scenario. The completed initial direction(1,0.12) matrix
is retained as an exploratory slanted-center scenario, not a matched
performance comparison. All36 cases, five fresh runs, first-use separation,
119 warm points, statistics and stopping rules remain unchanged. No final
horizontal-center GPU measurements had started when this correction was made.
