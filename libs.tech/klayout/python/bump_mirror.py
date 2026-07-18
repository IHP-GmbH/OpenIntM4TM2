#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
bump_mirror.py -- Standalone Cu-Pillar Generator with DRC Pre-Validation

Generates Cu-pillar pad GDS from pin_list JSON files, with DRC validation
against IHP SG13G2 interposer design rules (Table 6.1) before GDS output.

Can be used standalone or to pre-generate a GDS that hyp_to_gds.py merges
via --cupillar-gds.
"""

import argparse
import importlib.util
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import klayout.db as db
except ImportError:
    db = None


class CliInputError(ValueError):
    """Malformed user input (bad REF=path / coordinate / diameter).

    Raised by the importable parsers so a caller (test harness, the wider
    suite, an embedded interpreter) is not killed by a bare sys.exit; main()
    catches it and returns a non-zero exit code.
    """

# ---------------------------------------------------------------------------
# Constants -- mirrored from hyp_to_gds.py GDSGenerator
# ---------------------------------------------------------------------------

CUPILLAR_FAB_LAYERS = {
    'TopMetal2':      (134, 0),
    'Passiv:pillar':  (9, 35),
    'dfpad:pillar':   (41, 35),
    'Recog:pillar':   (99, 35),
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

# In-code fallback DRC values: Option 2 (40 um opening), matching the
# DEFAULT_BODY_DIAMETER (49 um) used for cupillar generation; used only when no
# tech JSON is found. The shipped interposer_tech_default.json carries Option 1
# (35 um opening) for the fab DRC deck, so DrcParams.load() returns Option 1
# when that file is present. The two defaults are intentionally distinct: fab
# deck default (Option 1) vs assembly cupillar default (Option 2).
DEFAULT_DRC_PARAMS = {
    'Padc_a': 40.0,
    'Padc_b': 40.0,
    'Padc_c': 7.5,
    'Padc_d': 30.0,
    'Padc_e': 80.0,
}


# Interconnect PDK 3D body generator (sibling repo). bump_mirror draws the fab
# pad openings the interposer fabricates; the 3D bodies (e.g. CuPillar 500/35,
# or a vendor's layers) are owned by the interconnect PDK. There is no built-in
# fallback: emitting attachment pads without their bodies would produce a
# complete GDS that looks fabricable while silently missing the interconnect,
# so cell generation fails loud when the PDK is absent.
_bump3d_cache = None


def _get_bump3d():
    """Import the interconnect PDK's bump3d_generator.py, or None if absent.

    Lives at <interconnect PDK>/libs.tech/klayout/python/ (IHP layout, same
    as this file in the interposer). $INTERCONNECT_PDK_ROOT first, then the
    sibling-checkout walk; a set-but-invalid env falls through to the walk.
    The walk accepts the canonical ecosystem name first, then the upstream
    repository name, so default GitHub clones resolve too.

    Only the filesystem discovery is guarded: if a bump3d_generator.py is found
    but fails to import (missing dep, syntax error), that error propagates with
    its real traceback instead of being masked as 'interconnect_pdk not found'.
    A genuine absence caches a negative result; a failed import is NOT cached,
    so it can be retried once the environment is fixed.
    """
    global _bump3d_cache
    if _bump3d_cache is not None:
        return _bump3d_cache or None

    py_subdir = ("libs.tech", "klayout", "python")
    cand_dir = None
    try:
        candidates = []
        env = os.environ.get("INTERCONNECT_PDK_ROOT")
        if env:
            candidates.append(Path(env).joinpath(*py_subdir))
        here = Path(__file__).resolve()
        for base in here.parents:
            for name in ("interconnect_pdk", "IHP-Interconnect-IntM4TM2"):
                candidates.append((base / name).joinpath(*py_subdir))
        for cand in candidates:
            if (cand / "bump3d_generator.py").is_file():
                cand_dir = cand
                break
    except Exception:
        cand_dir = None

    if cand_dir is None:
        _bump3d_cache = False   # genuinely absent
        return None

    # Add the dir so the module's own siblings (interconnect_manifest) resolve,
    # then load the exact discovered file so an unrelated bump3d_generator on
    # sys.path cannot shadow it. An import error here is intentionally unguarded.
    if str(cand_dir) not in sys.path:
        sys.path.insert(0, str(cand_dir))
    spec = importlib.util.spec_from_file_location(
        "bump3d_generator", cand_dir / "bump3d_generator.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bump3d_generator"] = mod
    spec.loader.exec_module(mod)
    _bump3d_cache = mod
    return mod


def _ensure_pcell_lib():
    """Register the IntM4TM2 PCell library in-process (lazy, idempotent).

    The fabrication pad geometry is owned by the CuPillarPad PCell in
    intm4tm2_pycell_lib. That library binds to the 'intm4tm2' technology,
    so registration is two steps: register the technology from the repo
    .lyt (skipped when the session already has it, e.g. a layout session
    with the interposer tech installed), then import the library package,
    whose import side effect registers the pya.Library. All paths resolve
    relative to this file, so a plain checkout works without environment
    setup, both under the plain python interpreter and inside the layout
    tool's batch interpreter.

    Returns immediately when a library named 'IntM4TM2' is already
    registered (an autorun/session may have done it first).

    Raises:
        ImportError: when the library cannot be registered; the message
            names the failing precondition and how to fix it.
    """
    import pya

    if 'IntM4TM2' in pya.Library.library_names():
        return

    here = Path(__file__).resolve().parent        # libs.tech/klayout/python
    klayout_root = here.parent                    # libs.tech/klayout

    if 'intm4tm2' not in pya.Technology.technology_names():
        lyt_path = klayout_root / 'tech' / 'intm4tm2.lyt'
        if not lyt_path.is_file():
            raise ImportError(
                f"intm4tm2 technology file not found: {lyt_path}. "
                f"bump_mirror.py must live in libs.tech/klayout/python of an "
                f"intact interposer PDK checkout (tech/intm4tm2.lyt is "
                f"resolved relative to it).")
        tech = pya.Technology.create_technology('intm4tm2')
        tech.load(str(lyt_path))

    api_dir = here / 'pycell4klayout-api' / 'source' / 'python'
    for path in (here, api_dir):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    # The library's tech class honors KLAYOUT_LYP_FILE as a layer-table
    # override. A foreign .lyp (any non-interposer flow) would silently
    # retag or drop the fabrication layers, so it must not reach the
    # registration of a library whose layer table is a contract here.
    saved_lyp = os.environ.pop('KLAYOUT_LYP_FILE', None)
    try:
        import intm4tm2_pycell_lib  # noqa: F401  (import registers the library)
    except Exception as exc:
        raise ImportError(
            "cannot import intm4tm2_pycell_lib to register the IntM4TM2 "
            "PCell library. Check that libs.tech/klayout/python (including "
            "the vendored pycell4klayout-api and pypreprocessor) is intact: "
            f"{exc}") from exc
    finally:
        if saved_lyp is not None:
            os.environ['KLAYOUT_LYP_FILE'] = saved_lyp

    if 'IntM4TM2' not in pya.Library.library_names():
        raise ImportError(
            "intm4tm2_pycell_lib imported but the IntM4TM2 library is not "
            "registered; the package import normally registers it as a side "
            "effect. Check intm4tm2_pycell_lib/__init__.py.")


def _um_str(value: float) -> str:
    """Format a micron value in the PCell string-parameter form (e.g. '35u')."""
    return f"{value:g}u"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DrcParams:
    """Cu-pillar DRC parameters.

    Field defaults are the Option 2 (40 um opening) fallback, matching
    DEFAULT_BODY_DIAMETER. ``load()`` overrides these from a tech JSON when one
    is found (the shipped interposer_tech_default.json carries Option 1).
    """
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
            candidate = (script_dir.parent / "tech" / "drc" /
                         "rule_decks" / "interposer_tech_default.json")
            if candidate.exists():
                path = str(candidate)

        if path and Path(path).exists():
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: failed to read tech JSON {path}: {e}; "
                      f"using built-in defaults", file=sys.stderr)
                return cls()
            rules = data.get('rules', {}) if isinstance(data, dict) else {}
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

        Note:
            This coordinate-only pre-validator covers Padc.a/b/c/e/f. Padc.d
            (pad-to-EdgeSeal spacing, min_edgeseal_um) is intentionally NOT
            checked here: it needs EdgeSeal geometry that the bump list does
            not carry. Padc.d is enforced by the full intm4tm2.drc deck. The
            field is retained so a loaded tech JSON round-trips its value.
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
    """Place Cu-pillar pad instances and their 3D bodies.

    This is a thin placer: the fabrication circles (TopMetal2, the pillar
    purposes of Passiv/dfpad/Recog) are owned by the CuPillarPad PCell in
    the IntM4TM2 library -- the single source of Cu-pillar pad geometry.
    The generator instantiates that PCell per body diameter, flattens the
    variant to static geometry (no library/PCell metadata survives into
    the output), and adds the 3D interconnect bodies on top.

    The PCell draws its circles at a fixed 256-point discretization; the
    num_points parameter only affects the 3D bodies.

    Supports per-device body diameters via Table 6.1 lookup.
    Caches pillar cells by body diameter so mixed-option assemblies
    use the correct geometry for each device.
    """

    def __init__(self, enclosure_um: float = 7.5, num_points: int = 256,
                 bodies: Optional[List[Tuple[str, int, int]]] = None,
                 passiv_opening_um: Optional[float] = None):
        """
        Args:
            enclosure_um: TM2 enclosure around the passiv opening.
            num_points:   circle discretization of the 3D bodies only; the
                          fabrication circles are PCell-defined at a fixed
                          256 points.
            bodies:       3D body layers as (name, gds_layer, gds_datatype)
                          tuples, normally interconnect_manifest.layers_3d()
                          for the active method. None = the interconnect
                          PDK generator's default (IHP cu-pillar pair).
            passiv_opening_um: fab pad opening for body diameters outside
                          Table 6.1, normally the method's manifest
                          fab_params (e.g. a vendor microbump). Table 6.1
                          still wins for the diameters it knows.
        """
        if db is None:
            raise ImportError("klayout package required for GDS generation. "
                              "Install with: pip install klayout")
        self.enclosure_um = enclosure_um
        self.num_points = num_points
        self.bodies = bodies
        self.passiv_opening_um = passiv_opening_um
        self.layout = db.Layout()
        self.layout.dbu = 0.001  # 1 nm
        self.top_cell = self.layout.create_cell("TOP")
        self._pillar_cells: Dict[Tuple[float, bool], db.Cell] = {}

    def _get_pillar_cell(self, body_diameter_um: float,
                         with_cap: bool = True) -> 'db.Cell':
        """Get or create Cu-pillar cell for a given body diameter.

        Uses Table 6.1 to derive the passiv opening from the body
        diameter; the fabrication circles themselves come from the
        CuPillarPad PCell. Cells are cached by (body_diameter, with_cap).

        Args:
            body_diameter_um: Cu-pillar body diameter from Table 6.1
            with_cap: If True, include SnAgCap 3D visualization layer.
                      If False, only CuPillar body (for wafer-level testing).
        """
        key = (body_diameter_um, with_cap)
        if key in self._pillar_cells:
            return self._pillar_cells[key]

        option = CUPILLAR_TABLE_6_1.get(body_diameter_um)
        if option is None and self.passiv_opening_um is not None:
            # Method-supplied fab geometry (interconnect manifest
            # fab_params): diameters outside the IHP Table 6.1 carry
            # their own passivation opening.
            option = {'option': 'manifest',
                      'passiv_opening': self.passiv_opening_um}
        if option is None:
            raise ValueError(
                f"Unknown Cu-pillar body diameter: {body_diameter_um} um. "
                f"Valid: {sorted(CUPILLAR_TABLE_6_1.keys())} (Table 6.1), "
                f"or construct the generator with passiv_opening_um for "
                f"manifest-defined methods.")

        body_radius = body_diameter_um / 2.0

        cap_suffix = "" if with_cap else "_nocap"
        opt_val = option['option']
        opt_token = f"opt{opt_val}" if isinstance(opt_val, int) else str(opt_val)
        cell = self.layout.create_cell(
            f"CUPILLAR_{body_diameter_um:.0f}um_{opt_token}{cap_suffix}")

        # Fabrication layers (CUPILLAR_FAB_LAYERS) come from the CuPillarPad
        # PCell -- the single source of Cu-pillar pad geometry. The variant
        # is flattened, its shapes copied into the static cell, and the
        # leftover proxy pruned, so the output layout carries plain static
        # polygons with no library or PCell metadata.
        _ensure_pcell_lib()
        self.layout.technology_name = 'intm4tm2'
        variant = self.layout.create_cell(
            'CuPillarPad', 'IntM4TM2',
            {'diameter': _um_str(option['passiv_opening']),
             'passEncl': _um_str(self.enclosure_um),
             'addFillerEx': 'nil'})
        if variant is None:
            raise RuntimeError(
                "IntM4TM2/CuPillarPad PCell variant could not be created "
                "(layout.create_cell returned None; library or technology "
                "did not resolve)")
        variant.flatten(-1, True)
        cell.copy_shapes(variant)
        self.layout.prune_cell(variant.cell_index(), -1)

        # Contract check: the PCell must have produced exactly the four
        # fabrication layers. A poisoned layer table (e.g. a stale
        # KLAYOUT_LYP_FILE honored by an earlier registration of the
        # library) would otherwise retag or drop pad geometry while the
        # run still exits cleanly.
        drawn = set()
        for idx in self.layout.layer_indexes():
            if not cell.bbox_per_layer(idx).empty():
                info = self.layout.get_info(idx)
                drawn.add((info.layer, info.datatype))
        expected = set(CUPILLAR_FAB_LAYERS.values())
        if drawn != expected:
            raise RuntimeError(
                f"CuPillarPad PCell produced layers {sorted(drawn)} instead "
                f"of the fabrication set {sorted(expected)}. The IntM4TM2 "
                f"library layer table is wrong -- check for a foreign "
                f"KLAYOUT_LYP_FILE in the environment of whatever process "
                f"first registered the library.")

        # 3D body layers (pillar body always, cap optional). Owned by the
        # interconnect PDK; no silent fallback -- pads without their bodies
        # would be an incomplete attachment that no downstream DRC can flag.
        bump3d = _get_bump3d()
        if bump3d is None:
            raise RuntimeError(
                "interconnect_pdk not found: cannot draw the 3D interconnect "
                "bodies. Set INTERCONNECT_PDK_ROOT or clone the interconnect "
                "PDK as a sibling checkout named 'interconnect_pdk' or "
                "'IHP-Interconnect-IntM4TM2' "
                "(libs.tech/klayout/python/bump3d_generator.py).")
        bump3d.add_3d_bodies(self.layout, cell, body_radius,
                             bodies=self.bodies, with_cap=with_cap,
                             num_points=self.num_points)

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
        """Write GDS file.

        Context info is suppressed so no library/PCell metadata reaches the
        output; the pillar cells hold flattened static geometry only.
        """
        save_opts = db.SaveLayoutOptions()
        save_opts.set_format_from_filename(output_path)
        save_opts.write_context_info = False
        self.layout.write(output_path, save_opts)


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _import_pinlist():
    """Locate the gds_to_kicad sibling and return its PinList class.

    Resolution: $GDS_TO_KICAD_ROOT first, then an upward walk from this file
    for a sibling checkout named gds_to_kicad/ (canonical) or gds-to-kicad/
    (upstream repository name) that actually contains pin_list.py -- ecosystem
    discovery convention, no fixed-depth path arithmetic. The directory is
    added to sys.path (idempotently) so pin_list's own imports resolve, and
    pin_list.py is loaded from that exact file so an unrelated pin_list earlier
    on sys.path cannot shadow it.
    """
    if "pin_list" in sys.modules:
        return sys.modules["pin_list"].PinList
    candidates = []
    env = os.environ.get("GDS_TO_KICAD_ROOT")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    candidates.extend(base / name for base in here.parents
                      for name in ("gds_to_kicad", "gds-to-kicad"))
    for cand in candidates:
        pin_list_py = cand / "pin_list.py"
        if pin_list_py.is_file():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            spec = importlib.util.spec_from_file_location("pin_list", pin_list_py)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["pin_list"] = mod
            spec.loader.exec_module(mod)
            return mod.PinList
    raise CliInputError(
        "gds_to_kicad not found: cannot import PinList. Set GDS_TO_KICAD_ROOT "
        "or clone gds_to_kicad as a sibling checkout.")


def load_pin_lists(pin_args: List[str]) -> Dict[str, 'PinList']:
    """Parse REF=path pin list arguments.

    Args:
        pin_args: List of "REF=path" strings

    Returns:
        Dict mapping device ref to loaded PinList

    Raises:
        CliInputError on a malformed REF=path token or a missing pin file.
    """
    PinList = _import_pinlist()

    result = {}
    for item in pin_args:
        if '=' not in item:
            raise CliInputError(
                f"Invalid --pins format: '{item}'. Use REF=FILE.")
        ref, path = item.split('=', 1)
        ref = ref.strip()
        path = path.strip()
        if not Path(path).exists():
            raise CliInputError(f"Pin list not found: {path}")
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


def _num(value, default=0.0):
    """Coerce a YAML scalar to float, tolerating None and non-numeric strings."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_positions(data: dict) -> Dict[str, dict]:
    """Extract positions and connection stack info from parsed chiplet data."""
    connection_stacks = data.get('connection_stacks', {}) or {}

    positions = {}
    for comp in data.get('components', []):
        if comp.get('type') == 'interposer':
            continue
        cid = comp.get('id', '')
        # A present-but-null field yields None (not the {} default, which only
        # applies when the key is absent), and hand-written YAML may use a
        # scalar rotation (`rotation: 90`); coerce both shapes rather than
        # assuming a dict and crashing with AttributeError.
        pos = comp.get('position') or {}
        if not isinstance(pos, dict):
            pos = {}
        rot = comp.get('rotation')
        if isinstance(rot, dict):
            rotation = _num(rot.get('z', 0.0))
        else:
            rotation = _num(rot)   # scalar rotation, or None -> 0.0

        # Look up body diameter and cap presence from connection stack
        conn_id = comp.get('connection')
        body_diameter = None
        with_cap = True  # default: with SnAg cap (assembly config)
        if conn_id and conn_id in connection_stacks:
            stack = connection_stacks[conn_id]
            layers = stack.get('layers', []) if isinstance(stack, dict) else []
            if layers:
                d = _num(layers[0].get('diameter', 0), default=None)
                body_diameter = d if d else None
            layer_names = [l.get('name', '') for l in layers]
            with_cap = any('SnAg' in n for n in layer_names)

        positions[cid] = {
            'x': _num(pos.get('x', 0.0)),
            'y': _num(pos.get('y', 0.0)),
            'rotation': rotation,
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
                # A coincident pair (d ~ 0) cannot be split, so it is excluded
                # from the worst-pair search by DISTANCE (not a permanent index
                # set): every other violation is still resolved, and if a later
                # move separates the pair it becomes movable and is resolved in a
                # subsequent iteration.
                if gap > 0.01 and d >= 1e-6 and gap > worst_gap:
                    worst_gap = gap
                    worst = (i, j, d, dx, dy)
        if worst is None:
            converged = True
            break

        i, j, d, dx, dy = worst
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

    # Report pairs STILL coincident after resolution (overlapping source pads
    # that no move separated); these block convergence and need a footprint fix.
    for i in range(n):
        for j in range(i + 1, n):
            dx = work[j].global_x_um - work[i].global_x_um
            dy = work[j].global_y_um - work[i].global_y_um
            d = math.sqrt(dx * dx + dy * dy)
            if d < 1e-6 and required - d > 0.01:
                coincident_skipped = True
                print(f"Warning: bumps {work[i].device_ref}:{work[i].pin_name} "
                      f"and {work[j].device_ref}:{work[j].pin_name} are "
                      f"coincident (overlap in source footprint); auto-resolve "
                      f"cannot split them. Fix the footprint.", file=sys.stderr)

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

  # With explicit rotation:
  %(prog)s --pins U1=pins.json --position U1=100,200 --rotation U1=90 \\
           -o out.gds
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
    """Parse --position REF=X,Y arguments.

    Raises:
        CliInputError on a malformed REF=X,Y token or non-numeric coordinates.
    """
    positions = {}
    for item in pos_args:
        if '=' not in item:
            raise CliInputError(
                f"Invalid --position format: '{item}'. Use REF=X,Y.")
        ref, coords = item.split('=', 1)
        parts = coords.split(',')
        if len(parts) != 2:
            raise CliInputError(
                f"Invalid coordinates for {ref}: '{coords}'. Use X,Y.")
        try:
            x, y = float(parts[0]), float(parts[1])
        except ValueError:
            raise CliInputError(
                f"Non-numeric coordinates for {ref}: '{coords}'. Use X,Y.")
        positions[ref.strip()] = {'x': x, 'y': y, 'rotation': 0.0}
    return positions


def parse_rotations(rot_args: Optional[List[str]],
                    positions: Dict[str, dict]):
    """Apply --rotation REF=DEG arguments to positions dict (in-place)."""
    if not rot_args:
        return
    for item in rot_args:
        if '=' not in item:
            raise CliInputError(
                f"Invalid --rotation format: '{item}'. Use REF=DEG.")
        ref, deg = item.split('=', 1)
        ref = ref.strip()
        try:
            angle = float(deg)
        except ValueError:
            raise CliInputError(
                f"Non-numeric rotation for {ref}: '{deg}'. Use REF=DEG.")
        if ref in positions:
            positions[ref]['rotation'] = angle
        else:
            print(f"Warning: Rotation for unknown device {ref}, ignoring",
                  file=sys.stderr)


def _resolve_body_diameter(positions: Dict[str, dict], ref: str,
                           cli_diameter: Optional[float]) -> float:
    """Determine body diameter for a device.

    Priority: CLI override (reverse-lookup) > chiplet connection_stack > default.

    Raises:
        CliInputError if --diameter does not map to any Table 6.1 passiv
        opening (silently substituting the default would generate and validate
        geometry against a diameter the user did not request).
    """
    if cli_diameter is not None:
        # CLI --diameter is a passiv opening; reverse-lookup to a body diameter.
        for body_diam, opt in CUPILLAR_TABLE_6_1.items():
            if abs(opt['passiv_opening'] - cli_diameter) < 0.01:
                return float(body_diam)
        valid = sorted({opt['passiv_opening']
                        for opt in CUPILLAR_TABLE_6_1.values()})
        raise CliInputError(
            f"--diameter {cli_diameter} um does not match any Table 6.1 "
            f"passiv opening (valid: {valid}).")

    pos_info = positions.get(ref, {})
    body_diam = pos_info.get('body_diameter')
    if body_diam is not None and body_diam in CUPILLAR_TABLE_6_1:
        return float(body_diam)
    if body_diam is not None:
        print(f"Warning: body diameter {body_diam} for {ref} is not in Table "
              f"6.1; using default {DEFAULT_BODY_DIAMETER}", file=sys.stderr)
    return float(DEFAULT_BODY_DIAMETER)


def _run_main(args) -> int:
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


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run_main(args)
    except CliInputError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
