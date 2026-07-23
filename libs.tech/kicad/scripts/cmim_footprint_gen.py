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
"""Parametric KiCad footprint generator for the cmim MIM capacitor.

The device is the MIM capacitor of the IHP SG13G2 open PDK, IHP IntM4TM2
module (model ``cap_cmim``). This standalone, stdlib-only script mirrors
the plate geometry produced by the KLayout PCell
(``libs.tech/klayout/python/intm4tm2_pycell_lib/ihp/cmim_code.py``) into a
KiCad v9 footprint (``.kicad_mod``).

The tech CONSTANTS (Mim_c, Mim_d, TV1_a, TV1_d, grid, epsilon1 and the
area/perimeter capacitance specs) are read from the same
``intm4tm2_tech.json`` the PCell uses, so there is no value drift; the
geometry model is deliberately re-implemented here (the PCell itself is
never imported, to keep this script free of KLayout/CNI dependencies).

Plate geometry reproduced from the PCell (all micrometres, plate w x l):

* MIM dielectric plate : box (0, 0) - (w, l)                 [internal, not drawn]
* Metal5   = MINUS plate: box (-Mim_c, -Mim_c) - (w+Mim_c, l+Mim_c)  [largest extent]
* TopMetal1 = PLUS plate: box built from the via array (generateVias)

KiCad layer map (from ``libs.tech/kicad/interposer_template.kicad_pcb``):
TopMetal1 -> In1.Cu (PLUS), Metal5 -> In2.Cu (MINUS). Both pads are
centred on the origin (concentric), courtyard and fab outline follow the
Metal5 outer rectangle.

CLI::

    python cmim_footprint_gen.py --w 7u --l 7u [--m 1] --out <file.kicad_mod>
    python cmim_footprint_gen.py --cap 250f --out <file.kicad_mod>
    python cmim_footprint_gen.py --family --outdir <intm4tm2.pretty dir>

The discrete ``--family`` is keyed by round capacitance (CMIM_10fF ...
CMIM_5pF); the anchor member (CMIM_100fF) matches the cap_cmim symbol.
"""

import argparse
import json
import math
import os
import sys
import uuid

# ---------------------------------------------------------------------------
# Repo-relative default paths (computed from __file__, never hard-coded).
# scripts -> kicad -> libs.tech -> <repo root>
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_SCRIPT_DIR, os.pardir, os.pardir, os.pardir))
DEFAULT_TECH = os.path.join(
    _REPO_ROOT, "libs.tech", "klayout", "python",
    "intm4tm2_pycell_lib", "intm4tm2_tech.json")
DEFAULT_OUTDIR = os.path.join(
    _REPO_ROOT, "libs.tech", "kicad", "footprints", "intm4tm2.pretty")

# KiCad file format tags, mirrored from the template board.
KICAD_VERSION = "20251027"
KICAD_GENERATOR = "pcbnew"
KICAD_GENERATOR_VERSION = "9.99"

# KiCad copper layers for the two plates (template layer map).
PLUS_LAYER = "In1.Cu"    # TopMetal1
MINUS_LAYER = "In2.Cu"   # Metal5

# Deterministic UUID namespace so re-running the generator is reproducible.
_UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "ihp.intm4tm2.cmim.footprint")

# Round-capacitance family. Each entry is (footprint name, nominal-C label,
# w=l in micrometres). The widths are the grid-snapped (0.005 um) square-cap
# solutions for the nominal values; they are used VERBATIM (never re-solved)
# so the names, geometry and stated capacitance never drift. Every actual C
# stays within ~0.1 % of nominal and every width is under Cmax = 8 pF.
# Discrete family keyed by round CAPACITANCE (fF). Each plate width is derived
# at runtime from the nominal via cap_to_width() -- the SAME path --cap uses --
# so the pre-generated family and on-demand footprints never diverge. Every
# actual C stays within ~0.1 % of nominal and every width is under Cmax = 8 pF.
FAMILY_FF = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
# Anchor member (100 fF), kept consistent with the cap_cmim symbol default.
DEFAULT_FF = 100
DEFAULT_NAME = "CMIM_100fF"


# ---------------------------------------------------------------------------
# Tech-constant loading
# ---------------------------------------------------------------------------
_SI_SUFFIX = {
    "y": 1e-24, "z": 1e-21, "a": 1e-18, "f": 1e-15, "p": 1e-12,
    "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9,
    "T": 1e12,
}


