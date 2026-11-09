#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Tests for bump_mirror.py -- Cu-Pillar Generator with DRC Pre-Validation."""

import json
import math
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

# Add bump_mirror's directory to path
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "python"
sys.path.insert(0, str(SCRIPTS_DIR))

import bump_mirror
from bump_mirror import (
    BumpLocation,
    CUPILLAR_TABLE_6_1,
    CuPillarGenerator,
    DEFAULT_BODY_DIAMETER,
    DrcParams,
    DrcValidator,
    ValidationReport,
    compute_bump_locations,
    load_chiplet_positions,
    load_pin_lists,
    main,
)

# ---------------------------------------------------------------------------
# Siblings. gds2kicad supplies PinList, and the interconnect PDK the 3D bump
# bodies (bump_mirror._get_bump3d). A case that reads either one carries
# needs_sibling_sources with the sibling's id, through the two marks below.
# The tiered runner's T0 deselects those cases, and its T1 runs them with the
# pinned siblings exported under $HCDP_ROOT.
#
# The door, per sibling: the explicit variable decides when it is set
# (GDS_TO_KICAD_ROOT, INTERCONNECT_PDK_ROOT; empty counts as unset), otherwise
# $HCDP_ROOT/<id>. It never walks up from this checkout. The case gets the
# chosen root in that variable, so the exporter reads the same tree the door
# checked. A root that does not hold the sibling fails and names the root,
# because the run asked for a tree and did not get it. With neither set, the
# case skips, which is what a bare clone does.
# ---------------------------------------------------------------------------

SIBLINGS = {
    # id: (variable, the file that identifies the tree)
    "gds_to_kicad": ("GDS_TO_KICAD_ROOT", Path("pin_list.py")),
    "interconnect_pdk": ("INTERCONNECT_PDK_ROOT",
                         Path("libs.tech", "klayout", "python", "bump3d_generator.py")),
}


def sibling_root(sibling_id, environ=os.environ):
    """The root the door picks for a sibling, or None when nothing names one."""
    variable, _ = SIBLINGS[sibling_id]
    if environ.get(variable):
        return Path(environ[variable]).expanduser()
    if environ.get("HCDP_ROOT"):
        return Path(environ["HCDP_ROOT"]) / sibling_id
    return None


def require_sibling(sibling_id, environ=os.environ):
    """The sibling's root, or end the case: fail on a wrong root, skip on none."""
    variable, marker = SIBLINGS[sibling_id]
    root = sibling_root(sibling_id, environ)
    if root is None:
        pytest.skip(f"needs the {sibling_id} sibling: set {variable} or HCDP_ROOT")
    if not (root / marker).is_file():
        pytest.fail(f"{root} does not hold {sibling_id} (no {marker}). Refusing to "
                    "look anywhere else: the case would measure a tree nobody selected.",
                    pytrace=False)
    return root


needs_gds2kicad = pytest.mark.needs_sibling_sources("gds_to_kicad")
needs_interconnect = pytest.mark.needs_sibling_sources("interconnect_pdk")


@pytest.fixture(autouse=True)
def _sibling_door(request, monkeypatch):
    for mark in request.node.iter_markers("needs_sibling_sources"):
        for sibling_id in mark.args:
            variable, _ = SIBLINGS[sibling_id]
            monkeypatch.setenv(variable, str(require_sibling(sibling_id)))


# ---------------------------------------------------------------------------
# The door itself. These run everywhere, with no sibling of their own: each
# hands the door its own environment and fake trees under tmp_path.
# ---------------------------------------------------------------------------


def _fake_sibling(root, sibling_id):
    """A directory that passes for the sibling, i.e. one holding its marker file."""
    marker = root / SIBLINGS[sibling_id][1]
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")
    return root


@pytest.mark.parametrize("sibling_id", sorted(SIBLINGS))
class TestSiblingDoor:
    """The variable decides, then $HCDP_ROOT/<id>; a wrong root fails, none skips."""

    def test_the_variable_wins_over_hcdp_root(self, tmp_path, sibling_id):
        variable, _ = SIBLINGS[sibling_id]
        chosen = _fake_sibling(tmp_path / "chosen", sibling_id)
        _fake_sibling(tmp_path / "umbrella" / sibling_id, sibling_id)
        environ = {variable: str(chosen), "HCDP_ROOT": str(tmp_path / "umbrella")}
        assert require_sibling(sibling_id, environ) == chosen

    def test_hcdp_root_names_the_sibling_when_the_variable_is_unset(self, tmp_path, sibling_id):
        exported = _fake_sibling(tmp_path / "siblings" / sibling_id, sibling_id)
        assert require_sibling(sibling_id, {"HCDP_ROOT": str(tmp_path / "siblings")}) == exported

    def test_an_empty_variable_counts_as_unset(self, tmp_path, sibling_id):
        variable, _ = SIBLINGS[sibling_id]
        exported = _fake_sibling(tmp_path / "siblings" / sibling_id, sibling_id)
        environ = {variable: "", "HCDP_ROOT": str(tmp_path / "siblings")}
        assert require_sibling(sibling_id, environ) == exported

    def test_a_wrong_variable_fails_and_names_the_root(self, tmp_path, sibling_id):
        variable, _ = SIBLINGS[sibling_id]
        _fake_sibling(tmp_path / "siblings" / sibling_id, sibling_id)
        environ = {variable: str(tmp_path / "typo"), "HCDP_ROOT": str(tmp_path / "siblings")}
        with pytest.raises(pytest.fail.Exception) as excinfo:
            require_sibling(sibling_id, environ)
        assert str(tmp_path / "typo") in str(excinfo.value)

    def test_a_sibling_missing_from_hcdp_root_fails(self, tmp_path, sibling_id):
        # The runner's T1 exports every sibling it selects for, so an absent one
        # is a broken export, never a reason to skip.
        with pytest.raises(pytest.fail.Exception) as excinfo:
            require_sibling(sibling_id, {"HCDP_ROOT": str(tmp_path)})
        assert str(tmp_path / sibling_id) in str(excinfo.value)

    def test_nothing_named_skips(self, sibling_id):
        assert sibling_root(sibling_id, {}) is None
        with pytest.raises(pytest.skip.Exception):
            require_sibling(sibling_id, {})


# ---------------------------------------------------------------------------
# The interconnect-PDK resolver behind the 3D bodies. This one is in the
# exporter, and the door above hands it its root: the full coupon path
# reaches _get_bump3d, so a mistyped INTERCONNECT_PDK_ROOT that fell through to
# an ambient checkout would draw bodies from a tree nobody selected and produce
# a GDS that looks complete. Fake trees under tmp_path, so no host sibling can
# decide the outcome.
# ---------------------------------------------------------------------------


def _fake_interconnect(root, body="def add_3d_bodies(*a, **k):\n    return None\n"):
    """A directory laid out like the interconnect PDK, holding a generator."""
    py_dir = root.joinpath("libs.tech", "klayout", "python")
    py_dir.mkdir(parents=True, exist_ok=True)
    (py_dir / "bump3d_generator.py").write_text(body)
    return root


