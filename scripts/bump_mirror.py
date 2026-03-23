#!/usr/bin/env python3
"""
bump_mirror.py -- Standalone Cu-Pillar Generator with DRC Pre-Validation

Generates Cu-pillar pad GDS from pin_list JSON files, with DRC validation
against IHP SG13G2 interposer design rules (Table 6.1) before GDS output.

Can be used standalone or to pre-generate a GDS that hyp_to_gds.py merges
via --cupillar-gds.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import klayout.db as db
except ImportError:
    db = None

# ---------------------------------------------------------------------------
# Constants -- mirrored from hyp_to_gds.py GDSGenerator
# ---------------------------------------------------------------------------

CUPILLAR_FAB_LAYERS = {
    'TopMetal2':      (134, 0),
    'Passiv:pillar':  (9, 35),
    'dfpad:pillar':   (41, 35),
    'Recog:pillar':   (99, 35),
}

CUPILLAR_3D_LAYERS = {
    'CuPillar:pillar':  (500, 35),
    'SnAgCap:pillar':   (501, 35),
}

CUPILLAR_BODY_DIAMETER = 44.0  # um, Table 6.1 Option 1

# IHP SG13G2: 1 DBU = 1 nm -> 0.001 um
DBU_TO_UM = 0.001

# Default tech values (Option 1, 35 um opening) -- used when no JSON provided
DEFAULT_DRC_PARAMS = {
    'Padc_a': 35.0,
    'Padc_b': 40.0,
    'Padc_c': 7.5,
    'Padc_d': 30.0,
    'Padc_e': 75.0,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DrcParams:
    """Cu-pillar DRC parameters from interposer_tech_default.json."""
    diameter_um: float = 35.0       # Padc_a
    min_spacing_um: float = 40.0    # Padc_b (edge-to-edge)
    min_enclosure_um: float = 7.5   # Padc_c (TM2 enclosure around passiv)
    min_edgeseal_um: float = 30.0   # Padc_d
    min_pitch_um: float = 75.0      # Padc_e (center-to-center)

    @classmethod
    def load(cls, path: Optional[str] = None) -> 'DrcParams':
        """Load DRC params from interposer_tech_default.json.

        Falls back to hardcoded Option 1 defaults if path is None or
        file is missing.
        """
        if path is None:
            # Try auto-detect relative to this script
            script_dir = Path(__file__).resolve().parent
            candidate = (script_dir.parent / "interposer_klayout" / "tech" /
                         "drc" / "rule_decks" / "interposer_tech_default.json")
            if candidate.exists():
                path = str(candidate)

        if path and Path(path).exists():
            with open(path, 'r') as f:
                data = json.load(f)
            rules = data.get('rules', {})
            return cls(
                diameter_um=rules.get('Padc_a', DEFAULT_DRC_PARAMS['Padc_a']),
                min_spacing_um=rules.get('Padc_b', DEFAULT_DRC_PARAMS['Padc_b']),
                min_enclosure_um=rules.get('Padc_c', DEFAULT_DRC_PARAMS['Padc_c']),
                min_edgeseal_um=rules.get('Padc_d', DEFAULT_DRC_PARAMS['Padc_d']),
                min_pitch_um=rules.get('Padc_e', DEFAULT_DRC_PARAMS['Padc_e']),
            )

        return cls()  # hardcoded defaults

    def to_dict(self) -> dict:
        return {
            'Padc_a_diameter_um': self.diameter_um,
            'Padc_b_min_spacing_um': self.min_spacing_um,
            'Padc_c_min_enclosure_um': self.min_enclosure_um,
            'Padc_d_min_edgeseal_um': self.min_edgeseal_um,
            'Padc_e_min_pitch_um': self.min_pitch_um,
        }


@dataclass
class BumpLocation:
    """One Cu-pillar pad in global interposer coordinates."""
    device_ref: str
    pin_name: str
    global_x_um: float
    global_y_um: float


@dataclass
class ValidationResult:
    """Single DRC check result."""
    rule: str
    severity: str  # "error", "warning", "info"
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Aggregated DRC validation report."""
    passed: bool
    params: dict
    results: List[ValidationResult]

    @property
    def summary(self) -> dict:
        counts = {'error': 0, 'warning': 0, 'info': 0}
        for r in self.results:
            counts[r.severity] = counts.get(r.severity, 0) + 1
        counts['total'] = len(self.results)
        return counts

    def to_dict(self) -> dict:
        return {
            'passed': self.passed,
            'params': self.params,
            'results': [
                {'rule': r.rule, 'severity': r.severity,
                 'message': r.message, 'details': r.details}
                for r in self.results
            ],
            'summary': self.summary,
        }

    def to_json(self, path: str):
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)


