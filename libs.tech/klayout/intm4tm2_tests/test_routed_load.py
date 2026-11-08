"""The routed load tool, libs.tech/parasitics/routed_load.py, its GDS check and its solver.

The solver half pins the physics: an isolated line against Sakurai and Tamaru inside their
fitted range, the axisymmetric weights against a coaxial line, the two directions in which
the answer may only err (a finer mesh and a larger box both lower the self term), and the
refusal of conductors at another potential that touch the bounded one.

The rest feeds small routes built here, so each rule has a case that moves only it: the
halo and its sidecar, the parallel rules the first version had (to the digit), the fill
envelope against floating fill, the die face and its openings, the pillars, crossings and
pad neighbours, vias and end caps, the GDS check, and the patches it names. Those run on a
coarse mesh to stay fast; the mesh does not change which rule fires. The cases
that still take more than about 1.5 s at one CPU carry the slow marker, which the
default options deselect; pytest -m slow runs them.

Floating fill is solved by superposition, so the CI's bare Python runs it. The GDS check
needs klayout.db, and its tests skip without it.
"""
import json
import math
import re
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest


def _repo_root():
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "libs.tech").is_dir():
            return parent
    raise RuntimeError("could not locate repo root (libs.tech not found)")


ROOT = _repo_root()
sys.path.insert(0, str(ROOT / "libs.tech/parasitics"))
import field2d  # noqa: E402
import routed_load as rl  # noqa: E402

EPS_OX = 4.1


def sakurai_tamaru(w, h, t, er=EPS_OX):
    """Isolated line over a plane, F/m (IEEE TED 30(2), 1983)."""
    return field2d.EPS0 * er * (1.15 * w / h + 2.80 * (t / h) ** 0.222)


# ----------------------------------------------------------------------------
# the solver

def test_an_isolated_line_matches_sakurai_tamaru_inside_their_range():
    """TopMetal1 at 1.64 um: W/H 0.54 and T/H 0.66, inside the fit; within 2 %."""
    w, t, h = 1.64, 2.0, 3.0103
    sec = field2d.Section(-120, 120, 120, [(0, 120, EPS_OX)],
                          [("a", -w / 2, w / 2, h, h + t)])
    c = sec.solve({"a": 1.0})[0]["a"]
    assert c == pytest.approx(sakurai_tamaru(w, h, t), rel=0.02, abs=0)


def test_the_axisymmetric_weights_give_a_coaxial_line():
    """A rod inside a grounded tube: dQ/dL is 2 pi eps / ln(b/a) to 0.5 %."""
    a, b = 5.0, 40.0
    q = []
    for length in (100.0, 200.0):
        sec = field2d.Section(0, b, length + 2 * b, [(0, length + 2 * b, 1.0)],
                              [("rod", 0, a, b, b + length)], axisymmetric=True)
        q.append(sec.solve({"rod": 1.0})[0]["rod"])
    per_m = (q[1] - q[0]) / 100e-6
    exact = 2 * math.pi * field2d.EPS0 / math.log(b / a)
    assert per_m == pytest.approx(exact, rel=0.005, abs=0)


def test_each_triangle_is_weighted_at_its_centroid_radius():
    """The exact first-order integral, which the upper bound rests on.

    The cell [1, 3] x [0, 1]: its lower triangle holds the bottom and left
    edges, centroid radius 1 + 2/3; its upper one the top and right edges,
    centroid radius 1 + 4/3. The left edge is also the right edge of the
    conductor's cell [0, 1] x [0, 1], whose upper triangle sits at radius 2/3.
    A midpoint weight converges as well, but it is not the element's energy.
    """
    sec = field2d.Section(0, 3, 1, [(0, 1, 2.0)], [("c", 0, 1, 0, 1)],
                          axisymmetric=True, h0=10, hmax=10)
    assert (sec.xs, sec.zs) == ([0.0, 1.0, 3.0], [0.0, 1.0])
    gx, gz = 2.0 * 1 / (2 * 2), 2.0 * 2 / (2 * 1)
    lower, upper = 2 * math.pi * (1 + 2 / 3), 2 * math.pi * (1 + 4 / 3)
    nx = 3
    assert sec.edges[(1, 2)] == pytest.approx(gx * lower, rel=1e-12, abs=0)            # bottom
    shared = 2.0 * 1 / (2 * 1) * 2 * math.pi * (2 / 3)
    assert sec.edges[(1, 1 + nx)] == pytest.approx(gz * lower + shared, rel=1e-12, abs=0)
    assert sec.edges[(1 + nx, 2 + nx)] == pytest.approx(gx * upper, rel=1e-12, abs=0)  # top
    assert sec.edges[(2, 2 + nx)] == pytest.approx(gz * upper, rel=1e-12, abs=0)       # right


def _pair(h0, growth, box):
    z = (7.8103, 10.8103)
    sec = field2d.Section(-box, box, box, [(0, box, EPS_OX)],
                          [("a", -1, 1) + z, ("b", 3, 5) + z], h0=h0, growth=growth)
    static = sec.solve({"a": 1.0})[0]["a"]
    miller = sec.solve({"a": 1.0, "b": -1.0})[0]["a"]
    return static, miller


@pytest.mark.slow
def test_a_finer_mesh_only_lowers_the_answer():
    """Two TopMetal2 lines 2 um apart: the self term is an energy and may only
    fall as the mesh refines; the Miller drive falls with it."""
    coarse = _pair(0.5, 1.5, 100)
    default = _pair(field2d.H0_UM, field2d.GROWTH, 100)
    assert coarse[0] > default[0] and coarse[1] > default[1]
    assert coarse[0] / default[0] < 1.05


def test_a_smaller_box_only_raises_the_answer():
    assert _pair(0.5, 1.5, 40)[0] > _pair(0.5, 1.5, 100)[0]


def test_overlapping_conductors_are_refused():
    with pytest.raises(ValueError, match="overlap"):
        field2d.Section(-50, 50, 50, [(0, 50, 1.0)],
                        [("a", -2, 2, 1, 2), ("b", 1, 3, 1.5, 2.5)])


def test_the_graded_steps_from_both_ends_meet_in_one_point(monkeypatch):
    """0.82 to 1.32, two edges of a real section, is 2 h0 plus 1e-16 on the default mesh. The
    steps from its two ends land 2e-16 um apart, a cell the solve's CG does not converge on;
    one point is left there. A step under MEET_UM that reaches the grid all the same is
    refused."""
    assert 1.32 - 0.82 > 2 * field2d.H0_UM
    assert field2d.graded(0.82, 1.32, field2d.H0_UM, field2d.HMAX_UM, field2d.GROWTH) == [0.82 + 0.25]
    monkeypatch.setattr(field2d, "graded", lambda a, b, *rest: [a + 0.25, a + 0.25 + 1e-12])
    with pytest.raises(ValueError, match="a mesh step"):
        field2d.grid([0.0, 1.0], field2d.H0_UM, field2d.HMAX_UM, field2d.GROWTH)


def test_a_conductor_at_another_potential_may_not_touch_the_bounded_one():
    """field2d gives the nodes two touching conductors share to the later one, which would
    short the bounded conductor to its neighbour. Both solves refuse that; own metal may touch."""
    solver = rl.Solver(rl.load_stack())
    t = (-1.0, 1.0, 7.8103, 10.8103)
    with pytest.raises(rl.Refused, match="touches"):
        solver.section(t, ((1.0, 3.0, 7.8103, 10.8103, "signal"),), -50, 50)
    with pytest.raises(rl.Refused, match="touches"):
        solver.section(t, ((-0.5, 0.5, 10.8103, 12.0, "low"),), -50, 50)
    assert solver.section(t, ((1.0, 3.0, 7.8103, 10.8103, "own"),), -50, 50)[0] > 0
    with pytest.raises(rl.Refused, match="touches"):
        solver.revolution(((0.0, 15.0, 10.8103, 38.8103),), ((0.0, 80.0, 38.8103, 60.0),), 80.0)


@pytest.mark.slow
def test_own_metal_that_touches_the_bounded_conductor_joins_it():
    """A patch of the net that touches the wire shares its edge nodes with it, and field2d
    gives those to one of the two. The section's charge is the sum over both, which a
    section written here with both at +1 V gives; the wire's charge alone is less."""
    st = rl.load_stack()
    z = st["metals"][TM2]
    t, own, sig = (-1.0, 1.0) + z, (1.0, 3.0) + z, (6.0, 8.0) + z
    got = rl.Solver(st).section(t, (own + ("own",), sig + ("signal",)), -80, 80)
    sec = field2d.Section(-80, 80, st["ztop"], st["bands"], [("t",) + t, ("o",) + own, ("s",) + sig])
    static = sec.solve({"t": 1.0, "o": 1.0})[0]
    miller = sec.solve({"t": 1.0, "o": 1.0, "s": -1.0})[0]
    assert got == (static["t"] + static["o"], miller["t"] + miller["o"])
    assert got[1] > miller["t"]


@pytest.mark.slow
def test_a_section_solve_is_cached_on_its_exact_conductors(tmp_path, coarse):
    """Two lanes far apart, each crossed by a signal wire; the second crossing sits 0.1 nm
    off the first's place relative to its lane, one step of the sections' grid. Solving
    both in one process, in either order, gives each lane, byte for byte, what it gets
    alone."""
    nets = {"a": (None, [seg(TM2, 0, 0, 300, 0)]), "b": (None, [seg(TM2, 0, 500, 300, 500)]),
            "c": (None, [seg(TM1, 150, -40, 150, 40, 1.64)]),
            "d": (None, [seg(TM1, 150, 460.0001, 150, 540.0001, 1.64)])}
    t, b = write_route(tmp_path, nets)

    def rows(names):
        return {n: json.dumps(r, sort_keys=True)
                for n, r in rl.load(t, b, names, terms=("perpendicular",))[0].items()}
    alone = dict(rows(["a"]), **rows(["b"]))
    assert rows(["a", "b"]) == alone == rows(["b", "a"])


# ----------------------------------------------------------------------------
# routes built here

@pytest.fixture
def coarse(monkeypatch):
    monkeypatch.setattr(field2d, "H0_UM", 0.5)
    monkeypatch.setattr(field2d, "GROWTH", 1.5)
    monkeypatch.setattr(rl, "BOX_UM", 100.0)


TM1, TM2 = "TopMetal1", "TopMetal2"
HALO_SOURCE = "0" * 64


def seg(layer, x0, y0, x1, y1, width=2.0):
    return {"layer": layer, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "width": width}


def via(x, y):
    return {"via": rl.VIA, "bottom_layer": TM1, "top_layer": TM2, "x": x, "y": y}


def write_route(tmp_path, nets, bumps=(), name="", extra=None):
    """nets: {net: (net_class, segments[, vias])}; bumps: (net, x, y, landing[, component, pad])."""
    traces = {"format": rl.TRACES_FORMAT, "version": 1, "dbu_um": 0.001,
              "nets": [{"name": n, "segments": v[1], "vias": v[2] if len(v) > 2 else [],
                        "net_class": v[0]} for n, v in nets.items()]}
    rows = []
    for k, b in enumerate(bumps):
        comp, pad = (b[4], b[5]) if len(b) > 4 else ("U%d" % k, "P")
        rows.append({"net": b[0], "component": comp, "pad": pad, "x_um": b[1], "y_um": b[2],
                     "landing_um": b[3]})
    bump_rec = dict({"format": rl.BUMPS_FORMAT, "version": 1, "frame": rl.BUMPS_FRAME,
                     "bumps": rows}, **(extra or {}))
    t, b = tmp_path / ("traces%s.json" % name), tmp_path / ("bumps%s.json" % name)
    t.write_text(json.dumps(traces))
    b.write_text(json.dumps(bump_rec))
    return t, b


