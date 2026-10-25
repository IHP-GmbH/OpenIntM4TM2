# SPDX-License-Identifier: Apache-2.0
"""Pure tests for menu configuration and job binding; no KLayout process."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


PYTHON_DIR = Path(__file__).resolve().parents[1] / "python"
SPEC = importlib.util.spec_from_file_location(
    "_interposer_verification_menu_test", PYTHON_DIR / "interposer_verification.py")
menu = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = menu
SPEC.loader.exec_module(menu)


@pytest.fixture
def design(tmp_path, monkeypatch):
    """Input bytes suffice: the helper binds files, the existing CLI reads GDS."""
    layout = tmp_path / "layout with spaces.gds"
    layout.write_bytes(b"saved layout input")
    monkeypatch.setattr(menu.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(menu.shutil, "which", lambda name: sys.executable)
    return dict(filename=str(layout), active_top="TOP", technology="intm4tm2",
                dirty=False, cell_names=("TOP", "OTHER"))


def job(design, kind="drc", **options):
    return menu.prepare_job(kind, options, **design)


@pytest.mark.parametrize("kind", ["drc", "lvs"])
def test_defaults_are_independent_and_survive_settings_round_trip(kind):
    original = menu.defaults(kind)
    saved = json.loads(json.dumps(original))
    assert menu.validate_options(kind, saved) == original
    saved["topcell"] = "CUSTOM"
    assert menu.defaults(kind)["topcell"] == ""
    assert menu.validate_options(kind, {}) == original


@pytest.mark.parametrize("cpus", [None, 0, 1, 2, 8])
def test_reset_defaults_are_valid_even_on_one_cpu(monkeypatch, cpus):
    monkeypatch.setattr(menu.os, "cpu_count", lambda: cpus)
    options = menu.defaults("drc")
    assert 1 <= options["threads"] <= max(1, cpus or 1)
    assert menu.validate_options("drc", options) == options


@pytest.mark.parametrize("kind, saved", [
    ("drc", None), ("lvs", []), ("drc", {"unknown": True}),
    ("drc", {"threads": True}), ("drc", {"threads": "2"}),
    ("drc", {"threads": 2.0}), ("drc", {"density_sanity": 1}),
    ("drc", {"output_root": None}), ("lvs", {"combine_devices": 1}),
    ("lvs", {"reference": False}), ("lvs", {"topcell": 7}),
])
def test_corrupt_or_wrongly_typed_settings_are_not_silently_reset(kind, saved):
    with pytest.raises(ValueError):
        menu.validate_options(kind, saved)


@pytest.mark.parametrize("kind, saved", [
    ("drc", {"run_mode": "automatic"}),
    ("drc", {"selection": "assembly"}),
    ("drc", {"selection": "not-a-deck"}),
    ("drc", {"threads": 0}), ("drc", {"threads": 9}),
    ("lvs", {"run_mode": "tiling"}),
    ("lvs", {"mode": "best-effort"}),
])
def test_unsupported_scope_and_resource_options_refuse(monkeypatch, kind, saved):
    monkeypatch.setattr(menu.os, "cpu_count", lambda: 8)
    with pytest.raises(ValueError):
        menu.validate_options(kind, saved)


@pytest.mark.parametrize("kind", ["assembly", "", None])
def test_unknown_verification_kind_refuses(kind):
    with pytest.raises(ValueError):
        menu.defaults(kind)


def test_every_registry_deck_can_be_selected():
    for deck in menu.DECKS:
        assert menu.validate_options("drc", {"selection": deck})["selection"] == deck


def test_no_deck_takes_the_name_of_a_whole_run_selection():
    # A deck called "default" or "full" would replace that entry's label and
    # could never run alone, since those names take the whole-run branches.
    # The full list also pins the dialog's order.
    assert list(menu.SELECTIONS) == ["default", "full", *menu.DECKS]


def test_each_run_gets_a_new_child_and_preserves_existing_outputs(design, tmp_path):
    parent = tmp_path / "results with spaces"
    parent.mkdir()
    sentinel = parent / "old-report.txt"
    sentinel.write_text("accepted old report")
    first = job(design, output_root=str(parent))
    existing_log = first.run_dir / "run.log"
    existing_log.write_text("first run")
    second = job(design, output_root=str(parent))
    assert first.run_dir != second.run_dir
    assert first.run_dir.parent == second.run_dir.parent == parent
    assert first.run_dir.is_dir() and second.run_dir.is_dir()
    assert sentinel.read_text() == "accepted old report"
    assert existing_log.read_text() == "first run"


def test_default_parent_is_beside_the_saved_layout(design):
    result = job(design)
    assert result.run_dir.parent == Path(design["filename"]).parent / "verification_runs"


def test_argv_preserves_paths_and_topcell_without_shell_expansion(design, tmp_path, monkeypatch):
    executable = str(tmp_path / "python executable")
    monkeypatch.setattr(menu.shutil, "which", lambda name: executable)
    top = "TOP;$(do_not_execute)/odd cell"
    design["cell_names"] = (top,)
    result = job(design, topcell=top, output_root=str(tmp_path / "run root"))
    argv = result.command
    assert isinstance(argv, tuple)
    assert argv[:3] == (executable, "-u", str(menu.TECH / "drc" / "run_drc.py"))
    assert argv[argv.index("--path") + 1] == design["filename"]
    assert argv[argv.index("--topcell") + 1] == top
    assert argv[argv.index("--run_dir") + 1] == str(result.run_dir)
    assert argv[argv.index("--mp") + 1] == "1"
    assert "-c" not in argv and "shell=True" not in argv
    assert result.run_dir.parent == tmp_path / "run root"
    assert "/" not in result.run_dir.name and "$" not in result.run_dir.name


def test_drc_default_excludes_density_and_full_always_pairs_sanity(design):
    default = job(design)
    assert "DENSITY EXCLUDED" in default.scope
    assert "--density" not in default.command and "--deck" not in default.command
    for sanity in (False, True):
        full = job(design, selection="full", density_sanity=sanity)
        assert "--density" in full.command and "--density_sanity" in full.command
        assert "--deck" not in full.command
        assert "including density and boundary sanity" in full.scope


@pytest.mark.parametrize("sanity", [False, True])
def test_density_only_records_its_actual_scope(design, sanity):
    result = job(design, selection="density", density_sanity=sanity)
    assert result.command[result.command.index("--deck") + 1] == "density"
    assert ("--density_sanity" in result.command) is sanity
    assert "other DRC not run" in result.scope
    assert ("with boundary sanity" if sanity else "without boundary sanity") in result.scope


@pytest.mark.parametrize("selection", list(menu.SELECTIONS))
def test_every_drc_scope_says_the_seal_ring_requirement_is_not_checked(design, selection):
    result = job(design, selection=selection)
    assert "--require_sealring" not in result.command
    assert menu.NO_SEAL_REQ in result.scope


def test_single_physical_deck_does_not_claim_other_checks(design):
    result = job(design, selection="metaln")
    assert result.command[result.command.index("--deck") + 1] == "metaln"
    assert "--density" not in result.command
    assert "density and other decks not run" in result.scope


@pytest.mark.parametrize("reference", ["", "   ", "/nonexistent/reference.cir"])
def test_lvs_reference_is_required_and_never_downgrades(design, reference):
    with pytest.raises(ValueError, match="Reference"):
        job(design, "lvs", reference=reference)
    assert not (Path(design["filename"]).parent / "verification_runs").exists()


def test_lvs_reference_and_device_options_are_explicit(design, tmp_path):
    reference = tmp_path / "reference with spaces.cir"
    reference.write_text(".SUBCKT TOP A B\n.ENDS TOP\n")
    result = job(design, "lvs", reference=str(reference), combine_devices=True)
    assert result.reference == reference
    assert result.reference_digest == menu.file_digest(reference)
    assert result.command[result.command.index("--netlist") + 1] == str(reference)
    assert "--combine_devices" in result.command
    assert "--layout" in result.command and "--path" not in result.command
    assert "against reference SPICE" in result.scope


def test_connectivity_requires_explicit_choice_and_does_not_claim_comparison(design):
    assert menu.defaults("lvs")["mode"] == "reference"
    result = job(design, "lvs", mode="connectivity", reference="unused.cir")
    assert result.reference is None and result.reference_digest is None
    assert "--netlist" not in result.command and "--combine_devices" not in result.command
    assert "connectivity ONLY; no schematic comparison" in result.scope


@pytest.mark.parametrize("changes, message", [
    ({"dirty": True}, "Save the layout"),
    ({"filename": ""}, "Save the layout"),
    ({"filename": "/nonexistent/layout.gds"}, "does not exist"),
    ({"technology": "sg13g2"}, "intm4tm2"),
    ({"technology": ""}, "intm4tm2"),
    ({"active_top": "MISSING"}, "Top cell"),
    ({"active_top": ""}, "Top cell"),
])
def test_invalid_active_layout_refuses_before_output_allocation(design, changes, message):
    saved_path = Path(design["filename"])
    design.update(changes)
    with pytest.raises(ValueError, match=message):
        job(design)
    assert not (saved_path.parent / "verification_runs").exists()


def test_selected_top_must_exist_and_can_override_active_top(design):
    with pytest.raises(ValueError, match="Top cell"):
        job(design, topcell="MISSING")
    other = job(design, topcell=" OTHER ")
    assert other.topcell == "OTHER" and other.scope.endswith("; on cell OTHER")


def test_missing_interpreter_refuses_before_output_allocation(design, monkeypatch):
    monkeypatch.setattr(menu.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="Python executable not found"):
        job(design, python="missing-python")
    assert not (Path(design["filename"]).parent / "verification_runs").exists()


@pytest.mark.parametrize("target, removed", [("layout", False), ("layout", True),
                                           ("reference", False), ("reference", True)])
def test_changed_or_removed_input_cannot_produce_pass(design, tmp_path, target, removed):
    reference = tmp_path / "reference.cir"
    reference.write_text("reference before run")
    result = job(design, "lvs", reference=str(reference))
    assert result.inputs_unchanged()
    changed = result.layout if target == "layout" else reference
    if removed:
        changed.unlink()
    else:
        changed.write_bytes(b"different input bytes")
    assert not result.inputs_unchanged()
    assert menu.result_description(result, 0).startswith("INCONCLUSIVE")


@pytest.mark.parametrize("returncode, prefix", [(0, "PASS within scope:"),
                                               (1, "NOT PASSED"),
                                               (2, "ERROR / INCONCLUSIVE"),
                                               (-9, "ERROR / INCONCLUSIVE")])
def test_result_preserves_process_outcome_and_scope(design, returncode, prefix):
    result = job(design)
    text = menu.result_description(result, returncode)
    assert text.startswith(prefix)
    if returncode in (0, 1):
        assert result.scope in text
    if returncode == 0:
        assert "Not assembly or fabrication signoff" in text
    assert menu.result_description(result, returncode, cancelled=True).startswith("CANCELLED")
