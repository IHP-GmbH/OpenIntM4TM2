# OpenIntM4TM2

KLayout PDK and assembly tooling for the **IHP 130-nm IntM4TM2 aluminum BEOL interposer**.

IntM4TM2 is a passive interposer technology derived from the IHP SG13G2 130 nm
BiCMOS process: all frontend device layers are removed and only the backend
aluminum metal redistribution stack (Metal4 through TopMetal2) is retained,
together with MIM capacitors, thin-film resistors, passivation/pad openings,
edge seal and localized backside etch (LBE).

## Repository layout

| Path | Contents |
|---|---|
| `interposer_klayout/` | KLayout technology (`intm4tm2.lyt`, `intm4tm2.lyp`, `intm4tm2.map`), DRC and LVS runsets, PCells and macros |
| `scripts/` | Assembly utilities: `bump_mirror.py` (bump/pillar mirroring between mating dies), `flip_chip_gds.py` (flip-chip GDS transform) |
| `tests/` | Cu-pillar PCell and DRC integration tests |
| `layout_examples/` | Example layouts |
| `interposer_pdk_layers.csv` | Layer reference table |

## Getting started

Open KLayout and install the technology from `interposer_klayout/tech/intm4tm2.lyt`
(Tools > Manage Technologies > Import Technology). The layer properties file and
LEF/DEF layer mapping are picked up automatically.

DRC:

```bash
python3 interposer_klayout/tech/drc/run_drc.py --path <layout.gds> --topcell <cell>
```

## Ecosystem

IntM4TM2 is designed to compose with:

- an **assembly design kit (ADK)** providing technology-agnostic assembly DRC,
  where IntM4TM2 is addressed through an interposer adapter (`intm4tm2`);
- an **interconnect PDK** providing the chiplet attachment methods
  (Cu-pillar, solder bump, vendor microbumps) as a separate, vendor-swappable
  axis. The interposer itself only carries the fab-side pad openings it
  manufactures.

## License

Apache License 2.0, following the upstream IHP SG13G2 PDK.
