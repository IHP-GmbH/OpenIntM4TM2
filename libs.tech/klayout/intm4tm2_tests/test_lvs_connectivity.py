#!/usr/bin/env python3
"""
Interposer LVS connectivity test harness.

Generates minimal GDS fixtures for the IntM4TM2 LVS deck and validates the
three connectivity verdicts:

  - clean: labeled nets extracted, no findings, reference netlist matches
  - open:  one label split across disconnected nets -> Connectivity OPEN
  - short: two labels landing on a bridged net      -> Connectivity SHORT

Usage:
    python test_lvs_connectivity.py --generate          # Generate fixtures
    python test_lvs_connectivity.py --validate-lvs      # Run LVS and check verdicts
    python test_lvs_connectivity.py --generate --validate-lvs  # Both
"""

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import klayout.db as db
except ImportError:
    print("Error: KLayout Python module not found.", file=sys.stderr)
    print("Install with: pip install klayout", file=sys.stderr)
    sys.exit(1)


# Layers used by the LVS deck (see lvs/rule_decks/layers_definitions.lvs)
TM2 = (134, 0)
TM2_TEXT = (134, 25)
TOPVIA2 = (133, 0)
TM1 = (126, 0)

UM = 1000  # dbu per um at dbu = 0.001


def _box(cell, layer_idx, x1, y1, x2, y2):
    cell.shapes(layer_idx).insert(
        db.Box(int(x1 * UM), int(y1 * UM), int(x2 * UM), int(y2 * UM))
    )


def _label(cell, layer_idx, text, x, y):
    cell.shapes(layer_idx).insert(
        db.Text(text, db.Trans(db.Point(int(x * UM), int(y * UM))))
    )


def _new_layout(topcell_name):
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell(topcell_name)
    layers = {
        'tm2': layout.layer(*TM2),
        'tm2_text': layout.layer(*TM2_TEXT),
        'topvia2': layout.layer(*TOPVIA2),
        'tm1': layout.layer(*TM1),
    }
    return layout, top, layers


def _add_connected_pair(top, ly, label):
    """Two TM2 pads joined through TopVia2 / TM1: a single labeled net."""
    _box(top, ly['tm2'], 0, 0, 50, 50)        # pad A
    _box(top, ly['tm2'], 200, 0, 250, 50)     # pad B
    _box(top, ly['topvia2'], 20, 20, 30, 30)
    _box(top, ly['topvia2'], 220, 20, 230, 30)
    _box(top, ly['tm1'], 15, 15, 235, 35)     # TM1 trace
    _label(top, ly['tm2_text'], label, 25, 25)


def generate_clean(gds_path: Path):
    """Net VDD (connected pair) + isolated FLOATPAD net."""
    layout, top, ly = _new_layout("LVS_CLEAN")
    _add_connected_pair(top, ly, "VDD")
    _box(top, ly['tm2'], 400, 0, 450, 50)
    _label(top, ly['tm2_text'], "FLOATPAD", 425, 25)
    layout.write(str(gds_path))


def generate_open(gds_path: Path):
    """Clean case plus a disconnected pad carrying the same VDD label."""
    layout, top, ly = _new_layout("LVS_OPEN")
    _add_connected_pair(top, ly, "VDD")
    _box(top, ly['tm2'], 400, 0, 450, 50)
    _label(top, ly['tm2_text'], "FLOATPAD", 425, 25)
    _box(top, ly['tm2'], 600, 0, 650, 50)     # disconnected, also labeled VDD
    _label(top, ly['tm2_text'], "VDD", 625, 25)
    layout.write(str(gds_path))


def generate_short(gds_path: Path):
    """Two pads with different labels accidentally bridged on TM2."""
    layout, top, ly = _new_layout("LVS_SHORT")
    _box(top, ly['tm2'], 0, 0, 50, 50)
    _box(top, ly['tm2'], 100, 0, 150, 50)
    _box(top, ly['tm2'], 50, 20, 100, 30)     # the bridge
    _label(top, ly['tm2_text'], "VDD", 25, 25)
    _label(top, ly['tm2_text'], "SIG", 125, 25)
    layout.write(str(gds_path))


