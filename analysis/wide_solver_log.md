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

## Best rule found on this line of attack

Sawtooth ramp in dm (`wide_solver_saw.py`, `wide_solver_saw2.py`):
`E = (q - ((dm - wrap) mod 2^13)) * 2^(cut-13)` applied only when
`bl(P) > 30`.  Best `q = 5728, wrap = 3712`: **tt4 16672**, tt3 18001,
tt1 2124.  Best tt4 of any closed form tried, but tt1 falls below the
narrow-law baseline of 2300, so the ramp is fitting something that is
dm-offset-specific to tt4 (where dm only ever lies in
`[2^23, 2^23 + 32512]`).  The value at `dm = 2^23` is `1248/8192 ~ 9/64`,
matching solver-2's measured compensation constant.

Variant that strictly beats the campaign baseline on all three
simultaneously: `q = 5568, wrap = 3840` -> tt4 16668, tt3 18001, tt1 2128.
Reported for completeness only: **the plain narrow law still beats every
one of these on tt1 (2300)**, so no single rule found this session
improves all three datasets at once.

## *** CEILING THEOREM IS EXPORT-INDEPENDENT (extends solver-2 section 2) ***

Solver-2's ceiling assumes `hw_word = encode(narrow(V))`.  If the wide
path rounded differently the preimage - and hence the ceiling - would be
wrong.  `wide_solver_export.py` recomputes the preimage of every wide
capture (bl >= 31) under **24 different export stages** (outer rne/rna/
rtz/rup at 24 bits, optionally preceded by rna/rne/rtz/rodd/rup at 27
bits) and re-runs the stabbing ceiling for a bias keyed on dm:

| export | tt4 wide ceiling | tt1 wide ceiling |
|---|---|---|
| rne24(rup27) | 17307 | 2410 |
| **narrow = rne24(rna27)** | **17294** | **2412** |
| rne24 direct | 17067 | 2396 |
| rtz24 direct | 16664 | 2382 |
| rup24 direct | 16639 | 2360 |

No export choice moves the ceiling materially, and none reaches 18001.
**The bias-on-dm family is dead regardless of the export rounding.**  The
narrow export is also (near) the best of the 24, which independently
corroborates `RNE24(rna27(.))` as the real export stage.

## Extra keys ceilinged (`wide_solver_ceiling2.py`)

Nothing reaches 18001.  Keys built from the result mantissa `M = P >> cut`
are legitimate low-dimensional keys depending on BOTH operands, and they
still fail:

| key / scale | groups (tt4) | tt4 ceiling | tt1 ceiling |
|---|---|---|---|
| (dm, bl) / d_o | 2298 | 17710 | 2600 |
| (dm mod 2^13, M mod 4) / d_o | 1107 | 17518 | 2600 |
| (dm mod 2^13, M mod 2) / d_o | 570 | 17372 | 2592 |
| dm / d_o | 383 | 17294 | 2578 |
| dropped = P mod 2^cut / d_o | 3143 | 16229 | 2584 |
| d_o / anything | 47 | 15528 | 2418 |
| phi30 (top bits below the 30-bit cut) / 2^(bl-30) | 64 | 15040 | 2394 |

## Additional falsifications (mine; tt3 = 18001 unless noted)

| family | tt4 | tt1 | verdict |
|---|---|---|---|
| sawtooth ramp in P: `(q - ((P>>j) - wrap mod 2^13)) * 2^(cut-13)`, j=0..12 absolute and cut-relative | 14903 | 2252 | FALSIFIED - a product-based ramp is strictly worse than a dm-based one, so the period-2^13 structure really lives in dm |
| **array truncation + granule-relative constant** (`wide_solver_guard.py`): drop every column below T of `dm * (d_o << (24-bl(d_o)))`, then add `c/64` of a result ulp; T=8..23 x rtz/rne/rna x c=0..64 | 14084 (T=19, rna, c<=1) | 2290 | FALSIFIED.  This was the one combination solver-2's section 3 left open (they falsified absolute-constant truncation, and separately measured the constant to be granule-relative).  It also structurally preserves tt3: for T <= 18 the drop position `u = T - 24 + bl(d_o)` is <= 0 for every tt3 row, so tt3 stays exact by construction rather than by fiat |
| information test (`wide_solver_info.py`): is the export a function of the top W bits of P, W=24..40 x rtz/rne/rodd | - | - | NO POWER - the three datasets have almost no product collisions (37288 distinct keys for 38612 captures), so this cannot discriminate.  Do not repeat it |
| **radix-4 Booth, per-partial truncation, floating window** (`wide_solver_booth.py`): recode dm or d_o into signed digits, truncate each partial at `bl(P)-W`, sum, add `c/64` result ulp; recode in {dm, d_o} x W=28..38 x c=-32..64, 2134 tt3-preserving combos | 14850 | 2218 | FALSIFIED - and instructively so: the optimum is reached at every large W, i.e. **the truncation contributes nothing and the entire gain is the `c = 9` constant**.  Truncated Booth degenerates to `narrow(P + 9*2^(bl-30))`.  I ran this because the residual pattern pointed at Booth; it does not survive |

