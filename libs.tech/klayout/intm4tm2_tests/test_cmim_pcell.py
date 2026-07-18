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
"""Regression for the cmim MIM-capacitor PCell (intm4tm2_pycell_lib).

The cmim PCell is ported from the IHP-Open-PDK (SG13G2 open PDK) and the
interposer metal stack keeps the same layer numbering for the MIM module
(MIM 36/0, Metal5 67/0, TopMetal1 126/0, Vmim 129/0, TEXT 63/0). Two
things are pinned here:

1. Geometry: for a set of (w, l) cases the drawn boxes must match the
   reference formulas exactly (MIM plate, Metal5 bottom plate sized by
   Mim_c, Vmim via array from the fix()/GridFix() layout math, TopMetal1
   top plate, TEXT labels).
2. Upstream-oracle parity: when PDK_ROOT points at an IHP-Open-PDK
   checkout, the same cases are generated with the upstream PCell
   library in a second headless batch and each fabrication layer must
   XOR empty against ours. The test skips cleanly when PDK_ROOT is
   unset.

The PCell side runs inside a headless klayout batch process because the
PCell library registers through pya. Note that the default 'Calculate'
parameter is 'w&l' (derive w and l from C), so cases that pin w and l
explicitly must also pass Calculate='C' -- same behavior as upstream.
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import klayout.db as kdb

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"

KLAYOUT_BIN = shutil.which("klayout")

# (case name, PCell parameter dict, w_um, l_um). The first case uses the
# PCell defaults (w = l = cmim_defLW = 6.99 um); the coercion callback
# recomputes w and l from the default C there, which lands back on 6.99.
CASES = [
    ("default", {}, 6.99, 6.99),
    ("rect", {"Calculate": "C", "w": "10u", "l": "5u"}, 10.0, 5.0),
    ("nearmin", {"Calculate": "C", "w": "1.14u", "l": "1.14u"}, 1.14, 1.14),
]

FAB_LAYERS = [(36, 0), (67, 0), (126, 0), (129, 0)]
TEXT_LAYER = (63, 0)

# Tech parameters mirrored from intm4tm2_tech.json (values identical to
# the SG13G2 open PDK): used to recompute the expected geometry.
GRID = 0.005          # 'grid' fallback; getGridResolution() returns 0.0
IGRID = 1.0 / GRID
EPS = 0.001           # epsilon1
MIM_C = 0.6           # Metal5 bottom plate enclosure of MIM
MIM_D = 0.36          # via array inset from the MIM plate (cont_over)
TV1_A = 0.42          # Vmim via size (cont_size)
TV1_D = 0.42
CONT_DIST = 0.84      # via spacing, fixed in the PCell code


BATCH_SCRIPT = textwrap.dedent("""\
    import sys
    import pya

    # The library binds to the technology; register it from the .lyt so
    # the batch session resolves the library and its layer table. The
    # technology may already exist if user-level search paths leak in.
    if {tech_name!r} in pya.Technology.technology_names():
        tech = pya.Technology.technology_by_name({tech_name!r})
    else:
        tech = pya.Technology.create_technology({tech_name!r})
    tech.load({lyt_path!r})

    sys.path.insert(0, {python_dir!r})
    sys.path.insert(0, {api_dir!r})

    import {lib_module}  # registers the PCell library

    cases = {cases!r}

    for name, params in cases:
        layout = pya.Layout()
        layout.dbu = 0.001
        layout.technology_name = {tech_name!r}
        cell = layout.create_cell("cmim", {lib_name!r}, params)
        assert cell is not None, "create_cell returned None for " + name
        top = layout.create_cell("TOP")
        top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))
        opts = pya.SaveLayoutOptions()
        opts.write_context_info = False
        layout.write({out_dir!r} + "/" + {prefix!r} + name + ".gds", opts)
