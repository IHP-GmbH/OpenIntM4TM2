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

Display capacitance uses `C[fF] = w*l*1.5 + 2*(w+l)*0.04` (w, l in um), which
for a square plate reduces to `C[fF] = 1.5*w^2 + 0.16*w`.

### Family members (keyed by round capacitance)

The discrete family is keyed by **round capacitance**, not by round plate
dimensions. Each member's `w = l` is the grid-snapped square-cap solution for
its nominal value, so the on-shelf part you pick is the capacitance you want.
The "Metal5 pad" column is the real outer pad size (`w + 1.2` um), i.e. the
courtyard and the copper drawn on the board.

| Footprint     | Nominal C | w = l (um) | Metal5 pad (um) | Actual C   |
|---------------|-----------|------------|-----------------|------------|
| `CMIM_10fF`   | 10 fF     | 2.53       | 3.73            | 10.01 fF   |
| `CMIM_20fF`   | 20 fF     | 3.6        | 4.8             | 20.02 fF   |
| `CMIM_50fF`   | 50 fF     | 5.72       | 6.92            | 49.99 fF   |
| `CMIM_100fF`  | 100 fF    | 8.11       | 9.31            | 99.96 fF   |
| `CMIM_200fF`  | 200 fF    | 11.495     | 12.695          | 200.04 fF  |
| `CMIM_500fF`  | 500 fF    | 18.205     | 19.405          | 500.05 fF  |
| `CMIM_1pF`    | 1 pF      | 25.765     | 26.965          | 999.88 fF  |
| `CMIM_2pF`    | 2 pF      | 36.46      | 37.66           | 1999.83 fF |
| `CMIM_5pF`    | 5 pF      | 57.68      | 58.88           | 4999.70 fF |

Every actual C stays within about 0.1 % of its nominal value. `CMIM_100fF` is
the anchor member: it matches the default `w = l = 8.11e-6` m carried by the
`cap_cmim` symbol (see `../symbols/README.md`).

## Generating an arbitrary capacitance on demand

The family is emitted by `../scripts/cmim_footprint_gen.py`. For a value that is
not on the ladder above, solve a square (`w = l`) cap straight from a target
capacitance with `--cap`:

```
python ../scripts/cmim_footprint_gen.py --cap 250f --out CMIM_250fF.kicad_mod
```

`--cap` accepts `f` / `fF` (femtofarad), `p` / `pF` (picofarad) or a bare number
(read as fF): `250f`, `1p`, `10fF`, `2.13fF`. It inverts the square-cap model,
snaps the resulting `w = l` to the nearest step of the layout grid (0.005 um) so
the emitted plate is the closest match to the target capacitance, and needs an
`--out` path. `--cap` at a family value reproduces that family member exactly
(same grid-snap as `--family`). The valid range is the device range, roughly **2.13 fF**
(at the minimum plate `Wmin = 1.14` um) up to **8 pF** (`cmim_maxC`); a target
outside that band is rejected.

To emit a rectangular (`w != l`) plate for an arbitrary size instead of a target
capacitance:

```
python ../scripts/cmim_footprint_gen.py --w 12u --l 8u --out CMIM_12x8um.kicad_mod
```

`--w` / `--l` accept `u`, `um` or a bare number in micrometres, and are snapped
to the layout grid the way the PCell would snap them. To regenerate the whole
round-capacitance family in place:

```
python ../scripts/cmim_footprint_gen.py --family
```

The generator re-implements the PCell plate math and is pinned to the real
layout PCell by `libs.tech/klayout/intm4tm2_tests/test_cmim_footprint.py`, which
checks each footprint's pad sizes against the PCell's Metal5 / TopMetal1
bounding boxes within 2 nm.