## *** WHERE THE REMAINING STRUCTURE IS (sharpest counterexamples) ***

Take the **best possible** `f(dm)` (interval-stabbing optimum, solved
jointly over tt4+tt1 wide cells by `wide_solver_fdm.py`; it scores tt4
17188 / tt1 2412 and is a 383-entry table, not a law).  Its 845 residual
misses are not uniform - they concentrate exactly where a Booth/carry
recoding of dm would misbehave:

- **`dm = 0x801000` misses all 47/47 tt4 rows.**  This is the single
  sharpest counterexample in the corpus: `dm - 2^23 = 4096 = 2^12`
  exactly, and *no* perturbation value works for it at any d_o.  Any
  candidate law should be checked against this dm first.
- Next worst dm, with their bit patterns: `0x80000f` (24/47, low bits
  `0b1111`), `0x8000ff` (22/47, `0b11111111`), `0x800008` (11/47, `2^3`),
  `0x8000c1` (7/47, `0b11000001`), `0x80003f` (6/47, `0b111111`),
  `0x800041` (6/47, `0b1000001`).  **Runs of ones and isolated powers of
  two** - the classic radix-4 Booth recoding boundaries.
- By product width: tt4 bl=36 misses 9.0%, bl=31..35 miss 1.3-3.7%,
  tt1 bl=31,32 miss 0%.  The failure grows with the product width.
- By row: tt4 d_o = 4993..6017 (tileY 55..63, the largest displacements)
  account for 477 of the 553 bl=36 misses, ~50/383 each; every other row
  is <= 26/383.

Together with the +-1-ulp magnitude, this says the residual mechanism is a
**signed-digit (Booth) recoding of dm whose partial-product signs
interact with d_o** - not any additive bias.  Note the earlier campaign's
Booth sweeps (TASK.md later-36/37) predate both the proven narrow export
and this interval-stabbing harness, so re-testing Booth *with the narrow
export and a granule-relative compensation* is not a repeat.

## Two dead ends worth not repeating

- The proven narrow law is proven only over **tt3's range, bl 24..29**.
  tt1 contains bl=30 cells and they are NOT all exact (6/54 fail), plus
  8/54 at bl=29 and 2/52 at bl=27.  Do not assume bl <= 30 is safe.
- Cross-dataset collision audit: of 19 `(dm, d_o)` pairs shared between
  datasets, exactly one disagrees (`dm=0x800004, d_o=13`, tt1 vs tt3) -
  the negative-displacement sign effect solver-2 already isolated.  So
  apart from sign, the law IS a function of `(dm, d_o)`; the scale/
  alignment of the capture does not enter.

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

## Track B (operand/frame)

Files `wide_solver_B_*.py`.  All scores exact, all three datasets.

### B1. Fixed-position injection in the dm x didx24 frame — BEST STRUCTURE

`wide_solver_B_frame.py`.  didx24 = odd part of the displacement normalised
to 24 bits; `sh_f = 24 - bl(odd(d_o)) - tz(d_o)`, so `frame = P << sh_f`.
The array drops columns below a fixed frame bit T and injects K there:

    C = narrow( P + K * 2^(T - sh_f) ),   bias = 0 when sh_f > T