def _num(value):
    """Parse a tech value that may be a plain number or an SI-suffixed string.

    e.g. 0.36 -> 0.36, "1.5m" -> 1.5e-3, "40p" -> 40e-12, "6.99u" -> 6.99e-6.
    """
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        raise ValueError("empty tech value")
    suffix = text[-1]
    if suffix in _SI_SUFFIX and not text[-1].isdigit():
        return float(text[:-1]) * _SI_SUFFIX[suffix]
    return float(text)


def load_tech(tech_path):
    """Load the geometry and capacitance constants from intm4tm2_tech.json."""
    with open(tech_path, "r") as handle:
        data = json.load(handle)
    params = data["techParams"]
    tech = {
        "Mim_c": _num(params["Mim_c"]),
        "Mim_d": _num(params["Mim_d"]),
        "TV1_a": _num(params["TV1_a"]),
        "TV1_d": _num(params["TV1_d"]),
        "grid": _num(params["grid"]),
        "epsilon1": _num(params["epsilon1"]),
        # Capacitance specs (display only), matching CbCapCalc unit handling:
        #   caspec [F/um^2] = Numeric(cmim_caspec) * 1e-12
        #   cpspec [F/um]   = Numeric(cmim_cpspec) * 1e-6
        "caspec_F_per_um2": _num(params["cmim_caspec"]) * 1e-12,
        "cpspec_F_per_um": _num(params["cmim_cpspec"]) * 1e-6,
        "cmax_F": _num(params["cmim_maxC"]),
        "minLW_um": _num(params["cmim_minLW"]) * 1e6,
        "model": params.get("cmim_model", "cap_cmim"),
    }
    return tech


# ---------------------------------------------------------------------------
# PCell numeric helpers (re-implemented from utility_functions.py)
# ---------------------------------------------------------------------------
def _fix(value):
    """PCell fix(): int(floor(value)). Equals truncate-toward-zero for the
    strictly-positive arguments used in the cmim geometry."""
    return int(math.floor(value))


def _grid_fix(value, grid, eps):
    """PCell GridFix(): fix(value/grid + eps) * grid, snapping to 'grid'.

    getGridResolution() returns 0.0 in the reference PCell, so the fallback
    'grid' tech parameter is used (as in utility_functions.py)."""
    igrid = 1.0 / grid
    return _fix(value * igrid + eps) * grid


def _grid_round(value, grid):
    """Snap to the NEAREST grid multiple (round-to-nearest).

    Used only for the capacitance solver, where the target is a capacitance
    rather than a dimension: rounding to the closest on-grid plate minimises
    |C - nominal| (floor would systematically undershoot). Both --cap and
    --family route through cap_to_width(), so they can never diverge."""
    return round(round(value / grid) * grid, 6)


