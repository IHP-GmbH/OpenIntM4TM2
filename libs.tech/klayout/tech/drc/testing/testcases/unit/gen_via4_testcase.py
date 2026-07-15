#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Via4 DRC unit testcase (`via4.gds`) for the interposer, following the
IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with intentional
PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - via4_viol : one violating structure per rule  -> expects {V4.b1, V4.c1, M5.c1}
  - via4_clean: the corresponding legal structures -> expects {} (also guards V4.a/b/c/c2)

Structures are spaced several um apart so they never interact (no spurious V4.b).
All via cuts are 0.19x0.19 um (= Vn_a) so V4.a never fires.

Regenerate with:  python gen_via4_testcase.py   (writes via4.gds next to this file)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert for the deck)
L_VIA4 = (66, 0)
L_METAL4 = (50, 0)
L_METAL5 = (67, 0)
L_TEXT = (63, 0)

VIA = 0.19       # Vn_a : via cut size (um)
MARGIN = 0.10    # generous metal enclosure (> Vn_c1 = 0.05) for clean structures
STRIDE = 5.0     # spacing between independent structures (>> any spacing rule)


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _via_ll(cell, idx, llx, lly, size=VIA):
    _box(cell, idx, llx, lly, llx + size, lly + size)


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _array(cell, v4, ox, oy, cols, rows, sx, sy, size=VIA):
    """Draw a cols x rows cut array at origin (ox, oy); sx/sy are edge-to-edge gaps."""
    px = size + sx
    py = size + sy
    for i in range(cols):
        for j in range(rows):
            _via_ll(cell, v4, ox + i * px, oy + j * py, size)
    return ox, oy, ox + (cols - 1) * px + size, oy + (rows - 1) * py + size


def _cover(cell, idx, x0, y0, x1, y1, m=MARGIN):
    _box(cell, idx, x0 - m, y0 - m, x1 + m, y1 + m)


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    v4 = layout.layer(*L_VIA4)
    m4 = layout.layer(*L_METAL4)
    m5 = layout.layer(*L_METAL5)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("via4_viol")
    _text(viol, tx, 0.0, -1.0, "5.20 Vian - FAIL")

    # V4.b1 FAIL: 4x4 @ 0.22/0.22 both axes (span 1.42 >= 1.37 -> >3x3, full mesh).
    x0, y0, x1, y1 = _array(viol, v4, 0.0, 0.0, 4, 4, 0.22, 0.22)
    _cover(viol, m4, x0, y0, x1, y1)
    _cover(viol, m5, x0, y0, x1, y1)
    _text(viol, tx, x0, y1 + 0.3, "V4.b1 FAIL")

    # V4.c1 / M5.c1 FAIL: symmetric 0.005 enclosure on all 4 sides.
    ox = STRIDE
    _via_ll(viol, v4, ox, 0.0)
    for m in (m4, m5):
        _box(viol, m, ox - 0.005, -0.005, ox + VIA + 0.005, VIA + 0.005)
    _text(viol, tx, ox, VIA + 0.3, "V4.c1/M5.c1 FAIL (sym 0.005)")

    # V4.c1 / M5.c1 FAIL: via with NO Metal4 / NO Metal5 landing (miss branch).
    ox = 2 * STRIDE
    _via_ll(viol, v4, ox, 0.0)
    _text(viol, tx, ox, VIA + 0.3, "V4.c1/M5.c1 FAIL (no metal)")

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("via4_clean")
    _text(clean, tx, 0.0, -1.0, "5.20 Vian - PASS")

    # V4.b1 PASS: 4x4 with 0.29 in x / 0.22 in y (directionally legal).
    x0, y0, x1, y1 = _array(clean, v4, 0.0, 0.0, 4, 4, 0.29, 0.22)
    _cover(clean, m4, x0, y0, x1, y1)
    _cover(clean, m5, x0, y0, x1, y1)
    _text(clean, tx, x0, y1 + 0.3, "V4.b1 PASS (0.29/0.22)")

    # V4.b PASS: 3x3 @ 0.22/0.22 (span 1.01 < 1.37 -> not a >3x3 array).
    ox = STRIDE
    x0, y0, x1, y1 = _array(clean, v4, ox, 0.0, 3, 3, 0.22, 0.22)
    _cover(clean, m4, x0, y0, x1, y1)
    _cover(clean, m5, x0, y0, x1, y1)
    _text(clean, tx, x0, y1 + 0.3, "V4.b PASS (3x3)")

    # V4.c1 / M5.c1 PASS: 0.05 in x, 0.005 in y (deficiency on 2 opposite sides -> suppressed).
    ox = 2 * STRIDE
    _via_ll(clean, v4, ox, 0.0)
    for m in (m4, m5):
        _box(clean, m, ox - 0.05, -0.005, ox + VIA + 0.05, VIA + 0.005)
    _text(clean, tx, ox, VIA + 0.3, "V4.c1/M5.c1 PASS (endcap 0.05)")

    # V4.c / V4.c2 PASS: single via, generous enclosure.
    ox = 3 * STRIDE
    _via_ll(clean, v4, ox, 0.0)
    _cover(clean, m4, ox, 0.0, ox + VIA, VIA)
    _cover(clean, m5, ox, 0.0, ox + VIA, VIA)
    _text(clean, tx, ox, VIA + 0.3, "V4.c/V4.c2 PASS")

    return layout


def main():
    out = Path(__file__).resolve().parent / "via4.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
