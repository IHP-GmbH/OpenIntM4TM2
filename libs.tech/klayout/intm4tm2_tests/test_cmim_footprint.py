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
"""Regression + anti-divergence guard for the cmim KiCad footprints.

The discrete footprint family (libs.tech/kicad/footprints/intm4tm2.pretty)
is emitted by libs.tech/kicad/scripts/cmim_footprint_gen.py, which
re-implements the plate geometry of the cmim KLayout PCell. The family is
now keyed by round CAPACITANCE (CMIM_10fF ... CMIM_5pF). Three layers of
checks:

1. STRUCTURAL: every committed .kicad_mod parses as an S-expression and
   carries exactly two copper pads on the interposer layer map
   (pad "1" = PLUS = TopMetal1 = In1.Cu, pad "2" = MINUS = Metal5 = In2.Cu),
   both concentric, with positive sizes, plus an F.CrtYd courtyard.

2. GEOMETRY PARITY (the important one): for a couple of committed family
   members the DECLARED w/l are read from the footprint properties (never
   from the name), the real cmim PCell is generated headlessly for that
   (w, l), and the Metal5 (67/0) and TopMetal1 (126/0) bounding boxes read
   from the GDS must equal the footprint's pad "2" and pad "1" sizes within
   2 nm. This is the guard that stops the footprint generator from drifting
   away from the layout PCell it mirrors.

3. CAP SOLVER: the ``--cap`` mode is exercised end to end -- a target C is
   solved into a fresh footprint, the emitted w must reproduce the target
   capacitance, and its pads must match a fresh PCell run for that w.

The PCell steps skip cleanly when the klayout binary is not available (the
PCell registers through pya, so it needs a batch run).
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
LIBS_TECH = Path(__file__).resolve().parents[2]
PYTHON_DIR = REPO_KLAYOUT / "python"
FOOTPRINT_DIR = LIBS_TECH / "kicad" / "footprints" / "intm4tm2.pretty"
GEN_SCRIPT = LIBS_TECH / "kicad" / "scripts" / "cmim_footprint_gen.py"

KLAYOUT_BIN = shutil.which("klayout")

# Interposer MIM layer map (same numbers as the SG13G2 open PDK).
METAL5 = (67, 0)      # MINUS / bottom plate -> footprint pad "2" / In2.Cu
TOPMETAL1 = (126, 0)  # PLUS  / top plate    -> footprint pad "1" / In1.Cu

PLUS_LAYER = "In1.Cu"
MINUS_LAYER = "In2.Cu"

# Parity tolerance: the footprint emits nm-resolution mm; the layout is on a
# 1 nm dbu grid. 2 nm covers rounding on both sides.
TOL_UM = 0.002

# The value-keyed family, exactly (no leftover dimension-keyed footprints).
EXPECTED_FAMILY = {
    "CMIM_10fF", "CMIM_20fF", "CMIM_50fF", "CMIM_100fF", "CMIM_200fF",
    "CMIM_500fF", "CMIM_1pF", "CMIM_2pF", "CMIM_5pF",
}

# Committed members whose pads are checked against a fresh PCell run. One
# small (100 fF) and one large (1 pF) member exercise different via arrays.
PARITY_MEMBERS = ["CMIM_100fF", "CMIM_1pF"]


def cap_model_fF(w_um):
    """Square-cap capacitance model: C[fF] = 1.5*w^2 + 0.16*w (w in um)."""
    return 1.5 * w_um * w_um + 0.16 * w_um


# ---------------------------------------------------------------------------
# Tiny tolerant S-expression tokenizer / parser (stdlib only).
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

    while tokens[pos[0]] != "(":
        pos[0] += 1
    return build()


def find_all(node, head):
    out = []
    if isinstance(node, list):
        if node and node[0] == head:
            out.append(node)
        for c in node:
            if isinstance(c, list):
                out.extend(find_all(c, head))
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


def pad_info(footprint):
    """Return {number: {'layer', 'size': (w, h), 'at': (x, y)}} for each pad."""
    pads = {}
    for pad in find_all(footprint, "pad"):
        number = pad[1]
        size = child(pad, "size")
        at = child(pad, "at")
        layers = child(pad, "layers")
        assert size is not None, f"pad {number} has no (size ...)"
        assert at is not None, f"pad {number} has no (at ...)"
        assert layers is not None, f"pad {number} has no (layers ...)"
        pads[number] = {
            "layer": layers[1],
            "size": (float(size[1]), float(size[2])),
            "at": (float(at[1]), float(at[2])),
        }
    return pads


def _um_from_prop(text):
    """Parse a 'w'/'l' property ('8.11um' / '12.855um') to micrometres."""
    t = str(text).strip().lower()
    if t.endswith("um"):
        t = t[:-2]
    elif t.endswith("u"):
        t = t[:-1]
    return float(t)


def footprint_declared_wl_um(footprint):
    """Read the DECLARED plate w/l (um) from the footprint properties.

    Deliberately reads the 'w'/'l' properties, not the footprint name, so the
    parity check pins the emitted geometry to the recorded dimensions."""
    props = properties(footprint)
    assert "w" in props and "l" in props, (
        f"footprint missing declared w/l properties: {sorted(props)}")
    return _um_from_prop(props["w"]), _um_from_prop(props["l"])


def _mod_files():
    return sorted(FOOTPRINT_DIR.glob("*.kicad_mod"))


# ---------------------------------------------------------------------------
# STRUCTURAL checks (no klayout needed): run on every committed footprint.
# ---------------------------------------------------------------------------
def test_footprint_family_present():
    files = _mod_files()
    assert files, f"no .kicad_mod files found in {FOOTPRINT_DIR}"


def test_footprint_family_is_capacitance_keyed():
    stems = {p.stem for p in _mod_files()}
    assert stems == EXPECTED_FAMILY, (
        f"footprint family must be exactly the value-keyed members "
        f"{sorted(EXPECTED_FAMILY)}, got {sorted(stems)}")


@pytest.mark.parametrize("mod_path", _mod_files(), ids=lambda p: p.stem)
def test_footprint_structure(mod_path):
    footprint = parse_sexp(mod_path.read_text())
    assert footprint[0] == "footprint", f"{mod_path.name} is not a footprint"

    pads = pad_info(footprint)
    assert set(pads) == {"1", "2"}, (
        f"{mod_path.name} must have exactly pads '1' and '2', got {set(pads)}")

    assert pads["1"]["layer"] == PLUS_LAYER, (
        f"{mod_path.name} pad 1 (PLUS) must be on {PLUS_LAYER}")
    assert pads["2"]["layer"] == MINUS_LAYER, (
        f"{mod_path.name} pad 2 (MINUS) must be on {MINUS_LAYER}")

    for num in ("1", "2"):
        w, h = pads[num]["size"]
        assert w > 0 and h > 0, (
            f"{mod_path.name} pad {num} must have positive size, got {(w, h)}")

    # Concentric: both pads share the same centre.
    assert pads["1"]["at"] == pads["2"]["at"], (
        f"{mod_path.name} pads must be concentric, "
        f"{pads['1']['at']} != {pads['2']['at']}")

    # MINUS (Metal5) is the larger outer plate; PLUS (TopMetal1) sits inside.
    assert pads["2"]["size"][0] >= pads["1"]["size"][0], (
        f"{mod_path.name}: Metal5 pad must be at least as wide as TopMetal1")
    assert pads["2"]["size"][1] >= pads["1"]["size"][1], (
        f"{mod_path.name}: Metal5 pad must be at least as tall as TopMetal1")

    courtyards = [r for r in find_all(footprint, "fp_rect")
                  if child(r, "layer") and child(r, "layer")[1] == "F.CrtYd"]
    assert courtyards, f"{mod_path.name} has no F.CrtYd courtyard"


def _fp_texts(footprint):
    """Return [(text, layer), ...] for every (fp_text user "..." ...) element."""
    out = []
    for t in find_all(footprint, "fp_text"):
        text = t[2] if len(t) > 2 and isinstance(t[2], str) else None
        layer_node = child(t, "layer")
        out.append((text, layer_node[1] if layer_node else None))
    return out


@pytest.mark.parametrize("mod_path", _mod_files(), ids=lambda p: p.stem)
def test_footprint_polarity_layer_guide(mod_path):
    """Every footprint must carry the connection guide: a readable +/- polarity
    and upper/lower metal label on silk, plus a fab legend mapping each
    polarity to its KiCad copper layer. The real copper stays the two
    concentric plate pads (checked by test_footprint_structure); this only
    pins the annotation so the usability guide cannot silently regress."""
    footprint = parse_sexp(mod_path.read_text())
    texts = _fp_texts(footprint)
    silk = {t for t, layer in texts if layer == "F.SilkS"}
    fab = " ".join(t for t, layer in texts if layer == "F.Fab" and t)
    assert "+ TopMetal1" in silk, (
        f"{mod_path.name} missing the '+ TopMetal1' silk label, got {silk}")
    assert "- Metal5" in silk, (
        f"{mod_path.name} missing the '- Metal5' silk label, got {silk}")
    assert "In1.Cu" in fab and "In2.Cu" in fab, (
        f"{mod_path.name} fab legend must map PLUS/MINUS to In1.Cu/In2.Cu, "
        f"got {fab!r}")


# ---------------------------------------------------------------------------
# GEOMETRY PARITY: footprint pad sizes vs. real PCell plate bounding boxes.
# ---------------------------------------------------------------------------
BATCH_SCRIPT = textwrap.dedent("""\
    import sys
    import pya

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
        cell = layout.create_cell("cmim", "IntM4TM2", params)
        assert cell is not None, "create_cell returned None for " + name
        top = layout.create_cell("TOP")
        top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))
        opts = pya.SaveLayoutOptions()
        opts.write_context_info = False
        layout.write({out_dir!r} + "/pcell_" + name + ".gds", opts)
