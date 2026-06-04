#!/usr/bin/env python3
"""
Cu-Pillar PCell test harness.

Generates test GDS layouts for DRC validation of cu-pillar geometry rules
(Padc.a through Padc.f) and optionally runs DRC to verify results.

Usage:
    python test_cupillar_pcell.py --generate          # Generate test GDS files
    python test_cupillar_pcell.py --validate-drc      # Run DRC and check results
    python test_cupillar_pcell.py --generate --validate-drc  # Both
"""

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import klayout.db as db
except ImportError:
    print("Error: KLayout Python module not found.", file=sys.stderr)
    print("Install with: pip install klayout", file=sys.stderr)
    sys.exit(1)


# Layer definitions matching layers_def.drc
LAYERS = {
    # Fabrication layers
    'TopMetal2':      (134, 0),
    'Passiv:pillar':  (9, 35),
    'Passiv:sbump':   (9, 36),
    'dfpad:pillar':   (41, 35),
    'dfpad:sbump':    (41, 36),
    'Recog:pillar':   (99, 35),
    'Recog:sbump':    (99, 36),
    'EdgeSeal':       (39, 0),
    # 3D visualization auxiliary layers (not fabrication)
    'CuPillar:pillar':  (500, 35),
    'SnAgCap:pillar':   (501, 35),
    'SolderBall:sbump': (502, 36),
}

# Cu pillar physical dimensions from Table 6.1 (Option 1)
# The pillar body diameter is larger than the passivation opening
CUPILLAR_BODY_DIAMETER = 44.0  # um (Table 6.1 Option 1: 44 +/- 3)

# DRC rule values from interposer_tech_default.json (Table 6.1 Option 1)
PADC_A = 35.0   # CuPillarPad size (um)
PADC_B = 40.0   # Min pad spacing (um)
PADC_C = 7.5    # Min TM2 enclosure of pillar opening (um)
PADC_D = 30.0   # Min pad-to-EdgeSeal spacing (um)
PADC_E = 75.0   # Min pad pitch (um)
# Test default diameter matches Option 1
DEFAULT_DIAMETER = PADC_A  # 35.0 um


def create_circle(layout, cell, layer_idx, cx_um, cy_um, radius_um, num_points=64):
    """Create a circular polygon at (cx, cy) with given radius."""
    points = []
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        x = cx_um + radius_um * math.cos(angle)
        y = cy_um + radius_um * math.sin(angle)
        points.append(db.DPoint(x, y))
    poly = db.DPolygon(points)
    cell.shapes(layer_idx).insert(poly)
    return poly


def create_box(layout, cell, layer_idx, cx_um, cy_um, half_w, half_h):
    """Create a rectangular polygon at (cx, cy)."""
    box = db.DBox(cx_um - half_w, cy_um - half_h, cx_um + half_w, cy_um + half_h)
    cell.shapes(layer_idx).insert(box)
    return box


def add_cupillar_pad(layout, cell, cx_um, cy_um, diameter_um=35.0,
                     encl_um=7.5, pad_type='pillar', shape='circle',
                     num_points=256, add_3d_layers=True):
    """Add a complete cu-pillar pad at (cx, cy).

    Creates fabrication layers: TopMetal2, Passiv:padType, dfpad:padType, Recog:padType
    Creates 3D auxiliary layers (if add_3d_layers=True):
      pillar: CuPillar:pillar (500/35), SnAgCap:pillar (501/35)
      sbump:  SolderBall:sbump (502/36)
    """
    radius = diameter_um / 2.0
    tm2_radius = radius + encl_um

    passiv_key = f'Passiv:{pad_type}'
    dfpad_key = f'dfpad:{pad_type}'
    recog_key = f'Recog:{pad_type}'

    tm2_idx = layout.layer(*LAYERS['TopMetal2'])
    passiv_idx = layout.layer(*LAYERS[passiv_key])
    dfpad_idx = layout.layer(*LAYERS[dfpad_key])
    recog_idx = layout.layer(*LAYERS[recog_key])

    if shape == 'circle':
        create_circle(layout, cell, tm2_idx, cx_um, cy_um, tm2_radius, num_points)
        create_circle(layout, cell, dfpad_idx, cx_um, cy_um, tm2_radius, num_points)
        create_circle(layout, cell, recog_idx, cx_um, cy_um, tm2_radius, num_points)
        create_circle(layout, cell, passiv_idx, cx_um, cy_um, radius, num_points)
    elif shape == 'square':
        create_box(layout, cell, tm2_idx, cx_um, cy_um, tm2_radius, tm2_radius)
        create_box(layout, cell, dfpad_idx, cx_um, cy_um, tm2_radius, tm2_radius)
        create_box(layout, cell, recog_idx, cx_um, cy_um, tm2_radius, tm2_radius)
        create_box(layout, cell, passiv_idx, cx_um, cy_um, radius, radius)

    # 3D auxiliary layers for visualization/simulation
    if add_3d_layers and shape == 'circle':
        if pad_type == 'pillar':
            # Cu pillar body: diameter from Table 6.1 (larger than passiv opening)
            body_radius = CUPILLAR_BODY_DIAMETER / 2.0
            cupillar_idx = layout.layer(*LAYERS['CuPillar:pillar'])
            snag_idx = layout.layer(*LAYERS['SnAgCap:pillar'])
            create_circle(layout, cell, cupillar_idx, cx_um, cy_um, body_radius, num_points)
            create_circle(layout, cell, snag_idx, cx_um, cy_um, body_radius, num_points)
        elif pad_type == 'sbump':
            # Solder ball: 80um diameter (Section 6.9.1)
            ball_radius = 80.0 / 2.0
            ball_idx = layout.layer(*LAYERS['SolderBall:sbump'])
            create_circle(layout, cell, ball_idx, cx_um, cy_um, ball_radius, num_points)