def run(tmp_path, nets, bumps=(), asm=None, terms=(), net="a", name=""):
    t, b = write_route(tmp_path, nets, bumps, name)
    return rl.load(t, b, [net], asm, terms)[0][net]


#: net a runs 1000 um along y = 0; the others are added per test.
A = {"a": (None, [seg(TM2, 0, 0, 1000, 0)])}
A_PADS = [("a", 0, 0, 50.0), ("a", 1000, 0, 50.0)]
CLEARANCE = {"M4": 0.42, "M5": 0.42, "TM1": 3.0, "TM2": 3.0}


# ----------------------------------------------------------------------------
# what the first version solved, unchanged

MIXED = dict(A, b=(None, [seg(TM2, 200, 4, 700, 4)]),
             g=("ground", [seg(TM2, -50, -6, 400, -6), seg(TM1, 100, 10, 900, 10, 1.64)]),
             c=(None, [seg(TM1, 300, -20, 1100, -20, 1.64)]))
SPLIT = {"a": (None, [seg(TM2, 0, 0, 400, 0), seg(TM2, 400, 0, 1000, 0), seg(TM2, 300, 0, 500, 0)])}


@pytest.mark.parametrize("nets,static,miller", [
    (A, 8.071104710826684e-14, 8.071104710826684e-14),
    (dict(A, b=(None, [seg(TM2, 200, 4, 700, 4)])), 1.1456917596577475e-13, 1.6339748601793014e-13),
    (dict(A, b=("ground", [seg(TM2, 200, 4, 700, 4)])), 1.1456917596577475e-13,
     1.1456917596577475e-13),
    pytest.param(MIXED, 1.2592533669584586e-13, 1.7709610272828354e-13,
                 marks=pytest.mark.slow),
    (SPLIT, 8.071104710826684e-14, 8.071104710826684e-14),
], ids=["alone", "signal", "rail", "mixed", "split"])
def test_with_the_new_terms_off_the_wire_term_is_the_first_versions(tmp_path, coarse, nets,
                                                                     static, miller):
    """The digits the first version gave on these routes, with no pads, no die and no fill."""
    row = run(tmp_path, nets)
    assert (row["c_wires_static_f"], row["c_wires_f"]) == (static, miller)


@pytest.mark.slow
def test_a_signal_neighbour_counts_twice_and_a_rail_once(tmp_path, coarse):
    alone = run(tmp_path, A)
    signal = run(tmp_path, dict(A, b=(None, [seg(TM2, 200, 4, 700, 4)])))
    rail = run(tmp_path, dict(A, b=("ground", [seg(TM2, 200, 4, 700, 4)])))
    assert alone["c_wires_f"] == alone["c_wires_static_f"]
    assert rail["c_wires_f"] == rail["c_wires_static_f"] > alone["c_wires_static_f"]
    assert signal["c_wires_static_f"] == rail["c_wires_static_f"]
    assert signal["c_wires_f"] > signal["c_wires_static_f"]
    assert signal["neighbours"] == [{"net": "b", "layer": TM2, "min_gap_um": 2.0,
                                     "alongside_um": 502.0}]


def test_the_window_decides_who_enters_the_section(tmp_path, coarse):
    alone = run(tmp_path, A)
    outside = run(tmp_path, dict(A, b=(None, [seg(TM2, 200, 63.1, 700, 63.1)])))
    inside = run(tmp_path, dict(A, b=(None, [seg(TM2, 200, 61.9, 700, 61.9)])))
    assert outside["c_total_f"] == alone["c_total_f"] and outside["neighbours"] == []
    assert inside["c_total_f"] > alone["c_total_f"]


def test_a_split_wire_loads_like_a_whole_one(tmp_path, coarse):
    whole = run(tmp_path, A)
    split = run(tmp_path, SPLIT)
    assert split["c_wires_f"] == pytest.approx(whole["c_wires_f"], rel=1e-12, abs=0)
    assert split["length_um"] == 1200.0   # what the route drew, overlap and all


@pytest.mark.parametrize("nets,bumps,names,match", [
    (dict(A, b=(None, [seg(TM2, 0, 0, 10, 10)])), A_PADS, ["a"], "off the axes"),
    (dict(A, b=(None, [seg(TM2, 0, 1, 500, 1)])), A_PADS, ["a"], "overlap on TopMetal2"),
    (dict(A, b=(None, [seg(TM2, 500, -40, 500, 40)])), A_PADS, ["a"], "cross on TopMetal2"),
    (A, A_PADS, ["a", "z"], "not in the traces"),
    (A, A_PADS, ["a", "a"], "each net once"),
    (A, [("a", 0, 0, None)], ["a"], "no landing_um"),
    (dict(A, b=(None, [seg("Metal9", 0, 9, 10, 9)])), A_PADS, ["a"], "not in the stack key"),
    ({"a": (None, [seg(TM2, 0, 0, 1000, 0)], [dict(via(5, 0), via="TopVia1")])}, A_PADS, ["a"],
     "not modelled"),
])
def test_what_the_solve_refuses(tmp_path, nets, bumps, names, match):
    t, b = write_route(tmp_path, nets, bumps)
    with pytest.raises(rl.Refused, match=match):
        rl.load(t, b, names, terms=())


def test_another_format_or_frame_is_refused(tmp_path):
    t, b = write_route(tmp_path, A, A_PADS)
    b.write_text(json.dumps(dict(json.loads(b.read_text()), frame="kicad mm, Y down")))
    with pytest.raises(rl.Refused, match="frame"):
        rl.read_route(t, b)
    t.write_text(json.dumps({"format": "interposer_pnr.routed_traces", "version": 2}))
    with pytest.raises(rl.Refused, match="version 1"):
        rl.load(t, b, ["a"], terms=())


def test_a_coordinate_off_the_sections_grid_is_refused_not_moved(tmp_path):
    """Drawn metal is on the 1 nm grid, so snapping a section's coordinates to 0.1 nm only
    removes float noise, such as a patch width of 2.68 + 6e-14. A wire 30 pm off that grid
    is not noise: it is refused."""
    assert rl.snap(2.6800000000000637) == 2.68
    t, b = write_route(tmp_path, dict(A, b=(None, [seg(TM2, 200, 4.00003, 700, 4.00003)])))
    with pytest.raises(rl.Refused, match="off the 0.1 nm grid"):
        rl.load(t, b, ["a"], terms=())


# ----------------------------------------------------------------------------
# halo: the keep-out regions and their record

def halo(tmp_path, nets, bumps, lanes, widths, name="halo"):
    t, b = write_route(tmp_path, nets, bumps, name)
    rec, side = tmp_path / ("%s.json" % name), tmp_path / ("%s.nofill.json" % name)
    rl.halo(t, b, lanes, widths, HALO_SOURCE, rec, side)
    return t, b, rec, json.loads(rec.read_text()), json.loads(side.read_text())


def test_halo_regions_are_the_footprints_grown_record_for_record(tmp_path):
    """Written out by hand: a segment with half its width past each end, a TopVia2_C landing
    of 1.9 um and a landing square whose half side is rounded up to the 1 nm grid. Each is
    grown by halo_um plus 5 nm and rounded outward onto the 5 nm grid. A metal at 0 gets
    nothing, and nothing is merged."""
    nets = {"a": (None, [seg(TM2, 0, 0, 10, 0)], [via(10, 0)])}
    *_, rec, side = halo(tmp_path, nets, [("a", 0, 0, 3.003)], ["a"],
                         {"M4": 0, "M5": 0, "TM1": 0, "TM2": 1.0})

    def sq(x0, y0, x1, y1):
        return {"role": "TM2", "polygon_um": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}
    assert side == {"version": 1, "frame": "traces_um_y_up", "regions": [
        sq(-2.005, -2.005, 12.005, 2.005),     # segment: -1..11 x -1..1, grown 1.005
        sq(8.045, -1.955, 11.955, 1.955),      # via: 9.05..10.95, grown 1.005
        sq(-2.51, -2.51, 2.51, 2.51),          # pad: 1.502 half side, 2.507 out to 2.510
    ]}
    assert rec["sidecar"]["regions"] == 3


HALO_ROUTE = {
    # a TopMetal2 run, a TopMetal1 run up from it, and the via that joins them; the 1.9 um
    # landing sticks out of both 1.64 um wires at the corner
    "a": (None, [seg(TM2, 0, 0, 100, 0, 1.64), seg(TM1, 100, 0, 100, 80, 1.64)], [via(100, 0)]),
    "b": (None, [seg(TM2, 0, 30, 100, 30)]),
}
HALO_BUMPS = [("a", 0, 0, 40.0), ("a", 100, 80, 40.0), ("b", 0, 30, 40.0)]
HALO_WIDTHS = {"M4": 20.0, "M5": 0.0, "TM1": 7.5, "TM2": 60.0}


def test_the_halo_covers_every_footprint_grown_by_its_width(tmp_path):
    """Every footprint of every lane, grown by halo_um plus one grid step, lies inside a
    region of its own on each metal with a width, and no region reaches past it by more
    than the outward rounding. The fixture's edges sit on the 5 nm grid, so the rounding
    adds nothing and the step alone gives the 5 nm."""
    *_, rec, side = halo(tmp_path, HALO_ROUTE, HALO_BUMPS, ["a"], HALO_WIDTHS)
    fps = [(-0.82, -0.82, 100.82, 0.82),        # TopMetal2 run, half a width past each end
           (99.18, -0.82, 100.82, 80.82),       # TopMetal1 run
           (99.05, -0.95, 100.95, 0.95),        # TopVia2_C landing
           (-20, -20, 20, 20), (80, 60, 120, 100)]  # landing squares
    regions = {}
    for r in side["regions"]:
        (x0, y0), _, (x1, y1), _ = r["polygon_um"]
        regions.setdefault(r["role"], []).append((x0, y0, x1, y1))
    assert sorted(regions) == ["M4", "TM1", "TM2"]
    for role, rects in regions.items():
        h = HALO_WIDTHS[role]
        assert len(rects) == len(fps)
        for x0, y0, x1, y1 in fps:
            need = (x0 - h - 0.005, y0 - h - 0.005, x1 + h + 0.005, y1 + h + 0.005)
            most = (x0 - h - 0.010, y0 - h - 0.010, x1 + h + 0.010, y1 + h + 0.010)
            assert any(r[0] <= need[0] + 1e-9 and r[1] <= need[1] + 1e-9 and r[2] >= need[2] - 1e-9
                       and r[3] >= need[3] - 1e-9 and r[0] >= most[0] - 1e-9
                       and r[1] >= most[1] - 1e-9 and r[2] <= most[2] + 1e-9
                       and r[3] <= most[3] + 1e-9 for r in rects), (role, (x0, y0, x1, y1))
    assert rec["sidecar"]["regions"] == len(side["regions"]) == 3 * len(fps)


