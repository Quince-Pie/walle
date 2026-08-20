# Walle parity TODO

The remaining distance to total parity, as concrete items. Each carries its
receipt trail in TASK.md (sessions 179-191) and the scores it moves.
Current: mask 100.0% corpus-exact; material settled 1.46 / animated 2.17
code values against the M1 captures (0.95 natural holdout; 1.46 reproduced
digit-identical against a fresh capture on 2026-08-19; 3.35 at the
near-primary chroma stress limit). Every checkbox below is done; the sole
remaining item is the user's own AGX campaign (last section), which per the
2026-08-13 reframe concerns residuals the corpus does not need.

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
- [x] Reduce Motion — measured (session 190): Apple's wallpaper transition
      is EXEMPT from Reduce Motion (identical radius ladder, motion and
      duration; set lgcap-reduce-motion-1024; restore verified). walle's
      always-animate behavior is already Apple's. Rig flag
      --expect-reduce-motion (lg-test 6815c68).
- [x] Non-@2x backing-scale validation — CLOSED (session 191) via a 1x
      virtual display (CGVirtualDisplay tool on the M1, created and
      released around one capture; set lgcap-1x-1024, backingScale 1,
      clean preflight). Verdict: the reveal ladder and the material radii
      are POINT-anchored — edge radius ratio 2.002 vs @2x, rim profile
      matches the point-anchored hypothesis at 0.31 rms vs 2.82 for
      pixel-anchored — so walle's points = scale/GLASS_CAPTURE_SCALE law
      is measured-correct at 1x. Residual: the virtual display carries a
      generic sRGB profile (not the panel ICC), visible as a ~7-code
      interior offset; matches the blur-space law's prediction.
- [x] Saturated-background Reduce Transparency session — CLOSED (session
      191): rig gained a deep-red/deep-blue pair (--saturated-backgrounds,
      --swap-dynamic-backgrounds; lg-test e322768); four guarded captures
      (normal + RT over each colour, restore verified). Verdict: the RT
      plates are BACKDROP-DERIVED, not constant system colours — the
      "AppKit solid fill" hypothesis is falsified. First-order laws from
      the two-backdrop cross-solve: regular/dark plate ~ 0.21 x backdrop
      + 5 per channel (consistent to 0.005 between red and blue);
      regular/light ~ white - 0.08 x (white - backdrop); clear scrims
      track the blurred backdrop hue. Instrument:
      analysis/measure_saturated_rt_plates.py.
- [x] Extreme-chroma material holdout (bonus from the saturated pair):
      walle scores 3.35/3.39 full-frame (interior ~8.0) over the
      near-primary red/blue fields against 1.46 coded / 0.95 natural —
      the measured limit of the transfer/chroma laws at saturation the
      fitting corpus never reached. A future chroma-extended transfer fit
      is the lead; receipts m1-transition-25G76-saturated-{red,blue}-
      sweep.json.

## User-gated (not agent-completable; awaiting the user)

These are not open work items for an agent session; they are the user's own
active work or decisions that need the user present. Recorded so the list
above stays honest about what "done" means.

- **The AGX clip-interpolator (divider) law** — the user's own live probe
  campaign (walle-agx-* on the M1; rulers captured through v7, per the
  ledger's sha receipts); agent sessions must not run captures or solvers
  against that directory mid-flight. Status per the ledger's 2026-08-13
  reframe: the production corpus does NOT depend on an unknown
  interpolator law — 176/188 channels reproduce from public inputs at
  zero offset with exact-rational-RNE varyings; what remains
  hardware-anchored is 9 setup-law channels (degenerate partner axes,
  1–2 low bits in slope/C words), the P25 selector table, and the
  sub-0.35-ulp24 divider epsilon that the corpus does not need. The
  campaign continues as refinement beyond corpus requirements; the
  "public inputs" flag concerns those residuals only.


## The road from 1.43 to 0.000 (opened 2026-08-19, session 193)

Licensed by the determinism theorem (TASK.md session 192 addendum):
Apple's settled renderer is bit-deterministic across sessions, so the
remaining 1.43 codes are pure implementation distance with no
measurement floor beneath them. Method per the GPU-bake result: match
the MECHANISM, verify on controlled fields, judge by the gates.

- [x] Crack the dither/quantization pattern — CLOSED session 193:
      Apple does NOT dither (flat interiors are single codes at
      >0.9999 dominant fraction; the ~0.5-code texture was plain
      rounding).  walle's triangular dither was injected noise and is
      removed (coded 1.43->1.37, natural 0.93->0.86).
- [x] The real blur mechanism — CLOSED session 194, in two moves:
      (1) the mip-chain hypothesis is FALSIFIED (it tied the
      two-Gaussian at the edge once the exact flat tables retired the
      fitters' free gain/offset; the -38%/-52% "win" was affine
      contamination); (2) the honest residual was an appearance-keyed
      ASYMMETRY, cracked as the FAR-FIELD TONAL WARP: the wide field
      runs in a power-warped code space (p 0.40 light / 1.34 dark),
      checker/flat/clear fixed points, edges 2.31->0.92 / 4.32->2.30,
      natural 0.86->0.85, coded tied.  SHIPPED as default
      (WALLE_GLASS_WIDE=gauss replays).  Instrument:
      analysis/fit_backdrop_space_mixture.py.
- [ ] The native transfer form — regular's flat law is a near-shared
      power (~0.84); the warp discovery says the pipeline has TWO
      tonal spaces, so re-derive the transfer's split across them
      (clear's exact affine already closed).  Known tension, session
      194: the edge-optimal constants (free joint fit = the `cascade`
      variant, edge rms 0.70/1.12) LOSE end-to-end (coded 1.39),
      while the sweep-optimal shipped constants leave edge rms
      0.92/2.30 vs the 0.43 noise floor — one constant set cannot yet
      satisfy both referees, so the warp model is still incomplete.
      Next leads: the warp's exact FORM (power vs sRGB-decode shape,
      testable on the edges), and its per-channel/chroma coupling
      (the structured-content per-channel constants are the candidate
      symptom; the saturated/coded captures are the instrument).
- [ ] The edge band's remaining texture — after the AA mean law, the
      structured residual on the boundary rows via the R8-keyed
      instrument.  MAJOR CUT session 195: the m-keyed sawtooth was
      walle's own lens step at the analytic circle — fixed by
      coverage-centroid sampling (measure_boundary_blend_curve.py is
      the referee; natural edges improved across the board, aaHump
      constants re-projected).  Remaining: the texture proper
      (12–17 rms, mask is corpus-exact so it is CONTENT sampled
      wrong at the boundary) and the R +6/+12 channel structure.
      Sharpened lead: the lens profile below depth 1 capture px has
      never been measured (walle extrapolates a diverging
      1/(u+12.62) exactly where the centroid samples land) — a
      sub-depth-1 lens instrument is the next capture.
- [ ] The chroma space of the far-field warp — the coded and natural
      holdouts disagree (full-channel flip3 vs luma/mild) and gray
      statics are structurally blind.  Rig instrument READY
      (lg-test 37a8c62: chroma-rc/chroma-il ladders + edges,
      --static-only) — BLOCKED on the M1 being reachable (down
      2026-08-20, no ping).
