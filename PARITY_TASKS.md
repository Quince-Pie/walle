# What parity still costs

Every number here is measured, not estimated. Scores are
`analysis/score_walle_transition_against_m1.py --material-progress 0.66`
(full / inside / worst, in 8-bit codes) over 68 settled states.

    capture      full   inside   worst      what it stresses
    coded        1.27     1.92    3.33      structured synthetic field
    natural      0.81     1.18    1.61      natural-statistics photographs
    sat-red      1.59     2.67    4.75      saturated blue -> red
    sat-blue     1.57     2.53    4.57      saturated red -> blue  (reversed pair)

Already at parity and not on this list: the reveal mask geometry (byte-exact,
100.0% at sha `8ac1bd7c`), the boundary coverage ramp (+0.007 px, sd 0.025),
and the whole `clear` variant, whose interior sits at its dither floor.

## The floor is not zero

`clear`'s interior reads 0.43 rms and that is **dither**, not material.
High-pass energy gives Apple a noise sigma of 0.235 - essentially pure 8-bit
quantization, 1/sqrt(12) = 0.289 - against walle's 0.268. Two independent
noises at those levels predict 0.36 codes of difference. So ~0.4 codes is the
practical floor for any renderer scored against an 8-bit capture, and the
target is parity *to the floor*, not 0.000.

## Where the remaining error is

Shares of total squared error, from `analysis/error_budget.py`.

    coded            regular = 91% of all squared error
                     regular 120-450 px      32%
                     regular 50-120 px       11%
                     regular 450-1100 px     20%
                     regular outside (shadow) 6.1%
                     all four boundary rings  4.4%

    sat-red          regular/dark 50-450 px  53%   <- the single largest cell
                     regular/light 50-450 px 17%

Two structural facts cut across those cells:

* **the lever arm.** Deep inside, the residual is flat where `narrow` and
  `wide` agree and ~3x higher where they disagree. CAUSE FOUND and the linear
  part shipped (P2-1): it is the mixture weight, and the surface proves the
  transfer exact along the diagonal. A quadratic term remains, real at large
  |narrow - wide| and sign-flipping between appearances, deliberately
  unfitted.
* **the transfer at the extremes.** Binned by output luma at depth >= 450,
  natural/regular/light reads 1.44 / 0.79 / 0.71 / 1.64 across luma
  175-195 / 195-215 / 215-235 / 235-256 while `clear` reads ~0.85 flat. Mid
  range, `regular` is ALREADY at `clear`'s floor; the excess is at the ends
  of the backdrop range, which is where the flat ladders say the polynomial
  is weakest.

---

## P0 - foundational, do first

**P0-1. Regenerate the decoded parameter artifact at real element sizes.**
`analysis/results/apple-material-parameters-26.6.1.json` holds the `_nil`
table - the no-geometry default - not the values at the sizes the reveal
actually uses (`refraction.innerHeight` 12.0 against the real 20.0,
`faceEffects.ycc.black` 0.85 against the real 0.50). Session 201 already
mis-fitted the lens once by trusting `_nil`. Every parameter claim should be
regenerated from the size-dependent table, with the size law stated per field.

**P0-2. One shared, typed reader for the decoded blobs.**
The field table's type strings are `Double` and `Float`. A reader that tests
for `CGFloat` silently reads a Double's low four bytes as a Float - zero for
small round values, garbage for large - which cost a false "the offsets are
wrong" conclusion. Put the reader in one place so no instrument can repeat it.

**P0-3. Fold the saturated captures into the standing referee.**
`sat-red`/`sat-blue` are the same two wallpapers reversed, so any chroma law
must survive the sign flip; they are ~3x more sensitive to the chroma seam
than coded or natural, and they caught a shipped constant that was 20% low.
Score all four captures on every change, not two.

**P0-4. Cache the wide fields in-repo.**
The sigma-330 blur is 2455 taps and gets recomputed by every instrument;
several runs this session took 10+ minutes on it alone. One cached helper.

## P1 - named, measured defects

**P1-1. The dark shadow penumbra falls off too slowly.** Transmittance outside
the boundary: at 10-18 px Apple reads 0.9404 and walle 0.9318; the gap runs
from 4 to 70 px and peaks near 1.1 codes. Light is within 0.4 codes
everywhere. Worth a few hundredths of a code overall - small, but it is a
*named* defect with decoded parameters (`shadow.opacity` 0.25,
`shadow.ycc.saturation` 1.80 light / 1.00 dark) and no ambiguity about the
mechanism. Referee: `analysis/measure_outside_shadow_transmittance.py`.

**P1-2. The transfer at the extremes of the backdrop range.** See above. The
decoded face law is affine and cannot wobble; walle's 35/56-term polynomial
does, at the ends. Fitting the affine law against the polynomial was a tie
*in the middle* of the gamut - the comparison was never run at the ends
where the difference should live. Re-run it there before deciding.