**T=17, K=9: tt4 14850, tt3 18001, tt1 2226.**  Same tt4 as the
result-relative form, +18 on tt1, and — the real gain — **tt3's exactness
becomes automatic instead of asserted**: every tt3 displacement has
`bl(odd) + tz <= 6`, hence `sh_f >= 18 > 17`, so no tt3 column is ever
dropped.  No `bl(P) <= 30` side condition is needed.
T=16/K=18 is the same law one bit down and scores identically on tt4.

### B2. *** THE bl(P) <= 30 GATE IS FALSIFIED ***

The brief's "narrow law is PROVEN for bl(P) <= 30" is an artefact of tt3's
displacement alignment, not a law about product width.  tt1 contains 8
distinct cells (16 counting the 0.5x twins) with `bl(P) = 27..30` that
deviate from the narrow law by a full granule:

| dm | d_o | bl | cut | drop | D | sh_f |
|---|---|---|---|---|---|---|
| 0x800010 | 51 | 29 | 5 | 16/32 | -1 | 18 |
| 0x800008 | 51 | 29 | 5 | 24/32 | -1 | 18 |
| 0x800004 | 51 | 29 | 5 | 12/32 | +1 | 18 |
| 0x80000F | 51 | 29 | 5 | 29/32 | -1 | 18 |
| 0x800010 | 115 | 30 | 6 | 48/64 | -1 | 17 |
| 0x800003 | 115 | 30 | 6 | 25/64 | +1 | 17 |
| 0x80000F | 115 | 30 | 6 | 61/64 | -1 | 17 |
| 0x800004 | 13 | 27 | 3 | 4/8 | +1 | 20 |

tt3 is exact at the same bl and the same dm values because tt3 never probes
`d_o > 47`.  Note d_o = 51 has `sh_f = 18`, identical to tt3's d_o = 47 —
the frames are structurally identical — so the discriminator is the RAW
subpixel trailing-zero count (tt1 z=7 vs tt3 z=13), confirming the lead's tz
clue.  The last row is the known sign cell (tileY=19, negative displacement).

### B3. *** tt1 AND tt4 REQUIRE OPPOSITE-SIGN COMPENSATION ***

Deviation sign is inverted between the two regimes:
tt4 `{+1: 3012, -1: 1096, +2: 17}` but tt1 `{-1: 260, +1: 50}`.
Scanning a single global granule-relative constant `K * 2^(bl(P)-30)` over
K = -16..+16 (no gate) gives best tt4 at K=+9 (14850) and best tt1 at
K=-4/-2 (2308, barely over its 2300 baseline); tt3 needs K=0 exactly and
falls to 17681 by K=-4 and 16561 by K=+9.  **No single compensation constant
can serve tt4 and tt1 at once.**  This is the hard obstruction behind every
"trades tt4 against tt1" result in the log, including the {29,27,26} cascade.

### B4. Falsified on this track

