#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the offgrid DRC unit testcase (`offgrid.gds`) for the interposer,
following the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS
with intentional FAIL / PASS structures labeled by text on layer 63/0.

Two top cells, each checked as a whole by run_regression.py:
  - offgrid_viol : off-grid vertices           -> expects {metal4_drw_Offgrid,
                                                            via4_drw_Offgrid}
  - offgrid_clean: on-grid shapes + a circle    -> expects {}

The clean cell exercises on-grid geometry across the full scoped layer set
(mim/vmim, the metal/via stacks up to TopMetal2, thinfilmres, lbe, a metal
filler datatype and a passiv PDL datatype) plus a circle on passiv_pillar whose
intermediate vertices are deliberately OFF the 5 nm grid: it proves the
get_circle() exemption (the circle is detected and removed before ongrid, so it
must NOT flag). Every non-circle coordinate sits on the 5 nm grid.

Regenerate with:  python gen_offgrid_testcase.py   (writes offgrid.gds here)
"""

import math
from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text.
L_MIM = (36, 0)
L_VMIM = (129, 0)
L_METAL4 = (50, 0)
L_METAL4_FILLER = (50, 22)
L_VIA4 = (66, 0)
L_METAL5 = (67, 0)
L_TOPVIA1 = (125, 0)
L_TOPMETAL1 = (126, 0)
L_TOPVIA2 = (133, 0)
L_TOPMETAL2 = (134, 0)
L_THINFILMRES = (146, 0)
L_LBE = (157, 0)
L_PASSIV = (9, 0)
L_PASSIV_PILLAR = (9, 35)
L_PASSIV_PDL = (9, 40)
L_TEXT = (63, 0)

OFFGRID_X = 0.002  # 2 nm: off the 5 nm grid


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _poly(cell, idx, pts):
    cell.shapes(idx).insert(db.DPolygon([db.DPoint(x, y) for x, y in pts]))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _circle(cell, idx, cx, cy, r, n=96):
    """Regular n-gon inscribed in radius r. The four axis points (i=0,n/4,n/2,3n/4)
    land exactly on (+-r) so the bbox is a perfect square (aspect ratio 1) and
    get_circle() detects it; the remaining vertices fall off the 5 nm grid, which
    is exactly what exercises the circle exemption in the offgrid check."""
    pts = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        pts.append(db.DPoint(cx + r * math.cos(a), cy + r * math.sin(a)))
    cell.shapes(idx).insert(db.DPolygon(pts))


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    mim = layout.layer(*L_MIM)
    vmim = layout.layer(*L_VMIM)
    m4 = layout.layer(*L_METAL4)
    m4fill = layout.layer(*L_METAL4_FILLER)
    v4 = layout.layer(*L_VIA4)
    m5 = layout.layer(*L_METAL5)
    tv1 = layout.layer(*L_TOPVIA1)
    tm1 = layout.layer(*L_TOPMETAL1)
    tv2 = layout.layer(*L_TOPVIA2)
    tm2 = layout.layer(*L_TOPMETAL2)
    tfr = layout.layer(*L_THINFILMRES)
    lbe = layout.layer(*L_LBE)
    pv = layout.layer(*L_PASSIV)
    pvp = layout.layer(*L_PASSIV_PILLAR)
    pvpdl = layout.layer(*L_PASSIV_PDL)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("offgrid_viol")
    _text(viol, tx, 0.0, -1.0, "3.1 Offgrid - FAIL")

    # metal4_drw_Offgrid: box with one vertex pushed to x = 0.002 (off 5 nm grid).
    _poly(viol, m4, [(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (OFFGRID_X, 0.5)])
    _text(viol, tx, 0.0, 0.8, "metal4_drw_Offgrid FAIL")

    # via4_drw_Offgrid: via cut with one vertex off the 5 nm grid.
    _poly(viol, v4, [(5.0, 0.0), (5.19, 0.0), (5.19, 0.19), (5.0 + OFFGRID_X, 0.19)])
    _text(viol, tx, 5.0, 0.8, "via4_drw_Offgrid FAIL")

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("offgrid_clean")
    _text(clean, tx, 0.0, -1.0, "3.1 Offgrid - PASS")

    # On-grid boxes: first row covers metal/via/passiv drawing layers.
    _box(clean, m4, 0.0, 0.0, 0.5, 0.5)
    _box(clean, v4, 3.0, 0.0, 3.19, 0.19)
    _box(clean, m5, 6.0, 0.0, 6.5, 0.5)
    _box(clean, tm1, 9.0, 0.0, 9.5, 0.5)
    _box(clean, pv, 12.0, 0.0, 12.5, 0.5)
    _text(clean, tx, 0.0, 0.8, "on-grid boxes PASS")

    # Second row broadens coverage to the remaining scoped layers so the clean
    # cell exercises every offgrid rule id, not just the ones the viol cell hits.
    _box(clean, mim, 0.0, 2.0, 0.5, 2.5)
    _box(clean, vmim, 3.0, 2.0, 3.19, 2.19)
    _box(clean, tfr, 6.0, 2.0, 6.5, 2.5)
    _box(clean, lbe, 9.0, 2.0, 9.5, 2.5)
    _box(clean, tv1, 12.0, 2.0, 12.19, 2.19)
    _box(clean, tv2, 15.0, 2.0, 15.19, 2.19)
    _box(clean, tm2, 18.0, 2.0, 18.5, 2.5)
    _box(clean, m4fill, 21.0, 2.0, 21.5, 2.5)
    _box(clean, pvpdl, 24.0, 2.0, 24.5, 2.5)
    _text(clean, tx, 0.0, 2.8, "on-grid boxes (extended scope) PASS")

    # Circle on passiv_pillar: off-grid vertices, exempt via get_circle().
    _circle(clean, pvp, 16.0, 0.5, 0.5)
    _text(clean, tx, 15.0, 1.3, "passiv_pillar circle PASS (exempt)")

    # Bump pad pattern: an off-grid-vertex circle on TopMetal2 MERGED with an
    # on-grid routing bar through its center. The raw-level circle exemption
    # must still recognize the drawn circle even though the merged polygon is
    # no longer circular (regression for the merged-pad false positive).
    _circle(clean, tm2, 30.0, 0.5, 2.5, n=256)
    _box(clean, tm2, 24.0, 0.0, 36.0, 1.0)
    _text(clean, tx, 27.0, 3.5, "tm2 circle + routing bar PASS (raw exempt)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "offgrid.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
