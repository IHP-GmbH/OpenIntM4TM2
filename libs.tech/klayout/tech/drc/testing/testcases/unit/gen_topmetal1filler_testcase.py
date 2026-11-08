#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the TopMetal1:filler DRC unit testcase (`topmetal1filler.gds`) for the
interposer, following the IHP-SG13G2 `testing/testcases/unit/` convention: one
table GDS with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has six top cells, each run through the deck on its own by
run_regression.py:
  - topmetal1filler_viol          : space and width violations
  - topmetal1filler_iso_contained : filler inside drawn TopMetal1
  - topmetal1filler_iso_bridge    : filler spanning two drawn shapes
  - topmetal1filler_iso_edge      : filler abutting drawn metal, shared edge
  - topmetal1filler_iso_corner    : filler touching drawn metal at one corner
  - topmetal1filler_clean         : near-limit legal ones -> expects {}

Rules exercised (deck key: topmetal1filler):
  - TM1Fil.c  : min. TopMetal1:filler space to drawn TopMetal1 is 3.00 um
  - TM1Fil.a1 : max. TopMetal1:filler width is 10.00 um
  - TM1Fil.b  : min. TopMetal1:filler space to TopMetal1:filler is 3.00 um
  - TM1Fil.iso: filler must not touch or overlap drawn TopMetal1

Containment and bridging are missed by the separation check; edge and corner
contact also exercise the existing separation rule. Each shape gets its own top
cell because the golden comparison is a set of
rule names, every one of these structures emits TM1Fil.iso, and in a combined
cell a containment-only implementation would produce exactly the same set as a
correct one. Split, the bridge cell fails when the contained cell passes, which
is the distinction worth testing.

The pre-fix measurements that motivated this rule were made on Metal4 coupons,
not on TopMetal1; see the note in gen_metalnfiller_testcase.py for exactly what
those two runs covered. The TopMetal1 structures here are the same gap reasoned
onto this layer, not a measurement of it. Whether the bridging case is also an
electrical short is a separate question this file does not answer; see the note
in 5_18_metalnfiller.drc.

The clean isolation structures sit at exactly TM1FIL_C, not at a comfortable
distance, and that is deliberate. At a comfortable distance the clean cell only
shows the rule does not fire on obviously safe geometry. At the limit it shows
the rule does not steal cases that TM1Fil.c already owns, which is the realistic
way a containment or overlap check written next to a separation rule goes wrong.
Do not relax those distances.

Structures are spaced well beyond the 3.0 space rule so they never interact.
All coordinates are on the 5 nm grid.

Regenerate with:  python gen_topmetal1filler_testcase.py  (writes topmetal1filler.gds)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text.
L_TM1_DRW = (126, 0)
L_TM1_FIL = (126, 22)
L_TEXT = (63, 0)

TM1FIL_C = 3.0        # min filler-to-drawn-metal space (um)
TM1FIL_A1 = 10.0      # max filler width (um)
TM1FIL_B = 3.0        # min filler-to-filler space (um)

C_GAP_VIOL = 2.0      # < TM1FIL_C  -> TM1Fil.c fires
C_GAP_CLEAN = 3.0     # == TM1FIL_C -> legal (space rule is strict-less-than)
A1_LEN_VIOL = 11.0    # > TM1FIL_A1 -> TM1Fil.a1 fires
A1_LEN_CLEAN = 10.0   # == TM1FIL_A1 -> legal (with_bbox_max threshold is +0.001)
B_GAP_VIOL = 2.0      # < TM1FIL_B  -> TM1Fil.b fires
B_GAP_CLEAN = 3.0     # == TM1FIL_B -> legal (space rule is strict-less-than)

A1_ORIGIN_X = 25.0    # x-origin of the width structure (far from the space one)
B_ORIGIN_X = 50.0     # x-origin of the filler-to-filler structure

ISO_CLEAN_Y = -30.0      # y-band for the clean isolation pair, clear of the rest
ISO_CLEAN_BESIDE_X = 0.0 # x-origin of the clean beside-the-square structure
ISO_CLEAN_GAP_X = 30.0   # x-origin of the clean in-channel structure
ISO_CLEAN_GAP = TM1FIL_C # == TM1FIL_C: the clean isolation structures sit at the
                         # exact separation limit, so they guard near-spacing too