# ---------------------------------------------------------------------------
# DRC Validator -- pure coordinate math, no GDS dependency
# ---------------------------------------------------------------------------

class DrcValidator:
    """Validate Cu-pillar bump locations against DRC rules."""

    def __init__(self, params: DrcParams):
        self.params = params

    def validate(self, bumps: List[BumpLocation],
                 diameter_um: Optional[float] = None,
                 enclosure_um: Optional[float] = None) -> ValidationReport:
        """Run all DRC checks on bump locations.

        Args:
            bumps: List of bump locations in global coordinates
            diameter_um: User-specified diameter (checked against Padc_a)
            enclosure_um: User-specified enclosure (checked against Padc_c)

        Returns:
            ValidationReport with all check results
        """
        results: List[ValidationResult] = []

        # Padc.a -- diameter check
        results.extend(self._check_diameter(diameter_um))

        # Padc.b -- edge-to-edge spacing
        results.extend(self._check_spacing(bumps, diameter_um))

        # Padc.c -- enclosure check
        results.extend(self._check_enclosure(enclosure_um))

        # Padc.e -- center-to-center pitch
        results.extend(self._check_pitch(bumps))

        # Padc.f -- circular shape (always passes, info)
        results.append(ValidationResult(
            rule='Padc.f',
            severity='info',
            message='Circular shape: OK (256-point polygon approximation)',
        ))

        has_errors = any(r.severity == 'error' for r in results)
        return ValidationReport(
            passed=not has_errors,
            params=self.params.to_dict(),
            results=results,
        )

    def _check_diameter(self, diameter_um: Optional[float]) -> List[ValidationResult]:
        if diameter_um is None:
            return [ValidationResult(
                rule='Padc.a',
                severity='info',
                message=f'Using tech diameter: {self.params.diameter_um} um',
            )]
        if abs(diameter_um - self.params.diameter_um) > 0.01:
            return [ValidationResult(
                rule='Padc.a',
                severity='error',
                message=(f'Diameter {diameter_um} um does not match tech value '
                         f'{self.params.diameter_um} um'),
                details={'given': diameter_um, 'expected': self.params.diameter_um},
            )]
        return [ValidationResult(
            rule='Padc.a',
            severity='info',
            message=f'Diameter matches tech: {diameter_um} um',
        )]

    def _check_spacing(self, bumps: List[BumpLocation],
                       diameter_um: Optional[float] = None) -> List[ValidationResult]:
        """Padc.b: edge-to-edge spacing >= min_spacing_um."""
        d = diameter_um if diameter_um is not None else self.params.diameter_um
        min_spacing = self.params.min_spacing_um
        results: List[ValidationResult] = []

        # Sort by x for spatial pruning
        sorted_bumps = sorted(bumps, key=lambda b: b.global_x_um)
        # Max distance to check: diameter + min_spacing (beyond this, spacing is fine)
        max_check_dist = d + min_spacing + 1.0

        violations = 0
        for i, b1 in enumerate(sorted_bumps):
            for j in range(i + 1, len(sorted_bumps)):
                b2 = sorted_bumps[j]
                dx = b2.global_x_um - b1.global_x_um
                if dx > max_check_dist:
                    break  # all further bumps are too far in x

                dy = b2.global_y_um - b1.global_y_um
                center_dist = math.sqrt(dx * dx + dy * dy)
                edge_spacing = center_dist - d

                if edge_spacing < min_spacing - 0.01:
                    violations += 1
                    if violations <= 5:  # limit detail entries
                        results.append(ValidationResult(
                            rule='Padc.b',
                            severity='error',
                            message=(f'Edge spacing {edge_spacing:.2f} um < '
                                     f'{min_spacing} um between '
                                     f'{b1.device_ref}:{b1.pin_name} and '
                                     f'{b2.device_ref}:{b2.pin_name}'),
                            details={
                                'bump1': f'{b1.device_ref}:{b1.pin_name}',
                                'bump2': f'{b2.device_ref}:{b2.pin_name}',
                                'edge_spacing_um': round(edge_spacing, 3),
                                'min_spacing_um': min_spacing,
                            },
                        ))

        if violations == 0:
            results.append(ValidationResult(
                rule='Padc.b',
                severity='info',
                message=f'All edge-to-edge spacings >= {min_spacing} um',
            ))
        elif violations > 5:
            results.append(ValidationResult(
                rule='Padc.b',
                severity='error',
                message=f'... and {violations - 5} more spacing violations',
            ))

        return results

    def _check_enclosure(self, enclosure_um: Optional[float]) -> List[ValidationResult]:
        """Padc.c: TM2 enclosure around passiv opening."""
        min_encl = self.params.min_enclosure_um
        if enclosure_um is None:
            return [ValidationResult(
                rule='Padc.c',
                severity='info',
                message=f'Using tech enclosure: {min_encl} um',
            )]
        if enclosure_um < min_encl - 0.01:
            return [ValidationResult(
                rule='Padc.c',
                severity='warning',
                message=(f'Enclosure {enclosure_um} um < minimum {min_encl} um'),
                details={'given': enclosure_um, 'minimum': min_encl},
            )]
        return [ValidationResult(
            rule='Padc.c',
            severity='info',
            message=f'Enclosure {enclosure_um} um >= minimum {min_encl} um',
        )]

    def _check_pitch(self, bumps: List[BumpLocation]) -> List[ValidationResult]:
        """Padc.e: center-to-center pitch >= min_pitch_um."""
        min_pitch = self.params.min_pitch_um
        results: List[ValidationResult] = []

        sorted_bumps = sorted(bumps, key=lambda b: b.global_x_um)
        max_check_dist = min_pitch + 1.0

        violations = 0
        for i, b1 in enumerate(sorted_bumps):
            for j in range(i + 1, len(sorted_bumps)):
                b2 = sorted_bumps[j]
                dx = b2.global_x_um - b1.global_x_um
                if dx > max_check_dist:
                    break

                dy = b2.global_y_um - b1.global_y_um
                center_dist = math.sqrt(dx * dx + dy * dy)

                if center_dist < min_pitch - 0.01:
                    violations += 1
                    if violations <= 5:
                        results.append(ValidationResult(
                            rule='Padc.e',
                            severity='error',
                            message=(f'Pitch {center_dist:.2f} um < '
                                     f'{min_pitch} um between '
                                     f'{b1.device_ref}:{b1.pin_name} and '
                                     f'{b2.device_ref}:{b2.pin_name}'),
                            details={
                                'bump1': f'{b1.device_ref}:{b1.pin_name}',
                                'bump2': f'{b2.device_ref}:{b2.pin_name}',
                                'pitch_um': round(center_dist, 3),
                                'min_pitch_um': min_pitch,
                            },
                        ))

        if violations == 0:
            results.append(ValidationResult(
                rule='Padc.e',
                severity='info',
                message=f'All center-to-center pitches >= {min_pitch} um',
            ))
        elif violations > 5:
            results.append(ValidationResult(
                rule='Padc.e',
                severity='error',
                message=f'... and {violations - 5} more pitch violations',
            ))

        return results


