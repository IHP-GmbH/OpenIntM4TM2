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
live_tool tests run kicad-cli 9.99 on a probe board that breaks each rule KiCad can
catch, checking that the report names each rule with its severity as often as the
probe's copper breaks it, and on the template board, checking that KiCad read the
netclasses. Each probe item states what it breaks from its own geometry and the rule
values; a hermetic test holds those counts to a table counted by hand, so a probe
edit that drops a case fails without kicad-cli.

One intm4tm2 version names one set of rule and project bytes: SHIPPED holds the
digests of each version, and a byte change without a new row and a version bump
fails here.
"""
import collections
import hashlib
import importlib.util
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
     f"A.Type == 'Track' && A.Width > {mm('TM2_bR_w')} && A.Net != B.Net"
     " && B.existsOnLayer('F.Cu')"),
    ("TM2_bR", "F.Cu", "physical_clearance", "min", "warning",
     f"A.Type == 'Pad' && (A.Size_X > {mm('TM2_bR_cr')} || A.Size_Y > {mm('TM2_bR_cr')})"
     f" && A.Size_X > {mm('TM2_bR_w')} && A.Size_Y > {mm('TM2_bR_w')} && A.Net != B.Net"
     " && B.existsOnLayer('F.Cu')"),
] + [("Slt_c", layer, "track_width", "max", "warning", f"A.Width > {mm('Slt_c')}")
      for layer in LAYERS.values()]

# The keys a rule's condition reads besides its own value.
CONDITION_KEYS = {"TM2_bR": ["TM2_bR_w", "TM2_bR_cr"]}

# What KiCad 9.99 was seen to parse. kicad-cli drops the whole file, with no message,
# on one unknown token: a unit other than mm (um), a property it does not know
# (Pad_Size_X, Length).
PROPERTIES = {"Type", "Width", "Size_X", "Size_Y", "Net"}
FUNCTIONS = {"existsOnLayer"}

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
# Per metal, the JSON keys of its minimum width and minimum space.
METAL_KEYS = {metal: CLASSES[metal][:2] for metal in LAYERS}

RULE = re.compile(r'\(rule "(?P<name>[^"]* \((?P<key>\w+)\))"\n'
                  r'(?:\t\(severity (?P<severity>\w+)\)\n)?'
                  r'\t\(layer "(?P<layer>[^"]+)"\)\n'
                  r'(?:\t\(condition "(?P<condition>[^"]*)"\)\n)?'
                  r'\t\(constraint (?P<constraint>\w+) \((?P<bound>min|max) (?P<value>[0-9.]+mm)\)\)\)')


def rules_in(text):
    return [m.groupdict() for m in RULE.finditer(text)]


def rule_id(rule):
    """A rule as the probe names what it breaks: its key, constraint, KiCad layer and
    the item type its condition asks for (None for none). The two TM2_bR rules differ
    only in that type."""
    kind = re.search(r"A\.Type == '(\w+)'", rule["condition"] or "")
    return rule["key"], rule["constraint"], rule["layer"], kind and kind.group(1)


def test_the_committed_rules_are_what_the_json_gives():
    assert DRU.read_text(encoding="utf-8") == kicad_rules_gen.generate(), (
        f"{DRU.name} is stale: run python libs.tech/kicad/scripts/kicad_rules_gen.py")


def test_every_rule_carries_its_json_value():
    text = DRU.read_text(encoding="utf-8")
    found = rules_in(text)
    assert text.count("(rule ") == len(found) == len(EXPECTED)
    # In file order: the slit rules must follow the width minimums (the generator's
    # docstring says why).
    assert [(r["key"], r["layer"], r["constraint"], r["bound"], r["severity"] or "error",
             r["condition"]) for r in found] == EXPECTED
    for r in found:
        assert r["value"] == mm(r["key"]), r["name"]


def test_the_rules_use_only_what_kicad_parses():
    body = DRU.read_text(encoding="utf-8").split("(version 1)")[1]
    conditioned = [(r["layer"], r["condition"]) for r in rules_in(body) if r["condition"]]
    assert conditioned
    for layer, condition in conditioned:
        # A quoted name is an item type or the rule's own layer, and that layer, by its
        # KiCad name, is all a function is given.
        assert set(re.findall(r"'([^']*)'", condition)) <= {"Track", "Pad", layer}, condition
        assert set(re.findall(r"\w+\('([^']*)'\)", condition)) <= {layer}, condition
        code = re.sub(r"'[^']*'", "''", condition)
        assert not re.findall(r"(?<![.\w])\w+\(", code), condition     # every call is A.f or B.f
        items = re.findall(r"\b([A-Za-z_]\w*)\.(\w+)(\()?", code)
        assert {item for item, _, _ in items} <= {"A", "B"}, condition
        assert {prop for _, prop, call in items if not call} <= PROPERTIES, condition
        assert {prop for _, prop, call in items if call} <= FUNCTIONS, condition
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


# The rules and the project each intm4tm2 version carries, by SHA-256, oldest first,
# as (version, rules, project). A board takes both files beside it and a consumer may
# pin them (interposer-pnr freezes their digests), so one version names one byte set:
# new bytes take a new row under a newer version, and pyproject.toml's version moves to
# it. 0.3.0 is the first version held this way; git holds the bytes before it. A row is
# never edited, and that is the reviewer's to hold: this test reads only the newest row.
SHIPPED = [
    ("0.3.0", "ece6dd6d08bd96e3230311fbcadc37c1d2896b882a56d06b91b3cf3c28aebd61",
     "f0d67ea8a7af4b473dcc072f2d660058cc568637b658ea08ea96c133b4c62453"),
    ("0.3.1", "bd34aae94753da14a5f94cda20714807f8a85ffa9e172dcdad9dfa3a0510d814",
     "f0d67ea8a7af4b473dcc072f2d660058cc568637b658ea08ea96c133b4c62453"),
    ("0.4.0", "bd34aae94753da14a5f94cda20714807f8a85ffa9e172dcdad9dfa3a0510d814",
     "f0d67ea8a7af4b473dcc072f2d660058cc568637b658ea08ea96c133b4c62453"),
    ("0.4.1", "1f4143c65fedcc44d891b87960a3c71a791aa3921ff27fcf7334f148ce261322",
     "f0d67ea8a7af4b473dcc072f2d660058cc568637b658ea08ea96c133b4c62453"),
    ("0.5.0", "1f4143c65fedcc44d891b87960a3c71a791aa3921ff27fcf7334f148ce261322",
     "f0d67ea8a7af4b473dcc072f2d660058cc568637b658ea08ea96c133b4c62453"),
]


def _declared_version():
    path = ROOT / "libs.tech/klayout/python/intm4tm2_pycell_lib/ihp/pdk_version.py"
    spec = importlib.util.spec_from_file_location("pdk_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.declared_version(ROOT)


def _release(version):
    """MAJOR.MINOR.PATCH of a version, so 0.3.1.dev1 is 0.3.1 and 0.3 is 0.3.0."""
    found = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(?:[.+-]?[A-Za-z].*)?", version)
    assert found, f"{version!r} is not a MAJOR.MINOR[.PATCH] version"
    return tuple(int(part or 0) for part in found.groups())


def test_one_pdk_version_names_one_set_of_rule_bytes():
    versions = [version for version, *_ in SHIPPED]
    releases = [_release(version) for version in versions]
    assert releases == sorted(set(releases)), f"SHIPPED is not oldest first, once each: {versions}"
    newest, *digests = SHIPPED[-1]
    now = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (DRU, PRO)]
    assert digests == now, (
        f"{DRU.name} or {PRO.name} changed since intm4tm2 {newest}: add a row with "
        f"{now} to SHIPPED under a new version and set pyproject.toml's version to it")
    declared = _declared_version()
    assert declared and _release(declared) == _release(newest), (
        f"pyproject.toml declares {declared}, the newest rule bytes are {newest}'s: if "
        f"the rules did not change, add a {declared} row with the same digests")


@pytest.mark.parametrize("version, release", [
    ("0.3.1", (0, 3, 1)), ("0.3", (0, 3, 0)), ("0.3.1.dev2", (0, 3, 1)),
    ("1.0.0rc1", (1, 0, 0)), ("0.3.1+local", (0, 3, 1)),
])
def test_a_release_is_read_off_a_version(version, release):
    assert _release(version) == release


@pytest.mark.parametrize("version", ["v0.3.1", "0", "", "0.3.1.4"])
def test_a_version_that_is_not_one_is_refused(version):
    with pytest.raises(AssertionError, match="is not a MAJOR.MINOR"):
        _release(version)


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
# kicad-cli reads the committed rules and project: a probe board with copper that
# breaks each rule KiCad can catch, and the template board's vias.
# ---------------------------------------------------------------------------
KICAD_CLI = shutil.which("kicad-cli")


def _probe_board():
    """The probe board's text: the template's copper layers, a 2 mm square outline, and
    copper that breaks each rule KiCad can catch. Lengths are um.

    Each item also states what it breaks, from its own geometry and the rule values,
    never from a KiCad report: a Counter of rule_id() for each named rule, of
    ("width", metal) for a track under its metal's minimum width and of "edge" for
    copper on the outline."""
    template = kicad_rules_gen.TEMPLATE.read_text(encoding="utf-8")
    version = re.search(r"\(version \d+\)", template).group(0)
    copper = re.findall(r'\(\d+ "[A-Za-z0-9.]+\.Cu" signal "[^"]+"\)', template)
    layers = kicad_rules_gen.copper_layers(template)
    metal_of = {layer: metal for metal, layer in layers.items()}
    mm_str = lambda v: f"{Decimal(str(v)) / 1000:f}"
    r = {k: Decimal(str(v)) for k, v in RULES.items()}
    floors = json.loads(PRO.read_text(encoding="utf-8"))["board"]["design_settings"]["rules"]
    edge_floor = Decimal(str(floors[EDGE_FLOOR])) * 1000
    side, stroke = 2000, Decimal(100)
    nets, items = [], []
    expect = collections.Counter()
    under = lambda key: r[key] - 1          # a gap 2x KiCad's 0.5 um epsilon under the rule
    tm2, tm1 = layers["TopMetal2"], layers["TopMetal1"]

    def net():
        nets.append(f'  (net {len(nets) + 1} "N{len(nets) + 1}")')
        return len(nets)

    def outline(x_min, x_max, y_min, y_max):
        if min(x_min, y_min, side - x_max, side - y_max) < edge_floor:
            expect["edge"] += 1

    def line(width, layer, x_min, x_max, y_min, y_max):
        """What a track or an arc of width breaks on its own, inside that box."""
        metal = metal_of[layer]
        if width < r[METAL_KEYS[metal][0]]:
            expect["width", metal] += 1
        if width > r["Slt_c"]:
            expect["Slt_c", "track_width", layer, None] += 1
        outline(x_min, x_max, y_min, y_max)

    def track(x0, y, x1, width, layer, n):
        items.append(f'  (segment (start {mm_str(x0)} {mm_str(y)}) (end {mm_str(x1)} {mm_str(y)}) '
                     f'(width {mm_str(width)}) (layer "{layer}") (net {n}))')
        line(width, layer, x0 - width / 2, x1 + width / 2, y - width / 2, y + width / 2)

    def spaced(layer, gap, wide_track=None, pad=None):
        """What a gap between items of two nets on layer breaks: the layer's clearance,
        and on TopMetal2 TM2_bR beside a track wider than TM2_bR_w or a pad wider than
        it and longer than TM2_bR_cr. KiCad takes a pair's physical clearance from the
        last rule that matches it, the pad rule."""
        space = METAL_KEYS[metal_of[layer]][1]
        if gap < r[space]:
            expect[space, "clearance", layer, None] += 1
        if metal_of[layer] != "TopMetal2" or gap >= r["TM2_bR"]:
            return
        if pad and max(pad) > r["TM2_bR_cr"] and min(pad) > r["TM2_bR_w"]:
            expect["TM2_bR", "physical_clearance", layer, "Pad"] += 1
        elif wide_track is not None and wide_track > r["TM2_bR_w"]:
            expect["TM2_bR", "physical_clearance", layer, "Track"] += 1

    def beside(y, wide, gap, thin):
        """The centre line of a track thin, gap away from a track wide centred on y."""
        return y + wide / 2 + gap + thin / 2

    def pair(x, y, layer, wide, thin, gap, run=100, same_net=False, arc=False):
        """Two tracks along x, thin below wide. As an arc, wide bulges 1 um away from
        thin, so gap is the closest they come; KiCad 9.99 reads an arc as a Track."""
        n = net()
        if arc:
            items.append(f'  (arc (start {mm_str(x)} {mm_str(y)}) (mid {mm_str(x + run / 2)} '
                         f'{mm_str(y - 1)}) (end {mm_str(x + run)} {mm_str(y)}) '
                         f'(width {mm_str(wide)}) (layer "{layer}") (net {n}))')
            line(wide, layer, x - wide / 2, x + run + wide / 2, y - 1 - wide / 2, y + wide / 2)
        else:
            track(x, y, x + run, wide, layer, n)
        track(x, beside(y, wide, gap, thin), x + run, thin, layer, n if same_net else net())
        if not same_net:
            spaced(layer, gap, wide_track=wide)

    def octagon(width):
        """A regular octagon width across its flats: the leg each corner is cut by, and
        its corners."""
        leg = (width - width / (1 + Decimal(2).sqrt())) / 2
        h = width / 2
        return leg, [(-h + leg, -h), (h - leg, -h), (h, -h + leg), (h, h - leg),
                     (h - leg, h), (-h + leg, h), (-h, h - leg), (-h, -h + leg)]

    def pad(x, y, size_x, size_y, n, shape="rect", anchor=None):
        """A TopMetal2 pad of copper size_x by size_y on net n. An "octagon" is a
        chamfered rectangle, a "custom" one a custom pad holding the octagon on an
        anchor. The copper is the anchor plus the octagon, so an anchor the copper's
        size is a circle, which the octagon holds; a smaller one given is a rect. KiCad
        reads Size_X and Size_Y as the pad's size, so the anchor's."""
        assert shape in ("rect", "octagon", "custom"), shape
        size = anchor or (size_x, size_y)                    # what KiCad reads as its size
        head = f'(pad "1" smd {{}} (at 0 0) (size {mm_str(size[0])} {mm_str(size[1])}) ' \
               f'(layers "{tm2}")'
        if shape == "rect":
            sexp = head.format("rect")
        else:
            assert size_x == size_y, "an octagon probe is square"
            leg, corners = octagon(size_x)
            if shape == "octagon":
                sexp = (head.format("roundrect") + f" (roundrect_rratio 0) (chamfer_ratio "
                        f"{leg / size_x:.4f}) (chamfer top_left top_right bottom_left bottom_right)")
            else:
                pts = " ".join(f"(xy {mm_str(cx)} {mm_str(cy)})" for cx, cy in corners)
                sexp = (head.format("custom") + " (options (clearance outline) "
                        f"(anchor {'rect' if anchor else 'circle'})) "
                        f"(primitives (gr_poly (pts {pts}) (width 0) (fill yes)))")
        items.append(f'  (footprint "probe:PAD" (layer "F.Cu") (at {mm_str(x)} {mm_str(y)})\n'
                     f'    (property "Reference" "P{n}" (at 0 0) (layer "F.Fab"))\n'
                     f'    {sexp} (net {n} "N{n}"))\n  )')
        outline(x - size_x / 2, x + size_x / 2, y - size_y / 2, y + size_y / 2)
        return size

    def pad_and_track(x, y, size_x, size_y, gap=under("TM2_bR"), width=r["TM2_a"],
                      same_net=False, shape="rect", anchor=None):
        """A TopMetal2 pad, as pad() draws it, and a track of width along its long
        side, gap away."""
        n = net()
        size = pad(x, y, size_x, size_y, n, shape, anchor)
        track(x - size_x, beside(y, size_y, gap, width), x + size_x, width,
              tm2, n if same_net else net())
        if not same_net:
            spaced(tm2, gap, wide_track=width, pad=size)

    wide = r["TM2_bR_w"] + 1
    pair(100, 100, tm2, r["TM2_a"], r["TM2_a"], under("TM2_b"))
    pair(300, 100, tm1, r["TM1_a"], r["TM1_a"], under("TM1_b"))
    pair(500, 100, tm2, wide, r["TM2_a"], under("TM2_bR"))
    pair(500, 300, tm2, wide, r["TM2_a"], under("TM2_b"))     # TM2_b beside a wide track
    pair(500, 500, tm2, wide, r["TM2_a"], under("TM2_bR"), same_net=True)
    pair(1100, 100, tm2, wide, r["TM2_a"], under("TM2_bR"), arc=True)
    long = r["TM2_bR_cr"] + 10
    pad_and_track(800, 100, long, long)
    pad_and_track(800, 300, long, r["TM2_bR_w"])             # a narrow pad is no wide line
    pad_and_track(800, 500, long, long, same_net=True)
    pad_and_track(800, 700, long, long, gap=under("TM2_b"))  # TM2_b beside a wide pad
    pad_and_track(1300, 300, long, long, width=wide)          # a wide pad beside a wide track
    bond = Decimal(80)                                       # the default bond pad, sides ~33 um
    pad_and_track(1600, 200, bond, bond, shape="octagon")
    pad_and_track(1600, 500, bond, bond, shape="custom")
    pad_and_track(1600, 800, bond, bond, shape="custom", anchor=(r["TM2_a"], r["TM2_a"]))
    for i, metal in enumerate(LAYERS):
        track(100 + 200 * i, 900, 200 + 200 * i, r["Slt_c"] + 1, layers[metal], net())
        track(100 + 200 * i, 1200, 200 + 200 * i, r[METAL_KEYS[metal][0]] / 2, layers[metal],
              net())
    track(-20, 1500, 40, r["TM2_a"], tm2, net())                           # across the edge
    track(r["TM2_a"] / 2, 1700, 60, r["TM2_a"], tm2, net())                # touching it
    # A wide track and a wide pad closer to the outline's stroke than TM2_bR, and far
    # from its line: TM2_bR is metal to metal, so neither breaks anything.
    near = stroke / 2 + under("TM2_bR")
    track(1700, near + wide / 2, 1800, wide, tm2, net())
    pad(1850, near + long / 2, long, long, net())
    edge = [((0, 0), (side, 0)), ((side, 0), (side, side)), ((side, side), (0, side)),
            ((0, side), (0, 0))]
    head = [f'(kicad_pcb {version} (generator "probe")', "  (general (thickness 0.26))", "  (layers"]
    head += [f"    {c}" for c in copper] + ['    (25 "Edge.Cuts" user)', '    (35 "F.Fab" user)', "  )",
                                             '  (net 0 "")']
    head += [f'  (gr_line (start {mm_str(a[0])} {mm_str(a[1])}) (end {mm_str(b[0])} {mm_str(b[1])}) '
             f'(stroke (width {mm_str(stroke)}) (type solid)) (layer "Edge.Cuts"))'
             for a, b in edge]
    return "\n".join(head + nets + items) + "\n)\n", expect


def _named(rules):
    """The rules a KiCad report names. The Metal5 and Metal4 space sits under KiCad's
    0.5 um DRC epsilon, and KiCad names a width minimum "board setup constraints":
    those two are checked by value."""
    return [r for r in rules if not (r["constraint"] == "track_width" and r["bound"] == "min")
            and r["key"] != "Mn_b"]


# What the probe's copper breaks, counted by hand from the items in _probe_board(), not
# from its own bookkeeping. The pad at (800, 300) is too narrow to count as wide and
# breaks nothing, nor does the octagon at (1600, 800), whose size is its small anchor;
# the two same-net items break nothing either, nor do the wide track and wide pad by
# the outline (1700, 57) and (1850, 84), which come closer than TM2_bR to its stroke
# only.
# The two octagon pads counted below break no deck rule: they are warnings KiCad
# raises and the deck does not.
PROBE_BREAKS = {
    # (100, 100), and beside a wide track (500, 300) and a wide pad (800, 700).
    ("TM2_b", "clearance", "F.Cu", None): 3,
    ("TM1_b", "clearance", "In1.Cu", None): 1,
    # Beside a wide track (500, 100), its arc (1100, 100), and as a TM2_b gap (500, 300).
    ("TM2_bR", "physical_clearance", "F.Cu", "Track"): 3,
    # Beside a wide pad (800, 100), as a TM2_b gap (800, 700), beside a wide pad and a
    # wide track (1300, 300), where the pad rule is the one that matches last, and,
    # KiCad only, beside an 80 um octagon, chamfered (1600, 200) or a custom pad on a
    # circle anchor the copper's size (1600, 500).
    ("TM2_bR", "physical_clearance", "F.Cu", "Pad"): 5,
    **{("Slt_c", "track_width", layer, None): 1 for layer in LAYERS.values()},
    **{("width", metal): 1 for metal in LAYERS},
    "edge": 2,
}


def test_the_probe_board_breaks_every_rule_it_checks():
    # Without kicad-cli: a probe edit that drops a case, or a misread rule in the
    # probe's bookkeeping, moves a count here, not only where the live test runs.
    _, expect = _probe_board()
    named = _named(rules_in(DRU.read_text(encoding="utf-8")))
    checked = ({rule_id(r) for r in named} | {("width", m) for m in LAYERS} | {"edge"})
    assert set(PROBE_BREAKS) == checked, set(PROBE_BREAKS) ^ checked
    assert dict(expect) == PROBE_BREAKS


def _kicad_drc(tmp_path, board_text, project_text=None):
    """kicad-cli's violations on a board checked beside the committed rules and the
    committed project, or project_text."""
    version = subprocess.run([KICAD_CLI, "version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    assert version.startswith("9.99"), (
        f"the rules were measured on kicad-cli 9.99, this is {version}: measure again")
    tmp_path.mkdir(exist_ok=True)
    board = tmp_path / "board.kicad_pcb"
    board.write_text(board_text, encoding="utf-8")
    shutil.copy(DRU, tmp_path / "board.kicad_dru")
    (tmp_path / "board.kicad_pro").write_text(
        PRO.read_text(encoding="utf-8") if project_text is None else project_text,
        encoding="utf-8")
    report = tmp_path / "drc.json"
    run = subprocess.run([KICAD_CLI, "pcb", "drc", "--format", "json", "--severity-all",
                          "--units", "mm", "-o", str(report), str(board)],
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(report.read_text())["violations"]


@pytest.mark.live_tool
@pytest.mark.skipif(KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_kicad_cli_reads_the_netclasses(tmp_path):
    # KiCad 9.99 drops every netclass of a project that lacks one of its keys, and then
    # holds the template's vias, 5 um placeholders in Default, to a 0.3 mm diameter.
    # The control drops tuning_profile, so each via must fail there.
    template = kicad_rules_gen.TEMPLATE.read_text(encoding="utf-8")
    vias = len(re.findall(r"\(via\b", template))
    project = json.loads(PRO.read_text(encoding="utf-8"))
    for c in project["net_settings"]["classes"]:
        del c["tuning_profile"]

    def via_errors(name, project_text=None):
        found = _kicad_drc(tmp_path / name, template, project_text)
        return [v for v in found if v["type"] == "via_diameter"]

    dropped = via_errors("dropped", json.dumps(project, indent=2) + "\n")
    assert len(dropped) == vias > 0, dropped
    read = via_errors("read")
    assert read == [], read


@pytest.mark.live_tool
@pytest.mark.skipif(KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_kicad_cli_reads_every_rule(tmp_path):
    board, expect = _probe_board()
    violations = _kicad_drc(tmp_path, board)
    said = [(v["type"], v["severity"], v["description"]) for v in violations]
    rules = rules_in(DRU.read_text(encoding="utf-8"))
    assert len(rules) == len(EXPECTED), "the rules file does not read as written"
    named = _named(rules)
    # Every rule has copper that breaks it, and KiCad names it as often as the copper
    # does: a warning that took over an error beside it, or fired on its own net, would
    # move a count.
    for r in named:
        hits = [s for s in said if f"rule '{r['name']}'" in s[2]]
        assert len(hits) == expect[rule_id(r)], (r["name"], expect[rule_id(r)], hits)
        assert {s[1] for s in hits} == {r["severity"] or "error"}, (r["name"], hits)
    # On Metal5 and Metal4 the project's min_track_width (Mn_a as well) flags the same
    # tracks, so there this holds the layer mapping more than the rule.
    for metal in LAYERS:
        width = f"min width {Decimal(str(RULES[METAL_KEYS[metal][0]])) / 1000:.4f} mm"
        found = [v["severity"] for v in violations if v["type"] == "track_width"
                 and width in v["description"]
                 and any(f" on {metal}," in item["description"] for item in v["items"])]
        assert found == ["error"] * expect["width", metal], (metal, found)
    edges = [s for s in said if s[0] == "copper_edge_clearance"]
    assert len(edges) == expect["edge"], said
