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
"""Regression for the TopMetal1/TopMetal2 fill generator (tech/macros/interposer_filler_topmetal.lym).

Guards the keep-out fix: the earlier macro subtracted the raw, unsized TopMetal
filler layer, so a rerun (or an imported block already carrying fill) placed new
fill closer than the 3.0 um filler-to-filler minimum. The design here seeds a
legal pre-existing filler bar; after the macro runs and the fill is merged back
in, the now-checked TM(n)Fil.b rule (5_23 / 5_26) must stay clean, and the top
metals must land inside their 25..70 % density band.

Two region paths are exercised (EdgeSeal-only and prBoundary), matching the
generator's prBoundary > seal > extent fallback.
"""

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
MACRO = REPO_KLAYOUT / "tech" / "macros" / "interposer_filler_topmetal.lym"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"
DRC_SCRIPT = DRC_DIR / "intm4tm2.drc"

KLAYOUT_BIN = shutil.which("klayout")

# density.drc TopMetal band (interposer_tech_default.json TM1_c/TM1_d, TM2_c/TM2_d).
DENSITY_MIN = 25.0
DENSITY_MAX = 70.0


def _macro_body():
    return ET.parse(MACRO).getroot().find("text").text


def _build_design(path, with_prboundary):
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")

    def box(layer, dt, x0, y0, x1, y1):
        top.shapes(ly.layer(layer, dt)).insert(kdb.DBox(x0, y0, x1, y1))

    ring = kdb.Region(kdb.Box(0, 0, 200000, 200000)) - kdb.Region(kdb.Box(5000, 5000, 195000, 195000))
    top.shapes(ly.layer(39, 0)).insert(ring)
    if with_prboundary:
        box(235, 0, 5, 5, 195, 195)

    # TopMetal1: a block, a legal 5x10 pre-existing filler (the merge-fix probe),
    # and a designer nofill box.
    box(126, 0, 20, 20, 70, 70)
    box(126, 22, 160, 20, 165, 30)
    box(126, 23, 150, 150, 185, 185)

    # TopMetal2: a block.
    box(134, 0, 20, 120, 90, 175)

    ly.write(str(path))


def _run_macro(design, fill_only, workdir):
    runner = workdir / "run_fill.drc"
    runner.write_text(f'source("{design}")\ntarget("{fill_only}")\n' + _macro_body())
    subprocess.run([KLAYOUT_BIN, "-b", "-r", str(runner)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _merge(design, fill_only, combined):
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


def _density(gds, metal_layer):
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
def test_topmetal_fill_is_drc_clean_and_in_band(with_prboundary, tmp_path):
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

    # filler geometry, including TM(n)Fil.b against the seeded pre-existing filler.
    for deck in ("topmetal1filler", "topmetal2filler"):
        filler = violated(deck)
        assert not filler, f"{deck} fired on generated fill: {sorted(filler)}"

    density_tm = {r for r in violated("density") if r.startswith(("TM1", "TM2"))}
    assert not density_tm, f"fill left TopMetal density violations: {sorted(density_tm)}"

    for metal_layer, name in ((126, "TopMetal1"), (134, "TopMetal2")):
        d = _density(combined, metal_layer)
        assert DENSITY_MIN <= d <= DENSITY_MAX, \
            f"{name} drawn+fill density {d:.1f}% outside [{DENSITY_MIN}, {DENSITY_MAX}] %"
