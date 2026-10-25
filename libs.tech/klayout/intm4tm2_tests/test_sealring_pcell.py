########################################################################
#
# Copyright 2026 IHP PDK Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
########################################################################
"""Regression for the 'sealring' PCell (IntM4TM2 interposer PDK).

The PCell is generated in one headless 'klayout -zz -rx -r' batch, like the
bondpad test. Its geometry is then checked against KLayout's own sizing of
the EdgeSeal outline, not against a copy of the PCell's construction, and
every width, enclosure and space is checked for exactness: at least the rule
value and nothing wider. The measurement itself is validated first on
synthetic rings with a known answer. Finally the default decks run on a
carrier made of the ring plus prBoundary, and the pad deck shows that the
ring makes Pad.dR live.

Fill around the ring: fill_stack, the KiCad plugin's entry point, keeps every
filler inside prBoundary AND the ring interior, and the filled carrier passes
the seal, filler and density decks. The fill macros stop, each with its own
reason, on a ring that cannot bound the fill.
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import klayout.db as kdb

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"
RULES = json.loads((DRC_DIR / "rule_decks" / "interposer_tech_default.json").read_text())["rules"]

KLAYOUT_BIN = shutil.which("klayout")

# All values in nm (dbu 0.001 um).
SEAL_A = round(RULES["Seal_a"] * 1000)
SEAL_E = round(RULES["Seal_e"] * 1000)
SEAL_F = round(RULES["Seal_f"] * 1000)
SEAL_K = round(RULES["Seal_k"] * 1000)
VIA_RINGS = {  # layer -> ring width
    (66, 0): round(RULES["Seal_c1"] * 1000),   # Via4
    (125, 0): round(RULES["Seal_c2"] * 1000),  # TopVia1
    (133, 0): round(RULES["Seal_c3"] * 1000),  # TopVia2
}
BOUNDARY_TO_EDGESEAL = 30000
STEP = 5

# Public SG13G2 section 6.10 values, as literals: every other test reads the
# same rule file as the PCell and would follow a drifted value.
PUBLIC_SEAL_RULES = {"Seal_a": 3.5, "Seal_c1": 0.19, "Seal_c2": 0.42, "Seal_c3": 0.9,
                     "Seal_e": 4.2, "Seal_f": 1.0, "Seal_k": 21.0}

EDGESEAL = (39, 0)
BOUNDARY = (39, 4)
PASSIV = (9, 0)
PRBOUNDARY = (235, 0)
DFPAD = (41, 0)
METALS = [(50, 0), (67, 0), (126, 0), (134, 0)]
DRAWN = {EDGESEAL, BOUNDARY, PASSIV} | set(METALS) | set(VIA_RINGS)

SIZE_X, SIZE_Y = 1000000, 800000

# Each case uses its own parameter spelling, so every build is a new PCell
# variant and never KLayout's cached copy of an earlier one.
CASES = [
    ("ok", {"l": "1000.000u", "w": "800.000u"}),
    ("ok_again", {"l": "1000.0u", "w": "800.0u"}),
    ("min", {"l": "114.250u", "w": "114.250u"}),
    ("min_w", {"l": "1000.000u", "w": "114.250u"}),
    ("offgrid", {"l": "1000.002u", "w": "800.000u"}),
    ("small", {"l": "1000.000u", "w": "114.245u"}),
    ("no_unit", {"l": "1000", "w": "800.000u"}),
    ("prefixed", {"l": "1000.000u", "w": "800.000mu"}),
]
# Builds against a rule file with one key removed (None) or changed.
RULE_EDITS = [
    ("missing_key", "Seal_a", None, "1005.000u"),
    ("rule_offgrid", "Seal_e", 4.202, "1010.000u"),
    ("via_offcentre", "Seal_c1", 0.195, "1015.000u"),
]
GUARDED = ["offgrid", "small", "no_unit", "prefixed"] + [edit[0] for edit in RULE_EDITS]

PCELL_BATCH_SCRIPT = textwrap.dedent("""\
    import json
    import sys
    import pya

    if 'intm4tm2' in pya.Technology.technology_names():
        tech = pya.Technology.technology_by_name('intm4tm2')
    else:
        tech = pya.Technology.create_technology('intm4tm2')
    tech.load({lyt_path!r})

    sys.path.insert(0, {python_dir!r})
    sys.path.insert(0, {api_dir!r})

    import intm4tm2_pycell_lib  # registers the 'IntM4TM2' library

    def build(name, params):
        layout = pya.Layout()
        layout.dbu = 0.001
        layout.technology_name = 'intm4tm2'
        cell = layout.create_cell("sealring", "IntM4TM2", params)
        assert cell is not None, "create_cell returned None for " + name
        top = layout.create_cell("TOP")
        top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))
        opts = pya.SaveLayoutOptions()
        opts.write_context_info = False
        layout.write({out_dir!r} + "/sealring_" + name + ".gds", opts)

    for name, params in {cases!r}:
        build(name, params)

    # A broken rule file must stop the PCell, not fall back or draw part of it.
    code = sys.modules['intm4tm2_pycell_lib.ihp.sealring_code']
    with open(code.RULES_FILE) as f:
        pristine = f.read()
    for name, key, value, size in {rule_edits!r}:
        data = json.loads(pristine)
        if value is None:
            del data['rules'][key]
        else:
            data['rules'][key] = value
        path = {out_dir!r} + "/rules_" + name + ".json"
        with open(path, 'w') as f:
            json.dump(data, f)
        code.RULES_FILE = path
        build(name, {{"l": size, "w": "800.000u"}})
