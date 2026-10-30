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
"""Write the KiCad design rules and project file of the template board from the DRC rule values.

The values come from ``tech/drc/rule_decks/interposer_tech_default.json``, the file
the KLayout decks read, so KiCad and KLayout check one number. Which KiCad copper
layer carries which metal comes from the template board's own layer names. No rule
value in the output is typed by hand: change the JSON and run this script again.
The few KiCad-format values that are no DRC rule are the named constants below.

KiCad gets the rules it can check on the copper it draws, in the ``.kicad_dru``: the
minimum track width and the minimum clearance of each metal, and the two width-dependent
rules that rest on one item's own size, TM2.bR and Slt.c. Every other key is listed in
that file's header as a KLayout-only rule. libs.tech/kicad/README.md says what each
ported rule catches and misses; the KLayout decks on the exported GDS are the DRC that
counts.

KiCad applies, of the rules that match a pair, the last one in the file, with its
severity. So a warning rule must not match what an error rule above it checks: the
wide-track rule is a physical clearance, a check of its own, and the slit rule matches
only the tracks it is about.

kicad-cli drops a whole rules file with no message when one token of it is unknown,
and checks the board against its printed-board defaults instead. So the rules use mm
only, and only the item properties Type, Width, Size_X, Size_Y and Net, which KiCad
9.99 was seen to parse. The live test in test_kicad_rules.py runs kicad-cli on them.

The ``.kicad_pro`` carries the board setup floors and the netclasses, so the template
does not open against KiCad's printed-board defaults. A KiCad via stands for the via
array the exporter draws: its drill for one cut, its diameter for one cut plus the
metal enclosure on both sides, from the keys the exporter reads. The netclass keys and
the copper-to-edge floor are KiCad 9.99's: another KiCad version may want others.

CLI::

    python kicad_rules_gen.py            write both files into libs.tech/kicad
    python kicad_rules_gen.py --check    exit 1 if a committed file is not what the JSON gives
    python kicad_rules_gen.py --dir D    write into D instead
"""

import argparse
import json
import re
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

KICAD_DIR = Path(__file__).resolve().parent.parent
RULES_JSON = KICAD_DIR.parent / "klayout/tech/drc/rule_decks/interposer_tech_default.json"
TEMPLATE = KICAD_DIR / "interposer_template.kicad_pcb"
STEM = "interposer_template"

# Per metal, top down: the JSON keys of its minimum width and minimum space.
PORTED = [
    ("TopMetal2", "TM2_a", "TM2_b"),
    ("TopMetal1", "TM1_a", "TM1_b"),
    ("Metal5", "Mn_a", "Mn_b"),
    ("Metal4", "Mn_a", "Mn_b"),
]

# Per via level, top down: the JSON keys of its cut size, its cut space and the metal
# enclosure the exporter draws around a cut.
VIAS = {
    "TopVia2": ("TV2_a", "TV2_b", "TV2_d"),
    "TopVia1": ("TV1_a", "TV1_b", "TV1_d"),
    "Via4": ("Vn_a", "Vn_b", "Vn_c1"),
}

# The via a netclass places by default: the level that lands on its metal from below,
# and Via4 for Metal4, the bottom of the stack.
NETCLASS_VIA = {"TopMetal2": "TopVia2", "TopMetal1": "TopVia1", "Metal5": "Via4",
                "Metal4": "Via4"}

# TM2.bR: the JSON keys of the space, of the width over which a line is wide, and of the
# parallel run over which the space applies, on the metal it belongs to.
WIDE_SPACE = ("TopMetal2", "TM2_bR", "TM2_bR_w", "TM2_bR_cr")

# Slt.c: the JSON key of the widest metal that needs no slit, on every ported metal.
SLIT_WIDTH = "Slt_c"

# KiCad 9.99 ignores every netclass of a project that lacks one of the keys it writes
# (priority and tuning_profile were seen to matter), and then opens the board at its
# printed-board defaults: a 200 um track and clearance, a 600 um via. So each class
# carries KiCad 9.99's full key set. The keys with no interposer meaning take KiCad's
# own values; a differential pair takes the class's width and space. The priority
# KiCad gives Default is the lowest there is, the largest int32; the metal classes
# take theirs in PORTED order.
DEFAULT_PRIORITY = 2**31 - 1
KICAD_CLASS_DEFAULTS = {"bus_width": 12, "wire_width": 6, "line_style": 0,
                        "pcb_color": "rgba(0, 0, 0, 0.000)",
                        "schematic_color": "rgba(0, 0, 0, 0.000)", "tuning_profile": ""}

