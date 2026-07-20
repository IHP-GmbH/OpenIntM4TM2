# KiCad symbols

Schematic symbols for the interposer device library. This directory is
the KiCad counterpart of the xschem symbol library at
`libs.tech/xschem/intm4tm2_pr/`.

## `cap_cmim.kicad_sym` (delivered) - hybrid symbol set

The library ships a **hybrid** set: one generic symbol plus one value-keyed
symbol per round capacitance, all in the single `cap_cmim.kicad_sym` file.

- **`cap_cmim`** - the generic MIM capacitor (Metal5 / MIM / TopMetal1 stack).
  Place it and edit `w` / `l` (and `m`) yourself for any plate size. Its
  defaults are `w = l = 8.11e-6` m, which display as 100 fF and reference the
  `intm4tm2:CMIM_100fF` footprint (the anchor member).
- **9 value-keyed derived symbols** - `CMIM_10fF`, `CMIM_20fF`, `CMIM_50fF`,
  `CMIM_100fF`, `CMIM_200fF`, `CMIM_500fF`, `CMIM_1pF`, `CMIM_2pF`, `CMIM_5pF`.
  Each is a derived symbol (`extends "cap_cmim"`) that inherits the pins,
  graphics and simulation model from the generic symbol, and pre-wires the
  round-capacitance mapping: `Value` (e.g. `100 fF`), the matching
  `intm4tm2:CMIM_<value>` footprint, and the `w` / `l` / `m` / `Capacitance`
  properties. Pick a capacitance directly and you get a consistent symbol,
  footprint and netlist with no manual number entry.

The xschem symbol at `libs.tech/xschem/intm4tm2_pr/cap_cmim.sym` is the
reference for pin order and attributes; the KiCad symbols are electrically
equivalent and are pinned to it by
`libs.tech/klayout/intm4tm2_tests/test_cmim_kicad_symbol.py` (and, on the
xschem side, `test_cmim_symbol.py`).

### Why pre-baked value symbols

KiCad cannot back-solve capacitance live: there is no way to type "100 fF" and
have the schematic editor compute the plate `w` / `l`. The value-keyed symbols
therefore **pre-bake** the capacitance-to-geometry mapping (the same grid-snapped
square-cap solutions used by the footprint family), so a user picks a round
value without touching the geometry. The electrical contract is unchanged: the
netlist still carries `w` / `l` / `m`, not a capacitance number (see Spice
netlisting below). The displayed `Capacitance` property is informational only.

The nine values and their pre-baked `w = l` (metres, matching the footprint
family) are:

| Symbol       | Value  | w = l (m)  |
|--------------|--------|------------|
| `CMIM_10fF`  | 10 fF  | 2.53e-6    |
| `CMIM_20fF`  | 20 fF  | 3.6e-6     |
| `CMIM_50fF`  | 50 fF  | 5.72e-6    |
| `CMIM_100fF` | 100 fF | 8.11e-6    |
| `CMIM_200fF` | 200 fF | 11.495e-6  |
| `CMIM_500fF` | 500 fF | 18.205e-6  |
| `CMIM_1pF`   | 1 pF   | 25.765e-6  |
| `CMIM_2pF`   | 2 pF   | 36.46e-6   |
| `CMIM_5pF`   | 5 pF   | 57.68e-6   |

For a capacitance that is not on this ladder, use the generic `cap_cmim` symbol
(edit `w` / `l`) and generate the matching footprint on demand with
`../scripts/cmim_footprint_gen.py --cap <value>` (see
`../footprints/README.md`).

### Pins

| Pin   | Number | Position in netlist | Plate                          |
|-------|--------|---------------------|--------------------------------|
| PLUS  | 1      | first               | top plate (TopMetal1 side)     |
| MINUS | 2      | second              | bottom plate (Metal5)          |

Pin order matters: it must match the xschem symbol (pin `c0` = PLUS
first, pin `c1` = MINUS second), otherwise LVS and simulation netlists
disagree between the two flows. The pins carry numeric identifiers
(1 = PLUS, 2 = MINUS) so the SPICE node order is fixed and cannot be
reordered alphabetically. The derived value symbols inherit these pins
unchanged.

### Spice netlisting

The device is a subcircuit, not a primitive capacitor. Netlisting emits
a subckt instance line with an `X` name prefix, never a plain `C`
element:

```
X<ref> PLUS MINUS cap_cmim w=<w> l=<l> m=<m>
```

In KiCad terms: simulation model type SUBCKT (`Sim.Device SUBCKT`) with
model name `cap_cmim` (`Sim.Name cap_cmim`), passing `w`, `l` and `m` as
instance parameters (`Sim.Params w={w} l={l} m={m}`). This holds for the
generic symbol and every value-keyed derived symbol - the derived symbols
only differ in the pre-baked `w` / `l` numbers they carry.

Parameter defaults (generic `cap_cmim`):

| Parameter | Default | Meaning                    |
|-----------|---------|----------------------------|
| w         | 8.11e-6 | plate width in metres      |
| l         | 8.11e-6 | plate length in metres     |
| m         | 1       | parallel multiplier        |

#### Note on the default value (anchor at 100 fF)

The generic `cap_cmim` symbol defaults to `w = l = 8.11e-6` m, the
grid-snapped square-cap solution for 100 fF, and references the
`intm4tm2:CMIM_100fF` footprint. This makes the anchor member of the
round-capacitance family the default, so an unedited generic symbol and the
`CMIM_100fF` derived symbol are electrically identical. The regression tests
(`test_cmim_kicad_symbol.py` and `test_cmim_symbol.py`) pin the default and the
derived-symbol wiring so the two schematic flows stay consistent.

### Capacitance display

The symbol displays the computed capacitance next to the instance, using
the same formula as the xschem symbol and the PCell (area capacitance
1.5 fF/um^2, perimeter capacitance 0.04 fF/um):

```
C[fF] = w*l*1.5 + 2*(w+l)*0.04     (w, l in um)
```

For a square plate this reduces to `C[fF] = 1.5*w^2 + 0.16*w`. With the generic
defaults (w = l = 8.11 um) it evaluates to about 100 fF. Each value symbol
carries its round label directly.

### Simulation model

The subckt definition comes from the ngspice corner library:

- include file: `libs.tech/ngspice/models/cornerCAP.lib`
- library section: `cap_typ` (typical corner), e.g.
  `.lib cornerCAP.lib cap_typ`

The symbol's `Sim.Library` property references `cornerCAP.lib` via the
portable `${INTERPOSER_PDK_ROOT}` prefix. The derived value symbols inherit
this model reference.