class TestInterconnectRootResolution:
    """INTERCONNECT_PDK_ROOT decides when set, and refuses rather than drift."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        # Both caches cleared, and the module's idea of where it lives pointed
        # into tmp_path so the sibling walk searches a tree we control.
        monkeypatch.setattr(bump_mirror, "_bump3d_cache", None)
        # raising=False so this class stays runnable against a bump_mirror that
        # has no root-keyed cache: an ablation must fail on the behaviour under
        # test, not on a missing attribute in the fixture.
        monkeypatch.setattr(bump_mirror, "_bump3d_cache_root", None, raising=False)
        here = tmp_path / "ambient" / "interposer" / "libs.tech" / "klayout" / "python"
        here.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bump_mirror, "__file__", str(here / "bump_mirror.py"))
        saved_path = list(sys.path)
        saved_mod = sys.modules.pop("bump3d_generator", None)
        yield
        sys.path[:] = saved_path
        sys.modules.pop("bump3d_generator", None)
        if saved_mod is not None:
            sys.modules["bump3d_generator"] = saved_mod

    def test_a_valid_explicit_root_is_the_tree_that_gets_loaded(
            self, monkeypatch, tmp_path):
        chosen = _fake_interconnect(tmp_path / "chosen")
        _fake_interconnect(tmp_path / "ambient" / "interconnect_pdk")
        monkeypatch.setenv("INTERCONNECT_PDK_ROOT", str(chosen))
        mod = bump_mirror._get_bump3d()
        assert Path(mod.__file__).is_relative_to(chosen)

    def test_an_invalid_explicit_root_refuses_the_ambient_sibling(
            self, monkeypatch, tmp_path):
        # The ambient tree is right there and perfectly usable. Using it would
        # be the silent failure: bodies from a tree nobody asked for.
        _fake_interconnect(tmp_path / "ambient" / "interconnect_pdk")
        monkeypatch.setenv("INTERCONNECT_PDK_ROOT", str(tmp_path / "typo"))
        with pytest.raises(RuntimeError) as excinfo:
            bump_mirror._get_bump3d()
        assert str(tmp_path / "typo") in str(excinfo.value)

    def test_an_unset_root_still_discovers_the_sibling(
            self, monkeypatch, tmp_path):
        ambient = _fake_interconnect(tmp_path / "ambient" / "interconnect_pdk")
        monkeypatch.delenv("INTERCONNECT_PDK_ROOT", raising=False)
        mod = bump_mirror._get_bump3d()
        assert Path(mod.__file__).is_relative_to(ambient)

    def test_an_empty_root_counts_as_unconfigured_not_as_invalid(
            self, monkeypatch, tmp_path):
        # Exporting the variable empty is how a shell says "not set", so it
        # discovers rather than refusing. Only a non-empty wrong value refuses.
        ambient = _fake_interconnect(tmp_path / "ambient" / "interconnect_pdk")
        monkeypatch.setenv("INTERCONNECT_PDK_ROOT", "")
        mod = bump_mirror._get_bump3d()
        assert mod is not None and Path(mod.__file__).is_relative_to(ambient)

    def test_an_unset_root_with_no_sibling_is_still_a_quiet_absence(
            self, monkeypatch):
        monkeypatch.delenv("INTERCONNECT_PDK_ROOT", raising=False)
        assert bump_mirror._get_bump3d() is None

    def test_a_cached_selection_does_not_survive_a_new_explicit_root(
            self, monkeypatch, tmp_path):
        # Resolve once with discovery, then point the variable somewhere wrong.
        _fake_interconnect(tmp_path / "ambient" / "interconnect_pdk")
        monkeypatch.delenv("INTERCONNECT_PDK_ROOT", raising=False)
        assert bump_mirror._get_bump3d() is not None
        monkeypatch.setenv("INTERCONNECT_PDK_ROOT", str(tmp_path / "typo"))
        with pytest.raises(RuntimeError):
            bump_mirror._get_bump3d()

    def test_a_cached_absence_does_not_survive_a_valid_explicit_root(
            self, monkeypatch, tmp_path):
        monkeypatch.delenv("INTERCONNECT_PDK_ROOT", raising=False)
        assert bump_mirror._get_bump3d() is None      # caches the absence
        chosen = _fake_interconnect(tmp_path / "chosen")
        monkeypatch.setenv("INTERCONNECT_PDK_ROOT", str(chosen))
        mod = bump_mirror._get_bump3d()
        assert mod is not None and Path(mod.__file__).is_relative_to(chosen)

    def test_a_broken_generator_keeps_its_own_traceback(
            self, monkeypatch, tmp_path):
        # Discovery is guarded; the import is not. A syntax or dependency error
        # must not be reported as "interconnect_pdk not found".
        chosen = _fake_interconnect(tmp_path / "chosen",
                                    body="raise ValueError('boom')\n")
        monkeypatch.setenv("INTERCONNECT_PDK_ROOT", str(chosen))
        with pytest.raises(ValueError, match="boom"):
            bump_mirror._get_bump3d()
        assert bump_mirror._bump3d_cache is None       # a failed import is not cached


class TestPinListRootResolution:
    """GDS_TO_KICAD_ROOT decides when set, and refuses rather than drift."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        here = tmp_path / "ambient" / "interposer" / "libs.tech" / "klayout" / "python"
        here.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bump_mirror, "__file__", str(here / "bump_mirror.py"))
        # raising=False, as for _bump3d_cache_root above: an ablation without
        # the binding must fail on the behaviour, not on the fixture.
        monkeypatch.setattr(bump_mirror, "_pinlist_file", None, raising=False)
        saved_path = list(sys.path)
        saved_mod = sys.modules.pop("pin_list", None)
        yield
        sys.path[:] = saved_path
        sys.modules.pop("pin_list", None)
        if saved_mod is not None:
            sys.modules["pin_list"] = saved_mod

    @staticmethod
    def _fake_gds2kicad(root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "pin_list.py").write_text("class PinList:\n    pass\n")
        return root

    def test_a_valid_explicit_root_is_the_tree_that_gets_loaded(
            self, monkeypatch, tmp_path):
        chosen = self._fake_gds2kicad(tmp_path / "chosen")
        self._fake_gds2kicad(tmp_path / "ambient" / "gds_to_kicad")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(chosen))
        bump_mirror._import_pinlist()
        assert Path(sys.modules["pin_list"].__file__).is_relative_to(chosen)

    def test_an_invalid_explicit_root_refuses_the_ambient_sibling(
            self, monkeypatch, tmp_path):
        self._fake_gds2kicad(tmp_path / "ambient" / "gds_to_kicad")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(tmp_path / "typo"))
        path_before = list(sys.path)
        with pytest.raises(bump_mirror.CliInputError) as excinfo:
            bump_mirror._import_pinlist()
        assert str(tmp_path / "typo") in str(excinfo.value)
        assert "pin_list" not in sys.modules
        assert sys.path == path_before

    def test_an_empty_root_counts_as_unconfigured_not_as_invalid(
            self, monkeypatch, tmp_path):
        ambient = self._fake_gds2kicad(tmp_path / "ambient" / "gds_to_kicad")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", "")
        bump_mirror._import_pinlist()
        assert Path(sys.modules["pin_list"].__file__).is_relative_to(ambient)

    def test_the_loaded_pin_list_is_reused_through_another_path_to_it(
            self, monkeypatch, tmp_path):
        chosen = self._fake_gds2kicad(tmp_path / "chosen")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(chosen))
        bump_mirror._import_pinlist()
        first = sys.modules["pin_list"]
        (tmp_path / "link").symlink_to(chosen)
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(tmp_path / "link"))
        bump_mirror._import_pinlist()
        assert sys.modules["pin_list"] is first

    def test_the_root_goes_on_sys_path_as_found_not_resolved(
            self, monkeypatch, tmp_path):
        chosen = self._fake_gds2kicad(tmp_path / "chosen")
        (tmp_path / "link").symlink_to(chosen)
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(tmp_path / "link"))
        bump_mirror._import_pinlist()
        assert str(tmp_path / "link") in sys.path
        assert str((tmp_path / "link").resolve()) not in sys.path

    def test_a_pin_list_loaded_from_another_root_is_refused(
            self, monkeypatch, tmp_path):
        ambient = self._fake_gds2kicad(tmp_path / "ambient" / "gds_to_kicad")
        monkeypatch.delenv("GDS_TO_KICAD_ROOT", raising=False)
        bump_mirror._import_pinlist()
        first = sys.modules["pin_list"]
        chosen = self._fake_gds2kicad(tmp_path / "chosen")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(chosen))
        path_before = list(sys.path)
        with pytest.raises(bump_mirror.CliInputError) as excinfo:
            bump_mirror._import_pinlist()
        assert str(ambient) in str(excinfo.value) and str(chosen) in str(excinfo.value)
        assert sys.modules["pin_list"] is first
        assert sys.path == path_before

    def test_an_invalid_root_is_refused_with_a_pin_list_already_loaded(
            self, monkeypatch, tmp_path):
        self._fake_gds2kicad(tmp_path / "ambient" / "gds_to_kicad")
        monkeypatch.delenv("GDS_TO_KICAD_ROOT", raising=False)
        bump_mirror._import_pinlist()
        first = sys.modules["pin_list"]
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(tmp_path / "typo"))
        with pytest.raises(bump_mirror.CliInputError) as excinfo:
            bump_mirror._import_pinlist()
        assert str(tmp_path / "typo") in str(excinfo.value)
        assert sys.modules["pin_list"] is first

    def test_a_pin_list_that_fails_to_load_is_not_left_behind(
            self, monkeypatch, tmp_path):
        chosen = tmp_path / "chosen"
        chosen.mkdir()
        (chosen / "pin_list.py").write_text("raise ValueError('boom')\n")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(chosen))
        with pytest.raises(ValueError, match="boom"):
            bump_mirror._import_pinlist()
        assert "pin_list" not in sys.modules

    def test_a_failed_load_binds_its_checkout_and_reruns_that_file(
            self, monkeypatch, tmp_path):
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "pin_list.py").write_text("raise ValueError('boom')\n")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(broken))
        with pytest.raises(ValueError, match="boom"):
            bump_mirror._import_pinlist()
        chosen = self._fake_gds2kicad(tmp_path / "chosen")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(chosen))
        path_before = list(sys.path)
        with pytest.raises(bump_mirror.CliInputError) as excinfo:
            bump_mirror._import_pinlist()
        assert str(broken) in str(excinfo.value) and str(chosen) in str(excinfo.value)
        assert "pin_list" not in sys.modules
        assert sys.path == path_before
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(broken))
        with pytest.raises(ValueError, match="boom"):    # the same file is run again
            bump_mirror._import_pinlist()

    def test_the_binding_holds_when_another_pin_list_was_imported_meanwhile(
            self, monkeypatch, tmp_path):
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "pin_list.py").write_text("raise ValueError('boom')\n")
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(broken))
        with pytest.raises(ValueError, match="boom"):
            bump_mirror._import_pinlist()
        chosen = self._fake_gds2kicad(tmp_path / "chosen")
        imported = types.ModuleType("pin_list")    # as a plain import would leave it
        imported.__file__ = str(chosen / "pin_list.py")
        # A loader that wrongly reuses it must fail on the missing refusal, not
        # on an AttributeError.
        imported.PinList = object
        sys.modules["pin_list"] = imported
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(chosen))
        with pytest.raises(bump_mirror.CliInputError) as excinfo:
            bump_mirror._import_pinlist()
        assert str(broken) in str(excinfo.value) and str(chosen) in str(excinfo.value)
        assert sys.modules["pin_list"] is imported

    def test_a_loaded_pin_list_without_a_source_file_is_refused(
            self, monkeypatch, tmp_path):
        chosen = self._fake_gds2kicad(tmp_path / "chosen")
        imported = types.ModuleType("pin_list")    # built in memory, so no __file__
        imported.PinList = object
        sys.modules["pin_list"] = imported
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(chosen))
        with pytest.raises(bump_mirror.CliInputError, match="no source file"):
            bump_mirror._import_pinlist()
        assert sys.modules["pin_list"] is imported

    def test_a_symlinked_checkout_retargeted_after_the_load_is_refused(
            self, monkeypatch, tmp_path):
        first = self._fake_gds2kicad(tmp_path / "first")
        second = self._fake_gds2kicad(tmp_path / "second")
        link = tmp_path / "link"
        link.symlink_to(first)
        monkeypatch.setenv("GDS_TO_KICAD_ROOT", str(link))
        bump_mirror._import_pinlist()
        loaded = sys.modules["pin_list"]
        link.unlink()
        link.symlink_to(second)
        with pytest.raises(bump_mirror.CliInputError) as excinfo:
            bump_mirror._import_pinlist()
        assert str(first) in str(excinfo.value) and str(second) in str(excinfo.value)
        assert sys.modules["pin_list"] is loaded


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_params():
    """DRC params with default Option 2 values."""
    return DrcParams()


