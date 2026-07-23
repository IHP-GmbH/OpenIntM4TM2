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
"""Regression for the cap_cmim KiCad symbol library (libs.tech/kicad/symbols).

The library is a HYBRID set: one generic base symbol ``cap_cmim`` plus one
value-keyed derived symbol per round capacitance (CMIM_10fF ... CMIM_5pF).

The BASE symbol must stay electrically equivalent to the xschem symbol
(libs.tech/xschem/intm4tm2_pr/cap_cmim.sym, pinned by test_cmim_symbol.py):
same model (SUBCKT cap_cmim), same pin order (PLUS first / MINUS second) and
a default footprint that points at an existing family member so the
schematic and the generated footprints cannot drift apart.

Each DERIVED symbol must ``(extends "cap_cmim")`` (so it inherits the base
pins/graphic and never redefines pins), point its Footprint at the matching
value-keyed footprint that actually exists on disk, carry the grid-snapped
w == l for that value (per the authoritative capacitance model) and a
displayed capacitance whose value the square-cap formula reproduces from w
to within 1 %.

The .kicad_sym file is a well-formed S-expression, so the checks parse it
directly with a tiny stdlib tokenizer (no sexpdata / kiutils dependency).
"""

from pathlib import Path

import pytest

LIBS_TECH = Path(__file__).resolve().parents[2]
SYM_PATH = LIBS_TECH / "kicad" / "symbols" / "cap_cmim.kicad_sym"
FOOTPRINT_DIR = LIBS_TECH / "kicad" / "footprints" / "intm4tm2.pretty"

BASE_NAME = "cap_cmim"

# Authoritative round-capacitance family: (symbol name, grid-snapped w == l in
# METRES, nominal capacitance in fF). The widths are the exact grid-snapped
# square-cap solutions and are used verbatim -- never recomputed here -- so the
# test locks the file to the published values instead of re-deriving them.
FAMILY = [
    ("CMIM_10fF", 2.53e-6, 10.0),
    ("CMIM_20fF", 3.6e-6, 20.0),
    ("CMIM_50fF", 5.72e-6, 50.0),
    ("CMIM_100fF", 8.11e-6, 100.0),
    ("CMIM_200fF", 11.495e-6, 200.0),
    ("CMIM_500fF", 18.205e-6, 500.0),
    ("CMIM_1pF", 25.765e-6, 1000.0),
    ("CMIM_2pF", 36.46e-6, 2000.0),
    ("CMIM_5pF", 57.68e-6, 5000.0),
]
FAMILY_NAMES = {name for name, _, _ in FAMILY}


def cap_model_fF(w_um):
    """Square-cap capacitance model: C[fF] = 1.5*w^2 + 0.16*w (w in um)."""
    return 1.5 * w_um * w_um + 0.16 * w_um


def parse_cap_to_fF(text):
    """Parse a human capacitance display ('10 fF', '1 pF') to femtofarads."""
    parts = str(text).split()
    value = float(parts[0])
    unit = (parts[1] if len(parts) > 1 else "fF").strip().lower()
    if unit == "pf":
        return value * 1000.0
    if unit == "ff":
        return value
    raise ValueError(f"unrecognised capacitance unit in {text!r}")


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
        for child_node in node:
            if isinstance(child_node, list):
                out.extend(find_all(child_node, head))
    return out


def child(node, head):
    for c in node:
        if isinstance(c, list) and c and c[0] == head:
            return c
    return None


def properties(node):
    props = {}
    for prop in find_all(node, "property"):
        if len(prop) >= 3 and isinstance(prop[1], str):
            props[prop[1]] = prop[2]
    return props


def top_level_symbols(root):
    """Direct (symbol ...) children of the library root.

    Excludes the graphic sub-units nested inside a symbol (cap_cmim_0_1,
    cap_cmim_1_1), which are not direct children of the library root."""
    return [c for c in root
            if isinstance(c, list) and c and c[0] == "symbol"]


def symbol_by_name(root, name):
    for sym in top_level_symbols(root):
        if len(sym) > 1 and sym[1] == name:
            return sym
    return None


@pytest.fixture(scope="module")
def lib():
    assert SYM_PATH.is_file(), f"missing KiCad symbol: {SYM_PATH}"
    return parse_sexp(SYM_PATH.read_text())


@pytest.fixture(scope="module")
def base(lib):
    sym = symbol_by_name(lib, BASE_NAME)
    assert sym is not None, f"missing base symbol {BASE_NAME!r}"
    return sym


