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
"""The PCell library loads and draws in a Python without tkinter.

The vendored cni runs the parameter callbacks (only cmim has one) through a
Tcl interpreter from tkinter. A Python can ship the tkinter package without
its _tkinter extension; there the library must still register and draw, and
the callback is skipped. Each run is a fresh interpreter that loads the
library the way plain-Python callers do (bump_mirror._ensure_pcell_lib) and
writes three cells: one run as this Python is, one with an import hook that
refuses _tkinter. Cells the callback does not change must be byte-equal
across the two runs, and one it does change must differ, so the callback is
shown to still run where tkinter is there.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1] / "python"

SCRIPT = textwrap.dedent("""\
    import importlib.abc
    import sys

    blocked, python_dir, out_dir = sys.argv[1:4]


    class Refuse(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == blocked:
                raise ImportError(f"No module named {name!r}")
            return None


    if blocked:
        sys.meta_path.insert(0, Refuse())
    sys.path.insert(0, python_dir)
    import pya
    import bump_mirror

    bump_mirror._ensure_pcell_lib()
    cells = {
        "cmim": ("cmim", {"Calculate": "C", "w": "10u", "l": "5u"}),
        "cmim_wl": ("cmim", {"w": "10u", "l": "5u"}),
        "CuPillarPad": ("CuPillarPad", {}),
        "SolderBumpPad": ("SolderBumpPad", {}),
    }
    for key, (name, params) in cells.items():
        layout = pya.Layout()
        layout.dbu = 0.001
        layout.technology_name = "intm4tm2"
        cell = layout.create_cell(name, "IntM4TM2", params)
        top = layout.create_cell("TOP")
        top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))
        opts = pya.SaveLayoutOptions()
        opts.write_context_info = False
        opts.gds2_write_timestamps = False
        layout.write(f"{out_dir}/{key}.gds", opts)
    print("tkinter loaded:", "tkinter" in sys.modules)
""")


def _draw(tmp_path, blocked):
    """Draw the cells in a fresh interpreter; return (output dir, stdout)."""
    out_dir = tmp_path / (blocked or "reference")
    out_dir.mkdir()
    home = tmp_path / "klayout_home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, KLAYOUT_HOME=str(home))
    env.pop("KLAYOUT_PATH", None)
    env.pop("KLAYOUT_LYP_FILE", None)
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, blocked, str(PYTHON_DIR), str(out_dir)],
        capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return out_dir, result.stdout


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    # Refusing '_tkinter' is the field case: the package is there, its
    # extension is not, so tkinter/__init__ starts and must not stay loaded.
    tmp_path = tmp_path_factory.mktemp("tkinter")
    return _draw(tmp_path, ""), _draw(tmp_path, "_tkinter")


def test_pcells_draw_without_tkinter(runs):
    (reference, _), (out_dir, stdout) = runs
    assert "tkinter loaded: False" in stdout, stdout
    for name in ("cmim", "CuPillarPad", "SolderBumpPad"):
        drawn = (out_dir / f"{name}.gds").read_bytes()
        assert drawn == (reference / f"{name}.gds").read_bytes(), (
            f"{name} drawn without tkinter differs from the reference")


def test_cmim_callback_runs_with_tkinter(runs):
    # With the default Calculate the callback sizes the plate from C, so the
    # 10 x 5 um given is not what gets drawn; without tkinter it is.
    (reference, stdout), (out_dir, _) = runs
    if "tkinter loaded: True" not in stdout:
        pytest.skip("this Python has no tkinter")
    drawn = (out_dir / "cmim_wl.gds").read_bytes()
    assert drawn != (reference / "cmim_wl.gds").read_bytes(), (
        "the cmim callback did not run with tkinter available")
