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
"""The template board's KiCad rules and project carry the DRC rule values.

libs.tech/kicad/interposer_template.kicad_dru and interposer_template.kicad_pro are
written by libs.tech/kicad/scripts/kicad_rules_gen.py from interposer_tech_default.json.
A value changed in the JSON without running the script, or typed into either file, is
a KiCad check that no longer agrees with the KLayout deck. The netclass keys with no
interposer meaning are KiCad 9.99's own; the copper-to-edge floor is the value
measured on it.

kicad-cli drops a rules file it cannot parse without a message, so the hermetic tests
pin the units and the item properties to what KiCad 9.99 was seen to read. The
live_tool test runs kicad-cli 9.99 on a probe board that breaks each rule KiCad can
catch, and checks that the report names the rule with its severity.
"""
import json
import re
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest


def _repo_root():
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "libs.tech").is_dir():
            return parent
    raise RuntimeError("could not locate repo root (libs.tech not found)")


ROOT = _repo_root()
KICAD = ROOT / "libs.tech/kicad"
DRU = KICAD / "interposer_template.kicad_dru"
PRO = KICAD / "interposer_template.kicad_pro"
RULES = json.loads((ROOT / "libs.tech/klayout/tech/drc/rule_decks/"
                    "interposer_tech_default.json").read_text())["rules"]
sys.path.insert(0, str(KICAD / "scripts"))
import kicad_rules_gen  # noqa: E402

LAYERS = {"TopMetal2": "F.Cu", "TopMetal1": "In1.Cu", "Metal5": "In2.Cu", "Metal4": "B.Cu"}


def mm(*keys):
    """The sum of the JSON values under keys, as the rules file writes millimetres."""
    return format((sum(Decimal(str(RULES[k])) for k in keys) / 1000).normalize(), "f") + "mm"


# Read on its own, not from the generator: per rule in file order, its JSON key,
# KiCad layer, constraint, bound and severity, and the condition it carries (None
# for none).
EXPECTED = [
    ("TM2_a", "F.Cu", "track_width", "min", "error", None),
    ("TM2_b", "F.Cu", "clearance", "min", "error", None),
    ("TM1_a", "In1.Cu", "track_width", "min", "error", None),
    ("TM1_b", "In1.Cu", "clearance", "min", "error", None),
    ("Mn_a", "In2.Cu", "track_width", "min", "error", None),
    ("Mn_b", "In2.Cu", "clearance", "min", "error", None),
    ("Mn_a", "B.Cu", "track_width", "min", "error", None),
    ("Mn_b", "B.Cu", "clearance", "min", "error", None),
    ("TM2_bR", "F.Cu", "physical_clearance", "min", "warning",
     f"A.Type == 'Track' && A.Width > {mm('TM2_bR_w')} && A.Net != B.Net"),
    ("TM2_bR", "F.Cu", "clearance", "min", "error",
     f"A.Type == 'Pad' && (A.Size_X > {mm('TM2_bR_cr')} || A.Size_Y > {mm('TM2_bR_cr')})"
     f" && A.Size_X > {mm('TM2_bR_w')} && A.Size_Y > {mm('TM2_bR_w')}"),
] + [("Slt_c", layer, "track_width", "max", "warning", f"A.Width > {mm('Slt_c')}")
      for layer in LAYERS.values()]

# The keys a rule's condition reads besides its own value.
CONDITION_KEYS = {"TM2_bR": ["TM2_bR_w", "TM2_bR_cr"]}

# What KiCad 9.99 was seen to parse. kicad-cli drops the whole file, with no message,
# on one unknown token: a unit other than mm (um), a property it does not know
# (Pad_Size_X, Length).
PROPERTIES = {"Type", "Width", "Size_X", "Size_Y", "Net"}

# Read on its own too: the JSON keys whose sum gives each board setup floor, and per
# netclass the keys of its width, its space, and the cut and enclosure of its via.
FLOORS = {
    "min_clearance": ["Mn_b"], "min_track_width": ["Mn_a"],
    "min_via_diameter": ["Vn_a", "Vn_c1", "Vn_c1"],
    "min_microvia_diameter": ["Vn_a", "Vn_c1", "Vn_c1"],
    "min_microvia_drill": ["Vn_a"], "min_through_hole_diameter": ["Vn_a"],
    "min_via_annular_width": ["Vn_c1"], "min_hole_to_hole": ["Vn_b"],
    "min_hole_clearance": ["Mn_b"],
}
# The copper-to-edge floor is no JSON value: the deck states no metal-to-edge rule.
EDGE_FLOOR = "min_copper_edge_clearance"
CLASSES = {
    "Default": ("TM2_a", "TM2_b", "TV2_a", "TV2_d"),
    "TopMetal2": ("TM2_a", "TM2_b", "TV2_a", "TV2_d"),
    "TopMetal1": ("TM1_a", "TM1_b", "TV1_a", "TV1_d"),
    "Metal5": ("Mn_a", "Mn_b", "Vn_a", "Vn_c1"),
    "Metal4": ("Mn_a", "Mn_b", "Vn_a", "Vn_c1"),
}

