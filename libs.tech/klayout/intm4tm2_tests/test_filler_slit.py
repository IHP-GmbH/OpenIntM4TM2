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
"""Regression for the stress-relief slit generator (tech/macros/interposer_filler_slit.lym).

Rule Slt.i (density.drc) demands at least 6% slit coverage on any metal plate bigger
than 35 x 35 um; rule Slt.c (7_3_metalslits.drc) flags any metal wider than 30 um that
carries no slit at all. Wide interposer power/ground planes trip both and today must be
slotted by hand. This generator cuts a fixed grid of small square slits into every wide
drawn plate, sized so the 7.3 geometry rules (Slt.a min width, Slt.b max edge, Slt.f
enclosure, Slt.h via spacing) hold by construction and the per-plate slit ratio clears
the 6% Slt.i floor.

The unit tests (macro only, fast) check the coverage floor, that sub-threshold plates are
left alone, the slit geometry, and the via keep-out. The two deck tests are the authority:
they run the interposer decks and assert a bare plate fails Slt.c while a generated one is
clean, and that a sparsely-slit plate fails Slt.i while a generated one is clean.
"""

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
SLIT_MACRO = REPO_KLAYOUT / "tech" / "macros" / "interposer_filler_slit.lym"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"
DRC_SCRIPT = DRC_DIR / "intm4tm2.drc"
TECH_JSON = DRC_DIR / "rule_decks" / "interposer_tech_default.json"

KLAYOUT_BIN = shutil.which("klayout")

DBU = 0.001
HALF_W = 17500                       # Slt_i_w/2 in dbu: the opening the deck uses for Slt.i
SLT_I = 0.06                         # min slit area ratio on a wide plate
# GDS metal layer -> (slit datatype always 24, adjacent via "layer/dt", via clearance um).
METALS = {50: "M4", 67: "M5", 126: "TM1", 134: "TM2"}


def _macro_body(path: Path) -> str:
    return ET.parse(path).getroot().find("text").text


def _run_slit(design: Path, out: Path, workdir: Path, defines=None) -> None:
    """Run the slit macro headless: source = design, target = out (new slits only)."""
    runner = workdir / "run_slit.drc"
    runner.write_text(f'source("{design}")\ntarget("{out}")\n' + _macro_body(SLIT_MACRO))
    cmd = [KLAYOUT_BIN, "-b", "-rd", f"tech_json={TECH_JSON}"]
    for key, value in (defines or {}).items():
        cmd += ["-rd", f"{key}={value}"]
    cmd += ["-r", str(runner)]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _merge(design: Path, extra: Path, combined: Path) -> None:
    """Union `extra`'s shapes into `design` layer by layer (the interactive result)."""
    base = kdb.Layout()
    base.read(str(design))
    add = kdb.Layout()
    add.read(str(extra))
    base_top = base.top_cell()
    add_top = add.top_cell()
    for li in add.layer_indexes():
        info = add.get_info(li)
        target = base.layer(info.layer, info.datatype)
        base_top.shapes(target).insert(kdb.Region(add_top.begin_shapes_rec(li)))
    base.write(str(combined))


def _slit_and_metal(gds: Path, metal_layer: int):
    """(slit Region, drawn-metal Region), both flat and self-owned so they outlive the layout."""
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()

    def reg(layer, dt):
        li = ly.find_layer(layer, dt)
        return kdb.Region() if li is None else kdb.Region(top.begin_shapes_rec(li))

    slit = reg(metal_layer, 24).merged()
    metal = reg(metal_layer, 0).merged()
    slit.flatten()
    metal.flatten()
    return slit, metal


def _worst_plate_coverage(gds: Path, metal_layer: int):
    """Min slit-area ratio over the wide plates (the deck opening), or None if no plate.

    Mirrors the Slt.i construct: each plate is the drawn metal opened by Slt_i_w/2, and
    the ratio is the slit area inside the plate over the plate area. The worst (min) plate
    is what the antenna_check would flag, so that is what must clear the 6% floor.
    """
    slit, metal = _slit_and_metal(gds, metal_layer)
    worst = None
    for poly in metal.each():
        plate = kdb.Region(poly).sized(-HALF_W).sized(HALF_W)
        if plate.is_empty():
            continue
        cov = (slit & plate).area() / plate.area()
        worst = cov if worst is None else min(worst, cov)
    return worst


