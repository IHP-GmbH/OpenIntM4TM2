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
"""The technology manifest, libs.tech/adk/technology.json, against the tree it describes.

The manifest is how the design flow finds this PDK's files and facts. These tests
hold what the tree itself can prove: every member path names a file here, the
routing descriptors are the bytes the manifest binds, the revision is the PDK
version, the grid and database unit are the technology's, the named layers,
libraries and cells exist, the attach methods are the interconnect PDK's for this
carrier, and the occupied GDS pairs are recomputed from the layer sheet, the rule
decks and the descriptors rather than copied. Paths and digests are checked here
too, although the technology package validator checks them again: a release of
this repository must not depend on a tool outside it to notice a renamed file.
Whether the manifest carries every fact a design flow needs is the validator's
to judge, in the design flow's own tests.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from test_bump_mirror import require_sibling
from test_kicad_rules import ROOT, _declared_version
from test_layer_parity import _lyp_entries

MANIFEST = ROOT / "libs.tech/adk/technology.json"
TECH = ROOT / "libs.tech/klayout/tech"
PYCELLS = ROOT / "libs.tech/klayout/python/intm4tm2_pycell_lib/__init__.py"
OFFGRID_DECK = TECH / "drc/rule_decks/3_1_offgrid.drc"

# A deck reads a layer as input(N, D), or through one of the typed readers.
DECK_READ = re.compile(r"\b(?:input|polygons|labels|edges|texts)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)")

# Every key of the manifest that holds a path of this tree. A new path key is
# added here, or test_every_path_key_is_listed fails.
PATH_KEYS = [
    ("layer_properties", "lyp"),
    ("layer_properties", "lyt"),
    ("stack", "path"),
    ("drc", "main_deck"),
    ("drc", "deck_folder"),
    ("drc", "runner"),
    ("drc", "density_deck"),
    ("fill", "halo_tool"),
    ("kicad_template", "board"),
    ("kicad_template", "project"),
    ("kicad_template", "rules"),
]


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _member_paths(manifest):
    """Each listed path key, each routing variant and the file of the test that
    measures the occupied pairs (``file::test``)."""
    paths = {".".join(keys): manifest[keys[0]][keys[1]] for keys in PATH_KEYS}
    for name, variant in manifest["routing"]["variants"].items():
        paths[f"routing.variants.{name}.path"] = variant["path"]
    # A path inside a descriptor resolves against this root as well.
    for name, descriptor in _descriptors(manifest).items():
        paths[f"routing.variants.{name}: fab.layer_set"] = descriptor["fab"]["layer_set"]
    paths["occupied_pairs.measured_by"] = manifest["occupied_pairs"]["measured_by"].split("::", 1)[0]
    return paths


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _layer_names():
    return {name.split(".", 1)[0] for name, _, _ in _lyp_entries()}


def _sheet_pairs():
    return {(layer, datatype) for _, layer, datatype in _lyp_entries()}


def _read_pairs(text):
    return {(int(n), int(d)) for n, d in DECK_READ.findall(text.split("#", 1)[0])}


def _deck_pairs():
    pairs = set()
    decks = sorted((TECH / "drc").rglob("*.drc")) + sorted((TECH / "lvs").rglob("*.lvs"))
    for deck in decks:
        for line in deck.read_text(encoding="utf-8").splitlines():
            pairs |= _read_pairs(line)
    return pairs


def _descriptors(manifest):
    return {name: json.loads((ROOT / variant["path"]).read_text(encoding="utf-8"))
            for name, variant in manifest["routing"]["variants"].items()}


def _pair(text):
    layer, datatype = text.split("/")
    return int(layer), int(datatype)


def _descriptor_pairs():
    """Every pair a descriptor writes: its layer map, its routing layers and
    vias, and the pad openings it attaches through."""
    pairs = set()
    for descriptor in _descriptors(_manifest()).values():
        for purposes in descriptor["gds_layermap"].values():
            pairs |= {_pair(pair) for pair in purposes.values()}
        for shape in descriptor["routing_layers"] + descriptor["vias"]:
            pairs.add((shape["gds_layer"], shape["gds_datatype"]))
        pairs |= {_pair(pair) for pair in descriptor["attachment"]["ubm_opening_layers"]}
    return pairs


def test_every_member_path_names_a_file_of_the_tree():
    missing = {key: path for key, path in _member_paths(_manifest()).items()
               if not (ROOT / path).exists()}
    assert missing == {}


def test_every_path_key_is_listed():
    manifest = _manifest()
    listed = set(_member_paths(manifest).values())
    unlisted = [s for s in _strings(manifest) if s.startswith("libs.tech/")
                and s.split("::", 1)[0] not in listed]
    assert unlisted == []


def test_the_routing_variants_are_the_bytes_the_manifest_binds():
    for name, variant in _manifest()["routing"]["variants"].items():
        actual = hashlib.sha256((ROOT / variant["path"]).read_bytes()).hexdigest()
        assert actual == variant["sha256"], name


@pytest.mark.parametrize("line, pairs", [
    ("m4 = input(50, 0)", {(50, 0)}),
    ("pads = polygons( 9 ,35 ).merged", {(9, 35)}),
    ("names = labels(50, 1); pins = texts(134, 25)", {(50, 1), (134, 25)}),
    ("e = edges(39,0)", {(39, 0)}),
    ("# input(50, 0) in a comment", set()),
    ("m4 = input(50, 0)  # and input(51, 0) after one", {(50, 0)}),
    ("my_input(50, 0)", set()),
    ("input(\"Metal4\")", set()),
], ids=["input", "polygons", "two", "edges", "comment", "trailing", "suffix", "by-name"])
def test_a_deck_read_is_recognised(line, pairs):
    assert _read_pairs(line) == pairs


def test_the_scans_find_the_layers_they_must():
    # An empty or broken scan would pass the subset checks below.
    assert {(134, 0), (9, 0), (9, 35), (39, 0)} <= _deck_pairs()
    assert (50, 0) in _descriptor_pairs()
    assert len(_sheet_pairs()) == 158


def test_occupied_pairs_are_the_measured_ones():
    """The pairs the carrier and its decks read: the layer sheet, every pair a
    DRC or LVS deck reads, and every pair a routing descriptor writes. A deck or
    descriptor pair the sheet does not define is a defect of its own."""
    sheet, decks, descriptors = _sheet_pairs(), _deck_pairs(), _descriptor_pairs()
    assert decks - sheet == set()
    assert descriptors - sheet == set()
    pairs = _manifest()["occupied_pairs"]
    this_test = f"{Path(__file__).resolve().relative_to(ROOT)}::{test_occupied_pairs_are_the_measured_ones.__name__}"
    assert pairs["measured_by"] == this_test
    assert [tuple(p) for p in pairs["pairs"]] == sorted(sheet | decks | descriptors)


def test_the_revision_is_the_pdk_version():
    assert _manifest()["revision"] == _declared_version()


def test_the_newest_changelog_row_is_this_version():
    rows = re.findall(r"^\| (\d+\.\d+\.\d+) \|", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert rows[0] == _declared_version()


def test_the_database_unit_is_the_technology_files():
    lyt = (TECH / "intm4tm2.lyt").read_text(encoding="utf-8")
    assert float(re.search(r"<dbu>([^<]+)</dbu>", lyt).group(1)) == _manifest()["dbu_um"]


def test_the_grid_is_the_offgrid_decks():
    grid_nm = re.search(r"^\s*GRID\s*=\s*(\d+)\.nm\b", OFFGRID_DECK.read_text(encoding="utf-8"), re.M)
    assert int(grid_nm.group(1)) / 1000 == _manifest()["manufacturing_grid_um"]


def test_the_wire_bond_layers_are_on_the_sheet():
    wire_bond = _manifest()["external"]["wire_bond"]
    assert {wire_bond["pad_layer"], wire_bond["opening_layer"]} <= _layer_names()


def test_the_seal_ring_and_pad_cells_are_the_pcell_librarys():
    """The library registers under the name in its register() call and one
    cell per module in its moduleNames, named after the module."""
    source = PYCELLS.read_text(encoding="utf-8")
    library = re.search(r'self\.register\("([^"]+)"\)', source).group(1)
    modules = re.search(r"moduleNames\s*=\s*\[([^\]]*)\]", source).group(1)
    cells = {name[:-len("_code")] for name in re.findall(r"'([^']+_code)'", modules)}
    manifest = _manifest()
    assert manifest["seal_ring"]["library"] == manifest["pcells"]["library"] == library
    assert {manifest["seal_ring"]["cell"], manifest["pcells"]["bondpad"]} <= cells


@pytest.mark.needs_sibling_sources("interconnect_pdk")
def test_the_attach_methods_are_the_interconnect_pdks_for_this_carrier():
    """The methods whose fab anchor names this carrier's attachment adapter,
    in the interconnect PDK's method registry."""
    root = require_sibling("interconnect_pdk")
    registry = json.loads((root / "manifest/interconnect_methods.json").read_text(encoding="utf-8"))
    ours = {name for name, anchor in registry["fab_anchors"].items()
            if anchor.get("adk_attachment_adapter") == _manifest()["id"]}
    methods = sorted(name for name, method in registry["methods"].items() if method.get("fab_anchor") in ours)
    assert methods
    assert _manifest()["attach"]["methods"] == methods
