#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Solder Bump DRC unit testcase (`solderbump.gds`) for the interposer,
following the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with
intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - solderbump_viol : one violating structure per rule
                      -> expects {Padb.a, Padb.b, Padb.c, Padb.d, Padb.f}
  - solderbump_clean: near-limit legal versions -> expects {}

Device recognition (layers_def.drc): sbumppad = passiv_sbump(9/36) AND dfpad_sbump(41/36).
Each opening is therefore drawn identically on BOTH 9/36 and 41/36. TopMetal2 (134/0)
provides the enclosure, EdgeSeal is 39/0.

Openings are octagons/circles/squares. Octagons are drawn with exactly 8 points
(2 horizontal + 2 vertical + 4 diagonal-45 edges) and a square bounding box so that
get_octagon() recognizes them; circles use 256 grid-snapped points with exact cardinal
vertices so get_circle() recognizes them (bbox aspect 1, area ratio ~4/pi). All
coordinates are snapped to the 5 nm grid.

Regenerate with:  python3 gen_solderbump_testcase.py   (writes solderbump.gds next to it)
"""

import math
from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert)
L_PASSIV_SBUMP = (9, 36)
L_DFPAD_SBUMP = (41, 36)
L_TOPMETAL2 = (134, 0)
L_EDGESEAL = (39, 0)
L_TEXT = (63, 0)

GRID = 0.005  # 5 nm grid (um)

PAD = 60.0    # Padb_a : nominal SBumpPad opening size (um)
OCT_C = 17.5  # octagon chamfer for a 60 um bbox (on grid, diagonals at exactly 45 deg)
TM2_ENC = 10.0  # Padb_c : nominal legal TopMetal2 enclosure (um)


def snap(v):
    """Snap a micron value to the 5 nm grid."""
    return round(v / GRID) * GRID


def octagon_points(x0, y0, s, c):
    """8-point octagon in bbox [x0,x0+s] x [y0,y0+s], chamfer c (diagonals at 45 deg)."""
    raw = [
        (x0 + c, y0),
        (x0 + s - c, y0),
        (x0 + s, y0 + c),
        (x0 + s, y0 + s - c),
        (x0 + s - c, y0 + s),
        (x0 + c, y0 + s),
        (x0, y0 + s - c),
        (x0, y0 + c),
    ]
    return [db.DPoint(snap(px), snap(py)) for px, py in raw]


def circle_points(cx, cy, r, n=256):
    """n-point circle with exact cardinal vertices (n divisible by 4)."""
    pts = []
    for i in range(n):
        th = 2.0 * math.pi * i / n
        pts.append(db.DPoint(snap(cx + r * math.cos(th)), snap(cy + r * math.sin(th))))
    return pts


def square_points(x0, y0, s):
    return [
        db.DPoint(x0, y0),
        db.DPoint(x0 + s, y0),
        db.DPoint(x0 + s, y0 + s),
        db.DPoint(x0, y0 + s),
    ]


def draw_opening(cell, layer_indices, points):
    """Insert the same polygon on every listed layer (sbump = 9/36 AND 41/36)."""
    poly = db.DPolygon(points)
    for li in layer_indices:
        cell.shapes(li).insert(poly)


def tm2_cover_box(cell, tm2_idx, x0, y0, x1, y1, enc):
    """TopMetal2 square that encloses bbox [x0,y0,x1,y1] by `enc` on all sides."""
    cell.shapes(tm2_idx).insert(db.DBox(x0 - enc, y0 - enc, x1 + enc, y1 + enc))


def strip(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def text(cell, tx_idx, x, y, s):
    cell.shapes(tx_idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def build():
    layout = db.Layout()
    layout.dbu = 0.001

    ps = layout.layer(*L_PASSIV_SBUMP)
    df = layout.layer(*L_DFPAD_SBUMP)
    tm2 = layout.layer(*L_TOPMETAL2)
    es = layout.layer(*L_EDGESEAL)
    tx = layout.layer(*L_TEXT)
    sbump = [ps, df]

    # =========================== violating cell ============================ #
    viol = layout.create_cell("solderbump_viol")
    text(viol, tx, 0.0, -40.0, "6.9 Solder Bump - FAIL")

    # (a) Padb.a: 55-across octagon (bbox 55x55 < 60), legal TM2 enclosure 10.
    ax = 0.0
    s55 = 55.0
    c55 = 15.0
    draw_opening(viol, sbump, octagon_points(ax, 0.0, s55, c55))
    tm2_cover_box(viol, tm2, ax, 0.0, ax + s55, s55, TM2_ENC)
    text(viol, tx, ax, s55 + 8.0, "Padb.a FAIL (55 octagon)")

    # (b) Padb.b: two 60 octagons at 60 um edge-to-edge (< 70 um space). The Padb.e
    #     pitch rule is no longer emitted by the deck. Each gets its own legal TM2 enclosure 10.
    bx1 = 300.0
    bx2 = bx1 + PAD + 60.0  # 60 um edge-to-edge gap
    for bx in (bx1, bx2):
        draw_opening(viol, sbump, octagon_points(bx, 0.0, PAD, OCT_C))
        tm2_cover_box(viol, tm2, bx, 0.0, bx + PAD, PAD, TM2_ENC)
    text(viol, tx, bx1, PAD + 8.0, "Padb.b FAIL (60 spaced 60)")

    # (c) Padb.c: 60 octagon with only 5 um TopMetal2 enclosure (< 10).
    cx = 650.0
    draw_opening(viol, sbump, octagon_points(cx, 0.0, PAD, OCT_C))
    tm2_cover_box(viol, tm2, cx, 0.0, cx + PAD, PAD, 5.0)
    text(viol, tx, cx, PAD + 8.0, "Padb.c FAIL (TM2 enc 5)")

    # (d) Padb.d: 60 octagon 40 um from an EdgeSeal strip (< 50). Legal TM2 enc 10.
    dx = 900.0
    draw_opening(viol, sbump, octagon_points(dx, 0.0, PAD, OCT_C))
    tm2_cover_box(viol, tm2, dx, 0.0, dx + PAD, PAD, TM2_ENC)
    es_left = dx + PAD + 40.0  # 40 um from the octagon right flat
    strip(viol, es, es_left, -20.0, es_left + 10.0, 80.0)
    text(viol, tx, dx, PAD + 8.0, "Padb.d FAIL (40 to edgeseal)")

    # (e) Padb.f: 60x60 square opening (neither circle nor octagon). Legal TM2 enc 10.
    ex = 1200.0
    draw_opening(viol, sbump, square_points(ex, 0.0, PAD))
    tm2_cover_box(viol, tm2, ex, 0.0, ex + PAD, PAD, TM2_ENC)
    text(viol, tx, ex, PAD + 8.0, "Padb.f FAIL (square)")

    # (f) Padb.c miss branch: legal 60 octagon with NO TopMetal2 underneath at all.
    #     enclosed() is blind to total non-overlap, so the miss branch
    #     (dfpad AND passiv NOT topmetal2) is what catches an opening with no landing.
    #     Placed 140 um (>= 70 um) from every other opening and 390 um (>= 50 um) from
    #     the EdgeSeal, so it exercises ONLY Padb.c (already in the expected set).
    gx = 1400.0
    draw_opening(viol, sbump, octagon_points(gx, 0.0, PAD, OCT_C))
    # NOTE: intentionally no tm2_cover_box here -> no TopMetal2 landing.
    text(viol, tx, gx, PAD + 8.0, "Padb.c FAIL (no TM2)")

    # ============================= clean cell ============================== #
    clean = layout.create_cell("solderbump_clean")
    text(clean, tx, 0.0, -40.0, "6.9 Solder Bump - PASS")

    # Legal 60 octagon, TM2 enclosure exactly 10, EdgeSeal exactly 50 um to the left.
    ox = 0.0
    draw_opening(clean, sbump, octagon_points(ox, 0.0, PAD, OCT_C))
    tm2_cover_box(clean, tm2, ox, 0.0, ox + PAD, PAD, TM2_ENC)
    es_right = ox - 50.0  # octagon left flat at x=0 -> exactly 50 um edge-to-edge
    strip(clean, es, es_right - 10.0, -20.0, es_right, 80.0)
    text(clean, tx, ox, PAD + 8.0, "Padb.* PASS (60 octagon, enc 10, 50 to seal)")

    # Legal 60 circle, TM2 enclosure exactly 10, EXACTLY 70 um edge-to-edge from the
    # octagon. This exercises Padb.b (min space 70 um) at-limit legal: the measured
    # spacing equals the nominal minimum, above the 69.99 um rule threshold. The Padb.e
    # pitch rule is no longer emitted by the deck.
    circ_left = ox + PAD + 70.0     # exactly 70 um edge-to-edge
    cxc = circ_left + PAD / 2.0     # center x
    cyc = PAD / 2.0                 # center y
    draw_opening(clean, sbump, circle_points(cxc, cyc, PAD / 2.0, 256))
    tm2_cover_box(clean, tm2, cxc - PAD / 2.0, cyc - PAD / 2.0,
                  cxc + PAD / 2.0, cyc + PAD / 2.0, TM2_ENC)
    text(clean, tx, circ_left, PAD + 8.0, "Padb.b PASS (60 circle, 70 space)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "solderbump.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
