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
"""The DRC margin list (python/drc_margins.py) against the deck and against layouts with
known distances.

The table is checked against the deck text without KLayout: each rule's key is the value
that sets the threshold of the deck's check of that rule and its offset is the term the
check adds to it, the rules marked as measured against the seal ring are the ones whose
check reads it or a layer derived from it, the tool taps the deck lines of each rule it
names and changes nothing else, and the decks it runs and leaves out are the registry.

The rest runs the klayout binary. One layout holds a structure per kind (width, space,
enclosure, separation) at a known distance inside the window, and the pad and Passiv
opening rules (some of which the deck lowers by a circle tolerance) exactly at their
floors. The list reads each known distance and a zero margin at each floor, and nothing
for every other rule. The real deck passes that layout on those rules, and flags each of
them once its structure is 1 nm under the floor, so each floor in the table is the one
the deck applies. A run that sees every rule name also proves the table names every rule
the decks emit, and a rule whose deck output is polygons but is not tapped stops it. The
same layout without its seal ring lists the seal rules as unmeasured, and a layout with
openings and MIMs off their metal and slits over MIMs lists every tapped rule as flagged.
"""

import ast
import json
import operator
import os
import re
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"
KLAYOUT_BIN = shutil.which("klayout")

sys.path.insert(0, str(PYTHON_DIR))
sys.path.insert(0, str(DRC_DIR))
import drc_margins as dm  # noqa: E402
from intm4tm2_decks import AVAILABLE_DECKS  # noqa: E402

TABLE = dm.load_table()
TECH = json.loads((DRC_DIR / "rule_decks" / dm.TECH_JSON).read_text())["rules"]
FILES = dict(re.findall(r"'(\w+)'\s*=>\s*'([^']+\.drc)'",
                        (DRC_DIR / "intm4tm2_decks.rb").read_text()))
SOURCES = [(DRC_DIR / "rule_decks" / FILES[deck]).read_text() for deck in TABLE["decks"]]
LAYERS = (DRC_DIR / "rule_decks" / "layers_def.drc").read_text()
FETCH = r"(\w+) = fetch_rule\(drc_rules, '(\w+)'\)"
ASSIGN = re.compile(r"^\s*(\w+)\s*=(?!=)\s*(.+)$", re.M)
# A check call, the variable of its threshold (after the other layer, if any) and the term
# added to it, up to the next argument.
THRESHOLD = re.compile(r"\.(?:width|space|notch|isolated|sep|separation|enclosed|enclosing|"
                       r"overlap)\((?:\s*\w+\s*,)?\s*(\w+)\.um\b([^,)]*)")
MAIN = (DRC_DIR / "intm4tm2.drc").read_text()

M4, M5, VIA4, MIM, TM1, TM2 = (50, 0), (67, 0), (66, 0), (36, 0), (126, 0), (134, 0)
M5_SLIT, TM1_SLIT, SEAL = (67, 24), (126, 24), (39, 0)
PASSIV, PILLAR, SBUMP = (9, 0), (9, 35), (9, 36)
DF_PILLAR, DF_SBUMP = (41, 35), (41, 36)

# The structures at a known distance inside the window: rule -> thinnest um.
KNOWN = {"M4.a": 0.22, "M5.a": 0.22, "M5.b": 0.24, "V4.c": 0.02, "MIM.c": 0.7, "MIM.e": 0.7,
         "Slt.g_M5": 0.65}
# The structures drawn at the floor plus delta.
AT_FLOOR = ("Padc.b", "Padc.c", "Padb.b", "Padb.c", "Pas.c", "PadU.w", "PadU.s")
PAD_DECKS = "passiv,copperpillar,solderbump,ubm_floor"
NEEDS_SEAL = sorted(name for name, rule in TABLE["rules"].items() if rule.get("needs_seal"))

# Slt.g sits in the slit loop, once per row of slt_metal_cfg whose Slt.g flag is true.
SLITS = "7_3_metalslits.drc"
SLIT_LOOP = ("slt_metal_cfg.each do |slt_abbrev, slt_met, slt_met_slit, slt_exempt, "
             "slt_g_applies, slt_sep_rules|")
