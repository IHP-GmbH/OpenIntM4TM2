# IntM4TM2 KLayout PDK

The KLayout side of the OpenIntM4TM2 PDK: the technology files, the DRC and LVS
runsets, the Cu-pillar assembly tooling, and the tests. The target is the IHP
130-nm IntM4TM2 aluminum BEOL interposer, a passive interposer derived from
SG13G2 that keeps only the Metal4..TopMetal2 backend stack. Beyond bare metal it
also carries MIM capacitors, thin-film resistors, passivation and pad openings,
edge seal, and LBE, so the layer set is richer than the routing metals alone.

This PDK composes with two siblings:

- The ADK supplies the assembly DRC; the former `assembly` deck was promoted out
  of this PDK. Run it via `adk/klayout/drc/run_drc.py --interposer-adapter <name>`.
- A separate interconnect PDK (`$INTERCONNECT_PDK_ROOT`) supplies the 3D bump and
  pillar bodies that the assembly tooling draws. See `bump_mirror.py` below.

## Directory Structure

```
libs.tech/klayout/
├── tech/
│   ├── intm4tm2.lyp                 # Layer properties (colors, patterns, GDS layer/datatype)
│   ├── intm4tm2.lyt                 # Technology file (metal-stack connectivity)
│   ├── intm4tm2.map                 # EDI/stream layer mapping
│   ├── intm4tm2_layers.txt          # Canonical layer list (parity golden for tests)
│   ├── drc/                         # Design Rule Check
│   │   ├── intm4tm2.drc             # Top runset; loads layers_def + selected decks
│   │   ├── run_drc.py               # CLI driver (deck selection, parallel runs)
│   │   └── rule_decks/
│   │       ├── layers_def.drc       # Layer definitions (always loaded first)
│   │       ├── 5_20_via4.drc        # deck: via4
│   │       ├── 5_21_topvia1.drc     # deck: topvia1
│   │       ├── 5_24_topvia2.drc     # deck: topvia2
│   │       ├── 5_27_passiv.drc      # deck: passiv
│   │       ├── 6_9_pad.drc          # deck: pad
│   │       ├── 6_9_copperpillar.drc # deck: copperpillar
│   │       ├── 9_1_lbe.drc          # deck: lbe
│   │       └── interposer_tech_default.json  # tech params (diameters, enclosures)
│   │   └── testing/                 # DRC unit tests (IHP testcases/ convention)
│   │       ├── run_regression.py    # runner: deck vs golden expectation per top cell
│   │       └── testcases/unit/      # via4.gds (+ gen_via4_testcase.py generator)
│   ├── lvs/                         # Layout vs Schematic
│   │   ├── intm4tm2.lvs             # Top runset; loads the two rule decks
│   │   ├── run_lvs.py               # CLI driver
│   │   └── rule_decks/
│   │       ├── layers_definitions.lvs
│   │       └── connectivity.lvs     # Metal-stack connectivity extraction
│   └── macros/
│       └── interposer_filler_topmetal.lym  # TopMetal filler macro
├── python/
│   └── bump_mirror.py               # Cu-pillar GDS generation + mirroring (CLI)
└── intm4tm2_tests/
    ├── test_bump_mirror.py          # pytest suite for bump_mirror
    ├── test_layer_parity.py         # pytest: intm4tm2.lyp vs canonical layer list
    ├── cupillar_pcell_harness.py    # script harness (--generate / --validate-drc)
    └── lvs_connectivity_harness.py  # script harness (--generate / --validate-lvs)
```

## Technology files

Core files KLayout reads to display, route, and stream the interposer.

| File            | Purpose                                                                 |
| --------------- | ----------------------------------------------------------------------- |
| `intm4tm2.lyp`  | Layer properties: colors, fill patterns, and the GDS layer/datatype map. Defines the 158 layer entries of the IHP IntM4TM2 module layer map (Metal4/5, TopMetal1/2, Via4, TopVia1/2, MIM, Passiv, dfpad, Recog, EdgeSeal, LBE, ThinFilmRes, NoMetFiller, NoRCX, BackMetal1, BackPassiv, IC, prBoundary, Exchange0-4, instance, plus probe/fill/text variants). |
| `intm4tm2_layers.txt` | The canonical layer list (`name purpose layer datatype`, one row per entry). `intm4tm2_tests/test_layer_parity.py` asserts the `.lyp` matches it exactly; update both together when the layer set changes. |
| `intm4tm2.lyt`  | Technology file. Declares the metal-stack connectivity used for net tracing: `Metal4-Via4-Metal5`, `Metal5-TopVia1-TopMetal1`, `TopMetal1-TopVia2-TopMetal2`. Tech name `intm4tm2`, dbu 0.001. |
| `intm4tm2.map`  | EDI stream layer mapping for the IHP-derived interposer backend. Covers Metal4/5 and TopMetal1/2 routing plus the via and pad/recog layers. `COMP`/`DIEAREA` stream on prBoundary (235/0, 235/4). |