def _slit_layer_empty(gds: Path, metal_layer: int) -> bool:
    slit, _ = _slit_and_metal(gds, metal_layer)
    return slit.is_empty()


def _geometry_stats(gds: Path, metal_layer: int):
    """(min slit width um, max slit edge um, slit-area outside slt_f-eroded metal um^2)."""
    slit, metal = _slit_and_metal(gds, metal_layer)
    min_w = None
    for poly in slit.each():
        b = poly.bbox()
        w = min(b.width(), b.height()) * DBU
        min_w = w if min_w is None else min(min_w, w)
    max_edge = max((e.length() for e in slit.edges().each()), default=0) * DBU
    # Slt.f: every slit must sit at least 1.0 um (Slt_f) inside the drawn metal; the area
    # of slit falling outside the 1.0 um-eroded metal must be zero.
    outside = (slit - metal.sized(-1000)).area() * DBU * DBU
    return min_w, max_edge, outside


def _overlap_area(gds: Path, layer: int, dt: int, other: kdb.Region) -> float:
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.find_layer(layer, dt)
    if li is None:
        return 0.0
    return (kdb.Region(top.begin_shapes_rec(li)) & other).area() * DBU * DBU


def _deck_slt_violations(gds: Path, deck: str, workdir: Path):
    """Rule ids starting with 'Slt' the given interposer deck reports on `gds`."""
    if str(DRC_DIR) not in sys.path:
        sys.path.insert(0, str(DRC_DIR))
    from run_drc import get_rules_with_violations, run_deck

    run_dir = workdir / f"drc_{deck}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report = run_deck(str(DRC_SCRIPT), deck, str(gds), "INTERPOSER", run_dir,
                      threads=2, run_mode="flat")
    return sorted(r for r in get_rules_with_violations(report) if r.startswith("Slt"))