@pytest.fixture
def validator(default_params):
    return DrcValidator(default_params)


def _make_pin_list_json(pins, chiplet_name="test"):
    """Create a temporary pin list JSON file.

    Args:
        pins: List of dicts with keys: name, center_x_dbu, center_y_dbu

    Returns:
        Path to temporary JSON file
    """
    data = {
        "version": 1,
        "chiplet_name": chiplet_name,
        "gds_source": "test.gds",
        "lyp_file": "test.lyp",
        "pad_layer": "TopMetal2.drawing",
        "text_layers": [],
        "pins": [
            {
                "name": p["name"],
                "type": "passive",
                "side": "left",
                "pad_index": i,
                "center_x_dbu": p.get("center_x_dbu", 0.0),
                "center_y_dbu": p.get("center_y_dbu", 0.0),
                "width_dbu": p.get("width_dbu", 35000.0),
                "height_dbu": p.get("height_dbu", 35000.0),
            }
            for i, p in enumerate(pins)
        ],
    }
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    return path


def _make_tech_json(rules=None):
    """Create a temporary tech JSON file."""
    data = {
        "description": "Test DRC rules",
        "version": "1.0",
        "rules": rules or {
            "Padc_a": 35.0,
            "Padc_b": 40.0,
            "Padc_c": 7.5,
            "Padc_d": 30.0,
            "Padc_e": 75.0,
        },
    }
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    return path


def _make_chiplet_yaml(components, connection_stacks=None):
    """Create a temporary chiplet YAML file.

    Args:
        components: List of dicts with keys: id, type, x, y, rotation, connection
        connection_stacks: Optional dict of stack_id -> {layers: [...]}
    """
    lines = [
        "format_version: '1.0'",
        "assembly:",
        "  name: test_assembly",
        "  units: um",
    ]
    if connection_stacks:
        lines.append("connection_stacks:")
        for stack_id, stack in connection_stacks.items():
            lines.append(f"  {stack_id}:")
            lines.append(f"    description: '{stack.get('description', stack_id)}'")
            lines.append(f"    layers:")
            for layer in stack.get("layers", []):
                lines.append(
                    f"      - {{name: {layer['name']}, material: {layer['material']}, "
                    f"height: {layer['height']}, diameter: {layer['diameter']}}}")
    lines.append("components:")
    for c in components:
        lines.append(f"- id: {c['id']}")
        lines.append(f"  type: {c.get('type', 'die')}")
        lines.append(f"  technology: sg13g2")
        if 'connection' in c:
            lines.append(f"  connection: {c['connection']}")
        lines.append(f"  position:")
        lines.append(f"    x: {c.get('x', 0.0)}")
        lines.append(f"    y: {c.get('y', 0.0)}")
        lines.append(f"    z: 0.0")
        if 'rotation' in c:
            lines.append(f"  rotation:")
            lines.append(f"    z: {c['rotation']}")

    fd, path = tempfile.mkstemp(suffix=".chiplet")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# Required tests
# ---------------------------------------------------------------------------

