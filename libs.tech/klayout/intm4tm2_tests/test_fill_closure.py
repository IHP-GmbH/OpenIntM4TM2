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
"""Regression for the Metal4/Metal5 density-feedback fill closure (python/fill_closure.py).

Two behaviors are proven end to end against the real density deck:

  - a design that is already fillable to band closes in a single iteration, with
    no needless densification;
  - a design that comes in under the floor (here forced with a large nofill box)
    is detected as under-filled and driven into band by shrinking the lattice,
    and the densified fill it emits is still geometrically legal (no MFil.c/a2).

The closure uses density.drc as the authority for pass/fail, so these tests also
guard that the generator's -rd pitch knobs and the driver's classify/adjust logic
stay wired to the deck verdict.
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

BAND_MIN, BAND_MAX = 35.0, 60.0


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
    ly.write(str(path))


def _write_sparse(path):
    """A large nofill box on both metals starves the default fill below the floor."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 23, 10, 10, 130, 130)
    _box(ly, top, 67, 23, 10, 10, 130, 130)
    _box(ly, top, 50, 0, 150, 150, 160, 160)
    _box(ly, top, 67, 0, 150, 150, 160, 160)
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
    assert len(history) == 1, "an already-fillable design must not be densified"
    for layer in fc.METALS:
        assert history[-1]["state"][layer] == "ok"
        assert BAND_MIN <= history[-1]["density"][layer] <= BAND_MAX


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_densifies_underfilled_design_into_band(tmp_path):
    design = tmp_path / "sparse.gds"
    out = tmp_path / "sparse_out.gds"
    _write_sparse(design)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=8, workdir=tmp_path)

    # starts under the floor, needs more than one pass, and reaches the band.
    assert history[0]["state"][50] == "under"
    assert len(history) >= 2, "under-filled design should have required densification"
    assert converged
    for layer in fc.METALS:
        assert history[-1]["state"][layer] == "ok"
        assert history[-1]["density"][layer] >= BAND_MIN

    # the densified fill is still legal geometry.
    run_dir = tmp_path / "final_drc"
    run_dir.mkdir()
    filler = _metalnfiller_violations(out, run_dir)
    assert not filler, f"densified fill tripped filler-geometry DRC: {sorted(filler)}"