| family | tt4 | tt3 | tt1 | verdict |
|---|---|---|---|---|
| tz-gated granule-relative bias, `bias = K*2^(bl-30)` iff `tz(disp) < T`; T=7..13, K=0..25 | 14850 | 18001 | 2300 | best TOTAL to date (35151, T=7 K=9) but hollow: T=7 works only by excluding tt1 (z=7) from the bias entirely, leaving it at its narrow-law baseline.  T=8..13 (tt1 biased) gives tt1 2212. |
| absolute-frame injection magnitude (constant bias in P units) | — | — | — | FALSIFIED by scaling: the measured bias grows x32 across bl=31..36, so the injected constant must be weighed on the normaliser side, not in the subpixel frame.  Confirms the earlier T-sweep ceiling of 14220. |
| mantissa-product overflow as the tt1 discriminator (`bl(dm*didx24) == 48`, which halves a fixed injection's relative weight) | — | — | — | FALSIFIED: tt1's deviations sit in the NON-overflow set (246 of 260 D=-1 cells have no overflow; the 216 overflow cells contribute only 14). |
| gate on `sh_f <= T` for tt1 | — | — | 2226 | over-fires: 50 tt1 cells have `bl(P) <= 30` with `sh_f <= 17`, of which only 6 actually deviate. |

### B5. Killer cell status

`dm=0x800C00, d_o=1793` (bl=34, drop=0) still misses under every Track B
law: they all predict `P + 9*2^4 = P + 144`, which rounds to M, whereas the
hardware exports M-1 (X in [-1536,-512]).  Consistent with the integrator's
"exact-on-grid mistreatment" profile — and with the dm-dependent term that
the ceiling theorem (solver-2 section) shows cannot be a function of dm
alone.  Any winning candidate must produce a NEGATIVE excursion of about one
granule at drop = 0 for this dm while keeping +9 as the mean.

### B6. *** CROSS-DATASET COUNTEREXAMPLE: c IS NOT A FUNCTION OF dm ***

tt4 and tt1 share 19 dm values, and they differ ONLY in the subpixel
trailing-zero count of the displacement (tt4 z=6, tt1 z=7).  Intersecting
the admissible interval for `c` (bias in units of `2^(bl(P)-30)`) over each
dataset's rows separately:

| dm | tt4 (z=6) requires c in | tt1 (z=7) requires c in | joint |
|---|---|---|---|
| 0x801000 | **[36.00, 91.50]** | **[-32.00, 32.00]** | ***EMPTY*** |
| 0x800040 | [4.00, 19.75] | infeasible | — |
| 0x800080 | [4.00, 19.88] | infeasible | — |
| 0x800100 | [4.00, 19.94] | infeasible | — |
| 0x800400 | [-28.00, 3.97] | [-4.00, 27.94] | ok |
| 0x800800 | [-28.00, 3.98] | [-28.00, 27.97] | ok |

**dm = 0x801000 is a hard input-level contradiction**: both datasets admit a
constant on their own, and the two constants are disjoint.  This proves,
without any family-ceiling argument, that the compensation coefficient
cannot be a function of dm — a ramp/sawtooth/table in dm fitted on tt4 is
guaranteed to be wrong on tt1, which is exactly why the tt4-fitted sawtooth
lands at tt1 2124, below the 2300 narrow-law baseline.  Any dm-indexed ramp
must be parameterised by the frame (tz is the only systematic difference).

Note also that tt1 alone leaves 10 of its 29 dm values infeasible for a
constant c, so tt1 needs finer-than-dm structure independently of tt4.

Periodicity caveat: tt1's dm values with `dm = 0 (mod 8192)` do share a
common c, but vacuously — every one of them has the wide interval
[-32, 32], because those dm make P land on the grid where the narrow law is
already right.  tt1 cannot confirm or refute period-2^13 periodicity across
the binade; the informative dm are the low-bit-pattern ones.

### B7. tz-parameterised frame ramp — FALSIFIED

`wide_solver_B_tzramp.py`.  The obvious fix implied by B6: move the ramp's
argument from dm into the raw subpixel frame, `u = ((dm << z) - wrap) mod
2^Wf`, so the modulus becomes `2^(Wf - z)` — 2^13 for tt4 (z=6, reproducing
the period seen there) and 2^12 for tt1 (z=7).  Amplitude normalised to one
output granule, bias still granule-relative, gated off when no columns drop.

Swept Wf = 18/19/20 x wrap x q on a coarse grid.  Best: Wf=19,
**tt4 16463, tt3 18001, tt1 2120** — it recovers the tt4 gain (a finer grid
would approach the 16672 of the dm-indexed ramp) but tt1 still lands *below*
its 2300 narrow-law baseline.  So simply making the sawtooth's MODULUS
tz-dependent does not reconcile the two regimes; whatever varies with the
frame changes the ramp's sign/phase, not just its period.  Combined with B3
(tt4 wants +9, tt1 wants -4) the next thing to try is a tz-dependent SIGN or
phase offset, not a tz-dependent period.

### B8. NEW HARDWARE CAPTURES: five tz classes (M1, this session)

Generator `wide_solver_B_gen_plan.py`, loader `wide_solver_B_data.py`.
Each capture clones tt4's geometry exactly (x-edge 2048, height 4096, det a
power of two, same 383-word dm scan, rows ty=17..63) and moves ONLY the
anchor's subpixel position, by at most two pixels.  With tiles 8192 subpixels
apart and `ay = 2^k * odd`, `disp = 2^k * (2^(13-k)*ty - odd)` has tz = k for
every row and d_o odd — so the anchor alone sets the class.

