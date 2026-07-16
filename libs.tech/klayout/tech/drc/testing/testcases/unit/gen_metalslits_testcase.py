#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Metal Slits DRC unit testcase (`metalslits.gds`) for the interposer,
following the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS with
intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - metalslits_viol : one violating structure per rule ->
      {Slt.a_*, Slt.b_*, Slt.c_*, Slt.e_*, Slt.e1_*, Slt.f_*} for M4/M5/TM1/TM2,
      plus {Slt.g_M5, Slt.g_TM1, Slt.h2_M4, Slt.h2_M5, Slt.h3, Slt.h4}
  - metalslits_clean: near-limit legal counterparts -> expects {}

Grid layout: one row per metal (y pitch 100 um), one column per structure
(x pitch 100 um). Structures are >= 60 um apart, far above the largest spacing
value of the table (Slt_c/2 = 15 um morphology, 1.0 um seps), so they never
interact. All coordinates are on the 5 nm grid.

Note: a slit shape does NOT subtract from the drawn metal (separate datatype);
the deck's Slt.c derivation performs the subtraction itself. Slt.c only fires
on plates wider than 30 um in BOTH axes (40x40 here); clean plates are 25x25,
plus one slitted 40x40 plate proving that legal slits satisfy Slt.c.

Regenerate with:  python gen_metalslits_testcase.py  (writes metalslits.gds
next to this file)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text
L_TEXT = (63, 0)
L_MIM = (36, 0)
L_PASSIV = (9, 0)
L_DFPAD = (41, 0)
L_VIA4 = (66, 0)
L_TOPVIA1 = (125, 0)
L_TOPVIA2 = (133, 0)

# Per metal: (abbrev, metal layer, slit layer, adjacent via layer,
#             slit-to-via rule id, slit-to-via limit, violating sep, via cut size)
METALS = [
    ("M4", (50, 0), (50, 24), L_VIA4, "Slt.h2_M4", 0.3, 0.2, 0.19),
    ("M5", (67, 0), (67, 24), L_VIA4, "Slt.h2_M5", 0.3, 0.2, 0.19),
    ("TM1", (126, 0), (126, 24), L_TOPVIA1, "Slt.h3", 1.0, 0.5, 0.42),
    ("TM2", (134, 0), (134, 24), L_TOPVIA2, "Slt.h4", 1.0, 0.5, 0.90),
]