# ---------------------------------------------------------------------------
# CuPillar GDS Generator
# ---------------------------------------------------------------------------

class CuPillarGenerator:
    """Generate Cu-pillar pad GDS from bump locations."""

    def __init__(self, diameter_um: float = 35.0, enclosure_um: float = 7.5,
                 num_points: int = 256):
        if db is None:
            raise ImportError("klayout package required for GDS generation. "
                              "Install with: pip install klayout")
        self.diameter_um = diameter_um
        self.enclosure_um = enclosure_um
        self.num_points = num_points
        self.layout = db.Layout()
        self.layout.dbu = 0.001  # 1 nm
        self.top_cell = self.layout.create_cell("TOP")
        self._pillar_cell: Optional[db.Cell] = None

    def _get_pillar_cell(self) -> 'db.Cell':
        """Get or create the shared Cu-pillar geometry cell."""
        if self._pillar_cell is not None:
            return self._pillar_cell

        radius = self.diameter_um / 2.0
        tm2_radius = radius + self.enclosure_um

        cell_name = f"CUPILLAR_{self.diameter_um:.0f}um"
        cell = self.layout.create_cell(cell_name)

        # Fabrication layers
        for layer_name, (layer_num, datatype) in CUPILLAR_FAB_LAYERS.items():
            layer_idx = self.layout.layer(layer_num, datatype)
            r = radius if layer_name == 'Passiv:pillar' else tm2_radius

            points = []
            for i in range(self.num_points):
                angle = 2 * math.pi * i / self.num_points
                x = r * math.cos(angle)
                y = r * math.sin(angle)
                points.append(db.DPoint(x, y))
            cell.shapes(layer_idx).insert(db.DPolygon(points))

        # 3D auxiliary layers
        body_radius = CUPILLAR_BODY_DIAMETER / 2.0
        for layer_name, (layer_num, datatype) in CUPILLAR_3D_LAYERS.items():
            layer_idx = self.layout.layer(layer_num, datatype)
            points = []
            for i in range(self.num_points):
                angle = 2 * math.pi * i / self.num_points
                x = body_radius * math.cos(angle)
                y = body_radius * math.sin(angle)
                points.append(db.DPoint(x, y))
            cell.shapes(layer_idx).insert(db.DPolygon(points))

        self._pillar_cell = cell
        return cell

    def add_bumps(self, bumps: List[BumpLocation]) -> int:
        """Place Cu-pillar instances at bump locations.

        Groups by device_ref for cell hierarchy:
        TOP > CUPILLARS_{ref} > CUPILLAR_{diameter}um instances
        """
        pillar_cell = self._get_pillar_cell()

        # Group bumps by device
        by_device: Dict[str, List[BumpLocation]] = {}
        for b in bumps:
            by_device.setdefault(b.device_ref, []).append(b)

        total = 0
        for ref, device_bumps in sorted(by_device.items()):
            group_cell = self.layout.create_cell(f"CUPILLARS_{ref}")
            self.top_cell.insert(db.DCellInstArray(group_cell, db.DTrans()))

            for b in device_bumps:
                trans = db.DTrans(db.DVector(b.global_x_um, b.global_y_um))
                group_cell.insert(db.DCellInstArray(pillar_cell, trans))
                total += 1

        return total

    def write(self, output_path: str):
        """Write GDS file."""
        self.layout.write(output_path)


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _add_gds_to_kicad_to_path():
    """Add gds_to_kicad directory to sys.path for PinList import."""
    gds_to_kicad_dir = (Path(__file__).resolve().parent.parent.parent /
                        "gds_to_kicad")
    if gds_to_kicad_dir.is_dir():
        path_str = str(gds_to_kicad_dir)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
        return True
    return False


