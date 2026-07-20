# KiCad template board

`interposer_template.kicad_pcb` is the starting point for IntM4TM2 interposer
assembly designs in KiCad (Chiplets KiCad plugin / `hyp_to_gds` flow). It ships
a 4-copper-layer board with the layers pre-named after the PDK metals, so the
exporter can map routing without any manual layer setup.

## Why start here

The HYP-to-GDS exporter maps board copper layers to the PDK metals **by name**.
A board that keeps the default KiCad copper names (`F.Cu`, `In1.Cu`, ...) leaves
its routing on layers the PDK does not recognize. When most of the routing lands
on unmapped layers the exporter aborts and points back at this template; when
only some of it does it emits a warning and continues. Either way, starting from
the template avoids the problem because the four copper layers are already
renamed:

| KiCad layer | Renamed to (PDK metal) |
|---|---|
| `F.Cu`   | `TopMetal2` |
| `In1.Cu` | `TopMetal1` |
| `In2.Cu` | `Metal5`    |
| `B.Cu`   | `Metal4`    |

To reproduce the renames on an existing board instead, open
Board Setup > Board Editor Layers and enter the PDK metal names as the
user-defined layer names.

## Using the template

1. Copy `interposer_template.kicad_pcb` into your project directory under your
   board name and open it in KiCad (use the ADK KiCad build; the file is saved
   in its board format).
2. Draw the interposer outline on `Edge.Cuts`. The exporter derives the
   interposer dimensions from the outline bounding box and warns about
   components placed outside it.
3. Delete the example routing once it has served as a reference: a small mesh on
   `Metal5` plus a few traces on the other metals, and the 4 vias that connect
   them.
4. Set the text variables below, place your chiplet footprints, route.

## Text variables (Board Setup > Text Variables)

These three board-level variables tell the exporter and the assembly DRC how to
resolve the interposer PDK and which attachment methods to use. The template
ships none of them pre-populated; you set the ones you need.

| Variable | Meaning |
|---|---|
| `INTERPOSER_LYP` | Path to the interposer layer properties file. Use the portable form `${INTERPOSER_PDK_ROOT}/libs.tech/klayout/tech/intm4tm2.lyp`; consumers expand `${INTERPOSER_PDK_ROOT}` from the environment or a sibling-checkout search. When unset, readers fall back to the same discovery. |
| `INTERPOSER_ADAPTER` | ADK interposer adapter id used by the assembly DRC. Defaults to `intm4tm2`; set only to override. |
| `INTERCONNECT_ADAPTER` | Optional interconnect method id (second axis: Cu-pillar, solder bump, vendor microbump). Validated against the interconnect PDK manifest at export time; per-die overrides go in a `CONNECTION` footprint field. |

## Device footprints (cmim MIM capacitor)

Beyond the template board, this tree ships real-size KiCad footprints for the
IntM4TM2 MIM capacitor (`cap_cmim`, model of the IHP SG13G2 open PDK). The
schematic side is a hybrid symbol set in `symbols/cap_cmim.kicad_sym` (see
`symbols/README.md`): the generic `cap_cmim` symbol (edit `w` / `l` for any
value) plus 9 value-keyed derived symbols (`CMIM_10fF` ... `CMIM_5pF`) that
pre-wire a round capacitance to its `w` / `l` and matching footprint. The
footprints and their generator are described below.

### Footprint library `footprints/intm4tm2.pretty`

The `.pretty` library holds the discrete cmim footprint family. Each footprint
is **real-size**: it mirrors the plate geometry of the KLayout PCell rather
than using a symbolic PCB land pattern, so the copper drawn on the board is the
same size as the capacitor drawn in the layout. For a plate of width `w` and
length `l` (micrometres):

- MINUS plate (Metal5) = outer rectangle `(w + 1.2) x (l + 1.2)` um.
- PLUS plate (TopMetal1) = inner rectangle derived from the PCell via array
  (a couple of microns smaller than the Metal5 plate, concentric with it).

Both plates share the same centre. The footprint's courtyard and fab outline
follow the outer Metal5 rectangle. The discrete family is keyed by round
capacitance (`CMIM_10fF` ... `CMIM_5pF`, anchored at `CMIM_100fF`); see
`footprints/README.md` for the member list with plate sizes and real pad sizes.

### Inner-copper layer placement

Unlike ordinary SMD parts that land on `F.Cu` / `B.Cu`, the cmim pads sit on
the two **inner** copper layers, following the template layer map:

| Pad | Plate     | KiCad layer | PDK metal |
|-----|-----------|-------------|-----------|
| `1` | PLUS      | `In1.Cu`    | TopMetal1 |
| `2` | MINUS     | `In2.Cu`    | Metal5    |

Routing to a cmim therefore happens on those inner metals (TopMetal1 and
Metal5), not on the outer copper. This is expected and correct for the
interposer metal stack.

### Generator `scripts/cmim_footprint_gen.py`

A standalone, stdlib-only script generates the footprints. It reads the tech
constants from the same `intm4tm2_tech.json` the PCell uses, and re-implements
the PCell plate math (the PCell itself is never imported, so the script has no
KLayout dependency). CLI modes:

- Arbitrary capacitance on demand: solve a square (`w = l`) plate straight from
  a target C. Accepts `f` / `fF`, `p` / `pF` or a bare number in fF, over the
  device range of roughly 2.13 fF (minimum plate) up to 8 pF:

  ```
  python scripts/cmim_footprint_gen.py --cap 250f --out my_cmim.kicad_mod
  ```

- Single, on-demand footprint for an arbitrary rectangular size:

  ```
  python scripts/cmim_footprint_gen.py --w 12u --l 8u --out my_cmim.kicad_mod
  ```

- Regenerate the whole round-capacitance family into the `.pretty` directory:

  ```
  python scripts/cmim_footprint_gen.py --family
  ```

The size math is duplicated from the layout PCell, but it is not allowed to
drift: `libs.tech/klayout/intm4tm2_tests/test_cmim_footprint.py` regenerates
the real PCell headlessly and asserts that each footprint's pad sizes equal the
PCell's Metal5 / TopMetal1 bounding boxes within 2 nm.

## See also

- A fully wired demo assembly (board, footprints, exported `.chiplet`):
  `examples/interposer_wire_bonding_demo/` in the `adk-tools` distribution.
- Chiplet footprints carry `GDS_FILE` / `LYP_FILE` fields; footprints are
  generated from die GDS by the `gds_to_kicad` converter.