# The copper-to-edge floor, in mm. The deck states no metal-to-edge rule, so the floor
# only keeps copper inside the outline. KiCad flags copper closer than the floor: 0
# catches copper across the outline but not copper that touches it.
EDGE_FLOOR_MM = 0.001

_COPPER = re.compile(r'\(\d+ "([A-Za-z0-9.]+\.Cu)" signal "([^"]+)"\)')


def copper_layers(board_text):
    """The template's copper layers, as {metal: KiCad layer}."""
    found = {metal: layer for layer, metal in _COPPER.findall(board_text)}
    missing = [metal for metal, _, _ in PORTED if metal not in found]
    if missing:
        raise ValueError(f"{TEMPLATE.name} names no copper layer for {', '.join(missing)}")
    return found


def mm(value_um):
    """A micrometre value as KiCad millimetres, exactly as the JSON writes it."""
    return format((Decimal(str(value_um)) / 1000).normalize(), "f") + "mm"


def render(rules_doc, board_text):
    rules = rules_doc["rules"]
    layers = copper_layers(board_text)
    ported = []
    for _, width, space in PORTED:
        ported += [k for k in (width, space) if k not in ported]
    wide_metal, wide_space, wide_width, wide_run = WIDE_SPACE
    ported += [wide_space, wide_width, wide_run, SLIT_WIDTH]
    missing = [k for k in ported if k not in rules]
    if missing:
        raise ValueError(f"{RULES_JSON.name} has no {', '.join(missing)}")
    klayout_only = [k for k in rules if k not in ported]

    head = [
        "Generated by libs.tech/kicad/scripts/kicad_rules_gen.py from",
        f"libs.tech/klayout/tech/drc/rule_decks/{RULES_JSON.name}",
        f"(version {rules_doc['version']}). Do not edit: change the JSON and run the script.",
        "",
        "KiCad loads the rules file named after the board, so copy this file beside",
        "your board as <board>.kicad_dru. libs.tech/kicad/README.md says what each",
        "rule catches and misses; the KLayout decks on the exported GDS are the DRC",
        "that counts.",
        "",
        "Ported, per copper layer: minimum track width and minimum clearance.",
    ]
    head += [f"  {metal} ({layers[metal]}): {width}, {space}" for metal, width, space in PORTED]
    head += [
        "Ported, on one item's own size:",
        f"  {wide_metal} ({layers[wide_metal]}): {wide_space}, {wide_width}, {wide_run},",
        "  as a clearance from a wide track (a warning) and from a wide pad.",
        f"  Every copper layer: {SLIT_WIDTH}, as a maximum track width (a warning).",
        "",
        "KLayout only:",
    ]
    head += textwrap.wrap(" ".join(klayout_only), 76, initial_indent="  ",
                          subsequent_indent="  ")
    out = ["# " + line if line else "#" for line in head]
    out += ["(version 1)"]
    for metal, width, space in PORTED:
        for key, constraint, what in ((width, "track_width", "width"),
                                      (space, "clearance", "clearance")):
            out += [
                "",
                f'(rule "{metal} {what} ({key})"',
                f'\t(layer "{layers[metal]}")',
                f"\t(constraint {constraint} (min {mm(rules[key])})))",
            ]
    space, width, run = (mm(rules[k]) for k in (wide_space, wide_width, wide_run))
    out += [
        "",
        f'(rule "{wide_metal} clearance from a track wider than {width} ({wide_space})"',
        "\t(severity warning)",
        f'\t(layer "{layers[wide_metal]}")',
        f"\t(condition \"A.Type == 'Track' && A.Width > {width} && A.Net != B.Net\")",
        f"\t(constraint physical_clearance (min {space})))",
        "",
        f'(rule "{wide_metal} clearance from a pad wider than {width} and longer than {run} '
        f'({wide_space})"',
        f'\t(layer "{layers[wide_metal]}")',
        f"\t(condition \"A.Type == 'Pad' && (A.Size_X > {run} || A.Size_Y > {run})"
        f" && A.Size_X > {width} && A.Size_Y > {width}\")",
        f"\t(constraint clearance (min {space})))",
    ]
    widest = mm(rules[SLIT_WIDTH])
    for metal, _, _ in PORTED:
        out += [
            "",
            f'(rule "{metal} track wider than {widest} needs slits ({SLIT_WIDTH})"',
            "\t(severity warning)",
            f'\t(layer "{layers[metal]}")',
            f'\t(condition "A.Width > {widest}")',
            f"\t(constraint track_width (max {widest})))",
        ]
    return "\n".join(out) + "\n"


