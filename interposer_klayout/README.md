# Interposer KLayout PDK

IHP-derived interposer technology PDK for KLayout. This PDK contains only backend metal layers suitable for interposer applications.

## Overview

This PDK is derived from IHP SG13G2 130nm BiCMOS process, with all frontend device layers removed. Only the backend metal redistribution stack is retained.

## Layer Stack

The interposer supports 13 layer groups:

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

### Utility
- **prBoundary** (189/0) - Cell boundary layer

## Directory Structure

```
interposer_klayout/
├── tech/
│   ├── interposer_ihp.lyp    # Layer properties (colors, patterns)
│   ├── interposer_ihp.lyt    # Technology file (connectivity)
│   ├── interposer_ihp.map    # LEF/DEF layer mapping
│   ├── drc/                  # Design Rule Check
│   │   ├── interposer_ihp.drc
│   │   ├── run_drc.py
│   │   └── rule_decks/
│   │       └── layers_def.drc
│   ├── lvs/                  # Layout vs Schematic
│   │   ├── interposer_ihp.lvs
│   │   └── rule_decks/
│   │       └── layers_definitions.lvs
│   └── macros/
└── scripts/
```

## Files

### Core Technology Files

- `interposer_ihp.lyp` - Layer properties file defining visual appearance and GDS layer/datatype mapping for all 13 layer groups
- `interposer_ihp.lyt` - Technology file defining connectivity between metal layers
- `interposer_ihp.map` - LEF/DEF layer mapping for Metal4-5 and TopMetal1-2 routing

### Design Verification

- `drc/interposer_ihp.drc` - Design Rule Check runset (simplified for backend only)
- `drc/rule_decks/layers_def.drc` - Layer definitions for DRC
- `lvs/interposer_ihp.lvs` - Layout vs Schematic runset (connectivity checking only)
- `lvs/rule_decks/layers_definitions.lvs` - Layer definitions for LVS

## Usage

### Loading the Technology in KLayout

1. Open KLayout
2. Tools → Manage Technologies
3. Add new technology pointing to `interposer_ihp.lyt`

### Running DRC

```bash
python tech/drc/run_drc.py --gds <your_layout.gds>
```

### Running LVS

```bash
klayout -b -r tech/lvs/interposer_ihp.lvs -rd input=<your_layout.gds>
```

## Differences from Original SG13G2

### Removed (~180+ layers)
- All frontend layers (substrate, active, gate, poly, wells, diffusions)
- Lower metal layers (Metal1, Via1, Metal2, Via2, Metal3, Via3, Cont)
- All device layers (MOS, BJT, diode, resistor, inductor, etc.)
- AntMetal1 layer (not needed for interposer)

### Retained (13 layers)
- Backend metal stack only (Metal4 through TopMetal2)
- Passive components (MIM capacitors, thin film resistors)
- RFMEMS integration layers
- Essential boundary layers

## Source

Derived from IHP SG13G2 PDK:
- Original: https://github.com/IHP-GmbH/IHP-Open-PDK

Layer definitions based on `interposer_pdk_layers.csv`
