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
"""Regression for the designer no-fill emitter (tech/macros/interposer_nofill.lym).

The fill generators keep dummy fill only a DRC clearance (MFil.c 0.42 um, TM(n)Fil.c
3.0 um) off drawn metal, which is not enough around pillar pads, RF coils, matched
devices or probe areas. This emitter lets a designer mark such a region on
NoMetFiller (160/0) and stamp it, grown by a chosen clearance, onto the per-metal
nofill datatypes (x/23) that both generators already honor as absolute keep-outs.

The tests check the emitter as a unit (the emitted x/23 is exactly the marker grown
by the clearance, on the metals selected) and end to end (after the emitted nofill
is merged in, the Metal4/Metal5 generator leaves the marked region and its halo
fill-free).
"""

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
NOFILL_MACRO = REPO_KLAYOUT / "tech" / "macros" / "interposer_nofill.lym"
FILL_METAL_MACRO = REPO_KLAYOUT / "tech" / "macros" / "interposer_filler_metal.lym"
TECH_JSON = REPO_KLAYOUT / "tech" / "drc" / "rule_decks" / "interposer_tech_default.json"

KLAYOUT_BIN = shutil.which("klayout")

# The marked region (a 160/0 box) and the metals the emitter can target.
MARKER = (40.0, 40.0, 60.0, 60.0)
ALL_METALS = (50, 67, 126, 134)


def _macro_body(path: Path) -> str:
    return ET.parse(path).getroot().find("text").text


def _run_macro(macro: Path, design: Path, out: Path, workdir: Path, defines=None) -> None:
    """Run a DRC-DSL macro headless: source = design, target = out, plus -rd defines."""
    runner = workdir / f"run_{macro.stem}.drc"
    runner.write_text(f'source("{design}")\ntarget("{out}")\n' + _macro_body(macro))
    cmd = [KLAYOUT_BIN, "-b"]
    for key, value in (defines or {}).items():
        cmd += ["-rd", f"{key}={value}"]
    cmd += ["-r", str(runner)]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _merge(design: Path, extra: Path, combined: Path) -> None:
    """Union `extra`'s shapes into `design`, layer by layer (the interactive result)."""
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


def _matches(gds: Path, layer: int, dt: int, expected: kdb.Region) -> bool:
    """Whether one layer of `gds` is non-empty and exactly equals `expected`.

    The layer's region is derived from a recursive iterator (it borrows the layout),
    so the comparison is done here while the layout is alive; only the boolean leaves.
    `expected` is a flat, self-owned region and is safe to pass in.
    """
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.find_layer(layer, dt)
    got = kdb.Region() if li is None else kdb.Region(top.begin_shapes_rec(li))
    return (not got.is_empty()) and (got ^ expected).is_empty()


def _layer_empty(gds: Path, layer: int, dt: int) -> bool:
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.find_layer(layer, dt)
    if li is None:
        return True
    return kdb.Region(top.begin_shapes_rec(li)).is_empty()


def _overlap_area(gds: Path, layer: int, dt: int, other: kdb.Region) -> float:
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.find_layer(layer, dt)
    if li is None:
        return 0.0
    got = kdb.Region(top.begin_shapes_rec(li))
    return (got & other).area() * ly.dbu * ly.dbu


def _marker_grown(clearance_um: float, dbu: float = 0.001) -> kdb.Region:
    x0, y0, x1, y1 = MARKER
    box = kdb.Region(kdb.Box(int(x0 / dbu), int(y0 / dbu), int(x1 / dbu), int(y1 / dbu)))
    return box if clearance_um == 0 else box.sized(int(round(clearance_um / dbu)))