""")


def _fix(value):
    if type(value) == float:
        return int(math.floor(value))
    return value


def _grid_fix(value):
    return _fix(value * IGRID + EPS) * GRID


def _dbu(value):
    return int(round(value * 1000.0))


def _expected_geometry(w, l):
    """Replicates the cmim layout math; returns boxes in integer dbu."""
    xanz = _fix((w - MIM_D - MIM_D + CONT_DIST) / (TV1_A + CONT_DIST) + EPS)
    w1 = xanz * (TV1_A + CONT_DIST) - CONT_DIST + MIM_D + MIM_D
    xoffset = _grid_fix((w - w1) / 2)

    yanz = _fix((l - MIM_D - MIM_D + CONT_DIST) / (TV1_A + CONT_DIST) + EPS)
    l1 = yanz * (TV1_A + CONT_DIST) - CONT_DIST + MIM_D + MIM_D
    yoffset = _grid_fix((l - l1) / 2)

    vias = []
    ycont = MIM_D + yoffset
    while ycont + TV1_A + MIM_D <= l + EPS:
        xcont = MIM_D + xoffset
        while xcont + TV1_A + MIM_D <= w + EPS:
            vias.append((_dbu(xcont), _dbu(ycont),
                         _dbu(xcont + TV1_A), _dbu(ycont + TV1_A)))
            xcont = xcont + TV1_A + CONT_DIST
        ycont = ycont + TV1_A + CONT_DIST

    x2 = xcont + TV1_D - CONT_DIST
    y2 = ycont + TV1_D - CONT_DIST
    x1 = MIM_D - TV1_D + xoffset
    y1 = MIM_D - TV1_D + yoffset

    return {
        "mim": (0, 0, _dbu(w), _dbu(l)),
        "metal5": (_dbu(-MIM_C), _dbu(-MIM_C),
                   _dbu(w + MIM_C), _dbu(l + MIM_C)),
        "topmetal1": (_dbu(x1), _dbu(y1), _dbu(x2), _dbu(y2)),
        "vias": vias,
        "nvias": xanz * yanz,
    }


def _region_of(layout, cell, layer, datatype):
    idx = layout.layer(layer, datatype)
    region = kdb.Region(cell.begin_shapes_rec(idx))
    region.merge()
    return region


def _boxes_of(layout, cell, layer, datatype):
    idx = layout.layer(layer, datatype)
    region = kdb.Region(cell.begin_shapes_rec(idx))
    boxes = []
    for polygon in region.each():
        box = polygon.bbox()
        boxes.append((box.left, box.bottom, box.right, box.top))
    return sorted(boxes)


def _texts_of(layout, cell, layer, datatype):
    idx = layout.layer(layer, datatype)
    return sorted(t.string for t in kdb.Texts(cell.begin_shapes_rec(idx)))


def _drawn_layers(layout, cell):
    drawn = set()
    for idx in layout.layer_indexes():
        if not cell.bbox_per_layer(idx).empty():
            info = layout.get_info(idx)
            drawn.add((info.layer, info.datatype))
    return drawn


def _batch_env(out_dir):
    empty_home = out_dir / "klayout_home"
    empty_home.mkdir(exist_ok=True)
    env = dict(os.environ, KLAYOUT_HOME=str(empty_home))
    # A stale KLAYOUT_LYP_FILE would replace the layer table with another
    # PDK's; the tech class honors it, so keep it out of the batch. The
    # same goes for KLAYOUT_PATH, which would preload user technologies.
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)
    return env


def _run_batch(out_dir, script_name, script_text):
    script = out_dir / script_name
    script.write_text(script_text)
    result = subprocess.run(
        [KLAYOUT_BIN, "-zz", "-rx", "-r", str(script)],
        capture_output=True, text=True, env=_batch_env(out_dir), timeout=600)
    assert result.returncode == 0, (
        f"klayout batch failed:\n{result.stdout}\n{result.stderr}")


def _generate_ours(out_dir, cases):
    _run_batch(out_dir, "gen_ours.py", BATCH_SCRIPT.format(
        tech_name="intm4tm2",
        lyt_path=str(REPO_KLAYOUT / "tech" / "intm4tm2.lyt"),
        python_dir=str(PYTHON_DIR),
        api_dir=str(PYTHON_DIR / "pycell4klayout-api" / "source" / "python"),
        lib_module="intm4tm2_pycell_lib",
        lib_name="IntM4TM2",
        cases=cases,
        out_dir=str(out_dir),
        prefix="ours_"))


def _generate_oracle(out_dir, cases, upstream_klayout):
    _run_batch(out_dir, "gen_oracle.py", BATCH_SCRIPT.format(
        tech_name="sg13g2",
        lyt_path=str(upstream_klayout / "tech" / "sg13g2.lyt"),
        python_dir=str(upstream_klayout / "python"),
        api_dir=str(upstream_klayout / "python" / "pycell4klayout-api"
                    / "source" / "python"),
        lib_module="sg13g2_pycell_lib",
        lib_name="SG13_dev",
        cases=cases,
        out_dir=str(out_dir),
        prefix="oracle_"))


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_cmim_geometry():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _generate_ours(out_dir, [(name, params) for name, params, _, _
                                 in CASES])

        for name, _, w, l in CASES:
            layout = kdb.Layout()
            layout.read(str(out_dir / f"ours_{name}.gds"))
            top = layout.top_cell()
            expected = _expected_geometry(w, l)

            assert _drawn_layers(layout, top) == set(
                FAB_LAYERS + [TEXT_LAYER]), (
                f"unexpected layer set for case {name}")

            assert _boxes_of(layout, top, 36, 0) == [expected["mim"]], (
                f"MIM plate mismatch for case {name}")
            assert _boxes_of(layout, top, 67, 0) == [expected["metal5"]], (
                f"Metal5 bottom plate mismatch for case {name}")
            assert _boxes_of(layout, top, 126, 0) == [
                expected["topmetal1"]], (
                f"TopMetal1 top plate mismatch for case {name}")

            vias = _boxes_of(layout, top, 129, 0)
            assert len(vias) == expected["nvias"], (
                f"via count mismatch for case {name}: "
                f"{len(vias)} != {expected['nvias']}")
            assert vias == sorted(expected["vias"]), (
                f"via array mismatch for case {name}")

            texts = _texts_of(layout, top, *TEXT_LAYER)
            assert len(texts) == 2 and "cmim" in texts, (
                f"labels mismatch for case {name}: {texts}")
            cap_labels = [t for t in texts if t.startswith("c=")]
            assert len(cap_labels) == 1, (
                f"capacitance label missing for case {name}: {texts}")
            if name == "default":
                assert cap_labels[0] == "c=74.6f"


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_cmim_upstream_parity():
    pdk_root = os.environ.get("PDK_ROOT")
    if not pdk_root:
        pytest.skip("PDK_ROOT not set; upstream oracle unavailable")
    upstream_klayout = Path(pdk_root) / "ihp-sg13g2" / "libs.tech" / "klayout"
    if not (upstream_klayout / "python" / "sg13g2_pycell_lib").is_dir():
        pytest.skip("upstream PCell library not found under PDK_ROOT")

    cases = [(name, params) for name, params, _, _ in CASES]

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _generate_ours(out_dir, cases)
        _generate_oracle(out_dir, cases, upstream_klayout)

        for name, _ in cases:
            ours = kdb.Layout()
            ours.read(str(out_dir / f"ours_{name}.gds"))
            ours_top = ours.top_cell()

            oracle = kdb.Layout()
            oracle.read(str(out_dir / f"oracle_{name}.gds"))
            oracle_top = oracle.top_cell()

            for layer, datatype in FAB_LAYERS:
                ours_region = _region_of(ours, ours_top, layer, datatype)
                oracle_region = _region_of(oracle, oracle_top, layer,
                                           datatype)
                assert not oracle_region.is_empty(), (
                    f"oracle drew nothing on {layer}/{datatype} ({name})")
                assert not ours_region.is_empty(), (
                    f"pcell drew nothing on {layer}/{datatype} ({name})")
                xor = ours_region ^ oracle_region
                assert xor.is_empty(), (
                    f"layer {layer}/{datatype} differs for case {name}: "
                    f"XOR area {xor.area()} dbu^2")