**P1-3. Dither amplitude.** walle 0.268 against Apple 0.235. Matching it buys
~7% of the floor. **Do not simply reduce the amplitude** - that trades banding
on smooth gradients for a metric win. Only worth doing if a better-shaped
(blue-noise) pattern gets the same visual result at lower energy.

## P2 - measured structure with no mechanism yet

**P2-1. The lever-arm dependence. DONE (commit 3071a73).** The surface read
settled it: the residual is zero along `narrow == wide` - so the transfer is
exact - and linear across it, so the error is the mixture weight. The linear
coefficient divided by the transfer's luma slope predicted the correction, and
the referee confirmed the predicted value is the optimum (half too little,
twice too much). Shipped 0.8846 -> 0.8983 light, 0.5164 -> 0.5502 dark.
REMAINING: the quadratic term is real at large |narrow - wide| and flips sign
between appearances, so a pure reweighting is not the whole story. Left
unfitted deliberately.

**P2-2. The coded capture's uniform excess.** coded/regular/light reads
1.54-2.31 across its whole narrow output range where natural reads 0.71
mid-range. Something about the synthetic field is not in the natural set.
Identify it before treating either as representative.

**P2-3. The seam weight is content-dependent.** The shipped constant is a
compromise: the required weight at the boundary is 1.03 (saturated), 1.11
(natural), 1.66 (coded). A material law cannot depend on content, so a term
is still missing - the composited-screen candidate for it was falsified.

**P2-4. The saturated dark 50-450 px band** - 53% of that capture's error,
rms 6.5-9.6. Keeps its size, has lost its explanation. Its DC part reverses
sign with the wallpaper pair so THAT part is seam-family, but DC is only 35%
of the energy at 120-250 px and the seam basis - optimally gained - removes
about a tenth of the variance. Ruled out for this band: the kernel geometry
(exact disc ties or loses against the straight-edge approximation, #21), a
global chroma scale, a chroma-magnitude law (coded light and dark trend in
OPPOSITE directions on the same wallpaper, #22), and the transfer (the
surface proves it exact along the diagonal).
NEXT INSTRUMENT, not next law: the luma surface cannot diagnose this band
because the saturated wallpapers' luma range is too narrow to populate the
grid. It needs a chroma-space analogue - a real 2-D read over the chroma
plane, not a projection onto one direction, which is precisely what failed.

## P3 - decode work

**P3-1. Settle `blur.distances` / `blur.opacities` semantics.** Four distances
(-size/2, -1, 0, 0) against five opacities (1.0, 0.5, 0.5, 1.0, 1.0), on every
case and both variants. Reading them as compositing strength is falsified
(high-pass ratio 1.00-1.02). Reading them as a depth-keyed profile is a guess.
This needs the consumer code, not the struct.

**P3-2. Reconcile `blur.radius` with walle's fitted sigmas.** Apple: 8.0
regular / 2.0 clear, `backdropScale` 0.25 / 0.5. walle: sigma 14.188 / 0.7251
capture px - a ratio of 19.6 where Apple's parameters imply 4 or 8 depending
on how `backdropScale` enters. `clear` is at the dither floor, so walle's
clear sigma is right in practice; the conversion law is therefore not what it
looks like, and pinning it would let the narrow blur be *decoded* rather than
fitted.

**P3-3. The mix fraction.** These blobs are sampled at an animation fraction
(`backdropScale` alone runs 2.0 -> 4.0 across fractions 0 -> 1). Every constant
read so far is implicitly at one fraction. Establish which, and whether the
settled-state values are the ones being used.

## P4 - verification

**P4-1.** Reveal gate 100.0% at sha `8ac1bd7c` and live transition gate pass,
on every change. Both currently green.
**P4-2.** `clear` must stay byte-identical under any `regular`-only change.
**P4-3.** Every shipped mechanism keeps an env escape (`WALLE_SCREEN_CHROMA`,
`WALLE_SCREEN_BACKDROP`, `WALLE_SHADOW`, ...) so it can be replayed off.

---

## Ledger of what has been ruled out

Twenty mechanisms, each on a holdout it did not choose: mip chain; gamma
family; luma-only warp; pointwise per-channel; v^p(Y) slice family; scalar
luma-gated (twice); cube-only cross-channel V; per-variant refraction band;
Apple's 20 pt band; directional rim transplant; wide-layer size scaling;
radial displacement; clipped backdrop; boundary coverage geometry; the
decoded affine law as a transfer replacement; face-plus-bleed two-layer
blend; depth-keyed wide-field luma weight; near-edge chroma tint and
k*chroma(wide); `backdropScale` resampling; gamut clipping; Apple blurring
the composited screen; chroma-magnitude dependent saturation; and
`blur.opacities` as compositing strength.