## Design verification

Both runsets are functional. They are driven by their Python CLI wrappers, not by
invoking the `.drc`/`.lvs` decks directly.

### DRC

`intm4tm2.drc` loads `layers_def.drc` first, then the requested decks. The seven
available decks are `passiv`, `pad`, `copperpillar`, `via4`, `topvia1`, `topvia2`,
`lbe`. With no `--deck`, all of them run.

```bash
python tech/drc/run_drc.py --path <your_layout.gds>                       # all decks
python tech/drc/run_drc.py --path <your_layout.gds> --deck pad            # pad only
python tech/drc/run_drc.py --path <your_layout.gds> --deck lbe --deck pad # two decks, merged
python tech/drc/run_drc.py --path <your_layout.gds> --mp 5                # parallel, one worker per deck
```

Other flags: `--topcell` (auto-detected if omitted), `--run_dir` (default: a
timestamped subdir), `--threads` (per-invocation, default 4), `--run_mode`
(`tiling`, `deep`, or `flat`; default `tiling`).

The `assembly` deck is no longer here; if requested, `run_drc.py` redirects you to
the ADK assembly DRC (`adk/klayout/drc/run_drc.py --interposer-adapter <name>`).

### LVS

The interposer is a passive backend stack, so LVS extracts metal-stack
connectivity, names nets from text labels, and checks label consistency
(opens/shorts). A reference netlist is optional; when given, KLayout's netlist
compare runs in addition to the label checks.

```bash
python tech/lvs/run_lvs.py --layout=<your_layout.gds>                          # connectivity-only
python tech/lvs/run_lvs.py --layout=<your_layout.gds> --netlist=<ref.spice>    # with schematic compare
```

Other options: `--run_dir` (default: a timestamped `lvs_run_*` subdir under the
current directory), `--topcell`, `--run_mode` (`flat` or `deep`; default
`deep`), `--no_top_lvl_pins`, `--verbose`.

## Assembly tooling: `python/bump_mirror.py`

Cu-pillar GDS generator with DRC pre-validation. It places and mirrors Cu pillars
for chiplet attachment, optionally auto-resolving DRC collisions by shifting
pillars within a budget.

```bash
# Validate only (no GDS output):
python python/bump_mirror.py --pins U1=pins_u1.json U2=pins_u2.json \
    --chiplet design.chiplet --validate-only --report report.json

# Generate Cu-pillar GDS:
python python/bump_mirror.py --pins U1=pins_u1.json --position U1=1000,2000 -o cupillars.gds
```

Key arguments:

- `--pins REF=FILE` (required, repeatable): pin-list JSON per device.
- `--position REF=X,Y` or `--chiplet FILE` (mutually exclusive, one required):
  device positions in um, or a chiplet YAML that supplies them.
- `--rotation REF=DEG`: device rotations in degrees.
- `--diameter UM`, `--enclosure UM`: override the passiv opening and TM2 enclosure.
- `--validate-only`: run DRC validation only, no GDS.
- `--auto-resolve` / `--no-auto-resolve`: auto-resolve is ON by default; the
  no-form fails on any DRC violation.
- `--max-displacement UM`: per-pillar shift budget for auto-resolve (default 10).
- `--auto-resolve-best-effort`: emit GDS even if auto-resolve does not converge.
- `--report FILE`: write a JSON validation report.
- `-o`, `--output FILE`: output GDS path (default `cupillars.gds`).

Interconnect PDK requirement: drawing the 3D pillar and bump bodies needs the
interconnect PDK. `bump_mirror.py` resolves it via `$INTERCONNECT_PDK_ROOT` first,
then a sibling checkout named `interconnect_pdk` or `IHP-Interconnect-IntM4TM2`. If
none is found it raises `RuntimeError ("interconnect_pdk not found")` rather than
emitting pads without their bodies.

## Tests

In `intm4tm2_tests/`. Two pytest suites and two script harnesses; the harnesses are
CLI tools (not `test_`-prefixed), so pytest does not collect them.

```bash
# pytest suites (bump_mirror + layer-map parity):
pytest intm4tm2_tests/test_bump_mirror.py
pytest intm4tm2_tests/test_layer_parity.py

# Cu-pillar PCell harness: generate fixtures, then validate with DRC:
python intm4tm2_tests/cupillar_pcell_harness.py --generate
python intm4tm2_tests/cupillar_pcell_harness.py --validate-drc

# DRC rule-deck unit tests (IHP testcases/ convention): run the regression on the
# committed testcase GDS. Exercises V4.b1 (large-array spacing) and V4.c1/M5.c1
# (endcap enclosure). Regenerate a testcase with its gen_*.py before editing it.
python tech/drc/testing/run_regression.py           # all tables
python tech/drc/testing/run_regression.py --table via4

# LVS connectivity harness: generate fixtures, then validate with LVS:
python intm4tm2_tests/lvs_connectivity_harness.py --generate
python intm4tm2_tests/lvs_connectivity_harness.py --validate-lvs
```
