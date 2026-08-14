# Wide-path C-product law: solver brief

## The problem (fully self-contained)

Identify the exact deterministic function used by Apple-silicon (M1 AGX)
triangle setup to produce interpolant plane-constant words, for products
wider than 30 bits. One function; three frozen hardware datasets; success
= 100% exact on all of them.

For each observation: inputs are a 24-bit mantissa `dm` (from an f32
probe word) and an odd displacement `d_o`; the exact product is
`P = dm * d_o`. The hardware exports a 24-bit-mantissa f32 word `C`.
Find `f` with `C = f(dm, d_o)` (equivalently f(P) with possible
operand-level terms) exact on every observation.

## Established (do not re-derive)

1. NARROW LAW (bit-exact, 18001/18001 on tt3): for bit_length(P) <= 30:
   `C = RNE24( rna27(P) )` where rna27 = round-half-AWAY to 27 bits
   (with overflow renormalize), RNE24 = IEEE round-nearest-even to
   24-bit mantissa.
2. Wide (bl >= 31) deviates. Measured facts:
   - tt4 rows d_o=129/257/385/513/641/769 (low-block dm = 0x800000+t,
     t=0..255): the RNE24-implied excess X is a plateau
     HI ~ 13*2^(msb(d_o)-6) then apparent -1/lsb ramp = signature of a
     SHARP THRESHOLD at dropped-fraction phi0 = 2^(cut-1) - 13*2^(cut-6)
     (cut = bl-24). Verified thresholds 38/76/152 vs observed 39/77/154.
   - BUT global "narrow law + biased floor (P + 2^(cut-1) + B*2^cut/64)"
     peaks at B=11..13 with only ~14100/18001 (tt4), 2124/2610 (tt1).
   - KILLER CELL: dm=0x800C00, d_o=1793 (P exactly representable,
     dropped=0): hw word is ONE FULL GRANULE LOW (X ~ -1024 at cut=10).
     No |bias| < half-granule can do this. Consistent with
     P_hw = P*(1-2^-24) + biased-round for THIS cell, but that variant
     breaks rows 17-22 (verified: deficit cancels the half-granule).
   - ty63 (d_o=6017): negative X values, not constant per row.
   - X is NOT: linear in dropped bits (dropped=0 cells have X!=0),
     constant per (bl,parity), a per-step walk (tt3 exactness kills
     27-bit walks; error budget kills wide-accumulator walks), Booth
     truncation (all orientations/corrections swept), cascade
     roundings, f32 chains, preshifted operands, postround W=31..33.
     All swept to their ceilings ~78-90%; see TASK.md later-36..40.

## Datasets (frozen, with loaders)

All under /tmp/walle/build/analysis-agx-basis/<name>/ with
reveal-agx-setup-accumulator-plan.json + capture.raw.

- c-truthtable3-plan-v1: ay=131072, d_o = disp>>13, value scale 2^-7.
  All-narrow control; any candidate must keep 18001/18001.
- c-truthtable4-plan-v1: ay=131008, d_o = disp>>6, scale 2^-14.
  The wide-path battleground (18001 cells; dm scans t=0..255 low-block
  and t<<8 high-block; d_o = 128*ty-2047 for ty=17..63).
- c-truthtable-plan-v1 (tt1): ay=157312, d_o = disp>>7, scale 2^-12.
  Mixed narrow/wide; includes the Mb=24 split rows (ty24/25 export M,
  ty26/27 export M-1, same frames, M odd - any candidate must pass).

Loader pattern (python, /tmp/walle as cwd):
```python
import sys, json; sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]
from pathlib import Path
import _sweep_fused_join_lattice as m
D = Path("build/analysis-agx-basis/c-truthtable4-plan-v1")
PLAN = json.load(open(D/"reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D/"capture.raw", len(PLAN["draws"]))
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    w = exp["word"]                       # probe f32 word (dm = mantissa)
    disp = draw["tileY"]*8192 - 131008    # subpixels; negative on top row
    C = int(T[exp["recordIndex"]][0][2])  # exported f32 word
```
Scoring: value = signed(P')*2^(de)+scale as in
analysis/score-style scripts; compare rne24_word_frac(value) == C, or
better, compare the predicted WORD directly. m.f32_parts(w) ->
(sign, mant24, e_lsb); hunt_c_walk_seed.py has frac_of and
rne24_word_frac.

## Candidate directions not yet exhausted

- Two-stage: multiplier truncation at a fixed FRAME position (absolute
  bit of the 48-bit dm x didx24 grid, didx24 = d_o normalized <<= to 24
  bits) followed by the narrow law; sweep frame positions/modes.
- Operand-level: hw slope operand wider than dm (27-bit padded) with a
  reciprocal-style correction term; interactions (dm mod 2^12)*f(d_o).
- The 13/64 constant: 13 = 0b1101; appears as relative excess 13*2^-29
  in multiple regimes; find the datapath expression producing it.
- Segmented multiplier: dm split hi/lo at bit 11/12 (the killer cell's
  0xC00 pattern!), sub-products individually rounded through the
  narrow-law datapath, then summed (test all split points 10..14 and
  per-subproduct rounding rna27/RNE24).

## Success criterion

A single input-only rule scoring 18001/18001 (tt4), 18001/18001 (tt3),
2610/2610 (tt1). Then run analysis/score_c_chain_dense.py-style scoring
against the dense capture and report. Bank the law + scores to TASK.md
and commit (do NOT push without the lead's review).