def test_the_sidecar_is_what_the_exporter_paints(tmp_path):
    """Version 1, the traces frame, and per region one of the four roles and a closed
    rectangle of four points on the 5 nm grid, drawn counterclockwise from its lower left."""
    t, b, rec, data, side = halo(tmp_path, HALO_ROUTE, HALO_BUMPS, ["a"], HALO_WIDTHS)
    assert (side["version"], side["frame"], sorted(side)) == (1, "traces_um_y_up",
                                                              ["frame", "regions", "version"])
    for r in side["regions"]:
        assert sorted(r) == ["polygon_um", "role"] and r["role"] in ("M4", "M5", "TM1", "TM2")
        pts = r["polygon_um"]
        assert len(pts) == 4
        (x0, y0), (x1, y1b), (x1b, y1), (x0b, y1c) = pts
        assert (y1b, x1b, x0b, y1c) == (y0, x1, x0, y1) and x1 > x0 and y1 > y0
        for v in (x0, y0, x1, y1):
            assert round(v * 1000) % 5 == 0 and abs(round(v * 1000) - v * 1000) < 1e-6


def test_the_halo_record_names_its_inputs_and_method(tmp_path):
    t, b, rec_path, rec, side = halo(tmp_path, HALO_ROUTE, HALO_BUMPS, ["a"], HALO_WIDTHS)
    assert (rec["format"], rec["version"], rec["mode"], rec["nets"]) == (rl.FORMAT, 2, "halo", ["a"])
    assert rec["inputs"]["traces"] == {"path": str(t), "sha256": rl.sha256(t)}
    assert rec["inputs"]["bumps"] == {"path": str(b), "sha256": rl.sha256(b)}
    assert rec["method"] == {"window_um": rl.WINDOW_UM, "halo_um": HALO_WIDTHS, "grid_um": 0.005,
                             "footprint": rl.FOOTPRINT, "halo_source": HALO_SOURCE}
    side_path = Path(rec["sidecar"]["path"])
    assert rec["sidecar"]["sha256"] == rl.sha256(side_path)


def test_the_halo_command_writes_new_files_and_exits_0_or_2(tmp_path, capsys):
    t, b = write_route(tmp_path, HALO_ROUTE, HALO_BUMPS)
    rec, side = tmp_path / "rec.json", tmp_path / "side.json"
    argv = ["halo", "--traces", str(t), "--bumps", str(b), "--nets", "a",
            "--halo-um", "M4=20,M5=0,TM1=7.5,TM2=60", "--halo-source", HALO_SOURCE,
            "--out", str(rec), "--sidecar", str(side)]
    assert rl.main(argv) == 0
    assert "15 regions" in capsys.readouterr().out
    before = rec.read_bytes()
    assert rl.main(argv) == 2                     # never overwrites
    assert rec.read_bytes() == before
    for i, bad in [(8, "M4=20,M5=0,TM1=7.5"), (8, "M4=20,M5=0,TM1=7.5,TM2=60.001"),
                   (8, "M4=0,M5=0,TM1=0,TM2=0"), (8, "Metal4=20,M5=0,TM1=7.5,TM2=60"),
                   (10, "not-a-sha"), (6, "a,a")]:
        out = tmp_path / ("r%s.json" % len(list(tmp_path.iterdir())))
        args = list(argv)
        args[i], args[-3], args[-1] = bad, str(out), str(out) + ".side"
        assert rl.main(args) == 2, bad
        assert not out.exists() and not Path(str(out) + ".side").exists()


@pytest.mark.parametrize("nets,bumps,lanes,match", [
    (HALO_ROUTE, HALO_BUMPS, ["z"], "not in traces"),
    (dict(HALO_ROUTE, c=(None, [])), HALO_BUMPS, ["c"], "no segment"),
    (HALO_ROUTE, HALO_BUMPS, ["b", "a"], None),
    (HALO_ROUTE, HALO_BUMPS[:2], ["b"], "no landing pad"),
    ({"a": (None, [seg(TM2, 0, 0, 5, 5)])}, HALO_BUMPS, ["a"], "off the axes"),
    ({"a": (None, [seg("Metal5", 0, 0, 5, 0)])}, HALO_BUMPS, ["a"], "segment on Metal5"),
    ({"a": (None, [seg(TM2, 0, 0, 5, 0)], [dict(via(5, 0), via="TopVia1")])}, HALO_BUMPS, ["a"],
     "via TopVia1"),
])
def test_what_halo_refuses(tmp_path, nets, bumps, lanes, match):
    t, b = write_route(tmp_path, nets, bumps)
    rec, side = tmp_path / "rec.json", tmp_path / "side.json"
    if match is None:                              # the control: two lanes, written
        rl.halo(t, b, lanes, HALO_WIDTHS, HALO_SOURCE, rec, side)
        return
    with pytest.raises(rl.Refused, match=match):
        rl.halo(t, b, lanes, HALO_WIDTHS, HALO_SOURCE, rec, side)
    assert not rec.exists() and not side.exists()


# ----------------------------------------------------------------------------
# the fill envelope

#: lane a on TopMetal2 over a wide signal wire b on TopMetal1, 11 um off to the side;
#: only TopMetal2 has a halo the box can see (10 um), the other metals' halos fill the box
ENV_NETS = {"a": (None, [seg(TM2, 0, 0, 200, 0)]), "b": (None, [seg(TM1, 0, 22, 200, 22, 20.0)])}
ENV_HALO = {"M4": 1000.0, "M5": 1000.0, "TM1": 1000.0, "TM2": 10.0}


def envelope_case(tmp_path):
    asm = rl.Assembly(halo_um=ENV_HALO, clearance_um=CLEARANCE, lanes=["a"])
    row = run(tmp_path, ENV_NETS, asm=asm, terms=("envelope",))
    st = rl.load_stack()
    tm1, tm2 = st["metals"][TM1], st["metals"][TM2]
    x0, x1 = -1 - rl.BOX_UM, 32 + rl.BOX_UM
    return row["c_wires_f"] / 200e-6, st, tm1, tm2, x0, x1


def test_the_envelope_is_minus_one_volt_outside_the_halo_out_to_the_box(tmp_path, coarse):
    """The section the solve builds equals one written here: the TopMetal2 band from the
    halo's edge (1 + 10 um) out to the box on both sides at -1 V. Nothing on the metals
    whose halo covers the box, and no clearance cut around b, which is on another metal."""
    got, st, tm1, tm2, x0, x1 = envelope_case(tmp_path)
    sec = field2d.Section(x0, x1, st["ztop"], st["bands"],
                          [("t", -1, 1) + tm2, ("b", 12, 32) + tm1,
                           ("e0", x0, -11) + tm2, ("e1", 11, x1) + tm2])
    want = sec.solve({"t": 1.0, "b": -1.0, "e0": -1.0, "e1": -1.0})[0]["t"]
    assert got == pytest.approx(want, rel=1e-9, abs=0)


def floating_tile(st, tm1, tm2, x0, x1):
    """Lane a with a floating TopMetal2 tile at the halo's edge, over b at -1 V: the tile's
    potential and a's charge, by superposition of two Dirichlet solves."""
    sec = field2d.Section(x0, x1, st["ztop"], st["bands"],
                          [("t", -1, 1) + tm2, ("b", 12, 32) + tm1, ("tile", 11, 31) + tm2])
    grounded = sec.solve({"t": 1.0, "b": -1.0})[0]
    unit = sec.solve({"tile": 1.0})[0]
    v = -grounded["tile"] / unit["tile"]
    return v, grounded["t"] + v * unit["t"], grounded["t"]


@pytest.mark.slow
def test_floating_fill_outside_the_halo_never_exceeds_the_envelope(tmp_path, coarse):
    """The tile floats well below 0 V over b, so it is not a ground plane; the envelope,
    which holds all of the fill-eligible band at -1 V, still bounds a's charge."""
    got, st, tm1, tm2, x0, x1 = envelope_case(tmp_path)
    v, floating, _ = floating_tile(st, tm1, tm2, x0, x1)
    assert v < -0.5
    assert floating <= got


@pytest.mark.parametrize("tm2,refused", [(0.0, True), (0.005, True), (0.01, False)])
def test_the_fill_may_not_reach_the_pad_disc(tmp_path, coarse, tm2, refused):
    """The pad disc reaches d/2 plus one grid step, and the fill band on the pad's metal
    starts at d/2 plus the TopMetal2 halo. A halo of one grid step or less is refused by
    its rule before any solve; one step more is solved."""
    nets = {"a": (None, [seg(TM2, 0, 0, 100, 0)])}
    bumps = [("a", 0, 0, 50.0), ("a", 100, 0, 50.0)]
    asm = rl.Assembly(halo_um={"M4": 0.0, "M5": 0.0, "TM1": 0.0, "TM2": tm2},
                      clearance_um=CLEARANCE, lanes=["a"])
    if refused:
        with pytest.raises(rl.Refused, match="TM2 halo must exceed one grid step"):
            run(tmp_path, nets, bumps, asm, ("envelope",))
    else:
        assert run(tmp_path, nets, bumps, asm, ("envelope",))["c_pads_f"] > 0


@pytest.mark.slow
def test_grounded_fill_is_not_a_miller_bound(tmp_path, coarse):
    """Documenting: on the same fixture, the tile at 0 V gives less than the tile floating.
    A solve that grounded the fill would come out under the load it claims to bound."""
    _, st, tm1, tm2, x0, x1 = envelope_case(tmp_path)
    v, floating, grounded = floating_tile(st, tm1, tm2, x0, x1)
    assert grounded < floating


# ----------------------------------------------------------------------------
# the dies: outline, face, openings

ATTACH = 10.8103
CHIPLET = {"components": [
    {"id": "IP", "type": "interposer", "anchor": "bbox_center", "position": {"x": 500, "y": 500},
     "dimensions": {"width": 1000, "height": 1000}},
    {"id": "U1", "type": "die", "anchor": "bbox_center", "position": {"x": 300, "y": 500},
     "dimensions": {"width": 300, "height": 200}, "rotation": {"z": 90}, "connection": "pillar"},
]}
METHODS = {"methods": {"pillar": {"connection_stack": {"layers": [
    {"name": "ubm", "material": "Ni", "height": 8.0, "diameter": 30.0},
    {"name": "pillar", "material": "Cu", "height": 20.0, "diameter": 30.0},
    {"name": "cap", "material": "SnAg", "height": 16.0, "diameter": 30.0}]}}}}


def pins(opening_um=40.0, names=("P1", "P2", "P3")):
    w = round(opening_um * 1000)
    return {"U1": {"dbu_um": 0.001, "pins": [{"name": n, "width_dbu": w, "height_dbu": w}
                                             for n in names]}}


def assembly(opening_um=40.0, names=("P1", "P2", "P3"), **kw):
    dies, openings = rl.assembly_of(CHIPLET, METHODS, pins(opening_um, names), rl.load_stack())
    return rl.Assembly(dies=dies, openings=openings, **kw)


def test_a_die_sits_on_its_outline_with_its_face_at_the_rigid_standoff():
    """The rotated outline, the face at the attach plane plus the stack without its solder
    (28 um) and, for the second standoff, the full stack (44 um); the opening is half the
    narrower side of the pin in the die's pins.json."""
    dies, openings = rl.assembly_of(CHIPLET, METHODS, pins(), rl.load_stack())
    assert dies == {"U1": {"rect": (200.0, 350.0, 400.0, 650.0),
                           "face": {"low": ATTACH + 28.0, "high": ATTACH + 44.0},
                           "pillar_um": 30.0, "connection": "pillar"}}
    assert openings == {("U1", "P1"): 20.0, ("U1", "P2"): 20.0, ("U1", "P3"): 20.0}