ROW = 100.0  # y pitch between metal rows
COL = 100.0  # x pitch between structures


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _slit_in_plate(cell, met, slit, x0, y0, sw, sl, enc):
    """Metal plate with a slit inside it: slit sw x sl at (x0, y0), plate
    surrounding it with enclosure `enc` on all four sides."""
    _box(cell, slit, x0, y0, x0 + sw, y0 + sl)
    _box(cell, met, x0 - enc, y0 - enc, x0 + sw + enc, y0 + sl + enc)


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    tx = layout.layer(*L_TEXT)
    mim = layout.layer(*L_MIM)
    passiv = layout.layer(*L_PASSIV)
    dfpad = layout.layer(*L_DFPAD)

    viol = layout.create_cell("metalslits_viol")
    clean = layout.create_cell("metalslits_clean")
    _text(viol, tx, 0.0, -10.0, "7.3 Metal Slits - FAIL")
    _text(clean, tx, 0.0, -10.0, "7.3 Metal Slits - PASS")

    for row, (ab, l_met, l_slit, l_via, h_id, h_lim, h_viol, via_sz) in enumerate(METALS):
        met = layout.layer(*l_met)
        slit = layout.layer(*l_slit)
        via = layout.layer(*l_via)
        y = row * ROW
        has_g = ab in ("M5", "TM1")  # Slt.g applies to M5 and TM1 only

        # ------------------- violating structures ------------------------- #
        # col 0 - Slt.a: slit 2.0 x 10 (width < 2.8), enclosure 1.0 (no Slt.f)
        _slit_in_plate(viol, met, slit, 1.0, y + 1.0, 2.0, 10.0, 1.0)
        _text(viol, tx, 0.0, y + 14.0, f"Slt.a_{ab} FAIL (w=2.0)")

        # col 1 - Slt.b: slit 3 x 25 (edge > 20), enclosure 1.0
        _slit_in_plate(viol, met, slit, COL + 1.0, y + 1.0, 3.0, 25.0, 1.0)
        _text(viol, tx, COL, y + 29.0, f"Slt.b_{ab} FAIL (l=25)")

        # col 2 - Slt.c: bare 40x40 plate, no slit / recog / dfpad / mim on it
        _box(viol, met, 2 * COL, y, 2 * COL + 40.0, y + 40.0)
        _text(viol, tx, 2 * COL, y + 42.0, f"Slt.c_{ab} FAIL (40x40 bare)")

        # col 3 - Slt.e: slit fully inside a dfpad-over-passiv pad region.
        # The pad slit is exempt from Slt.a/b/f/h (slit.not(pad) is empty).
        _box(viol, dfpad, 3 * COL, y, 3 * COL + 30.0, y + 30.0)
        _box(viol, passiv, 3 * COL + 2.0, y + 2.0, 3 * COL + 28.0, y + 28.0)
        _box(viol, slit, 3 * COL + 10.0, y + 10.0, 3 * COL + 14.0, y + 20.0)
        _text(viol, tx, 3 * COL, y + 32.0, f"Slt.e_{ab} FAIL (slit on pad)")

        # col 4 - Slt.f: slit 4 x 10 with metal enclosure 0.5 (< 1.0)
        _slit_in_plate(viol, met, slit, 4 * COL + 0.5, y + 0.5, 4.0, 10.0, 0.5)
        _text(viol, tx, 4 * COL, y + 13.0, f"Slt.f_{ab} FAIL (enc=0.5)")

        # col 5 - Slt.e1: slit overlapped by a MIM plate (for M5/TM1 the
        # overlap also feeds the and() branch of Slt.g - same-row rule ids)
        _slit_in_plate(viol, met, slit, 5 * COL + 1.0, y + 1.0, 4.0, 10.0, 1.0)
        _box(viol, mim, 5 * COL - 1.0, y - 1.0, 5 * COL + 7.0, y + 13.0)
        _text(viol, tx, 5 * COL, y + 15.0, f"Slt.e1_{ab} FAIL (MIM on slit)")

        # col 6 - Slt.g (M5/TM1 only): MIM at 0.3 from the slit (< 0.6)
        if has_g:
            _slit_in_plate(viol, met, slit, 6 * COL + 1.0, y + 1.0, 4.0, 10.0, 1.0)
            _box(viol, mim, 6 * COL + 5.3, y + 1.0, 6 * COL + 15.3, y + 11.0)
            _text(viol, tx, 6 * COL, y + 14.0, f"Slt.g_{ab} FAIL (sep=0.3)")

        # col 7 - Slt.h2/h3/h4: via too close to the slit (0.2 / 0.5)
        _slit_in_plate(viol, met, slit, 7 * COL + 1.0, y + 1.0, 4.0, 10.0, 1.0)
        hx = 7 * COL + 5.0 + h_viol  # slit right edge + violating gap
        _box(viol, via, hx, y + 5.0, hx + via_sz, y + 5.0 + via_sz)
        _text(viol, tx, 7 * COL, y + 14.0, f"{h_id} FAIL (sep={h_viol})")

        # --------------------- clean structures --------------------------- #
        # col 0 - Slt.a/f: slit exactly 2.8 wide, enclosure exactly 1.0
        _slit_in_plate(clean, met, slit, 1.0, y + 1.0, 2.8, 10.0, 1.0)
        _text(clean, tx, 0.0, y + 14.0, f"Slt.a_{ab}/Slt.f_{ab} PASS (w=2.8 enc=1.0)")

        # col 1 - Slt.b: slit edge exactly 20 (limit is strict >)
        _slit_in_plate(clean, met, slit, COL + 1.0, y + 1.0, 3.0, 20.0, 1.0)
        _text(clean, tx, COL, y + 24.0, f"Slt.b_{ab} PASS (l=20)")

        # col 2 - Slt.c: bare 25x25 plate (erodes away at -15)
        _box(clean, met, 2 * COL, y, 2 * COL + 25.0, y + 25.0)
        _text(clean, tx, 2 * COL, y + 27.0, f"Slt.c_{ab} PASS (25x25 bare)")

        # col 3 - Slt.e: pad region without any slit
        _box(clean, dfpad, 3 * COL, y, 3 * COL + 30.0, y + 30.0)
        _box(clean, passiv, 3 * COL + 2.0, y + 2.0, 3 * COL + 28.0, y + 28.0)
        _text(clean, tx, 3 * COL, y + 32.0, f"Slt.e_{ab} PASS (no slit on pad)")

        # col 4 - Slt.f: slit 4 x 10 with enclosure exactly 1.0
        _slit_in_plate(clean, met, slit, 4 * COL + 1.0, y + 1.0, 4.0, 10.0, 1.0)
        _text(clean, tx, 4 * COL, y + 14.0, f"Slt.f_{ab} PASS (enc=1.0)")

        # col 5 - Slt.e1/g: MIM at exactly 0.6 from the slit, no overlap
        _slit_in_plate(clean, met, slit, 5 * COL + 1.0, y + 1.0, 4.0, 10.0, 1.0)
        _box(clean, mim, 5 * COL + 5.6, y + 1.0, 5 * COL + 15.6, y + 11.0)
        _text(clean, tx, 5 * COL, y + 14.0, f"Slt.e1_{ab}/g PASS (sep=0.6)")

        # col 7 - Slt.h2/h3/h4: via at exactly the allowed distance
        _slit_in_plate(clean, met, slit, 7 * COL + 1.0, y + 1.0, 4.0, 10.0, 1.0)
        hx = 7 * COL + 5.0 + h_lim
        _box(clean, via, hx, y + 5.0, hx + via_sz, y + 5.0 + via_sz)
        _text(clean, tx, 7 * COL, y + 14.0, f"{h_id} PASS (sep={h_lim})")
        if ab == "M5":
            # second output of Slt.h2_M5: TopVia1 at exactly 0.3, left side
            tv1 = layout.layer(*L_TOPVIA1)
            _box(clean, tv1, 7 * COL + 0.28, y + 5.0, 7 * COL + 0.7, y + 5.42)
            _text(clean, tx, 7 * COL - 10.0, y + 8.0, "Slt.h2_M5 PASS (TopVia1 sep=0.3)")

        # col 8 - Slt.c satisfied by slits: 40x40 plate with two legal slits
        # (3 wide, 19/18 long, enclosure 1.0, 1 um metal web between them);
        # metal minus slits erodes away at -3/-12.
        _box(clean, met, 8 * COL, y, 8 * COL + 40.0, y + 40.0)
        _box(clean, slit, 8 * COL + 1.0, y + 18.5, 8 * COL + 20.0, y + 21.5)
        _box(clean, slit, 8 * COL + 21.0, y + 18.5, 8 * COL + 39.0, y + 21.5)
        _text(clean, tx, 8 * COL, y + 42.0, f"Slt.c_{ab} PASS (40x40 slitted)")

        # col 9 - Slt.e over-reach guard: a fully legal slit that ABUTS the
        # outside edge of a dfpad-over-passiv pad region. The slit's left edge is
        # coincident with the pad's right edge (a shared boundary), but the slit
        # lies entirely outside the pad, so slt_pad.and(slit) has zero area and
        # Slt.e_{ab} must stay quiet. This pins the slt_pad derivation against
        # over-reach (an abutting slit must not be treated as "on pad"). The slit
        # is legal on its own terms: width 3.0 (>= 2.8), long edge 15 (<= 20),
        # metal enclosure exactly 1.0 on all sides, and no via/mim within reach.
        x9 = 9 * COL
        _box(clean, dfpad, x9, y, x9 + 30.0, y + 30.0)
        _box(clean, passiv, x9 + 2.0, y + 2.0, x9 + 28.0, y + 28.0)
        # slit abutting the pad's right edge (x = x9 + 30), fully outside the pad
        _box(clean, slit, x9 + 30.0, y + 8.0, x9 + 33.0, y + 23.0)
        # metal enclosing the slit by exactly 1.0 um on every side
        _box(clean, met, x9 + 29.0, y + 7.0, x9 + 34.0, y + 24.0)
        _text(clean, tx, x9, y + 32.0, f"Slt.e_{ab} PASS (slit abuts pad edge)")

    return layout


def main():
    out = Path(__file__).resolve().parent / "metalslits.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
