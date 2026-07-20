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
"""Regression for the reduced 'bondpad' PCell (IntM4TM2 interposer PDK).

The bondpad device is a BEOL-only reduction of the IHP SG13G2 bondpad: it
draws only on the interposer stack (Metal4, Metal5, TopMetal1, TopMetal2,
their vias, plus dfpad/Passiv/nofill purposes). This test drives the PCell
headlessly and proves the reduction leaks no base-PDK (FEOL) layer.

The PCell library registers through pya, so generation runs inside a
headless 'klayout -zz -rx -r <script>' batch that emits one GDS per case
in a single invocation (keeps klayout runs light). The batch guards
technology creation so it is robust to user search paths that may already
have registered 'intm4tm2'.
"""

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

# Interposer layer numbers only -- anything else is a leaked FEOL layer.
INTERPOSER_LAYERS = {9, 41, 50, 66, 67, 99, 125, 126, 133, 134}
TM2    = (134, 0)
DFPAD  = (41, 0)
PASS   = (9, 0)
NOFILL = [(50, 23), (67, 23), (126, 23), (134, 23)]   # Metal4/5, TopMetal1, TopMetal2
STACK_METALS = [(50, 0), (67, 0), (126, 0)]           # Metal4, Metal5, TopMetal1 rings
STACK_VIAS   = [(66, 0), (125, 0), (133, 0)]          # Via4, TopVia1, TopVia2

KLAYOUT_BIN = shutil.which("klayout")

PCELL_BATCH_SCRIPT = textwrap.dedent("""\
    import sys
    import pya

    # The library binds to the 'intm4tm2' technology; register it from the
    # repo .lyt so the batch session resolves the library and its layers.
    # The technology may already exist if user-level search paths leak in.
    if 'intm4tm2' in pya.Technology.technology_names():
        tech = pya.Technology.technology_by_name('intm4tm2')
    else:
        tech = pya.Technology.create_technology('intm4tm2')
    tech.load({lyt_path!r})

    sys.path.insert(0, {python_dir!r})
    sys.path.insert(0, {api_dir!r})

    import intm4tm2_pycell_lib  # registers the 'IntM4TM2' library

    cases = {cases!r}

    for name, params in cases:
        layout = pya.Layout()
        layout.dbu = 0.001
        layout.technology_name = 'intm4tm2'
        cell = layout.create_cell("bondpad", "IntM4TM2", params)
        assert cell is not None, "create_cell returned None for " + name
        top = layout.create_cell("TOP")
        top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))
        opts = pya.SaveLayoutOptions()
        opts.write_context_info = False
        layout.write({out_dir!r} + "/bondpad_" + name + ".gds", opts)
""")


def _region_of(layout, cell, layer, datatype):
    idx = layout.layer(layer, datatype)
    region = kdb.Region(cell.begin_shapes_rec(idx))
    region.merge()
    return region


def _drawn_layers(layout, cell):
    drawn = set()
    for idx in layout.layer_indexes():
        if not cell.bbox_per_layer(idx).empty():
            info = layout.get_info(idx)
            drawn.add((info.layer, info.datatype))
    return drawn


def _run_pcell_batch(out_dir, cases):
    """Generate one PCell GDS per case in a single headless klayout run."""
    script = out_dir / "gen_bondpads.py"
    script.write_text(PCELL_BATCH_SCRIPT.format(
        lyt_path=str(REPO_KLAYOUT / "tech" / "intm4tm2.lyt"),
        python_dir=str(PYTHON_DIR),
        api_dir=str(PYTHON_DIR / "pycell4klayout-api" / "source" / "python"),
        cases=cases,
        out_dir=str(out_dir)))

    empty_home = out_dir / "klayout_home"
    empty_home.mkdir()
    env = dict(os.environ, KLAYOUT_HOME=str(empty_home))
    # A stale KLAYOUT_LYP_FILE would replace the layer table with another
    # PDK's; the tech class honors it, so keep it out of the batch. The
    # same goes for KLAYOUT_PATH, which would preload user technologies
    # (auto-registering 'intm4tm2') and collide with create_technology.
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)
    result = subprocess.run(
        [KLAYOUT_BIN, "-zz", "-rx", "-r", str(script)],
        capture_output=True, text=True, env=env, timeout=300)
    assert result.returncode == 0, (
        f"pcell batch failed:\n{result.stdout}\n{result.stderr}")


