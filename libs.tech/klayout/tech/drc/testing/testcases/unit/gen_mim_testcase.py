#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the MIM DRC unit testcase (`mim.gds`) for the interposer, following the
IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with intentional
PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - mim_viol : one violating structure per rule
               -> expects {MIM.a, MIM.b, MIM.c, MIM.d, MIM.e, MIM.f, MIM.g,
                           MIM.h, MIM.gR}
  - mim_clean: the corresponding legal structures -> expects {}

Rules exercised (deck 6_11_mim.drc):
  - MIM.a  : min. MIM width is 1.14 um
  - MIM.b  : min. MIM space is 0.60 um
  - MIM.c  : min. Metal5 enclosure of MIM is 0.60 um (plus no-landing miss branch)
  - MIM.d  : min. MIM enclosure of TopVia1 is 0.36 um
  - MIM.e  : min. TopMetal1 space to MIM is 0.60 um
  - MIM.f  : min. MIM area per device is 1.30 um2 (exactly 1.30 is legal)
  - MIM.g  : max. MIM area per device is 5625.00 um2 (exactly 5625 is legal)
  - MIM.h  : every MIM device must completely cover a TopVia1 or Vmim via
  - MIM.gR : max. recommended total MIM area per chip is 174800.00 um2

Every MIM plate that is not meant to violate MIM.c / MIM.h carries a legal
Metal5 landing (enclosure 0.60) and one covered Vmim via, so each construct
fires exactly its own rule. The MIM.gR budget in mim_viol comes from an array
of 74x74 um plates (5476 um2 each, below the per-device MIM.g limit) whose
total pushes the cell above 174800 um2.

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
L_TOPMETAL1 = (126, 0)
L_VMIM = (129, 0)
L_TEXT = (63, 0)

MIM_A = 1.14      # Mim_a : min MIM width (um)
MIM_B = 0.60      # Mim_b : min MIM space (um)
MIM_C = 0.60      # Mim_c : min Metal5 enclosure of MIM (um)
MIM_D = 0.36      # Mim_d : min MIM enclosure of TopVia1 (um)
MIM_E = 0.60      # Mim_e : min TopMetal1 space to MIM (um)
TV1 = 0.42        # TopVia1 / Vmim cut size used in the tests (um)
GRID = 0.005      # 5 nm layout grid (um)


def _snap(v):
    """Snap a coordinate to the 5 nm grid."""
    return round(round(v / GRID) * GRID, 6)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


class _Layers:
    def __init__(self, layout):
        self.mim = layout.layer(*L_MIM)
        self.m5 = layout.layer(*L_METAL5)
        self.tv1 = layout.layer(*L_TOPVIA1)
        self.tm1 = layout.layer(*L_TOPMETAL1)
        self.vmim = layout.layer(*L_VMIM)
        self.tx = layout.layer(*L_TEXT)


