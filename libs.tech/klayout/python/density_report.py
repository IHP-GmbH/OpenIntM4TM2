#!/usr/bin/env python3
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
"""One-click density report for the interposer BEOL.

The density deck (density.drc) already computes the global metal-density ratios, the
LBE ratio and the per-plate slit ratio, but it surfaces them only as violation markers
in a report database. A designer tuning fill wants the plain numbers: am I in band, and
by how much. This module measures the same quantities the deck does and formats them as
a compact table, so a menu macro (tech/macros/interposer_density_report.lym) or the CLI
can show them in one step, without parsing a lyrdb.

Measured, per the deck's own definitions:
  - global Metal4/Metal5/TopMetal1/TopMetal2 coverage = (drawn + filler) minus slit,
    over the chip area (prBoundary > EdgeSeal boundary > layout extent), against the
    Mn_j/Mn_k and TM(n)_c/TM(n)_d bands;
  - global LBE coverage against the LBE_i maximum;
  - per-metal slit adequacy: the worst slit ratio over the plates wider than Slt_i_w,
    against the Slt_i floor, per opened plate component. Pads, probe recognition, MIM
    and the designer's keep-outs (x/23, 160/0) are exempt, matching what the deck's
    Slt.c exemption and the slit generator both leave unslit, so a legitimately unslit
    pad is not reported (a wide power plane with no slit still reads as 0% / under).

Thresholds come from interposer_tech_default.json, the same file the decks read, so the
report cannot drift from sign-off. The windowed local-density rules (MnFil.h/k over
800 um windows) are NOT reproduced here; run the density deck for those and for the
authoritative pass/fail.

Usage:
    python density_report.py in.gds [--topcell TOP]
"""

import argparse
import json
import sys
from pathlib import Path

import klayout.db as kdb

HERE = Path(__file__).resolve().parent
REPO_KLAYOUT = HERE.parent
TECH_JSON = REPO_KLAYOUT / "tech" / "drc" / "rule_decks" / "interposer_tech_default.json"

# GDS drawn-metal layer -> (report key, (band-min JSON key, band-max JSON key)).
METAL_BANDS = {
    50:  ("M4",  ("Mn_j", "Mn_k")),
    67:  ("M5",  ("Mn_j", "Mn_k")),
    126: ("TM1", ("TM1_c", "TM1_d")),
    134: ("TM2", ("TM2_c", "TM2_d")),
}
LBE_LAYER = (157, 0)


def _rules(tech_json=TECH_JSON):
    return json.loads(Path(tech_json).read_text())["rules"]


def _open_layout(source):
    """Return (Layout, is_owned). `source` may be a path or an existing Layout.

    Anything that is not a str/Path is assumed to be a Layout-like object already (a
    klayout.db.Layout in tests, or the running app's pya.Layout when a menu macro passes
    the active cellview's layout), so the report can be produced without a disk round-trip.
    """
    if isinstance(source, (str, Path)):
        ly = kdb.Layout()
        ly.read(str(source))
        return ly, True
    return source, False


def _top(ly, topcell):
    if topcell is None:
        return ly.top_cell()
    idx = ly.cell_by_name(topcell) if ly.has_cell(topcell) else None
    return ly.cell(idx) if idx is not None else ly.top_cell()


def _reg(ly, top, layer, dt):
    li = ly.find_layer(layer, dt)
    return kdb.Region() if li is None else kdb.Region(top.begin_shapes_rec(li))


def _chip(ly, top):
    """(chip area um^2, source label). prBoundary (235/0) > EdgeSeal boundary (39/4) > extent."""
    dbu2 = ly.dbu * ly.dbu
    prb = _reg(ly, top, 235, 0)
    if not prb.is_empty():
        return prb.area() * dbu2, "prBoundary (235/0)"
    esb = _reg(ly, top, 39, 4)
    if not esb.is_empty():
        return esb.area() * dbu2, "EdgeSeal boundary (39/4)"
    bb = top.bbox()
    if bb.empty():                                              # empty cell: no extent
        return 0.0, "layout extent"
    return bb.width() * bb.height() * dbu2, "layout extent"


def _band_state(value, lo, hi):
    return "under" if value < lo else "over" if value > hi else "ok"


