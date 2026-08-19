# Walle parity TODO

The remaining distance to total parity, as concrete items. Each carries its
receipt trail in TASK.md (sessions 179-188) and the scores it moves.
Current: mask 100.0% corpus-exact; material settled 1.46 / animated 2.17
code values against the M1 captures (0.95 full-frame on the natural-content
holdout). Every agent-completable item below is done; what remains is
user-gated (see the last section).

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

- [x] Edge phase-offset instrument — RESOLVED, and the dossier's
      prescription was exactly right: keying on the R8 mask's own AA
      values (the boundary pixel class 0<mask<255) found the whole
      deficit. SHIPPED (c902a6f) as the AA boundary law: the final
      mask blend happens in sRGB code space (the same law the corpus
      proved for the reveal blend) plus a coverage highlight
      A·4m(1−m)·thickness with A = lerp(19.17,31.0,lightness) regular,
      lerp(25.13,28.7,lightness) clear. Acceptance met on BOTH content
      classes with a coded-only fit: coded clear edge 2.83→2.48,
      regular 10.40→10.02; natural clear 2.45→2.04, regular 3.95→3.54;
      full-frame 1.47→1.46 and 0.96→0.95; the reveal gate stays 100.0%
      (the term vanishes at mask 0 and 255). Code-space blend alone
      (without the highlight) worsens the edge, as the dossier
      predicted — the pair is the law.
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
      the exponent's cluster-aligned refit is also done (52f16ce): clear
      2.075 (max |residual| 0.051→0.017 over the early-cluster curves),
      regular's 1.585 confirmed as already cluster-clean. A per-sequence
      exit-alignment option in the scorer remains a nice-to-have for
      anyone scoring a single capture (the lottery is capture variance,
      not walle error).

## The long game

- [x] AGX divider-law status recon (the law itself is user-gated, below):
      the campaign in the M1 walle-agx-* dir is far deeper than this file
      previously recorded — ~90 experiment dirs; the single-clip-ruler
      *-plan-v1 dirs are empty PLAN dirs, the actual captures are the
      scr*-captures (scr through scr5b AND scr5c all exist), and v6/v7
      dirs plus their plan generators are already present. All solvers,
      plans, probes and preregistrations live in analysis/ here.
- [x] Live-path standing gate — `make live-transition-gate`
      (analysis/run_walle_live_transition_gate.sh): real timed transitions
      through the live renderer on a real DRM device, three geometries
      including 181's abort case, screenshot-verified motion, fails on any
      "Transition stopped". First run: pass (10-11 distinct frames per
      case, zero aborts).
- [x] Durable on-machine archives for the multi-GB capture sets: copied
      out of volatile /private/tmp into home on the M1 and into
      /home/quince/walle-archives on the workstation (with the corpus).
- [x] Off-SITE storage: all three sets are on GitHub as branches —
      archive/liquid-glass-reveal-coverage-01421a3-v1, archive/lgcap-2048,
      archive/lgcap-natural-1024. Mechanics note: pushes over ~1.3GB in a
      single pack get "! [remote rejected] ... (failed)" with no reason
      text; the fix is stacked commits pushed one at a time (the natural
      set went up as three ≈450MB packs; final tree verified bit-identical
      to the single-commit original).
- [x] Reduce Transparency rendition — captured (lgcap-reduce-transparency-
      1024 on the M1, manifest reduceTransparency=true, guarded
      toggle+restore verified "RESTORED reduceTransparency=0") and
      measured against the paired normal natural set (TASK.md session
      188): regular becomes an opaque near-neutral plate (abs luma 242.3
      light / 19.9 dark, RGB spread <0.5), rim/lens/refraction removed,
      reveal geometry and the 4px AA edge preserved to ±2px, shadow kept
      but re-weighted (dark ≈0.43×, light ≈1.34×, single-state read);
      clear becomes an extreme blur (Gaussian-equivalent σ≈500 capture px)
      under a flat scrim with light = dark + 61.5 codes exactly at every
      state — the appearance-blind clear core SURVIVES accessibility mode.
      Constant-vs-wallpaper-derived plate color is undecidable on the
      near-neutral field (neutrality and AppKit's solid-fill fallback
      favor constant); no walle RT mode ships until a saturated-background
      RT session decides it.
- [x] Increase Contrast rendition — rig gained --expect-increase-contrast
      (lg-test 0c52b25, symmetric to the RT flag); captured
      lgcap-increase-contrast-1024 with the same guarded toggle pattern;
      measured in TASK.md session 188.

## User-gated (not agent-completable; awaiting the user)

These are not open work items for an agent session; they are the user's own
active work or decisions that need the user present. Recorded so the list
above stays honest about what "done" means.

- **The AGX clip-interpolator (divider) law** — the one item that converts
  the mask's 100.0% from "with hardware-measured per-tile constants" to
  "from public inputs". This is the user's own live probe campaign
  (walle-agx-* on the M1, scr→scr5c captured, v6/v7 planned); agent
  sessions must not run captures or solvers against that directory
  mid-flight. The flag flips the moment the user's campaign closes the law.
- **Non-@2x backing-scale validation** — needs a display-mode change on the
  M1 (reflows the live desktop, including the AGX campaign's windows) or a
  virtual display; either wants the user at the machine.
- **A saturated-background Reduce Transparency session** — one rig
  background pair with strong chroma would decide constant-vs-derived for
  the RT plate colors and pin the clear-scrim law; only worth scheduling if
  walle grows an accessibility mode.