@pytest.mark.parametrize("change,match", [
    (lambda c: c["components"][1].update(anchor="lower_left"), "not bbox_center"),
    (lambda c: c["components"][1].update(rotation={"z": 45}), "multiple of 90"),
    (lambda c: c["components"][1].update(connection="wire"), "not in the manifest"),
    (lambda c: c["components"][0].update(position={"x": 400, "y": 500}), "lower-left"),
    (lambda c: c["components"][1].update(underfill={"material": "epoxy"}), "underfill"),
])
def test_what_the_assembly_refuses(change, match):
    chiplet = json.loads(json.dumps(CHIPLET))
    change(chiplet)
    with pytest.raises(rl.Refused, match=match):
        rl.assembly_of(chiplet, METHODS, pins(), rl.load_stack())


@pytest.mark.parametrize("material", ["Xx", "Sn", "SAC"])
def test_a_stack_material_neither_rigid_nor_solder_is_refused(material):
    """The rigid part is an allow-list: a material the tool does not know is refused, never
    counted as rigid or as solder."""
    methods = json.loads(json.dumps(METHODS))
    methods["methods"]["pillar"]["connection_stack"]["layers"][2]["material"] = material
    with pytest.raises(rl.Refused, match="neither rigid"):
        rl.assembly_of(CHIPLET, methods, pins(), rl.load_stack())


@pytest.mark.slow
def test_a_pad_near_a_die_whose_face_sits_at_another_height_is_refused(tmp_path, coarse):
    """The pad solve has one face plane. a's pad sits 20 um inside U1's edge; U2, on a
    shorter stack, comes within the window of it (40 / 2 + 60 um): refused. On U1's stack, or
    out past the window, it is not."""
    def with_u2(connection, x):
        chiplet = json.loads(json.dumps(CHIPLET))
        chiplet["components"].append({"id": "U2", "type": "die", "anchor": "bbox_center",
                                      "position": {"x": x, "y": 500},
                                      "dimensions": {"width": 100, "height": 100},
                                      "connection": connection})
        methods = json.loads(json.dumps(METHODS))
        short = json.loads(json.dumps(METHODS["methods"]["pillar"]))
        short["connection_stack"]["layers"][1]["height"] = 10.0
        methods["methods"]["short"] = short
        dies, openings = rl.assembly_of(chiplet, methods, pins(), rl.load_stack())
        return rl.Assembly(dies=dies, openings=openings)
    nets = {"a": (None, [seg(TM2, 380, 500, 380, 640)])}
    bumps = [("a", 380, 500, 40.0, "U1", "P1")]
    with pytest.raises(rl.Refused, match="another height"):
        run(tmp_path, nets, bumps, with_u2("short", 400 + 59.9 + 50), ("die",), name="near")
    run(tmp_path, nets, bumps, with_u2("pillar", 400 + 59.9 + 50), ("die",), name="same")
    run(tmp_path, nets, bumps, with_u2("short", 400 + 60.1 + 50), ("die",), name="far")


@pytest.mark.slow
def test_the_die_face_holds_minus_one_volt_over_its_outline_only(tmp_path, coarse):
    """A wire is cut where it passes under the die's edge: the part under it loads like a
    wire wholly under the die, the rest like one clear of it. Both orientations, since the
    outline is not square."""
    asm = assembly()
    faces = set()
    real = rl.Solver.section

    def spy(self, target, others, x0, x1):
        faces.update(o[2] for o in others if o[4] == "low")
        return real(self, target, others, x0, x1)

    def per_um(nets):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(rl.Solver, "section", spy)
            row = run(tmp_path, nets, asm=asm, terms=("die",),
                      name=str(len(list(tmp_path.iterdir()))))
        return row["c_wires_f"]
    h_under = per_um({"a": (None, [seg(TM2, 250, 500, 350, 500)])}) / 100
    assert faces == {ATTACH + 28.0}           # the face at the lowest standoff
    h_clear = per_um({"a": (None, [seg(TM2, 450, 500, 550, 500)])}) / 100
    h_across = per_um({"a": (None, [seg(TM2, 100, 500, 300, 500)])})
    v_under = per_um({"a": (None, [seg(TM2, 300, 400, 300, 600)])}) / 200
    v_clear = per_um({"a": (None, [seg(TM2, 300, 700, 300, 800)])}) / 100
    v_across = per_um({"a": (None, [seg(TM2, 300, 600, 300, 700)])})
    assert h_under > h_clear and v_under > v_clear
    assert h_across == pytest.approx(100 * h_clear + 100 * h_under, rel=1e-12, abs=0)
    assert v_across == pytest.approx(50 * v_under + 50 * v_clear, rel=1e-12, abs=0)


#: lane a's pad on U1, with two pads of net x a pitch of 60 um away on either side
PAD_NETS = {"a": (None, [seg(TM2, 300, 500, 300, 640)]),
            "x": (None, [seg(TM2, 240, 500, 240, 640), seg(TM2, 360, 500, 360, 640)])}
PAD_BUMPS = [("a", 300, 500, 40.0, "U1", "P1"), ("x", 240, 500, 40.0, "U1", "P2"),
             ("x", 360, 500, 40.0, "U1", "P3")]


def pad_solves(tmp_path, monkeypatch, asm, terms):
    """The pad row and every body of revolution the pad solve hands the solver."""
    seen = []
    real = rl.Solver.revolution

    def spy(self, target, lows, x1):
        seen.append((target, lows))
        return real(self, target, lows, x1)
    monkeypatch.setattr(rl.Solver, "revolution", spy)
    row = run(tmp_path, PAD_NETS, PAD_BUMPS, asm, terms, name=str(len(list(tmp_path.iterdir()))))
    monkeypatch.setattr(rl.Solver, "revolution", real)
    return row, seen


@pytest.mark.slow
def test_one_pad_geometry_bounds_every_standoff(tmp_path, coarse, monkeypatch):
    """With the die and the pillars, each pad is solved once: the die face at the lowest
    standoff (28 um), at -1 V from the die's opening outward, and the pillar up to the full
    stack (44 um) through the hole. Every standoff in between has its pillar inside this one
    and its -1 V region inside this one's, so this solve is at or above each of them."""
    row, seen = pad_solves(tmp_path, monkeypatch, assembly(), ("die", "pillars"))
    st = rl.load_stack()
    assert row["c_pads_f"] > 0
    assert len(seen) == 1
    target, lows = seen[0]
    assert target == ((0.0, 20.0 + rl.GRID_NM / 1000) + st["metals"][TM2],
                      (0.0, 15.0, ATTACH, ATTACH + 44.0))
    above = [lo for lo in lows if lo[3] > ATTACH + 28.0]
    assert above == [(20.0, lows[-1][1], ATTACH + 28.0, st["ztop"])]
    for t in target:
        for lo in lows:
            assert not rl.touches(t, lo), (t, lo)


@pytest.mark.parametrize("opening,names,match", [
    (30.0, ("P1", "P2", "P3"), "no wider than the pillar"),
    (40.0, ("P2", "P3"), "no opening"),
])
def test_a_pad_without_room_in_the_die_face_is_refused(tmp_path, coarse, opening, names, match):
    with pytest.raises(rl.Refused, match=match):
        run(tmp_path, PAD_NETS, PAD_BUMPS, assembly(opening, names), ("die", "pillars"))


# ----------------------------------------------------------------------------
# the pillars

def staircase(r, cx, cz, n=8):
    """Rectangles inside the circle of radius r about (cx, cz): 2n slabs, each as wide as
    the circle at its edge farther from the centre."""
    out = []
    for k in range(n):
        za, zb = r * k / n, r * (k + 1) / n
        half = math.sqrt(r * r - zb * zb)
        if half > 0:
            out.append((cx - half, cx + half, cz + za, cz + zb))
            out.append((cx - half, cx + half, cz - zb, cz - za))
    return out


@pytest.mark.slow
def test_the_pillar_coax_holds_its_neighbours_outside_and_bounds_the_array(tmp_path, coarse,
                                                                          monkeypatch):
    """Along the pillar, every band holds -1 V from the nearest neighbour pillar inward
    (60 - 15 = 45 um), so no neighbour lies inside it. Per unit height that coax carries
    2 * 2 pi eps0 / ln(45 / 15), which is at or above a plan-view section of the discrete
    array at the same pitch: the pillar as a staircase inside its circle at +1 V, the four
    nearest neighbours as squares of the pillar's diameter at -1 V. The margin is about 16 %,
    and the finer default mesh only widens it."""
    asm = assembly()
    with_pillars, seen = pad_solves(tmp_path, monkeypatch, asm, ("pillars",))
    without, _ = pad_solves(tmp_path, monkeypatch, asm, ())
    assert with_pillars["c_pads_f"] > without["c_pads_f"]
    r_p, nearest = 15.0, 60 - 15.0
    assert len(seen) == 1
    target, lows = seen[0]
    assert target[1:] == ((0.0, r_p, ATTACH, ATTACH + 44.0),)
    along = [lo for lo in lows if lo[2] >= ATTACH and lo[3] <= ATTACH + 44.0]
    assert along and all(lo[0] == nearest for lo in along)
    assert max(lo[3] for lo in along) == ATTACH + 44.0
    coax = 2 * 2 * math.pi * field2d.EPS0 / math.log(nearest / r_p)
    c = 120.0
    own = [("o%d" % k,) + s for k, s in enumerate(staircase(r_p, 0.0, c))]
    nbrs = [("n%d" % k, dx - r_p, dx + r_p, c + dz - r_p, c + dz + r_p)
            for k, (dx, dz) in enumerate([(-60, 0), (60, 0), (0, -60), (0, 60)])]
    sec = field2d.Section(-c, c, 2 * c, [(0, 2 * c, 1.0)], own + nbrs)
    q = sec.solve(dict({o[0]: 1.0 for o in own}, **{n[0]: -1.0 for n in nbrs}))[0]
    planar = sum(q[o[0]] for o in own)
    assert coax >= planar


# ----------------------------------------------------------------------------
# crossings and pad neighbours, now inside the solve

