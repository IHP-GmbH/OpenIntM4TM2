#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the Sealring DRC unit testcase (`sealring.gds`) for the interposer,
following the IHP-SG13G2 `testing/testcases/unit/` convention: one table GDS
with intentional PASS / FAIL structures labeled by text on layer 63/0.

The layout has two top cells, each checked as a whole by run_regression.py:
  - sealring_viol : violates every surviving rule -> expects
                    {Seal.k, Seal.l, Seal.m, Seal.n}
  - sealring_clean: near-limit legal versions      -> expects {}

Surviving rules (interposer backend-only subset; the Seal.b family is dropped
because its primary layer Activ does not exist here):
  - Seal.l : no drawn structure may fall outside the 39/4 sealring boundary.
  - Seal.n : the EdgeSeal (39/0) must be enclosed by an unbroken Passiv (9/0) ring.
  - Seal.k : every 45-degree EdgeSeal corner edge must be >= 21.00 um long.
  - Seal.m : only one sealring (one EdgeSeal polygon) per chip is allowed.

viol cell:
  - a main EdgeSeal donut whose outer corners are chamfered SHORT (~9.9 um < 21)
    -> Seal.k; it has NO surrounding Passiv ring -> Seal.n.
  - a second, far-away EdgeSeal donut -> two EdgeSeal polygons -> Seal.m
    (structurally unavoidable with two rings; deliberately in the golden set).
  - a 39/4 boundary that covers both rings but a Metal4 (50/0) box sits OUTSIDE
    it -> Seal.l.

clean cell:
  - a single EdgeSeal donut with 45-degree corners >= 21 um (Seal.k ok, Seal.m ok
    since one polygon), enclosed inside the hole of a Passiv donut (Seal.n ok),
    and one 39/4 box covering every drawn shape so nothing is outside (Seal.l ok).

Everything is placed on the 5 nm grid (dbu = 0.001 um, all coords multiples of
5 dbu) and structures are spaced far apart so they never interact.

sealreq_* cells cover the seal-ring presence rules (SEAL.REQ.*), which run only
with -rd require_sealring=true (the GOLDEN table sets it per cell). Each cell is
one 200 um square with a 39/4 boundary and, unless the cell says otherwise, a
complete rectangular ring 30 um inside it (EdgeSeal plus the Metal4..TopMetal2
stack, centred via rings and a Passiv ring outside) around a Metal4 core:
  - sealreq_clean, sealreq_sub_cell (ring in a sub-cell), sealreq_touching_core
    (core metal touching the ring from inside), sealreq_markers (nofill and
    keep-out markers from the board edge into the margin, prBoundary on the
    outline), sealreq_crossbar_ES (an EdgeSeal bar splits the interior in two)
    -> {}
  - sealreq_no_edgeseal -> {SEAL.REQ.1}; sealreq_no_boundary -> {SEAL.REQ.2}
  - sealreq_es_open (EdgeSeal cut, the stack is not) -> {SEAL.REQ.3_ES}
  - sealreq_break_<L>, one per ring layer, 1 um cut -> {SEAL.REQ.3_<L>}
  - sealreq_point_contact_M5 (two Metal5 pieces touching at one corner),
    sealreq_gap_5nm_TM1, sealreq_neck_M4 (0.18 um neck) and sealreq_missing_TV2
    -> {SEAL.REQ.3_M5}, {SEAL.REQ.3_TM1}, {SEAL.REQ.3_M4}, {SEAL.REQ.3_TV2}
  - sealreq_corner_ring (small ring, core outside it), sealreq_fill_in_margin,
    sealreq_stray_passiv, sealreq_passiv_loop (a 9/0 donut in the margin that
    does not go around EdgeSeal), sealreq_trace_crossing (Metal4 from the core
    through the ring into the margin) -> {SEAL.REQ.4}
  - sealreq_nested_ring: a second complete ring inside the first -> {Seal.m}
  - sealreq_off_no_seal: core only and no define -> {} (the default is unchanged)
  - sealreq_slit: every metal and via ring cut once on the right side, the
    metals by their minimum space, the vias by 2.81 um (the widest cut a slit
    may have) -> {}; sealreq_slit_wide_TM2 (TopMetal2 cut 2.815 um),
    sealreq_slit_two_cuts_M4 (a second Metal4 cut) and sealreq_slit_es_cut
    (EdgeSeal cut as well) -> {SEAL.REQ.3_TM2}, {SEAL.REQ.3_M4}, {SEAL.REQ.3_ES}
  - sealreq_corner_marks: a ring whose corners step in, with 2 x 2 um Metal4
    filler squares in the corners its Passiv ring cuts off -> {};
    sealreq_corner_metal (a drawn Metal4 square there instead) and
    sealreq_corner_marks_square (the squares beside a ring with square corners)
    -> {SEAL.REQ.4}

