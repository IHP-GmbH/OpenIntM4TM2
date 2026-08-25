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

EXPECTED_LAYERS = {
    (9, 0),
    (39, 0),
    (39, 4),
    (50, 0),
    (66, 0),
    (67, 0),
    (125, 0),
    (126, 0),
    (133, 0),
    (134, 0),
}
EDGESEAL_BOUNDARY = (39, 4)

KLAYOUT_BIN = shutil.which("klayout")

PCELL_BATCH_SCRIPT = textwrap.dedent("""\
    import sys
    import pya

    if 'intm4tm2' in pya.Technology.technology_names():
        tech = pya.Technology.technology_by_name('intm4tm2')
    else:
        tech = pya.Technology.create_technology('intm4tm2')
    tech.load({lyt_path!r})

    sys.path.insert(0, {python_dir!r})
    sys.path.insert(0, {api_dir!r})

    import intm4tm2_pycell_lib

    params = {params!r}

    layout = pya.Layout()
    layout.dbu = 0.001
    layout.technology_name = 'intm4tm2'
    cell = layout.create_cell("sealring", "IntM4TM2", params)
    assert cell is not None, "create_cell returned None for sealring"
    top = layout.create_cell("TOP")
    top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))
    opts = pya.SaveLayoutOptions()
    opts.write_context_info = False
    layout.write({out_path!r}, opts)
""")


def _drawn_layers(layout, cell):
    drawn = set()
    for idx in layout.layer_indexes():
        if not cell.bbox_per_layer(idx).empty():
            info = layout.get_info(idx)
            drawn.add((info.layer, info.datatype))
    return drawn


def _region_bbox(layout, cell, layer, datatype):
    idx = layout.layer(layer, datatype)
    region = kdb.Region(cell.begin_shapes_rec(idx))
    region.merge()
    return region.bbox()


@pytest.fixture(scope="module")
def generated_sealring():
    if KLAYOUT_BIN is None:
        pytest.skip("klayout binary not on PATH")

    tmp_dir = Path(tempfile.mkdtemp())
    script = tmp_dir / "gen_sealring.py"
    output_gds = tmp_dir / "sealring.gds"

    params = {
        "l": "400u",
        "w": "400u",
        "addLabel": "nil",
        "addSlit": "nil",
        "edgeBox": "25u",
    }

    script.write_text(PCELL_BATCH_SCRIPT.format(
        lyt_path=str(REPO_KLAYOUT / "tech" / "intm4tm2.lyt"),
        python_dir=str(PYTHON_DIR),
        api_dir=str(PYTHON_DIR / "pycell4klayout-api" / "source" / "python"),
        params=params,
        out_path=str(output_gds),
    ))

    empty_home = tmp_dir / "klayout_home"
    empty_home.mkdir()
    env = dict(os.environ, KLAYOUT_HOME=str(empty_home))
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)

    result = subprocess.run(
        [KLAYOUT_BIN, "-zz", "-rx", "-r", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, f"sealring pcell batch failed:\n{result.stdout}\n{result.stderr}"

    layout = kdb.Layout()
    layout.read(str(output_gds))
    yield layout, layout.top_cell()

    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_sealring_generates_interposer_only_layers(generated_sealring):
    layout, top = generated_sealring
    assert _drawn_layers(layout, top) == EXPECTED_LAYERS


def test_sealring_boundary_matches_outer_size(generated_sealring):
    layout, top = generated_sealring
    boundary_bbox = _region_bbox(layout, top, *EDGESEAL_BOUNDARY)
    assert boundary_bbox.width() == 450000
    assert boundary_bbox.height() == 450000
