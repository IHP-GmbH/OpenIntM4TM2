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
"""Regression for the cap_cmim xschem symbol (intm4tm2_pr).

The symbol is a verbatim copy of the cap_cmim symbol from the
IHP-Open-PDK (SG13G2 open PDK); the MIM module of the interposer stack
keeps the same model name, parameters and pin order, so nothing needs
to differ. Two things are pinned here:

1. Static contract (always on): the .sym file is parsed directly and
   the netlisting attributes are asserted -- type=capacitor, the
   format/lvs_format strings that produce the subckt instance line,
   the template defaults (model=cap_cmim, w/l/m/mm_ok/spiceprefix) and
   the two pins c0/c1 in that order (c0 = top plate, c1 = bottom
   plate). This keeps the contract pinned even where xschem is not
   installed.
2. Headless netlist (xschem-gated): a minimal schematic instantiating
   the committed symbol with two named nets is netlisted in batch mode
   and the produced .spice must contain the subckt instance line
   'XC1 PLUS MINUS cap_cmim w=... l=... m=...' (X name prefix from
   spiceprefix=X, both nets in pin order, model and parameters).

The net labels use a minimal label symbol written into the temp dir by
the test itself, so the run does not depend on the xschem system
library search path or on any user configuration (HOME is pointed at
the temp dir).
"""

import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

LIBS_TECH = Path(__file__).resolve().parents[2]
SYM_PATH = LIBS_TECH / "xschem" / "intm4tm2_pr" / "cap_cmim.sym"

XSCHEM_BIN = shutil.which("xschem")

EXPECTED_FORMAT = "@spiceprefix@name @pinlist @model w=@w l=@l m=@m mm_ok=@mm_ok"
EXPECTED_LVS_FORMAT = "C@name @pinlist @model w=@w l=@l m=@m"
EXPECTED_TEMPLATE_TOKENS = {
    "name": "C1",
    "model": "cap_cmim",
    "w": "7.0e-6",
    "l": "7.0e-6",
    "m": "1",
    "mm_ok": "1",
    "spiceprefix": "X",
}

# Minimal net-label symbol (type=label, format="*.alias @lab") written
# into the temp dir so the testbench schematic is self-contained.
NETLABEL_SYM = textwrap.dedent("""\
    v {xschem version=3.4.8RC file_version=1.3}
    G {}
    K {type=label
    format="*.alias @lab"
    template="name=l1 lab=net"
    }
    V {}
    S {}
    E {}
    B 5 -1.25 -1.25 1.25 1.25 {name=p dir=inout}
    T {@lab} 5 -5 0 0 0.3 0.3 {}
    """)

# Testbench: one cap_cmim instance, net labels placed directly on the
# two symbol pins (c0 at (0,-30), c1 at (0,30) in symbol coordinates).
TESTBENCH_SCH = textwrap.dedent("""\
    v {{xschem version=3.4.8RC file_version=1.3}}
    G {{}}
    K {{}}
    V {{}}
    S {{}}
    E {{}}
    C {{{sym_path}}} 0 0 0 0 {{name=C1 model=cap_cmim w=7.0e-6 l=7.0e-6 m=1 mm_ok=1 spiceprefix=X}}
    C {{netlabel.sym}} 0 -30 0 0 {{name=l1 lab=PLUS}}
    C {{netlabel.sym}} 0 30 0 0 {{name=l2 lab=MINUS}}
    """)


def _global_attrs(sym_text):
    """Returns the K {...} global attribute block of a .sym file."""
    match = re.search(r"^K \{(.*?)^\}", sym_text, re.MULTILINE | re.DOTALL)
    assert match, "no K {...} global attribute block found"
    return match.group(1)


def _quoted_attr(block, name):
    # (?<!\w) so that 'format' does not match inside 'lvs_format'.
    match = re.search(r"(?<!\w)" + name + r'="(.*?)"', block, re.DOTALL)
    assert match, f"attribute {name} not found in K block"
    return match.group(1)