| class | anchor y | ay | ay factorisation | d_o range | bl(d_o) | bl(P) | scale | capture.raw sha256 |
|---|---|---|---|---|---|---|---|---|
| tz=3 | 511.96875 | 131064 | 2^3 * 16383 | 1025..48129 | 11..16 | 34..39 | -17 | 4645bec9b5ab13c638639b63c4a93418e05182fa39ac9d5026bccc8620f524a5 |
| tz=4 | 511.9375 | 131056 | 2^4 * 8191 | 513..24065 | 10..15 | 33..38 | -16 | 85f0bbcd30481df7ef2240e1235f574c48f56bf3c2540a4cd0e29695097f94f7 |
| tz=5 | 511.875 | 131040 | 2^5 * 4095 | 257..12033 | 9..14 | 32..37 | -15 | f4b39ca2348cd3cfce3b3a81d58490f6f3b676c8cd53c61bb0f1d61d563aca1a |
| tz=8 | 511.0 | 130816 | 2^8 * 511 | 33..1505 | 6..11 | 29..34 | -12 | 32847213979bffd9976322c2841225f1ab0a8753b1bbb53deb70e7bb03224ae9 |
| tz=9 | 510.0 | 130560 | 2^9 * 255 | 17..753 | 5..10 | 28..33 | -11 | 31e99c340520579a4ef62e8efd08d678d0bfcd29a4bd8863b9656c05ef9647ae |

All five value scales were CALIBRATED from the captures, not assumed, and
every one landed on the predicted `tz - 20` (the tt4-cloned det), which
validates the geometry.  Narrow-law baselines: tz3 15442, tz4 15165,
tz5 14705, tz8 14829, tz9 14379 (of 18001 each).

**The confound is broken.** `cut + tz = bl(disp) - 1` is 13..18 in every
capture including tt3/tt4/tt1, and bl(d_o) ranges now overlap heavily across
classes (bl(d_o)=10 occurs at tz=4,5,8,9 and tt1's 7; bl(d_o)=11 at
tz=3,4,5,8, tt1 and tt4).  So tz can be varied with bl(d_o) held fixed.

### B9. *** THE +9 COMPENSATION IS SPECIFIC TO tz=6, NOT UNIVERSAL ***

Every tz-class capture uses tt4's dm scan, so dm is pinned near 2^23 in all
of them and any difference is attributable to tz alone.  Best global
compensation `narrow(P + K*2^(bl(P)-30))` per class (K swept -40..40):

| class | tz | narrow law | best K | best | gain |
|---|---|---|---|---|---|
| tt3 | 13 | 18001 | 0 | 18001 | +0 |
| tz3 | 3 | 15442 | -4 | 15490 | +48 |
| tz4 | 4 | 15165 | 0 | 15165 | +0 |
| tz5 | 5 | 14705 | -7 | 15201 | +496 |
| **tt4** | **6** | 13876 | **+9** | 14850 | +974 |
| tt1 | 7 | 2300 | -2 | 2308 | +8 |
| tz8 | 8 | 14829 | -4 | 15213 | +384 |
| tz9 | 9 | 14379 | -4 | 14911 | +532 |

Threshold brackets on the low-dm block agree and are sharp where the
threshold model holds at all: tz=6 gives +9.000 ulp30 (range [+8.75, +9.00]
over five clean brackets) and tz=5 gives -7.06 (range [-7.38, -7.00] over
ten).  tz=8 and tz=9 produce NO separable bracket at any cut — the threshold
model fails outright there.

Best K per (tz, cut), "." = no K explains >50% of the group:

| tz \ cut | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | | | | | | | -13 | -5 | -5 | -3 | 0 | 0 |
| 4 | | | | | | 1 | 1 | 1 | 0 | -5 | 0 | |
| 5 | | | | | -7 | -7 | -7 | -7 | -7 | 1 | | |
| 6 | | | | 9 | 9 | 9 | 9 | 9 | -9 | | | |
| 7 | | | | | -11 | -13 | -1 | -1 | | | | |
| 8 | | -7 | -7 | -7 | -7 | -7 | -4 | | | | | |
| 9 | -5 | -7 | -7 | -13 | -13 | -4 | | | | | | |
| 13 | 0 | 0 | | | | | | | | | | |

Two readings, both important:

1. **K is essentially constant along a row (fixed tz) and varies wildly down
   a column (fixed cut).**  At cut=9: K = +1 (tz4), -7 (tz5), +9 (tz6),
   -4 (tz9).  Same dm scan, same bl(d_o), different tz, different answer.
   So the compensation is a function of the FRAME, and tz is a real
   parameter of the law — this is the empirical closure of the tz question
   that tt1 alone could not give (tt1 differs from tt4 in both tz and dm
   distribution).
2. **+9 is a tz=6 fact, not a law.**  tz = 5, 8, 9 all want about -7/-4;
   tz=4 and tz=13 want 0.  Consequently the "total bias 13 = stages
   {29,27,26}" identity is a statement about tt4's regime only and must not
   be imposed as a constraint on candidates for other tz.  Likewise the
   {29,27,26} cascade, which was tuned to that sum, should not be expected
   to generalise.

Also visible: every class changes behaviour on the LAST diagonal,
`cut + tz = 18` i.e. bl(disp) = 19 — tz6 flips +9 -> -9, tz5 -7 -> +1,
tz8/tz9 -7 -> -4, tz3/tz4 -> 0.  That is the same bl=36 anomaly reported
earlier for tt4, and it is now shown to be a property of the DISPLACEMENT
width (19 bits) rather than of the product width.

Holdout status: tt3 remains at 18001 with K=0, so none of this disturbs the
narrow control.  tt3/tt4/tt1 were not used to fit anything in B8/B9.

Capture provenance: the .raw files live under `build/analysis-agx-basis/`
(gitignored, as tt3/tt4/tt1 are) and on the M1 at
`/tmp/walle-agx-single-axis-multi-anchor.GRzoaQ/c-tzclass<tz>-plan-v1/capture/`.
Plans are reproducible from `wide_solver_B_gen_plan.py --tz <tz>`; plan
sha256 prefixes b56c8e60 (tz3), a43f02f9 (tz4), 858fbeaa (tz5), beb89f19
(tz8), 0aaf9a19 (tz9).

### B10. Column-truncated array + normaliser compensation — FALSIFIED

`wide_solver_B_ctarray.py`.  The one shape that reconciles both halves of
B9: an array that omits partial-product bits below a fixed column T of the
raw subpixel frame (so tz controls the dropped sum, and tt3 is gated off
automatically because tz=13 leaves nothing to drop for T <= 13), combined
with a compensation added by the normaliser (so it is result-relative and
therefore flat along each row of the (tz, cut) table).

For each T the optimal c is SOLVED by interval stabbing, so these are
ceilings for the family, not sweep results:

| T | joint best c | joint | tt3 | tt4 | tt1 | tz3 | tz4 | tz5 | tz8 | tz9 |
|---|---|---|---|---|---|---|---|---|---|---|
| 7 | -3.91 | 109073/128617 | 18001 | 14850 | 2310 | 15572 | 15165 | 15202 | 15214 | 14911 |
| 9 | -4.00 | 109069 | 18001 | 14838 | 2312 | 15506 | 15165 | 15197 | 15213 | 14911 |
| 11 | -4.00 | 109049 | 18001 | 14730 | 2308 | 15490 | 15133 | 15113 | 15209 | 14913 |
| 13 | -2.00 | 108405 | 18001 | 14274 | 2266 | 15600 | 15037 | 14911 | 15035 | 14841 |
| 14 | 0.00 | 105814 | 17553 | 13598 | 2206 | 15273 | 14764 | 14643 | 14551 | 14585 |

T=7 is effectively no truncation for these captures, and the ceilings fall
monotonically as T grows — the column truncation actively destroys
agreement rather than explaining the tz dependence.  FALSIFIED.  (T=14 also
breaks the tt3 holdout, confirming the gate is real: tt3 tolerates T <= 13
exactly as predicted from its tz.)

Joint ceiling of the whole family (any T, single global c) is
109073/128617 = 84.8%.

### Track B summary of the state

Established: the compensation is result-relative in magnitude and
frame-dependent in value; its value per tz is measured (B9 table); the
bl(P)<=30 gate is dead (B2); no dm-only rule can close it (ceiling theorem,
solver-2 section); tt4's +9 and the bias-sum-13 identity are tz=6 facts.
Open: the mechanism that makes the compensation depend on tz while staying
cut-independent.  The (tz, cut) table in B9 is the object to explain, and
the five new captures make it measurable at will — a new class costs one
anchor change and about a second of GPU time.

