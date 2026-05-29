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

# IHP SG13G2 Layout Rules Table 6.1 -- Cu-pillar options
# Keyed by body diameter (um)
#
# Entry 'custom_25um' (body=35) is reverse-engineered from the manually-designed
# fabrication GDS gds_to_kicad/gds_files/for_thermal_eval/T608_Interposer_SigSrc.gds
# (Passiv 25 um circle, dfpad 45 um, top-row pitch 75 um). It corresponds to a
# fine-pitch PacTech variant NOT listed in Table 6.1 of SG13G2_os_layout_rules.pdf.
# The PDF itself defers such geometries to the PacTech datasheet, which is not
# yet available in the project. The body height/cap values below mirror Option 1
# as a placeholder until the datasheet arrives.
CUPILLAR_TABLE_6_1 = {
    35: {
        'option': 'custom_25um', 'passiv_opening': 25, 'spacing': 50, 'pitch': 75,
        'cu_height': 28, 'snag_height': 16, 'enclosure': 10.0,
    },
    44: {
        'option': 1, 'passiv_opening': 35, 'spacing': 40, 'pitch': 75,
        'cu_height': 28, 'snag_height': 16, 'enclosure': 7.5,
    },
    49: {
        'option': 2, 'passiv_opening': 40, 'spacing': 40, 'pitch': 80,
        'cu_height': 32, 'snag_height': 16, 'enclosure': 7.5,
    },
    54: {
        'option': 3, 'passiv_opening': 45, 'spacing': 50, 'pitch': 95,
        'cu_height': 42, 'snag_height': 19, 'enclosure': 7.5,
    },
}
DEFAULT_BODY_DIAMETER = 49  # Option 2 (40um opening) -- default for assembly

# IHP SG13G2: 1 DBU = 1 nm -> 0.001 um
DBU_TO_UM = 0.001

