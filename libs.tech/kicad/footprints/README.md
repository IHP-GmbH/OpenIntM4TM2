# KiCad footprints

Real-size footprints for the interposer device library. Currently this holds
the `intm4tm2.pretty` library with the cmim MIM capacitor family (`cap_cmim`,
model of the IHP SG13G2 open PDK, IHP IntM4TM2 module).

## `intm4tm2.pretty` - cmim MIM capacitor family

Each footprint mirrors the plate geometry of the KLayout PCell, so the copper
on the board matches the capacitor in the layout. For a square plate of side
`w = l`:

- MINUS plate (Metal5) is the outer rectangle `(w + 1.2) x (l + 1.2)` um, on
  `In2.Cu`, pad `2`.
- PLUS plate (TopMetal1) is the inner rectangle from the PCell via array, on
  `In1.Cu`, pad `1`, concentric with the MINUS plate.

Both pads share the same centre; the courtyard and fab outline follow the outer
Metal5 rectangle. Routing to these parts happens on the inner metals TopMetal1
(`In1.Cu`) and Metal5 (`In2.Cu`), not on `F.Cu` / `B.Cu`.

Display capacitance uses `C[fF] = w*l*1.5 + 2*(w+l)*0.04` (w, l in um).

### Family members

| Footprint            | w x l (um)   | Metal5 plate (um) | TopMetal1 plate (um) | C (approx) |
|----------------------|--------------|-------------------|----------------------|------------|
| `CMIM_1p14x1p14um`   | 1.14 x 1.14  | 2.34 x 2.34       | 1.26 x 1.26          | 2.1 fF     |
| `CMIM_3x3um`         | 3 x 3        | 4.2 x 4.2         | 2.52 x 2.52          | 14.0 fF    |
| `CMIM_5x5um`         | 5 x 5        | 6.2 x 6.2         | 5.04 x 5.04          | 38.3 fF    |
| `CMIM_7x7um`         | 7 x 7        | 8.2 x 8.2         | 6.3 x 6.3            | 74.6 fF    |
| `CMIM_10x10um`       | 10 x 10      | 11.2 x 11.2       | 10.08 x 10.08        | 151.6 fF   |
| `CMIM_15x15um`       | 15 x 15      | 16.2 x 16.2       | 15.12 x 15.12        | 339.9 fF   |
| `CMIM_20x20um`       | 20 x 20      | 21.2 x 21.2       | 18.9 x 18.9          | 603.2 fF   |
| `CMIM_30x30um`       | 30 x 30      | 31.2 x 31.2       | 28.98 x 28.98        | 1.35 pF    |
| `CMIM_50x50um`       | 50 x 50      | 51.2 x 51.2       | 49.14 x 49.14        | 3.76 pF    |
| `CMIM_70x70um`       | 70 x 70      | 71.2 x 71.2       | 69.3 x 69.3          | 7.36 pF    |

The smallest member is at the PCell minimum plate size (`cmim_minLW` = 1.14 um);
the largest stays under the device maximum capacitance (`cmim_maxC` = 8 pF).
`CMIM_7x7um` is the default the `cap_cmim` symbol references.

## Generating a custom size on demand

The family is emitted by `../scripts/cmim_footprint_gen.py`. To make a footprint
for an arbitrary plate size that is not in the ladder above:

```
python ../scripts/cmim_footprint_gen.py --w 12u --l 8u --out CMIM_12x8um.kicad_mod
```

`--w` / `--l` accept `u`, `um` or a bare number in micrometres, and are snapped
to the layout grid the way the PCell would snap them. To regenerate the whole
family in place:

```
python ../scripts/cmim_footprint_gen.py --family
```

The generator re-implements the PCell plate math and is pinned to the real
layout PCell by `libs.tech/klayout/intm4tm2_tests/test_cmim_footprint.py`, which
checks each footprint's pad sizes against the PCell's Metal5 / TopMetal1
bounding boxes within 2 nm.
