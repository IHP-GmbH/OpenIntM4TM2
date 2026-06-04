# IntM4TM2 KLayout PDK

KLayout PDK for the IHP 130-nm IntM4TM2 aluminum BEOL interposer. This PDK contains only backend metal layers suitable for interposer applications.

## Overview

IntM4TM2 is derived from the IHP SG13G2 130nm BiCMOS process, with all frontend device layers removed. Only the backend aluminum metal redistribution stack (Metal4 through TopMetal2) is retained.

## Layer Stack

The interposer supports 19 base layers:

### Metal Interconnect Stack
- **Metal4** (50/0) - 4th metal interconnect layer
- **Via4** (66/0) - Via between Metal4 and Metal5
- **Metal5** (67/0) - 5th metal interconnect layer
- **TopVia1** (125/0) - Via from Metal5 to TopMetal1
- **TopMetal1** (126/0) - 1st thick top metal layer
- **TopVia2** (133/0) - Via between TopMetal1 and TopMetal2
- **TopMetal2** (134/0) - 2nd thick top metal layer

### Passive Components
- **MIM** (36/0) - Metal-Insulator-Metal capacitor
- **Vmim** (129/0) - MIM capacitor marking layer
- **ThinFilmRes** (146/0) - Thin film resistor

### MEMS Integration
- **RFMEM** (147/0) - RFMEMS device areas
- **MEMVia** (145/0) - Local vias within RFMEMS area

### Pads & Utility
- **Passiv** (9/0) - Passivation opening regions (removes passivation coating)
- **EdgeSeal** (39/0, 39/4) - Die edge sealing (internal use)
- **dfpad** (41/0) - Pad recognition layer
- **dfpad.pillar** (41/35) - Copper pillar pad recognition
- **dfpad.sbump** (41/36) - Solder bump pad recognition
- **TEXT** (63/0) - Macro cell name and element text
- **prBoundary** (189/0) - Cell boundary layer
- **LBE** (157/0) - Localized backside etch for TSV/cavity applications
- **IND** (27/0, 27/2, 27/25) - Inductor marking (visual marker, TopMetal1/2 inductors)

## Directory Structure

```
libs.tech/klayout/
├── tech/
│   ├── intm4tm2.lyp    # Layer properties (colors, patterns)
│   ├── intm4tm2.lyt    # Technology file (connectivity)
│   ├── intm4tm2.map    # LEF/DEF layer mapping
│   ├── drc/                  # Design Rule Check
│   │   ├── intm4tm2.drc
│   │   ├── run_drc.py
│   │   └── rule_decks/
│   │       └── layers_def.drc
│   ├── lvs/                  # Layout vs Schematic
│   │   ├── intm4tm2.lvs
│   │   ├── run_lvs.py
│   │   └── rule_decks/
│   │       └── layers_definitions.lvs
│   └── macros/
├── python/                   # Assembly tooling (bump_mirror.py)
└── intm4tm2_tests/           # PCell, DRC and LVS tests
```

## Files

### Core Technology Files

- `intm4tm2.lyp` - Layer properties file defining visual appearance and GDS layer/datatype mapping for all 13 layer groups
- `intm4tm2.lyt` - Technology file defining connectivity between metal layers
- `intm4tm2.map` - LEF/DEF layer mapping for Metal4-5 and TopMetal1-2 routing

### Design Verification

- `drc/intm4tm2.drc` - Design Rule Check runset (simplified for backend only)
- `drc/rule_decks/layers_def.drc` - Layer definitions for DRC
- `lvs/intm4tm2.lvs` - Layout vs Schematic runset (connectivity checking only)
- `lvs/rule_decks/layers_definitions.lvs` - Layer definitions for LVS

## Usage

### Loading the Technology in KLayout

1. Open KLayout
2. Tools → Manage Technologies
3. Add new technology pointing to `intm4tm2.lyt`

### Running DRC

```bash
python tech/drc/run_drc.py --gds <your_layout.gds>
```

### Running LVS

```bash
klayout -b -r tech/lvs/intm4tm2.lvs -rd input=<your_layout.gds>
```

## Differences from Original SG13G2

### Removed (~180+ layers)
- All frontend layers (substrate, active, gate, poly, wells, diffusions)
- Lower metal layers (Metal1, Via1, Metal2, Via2, Metal3, Via3, Cont)
- All device layers (MOS, BJT, diode, resistor, inductor, etc.)
- AntMetal1 layer (not needed for interposer)

### Retained (19 base layers)
- Backend metal stack only (Metal4 through TopMetal2)
- Passive components (MIM capacitors, thin film resistors, inductors)
- RFMEMS integration layers
- Pad and utility layers (Passiv, EdgeSeal, dfpad, TEXT)
- Advanced features (LBE for TSV/cavities, IND for inductor marking)
- Essential boundary layers

**Note on IND layer:** Used for visual marking of inductor areas only. Full LVS device extraction not supported in interposer PDK (requires frontend substrate/well layers).

## Source

Derived from IHP SG13G2 PDK:
- Original: https://github.com/IHP-GmbH/IHP-Open-PDK

Layer definitions follow the IntM4TM2 process layer table.
