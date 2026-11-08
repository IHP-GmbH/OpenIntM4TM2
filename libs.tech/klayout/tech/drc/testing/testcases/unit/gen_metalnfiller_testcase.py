#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Metaln:filler DRC unit testcase (`metalnfiller.gds`) for the
interposer, following the IHP-SG13G2 `testing/testcases/unit/` convention: one
table GDS with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has six top cells, each run through the deck on its own by
run_regression.py:
  - metalnfiller_viol          : space and width violations
  - metalnfiller_iso_contained : filler inside drawn metal
  - metalnfiller_iso_bridge    : filler spanning two drawn shapes
  - metalnfiller_iso_edge      : filler abutting drawn metal, shared edge
  - metalnfiller_iso_corner    : filler touching drawn metal at one corner
  - metalnfiller_clean         : near-limit legal ones -> expects {}

Rules exercised (deck key: metalnfiller):
  - M{n}Fil.c  : min. Metal(n):filler space to drawn Metal(n) is 0.42 um
  - M{n}Fil.a2 : max. Metal(n):filler width is 5.00 um
  - M{n}Fil.iso: filler must not touch or overlap drawn Metal(n)

Containment and bridging are missed by the separation check; edge and corner
contact also exercise the existing separation rule. Each shape gets its own top
cell because the golden comparison is a set of
rule names, every one of these structures emits M{n}Fil.iso, and in a combined
cell a containment-only implementation would produce exactly the same set as a
correct one. Split, the bridge cell fails when the contained cell passes, which
is the distinction worth testing.

What was actually measured before M{n}Fil.iso existed, stated no more strongly
than the retained logs support. The bridging layout went through the full
default deck set and was not reported. The contained layout was run against the
metalnfiller deck alone, and the only violation reported there was that layout's
own deliberate near-spacing control, not the contained filler. Neither run says
anything about the edge and corner structures, which are new here and were
reasoned from the same gap rather than measured before the fix. Whether the
bridging case is also an electrical short is a separate question this file does
not answer; see the note in 5_18_metalnfiller.drc.

The clean isolation structures sit at exactly MFIL_C, not at a comfortable
distance, and that is deliberate. At a comfortable distance the clean cell only
shows the rule does not fire on obviously safe geometry. At the limit it shows
the rule does not steal cases that M{n}Fil.c already owns, which is the
realistic way a containment or overlap check written next to a separation rule
goes wrong. Do not relax those distances.

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

ISO_CLEAN_BESIDE_X = 30.0  # x-origin of the clean beside-the-square structure
ISO_CLEAN_GAP_X = 50.0     # x-origin of the clean in-channel structure
ISO_CLEAN_GAP = MFIL_C     # == MFIL_C: the clean isolation structures sit at the
                           # exact separation limit, so they guard near-spacing too


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


def _iso_contained(cell, drw, fil, tx, y0, metal_no):
    """A 2x2 filler wholly inside a 10x10 drawn square.

    There is no space between them for M{n}Fil.c to measure, so the separation
    rule stays silent.
    """
    _box(cell, drw, 0.0, y0, 10.0, y0 + 10.0)
    _box(cell, fil, 4.0, y0 + 4.0, 6.0, y0 + 6.0)
    _text(cell, tx, 0.0, y0 + 10.3, f"M{metal_no}Fil.iso contained")


def _iso_bridge(cell, drw, fil, tx, y0, metal_no):
    """One filler spanning the legal 1.0 um gap between two drawn squares.

    The discriminating case, and the reason this is its own cell. The filler is
    inside neither square, so an isolation rule written as a containment test
    misses it while still firing on the contained cell. The filler is 3.0 um
    wide, under the 5.0 um M{n}Fil.a2 limit, so only the isolation rule should
    fire here.
    """
    _box(cell, drw, 0.0, y0, 5.0, y0 + 5.0)
    _box(cell, drw, 6.0, y0, 11.0, y0 + 5.0)
    _box(cell, fil, 4.0, y0 + 2.0, 7.0, y0 + 3.0)
    _text(cell, tx, 0.0, y0 + 5.3, f"M{metal_no}Fil.iso bridging")


def _iso_edge(cell, drw, fil, tx, y0, metal_no):
    """A filler abutting drawn metal along a shared edge.

    Contact with zero overlap area, so an isolation rule written as an overlap
    area test misses it.

    Touching means zero space, which is below the separation limit, so
    M{n}Fil.c is reported here as well and that is correct, not noise. The
    cell still discriminates: a containment-only implementation emits the .c
    marker alone.
    """
    _box(cell, drw, 0.0, y0, 5.0, y0 + 5.0)
    _box(cell, fil, 5.0, y0 + 1.0, 8.0, y0 + 3.0)
    _text(cell, tx, 0.0, y0 + 5.3, f"M{metal_no}Fil.iso edge touch")


