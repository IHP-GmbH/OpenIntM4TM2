#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the TopMetal2 DRC unit testcase (`topmetal2.gds`) for the interposer,
following the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with
intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - topmetal2_viol : the violating structure  -> expects {TM2.bR}
  - topmetal2_clean: the corresponding legal structure -> expects {}
                     (also guards TM2.a min width and TM2.b min space)

TM2.bR is the recommended wide-line spacing rule: if at least one TopMetal2 line is
wider than TM2_bR_w (5.0 um) and the parallel run of two lines exceeds TM2_bR_cr
(50.0 um), the required space grows to TM2_bR (5.0 um).

Both cells use a pair of parallel lines, one 6.0 um wide (> TM2_bR_w) and one 3.0 um
wide, both 60 um long (parallel run 60 > 50). The only difference is the gap:
  - viol : 4.0 um gap -> passes TM2.b (2.0) but violates TM2.bR (< 5.0)
  - clean: 5.0 um gap -> legal for both TM2.b and TM2.bR

All coordinates lie on the 5 nm grid (dbu 0.001).

Regenerate with:  python gen_topmetal2_testcase.py   (writes topmetal2.gds next to this file)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert for the deck)
L_TOPMETAL2 = (134, 0)
L_TEXT = (63, 0)

WIDE = 6.0       # > TM2_bR_w (5.0 um): the line that triggers the wide-line spacing rule
NARROW = 3.0     # >= TM2_a (2.0 um): min-width-legal companion line
LEN = 60.0       # > TM2_bR_cr (50.0 um): parallel run long enough to arm TM2.bR
GAP_VIOL = 4.0   # >= TM2_b (2.0) but < TM2_bR (5.0) -> TM2.bR fires
GAP_CLEAN = 5.0  # == TM2_bR (5.0) -> legal (rule needs strictly less than 5.0)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _line_pair(cell, tm2, tx, gap, tag):
    """Draw a wide + narrow parallel TopMetal2 line pair separated by `gap` (edge-to-edge)."""
    # Wide line at x [0, WIDE]
    _box(cell, tm2, 0.0, 0.0, WIDE, LEN)
    # Narrow line at x [WIDE + gap, WIDE + gap + NARROW]
    nx0 = WIDE + gap
    _box(cell, tm2, nx0, 0.0, nx0 + NARROW, LEN)
    _text(cell, tx, 0.0, LEN + 1.0, tag)


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    tm2 = layout.layer(*L_TOPMETAL2)
    tx = layout.layer(*L_TEXT)

    # ---- violating structure --------------------------------------------- #
    viol = layout.create_cell("topmetal2_viol")
    _text(viol, tx, 0.0, -2.0, "5.25 TopMetal2 - FAIL")
    _line_pair(viol, tm2, tx, GAP_VIOL,
               "TM2.bR FAIL (wide 6.0, run 60 > 50, gap 4.0 < 5.0)")

    # ---- clean structure ------------------------------------------------- #
    clean = layout.create_cell("topmetal2_clean")
    _text(clean, tx, 0.0, -2.0, "5.25 TopMetal2 - PASS")
    _line_pair(clean, tm2, tx, GAP_CLEAN,
               "TM2.bR PASS (wide 6.0, run 60, gap 5.0 == limit)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "topmetal2.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