""")


def _batch_env(out_dir):
    empty_home = out_dir / "klayout_home"
    empty_home.mkdir(exist_ok=True)
    env = dict(os.environ, KLAYOUT_HOME=str(empty_home))
    # Keep any user PDK's layer table / technologies out of the batch.
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)
    return env


def _generate_pcell(out_dir, cases):
    """Run the headless PCell for cases = [(name, param_dict), ...]."""
    script = out_dir / "gen_cmim.py"
    script.write_text(BATCH_SCRIPT.format(
        lyt_path=str(REPO_KLAYOUT / "tech" / "intm4tm2.lyt"),
        python_dir=str(PYTHON_DIR),
        api_dir=str(PYTHON_DIR / "pycell4klayout-api" / "source" / "python"),
        cases=cases,
        out_dir=str(out_dir)))
    result = subprocess.run(
        [KLAYOUT_BIN, "-zz", "-rx", "-r", str(script)],
        capture_output=True, text=True, env=_batch_env(out_dir), timeout=600)
    assert result.returncode == 0, (
        f"klayout batch failed:\n{result.stdout}\n{result.stderr}")


def _bbox_extent_um(layout, cell, layer, datatype):
    idx = layout.layer(layer, datatype)
    region = kdb.Region(cell.begin_shapes_rec(idx))
    region.merge()
    assert not region.is_empty(), (
        f"PCell drew nothing on layer {layer}/{datatype}")
    box = region.bbox()
    dbu = layout.dbu
    return (box.width() * dbu, box.height() * dbu)


def _pcell_params(w_um, l_um):
    # Explicit w/l need Calculate="C" (the PCell default derives w&l from the
    # target C otherwise).
    return {"Calculate": "C", "w": f"{w_um}u", "l": f"{l_um}u"}


def _assert_pads_match_pcell(case, footprint, layout, top):
    """Assert the footprint's pads equal the PCell plate bboxes within TOL."""
    metal5_um = _bbox_extent_um(layout, top, *METAL5)
    topmetal1_um = _bbox_extent_um(layout, top, *TOPMETAL1)

    pads = pad_info(footprint)
    # Footprint pad sizes are in mm; convert to um.
    pad_minus_um = tuple(v * 1000.0 for v in pads["2"]["size"])
    pad_plus_um = tuple(v * 1000.0 for v in pads["1"]["size"])

    for axis, (fp_v, gds_v) in enumerate(zip(pad_minus_um, metal5_um)):
        assert abs(fp_v - gds_v) <= TOL_UM, (
            f"case {case}: pad 2 (Metal5) axis {axis} = {fp_v}um "
            f"!= PCell Metal5 bbox {gds_v}um "
            f"(diff {abs(fp_v - gds_v)}um > {TOL_UM}um)")

    for axis, (fp_v, gds_v) in enumerate(zip(pad_plus_um, topmetal1_um)):
        assert abs(fp_v - gds_v) <= TOL_UM, (
            f"case {case}: pad 1 (TopMetal1) axis {axis} = {fp_v}um "
            f"!= PCell TopMetal1 bbox {gds_v}um "
            f"(diff {abs(fp_v - gds_v)}um > {TOL_UM}um)")


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_committed_footprints_match_pcell():
    # Read the DECLARED w/l from each committed footprint (not from the name),
    # then generate the real PCell for those dimensions and compare plates.
    footprints = {}
    cases = []
    for name in PARITY_MEMBERS:
        mod_path = FOOTPRINT_DIR / (name + ".kicad_mod")
        assert mod_path.is_file(), f"missing committed footprint {mod_path}"
        footprint = parse_sexp(mod_path.read_text())
        w_um, l_um = footprint_declared_wl_um(footprint)
        footprints[name] = footprint
        cases.append((name, _pcell_params(w_um, l_um)))

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _generate_pcell(out_dir, cases)

        for name in PARITY_MEMBERS:
            layout = kdb.Layout()
            layout.read(str(out_dir / f"pcell_{name}.gds"))
            top = layout.top_cell()
            _assert_pads_match_pcell(name, footprints[name], layout, top)


