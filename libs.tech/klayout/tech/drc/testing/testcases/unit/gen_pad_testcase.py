#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Pad DRC unit testcase (`pad.gds`) for the interposer, following the
IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with intentional
PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - pad_viol : one violating structure per rule  -> expects {Pad.dR}
  - pad_clean: the corresponding legal structures -> expects {}

Only Pad.dR (min. recommended Pad-to-EdgeSeal space = 25.0 um) is designed to fire.
The other rules in 6_9_pad.drc are deliberately kept quiet in both cells:
  - Pad.m  : SBumpPad and CuPillarPad never coexist (no 9/35, 9/36, 41/35, 41/36 drawn).
  - Pad.i  : dfpad is fully covered by TopMetal2 (dfpad box == TopMetal2 box exactly).
  - Pad.fR : TopMetal2 box == dfpad box, so TopMetal2.interacting(pad).not(dfpad) is
             empty (no metal exit); no M4/M5/TM1 drawn at all.

Each pad is a coincident passiv (9/0) + dfpad (41/0) + TopMetal2 (134/0) box.
In pad_viol the pad sits 10 um from an EdgeSeal (39/0) strip (< 25 -> Pad.dR fires).
In pad_clean the same pad sits exactly 25 um from the EdgeSeal strip (>= 25 -> legal).

pad_clean also carries a second, independent pad only 10 um from an EdgeSeal
strip that is FULLY COVERED by a recognition (recog, 99/0) box. Pad.dR measures
against edgeseal.not(recog), so a fully recog-covered seal disappears from the
check and the rule stays quiet despite the 10 um gap. This exercises the
.not(recog) exclusion branch of Pad.dR, which must remain silent. The second
structure is placed far from the first so no rule interacts across the two
(Pad.m/Pad.fR/Pad.i all stay quiet by construction, as above).

Regenerate with:  python gen_pad_testcase.py   (writes pad.gds next to this file)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert for the deck)
L_PASSIV = (9, 0)
L_EDGESEAL = (39, 0)
L_DFPAD = (41, 0)
L_RECOG = (99, 0)
L_TOPMETAL2 = (134, 0)
L_TEXT = (63, 0)

PAD = 30.0        # pad opening size (um); not width-checked by this deck
GAP_VIOL = 10.0   # Pad-to-EdgeSeal gap that must FAIL  (< Pad_dR = 25.0)
GAP_CLEAN = 25.0  # Pad-to-EdgeSeal gap that must PASS  (== Pad_dR, not less-than)
SEAL_W = 10.0     # EdgeSeal strip width (um)
SEAL_OVER = 10.0  # EdgeSeal strip vertical overhang beyond the pad on each side (um)
RECOG_MARGIN = 2.0  # recog overhang beyond the EdgeSeal strip on each side (um)
STRUCT2_DX = 200.0  # x-offset of the recog-exclusion structure from the first (um)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _pad(cell, passiv, dfpad, tm2, x0, y0):
    """Coincident passiv + dfpad + TopMetal2 pad box with lower-left at (x0, y0)."""
    x1, y1 = x0 + PAD, y0 + PAD
    _box(cell, passiv, x0, y0, x1, y1)
    _box(cell, dfpad, x0, y0, x1, y1)
    _box(cell, tm2, x0, y0, x1, y1)
    return x0, y0, x1, y1


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    passiv = layout.layer(*L_PASSIV)
    edgeseal = layout.layer(*L_EDGESEAL)
    dfpad = layout.layer(*L_DFPAD)
    recog = layout.layer(*L_RECOG)
    tm2 = layout.layer(*L_TOPMETAL2)
    tx = layout.layer(*L_TEXT)

    # ---- violating structure --------------------------------------------- #
    viol = layout.create_cell("pad_viol")
    _text(viol, tx, 0.0, -SEAL_OVER - 1.0, "6.9 Pad - FAIL")

    # Pad.dR FAIL: pad opening 10 um from an EdgeSeal strip (< 25 um).
    px0, py0, px1, py1 = _pad(viol, passiv, dfpad, tm2, 0.0, 0.0)
    sx0 = px1 + GAP_VIOL
    _box(viol, edgeseal, sx0, py0 - SEAL_OVER, sx0 + SEAL_W, py1 + SEAL_OVER)
    _text(viol, tx, px0, py1 + 0.3, "Pad.dR FAIL (10 um to EdgeSeal)")

    # ---- clean structure ------------------------------------------------- #
    clean = layout.create_cell("pad_clean")
    _text(clean, tx, 0.0, -SEAL_OVER - 1.0, "6.9 Pad - PASS")

    # Pad.dR PASS: same pad exactly 25 um from the EdgeSeal strip (>= 25 um).
    qx0, qy0, qx1, qy1 = _pad(clean, passiv, dfpad, tm2, 0.0, 0.0)
    tx0 = qx1 + GAP_CLEAN
    _box(clean, edgeseal, tx0, qy0 - SEAL_OVER, tx0 + SEAL_W, qy1 + SEAL_OVER)
    _text(clean, tx, qx0, qy1 + 0.3, "Pad.dR PASS (25 um to EdgeSeal)")

    # Pad.dR PASS (recog exclusion): a second, independent pad only 10 um from an
    # EdgeSeal strip that is fully covered by a recog (99/0) box. Pad.dR checks
    # separation to edgeseal.not(recog); the recog-covered seal drops out of that
    # set, so the rule stays quiet despite the sub-25 um gap. Placed STRUCT2_DX
    # to the right of the first structure so Pad.dR (>25 um to the first seal),
    # Pad.m, Pad.fR and Pad.i all remain quiet across the two.
    rx0, ry0, rx1, ry1 = _pad(clean, passiv, dfpad, tm2, STRUCT2_DX, 0.0)
    esx0 = rx1 + GAP_VIOL
    esy0, esy1 = ry0 - SEAL_OVER, ry1 + SEAL_OVER
    _box(clean, edgeseal, esx0, esy0, esx0 + SEAL_W, esy1)
    # recog fully encloses the EdgeSeal strip (edgeseal.not(recog) is empty here).
    _box(clean, recog,
         esx0 - RECOG_MARGIN, esy0 - RECOG_MARGIN,
         esx0 + SEAL_W + RECOG_MARGIN, esy1 + RECOG_MARGIN)
    _text(clean, tx, rx0, ry1 + 0.3,
          "Pad.dR PASS (10 um to recog-covered EdgeSeal)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "pad.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