# ---------------------------------------------------------------------------
# BASE symbol
# ---------------------------------------------------------------------------
def test_single_base_symbol(lib):
    named = [s for s in top_level_symbols(lib)
             if len(s) > 1 and s[1] == BASE_NAME]
    assert len(named) == 1, "expected exactly one top-level 'cap_cmim' symbol"


def test_base_two_pins_plus_first_minus_second(base):
    pins = find_all(base, "pin")
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


def test_base_subckt_model(base):
    props = properties(base)
    assert props.get("Sim.Device") == "SUBCKT", (
        "model must be a SUBCKT, not a plain C primitive")
    assert props.get("Sim.Name") == "cap_cmim", (
        f"sim model name must be cap_cmim, got {props.get('Sim.Name')}")


def test_base_default_parameters(base):
    props = properties(base)
    # Base defaults re-anchored to the 100fF family member (w = l = 8.11 um).
    for key, expected in (("w", 8.11e-6), ("l", 8.11e-6), ("m", 1.0)):
        assert key in props, f"missing default field {key}"
        assert float(props[key]) == pytest.approx(expected, abs=1e-12), (
            f"default {key}={props[key]} must equal {expected}")


def test_base_sim_params_reference_defaults(base):
    props = properties(base)
    sim_params = props.get("Sim.Params", "")
    for token in ("w=", "l=", "m="):
        assert token in sim_params, (
            f"Sim.Params must pass {token} to the subckt, got {sim_params!r}")


def test_base_sim_library_references_cornercap(base):
    props = properties(base)
    lib_ref = props.get("Sim.Library", "")
    assert "cornerCAP.lib" in lib_ref, (
        f"Sim.Library must reference cornerCAP.lib, got {lib_ref!r}")
    assert not lib_ref.startswith("/home"), (
        f"Sim.Library must be portable (no absolute path), got {lib_ref!r}")


def test_base_footprint_points_at_existing_family_member(base):
    props = properties(base)
    footprint = props.get("Footprint", "")
    assert footprint.startswith("intm4tm2:"), (
        f"default Footprint must be an intm4tm2 library ref, got {footprint!r}")
    name = footprint.split(":", 1)[1]
    assert name in FAMILY_NAMES, (
        f"default footprint {name!r} must be a value-keyed family member")
    assert (FOOTPRINT_DIR / (name + ".kicad_mod")).is_file(), (
        f"default footprint {name}.kicad_mod not found in {FOOTPRINT_DIR}")


def test_base_capacitance_display_matches_model(base):
    props = properties(base)
    cap = props.get("Capacitance")
    assert cap, "the base symbol must carry a Capacitance display value"
    nominal_fF = parse_cap_to_fF(cap)
    w_um = float(props["w"]) * 1e6
    recomputed = cap_model_fF(w_um)
    assert abs(recomputed - nominal_fF) <= 0.01 * nominal_fF, (
        f"base capacitance {cap!r} ({nominal_fF} fF) not within 1% of the "
        f"model value {recomputed:.4f} fF for w={w_um} um")


# ---------------------------------------------------------------------------
# PRESENTATION: polarity clarity + no clutter (the symbol-picker fixes).
# ---------------------------------------------------------------------------
def test_base_pin_names_hidden(base):
    """Pin NAME text must be hidden: the vertical PLUS/MINUS labels overlapped
    the body and were unreadable. Polarity is shown by +/- graphics instead."""
    pn = child(base, "pin_names")
    assert pn is not None, "base symbol lost its (pin_names ...) block"
    hide = child(pn, "hide")
    assert hide is not None and hide[1] == "yes", (
        "pin_names must be hidden (hide yes) so the PLUS/MINUS text stops "
        "overlapping the symbol body")


def test_base_has_plus_and_minus_markers(base):
    """The base graphic must carry a '+' above the plates (PLUS/top) and a
    '-' below them (MINUS/bottom) so the polarity is unambiguous. The plate
    strokes sit at y = +/-0.762, so real markers are the strokes with |y|>1."""
    ys = []
    for poly in find_all(base, "polyline"):
        pts = child(poly, "pts")
        if not pts:
            continue
        for xy in find_all(pts, "xy"):
            ys.append(float(xy[2]))
    assert any(y > 1.0 for y in ys), "no PLUS ('+') marker above the plates"
    assert any(y < -1.0 for y in ys), "no MINUS ('-') marker below the plates"