def measure(source, topcell=None, tech_json=TECH_JSON):
    """Measure the interposer density quantities. Returns a report dict (percents).

    `source` is a GDS/OAS path or an in-memory kdb.Layout (so a KLayout macro can pass
    the active layout without a round-trip through disk). `topcell` selects the cell;
    None uses the layout's top cell.
    """
    rules = _rules(tech_json)
    ly, _owned = _open_layout(source)
    top = _top(ly, topcell)
    dbu2 = ly.dbu * ly.dbu

    chip_area, chip_source = _chip(ly, top)
    report = {
        "topcell": top.name,
        "chip_area_um2": round(chip_area, 3),
        "chip_source": chip_source,
        "metals": {},
        "lbe": None,
        "slits": {},
    }
    if chip_area <= 0:
        return report

    # --- global metal density: (drawn + filler) - slit, over the chip ---
    for layer, (key, (lo_key, hi_key)) in METAL_BANDS.items():
        lo, hi = float(rules[lo_key]) * 100.0, float(rules[hi_key]) * 100.0
        covered = (_reg(ly, top, layer, 0) + _reg(ly, top, layer, 22)
                   - _reg(ly, top, layer, 24)).area() * dbu2
        pct = 100.0 * covered / chip_area
        report["metals"][key] = {"coverage_pct": round(pct, 2), "band": [lo, hi],
                                 "state": _band_state(pct, lo, hi)}

    # --- global LBE density against the LBE_i maximum ---
    lbe_max = float(rules["LBE_i"]) * 100.0
    lbe_pct = 100.0 * _reg(ly, top, *LBE_LAYER).area() * dbu2 / chip_area
    report["lbe"] = {"coverage_pct": round(lbe_pct, 2), "max_pct": lbe_max,
                     "state": "over" if lbe_pct > lbe_max else "ok"}

    # --- per-metal slit adequacy on plates wider than Slt_i_w ---
    # The floor is Slt.i (6% on plates > Slt_i_w), the same plate opening the deck uses.
    # Exempt what the deck's Slt.c exemption and the slit generator both skip: pads and
    # probe recognition (drawn plus their Cu-pillar / solder-bump purposes), MIM, and the
    # designer's own keep-outs (per-metal nofill x/23, NoMetFiller 160/0). Without this a
    # legitimately unslit pad would read "under". The ratio is taken per opened plate
    # COMPONENT (matching the deck's per-net antenna_check), so a dumbbell whose neck the
    # opening severs is graded as two plates, not one averaged plate.
    floor_pct = float(rules["Slt_i"]) * 100.0
    half_w = int(round(0.5 * float(rules["Slt_i_w"]) / ly.dbu))     # dbu
    exempt_common = (_reg(ly, top, 41, 0) + _reg(ly, top, 41, 35) + _reg(ly, top, 41, 36)
                     + _reg(ly, top, 99, 0) + _reg(ly, top, 99, 35) + _reg(ly, top, 99, 36)
                     + _reg(ly, top, 36, 0) + _reg(ly, top, 160, 0))
    for layer, (key, _b) in METAL_BANDS.items():
        metal = _reg(ly, top, layer, 0)
        slit = _reg(ly, top, layer, 24)
        eligible = metal - (exempt_common + _reg(ly, top, layer, 23))
        plates_region = eligible.sized(-half_w).sized(half_w).merged()
        worst = None
        plates = 0
        for poly in plates_region.each():
            plate = kdb.Region(poly)
            plates += 1
            ratio = 100.0 * (slit & plate).area() / plate.area()
            worst = ratio if worst is None else min(worst, ratio)
        if plates == 0:
            state = "none"
        elif worst < floor_pct:
            state = "under"
        else:
            state = "ok"
        report["slits"][key] = {
            "wide_plates": plates,
            "min_ratio_pct": None if worst is None else round(worst, 2),
            "floor_pct": floor_pct,
            "state": state,
        }
    return report


def _fmt_pct(value):
    return "  n/a" if value is None else f"{value:5.1f}%"


def format_report(report):
    """A compact, human-readable density summary."""
    lines = []
    lines.append(f"Interposer density report  (cell '{report['topcell']}')")
    lines.append(f"  chip area {report['chip_area_um2']} um^2  from {report['chip_source']}")
    if not report["metals"]:
        lines.append("  chip area is zero; nothing measured.")
        return "\n".join(lines)

    lines.append("")
    lines.append("  Global metal density (drawn+filler-slit / chip)")
    lines.append("    metal   coverage   band            state")
    for key in ("M4", "M5", "TM1", "TM2"):
        m = report["metals"][key]
        lo, hi = m["band"]
        lines.append(f"    {key:5s} {_fmt_pct(m['coverage_pct'])}   "
                     f"[{lo:4.1f}, {hi:4.1f}]%   {m['state']}")

    lbe = report["lbe"]
    lines.append("")
    lines.append(f"  LBE density {_fmt_pct(lbe['coverage_pct'])}   "
                 f"max {lbe['max_pct']:.1f}%   {lbe['state']}")

    lines.append("")
    lines.append("  Slit adequacy on wide plates (worst plate vs Slt.i floor)")
    lines.append("    metal   wide plates   worst ratio   floor    state")
    for key in ("M4", "M5", "TM1", "TM2"):
        s = report["slits"][key]
        lines.append(f"    {key:5s} {s['wide_plates']:11d}   {_fmt_pct(s['min_ratio_pct'])}     "
                     f"{s['floor_pct']:4.1f}%   {s['state']}")

    lines.append("")
    lines.append("  Windowed local density (MnFil.h/k) and pass/fail: run the density deck.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="One-click interposer density report")
    parser.add_argument("input", help="input layout (GDS/OAS)")
    parser.add_argument("--topcell", default=None)
    parser.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    args = parser.parse_args(argv)

    report = measure(args.input, args.topcell)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
