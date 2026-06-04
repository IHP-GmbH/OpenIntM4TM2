# IntM4TM2 KLayout PDK

KLayout PDK for the IHP 130-nm IntM4TM2 aluminum BEOL interposer. This PDK contains only backend metal layers suitable for interposer applications.

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

### Design Verification (WIP)

- `drc/intm4tm2.drc` - Design Rule Check runset (WIP)
- `drc/rule_decks/layers_def.drc` - Layer definitions for DRC (WIP)
- `lvs/intm4tm2.lvs` - Layout vs Schematic runset (WIP)
- `lvs/rule_decks/layers_definitions.lvs` - Layer definitions for LVS (WIP)

## Usage

### Running DRC

```bash
python tech/drc/run_drc.py --gds <your_layout.gds>
```

### Running LVS

```bash
klayout -b -r tech/lvs/intm4tm2.lvs -rd input=<your_layout.gds>
```
