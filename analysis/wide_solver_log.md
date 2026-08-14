# Wide-path C-product law: solver log

Baselines to beat (proven narrow law `RNE24(rna27(P))` applied everywhere):
`tt4 13876/18001, tt3 18001/18001, tt1 2300/2610`.
Earlier campaign's best biased-floor: tt4 ~14141, tt1 2124.

Scoring: every capture is reduced to an exact admissible interval
`[xlo, xhi]` for `X = V - P`, where `V` is the value fed to the narrow-law
export (`analysis/wide_solver_xmap.py` computes the preimage of the
captured word under `narrow`).  A candidate hits iff `V - P` lands in the
interval.  Harness: `wide_solver_fast.py` (exact) / `wide_solver_np.py`
(vectorised, identical results).

## Established measurements (this session)

- Excess scales with the OUTPUT granule: `E ~ (9/64) * 2^cut` at
  `dm = 2^23`, for every d_o row (verified cut = 7..12).  Equivalently
  `E48 = 9 * 2^17` in the 48-bit normalised product frame.
- Excess reaches +-1 output ulp; cells whose product is exactly
  representable can still export P +- 1 ulp (tt4 has 385 cells at +1 and
  23 at -1), so this is arithmetic, not a rounding-mode question.
- The deviation matrix (`wide_solver_matrix.py`) is periodic in dm with
  period 2^13 for the large-d_o rows.
- Excess is NOT a function of: dm alone, dm mod 2^k alone, d_o alone,
  (dm, bl(d_o)), P mod 2^k for k <= 20.  `P mod 2^24` is the only coarse
  key with no infeasible group, and it is nearly singleton (17777 groups
  for 18001 cells), so it carries little evidence.

## Falsified families (best score, all with tt3 = 18001/18001)

| family | best tt4 | best tt1 | notes |
|---|---|---|---|
| narrow law everywhere (baseline) | 13876 | 2300 | reference |
| W-bit datapath truncate + constant K (`wide_solver_sweep1.py`), W 27..34, 5 modes, K 0..64 | 14850 (W=29, rne, K=4) | 2210 | tt1 regresses below baseline |
| segmented multiplier (`wide_solver_seg.py`): split dm / d_o / didx24 at bit 8..16, each partial quantised to 22..30 bits in 5 modes, summed | 14504 (dm, s=11, hi rna23) | 2240 | the lead's prime direction; 28755 tt3-preserving combos swept |
| operand perturbation `E = eps(dm) * d_o` (and * didx24, * P, * 2^cut, * 2^bl(d_o)); `E = eta(d_o) * dm` | infeasible | - | interval intersection: 266..327 of 383 dm groups infeasible |
| sawtooth ramp in P: `E = (q - ((P>>j) - wrap mod 2^13)) * 2^(cut-13)`, j 0..12 and cut-relative | 14903 | 2252 | product-based ramp is worse than dm-based |

## Live lead

Sawtooth ramp in dm (`wide_solver_saw.py`, `wide_solver_saw2.py`):
`E = (q - ((dm - wrap) mod 2^13)) * 2^(cut-13)` applied only when
`bl(P) > 30`.  Best `q = 5728, wrap = 3712`: **tt4 16672**, tt3 18001,
tt1 2124.  Best tt4 seen so far, but tt1 falls below baseline, so the
ramp is capturing something that is dm-offset-specific to tt4 (where dm
only ever lies in `[2^23, 2^23 + 32512]`).  The value at `dm = 2^23`
is `1248/8192 ~ 9/64`, matching the measured constant.

---

# Solver-2 section (independent line of attack; append-only below this line)

Tooling (all exact integer/Fraction arithmetic, no float, no `round()`):

- `wide_solver_xmap.py` — inverts the narrow law per capture to the exact
  admissible interval `[xlo, xhi]` for `X = V - P`.  (`narrow` is monotone,
  so the preimage of a captured word is one contiguous integer interval.)
- `wide_solver_dev.py` — integer deviation `D` in output granules.
  tt4 `{-1: 1096, 0: 13876, +1: 3012, +2: 17}`, tt1 `{-1: 260, 0: 2300,
  +1: 50}`, tt3 all zero.  The wide path never departs from the narrow law
  by more than 2 result lsbs.
- `wide_solver_thresh.py`, `wide_solver_rowfit.py`, `wide_solver_rows.py` —
  per-row / per-parity threshold brackets.
- `wide_solver_ceiling.py` — **optimal** score of a whole family by exact
  interval stabbing, without sweeping candidates.

## 1. Exact form of the wide-path rounding constant

Bracketing round-up thresholds on tt4's low-dm block (`dm = 0x800000+t`,
t = 0..255), with `v = 2^(bl(P)-30)` and M the 24-bit result mantissa:

    hardware   rounds up iff drop >= 27*v (M even)  or  drop >= 19*v (M odd)
    narrow law rounds up iff drop >= 36*v (M even)  or  drop >= 28*v (M odd)

