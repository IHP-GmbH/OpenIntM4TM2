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
"""Regression for the four-metal density-feedback fill closure (python/fill_closure.py).

close_fill drives Metal4, Metal5, TopMetal1 and TopMetal2 through the density deck each
iteration and adjusts each metal's lattice from the verdict. Three behaviors are proven
end to end against the real deck:

  - a design already fillable to band closes in a single iteration, with no needless
    adjustment on any of the four metals;
  - a Metal4/Metal5 that comes in under the floor (forced with a large nofill box) is
    densified into band, and the densified fill is still geometrically legal;
  - a TopMetal that is already dense in drawn metal, so the default fill would cross the
    70% cap, is relaxed (its lattice thinned) back into band. TopMetal is the metal that
    can only be relaxed, never densified, so this exercises that branch specifically;
  - a TopMetal starved below the 25% floor (its fill blocked by a nofill box) cannot be
    densified, so the closure reports it under and stops at the fixed point instead of
    spinning the whole iteration budget re-running identical work.
"""

import shutil
import sys
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"

KLAYOUT_BIN = shutil.which("klayout")

sys.path.insert(0, str(PYTHON_DIR))
import fill_closure as fc  # noqa: E402

MN_MIN, MN_MAX = 35.0, 60.0            # Metal4/Metal5 global band (Mn_j/Mn_k)
TM_MAX = 70.0                          # TopMetal global cap (TM(n)_d)


def _seal(top, ly):
    ring = kdb.Region(kdb.Box(0, 0, 200000, 200000)) - kdb.Region(kdb.Box(5000, 5000, 195000, 195000))
    top.shapes(ly.layer(39, 0)).insert(ring)


def _box(ly, top, layer, dt, x0, y0, x1, y1):
    top.shapes(ly.layer(layer, dt)).insert(kdb.DBox(x0, y0, x1, y1))


def _write_normal(path):
    """Open interior with a modest block on each metal: fillable to band directly."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 0, 20, 20, 60, 60)
    _box(ly, top, 67, 0, 120, 120, 170, 170)
    _box(ly, top, 126, 0, 20, 120, 60, 160)
    _box(ly, top, 134, 0, 120, 20, 170, 60)
    ly.write(str(path))


def _write_sparse(path):
    """A large nofill box on Metal4/Metal5 starves their default fill below the floor."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 23, 10, 10, 130, 130)
    _box(ly, top, 67, 23, 10, 10, 130, 130)
    _box(ly, top, 50, 0, 150, 150, 160, 160)
    _box(ly, top, 67, 0, 150, 150, 160, 160)
    ly.write(str(path))


def _write_dense_topmetal(path):
    """Drawn TopMetal1 near 62% of the chip: the default dense fill would cross the 70% cap."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 0, 20, 20, 60, 60)          # small M4/M5 blocks, easily in band
    _box(ly, top, 67, 0, 120, 120, 160, 160)
    _box(ly, top, 126, 0, 5, 5, 195, 135)         # 190 x 130 = 24700 um^2 over a 40000 chip
    ly.write(str(path))


def _write_starved_topmetal(path):
    """TopMetal1 drawn tiny and its fill blocked by a full-interior nofill: stuck under 25%."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 0, 20, 20, 60, 60)          # M4/M5 fill freely into band
    _box(ly, top, 67, 0, 120, 120, 170, 170)
    _box(ly, top, 126, 0, 20, 20, 45, 45)          # tiny drawn TopMetal1
    _box(ly, top, 126, 23, 10, 10, 190, 190)       # nofill over the interior starves its fill
    _box(ly, top, 134, 0, 120, 20, 170, 60)        # TopMetal2 fills freely into band
    ly.write(str(path))


def _metalnfiller_violations(gds, run_dir):
    if str(DRC_DIR) not in sys.path:
        sys.path.insert(0, str(DRC_DIR))
    from run_drc import run_deck, get_rules_with_violations
    report = run_deck(str(DRC_DIR / "intm4tm2.drc"), "metalnfiller", str(gds), "TOP",
                      run_dir, threads=2, run_mode="flat")
    return get_rules_with_violations(report)


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_converges_in_one_iteration_when_already_fillable(tmp_path):
    design = tmp_path / "normal.gds"
    out = tmp_path / "normal_out.gds"
    _write_normal(design)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=6, workdir=tmp_path)

    assert converged
    assert len(history) == 1, "an already-fillable design must not be adjusted"
    for layer in fc.STACK:
        assert history[-1]["state"][layer] == "ok"
    for layer in fc.METALS:
        assert MN_MIN <= history[-1]["density"][layer] <= MN_MAX
    for layer in fc.TOPMETALS:
        assert history[-1]["density"][layer] <= TM_MAX


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_densifies_underfilled_metal_into_band(tmp_path):
    design = tmp_path / "sparse.gds"
    out = tmp_path / "sparse_out.gds"
    _write_sparse(design)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=8, workdir=tmp_path)

    # Metal4 starts under the floor, needs more than one pass, and reaches the band.
    assert history[0]["state"][50] == "under"
    assert len(history) >= 2, "under-filled Metal4/Metal5 should have required densification"
    assert converged
    for layer in fc.STACK:
        assert history[-1]["state"][layer] == "ok"
    for layer in fc.METALS:
        assert history[-1]["density"][layer] >= MN_MIN

    # the densified fill is still legal geometry.
    run_dir = tmp_path / "final_drc"
    run_dir.mkdir()
    filler = _metalnfiller_violations(out, run_dir)
    assert not filler, f"densified fill tripped filler-geometry DRC: {sorted(filler)}"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_relaxes_overfilled_topmetal_into_band(tmp_path):
    """A TopMetal that overshoots the 70% cap at the dense default is thinned back into band."""
    design = tmp_path / "tmdense.gds"
    out = tmp_path / "tmdense_out.gds"
    _write_dense_topmetal(design)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=8, workdir=tmp_path)

    # TopMetal1 starts over the cap, its lattice is grown, and it lands back in band.
    assert history[0]["state"][126] == "over", "the dense TopMetal design should start over the cap"
    assert history[0]["gaps"][126] == fc.TM_DEFAULT_GAP
    assert len(history) >= 2, "an over-cap TopMetal should have required a relax pass"
    assert converged
    assert history[-1]["gaps"][126] > history[0]["gaps"][126], "the TopMetal lattice was not relaxed"
    assert history[-1]["state"][126] == "ok"
    assert history[-1]["density"][126] <= TM_MAX
    # the TopMetal relax touched only TopMetal1: every other metal's lattice is untouched.
    for layer in fc.METALS:
        assert history[-1]["gaps"][layer] == fc.DEFAULT_GAPS
    assert history[-1]["gaps"][134] == fc.TM_DEFAULT_GAP


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_reports_starved_topmetal_and_stops_at_fixed_point(tmp_path):
    """A TopMetal stuck under the 25% floor cannot be densified: report it, don't spin."""
    design = tmp_path / "tmstarved.gds"
    out = tmp_path / "tmstarved_out.gds"
    _write_starved_topmetal(design)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=8, workdir=tmp_path)

    assert not converged, "a TopMetal below the floor with no fill room cannot converge"
    assert history[-1]["state"][126] == "under"
    # the un-chased TopMetal 'under' is a fixed point: the loop must stop on the first
    # pass, not burn the whole max_iter budget re-running byte-identical work.
    assert len(history) == 1, "an unfixable state must stop at the fixed point"
    for layer in (50, 67, 134):
        assert history[-1]["state"][layer] == "ok"
