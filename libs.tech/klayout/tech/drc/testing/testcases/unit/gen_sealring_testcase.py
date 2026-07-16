#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Sealring DRC unit testcase (`sealring.gds`) for the interposer,
following the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS
with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - sealring_viol : violates every surviving rule -> expects
                    {Seal.k, Seal.l, Seal.m, Seal.n}
  - sealring_clean: near-limit legal versions      -> expects {}

Surviving rules (interposer backend-only subset; the Seal.b family is dropped
because its primary layer Activ does not exist here):
  - Seal.l : no drawn structure may fall outside the 39/4 sealring boundary.
  - Seal.n : the EdgeSeal (39/0) must be enclosed by an unbroken Passiv (9/0) ring.
  - Seal.k : every 45-degree EdgeSeal corner edge must be >= 21.00 um long.
  - Seal.m : only one sealring (one EdgeSeal polygon) per chip is allowed.

viol cell:
  - a main EdgeSeal donut whose outer corners are chamfered SHORT (~9.9 um < 21)
    -> Seal.k; it has NO surrounding Passiv ring -> Seal.n.
  - a second, far-away EdgeSeal donut -> two EdgeSeal polygons -> Seal.m
    (structurally unavoidable with two rings; deliberately in the golden set).
  - a 39/4 boundary that covers both rings but a Metal4 (50/0) box sits OUTSIDE
    it -> Seal.l.

clean cell:
  - a single EdgeSeal donut with 45-degree corners >= 21 um (Seal.k ok, Seal.m ok
    since one polygon), enclosed inside the hole of a Passiv donut (Seal.n ok),
    and one 39/4 box covering every drawn shape so nothing is outside (Seal.l ok).

Everything is placed on the 5 nm grid (dbu = 0.001 um, all coords multiples of
5 dbu) and structures are spaced far apart so they never interact.

Regenerate with:  python3 gen_sealring_testcase.py   (writes sealring.gds here)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert)
L_EDGESEAL = (39, 0)
L_EDGESEAL_BND = (39, 4)
L_PASSIV = (9, 0)
L_METAL4 = (50, 0)
L_TEXT = (63, 0)

DBU = 0.001  # um per database unit

# Chamfer legs. A 45-degree corner edge of leg c has length c * sqrt(2).
#   clean: c = 14850 dbu -> length ~= 21001 dbu = 21.001 um  (>= Seal_k = 21.00)
#   viol : c =  7000 dbu -> length ~=  9899 dbu =  9.899 um  (<  Seal_k)
CHAMFER_CLEAN = 14850
CHAMFER_VIOL = 7000


def _box(cell, lidx, x0, y0, x1, y1):
    cell.shapes(lidx).insert(db.Box(x0, y0, x1, y1))


def _text(cell, tidx, x, y, s):
    cell.shapes(tidx).insert(db.Text(s, db.Trans(db.Vector(x, y))))


def _chamfered_square(x0, y0, x1, y1, c):
    """Return a db.Polygon: the square [x0,x1]x[y0,y1] with all four corners
    cut back by leg `c`, yielding four 45-degree corner edges."""
    pts = [
        db.Point(x0 + c, y0),
        db.Point(x1 - c, y0),
        db.Point(x1, y0 + c),
        db.Point(x1, y1 - c),
        db.Point(x1 - c, y1),
        db.Point(x0 + c, y1),
        db.Point(x0, y1 - c),
        db.Point(x0, y0 + c),
    ]
    return db.Polygon(pts)


def _seal_donut(x0, y0, x1, y1, c, hx0, hy0, hx1, hy1):
    """EdgeSeal donut region: chamfered outer square minus an axis-aligned
    rectangular hole (the hole keeps square corners, so it adds no 45-degree
    edge of its own)."""
    outer = db.Region(_chamfered_square(x0, y0, x1, y1, c))
    hole = db.Region(db.Box(hx0, hy0, hx1, hy1))
    return outer - hole


def _rect_donut(x0, y0, x1, y1, hx0, hy0, hx1, hy1):
    """Plain rectangular donut region (no 45-degree edges)."""
    return db.Region(db.Box(x0, y0, x1, y1)) - db.Region(db.Box(hx0, hy0, hx1, hy1))


def build():
    layout = db.Layout()
    layout.dbu = DBU
    seal = layout.layer(*L_EDGESEAL)
    bnd = layout.layer(*L_EDGESEAL_BND)
    passiv = layout.layer(*L_PASSIV)
    m4 = layout.layer(*L_METAL4)
    tx = layout.layer(*L_TEXT)

    # ---------------------------------------------------------------- #
    # viol cell -> {Seal.k, Seal.l, Seal.m, Seal.n}
    # ---------------------------------------------------------------- #
    viol = layout.create_cell("sealring_viol")
    _text(viol, tx, 0, -70000, "6.10 Sealring - FAIL")

    # Main EdgeSeal donut: SHORT 45-degree corners (Seal.k) and NO Passiv (Seal.n).
    main = _seal_donut(0, 0, 60000, 60000, CHAMFER_VIOL,
                       20000, 20000, 40000, 40000)
    viol.shapes(seal).insert(main)
    _text(viol, tx, 0, 62000, "Seal.k: short 45deg corners (~9.9um) + Seal.n: no Passiv ring")

    # Second EdgeSeal ring, far away -> two EdgeSeal polygons -> Seal.m.
    second = _rect_donut(200000, 0, 230000, 30000, 210000, 10000, 220000, 20000)
    viol.shapes(seal).insert(second)
    _text(viol, tx, 200000, 32000, "Seal.m: second sealring")

    # 39/4 boundary covering BOTH rings (but not the Metal4 box below).
    _box(viol, bnd, -10000, -10000, 240000, 70000)

    # Metal4 box OUTSIDE the boundary -> Seal.l.
    _box(viol, m4, 0, -60000, 5000, -55000)
    _text(viol, tx, 8000, -58000, "Seal.l: Metal4 outside boundary")

    # ---------------------------------------------------------------- #
    # clean cell -> {}
    # ---------------------------------------------------------------- #
    clean = layout.create_cell("sealring_clean")
    _text(clean, tx, 0, -25000, "6.10 Sealring - PASS")

    # Single EdgeSeal donut with LONG 45-degree corners (~21.001 um >= 21).
    seal_clean = _seal_donut(0, 0, 60000, 60000, CHAMFER_CLEAN,
                             20000, 20000, 40000, 40000)
    clean.shapes(seal).insert(seal_clean)
    _text(clean, tx, 0, 62000, "Seal.k ok: 45deg corners ~21.001um; Seal.m ok: one ring")

    # Passiv donut whose hole (-5000..65000) fully contains the EdgeSeal
    # (bbox 0..60000) -> unbroken Passiv ring encloses the sealring (Seal.n ok).
    passiv_clean = _rect_donut(-15000, -15000, 75000, 75000,
                               -5000, -5000, 65000, 65000)
    clean.shapes(passiv).insert(passiv_clean)
    _text(clean, tx, 0, 78000, "Seal.n ok: Passiv ring hole encloses EdgeSeal")

    # One 39/4 boundary covering every drawn shape -> nothing outside (Seal.l ok).
    _box(clean, bnd, -20000, -20000, 80000, 80000)

    return layout


def main():
    out = Path(__file__).resolve().parent / "sealring.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
