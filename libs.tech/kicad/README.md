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

## See also

- A fully wired demo assembly (board, footprints, exported `.chiplet`):
  `examples/interposer_wire_bonding_demo/` in the `adk-tools` distribution.
- Chiplet footprints carry `GDS_FILE` / `LYP_FILE` fields; footprints are
  generated from die GDS by the `gds_to_kicad` converter.