---

## Track A (segmented multiplier)

Owner: track-A solver. Harness `analysis/wide_solver_A_frame.py` (mine;
`wide_solver_data.py` used read-only). Scripts prefixed `wide_solver_A_`.

### A0. Correction to a banked cross-track number

**The fixed-frame injection T=17 K=9 does NOT keep tt3 at 18001** — the
lead's summary carried it as "tt3 18001 derived". Measured directly in the
48-bit normalised frame `N = dm * (odd(d_o) << (24 - bl(odd)))`:

| rule | tt4 | tt3 | tt1 |
|---|---|---|---|
| `((N + 9*2^17) >> 17) << 17` | 14850 | **16561** | 2228 |
| `((N >> 17) + 9) << 17` | 14850 | **16561** | 2228 |
| same at T=16 | 14395 | 16881 | 2266 |
| same at T=18 | 13303 | 15313 | 1970 |
| sub-cut constant `((N + C) >> T) << T`, best (T=21, C=13*2^17) | 14524 | **17521** | 2258 |

Reason: the tt3-safety argument needs `TZ24 = 24 - bl(odd(d_o)) > T`, but
tt3 attains `TZ24 = 18` exactly (d_o with a 6-bit odd part, e.g. 47), so a
cut at 17 with a constant AT or ABOVE the cut still perturbs those rows,
and a cut at 21 drops their columns 18..20. A sub-cut constant `C < 2^T`
is invisible only when no column is dropped, which fails for the same
rows. **No injection in the normalised frame is simultaneously tt3-exact
and large enough to matter.** (`wide_solver_A_calib.py`)

### A1. The frame that IS structurally tt3-safe

Use the RAW subpixel frame `R = dm * disp` instead. Measured trailing
zeros of `disp`: tt3 **13..18**, tt4 **6**, tt1 **7**. So a cut at column
`T <= 13` with a sub-cut constant `C < 2^T` is exact on tt3 by
construction while biting on tt4 and tt1 — and the tz split 6 vs 7 is
available as the sign selector the lead asked about.

But that frame caps the achievable deviation: `T <= 13` bounds `|V - R|`
by ~`2^13` raw units, whereas the killer cell needs `V - R` in
**[-102400, -28673]** (one granule there is `2^16 = 65536`). **An
absolute-column cut can be tt3-safe or reach the killer cell, never
both.** Not swept further for that reason (`wide_solver_A_gate.py`).

Significant-width quantisation of the partials does have the range, and
is also tt3-exact by construction: tt3's `odd(disp)` is at most 6 bits, so
`H*disp` carries at most `30 - s` significant bits and `L*disp` at most
`s + 6`; **any width >= 20 is a no-op on tt3 for every split s in 10..14**,
while tt4/tt1 reach 25..27 bits in the same partials. That is the family
swept below — no product-width gate anywhere in it.

### A2. Segmented multiplier: FALSIFIED (`wide_solver_A_seg.py`)

    H = dm >> s ;  L = dm - (H << s)     (optionally signed, borrow into H)
    V = Q(H * disp) << s  +  Q(L * disp)

36000 combinations: s=10..14 x signed/unsigned x per-partial width 18..27
x per-partial mode in {rtz, rne, rna, rup, rodd, floor}, both partials
independent. tt3 held at 18001 throughout, structurally.

| result | tt4 | tt3 | tt1 |
|---|---|---|---|
| family ceiling (s=11, unsigned, hi rna23, lo rne18) | **14506** | 18001 | 2240 |
| best that also reproduces the killer cell | **12971** | 18001 | 2164 |
| baseline (narrow law) | 13876 | 18001 | 2300 |

**The family has a hard internal conflict.** 360 of 36000 combinations
reproduce the killer cell and *every one of them* has `signed=True` — so
the lead's hypothesis is confirmed: a signed low segment does produce the
exact-on-grid one-granule-LOW excursion naturally, via the borrow. But
those same 360 score 12971 on tt4 and 2164 on tt1, both **below the plain
narrow law**. The 14506 ceiling configurations all fail the killer cell.
The mechanism that explains the killer cell and the mechanism that fits
the bulk are mutually exclusive inside this family.

Note the frame-anchored sweep lands on essentially the same ceiling as the
earlier P-unit sweep (14506 vs 14504), which is good evidence the cap is a
property of the family and not of the framing.