class TestValidation:

    def test_validation_catches_pitch_violation(self, validator):
        """Bumps at 60um pitch -> Padc.e error (min pitch is 75um)."""
        bumps = [
            BumpLocation("U1", "Pin1", 0.0, 0.0),
            BumpLocation("U1", "Pin2", 60.0, 0.0),  # 60um center-to-center
        ]
        report = validator.validate(bumps)
        assert not report.passed
        pitch_errors = [r for r in report.results
                        if r.rule == 'Padc.e' and r.severity == 'error']
        assert len(pitch_errors) >= 1
        assert '60' in pitch_errors[0].message

    def test_validation_catches_diameter_mismatch(self, validator):
        """diameter=30 vs tech=35 -> Padc.a error."""
        bumps = [BumpLocation("U1", "Pin1", 0.0, 0.0)]
        report = validator.validate(bumps, diameter_um=30.0)
        assert not report.passed
        dia_errors = [r for r in report.results
                      if r.rule == 'Padc.a' and r.severity == 'error']
        assert len(dia_errors) == 1
        assert '30' in dia_errors[0].message

    def test_validation_passes_valid_layout(self, validator):
        """Bumps at 80um pitch -> passed=True."""
        bumps = [
            BumpLocation("U1", "Pin1", 0.0, 0.0),
            BumpLocation("U1", "Pin2", 80.0, 0.0),  # 80um > 75um min pitch
        ]
        report = validator.validate(bumps)
        assert report.passed

    @needs_interconnect
    def test_gds_output_has_expected_layers(self):
        """Generated GDS contains fab layers 9/35, 41/35, 134/0, 99/35."""
        import klayout.db as kdb

        bumps = [
            BumpLocation("U1", "Pin1", 100.0, 100.0),
            BumpLocation("U1", "Pin2", 200.0, 100.0),
        ]
        gen = CuPillarGenerator()
        gen.add_bumps(bumps)

        fd, path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            gen.write(path)

            # Read back and check layers
            layout = kdb.Layout()
            layout.read(path)

            layer_pairs = set()
            for li in layout.layer_indices():
                info = layout.get_info(li)
                layer_pairs.add((info.layer, info.datatype))

            expected = {(134, 0), (9, 35), (41, 35), (99, 35)}
            assert expected.issubset(layer_pairs), \
                f"Missing layers: {expected - layer_pairs}"

            # Also check 3D layers are present
            assert (500, 35) in layer_pairs
            assert (501, 35) in layer_pairs
        finally:
            os.unlink(path)

    def test_chiplet_mode_reads_positions(self):
        """Parse chiplet YAML -> correct x, y, rotation per component."""
        components = [
            {"id": "interposer", "type": "interposer", "x": 0, "y": 0},
            {"id": "U1", "type": "die", "x": 1000.5, "y": 2000.3,
             "rotation": 90.0},
            {"id": "U2", "type": "die", "x": 3000.0, "y": 4000.0,
             "rotation": 0.0},
        ]
        path = _make_chiplet_yaml(components)
        try:
            positions = load_chiplet_positions(path)
            # Interposer should be skipped
            assert "interposer" not in positions
            assert "U1" in positions
            assert "U2" in positions
            assert abs(positions["U1"]["x"] - 1000.5) < 0.01
            assert abs(positions["U1"]["y"] - 2000.3) < 0.01
            assert abs(positions["U1"]["rotation"] - 90.0) < 0.01
            assert abs(positions["U2"]["x"] - 3000.0) < 0.01
        finally:
            os.unlink(path)

    @needs_gds2kicad
    def test_validate_only_no_gds(self):
        """CLI --validate-only creates report but no GDS file."""
        pins_path = _make_pin_list_json([
            {"name": "Pin1", "center_x_dbu": 0.0, "center_y_dbu": 0.0},
            {"name": "Pin2", "center_x_dbu": 80000.0, "center_y_dbu": 0.0},
        ])
        fd, report_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(report_path)  # ensure it doesn't exist yet

        gds_path = report_path.replace(".json", ".gds")

        try:
            ret = main([
                '--pins', f'U1={pins_path}',
                '--position', 'U1=0,0',
                '--validate-only',
                '--report', report_path,
                '-o', gds_path,
            ])
            assert ret == 0
            assert Path(report_path).exists(), "Report should be created"
            assert not Path(gds_path).exists(), "GDS should NOT be created"
        finally:
            for p in [pins_path, report_path, gds_path]:
                if Path(p).exists():
                    os.unlink(p)

    def test_report_json_structure(self, validator):
        """Report has keys: passed, params, results, summary."""
        bumps = [BumpLocation("U1", "Pin1", 0.0, 0.0)]
        report = validator.validate(bumps)
        d = report.to_dict()

        assert 'passed' in d
        assert 'params' in d
        assert 'results' in d
        assert 'summary' in d
        assert isinstance(d['passed'], bool)
        assert isinstance(d['params'], dict)
        assert isinstance(d['results'], list)
        assert isinstance(d['summary'], dict)
        assert 'error' in d['summary']
        assert 'warning' in d['summary']
        assert 'info' in d['summary']
        assert 'total' in d['summary']


# ---------------------------------------------------------------------------
# Additional tests
# ---------------------------------------------------------------------------

class TestAdditionalValidation:

    def test_spacing_violation(self, validator):
        """Edge-to-edge < 40um -> Padc.b error.

        Two bumps 70um apart center-to-center with 35um diameter:
        edge spacing = 70 - 35 = 35um < 40um minimum.
        """
        bumps = [
            BumpLocation("U1", "Pin1", 0.0, 0.0),
            BumpLocation("U1", "Pin2", 70.0, 0.0),
        ]
        report = validator.validate(bumps)
        assert not report.passed
        spacing_errors = [r for r in report.results
                          if r.rule == 'Padc.b' and r.severity == 'error']
        assert len(spacing_errors) >= 1

    def test_max_detail_cap_vs_uncapped(self, validator):
        """max_detail caps per-rule detail; None reports every violation.

        Eight bumps in a 50um-pitch row produce 7 adjacent edge-spacing
        violations -- above the default cap of 5. The capped report keeps
        5 detailed entries plus one "... and N more" summary; the uncapped
        (max_detail=None) report lists all 7 with structured coordinates
        and no truncation summary.
        """
        bumps = [BumpLocation("U1", f"P{i}", i * 50.0, 0.0)
                 for i in range(8)]

        capped = validator.validate(bumps)  # default max_detail=5
        cap_b = [r for r in capped.results
                 if r.rule == 'Padc.b' and r.severity == 'error']
        assert len([r for r in cap_b if r.details]) == 5
        assert len([r for r in cap_b if 'more' in r.message]) == 1

        full = validator.validate(bumps, max_detail=None)
        full_b = [r for r in full.results
                  if r.rule == 'Padc.b' and r.severity == 'error']
        full_detail = [r for r in full_b if r.details]
        assert len(full_detail) == 7                       # all listed
        assert len([r for r in full_b if 'more' in r.message]) == 0
        for r in full_detail:                              # full coordinates
            assert 'bump1' in r.details and 'edge_spacing_um' in r.details

    def test_enclosure_warning(self, validator):
        """enclosure < 7.5 -> Padc.c warning (not error)."""
        bumps = [BumpLocation("U1", "Pin1", 0.0, 0.0)]
        report = validator.validate(bumps, enclosure_um=5.0)
        # Should be a warning, not an error
        encl_warns = [r for r in report.results
                      if r.rule == 'Padc.c' and r.severity == 'warning']
        assert len(encl_warns) == 1
        # Report should still pass (warnings don't block)
        assert report.passed

    @needs_gds2kicad
    def test_rotation_transform(self, monkeypatch):
        """Pin at (40000, 0) dbu + 90deg rotation -> (0, 40) um offset."""
        monkeypatch.syspath_prepend(str(require_sibling("gds_to_kicad")))
        from pin_list import PinList, PinEntry

        pin_list = PinList(pins=[
            PinEntry(name="Pin1", center_x_dbu=40000.0, center_y_dbu=0.0),
        ])
        pin_lists = {"U1": pin_list}
        positions = {"U1": {"x": 100.0, "y": 200.0, "rotation": 90.0}}

        bumps = compute_bump_locations(pin_lists, positions)
        assert len(bumps) == 1

        b = bumps[0]
        # 40000 dbu = 40 um
        # Rotation 90: (40, 0) -> (0, 40)
        # Global: (100 + 0, 200 + 40) = (100, 240)
        assert abs(b.global_x_um - 100.0) < 0.01
        assert abs(b.global_y_um - 240.0) < 0.01

    def test_tech_json_loading(self):
        """DrcParams.load() reads correct values from tech JSON."""
        path = _make_tech_json({
            "Padc_a": 40.0,
            "Padc_b": 45.0,
            "Padc_c": 10.0,
            "Padc_d": 35.0,
            "Padc_e": 80.0,
        })
        try:
            params = DrcParams.load(path)
            assert params.diameter_um == 40.0
            assert params.min_spacing_um == 45.0
            assert params.min_enclosure_um == 10.0
            assert params.min_edgeseal_um == 35.0
            assert params.min_pitch_um == 80.0
        finally:
            os.unlink(path)