def _mim_plate(cell, ly, x0, y0, x1, y1, m5=True, via=True):
    """Draw a MIM plate; by default with a legal Metal5 landing (enclosure
    MIM_C) and one centered, covered Vmim via so only the rule under test can
    fire on this plate."""
    _box(cell, ly.mim, x0, y0, x1, y1)
    if m5:
        _box(cell, ly.m5, x0 - MIM_C, y0 - MIM_C, x1 + MIM_C, y1 + MIM_C)
    if via:
        vx = _snap(x0 + ((x1 - x0) - TV1) / 2)
        vy = _snap(y0 + ((y1 - y0) - TV1) / 2)
        _box(cell, ly.vmim, vx, vy, vx + TV1, vy + TV1)


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    ly = _Layers(layout)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("mim_viol")
    _text(viol, ly.tx, 0.0, -3.0, "6.11 MIM - FAIL")

    # (a) MIM.a FAIL: 1.00 um wide MIM line (< 1.14); area 5.0 um2 is legal.
    _mim_plate(viol, ly, 0.0, 0.0, 1.0, 5.0)
    _text(viol, ly.tx, 0.0, 5.3, "MIM.a FAIL (width 1.00)")

    # (b) MIM.b FAIL: two 2x2 MIM plates only 0.30 apart (< 0.60), one
    #     shared Metal5 landing with legal enclosure.
    _mim_plate(viol, ly, 30.0, 0.0, 32.0, 2.0, m5=False)
    _mim_plate(viol, ly, 32.30, 0.0, 34.30, 2.0, m5=False)
    _box(viol, ly.m5, 30.0 - MIM_C, -MIM_C, 34.30 + MIM_C, 2.0 + MIM_C)
    _text(viol, ly.tx, 30.0, 2.3, "MIM.b FAIL (space 0.30)")

    # (c) MIM.c FAIL: 5x5 MIM, Metal5 encloses only 0.30 (< 0.60).
    _mim_plate(viol, ly, 60.0, 0.0, 65.0, 5.0, m5=False)
    _box(viol, ly.m5, 59.70, -0.30, 65.30, 5.30)
    _text(viol, ly.tx, 60.0, 5.3, "MIM.c FAIL (M5 enc 0.30)")

    # (d) MIM.c FAIL (miss branch): 5x5 MIM with NO Metal5 landing at all.
    _mim_plate(viol, ly, 90.0, 0.0, 95.0, 5.0, m5=False)
    _text(viol, ly.tx, 90.0, 5.3, "MIM.c FAIL (no M5)")

    # (e) MIM.d FAIL: MIM (Metal5 enc 0.60 OK) with a 0.42x0.42 TopVia1 only
    #     0.20 inside the left MIM edge (< 0.36); MIM.c must NOT fire here and
    #     the TopVia1 is still covered, so MIM.h must NOT fire either.
    _mim_plate(viol, ly, 120.0, 0.0, 123.0, 3.0, via=False)
    _box(viol, ly.tv1, 120.20, 1.0, 120.20 + TV1, 1.0 + TV1)
    _text(viol, ly.tx, 120.0, 3.3, "MIM.d FAIL (TopVia1 enc 0.20)")

    # (f) MIM.e FAIL: legal MIM device with an unrelated TopMetal1 plate only
    #     0.30 away from the MIM edge (< 0.60).
    _mim_plate(viol, ly, 150.0, 0.0, 153.0, 3.0)
    _box(viol, ly.tm1, 153.30, 0.0, 155.30, 3.0)
    _text(viol, ly.tx, 150.0, 3.3, "MIM.e FAIL (TM1 space 0.30)")

    # (g) MIM.f FAIL: 1.14 x 1.14 plate, area 1.2996 um2 (< 1.30); the width
    #     is exactly Mim_a, so MIM.a must NOT fire.
    _mim_plate(viol, ly, 180.0, 0.0, 181.14, 1.14)
    _text(viol, ly.tx, 180.0, 1.5, "MIM.f FAIL (area 1.2996)")

    # (h) MIM.g FAIL: 80 x 75 plate, area 6000 um2 (> 5625) on a single device.
    _mim_plate(viol, ly, 210.0, 0.0, 290.0, 75.0)
    _text(viol, ly.tx, 210.0, 75.3, "MIM.g FAIL (area 6000)")

    # (i) MIM.h FAIL: legal 3x3 MIM device with NO TopVia1/Vmim via over it.
    _mim_plate(viol, ly, 320.0, 0.0, 323.0, 3.0, via=False)
    _text(viol, ly.tx, 320.0, 3.3, "MIM.h FAIL (no via over MIM)")

    # (j) MIM.gR FAIL: array of 74x74 plates (5476 um2 each, legal per device)
    #     so the TOTAL MIM area of the cell exceeds 174800 um2.
    #     31 * 5476 = 169756; plus the constructs above the total is ~175847.
    count = 0
    for j in range(4):
        for i in range(8):
            if count >= 31:
                break
            x0 = 80.0 * i
            y0 = 100.0 + 80.0 * j
            _mim_plate(viol, ly, x0, y0, x0 + 74.0, y0 + 74.0)
            count += 1
    _text(viol, ly.tx, 0.0, 420.0, "MIM.gR FAIL (total area > 174800 um2)")

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("mim_clean")
    _text(clean, ly.tx, 0.0, -3.0, "6.11 MIM - PASS")

    # Small MIM device: Metal5 encloses by exactly 0.60 (legal), TopVia1
    # enclosed by MIM by exactly 0.36 (legal) and covered (MIM.h legal), and
    # a TopMetal1 top plate fully INSIDE the MIM (as the MIM PCell draws it)
    # which must NOT fire MIM.e.
    _mim_plate(clean, ly, 0.0, 0.0, 3.0, 3.0, via=False)
    _box(clean, ly.tv1, MIM_D, MIM_D, MIM_D + TV1, MIM_D + TV1)
    _box(clean, ly.tm1, 0.30, 0.30, 2.70, 2.70)
    _text(clean, ly.tx, 0.0, 3.3, "MIM.c/d/h PASS (enc 0.60/0.36), TM1 inside")

    # Exact-limit spacing pair: two 2x2 devices exactly 0.60 apart (MIM.b
    # legal) and a TopMetal1 plate exactly 0.60 away from MIM (MIM.e legal).
    _mim_plate(clean, ly, 20.0, 0.0, 22.0, 2.0, m5=False)
    _mim_plate(clean, ly, 22.60, 0.0, 24.60, 2.0, m5=False)
    _box(clean, ly.m5, 20.0 - MIM_C, -MIM_C, 24.60 + MIM_C, 2.0 + MIM_C)
    _box(clean, ly.tm1, 25.20, 0.0, 27.0, 2.0)
    _text(clean, ly.tx, 20.0, 2.3, "MIM.b/MIM.e PASS (space 0.60)")

    # Minimum legal device: width exactly 1.14 (MIM.a legal), area
    # 1.14 x 1.15 = 1.311 um2 (>= 1.30, MIM.f legal).
    _mim_plate(clean, ly, 40.0, 0.0, 41.14, 1.15)
    _text(clean, ly.tx, 40.0, 1.5, "MIM.a/MIM.f PASS (w 1.14, area 1.311)")

    # Maximum legal device: 75 x 75 = exactly 5625 um2 (MIM.g legal; the
    # limit is a strict '>'). Total clean-cell MIM area stays far below the
    # MIM.gR budget.
    _mim_plate(clean, ly, 60.0, 0.0, 135.0, 75.0)
    _text(clean, ly.tx, 60.0, 75.3, "MIM.g PASS (area exactly 5625)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "mim.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