def _box(cell, idx, x0, y0, x1, y1):
    cell.shapes(idx).insert(db.DBox(x0, y0, x1, y1))


def _text(cell, idx, x, y, s):
    cell.shapes(idx).insert(db.DText(s, db.DTrans(db.DVector(x, y))))


def _draw_group(cell, drw, fil, tx, c_gap, a1_len, b_gap):
    """Draw the three filler structures.

    Structure 1 (space): a 3x3 drawn-metal pad with a 2x2 filler c_gap to its
    right -> exercises TM1Fil.c.
    Structure 2 (width): a single a1_len x 2 filler bar, far from any drawn
    metal -> exercises TM1Fil.a1 only.
    Structure 3 (filler-to-filler): two 4x2 filler bars b_gap apart, far from any
    drawn metal -> exercises TM1Fil.b only.
    """
    # Structure 1: filler-to-drawn-metal space
    _box(cell, drw, 0.0, 0.0, 3.0, 3.0)
    _box(cell, fil, 3.0 + c_gap, 0.0, 5.0 + c_gap, 2.0)
    _text(cell, tx, 0.0, 3.3, f"TM1Fil.c gap={c_gap}")

    # Structure 2: max filler width (2 tall bar of length a1_len)
    _box(cell, fil, A1_ORIGIN_X, 0.0, A1_ORIGIN_X + a1_len, 2.0)
    _text(cell, tx, A1_ORIGIN_X, 3.3, f"TM1Fil.a1 len={a1_len}")

    # Structure 3: filler-to-filler space (two 4x2 bars b_gap apart)
    _box(cell, fil, B_ORIGIN_X, 0.0, B_ORIGIN_X + 4.0, 2.0)
    _box(cell, fil, B_ORIGIN_X + 4.0 + b_gap, 0.0, B_ORIGIN_X + 8.0 + b_gap, 2.0)
    _text(cell, tx, B_ORIGIN_X, 3.3, f"TM1Fil.b gap={b_gap}")


def _iso_contained(cell, drw, fil, tx):
    """A 4x4 filler wholly inside a 12x12 drawn square.

    There is no space between them for TM1Fil.c to measure, so the separation
    rule stays silent.
    """
    _box(cell, drw, 0.0, 0.0, 12.0, 12.0)
    _box(cell, fil, 4.0, 4.0, 8.0, 8.0)
    _text(cell, tx, 0.0, 12.3, "TM1Fil.iso contained")


def _iso_bridge(cell, drw, fil, tx):
    """One filler spanning the legal 3.0 um gap between two drawn squares.

    The discriminating case, and the reason this is its own cell. The filler is
    inside neither square, so an isolation rule written as a containment test
    misses it while still firing on the contained cell. The filler is 6.0 um
    wide, under the 10.0 um TM1Fil.a1 limit, so only the isolation rule should
    fire here.
    """
    _box(cell, drw, 0.0, 0.0, 6.0, 6.0)
    _box(cell, drw, 9.0, 0.0, 15.0, 6.0)
    _box(cell, fil, 5.0, 2.0, 11.0, 4.0)
    _text(cell, tx, 0.0, 6.3, "TM1Fil.iso bridging")


def _iso_edge(cell, drw, fil, tx):
    """A filler abutting drawn metal along a shared edge.

    Contact with zero overlap area, so an isolation rule written as an overlap
    area test misses it.

    Touching means zero space, which is below the separation limit, so
    TM1Fil.c is reported here as well and that is correct, not noise. The
    cell still discriminates: a containment-only implementation emits the .c
    marker alone.
    """
    _box(cell, drw, 0.0, 0.0, 6.0, 6.0)
    _box(cell, fil, 6.0, 2.0, 12.0, 4.0)
    _text(cell, tx, 0.0, 6.3, "TM1Fil.iso edge touch")