RULE = re.compile(r'\(rule "(?P<name>[^"]* \((?P<key>\w+)\))"\n'
                  r'(?:\t\(severity (?P<severity>\w+)\)\n)?'
                  r'\t\(layer "(?P<layer>[^"]+)"\)\n'
                  r'(?:\t\(condition "(?P<condition>[^"]*)"\)\n)?'
                  r'\t\(constraint (?P<constraint>\w+) \((?P<bound>min|max) (?P<value>[0-9.]+mm)\)\)\)')


def rules_in(text):
    return [m.groupdict() for m in RULE.finditer(text)]


def test_the_committed_rules_are_what_the_json_gives():
    assert DRU.read_text(encoding="utf-8") == kicad_rules_gen.generate(), (
        f"{DRU.name} is stale: run python libs.tech/kicad/scripts/kicad_rules_gen.py")


def test_every_rule_carries_its_json_value():
    text = DRU.read_text(encoding="utf-8")
    found = rules_in(text)
    assert text.count("(rule ") == len(found) == len(EXPECTED)
    # In file order: KiCad reports a violation under the last rule that matches, with
    # its severity, so the TM2.bR pad rule must follow TM2.b, and the slit rules the
    # width minimums.
    assert [(r["key"], r["layer"], r["constraint"], r["bound"], r["severity"] or "error",
             r["condition"]) for r in found] == EXPECTED
    for r in found:
        assert r["value"] == mm(r["key"]), r["name"]


def test_the_rules_use_only_what_kicad_parses():
    body = DRU.read_text(encoding="utf-8").split("(version 1)")[1]
    conditions = [r["condition"] for r in rules_in(body) if r["condition"]]
    assert conditions
    for condition in conditions:
        items = re.findall(r"\b([A-Za-z_]\w*)\.(\w+)", condition)
        assert {item for item, _ in items} <= {"A", "B"}, condition
        assert {prop for _, prop in items} <= PROPERTIES, condition
    # A rule's name is free text; every other number carries a unit.
    code = body
    for r in rules_in(body):
        code = code.replace(f'"{r["name"]}"', '""')
    units = set(re.findall(r"\d\s*([A-Za-z]+)", code))
    assert units == {"mm"}, units


def test_every_json_key_is_ported_or_named_klayout_only():
    head = DRU.read_text(encoding="utf-8").split("(version 1)")[0]
    ported, _, rest = head.partition("KLayout only:")
    named_ported = set(re.findall(r"\b\w+_\w+\b", ported.partition("Ported,")[2]))
    named_klayout = rest.replace("#", " ").split()
    expect = {key for key, *_ in EXPECTED}
    expect |= {k for key in expect for k in CONDITION_KEYS.get(key, [])}
    assert named_ported == expect
    assert sorted(named_klayout) == sorted(set(RULES) - named_ported)


def um(*keys):
    return sum(Decimal(str(RULES[k])) for k in keys)


def test_the_committed_project_is_what_the_json_gives():
    assert PRO.read_text(encoding="utf-8") == kicad_rules_gen.generate_project(), (
        f"{PRO.name} is stale: run python libs.tech/kicad/scripts/kicad_rules_gen.py")


def test_every_project_value_is_its_json_keys():
    doc = json.loads(PRO.read_text(encoding="utf-8"))
    floors = doc["board"]["design_settings"]["rules"]
    assert floors.keys() == FLOORS.keys() | {EDGE_FLOOR}
    for field, keys in FLOORS.items():
        assert Decimal(str(floors[field])) * 1000 == um(*keys), field
    # Measured on kicad-cli 9.99: 0 lets copper that touches the outline through,
    # 0.001 mm flags it.
    assert floors[EDGE_FLOOR] == 0.001
    classes = {c["name"]: c for c in doc["net_settings"]["classes"]}
    assert classes.keys() == CLASSES.keys()
    for name, (width, space, cut, enc) in CLASSES.items():
        expect = {"track_width": um(width), "clearance": um(space),
                  "via_drill": um(cut), "microvia_drill": um(cut),
                  "via_diameter": um(cut, enc, enc), "microvia_diameter": um(cut, enc, enc)}
        got = {k: Decimal(str(classes[name][k])) * 1000 for k in expect}
        assert got == expect, name