""")


def _clean_env(home):
    env = dict(os.environ, KLAYOUT_HOME=str(home))
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)
    return env


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    if KLAYOUT_BIN is None:
        pytest.skip("klayout binary not on PATH")
    out_dir = tmp_path_factory.mktemp("sealring")
    home = out_dir / "klayout_home"
    home.mkdir()
    script = out_dir / "gen_sealrings.py"
    script.write_text(PCELL_BATCH_SCRIPT.format(
        lyt_path=str(REPO_KLAYOUT / "tech" / "intm4tm2.lyt"),
        python_dir=str(PYTHON_DIR),
        api_dir=str(PYTHON_DIR / "pycell4klayout-api" / "source" / "python"),
        cases=CASES,
        rule_edits=RULE_EDITS,
        out_dir=str(out_dir)))
    result = subprocess.run(
        [KLAYOUT_BIN, "-zz", "-rx", "-r", str(script)],
        capture_output=True, text=True, env=_clean_env(home), timeout=300)
    assert result.returncode == 0, f"pcell batch failed:\n{result.stdout}\n{result.stderr}"
    layouts = {}
    for name in [c[0] for c in CASES] + [e[0] for e in RULE_EDITS]:
        layout = kdb.Layout()
        layout.read(str(out_dir / f"sealring_{name}.gds"))
        layouts[name] = (layout, layout.top_cell())
    return {"layouts": layouts, "log": result.stdout + result.stderr, "dir": out_dir}


def _region(layout, cell, key):
    region = kdb.Region(cell.begin_shapes_rec(layout.layer(*key)))
    region.merge()
    return region


def _drawn(layout, cell):
    drawn = set()
    for idx in layout.layer_indexes():
        if not cell.bbox_per_layer(idx).empty():
            info = layout.get_info(idx)
            drawn.add((info.layer, info.datatype))
    return drawn


def _exact_width(region, width):
    """Every width at least `width`, and some part exactly `width`."""
    return region.width_check(width).is_empty() and not region.width_check(width + STEP).is_empty()


def _ring(box, width):
    return kdb.Region(box) - kdb.Region(box.enlarged(-width, -width))


# --- the measurement, on synthetic rings with a known answer -------------

def test_width_measure_on_synthetic_control():
    box = kdb.Box(0, 0, 100000, 80000)
    assert _exact_width(_ring(box, SEAL_A), SEAL_A)
    assert not _exact_width(_ring(box, SEAL_A - STEP), SEAL_A)   # too narrow
    assert not _exact_width(_ring(box, SEAL_A + STEP), SEAL_A)   # too wide


def test_enclosure_and_space_measure_on_synthetic_control():
    outer = _ring(kdb.Box(0, 0, 100000, 80000), SEAL_A)
    inner = _ring(kdb.Box(1300, 1300, 98700, 78700), 900)
    assert outer.enclosing_check(inner, 1300).is_empty()
    assert not outer.enclosing_check(inner, 1305).is_empty()
    assert not outer.enclosing_check(_ring(kdb.Box(1295, 1295, 98705, 78705), 900), 1300).is_empty()
    around = _ring(kdb.Box(-1000 - SEAL_E, -1000 - SEAL_E, 101000 + SEAL_E, 81000 + SEAL_E), SEAL_E)
    assert around.separation_check(outer, 1000).is_empty()
    assert not around.separation_check(outer, 1005).is_empty()


def test_rule_values_are_the_public_section_6_10_values():
    assert {key: RULES[key] for key in PUBLIC_SEAL_RULES} == PUBLIC_SEAL_RULES


# --- the generated ring ---------------------------------------------------

@pytest.mark.live_tool
@pytest.mark.parametrize("case", ["ok", "min", "min_w"])
def test_drawn_layer_set(generated, case):
    layout, top = generated["layouts"][case]
    assert _drawn(layout, top) == DRAWN


@pytest.mark.live_tool
def test_boundary_is_the_outline(generated):
    layout, top = generated["layouts"]["ok"]
    boundary = _region(layout, top, BOUNDARY)
    assert (boundary ^ kdb.Region(kdb.Box(0, 0, SIZE_X, SIZE_Y))).is_empty()
    everything = kdb.Region()
    for key in DRAWN:
        everything += _region(layout, top, key)
    assert everything.bbox() == kdb.Box(0, 0, SIZE_X, SIZE_Y)


@pytest.mark.live_tool
def test_edgeseal_is_30um_inside_the_outline(generated):
    layout, top = generated["layouts"]["ok"]
    d = BOUNDARY_TO_EDGESEAL
    assert _region(layout, top, EDGESEAL).bbox() == kdb.Box(d, d, SIZE_X - d, SIZE_Y - d)


@pytest.mark.live_tool
@pytest.mark.parametrize("case", ["ok", "min", "min_w"])
@pytest.mark.parametrize("key", sorted(DRAWN - {BOUNDARY}))
def test_each_ring_is_one_closed_loop(generated, case, key):
    layout, top = generated["layouts"][case]
    polygons = list(_region(layout, top, key).each())
    assert len(polygons) == 1
    assert polygons[0].holes() == 1


@pytest.mark.live_tool
@pytest.mark.parametrize("key", sorted(DRAWN))
def test_all_vertices_on_grid(generated, key):
    layout, top = generated["layouts"]["ok"]
    for polygon in _region(layout, top, key).each():
        for point in polygon.to_simple_polygon().each_point():
            assert point.x % STEP == 0 and point.y % STEP == 0, (key, point)


@pytest.mark.live_tool
@pytest.mark.parametrize("case", ["ok", "min"])
def test_rings_match_klayout_sizing_of_the_edgeseal_outline(generated, case):
    layout, top = generated["layouts"][case]
    edgeseal = _region(layout, top, EDGESEAL)
    hull = edgeseal.hulls()

    def band(inset, width):
        return hull.sized(-inset) - hull.sized(-(inset + width))

    assert (edgeseal ^ band(0, SEAL_A)).is_empty()
    for key in METALS:
        assert (_region(layout, top, key) ^ edgeseal).is_empty(), key
    for key, width in VIA_RINGS.items():
        assert (_region(layout, top, key) ^ band((SEAL_A - width) // 2, width)).is_empty(), key
    passiv = hull.sized(SEAL_F + SEAL_E) - hull.sized(SEAL_F)
    assert (_region(layout, top, PASSIV) ^ passiv).is_empty()


@pytest.mark.live_tool
def test_widths_are_exact(generated):
    layout, top = generated["layouts"]["ok"]
    assert _exact_width(_region(layout, top, EDGESEAL), SEAL_A)
    for key in METALS:
        assert _exact_width(_region(layout, top, key), SEAL_A), key
    for key, width in VIA_RINGS.items():
        assert _exact_width(_region(layout, top, key), width), key
    assert _exact_width(_region(layout, top, PASSIV), SEAL_E)


@pytest.mark.live_tool
def test_via_enclosure_and_passiv_space_are_exact(generated):
    layout, top = generated["layouts"]["ok"]
    metal = _region(layout, top, METALS[0])
    top_via2 = _region(layout, top, (133, 0))
    # TopVia2 fills Seal.a with exactly Seal.d on both sides.
    assert metal.enclosing_check(top_via2, 1300).is_empty()
    assert not metal.enclosing_check(top_via2, 1305).is_empty()
    passiv = _region(layout, top, PASSIV)
    edgeseal = _region(layout, top, EDGESEAL)
    assert passiv.separation_check(edgeseal, SEAL_F).is_empty()
    assert not passiv.separation_check(edgeseal, SEAL_F + STEP).is_empty()


@pytest.mark.live_tool
def test_corners_are_inscribed_in_the_seal_k_chamfer(generated):
    layout, top = generated["layouts"]["ok"]
    hull = list(_region(layout, top, EDGESEAL).hulls().each())[0]
    box = hull.bbox()
    x0, y0, x1, y1 = box.left, box.bottom, box.right, box.top
    on_line = 0
    for p in hull.each_point_hull():
        for dx, dy in ((p.x - x0, p.y - y0), (x1 - p.x, p.y - y0),
                       (x1 - p.x, y1 - p.y), (p.x - x0, y1 - p.y)):
            if dx <= SEAL_K and dy <= SEAL_K:
                assert dx + dy >= SEAL_K, f"vertex {p} reaches past the chamfer line"
                on_line += dx + dy == SEAL_K
    # Each corner has its 5 convex step vertices on the line, ends included.
    assert on_line == 4 * 5


@pytest.mark.live_tool
@pytest.mark.parametrize("name", GUARDED)
def test_guards_leave_an_empty_cell(generated, name):
    layout, top = generated["layouts"][name]
    assert _drawn(layout, top) == set()


@pytest.mark.live_tool
def test_guards_name_the_reason(generated):
    log = generated["log"]
    assert "sealring: l=1000.002u is not on the 5 nm grid" in log
    assert "below the 114.25 um minimum" in log
    assert "sealring: l must be a string such as '5000.000u', got '1000'" in log
    assert "sealring: w must be a string such as '5000.000u', got '800.000mu'" in log
    assert "lacks Seal_a" in log
    assert "sealring: Seal_e=4.202 is not a positive multiple of 5 nm" in log
    assert "sealring: Seal_c1 cannot be centred on the grid in Seal_a" in log


@pytest.mark.live_tool
def test_build_is_reproducible(generated):
    first, top1 = generated["layouts"]["ok"]
    second, top2 = generated["layouts"]["ok_again"]
    for key in DRAWN:
        assert (_region(first, top1, key) ^ _region(second, top2, key)).is_empty(), key


# --- the decks on a carrier -----------------------------------------------

def _carrier(generated, name, pad_opening_left=None):
    """The generated ring as written, plus prBoundary on the outline and
    optionally one wire-bond pad.

    The pad is square: TopMetal2 and dfpad 80 um, Passiv opening 2.1 um
    inside, its left edge at `pad_opening_left` nm from the outline.
    """
    layout = kdb.Layout()
    layout.read(str(generated["dir"] / "sealring_ok.gds"))
    cell = layout.top_cell()
    cell.shapes(layout.layer(*PRBOUNDARY)).insert(kdb.Box(0, 0, SIZE_X, SIZE_Y))
    if pad_opening_left is not None:
        enclosure = round(RULES["Pas_c"] * 1000)
        left = pad_opening_left - enclosure
        bottom = SIZE_Y // 2 - 40000
        pad = kdb.Box(left, bottom, left + 80000, bottom + 80000)
        cell.shapes(layout.layer(134, 0)).insert(pad)
        cell.shapes(layout.layer(*DFPAD)).insert(pad)
        cell.shapes(layout.layer(*PASSIV)).insert(pad.enlarged(-enclosure, -enclosure))
    path = generated["dir"] / f"carrier_{name}.gds"
    layout.write(str(path))
    return path


def _violations(path, deck, tmp_path, monkeypatch, defines=None):
    monkeypatch.setenv("KLAYOUT_HOME", str(tmp_path))
    monkeypatch.delenv("KLAYOUT_PATH", raising=False)
    if str(DRC_DIR) not in sys.path:
        sys.path.insert(0, str(DRC_DIR))
    from run_drc import run_deck, get_rules_with_violations
    report = run_deck(str(DRC_DIR / "intm4tm2.drc"), deck, str(path), "TOP",
                      tmp_path, threads=1, run_mode="flat", extra_defines=defines)
    return get_rules_with_violations(report)


# Pad.dR measures the pad opening to EdgeSeal; the inner EdgeSeal edge is
# 30 + Seal.a um from the outline.
PAD_DR_LIMIT = BOUNDARY_TO_EDGESEAL + SEAL_A + round(RULES["Pad_dR"] * 1000)


@pytest.mark.live_tool
def test_default_decks_clean_on_ring_and_pad_at_the_limit(generated, tmp_path, monkeypatch):
    path = _carrier(generated, "pad_at_limit", PAD_DR_LIMIT)
    assert _violations(path, "all", tmp_path, monkeypatch) == set()


@pytest.mark.live_tool
def test_ring_passes_the_presence_rules(generated, tmp_path, monkeypatch):
    # The regression cells use square corners; this is the staircase and the
    # PCell's own Passiv ring, with a pad inside.
    path = _carrier(generated, "presence", PAD_DR_LIMIT)
    assert _violations(path, "sealring", tmp_path, monkeypatch, {"require_sealring": "true"}) == set()


@pytest.mark.live_tool
def test_ring_makes_pad_dr_live(generated, tmp_path, monkeypatch):
    path = _carrier(generated, "pad_too_close", PAD_DR_LIMIT - STEP)
    assert _violations(path, "pad", tmp_path, monkeypatch) == {"Pad.dR"}


# --- Fill around the ring ---------------------------------------------------

FILLED = [50, 67, 126, 134]
# prBoundary on the outline is what the exporter writes; the inner one lies
# inside the ring hole. Between them they separate "prBoundary AND ring
# interior" from either region alone.
PRBOUNDARIES = {
    "outline": kdb.Box(0, 0, SIZE_X, SIZE_Y),
    "inner": kdb.Box(100000, 100000, SIZE_X - 100000, SIZE_Y - 100000),
}
REFUSALS = {
    "open": "EdgeSeal (39/0) has no interior",
    "slot": "outside the seal ring, within (40,50;100,100)",
    "prboundary": "prBoundary (235/0) does not overlap the seal-ring interior",
}


def _fill_closure():
    if str(PYTHON_DIR) not in sys.path:
        sys.path.insert(0, str(PYTHON_DIR))
    import fill_closure
    return fill_closure


def _fill_stack(design, output, workdir, monkeypatch):
    home = workdir / "klayout_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("KLAYOUT_HOME", str(home))
    monkeypatch.delenv("KLAYOUT_PATH", raising=False)
    _fill_closure().fill_stack(str(design), str(output), topcell="TOP", workdir=workdir)


@pytest.fixture(scope="module")
def filled(generated):
    paths = {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, prboundary in PRBOUNDARIES.items():
            layout = kdb.Layout()
            layout.read(str(generated["dir"] / "sealring_ok.gds"))
            layout.top_cell().shapes(layout.layer(*PRBOUNDARY)).insert(prboundary)
            design = generated["dir"] / f"fill_{name}.gds"
            layout.write(str(design))
            paths[name] = generated["dir"] / f"filled_{name}.gds"
            workdir = generated["dir"] / f"fill_{name}_run"
            workdir.mkdir()
            _fill_stack(design, paths[name], workdir, monkeypatch)
    return paths


@pytest.mark.live_tool
@pytest.mark.parametrize("layer", FILLED)
@pytest.mark.parametrize("name", PRBOUNDARIES)
def test_fill_stays_inside_prboundary_and_the_ring(filled, name, layer):
    layout = kdb.Layout()
    layout.read(str(filled[name]))
    top = layout.top_cell()
    fill = _region(layout, top, (layer, 22))
    allowed = _region(layout, top, PRBOUNDARY) & _region(layout, top, EDGESEAL).holes()
    assert not fill.is_empty()
    assert (fill - allowed).is_empty()


@pytest.mark.live_tool
def test_filled_ring_passes_the_seal_filler_and_density_decks(filled, tmp_path, monkeypatch):
    path = filled["outline"]
    assert _violations(path, "sealring", tmp_path, monkeypatch, {"require_sealring": "true"}) == set()
    for deck in ("metalnfiller", "topmetal1filler", "topmetal2filler", "density"):
        assert _violations(path, deck, tmp_path, monkeypatch) == set(), deck


def _seal_design(path, case, layers):
    """A 200 um EdgeSeal ring with a 50 um block of drawn metal on each of
    `layers` inside it and prBoundary on the outline, bent into one case."""
    layout = kdb.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    ring = kdb.Region(kdb.Box(0, 0, 200000, 200000)) - kdb.Region(kdb.Box(5000, 5000, 195000, 195000))
    prboundary = kdb.Box(0, 0, 200000, 200000)
    metal = kdb.Box(50000, 50000, 100000, 100000)
    if case == "open":            # a 20 um gap in the ring
        ring -= kdb.Region(kdb.Box(90000, 0, 110000, 5000))
    elif case == "slot":          # the only hole is a slot beside the design,
        ring = kdb.Region(kdb.Box(0, 0, 40000, 200000)) - kdb.Region(kdb.Box(15000, 20000, 25000, 180000))
        metal = kdb.Box(30000, 50000, 100000, 100000)  # which straddles the ring's edge
    elif case == "prboundary":    # prBoundary outside the ring
        prboundary = kdb.Box(300000, 0, 400000, 100000)
    elif case == "bare":          # a second EdgeSeal loop inside the ring
        ring += kdb.Region(kdb.Box(120000, 120000, 170000, 170000)) - kdb.Region(kdb.Box(130000, 130000, 160000, 160000))
    top.shapes(layout.layer(*EDGESEAL)).insert(ring)
    top.shapes(layout.layer(*PRBOUNDARY)).insert(prboundary)
    for layer in layers:
        top.shapes(layout.layer(layer, 0)).insert(metal)
    layout.write(str(path))


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_no_fill_on_a_bare_edgeseal_ring(tmp_path, monkeypatch):
    # No metal on either EdgeSeal loop, so no metal keep-out covers them: only
    # the region does. The inner loop lies inside the outer loop's hole.
    design = tmp_path / "bare.gds"
    _seal_design(design, "bare", FILLED)
    output = tmp_path / "filled.gds"
    _fill_stack(design, output, tmp_path, monkeypatch)
    layout = kdb.Layout()
    layout.read(str(output))
    top = layout.top_cell()
    for layer in FILLED:
        fill = _region(layout, top, (layer, 22))
        assert not fill.is_empty()
        assert (fill & _region(layout, top, EDGESEAL)).is_empty(), layer


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
@pytest.mark.parametrize("case", REFUSALS)
@pytest.mark.parametrize("layer", FILLED)
def test_fill_macro_refuses_a_ring_that_cannot_bound_the_fill(tmp_path, layer, case):
    # Metal on one layer at a time, so each macro must read both of its layers.
    fill_closure = _fill_closure()
    macro = fill_closure.MACRO if layer in fill_closure.METALS else fill_closure.TOPMETAL_MACRO
    design = tmp_path / f"{case}.gds"
    _seal_design(design, case, [layer])
    runner = tmp_path / "run_fill.drc"
    runner.write_text(f'source("{design}")\ntarget("{tmp_path / "fill.gds"}")\n'
                      + fill_closure._macro_body(macro))
    home = tmp_path / "klayout_home"
    home.mkdir()
    result = subprocess.run(
        [KLAYOUT_BIN, "-b", "-rd", f"tech_json={fill_closure.TECH_JSON}", "-r", str(runner)],
        capture_output=True, text=True, env=_clean_env(home), timeout=120)
    assert result.returncode != 0
    assert REFUSALS[case] in result.stdout + result.stderr


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
@pytest.mark.parametrize("layer", [50, 134])  # the first generator, and the second
def test_fill_stack_says_why_the_fill_stopped(tmp_path, monkeypatch, layer):
    design = tmp_path / "slot.gds"
    _seal_design(design, "slot", [layer])
    with pytest.raises(RuntimeError) as error:
        _fill_stack(design, tmp_path / "filled.gds", tmp_path, monkeypatch)
    assert REFUSALS["slot"] in str(error.value).splitlines()[0]
