#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the density DRC unit testcase (`density.gds`) for the interposer,
following the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS
with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has four top cells, each checked as a whole by run_regression.py:
  - density_viol        : expects {M4.j, M4Fil.h, M5.k, M5Fil.k, TM1.c, TM2.d,
                                   LBE.i, Slt.i_M4}
  - density_clean       : all four metals at ~45% coverage, plus legal-side
                          coverage for the two omission-tested rules: an LBE
                          region at 19% (below the 20% LBE.i cap) and a
                          100x100 M4 plate with 7% slit coverage (above the
                          6% Slt.i_M4 minimum) -> expects {}
  - density_sanity_viol : two prBoundary + two EdgeSeal boundary polygons of
                          differing areas -> expects {DEN.BND.1, DEN.BND.2,
                          DEN.BND.3}. This cell must be run with the extra
                          define density_sanity=true (run_regression wires it
                          via the table's 'defines' entry).
  - density_sanity_clean: a single prBoundary + a single EdgeSeal boundary
                          polygon with areas matching within 1%, all four
                          metals at 45% -> expects {} (also run with
                          density_sanity=true; proves the DEN.BND.* rules do
                          not false-fire).

Density bookkeeping (chip = prBoundary area):
  - density_viol chip is 800x800 (640000 um^2). M4 = 3 stripes 20x800 + one
    100x100 solid plate (with a 1 um^2 slit) = 57999 um^2 ~ 9.1% -> under both
    the 35% global min (M4.j) and the 25% window min (M4Fil.h) while the plate
    still triggers Slt.i_M4. M5/TM2 are 80% (32 stripes 20x800 at 25 um pitch)
    -> over the 60% (M5.k) / 70% (TM2.d) global max and the 75% window max
    (M5Fil.k). TM1 is 10% (4 stripes) -> under the 25% min (TM1.c) but far
    from the 70% max. LBE is an 800x240 box = 30% -> over the 20% max (LBE.i).
    All stripes are 20 um wide so the Slt.i plate isolation (sized -17.5 um)
    empties everywhere except the intentional M4 plate.
  - density_clean uses 18 stripes 20x800 at 45 um pitch = 45.0% on M5, TM1
    and TM2: inside (35, 60) and (25, 70) globally and (25, 75) per window.
    M4 uses the same 18-stripe pattern, but stripes 13..15 are carved around
    a 100x100 plate (removing 3 x 20 x 140 = 8400 um^2); the plate carries
    7 slits of 2x50 um = 700 um^2 = 7.0% slit coverage (>= 6%), so Slt.i_M4
    is exercised on the legal side (plate/slit ratio 10000/700 = 14.29 <
    16.67 limit). M4 density = (288000 - 8400 + 10000 - 700) / 640000 =
    45.14%, still inside every global and window range. An 800x152 LBE box
    = 121600 um^2 = 19.0% stays below the 20% LBE.i cap.
  - density_sanity_viol has two prBoundary boxes (640000 + 360000 um^2) and
    two EdgeSeal boundary boxes (490000 + 250000 um^2): areas differ by 26%
    (> 1%) -> DEN.BND.3, and each layer has 2 polygons -> DEN.BND.1/2. Both
    prBoundary regions carry ~44% metal fill on all four metals so every
    global and window rule stays quiet.
  - density_sanity_clean has one prBoundary box (800x800 = 640000 um^2) and
    one EdgeSeal boundary box (798x800 = 638400 um^2): areas differ by 0.25%
    (< 1%) and each layer has a single polygon, so no DEN.BND.* rule fires.
    All four metals carry the 45.0% stripe fill.

All coordinates are on the 5 nm grid (dbu 0.001, values are multiples of
0.005 um). Regenerate with:  python gen_density_testcase.py
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text
L_PRB = (235, 0)     # prBoundary
L_ESB = (39, 4)      # EdgeSeal boundary
L_M4 = (50, 0)
L_M4_SLIT = (50, 24)
L_M5 = (67, 0)
L_TM1 = (126, 0)
L_TM2 = (134, 0)
L_LBE = (157, 0)
L_TEXT = (63, 0)

CHIP = 800.0         # chip edge for the single-window cells (um)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _stripes(cell, idx, x0, y0, y1, count, width, pitch):
    """Draw `count` vertical stripes of `width` starting at x0, on `pitch`."""
    for k in range(count):
        x = x0 + k * pitch
        _box(cell, idx, x, y0, x + width, y1)


def build():
    layout = db.Layout()
    layout.dbu = 0.001

    prb = layout.layer(*L_PRB)
    esb = layout.layer(*L_ESB)
    m4 = layout.layer(*L_M4)
    m4s = layout.layer(*L_M4_SLIT)
    m5 = layout.layer(*L_M5)
    tm1 = layout.layer(*L_TM1)
    tm2 = layout.layer(*L_TM2)
    lbe = layout.layer(*L_LBE)
    tx = layout.layer(*L_TEXT)

    # ---- density_viol ----------------------------------------------------- #
    viol = layout.create_cell("density_viol")
    _box(viol, prb, 0.0, 0.0, CHIP, CHIP)
    _text(viol, tx, 10.0, 795.0, "density_viol: 800x800 prBoundary chip")

    # M4 ~9.1%: 3 stripes 20x800 (48000) + 100x100 plate (10000) - 1 um^2 slit
    # -> M4.j (< 35%) and M4Fil.h (<= 25%); plate 100x100 with a single 1x1 um
    # slit -> slit density 0.01% << 6% -> Slt.i_M4.
    _stripes(viol, m4, 50.0, 0.0, CHIP, 3, 20.0, 100.0)
    _box(viol, m4, 600.0, 600.0, 700.0, 700.0)
    _box(viol, m4s, 649.5, 649.5, 650.5, 650.5)
    _text(viol, tx, 50.0, 785.0, "M4 ~9.1% -> M4.j + M4Fil.h")
    _text(viol, tx, 600.0, 705.0, "M4 plate 100x100, 1um2 slit -> Slt.i_M4")

    # M5 80%: 32 stripes 20x800 on 25 um pitch -> M5.k (> 60%) + M5Fil.k (>= 75%)
    _stripes(viol, m5, 0.0, 0.0, CHIP, 32, 20.0, 25.0)
    _text(viol, tx, 10.0, 775.0, "M5 80% -> M5.k + M5Fil.k")

    # TM1 10%: 4 stripes 20x800 -> TM1.c (< 25%)
    _stripes(viol, tm1, 350.0, 0.0, CHIP, 4, 20.0, 100.0)
    _text(viol, tx, 350.0, 765.0, "TM1 10% -> TM1.c")

    # TM2 80%: same pattern as M5 -> TM2.d (> 70%)
    _stripes(viol, tm2, 0.0, 0.0, CHIP, 32, 20.0, 25.0)
    _text(viol, tx, 10.0, 755.0, "TM2 80% -> TM2.d")

    # LBE 30%: 800x240 box -> LBE.i (> 20%)
    _box(viol, lbe, 0.0, 0.0, CHIP, 240.0)
    _text(viol, tx, 10.0, 245.0, "LBE 30% -> LBE.i")

    # ---- density_clean ---------------------------------------------------- #
    clean = layout.create_cell("density_clean")
    _box(clean, prb, 0.0, 0.0, CHIP, CHIP)
    _text(clean, tx, 10.0, 795.0,
          "density_clean: metals ~45%, LBE 19% and M4 plate w/ 7% slits (all legal)")

    # M5/TM1/TM2: 18 stripes 20x800 on 45 um pitch = 288000 um^2 = 45.0%.
    # 20 um stripes empty under the Slt.i opening (sized -17.5 um).
    for lay in (m5, tm1, tm2):
        _stripes(clean, lay, 0.0, 0.0, CHIP, 18, 20.0, 45.0)

    # M4: same 18-stripe pattern, but stripes 13..15 (x 585/630/675) are carved
    # around the plate below with 20 um clearance, so the sized -17.5/+17.5
    # Slt.i opening isolates exactly the 100x100 plate. The carve removes
    # 3 x 20 x 140 = 8400 um^2.
    for k in range(18):
        x = k * 45.0
        if k in (13, 14, 15):
            _box(clean, m4, x, 0.0, x + 20.0, 330.0)
            _box(clean, m4, x, 470.0, x + 20.0, CHIP)
        else:
            _box(clean, m4, x, 0.0, x + 20.0, CHIP)

    # Legal Slt.i_M4 coverage: 100x100 plate with 7 slits 2x50 = 700 um^2
    # -> slit density 7.0% >= 6% minimum (plate/slit ratio 14.29 < 16.67).
    # M4 density = (288000 - 8400 + 10000 - 700) / 640000 = 45.14% -> legal.
    _box(clean, m4, 600.0, 350.0, 700.0, 450.0)
    for k in range(7):
        x = 610.0 + k * 12.0
        _box(clean, m4s, x, 375.0, x + 2.0, 425.0)
    _text(clean, tx, 600.0, 455.0, "M4 plate 100x100, 7% slits -> Slt.i_M4 legal")

    # Legal LBE coverage: 800x152 = 121600 um^2 = 19.0% < 20% cap.
    _box(clean, lbe, 0.0, 0.0, CHIP, 152.0)
    _text(clean, tx, 10.0, 157.0, "LBE 19% -> LBE.i legal")

    # ---- density_sanity_viol ---------------------------------------------- #
    # Needs -rd density_sanity=true. Two prBoundary boxes of clearly different
    # areas + two EdgeSeal boundary boxes; ~44% metal fill inside BOTH
    # prBoundary regions keeps every global/window rule quiet.
    san = layout.create_cell("density_sanity_viol")
    _box(san, prb, 0.0, 0.0, 800.0, 800.0)      # 640000 um^2
    _box(san, prb, 900.0, 0.0, 1500.0, 600.0)   # 360000 um^2
    _box(san, esb, 0.0, 0.0, 700.0, 700.0)      # 490000 um^2
    _box(san, esb, 900.0, 0.0, 1400.0, 500.0)   # 250000 um^2
    _text(san, tx, 10.0, 795.0,
          "density_sanity_viol: 2x prBoundary + 2x EdgeSeal boundary -> DEN.BND.1/2/3")
    _text(san, tx, 910.0, 595.0, "second prBoundary region, metals ~43%")

    # Region A (800x800): 18 stripes -> 45.0%; region B (600x600): 13 stripes
    # -> 43.3%. Combined 444000 / 1000000 = 44.4% on every metal.
    for lay in (m4, m5, tm1, tm2):
        _stripes(san, lay, 0.0, 0.0, 800.0, 18, 20.0, 45.0)
        _stripes(san, lay, 900.0, 0.0, 600.0, 13, 20.0, 45.0)

    # ---- density_sanity_clean ---------------------------------------------- #
    # Also needs -rd density_sanity=true and must return the EMPTY set: one
    # prBoundary box (800x800 = 640000 um^2) plus one EdgeSeal boundary box
    # (798x800 = 638400 um^2). Areas differ by 0.25% (< 1% tolerance) so
    # DEN.BND.3 stays quiet, and a single polygon per layer keeps DEN.BND.1/2
    # quiet. All four metals at 45.0% keep every global/window rule quiet.
    sanc = layout.create_cell("density_sanity_clean")
    _box(sanc, prb, 0.0, 0.0, 800.0, 800.0)     # 640000 um^2
    _box(sanc, esb, 1.0, 0.0, 799.0, 800.0)     # 638400 um^2, delta 0.25%
    _text(sanc, tx, 10.0, 795.0,
          "density_sanity_clean: 1x prBoundary + 1x EdgeSeal boundary, "
          "areas within 1% -> no DEN.BND.*")

    for lay in (m4, m5, tm1, tm2):
        _stripes(sanc, lay, 0.0, 0.0, 800.0, 18, 20.0, 45.0)

    return layout


def main():
    out = Path(__file__).resolve().parent / "density.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