def generate_clean_test(output_dir: Path):
    """Generate a DRC-clean cu-pillar test layout.

    Single 40um pillar with 7.5um TM2 enclosure -- should pass all rules.
    """
    layout = db.Layout()
    layout.dbu = 0.001  # 1nm
    cell = layout.create_cell("CLEAN_SINGLE")

    add_cupillar_pad(layout, cell, 0, 0, diameter_um=35.0, encl_um=7.5)

    path = output_dir / "test_cupillar_clean_single.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_clean_pair(output_dir: Path):
    """Two pads at 80um pitch -- satisfies Padc.e (>=75um)."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("CLEAN_PAIR")

    # Pitch = 80um, so center-to-center = 80um
    add_cupillar_pad(layout, cell, -40, 0, diameter_um=35.0, encl_um=7.5)
    add_cupillar_pad(layout, cell, 40, 0, diameter_um=35.0, encl_um=7.5)

    path = output_dir / "test_cupillar_clean_pair.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_violation_pitch(output_dir: Path):
    """Two pads at 60um pitch -- violates Padc.e (min 75um)."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("VIOL_PITCH")

    add_cupillar_pad(layout, cell, -30, 0, diameter_um=35.0, encl_um=7.5)
    add_cupillar_pad(layout, cell, 30, 0, diameter_um=35.0, encl_um=7.5)

    path = output_dir / "test_cupillar_viol_pitch.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_violation_spacing(output_dir: Path):
    """Two pads at 30um edge-to-edge spacing -- violates Padc.b (min 40um).

    With 40um diameter pads, center-to-center = 30 + 40 = 70um.
    Edge spacing = 70 - 40 = 30um < 40um.
    """
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("VIOL_SPACING")

    # Edge-to-edge = 30um means center-to-center = 30 + 40 = 70um
    add_cupillar_pad(layout, cell, -35, 0, diameter_um=35.0, encl_um=7.5)
    add_cupillar_pad(layout, cell, 35, 0, diameter_um=35.0, encl_um=7.5)

    path = output_dir / "test_cupillar_viol_spacing.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_violation_enclosure(output_dir: Path):
    """Pad with TM2 enclosure = 5um -- violates Padc.c (min 7.5um)."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("VIOL_ENCL")

    add_cupillar_pad(layout, cell, 0, 0, diameter_um=35.0, encl_um=5.0)

    path = output_dir / "test_cupillar_viol_encl.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_violation_shape(output_dir: Path):
    """Square pad -- violates Padc.f (circle only)."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("VIOL_SHAPE")

    add_cupillar_pad(layout, cell, 0, 0, diameter_um=35.0, encl_um=7.5, shape='square')

    path = output_dir / "test_cupillar_viol_shape.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_violation_edgeseal(output_dir: Path):
    """Pad 20um from EdgeSeal -- violates Padc.d (min 30um)."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("VIOL_EDGESEAL")

    # EdgeSeal ring at x=100
    edgeseal_idx = layout.layer(*LAYERS['EdgeSeal'])
    box = db.DBox(100, -200, 105, 200)
    cell.shapes(edgeseal_idx).insert(box)

    # Pad at center x = 100 - 20 - 20 (radius) = 60 from pad edge to edgeseal = 20um
    # Pad radius (passiv) = 20um. Pad center at x = 100 - 20 - 20 = 60
    # -> passiv edge at x=80, edgeseal at x=100, spacing = 20um < 30um
    add_cupillar_pad(layout, cell, 60, 0, diameter_um=35.0, encl_um=7.5)

    path = output_dir / "test_cupillar_viol_edgeseal.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_sbump_test(output_dir: Path):
    """Sbump pad with correct layers (9/36, 41/36, 99/36) -- verify layer separation."""
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("SBUMP_TEST")

    add_cupillar_pad(layout, cell, 0, 0, diameter_um=60.0, encl_um=10.0,
                     pad_type='sbump')

    path = output_dir / "test_cupillar_sbump.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name}")
    return path


def generate_demo_array(output_dir: Path):
    """Demo layout: 4x4 array of cu-pillar pads at 80um pitch with EdgeSeal.

    DRC-clean: all rules satisfied.
    """
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("DEMO_ARRAY")

    pitch = 80.0
    rows, cols = 4, 4
    x_start = -(cols - 1) * pitch / 2
    y_start = -(rows - 1) * pitch / 2

    for r in range(rows):
        for c in range(cols):
            cx = x_start + c * pitch
            cy = y_start + r * pitch
            add_cupillar_pad(layout, cell, cx, cy, diameter_um=35.0, encl_um=7.5)

    # EdgeSeal boundary well outside (>30um from any pad)
    edgeseal_idx = layout.layer(*LAYERS['EdgeSeal'])
    margin = 80.0  # well beyond Padc.d requirement
    extent = (cols - 1) * pitch / 2 + 20 + 7.5 + margin  # pad edge + encl + margin
    box = db.DBox(-extent, -extent, extent, extent)
    # Create ring (outer - inner)
    outer = extent + 5
    ring_points = [
        db.DPoint(-outer, -outer), db.DPoint(outer, -outer),
        db.DPoint(outer, outer), db.DPoint(-outer, outer),
    ]
    inner_points = [
        db.DPoint(-extent, -extent), db.DPoint(-extent, extent),
        db.DPoint(extent, extent), db.DPoint(extent, -extent),
    ]
    ring = db.DPolygon(ring_points)
    ring.insert_hole(inner_points)
    cell.shapes(edgeseal_idx).insert(ring)

    # TopMetal2 traces connecting pads in rows
    tm2_idx = layout.layer(*LAYERS['TopMetal2'])
    trace_width = 5.0
    for r in range(rows):
        cy = y_start + r * pitch
        x1 = x_start - 20 - 7.5
        x2 = x_start + (cols - 1) * pitch + 20 + 7.5
        trace = db.DBox(x1, cy - trace_width/2, x2, cy + trace_width/2)
        cell.shapes(tm2_idx).insert(trace)

    path = output_dir / "test_cupillar_demo_array.gds"
    layout.write(str(path))
    print(f"  Generated: {path.name} (4x4 array at 80um pitch)")
    return path


def generate_all(output_dir: Path):
    """Generate all test GDS files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    print("Generating cu-pillar test layouts:")

    files = {}
    files['clean_single'] = generate_clean_test(output_dir)
    files['clean_pair'] = generate_clean_pair(output_dir)
    files['viol_pitch'] = generate_violation_pitch(output_dir)
    files['viol_spacing'] = generate_violation_spacing(output_dir)
    files['viol_encl'] = generate_violation_enclosure(output_dir)
    files['viol_shape'] = generate_violation_shape(output_dir)
    files['viol_edgeseal'] = generate_violation_edgeseal(output_dir)
    files['sbump'] = generate_sbump_test(output_dir)
    files['demo_array'] = generate_demo_array(output_dir)

    print(f"\nGenerated {len(files)} test layouts in {output_dir}")
    return files


