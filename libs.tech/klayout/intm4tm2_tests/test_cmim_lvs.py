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
"""LVS regression for cap_cmim MIM capacitor device extraction.

Builds a cmim PCell layout (intm4tm2_pycell_lib) in a headless klayout
batch, adds net labels on Metal5 (67/25) and TopMetal1 (126/25) over the
capacitor plates (the PCell stores pins as metadata only, so the LVS
nets are named from text labels), then drives the IntM4TM2 LVS through
tech/lvs/run_lvs.py and checks:

1. Extraction: exactly one cap_cmim device whose w/l are within 1 nm of
   the drawn MIM plate size and whose terminals sit on the two labeled
   nets (read back from the LVS report database).
2. Compare: a matching reference netlist passes KLayout netlist compare.
   Both accepted cap_cmim netlist forms are exercised:
   'C1 PLUS MINUS cap_cmim w=.. l=.. m=..' and the value-first
   'C1 PLUS MINUS <value> $[cap_cmim] w=.. l=..'.
3. Negative controls: a reference with a wrong plate size and one with
   both capacitor terminals shorted must FAIL the compare.

The device compare checks the A/P/m parameters (w/l are informational,
upstream IHP-Open-PDK convention, so a device rotated by 90 degrees
still matches); the wrong-size negative control therefore changes the
plate area, not just the w/l orientation.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import klayout.db as kdb

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"
RUN_LVS = REPO_KLAYOUT / "tech" / "lvs" / "run_lvs.py"

KLAYOUT_BIN = shutil.which("klayout")

# Drawn MIM plate size (um) and the testbench cell name
CAP_W = 10.0
CAP_L = 5.0
TOPCELL = "CMIM_TB"

GEN_SCRIPT = textwrap.dedent("""\
    import sys
    import pya

    # Register the technology from the .lyt so the batch session
    # resolves the PCell library and its layer table.
    if {tech_name!r} in pya.Technology.technology_names():
        tech = pya.Technology.technology_by_name({tech_name!r})
    else:
        tech = pya.Technology.create_technology({tech_name!r})
    tech.load({lyt_path!r})

    sys.path.insert(0, {python_dir!r})
    sys.path.insert(0, {api_dir!r})

    import intm4tm2_pycell_lib  # registers the PCell library

    layout = pya.Layout()
    layout.dbu = 0.001
    layout.technology_name = {tech_name!r}
    cell = layout.create_cell("cmim", "IntM4TM2",
                              {{"Calculate": "C", "w": "10u", "l": "5u"}})
    assert cell is not None, "create_cell returned None"
    top = layout.create_cell({topcell!r})
    top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))

    # Net labels over the plates. The Metal5 bottom plate extends 0.6 um
    # beyond the MIM plate, so a label just outside the MIM corner lands
    # on Metal5 only; the TopMetal1 top plate covers the plate center.
    top.shapes(layout.layer(67, 25)).insert(
        pya.Text("MINUS", pya.Trans(pya.Point(-300, -300))))
    top.shapes(layout.layer(126, 25)).insert(
        pya.Text("PLUS", pya.Trans(pya.Point(5000, 2500))))

    opts = pya.SaveLayoutOptions()
    opts.write_context_info = False
    layout.write({gds_path!r}, opts)
