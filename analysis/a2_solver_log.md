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

## 2026-08-14 entry 3: the byte model is confirmed on 10,486 pixels

`analysis/a2_solver_constraints.py` labels every "sensitive" pixel - one whose
corpus byte differs between the two secondaries - with the secondary apple
actually used, and buckets it by the covering A2 transfer triangle (exact
integer top-left rule on the captured mesh, `a2_solver_constraints.triangle_map`).

Across all 51 border-grid states: 10,486 sensitive pixels, 45 resolved with
0x3BFF, and **zero** whose corpus byte matches neither candidate.  That is a
strong independent confirmation of the banked byte model
byte = round255(h16(h16(primary) * secondary)), secondary in {0x3C00, 0x3BFF},
and of walle's primary everywhere the secondary is observable.

## 2026-08-14 entry 4: the LOW set splits into two mechanisms

Per-tile shape of the 45 LOW pixels (32x32 tiles, `a2_solver_census.py`):

- state 42 tile (56,0): 11 LOW / 0 HIGH - the whole sampled arc inside one
  tile flips.  This is the only tile-filling cluster in the entire corpus.
- 34 other LOW pixels, in states 33/35/40/41/42/44/45/58/60: every one is an
  ISOLATED LOW inside an otherwise-HIGH tile (1-3 LOW against 1-6 HIGH).

Falsifications, all exact:

1. Per-tile-constant secondary, swept over tile sizes 8..128 AND both phases
   (`a2_solver_tile_test.py`, `a2_solver_tile_offsets.py`): the best (size,
   phase) still leaves 8 mixed tiles.  Dead as a model of all 45.
2. Per-tile constant + shared per-primitive slope - the actual AGX setup
   shape, where inside a tile the selection must be a threshold on
   t = A*x + B*y (`a2_solver_slope_cone.py`, exact integer cone over every
   same-tile (low, high) pair): INFEASIBLE at tiles 64/32/16/8 for states
   40/58/60, and still infeasible at 4x4 for state 58.
3. One affine plane per transfer triangle (`a2_solver_plane.py`, exact
   3-D cone by cross products of constraint normals): INFEASIBLE for
   state 40 tri 2 (12 LOW), state 58 tri 4 (7 LOW), state 60 tri 4 (4 LOW),
   and for state 41's single LOW (feasible only with the pixel exactly ON
   the crossing line, i.e. not strictly).
   The geometric reason is decisive: the sensitive pixels lie on the convex
   antialiased arc, so any half-plane cuts a CONTIGUOUS run of them.  In
   arc order state 40's LOW positions are 0,3,4,11,15,24,28,39,41,43,45,48 -
   not a run.  State 40 even flips inside one column between adjacent rows
   ((1852,434) LOW, (1852,435) HIGH; (1717,0) LOW, (1717,1) HIGH), and those
   two pixels share a 2x2 quad.
4. A different binary16 -> unorm8 conversion law (half-up, truncation,
   narrowed 255*p product, f16 product) - `a2_solver_conversion_laws.py`
   scores every candidate over the full frame; walle's rint(255p) is the
   unique best (66 vs 556+ mismatches over the five states).
5. "walle's binary32 alpha sits at its binary16 rounding boundary", i.e. the
   flips are ordinary last-bit noise (`a2_solver_boundary_test.py`,
   `a2_solver_distance_ulps.py`): the flip headroom, expressed in binary32
   ulps of the SDF distance, is 0.3-2.5 ulps at LOW pixels while HIGH pixels
   exist needing only 0.002 ulps and did not flip.  No uniform distance
   offset orders these two sets.

Conclusion: the transfer-plane (presentation) class is state 42's tile (56,0),
11 pixels.  The other 34 LOW pixels are one-binary16-ulp primary residuals of
the same family as the 54 insensitive residuals - they are only visible
because a byte boundary happens to sit under them.  Note the corpus can only
reveal a DOWNWARD primary error at a sensitive pixel, which is why
excluded = 0 despite the full residual set carrying both signs.

## 2026-08-14 entry 5: solved plane polytope for the presentation class

`analysis/a2_solver_plane.py` solves, in exact integers, the cone of affine
deficits g(P) = D(P) - 2^-25 with g > 0 at LOW and g <= 0 at HIGH, over pixel
centres doubled to integers.  For state 42 triangle 2 (verts (1933,-806.5),
(1933,614.5),(512,614.5)), 11 LOW + 65 HIGH, excluding the raster pixel
(1837,103):

  FEASIBLE, simplicial cone with three extreme rays (A,B,C) on
  g = A*(2x+1) + B*(2y+1) + 2C:
      (-1116,  250,  2008179)
      ( -185,   41,   333158)
      (    6,   -4,   -10713)
  strictly feasible interior normal (-1295, 287, 2330624).

Including (1837,103) makes it INFEASIBLE - an exact proof that that pixel
cannot belong to the plane class, which is what entry 4 concluded on
independent grounds.

The corpus fixes the crossing LINE, not the deficit magnitude (the constraint
system is homogeneous once the 2^-25 threshold is folded into C).  The cone
contains the pure-y family A = 0, whose crossing row Y* is pinned to

    Y* in (31.5, 34.5]      (last LOW row 31, first HIGH row 34)

which brackets the 32-row tile boundary y = 32.  So the solved polytope is
exactly consistent with the later-44 hardware measurement: A = 0,
B = 2e68b4e5 (~ +2^-34), C = 3f7fffff = 1 - 2^-24 in transfer tile row 0 and
C = 3f800000 = 1.0 in tile row 1.  The presentation mechanism is therefore
CLOSED as a mechanism; what remains is the setup law that produces
C_tile = 1 - 2^-24 in that one tile row from the triangle geometry - i.e. the
two-product cancellation residue of task #2/#3 evaluated at binary32 for the
constant-1.0 varying, not a new unknown.

## 2026-08-14 entry 6: what later-44/45/46 had wrong

The blocker of later-44 ("(1837,103) and (1838,106) share tile (57,3) with
opposite corpus directions - killing any per-tile-constant secondary model")
and the later-46 obliqueness algebra both rest on (1838,106), which is an
apple = walle+1 pixel and therefore cannot be a secondary effect at all: a
secondary <= 1.0 can only lower a byte.  With that pixel removed, no tile
carries contradictory plane evidence, the per-tile-constant reading of the
hardware C words is restored, and the "oblique plane" requirement disappears.
The remaining state-42 pixel in tile (57,3), (1837,103), is a raster residual
by the exact infeasibility above.
