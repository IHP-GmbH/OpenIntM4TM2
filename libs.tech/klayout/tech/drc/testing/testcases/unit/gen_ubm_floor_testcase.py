#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the UBM / passivation-opening floor DRC unit testcase
(`ubm_floor.gds`) for the interposer, following the IHP-SG13G2
`testing/testcases/unit/` convention: one table GDS with intentional PASS / FAIL
structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - ubm_floor_viol : one structure per rule ->
        expects {PadU.w, PadU.s}
  - ubm_floor_clean: the corresponding legal structures -> expects {}

The deck (6_9_ubm_floor.drc) derives `ubm_open = passiv_pillar.join(passiv_sbump)`,
i.e. the union of the Cu-pillar opening datatype 9/35 and the solder-bump opening
datatype 9/36, then checks a minimum opening width (PadU.w) and a minimum
opening-to-opening space (PadU.s), both the method-independent 5.27 passivation-
opening litho floor values Pas.a (2.10 um) / Pas.b (3.50 um). The values carry a
10 nm tolerance for the polygon discretization of circular openings; circles here
use 256 points to match it.

The PadU.s structure straddles both datatypes (one opening on 9/35, one on 9/36)
so it also proves the cross-datatype 35-to-36 separation that nothing else checks.

Structures are placed on a wide stride so no two openings from different
structures fall within the spacing rule (PadU.s = 3.50 um).

Regenerate with:  python gen_ubm_floor_testcase.py   (writes ubm_floor.gds here)
"""

import math
from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert)
L_PASSIV_PILLAR = (9, 35)
L_PASSIV_SBUMP = (9, 36)
L_TEXT = (63, 0)

# Rule values from interposer_tech_default.json (5.27 litho floor, from Pas.a / Pas.b)
PADU_W = 2.10    # min UBM/passivation opening width (um)
PADU_S = 3.50    # min UBM/passivation opening space (um)

STRIDE = 300.0   # between independent structures (>> PadU.s = 3.50)
NPTS = 256       # circle approximation points (matches the deck tolerance)


def _circle(cell, idx, cx, cy, r, npts=NPTS):
    pts = [db.DPoint(cx + r * math.cos(2 * math.pi * i / npts),
                     cy + r * math.sin(2 * math.pi * i / npts))
           for i in range(npts)]
    cell.shapes(idx).insert(db.DPolygon(pts))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    pillar = layout.layer(*L_PASSIV_PILLAR)
    sbump = layout.layer(*L_PASSIV_SBUMP)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures ------------------------------------------- #
    viol = layout.create_cell("ubm_floor_viol")
    _text(viol, tx, 0.0, -60.0, "6.9 UBM floor - FAIL")

    # PadU.w FAIL: a single 1.5 um-diameter opening on 9/35 (width 1.5 < 2.10).
    # Isolated (>> STRIDE from anything else) so it adds no spurious PadU.s.
    ox = 0.0
    _circle(viol, pillar, ox, 0.0, 0.75)
    _text(viol, tx, ox, 30.0, "PadU.w FAIL (1.5um)")

    # PadU.s FAIL: two width-legal 6.0 um-diameter openings 2.0 um edge-to-edge
    # (< 3.50), one on 9/35 and one on 9/36. Centers 8.0 um apart give an exact
    # 2.0 um gap (8.0 - 3.0 - 3.0). Width 6.0 > 2.10, so no spurious PadU.w. This
    # also exercises the cross-datatype 35-to-36 separation.
    ox = STRIDE
    _circle(viol, pillar, ox - 4.0, 0.0, 3.0)
    _circle(viol, sbump, ox + 4.0, 0.0, 3.0)
    _text(viol, tx, ox, 30.0, "PadU.s FAIL (2.0um gap, 35-to-36)")

    # ---- clean structures ----------------------------------------------- #
    clean = layout.create_cell("ubm_floor_clean")
    _text(clean, tx, 0.0, -60.0, "6.9 UBM floor - PASS")

    # Single legal opening: 5.0 um circle on 9/35 (width > 2.10), isolated.
    # A euclidian width check on a discretized circle produces spurious short
    # interior chord violations when the diameter sits within ~1 um of the
    # threshold (the 2.09 to ~3.0 um band here); a realistic UBM opening is far
    # larger (Padc.a = 35 um, Padb.a = 60 um), so the clean fixture uses a
    # diameter with margin above that artifact band.
    ox = 0.0
    _circle(clean, pillar, ox, 0.0, 2.5)
    _text(clean, tx, ox, 30.0, "PadU.w PASS (5.0um)")

    # Legal pair: two 6.0 um openings 5.0 um edge-to-edge (> 3.50), one on 9/35
    # and one on 9/36. Centers 11.0 um apart give a 5.0 um gap (cross-datatype
    # clean).
    ox = STRIDE
    _circle(clean, pillar, ox - 5.5, 0.0, 3.0)
    _circle(clean, sbump, ox + 5.5, 0.0, 3.0)
    _text(clean, tx, ox, 30.0, "PadU.s PASS (5.0um gap, 35-to-36)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "ubm_floor.gds"
    build().write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
