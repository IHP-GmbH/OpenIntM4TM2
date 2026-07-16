#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the angle DRC unit testcase (`angle.gds`) for the interposer, following
the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with
intentional FAIL / PASS structures labeled by text on layer 63/0.

Two top cells, each checked as a whole by run_regression.py:
  - angle_viol : bad-angle shapes  -> expects {via4_drw_Angle90,
                                               metal4_drw_Angle45,
                                               metal4_drw_Acute}
  - angle_clean: legal shapes       -> expects {}

The via4 pentagon has a 45-degree chamfer edge (illegal on an orthogonal-only
layer -> Angle90) but only 90/135-degree corners (no Acute). The metal4 triangle
has a shallow non-0/45/90 edge (-> Angle45) and two acute corners (-> Acute).
The clean cell broadens coverage across every angle rule id: rectilinear shapes
on both Angle90 layers not hit by the viol cell (vmim, topvia2), true 45-degree
chamfered shapes on the Angle45 layers topmetal1/topmetal2, a polygon with an
88-degree corner on an acute-only layer (passiv, near-limit legal because the
acute check fires only below 87 degree) and a circle on metal4 (all-angle edges,
exempt via get_circle). All coordinates sit on the 5 nm grid except the circle's
off-axis vertices (exempt by design).

Regenerate with:  python gen_angle_testcase.py   (writes angle.gds here)
"""

import math
from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text.
L_METAL4 = (50, 0)
L_METAL5 = (67, 0)
L_VIA4 = (66, 0)
L_VMIM = (129, 0)
L_TOPVIA1 = (125, 0)
L_TOPMETAL1 = (126, 0)
L_TOPVIA2 = (133, 0)
L_TOPMETAL2 = (134, 0)
L_PASSIV = (9, 0)
L_TEXT = (63, 0)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _poly(cell, idx, pts):
    cell.shapes(idx).insert(db.DPolygon([db.DPoint(x, y) for x, y in pts]))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _circle(cell, idx, cx, cy, r, n=96):
    """Regular n-gon inscribed in radius r; axis points land exactly on (+-r) so
    the bbox stays square (aspect ratio 1) and get_circle() detects it. The
    off-axis vertices produce edges at every angle, which would flood Angle45 on
    metal4 if the circle were not exempted -- exactly what this proves."""
    pts = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        pts.append(db.DPoint(cx + r * math.cos(a), cy + r * math.sin(a)))
    cell.shapes(idx).insert(db.DPolygon(pts))


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    m4 = layout.layer(*L_METAL4)
    m5 = layout.layer(*L_METAL5)
    v4 = layout.layer(*L_VIA4)
    vmim = layout.layer(*L_VMIM)
    tv1 = layout.layer(*L_TOPVIA1)
    tm1 = layout.layer(*L_TOPMETAL1)
    tv2 = layout.layer(*L_TOPVIA2)
    tm2 = layout.layer(*L_TOPMETAL2)
    pv = layout.layer(*L_PASSIV)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("angle_viol")
    _text(viol, tx, 0.0, -1.5, "3.2 Angle - FAIL")

    # via4_drw_Angle90: pentagon with a 45-degree chamfer on an orthogonal-only
    # layer. Corners are 90/135 degree only, so it must NOT raise via4_drw_Acute.
    _poly(viol, v4, [(0.0, 0.0), (1.0, 0.0), (1.0, 0.6), (0.6, 1.0), (0.0, 1.0)])
    _text(viol, tx, 0.0, 1.3, "via4_drw_Angle90 FAIL")

    # metal4_drw_Angle45 + metal4_drw_Acute: right triangle with a shallow
    # (~16.7 degree) hypotenuse -> edge not 0/45/90, and acute corners.
    _poly(viol, m4, [(5.0, 0.0), (6.0, 0.0), (6.0, 0.3)])
    _text(viol, tx, 5.0, 1.3, "metal4_drw_Angle45+Acute FAIL")

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("angle_clean")
    _text(clean, tx, 0.0, -1.5, "3.2 Angle - PASS")

    # Rectilinear boxes on orthogonal-only and 0/45/90 layers. vmim and topvia2
    # are the two Angle90 layers the viol cell never touches, so they are added
    # here to exercise their orthogonal-only rule on legal geometry.
    _box(clean, v4, 0.0, 0.0, 0.19, 0.19)
    _box(clean, tv1, 1.0, 0.0, 1.19, 0.19)
    _box(clean, m4, 2.0, 0.0, 2.5, 0.5)
    _box(clean, m5, 3.0, 0.0, 3.5, 0.5)
    _box(clean, vmim, 4.0, 0.0, 4.19, 0.19)
    _box(clean, tv2, 5.0, 0.0, 5.19, 0.19)
    _text(clean, tx, 0.0, 0.8, "rectilinear PASS")

    # True 45-degree metal4 shape: chamfered square, all edges 0/45/90, all
    # corners 135 degree.
    _poly(clean, m4, [
        (6.2, 0.0), (6.8, 0.0), (7.0, 0.2), (7.0, 0.8),
        (6.8, 1.0), (6.2, 1.0), (6.0, 0.8), (6.0, 0.2),
    ])
    _text(clean, tx, 6.0, 1.3, "metal4 true-45 PASS")

    # Circle on metal4 (0/45/90 layer): all-angle edges, exempt via get_circle.
    _circle(clean, m4, 10.0, 0.5, 0.5)
    _text(clean, tx, 9.0, 1.3, "metal4 circle PASS (exempt)")

    # True 45-degree shapes on the remaining Angle45 layers (topmetal1/topmetal2):
    # identical chamfered squares, all edges 0/45/90, all corners 135 degree.
    _poly(clean, tm1, [
        (12.2, 0.0), (12.8, 0.0), (13.0, 0.2), (13.0, 0.8),
        (12.8, 1.0), (12.2, 1.0), (12.0, 0.8), (12.0, 0.2),
    ])
    _text(clean, tx, 12.0, 1.3, "topmetal1 true-45 PASS")
    _poly(clean, tm2, [
        (14.2, 0.0), (14.8, 0.0), (15.0, 0.2), (15.0, 0.8),
        (14.8, 1.0), (14.2, 1.0), (14.0, 0.8), (14.0, 0.2),
    ])
    _text(clean, tx, 14.0, 1.3, "topmetal2 true-45 PASS")

    # Near-limit acute corner on passiv (acute-only scope): a right trapezoid
    # whose lower-left corner is ~88 degree. The acute rule fires only below 87
    # degree, so this legal shape must NOT flag. Corners: 88 / 90 / 90 / 92.
    _poly(clean, pv, [(16.0, 0.0), (17.0, 0.0), (17.0, 1.0), (16.035, 1.0)])
    _text(clean, tx, 16.0, 1.3, "passiv 88-degree corner PASS (near-limit)")

    # Bump pad pattern: a circle on TopMetal2 MERGED with an on-grid routing bar
    # through its center. The raw-level circle exemption must still recognize
    # the drawn circle even though the merged polygon is no longer circular
    # (regression for the merged-pad Angle45 false positive).
    _circle(clean, tm2, 24.0, 0.5, 2.5, n=256)
    _box(clean, tm2, 18.0, 0.0, 30.0, 1.0)
    _text(clean, tx, 21.0, 3.5, "tm2 circle + routing bar PASS (raw exempt)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "angle.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