class TestCuPillarGenerator:

    @needs_interconnect
    def test_cell_hierarchy(self):
        """Generated GDS has TOP > CUPILLARS_U1 > CUPILLAR_49um_opt2 instances."""
        import klayout.db as kdb

        bumps = [
            BumpLocation("U1", "Pin1", 0.0, 0.0),
            BumpLocation("U2", "Pin1", 100.0, 0.0),
        ]
        gen = CuPillarGenerator()
        count = gen.add_bumps(bumps)
        assert count == 2

        fd, path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            gen.write(path)
            layout = kdb.Layout()
            layout.read(path)

            cell_names = [layout.cell(i).name for i in range(layout.cells())]
            assert "TOP" in cell_names
            assert "CUPILLARS_U1" in cell_names
            assert "CUPILLARS_U2" in cell_names
            # Default body diameter is 49um (Option 2)
            assert "CUPILLAR_49um_opt2" in cell_names
        finally:
            os.unlink(path)

    @needs_interconnect
    def test_bump_count(self):
        """add_bumps returns correct count."""
        bumps = [
            BumpLocation("U1", f"Pin{i}", i * 80.0, 0.0)
            for i in range(5)
        ]
        gen = CuPillarGenerator()
        assert gen.add_bumps(bumps) == 5

    @needs_interconnect
    def test_custom_bodies_select_method_layers(self):
        """bodies= draws the method's 3D layers (e.g. a vendor's 510/511)
        instead of the default IHP cu-pillar pair."""
        import klayout.db as kdb

        gen = CuPillarGenerator(bodies=[("VendorXBumpCu", 510, 35),
                                        ("VendorXBumpCap", 511, 35)])
        gen.add_bumps([BumpLocation("U1", "Pin1", 0.0, 0.0)])

        fd, path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            gen.write(path)
            layout = kdb.Layout()
            layout.read(path)
            layer_pairs = set()
            for li in layout.layer_indices():
                info = layout.get_info(li)
                layer_pairs.add((info.layer, info.datatype))
            # Vendor bodies present, IHP pair absent; fab pads unchanged.
            assert (510, 35) in layer_pairs
            assert (511, 35) in layer_pairs
            assert (500, 35) not in layer_pairs
            assert (501, 35) not in layer_pairs
            assert {(134, 0), (9, 35), (41, 35), (99, 35)} <= layer_pairs
        finally:
            os.unlink(path)

    @needs_interconnect
    def test_written_gds_rereads_static_without_library_context(self):
        """Written GDS re-reads in a fresh Layout with no library context.

        The pillar cells are placed from the CuPillarPad PCell but must be
        written as plain static geometry: no proxy cells (names starting
        with '$'), no leftover 'CuPillarPad' cell, and the fab-layer
        polygons present under the top cell."""
        import klayout.db as kdb

        gen = CuPillarGenerator()
        gen.add_bumps([BumpLocation("U1", "P1", 0.0, 0.0)])

        fd, path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            gen.write(path)
            layout = kdb.Layout()
            layout.read(path)

            names = [c.name for c in layout.each_cell()]
            assert not any(n.startswith("$") for n in names), names
            assert "CuPillarPad" not in names, names

            top = layout.top_cell()
            assert top.name == "TOP"
            for layer, datatype in [(134, 0), (9, 35), (41, 35), (99, 35)]:
                idx = layout.layer(layer, datatype)
                region = kdb.Region(top.begin_shapes_rec(idx))
                assert not region.is_empty(), (
                    f"no geometry on {layer}/{datatype} after re-read")
        finally:
            os.unlink(path)

    @needs_interconnect
    def test_foreign_lyp_env_cannot_poison_fab_layers(self):
        """KLAYOUT_LYP_FILE must not reach the PCell library registration.

        The library's tech class honors that env var as a layer-table
        override; a foreign .lyp would silently retag the fabrication
        layers. The bootstrap strips it during import (and restores it
        afterwards), so a fresh process generating with a hostile value
        must still emit the canonical fab layer set."""
        import klayout.db as kdb

        foreign_lyp = "\n".join(
            ['<?xml version="1.0" encoding="utf-8"?>',
             '<layer-properties>'] +
            [f' <properties>\n  <source>{src}</source>\n'
             f'  <name>{name}</name>\n </properties>'
             for name, src in [("TopMetal2.drawing", "777/0"),
                               ("Passiv.pillar", "888/88"),
                               ("dfpad.pillar", "889/88"),
                               ("Recog.pillar", "890/88")]] +
            ['</layer-properties>'])

        python_dir = Path(__file__).resolve().parents[1] / "python"
        with tempfile.TemporaryDirectory() as tmp:
            lyp_path = Path(tmp) / "foreign.lyp"
            lyp_path.write_text(foreign_lyp)
            out_gds = Path(tmp) / "out.gds"
            script = Path(tmp) / "gen.py"
            script.write_text(
                "import os, sys\n"
                f"sys.path.insert(0, {str(python_dir)!r})\n"
                "from bump_mirror import BumpLocation, CuPillarGenerator\n"
                "gen = CuPillarGenerator()\n"
                "gen.add_bumps([BumpLocation('U1', 'P1', 0.0, 0.0)])\n"
                f"gen.write({str(out_gds)!r})\n"
                "assert os.environ.get('KLAYOUT_LYP_FILE'), "
                "'env var must be restored after registration'\n")

            env = dict(os.environ, KLAYOUT_LYP_FILE=str(lyp_path))
            result = subprocess.run([sys.executable, str(script)],
                                    capture_output=True, text=True, env=env,
                                    timeout=120)
            assert result.returncode == 0, (
                f"generation failed under foreign KLAYOUT_LYP_FILE:\n"
                f"{result.stdout}\n{result.stderr}")

            layout = kdb.Layout()
            layout.read(str(out_gds))
            top = layout.top_cell()
            drawn = set()
            for idx in layout.layer_indexes():
                info = layout.get_info(idx)
                region = kdb.Region(top.begin_shapes_rec(idx))
                if not region.is_empty():
                    drawn.add((info.layer, info.datatype))
            fab = {(134, 0), (9, 35), (41, 35), (99, 35)}
            assert fab <= drawn, (
                f"fab layers missing or retagged under foreign "
                f"KLAYOUT_LYP_FILE: drawn {sorted(drawn)}")
            assert not any(l in (777, 888, 889, 890) for l, _ in drawn), (
                f"foreign layer numbers leaked into the output: "
                f"{sorted(drawn)}")

    def test_fab_layer_contract_guard(self, monkeypatch):
        """A wrong library layer table cannot pass silently: the drawn
        set is checked against CUPILLAR_FAB_LAYERS after PCell flatten."""
        import bump_mirror

        monkeypatch.setattr(
            bump_mirror, "CUPILLAR_FAB_LAYERS",
            {"TopMetal2": (777, 0), "Passiv:pillar": (888, 88),
             "dfpad:pillar": (889, 88), "Recog:pillar": (890, 88)})
        gen = CuPillarGenerator()
        with pytest.raises(RuntimeError, match="instead of the fabrication"):
            gen.add_bumps([BumpLocation("U1", "P1", 0.0, 0.0)])

    @needs_interconnect
    def test_write_infers_format_from_suffix(self):
        """write() keeps stream-format inference from the file suffix
        (an .oas path must produce OASIS bytes, not GDS2)."""
        gen = CuPillarGenerator()
        gen.add_bumps([BumpLocation("U1", "P1", 0.0, 0.0)])

        fd, path = tempfile.mkstemp(suffix=".oas")
        os.close(fd)
        try:
            gen.write(path)
            with open(path, "rb") as f:
                magic = f.read(11)
            assert magic == b"%SEMI-OASIS", (
                f"expected OASIS magic for .oas suffix, got {magic!r}")
        finally:
            os.unlink(path)

    def test_loud_failure_without_interconnect_pdk(self, monkeypatch):
        """No interconnect PDK -> cell generation refuses (no silent
        fallback that would emit pads without their bodies)."""
        import bump_mirror

        monkeypatch.setattr(bump_mirror, "_bump3d_cache", False)
        # The absence is cached under the root this case runs with, so a body
        # cached by an earlier sibling case cannot answer for it.
        monkeypatch.setattr(bump_mirror, "_bump3d_cache_root",
                            os.environ.get("INTERCONNECT_PDK_ROOT"), raising=False)
        gen = CuPillarGenerator()
        with pytest.raises(RuntimeError, match="interconnect_pdk not found"):
            gen.add_bumps([BumpLocation("U1", "Pin1", 0.0, 0.0)])

    @needs_interconnect
    def test_manifest_opening_enables_non_table_diameter(self):
        """A diameter outside Table 6.1 draws with the method-supplied
        passivation opening (manifest fab_params); Table 6.1 still wins
        for the diameters it knows."""
        import klayout.db as kdb

        gen = CuPillarGenerator(bodies=[("VendorXBumpCu", 510, 35),
                                        ("VendorXBumpCap", 511, 35)],
                                passiv_opening_um=35.0)
        gen.add_bumps([BumpLocation("U1", "Pin1", 0.0, 0.0)],
                      body_diameter_um=40)

        fd, path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            gen.write(path)
            layout = kdb.Layout()
            layout.read(path)
            cell_names = [layout.cell(i).name for i in range(layout.cells())]
            assert "CUPILLAR_40um_manifest" in cell_names
            # Passiv opening 35 -> r 17.5; TM2 = r + enclosure 7.5 = 25.
            passiv = layout.layer(9, 35)
            tm2 = layout.layer(134, 0)
            cell = layout.cell("CUPILLAR_40um_manifest")
            pbox = cell.dbbox_per_layer(passiv)
            tbox = cell.dbbox_per_layer(tm2)
            assert abs(pbox.width() - 35.0) < 0.1
            assert abs(tbox.width() - 50.0) < 0.1
        finally:
            os.unlink(path)

    def test_non_table_diameter_without_opening_still_raises(self):
        """Without the manifest opening the unknown diameter stays loud."""
        gen = CuPillarGenerator()
        with pytest.raises(ValueError, match="Unknown Cu-pillar body diameter"):
            gen.add_bumps([BumpLocation("U1", "Pin1", 0.0, 0.0)],
                          body_diameter_um=40)


