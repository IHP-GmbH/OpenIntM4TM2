# xschem symbols

Schematic symbols for the interposer device library, ported from the
IHP-Open-PDK (SG13G2 open PDK). Each symbol keeps its upstream Apache-2.0
header.

## Contents

| Symbol | Device |
|---|---|
| `intm4tm2_pr/cap_cmim.sym` | MIM capacitor. Netlists as an `X`-prefixed subckt instance: `X<name> <plus> <minus> cap_cmim w=<w> l=<l> m=<m>` (LVS form uses a `C` prefix). Template defaults `w=l=7.0e-6`, `m=1`. Displays the computed capacitance `m*(w*l*1.5e-3 + 2*(w+l)*40e-12)`. |

## Usage

Add the library to `XSCHEM_LIBRARY_PATH` (or reference the symbol by path)
and include the simulation model in the testbench:

```spice
.lib /path/to/libs.tech/ngspice/models/cornerCAP.lib cap_typ
```

Pin order is part of the contract: `c0` = PLUS (top plate, TopMetal1 side)
first, `c1` = MINUS (bottom plate, Metal5) second. The KiCad counterpart is
specified in `../kicad/symbols/README.md`.

`intm4tm2_tests/test_cmim_symbol.py` (under `../klayout/`) pins the symbol
attributes statically and, when xschem is installed, netlists a minimal
schematic headlessly and checks the emitted instance line.