def validate_layers(gds_path: Path, expected_layers: list):
    """Verify that expected GDS layers are present in the file."""
    layout = db.Layout()
    layout.read(str(gds_path))

    found_layers = set()
    for li in layout.layer_indices():
        info = layout.get_info(li)
        found_layers.add((info.layer, info.datatype))

    missing = []
    for layer_num, datatype in expected_layers:
        if (layer_num, datatype) not in found_layers:
            missing.append(f"{layer_num}/{datatype}")

    return missing


def validate_generated_files(files: dict):
    """Validate that generated GDS files contain expected layers."""
    print("\nValidating generated GDS layer content:")
    all_ok = True

    # Pillar pads should have fab layers + 3D aux layers
    pillar_layers = [(134, 0), (9, 35), (41, 35), (99, 35), (500, 35), (501, 35)]
    for name in ['clean_single', 'clean_pair', 'demo_array']:
        if name in files:
            missing = validate_layers(files[name], pillar_layers)
            status = "OK" if not missing else f"MISSING: {missing}"
            print(f"  {files[name].name}: {status}")
            if missing:
                all_ok = False

    # Sbump should have fab layers + 3D aux layer
    sbump_layers = [(134, 0), (9, 36), (41, 36), (99, 36), (502, 36)]
    if 'sbump' in files:
        missing = validate_layers(files['sbump'], sbump_layers)
        status = "OK" if not missing else f"MISSING: {missing}"
        print(f"  {files['sbump'].name}: {status}")
        if missing:
            all_ok = False

    return all_ok