# The table rules whose deck output is polygons, so the tool taps them.
TAPPED = {"Pas.c", "Padc.c", "Padb.c", "MIM.c", "Slt.g_M5", "Slt.g_TM1"}


def _reads_seal(text, seal):
    """Whether text reads the seal ring or a layer derived from it. Cutting it out
    (.not(edgeseal_drw)) only keeps the seal ring out of the checked layer."""
    names = "|".join(["edgeseal\\w*", *map(re.escape, sorted(seal))])
    return re.search(rf"(?<!\.not\()\b(?:{names})\b", text) is not None


def _seal_layers(source, seal=frozenset()):
    """The variables source derives from the seal ring, on top of seal."""
    seal = set(seal)
    grown = True
    while grown:
        grown = False
        for variable, expr in ASSIGN.findall(source):
            if variable not in seal and _reads_seal(expr, seal):
                seal.add(variable)
                grown = True
    return seal


SEAL_LAYERS = _seal_layers(LAYERS)
Block = namedtuple("Block", "logged code values seal source")


def _blocks():
    """A Block per 'Executing rule' line of the decks the table runs: the rule name as
    logged, the code up to the next such line without its comment lines, and the deck's
    rule value variables and seal-derived layers."""
    blocks = []
    for source in SOURCES:
        values = dict(re.findall(FETCH, source))
        seal = _seal_layers(source, SEAL_LAYERS)
        marks = list(re.finditer(r"Executing rule ([^'\"]+)['\"]", source))
        ends = [mark.start() for mark in marks[1:]] + [len(source)]
        for mark, end in zip(marks, ends):
            code = "\n".join(line for line in source[mark.start():end].splitlines()
                             if not line.lstrip().startswith("#"))
            blocks.append(Block(mark.group(1), code, values, seal, source))
    return blocks


BLOCKS = _blocks()


def _rule_blocks(name):
    """The blocks that check name: its own; else the templated ones it fits
    (M#{met_no}.a); else, in a deck with a row naming it, the ones logged under a
    field of the row (#{slt_h_id})."""
    own = [block for block in BLOCKS if block.logged == name]
    fits = [block for block in BLOCKS if "#{" in block.logged and re.fullmatch(
        r"\w+".join(map(re.escape, re.split(r"#\{\w+\}", block.logged))), name)]
    rows = [block for block in BLOCKS
            if block.logged.startswith("#{") and f"'{name}'" in block.source]
    return own or fits or rows


def _thresholds(name):
    """The keys whose value is the threshold of a check that the blocks of name run; a
    threshold bound by a row of a loop (Slt.h2_M4) is read from the rows naming name."""
    keys = set()
    for block in _rule_blocks(name):
        for variable, _term in THRESHOLD.findall(block.code):
            words = [variable] if variable in block.values else [
                word for line in block.source.splitlines() if f"'{name}'" in line
                for word in re.findall(r"\w+", line)]
            keys |= {block.values[word] for word in words if word in block.values}
    return keys


def test_each_rule_takes_the_key_that_sets_its_threshold():
    for name, rule in TABLE["rules"].items():
        assert rule["key"] in TECH, name
        assert _thresholds(name) == {rule["key"]}, name


def _number(word, source):
    """The literal a variable is assigned in source or in the main deck."""
    for text in (source, MAIN):
        found = re.findall(rf"^\s*{word}\s*=\s*([-+]?[\d.]+(?:e[-+]?\d+)?)\b", text, re.M)
        if found:
            assert len(set(found)) == 1, word
            return float(found[0])
    raise AssertionError(f"no literal for {word}")


UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}


