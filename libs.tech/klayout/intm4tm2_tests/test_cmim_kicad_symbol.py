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
"""Regression for the cap_cmim KiCad symbol (libs.tech/kicad/symbols).

The KiCad symbol must stay electrically equivalent to the xschem symbol
(libs.tech/xschem/intm4tm2_pr/cap_cmim.sym, pinned by test_cmim_symbol.py):
same model (SUBCKT cap_cmim), same pin order (PLUS first / MINUS second)
and the same default parameters (w = l = 7e-6, m = 1). It must also point
at the default discrete footprint family member so the schematic and the
generated footprints cannot drift apart.

The .kicad_sym file is a well-formed S-expression, so the checks parse it
directly with a tiny stdlib tokenizer (no sexpdata / kiutils dependency).
"""

from pathlib import Path

import pytest

LIBS_TECH = Path(__file__).resolve().parents[2]
SYM_PATH = LIBS_TECH / "kicad" / "symbols" / "cap_cmim.kicad_sym"
FOOTPRINT_DIR = LIBS_TECH / "kicad" / "footprints" / "intm4tm2.pretty"

# Default footprint the symbol must reference (KiCad "libnick:name" form).
DEFAULT_FOOTPRINT = "intm4tm2:CMIM_7x7um"


# ---------------------------------------------------------------------------
# Tiny tolerant S-expression tokenizer / parser (stdlib only).
# Quoted strings and bare atoms both collapse to Python str; parentheses
# inside quoted strings are ignored. Returns nested lists whose first
# element is the node head (e.g. "symbol", "pin", "property").
# ---------------------------------------------------------------------------
def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            tokens.append(c)
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                elif c == '"':
                    i += 1
                    break
                else:
                    buf.append(c)
                    i += 1
            tokens.append(("".join(buf),))     # 1-tuple marks a leaf string
        elif c.isspace():
            i += 1
        else:
            buf = []
            while i < n and not text[i].isspace() and text[i] not in '()"':
                buf.append(text[i])
                i += 1
            tokens.append(("".join(buf),))
    return tokens


def parse_sexp(text):
    tokens = _tokenize(text)
    pos = [0]

    def build():
        assert tokens[pos[0]] == "(", "expected '('"
        pos[0] += 1
        node = []
        while True:
            tok = tokens[pos[0]]
            if tok == "(":
                node.append(build())
            elif tok == ")":
                pos[0] += 1
                return node
            else:
                node.append(tok[0])
                pos[0] += 1

    # Skip to the first '(' (KiCad files start with it).
    while tokens[pos[0]] != "(":
        pos[0] += 1
    return build()


def find_all(node, head):
    """Depth-first list of all list-nodes whose head == head, in file order."""
    out = []
    if isinstance(node, list):
        if node and node[0] == head:
            out.append(node)
        for child in node:
            if isinstance(child, list):
                out.extend(find_all(child, head))
    return out


def child(node, head):
    for c in node:
        if isinstance(c, list) and c and c[0] == head:
            return c
    return None


def properties(root):
    props = {}
    for prop in find_all(root, "property"):
        if len(prop) >= 3 and isinstance(prop[1], str):
            props[prop[1]] = prop[2]
    return props


@pytest.fixture(scope="module")
def symbol():
    assert SYM_PATH.is_file(), f"missing KiCad symbol: {SYM_PATH}"
    return parse_sexp(SYM_PATH.read_text())


def test_single_cap_cmim_symbol(symbol):
    named = [s for s in find_all(symbol, "symbol") if len(s) > 1
             and s[1] == "cap_cmim"]
    assert len(named) == 1, "expected exactly one top-level 'cap_cmim' symbol"


def test_two_pins_plus_first_minus_second(symbol):
    pins = find_all(symbol, "pin")
    assert len(pins) == 2, f"expected exactly two pins, got {len(pins)}"

    seq = []
    for pin in pins:
        name = child(pin, "name")
        number = child(pin, "number")
        assert name is not None and number is not None
        seq.append((number[1], name[1]))

    # Document order: PLUS first, MINUS second (mirrors xschem c0/c1).
    assert [name for _, name in seq] == ["PLUS", "MINUS"], (
        f"pin order must be PLUS then MINUS, got {seq}")
    # Numeric pad identifiers lock SPICE node order: 1 -> PLUS, 2 -> MINUS.
    by_number = dict(seq)
    assert by_number.get("1") == "PLUS", f"pin 1 must be PLUS, got {seq}"
    assert by_number.get("2") == "MINUS", f"pin 2 must be MINUS, got {seq}"


def test_subckt_model(symbol):
    props = properties(symbol)
    assert props.get("Sim.Device") == "SUBCKT", (
        "model must be a SUBCKT, not a plain C primitive")
    assert props.get("Sim.Name") == "cap_cmim", (
        f"sim model name must be cap_cmim, got {props.get('Sim.Name')}")


def test_default_parameters(symbol):
    props = properties(symbol)
    for key, expected in (("w", 7e-6), ("l", 7e-6), ("m", 1.0)):
        assert key in props, f"missing default field {key}"
        # Numeric equality mirrors the electrical contract (7e-6 == 7.0e-6);
        # not a brittle string compare.
        assert float(props[key]) == pytest.approx(expected), (
            f"default {key}={props[key]} must equal {expected}")


def test_sim_params_reference_defaults(symbol):
    props = properties(symbol)
    sim_params = props.get("Sim.Params", "")
    for token in ("w=", "l=", "m="):
        assert token in sim_params, (
            f"Sim.Params must pass {token} to the subckt, got {sim_params!r}")


def test_footprint_points_at_default_family_member(symbol):
    props = properties(symbol)
    assert props.get("Footprint") == DEFAULT_FOOTPRINT, (
        f"Footprint must be {DEFAULT_FOOTPRINT}, got {props.get('Footprint')}")
    # Anti-divergence: the referenced footprint must actually exist.
    name = DEFAULT_FOOTPRINT.split(":", 1)[1]
    assert (FOOTPRINT_DIR / (name + ".kicad_mod")).is_file(), (
        f"default footprint {name}.kicad_mod not found in {FOOTPRINT_DIR}")


def test_capacitance_display_present(symbol):
    props = properties(symbol)
    cap = props.get("Capacitance")
    assert cap, "a Capacitance display value must be present"
    assert "fF" in cap, f"capacitance display should be in fF, got {cap!r}"
    value = float(cap.split()[0])
    # C[fF] = w*l*1.5 + 2*(w+l)*0.04 at w=l=7um -> 74.62 (shown as 74.6).
    assert value == pytest.approx(74.62, abs=0.1), (
        f"capacitance display {cap!r} does not match the formula (~74.6 fF)")


def test_sim_library_references_cornercap(symbol):
    props = properties(symbol)
    lib = props.get("Sim.Library", "")
    assert "cornerCAP.lib" in lib, (
        f"Sim.Library must reference cornerCAP.lib, got {lib!r}")