seal_* cells measure the ring itself (full mode, no define), each on the
complete ring of the sealreq cells, which sits at every limit (Passiv 1.00 um
from the ring, the Seal.f floor):
  - seal_a_<L>: Metal4, Metal5, TopMetal1 or TopMetal2 5 nm narrower for 10 um
    -> {Seal.a_<L>}
  - seal_c_narrow_<V> / seal_c_wide_<V>: a via ring 5 nm narrower or wider for
    10 um -> {Seal.c1.Via4}, {Seal.c2}, {Seal.c3}
  - seal_e: the Passiv ring 5 nm narrower for 10 um -> {Seal.e}
  - seal_f_<L>: EdgeSeal and <L> 5 nm closer to the Passiv ring for 10 um
    -> {Seal.f.<L>}

Regenerate with:  python3 gen_sealring_testcase.py   (writes sealring.gds here)
"""

from pathlib import Path

import klayout.db as db

# GDS layer/datatype (must match layers_def.drc); 63/0 = annotation text (inert)
L_EDGESEAL = (39, 0)
L_EDGESEAL_BND = (39, 4)
L_PASSIV = (9, 0)
L_METAL4 = (50, 0)
L_TEXT = (63, 0)

DBU = 0.001  # um per database unit

# Chamfer legs. A 45-degree corner edge of leg c has length c * sqrt(2).
#   clean: c = 14850 dbu -> length ~= 21001 dbu = 21.001 um  (>= Seal_k = 21.00)
#   viol : c =  7000 dbu -> length ~=  9899 dbu =  9.899 um  (<  Seal_k)
CHAMFER_CLEAN = 14850
CHAMFER_VIOL = 7000


def _box(cell, lidx, x0, y0, x1, y1):
    cell.shapes(lidx).insert(db.Box(x0, y0, x1, y1))


def _text(cell, tidx, x, y, s):
    cell.shapes(tidx).insert(db.Text(s, db.Trans(db.Vector(x, y))))


def _chamfered_square(x0, y0, x1, y1, c):
    """Return a db.Polygon: the square [x0,x1]x[y0,y1] with all four corners
    cut back by leg `c`, yielding four 45-degree corner edges."""
    pts = [
        db.Point(x0 + c, y0),
        db.Point(x1 - c, y0),
        db.Point(x1, y0 + c),
        db.Point(x1, y1 - c),
        db.Point(x1 - c, y1),
        db.Point(x0 + c, y1),
        db.Point(x0, y1 - c),
        db.Point(x0, y0 + c),
    ]
    return db.Polygon(pts)


def _seal_donut(x0, y0, x1, y1, c, hx0, hy0, hx1, hy1):
    """EdgeSeal donut region: chamfered outer square minus an axis-aligned
    rectangular hole (the hole keeps square corners, so it adds no 45-degree
    edge of its own)."""
    outer = db.Region(_chamfered_square(x0, y0, x1, y1, c))
    hole = db.Region(db.Box(hx0, hy0, hx1, hy1))
    return outer - hole


def _rect_donut(x0, y0, x1, y1, hx0, hy0, hx1, hy1):
    """Plain rectangular donut region (no 45-degree edges)."""
    return db.Region(db.Box(x0, y0, x1, y1)) - db.Region(db.Box(hx0, hy0, hx1, hy1))


def build():
    layout = db.Layout()
    layout.dbu = DBU
    seal = layout.layer(*L_EDGESEAL)
    bnd = layout.layer(*L_EDGESEAL_BND)
    passiv = layout.layer(*L_PASSIV)
    m4 = layout.layer(*L_METAL4)
    tx = layout.layer(*L_TEXT)

    # ---------------------------------------------------------------- #
    # viol cell -> {Seal.k, Seal.l, Seal.m, Seal.n}
    # ---------------------------------------------------------------- #
    viol = layout.create_cell("sealring_viol")
    _text(viol, tx, 0, -70000, "6.10 Sealring - FAIL")

    # Main EdgeSeal donut: SHORT 45-degree corners (Seal.k) and NO Passiv (Seal.n).
    main = _seal_donut(0, 0, 60000, 60000, CHAMFER_VIOL,
                       20000, 20000, 40000, 40000)
    viol.shapes(seal).insert(main)
    _text(viol, tx, 0, 62000, "Seal.k: short 45deg corners (~9.9um) + Seal.n: no Passiv ring")

    # Second EdgeSeal ring, far away -> two EdgeSeal polygons -> Seal.m.
    second = _rect_donut(200000, 0, 230000, 30000, 210000, 10000, 220000, 20000)
    viol.shapes(seal).insert(second)
    _text(viol, tx, 200000, 32000, "Seal.m: second sealring")

    # 39/4 boundary covering BOTH rings (but not the Metal4 box below).
    _box(viol, bnd, -10000, -10000, 240000, 70000)

    # Metal4 box OUTSIDE the boundary -> Seal.l.
    _box(viol, m4, 0, -60000, 5000, -55000)
    _text(viol, tx, 8000, -58000, "Seal.l: Metal4 outside boundary")

    # ---------------------------------------------------------------- #
    # clean cell -> {}
    # ---------------------------------------------------------------- #
    clean = layout.create_cell("sealring_clean")
    _text(clean, tx, 0, -25000, "6.10 Sealring - PASS")

    # Single EdgeSeal donut with LONG 45-degree corners (~21.001 um >= 21).
    seal_clean = _seal_donut(0, 0, 60000, 60000, CHAMFER_CLEAN,
                             20000, 20000, 40000, 40000)
    clean.shapes(seal).insert(seal_clean)
    _text(clean, tx, 0, 62000, "Seal.k ok: 45deg corners ~21.001um; Seal.m ok: one ring")

    # Passiv donut whose hole (-5000..65000) fully contains the EdgeSeal
    # (bbox 0..60000) -> unbroken Passiv ring encloses the sealring (Seal.n ok).
    passiv_clean = _rect_donut(-15000, -15000, 75000, 75000,
                               -5000, -5000, 65000, 65000)
    clean.shapes(passiv).insert(passiv_clean)
    _text(clean, tx, 0, 78000, "Seal.n ok: Passiv ring hole encloses EdgeSeal")

    # One 39/4 boundary covering every drawn shape -> nothing outside (Seal.l ok).
    _box(clean, bnd, -20000, -20000, 80000, 80000)

    build_presence(layout)
    return layout


# --- seal-ring presence (SEAL.REQ.*) ---------------------------------------

SIDE = 200000          # 39/4 boundary, 200 um square
INSET = 30000          # boundary to EdgeSeal
RING = 3500            # Seal.a
VIA_RINGS = {(66, 0): 190, (125, 0): 420, (133, 0): 900}   # Via4, TopVia1, TopVia2
STACK = [(50, 0), (67, 0), (126, 0), (134, 0)]             # Metal4..TopMetal2
LAYER_OF = {'M4': (50, 0), 'V4': (66, 0), 'M5': (67, 0), 'TV1': (125, 0),
            'TM1': (126, 0), 'TV2': (133, 0), 'TM2': (134, 0)}


def _band(inset, width, side=SIDE):
    """Rectangular ring `inset` from the boundary, `width` wide."""
    box = db.Box(inset, inset, side - inset, side - inset)
    return db.Region(box) - db.Region(box.enlarged(-width, -width))


def _ring_shapes(side=SIDE):
    """Layer -> Region of a complete ring for a `side` square boundary."""
    shapes = {L_EDGESEAL: _band(INSET, RING, side)}
    for key in STACK:
        shapes[key] = _band(INSET, RING, side)
    for key, width in VIA_RINGS.items():
        shapes[key] = _band(INSET + (RING - width) // 2, width, side)
    shapes[L_PASSIV] = _band(INSET - 1000 - 4200, 4200, side)
    return shapes


def _cut(region, x0, y0, x1, y1):
    return region - db.Region(db.Box(x0, y0, x1, y1))


def build_presence(layout):
    lidx = lambda key: layout.layer(*key)
    right = SIDE - INSET - RING     # inner edge of the ring's right side
    core = db.Box(90000, 90000, 110000, 110000)

    def cell(name, shapes, boundary=True, core_box=core):
        top = layout.create_cell(name)
        for key, region in shapes.items():
            top.shapes(lidx(key)).insert(region)
        if boundary:
            top.shapes(lidx(L_EDGESEAL_BND)).insert(db.Box(0, 0, SIDE, SIDE))
        if core_box is not None:
            top.shapes(lidx(L_METAL4)).insert(core_box)
        return top

    cell("sealreq_clean", _ring_shapes())

    top = cell("sealreq_sub_cell", {})
    ring = layout.create_cell("sealreq_ring")
    for key, region in _ring_shapes().items():
        ring.shapes(lidx(key)).insert(region)
    top.insert(db.CellInstArray(ring.cell_index(), db.Trans()))

    top = cell("sealreq_touching_core", _ring_shapes())
    top.shapes(lidx(L_METAL4)).insert(db.Box(110000, 99000, right, 101000))

    top = cell("sealreq_markers", _ring_shapes())
    top.shapes(lidx((50, 23))).insert(db.Box(0, 0, 20000, SIDE))
    top.shapes(lidx((160, 0))).insert(db.Box(0, 150000, SIDE, 160000))
    top.shapes(lidx((235, 0))).insert(db.Box(0, 0, SIDE, SIDE))

    # An EdgeSeal bar across the interior: two holes, still one closed ring.
    shapes = _ring_shapes()
    shapes[L_EDGESEAL] += db.Region(db.Box(INSET + RING, 60000, right, 63500))
    cell("sealreq_crossbar_ES", shapes)

    cell("sealreq_no_edgeseal", {})

    cell("sealreq_no_boundary", _ring_shapes(), boundary=False)

    shapes = _ring_shapes()
    shapes[L_EDGESEAL] = _cut(shapes[L_EDGESEAL], right, 99500, SIDE - INSET, 100500)
    cell("sealreq_es_open", shapes)

    for name, key in LAYER_OF.items():
        shapes = _ring_shapes()
        shapes[key] = _cut(shapes[key], right, 99500, SIDE - INSET, 100500)
        cell(f"sealreq_break_{name}", shapes)

    # Metal5 pieces meeting only at the point (mid, 100000): the lower piece
    # keeps the inner half of the band below it, the upper piece the outer
    # half above it.
    shapes = _ring_shapes()
    mid = right + RING // 2
    shapes[(67, 0)] = _cut(_cut(shapes[(67, 0)], mid, 99000, SIDE - INSET, 100000),
                           right, 100000, mid, 101000)
    cell("sealreq_point_contact_M5", shapes)

    shapes = _ring_shapes()
    shapes[(126, 0)] = _cut(shapes[(126, 0)], right, 100000, SIDE - INSET, 100005)
    cell("sealreq_gap_5nm_TM1", shapes)

    # Metal4 narrowed to its outer 0.18 um for 1 um of the right side.
    shapes = _ring_shapes()
    shapes[(50, 0)] = _cut(shapes[(50, 0)], right, 99500, SIDE - INSET - 180, 100500)
    cell("sealreq_neck_M4", shapes)

    shapes = _ring_shapes()
    del shapes[(133, 0)]
    cell("sealreq_missing_TV2", shapes)

    # The ring for a 100 um outline (a 40 um EdgeSeal square) in the lower-left
    # corner; the core sits outside it.
    cell("sealreq_corner_ring", _ring_shapes(side=100000),
         core_box=db.Box(140000, 140000, 160000, 160000))

    top = cell("sealreq_fill_in_margin", _ring_shapes())
    top.shapes(lidx((50, 22))).insert(db.Box(10000, 90000, 15000, 95000))

    top = cell("sealreq_stray_passiv", _ring_shapes())
    top.shapes(lidx(L_PASSIV)).insert(db.Box(10000, 90000, 15000, 95000))

    top = cell("sealreq_passiv_loop", _ring_shapes())
    top.shapes(lidx(L_PASSIV)).insert(_band(0, 2000, side=12000).moved(8000, 80000))

    top = cell("sealreq_trace_crossing", _ring_shapes())
    top.shapes(lidx(L_METAL4)).insert(db.Box(110000, 99000, SIDE - 10000, 101000))

    # A second complete ring (for a 100 um outline) centred inside the first.
    shapes = _ring_shapes()
    for key, region in _ring_shapes(side=100000).items():
        shapes[key] += region.moved(50000, 50000)
    cell("sealreq_nested_ring", shapes)

    cell("sealreq_off_no_seal", {}, boundary=False)

    build_slit(cell)
    build_corner_marks(cell)
    build_ring_rules(cell)


# Slit cuts on the right side, centred on y = 100 um.
SLIT_CUTS = {(50, 0): 240, (67, 0): 240, (126, 0): 1640, (134, 0): 2000,
             (66, 0): 2810, (125, 0): 2810, (133, 0): 2810}


def _slit_shapes(cuts=SLIT_CUTS):
    shapes = _ring_shapes()
    right = SIDE - INSET - RING
    for key, cut in cuts.items():
        shapes[key] = _cut(shapes[key], right, 100000 - cut // 2, SIDE - INSET, 100000 + cut - cut // 2)
    return shapes


def build_slit(cell):
    cell("sealreq_slit", _slit_shapes())
    cell("sealreq_slit_wide_TM2", _slit_shapes(SLIT_CUTS | {(134, 0): 2815}))
    shapes = _slit_shapes()
    shapes[(50, 0)] = _cut(shapes[(50, 0)], INSET, 100000, INSET + RING, 100240)
    cell("sealreq_slit_two_cuts_M4", shapes)
    shapes = _slit_shapes()
    shapes[L_EDGESEAL] = _cut(shapes[L_EDGESEAL], SIDE - INSET - RING, 99880, SIDE - INSET, 100120)
    cell("sealreq_slit_es_cut", shapes)


NOTCH = 10000          # corner step of the stepped ring


def _stepped_ring_shapes():
    """The complete ring of _ring_shapes with a NOTCH square stepped out of
    every outer corner of the ring and of its Passiv loop."""
    outer = db.Region(db.Box(INSET, INSET, SIDE - INSET, SIDE - INSET))
    for x, y in ((INSET, INSET), (SIDE - INSET - NOTCH, INSET),
                 (INSET, SIDE - INSET - NOTCH), (SIDE - INSET - NOTCH, SIDE - INSET - NOTCH)):
        outer -= db.Region(db.Box(x, y, x + NOTCH, y + NOTCH))

    def band(inset, width):
        return outer.sized(-inset) - outer.sized(-(inset + width))

    shapes = {L_EDGESEAL: band(0, RING)}
    for key in STACK:
        shapes[key] = band(0, RING)
    for key, width in VIA_RINGS.items():
        shapes[key] = band((RING - width) // 2, width)
    shapes[L_PASSIV] = outer.sized(1000 + 4200) - outer.sized(1000)
    return shapes


def _corner_squares():
    """Two 2 x 2 um squares in each corner the Passiv loop of the stepped
    ring cuts off its bounding box ([24.8, 34.8] um from each outline corner)."""
    squares = db.Region()
    for dx, dy in ((25800, 25800), (28800, 25800)):
        for x, y in ((dx, dy), (SIDE - dx - 2000, dy), (dx, SIDE - dy - 2000),
                     (SIDE - dx - 2000, SIDE - dy - 2000)):
            squares.insert(db.Box(x, y, x + 2000, y + 2000))
    return squares


def build_corner_marks(cell):
    top = cell("sealreq_corner_marks", _stepped_ring_shapes())
    top.shapes(top.layout().layer(50, 22)).insert(_corner_squares())
    top = cell("sealreq_corner_metal", _stepped_ring_shapes())
    top.shapes(top.layout().layer(*L_METAL4)).insert(db.Box(25800, 25800, 27800, 27800))
    top = cell("sealreq_corner_marks_square", _ring_shapes())
    top.shapes(top.layout().layer(50, 22)).insert(_corner_squares())


METAL_NAMES = {(50, 0): 'Metal4', (67, 0): 'Metal5', (126, 0): 'TopMetal1', (134, 0): 'TopMetal2'}
VIA_NAMES = {(66, 0): 'Via4', (125, 0): 'TopVia1', (133, 0): 'TopVia2'}


def build_ring_rules(cell):
    right = SIDE - INSET          # outer edge of the ring's right side
    stretch = (95000, 105000)     # 10 um of the right side
    for key, name in METAL_NAMES.items():
        shapes = _ring_shapes()
        shapes[key] = _cut(shapes[key], right - RING, stretch[0], right - RING + 5, stretch[1])
        cell(f"seal_a_{name}", shapes)
    for key, name in VIA_NAMES.items():
        inner = right - (RING - VIA_RINGS[key]) // 2 - VIA_RINGS[key]   # via ring's inner edge
        shapes = _ring_shapes()
        shapes[key] = _cut(shapes[key], inner, stretch[0], inner + 5, stretch[1])
        cell(f"seal_c_narrow_{name}", shapes)
        shapes = _ring_shapes()
        shapes[key] += db.Region(db.Box(inner - 5, stretch[0], inner, stretch[1]))
        cell(f"seal_c_wide_{name}", shapes)
    passiv_inner = right + 1000   # Passiv ring's inner edge on the right side
    shapes = _ring_shapes()
    shapes[L_PASSIV] = _cut(shapes[L_PASSIV], passiv_inner, stretch[0], passiv_inner + 5, stretch[1])
    cell("seal_e", shapes)
    for key, name in METAL_NAMES.items():
        shapes = _ring_shapes()
        for grown in (L_EDGESEAL, key):
            shapes[grown] += db.Region(db.Box(right, stretch[0], right + 5, stretch[1]))
        cell(f"seal_f_{name}", shapes)


def main():
    out = Path(__file__).resolve().parent / "sealring.gds"
    layout = build()
    layout.write(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