def load_pin_lists(pin_args: List[str]) -> Dict[str, 'PinList']:
    """Parse REF=path pin list arguments.

    Args:
        pin_args: List of "REF=path" strings

    Returns:
        Dict mapping device ref to loaded PinList
    """
    _add_gds_to_kicad_to_path()
    from pin_list import PinList

    result = {}
    for item in pin_args:
        if '=' not in item:
            print(f"Error: Invalid --pins format: '{item}'. Use REF=FILE.",
                  file=sys.stderr)
            sys.exit(1)
        ref, path = item.split('=', 1)
        ref = ref.strip()
        path = path.strip()
        if not Path(path).exists():
            print(f"Error: Pin list not found: {path}", file=sys.stderr)
            sys.exit(1)
        result[ref] = PinList.load(path)
    return result


def load_chiplet_positions(path: str) -> Dict[str, dict]:
    """Parse chiplet YAML for component positions.

    Returns:
        Dict mapping component id to {'x': float, 'y': float, 'rotation': float}
        Skips components with type 'interposer'.
    """
    try:
        import yaml
    except ImportError:
        # Fallback: simple YAML parsing for the subset we need
        return _parse_chiplet_simple(path)

    with open(path, 'r') as f:
        data = yaml.safe_load(f)

    positions = {}
    for comp in data.get('components', []):
        if comp.get('type') == 'interposer':
            continue
        cid = comp.get('id', '')
        pos = comp.get('position', {})
        rot = comp.get('rotation', {})
        positions[cid] = {
            'x': float(pos.get('x', 0.0)),
            'y': float(pos.get('y', 0.0)),
            'rotation': float(rot.get('z', 0.0)),
        }
    return positions


def _parse_chiplet_simple(path: str) -> Dict[str, dict]:
    """Minimal YAML-like parser for chiplet files (no PyYAML dependency)."""
    with open(path, 'r') as f:
        content = f.read()

    # Try to use yaml if somehow available
    try:
        import yaml
        data = yaml.safe_load(content)
        return _extract_positions(data)
    except ImportError:
        pass

    # Very basic fallback -- parse JSON if the file happens to be JSON
    try:
        data = json.loads(content)
        return _extract_positions(data)
    except (json.JSONDecodeError, ValueError):
        pass

    print("Warning: PyYAML not installed and file is not JSON. "
          "Use --position/--rotation instead of --chiplet, "
          "or install PyYAML.", file=sys.stderr)
    return {}


