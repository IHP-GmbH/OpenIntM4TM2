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
"""Regression for the Metal4/Metal5 fill generator (tech/macros/interposer_filler_metal.lym).

The interposer density deck enforces a minimum on Metal4/Metal5 (M4.j/M5.j 35%
global, MnFil.h 25% per 800x800 um window) but nothing in the open flow could
create that fill until this generator. This test proves the generator is a real,
DRC-clean artifact: it builds a stress design (a solid block, a field of thin
sparse lines the large fill cannot enter, a designer nofill box, and a legal
pre-existing filler shape), runs the macro headlessly, merges the fill back into
the design the way the interactive flow does, and then signs it off with the
interposer's own decks.

The macro is a KLayout DRC-DSL macro. `target()` writes only the generated fill,
so the fill-only output is merged back into the design before DRC; that combined
layout is exactly what a designer gets after running the macro on an open layout.

Two region paths are exercised: a design bounded only by an EdgeSeal ring
(fill region = seal interior) and one that also carries a prBoundary
(fill region = prBoundary), matching the generator's prBoundary > seal > extent
fallback.

Asserts, per path: no Metal4/Metal5 density violation, no filler-geometry
violation (MFil.c / MFil.a2, which also covers merge with the pre-existing
filler), and an achieved drawn+fill density inside the [35, 60] % band.
"""

import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
MACRO = REPO_KLAYOUT / "tech" / "macros" / "interposer_filler_metal.lym"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"
DRC_SCRIPT = DRC_DIR / "intm4tm2.drc"
TECH_JSON = DRC_DIR / "rule_decks" / "interposer_tech_default.json"

KLAYOUT_BIN = shutil.which("klayout")

# density.drc Metal4/Metal5 global band (interposer_tech_default.json Mn_j / Mn_k).
DENSITY_MIN = 35.0
DENSITY_MAX = 60.0


def _macro_body() -> str:
    """The DRC-DSL body of the .lym macro (KLayout un-escapes the XML entities)."""
    return ET.parse(MACRO).getroot().find("text").text


