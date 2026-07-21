#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Generate the LVS connectivity unit fixtures for the interposer, following the
IHP-SG13G2 `lvs/testing/` convention: small GDS testcases exercised by the LVS
deck, checked by run_regression.py.

Three fixtures plus a reference netlist, each a self-contained top cell:
  - lvs_clean.gds  (LVS_CLEAN) : labeled nets extract cleanly; matches
                                 lvs_clean_reference.cir
  - lvs_open.gds   (LVS_OPEN)  : one label split across disconnected nets
                                 -> Connectivity OPEN
  - lvs_short.gds  (LVS_SHORT) : two labels bridged on one net
                                 -> Connectivity SHORT

Nets are built from TopMetal2 pads (134/0) joined through TopVia2 (133/0) and
TopMetal1 (126/0); labels are placed on TopMetal2 text (134/25). See
lvs/rule_decks/layers_definitions.lvs for the layers the deck consumes.

Regenerate with:  python gen_lvs_connectivity_testcase.py   (writes the .gds/.cir here)
"""

from pathlib import Path

import klayout.db as db

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


def main():
    here = Path(__file__).resolve().parent
    generate_clean(here / "lvs_clean.gds")
    generate_open(here / "lvs_open.gds")
    generate_short(here / "lvs_short.gds")
    generate_reference(here / "lvs_clean_reference.cir")
    print(f"Wrote lvs_clean/open/short.gds + lvs_clean_reference.cir in {here}")


if __name__ == "__main__":
    main()
