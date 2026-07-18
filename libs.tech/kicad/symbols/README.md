# KiCad symbols

Schematic symbols for the interposer device library. This directory is
the KiCad counterpart of the xschem symbol library at
`libs.tech/xschem/intm4tm2_pr/`.

## To be delivered: `cap_cmim.kicad_sym`

MIM capacitor (Metal5 / MIM / TopMetal1 stack). The xschem symbol at
`libs.tech/xschem/intm4tm2_pr/cap_cmim.sym` is the reference for pin
order and attributes; the KiCad symbol must be electrically equivalent.

### Pins

| Pin   | Position in netlist | Plate                          |
|-------|---------------------|--------------------------------|
| PLUS  | first               | top plate (TopMetal1 side)     |
| MINUS | second              | bottom plate (Metal5)          |

Pin order matters: it must match the xschem symbol (pin `c0` = PLUS
first, pin `c1` = MINUS second), otherwise LVS and simulation netlists
disagree between the two flows.

### Spice netlisting

The device is a subcircuit, not a primitive capacitor. Netlisting must
emit a subckt instance line with an `X` name prefix, never a plain `C`
element:

```
X<ref> PLUS MINUS cap_cmim w=<w> l=<l> m=<m>
```

In KiCad terms: simulation model type SUBCKT with model name
`cap_cmim`, passing `w`, `l` and `m` as instance parameters.

Parameter defaults (matching the layout PCell default of 6.99 um):

| Parameter | Default | Meaning                    |
|-----------|---------|----------------------------|
| w         | 6.99u   | plate width in meters      |
| l         | 6.99u   | plate length in meters     |
| m         | 1       | parallel multiplier        |

### Capacitance display

The symbol should display the computed capacitance next to the
instance, using the same formula as the xschem symbol and the PCell
(area capacitance 1.5 fF/um^2, perimeter capacitance 0.04 fF/um):

```
C[fF] = w*l*1.5 + 2*(w+l)*0.04     (w, l in um)
```

With the defaults (w = l = 6.99 um) this evaluates to about 74.6 fF.

### Simulation model

The subckt definition comes from the ngspice corner library:

- include file: `libs.tech/ngspice/models/cornerCAP.lib`
- library section: `cap_typ` (typical corner), e.g.
  `.lib cornerCAP.lib cap_typ`

### Acceptance checklist

- [ ] File `cap_cmim.kicad_sym` in this directory
- [ ] Pins named PLUS and MINUS, in that netlist order
- [ ] Netlist emits `X<ref> PLUS MINUS cap_cmim w=<w> l=<l> m=<m>`
- [ ] Defaults w=6.99u, l=6.99u, m=1
- [ ] Capacitance display formula as above
- [ ] Simulates against `cornerCAP.lib` section `cap_typ` in ngspice