# ---------------------------------------------------------------------------
# Geometry model
# ---------------------------------------------------------------------------
def cmim_footprint_boxes(w_um, l_um, tech):
    """Return the MINUS (Metal5) and PLUS (TopMetal1) plate boxes in microns.

    Reproduces cmim_code.py genLayout()/generateVias() exactly. Returns a dict
    with absolute plate boxes (PCell coordinate frame, plate lower-left at
    origin) and their extents (width, height); both plates are concentric with
    the plate centre (w/2, l/2).
    """
    mim_c = tech["Mim_c"]
    mim_d = tech["Mim_d"]
    tv1_a = tech["TV1_a"]
    tv1_d = tech["TV1_d"]
    grid = tech["grid"]
    eps = tech["epsilon1"]

    cont_over = mim_d
    cont_dist = 0.84
    cont_size = tv1_a

    # --- via count and array offsets (generateVias) ---
    xanz = _fix((w_um - cont_over - cont_over + cont_dist) / (cont_size + cont_dist) + eps)
    w1 = xanz * (cont_size + cont_dist) - cont_dist + cont_over + cont_over
    xoffset = _grid_fix((w_um - w1) / 2, grid, eps)

    yanz = _fix((l_um - cont_over - cont_over + cont_dist) / (cont_size + cont_dist) + eps)
    l1 = yanz * (cont_size + cont_dist) - cont_dist + cont_over + cont_over
    yoffset = _grid_fix((l_um - l1) / 2, grid, eps)

    # --- via placement loop: keep the loop-final counter (as the PCell does) ---
    ycont_cnt = cont_over + yoffset
    xcont_cnt = cont_over + xoffset  # value if the y-loop body never runs
    while ycont_cnt + cont_size + cont_over <= l_um + eps:
        xcont_cnt = cont_over + xoffset
        while xcont_cnt + cont_size + cont_over <= w_um + eps:
            xcont_cnt = xcont_cnt + cont_size + cont_dist
        ycont_cnt = ycont_cnt + cont_size + cont_dist

    xcont_end = xcont_cnt + tv1_d - cont_dist
    ycont_end = ycont_cnt + tv1_d - cont_dist

    # --- TopMetal1 (PLUS) box ---
    px1 = mim_d - tv1_d + xoffset
    py1 = mim_d - tv1_d + yoffset
    px2 = xcont_end
    py2 = ycont_end

    # --- Metal5 (MINUS) box ---
    mx1 = -mim_c
    my1 = -mim_c
    mx2 = w_um + mim_c
    my2 = l_um + mim_c

    return {
        "plus_box": (px1, py1, px2, py2),
        "minus_box": (mx1, my1, mx2, my2),
        "plus_extent": (px2 - px1, py2 - py1),
        "minus_extent": (mx2 - mx1, my2 - my1),
        "plus_center": ((px1 + px2) / 2.0, (py1 + py2) / 2.0),
        "minus_center": ((mx1 + mx2) / 2.0, (my1 + my2) / 2.0),
    }


def cmim_capacitance_fF(w_um, l_um, tech, m=1):
    """Display capacitance in fF: C = m*(w*l*1.5 + 2*(w+l)*0.04) at w,l in um.

    Coefficients are derived from the tech caspec/cpspec (1.5 fF/um^2,
    0.04 fF/um) rather than hard-coded, to avoid value drift."""
    area_coef_fF = tech["caspec_F_per_um2"] * 1e15   # 1.5 fF/um^2
    perim_coef_fF = tech["cpspec_F_per_um"] * 1e15   # 0.04 fF/um
    return m * (w_um * l_um * area_coef_fF + 2.0 * (w_um + l_um) * perim_coef_fF)


def cap_bounds_fF(tech):
    """Return (Cmin, Cmax) in fF for the square-cap solver.

    Cmin is the capacitance of a square plate at the device minimum width
    (cmim_minLW ~ 1.14 um -> ~2.13 fF); Cmax is the tech cmim_maxC (8 pF).
    Both are derived from tech, never hard-coded."""
    cmin = cmim_capacitance_fF(tech["minLW_um"], tech["minLW_um"], tech, 1)
    cmax = tech["cmax_F"] * 1e15
    return cmin, cmax


def solve_square_cap(cap_fF, tech):
    """Inverse square-cap model: plate width (um) for a target C (fF), w=l.

    For a square C = a*w^2 + b*w, with a = area spec (1.5 fF/um^2) and
    b = 4 * perimeter spec (4 * 0.04 = 0.16 fF/um) since 2*(w+l) = 4*w.
    Coefficients come from tech (no drift). Positive root:
    w = (-b + sqrt(b^2 + 4*a*C)) / (2*a)."""
    a = tech["caspec_F_per_um2"] * 1e15
    b = 4.0 * (tech["cpspec_F_per_um"] * 1e15)
    return (-b + math.sqrt(b * b + 4.0 * a * cap_fF)) / (2.0 * a)


def cap_to_width(cap_fF, tech):
    """Grid-snapped square-plate width (um) for a target capacitance (fF).

    The single source of the C->width mapping, shared by --cap and --family so
    on-demand and pre-generated footprints are byte-for-byte consistent."""
    return _grid_round(solve_square_cap(cap_fF, tech), tech["grid"])


def _cap_nominal_label(cap_fF):
    """Human capacitance label: 10 -> '10fF', 1000 -> '1pF', 1500 -> '1.5pF'."""
    if cap_fF >= 1000.0:
        val, unit = cap_fF / 1000.0, "pF"
    else:
        val, unit = float(cap_fF), "fF"
    return "{:g}{}".format(val, unit)