### A3. Recursive narrow-law partials: FALSIFIED (`wide_solver_A_recursive.py`)

Partials routed through the export chain itself (rna at w1 then RNE at
w2), 2160 combinations over s=10..14 x signed/unsigned x route in
{both, hi, lo} x w1=21..29 x w2=18..w1:

| result | tt4 | tt3 | tt1 |
|---|---|---|---|
| ceiling (s=11, unsigned, route=hi, w1=w2=23) | 14504 | 18001 | 2240 |
| best with no tt1 regression (s=12, signed, route=hi, w1=w2=23) | 14292 | 18001 | **2300** |
| reproduce the killer cell | **0 of 2160** | — | — |

Same 14.5k ceiling; the recursive form never reaches the killer cell at
all.

### A4. Track A verdict

The segmented multiplier is **falsified in both halves of the assignment**
and in both framings (P units and raw subpixel frame), with tt3 held at
18001 structurally rather than by a product-width gate. Its ceiling
(14506) sits below the already-banked fixed-frame injection (14850) and it
cannot beat the narrow law on tt1 (2240 vs 2300) except at a configuration
that gives up 200 cells of tt4.

The one durable finding to carry forward: **a signed low segment is a
working generator for the killer cell's down-excursion**, and it is the
only such generator found in 38160 swept configurations. Whatever the true
law is, its low-order term is signed and carries a borrow; but it is not
combined with the high partial the way a plain two-segment product does.

### B11. Reconciliation with Track A's correction on the T=17 injection

Track A measured the frame injection as tt3-breaking; both numbers are
right, because they are different laws.  Verified here:

| law | tt4 | tt3 | tt1 |
|---|---|---|---|
| B1 as implemented (injection GATED off when `sh_f > T`) | 14850 | **18001** | 2226 |
| unconditional injection in the normalised frame, `((N + 9*2^17) >> 17) << 17` | 14850 | **8424** | 2228 |

So the B1 score stands, but Track A's substantive point is correct and the
"structural / automatic" framing in B1 was too strong.  What is automatic is
the TRUNCATION: dropping columns below bit 17 of a frame whose low bits are
zero removes nothing.  What is NOT automatic is the injected CONSTANT — a
physical constant-correction array injects it whether or not anything was
dropped, and that is what breaks tt3.  B1's `sh_f > T` test is an explicit
conditional, not something the hardware gives for free.  (This is the same
tension already recorded in B4 and B10: an absolute compensation cannot
coexist with the measured compensation, which is result-relative.)

The gate is also tight rather than comfortable.  Measured `sh_f` ranges:
tt3 18..23, tt4 11..16, tt1 12..20.  tt3's minimum is exactly 18, one bit
above T=17, and 6128 of its cells sit at that minimum.

### B12. Theorem (Track A's, verified here): tt3-safe XOR killer-cell-reachable

For an absolute-column cut at bit T of the RAW subpixel frame `R = dm*disp`
with a sub-cut constant `C < 2^T`, tt3-exactness needs `T <= 13` (tt3's disp
carries 13..18 trailing zeros, so `((R+C) >> T) << T == R` with no gate at
all — genuinely structural, unlike B1).  But then `|V - R| < 2^13 = 8192`.

Measured requirement at the killer cell (dm=0x800C00, d_o=1793, drop=0):
`X in [-1600, -449]` output units, i.e. `[-102400, -28736]` raw units at
z=6, against a raw granule of 2^16.  The smallest admissible excursion,
28736, exceeds the largest achievable perturbation, 8192, by 3.5x.

**No absolute-column cut is both tt3-safe and able to reach the killer
cell.**  Confirms Track A's bound independently.

Carried forward from Track A: of 38160 swept segmented configurations, the
only ones reproducing the killer cell (360 of them) use a SIGNED low segment,
so the borrow is confirmed as the generator of the exact-on-grid
one-granule-low excursion — but those configurations score 12971 on tt4 and
2164 on tt1, both below the plain narrow law, so the borrow is not combined
with the high partial the way a plain two-segment product is.  Track A also
re-ran the dm-keyed additive-bias ceiling under 24 export roundings: best
17307, so no rounder choice rescues a dm-keyed bias.