def _iso_corner(cell, drw, fil, tx, y0, metal_no):
    """A filler touching drawn metal at a single corner point.

    The weakest contact there is, and the one a rule built on containment or on
    overlap area is most likely to miss.

    Touching means zero space, which is below the separation limit, so
    M{n}Fil.c is reported here as well and that is correct, not noise. The
    cell still discriminates: a containment-only implementation emits the .c
    marker alone.
    """
    _box(cell, drw, 0.0, y0, 5.0, y0 + 5.0)
    _box(cell, fil, 5.0, y0 + 5.0, 8.0, y0 + 7.0)
    _text(cell, tx, 0.0, y0 + 7.3, f"M{metal_no}Fil.iso corner touch")


def _iso_clean(cell, drw, fil, tx, y0, metal_no):
    """The legal counterparts, so the rule cannot pass by over-firing.

    Beside: the filler sits ISO_CLEAN_GAP away from the square instead of inside
    it. In-gap: the filler sits in the channel between two drawn squares, clear
    of both, which is what legal generated fill in a routing channel looks like.
    Both gaps are exactly MFIL_C, the tightest legal spacing, so this also
    guards against an isolation rule that fires on near-but-not-touching fill.
    Neither structure may be reported.
    """
    _box(cell, drw, ISO_CLEAN_BESIDE_X, y0,
         ISO_CLEAN_BESIDE_X + 10.0, y0 + 10.0)
    _box(cell, fil, ISO_CLEAN_BESIDE_X + 10.0 + ISO_CLEAN_GAP, y0 + 4.0,
         ISO_CLEAN_BESIDE_X + 12.0 + ISO_CLEAN_GAP, y0 + 6.0)
    _text(cell, tx, ISO_CLEAN_BESIDE_X, y0 + 10.3,
          f"M{metal_no}Fil.iso beside, gap={ISO_CLEAN_GAP}")

    _box(cell, drw, ISO_CLEAN_GAP_X, y0, ISO_CLEAN_GAP_X + 5.0, y0 + 5.0)
    _box(cell, drw, ISO_CLEAN_GAP_X + 9.0, y0, ISO_CLEAN_GAP_X + 14.0, y0 + 5.0)
    _box(cell, fil, ISO_CLEAN_GAP_X + 5.0 + ISO_CLEAN_GAP, y0 + 2.0,
         ISO_CLEAN_GAP_X + 9.0 - ISO_CLEAN_GAP, y0 + 3.0)
    _text(cell, tx, ISO_CLEAN_GAP_X, y0 + 5.3,
          f"M{metal_no}Fil.iso in-gap, clear by {ISO_CLEAN_GAP}")


# One cell per contact shape, so the golden comparison can tell them apart. A
# single combined cell cannot: every structure emits the same M{n}Fil.iso name,
# so a containment-only implementation would match the same set as a correct one
# and the bridging case would stop discriminating.
ISO_VIOL_CELLS = (
    ("metalnfiller_iso_contained", _iso_contained),
    ("metalnfiller_iso_bridge", _iso_bridge),
    ("metalnfiller_iso_edge", _iso_edge),
    ("metalnfiller_iso_corner", _iso_corner),
)


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

    # ---- isolation, one cell per contact shape --------------------------- #
    for name, draw in ISO_VIOL_CELLS:
        iso = layout.create_cell(name)
        draw(iso, m4d, m4f, tx, 0.0, 4)
        draw(iso, m5d, m5f, tx, BAND_M5_Y, 5)

    # ---- clean structures ------------------------------------------------ #
    clean = layout.create_cell("metalnfiller_clean")
    _text(clean, tx, 0.0, -1.5, "5.18 Metaln:filler - PASS")
    _draw_group(clean, m4d, m4f, tx, 0.0, 4, C_GAP_CLEAN, A2_LEN_CLEAN)
    _draw_group(clean, m5d, m5f, tx, BAND_M5_Y, 5, C_GAP_CLEAN, A2_LEN_CLEAN)
    _iso_clean(clean, m4d, m4f, tx, 0.0, 4)
    _iso_clean(clean, m5d, m5f, tx, BAND_M5_Y, 5)

    return layout


def main():
    out = Path(__file__).resolve().parent / "metalnfiller.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