def _build_marker_design(path: Path) -> None:
    """A design carrying just the 160/0 no-fill marker (top cell 'INTERPOSER')."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("INTERPOSER")
    top.shapes(ly.layer(160, 0)).insert(kdb.DBox(*MARKER))
    ly.write(str(path))


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
@pytest.mark.parametrize("clearance", [0.0, 5.0], ids=["c0", "c5"])
def test_emitted_nofill_is_marker_grown_by_clearance(clearance, tmp_path):
    design = tmp_path / "marker.gds"
    out = tmp_path / "nofill.gds"
    _build_marker_design(design)
    _run_macro(NOFILL_MACRO, design, out, tmp_path, {"clearance": f"{clearance}"})

    expected = _marker_grown(clearance)
    for ml in ALL_METALS:
        assert _matches(out, ml, 23, expected), \
            f"{ml}/23 nofill is not the marker grown by {clearance} um"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_metal_selection_limits_the_emitted_layers(tmp_path):
    design = tmp_path / "marker.gds"
    out = tmp_path / "nofill.gds"
    _build_marker_design(design)
    _run_macro(NOFILL_MACRO, design, out, tmp_path, {"clearance": "5.0", "metals": "50,67"})

    expected = _marker_grown(5.0)
    for ml in (50, 67):
        assert _matches(out, ml, 23, expected), f"expected nofill on {ml}/23"
    for ml in (126, 134):
        assert _layer_empty(out, ml, 23), f"unexpected nofill on {ml}/23"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_existing_nofill_is_preserved_not_overwritten(tmp_path):
    """The emitter unions onto existing per-metal nofill instead of replacing it.

    Run from the menu (no target), KLayout's DRC engine writes the first output to a
    layer by swapping in a fresh temp layer, dropping the layer's prior content; the
    emitter guards against that by re-emitting source.input("<metal>/23"). A disjoint
    box is seeded on 50/23 in the input; the emitted 50/23 must be that box unioned
    with the halo, so the designer's hand-drawn keep-out is not silently wiped.
    """
    design = tmp_path / "design.gds"
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("INTERPOSER")
    top.shapes(ly.layer(160, 0)).insert(kdb.DBox(*MARKER))
    existing = (10.0, 10.0, 20.0, 20.0)               # disjoint from the marker and its 5 um halo
    top.shapes(ly.layer(50, 23)).insert(kdb.DBox(*existing))
    ly.write(str(design))

    out = tmp_path / "nofill.gds"
    _run_macro(NOFILL_MACRO, design, out, tmp_path, {"clearance": "5.0", "metals": "50"})

    dbu = 0.001
    x0, y0, x1, y1 = existing
    box = kdb.Region(kdb.Box(int(x0 / dbu), int(y0 / dbu), int(x1 / dbu), int(y1 / dbu)))
    assert _matches(out, 50, 23, box | _marker_grown(5.0)), \
        "emitter overwrote the designer's pre-existing 50/23 nofill instead of unioning it"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_metal_fill_generator_honors_emitted_nofill(tmp_path):
    """End to end: fill stays out of the marked region plus its halo.

    A design with a seal interior and open Metal4/Metal5 fields gets a 160/0 marker;
    the emitter turns it into a 5 um-clearance nofill on M4/M5; the fill generator is
    then run on the merged design and its fill must not intersect the marker halo.
    """
    design = tmp_path / "design.gds"
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("INTERPOSER")
    # EdgeSeal ring so the generator has a fill region, plus a central nofill marker.
    ring = kdb.Region(kdb.Box(0, 0, 120000, 120000)) - kdb.Region(kdb.Box(3000, 3000, 117000, 117000))
    top.shapes(ly.layer(39, 0)).insert(ring)
    top.shapes(ly.layer(160, 0)).insert(kdb.DBox(*MARKER))
    ly.write(str(design))

    # 1. emit the nofill halo on Metal4/Metal5 and merge it into the design.
    nofill = tmp_path / "nofill.gds"
    _run_macro(NOFILL_MACRO, design, nofill, tmp_path, {"clearance": "5.0", "metals": "50,67"})
    with_nofill = tmp_path / "with_nofill.gds"
    _merge(design, nofill, with_nofill)

    # 2. run the fill generator on the design that now carries the nofill, and merge.
    fill = tmp_path / "fill.gds"
    _run_macro(FILL_METAL_MACRO, with_nofill, fill, tmp_path, {"tech_json": f"{TECH_JSON}"})
    combined = tmp_path / "combined.gds"
    _merge(with_nofill, fill, combined)

    halo = _marker_grown(5.0)
    for ml in (50, 67):
        assert _overlap_area(combined, ml, 22, halo) == 0, \
            f"generated fill on {ml}/22 intruded into the {ml}/23 nofill halo"