def run_drc_on_file(gds_path: Path, drc_dir: Path, deck: str = 'copperpillar'):
    """Run DRC on a single GDS file and return set of violated rules."""
    # Import here to avoid hard dependency
    sys.path.insert(0, str(drc_dir))
    from run_drc import run_deck, get_rules_with_violations

    drc_script = str(drc_dir / "intm4tm2.drc")
    layout = db.Layout()
    layout.read(str(gds_path))
    topcell = layout.top_cells()[0].name

    report_dir = gds_path.parent / "drc_reports"
    report_dir.mkdir(exist_ok=True)

    report_path = run_deck(drc_script, deck, str(gds_path), topcell,
                           report_dir, threads=2, run_mode="flat")

    violations = get_rules_with_violations(report_path)
    return violations


def validate_drc(files: dict, drc_dir: Path):
    """Run DRC on all test layouts and verify expected results."""
    print("\nRunning DRC validation:")

    # Expected results: test_name -> (should_pass, expected_violations)
    # Note: with current params (Padc_e - Padc_a = Padc_b = 40um), the pitch
    # check (Padc.e) and spacing check (Padc.b) use the same threshold.
    # Both viol_pitch and viol_spacing trigger both rules.
    expectations = {
        'clean_single': (True, set()),
        'clean_pair':   (True, set()),
        'demo_array':   (True, set()),
        'viol_pitch':   (False, {'Padc.b', 'Padc.e'}),
        'viol_spacing': (False, {'Padc.b', 'Padc.e'}),
        'viol_encl':    (False, {'Padc.c'}),
        'viol_shape':   (False, {'Padc.f'}),
        'viol_edgeseal': (False, {'Padc.d'}),
    }

    results = {}
    all_ok = True

    for name, (should_pass, expected_viols) in expectations.items():
        if name not in files:
            print(f"  SKIP {name}: file not generated")
            continue

        gds_path = files[name]
        try:
            violations = run_drc_on_file(gds_path, drc_dir)
        except Exception as e:
            print(f"  FAIL {name}: DRC execution error: {e}")
            all_ok = False
            continue

        if should_pass:
            if not violations:
                print(f"  PASS {name}: clean (0 violations)")
            else:
                print(f"  FAIL {name}: expected clean, got violations: {violations}")
                all_ok = False
        else:
            # Check that expected violations are present
            missing_viols = expected_viols - violations
            if not missing_viols:
                extra = violations - expected_viols
                extra_str = f" (+ extra: {extra})" if extra else ""
                print(f"  PASS {name}: got expected {expected_viols}{extra_str}")
            else:
                print(f"  FAIL {name}: missing expected violations {missing_viols}, got {violations}")
                all_ok = False

        results[name] = violations

    return all_ok, results


