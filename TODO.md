# Walle parity TODO

The remaining distance to total parity, as concrete items. Each carries its
receipt trail in TASK.md (sessions 179-184) and the scores it moves.
Current: mask 100.0% corpus-exact; material settled 1.46 / animated 2.17
code values against the M1 captures.

## Completable now

- [x] Land main's `run_materialize_v2_gate.sh` ASan-leg repair on a branch —
      pushed as `main-gate-asan-fix`.
- [x] Off-machine archive of the restored ground-truth corpus — pushed as
      `archive/liquid-glass-reveal-coverage-01421a3-v1` (the bytes, the
      validator output, and the provenance; install by copying into
      `artifacts/`).
- [x] Mask-anchored shadow re-measurement — done, and it decided everything:
      walle's "decay" was quiet-ring anchor drift (mask-anchored bands are
      flat to ±0.1 across eleven states); the deficit is one uniform
      amplitude ratio per appearance. SHIPPED as `shadowBlend *= lerp(1.25,
      1.46, lightness)`; verified residuals ≤0.1 codes per band (dark band
      0 overshoots −0.38 — a per-band refinement if it ever matters).
- [x] Second-content capture feasibility — investigated: the rig's
      backgrounds are compiled-in procedural closures (no CLI flag), so a
      natural-content set needs a probe change; moved to the M1 instrument
      session below.

## Needs a dedicated M1 instrument session

- [ ] Edge phase-offset instrument: the element boundary stepped through the
      pixel grid in sub-pixel increments over flat and gradient backgrounds,
      read at the final two pixels. Target: the rim's last-pixel row (walle
      10-14 codes dark at depth 0.5-2.5; falsified for field surgery three
      ways in 183b). Acceptance: mean deficit +7 light / +5 dark removed
      without regressing the 128-background edge fit.
- [ ] Regular's settled-interior correlated structure (~1.0 code rms): hunt
      with the second-content set plus the coded field; candidate mechanisms
      after panel-space: transfer/blur interplay on structured content,
      capture-side processing.
- [ ] Second-content probe change: add a natural-image (or third
      procedural) Background family to GlassCapture and capture one set —
      the decorrelation instrument for regular's ~1-code interior structure
      and the rim's angular reading.

## The long game

- [ ] AGX clip-interpolator coefficient (divider) law - converts the mask's
      100.0% from "with hardware-measured per-tile constants" to "from
      public inputs". Probe campaign active (M1 walle-agx-* dirs); the flag
      flips the moment the law closes (TASK.md later-172 era note).
- [ ] Live-path standing gate: a permanent headless-DRM live-transition
      soak (the capture path cannot see live-only failures; the 181 abort
      class must never hide again).
- [ ] Durable archive for the capture corpora that exceed GitHub scale
      (lgcap sets, 1.7-9.5 GB each): storage is an infrastructure call.
- [ ] Reduce Transparency / Increase Contrast renditions and non-@2x
      backing-scale validation.
