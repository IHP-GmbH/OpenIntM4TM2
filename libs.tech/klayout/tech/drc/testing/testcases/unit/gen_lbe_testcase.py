#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the LBE (Localized Backside Etch) DRC unit testcase (`lbe.gds`) for the
interposer, following the IHP-SG13G2 `testing/testcases/unit/` convention: one
table GDS with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - lbe_viol : one violating structure per new rule -> expects
               {LBE.b2, LBE.e.dfPad, LBE.e.Passiv}
  - lbe_clean: near-limit legal versions            -> expects {} (also guards the
               pre-existing LBE.a/b/b1/c/d/h rules and exercises LBE.b2 at its
               legal area limit of exactly 30000 um^2)

CAUTION on the pre-existing rules of the deck:
  LBE.a  min LBE width      = 100 um   -> all LBE boxes are >= 120 um wide
  LBE.b  max LBE width      = 1500 um  -> all LBE boxes are 200 um (well below)
  LBE.b1 max LBE area       = 250000   -> all LBE boxes are <= 40000 um^2
  LBE.c  min LBE space      = 100 um   -> LBE boxes kept >= 100 um apart
  LBE.d  space to EdgeSeal  = 150 um   -> NO edgeseal drawn (rule cannot fire)
  LBE.h  no LBE ring        -> only solid boxes, no holes

Only the three new rules are meant to fire in lbe_viol:
  LBE.b2      : LBE area < 30000 um^2         (120 x 200 = 24000 box)
  LBE.e.dfPad : LBE-to-dfpad space < 50 um    (200 x 200 box, gap 30 to dfpad)
  LBE.e.Passiv: LBE-to-passiv space < 50 um   (200 x 200 box, gap 30 to passiv)

Regenerate with:  python gen_lbe_testcase.py   (writes lbe.gds next to this file)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert for the deck)
L_LBE = (157, 0)
L_DFPAD = (41, 0)
L_PASSIV = (9, 0)
L_TEXT = (63, 0)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    lbe = layout.layer(*L_LBE)
    dfpad = layout.layer(*L_DFPAD)
    passiv = layout.layer(*L_PASSIV)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("lbe_viol")
    _text(viol, tx, 0.0, -30.0, "9.1 LBE - FAIL")

    # LBE.b2 FAIL: 120 x 200 LBE -> area 24000 < 30000 (width 120 >= 100 keeps LBE.a quiet).
    _box(viol, lbe, 0.0, 0.0, 120.0, 200.0)
    _text(viol, tx, 0.0, 210.0, "LBE.b2 FAIL (area 24000 < 30000)")

    # LBE.e.dfPad FAIL: 200 x 200 LBE, dfpad 30 um to the right (< 50) -> area 40000 keeps LBE.b2 quiet.
    _box(viol, lbe, 500.0, 0.0, 700.0, 200.0)
    _box(viol, dfpad, 730.0, 0.0, 830.0, 100.0)
    _text(viol, tx, 500.0, 210.0, "LBE.e.dfPad FAIL (gap 30 < 50)")

    # LBE.e.Passiv FAIL: 200 x 200 LBE, passiv 30 um to the right (< 50).
    _box(viol, lbe, 1100.0, 0.0, 1300.0, 200.0)
    _box(viol, passiv, 1330.0, 0.0, 1430.0, 100.0)
    _text(viol, tx, 1100.0, 210.0, "LBE.e.Passiv FAIL (gap 30 < 50)")

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("lbe_clean")
    _text(clean, tx, 0.0, -180.0, "9.1 LBE - PASS")

    # LBE box (150 x 200, area EXACTLY 30000): width 150 >= LBE.a 100 and bbox 200 <= LBE.b 1500,
    # area 30000 <= LBE.b1 250000, and area is at the LBE.b2 limit -- with_area(nil, 30000) selects
    # area < 30000, so exactly 30000 must NOT fire (at-limit legal guard for LBE.b2). dfpad exactly
    # 50 um to the right and passiv exactly 50 um below -> both LBE.e checks at the legal limit
    # (sep fires only for space < 50).
    _box(clean, lbe, 0.0, 0.0, 150.0, 200.0)
    _box(clean, dfpad, 200.0, 0.0, 300.0, 100.0)      # gap 50 to the right of x=150
    _box(clean, passiv, 0.0, -150.0, 100.0, -50.0)    # gap 50 below
    _text(clean, tx, 0.0, 210.0, "LBE clean (area 30000 at LBE.b2 limit; dfpad/passiv gap 50)")

    # Second LBE box (200 x 200) exactly 100 um above the first -> LBE.c at the legal limit
    # (space fires only for space < 100). Far (>50 um) from any dfpad/passiv.
    _box(clean, lbe, 0.0, 300.0, 200.0, 500.0)
    _text(clean, tx, 0.0, 510.0, "LBE clean (LBE.c gap 100)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "lbe.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
