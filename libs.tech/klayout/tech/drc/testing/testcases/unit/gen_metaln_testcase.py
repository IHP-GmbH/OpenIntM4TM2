#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Metaln DRC unit testcase (`metaln.gds`) for the interposer, following the
IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with intentional
PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - metaln_viol : one violating structure per wide-line / 45-degree rule
                  -> expects {M4.e, M4.f, M4.g, M4.i, M5.e, M5.f, M5.g, M5.i}
  - metaln_clean: the corresponding near-limit legal structures -> expects {}

Every structure is drawn identically on BOTH Metal4 (50/0) and Metal5 (67/0); the
metaln rules run per metal layer with no inter-layer interaction, so each M4.x maps to
an identical M5.x. Structures are placed several um apart (the M{n}.f block is 12 um
wide) so they never interact and never raise a spurious M{n}.a / M{n}.b.

Rule thresholds exercised (interposer_tech_default.json):
  M{n}.e : space 0.24 when a line is wider than 0.39 um and parallel run > 1.0 um
  M{n}.f : space 0.60 when a line is wider than 10.0 um and parallel run > 10.0 um
  M{n}.g : 0.24 min width of a 45-degree edge longer than 0.5 um
  M{n}.i : space 0.24 involving at least one 45-degree edge

Regenerate with:  python gen_metaln_testcase.py   (writes metaln.gds next to this file)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert for the deck)
L_METAL4 = (50, 0)
L_METAL5 = (67, 0)
L_TEXT = (63, 0)

# Structure origins along x (um). Gaps are large enough that structures never interact
# (the M.f block spans 12 um in x).
X_E = 0.0    # M{n}.e wide-line pair
X_F = 20.0   # M{n}.f very-wide-line pair (12 um block)
X_G = 40.0   # M{n}.g 45-degree bent strip
X_I = 50.0   # M{n}.i 45-degree edge near a neighbour


def _box(cell, layers, x0, y0, x1, y1):
    """Insert an axis-aligned box on every layer in `layers`."""
    for lyr in layers:
        cell.shapes(lyr).insert(db.DBox(x0, y0, x1, y1))


def _poly(cell, layers, pts):
    """Insert a polygon (list of (x, y) tuples) on every layer in `layers`."""
    dpts = [db.DPoint(x, y) for x, y in pts]
    for lyr in layers:
        cell.shapes(lyr).insert(db.DPolygon(dpts))


