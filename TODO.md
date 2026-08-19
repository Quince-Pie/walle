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

- [ ] Edge phase-offset instrument — now with a complete dossier: the
      last-pixel deficit reproduces on natural content to the code (same
      two rows, same constants and bottom lobe on both content classes),
      and FOUR field surgeries are falsified (profile rescale both ways,
      additive white, multiplicative colour-correct caustic — the last
      worsened both contents). Conclusion: the feature lives at per-pixel
      positions set by the RASTERIZED boundary; the instrument should read,
      and the law should key on, the R8 mask's own AA values rather than
      analytic depth. Acceptance: mean deficit +7 light / +5 dark removed
      on BOTH content classes without regressing the 128-background edge
      fit.
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

- [x] Exit scheduling jitter — measured with a four-repeat instrument: the
      start distribution is bimodal (0.691±0.003 / 0.732±0.004, ~2.4 frames
      apart, drawn per sequence) — a vsync-slot lottery on a separately
      scheduled fade. The un-delayed cluster is the law; clear's start was
      a cluster-contaminated fit and now shares regular's 0.691. Shipped;
      the exponent's cluster-aligned refit and a per-sequence exit
      alignment option in the scorer remain minor follow-ups.

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