def _extract_positions(data: dict) -> Dict[str, dict]:
    """Extract positions from parsed chiplet data."""
    positions = {}
    for comp in data.get('components', []):
        if comp.get('type') == 'interposer':
            continue
        cid = comp.get('id', '')
        pos = comp.get('position', {})
        rot = comp.get('rotation', {})
        positions[cid] = {
            'x': float(pos.get('x', 0.0)),
            'y': float(pos.get('y', 0.0)),
            'rotation': float(rot.get('z', 0.0)),
        }
    return positions


def compute_bump_locations(
    pin_lists: Dict[str, object],
    positions: Dict[str, dict],
) -> List[BumpLocation]:
    """Convert pin lists + device positions to global bump locations.

    Args:
        pin_lists: Dict of ref -> PinList objects
        positions: Dict of ref -> {'x': um, 'y': um, 'rotation': deg}

    Returns:
        Flat list of BumpLocation across all devices
    """
    bumps: List[BumpLocation] = []

    for ref, pin_list in pin_lists.items():
        pos = positions.get(ref)
        if pos is None:
            print(f"Warning: No position for device {ref}, skipping",
                  file=sys.stderr)
            continue

        dev_x = pos['x']
        dev_y = pos['y']
        rotation = pos.get('rotation', 0.0)

        for pin in pin_list.pins:
            # Convert from DBU (nm) to um
            pad_x_um = pin.center_x_dbu * DBU_TO_UM
            pad_y_um = pin.center_y_dbu * DBU_TO_UM

            # Apply rotation transform
            if rotation != 0.0:
                angle_rad = math.radians(rotation)
                cos_a = math.cos(angle_rad)
                sin_a = math.sin(angle_rad)
                gx = dev_x + pad_x_um * cos_a - pad_y_um * sin_a
                gy = dev_y + pad_x_um * sin_a + pad_y_um * cos_a
            else:
                gx = dev_x + pad_x_um
                gy = dev_y + pad_y_um

            bumps.append(BumpLocation(
                device_ref=ref,
                pin_name=pin.name,
                global_x_um=gx,
                global_y_um=gy,
            ))

    return bumps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cu-Pillar GDS generator with DRC pre-validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Validate only (no GDS output):
  %(prog)s --pins U1=pins_u1.json U2=pins_u2.json \\
           --chiplet design.chiplet --validate-only --report report.json

  # Generate Cu-pillar GDS:
  %(prog)s --pins U1=pins_u1.json --position U1=1000,2000 -o cupillars.gds

  # With explicit rotation and tech JSON:
  %(prog)s --pins U1=pins.json --position U1=100,200 --rotation U1=90 \\
           --tech-json interposer_tech_default.json -o out.gds
        """
    )

    parser.add_argument(
        '--pins', nargs='+', required=True, metavar='REF=FILE',
        help='Pin list JSON files per device (e.g., U1=pins_u1.json)',
    )

    pos_group = parser.add_mutually_exclusive_group(required=True)
    pos_group.add_argument(
        '--position', nargs='+', metavar='REF=X,Y',
        help='Device positions in um (e.g., U1=1000,2000)',
    )
    pos_group.add_argument(
        '--chiplet', metavar='FILE',
        help='Chiplet YAML file for positions (replaces --position/--rotation)',
    )

    parser.add_argument(
        '--rotation', nargs='+', metavar='REF=DEG',
        help='Device rotations in degrees (e.g., U1=90)',
    )
    parser.add_argument(
        '--tech-json', metavar='FILE',
        help='Path to interposer_tech_default.json (auto-detected if omitted)',
    )
    parser.add_argument(
        '--diameter', type=float, metavar='UM',
        help='Override passiv opening diameter (Padc_a)',
    )
    parser.add_argument(
        '--enclosure', type=float, metavar='UM',
        help='Override TM2 enclosure (Padc_c)',
    )
    parser.add_argument(
        '--validate-only', action='store_true',
        help='Run DRC validation only, no GDS output',
    )
    parser.add_argument(
        '--report', metavar='FILE',
        help='Write JSON validation report to file',
    )
    parser.add_argument(
        '-o', '--output', default='cupillars.gds', metavar='FILE',
        help='Output GDS path (default: cupillars.gds)',
    )

    return parser


def parse_positions(pos_args: List[str]) -> Dict[str, dict]:
    """Parse --position REF=X,Y arguments."""
    positions = {}
    for item in pos_args:
        if '=' not in item:
            print(f"Error: Invalid --position format: '{item}'. Use REF=X,Y.",
                  file=sys.stderr)
            sys.exit(1)
        ref, coords = item.split('=', 1)
        parts = coords.split(',')
        if len(parts) != 2:
            print(f"Error: Invalid coordinates for {ref}: '{coords}'. Use X,Y.",
                  file=sys.stderr)
            sys.exit(1)
        positions[ref.strip()] = {
            'x': float(parts[0]),
            'y': float(parts[1]),
            'rotation': 0.0,
        }
    return positions


def parse_rotations(rot_args: Optional[List[str]],
                    positions: Dict[str, dict]):
    """Apply --rotation REF=DEG arguments to positions dict (in-place)."""
    if not rot_args:
        return
    for item in rot_args:
        if '=' not in item:
            print(f"Error: Invalid --rotation format: '{item}'. Use REF=DEG.",
                  file=sys.stderr)
            sys.exit(1)
        ref, deg = item.split('=', 1)
        ref = ref.strip()
        if ref in positions:
            positions[ref]['rotation'] = float(deg)
        else:
            print(f"Warning: Rotation for unknown device {ref}, ignoring",
                  file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Load DRC params
    params = DrcParams.load(args.tech_json)

    # Resolve diameter/enclosure
    diameter = args.diameter if args.diameter is not None else params.diameter_um
    enclosure = args.enclosure if args.enclosure is not None else params.min_enclosure_um

    # Load pin lists
    pin_lists = load_pin_lists(args.pins)

    # Load positions
    if args.chiplet:
        positions = load_chiplet_positions(args.chiplet)
    else:
        positions = parse_positions(args.position)
        parse_rotations(args.rotation, positions)

    # Compute and validate per device
    validator = DrcValidator(params)
    passed_devices: Dict[str, List[BumpLocation]] = {}
    failed_devices: List[str] = []
    all_reports: Dict[str, ValidationReport] = {}

    for ref, pin_list in pin_lists.items():
        device_bumps = compute_bump_locations({ref: pin_list}, positions)
        if not device_bumps:
            print(f"  {ref}: no bump locations (missing position?), skipping")
            failed_devices.append(ref)
            continue

        report = validator.validate(device_bumps, args.diameter, args.enclosure)
        all_reports[ref] = report

        if report.passed:
            passed_devices[ref] = device_bumps
            print(f"  {ref}: {len(device_bumps)} bumps, DRC PASSED")
        else:
            failed_devices.append(ref)
            summary = report.summary
            print(f"  {ref}: {len(device_bumps)} bumps, DRC FAILED "
                  f"({summary['error']} errors)")
            for r in report.results:
                if r.severity == 'error':
                    print(f"    ERROR  [{r.rule}] {r.message}")

    total_passed = sum(len(b) for b in passed_devices.values())
    total_devices = len(pin_lists)
    print(f"\nSummary: {len(passed_devices)}/{total_devices} devices passed DRC, "
          f"{total_passed} bumps valid")

    if failed_devices:
        print(f"Failed devices: {', '.join(failed_devices)}")

    # Write combined report
    if args.report:
        combined = {
            'devices': {ref: rpt.to_dict() for ref, rpt in all_reports.items()},
            'passed_devices': list(passed_devices.keys()),
            'failed_devices': failed_devices,
        }
        with open(args.report, 'w') as f:
            json.dump(combined, f, indent=2)
        print(f"Report written to: {args.report}")

    if args.validate_only:
        return 0

    # Generate GDS for devices that passed
    if not passed_devices:
        print("\nNo devices passed DRC. GDS generation skipped.",
              file=sys.stderr)
        return 1

    all_bumps = [b for bumps in passed_devices.values() for b in bumps]
    generator = CuPillarGenerator(diameter, enclosure)
    count = generator.add_bumps(all_bumps)
    generator.write(args.output)
    print(f"\nGenerated {count} Cu-pillar pads ({len(passed_devices)} devices) "
          f"-> {args.output}")

    if failed_devices:
        print(f"Skipped {len(failed_devices)} device(s) with DRC errors: "
              f"{', '.join(failed_devices)}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
