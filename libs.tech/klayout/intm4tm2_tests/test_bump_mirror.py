#!/usr/bin/env python3
"""Tests for bump_mirror.py -- Cu-Pillar Generator with DRC Pre-Validation."""

import json
import math
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add bump_mirror's directory to path
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "python"
sys.path.insert(0, str(SCRIPTS_DIR))

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

# Also add gds_to_kicad for PinList
GDS_TO_KICAD_DIR = Path(__file__).resolve().parents[4] / "gds_to_kicad"
sys.path.insert(0, str(GDS_TO_KICAD_DIR))


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

    def test_rotation_transform(self):
        """Pin at (40000, 0) dbu + 90deg rotation -> (0, 40) um offset."""
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

    def test_bump_count(self):
        """add_bumps returns correct count."""
        bumps = [
            BumpLocation("U1", f"Pin{i}", i * 80.0, 0.0)
            for i in range(5)
        ]
        gen = CuPillarGenerator()
        assert gen.add_bumps(bumps) == 5


class TestCLI:

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

    def test_validation_failure_blocks_gds(self):
        """CLI with pitch violation -> exit 1, no GDS."""
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
            assert ret == 1
            assert not Path(gds_path).exists()
        finally:
            for p in [pins_path, gds_path]:
                if Path(p).exists():
                    os.unlink(p)


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