def _mm(rules, *keys):
    """The sum of the JSON values under keys, in KiCad millimetres."""
    return float(sum(Decimal(str(rules[k])) for k in keys) / 1000)


def via(rules, level):
    """One cut of a via level as a KiCad via: (diameter, drill) in mm."""
    cut, _, enc = VIAS[level]
    return _mm(rules, cut, enc, enc), _mm(rules, cut)


def netclass(rules, name, width, space, level, priority):
    diameter, drill = via(rules, level)
    return {"name": name, "clearance": _mm(rules, space), "track_width": _mm(rules, width),
            "via_diameter": diameter, "via_drill": drill,
            "microvia_diameter": diameter, "microvia_drill": drill,
            "diff_pair_width": _mm(rules, width), "diff_pair_gap": _mm(rules, space),
            "diff_pair_via_gap": _mm(rules, space), "priority": priority,
            **KICAD_CLASS_DEFAULTS}


def render_project(rules_doc):
    rules = rules_doc["rules"]
    missing = [k for keys in VIAS.values() for k in keys if k not in rules]
    if missing:
        raise ValueError(f"{RULES_JSON.name} has no {', '.join(missing)}")
    widths = [rules[w] for _, w, _ in PORTED]
    spaces = [rules[s] for _, _, s in PORTED]
    narrowest = min((w for _, w, _ in PORTED), key=rules.get)
    closest = min((s for _, _, s in PORTED), key=rules.get)
    # Default takes the metal with the widest width and space and the via with the
    # largest landing, so a net left in it is legal on every layer.
    widest = [m for m, w, s in PORTED if rules[w] == max(widths) and rules[s] == max(spaces)]
    if not widest:
        raise ValueError("no metal has both the widest width and the widest space")
    biggest = max(VIAS, key=lambda level: via(rules, level))
    smallest = min(VIAS, key=lambda level: via(rules, level))
    _, width, space = next(p for p in PORTED if p[0] == widest[0])
    classes = [netclass(rules, "Default", width, space, biggest, DEFAULT_PRIORITY)]
    classes += [netclass(rules, m, w, s, NETCLASS_VIA[m], i)
                for i, (m, w, s) in enumerate(PORTED)]
    # The board floors are the smallest values any layer or via level allows. A cut
    # sits inside metal of its own net, so a hole is at least a metal space from
    # another net's copper.
    floors = {
        "min_clearance": _mm(rules, closest),
        "min_track_width": _mm(rules, narrowest),
        "min_via_diameter": via(rules, smallest)[0],
        "min_microvia_diameter": via(rules, smallest)[0],
        "min_microvia_drill": min(_mm(rules, c) for c, _, _ in VIAS.values()),
        "min_through_hole_diameter": min(_mm(rules, c) for c, _, _ in VIAS.values()),
        "min_via_annular_width": min(_mm(rules, e) for _, _, e in VIAS.values()),
        "min_hole_to_hole": min(_mm(rules, s) for _, s, _ in VIAS.values()),
        "min_hole_clearance": _mm(rules, closest),
        "min_copper_edge_clearance": EDGE_FLOOR_MM,
    }
    doc = {
        "board": {"design_settings": {"rules": floors}},
        "meta": {"filename": f"{STEM}.kicad_pro", "version": 3},
        "net_settings": {"classes": classes, "meta": {"version": 5},
                         "netclass_patterns": []},
    }
    return json.dumps(doc, indent=2) + "\n"


def _rules_doc(rules_json):
    return json.loads(Path(rules_json).read_text(encoding="utf-8"))


def generate(rules_json=RULES_JSON, template=TEMPLATE):
    return render(_rules_doc(rules_json), Path(template).read_text(encoding="utf-8"))


def generate_project(rules_json=RULES_JSON):
    return render_project(_rules_doc(rules_json))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, default=KICAD_DIR)
    ap.add_argument("--check", action="store_true",
                    help="compare with the files instead of writing them")
    args = ap.parse_args(argv)
    outputs = {args.dir / f"{STEM}.kicad_dru": generate(),
               args.dir / f"{STEM}.kicad_pro": generate_project()}
    if args.check:
        stale = [p for p, text in outputs.items()
                 if not p.is_file() or p.read_text(encoding="utf-8") != text]
        for p in stale:
            print(f"{p} is not what {RULES_JSON.name} gives; run {Path(__file__).name}",
                  file=sys.stderr)
        return 1 if stale else 0
    for p, text in outputs.items():
        p.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