# Default tech values (Option 2, 40 um opening) -- used when no JSON provided
DEFAULT_DRC_PARAMS = {
    'Padc_a': 40.0,
    'Padc_b': 40.0,
    'Padc_c': 7.5,
    'Padc_d': 30.0,
    'Padc_e': 80.0,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DrcParams:
    """Cu-pillar DRC parameters from interposer_tech_default.json."""
    diameter_um: float = 40.0       # Padc_a (Option 2 default)
    min_spacing_um: float = 40.0    # Padc_b (edge-to-edge)
    min_enclosure_um: float = 7.5   # Padc_c (TM2 enclosure around passiv)
    min_edgeseal_um: float = 30.0   # Padc_d
    min_pitch_um: float = 80.0      # Padc_e (center-to-center, Option 2)

    @classmethod
    def from_body_diameter(cls, body_diameter: float) -> 'DrcParams':
        """Create DRC params from Table 6.1 based on Cu-pillar body diameter."""
        option = CUPILLAR_TABLE_6_1.get(body_diameter)
        if option is None:
            print(f"Warning: Unknown body diameter {body_diameter} um, "
                  f"using Option 2 defaults", file=sys.stderr)
            return cls()
        return cls(
            diameter_um=option['passiv_opening'],
            min_spacing_um=option['spacing'],
            min_enclosure_um=option['enclosure'],
            min_edgeseal_um=30.0,
            min_pitch_um=option['pitch'],
        )

    @classmethod
    def load(cls, path: Optional[str] = None) -> 'DrcParams':
        """Load DRC params from interposer_tech_default.json.

        Falls back to hardcoded Option 2 defaults if path is None or
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
                 enclosure_um: Optional[float] = None,
                 max_detail: Optional[int] = 5) -> ValidationReport:
        """Run all DRC checks on bump locations.

        Args:
            bumps: List of bump locations in global coordinates
            diameter_um: User-specified diameter (checked against Padc_a)
            enclosure_um: User-specified enclosure (checked against Padc_c)
            max_detail: Max per-rule detailed violation entries to emit
                (a trailing "... and N more" summary covers the rest).
                Pass ``None`` for a complete, uncapped report (every
                violation gets its own result with structured details).

        Returns:
            ValidationReport with all check results
        """
        results: List[ValidationResult] = []

        # Padc.a -- diameter check
        results.extend(self._check_diameter(diameter_um))

        # Padc.b -- edge-to-edge spacing
        results.extend(self._check_spacing(bumps, diameter_um, max_detail))

        # Padc.c -- enclosure check
        results.extend(self._check_enclosure(enclosure_um))

        # Padc.e -- center-to-center pitch
        results.extend(self._check_pitch(bumps, max_detail))

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
                       diameter_um: Optional[float] = None,
                       max_detail: Optional[int] = 5) -> List[ValidationResult]:
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
                    if max_detail is None or violations <= max_detail:
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
        elif max_detail is not None and violations > max_detail:
            results.append(ValidationResult(
                rule='Padc.b',
                severity='error',
                message=f'... and {violations - max_detail} more spacing '
                        f'violations',
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

    def _check_pitch(self, bumps: List[BumpLocation],
                     max_detail: Optional[int] = 5) -> List[ValidationResult]:
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
                    if max_detail is None or violations <= max_detail:
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
        elif max_detail is not None and violations > max_detail:
            results.append(ValidationResult(
                rule='Padc.e',
                severity='error',
                message=f'... and {violations - max_detail} more pitch '
                        f'violations',
            ))

        return results


# ---------------------------------------------------------------------------
# CuPillar GDS Generator
# ---------------------------------------------------------------------------

class CuPillarGenerator:
    """Generate Cu-pillar pad GDS from bump locations.

    Supports per-device body diameters via Table 6.1 lookup.
    Caches pillar cells by body diameter so mixed-option assemblies
    use the correct geometry for each device.
    """

    def __init__(self, enclosure_um: float = 7.5, num_points: int = 256):
        if db is None:
            raise ImportError("klayout package required for GDS generation. "
                              "Install with: pip install klayout")
        self.enclosure_um = enclosure_um
        self.num_points = num_points
        self.layout = db.Layout()
        self.layout.dbu = 0.001  # 1 nm
        self.top_cell = self.layout.create_cell("TOP")
        self._pillar_cells: Dict[Tuple[float, bool], db.Cell] = {}

    def _make_circle(self, radius: float) -> List:
        """Generate circle polygon points."""
        points = []
        for i in range(self.num_points):
            angle = 2 * math.pi * i / self.num_points
            points.append(db.DPoint(radius * math.cos(angle),
                                    radius * math.sin(angle)))
        return points

    def _get_pillar_cell(self, body_diameter_um: float,
                         with_cap: bool = True) -> 'db.Cell':
        """Get or create Cu-pillar cell for a given body diameter.

        Uses Table 6.1 to derive passiv opening and TM2 radii from
        the body diameter. Cells are cached by (body_diameter, with_cap).

        Args:
            body_diameter_um: Cu-pillar body diameter from Table 6.1
            with_cap: If True, include SnAgCap 3D visualization layer.
                      If False, only CuPillar body (for wafer-level testing).
        """
        key = (body_diameter_um, with_cap)
        if key in self._pillar_cells:
            return self._pillar_cells[key]

        option = CUPILLAR_TABLE_6_1.get(body_diameter_um)
        if option is None:
            raise ValueError(
                f"Unknown Cu-pillar body diameter: {body_diameter_um} um. "
                f"Valid: {sorted(CUPILLAR_TABLE_6_1.keys())} (Table 6.1)")

        passiv_radius = option['passiv_opening'] / 2.0
        tm2_radius = passiv_radius + self.enclosure_um
        body_radius = body_diameter_um / 2.0

        cap_suffix = "" if with_cap else "_nocap"
        opt_val = option['option']
        opt_token = f"opt{opt_val}" if isinstance(opt_val, int) else str(opt_val)
        cell = self.layout.create_cell(
            f"CUPILLAR_{body_diameter_um:.0f}um_{opt_token}{cap_suffix}")

        # Fabrication layers: Passiv uses passiv_radius, rest use tm2_radius
        for layer_name, (layer_num, datatype) in CUPILLAR_FAB_LAYERS.items():
            layer_idx = self.layout.layer(layer_num, datatype)
            r = passiv_radius if layer_name == 'Passiv:pillar' else tm2_radius
            cell.shapes(layer_idx).insert(db.DPolygon(self._make_circle(r)))

        # 3D visualization layers (CuPillar body always, SnAgCap optional)
        for layer_name, (layer_num, datatype) in CUPILLAR_3D_LAYERS.items():
            if not with_cap and 'SnAgCap' in layer_name:
                continue
            layer_idx = self.layout.layer(layer_num, datatype)
            cell.shapes(layer_idx).insert(
                db.DPolygon(self._make_circle(body_radius)))

        self._pillar_cells[key] = cell
        return cell

    def add_device_bumps(self, ref: str, bumps: List[BumpLocation],
                         body_diameter_um: float,
                         with_cap: bool = True) -> int:
        """Place Cu-pillar instances for a single device.

        Creates cell hierarchy: TOP > CUPILLARS_{ref} > CUPILLAR_{diam}um
        """
        pillar_cell = self._get_pillar_cell(body_diameter_um, with_cap)
        group_cell = self.layout.create_cell(f"CUPILLARS_{ref}")
        self.top_cell.insert(db.DCellInstArray(group_cell, db.DTrans()))

        count = 0
        for b in bumps:
            trans = db.DTrans(db.DVector(b.global_x_um, b.global_y_um))
            group_cell.insert(db.DCellInstArray(pillar_cell, trans))
            count += 1
        return count

    def add_bumps(self, bumps: List[BumpLocation],
                  body_diameter_um: float = DEFAULT_BODY_DIAMETER,
                  with_cap: bool = True) -> int:
        """Place Cu-pillar instances at bump locations (single diameter).

        Groups by device_ref for cell hierarchy.
        For mixed-option assemblies, use add_device_bumps() per device.
        """
        by_device: Dict[str, List[BumpLocation]] = {}
        for b in bumps:
            by_device.setdefault(b.device_ref, []).append(b)

        total = 0
        for ref, device_bumps in sorted(by_device.items()):
            total += self.add_device_bumps(ref, device_bumps,
                                           body_diameter_um, with_cap)
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
    """Parse chiplet YAML for component positions and connection stacks.

    Returns:
        Dict mapping component id to {
            'x': float, 'y': float, 'rotation': float,
            'connection': str or None,
            'body_diameter': float or None
        }
        Skips components with type 'interposer'.
    """
    try:
        import yaml
    except ImportError:
        return _parse_chiplet_simple(path)

    with open(path, 'r') as f:
        data = yaml.safe_load(f)

    return _extract_positions(data)


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
    """Extract positions and connection stack info from parsed chiplet data."""
    connection_stacks = data.get('connection_stacks', {})

    positions = {}
    for comp in data.get('components', []):
        if comp.get('type') == 'interposer':
            continue
        cid = comp.get('id', '')
        pos = comp.get('position', {})
        rot = comp.get('rotation', {})

        # Look up body diameter and cap presence from connection stack
        conn_id = comp.get('connection')
        body_diameter = None
        with_cap = True  # default: with SnAg cap (assembly config)
        if conn_id and conn_id in connection_stacks:
            stack = connection_stacks[conn_id]
            layers = stack.get('layers', [])
            if layers:
                body_diameter = float(layers[0].get('diameter', 0))
            layer_names = [l.get('name', '') for l in layers]
            with_cap = any('SnAg' in n for n in layer_names)

        positions[cid] = {
            'x': float(pos.get('x', 0.0)),
            'y': float(pos.get('y', 0.0)),
            'rotation': float(rot.get('z', 0.0)),
            'connection': conn_id,
            'body_diameter': body_diameter,
            'with_cap': with_cap,
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
# Auto-resolver -- conservative DRC-aware bump shifting
# ---------------------------------------------------------------------------

def auto_resolve_collisions(
    bumps: List[BumpLocation],
    params: 'DrcParams',
    diameter_um: float,
    max_displacement_um: float = 10.0,
    max_iters: int = 20,
) -> Tuple[List[BumpLocation], dict]:
    """Conservatively shift bump positions to resolve DRC violations.

    Strategy: in each iteration find the single worst-violating pair (smallest
    center-to-center gap below required) and push the two bumps apart along
    their connecting line, splitting the deficit symmetrically. Each bump has a
    per-pillar displacement budget (max_displacement_um from origin); when the
    budget is exhausted the bump stops contributing. Iterates until convergence
    or max_iters.

    Required separation = max(Padc.e min pitch, Padc.a diameter + Padc.b min
    spacing). Coincident points (overlapping pads) cannot be split and produce
    a warning -- those must be fixed in the footprint.

    Args:
        bumps: 1:1 bump locations (caller's list is not modified).
        params: DRC parameters (uses min_spacing_um, min_pitch_um).
        diameter_um: passiv opening diameter (Padc.a).
        max_displacement_um: per-bump displacement cap from origin.
        max_iters: iteration cap.

    Returns:
        (resolved_bumps, report) -- report has 'converged', 'moved_count',
        'max_delta_um', 'iterations_used', 'movements' (per-bump deltas) and
        'remaining_violations'.
    """
    work = [BumpLocation(b.device_ref, b.pin_name, b.global_x_um, b.global_y_um)
            for b in bumps]
    n = len(work)
    origins = [(b.global_x_um, b.global_y_um) for b in work]

    required = max(params.min_pitch_um,
                   diameter_um + params.min_spacing_um)

    converged = False
    coincident_skipped = False
    iters_used = 0
    for it in range(max_iters):
        iters_used = it + 1
        worst = None
        worst_gap = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                dx = work[j].global_x_um - work[i].global_x_um
                dy = work[j].global_y_um - work[i].global_y_um
                d = math.sqrt(dx * dx + dy * dy)
                gap = required - d
                if gap > 0.01 and gap > worst_gap:
                    worst_gap = gap
                    worst = (i, j, d, dx, dy)
        if worst is None:
            converged = True
            break

        i, j, d, dx, dy = worst
        if d < 1e-6:
            print(f"Warning: bumps {work[i].device_ref}:{work[i].pin_name} and "
                  f"{work[j].device_ref}:{work[j].pin_name} are coincident "
                  f"(overlap in source footprint); auto-resolve cannot split "
                  f"them. Fix the footprint.", file=sys.stderr)
            coincident_skipped = True
            break

        ux, uy = dx / d, dy / d
        push_each = worst_gap / 2.0

        for idx, sign in ((i, -1), (j, +1)):
            ox, oy = origins[idx]
            cur_disp = math.hypot(work[idx].global_x_um - ox,
                                   work[idx].global_y_um - oy)
            budget = max_displacement_um - cur_disp
            if budget <= 0.0:
                continue
            actual = min(push_each, budget)
            work[idx].global_x_um += sign * ux * actual
            work[idx].global_y_um += sign * uy * actual

    movements = []
    for orig, b in zip(origins, work):
        ddx = b.global_x_um - orig[0]
        ddy = b.global_y_um - orig[1]
        dd = math.hypot(ddx, ddy)
        if dd > 0.01:
            movements.append({
                'device_ref': b.device_ref,
                'pin_name': b.pin_name,
                'orig_x_um': round(orig[0], 4),
                'orig_y_um': round(orig[1], 4),
                'new_x_um': round(b.global_x_um, 4),
                'new_y_um': round(b.global_y_um, 4),
                'delta_x_um': round(ddx, 4),
                'delta_y_um': round(ddy, 4),
                'delta_total_um': round(dd, 4),
            })

    remaining = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = work[j].global_x_um - work[i].global_x_um
            dy = work[j].global_y_um - work[i].global_y_um
            if math.sqrt(dx * dx + dy * dy) < required - 0.01:
                remaining += 1

    report = {
        'iterations_used': iters_used,
        'converged': converged and not coincident_skipped,
        'moved_count': len(movements),
        'max_delta_um': max((m['delta_total_um'] for m in movements),
                            default=0.0),
        'max_displacement_budget_um': max_displacement_um,
        'required_separation_um': round(required, 3),
        'movements': movements,
        'remaining_violations': remaining,
        'coincident_skipped': coincident_skipped,
    }
    return work, report


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
    ar_group = parser.add_mutually_exclusive_group()
    ar_group.add_argument(
        '--auto-resolve', dest='auto_resolve', action='store_true',
        default=True,
        help='Conservatively shift cu-pillars to resolve DRC collisions (default ON)',
    )
    ar_group.add_argument(
        '--no-auto-resolve', dest='auto_resolve', action='store_false',
        help='Disable auto-resolve; fail on any DRC violation',
    )
    parser.add_argument(
        '--max-displacement', type=float, default=10.0, metavar='UM',
        help='Per-pillar displacement budget for auto-resolve (default 10 um). '
             'Conservative -- chiplet designers should leave room between pads.',
    )
    parser.add_argument(
        '--auto-resolve-best-effort', action='store_true',
        help='Generate GDS even if auto-resolve does not converge (with warnings)',
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


def _resolve_body_diameter(positions: Dict[str, dict], ref: str,
                           cli_diameter: Optional[float]) -> float:
    """Determine body diameter for a device.

    Priority: CLI override (reverse-lookup) > chiplet connection_stack > default.
    """
    if cli_diameter is not None:
        # CLI --diameter is passiv opening; reverse-lookup to body diameter
        for body_diam, opt in CUPILLAR_TABLE_6_1.items():
            if abs(opt['passiv_opening'] - cli_diameter) < 0.01:
                return float(body_diam)
        # No match -- warn and use default
        print(f"Warning: --diameter {cli_diameter} does not match any Table 6.1 "
              f"passiv opening, using default body diameter {DEFAULT_BODY_DIAMETER}",
              file=sys.stderr)
        return float(DEFAULT_BODY_DIAMETER)

    pos_info = positions.get(ref, {})
    body_diam = pos_info.get('body_diameter')
    if body_diam and body_diam in CUPILLAR_TABLE_6_1:
        return body_diam
    return float(DEFAULT_BODY_DIAMETER)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Load pin lists
    pin_lists = load_pin_lists(args.pins)

    # Load positions (includes connection stack info when using --chiplet)
    if args.chiplet:
        positions = load_chiplet_positions(args.chiplet)
    else:
        positions = parse_positions(args.position)
        parse_rotations(args.rotation, positions)

    enclosure = args.enclosure if args.enclosure is not None else 7.5

    # Compute and validate per device with per-device DRC params
    passed_devices: Dict[str, List[BumpLocation]] = {}
    device_body_diameters: Dict[str, float] = {}
    device_with_cap: Dict[str, bool] = {}
    failed_devices: List[str] = []
    skipped_devices: List[str] = []
    all_reports: Dict[str, ValidationReport] = {}

    for ref, pin_list in pin_lists.items():
        pos_info = positions.get(ref, {})

        # In chiplet mode, skip devices without a connection field
        if args.chiplet and not pos_info.get('connection'):
            print(f"  {ref}: no connection stack, skipping (wirebond/other)")
            skipped_devices.append(ref)
            continue

        body_diam = _resolve_body_diameter(positions, ref, args.diameter)
        device_body_diameters[ref] = body_diam
        device_with_cap[ref] = pos_info.get('with_cap', True)
        option = CUPILLAR_TABLE_6_1.get(body_diam, {})
        if option:
            opt_val = option.get('option', '?')
            opt_label = f"opt{opt_val}" if isinstance(opt_val, int) else str(opt_val)
        else:
            opt_label = "default"

        # Per-device DRC params from Table 6.1
        params = DrcParams.from_body_diameter(body_diam)
        passiv_diam = option.get('passiv_opening', params.diameter_um)

        device_bumps = compute_bump_locations({ref: pin_list}, positions)
        if not device_bumps:
            print(f"  {ref}: no bump locations (missing position?), skipping")
            failed_devices.append(ref)
            continue

        ar_report: Optional[dict] = None
        if args.auto_resolve:
            device_bumps, ar_report = auto_resolve_collisions(
                device_bumps, params, passiv_diam,
                max_displacement_um=args.max_displacement,
            )
            if ar_report['moved_count'] > 0:
                print(f"  {ref}: auto-resolve moved "
                      f"{ar_report['moved_count']} cu-pillars "
                      f"(max delta {ar_report['max_delta_um']:.2f} um, "
                      f"{ar_report['iterations_used']} iters, "
                      f"converged={ar_report['converged']})")
            if not ar_report['converged'] and not args.auto_resolve_best_effort:
                msg = (f'{ar_report["remaining_violations"]} unresolved '
                       f'collisions after {ar_report["iterations_used"]} '
                       f'iterations (budget {args.max_displacement} um/pillar)')
                if ar_report['coincident_skipped']:
                    msg += '; coincident pads in source footprint'
                print(f"  {ref}: auto-resolve FAILED -- {msg}",
                      file=sys.stderr)
                # fall through: let DRC validator emit the formal errors

        validator = DrcValidator(params)
        report = validator.validate(device_bumps, passiv_diam, enclosure)
        if ar_report is not None:
            report.params = {**report.params, 'auto_resolve': ar_report}
        all_reports[ref] = report

        if report.passed:
            passed_devices[ref] = device_bumps
            print(f"  {ref}: {len(device_bumps)} bumps, {opt_label} "
                  f"(body={body_diam}um), DRC PASSED")
        else:
            failed_devices.append(ref)
            summary = report.summary
            print(f"  {ref}: {len(device_bumps)} bumps, {opt_label} "
                  f"(body={body_diam}um), DRC FAILED "
                  f"({summary['error']} errors)")
            for r in report.results:
                if r.severity == 'error':
                    print(f"    ERROR  [{r.rule}] {r.message}")

    total_passed = sum(len(b) for b in passed_devices.values())
    total_devices = len(pin_lists) - len(skipped_devices)
    print(f"\nSummary: {len(passed_devices)}/{total_devices} devices passed DRC, "
          f"{total_passed} bumps valid")

    if skipped_devices:
        print(f"Skipped (no connection): {', '.join(skipped_devices)}")
    if failed_devices:
        print(f"Failed devices: {', '.join(failed_devices)}")

    # Write combined report
    if args.report:
        combined = {
            'devices': {ref: rpt.to_dict() for ref, rpt in all_reports.items()},
            'passed_devices': list(passed_devices.keys()),
            'failed_devices': failed_devices,
            'skipped_devices': skipped_devices,
        }
        with open(args.report, 'w') as f:
            json.dump(combined, f, indent=2)
        print(f"Report written to: {args.report}")

    if args.validate_only:
        return 0

    # Generate GDS for devices that passed, with per-device geometry
    if not passed_devices:
        print("\nNo devices passed DRC. GDS generation skipped.",
              file=sys.stderr)
        return 1

    generator = CuPillarGenerator(enclosure_um=enclosure)
    total_count = 0
    for ref, device_bumps in sorted(passed_devices.items()):
        body_diam = device_body_diameters[ref]
        cap = device_with_cap.get(ref, True)
        total_count += generator.add_device_bumps(ref, device_bumps,
                                                  body_diam, cap)
    generator.write(args.output)
    print(f"\nGenerated {total_count} Cu-pillar pads ({len(passed_devices)} devices) "
          f"-> {args.output}")

    if failed_devices:
        print(f"Skipped {len(failed_devices)} device(s) with DRC errors: "
              f"{', '.join(failed_devices)}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