class TestCLI:

    @needs_gds2kicad
    @needs_interconnect
    def test_full_generation(self):
        """End-to-end CLI test: pins + position -> GDS output."""
        pins_path = _make_pin_list_json([
            {"name": "Pin1", "center_x_dbu": 0.0, "center_y_dbu": 0.0},
            {"name": "Pin2", "center_x_dbu": 80000.0, "center_y_dbu": 0.0},
        ])
        fd, gds_path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        os.unlink(gds_path)

        try:
            ret = main([
                '--pins', f'U1={pins_path}',
                '--position', 'U1=0,0',
                '-o', gds_path,
            ])
            assert ret == 0
            assert Path(gds_path).exists()
        finally:
            for p in [pins_path, gds_path]:
                if Path(p).exists():
                    os.unlink(p)

    @needs_gds2kicad
    def test_validation_failure_blocks_gds(self, capsys):
        """--no-auto-resolve + pitch violation -> exit 1, no GDS.

        This is the strict contract: with auto-resolve disabled a DRC
        violation must block GDS generation outright."""
        pins_path = _make_pin_list_json([
            {"name": "Pin1", "center_x_dbu": 0.0, "center_y_dbu": 0.0},
            {"name": "Pin2", "center_x_dbu": 60000.0, "center_y_dbu": 0.0},
        ])
        fd, gds_path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        os.unlink(gds_path)

        try:
            ret = main([
                '--pins', f'U1={pins_path}',
                '--position', 'U1=0,0',
                '--no-auto-resolve',
                '-o', gds_path,
            ])
            assert ret == 1
            assert "ERROR  [Padc.e]" in capsys.readouterr().out
            assert not Path(gds_path).exists()
        finally:
            for p in [pins_path, gds_path]:
                if Path(p).exists():
                    os.unlink(p)

    @needs_gds2kicad
    @needs_interconnect
    def test_default_auto_resolve_recovers_and_writes_gds(self):
        """Default contract (auto-resolve ON): a pitch violation within the
        displacement budget is shifted until DRC passes and GDS is written."""
        pins_path = _make_pin_list_json([
            {"name": "Pin1", "center_x_dbu": 0.0, "center_y_dbu": 0.0},
            {"name": "Pin2", "center_x_dbu": 60000.0, "center_y_dbu": 0.0},
        ])
        fd, gds_path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        os.unlink(gds_path)

        try:
            ret = main([
                '--pins', f'U1={pins_path}',
                '--position', 'U1=0,0',
                '-o', gds_path,
            ])
            assert ret == 0
            assert Path(gds_path).exists()
        finally:
            for p in [pins_path, gds_path]:
                if Path(p).exists():
                    os.unlink(p)


    #: Two pins 60 um apart: under the 80 um minimum pitch of the default
    #: 49 um body (Padc.e). The passing pair sits at exactly that pitch.
    FAILING_PINS = (
        {"name": "Pin1", "center_x_dbu": 0.0, "center_y_dbu": 0.0},
        {"name": "Pin2", "center_x_dbu": 60000.0, "center_y_dbu": 0.0},
    )
    PASSING_PINS = (
        {"name": "Pin1", "center_x_dbu": 0.0, "center_y_dbu": 0.0},
        {"name": "Pin2", "center_x_dbu": 80000.0, "center_y_dbu": 0.0},
    )

    @needs_gds2kicad
    def test_validate_only_with_a_failing_device_exits_1(self, tmp_path, capsys):
        """--validate-only never exits 0 over a failed device, and still
        writes the report naming it."""
        pins_path = _make_pin_list_json(self.FAILING_PINS)
        report_path = tmp_path / "report.json"
        try:
            ret = main([
                '--pins', f'U1={pins_path}',
                '--position', 'U1=0,0',
                '--no-auto-resolve',
                '--validate-only',
                '--report', str(report_path),
            ])
        finally:
            os.unlink(pins_path)
        assert ret == 1
        assert json.loads(report_path.read_text())["failed_devices"] == ["U1"]
        assert "Exiting non-zero: 1 device(s) failed: U1" in capsys.readouterr().err

    @needs_gds2kicad
    @needs_interconnect
    def test_generate_with_a_failing_device_exits_1_with_the_passing_pads(
            self, tmp_path, capsys):
        """The passed device's pads are still written (a partial GDS is worth
        inspecting), but the run exits 1 and names the failed device."""
        import klayout.db as kdb

        failing = _make_pin_list_json(self.FAILING_PINS)
        passing = _make_pin_list_json(self.PASSING_PINS)
        gds_path = tmp_path / "out.gds"
        try:
            ret = main([
                '--pins', f'U1={failing}', f'U2={passing}',
                '--position', 'U1=0,0', 'U2=2000,0',
                '--no-auto-resolve',
                '-o', str(gds_path),
            ])
        finally:
            os.unlink(failing)
            os.unlink(passing)
        assert ret == 1
        assert "Exiting non-zero: 1 device(s) failed: U1" in capsys.readouterr().err
        layout = kdb.Layout()
        layout.read(str(gds_path))
        cell_names = {layout.cell(i).name for i in range(layout.cells())}
        assert "CUPILLARS_U2" in cell_names
        assert "CUPILLARS_U1" not in cell_names

    def _skipped_alone(self, tmp_path, *args):
        """Run U1 with no connection stack in --chiplet mode; return the exit
        status and the report."""
        pins_path = _make_pin_list_json(self.PASSING_PINS)
        chiplet = _make_chiplet_yaml([{"id": "U1", "x": 0.0, "y": 0.0}])
        report_path = tmp_path / "report.json"
        try:
            ret = main([
                '--pins', f'U1={pins_path}',
                '--chiplet', chiplet,
                '--report', str(report_path),
                *args,
            ])
        finally:
            os.unlink(pins_path)
            os.unlink(chiplet)
        return ret, json.loads(report_path.read_text())

    @needs_gds2kicad
    def test_a_skipped_device_alone_exits_0(self, tmp_path):
        """A device with no connection stack is skipped in --chiplet mode,
        which is not a failure."""
        ret, report = self._skipped_alone(tmp_path, '--validate-only')
        assert ret == 0
        assert (report["skipped_devices"], report["failed_devices"]) == (["U1"], [])

    @needs_gds2kicad
    def test_a_generate_run_with_only_a_skipped_device_writes_nothing_and_exits_1(
            self, tmp_path):
        """Generate mode has no device to write, so it exits 1 with no GDS,
        though nothing failed."""
        gds_path = tmp_path / "out.gds"
        ret, report = self._skipped_alone(tmp_path, '-o', str(gds_path))
        assert ret == 1
        assert (report["skipped_devices"], report["failed_devices"]) == (["U1"], [])
        assert not gds_path.exists()


