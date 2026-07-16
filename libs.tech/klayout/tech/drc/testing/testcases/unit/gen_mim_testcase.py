#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the MIM DRC unit testcase (`mim.gds`) for the interposer, following the
IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with intentional
PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - mim_viol : one violating structure per rule  -> expects {MIM.c, MIM.d, MIM.gR}
  - mim_clean: the corresponding legal structures -> expects {}

Rules exercised (deck 6_11_mim.drc):
  - MIM.c  : min. Metal5 enclosure of MIM is 0.60 um (plus no-landing miss branch)
  - MIM.d  : min. MIM enclosure of TopVia1 is 0.36 um
  - MIM.gR : max. recommended total MIM area per chip is 174800.00 um2

Structures are spaced far apart so they never interact.
All coordinates are on the 5 nm grid; dbu = 0.001.

Regenerate with:  python gen_mim_testcase.py   (writes mim.gds next to this file)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert for the deck)
L_MIM = (36, 0)
L_METAL5 = (67, 0)
L_TOPVIA1 = (125, 0)
L_TEXT = (63, 0)

MIM_C = 0.60      # Mim_c : min Metal5 enclosure of MIM (um)
MIM_D = 0.36      # Mim_d : min MIM enclosure of TopVia1 (um)
TV1 = 0.42        # TopVia1 cut size used in the tests (um)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    mim = layout.layer(*L_MIM)
    m5 = layout.layer(*L_METAL5)
    tv1 = layout.layer(*L_TOPVIA1)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("mim_viol")
    _text(viol, tx, 0.0, -1.5, "6.11 MIM - FAIL")

    # (a) MIM.c FAIL: 5x5 MIM, Metal5 encloses only 0.30 (< 0.60).
    _box(viol, mim, 0.0, 0.0, 5.0, 5.0)
    _box(viol, m5, -0.30, -0.30, 5.30, 5.30)
    _text(viol, tx, 0.0, 5.3, "MIM.c FAIL (M5 enc 0.30)")

    # (b) MIM.c FAIL (miss branch): 5x5 MIM with NO Metal5 landing.
    _box(viol, mim, 15.0, 0.0, 20.0, 5.0)
    _text(viol, tx, 15.0, 5.3, "MIM.c FAIL (no M5)")

    # (c) MIM.d FAIL: MIM (Metal5 enc 0.60 OK) with a 0.42x0.42 TopVia1 only
    #     0.20 inside the left MIM edge (< 0.36); MIM.c must NOT fire here.
    _box(viol, mim, 30.0, 30.0, 33.0, 33.0)
    _box(viol, m5, 29.40, 29.40, 33.60, 33.60)
    _box(viol, tv1, 30.20, 31.0, 30.20 + TV1, 31.0 + TV1)
    _text(viol, tx, 30.0, 33.3, "MIM.d FAIL (TopVia1 enc 0.20)")

    # (d) MIM.gR FAIL: large MIM plate so the TOTAL MIM area in the cell
    #     exceeds 174800 um2 (420 x 420 = 176400 um2).
    _box(viol, mim, 100.0, 0.0, 520.0, 420.0)
    _text(viol, tx, 100.0, 420.3, "MIM.gR FAIL (total area > 174800 um2)")

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("mim_clean")
    _text(clean, tx, 0.0, -1.5, "6.11 MIM - PASS")

    # Small MIM: Metal5 encloses by exactly 0.60 (legal), TopVia1 enclosed by
    # MIM by exactly 0.36 (legal). Total MIM area far below the gR limit.
    _box(clean, mim, 0.0, 0.0, 3.0, 3.0)
    _box(clean, m5, -MIM_C, -MIM_C, 3.0 + MIM_C, 3.0 + MIM_C)
    _box(clean, tv1, MIM_D, MIM_D, MIM_D + TV1, MIM_D + TV1)
    _text(clean, tx, 0.0, 3.3, "MIM.c/MIM.d PASS (enc 0.60 / 0.36)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "mim.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
