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
"""The template board's KiCad rules and project carry the DRC rule values, and only those.

libs.tech/kicad/interposer_template.kicad_dru and interposer_template.kicad_pro are
written by libs.tech/kicad/scripts/kicad_rules_gen.py from interposer_tech_default.json.
A value changed in the JSON without running the script, or typed into either file, is
a KiCad check that no longer agrees with the KLayout deck.
"""
import json
import re
import sys
from decimal import Decimal
from pathlib import Path


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

# Read on its own, not from the generator: which key bounds which constraint on which
# KiCad layer of the template.
EXPECTED = {
    ("F.Cu", "track_width"): "TM2_a", ("F.Cu", "clearance"): "TM2_b",
    ("In1.Cu", "track_width"): "TM1_a", ("In1.Cu", "clearance"): "TM1_b",
    ("In2.Cu", "track_width"): "Mn_a", ("In2.Cu", "clearance"): "Mn_b",
    ("B.Cu", "track_width"): "Mn_a", ("B.Cu", "clearance"): "Mn_b",
}

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
CLASSES = {
    "Default": ("TM2_a", "TM2_b", "TV2_a", "TV2_d"),
    "TopMetal2": ("TM2_a", "TM2_b", "TV2_a", "TV2_d"),
    "TopMetal1": ("TM1_a", "TM1_b", "TV1_a", "TV1_d"),
    "Metal5": ("Mn_a", "Mn_b", "Vn_a", "Vn_c1"),
    "Metal4": ("Mn_a", "Mn_b", "Vn_a", "Vn_c1"),
}

RULE = re.compile(r'\(rule "[^"]* \((\w+)\)"\s*\(layer "([^"]+)"\)\s*'
                  r'\(constraint (\w+) \(min ([0-9.]+)mm\)\)\)')


def test_the_committed_rules_are_what_the_json_gives():
    assert DRU.read_text(encoding="utf-8") == kicad_rules_gen.generate(), (
        f"{DRU.name} is stale: run python libs.tech/kicad/scripts/kicad_rules_gen.py")


def test_every_rule_carries_its_json_value():
    text = DRU.read_text(encoding="utf-8")
    found = {(layer, constraint): (key, Decimal(value) * 1000)
             for key, layer, constraint, value in RULE.findall(text)}
    assert text.count("(rule ") == len(found) == len(EXPECTED)
    assert {k: v[0] for k, v in found.items()} == EXPECTED
    for (layer, constraint), (key, um) in found.items():
        assert um == Decimal(str(RULES[key])), (layer, constraint, key)


def test_every_json_key_is_ported_or_named_klayout_only():
    head = DRU.read_text(encoding="utf-8").split("(version 1)")[0]
    ported, _, rest = head.partition("KLayout only:")
    named_ported = set(re.findall(r"\b\w+_\w+\b", ported.split("Ported,")[1]))
    named_klayout = rest.replace("#", " ").split()
    assert named_ported == set(EXPECTED.values())
    assert sorted(named_klayout) == sorted(set(RULES) - named_ported)


def um(*keys):
    return sum(Decimal(str(RULES[k])) for k in keys)


def test_the_committed_project_is_what_the_json_gives():
    assert PRO.read_text(encoding="utf-8") == kicad_rules_gen.generate_project(), (
        f"{PRO.name} is stale: run python libs.tech/kicad/scripts/kicad_rules_gen.py")


def test_every_project_value_is_its_json_keys():
    doc = json.loads(PRO.read_text(encoding="utf-8"))
    floors = doc["board"]["design_settings"]["rules"]
    assert floors.keys() == FLOORS.keys()
    for field, keys in FLOORS.items():
        assert Decimal(str(floors[field])) * 1000 == um(*keys), field
    classes = {c["name"]: c for c in doc["net_settings"]["classes"]}
    assert classes.keys() == CLASSES.keys()
    for name, (width, space, cut, enc) in CLASSES.items():
        expect = {"track_width": um(width), "clearance": um(space),
                  "via_drill": um(cut), "microvia_drill": um(cut),
                  "via_diameter": um(cut, enc, enc), "microvia_diameter": um(cut, enc, enc)}
        got = {k: Decimal(str(classes[name][k])) * 1000 for k in expect}
        assert got == expect, name
