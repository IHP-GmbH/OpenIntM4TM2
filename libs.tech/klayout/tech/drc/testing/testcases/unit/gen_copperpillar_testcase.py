#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Copper Pillar DRC unit testcase (`copperpillar.gds`) for the
interposer, following the IHP-SG13G2 `testing/testcases/unit/` convention: one
table GDS with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - copperpillar_viol : one structure per rule ->
        expects {Padc.a, Padc.b, Padc.c, Padc.d, Padc.e, Padc.f}
  - copperpillar_clean: the corresponding legal structures -> expects {}

A CuPillarPad is Passiv:pillar (9/35) AND dfpad:pillar (41/35); the deck derives
`cu_pillarpad = passiv_pillar.and(dfpad_pillar)`. Each pad here draws the passivation
opening at radius r on 9/35, and dfpad + TopMetal2 at r + enclosure on 41/35 and
134/0, so the recognised pad is the passivation circle and the TM2 enclosure equals
the drawn margin. Circles use 256 points to match the deck's circle-discretization
tolerances (Padc.b/.c/.e carry a 10 nm tolerance for exactly this reason).

Structures are placed on a wide stride so no two pads from different structures fall
within a spacing rule (Padc.b/.e = 40 um) or the EdgeSeal rule (Padc.d = 30 um).

Regenerate with:  python gen_copperpillar_testcase.py   (writes copperpillar.gds here)
"""

import math
from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert)
L_TM2 = (134, 0)
L_PASSIV_PILLAR = (9, 35)
L_DFPAD_PILLAR = (41, 35)
L_EDGESEAL = (39, 0)
L_TEXT = (63, 0)

# Rule values from interposer_tech_default.json (Table 6.1, Option 1)
PADC_A = 35.0    # CuPillarPad size (um)          -> pad diameter
PADC_B = 40.0    # min pad space (um)
PADC_C = 7.5     # min TM2 enclosure of opening (um)
PADC_D = 30.0    # min pad-to-EdgeSeal space (um)
PADC_E = 75.0    # min pad pitch (um)

STRIDE = 300.0   # between independent structures (>> Padc.b/e = 40, Padc.d = 30)
NPTS = 256       # circle approximation points (matches the deck tolerances)


def _circle(cell, idx, cx, cy, r, npts=NPTS):
    pts = [db.DPoint(cx + r * math.cos(2 * math.pi * i / npts),
                     cy + r * math.sin(2 * math.pi * i / npts))
           for i in range(npts)]
    cell.shapes(idx).insert(db.DPolygon(pts))


def _square(cell, idx, cx, cy, half):
    cell.shapes(idx).insert(db.DBox(cx - half, cy - half, cx + half, cy + half))


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _pad(cell, tm2, passiv, dfpad, cx, cy, diameter=PADC_A, encl=PADC_C,
         shape='circle'):
    """A CuPillarPad: passiv opening at r, dfpad + TM2 at r + enclosure."""
    r = diameter / 2.0
    ro = r + encl
    if shape == 'circle':
        _circle(cell, passiv, cx, cy, r)
        _circle(cell, dfpad, cx, cy, ro)
        _circle(cell, tm2, cx, cy, ro)
    else:  # square
        _square(cell, passiv, cx, cy, r)
        _square(cell, dfpad, cx, cy, ro)
        _square(cell, tm2, cx, cy, ro)


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    tm2 = layout.layer(*L_TM2)
    passiv = layout.layer(*L_PASSIV_PILLAR)
    dfpad = layout.layer(*L_DFPAD_PILLAR)
    es = layout.layer(*L_EDGESEAL)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures ------------------------------------------- #
    viol = layout.create_cell("copperpillar_viol")
    _text(viol, tx, 0.0, -60.0, "6.9.2 Copper Pillar - FAIL")

    # Padc.a FAIL: undersize pad (30 um < 35 um), enclosure/shape otherwise legal.
    ox = 0.0
    _pad(viol, tm2, passiv, dfpad, ox, 0.0, diameter=30.0, encl=PADC_C)
    _text(viol, tx, ox, 30.0, "Padc.a FAIL (30um)")

    # Padc.c FAIL: correct 35 um pad but only 5 um TM2 enclosure (< 7.5 um).
    ox = STRIDE
    _pad(viol, tm2, passiv, dfpad, ox, 0.0, diameter=PADC_A, encl=5.0)
    _text(viol, tx, ox, 30.0, "Padc.c FAIL (encl 5um)")

    # Padc.b / Padc.e FAIL: two legal pads 30 um edge-to-edge (< 40, and pitch 65 < 75).
    # Both rules share the 40 um spacing threshold, so both fire.
    ox = 2 * STRIDE
    _pad(viol, tm2, passiv, dfpad, ox - 32.5, 0.0)
    _pad(viol, tm2, passiv, dfpad, ox + 32.5, 0.0)
    _text(viol, tx, ox, 30.0, "Padc.b/e FAIL (30um gap)")

    # Padc.d FAIL: legal pad 20 um from EdgeSeal (< 30 um).
    ox = 3 * STRIDE
    _pad(viol, tm2, passiv, dfpad, ox, 0.0)
    _box(viol, es, ox + 17.5 + 20.0, -60.0, ox + 17.5 + 25.0, 60.0)
    _text(viol, tx, ox, 30.0, "Padc.d FAIL (20um to seal)")

    # Padc.f FAIL: square passivation opening (circle only allowed).
    ox = 4 * STRIDE
    _pad(viol, tm2, passiv, dfpad, ox, 0.0, shape='square')
    _text(viol, tx, ox, 30.0, "Padc.f FAIL (square)")

    # ---- clean structures ----------------------------------------------- #
    clean = layout.create_cell("copperpillar_clean")
    _text(clean, tx, 0.0, -60.0, "6.9.2 Copper Pillar - PASS")

    # Single legal pad: 35 um circle, 7.5 um enclosure (guards Padc.a/.c/.f).
    ox = 0.0
    _pad(clean, tm2, passiv, dfpad, ox, 0.0)
    _text(clean, tx, ox, 30.0, "Padc.a/c/f PASS")

    # Legal pair: 45 um edge-to-edge (> 40) so Padc.b/.e stay clean.
    ox = STRIDE
    _pad(clean, tm2, passiv, dfpad, ox - 40.0, 0.0)
    _pad(clean, tm2, passiv, dfpad, ox + 40.0, 0.0)
    _text(clean, tx, ox, 30.0, "Padc.b/e PASS (45um gap)")

    # Legal pad 35 um from EdgeSeal (> 30) so Padc.d stays clean.
    ox = 2 * STRIDE
    _pad(clean, tm2, passiv, dfpad, ox, 0.0)
    _box(clean, es, ox + 17.5 + 35.0, -60.0, ox + 17.5 + 40.0, 60.0)
    _text(clean, tx, ox, 30.0, "Padc.d PASS (35um to seal)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "copperpillar.gds"
    build().write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