def _cap_name(cap_fF):
    """Footprint/symbol name for a nominal capacitance: 10 -> 'CMIM_10fF',
    1500 -> 'CMIM_1p5pF'."""
    return "CMIM_" + _cap_nominal_label(cap_fF).replace(".", "p")


def parse_cap_fF(text):
    """Parse a capacitance target to fF. Accepts f/fF (femto), p/pF (pico) and
    a plain number (interpreted as fF): '250f'->250, '1p'->1000, '10fF'->10,
    '250'->250, '2.13fF'->2.13."""
    token = str(text).strip()
    low = token.lower()
    if low.endswith("ff"):
        return float(token[:-2])
    if low.endswith("pf"):
        return float(token[:-2]) * 1000.0
    if low.endswith("f"):
        return float(token[:-1])
    if low.endswith("p"):
        return float(token[:-1]) * 1000.0
    return float(token)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _mm(um):
    """Micrometres -> millimetres (KiCad unit)."""
    return um / 1000.0


def _fmt_mm(um):
    """Format a micrometre value as millimetres, 6 decimals (nm resolution)."""
    return "{:.6f}".format(_mm(um))


def _fmt_dim(um):
    """Compact grid-exact size token for names: 7.0 -> '7', 1.14 -> '1p14'."""
    text = "{:.3f}".format(um).rstrip("0").rstrip(".")
    return text.replace(".", "p")


def _fmt_um(um):
    """Plain-decimal micrometre value (trailing zeros trimmed, keeps the dot):
    8.11 -> '8.11', 11.495 -> '11.495', 3.6 -> '3.6', 7.0 -> '7'. Used for
    human-readable descr text and the w/l properties (vs. _fmt_dim's names)."""
    text = "{:.3f}".format(um).rstrip("0").rstrip(".")
    return text if text else "0"


def footprint_name(w_um, l_um):
    return "CMIM_{}x{}um".format(_fmt_dim(w_um), _fmt_dim(l_um))


def _uid(name, tag):
    return str(uuid.uuid5(_UUID_NS, "{}/{}".format(name, tag)))


