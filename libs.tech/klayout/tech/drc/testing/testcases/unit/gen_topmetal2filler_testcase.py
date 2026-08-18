#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the TopMetal2:filler DRC unit testcase (`topmetal2filler.gds`) for the
interposer, following the IHP-SG13G2 `testing/testcases/unit/` convention: one
table GDS with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - topmetal2filler_viol : violating structures  -> expects {TM2Fil.c, TM2Fil.a1, TM2Fil.b}
  - topmetal2filler_clean: near-limit legal ones -> expects {} (guards all rules)

Rules exercised (deck key: topmetal2filler):
  - TM2Fil.c : min. TopMetal2:filler space to drawn TopMetal2 is 3.00 um
  - TM2Fil.a1: max. TopMetal2:filler width is 10.00 um
  - TM2Fil.b : min. TopMetal2:filler space to TopMetal2:filler is 3.00 um

Structures are spaced well beyond the 3.0 space rule so they never interact.
All coordinates are on the 5 nm grid.

Regenerate with:  python gen_topmetal2filler_testcase.py  (writes topmetal2filler.gds)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text.
L_TM2_DRW = (134, 0)
L_TM2_FIL = (134, 22)
L_TEXT = (63, 0)

TM2FIL_C = 3.0        # min filler-to-drawn-metal space (um)
TM2FIL_A1 = 10.0      # max filler width (um)
TM2FIL_B = 3.0        # min filler-to-filler space (um)

C_GAP_VIOL = 2.0      # < TM2FIL_C  -> TM2Fil.c fires
C_GAP_CLEAN = 3.0     # == TM2FIL_C -> legal (space rule is strict-less-than)
A1_LEN_VIOL = 11.0    # > TM2FIL_A1 -> TM2Fil.a1 fires
A1_LEN_CLEAN = 10.0   # == TM2FIL_A1 -> legal (with_bbox_max threshold is +0.001)
B_GAP_VIOL = 2.0      # < TM2FIL_B  -> TM2Fil.b fires
B_GAP_CLEAN = 3.0     # == TM2FIL_B -> legal (space rule is strict-less-than)

A1_ORIGIN_X = 25.0    # x-origin of the width structure (far from the space one)
B_ORIGIN_X = 50.0     # x-origin of the filler-to-filler structure


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _draw_group(cell, drw, fil, tx, c_gap, a1_len, b_gap):
    """Draw the three filler structures.

    Structure 1 (space): a 3x3 drawn-metal pad with a 2x2 filler c_gap to its
    right -> exercises TM2Fil.c.
    Structure 2 (width): a single a1_len x 2 filler bar, far from any drawn
    metal -> exercises TM2Fil.a1 only.
    Structure 3 (filler-to-filler): two 4x2 filler bars b_gap apart, far from any
    drawn metal -> exercises TM2Fil.b only.
    """
    # Structure 1: filler-to-drawn-metal space
    _box(cell, drw, 0.0, 0.0, 3.0, 3.0)
    _box(cell, fil, 3.0 + c_gap, 0.0, 5.0 + c_gap, 2.0)
    _text(cell, tx, 0.0, 3.3, f"TM2Fil.c gap={c_gap}")

    # Structure 2: max filler width (2 tall bar of length a1_len)
    _box(cell, fil, A1_ORIGIN_X, 0.0, A1_ORIGIN_X + a1_len, 2.0)
    _text(cell, tx, A1_ORIGIN_X, 3.3, f"TM2Fil.a1 len={a1_len}")

    # Structure 3: filler-to-filler space (two 4x2 bars b_gap apart)
    _box(cell, fil, B_ORIGIN_X, 0.0, B_ORIGIN_X + 4.0, 2.0)
    _box(cell, fil, B_ORIGIN_X + 4.0 + b_gap, 0.0, B_ORIGIN_X + 8.0 + b_gap, 2.0)
    _text(cell, tx, B_ORIGIN_X, 3.3, f"TM2Fil.b gap={b_gap}")


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    d = layout.layer(*L_TM2_DRW)
    f = layout.layer(*L_TM2_FIL)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("topmetal2filler_viol")
    _text(viol, tx, 0.0, -1.5, "5.26 TopMetal2:filler - FAIL")
    _draw_group(viol, d, f, tx, C_GAP_VIOL, A1_LEN_VIOL, B_GAP_VIOL)

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("topmetal2filler_clean")
    _text(clean, tx, 0.0, -1.5, "5.26 TopMetal2:filler - PASS")
    _draw_group(clean, d, f, tx, C_GAP_CLEAN, A1_LEN_CLEAN, B_GAP_CLEAN)

    return layout


def main():
    out = Path(__file__).resolve().parent / "topmetal2filler.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
