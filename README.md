# OpenIntM4TM2

KLayout PDK and assembly tooling for the **IHP 130-nm IntM4TM2 aluminum BEOL interposer**.

IntM4TM2 is a passive interposer technology derived from the IHP SG13G2 130 nm
BiCMOS process: all frontend device layers are removed and only the backend
aluminum metal redistribution stack (Metal4 through TopMetal2) is retained,
together with MIM capacitors, thin-film resistors, passivation/pad openings,
edge seal and localized backside etch (LBE).

## Repository layout

The repository follows the directory organization of the IHP open PDKs
(e.g. `ihp-sg13g2`, `ihp-sg13cmos5l`).

| Path | Contents |
|---|---|
| `libs.tech/klayout/tech/` | KLayout technology (`intm4tm2.lyt`, `intm4tm2.lyp`, `intm4tm2.map`), DRC and LVS runsets, macros |
| `libs.tech/klayout/python/` | Assembly tooling: `bump_mirror.py`, a standalone Cu-pillar pad generator that DRC pre-validates pin lists against the interposer rules before writing GDS |
| `libs.tech/klayout/intm4tm2_tests/` | Cu-pillar PCell, DRC and LVS connectivity tests |
| `libs.tech/kicad/` | KiCad template board for assembly designs (copper layers pre-named after the PDK metals) |
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