# ---------------------------------------------------------------------------
# KiCad .kicad_mod emission
# ---------------------------------------------------------------------------
def build_footprint(w_um, l_um, tech, m=1, name=None, nominal=None):
    """Return the .kicad_mod S-expression text for a cmim footprint.

    name    : footprint name; defaults to the dimension-keyed CMIM_<w>x<l>um.
    nominal : round-capacitance label (e.g. '100fF') for the C-keyed family;
              when given it is recorded in the descr and a 'Nominal' property
              alongside the actual (recomputed) capacitance and w/l."""
    boxes = cmim_footprint_boxes(w_um, l_um, tech)
    plus_w, plus_h = boxes["plus_extent"]
    minus_w, minus_h = boxes["minus_extent"]
    cap_fF = cmim_capacitance_fF(w_um, l_um, tech, m)
    if name is None:
        name = footprint_name(w_um, l_um)

    # Courtyard / fab outline follow the Metal5 outer rectangle (the true
    # keep-out), centred on the origin.
    half_x = minus_w / 2.0
    half_y = minus_h / 2.0

    # Graphic weights scaled to the (microscopic) plate so the footprint reads
    # cleanly at any zoom instead of being swamped by PCB-scale defaults.
    line_w = round(max(_mm(minus_w) * 0.02, 1e-5), 6)
    font = round(max(_mm(minus_h) * 0.20, 1e-4), 6)
    font_th = round(max(font * 0.15, 1e-5), 6)
    text_gap = _mm(minus_h) * 0.20 + font  # clear of the courtyard

    nom_txt = "nominal {}, ".format(nominal) if nominal else ""
    descr = ("IHP SG13G2 open PDK MIM capacitor (IHP IntM4TM2 module), "
             "model {model}; {nom}W={w}um L={l}um, C~{c:.2f}fF. "
             "PLUS=TopMetal1 (In1.Cu), MINUS=Metal5 (In2.Cu). "
             "Generated by cmim_footprint_gen.py.").format(
                 model=tech["model"], nom=nom_txt,
                 w=_fmt_um(w_um), l=_fmt_um(l_um), c=cap_fF)
    tags = "cmim MIM capacitor IHP SG13G2 IntM4TM2"

    def effects():
        return ("(effects\n\t\t\t(font\n\t\t\t\t(size {s} {s})\n"
                "\t\t\t\t(thickness {t})\n\t\t\t)\n\t\t)").format(
                    s=font, t=font_th)

    lines = []
    lines.append('(footprint "{}"'.format(name))
    lines.append('\t(version {})'.format(KICAD_VERSION))
    lines.append('\t(generator "{}")'.format(KICAD_GENERATOR))
    lines.append('\t(generator_version "{}")'.format(KICAD_GENERATOR_VERSION))
    lines.append('\t(layer "F.Cu")')
    lines.append('\t(descr "{}")'.format(descr))
    lines.append('\t(tags "{}")'.format(tags))

    # --- properties ---
    lines.append('\t(property "Reference" "C**"')
    lines.append('\t\t(at 0 {} 0)'.format("{:.6f}".format(-text_gap)))
    lines.append('\t\t(layer "F.SilkS")')
    lines.append('\t\t(uuid "{}")'.format(_uid(name, "ref")))
    lines.append('\t\t{}'.format(effects()))
    lines.append('\t)')

    lines.append('\t(property "Value" "{}"'.format(name))
    lines.append('\t\t(at 0 {} 0)'.format("{:.6f}".format(text_gap)))
    lines.append('\t\t(layer "F.Fab")')
    lines.append('\t\t(uuid "{}")'.format(_uid(name, "value")))
    lines.append('\t\t{}'.format(effects()))
    lines.append('\t)')

    def hidden_prop(key, value, tag):
        lines.append('\t(property "{}" "{}"'.format(key, value))
        lines.append('\t\t(at 0 0 0)')
        lines.append('\t\t(layer "F.Fab")')
        lines.append('\t\t(hide yes)')
        lines.append('\t\t(uuid "{}")'.format(_uid(name, tag)))
        lines.append('\t\t{}'.format(effects()))
        lines.append('\t)')

    hidden_prop("Description", descr, "descr")
    if nominal:
        hidden_prop("Nominal", nominal, "nominal")
    hidden_prop("w", "{}um".format(_fmt_um(w_um)), "w")
    hidden_prop("l", "{}um".format(_fmt_um(l_um)), "l")
    hidden_prop("m", "{}".format(m), "m")
    hidden_prop("Capacitance", "{:.2f}fF".format(cap_fF), "cap")
    hidden_prop("Model", tech["model"], "model")

    # --- footprint attributes: inner-copper SMD, not in position files ---
    lines.append('\t(attr smd exclude_from_pos_files)')

    # --- pads: PLUS on In1.Cu, MINUS on In2.Cu, concentric on origin ---
    lines.append('\t(pad "1" smd rect')
    lines.append('\t\t(at 0 0)')
    lines.append('\t\t(size {} {})'.format(_fmt_mm(plus_w), _fmt_mm(plus_h)))
    lines.append('\t\t(layers "{}")'.format(PLUS_LAYER))
    lines.append('\t\t(uuid "{}")'.format(_uid(name, "pad1")))
    lines.append('\t)')

    lines.append('\t(pad "2" smd rect')
    lines.append('\t\t(at 0 0)')
    lines.append('\t\t(size {} {})'.format(_fmt_mm(minus_w), _fmt_mm(minus_h)))
    lines.append('\t\t(layers "{}")'.format(MINUS_LAYER))
    lines.append('\t\t(uuid "{}")'.format(_uid(name, "pad2")))
    lines.append('\t)')

    # --- courtyard = Metal5 outer rectangle ---
    def fp_rect(layer, tag):
        lines.append('\t(fp_rect')
        lines.append('\t\t(start {} {})'.format(
            "{:.6f}".format(-_mm(half_x)), "{:.6f}".format(-_mm(half_y))))
        lines.append('\t\t(end {} {})'.format(
            "{:.6f}".format(_mm(half_x)), "{:.6f}".format(_mm(half_y))))
        lines.append('\t\t(stroke')
        lines.append('\t\t\t(width {})'.format(line_w))
        lines.append('\t\t\t(type solid)')
        lines.append('\t\t)')
        lines.append('\t\t(fill no)')
        lines.append('\t\t(layer "{}")'.format(layer))
        lines.append('\t\t(uuid "{}")'.format(_uid(name, tag)))
        lines.append('\t)')

    fp_rect("F.CrtYd", "crtyd")
    fp_rect("F.Fab", "fab")
    fp_rect("F.SilkS", "silk")

    # --- polarity / layer guide -------------------------------------------
    # The two plates are concentric parallel plates on adjacent metals, so the
    # copper cannot be split; instead the device square is annotated as a top
    # PLUS zone and a bottom MINUS zone (a visual aid -- the real copper stays
    # the two full concentric plate pads above). +y is downward in KiCad, so
    # PLUS (TopMetal1, pin 1) sits at the top (negative y) and MINUS (Metal5,
    # pin 2) at the bottom, matching the schematic symbol. Each zone names its
    # polarity and metal so the upper/lower layer is readable on the footprint.
    def effects_f(f):
        th = round(max(f * 0.15, 1e-5), 6)
        return ("(effects\n\t\t\t(font\n\t\t\t\t(size {s} {s})\n"
                "\t\t\t\t(thickness {t})\n\t\t\t)\n\t\t)").format(s=f, t=th)

    def guide_text(text, layer, y_mm, tag, f):
        lines.append('\t(fp_text user "{}"'.format(text))
        lines.append('\t\t(at 0 {} 0)'.format("{:.6f}".format(y_mm)))
        lines.append('\t\t(layer "{}")'.format(layer))
        lines.append('\t\t(uuid "{}")'.format(_uid(name, tag)))
        lines.append('\t\t{}'.format(effects_f(f)))
        lines.append('\t)')

    legend_font = round(max(font * 0.55, 8e-5), 6)
    zone_y = _mm(half_y) * 0.5

    # silk divider splitting the PLUS (top) zone from the MINUS (bottom) zone
    lines.append('\t(fp_line')
    lines.append('\t\t(start {} 0)'.format("{:.6f}".format(-_mm(half_x))))
    lines.append('\t\t(end {} 0)'.format("{:.6f}".format(_mm(half_x))))
    lines.append('\t\t(stroke')
    lines.append('\t\t\t(width {})'.format(line_w))
    lines.append('\t\t\t(type solid)')
    lines.append('\t\t)')
    lines.append('\t\t(layer "F.SilkS")')
    lines.append('\t\t(uuid "{}")'.format(_uid(name, "divider")))
    lines.append('\t)')

    # visible zone labels (silk): polarity + upper/lower metal name
    guide_text("+ TopMetal1", "F.SilkS", -zone_y, "plus_zone", legend_font)
    guide_text("- Metal5", "F.SilkS", zone_y, "minus_zone", legend_font)
    # fab legend: the KiCad copper-layer mapping of each polarity
    guide_text("PLUS=In1.Cu  MINUS=In2.Cu", "F.Fab", 0.0, "legend", legend_font)

    lines.append('\t(embedded_fonts no)')
    lines.append(')')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_size_um(text):
    """Parse a size given in micrometres, accepting 'u'/'um' suffixes."""
    token = str(text).strip().lower()
    if token.endswith("um"):
        token = token[:-2]
    elif token.endswith("u"):
        token = token[:-1]
    return float(token)