def _design_with_plate(path: Path, metal_layer: int, plate=(20, 20, 170, 170),
                       prb=(0, 0, 200, 200)) -> None:
    """prBoundary (235/0) plus one drawn plate on `metal_layer`."""
    ly = kdb.Layout()
    ly.dbu = DBU
    top = ly.create_cell("INTERPOSER")
    top.shapes(ly.layer(235, 0)).insert(kdb.DBox(*prb))
    top.shapes(ly.layer(metal_layer, 0)).insert(kdb.DBox(*plate))
    ly.write(str(path))


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
@pytest.mark.parametrize("metal_layer", list(METALS), ids=list(METALS.values()))
def test_wide_plate_slit_coverage_meets_floor(metal_layer, tmp_path):
    design = tmp_path / "design.gds"
    slits = tmp_path / "slits.gds"
    combined = tmp_path / "combined.gds"
    _design_with_plate(design, metal_layer)
    _run_slit(design, slits, tmp_path)
    _merge(design, slits, combined)

    assert not _slit_layer_empty(combined, metal_layer), "no slits emitted on a wide plate"
    worst = _worst_plate_coverage(combined, metal_layer)
    assert worst is not None and worst >= SLT_I, \
        f"{METALS[metal_layer]} worst plate slit ratio {worst} below the {SLT_I} floor"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_subthreshold_plate_gets_no_slits(tmp_path):
    """Metal no wider than Slt_c (30 um) needs no slit (neither Slt.c nor Slt.i), so it is left alone."""
    design = tmp_path / "design.gds"
    slits = tmp_path / "slits.gds"
    _design_with_plate(design, 50, plate=(20, 20, 48, 48))   # 28 x 28, below Slt_c
    _run_slit(design, slits, tmp_path)
    assert _slit_layer_empty(slits, 50), "slit generator slit a sub-30 um plate"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_near_threshold_plate_meets_floor(tmp_path):
    """The smallest Slt.i plate (just over 35 x 35 um) still clears the 6% floor.

    Pins the opening factor and the near-threshold coverage margin: the coverage on a
    36 x 36 plate is where the opening rounds hardest, so it is the worst Slt.i case.
    """
    design = tmp_path / "design.gds"
    slits = tmp_path / "slits.gds"
    combined = tmp_path / "combined.gds"
    _design_with_plate(design, 50, plate=(20, 20, 56, 56))   # 36 x 36, just over Slt_i_w
    _run_slit(design, slits, tmp_path)
    _merge(design, slits, combined)
    worst = _worst_plate_coverage(combined, 50)
    assert worst is not None and worst >= SLT_I, \
        f"36 um plate slit ratio {worst} below the {SLT_I} floor"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_slit_generator_covers_the_slt_c_band(tmp_path):
    """Metal between Slt_c (30) and Slt_i_w (35) um wide is slit, so Slt.c clears.

    A 31 x 200 um bus is wider than 30 um (fails Slt.c) but never forms a 35 x 35 plate
    (so Slt.i never applies). The generator must still slit it; a threshold keyed only on
    Slt_i_w would leave it Slt.c-dirty.
    """
    bus = tmp_path / "bus.gds"
    _design_with_plate(bus, 50, plate=(20, 20, 51, 220), prb=(0, 0, 250, 250))   # 31 x 200
    bare_slt = _deck_slt_violations(bus, "metalslits", tmp_path / "bare")
    assert "Slt.c_M4" in bare_slt, f"a 31 um bus should fail Slt.c bare, got {bare_slt}"

    slits = tmp_path / "slits.gds"
    combined = tmp_path / "combined.gds"
    _run_slit(bus, slits, tmp_path)
    _merge(bus, slits, combined)
    good_slt = _deck_slt_violations(combined, "metalslits", tmp_path / "good")
    assert good_slt == [], f"the slit generator should clear Slt.c on the bus, got {good_slt}"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_generated_slits_satisfy_geometry(tmp_path):
    """Every slit is at least Slt.a wide, at most Slt.b per edge, Slt.f inside the metal."""
    design = tmp_path / "design.gds"
    slits = tmp_path / "slits.gds"
    combined = tmp_path / "combined.gds"
    _design_with_plate(design, 50)
    _run_slit(design, slits, tmp_path)
    _merge(design, slits, combined)

    min_w, max_edge, outside = _geometry_stats(combined, 50)
    assert min_w is not None and min_w >= 2.8, f"slit narrower than Slt.a: {min_w} um"
    assert max_edge <= 20.0, f"slit edge longer than Slt.b: {max_edge} um"
    assert outside == 0, f"slit closer than Slt.f (1.0 um) to a metal edge: {outside} um^2 outside"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_slits_keep_clear_of_vias(tmp_path):
    """Slt.h2: Metal4 slits stay at least 0.3 um from Via4."""
    design = tmp_path / "design.gds"
    ly = kdb.Layout()
    ly.dbu = DBU
    top = ly.create_cell("INTERPOSER")
    top.shapes(ly.layer(235, 0)).insert(kdb.DBox(0, 0, 200, 200))
    top.shapes(ly.layer(50, 0)).insert(kdb.DBox(20, 20, 170, 170))
    top.shapes(ly.layer(66, 0)).insert(kdb.DBox(90, 90, 100, 100))   # a Via4 under the plate
    ly.write(str(design))

    slits = tmp_path / "slits.gds"
    _run_slit(design, slits, tmp_path)

    via_halo = kdb.Region(kdb.Box(90000, 90000, 100000, 100000)).sized(300)   # Via4 grown by Slt.h2
    assert _overlap_area(slits, 50, 24, via_halo) == 0, "a Metal4 slit intruded into the Via4 keep-out"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_existing_slit_not_overlapped(tmp_path):
    """The generator subtracts pre-existing slits, so it never stacks a new cell on one."""
    design = tmp_path / "design.gds"
    ly = kdb.Layout()
    ly.dbu = DBU
    top = ly.create_cell("INTERPOSER")
    top.shapes(ly.layer(235, 0)).insert(kdb.DBox(0, 0, 200, 200))
    top.shapes(ly.layer(50, 0)).insert(kdb.DBox(20, 20, 170, 170))
    top.shapes(ly.layer(50, 24)).insert(kdb.DBox(95, 95, 99, 99))
    ly.write(str(design))

    slits = tmp_path / "slits.gds"
    _run_slit(design, slits, tmp_path)

    # The macro emits new slits only; the generator's own output must not land on the
    # designer's pre-existing slit (this fails if line "region - source.input(<ml>/24)"
    # is removed, since the raster would otherwise place a cell there).
    kept = kdb.Region(kdb.Box(95000, 95000, 99000, 99000))
    assert _overlap_area(slits, 50, 24, kept) == 0, "generator stacked a new slit on an existing one"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_slits_keep_off_pillar_and_sbump_pads(tmp_path):
    """Slt.e: no slit is cut into a Cu-pillar (41/35) or solder-bump (41/36) landing pad.

    The pad markers carry the pillar/sbump purposes, not the drawn dfpad purpose, so a
    keep-out keyed on 41/0 alone would slit straight through a landing pad.
    """
    design = tmp_path / "design.gds"
    ly = kdb.Layout()
    ly.dbu = DBU
    top = ly.create_cell("INTERPOSER")
    top.shapes(ly.layer(235, 0)).insert(kdb.DBox(0, 0, 250, 250))
    top.shapes(ly.layer(50, 0)).insert(kdb.DBox(20, 20, 170, 170))
    top.shapes(ly.layer(41, 35)).insert(kdb.DBox(40, 40, 90, 90))     # Cu-pillar pad
    top.shapes(ly.layer(41, 36)).insert(kdb.DBox(110, 110, 160, 160)) # solder-bump pad
    ly.write(str(design))

    slits = tmp_path / "slits.gds"
    _run_slit(design, slits, tmp_path)

    for pad in (kdb.Region(kdb.Box(40000, 40000, 90000, 90000)),
                kdb.Region(kdb.Box(110000, 110000, 160000, 160000))):
        assert _overlap_area(slits, 50, 24, pad) == 0, "a slit was cut into a landing pad"
    assert not _slit_layer_empty(slits, 50), "no slits emitted at all (plate should still be slit)"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_generated_slits_clear_metalslits_deck_bare_plate_fails_sltc(tmp_path):
    """Authority: a bare wide plate fails Slt.c; the generated slits clear the whole 7.3 table."""
    bare = tmp_path / "bare.gds"
    _design_with_plate(bare, 50)
    bare_slt = _deck_slt_violations(bare, "metalslits", tmp_path / "bare")
    assert "Slt.c_M4" in bare_slt, f"a bare wide plate should fail Slt.c, got {bare_slt}"

    slits = tmp_path / "slits.gds"
    combined = tmp_path / "combined.gds"
    _run_slit(bare, slits, tmp_path)
    _merge(bare, slits, combined)
    good_slt = _deck_slt_violations(combined, "metalslits", tmp_path / "good")
    assert good_slt == [], f"generated slits should clear 7.3, got {good_slt}"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_generated_slits_satisfy_slti_sparse_plate_fails(tmp_path):
    """Authority: a sparsely-slit plate fails Slt.i; the generated slits clear it."""
    sparse = tmp_path / "sparse.gds"
    ly = kdb.Layout()
    ly.dbu = DBU
    top = ly.create_cell("INTERPOSER")
    top.shapes(ly.layer(235, 0)).insert(kdb.DBox(0, 0, 200, 200))
    top.shapes(ly.layer(50, 0)).insert(kdb.DBox(20, 20, 170, 170))
    top.shapes(ly.layer(50, 24)).insert(kdb.DBox(90, 90, 93, 93))   # one tiny slit, far under 6%
    ly.write(str(sparse))
    sparse_slt = _deck_slt_violations(sparse, "density", tmp_path / "sparse")
    assert "Slt.i_M4" in sparse_slt, f"an under-slit plate should fail Slt.i, got {sparse_slt}"

    design = tmp_path / "design.gds"
    slits = tmp_path / "slits.gds"
    combined = tmp_path / "combined.gds"
    _design_with_plate(design, 50)
    _run_slit(design, slits, tmp_path)
    _merge(design, slits, combined)
    good_slt = _deck_slt_violations(combined, "density", tmp_path / "good")
    assert good_slt == [], f"generated slits should satisfy Slt.i, got {good_slt}"