def _build_design(path: Path, with_prboundary: bool) -> None:
    """A deliberately fill-hostile Metal4/Metal5 design inside a 200x200 um seal."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")

    def box(layer, dt, x0, y0, x1, y1):
        top.shapes(ly.layer(layer, dt)).insert(kdb.DBox(x0, y0, x1, y1))

    # EdgeSeal ring (39/0): outer 0..200, inner 5..195.
    ring = kdb.Region(kdb.Box(0, 0, 200000, 200000)) - kdb.Region(kdb.Box(5000, 5000, 195000, 195000))
    top.shapes(ly.layer(39, 0)).insert(ring)
    if with_prboundary:
        box(235, 0, 5, 5, 195, 195)

    # Metal4: a solid block, a field of 2 um lines on 6 um pitch (gaps too tight
    # for the 2 um large fill cell, so only the 1 um pass can enter them), a legal
    # 2x2 pre-existing filler (the merge-fix probe), and a designer nofill box.
    box(50, 0, 20, 20, 60, 60)
    x = 70
    while x < 150:
        box(50, 0, x, 20, x + 2, 120)
        x += 6
    box(50, 22, 160, 20, 162, 22)
    box(50, 23, 150, 150, 180, 180)

    # Metal5: a block plus a line field, so both metals get non-trivial coverage.
    box(67, 0, 20, 140, 80, 180)
    y = 60
    while y < 130:
        box(67, 0, 100, y, 180, y + 2)
        y += 6

    ly.write(str(path))


def _run_macro(design: Path, fill_only: Path, workdir: Path, tech_json: Path = TECH_JSON) -> None:
    """Run the fill macro headless: source = design, target = fill-only output.

    tech_json is handed to the macro via -rd so it reads its MFil_c clearance from
    that file (the deterministic path used by the flow), instead of the menu-time
    fallback that resolves the JSON relative to the macro.
    """
    runner = workdir / "run_fill.drc"
    runner.write_text(f'source("{design}")\ntarget("{fill_only}")\n' + _macro_body())
    subprocess.run(
        [KLAYOUT_BIN, "-b", "-rd", f"tech_json={tech_json}", "-r", str(runner)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _merge(design: Path, fill_only: Path, combined: Path) -> None:
    """Flatten the generated fill back onto the design (= the interactive result)."""
    base = kdb.Layout()
    base.read(str(design))
    fill = kdb.Layout()
    fill.read(str(fill_only))
    base_top = base.top_cell()
    fill_top = fill.top_cell()
    for li in fill.layer_indexes():
        info = fill.get_info(li)
        target = base.layer(info.layer, info.datatype)
        base_top.shapes(target).insert(kdb.Region(fill_top.begin_shapes_rec(li)))
    base.write(str(combined))


def _density(gds: Path, metal_layer: int) -> float:
    """Drawn+filler-minus-slit coverage over the chip area, the way density.drc measures it."""
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    dbu = ly.dbu

    def reg(layer, dt):
        li = ly.find_layer(layer, dt)
        return kdb.Region() if li is None else kdb.Region(top.begin_shapes_rec(li))

    prb = reg(235, 0)
    if not prb.is_empty():
        chip = prb.area() * dbu * dbu
    else:
        bb = top.bbox()
        chip = bb.width() * bb.height() * dbu * dbu
    covered = (reg(metal_layer, 0) + reg(metal_layer, 22) - reg(metal_layer, 24)).area() * dbu * dbu
    return 100.0 * covered / chip


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
@pytest.mark.parametrize("with_prboundary", [False, True], ids=["seal", "prboundary"])
def test_metal_fill_is_drc_clean_and_in_band(with_prboundary, tmp_path):
    design = tmp_path / "design.gds"
    fill_only = tmp_path / "fill_only.gds"
    combined = tmp_path / "combined.gds"

    _build_design(design, with_prboundary)
    _run_macro(design, fill_only, tmp_path)
    _merge(design, fill_only, combined)

    sys.path.insert(0, str(DRC_DIR))
    from run_drc import run_deck, get_rules_with_violations

    run_dir = tmp_path / "drc_run"
    run_dir.mkdir()

    def violated(deck):
        report = run_deck(str(DRC_SCRIPT), deck, str(combined), "TOP",
                          run_dir, threads=2, run_mode="flat")
        return get_rules_with_violations(report)

    # Only Metal4/Metal5 rules are the generator's responsibility; TopMetal
    # density (TM1.c/TM2.c) fires because the stress design carries no top metal.
    density_m = {r for r in violated("density") if r.startswith(("M4", "M5"))}
    assert not density_m, f"fill left Metal4/Metal5 density violations: {sorted(density_m)}"

    filler = violated("metalnfiller")
    assert not filler, f"fill tripped filler-geometry DRC (incl. merge with pre-existing fill): {sorted(filler)}"

    for metal_layer, name in ((50, "Metal4"), (67, "Metal5")):
        d = _density(combined, metal_layer)
        assert DENSITY_MIN <= d <= DENSITY_MAX, \
            f"{name} drawn+fill density {d:.1f}% outside [{DENSITY_MIN}, {DENSITY_MAX}] %"


def _tech_json_with_mfil_c(base_json: Path, value: float, out: Path) -> Path:
    """A copy of the tech JSON with only MFil_c overridden."""
    data = json.loads(base_json.read_text())
    data["rules"]["MFil_c"] = value
    out.write_text(json.dumps(data))
    return out


def _fill_area_and_keepout(design_gds: Path, fill_gds: Path, layer: int, clearance: float):
    """(generated-filler area um2, keep-out honored) for one metal layer.

    All region work stays inside this function: a Region built from a recursive
    shape iterator borrows its source Layout, so returning the Region past the
    Layout's local scope would hand back an empty one. Both layouts are written at
    the same dbu, so metal.sized(...) can be intersected with the fill directly.
    """
    dly = kdb.Layout()
    dly.read(str(design_gds))
    dtop = dly.top_cell()
    dli = dly.find_layer(layer, 0)
    metal = kdb.Region() if dli is None else kdb.Region(dtop.begin_shapes_rec(dli))
    fly = kdb.Layout()
    fly.read(str(fill_gds))
    ftop = fly.top_cell()
    fli = fly.find_layer(layer, 22)
    filler = kdb.Region() if fli is None else kdb.Region(ftop.begin_shapes_rec(fli))
    dbu = dly.dbu
    area = filler.area() * dbu * dbu
    halo = int(round((clearance - 0.05) / dbu))
    honored = (metal.sized(halo) & filler).is_empty()
    return area, honored


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_generator_reads_mfil_c_from_tech_json(tmp_path):
    """The MFil.c keep-out comes from the tech JSON, not a hard-coded literal.

    The same design is filled against two JSONs that differ only in MFil_c. The
    larger keep-out must both push every generated cell farther than that distance
    from drawn metal and leave strictly less fill in place. A generator that
    ignored the JSON would emit byte-identical output for both, so equal areas
    (or fill within the bumped keep-out) means the value is not being read.
    """
    design = tmp_path / "design.gds"
    _build_design(design, with_prboundary=False)

    def run(mfil_c, tag):
        tj = _tech_json_with_mfil_c(TECH_JSON, mfil_c, tmp_path / f"tech_{tag}.json")
        fill = tmp_path / f"fill_{tag}.gds"
        _run_macro(design, fill, tmp_path, tech_json=tj)
        return _fill_area_and_keepout(design, fill, 50, mfil_c)

    area_small, ok_small = run(0.42, "small")
    area_large, ok_large = run(3.0, "large")

    assert area_small > 0 and area_large > 0, "generator produced no Metal4 fill"
    assert ok_small, "fill landed closer than MFil_c=0.42 to drawn metal"
    assert ok_large, "fill ignored the bumped MFil_c=3.0 keep-out from the tech JSON"
    assert area_large < area_small, \
        f"bumping MFil_c left the fill unchanged ({area_large:.1f} vs {area_small:.1f} um2): tech JSON not read"