@pytest.mark.slow
def test_a_crossing_adds_at_least_the_old_plate_less_the_carrier_it_shields(tmp_path, coarse):
    """c runs across a on TopMetal1. Its slab enters a's sections over its width grown by
    the layer gap on each side. The first version added the plate of that grown overlap
    (Miller 2) on top of a section that still coupled a to the carrier under it; here c takes
    that coupling, so the increment is at or above the plate less a's bare carrier coupling
    over the grown length, solved on the folded section's own mesh: the same grid with the
    crossing's slab taken out. On this fixture: about 1.22 fF against 1.43 - 0.58 = 0.84 fF;
    with the slab not grown, about 0.28 fF."""
    terms = ("perpendicular",)
    st = rl.load_stack()
    folded = []
    real = rl.Solver.section

    def spy(self, target, others, x0, x1):
        if any(o[2:4] == st["metals"][TM1] for o in others):
            folded.append((target, others, x0, x1))
        return real(self, target, others, x0, x1)
    alone = run(tmp_path, A, terms=terms, name="0")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rl.Solver, "section", spy)
        crossed = run(tmp_path, dict(A, c=(None, [seg(TM1, 500, -40, 500, 40, 1.64)])), terms=terms,
                      name="1")
    gap = st["metals"][TM2][0] - st["metals"][TM1][1]
    target, others, x0, x1 = folded[0]
    assert {o[:4] for _, os, _, _ in folded for o in os} == {o[:4] for o in others}  # one mesh
    bare = field2d.Section(x0, x1, st["ztop"], st["bands"], [("t",) + target],
                           lines=[o[:4] for o in others]).solve({"t": 1.0})[0]["t"]
    plate = rl.MILLER * field2d.EPS0 * st["eps_ox"] * (2 + 2 * gap) * (1.64 + 2 * gap) / gap * 1e-6
    carrier = bare * (1.64 + 2 * gap) * 1e-6
    increment = crossed["c_wires_f"] - alone["c_wires_f"]
    assert increment > 0
    assert increment >= plate - carrier


@pytest.mark.slow
def test_a_pad_neighbour_adds_at_least_the_old_pair_term(tmp_path, coarse):
    """The first version solved each pad as a disc alone and added, per neighbour, the 2D
    coupling of two strips over the pad's width, twice for a signal. The pad solve now holds
    -1 V from the nearest neighbour outward, which is at or above that sum."""
    near = run(tmp_path, A, A_PADS + [("x", 0, 80, 50.0)])
    st = rl.load_stack()
    z = st["metals"][TM2]
    solver = rl.Solver(st)
    x1 = 25 + rl.BOX_UM                       # the first version's box about the disc
    disc = solver.revolution(((0.0, 25.0) + z,), (), x1)[0]
    static, miller = solver.section((-25.0, 25.0) + z, ((55.0, 105.0) + z + ("signal",),),
                                    -25 - rl.BOX_UM, 105 + rl.BOX_UM)
    pair = rl.MILLER * (miller - static) * 50 * 1e-6
    assert near["c_pads_f"] >= 2 * disc + pair


@pytest.mark.slow
def test_fictitious_metal_holds_minus_one_volt_whatever_its_net(tmp_path, coarse, monkeypatch):
    """A rail's crossing is drawn metal at 0 V where it is, and -1 V where it is grown; a
    rail's pillar, a square of the stack's widest diameter, is -1 V, while its pad, drawn
    metal, stays at 0 V. Lowering only regions that are not the rail's metal keeps the solve
    at or above the true load."""
    seen = []
    real = rl.Solver.section

    def spy(self, target, others, x0, x1):
        seen.extend(others)
        return real(self, target, others, x0, x1)
    monkeypatch.setattr(rl.Solver, "section", spy)
    st = rl.load_stack()
    tm1 = st["metals"][TM1]
    run(tmp_path, dict(A, g=("ground", [seg(TM1, 500, -40, 500, 40, 1.64)])),
        terms=("perpendicular",), name="cross")
    crossing = {(o[1] - o[0], o[4]) for o in seen if o[2:4] == tm1}
    assert crossing == {(81.64, "rail"), (81.64, "low")}
    seen.clear()
    nets = dict(PAD_NETS, x=("ground", PAD_NETS["x"][1]))
    run(tmp_path, nets, PAD_BUMPS, assembly(), ("pillars",), name="pillar")
    pillars = {o[4] for o in seen if o[2:4] == (ATTACH, ATTACH + 44.0)}
    pads = {o[4] for o in seen if o[2:4] == st["metals"][TM2] and o[1] - o[0] == 40.0}
    assert (pillars, pads) == ({"low"}, {"rail"})


# ----------------------------------------------------------------------------
# vias and end caps

def test_end_caps_add_half_a_width_at_each_end(tmp_path, coarse):
    bare = run(tmp_path, {"a": (None, [seg(TM2, 0, 0, 200, 0)])}, name="0")
    capped = run(tmp_path, {"a": (None, [seg(TM2, 0, 0, 200, 0)])}, terms=("caps",), name="1")
    assert capped["c_wires_f"] == pytest.approx(bare["c_wires_f"] / 200 * 202, rel=1e-12, abs=0)


@pytest.mark.slow
def test_vias_are_counted_as_columns_of_their_own_net_and_of_others(tmp_path, coarse):
    """The net's own via adds its column; another net's via within the window enters a's
    sections as a column at that net's potential."""
    nets = {"a": (None, [seg(TM2, 0, 0, 100, 0), seg(TM1, 100, 0, 100, 80, 1.64)], [via(100, 0)]),
            "b": (None, [seg(TM2, 50, 8, 80, 8), seg(TM1, 50, 8, 50, 40, 1.64)], [via(50, 8)])}
    off = run(tmp_path, nets, name="0")
    on = run(tmp_path, nets, terms=("vias",), name="1")
    assert off["c_vias_f"] == 0 and on["vias"] == 1
    assert on["c_vias_f"] > 0
    assert on["c_wires_f"] > off["c_wires_f"]
    assert on["c_total_f"] == on["c_wires_f"] + on["c_vias_f"] + on["c_pads_f"]


# ----------------------------------------------------------------------------
# the GDS check

def kdb():
    return pytest.importorskip("klayout.db")


def pdk_layers():
    import routed_load_check as rlc
    lmap = rlc.layer_map()
    return {name: lmap[(name, "drawing")][0] for name in (TM2, TM1, "TopVia2")}


def pad_points(x, y, d, n=256, grid=0.005):
    """A landing pad as the exporter draws it: n points on the circle of diameter d, each
    snapped to the grid, so some lie a few nm outside it."""
    return [(round((x + d / 2 * math.sin(2 * math.pi * k / n)) / grid) * grid,
             round((y - d / 2 * math.cos(2 * math.pi * k / n)) / grid) * grid) for k in range(n)]


def write_gds(path, nets, bumps, extra=(), fill=(), layers=None, landing=None, cut=None):
    """The route drawn as the exporter draws it: segments with half a width past each end,
    TopVia2_C landings on both metals with their cut, grid-snapped discs of landing_um for
    pads. Layer numbers and via sizes are the PDK's unless given. extra: (layer, points or
    box) drawn on x/0; fill: the same on x/22."""
    db = kdb()
    layers = layers or pdk_layers()
    landing = landing or rl.VIA_LANDING_NM / 1000
    cut = cut or rl.VIA_CUT_NM / 1000
    ly = db.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("INTERPOSER")

    def nm(v):
        return int(round(v * 1000))

    def put(ln, dt, shape):
        if len(shape) == 4 and not isinstance(shape[0], (list, tuple)):
            top.shapes(ly.layer(ln, dt)).insert(db.Box(*[nm(v) for v in shape]))
        else:
            top.shapes(ly.layer(ln, dt)).insert(db.Polygon([db.Point(nm(x), nm(y)) for x, y in shape]))
    for name, v in nets.items():
        for s in v[1]:
            h = s["width"] / 2
            x0, x1 = sorted((s["x0"], s["x1"]))
            y0, y1 = sorted((s["y0"], s["y1"]))
            put(layers[s["layer"]], 0, (x0 - h, y0 - h, x1 + h, y1 + h))
        for w in (v[2] if len(v) > 2 else []):
            for ln, h in ((layers[TM1], landing / 2), (layers[TM2], landing / 2),
                          (layers["TopVia2"], cut / 2)):
                put(ln, 0, (w["x"] - h, w["y"] - h, w["x"] + h, w["y"] + h))
    for b in bumps:
        put(layers[TM2], 0, pad_points(b[1], b[2], b[3]))
    for ln, shape in extra:
        put(ln, 0, shape)
    for ln, shape in fill:
        put(ln, 22, shape)
    ly.write(str(path))
    return path


LANE = {"a": (None, [seg(TM2, 0, 0, 200, 0)])}
LANE_BUMPS = [("a", 0, 0, 50.0), ("a", 200, 0, 50.0)]
LANE_HALO = {"M4": 5.0, "M5": 5.0, "TM1": 10.0, "TM2": 10.0}


def check(tmp_path, nets=LANE, bumps=LANE_BUMPS, extra=(), fill=(), drawn=None, lanes=("a",)):
    """halo, then the GDS check on a drawing of the route plus extra and fill."""
    import routed_load_check as rlc
    tag = str(len(list(tmp_path.iterdir())))
    t, b, rec, *_ = halo(tmp_path, nets, bumps, list(lanes), LANE_HALO, "halo" + tag)
    gds = write_gds(tmp_path / ("i%s.gds" % tag), drawn or nets, bumps, extra, fill)
    chiplet = tmp_path / "design.chiplet"
    chiplet.write_text("components: []\n")
    return rlc.check(rec, gds, chiplet)


def whats(report, letter):
    return [f["what"] for f in report["findings"] if f["letter"] == letter]


def test_a_clean_drawing_of_the_route_passes(tmp_path):
    kdb()
    report = check(tmp_path)
    assert (report["result"], report["findings"], report["patches"]) == ("pass", [], [])
    assert report["top_cell"] == "INTERPOSER" and report["nets"] == ["a"]
    assert report["format"] == rl.CHECK_FORMAT and report["klayout_db"]


def test_the_check_finds_fill_inside_the_halo(tmp_path):
    """TopMetal2 halo 10 um: a fill tile 0.1 um inside it is a finding; one at its edge is not."""
    kdb()
    inside = check(tmp_path, fill=[(134, (90, 10.9, 110, 14.9))])
    edge = check(tmp_path, fill=[(134, (90, 11.0, 110, 15.0))])
    assert whats(inside, "iii") == ["dummy fill inside the halo"] and inside["result"] == "fail"
    assert inside["findings"][0]["layer"] == TM2 and inside["findings"][0]["net"] == "a"
    assert edge["result"] == "pass"


def test_the_check_finds_unmodelled_metal_within_the_window(tmp_path):
    """A floating TopMetal2 rectangle 59 um from the lane is a finding; at 61 um it is not."""
    kdb()
    inside = check(tmp_path, extra=[(134, (90, 60, 110, 62))])
    outside = check(tmp_path, extra=[(134, (90, 62, 110, 64))])
    assert whats(inside, "ii") == ["metal outside every footprint that touches no net"]
    assert outside["result"] == "pass"


def test_the_check_traces_the_lane_in_the_gds_not_in_the_traces(tmp_path):
    """A lane cut in the drawing, and a lane bridged to another net's wire: the traces are
    the same in both, so only tracing the GDS can see either."""
    kdb()
    cut = {"a": (None, [seg(TM2, 0, 0, 98, 0), seg(TM2, 102, 0, 200, 0)])}
    broken = check(tmp_path, drawn=cut)
    assert "no lane metal where the traces put it" in whats(broken, "i")
    assert "lane metal does not reach this landing pad" in whats(broken, "i")
    nets = dict(LANE, b=(None, [seg(TM2, 0, 12, 200, 12)]))
    bridged = check(tmp_path, nets=nets, extra=[(134, (99, 1, 101, 11))])
    assert "lane metal reaches net b" in whats(bridged, "i")


