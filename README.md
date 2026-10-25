# OpenIntM4TM2

KLayout PDK and assembly tooling for the **IHP 130-nm IntM4TM2 aluminum BEOL interposer**.

IntM4TM2 is a passive interposer technology derived from the IHP SG13G2 130 nm
BiCMOS process: all frontend device layers are removed and only the backend
aluminum metal redistribution stack (Metal4 through TopMetal2) is retained,
together with MIM capacitors, thin-film resistors, passivation/pad openings,
edge seal and localized backside etch (LBE).

## Status

> [!WARNING]
> OpenIntM4TM2 is currently a preview release only!
> DRC and LVS are still WIP.

## Repository layout

The repository follows the directory organization of the IHP open PDKs
(e.g. `ihp-sg13g2`, `ihp-sg13cmos5l`).

| Path | Contents |
|---|---|
| `libs.tech/klayout/tech/` | KLayout technology (`intm4tm2.lyt`, `intm4tm2.lyp`, `intm4tm2.map`), DRC and LVS runsets, macros |
| `libs.tech/klayout/python/` | Assembly tooling: `bump_mirror.py`, a standalone Cu-pillar pad generator that DRC pre-validates pin lists against the interposer rules before writing GDS |
| `libs.tech/klayout/intm4tm2_tests/` | Cu-pillar PCell, DRC and LVS connectivity tests |
| `libs.tech/kicad/` | KiCad template board for assembly designs (copper layers pre-named after the PDK metals) |
| `libs.tech/intm4tm2_stack.json` | Physical stack: the silicon substrate thickness with its source, the routing metals' z bounds, the attach plane, and the passivation over it |
| `libs.tech/chiplet_studio/intm4tm2.stackup.yaml` | 3D stackup for Chiplet Studio, derived from the physical stack |
| `libs.ref/intm4tm2_examples/gds/` | Example layouts (measurement test structures) |

## Getting started

Open KLayout and install the technology from `libs.tech/klayout/tech/intm4tm2.lyt`
(Tools > Manage Technologies > Import Technology). The layer properties file and
LEF/DEF layer mapping are picked up automatically.

DRC:

```bash
python3 libs.tech/klayout/tech/drc/run_drc.py --path <layout.gds> --topcell <cell>
```

For chiplet assembly designs in KiCad, start from the template board in
`libs.tech/kicad/`: the HYP-to-GDS exporter maps board copper to the PDK
metals by name, and the template ships the required layer renames
(`TopMetal2`/`TopMetal1`/`Metal5`/`Metal4`). Set the board text variables
yourself as described in `libs.tech/kicad/README.md`.

## Physical stack

`libs.tech/intm4tm2_stack.json` declares the silicon substrate thickness, with
its source, the z bounds of the four routing metals and the oxide around them,
`beol.attachment_surface_z_um`, the top of TopMetal2 in this PDK's reduced
back-end, and `beol.passivation`, the oxide and nitride films over that plane,
with their public source. The interposer body is the substrate plus the attach
plane; the passivation lies above it and is not part of it. Consumers derive the
body and no file stores it. The EM stackups, the KiCad template and the Chiplet
Studio stackup follow the key, and
`libs.tech/klayout/intm4tm2_tests/test_body_thickness.py` names every copy that
disagrees with it. The Studio stackup leaves out BackMetal1, which the key does
not carry, and draws no passivation: Studio extrudes a layer's polygons, and
Passiv holds the openings, not the film.

The attach plane in this key is the plane dies mount on. Assembly flows read
`beol.attachment_surface_z_um` from this file and keep no copy of it; a die's z
is that plane plus its connection stack height, written as the exact decimal sum
of the two as written, in the shortest text that reads back as that sum. The
0.64 um base oxide under Metal4 is the `dummyOx` of the public
SG13G2 ITF (`ihp-sg13g2/libs.tech/parasitics/itf/sg13g2_typ.itf`), the
dielectric under Metal1 there, reused for the carrier; no IntM4TM2-specific
value for it is published.

`libs.tech/parasitics/routed_load.py` reads one route's traces and bumps, as
interposer-pnr writes them, and gives each named net a capacitance meant to sit
above what its driver sees on the interposer. Each wire is solved as a 2D
cross-section at its routed layer and width. The section holds every parallel
conductor within 60 um, and signal neighbours count with a Miller factor of 2.
Pads are solved as top-metal discs, and crossings take a grown parallel plate.
The solver, `field2d.py`, is first-order finite elements on a tensor grid in
plain Python, inside a grounded box, so a conductor's own capacitance comes out
at or above the open-domain value. The dielectric is the key's, flat, which
puts oxide in every gap between top-metal wires and reads their coupling high.
Given a load budget and the load outside the interposer, it reads each net
WITHIN or OVER.

`libs.tech/parasitics/fill_halo_widths.json` gives the fill keep-out its halo
grows around a routed net, one width per fill metal: the widest halo at which
the fill still met the PDK's filler and density decks in a density-ladder
measurement on a reference route.

## Ecosystem

IntM4TM2 is designed to compose with:

- an **assembly design kit (ADK)** providing technology-agnostic assembly DRC,
  where IntM4TM2 is addressed through an interposer adapter (`intm4tm2`);
- an **interconnect PDK** providing the chiplet attachment methods
  (Cu-pillar, solder bump, vendor microbumps) as a separate, vendor-swappable
  axis. The interposer itself only carries the fab-side pad openings it
  manufactures. Bump generation (`bump_mirror.py`) therefore requires the
  interconnect PDK on disk (`$INTERCONNECT_PDK_ROOT`, or a sibling checkout
  named `interconnect_pdk/` or `IHP-Interconnect-IntM4TM2/`) to draw the
  3D bodies; it fails loud without it.

## License

Apache License 2.0
