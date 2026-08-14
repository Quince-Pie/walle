# A2 presentation plane solver log (task #4)

Append-only. Times are UTC dates.

## 2026-08-14 entry 1: walle's per-pixel primary f16 is back on line

`analysis/a2_solver_primary.py` renders walle's public CPU model retaining the
binary16 alpha (the "primary") instead of only the R8 byte.  The
`SelectorTableOverride` API drift noted in TASK.md later-41 is a *missing
helper class*, not a changed algorithm: `analysis/liquid_glass_runtime_raster
_coefficients.py` reads a selector table through `len()` and one `[index]`
only, so a two-line shim restores every prototype scorer
(`_analyze_reveal_second_stage.render_primary_half`,
`_analyze_reveal_captured_a2_geometry._overlay_triangle`, and therefore
`score_reveal_v74_public_raster`).  The shim lives in a2_solver_primary.py and
is installed only when the attribute is absent.

Validation: per-state mismatch counts against the corpus reproduce
`build/_residual_list.txt` exactly - 31:5, 33:2, 40:12, 41:1, 42:34, 58:11,
60:8, all |delta| = 1.  So the Python model *is* the current 91-pixel
frontier and its primary is trustworthy everywhere else.

## 2026-08-14 entry 2: the presentation class is 37 pixels, not 18

`analysis/a2_solver_probe_residuals.py` prints, at every one of the 91
residuals, walle's primary half bits and BOTH candidate corpus bytes
b(0x3C00) = round255(p) and b(0x3BFF) = round255(h16(p * (1-2^-11))).

A pixel can only be a *secondary* (presentation-plane) residual if the two
candidate bytes differ ("sensitive") and apple's byte equals the 0x3BFF one.
Sorting the 91 that way:

- secondary-explained (sensitive, apple == b(0x3BFF)), 37 pixels:
  state 40 x12 (all of state 40), 41 x1, 42 x12, 58 x8, 60 x4.
- NOT explainable by any secondary in {0x3C00, 0x3BFF}, 54 pixels: the two
  candidate bytes are EQUAL and apple differs from both, i.e. apple's
  PRIMARY differs by one binary16 ulp.  This covers all of states 31 and 33,
  and 22 of state 42.

This corrects TASK.md later-41's census.  In particular the two state-42
"+1" pixels the brief lists as plane constraints are primary residuals:
- (1838,106): p = 0x30f4 -> 39.47 -> 39; apple 40 needs p = 0x30f5 (39.50).
- (259,2011): p = 0x3a82 -> 207.4 -> 207; both secondaries give 207, apple
  208 needs p = 0x3a83.
Since a secondary <= 1.0 can never RAISE a byte, every "apple = walle+1"
residual is a primary residual by construction.  The later-44 hardware
result stands on its own (the plane really does dip below 1 in the y<=31
band); what dies is the requirement that the plane SPLIT (1837,103) from
(1838,106) inside tile (57,3) - (1838,106) carries no plane information.
That removes the later-46 obliqueness forcing argument.