def test_the_check_refuses_metal_that_touches_two_nets_none_or_is_not_manhattan(tmp_path):
    kdb()
    nets = dict(LANE, b=(None, [seg(TM2, 0, 12, 200, 12)]))
    two = check(tmp_path, nets=nets, extra=[(134, (99, 1, 101, 11))])
    floating = check(tmp_path, extra=[(134, (90, 30, 110, 32))])
    slanted = check(tmp_path, extra=[(134, [(90, 1), (110, 1), (100, 6)])])
    assert "metal outside the footprints that touches nets a, b" in whats(two, "ii")
    assert whats(floating, "ii") == ["metal outside every footprint that touches no net"]
    assert whats(slanted, "ii") == ["metal outside the footprints of net a that is not Manhattan"]
    for report in (two, floating, slanted):
        assert report["result"] == "fail" and report["patches"] == []


def test_a_patch_belongs_to_the_net_it_touches_not_the_one_its_bbox_meets(tmp_path):
    """An L of lane metal wraps around the end of b's wire without touching it: its bounding
    box overlaps b's footprint, the polygon does not. It is a patch of a."""
    kdb()
    nets = dict(LANE, b=(None, [seg(TM2, 120, 10, 120, 30)]))
    ell = [(100, 1), (104, 1), (104, 36), (140, 36), (140, 40), (100, 40)]
    report = check(tmp_path, nets=nets, extra=[(134, ell)])
    assert report["result"] == "pass", report["findings"]
    assert [(p["net"], p["layer"], p["area_um2"]) for p in report["patches"]] == [("a", TM2, 300.0)]
    assert sorted(report["patches"][0]["rects_um"]) == report["patches"][0]["rects_um"]
    assert sum((r[2] - r[0]) * (r[3] - r[1]) for r in report["patches"][0]["rects_um"]) == \
        pytest.approx(300.0, rel=1e-12, abs=0)


@pytest.mark.slow
def test_a_pad_drawn_on_the_grid_lies_inside_the_solves_disc(tmp_path, monkeypatch):
    """The exporter snaps the pad disc's points to the grid, which puts some a few nm
    outside landing_um / 2, beyond a 1 nm margin. The solve's disc reaches one grid step
    out, so the check finds no pad metal beyond it, and the pad solve is that disc."""
    kdb()
    far = max(math.hypot(px, py) for px, py in pad_points(0, 0, 50.0))
    assert 25.001 < far < rl.pad_radius(50.0) == 25.0 + rl.GRID_NM / 1000
    report = check(tmp_path)
    assert (report["result"], report["patches"]) == ("pass", [])
    seen = []
    real = rl.Solver.revolution
    monkeypatch.setattr(rl.Solver, "revolution",
                        lambda self, t, lows, x1: seen.append(t) or real(self, t, lows, x1))
    run(tmp_path, LANE, LANE_BUMPS, name="solve")
    assert {t[0][:2] for t in seen} == {(0.0, rl.pad_radius(50.0))}


NOTCH = (-29, -4.655, -22.855, 0.16)


def test_lane_metal_in_the_landing_square_beyond_the_disc_is_a_patch(tmp_path, coarse):
    """A notch-heal box of the lane crosses the edge of its landing square, as the exporter
    draws one. Outside the square it is a patch by the Manhattan rule; inside, the part beyond
    the pad disc is named by its covering rectangles and solved at +1 V, so the load rises
    over the solve that leaves it out."""
    kdb()
    report = check(tmp_path, extra=[(134, NOTCH)])
    assert report["result"] == "pass", report["findings"]
    where = sorted(p["where"] for p in report["patches"])
    assert where == ["landing square", "outside the footprints"]
    inner = [p for p in report["patches"] if p["where"] == "landing square"][0]
    x0, y0, x1, y1 = (min(r[0] for r in inner["rects_um"]), min(r[1] for r in inner["rects_um"]),
                      max(r[2] for r in inner["rects_um"]), max(r[3] for r in inner["rects_um"]))
    # from the square's edge and the box's lower edge to where the disc meets the edge
    assert (x0, y0) == (-25.0, -4.655) and x1 < -24.5
    assert -math.sqrt(rl.pad_radius(50.0) ** 2 - 25.0 ** 2) - 0.01 < y1 < 0
    assert len(inner["rects_um"]) == 1 and 0 < inner["area_um2"] < 0.7
    outer = [p for p in report["patches"] if p["where"] == "outside the footprints"]
    got = patched(tmp_path, LANE, report["patches"], name="all")
    without = patched(tmp_path, LANE, outer, name="outer")
    assert got["c_total_f"] > without["c_total_f"]


#: an L of lane metal along two edges of the lane pad's square, beyond the disc
ELL = [(-25, -25, -4, -23), (-25, -24, -23, -4)]


def test_metal_in_the_landing_square_whose_box_touches_another_net_is_a_finding(tmp_path):
    """An L of the lane's pad metal in a corner of its landing square, beyond the disc,
    and b's via landing inside the L's bend: the L is lane metal and touches nothing of b,
    but its covering box, the conductor the solve would add, overlaps b's via. A finding,
    not a patch; without b's via the L is a patch."""
    kdb()
    nets = dict(LANE, b=(None, [seg(TM1, -60, -19, -19, -19, 1.64)], [via(-19, -19)]))
    alone = check(tmp_path, extra=[(134, r) for r in ELL])
    assert alone["result"] == "pass", alone["findings"]
    assert [p["where"] for p in alone["patches"]] == ["landing square"]
    x0, y0, x1, y1 = alone["patches"][0]["rects_um"][0]
    assert (x0, y0) == (-25.0, -25.0) and x1 > -19 + 0.95 and y1 > -19 + 0.95
    report = check(tmp_path, nets=nets, extra=[(134, r) for r in ELL])
    assert report["result"] == "fail"
    assert [f["what"] for f in report["findings"]] == [
        "metal in the landing square of net a beyond its pad disc that is not its own or "
        "touches another net"]


def test_lane_metal_in_another_nets_landing_square_is_a_finding(tmp_path):
    """The lane's wire ends in a corner of g's landing square, clear of g's disc, and a
    notch box of the lane sits on it there. The solve holds g's pad as its whole square, so
    lane metal beyond g's disc in that square is a finding; the wire's own footprint is not."""
    kdb()
    nets = {"a": (None, [seg(TM2, 0, 0, 130, 0), seg(TM2, 130, 0, 130, -100)]),
            "g": ("ground", [])}
    bumps = [("a", 0, 0, 50.0), ("a", 130, -100, 50.0), ("g", 146, 24, 50.0)]
    clean = check(tmp_path, nets=nets, bumps=bumps)
    assert clean["result"] == "pass", clean["findings"]
    report = check(tmp_path, nets=nets, bumps=bumps, extra=[(134, (122, 1, 128, 3))])
    assert [(f["letter"], f["net"], f["what"], f["bbox_um"]) for f in report["findings"]] == [
        ("ii", "a", "lane metal in the landing square of net g beyond its pad disc",
         [122.0, 1.0, 128.0, 3.0])]


LAYERS_TXT = ROOT / "libs.tech/klayout/tech/intm4tm2_layers.txt"
PYCELL_JSON = ROOT / "libs.tech/klayout/python/intm4tm2_pycell_lib/intm4tm2_tech.json"


def copy_tool(where, lmap=None, tech=None):
    """The tool's three files and the two PDK files it reads, laid out as in the PDK under
    where/libs.tech. lmap, tech: text in place of the layer map's or the pycell tech's;
    tech False leaves the pycell tech out. Returns the copy's parasitics directory."""
    lib = where / "libs.tech"
    (lib / "parasitics").mkdir(parents=True)
    for f in ("routed_load.py", "routed_load_check.py", "field2d.py"):
        shutil.copy(ROOT / "libs.tech/parasitics" / f, lib / "parasitics" / f)
    (lib / "klayout/tech").mkdir(parents=True)
    (lib / "klayout/tech/intm4tm2_layers.txt").write_text(LAYERS_TXT.read_text() if lmap is None else lmap)
    if tech is not False:
        (lib / "klayout/python/intm4tm2_pycell_lib").mkdir(parents=True)
        (lib / "klayout/python/intm4tm2_pycell_lib/intm4tm2_tech.json").write_text(
            PYCELL_JSON.read_text() if tech is None else tech)
    return lib / "parasitics"


def tool(tree, *argv):
    return subprocess.run([sys.executable, str(tree / "routed_load.py")] + [str(a) for a in argv],
                          capture_output=True, text=True, timeout=50)


def test_the_layer_numbers_and_via_sizes_come_from_the_pdk_files(tmp_path):
    """A copy of the tool beside a layer map with TopMetal2 renumbered to 234 and a pycell
    tech with TV2_d at 0.6 um. Its halo grows a 2.1 um via landing, and its check reads the
    renumbered layer and the wider landing: a drawing made to those files passes. The tool
    here, on the same drawing, finds no TopMetal2 at the pads."""
    kdb()
    lmap, n = re.subn(r"^(TopMetal2 \S+) 134 ", r"\1 234 ", LAYERS_TXT.read_text(), flags=re.M)
    assert n > 2
    tech = json.loads(PYCELL_JSON.read_text())
    tech["techParams"]["TV2_d"] = 0.6
    there_tool = copy_tool(tmp_path / "pdk", lmap=lmap, tech=json.dumps(tech))
    nets = {"a": (None, [seg(TM2, 0, 0, 100, 0), seg(TM1, 100, 0, 100, 80, 1.64)],
                  [via(100, 0), via(100, 80)])}
    bumps = [("a", 0, 0, 50.0), ("a", 100, 80, 50.0)]
    t, b = write_route(tmp_path, nets, bumps)
    gds = write_gds(tmp_path / "renumbered.gds", nets, bumps,
                    layers={TM2: 234, TM1: 126, "TopVia2": 133}, landing=2.1)
    chiplet = tmp_path / "design.chiplet"
    chiplet.write_text("components: []\n")
    rec, side = tmp_path / "rec.json", tmp_path / "side.json"
    r = tool(there_tool, "halo", "--traces", t, "--bumps", b, "--nets", "a",
             "--halo-um", "M4=0,M5=0,TM1=0,TM2=10", "--halo-source", HALO_SOURCE,
             "--out", rec, "--sidecar", side)
    assert r.returncode == 0, r.stderr
    regions = [r["polygon_um"][0] + r["polygon_um"][2] for r in json.loads(side.read_text())["regions"]]
    assert [88.945, -11.055, 111.055, 11.055] in regions      # 2.1 um landing grown 10.005
    there = tool(there_tool, "check", "--record", rec, "--gds", gds, "--chiplet", chiplet,
                 "--out", tmp_path / "there.json")
    assert there.returncode == 0, there.stdout + there.stderr
    assert json.loads((tmp_path / "there.json").read_text())["patches"] == []
    here = tool(ROOT / "libs.tech/parasitics", "check", "--record", rec, "--gds", gds,
                "--chiplet", chiplet, "--out", tmp_path / "here.json")
    assert here.returncode == 3
    assert "no TopMetal2 at the landing pad" in whats(json.loads((tmp_path / "here.json").read_text()), "i")


@pytest.mark.parametrize("tech", [False, "{not json",
                                  json.dumps({"techParams": {"grid": 0.005, "TV2_a": 0.9}}),
                                  json.dumps({"techParams": {"grid": 0.005, "TV2_a": 0.9, "TV2_d": "0.5"}})],
                         ids=["missing", "not json", "no TV2_d", "TV2_d a string"])