# ---------------------------------------------------------------------------
# Table 6.1 per-device dimension tests
# ---------------------------------------------------------------------------

class TestTable61Lookup:

    def test_table_has_all_three_options(self):
        """Table 6.1 lookup has entries for diameters 44, 49, 54."""
        assert 44 in CUPILLAR_TABLE_6_1
        assert 49 in CUPILLAR_TABLE_6_1
        assert 54 in CUPILLAR_TABLE_6_1
        assert CUPILLAR_TABLE_6_1[44]['option'] == 1
        assert CUPILLAR_TABLE_6_1[49]['option'] == 2
        assert CUPILLAR_TABLE_6_1[54]['option'] == 3

    def test_default_is_option_2(self):
        """Default body diameter is 49um (Option 2)."""
        assert DEFAULT_BODY_DIAMETER == 49

    def test_option2_values_match_table(self):
        """Option 2 values match IHP Table 6.1."""
        opt2 = CUPILLAR_TABLE_6_1[49]
        assert opt2['passiv_opening'] == 40
        assert opt2['spacing'] == 40
        assert opt2['pitch'] == 80
        assert opt2['cu_height'] == 32
        assert opt2['snag_height'] == 16

    def test_option3_values_match_table(self):
        """Option 3 values match IHP Table 6.1."""
        opt3 = CUPILLAR_TABLE_6_1[54]
        assert opt3['passiv_opening'] == 45
        assert opt3['spacing'] == 50
        assert opt3['pitch'] == 95
        assert opt3['cu_height'] == 42
        assert opt3['snag_height'] == 19

    def test_drc_params_from_body_diameter_opt1(self):
        """DrcParams.from_body_diameter(44) returns Option 1 rules."""
        p = DrcParams.from_body_diameter(44)
        assert p.diameter_um == 35
        assert p.min_spacing_um == 40
        assert p.min_pitch_um == 75

    def test_drc_params_from_body_diameter_opt3(self):
        """DrcParams.from_body_diameter(54) returns Option 3 rules."""
        p = DrcParams.from_body_diameter(54)
        assert p.diameter_um == 45
        assert p.min_spacing_um == 50
        assert p.min_pitch_um == 95

    def test_drc_params_unknown_diameter_falls_back(self):
        """Unknown body diameter falls back to Option 2 defaults."""
        p = DrcParams.from_body_diameter(999)
        assert p.diameter_um == 40.0
        assert p.min_pitch_um == 80.0


class TestPerDeviceDimensions:

    def test_chiplet_with_connection_stacks(self):
        """load_chiplet_positions returns body_diameter from connection stack."""
        stacks = {
            "cupillar_opt2": {
                "description": "Option 2",
                "layers": [
                    {"name": "CuPillar", "material": "Cu",
                     "height": 32, "diameter": 49},
                    {"name": "SnAgCap", "material": "SnAg",
                     "height": 16, "diameter": 49},
                ],
            },
            "cupillar_opt3": {
                "description": "Option 3",
                "layers": [
                    {"name": "CuPillar", "material": "Cu",
                     "height": 42, "diameter": 54},
                    {"name": "SnAgCap", "material": "SnAg",
                     "height": 19, "diameter": 54},
                ],
            },
        }
        components = [
            {"id": "interposer", "type": "interposer", "x": 0, "y": 0},
            {"id": "U1", "type": "die", "x": 100, "y": 200,
             "connection": "cupillar_opt2"},
            {"id": "U2", "type": "die", "x": 300, "y": 400,
             "connection": "cupillar_opt3"},
        ]
        path = _make_chiplet_yaml(components, stacks)
        try:
            positions = load_chiplet_positions(path)
            assert "interposer" not in positions
            assert positions["U1"]["body_diameter"] == 49.0
            assert positions["U1"]["connection"] == "cupillar_opt2"
            assert positions["U2"]["body_diameter"] == 54.0
            assert positions["U2"]["connection"] == "cupillar_opt3"
        finally:
            os.unlink(path)

    def test_chiplet_no_connection_returns_none(self):
        """Components without connection field get body_diameter=None."""
        components = [
            {"id": "U1", "type": "die", "x": 100, "y": 200},
        ]
        path = _make_chiplet_yaml(components)
        try:
            positions = load_chiplet_positions(path)
            assert positions["U1"]["connection"] is None
            assert positions["U1"]["body_diameter"] is None
        finally:
            os.unlink(path)

    @needs_interconnect
    def test_mixed_option_gds_has_two_pillar_cells(self):
        """Mixed opt1 + opt2 assembly creates two different pillar cells."""
        import klayout.db as kdb

        gen = CuPillarGenerator()
        gen.add_device_bumps("U1",
                             [BumpLocation("U1", "P1", 0, 0)],
                             body_diameter_um=44)
        gen.add_device_bumps("U2",
                             [BumpLocation("U2", "P1", 200, 0)],
                             body_diameter_um=49)

        fd, path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            gen.write(path)
            layout = kdb.Layout()
            layout.read(path)
            cell_names = [layout.cell(i).name for i in range(layout.cells())]
            assert "CUPILLAR_44um_opt1" in cell_names
            assert "CUPILLAR_49um_opt2" in cell_names
            assert "CUPILLARS_U1" in cell_names
            assert "CUPILLARS_U2" in cell_names
        finally:
            os.unlink(path)

    @needs_interconnect
    def test_pillar_cell_caching(self):
        """Same body_diameter + with_cap reuses cached pillar cell."""
        gen = CuPillarGenerator()
        gen.add_device_bumps("U1",
                             [BumpLocation("U1", "P1", 0, 0)],
                             body_diameter_um=49)
        gen.add_device_bumps("U2",
                             [BumpLocation("U2", "P1", 200, 0)],
                             body_diameter_um=49)
        # Only one pillar cell should exist for (49um, with_cap=True)
        assert len(gen._pillar_cells) == 1
        assert (49, True) in gen._pillar_cells

    @needs_interconnect
    def test_nocap_creates_separate_cell(self):
        """with_cap=False creates a different cell without SnAgCap layer."""
        import klayout.db as kdb

        gen = CuPillarGenerator()
        gen.add_device_bumps("U1",
                             [BumpLocation("U1", "P1", 0, 0)],
                             body_diameter_um=49, with_cap=True)
        gen.add_device_bumps("U2",
                             [BumpLocation("U2", "P1", 200, 0)],
                             body_diameter_um=49, with_cap=False)
        # Two distinct pillar cells
        assert len(gen._pillar_cells) == 2
        assert (49, True) in gen._pillar_cells
        assert (49, False) in gen._pillar_cells

        fd, path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            gen.write(path)
            layout = kdb.Layout()
            layout.read(path)
            cell_names = [layout.cell(i).name for i in range(layout.cells())]
            assert "CUPILLAR_49um_opt2" in cell_names
            assert "CUPILLAR_49um_opt2_nocap" in cell_names

            # nocap cell should NOT have SnAgCap layer (501/35)
            nocap_cell = layout.cell("CUPILLAR_49um_opt2_nocap")
            snag_li = layout.layer(501, 35)
            region = kdb.Region(nocap_cell.begin_shapes_rec(snag_li))
            assert region.is_empty(), "nocap cell should not have SnAgCap"

            # but it should still have CuPillar layer (500/35)
            cu_li = layout.layer(500, 35)
            region = kdb.Region(nocap_cell.begin_shapes_rec(cu_li))
            assert not region.is_empty(), "nocap cell should have CuPillar"
        finally:
            os.unlink(path)

    def test_chiplet_nocap_stack_detected(self):
        """Connection stack without SnAgCap layer sets with_cap=False."""
        stacks = {
            "cupillar_nocap": {
                "description": "Cu pillar without cap",
                "layers": [
                    {"name": "CuPillar", "material": "Cu",
                     "height": 32, "diameter": 49},
                ],
            },
        }
        components = [
            {"id": "U1", "type": "die", "x": 100, "y": 200,
             "connection": "cupillar_nocap"},
        ]
        path = _make_chiplet_yaml(components, stacks)
        try:
            positions = load_chiplet_positions(path)
            assert positions["U1"]["with_cap"] is False
            assert positions["U1"]["body_diameter"] == 49.0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Robustness and contract regressions