# The keys KiCad 9.99 writes for a netclass. It drops every class that lacks one of
# them (a priority or a tuning profile was seen to matter), and then opens the board
# at a 200 um track and clearance.
KICAD_CLASS_KEYS = {
    "name", "clearance", "track_width", "via_diameter", "via_drill", "microvia_diameter",
    "microvia_drill", "diff_pair_width", "diff_pair_gap", "diff_pair_via_gap", "bus_width",
    "wire_width", "line_style", "pcb_color", "schematic_color", "priority", "tuning_profile",
}


def test_every_netclass_carries_the_keys_kicad_needs_to_read_it():
    classes = json.loads(PRO.read_text(encoding="utf-8"))["net_settings"]["classes"]
    for c in classes:
        assert c.keys() == KICAD_CLASS_KEYS, c["name"]
        assert isinstance(c["priority"], int), c["name"]
        assert isinstance(c["tuning_profile"], str), c["name"]
        assert (c["diff_pair_width"], c["diff_pair_gap"], c["diff_pair_via_gap"]) == (
            c["track_width"], c["clearance"], c["clearance"]), c["name"]
    priorities = [c["priority"] for c in classes]
    assert len(set(priorities)) == len(priorities)
    # KiCad gives Default the lowest priority there is, the largest int32.
    assert max(classes, key=lambda c: c["priority"])["name"] == "Default"
    assert max(priorities) == 2**31 - 1


# ---------------------------------------------------------------------------
# kicad-cli reads the committed rules: a probe board with copper that breaks each
# rule KiCad can catch, checked beside the committed .kicad_dru and .kicad_pro.
# ---------------------------------------------------------------------------
KICAD_CLI = shutil.which("kicad-cli")


def _probe_board():
    """The probe board's text: the template's copper layers, a 2 mm square outline, and
    copper that breaks each rule KiCad can catch. Lengths are um."""
    template = kicad_rules_gen.TEMPLATE.read_text(encoding="utf-8")
    version = re.search(r"\(version \d+\)", template).group(0)
    copper = re.findall(r'\(\d+ "[A-Za-z0-9.]+\.Cu" signal "[^"]+"\)', template)
    layers = kicad_rules_gen.copper_layers(template)
    mm_str = lambda v: f"{Decimal(str(v)) / 1000:f}"
    nets, items = [], []

    def net():
        nets.append(f'  (net {len(nets) + 1} "N{len(nets) + 1}")')
        return len(nets)

    def track(x0, y, x1, width, layer, n):
        items.append(f'  (segment (start {mm_str(x0)} {mm_str(y)}) (end {mm_str(x1)} {mm_str(y)}) '
                     f'(width {mm_str(width)}) (layer "{layer}") (net {n}))')

    def beside(y, wide, gap, thin):
        """The centre line of a track thin, gap away from a track wide centred on y."""
        return y + wide / 2 + gap + thin / 2

    def pair(x, y, layer, wide, thin, gap, run=100, same_net=False):
        track(x, y, x + run, wide, layer, n := net())
        track(x, beside(y, wide, gap, thin), x + run, thin, layer, n if same_net else net())

    r = {k: Decimal(str(v)) for k, v in RULES.items()}
    under = lambda key: r[key] - 1          # a gap 2x KiCad's 0.5 um epsilon under the rule
    wide = r["TM2_bR_w"] + 1
    tm2, tm1, m5, m4 = (layers[m] for m in ("TopMetal2", "TopMetal1", "Metal5", "Metal4"))
    pair(100, 100, tm2, r["TM2_a"], r["TM2_a"], under("TM2_b"))            # TM2_b
    pair(300, 100, tm1, r["TM1_a"], r["TM1_a"], under("TM1_b"))            # TM1_b
    pair(500, 100, tm2, wide, r["TM2_a"], under("TM2_bR"))                 # TM2_bR, track
    pair(500, 300, tm2, wide, r["TM2_a"], under("TM2_b"))    # TM2_b beside a wide track
    pair(500, 500, tm2, wide, r["TM2_a"], under("TM2_bR"), same_net=True)  # same net: clean
    def pad_and_track(x, y, size_x, size_y):
        """A TopMetal2 pad, and a track along its long side, under TM2_bR away."""
        n = net()
        items.append(f'  (footprint "probe:PAD" (layer "F.Cu") (at {mm_str(x)} {mm_str(y)})\n'
                     f'    (property "Reference" "P{n}" (at 0 0) (layer "F.Fab"))\n'
                     f'    (pad "1" smd rect (at 0 0) (size {mm_str(size_x)} {mm_str(size_y)}) '
                     f'(layers "{tm2}") (net {n} "N{n}"))\n  )')
        track(x - size_x, beside(y, size_y, under("TM2_bR"), r["TM2_a"]), x + size_x, r["TM2_a"],
              tm2, net())

    long = r["TM2_bR_cr"] + 10
    pad_and_track(800, 100, long, long)                                    # TM2_bR, pad
    pad_and_track(800, 300, long, r["TM2_bR_w"])          # a narrow pad is no wide line: clean
    for i, layer in enumerate((tm2, tm1, m5, m4)):                         # Slt_c
        track(100 + 200 * i, 900, 200 + 200 * i, r["Slt_c"] + 1, layer, net())
    for i, (layer, key) in enumerate(((tm2, "TM2_a"), (tm1, "TM1_a"), (m5, "Mn_a"))):
        track(100 + 200 * i, 1200, 200 + 200 * i, r[key] / 2, layer, net())  # width minimums
    track(-20, 1500, 40, r["TM2_a"], tm2, net())                           # across the edge
    track(r["TM2_a"] / 2, 1700, 60, r["TM2_a"], tm2, net())                # touching it
    edge = [((0, 0), (2000, 0)), ((2000, 0), (2000, 2000)), ((2000, 2000), (0, 2000)), ((0, 2000), (0, 0))]
    head = [f'(kicad_pcb {version} (generator "probe")', "  (general (thickness 0.26))", "  (layers"]
    head += [f"    {c}" for c in copper] + ['    (25 "Edge.Cuts" user)', '    (35 "F.Fab" user)', "  )",
                                             '  (net 0 "")']
    head += [f'  (gr_line (start {mm_str(a[0])} {mm_str(a[1])}) (end {mm_str(b[0])} {mm_str(b[1])}) '
             f'(stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))' for a, b in edge]
    return "\n".join(head + nets + items) + "\n)\n"


