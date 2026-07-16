#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Metaln:filler DRC unit testcase (`metalnfiller.gds`) for the
interposer, following the IHP-SG13G2 `testing/testcases/unit/` convention: one
table GDS with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - metalnfiller_viol : violating structures  -> expects {M4Fil.c, M4Fil.a2,
                                                           M5Fil.c, M5Fil.a2}
  - metalnfiller_clean: near-limit legal ones -> expects {} (guards all rules)

Rules exercised (deck key: metalnfiller):
  - M{n}Fil.c : min. Metal(n):filler space to drawn Metal(n) is 0.42 um
  - M{n}Fil.a2: max. Metal(n):filler width is 5.00 um

Metal4 structures live in the y=0 band, Metal5 in the y=20 band; structures are
spaced well beyond the 0.42 space rule so they never interact.
All coordinates are on the 5 nm grid.

Regenerate with:  python gen_metalnfiller_testcase.py   (writes metalnfiller.gds)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text.
L_METAL4_DRW = (50, 0)
L_METAL4_FIL = (50, 22)
L_METAL5_DRW = (67, 0)
L_METAL5_FIL = (67, 22)
L_TEXT = (63, 0)

MFIL_C = 0.42        # min filler-to-drawn-metal space (um)
MFIL_A2 = 5.0        # max filler width (um)

C_GAP_VIOL = 0.30    # < MFIL_C  -> M{n}Fil.c fires
C_GAP_CLEAN = 0.42   # == MFIL_C -> legal (space rule is strict-less-than)
A2_LEN_VIOL = 6.0    # > MFIL_A2 -> M{n}Fil.a2 fires
A2_LEN_CLEAN = 5.0   # == MFIL_A2 -> legal (with_bbox_max threshold is +0.001)

A2_ORIGIN_X = 15.0   # x-origin of the width structure (far from the space one)
BAND_M5_Y = 20.0     # y-band separating the Metal5 group from Metal4


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _draw_group(cell, drw, fil, tx, y0, metal_no, c_gap, a2_len):
    """Draw the two filler structures for one metal at y-band y0.

    Structure 1 (space): a 2x1 drawn-metal line with a 1x1 filler c_gap to its
    right -> exercises M{n}Fil.c.
    Structure 2 (width): a single a2_len x 1 filler bar, far from any drawn
    metal -> exercises M{n}Fil.a2 only.
    """
    # Structure 1: filler-to-drawn-metal space
    _box(cell, drw, 0.0, y0, 2.0, y0 + 1.0)
    _box(cell, fil, 2.0 + c_gap, y0, 3.0 + c_gap, y0 + 1.0)
    _text(cell, tx, 0.0, y0 + 1.3, f"M{metal_no}Fil.c gap={c_gap}")

    # Structure 2: max filler width (1x1 tall bar of length a2_len)
    _box(cell, fil, A2_ORIGIN_X, y0, A2_ORIGIN_X + a2_len, y0 + 1.0)
    _text(cell, tx, A2_ORIGIN_X, y0 + 1.3, f"M{metal_no}Fil.a2 len={a2_len}")


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    m4d = layout.layer(*L_METAL4_DRW)
    m4f = layout.layer(*L_METAL4_FIL)
    m5d = layout.layer(*L_METAL5_DRW)
    m5f = layout.layer(*L_METAL5_FIL)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("metalnfiller_viol")
    _text(viol, tx, 0.0, -1.5, "5.18 Metaln:filler - FAIL")
    _draw_group(viol, m4d, m4f, tx, 0.0, 4, C_GAP_VIOL, A2_LEN_VIOL)
    _draw_group(viol, m5d, m5f, tx, BAND_M5_Y, 5, C_GAP_VIOL, A2_LEN_VIOL)

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("metalnfiller_clean")
    _text(clean, tx, 0.0, -1.5, "5.18 Metaln:filler - PASS")
    _draw_group(clean, m4d, m4f, tx, 0.0, 4, C_GAP_CLEAN, A2_LEN_CLEAN)
    _draw_group(clean, m5d, m5f, tx, BAND_M5_Y, 5, C_GAP_CLEAN, A2_LEN_CLEAN)

    return layout


def main():
    out = Path(__file__).resolve().parent / "metalnfiller.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