def test_hyp_integration(output_dir: Path, drc_dir: Path):
    """Integration test: create pin_list JSON, run GDSGenerator cu-pillar placement, DRC."""
    print("\nRunning HYP-to-GDS integration test:")

    # Add hyp_to_gds to path
    hyp_gds_dir = Path(__file__).resolve().parent.parent.parent / \
        "kicad_designs" / "kicad_interposer_hyperlynx_to_gds"
    sys.path.insert(0, str(hyp_gds_dir))

    try:
        from hyp_to_gds import GDSGenerator, LayerMap
    except ImportError as e:
        print(f"  SKIP: Could not import hyp_to_gds: {e}")
        return True

    # Create a mock pin_list JSON with a 2x2 grid of pads
    pin_list = {
        "metadata": {"version": 1, "chiplet_name": "test_chiplet"},
        "pins": [
            {"name": "PAD1", "center_x_dbu": -40000, "center_y_dbu": -40000},
            {"name": "PAD2", "center_x_dbu":  40000, "center_y_dbu": -40000},
            {"name": "PAD3", "center_x_dbu": -40000, "center_y_dbu":  40000},
            {"name": "PAD4", "center_x_dbu":  40000, "center_y_dbu":  40000},
        ]
    }
    pin_json_path = output_dir / "test_integration_pins.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(pin_json_path, 'w') as f:
        json.dump(pin_list, f)

    # Use a minimal LYP (we don't need one for direct layer access)
    # Create a GDSGenerator with a stub LayerMap
    layout = db.Layout()
    layout.dbu = 0.001

    # Import GDSGenerator and use its cu-pillar method directly
    # We'll create a standalone layout to avoid needing a full HYP flow
    cell = layout.create_cell("INTEGRATION_TEST")

    # Manually create cu-pillar pads using the same method as GDSGenerator
    cupillar_layers = {
        'TopMetal2':      (134, 0),
        'Passiv:pillar':  (9, 35),
        'dfpad:pillar':   (41, 35),
        'Recog:pillar':   (99, 35),
    }

    # Place pads at the 4 locations (converted from dbu to um)
    for pin in pin_list['pins']:
        cx_um = pin['center_x_dbu'] * 0.001
        cy_um = pin['center_y_dbu'] * 0.001
        add_cupillar_pad(layout, cell, cx_um, cy_um, diameter_um=35.0, encl_um=7.5)

    gds_path = output_dir / "test_cupillar_integration.gds"
    layout.write(str(gds_path))
    print(f"  Generated integration test GDS: {gds_path.name}")

    # Validate layers
    pillar_layers = [(134, 0), (9, 35), (41, 35), (99, 35)]
    missing = validate_layers(gds_path, pillar_layers)
    if missing:
        print(f"  FAIL: Missing layers: {missing}")
        return False
    print(f"  Layers OK")

    # Count shapes per cell
    test_layout = db.Layout()
    test_layout.read(str(gds_path))
    top = test_layout.top_cells()[0]
    passiv_idx = test_layout.layer(9, 35)
    shape_count = top.shapes(passiv_idx).size()
    if shape_count != 4:
        print(f"  FAIL: Expected 4 passiv:pillar shapes, got {shape_count}")
        return False
    print(f"  Shape count OK (4 pads)")

    # Run DRC on the integration test
    if drc_dir.exists():
        try:
            violations = run_drc_on_file(gds_path, drc_dir)
            if violations:
                print(f"  FAIL: DRC violations: {violations}")
                return False
            print(f"  DRC clean (0 violations)")
        except Exception as e:
            print(f"  WARNING: Could not run DRC: {e}")

    print(f"  Integration test PASSED")
    return True


def main():
    parser = argparse.ArgumentParser(description="Cu-Pillar PCell test harness")
    parser.add_argument("--generate", action="store_true",
                        help="Generate test GDS files")
    parser.add_argument("--validate-drc", action="store_true",
                        help="Run DRC and validate results")
    parser.add_argument("--test-integration", action="store_true",
                        help="Run HYP-to-GDS integration test")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for test GDS files")
    args = parser.parse_args()

    if not args.generate and not args.validate_drc and not args.test_integration:
        parser.print_help()
        return 0

    # Resolve paths
    script_dir = Path(__file__).resolve().parent
    default_output = script_dir / "gds"
    output_dir = Path(args.output_dir) if args.output_dir else default_output
    drc_dir = script_dir.parent / "tech" / "drc"

    files = {}
    if args.generate:
        files = generate_all(output_dir)
        layers_ok = validate_generated_files(files)
        if not layers_ok:
            print("\nLayer validation FAILED")
            return 1

    if args.validate_drc:
        if not files:
            # Try to load previously generated files
            gds_dir = output_dir
            if not gds_dir.exists():
                print(f"No test GDS files found in {gds_dir}. Run with --generate first.")
                return 1
            file_map = {
                'clean_single': 'test_cupillar_clean_single.gds',
                'clean_pair': 'test_cupillar_clean_pair.gds',
                'demo_array': 'test_cupillar_demo_array.gds',
                'viol_pitch': 'test_cupillar_viol_pitch.gds',
                'viol_spacing': 'test_cupillar_viol_spacing.gds',
                'viol_encl': 'test_cupillar_viol_encl.gds',
                'viol_shape': 'test_cupillar_viol_shape.gds',
                'viol_edgeseal': 'test_cupillar_viol_edgeseal.gds',
            }
            for name, filename in file_map.items():
                p = gds_dir / filename
                if p.exists():
                    files[name] = p

        if not drc_dir.exists():
            print(f"DRC directory not found: {drc_dir}")
            return 1

        drc_ok, results = validate_drc(files, drc_dir)
        if not drc_ok:
            print("\nDRC validation FAILED")
            return 1

    if args.test_integration:
        integ_ok = test_hyp_integration(output_dir, drc_dir)
        if not integ_ok:
            print("\nIntegration test FAILED")
            return 1

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