def test_all_noise_fields_hidden(lib):
    """w/l/m/Capacitance are sim/detail fields; they must not render on the
    symbol face (they showed as raw '57.68e-6' / '1' clutter before)."""
    for sym in top_level_symbols(lib):
        for prop in find_all(sym, "property"):
            if len(prop) >= 2 and prop[1] in ("w", "l", "m", "Capacitance"):
                eff = child(prop, "effects")
                hide = child(eff, "hide") if eff else None
                assert hide is not None and hide[1] == "yes", (
                    f"{sym[1]}: property {prop[1]} must be hidden (hide yes)")


# ---------------------------------------------------------------------------
# DERIVED symbols (one value-keyed symbol per round capacitance)
# ---------------------------------------------------------------------------
def test_family_derived_symbols_present(lib):
    derived = [s[1] for s in top_level_symbols(lib)
               if len(s) > 1 and s[1] != BASE_NAME]
    assert set(derived) == FAMILY_NAMES, (
        f"derived symbol set must be exactly {sorted(FAMILY_NAMES)}, "
        f"got {sorted(derived)}")


@pytest.mark.parametrize("name,exp_w_m,nominal_fF", FAMILY,
                         ids=[n for n, _, _ in FAMILY])
def test_derived_extends_base(lib, name, exp_w_m, nominal_fF):
    sym = symbol_by_name(lib, name)
    assert sym is not None, f"missing derived symbol {name!r}"
    ext = child(sym, "extends")
    assert ext is not None and ext[1] == BASE_NAME, (
        f"{name} must (extends \"{BASE_NAME}\"), got {ext}")


@pytest.mark.parametrize("name,exp_w_m,nominal_fF", FAMILY,
                         ids=[n for n, _, _ in FAMILY])
def test_derived_does_not_redefine_pins(lib, name, exp_w_m, nominal_fF):
    sym = symbol_by_name(lib, name)
    pins = find_all(sym, "pin")
    assert pins == [], (
        f"{name} must inherit pins from the base, not redefine them "
        f"(found {len(pins)} pin blocks)")


@pytest.mark.parametrize("name,exp_w_m,nominal_fF", FAMILY,
                         ids=[n for n, _, _ in FAMILY])
def test_derived_footprint_exists(lib, name, exp_w_m, nominal_fF):
    sym = symbol_by_name(lib, name)
    props = properties(sym)
    footprint = props.get("Footprint", "")
    assert footprint == f"intm4tm2:{name}", (
        f"{name} Footprint must be intm4tm2:{name}, got {footprint!r}")
    assert (FOOTPRINT_DIR / (name + ".kicad_mod")).is_file(), (
        f"{name}.kicad_mod referenced by the symbol not found in "
        f"{FOOTPRINT_DIR}")


@pytest.mark.parametrize("name,exp_w_m,nominal_fF", FAMILY,
                         ids=[n for n, _, _ in FAMILY])
def test_derived_wl_and_m(lib, name, exp_w_m, nominal_fF):
    sym = symbol_by_name(lib, name)
    props = properties(sym)
    for key in ("w", "l"):
        assert key in props, f"{name} missing {key}"
    w = float(props["w"])
    l = float(props["l"])
    # Exact grid-snapped metres (tolerance well under the 1e-9 requirement).
    assert w == pytest.approx(exp_w_m, abs=1e-12), (
        f"{name} w={props['w']} must equal {exp_w_m} m")
    assert l == pytest.approx(exp_w_m, abs=1e-12), (
        f"{name} l={props['l']} must equal {exp_w_m} m")
    assert w == l, f"{name} must be a square cap (w == l), got {w} != {l}"
    assert float(props.get("m")) == pytest.approx(1.0), (
        f"{name} m must be 1, got {props.get('m')}")


@pytest.mark.parametrize("name,exp_w_m,nominal_fF", FAMILY,
                         ids=[n for n, _, _ in FAMILY])
def test_derived_capacitance_within_1pct(lib, name, exp_w_m, nominal_fF):
    sym = symbol_by_name(lib, name)
    props = properties(sym)
    displayed = props.get("Capacitance")
    assert displayed, f"{name} must carry a Capacitance display value"
    # The displayed value must be the family's nominal for this member.
    assert parse_cap_to_fF(displayed) == pytest.approx(nominal_fF), (
        f"{name} displayed capacitance {displayed!r} != nominal "
        f"{nominal_fF} fF")
    # And the square-cap model, recomputed from w, must land within 1 %.
    w_um = float(props["w"]) * 1e6
    recomputed = cap_model_fF(w_um)
    assert abs(recomputed - nominal_fF) <= 0.01 * nominal_fF, (
        f"{name}: model C={recomputed:.4f} fF from w={w_um} um is not within "
        f"1% of nominal {nominal_fF} fF")