# ---------------------------------------------------------------------------
# CAP SOLVER: --cap target -> footprint whose w reproduces C and matches PCell.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_cap_solver_footprint_matches_pcell():
    target_fF = 250.0
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        mod_path = out_dir / "cap250.kicad_mod"
        result = subprocess.run(
            [sys.executable, str(GEN_SCRIPT),
             "--cap", "250f", "--out", str(mod_path)],
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, (
            f"--cap generator CLI failed:\n{result.stdout}\n{result.stderr}")

        footprint = parse_sexp(mod_path.read_text())
        w_um, l_um = footprint_declared_wl_um(footprint)
        assert w_um == l_um, (
            f"--cap must solve a square cap, got w={w_um} l={l_um}")

        # The solved width must reproduce the target capacitance within 1 %.
        recomputed = cap_model_fF(w_um)
        assert abs(recomputed - target_fF) <= 0.01 * target_fF, (
            f"--cap 250f -> w={w_um}um gives C={recomputed:.4f}fF, "
            f"not within 1% of {target_fF}fF")

        # And the emitted pads must match a fresh PCell run for that w.
        _generate_pcell(out_dir, [("cap250", _pcell_params(w_um, l_um))])
        layout = kdb.Layout()
        layout.read(str(out_dir / "pcell_cap250.gds"))
        top = layout.top_cell()
        _assert_pads_match_pcell("cap250", footprint, layout, top)


# ---------------------------------------------------------------------------
# CONSISTENCY: --cap <nominal> must reproduce the committed family member.
# Both --cap and --family route through the same C->width mapping, so a
# floor-vs-round grid-snap divergence (which would make --cap emit a slightly
# different plate than the pre-generated family) is a regression. No klayout
# needed -- this is a pure generator check and always runs.
# ---------------------------------------------------------------------------
def _nominal_from_name(name):
    """'CMIM_10fF' -> '10fF', 'CMIM_1pF' -> '1pF' (the --cap target string)."""
    assert name.startswith("CMIM_"), name
    return name[len("CMIM_"):]


@pytest.mark.parametrize("member", sorted(EXPECTED_FAMILY))
def test_cap_mode_reproduces_family_member(member):
    committed = FOOTPRINT_DIR / (member + ".kicad_mod")
    assert committed.is_file(), f"missing committed footprint {committed}"
    w_fam, l_fam = footprint_declared_wl_um(parse_sexp(committed.read_text()))

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / (member + ".kicad_mod")
        result = subprocess.run(
            [sys.executable, str(GEN_SCRIPT),
             "--cap", _nominal_from_name(member), "--out", str(out)],
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, (
            f"--cap {_nominal_from_name(member)} failed:\n"
            f"{result.stdout}\n{result.stderr}")
        w_cap, l_cap = footprint_declared_wl_um(parse_sexp(out.read_text()))

    assert (w_cap, l_cap) == (w_fam, l_fam), (
        f"--cap {_nominal_from_name(member)} -> w={w_cap} l={l_cap} must equal "
        f"the committed {member} w={w_fam} l={l_fam}: --cap and --family must "
        f"share one grid-snap (round-to-nearest), never diverge")