def test_a_pycell_tech_the_tool_cannot_read_is_refused(tmp_path, tech):
    """Every mode exits 2 with the reason before it reads anything, never a traceback."""
    tree = copy_tool(tmp_path, tech=tech)
    path = tree.resolve().parent / "klayout/python/intm4tm2_pycell_lib/intm4tm2_tech.json"
    for argv in (["halo", "--traces", "t", "--bumps", "b", "--nets", "a", "--halo-um",
                  "M4=0,M5=0,TM1=0,TM2=10", "--halo-source", HALO_SOURCE, "--out", "r",
                  "--sidecar", "s"],
                 ["check", "--record", "r", "--gds", "g", "--chiplet", "c", "--out", "o"],
                 ["solve", "--record", "r", "--check", "c", "--traces", "t", "--bumps", "b",
                  "--chiplet", "d", "--methods", "m", "--pins", "U1=p", "--out", "o"]):
        r = tool(tree, *argv)
        assert (r.returncode, r.stdout) == (2, ""), r.stderr
        assert r.stderr.startswith("routed_load: %s: " % path) and "Traceback" not in r.stderr


def test_the_check_refuses_inputs_it_cannot_tie_to_the_record(tmp_path, capsys):
    """Exit 2 and no report: traces changed since the halo, a report path that exists, a GDS
    with two top cells."""
    db = kdb()
    t, b, rec, *_ = halo(tmp_path, LANE, LANE_BUMPS, ["a"], LANE_HALO)
    gds = write_gds(tmp_path / "i.gds", LANE, LANE_BUMPS)
    chiplet = tmp_path / "design.chiplet"
    chiplet.write_text("components: []\n")

    def main(gds_path, out):
        return rl.main(["check", "--record", str(rec), "--gds", str(gds_path), "--chiplet",
                        str(chiplet), "--out", str(out)])
    assert main(gds, tmp_path / "ok.json") == 0
    assert main(gds, tmp_path / "ok.json") == 2
    two = db.Layout()
    two.read(str(gds))
    two.create_cell("STRAY")
    two.write(str(tmp_path / "two.gds"))
    assert main(tmp_path / "two.gds", tmp_path / "two.json") == 2
    assert not (tmp_path / "two.json").exists()
    t.write_text(t.read_text() + " ")
    assert main(gds, tmp_path / "moved.json") == 2
    assert not (tmp_path / "moved.json").exists()
    fill = write_gds(tmp_path / "f.gds", LANE, LANE_BUMPS, fill=[(134, (90, 5, 110, 9))])
    t.write_text(t.read_text()[:-1])
    assert main(fill, tmp_path / "fill.json") == 3
    assert json.loads((tmp_path / "fill.json").read_text())["result"] == "fail"


# ----------------------------------------------------------------------------
# patches: metal the exporter draws outside the traces, named by the check, solved

def patched(tmp_path, nets, patches, terms=("caps", "patches"), name="p"):
    asm = rl.Assembly(patches=patches)
    return run(tmp_path, nets, asm=asm, terms=terms, name=name)


@pytest.mark.slow
def test_a_patch_on_the_bounded_net_loads_like_the_same_metal_in_the_traces(tmp_path, coarse):
    """The check names a notch patch on lane a; the solve with that report equals the solve
    of the same route with the patch written as one more segment of a."""
    kdb()
    report = check(tmp_path, extra=[(134, (90, 1, 110, 4))])
    assert report["result"] == "pass"
    assert report["patches"] == [{"net": "a", "layer": TM2, "where": "outside the footprints",
                                  "rects_um": [[90.0, 1.0, 110.0, 4.0]], "area_um2": 60.0}]
    got = patched(tmp_path, LANE, report["patches"])
    ref = run(tmp_path, {"a": (None, LANE["a"][1] + [seg(TM2, 91.5, 2.5, 108.5, 2.5, 3.0)])},
              terms=("caps",), name="ref")
    bare = run(tmp_path, LANE, terms=("caps",), name="bare")
    assert got["c_total_f"] == pytest.approx(ref["c_total_f"], rel=1e-12, abs=0)
    assert got["c_total_f"] > bare["c_total_f"]


@pytest.mark.slow
def test_another_nets_patch_sits_at_that_nets_potential(tmp_path, coarse):
    """A patch of signal net b reaching toward a is at -1 V under Miller, as b is: the solve
    equals the route with the patch drawn as b's metal, and it falls when b is a rail."""
    nets = dict(LANE, b=(None, [seg(TM2, 0, 12, 200, 12)]))
    patch = [{"net": "b", "layer": TM2, "rects_um": [[90, 6, 110, 11]]}]
    got = patched(tmp_path, nets, patch, name="s")
    ref = run(tmp_path, dict(LANE, b=(None, nets["b"][1] + [seg(TM2, 92.5, 8.5, 107.5, 8.5, 5.0)])),
              terms=("caps",), name="ref")
    rail = patched(tmp_path, dict(LANE, b=("ground", nets["b"][1])), patch, name="r")
    assert got["c_total_f"] == pytest.approx(ref["c_total_f"], rel=1e-12, abs=0)
    assert got["c_total_f"] > rail["c_total_f"]


def test_a_patch_has_no_size_cap(tmp_path, coarse):
    """A 50 x 50 um block on the ground rail within the window is a patch like any other,
    named by the check and solved at 0 V; the load rises."""
    kdb()
    nets = dict(LANE, g=("ground", [seg(TM2, 0, 30, 200, 30)]))
    report = check(tmp_path, nets=nets, extra=[(134, (75, 31, 125, 81))])
    assert report["result"] == "pass", report["findings"]
    assert [(p["net"], p["area_um2"]) for p in report["patches"]] == [("g", 2500.0)]
    assert report["patch_summary"] == {"g": {"patches": 1, "area_um2": 2500.0,
                                             "largest_um2": 2500.0}}
    got = patched(tmp_path, nets, report["patches"])
    bare = run(tmp_path, nets, terms=("caps",), name="bare")
    assert got["c_total_f"] > bare["c_total_f"]


# ----------------------------------------------------------------------------
# same-net metal in a section: one conductor with the piece, never dropped, never moved

def built(tmp_path, monkeypatch, segments, vias=(), rects=()):
    """Every section the solve builds for net a, on the default mesh, built and checked with
    no solve as the sections mode does: [(piece, mid, target, others)]."""
    seen = []
    real = rl.section_at

    def spy(t, mid, route, items, holes, dies):
        got = real(t, mid, route, items, holes, dies)
        seen.append((t, mid) + got[:2])
        return got
    monkeypatch.setattr(rl, "section_at", spy)
    t, b = write_route(tmp_path, {"a": (None, segments, list(vias))})
    asm = rl.Assembly(patches=[{"net": "a", "layer": TM2, "rects_um": list(rects)}] if rects else [])
    route, _ = rl.route_of(t, b, ["a"], asm, ("caps", "vias", "patches"))
    rl.net_load("a", route, rl.Survey(route.stack))
    return seen


def drawn(segments, rects):
    """Net a's drawn metal, exact: (layer, x0, y0, x1, y1), each segment with half its width
    past each end, and each patch on TopMetal2."""
    def d(v):
        return Decimal(repr(v))
    out = []
    for s in segments:
        h = d(s["width"]) / 2
        (x0, x1), (y0, y1) = sorted((d(s["x0"]), d(s["x1"]))), sorted((d(s["y0"]), d(s["y1"])))
        out.append((s["layer"], x0 - h, y0 - h, x1 + h, y1 + h))
    return out + [(TM2,) + tuple(d(v) for v in r) for r in rects]


def unjoined(seen, metal):
    """The wire sections whose bounded conductor (the target and the own metal that touches
    it on its layer) is not, to 0.1 nm, the drawn metal of the net on that layer that runs
    along the piece and is connected to it across at mid; and how many sections that metal
    holds more than the piece itself."""
    bad, joins = [], 0
    for t, mid, target, others in seen:
        if t.src is None:            # a via's sections leave out its host wires
            continue
        mine = [target] + [o for o in others
                           if o[4] == "own" and o[2:4] == target[2:4] and rl.touches(target, o)]
        got = (Decimal(repr(round(t.c + min(m[0] for m in mine), 4))),
               Decimal(repr(round(t.c + max(m[1] for m in mine), 4))))
        at, spans = Decimal(repr(mid)), []
        for layer, x0, y0, x1, y1 in metal:
            orient = "h" if x1 - x0 >= y1 - y0 else "v"
            a0, a1, c0, c1 = (x0, x1, y0, y1) if orient == "h" else (y0, y1, x0, x1)
            if layer == t.src.layer and orient == t.orient and a0 < at < a1:
                spans.append((c0, c1))
        merged = []
        for lo, hi in sorted(spans):
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        c = Decimal(repr(round(t.c, 4)))
        want = next(m for m in merged if m[0] < c < m[1])
        if got != want:
            bad.append((t.orient, c, mid, got, want))
        joins += want[1] - want[0] > Decimal(repr(round(t.w, 4)))
    return bad, joins


#: Where the exporter's notch patches meet a lane's trace on TopMetal2, at a routed lane's
#: real coordinates: (segments, vias, patch rects).
REAL = {
    "patch-inside-trace": ([seg(TM2, 1336.0, 606.67, 1336.0, 751.12)], [],
                           [[1337.0, 609.3, 1337.219, 609.305], [1337.0, 609.305, 1339.68, 613.495],
                            [1338.141, 609.229, 1339.68, 609.305]]),
    "patch-inside-trace-2": ([seg(TM2, 736.0, 1436.64, 736.0, 2023.76)], [],
                             [[737.0, 1439.275, 739.36, 1443.47], [737.821, 1439.199, 739.36, 1439.275]]),
    "patch-short-of-trace": ([seg(TM2, 1036.0, 1436.64, 1036.0, 1623.6)], [],
                             [[1037.0, 1439.274, 1037.059, 1439.275], [1037.0, 1439.275, 1039.52, 1443.47],
                              [1037.981, 1439.199, 1039.52, 1439.275]]),
    "patch-dropped": ([seg(TM2, 664.0, 1436.64, 664.0, 1869.6)], [],
                      [[660.32, 1439.199, 661.859, 1439.275], [660.32, 1439.275, 663.0, 1443.465],
                       [662.781, 1439.27, 663.0, 1439.275]]),
    "patch-dropped-2": ([seg(TM2, 1264.0, 1436.64, 1264.0, 1623.6)], [],
                        [[1260.64, 1439.199, 1262.179, 1439.275], [1260.64, 1439.275, 1263.0, 1443.47]]),
    "corner": ([seg(TM2, 1372, 2633.84, 1372, 2634.16), seg(TM2, 1372, 2634.16, 1376, 2634.16),
                seg(TM1, 1276, 2633.84, 1372, 2633.84, 1.64)], [via(1372, 2633.84)],
               [[1371.0, 2630.345, 1375.0, 2632.84], [1373.0, 2632.84, 1375.0, 2633.16],
                [1375.0, 2630.345, 1375.432, 2633.16]]),
    "corner-2": ([seg(TM2, 1372, 2814.16, 1372, 2814.24), seg(TM2, 1372, 2814.16, 1376, 2814.16),
                  seg(TM1, 1192, 2814.24, 1372, 2814.24, 1.64)], [via(1372, 2814.24)],
                 [[1371.0, 2815.24, 1375.0, 2818.1], [1373.0, 2815.16, 1375.0, 2815.24],
                  [1375.0, 2815.461, 1375.188, 2818.1]]),
}