def generate_reference(cir_path: Path):
    """Reference netlist matching the clean fixture (interface nets as pins)."""
    cir_path.write_text(
        ".SUBCKT LVS_CLEAN VDD FLOATPAD\n"
        ".ENDS LVS_CLEAN\n"
    )


def generate_fixtures(gds_dir: Path):
    gds_dir.mkdir(parents=True, exist_ok=True)
    files = {
        'clean': gds_dir / "lvs_clean.gds",
        'open': gds_dir / "lvs_open.gds",
        'short': gds_dir / "lvs_short.gds",
        'reference': gds_dir / "lvs_clean_reference.cir",
    }
    generate_clean(files['clean'])
    generate_open(files['open'])
    generate_short(files['short'])
    generate_reference(files['reference'])
    for name, path in files.items():
        print(f"  generated {name}: {path}")
    return files


def run_lvs(lvs_deck: Path, gds_path: Path, schematic: Path = None):
    """Run the LVS deck in KLayout batch mode and return combined output."""
    cmd = [
        "klayout", "-b", "-r", str(lvs_deck),
        "-rd", f"input={gds_path}",
        "-rd", "top_lvl_pins=true",
    ]
    if schematic is not None:
        cmd += ["-rd", f"schematic={schematic}"]
        cmd += ["-rd", f"report={gds_path.with_suffix('.lvsdb')}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.stdout + result.stderr


def validate_lvs(files: dict, lvs_deck: Path):
    failures = []

    # Clean fixture: no findings, and the reference netlist must match
    out = run_lvs(lvs_deck, files['clean'], schematic=files['reference'])
    if "Connectivity OPEN" in out or "Connectivity SHORT" in out:
        failures.append("clean: unexpected open/short finding")
    if "LVS netlists match" not in out:
        failures.append("clean: reference netlist did not match")

    # Open fixture: split VDD label must be reported
    out = run_lvs(lvs_deck, files['open'])
    if "Connectivity OPEN: label 'VDD'" not in out:
        failures.append("open: split VDD net not reported")

    # Short fixture: bridged SIG/VDD labels must be reported
    out = run_lvs(lvs_deck, files['short'])
    if "Connectivity SHORT" not in out:
        failures.append("short: bridged nets not reported")

    if failures:
        print("LVS validation FAILED:")
        for f in failures:
            print(f"  - {f}")
        return False

    print("LVS validation PASSED: clean/open/short verdicts all correct")
    return True


def main():
    parser = argparse.ArgumentParser(description="Interposer LVS test harness")
    parser.add_argument("--generate", action="store_true",
                        help="Generate test GDS fixtures")
    parser.add_argument("--validate-lvs", action="store_true",
                        help="Run LVS on fixtures and check verdicts")
    args = parser.parse_args()

    if not args.generate and not args.validate_lvs:
        parser.print_help()
        return 1

    script_dir = Path(__file__).resolve().parent
    gds_dir = script_dir / "gds"
    lvs_deck = script_dir.parent / "tech" / "lvs" / "intm4tm2.lvs"

    files = {
        'clean': gds_dir / "lvs_clean.gds",
        'open': gds_dir / "lvs_open.gds",
        'short': gds_dir / "lvs_short.gds",
        'reference': gds_dir / "lvs_clean_reference.cir",
    }

    if args.generate:
        print("Generating LVS fixtures...")
        files = generate_fixtures(gds_dir)

    if args.validate_lvs:
        missing = [str(p) for p in files.values() if not p.is_file()]
        if missing:
            print(f"Missing fixtures (run with --generate first): {missing}")
            return 1
        if not validate_lvs(files, lvs_deck):
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
