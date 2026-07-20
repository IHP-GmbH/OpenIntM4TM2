# KiCad symbols

Schematic symbols for the interposer device library. This directory is
the KiCad counterpart of the xschem symbol library at
`libs.tech/xschem/intm4tm2_pr/`.

## `cap_cmim.kicad_sym` (delivered)

MIM capacitor (Metal5 / MIM / TopMetal1 stack). The xschem symbol at
`libs.tech/xschem/intm4tm2_pr/cap_cmim.sym` is the reference for pin
order and attributes; the KiCad symbol is electrically equivalent and is
pinned to it by `libs.tech/klayout/intm4tm2_tests/test_cmim_kicad_symbol.py`
(and, on the xschem side, `test_cmim_symbol.py`).

### Pins

| Pin   | Number | Position in netlist | Plate                          |
|-------|--------|---------------------|--------------------------------|
| PLUS  | 1      | first               | top plate (TopMetal1 side)     |
| MINUS | 2      | second              | bottom plate (Metal5)          |

Pin order matters: it must match the xschem symbol (pin `c0` = PLUS
first, pin `c1` = MINUS second), otherwise LVS and simulation netlists
disagree between the two flows. The pins carry numeric identifiers
(1 = PLUS, 2 = MINUS) so the SPICE node order is fixed and cannot be
reordered alphabetically.

### Spice netlisting

The device is a subcircuit, not a primitive capacitor. Netlisting emits
a subckt instance line with an `X` name prefix, never a plain `C`
element:

```
X<ref> PLUS MINUS cap_cmim w=<w> l=<l> m=<m>
```

In KiCad terms: simulation model type SUBCKT (`Sim.Device SUBCKT`) with
model name `cap_cmim` (`Sim.Name cap_cmim`), passing `w`, `l` and `m` as
instance parameters (`Sim.Params w={w} l={l} m={m}`).

Parameter defaults:

| Parameter | Default | Meaning                    |
|-----------|---------|----------------------------|
| w         | 7.0e-6  | plate width in metres      |
| l         | 7.0e-6  | plate length in metres     |
| m         | 1       | parallel multiplier        |

#### Note on the default value (7.0e-6 vs 6.99u)

The symbol uses `w = l = 7.0e-6` m, not the layout PCell's own default
plate size of 6.99 um. This is deliberate: `7.0e-6` is the netlisted /
simulation default shared with the xschem `cap_cmim` reference, and the
regression tests (`test_cmim_kicad_symbol.py` and `test_cmim_symbol.py`)
pin exactly `7.0e-6` so the two schematic flows stay electrically
identical. The 6.99 um figure is the PCell's default *plate* dimension;
it does not change the electrical default carried by the symbol. The
default footprint referenced by the symbol is the matching
`intm4tm2:CMIM_7x7um` family member.

### Capacitance display

The symbol displays the computed capacitance next to the instance, using
the same formula as the xschem symbol and the PCell (area capacitance
1.5 fF/um^2, perimeter capacitance 0.04 fF/um):

```
C[fF] = w*l*1.5 + 2*(w+l)*0.04     (w, l in um)
```

With the defaults (w = l = 7.0 um) this evaluates to about 74.6 fF.

### Simulation model

The subckt definition comes from the ngspice corner library:

- include file: `libs.tech/ngspice/models/cornerCAP.lib`
- library section: `cap_typ` (typical corner), e.g.
  `.lib cornerCAP.lib cap_typ`

The symbol's `Sim.Library` property references `cornerCAP.lib` via the
portable `${INTERPOSER_PDK_ROOT}` prefix.

### Acceptance checklist

- [x] File `cap_cmim.kicad_sym` in this directory
- [x] Pins named PLUS and MINUS, in that netlist order (numbers 1/2)
- [x] Netlist emits `X<ref> PLUS MINUS cap_cmim w=<w> l=<l> m=<m>` (SUBCKT)
- [x] Defaults w=7.0e-6, l=7.0e-6, m=1 (see note above)
- [x] Capacitance display formula as above (~74.6 fF at the defaults)
- [x] Simulation model references `cornerCAP.lib` section `cap_typ`

The layout counterpart (real-size footprints mirroring the PCell plates)
lives in `libs.tech/kicad/footprints/intm4tm2.pretty`; see
`libs.tech/kicad/footprints/README.md`.