Both shifted by exactly `9*v`, with ZERO misclassifications at bl=31,32 and
<= 15/2048 through bl=35.  Decomposed through the two rounding stages:

    Q27 = floor( (P + 13*2^(bl-30)) / 2^(bl-27) )
    C   = RNE24( Q27 * 2^(bl-27) )

i.e. **the 27-bit stage's rounding constant is 13 ulp30 (13/8 ulp27) rather
than rna27's 4 ulp30 (1/2 ulp27)** — a +9 ulp30 compensation on top of
round-half-away.  That is the true origin of the "13"; measured against the
correct narrow baseline the excess is 9/64 of a result ulp, not 13/64.
Closed form `narrow(P + 9*2^(bl(P)-30))` scores tt4 14850 / tt3 18001 /
tt1 2208.  Miss rate by region: tt4 low-dm block bl=31..35 is
0%, 0%, 0.2%, 0.7%, 0.7%; bl=36 is 16.4%; high-dm block 7% -> 45%.

## 2. *** CEILING THEOREM — this bounds the sawtooth lead ***

Any rule of the form `C = narrow(P + f(key) * scale)` is pinned per key by
interval stabbing, so its best possible score is computable exactly.  With
f an ARBITRARY function (sawtooth, lookup table, anything):

| key | scale | tt4 ceiling | tt3 | tt1 ceiling |
|---|---|---|---|---|
| dm | 2^(bl-30) | **17092/18001** | 18001 | 2546/2610 |
| dm | d_o | **17294/18001** | 18001 | 2578/2610 |
| dm mod 2^13 | 2^(bl-30) | 17083 | 18001 | 2546 |
| dm mod 2^13 | d_o | 17273 | 18001 | 2578 |
| (dm, bl) | 2^(bl-30) | 17413 | 18001 | 2568 |
| (dm, bl) | d_o | 17710 | 18001 | 2600 |

**No function of dm alone can reach 18001 on tt4 or 2610 on tt1**, whatever
its shape.  The dm-sawtooth lead is therefore capped at 17092 (granule
scaling) / 17294 (d_o scaling) and cannot be the final law.  Note also that
d_o scaling beats granule scaling at every key, which favours reading the
deviation as an operand perturbation `dm_eff = dm + delta(dm)` rather than a
granule-relative bias.  Even a full `(dm, bl)` lookup table tops out at
17710.  Keys with finer d_o resolution (`d_o>>8`, `didx24>>16`) do reach
~18000, but those keys are near-injective on tt4's 47 rows (~1 cell per
group), so those numbers are fitting artefacts, not evidence.

## 3. Additional falsifications (all with tt3 held at 18001 unless noted)

| family | tt4 | tt1 | verdict |
|---|---|---|---|
| `narrow(quant_T(dm*disp))`, T absolute in the subpixel frame, T=0..19 x 5 modes | best 14220 | 2228 | FALSIFIED |
| truncated multiplier `((dm*didx + K) >> T) << T`, T=7..17, K solved exactly by interval intersection | — | — | FALSIFIED: K-intersection EMPTY for every T; only 34.2-34.8k of 38612 captures reachable by any single K |
| cascade `P -> W1(mode) -> rna27 -> RNE24`, W1=25..39 x 5 modes | 14288 (tt3 falls to 17937) | 2262 | FALSIFIED |
| segmented dm, low partial `L*didx` rounded to W bits; s=9..16, W=14..27 x 4 modes | 14611 | 2182 | FALSIFIED |
| segmented didx, low partial `dm*DL` rounded to W bits; s=8..16, W=14..27 x 4 modes | 14278 | 2278 | FALSIFIED — and structurally impossible: every tt4 row has `d_o = 1 (mod 128)`, so the low half of didx is the SAME constant 64 in all 47 rows and cannot produce row-dependent deviation |
| **column-truncated array** `sum_{i+j>=T} dm_i*didx24_j*2^(i+j) + K` (`wide_solver_array.py`), T=12..21, K solved exactly | — | — | FALSIFIED: K-intersection EMPTY for every T; only 25.5-29.0k of 38612 reachable.  This family is attractive because it explains tt3 structurally (tt3's normalised displacement has >= 18 trailing zeros, so no partial product lands in a dropped column for any T <= 18) — but an ABSOLUTE compensation constant cannot coexist with the measured compensation, which is granule-relative.  Conclusion: the compensation is applied POST-normalisation, in the rounder. |

## 4. Two facts worth not re-deriving

- **Sign matters.** The tt1-vs-tt3 collision at `dm=0x800004, d_o=13`
  (identical product P, exact half tie, opposite resolution) is a sign
  effect: tt1's `d_o=13` row is tileY=19, a *negative* displacement.  tt1
  has 58 negative-sign cells; tt3 and tt4 have none.  Ties round toward
  -infinity.  It accounts for exactly 2 cells — do not spend time on it.
- **No tt4 row admits a constant X**, nor constant X/granule, nor constant
  X/P (all 47 rows infeasible), so per-row constants are dead.