@pytest.mark.parametrize("segments,vias,rects", REAL.values(), ids=REAL.keys())
def test_a_patch_beside_a_trace_is_one_conductor_with_it(tmp_path, monkeypatch, segments, vias,
                                                          rects):
    """A patch's width is a difference of absolute coordinates and carries float noise, so a
    patch and the trace it abuts touched by +-1e-13 um: the patch's edge inside the trace's
    (two conductors that overlap), short of it (a +1 V conductor solved but not counted), or
    a gap read as an overlap that left each out of the other's sections. In every wire
    section the bounded conductor is now the net's drawn metal along the piece and connected
    to it, to 0.1 nm. The corners hold a via and an interval of 2 h0 plus 1e-16, where the
    graded mesh met itself."""
    bad, joins = unjoined(built(tmp_path, monkeypatch, segments, vias, rects), drawn(segments, rects))
    assert bad == [] and joins > 0


#: a lane that steps from 2 um to 3 um wide on TopMetal2: its two segments overlap at the step
STEP = [seg(TM2, 0, 0, 100, 0), seg(TM2, 100, 0, 200, 0, 3.0)]


def test_own_metal_that_overlaps_a_wire_is_one_conductor_with_it(tmp_path, monkeypatch):
    """At the step each wire's sections hold the union of the two: the wide wire's parts
    outside the narrow target join it, and the narrow wire lies inside the wide target."""
    bad, joins = unjoined(built(tmp_path, monkeypatch, STEP), drawn(STEP, ()))
    assert bad == [] and joins > 0


def test_same_net_metal_left_out_of_a_section_is_refused(tmp_path, monkeypatch):
    """Each section is checked against the net's wires along its piece: one left out of it,
    here by a zone that never sees the net's own metal (so its ends are not cuts either), is
    refused, never solved without it."""
    real = rl.zone

    def zone(t, route, neighbours=None):
        items, holes, dies, _ = real(t, route, neighbours)
        items = [it for it in items if it[0] != t.net]
        cuts = {t.s0, t.s1} | {v for it in items + holes + dies for v in it[-2:] if t.s0 < v < t.s1}
        return items, holes, dies, sorted(cuts)
    monkeypatch.setattr(rl, "zone", zone)
    with pytest.raises(rl.Refused, match="same-net metal is never dropped"):
        built(tmp_path, monkeypatch, STEP)


def test_own_metal_that_abuts_a_via_column_joins_it_and_only_its_hosts_are_left_out(tmp_path,
                                                                                     monkeypatch):
    """A via's sections leave out its host wires, the net's metal over its column. A patch that
    only abuts the column is not a host, though its raw gap reads -2e-13 um: it joins every
    section of the via."""
    segments, rect = [seg(TM1, 1276, 2633.84, 1372, 2633.84, 1.64)], [1371.0, 2634.79, 1373.0, 2635.2]
    seen = built(tmp_path, monkeypatch, segments, [via(1372, 2633.84)], [rect])
    vias = [(target, others) for t, mid, target, others in seen if t.src is None]
    assert vias
    for target, others in vias:
        assert [o[:2] for o in others if o[4] == "own" and rl.touches(target, o)] == [(0.95, 1.36)]
    assert unjoined(seen, drawn(segments, [rect]))[0] == []


E2E_NETS = {"a": (None, [seg(TM2, 100, 300, 250, 300)]), "g": ("ground", [seg(TM2, 100, 330, 250, 330)])}
E2E_BUMPS = [("a", 100, 300, 50.0, "U1", "P1"), ("a", 250, 300, 50.0, "U2", "P1")]
E2E_CHIPLET = """components:
  - id: IP
    type: interposer
    anchor: bbox_center
    position: {x: 500, y: 500}
    dimensions: {width: 1000, height: 1000}
  - id: U1
    type: die
    anchor: bbox_center
    position: {x: 100, y: 300}
    dimensions: {width: 120, height: 120}
    connection: pillar
  - id: U2
    type: die
    anchor: bbox_center
    position: {x: 250, y: 300}
    dimensions: {width: 120, height: 120}
    connection: pillar
"""


def cli_route(tmp_path):
    """The E2E route with its halo record and passing check report, made by the commands:
    (the solve's argv without --out, record, GDS, design.chiplet, report)."""
    pytest.importorskip("yaml")
    kdb()
    chiplet, methods = tmp_path / "design.chiplet", tmp_path / "interconnect_methods.json"
    chiplet.write_text(E2E_CHIPLET)
    methods.write_text(json.dumps(METHODS))
    pin_paths = {}
    for comp in ("U1", "U2"):
        pin_paths[comp] = tmp_path / ("%s.pins.json" % comp)
        pin_paths[comp].write_text(json.dumps(pins(40.0, ("P1",))["U1"]))
    extra = {"inputs": {"chiplet": {"path": str(chiplet), "sha256": rl.sha256(chiplet)},
                        "pins": {c: {"path": str(p), "sha256": rl.sha256(p)}
                                 for c, p in pin_paths.items()}},
             "rules_source": {"interconnect_methods": {"path": str(methods),
                                                       "sha256": rl.sha256(methods)}}}
    t, b = write_route(tmp_path, E2E_NETS, E2E_BUMPS, extra=extra)
    rec, side = tmp_path / "routed_load.json", tmp_path / "routed_load.nofill.json"
    assert rl.main(["halo", "--traces", str(t), "--bumps", str(b), "--nets", "a",
                    "--halo-um", "M4=20,M5=20,TM1=20,TM2=20", "--halo-source", HALO_SOURCE,
                    "--out", str(rec), "--sidecar", str(side)]) == 0
    gds = write_gds(tmp_path / "interposer.gds", E2E_NETS, E2E_BUMPS)
    report = tmp_path / "routed_load.check.json"
    assert rl.main(["check", "--record", str(rec), "--gds", str(gds), "--chiplet", str(chiplet),
                    "--out", str(report)]) == 0
    base = ["solve", "--record", str(rec), "--check", str(report), "--traces", str(t),
            "--bumps", str(b), "--chiplet", str(chiplet), "--methods", str(methods),
            "--pins", ",".join("%s=%s" % kv for kv in sorted(pin_paths.items()))]
    return base, rec, gds, chiplet, report


@pytest.mark.slow
def test_halo_check_and_solve_from_the_command_line(tmp_path, coarse, capsys):
    base, rec, gds, chiplet, report = cli_route(tmp_path)
    out = tmp_path / "load.json"
    assert rl.main(base + ["--out", str(out), "--budget-ff", "1000", "--outside-ff", "0"]) == 0
    load = json.loads(out.read_text())
    assert (load["format"], load["version"], load["mode"]) == (rl.FORMAT, 2, "solve")
    assert load["inputs"]["check"]["sha256"] == rl.sha256(report)
    assert load["method"]["terms"] == list(rl.TERMS)
    assert load["method"]["dies"]["U1"]["face_z_um"] == {"low": ATTACH + 28.0, "high": ATTACH + 44.0}
    assert load["method"].get("control") == (
        "the 2D-section and mesh approximations are not signed; their control is a refinement "
        "gate outside this tool, which accepts a lane only when a finer mesh moves its value by "
        "no less than -3 % and no more than +0.1 %. That is the gate's acceptance band, not a "
        "measured error: the shift measured on each lane is in the record of the reading that "
        "applies the gate, not in this one")
    assert "no bonded standoff is read" in load["method"].get("standoff", "")
    assert load["inputs"]["pycell_tech"]["sha256"] == rl.sha256(rl.PYCELL_TECH)
    assert json.loads(report.read_text())["inputs"]["layers"]["sha256"] == \
        rl.sha256(ROOT / "libs.tech/klayout/tech/intm4tm2_layers.txt")
    row = load["nets"]["a"]
    assert row["c_total_f"] == row["c_wires_f"] + row["c_vias_f"] + row["c_pads_f"]
    assert row["pads"] == 2 and load["budget"]["reading"] == "WITHIN"
    assert "via sections leave out their host wires; via and host are counted on their own" \
        in load["method"]["approximations"]
    assert rl.main(base + ["--out", str(out)]) == 2          # never overwrites
    capsys.readouterr()
    over = tmp_path / "over.json"
    assert rl.main(base + ["--out", str(over), "--budget-ff", "1", "--outside-ff", "0"]) == 3
    assert "OVER" in capsys.readouterr().out
    other = tmp_path / "other.chiplet"
    other.write_text(E2E_CHIPLET + "\n")
    bad = list(base)
    bad[bad.index("--chiplet") + 1] = str(other)
    assert rl.main(bad + ["--out", str(tmp_path / "bad.json")]) == 2
    assert not (tmp_path / "bad.json").exists()
    # a check report made by another tree: one file the check reads from the PDK differs
    trees = {"pycell_tech": copy_tool(tmp_path / "t0", tech=json.dumps(json.loads(PYCELL_JSON.read_text()))),
             "layers": copy_tool(tmp_path / "t1", lmap=LAYERS_TXT.read_text() + "# another tree\n")}
    for name in ("routed_load", "routed_load_check"):
        trees[name] = copy_tool(tmp_path / name)
        with open(trees[name] / ("%s.py" % name), "a") as f:
            f.write("# another tree\n")
    assert (tmp_path / "t0/libs.tech/klayout/python/intm4tm2_pycell_lib/intm4tm2_tech.json").read_bytes() \
        != PYCELL_JSON.read_bytes()
    for name, tree in trees.items():
        theirs = tmp_path / ("other_%s.check.json" % name)
        r = tool(tree, "check", "--record", rec, "--gds", gds, "--chiplet", chiplet, "--out", theirs)
        assert r.returncode == 0, r.stderr
        other = list(base)
        other[other.index("--check") + 1] = str(theirs)
        capsys.readouterr()
        assert rl.main(other + ["--out", str(tmp_path / ("other_%s.load.json" % name))]) == 2
        assert "made with another %s than this tool's" % name in capsys.readouterr().err
        assert rl.main(["sections"] + other[1:]) == 2


@pytest.mark.slow
def test_the_sections_mode_builds_what_the_solve_solves_and_writes_nothing(tmp_path, coarse,
                                                                           capsys):
    """sections reads what solve reads and builds the same sections and pad bodies, with no
    solve and no file: exit 0 and the solve's counts. Inputs the solve refuses, it refuses."""
    base, rec, gds, chiplet, report = cli_route(tmp_path)
    before = sorted(tmp_path.iterdir())
    capsys.readouterr()
    assert rl.main(["sections"] + base[1:]) == 0
    said = capsys.readouterr().out
    assert sorted(tmp_path.iterdir()) == before
    out = tmp_path / "load.json"
    assert rl.main(base + ["--out", str(out)]) == 0
    row = json.loads(out.read_text())["nets"]["a"]
    assert "a: %d sections, %d pad solves" % (row["pieces"], row["pads"]) in said
    assert "%d sections on 1 nets" % (row["pieces"] + row["pads"]) in said
    other = tmp_path / "other.chiplet"
    other.write_text(E2E_CHIPLET + "\n")
    bad = ["sections"] + base[1:]
    bad[bad.index("--chiplet") + 1] = str(other)
    assert rl.main(bad) == 2