def test_cap_cmim_symbol_contract():
    """Static parse of the committed .sym: netlist attributes and pins."""
    assert SYM_PATH.is_file(), f"missing symbol: {SYM_PATH}"
    text = SYM_PATH.read_text()
    attrs = _global_attrs(text)

    assert "type=capacitor" in attrs

    fmt = _quoted_attr(attrs, "format")
    assert fmt == EXPECTED_FORMAT
    # The instance name prefix comes from spiceprefix (X), the model
    # (cap_cmim) from the template; both are asserted below. The format
    # string itself must reference them and the w/l/m parameters.
    for token in ("@spiceprefix", "@name", "@pinlist", "@model",
                  "w=@w", "l=@l", "m=@m"):
        assert token in fmt, f"format string lost {token}"

    lvs_fmt = _quoted_attr(attrs, "lvs_format")
    assert lvs_fmt == EXPECTED_LVS_FORMAT
    for token in ("@pinlist", "@model", "w=@w", "l=@l", "m=@m"):
        assert token in lvs_fmt, f"lvs_format string lost {token}"

    template = _quoted_attr(attrs, "template")
    template_tokens = dict(item.split("=", 1)
                           for item in template.split())
    assert template_tokens == EXPECTED_TEMPLATE_TOKENS

    # Pin boxes: layer-5 B lines define the pins; their file order is
    # the @pinlist order. c0 is the top plate lead, c1 the bottom.
    pins = re.findall(r"^B 5 .*\{name=(\w+) dir=(\w+)\}", text,
                      re.MULTILINE)
    assert pins == [("c0", "inout"), ("c1", "inout")]

    # The on-canvas capacitance label must keep the computation
    # C = m * (w*l*1.5e-3 + 2*(w+l)*40e-12) (w/l in meters -> farads;
    # same as C[fF] = w*l*1.5 + 2*(w+l)*0.04 with w/l in um).
    assert "@m * (@w * @l * 1.5e-3 + 2*( @w + @l ) * 40e-12)" in text


@pytest.mark.skipif(XSCHEM_BIN is None, reason="xschem binary not on PATH")
def test_cap_cmim_symbol_netlists():
    """Headless xschem batch netlist of a schematic using the symbol."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "netlabel.sym").write_text(NETLABEL_SYM)
        sch = tmp_dir / "cap_cmim_tb.sch"
        sch.write_text(TESTBENCH_SCH.format(sym_path=SYM_PATH))

        # HOME at the temp dir keeps ~/.xschem/xschemrc out of the run;
        # cwd at the temp dir keeps any ./xschemrc of the repo out too.
        env = dict(os.environ, HOME=str(tmp_dir))
        result = subprocess.run(
            [XSCHEM_BIN, "--netlist", "--quit", "--no_x", "--spice",
             "-o", str(tmp_dir), "-N", "cap_cmim_tb.spice", str(sch)],
            capture_output=True, text=True, cwd=str(tmp_dir), env=env,
            timeout=120)
        assert result.returncode == 0, (
            f"xschem batch failed:\n{result.stdout}\n{result.stderr}")

        spice = tmp_dir / "cap_cmim_tb.spice"
        assert spice.is_file(), (
            f"netlist not produced:\n{result.stdout}\n{result.stderr}")
        netlist = spice.read_text()

        instances = [line for line in netlist.splitlines()
                     if line.startswith("X")]
        assert len(instances) == 1, (
            f"expected exactly one subckt instance line:\n{netlist}")

        tokens = instances[0].split()
        # X<name> <plus net> <minus net> cap_cmim w=... l=... m=... mm_ok=...
        assert tokens[0] == "XC1"
        assert tokens[1:3] == ["PLUS", "MINUS"], (
            f"net/pin order mismatch: {instances[0]}")
        assert tokens[3] == "cap_cmim"
        params = dict(item.split("=", 1) for item in tokens[4:])
        assert params.get("w") == "7.0e-6"
        assert params.get("l") == "7.0e-6"
        assert params.get("m") == "1"
