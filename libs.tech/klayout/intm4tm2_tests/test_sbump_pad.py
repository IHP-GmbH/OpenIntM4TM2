#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The solder-bump pad: its fab anchor in bump_mirror, the Padb rules it is
judged by, and the SolderBumpPad cell the generator places.

A die on SAC305 solder bumps lands on the solder-bump anchor (9/36, 41/36,
99/36), not on the Cu-pillar one. Before this anchor had its own entry, a
solder-bump die was drawn as CuPillarPad and pre-checked against the Padc
rules (a 75 um pitch where the bump needs 130). These cases hold each half:
the anchor names its own cell, layers and rule set, the validator reports
Padb ids at Padb values, and the solderbump deck passes the pads the
generator places at pitch and flags them below it.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_KLAYOUT / "python"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"
TECH_JSON = DRC_DIR / "rule_decks" / "interposer_tech_default.json"
sys.path.insert(0, str(SCRIPTS_DIR))

from bump_mirror import (  # noqa: E402
    CUPILLAR_FAB_LAYERS,
    FAB_ANCHORS,
    BumpLocation,
    CuPillarGenerator,
    DrcParams,
    DrcValidator,
    fab_anchor,
    fab_anchor_openings,
)
# The sibling door: a case marked needs_interconnect gets the interconnect
# PDK root in INTERCONNECT_PDK_ROOT, or skips when nothing names one.
from test_bump_mirror import _sibling_door, needs_interconnect  # noqa: E402,F401

KLAYOUT_BIN = shutil.which("klayout")

SBUMP = "ihp_sbump_anchor"
SBUMP_LAYERS = {(134, 0), (9, 36), (41, 36), (99, 36)}
SOLDER_BALL = ("SolderBall", 502, 36)   # sbump_sac305's layers_3d


def _row(pitch_um, count=3):
    return [BumpLocation("U1", f"P{i}", i * pitch_um, 0.0) for i in range(count)]


def _sbump_generator():
    # No enclosure named: the anchor's Padb.c (10 um), never the pillar's 7.5.
    return CuPillarGenerator(bodies=[SOLDER_BALL], passiv_opening_um=60.0,
                             fab_anchor_id=SBUMP)


def test_each_anchor_names_its_cell_rules_and_openings():
    assert (fab_anchor(SBUMP).pcell, fab_anchor(SBUMP).rules) == ("SolderBumpPad", "Padb")
    assert set(fab_anchor(SBUMP).layers.values()) == SBUMP_LAYERS
    assert fab_anchor_openings(SBUMP) == {
        "passiv": (9, 36), "dfpad": (41, 36), "recog": (99, 36)}
    assert fab_anchor_openings("ihp_cupillar_anchor") == {
        "passiv": (9, 35), "dfpad": (41, 35), "recog": (99, 35)}
    assert CUPILLAR_FAB_LAYERS is FAB_ANCHORS["ihp_cupillar_anchor"].layers
    with pytest.raises(ValueError, match="'ihp_hybrid_anchor'.*ihp_cupillar_anchor, ihp_sbump_anchor"):
        fab_anchor("ihp_hybrid_anchor")


@needs_interconnect
def test_the_openings_are_what_the_manifest_lists_for_each_anchor():
    import os
    manifest = Path(os.environ["INTERCONNECT_PDK_ROOT"], "manifest",
                    "interconnect_methods.json")
    anchors = json.loads(manifest.read_text())["fab_anchors"]
    for anchor_id in FAB_ANCHORS:
        listed = {key: tuple(int(n) for n in value.split("/"))
                  for key, value in anchors[anchor_id]["layers"].items()}
        assert fab_anchor_openings(anchor_id) == listed, anchor_id


def test_solder_bump_params_are_the_padb_rules(tmp_path):
    rules = json.loads(TECH_JSON.read_text())["rules"]
    shipped = DrcParams.load(fab_anchor_id=SBUMP)
    assert shipped.rules == "Padb"
    assert (shipped.diameter_um, shipped.min_spacing_um, shipped.min_enclosure_um,
            shipped.min_edgeseal_um, shipped.min_pitch_um) == tuple(
        rules[f"Padb_{x}"] for x in "abcde")
    # Without a tech JSON the fallback is the same rule set, never Padc.
    assert DrcParams.load(str(tmp_path / "absent.json"), fab_anchor_id=SBUMP) == shipped
    assert sorted(shipped.to_dict()) == [
        "Padb_a_diameter_um", "Padb_b_min_spacing_um", "Padb_c_min_enclosure_um",
        "Padb_d_min_edgeseal_um", "Padb_e_min_pitch_um"]