def _text(cell, tx, x, y, s):
    cell.shapes(tx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    m4 = layout.layer(*L_METAL4)
    m5 = layout.layer(*L_METAL5)
    tx = layout.layer(*L_TEXT)
    met = (m4, m5)

    # =================================================================== #
    #  Violating structures                                               #
    # =================================================================== #
    viol = layout.create_cell("metaln_viol")
    _text(viol, tx, X_E, -1.0, "5.17 Metaln - FAIL")

    # (a) M{n}.e : a 0.5 um wide line (> 0.39) beside a 0.30 um narrow line, spaced
    #     0.22 um over a 3 um parallel run. NOTE the ported construct (layer.sep(wide_seed))
    #     flags the neighbour edge that is NOT part of the wide seed, so exactly one line
    #     must be narrow (<= Mn_e_w) -- a wide/wide pair does not trip this construct.
    #     0.22 > Mn_b (0.21) so M.b stays quiet; 0.22 < Mn_e (0.24) with 3 um parallel
    #     run (> 1.0) -> M{n}.e. Neither line reaches 10 um so M.f stays quiet.
    _box(viol, met, X_E + 0.00, 0.0, X_E + 0.50, 3.0)
    _box(viol, met, X_E + 0.72, 0.0, X_E + 1.02, 3.0)
    _text(viol, tx, X_E, 3.3, "M.e FAIL (wide+narrow @0.22)")

    # (b) M{n}.f : a 12 x 12 um block (> 10 wide) and a 0.24 um wide x 12 um line
    #     spaced 0.30 um. 0.30 > Mn_e (0.24) so M.e stays quiet; 0.30 < Mn_f (0.60)
    #     with 12 um parallel run (> 10.0) -> M{n}.f. 0.24 > Mn_a keeps M.a quiet.
    _box(viol, met, X_F + 0.0, 0.0, X_F + 12.0, 12.0)
    _box(viol, met, X_F + 12.30, 0.0, X_F + 12.54, 12.0)
    _text(viol, tx, X_F, 12.3, "M.f FAIL (12um block @0.30)")

    # (c) M{n}.g : a rectangle rotated 45 degrees (all corners 90 degrees, so no acute
    #     corner artefacts on the M.a width check). The two long edges (1.41 um > Mn_g_min
    #     0.5) are at +45 degrees; the perpendicular width between them is 0.15*sqrt(2) =
    #     0.212 um (< Mn_g 0.24 -> M.g fires, but > Mn_a 0.20 -> M.a stays quiet). The short
    #     end caps (0.212 um) are below Mn_g_min and are dropped by the length filter.
    _poly(viol, met, [
        (X_G + 0.00, 1.00),
        (X_G + 1.00, 2.00),
        (X_G + 1.15, 1.85),
        (X_G + 0.15, 0.85),
    ])
    _text(viol, tx, X_G, 2.3, "M.g FAIL (0.21 wide 45 rect)")

    # (d) M{n}.i : a 0.3 um wide line (A) and a 0.3 um wide line (B) carrying a short
    #     45-degree chamfer whose closest point is 0.22 um from A. Both lines are 0.3
    #     (< 0.39) so M.e/M.f stay quiet; the chamfer edge is 0.07 um (< Mn_g_min 0.5)
    #     so M.g stays quiet; 0.22 > Mn_b (0.21) so M.b stays quiet; a 45-degree edge is
    #     within Mn_i (0.24) of a neighbour -> M{n}.i.
    _box(viol, met, X_I + 0.0, 0.0, X_I + 2.0, 0.30)
    _poly(viol, met, [
        (X_I + 0.05, 0.52),
        (X_I + 2.0, 0.52),
        (X_I + 2.0, 0.82),
        (X_I + 0.0, 0.82),
        (X_I + 0.0, 0.57),
    ])
    _text(viol, tx, X_I, 1.1, "M.i FAIL (45 chamfer @0.22)")

    # =================================================================== #
    #  Clean structures (near-limit legal variants of the same shapes)    #
    # =================================================================== #
    clean = layout.create_cell("metaln_clean")
    _text(clean, tx, X_E, -1.0, "5.17 Metaln - PASS")

    # (a) M{n}.e PASS: wide + narrow line at exactly Mn_e (0.24 um) spacing.
    _box(clean, met, X_E + 0.00, 0.0, X_E + 0.50, 3.0)
    _box(clean, met, X_E + 0.74, 0.0, X_E + 1.04, 3.0)
    _text(clean, tx, X_E, 3.3, "M.e PASS (wide+narrow @0.24)")

    # (b) M{n}.f PASS: 12 um block and narrow line at exactly Mn_f (0.60 um) spacing.
    _box(clean, met, X_F + 0.0, 0.0, X_F + 12.0, 12.0)
    _box(clean, met, X_F + 12.60, 0.0, X_F + 12.84, 12.0)
    _text(clean, tx, X_F, 12.3, "M.f PASS (12um block @0.60)")

    # (c) M{n}.g PASS: same 45-rotated rectangle but with perpendicular width
    #     0.175*sqrt(2) = 0.247 um (>= Mn_g 0.24) so the 45-degree width check passes.
    _poly(clean, met, [
        (X_G + 0.000, 1.000),
        (X_G + 1.000, 2.000),
        (X_G + 1.175, 1.825),
        (X_G + 0.175, 0.825),
    ])
    _text(clean, tx, X_G, 2.3, "M.g PASS (0.247 wide 45 rect)")

    # (d) M{n}.i PASS: same pair but the 45-degree chamfer sits exactly Mn_i (0.24 um)
    #     from the neighbour.
    _box(clean, met, X_I + 0.0, 0.0, X_I + 2.0, 0.30)
    _poly(clean, met, [
        (X_I + 0.05, 0.54),
        (X_I + 2.0, 0.54),
        (X_I + 2.0, 0.84),
        (X_I + 0.0, 0.84),
        (X_I + 0.0, 0.59),
    ])
    _text(clean, tx, X_I, 1.1, "M.i PASS (45 chamfer @0.24)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "metaln.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