def _snapped(um, tech):
    """Snap a plate dimension to the layout grid, as the PCell would."""
    return _grid_fix(um, tech["grid"], tech["epsilon1"])


def write_footprint(w_um, l_um, tech, out_path, m=1, name=None, nominal=None):
    text = build_footprint(w_um, l_um, tech, m, name=name, nominal=nominal)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as handle:
        handle.write(text)
    return out_path


def _summary(w_um, l_um, tech, m=1, name=None, nominal=None):
    boxes = cmim_footprint_boxes(w_um, l_um, tech)
    return {
        "name": name if name is not None else footprint_name(w_um, l_um),
        "nominal": nominal,
        "w_um": w_um,
        "l_um": l_um,
        "m": m,
        "minus_extent": [round(v, 6) for v in boxes["minus_extent"]],
        "plus_extent": [round(v, 6) for v in boxes["plus_extent"]],
        "minus_pad_um": round(boxes["minus_extent"][0], 6),
        "cap_fF": round(cmim_capacitance_fF(w_um, l_um, tech, m), 4),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate KiCad footprints for the cmim MIM capacitor "
                    "(IHP SG13G2 open PDK, IHP IntM4TM2 module).")
    parser.add_argument("--tech", default=DEFAULT_TECH,
                        help="Path to intm4tm2_tech.json (default: repo copy).")
    parser.add_argument("--w", help="Plate width, e.g. 7u / 7um / 7.")
    parser.add_argument("--l", help="Plate length, e.g. 7u / 7um / 7.")
    parser.add_argument("--cap",
                        help="Solve a square (w=l) cap for a target C, then "
                             "emit like --w/--l. Accepts f/fF, p/pF or plain "
                             "fF, e.g. 250f / 1p / 10fF.")
    parser.add_argument("--m", type=int, default=1, help="Multiplier (default 1).")
    parser.add_argument("--out", help="Output .kicad_mod path (single footprint).")
    parser.add_argument("--family", action="store_true",
                        help="Generate the full discrete family.")
    parser.add_argument("--outdir", default=DEFAULT_OUTDIR,
                        help="Output .pretty directory for --family.")
    args = parser.parse_args(argv)

    tech = load_tech(args.tech)

    if args.family:
        written = []
        for nominal_fF in FAMILY_FF:
            name = _cap_name(nominal_fF)
            nominal = _cap_nominal_label(nominal_fF)
            w_um = cap_to_width(nominal_fF, tech)
            l_um = w_um
            out_path = os.path.join(args.outdir, name + ".kicad_mod")
            write_footprint(w_um, l_um, tech, out_path, args.m,
                            name=name, nominal=nominal)
            written.append(out_path)
            summary = _summary(w_um, l_um, tech, args.m,
                               name=name, nominal=nominal)
            print("{name}: nominal={nom} w=l={w}um C={cap}fF "
                  "MINUS={minus}um -> {path}".format(
                      name=name, nom=nominal, w=_fmt_um(w_um),
                      cap=summary["cap_fF"], minus=summary["minus_pad_um"],
                      path=out_path))
        # Cmax sanity check on the largest member.
        cmax_fF = tech["cmax_F"] * 1e15
        top_um = cap_to_width(FAMILY_FF[-1], tech)
        top_fF = cmim_capacitance_fF(top_um, top_um, tech, args.m)
        status = "OK" if top_fF < cmax_fF else "OVER"
        print("Cmax check: {}um -> {:.1f}fF (Cmax {:.1f}fF) [{}]".format(
            _fmt_um(top_um), top_fF, cmax_fF, status))
        return 0

    if args.cap is not None:
        cap_fF = parse_cap_fF(args.cap)
        cmin, cmax = cap_bounds_fF(tech)
        if cap_fF < cmin - 1e-6 or cap_fF > cmax + 1e-6:
            parser.error(
                "--cap {arg} ({c:.4f}fF) is out of range "
                "[{lo:.2f}fF .. {hi:.1f}fF]: a square cap_cmim must stay "
                "between Wmin={wmin}um (Cmin~{lo:.2f}fF) and "
                "Cmax={cmaxp:g}pF.".format(
                    arg=args.cap, c=cap_fF, lo=cmin, hi=cmax,
                    wmin=_fmt_um(tech["minLW_um"]), cmaxp=cmax / 1000.0))
        if args.out is None:
            parser.error("--cap needs --out")
        w = cap_to_width(cap_fF, tech)
        l = w
        name = _cap_name(cap_fF)
        nominal = _cap_nominal_label(cap_fF)
        write_footprint(w, l, tech, args.out, args.m, name=name, nominal=nominal)
        summary = _summary(w, l, tech, args.m, name=name, nominal=nominal)
        print("{name}: target={t:.2f}fF w=l={w}um C={cap}fF "
              "MINUS={minus}um -> {path}".format(
                  name=summary["name"], t=cap_fF, w=_fmt_um(w),
                  cap=summary["cap_fF"], minus=summary["minus_pad_um"],
                  path=args.out))
        return 0

    if args.w is None or args.l is None or args.out is None:
        parser.error("single-footprint mode needs --w, --l and --out "
                     "(or use --cap / --family)")

    w = _snapped(parse_size_um(args.w), tech)
    l = _snapped(parse_size_um(args.l), tech)
    write_footprint(w, l, tech, args.out, args.m)
    summary = _summary(w, l, tech, args.m)
    print("{name}: MINUS={minus}um PLUS={plus} C={cap}fF -> {path}".format(
        name=summary["name"], minus=summary["minus_pad_um"],
        plus=summary["plus_extent"], cap=summary["cap_fF"], path=args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
