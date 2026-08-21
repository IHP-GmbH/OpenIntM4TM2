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
"""Regression for the unified fill entry point fill_closure.fill_stack.

fill_stack fills all four interposer metals (Metal4, Metal5, TopMetal1, TopMetal2)
in one call, so a consumer (the KiCad chiplet_export plugin) can bind a single
in-process function. It has two modes: "single-pass" grades coverage by area (fast,
no deck); "closure" drives Metal4/Metal5 through the deck-verified feedback loop and
adds a single TopMetal pass. These tests check the report contract, both modes, that
keep-outs already in the design are honored, and the in-place / workdir=None guards.
"""

import shutil
import sys
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"

KLAYOUT_BIN = shutil.which("klayout")

sys.path.insert(0, str(PYTHON_DIR))
import fill_closure as fc  # noqa: E402

STACK_KEYS = ("M4", "M5", "TM1", "TM2")
FILL_LAYERS = (50, 67, 126, 134)


def _build_open_design(path, nofill50=None, starve_m45=False):
    """An open interposer (seal interior + a small drawn block per metal).

    The seal interior is largely empty, so every generator has room to fill and lands
    in band. `nofill50` optionally stamps a Metal4 nofill (50/23) keep-out box.
    `starve_m45` stamps a large nofill on 50/23 and 67/23 that covers most of the
    interior, so the Metal4/Metal5 generator cannot reach the 35 % floor and the deck
    flags them under band (used to exercise the non-converged closure branch).
    """
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("INTERPOSER")

    def box(layer, dt, x0, y0, x1, y1):
        top.shapes(ly.layer(layer, dt)).insert(kdb.DBox(x0, y0, x1, y1))

    ring = kdb.Region(kdb.Box(0, 0, 150000, 150000)) - kdb.Region(kdb.Box(3000, 3000, 147000, 147000))
    top.shapes(ly.layer(39, 0)).insert(ring)
    for layer in FILL_LAYERS:
        box(layer, 0, 20, 20, 50, 50)
    if nofill50 is not None:
        box(50, 23, *nofill50)
    if starve_m45:
        for layer in (50, 67):
            box(layer, 23, 10, 10, 146, 146)
    ly.write(str(path))


def _has_fill(gds, layer, dt=22):
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.find_layer(layer, dt)
    if li is None:
        return False
    return not kdb.Region(top.begin_shapes_rec(li)).is_empty()


def _overlap_area(gds, layer, dt, other):
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.find_layer(layer, dt)
    if li is None:
        return 0.0
    return (kdb.Region(top.begin_shapes_rec(li)) & other).area() * ly.dbu * ly.dbu


def _drawn_area(gds, layer):
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    li = ly.find_layer(layer, 0)
    if li is None:
        return 0.0
    return kdb.Region(top.begin_shapes_rec(li)).area() * ly.dbu * ly.dbu


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_single_pass_fills_all_four_in_band(tmp_path):
    design = tmp_path / "design.gds"
    out = tmp_path / "out.gds"
    _build_open_design(design)

    report = fc.fill_stack(str(design), str(out), topcell="INTERPOSER", mode="single-pass")

    assert report["mode"] == "single-pass"
    for key in STACK_KEYS:
        entry = report[key]
        assert set(entry) == {"coverage_pct", "band", "state", "converged"}
        assert entry["coverage_pct"] > 0
        assert entry["state"] == "ok" and entry["converged"] is True
        lo, hi = entry["band"]
        assert lo <= entry["coverage_pct"] <= hi
    assert report["converged"] is True
    for layer in FILL_LAYERS:
        assert _has_fill(out, layer), f"no fill emitted on {layer}/22"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_mode_reports_in_band_metals_as_converged(tmp_path):
    """On an open design, closure grades every metal ok/converged with in-band percents.

    The band-containment and percent-scale checks (not just state membership) are what
    catch a broken deck-verdict-to-report mapping: a fraction returned as coverage, or
    an unconditional converged, would fall out of [band] here.
    """
    design = tmp_path / "design.gds"
    out = tmp_path / "out.gds"
    _build_open_design(design)

    report = fc.fill_stack(str(design), str(out), topcell="INTERPOSER", mode="closure")

    assert report["mode"] == "closure"
    for key in STACK_KEYS:
        entry = report[key]
        assert set(entry) == {"coverage_pct", "band", "state", "converged"}
        lo, hi = entry["band"]
        assert 0 < entry["coverage_pct"] < 100
        assert lo <= entry["coverage_pct"] <= hi
        assert entry["state"] == "ok" and entry["converged"] is True
    assert report["converged"] is True
    for layer in FILL_LAYERS:
        assert _has_fill(out, layer), f"no fill emitted on {layer}/22"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_mode_flags_underfilled_metal(tmp_path):
    """A deck-flagged under-filled Metal4/Metal5 must surface as not-converged.

    This is the branch the consumer keys on for its density warning; the open-design
    test cannot exercise it. A heavy 50/23+67/23 nofill starves the Metal4/Metal5
    generator, so one closure iteration leaves both under the 35 % floor.
    """
    design = tmp_path / "design.gds"
    out = tmp_path / "out.gds"
    _build_open_design(design, starve_m45=True)

    report = fc.fill_stack(str(design), str(out), topcell="INTERPOSER",
                           mode="closure", max_iter=1)

    for key in ("M4", "M5"):
        assert report[key]["state"] == "under", f"{key} not flagged under band"
        assert report[key]["converged"] is False
        assert report[key]["coverage_pct"] < report[key]["band"][0]
    assert report["converged"] is False


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_prestamped_nofill_is_honored(tmp_path):
    """Fill stays out of a Metal4 nofill (50/23) that was already in the design."""
    design = tmp_path / "design.gds"
    out = tmp_path / "out.gds"
    nofill = (80.0, 80.0, 110.0, 110.0)                 # in open area the fill would otherwise cover
    _build_open_design(design, nofill50=nofill)

    fc.fill_stack(str(design), str(out), topcell="INTERPOSER", mode="single-pass")

    keepout = kdb.Region(kdb.Box(80000, 80000, 110000, 110000))
    assert _overlap_area(out, 50, 22, keepout) == 0, \
        "fill_stack put Metal4 fill inside a pre-stamped 50/23 nofill"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_in_place_output_and_workdir_none(tmp_path):
    """output may equal input; workdir=None uses a temp dir. Drawn metal survives."""
    design = tmp_path / "design.gds"
    _build_open_design(design)
    before = _drawn_area(design, 50)

    report = fc.fill_stack(str(design), str(design), topcell="INTERPOSER",
                           mode="single-pass", workdir=None)

    assert _drawn_area(design, 50) == before, "in-place run lost the drawn Metal4"
    assert _has_fill(design, 50), "in-place run added no Metal4 fill"
    assert report["converged"] is True


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_unknown_mode_raises(tmp_path):
    design = tmp_path / "design.gds"
    _build_open_design(design)
    with pytest.raises(ValueError):
        fc.fill_stack(str(design), str(tmp_path / "out.gds"), mode="bogus")