def _base(**overrides):
    params = {
        "shape": "octagon",
        "padType": "bondpad",
        "stack": "nil",
        "addFillerEx": "nil",
        "topMetal": "TM2",
        "bottomMetal": "4",
        "diameter": "80.00u",
    }
    params.update(overrides)
    return params


# Full matrix: shape x padType x stack x addFillerEx (24 combos), plus the
# named cases the targeted assertions read.
_MATRIX = []
for _shape in ("octagon", "square", "circle"):
    for _pad in ("bondpad", "probepad"):
        for _stack in ("nil", "t"):
            for _fill in ("nil", "t"):
                _name = f"m_{_shape}_{_pad}_{_stack}_{_fill}"
                _MATRIX.append((_name, _base(shape=_shape, padType=_pad,
                                             stack=_stack, addFillerEx=_fill)))

CASES = [
    ("default", _base()),
    ("probepad", _base(padType="probepad")),
    ("filler", _base(addFillerEx="t")),
    ("stacked", _base(stack="t")),
] + _MATRIX


@pytest.fixture(scope="module")
def generated():
    if KLAYOUT_BIN is None:
        pytest.skip("klayout binary not on PATH")
    tmp = tempfile.mkdtemp()
    out_dir = Path(tmp)
    _run_pcell_batch(out_dir, CASES)
    layouts = {}
    for name, _ in CASES:
        layout = kdb.Layout()
        layout.read(str(out_dir / f"bondpad_{name}.gds"))
        layouts[name] = (layout, layout.top_cell())
    yield layouts
    shutil.rmtree(tmp, ignore_errors=True)


def test_default_octagon_bondpad_layer_set(generated):
    layout, top = generated["default"]
    assert _drawn_layers(layout, top) == {TM2, DFPAD, PASS}


def test_probepad_has_no_dfpad(generated):
    layout, top = generated["probepad"]
    drawn = _drawn_layers(layout, top)
    assert PASS in drawn
    assert TM2 in drawn
    assert DFPAD not in drawn


def test_passiv_opening_strictly_inside_pad(generated):
    layout, top = generated["default"]
    pad = _region_of(layout, top, *TM2).bbox()
    opening = _region_of(layout, top, *PASS).bbox()
    assert opening.left > pad.left
    assert opening.right < pad.right
    assert opening.bottom > pad.bottom
    assert opening.top < pad.top


def test_filler_exclusion_adds_nofill(generated):
    layout, top = generated["filler"]
    # exact set: the plain pad plus one nofill circle per interposer metal
    assert _drawn_layers(layout, top) == {PASS, DFPAD, TM2} | set(NOFILL)


def test_stack_adds_metals_and_vias(generated):
    layout, top = generated["stacked"]
    # exact set pins the full stack and doubles as a FEOL-leak guard
    assert _drawn_layers(layout, top) == (
        {PASS, DFPAD, TM2} | set(STACK_METALS) | set(STACK_VIAS))


def test_circle_bondpad_carries_dfpad(generated):
    # The circle branch draws dfpad/nofill/metals directly on their layers;
    # the upstream copy-and-retag idiom is a no-op under pycell4klayout-api and
    # would silently drop them. Pin the corrected direct-draw behavior.
    layout, top = generated["m_circle_bondpad_nil_nil"]
    assert _drawn_layers(layout, top) == {PASS, DFPAD, TM2}


def test_circle_filler_adds_all_nofill(generated):
    layout, top = generated["m_circle_bondpad_nil_t"]
    assert _drawn_layers(layout, top) == {PASS, DFPAD, TM2} | set(NOFILL)


def test_circle_stack_adds_metals_and_vias(generated):
    layout, top = generated["m_circle_bondpad_t_nil"]
    assert _drawn_layers(layout, top) == (
        {PASS, DFPAD, TM2} | set(STACK_METALS) | set(STACK_VIAS))


@pytest.mark.parametrize("name", [c[0] for c in CASES])
def test_no_feol_leak(generated, name):
    layout, top = generated[name]
    drawn = _drawn_layers(layout, top)
    assert drawn, f"case {name} drew nothing"
    for layer, _dt in drawn:
        assert layer in INTERPOSER_LAYERS, (
            f"case {name} leaked non-interposer layer {layer}")