""")

REF_GOOD = """\
.SUBCKT CMIM_TB PLUS MINUS
C1 PLUS MINUS cap_cmim w=10u l=5u m=1
.ENDS CMIM_TB
"""

# Same device, value-first form as written by the KLayout SPICE writer
REF_GOOD_VALUE_FIRST = """\
.SUBCKT CMIM_TB PLUS MINUS
C1 PLUS MINUS 75f $[cap_cmim] w=10u l=5u m=1
.ENDS CMIM_TB
"""

# Negative control: wrong plate size (different area)
REF_BAD_SIZE = """\
.SUBCKT CMIM_TB PLUS MINUS
C1 PLUS MINUS cap_cmim w=5u l=5u m=1
.ENDS CMIM_TB
"""

# Negative control: both capacitor terminals on the same net
REF_BAD_SHORT = """\
.SUBCKT CMIM_TB PLUS MINUS
C1 PLUS PLUS cap_cmim w=10u l=5u m=1
.ENDS CMIM_TB
"""


def _batch_env(out_dir):
    empty_home = out_dir / "klayout_home"
    empty_home.mkdir(exist_ok=True)
    env = dict(os.environ, KLAYOUT_HOME=str(empty_home))
    # A stale KLAYOUT_LYP_FILE would replace the layer table with another
    # PDK's, and KLAYOUT_PATH would preload user technologies.
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)
    return env


def _generate_layout(out_dir):
    gds_path = out_dir / "cmim_tb.gds"
    script = out_dir / "gen_cmim_tb.py"
    script.write_text(GEN_SCRIPT.format(
        tech_name="intm4tm2",
        lyt_path=str(REPO_KLAYOUT / "tech" / "intm4tm2.lyt"),
        python_dir=str(PYTHON_DIR),
        api_dir=str(PYTHON_DIR / "pycell4klayout-api" / "source" / "python"),
        topcell=TOPCELL,
        gds_path=str(gds_path)))
    result = subprocess.run(
        [KLAYOUT_BIN, "-zz", "-rx", "-r", str(script)],
        capture_output=True, text=True, env=_batch_env(out_dir), timeout=600)
    assert result.returncode == 0, (
        f"klayout batch failed:\n{result.stdout}\n{result.stderr}")
    assert gds_path.is_file()
    return gds_path


def _generate_novia_layout(out_dir):
    """Manual cmim geometry WITHOUT the Vmim via array.

    The top-plate terminal then sits on a floating net while the PLUS
    label names a TopMetal1 net with no device on it -- the strict port
    compare (flag_missing_ports) must reject this."""
    layout = kdb.Layout()
    layout.dbu = 0.001
    top = layout.create_cell(TOPCELL)
    top.shapes(layout.layer(36, 0)).insert(kdb.Box(0, 0, 10000, 5000))
    top.shapes(layout.layer(67, 0)).insert(kdb.Box(-600, -600, 10600, 5600))
    top.shapes(layout.layer(126, 0)).insert(kdb.Box(500, 500, 9500, 4500))
    top.shapes(layout.layer(67, 25)).insert(
        kdb.Text("MINUS", kdb.Trans(kdb.Point(-300, -300))))
    top.shapes(layout.layer(126, 25)).insert(
        kdb.Text("PLUS", kdb.Trans(kdb.Point(5000, 2500))))
    path = out_dir / "cmim_tb_novia.gds"
    layout.write(str(path))
    return path


def _run_lvs(out_dir, gds_path, netlist_path, run_name):
    """Run tech/lvs/run_lvs.py and return (combined output, run_dir)."""
    run_dir = out_dir / run_name
    result = subprocess.run(
        [sys.executable, str(RUN_LVS),
         f"--layout={gds_path}",
         f"--netlist={netlist_path}",
         f"--run_dir={run_dir}"],
        capture_output=True, text=True, env=_batch_env(out_dir), timeout=600)
    out = result.stdout + result.stderr
    assert result.returncode == 0, f"run_lvs.py failed ({run_name}):\n{out}"
    return out, run_dir


@pytest.fixture(scope="module")
def cmim_lvs(tmp_path_factory):
    """Generate the cmim testbench and run all LVS compare cases once."""
    out_dir = tmp_path_factory.mktemp("cmim_lvs")
    gds_path = _generate_layout(out_dir)

    refs = {
        "good": REF_GOOD,
        "good_value_first": REF_GOOD_VALUE_FIRST,
        "bad_size": REF_BAD_SIZE,
        "bad_short": REF_BAD_SHORT,
    }
    runs = {}
    for name, text in refs.items():
        ref_path = out_dir / f"ref_{name}.cir"
        ref_path.write_text(text)
        out, run_dir = _run_lvs(out_dir, gds_path, ref_path, f"run_{name}")
        runs[name] = {"out": out, "run_dir": run_dir}

    # Via-less cmim vs the connected reference netlist
    novia_gds = _generate_novia_layout(out_dir)
    out, run_dir = _run_lvs(out_dir, novia_gds, out_dir / "ref_good.cir",
                            "run_novia")
    runs["novia"] = {"out": out, "run_dir": run_dir}
    return runs


pytestmark = pytest.mark.skipif(
    KLAYOUT_BIN is None, reason="klayout binary not on PATH")


def test_cmim_device_extraction(cmim_lvs):
    """Exactly one cap_cmim with drawn w/l on the labeled nets."""
    run_dir = cmim_lvs["good"]["run_dir"]
    lvsdb_path = run_dir / "cmim_tb.lvsdb"
    assert lvsdb_path.is_file(), "LVS report database not written"

    lvs = kdb.LayoutVsSchematic()
    lvs.read(str(lvsdb_path))
    netlist = lvs.netlist()

    devices = []
    for circuit in netlist.each_circuit():
        for device in circuit.each_device():
            devices.append(device)

    assert len(devices) == 1, (
        f"expected exactly one extracted device, got {len(devices)}")
    device = devices[0]
    assert device.device_class().name == "cap_cmim"

    # w/l within 1 nm of the drawn MIM plate
    assert abs(device.parameter("w") - CAP_W) <= 0.001
    assert abs(device.parameter("l") - CAP_L) <= 0.001
    assert abs(device.parameter("A") - CAP_W * CAP_L) <= 0.01
    assert abs(device.parameter("P") - 2 * (CAP_W + CAP_L)) <= 0.01

    # Terminals on the two labeled nets
    top_net = device.net_for_terminal("mim_top")
    btm_net = device.net_for_terminal("mim_btm")
    assert top_net is not None and btm_net is not None
    assert top_net.expanded_name() == "PLUS"
    assert btm_net.expanded_name() == "MINUS"


def test_cmim_compare_matches(cmim_lvs):
    """Matching reference netlists pass compare with no label findings."""
    for name in ("good", "good_value_first"):
        out = cmim_lvs[name]["out"]
        assert "LVS netlists match" in out, f"{name} did not match:\n{out}"
        assert "LVS netlists do NOT match" not in out
        assert "Connectivity OPEN" not in out
        assert "Connectivity SHORT" not in out

    # The extracted netlist must carry the device
    extracted = cmim_lvs["good"]["run_dir"] / "cmim_tb_extracted.cir"
    assert extracted.is_file()
    assert "cap_cmim" in extracted.read_text()


def test_cmim_compare_negative_controls(cmim_lvs):
    """Wrong plate size and shorted terminals must FAIL the compare."""
    for name in ("bad_size", "bad_short"):
        out = cmim_lvs[name]["out"]
        assert "LVS netlists do NOT match" in out, (
            f"negative control {name} unexpectedly passed:\n{out}")
        assert "LVS netlists match" not in out


def test_cmim_novia_floating_terminal_fails(cmim_lvs):
    """A cmim without its via must not match a connected schematic.

    The device still extracts, but its top-plate terminal lands on a
    floating net; only the strict port compare rejects it."""
    out = cmim_lvs["novia"]["out"]
    assert "LVS netlists do NOT match" in out, (
        f"via-less cmim unexpectedly matched a connected netlist:\n{out}")
    assert "LVS netlists match" not in out