def _iso_corner(cell, drw, fil, tx):
    """A filler touching drawn metal at a single corner point.

    The weakest contact there is, and the one a rule built on containment or on
    overlap area is most likely to miss.

    Touching means zero space, which is below the separation limit, so
    TM1Fil.c is reported here as well and that is correct, not noise. The
    cell still discriminates: a containment-only implementation emits the .c
    marker alone.
    """
    _box(cell, drw, 0.0, 0.0, 6.0, 6.0)
    _box(cell, fil, 6.0, 6.0, 12.0, 8.0)
    _text(cell, tx, 0.0, 8.3, "TM1Fil.iso corner touch")


def _iso_clean(cell, drw, fil, tx):
    """The legal counterparts, so the rule cannot pass by over-firing.

    Beside: the filler sits ISO_CLEAN_GAP from the square instead of inside it.
    In-gap: the filler sits in the channel between two drawn squares, clear of
    both, which is what legal generated fill in a routing channel looks like.
    Both gaps are exactly TM1FIL_C, the tightest legal spacing, so this also
    guards against an isolation rule that fires on near-but-not-touching fill.
    """
    _box(cell, drw, ISO_CLEAN_BESIDE_X, ISO_CLEAN_Y,
         ISO_CLEAN_BESIDE_X + 12.0, ISO_CLEAN_Y + 12.0)
    _box(cell, fil, ISO_CLEAN_BESIDE_X + 12.0 + ISO_CLEAN_GAP, ISO_CLEAN_Y + 4.0,
         ISO_CLEAN_BESIDE_X + 16.0 + ISO_CLEAN_GAP, ISO_CLEAN_Y + 8.0)
    _text(cell, tx, ISO_CLEAN_BESIDE_X, ISO_CLEAN_Y + 12.3,
          f"TM1Fil.iso beside, gap={ISO_CLEAN_GAP}")

    _box(cell, drw, ISO_CLEAN_GAP_X, ISO_CLEAN_Y,
         ISO_CLEAN_GAP_X + 6.0, ISO_CLEAN_Y + 6.0)
    _box(cell, drw, ISO_CLEAN_GAP_X + 16.0, ISO_CLEAN_Y,
         ISO_CLEAN_GAP_X + 22.0, ISO_CLEAN_Y + 6.0)
    _box(cell, fil, ISO_CLEAN_GAP_X + 6.0 + ISO_CLEAN_GAP, ISO_CLEAN_Y + 2.0,
         ISO_CLEAN_GAP_X + 16.0 - ISO_CLEAN_GAP, ISO_CLEAN_Y + 4.0)
    _text(cell, tx, ISO_CLEAN_GAP_X, ISO_CLEAN_Y + 6.3,
          f"TM1Fil.iso in-gap, clear by {ISO_CLEAN_GAP}")


# One cell per contact shape, so the golden comparison can tell them apart. A
# single combined cell cannot: every structure emits the same TM1Fil.iso name,
# so a containment-only implementation would match the same set as a correct one
# and the bridging case would stop discriminating.
ISO_VIOL_CELLS = (
    ("topmetal1filler_iso_contained", _iso_contained),
    ("topmetal1filler_iso_bridge", _iso_bridge),
    ("topmetal1filler_iso_edge", _iso_edge),
    ("topmetal1filler_iso_corner", _iso_corner),
)


def build():
    layout = db.Layout()
    layout.dbu = 0.001
    d = layout.layer(*L_TM1_DRW)
    f = layout.layer(*L_TM1_FIL)
    tx = layout.layer(*L_TEXT)

    # ---- violating structures -------------------------------------------- #
    viol = layout.create_cell("topmetal1filler_viol")
    _text(viol, tx, 0.0, -1.5, "5.23 TopMetal1:filler - FAIL")
    _draw_group(viol, d, f, tx, C_GAP_VIOL, A1_LEN_VIOL, B_GAP_VIOL)

    # ---- isolation, one cell per contact shape --------------------------- #
    for name, draw in ISO_VIOL_CELLS:
        draw(layout.create_cell(name), d, f, tx)

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("topmetal1filler_clean")
    _text(clean, tx, 0.0, -1.5, "5.23 TopMetal1:filler - PASS")
    _draw_group(clean, d, f, tx, C_GAP_CLEAN, A1_LEN_CLEAN, B_GAP_CLEAN)
    _iso_clean(clean, d, f, tx)

    return layout


def main():
    out = Path(__file__).resolve().parent / "topmetal1filler.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