@pytest.mark.live_tool
@pytest.mark.skipif(KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_kicad_cli_reads_every_rule(tmp_path):
    version = subprocess.run([KICAD_CLI, "version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    assert version.startswith("9.99"), (
        f"the rules were measured on kicad-cli 9.99, this is {version}: measure again")
    board = tmp_path / "probe.kicad_pcb"
    board.write_text(_probe_board(), encoding="utf-8")
    shutil.copy(DRU, tmp_path / "probe.kicad_dru")
    shutil.copy(PRO, tmp_path / "probe.kicad_pro")
    report = tmp_path / "drc.json"
    run = subprocess.run([KICAD_CLI, "pcb", "drc", "--format", "json", "--severity-all",
                          "--units", "mm", "-o", str(report), str(board)],
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    violations = json.loads(report.read_text())["violations"]
    said = [(v["type"], v["severity"], v["description"]) for v in violations]
    rules = rules_in(DRU.read_text(encoding="utf-8"))
    assert len(rules) == len(EXPECTED), "the rules file does not read as written"
    # The Metal5 and Metal4 space sits under KiCad's 0.5 um DRC epsilon, and KiCad
    # names a width minimum "board setup constraints": those two are checked by value.
    named = [r for r in rules if not (r["constraint"] == "track_width" and r["bound"] == "min")
             and r["key"] != "Mn_b"]
    hits = {}
    for r in named:
        hits[r["name"]] = [s for s in said if f"rule '{r['name']}'" in s[2]]
        assert hits[r["name"]], f"no hit names {r['name']}"
        assert {s[1] for s in hits[r["name"]]} == {r["severity"] or "error"}, (
            r["name"], hits[r["name"]])
    # KiCad gives a pair the last matching rule's severity: the wide-track warning must
    # not take over a TM2_b error beside it, nor fire on its own net.
    count = {r["key"] + " " + r["constraint"]: len(hits[r["name"]]) for r in named}
    assert count["TM2_b clearance"] == 2, said
    assert count["TM2_bR physical_clearance"] == 2, said
    assert count["TM2_bR clearance"] == 1, said
    for key in ("TM2_a", "TM1_a", "Mn_a"):
        width = f"min width {Decimal(str(RULES[key])) / 1000:.4f} mm"
        found = [s for s in said if s[0] == "track_width" and width in s[2]]
        assert [s[1] for s in found] == ["error"], (key, found)
    assert len([s for s in said if s[0] == "copper_edge_clearance"]) == 2, said
