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
- [x] Regular's settled-interior correlated structure — the natural-content
      holdout answered it: settled interiors read 1.47/2.05 (regular
      light/dark) and 1.15 (clear) on unseen content against 3.4-3.8 on the
      coded field, with the whole settled sweep at 0.96 full-frame mean.
      The ~1-code "structure" was largely the coded field's own statistics
      (channel-independent high-chroma checks); the measured laws
      generalize. Remaining on the coded field only, tracked under the edge
      instrument.
- [x] Second-content probe change — shipped as lg-test branch
      `rig-natural-backgrounds` (e2347ee): a channel-correlated,
      red-spectrum, hard-edged natural-statistics pair behind
      `--natural-backgrounds`. Captured: `lgcap-natural-1024` (M1 home +
      /tmp + walle-archives), scored end-to-end.

- [ ] Exit scheduling jitter: the natural holdout exposed that the
      dematerialize is a separately scheduled animation - geometry ran on
      time (+0.1..+0.8 frames) while Apple's exit ran ~3 frames late
      relative to the coded run's schedule, costing up to 17 codes on
      high-contrast mid-exit frames. A four-repeat capture instrument is
      measuring the exit-start distribution; the law's anchor (fixed f(t)
      vs cover+median-delay) follows from it.

## The long game

- [ ] AGX clip-interpolator coefficient (divider) law - converts the mask's
      100.0% from "with hardware-measured per-tile constants" to "from
      public inputs". Probe campaign active (M1 walle-agx-* dirs); the flag
      flips the moment the law closes (TASK.md later-172 era note).
- [x] Live-path standing gate — `make live-transition-gate`
      (analysis/run_walle_live_transition_gate.sh): real timed transitions
      through the live renderer on a real DRM device, three geometries
      including 181's abort case, screenshot-verified motion, fails on any
      "Transition stopped". First run: pass (10-11 distinct frames per
      case, zero aborts).
- [x] Durable on-machine archives for the multi-GB capture sets: copied
      out of volatile /private/tmp into home on the M1 and into
      /home/quince/walle-archives on the workstation (with the corpus).
      Off-SITE storage remains an infrastructure choice.
- [ ] Reduce Transparency / Increase Contrast renditions and non-@2x
      backing-scale validation.