# ---------------------------------------------------------------------------

class TestRobustness:

    def test_chiplet_scalar_rotation_and_missing_fields(self):
        """Hand-written YAML may use a scalar `rotation: 90` and omit a field;
        load_chiplet_positions must coerce, not crash with AttributeError."""
        yaml_text = (
            "format_version: '1.0'\n"
            "assembly:\n  name: t\n  units: um\n"
            "components:\n"
            "- id: U1\n  type: die\n  technology: sg13g2\n"
            "  position:\n    x: 10\n    y: 20\n"
            "  rotation: 90\n"                 # scalar, not {z: ...}
            "- id: U2\n  type: die\n  technology: sg13g2\n"
            "  position:\n    x: 5\n    y: 5\n"   # no rotation key at all
        )
        fd, path = tempfile.mkstemp(suffix=".chiplet")
        with os.fdopen(fd, "w") as f:
            f.write(yaml_text)
        try:
            pos = load_chiplet_positions(path)
            assert abs(pos["U1"]["rotation"] - 90.0) < 1e-9
            assert pos["U2"]["rotation"] == 0.0
            assert pos["U1"]["x"] == 10.0 and pos["U1"]["y"] == 20.0
        finally:
            os.unlink(path)

    def test_bad_position_and_rotation_raise_cli_error(self):
        """Malformed CLI tokens raise CliInputError (caught by main), instead of
        an uncaught ValueError or sys.exit inside importable parsers."""
        from bump_mirror import parse_positions, parse_rotations, CliInputError
        with pytest.raises(CliInputError):
            parse_positions(["U1=abc"])          # wrong arity (no comma)
        with pytest.raises(CliInputError):
            parse_positions(["U1=1,2,3"])        # wrong arity (3 parts)
        with pytest.raises(CliInputError):
            parse_positions(["U1=x,y"])          # right arity, non-numeric coords
        pos = {"U1": {"x": 0.0, "y": 0.0, "rotation": 0.0}}
        with pytest.raises(CliInputError):
            parse_rotations(["U1=spin"], pos)    # non-numeric angle

    @needs_gds2kicad
    def test_diameter_no_table_match_errors(self, capsys):
        """An unmatched --diameter hard-errors (exit 1, no GDS) instead of
        silently validating against a fallback diameter the user did not ask
        for."""
        pins_path = _make_pin_list_json([
            {"name": "Pin1", "center_x_dbu": 0.0, "center_y_dbu": 0.0},
        ])
        fd, gds_path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        os.unlink(gds_path)
        try:
            ret = main(['--pins', f'U1={pins_path}', '--position', 'U1=0,0',
                        '--diameter', '33', '-o', gds_path])
            assert ret == 1
            assert "does not match any Table 6.1" in capsys.readouterr().err
            assert not Path(gds_path).exists()
        finally:
            for p in (pins_path, gds_path):
                if Path(p).exists():
                    os.unlink(p)

    def test_auto_resolve_skips_coincident_but_fixes_others(self):
        """A coincident pair no longer aborts the whole pass; other resolvable
        collisions are still shifted (and the coincident overlap is reported)."""
        from bump_mirror import auto_resolve_collisions

        params = DrcParams()  # min_pitch 80 um
        bumps = [
            BumpLocation("U1", "P0", 0.0, 0.0),
            BumpLocation("U1", "P1", 0.0, 0.0),     # coincident with P0
            BumpLocation("U1", "P2", 500.0, 0.0),
            BumpLocation("U1", "P3", 560.0, 0.0),   # 60 um < 80 um -> resolvable
        ]
        _, report = auto_resolve_collisions(
            bumps, params, 40.0, max_displacement_um=20.0)
        assert report["coincident_skipped"] is True
        assert report["moved_count"] >= 2           # P2/P3 shifted regardless
        assert report["converged"] is False         # coincident overlap remains

    def test_auto_resolve_separates_coincident_when_other_moves_allow(self):
        """A pair that starts coincident but is pushed apart while resolving
        other pairs must be retried and separated, not excluded forever (the
        coincident skip is re-evaluated by distance, not pinned to indices)."""
        from bump_mirror import auto_resolve_collisions

        params = DrcParams()  # min_pitch 80 um
        bumps = [
            BumpLocation("U1", "P0", 0.0, 0.0),
            BumpLocation("U1", "P1", 0.0, 0.0),     # coincident with P0
            BumpLocation("U1", "P2", 60.0, 0.0),    # near both -> moves split P0/P1
        ]
        resolved, report = auto_resolve_collisions(
            bumps, params, 40.0, max_displacement_um=100.0)
        d01 = math.hypot(resolved[0].global_x_um - resolved[1].global_x_um,
                         resolved[0].global_y_um - resolved[1].global_y_um)
        assert d01 > 1e-6                            # no longer stuck-coincident
        assert report["coincident_skipped"] is False

    @needs_interconnect
    def test_bump3d_signature_contract(self):
        """The interposer calls bump3d_generator.add_3d_bodies(layout, cell,
        body_radius_um, bodies=, with_cap=, num_points=) across the repo split.
        Pin that signature so a rename/reorder in the interconnect repo is
        caught here instead of silently breaking cu-pillar generation."""
        import inspect
        import bump_mirror

        bump3d = bump_mirror._get_bump3d()
        if bump3d is None:
            pytest.skip("interconnect PDK not discoverable")
        params = list(inspect.signature(bump3d.add_3d_bodies).parameters)
        assert params[:3] == ["layout", "cell", "body_radius_um"]
        for kw in ("bodies", "with_cap", "num_points"):
            assert kw in params, kw