def test_a_solder_bump_row_is_judged_by_padb_rules():
    params = DrcParams.load(fab_anchor_id=SBUMP)
    at_pitch = DrcValidator(params).validate(_row(130.0), 60.0, 10.0, max_detail=None)
    assert at_pitch.passed
    assert {r.rule for r in at_pitch.results} == {f"Padb.{x}" for x in "abcef"}

    tight = DrcValidator(params).validate(_row(125.0), 60.0, 10.0, max_detail=None)
    errors = {r.rule for r in tight.results if r.severity == "error"}
    assert errors == {"Padb.b", "Padb.e"}
    # The row the Padc rules would have let through.
    assert DrcValidator(DrcParams.load()).validate(_row(125.0), 35.0, 7.5).passed


@pytest.mark.parametrize("opening_um", [55.0, 65.0])
def test_a_solder_bump_opening_other_than_60_is_refused(opening_um):
    # Exact, both ways: the deck's Padb.a only flags an opening under 60.
    report = DrcValidator(DrcParams.load(fab_anchor_id=SBUMP)).validate(
        _row(200.0), opening_um, 10.0, max_detail=None)
    assert {r.rule for r in report.results if r.severity == "error"} == {"Padb.a"}


def test_a_solder_bump_takes_its_opening_from_the_method_not_the_body():
    # 44 um is a Table 6.1 pillar body; for a solder bump it still says nothing.
    generator = CuPillarGenerator(fab_anchor_id=SBUMP)
    with pytest.raises(ValueError, match="passiv_opening_um"):
        generator.add_bumps(_row(130.0), body_diameter_um=44)


@needs_interconnect
def test_generated_solder_bump_pads_draw_the_sbump_layers_and_the_ball(tmp_path):
    import klayout.db as kdb

    generator = _sbump_generator()
    assert generator.add_bumps(_row(130.0), body_diameter_um=80) == 3
    path = tmp_path / "sbump.gds"
    generator.write(str(path))

    layout = kdb.Layout()
    layout.read(str(path))
    top = layout.top_cell()
    drawn = {}
    for idx in layout.layer_indexes():
        if not top.bbox_per_layer(idx).empty():
            info = layout.get_info(idx)
            drawn[(info.layer, info.datatype)] = idx
    assert set(drawn) == SBUMP_LAYERS | {SOLDER_BALL[1:]}
    names = {c.name for c in layout.each_cell()}
    assert "SolderBumpPad" not in names
    assert not any(n.startswith("CUPILLAR") for n in names), names
    pad = layout.cell("SBUMP_80um_manifest")
    widths = {key: pad.bbox_per_layer(idx).width() for key, idx in drawn.items()}
    # 1 nm dbu: opening 60, landing 60 + 2 * 10, ball 80.
    assert widths == {(9, 36): 60000, (41, 36): 80000, (99, 36): 80000,
                      (134, 0): 80000, (502, 36): 80000}


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
@needs_interconnect
@pytest.mark.parametrize("pitch_um,expected", [(130.0, set()), (125.0, {"Padb.b"})])
def test_the_pad_decks_pass_generated_pads_at_pitch_and_flag_them_below(
        tmp_path, pitch_um, expected):
    """The deck on the generator's own output: at 130 um no pad deck flags
    anything, the pillar deck included; 5 um closer, Padb.b alone fires (the
    deck leaves the pitch to Padb.b, which is the same check while Padb.a
    holds the opening at 60)."""
    if str(DRC_DIR) not in sys.path:
        sys.path.insert(0, str(DRC_DIR))
    from run_drc import get_violation_counts, run_deck

    generator = _sbump_generator()
    generator.add_bumps(_row(pitch_um), body_diameter_um=80)
    gds = tmp_path / "sbump.gds"
    generator.write(str(gds))

    flagged = set()
    for deck in ("solderbump", "copperpillar", "pad", "ubm_floor"):
        report = run_deck(str(DRC_DIR / "intm4tm2.drc"), deck, str(gds), "TOP",
                          tmp_path, threads=2, run_mode="flat")
        flagged |= set(get_violation_counts(report))
    assert flagged == expected