def _evaluate(node):
    """The value of an arithmetic expression of numbers."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        return UNARY[type(node.op)](_evaluate(node.operand))
    assert isinstance(node, ast.BinOp) and type(node.op) in BINARY, ast.dump(node)
    return BINARY[type(node.op)](_evaluate(node.left), _evaluate(node.right))


def _offsets(name):
    """The term (integer nm) each check of name adds to its threshold."""
    offsets = set()
    for block in _rule_blocks(name):
        for _variable, term in THRESHOLD.findall(block.code):
            numbers = re.sub(r"\b[a-z_]\w*\b",
                             lambda m: repr(_number(m.group(0), block.source)), term.strip())
            value = _evaluate(ast.parse(numbers or "0", mode="eval").body)
            offsets.add(round(1000 * value))
    return offsets


def test_each_rule_takes_the_offset_its_check_adds():
    for name, rule in TABLE["rules"].items():
        assert _offsets(name) == {rule.get("offset_nm", 0)}, name


def test_the_rules_measured_against_the_seal_ring_are_the_ones_that_read_it():
    for name, rule in TABLE["rules"].items():
        blocks = _rule_blocks(name)
        assert blocks, name
        reads = any(_reads_seal(block.code, block.seal) for block in blocks)
        assert reads == bool(rule.get("needs_seal")), name


def test_the_tool_taps_the_deck_lines_of_its_rules_and_nothing_else():
    """In the copy of each TAPS deck two lines change, the check and then the join, and
    each reads as before without what the tap put in, which sits right before the check's
    .polygons and right after the join's branch. The rules tapped are TAPPED: the four
    named ones, and Slt.g on each slit row whose Slt.g flag is true."""
    tapped = set()
    for name, deck in dm.TAPS.items():
        text = (DRC_DIR / "rule_decks" / deck).read_text()
        before, after = text.splitlines(), dm._tap(text, name, deck).splitlines()
        assert len(after) == len(before), name
        changed = [(old, new) for old, new in zip(before, after) if old != new]
        assert len(changed) == 2, name
        (check, tapped_check), (join, tapped_join) = changed
        assert tapped_check.replace(f'.margin_tap("{name}")', "") == check
        assert tapped_check.rstrip().endswith(f'.margin_tap("{name}").polygons')
        assert tapped_join.replace(f'.margin_branch("{name}")', "") == join
        assert tapped_join.rstrip().endswith(f'.margin_branch("{name}"))')
        tapped.add(name)
    slits = (DRC_DIR / "rule_decks" / SLITS).read_text()
    assert SLIT_LOOP in slits and "if slt_g_applies" in slits
    rows = re.findall(r"\['(\w+)', \w+, \w+, \w+, true,", slits)
    tapped.remove("Slt.g_#{slt_abbrev}")
    tapped |= {f"Slt.g_{abbrev}" for abbrev in rows}
    assert tapped == TAPPED
    assert TAPPED <= set(TABLE["rules"])


# Ways to put a MIM.c line the tap reads out of shape: the start of the line, and what
# the line becomes.
MIM_C_CHECK, MIM_C_JOIN, MIM_C_OUTPUT = "mim_c_l1 = ", "mim_c_l = ", "mim_c_l.output("
# The shape the tool names when that line is out of it.
MIM_C_SHAPES = {MIM_C_CHECK: "'mim_c_l1 = <expr>.polygons' with no '#'",
                MIM_C_JOIN: "'mim_c_l = <check>.join(<branch>)' with no '#'",
                MIM_C_OUTPUT: "'<join>.output('MIM.c''"}
OUT_OF_SHAPE = {
    "check-dropped": (MIM_C_CHECK, lambda line: ""),
    "check-repeated": (MIM_C_CHECK, lambda line: f"{line}\n{line}"),
    "check-with-a-comment": (MIM_C_CHECK, lambda line: f"{line}  # merged below"),
    "check-polygons-only-in-a-comment": (MIM_C_CHECK, lambda line: line.replace(
        ".polygons", "  # .polygons")),
    "join-dropped": (MIM_C_JOIN, lambda line: ""),
    "join-repeated": (MIM_C_JOIN, lambda line: f"{line}\n{line}"),
    "join-with-a-comment": (MIM_C_JOIN, lambda line: f"{line}  # merged below"),
    "join-chained": (MIM_C_JOIN, lambda line: f"{line}.merged(0)"),
    "join-branch-an-expression": (MIM_C_JOIN, lambda line: line.replace(
        "mim_c_l2)", "mim_c_l2.merged(0))")),
    "output-dropped": (MIM_C_OUTPUT, lambda line: ""),
    "output-repeated": (MIM_C_OUTPUT, lambda line: f"{line}\n{line}"),
}


@pytest.mark.parametrize("start, change", OUT_OF_SHAPE.values(), ids=OUT_OF_SHAPE)
def test_a_tapped_line_out_of_shape_stops_the_copy(start, change):
    deck = dm.TAPS["MIM.c"]
    text = (DRC_DIR / "rule_decks" / deck).read_text()
    target = next(row for row in text.splitlines() if row.lstrip().startswith(start))
    assert change(target) != target
    text = text.replace(target, change(target))
    with pytest.raises(ValueError, match=rf"{re.escape(deck)}: MIM\.c: \d+ lines read "
                                         rf"{re.escape(MIM_C_SHAPES[start])}, not one"):
        dm._tap(text, "MIM.c", deck)


def test_a_comment_after_the_output_line_leaves_the_tap_as_it_was():
    deck = dm.TAPS["MIM.c"]
    text = (DRC_DIR / "rule_decks" / deck).read_text()
    target = next(row for row in text.splitlines() if row.lstrip().startswith(MIM_C_OUTPUT))
    commented = text.replace(target, f"{target}  # the polygons")
    assert dm._tap(commented, "MIM.c", deck) == dm._tap(text, "MIM.c", deck).replace(
        target, f"{target}  # the polygons")


def test_the_table_runs_or_names_every_deck():
    assert not set(TABLE["decks"]) & set(TABLE["decks_not_run"])
    assert set(TABLE["decks"]) | set(TABLE["decks_not_run"]) == set(AVAILABLE_DECKS)


@pytest.mark.parametrize("value_nm, window", [(5, 50), (210, 52), (2100, 525), (7500, 1875),
                                              (25000, 5000), (150000, 5000)])
def test_the_window_is_a_quarter_kept_between_its_bounds(value_nm, window):
    assert dm.window_nm(value_nm, TABLE["window"]) == window


def _floor(name):
    rule = TABLE["rules"][name]
    return (round(TECH[rule["key"]] * 1000) + rule.get("offset_nm", 0)) / 1000


def _top(seal=True):
    """A layout with a top cell TOP, the seal ring around it unless not seal, and a box
    drawer for the cell."""
    layout = kdb.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")

    def box(layer, x1, y1, x2, y2):
        top.shapes(layout.layer(*layer)).insert(kdb.DBox(x1, y1, x2, y2))

    if seal:
        ring = kdb.DPolygon(kdb.DBox(0, 0, 800, 800))
        ring.insert_hole(kdb.DBox(10, 10, 790, 790))
        top.shapes(layout.layer(*SEAL)).insert(ring)
    return layout, box


def _layout(path, delta=0.0, seal=True):
    """The known-distance structures, and the AT_FLOOR rules at their floor + delta."""
    layout, box = _top(seal)

    # M4.a and M5.a: two lines each, the thinner one first on Metal4 and last on
    # Metal5, so the list keeps the minimum whatever order the pairs come in.
    box(M4, 400, 100, 400.22, 105)
    box(M4, 402, 100, 402.23, 105)
    box(M5, 460, 100, 460.23, 105)
    box(M5, 462, 100, 462.22, 105)
    box(M5, 450, 100, 450.3, 105)                        # M5.b: space 0.24
    box(M5, 450.54, 100, 450.84, 105)
    box(VIA4, 500, 100, 500.19, 100.19)                  # V4.c: Metal4 0.02 on the left
    box(M4, 499.98, 99.9, 500.29, 100.29)
    box(M5, 499.9, 99.9, 500.29, 100.29)
    box(MIM, 550, 100, 555, 105)                         # MIM.c: Metal5 0.7 around
    box(M5, 549.3, 99.3, 555.7, 105.7)
    box(TM1, 555.7, 100, 560.7, 105)                     # MIM.e: TopMetal1 0.7 away
    # Slt.g_M5: a second MIM in a wide Metal5, and a Metal5 slit 0.65 from it.
    box(MIM, 650, 100, 655, 105)
    box(M5, 640, 90, 675, 115)
    box(M5_SLIT, 655.65, 100, 659.25, 105)

    def pad(opening, dfpad, x, y, size, enclosure):
        box(opening, x, y, x + size, y + size)
        box(dfpad, x, y, x + size, y + size)
        box(TM2, x - enclosure, y - enclosure, x + size + enclosure, y + size + enclosure)

    # Padc.b/Padc.c: two 35 um pillar openings, enclosure and space at their floors.
    space, enclosure = _floor("Padc.b") + delta, _floor("Padc.c") + delta
    pad(PILLAR, DF_PILLAR, 300, 400, 35, enclosure)
    pad(PILLAR, DF_PILLAR, 335 + space, 400, 35, enclosure)
    # Padb.b/Padb.c: two 60 um solder-bump openings.
    space, enclosure = _floor("Padb.b") + delta, _floor("Padb.c") + delta
    pad(SBUMP, DF_SBUMP, 100, 100, 60, enclosure)
    pad(SBUMP, DF_SBUMP, 160 + space, 100, 60, enclosure)
    # Pas.c: a Passiv opening inside the seal ring, TopMetal2 around it.
    enclosure = _floor("Pas.c") + delta
    box(PASSIV, 100, 600, 110, 610)
    box(TM2, 100 - enclosure, 600 - enclosure, 110 + enclosure, 610 + enclosure)
    # PadU.w/PadU.s: pad-opening litho floor, openings without dfpad.
    box(PILLAR, 600, 400, 600 + _floor("PadU.w") + delta, 410)
    box(PILLAR, 600, 450, 605, 460)
    gap = _floor("PadU.s") + delta
    box(PILLAR, 605 + gap, 450, 610 + gap, 460)
    layout.write(str(path))
    return path


@pytest.fixture
def clean_env(tmp_path):
    if KLAYOUT_BIN is None:
        pytest.skip("klayout binary not on PATH")
    home = tmp_path / "klayout_home"
    home.mkdir()
    env = dict(os.environ, KLAYOUT_HOME=str(home))
    env.pop("KLAYOUT_PATH", None)
    return env


@pytest.mark.live_tool
def test_the_list_reads_each_known_distance_and_nothing_else(tmp_path, clean_env):
    record = dm.measure(_layout(tmp_path / "known.gds"), tmp_path / "out", env=clean_env)
    rows = {row["rule"]: row for row in record["rules"]}
    assert set(rows) == set(TABLE["rules"])
    assert record["format"] == dm.FORMAT
    assert not any(row["flagged_shapes"] for row in rows.values())
    assert record["unclassified"] == []
    assert record["seal_ring"] is True
    assert [name for name, row in rows.items() if row["unmeasured"]] == []
    for name, distance in KNOWN.items():
        assert rows[name]["thinnest_um"] == distance, rows[name]
        assert rows[name]["margin_nm"] == round(1000 * distance) - round(1000 * _floor(name))
    for name in AT_FLOOR:
        assert rows[name]["margin_nm"] == 0, rows[name]
    assert rows["M4.a"]["at_um"] == [400.11, 102.5]
    assert rows["M5.a"]["at_um"] == [462.11, 102.5]
    assert rows["M4.a"]["pairs"] == rows["M5.a"]["pairs"] == 2
    quiet = [name for name, row in rows.items() if row["pairs"] and name not in
             (*KNOWN, *AT_FLOOR)]
    assert quiet == []
    # Nothing flagged: smallest margin first, then the rules with nothing in their window.
    assert [row["rule"] for row in record["rules"][:len(AT_FLOOR)]] == sorted(AT_FLOOR)
    text = (tmp_path / "out" / "margins.txt").read_text()
    assert re.search(r"^M4\.b +0\.210 +- +>= 52 ", text, re.M), text


@pytest.mark.live_tool
@pytest.mark.parametrize("delta, flagged", [(0.0, set()), (-0.001, set(AT_FLOOR))])
def test_the_deck_applies_the_floor_the_table_names(tmp_path, clean_env, delta, flagged):
    from run_drc import get_rules_with_violations

    gds = _layout(tmp_path / "floor.gds", delta)
    report = tmp_path / "floor.lyrdb"
    subprocess.run([KLAYOUT_BIN, "-b", "-r", str(DRC_DIR / "intm4tm2.drc"),
                    "-rd", f"input={gds}", "-rd", "topcell=TOP", "-rd", f"report={report}",
                    "-rd", "threads=1", "-rd", "run_mode=flat", "-rd", f"deck={PAD_DECKS}"],
                   check=True, capture_output=True, env=clean_env, timeout=300)
    assert get_rules_with_violations(report) & set(AT_FLOOR) == flagged


@pytest.mark.live_tool
def test_without_a_seal_ring_the_seal_rules_read_unmeasured(tmp_path, clean_env):
    gds = _layout(tmp_path / "no_seal.gds", seal=False)
    record = dm.measure(gds, tmp_path / "out", env=clean_env)
    assert record["seal_ring"] is False
    last = record["rules"][-len(NEEDS_SEAL):]
    assert [row["rule"] for row in last] == NEEDS_SEAL
    assert all(row["unmeasured"] == "no seal ring" and row["margin_nm"] is None for row in last)
    assert not any(row["unmeasured"] for row in record["rules"][:-len(NEEDS_SEAL)])
    text = (tmp_path / "out" / "margins.txt").read_text()
    assert "The layout has no seal ring" in text
    assert re.search(r"^Pas\.c +2\.100 +- +no seal ring ", text, re.M), text


@pytest.mark.live_tool
def test_a_rule_the_deck_flags_outright_reads_flagged(tmp_path, clean_env):
    """A Passiv opening, a pillar opening, a solder-bump opening and a MIM with no metal
    under them, and a Metal5 and a TopMetal1 slit inside a MIM: the deck flags each
    whatever the distance, so every tapped rule reads flagged, first."""
    layout, box = _top()
    box(PASSIV, 100, 100, 110, 110)                       # Pas.c: no TopMetal2
    box(PILLAR, 200, 100, 235, 135)                       # Padc.c: no TopMetal2
    box(DF_PILLAR, 200, 100, 235, 135)
    box(SBUMP, 600, 100, 660, 160)                        # Padb.c: no TopMetal2
    box(DF_SBUMP, 600, 100, 660, 160)
    box(MIM, 300, 100, 305, 105)                          # MIM.c: no Metal5
    box(MIM, 400, 100, 420, 120)                          # Slt.g_M5: slit inside a MIM
    box(M5, 390, 90, 430, 130)
    box(M5_SLIT, 405, 105, 408, 115)
    box(MIM, 500, 300, 520, 320)                          # Slt.g_TM1: slit inside a MIM
    box(M5, 490, 290, 530, 330)
    box(TM1, 490, 290, 530, 330)
    box(TM1_SLIT, 505, 305, 508, 315)
    gds = tmp_path / "outright.gds"
    layout.write(str(gds))
    record = dm.measure(gds, tmp_path / "out", env=clean_env)
    rows = {row["rule"]: row for row in record["rules"]}
    assert {name for name, row in rows.items() if row["flagged_shapes"]} == TAPPED
    assert {row["rule"] for row in record["rules"][:len(TAPPED)]} == TAPPED
    text = (tmp_path / "out" / "margins.txt").read_text()
    for name in TAPPED:
        assert re.search(rf"^{re.escape(name)} +[\d.]+ +\S+ +flagged ", text, re.M), text
