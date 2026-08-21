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
│   │       ├── 3_1_offgrid.drc      # deck: offgrid
│   │       ├── 3_2_angle.drc        # deck: angle
│   │       ├── 5_17_metaln.drc      # deck: metaln
│   │       ├── 5_18_metalnfiller.drc      # deck: metalnfiller
│   │       ├── 5_20_via4.drc        # deck: via4
│   │       ├── 5_21_topvia1.drc     # deck: topvia1
│   │       ├── 5_22_topmetal1.drc   # deck: topmetal1
│   │       ├── 5_23_topmetal1filler.drc   # deck: topmetal1filler
│   │       ├── 5_24_topvia2.drc     # deck: topvia2
│   │       ├── 5_25_topmetal2.drc   # deck: topmetal2
│   │       ├── 5_26_topmetal2filler.drc   # deck: topmetal2filler
│   │       ├── 5_27_passiv.drc      # deck: passiv
│   │       ├── 6_9_pad.drc          # deck: pad
│   │       ├── 6_9_copperpillar.drc # deck: copperpillar
│   │       ├── 6_9_solderbump.drc   # deck: solderbump
│   │       ├── 6_10_sealring.drc    # deck: sealring
│   │       ├── 6_11_mim.drc         # deck: mim
│   │       ├── 7_3_metalslits.drc   # deck: metalslits
│   │       ├── 9_1_lbe.drc          # deck: lbe
│   │       ├── density.drc          # deck: density (opt-in, full-chip only)
│   │       └── interposer_tech_default.json  # tech params (diameters, enclosures)
│   │   └── testing/                 # DRC unit tests (IHP testcases/ convention)
│   │       ├── run_regression.py    # runner: deck vs golden expectation per top cell
│   │       └── testcases/unit/      # <table>.gds + gen_<table>_testcase.py per rule table
│   ├── lvs/                         # Layout vs Schematic
│   │   ├── intm4tm2.lvs             # Top runset; loads the two rule decks
│   │   ├── run_lvs.py               # CLI driver
│   │   ├── rule_decks/
│   │   │   ├── layers_definitions.lvs
│   │   │   └── connectivity.lvs     # Metal-stack connectivity extraction
│   │   └── testing/                 # LVS connectivity unit tests (IHP lvs/testing/ convention)
│   │       ├── run_regression.py    # runner: deck verdict vs golden per fixture
│   │       └── testcases/unit/      # lvs_{clean,open,short}.gds (+ generator)
│   └── macros/
│       ├── interposer_filler_metal.lym     # Metal4/Metal5 filler macro
│       ├── interposer_filler_topmetal.lym  # TopMetal filler macro
│       └── interposer_nofill.lym           # designer no-fill emitter (160/0 -> x/23)
├── python/
│   ├── bump_mirror.py               # Cu-pillar GDS generation + mirroring (CLI)
│   └── fill_closure.py              # Metal4/Metal5 density-feedback fill driver (CLI)
└── intm4tm2_tests/
    ├── test_bump_mirror.py          # pytest suite for bump_mirror
    ├── test_fill_closure.py         # pytest: fill closure converges density into band
    ├── test_filler_metal.py         # pytest: Metal4/Metal5 fill is DRC-clean and in-band
    ├── test_filler_topmetal.py      # pytest: TopMetal fill is DRC-clean and in-band
    ├── test_nofill_emitter.py       # pytest: no-fill emitter geometry + generator honors it
    └── test_layer_parity.py         # pytest: intm4tm2.lyp vs canonical layer list
```

The Cu-pillar DRC and LVS-connectivity checks live with their decks under the
IHP `testing/` convention (`tech/drc/testing/` copperpillar table and
`tech/lvs/testing/`), not as standalone harnesses.

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

`intm4tm2.drc` loads `layers_def.drc` first, then the requested decks. The
available decks and what they check:

| Deck | Rule table | Checks |
| --- | --- | --- |
| `offgrid` | 3.1 | Vertices on the 5 nm manufacturing grid (circles exempt) |
| `angle` | 3.2 | Allowed edge angles (vias: 90; metals: 45/90) and acute corners |
| `metaln` | 5.17 | Metal4/Metal5 width, space, wide-line space, 45-degree bends |
| `metalnfiller` | 5.18 | Metal4/Metal5 filler width and space to drawn metal |
| `via4` | 5.20 | Via4 size, spacing, array spacing, metal enclosure/endcap |
| `topvia1` | 5.21 | TopVia1 size, spacing, enclosures |
| `topmetal1` | 5.22 | TopMetal1 width and space |
| `topmetal1filler` | 5.23 | TopMetal1 filler width and space |
| `topvia2` | 5.24 | TopVia2 size, spacing, enclosures |
| `topmetal2` | 5.25 | TopMetal2 width, space, wide-line recommended space |
| `topmetal2filler` | 5.26 | TopMetal2 filler width and space |
| `passiv` | 5.27 | Passivation opening width, space, enclosure |
| `pad` | 6.9 | Pad recognition consistency, pad-to-sealring distance |
| `copperpillar` | 6.9.2 | Cu-pillar opening size, pitch, enclosure, shape |
| `solderbump` | 6.9.1 | Solder-bump opening size, pitch, enclosure, shape |
| `sealring` | 6.10 | Seal ring integrity, corners, uniqueness, outside structures |
| `mim` | 6.11 | MIM capacitor: width, space, enclosures, per-device min/max area, via coverage, total area |
| `metalslits` | 7.3 | Slit size/coverage on wide metal plates |
| `lbe` | 9.1 | Local back etch size, spacing, keep-outs |
| `density` | - | Global/local metal, slit and LBE density (opt-in) |

With no `--deck`, all decks except `density` run. Density carries global
minimum-density rules that only make sense on a finished full-chip layout, so
it is opt-in via `--density` (or an explicit `--deck density`); its DEN.BND.*
boundary sanity rules are enabled with `--density_sanity`.

```bash
python tech/drc/run_drc.py --path <your_layout.gds>                       # all default decks
python tech/drc/run_drc.py --path <your_layout.gds> --deck pad            # pad only
python tech/drc/run_drc.py --path <your_layout.gds> --deck lbe --deck pad # two decks, merged
python tech/drc/run_drc.py --path <your_layout.gds> --mp 5                # parallel, one worker per deck
python tech/drc/run_drc.py --path <your_layout.gds> --density --density_sanity  # full chip sign-off
```

Other flags: `--topcell` (auto-detected if omitted), `--run_dir` (default: a
timestamped subdir), `--threads` (per-invocation, default 4), `--run_mode`
(`tiling`, `deep`, or `flat`; default `tiling`).

The `assembly` deck is no longer here; if requested, `run_drc.py` redirects you to
the ADK assembly DRC (`adk/klayout/drc/run_drc.py --interposer-adapter <name>`).

### Metal fill

Two KLayout menu macros generate dummy fill to satisfy the density rules:
`tech/macros/interposer_filler_metal.lym` for Metal4/Metal5 and
`interposer_filler_topmetal.lym` for TopMetal1/TopMetal2. Run them on the open
layout (menu `IntM4TM2 Interposer > filler`); the Metal4/Metal5 macro fills the
prBoundary, else the EdgeSeal interior, else the layout extent, and honors drawn
metal, vias, the `nofill` purpose (datatype 23) and NoMetFiller (160/0). The
filler-to-metal and filler-to-filler clearances (`MFil_c`, `TM(n)Fil_c/b`) are
read from `tech/drc/rule_decks/interposer_tech_default.json`, the same file the
sign-off decks use, so the generators and the checker cannot drift apart.

For a batch flow that also verifies the result, `python/fill_closure.py` runs the
Metal4/Metal5 generator, checks the density deck, and shrinks or grows the fill
lattice per metal until both are in band (or the iteration budget is spent):

```bash
python python/fill_closure.py in.gds -o out.gds            # design + closed fill
python python/fill_closure.py in.gds -o out.gds --max-iter 8
```

To keep fill away from RF structures, matched devices, pillar pads or probe areas,
`tech/macros/interposer_nofill.lym` turns a region marked on NoMetFiller (160/0)
into per-metal nofill (`x/23`), grown by a designer-chosen clearance (`-rd
clearance=<um>`, `-rd metals=50,67,126,134`); both generators already honor `x/23`
and `160/0` as absolute keep-outs, so the halo becomes real clearance. Run it before
the generators.

### LVS

LVS extracts metal-stack connectivity, names nets from text labels, and checks
label consistency (opens/shorts). MIM capacitors are extracted as `cap_cmim`
devices: bottom plate on Metal5, top plate on TopMetal1 through the Vmim via
array, parameters `w`/`l`/`A`/`P` and parallel multiplicity `m` (a TopVia1
drawn over the MIM plate counts as a MIM via and does not short the plates; MIM
under an IND recognition region is not extracted as a device). A reference
netlist is optional; when given, KLayout's netlist compare runs in strict port
mode in addition to the label checks -- a device terminal left on a floating
net (e.g. a cap without its via) fails the compare. Reference netlists may
write the device as `C1 PLUS MINUS cap_cmim w=10u l=5u m=1` or in the
value-first form `C1 PLUS MINUS 75f $[cap_cmim] w=10u l=5u m=1`.

```bash
python tech/lvs/run_lvs.py --layout=<your_layout.gds>                          # connectivity-only
python tech/lvs/run_lvs.py --layout=<your_layout.gds> --netlist=<ref.spice>    # with schematic compare
```

Other options: `--run_dir` (default: a timestamped `lvs_run_*` subdir under the
current directory), `--topcell`, `--run_mode` (`flat` or `deep`; default
`deep`), `--no_top_lvl_pins`, `--combine_devices` (merge parallel devices into
one with summed `m` before compare), `--verbose`.

## PCell library: `python/intm4tm2_pycell_lib`

KLayout PCell library for the interposer, served the same way the SG13G2 open
PDK serves its pycell library: the technology macro `tech/pymacros/autorun.lym`
bootstraps `sys.path` (including the vendored `python/pycell4klayout-api` shim
and `python/pypreprocessor`) and imports `intm4tm2_pycell_lib`, which registers
the `pya.Library` named `IntM4TM2` bound to the `intm4tm2` technology. The
vendored trees carry their own upstream licenses (`pycell4klayout-api`
GPL-3.0, `pypreprocessor` MIT), exactly as the SG13G2 PDK ships them.

Cells:

| Cell | Parameters | Output |
|---|---|---|
| `CuPillarPad` | `diameter` (Passiv opening, default `35u` = Table 6.1 Option 1), `passEncl` (TopMetal2 enclosure, default `7.5u`), `addFillerEx` (`nil`/`t`) | TopMetal2 134/0, dfpad:pillar 41/35 and Recog:pillar 99/35 at pad size; Passiv:pillar 9/35 at the opening; optional nofill circles on the interposer metal stack. 256-point circles, the discretization the assembly flow and the copperpillar DRC tolerances assume. |
| `cmim` | `w`, `l` (plate size, default `6.99u`), `C`/`Calculate` (capacitance-driven sizing via the `CbCap` callback; the default `Calculate='w&l'` recomputes `w`/`l` from `C` -- pass `Calculate='C'` to pin `w`/`l`) | MIM 36/0 plate of `w` x `l`; Metal5 67/0 bottom plate (Mim.c enclosure 0.6); TopMetal1 126/0 top plate; Vmim 129/0 via array (0.42 vias, 0.84 spacing, Mim.d enclosure 0.36); TEXT 63/0 labels. Ported from the SG13G2 open PDK `cmim`; `intm4tm2_tests/test_cmim_pcell.py` pins it XOR-identical to the upstream cell (`PDK_ROOT`-gated oracle) and the `mim` DRC deck runs clean on its output. |
| `bondpad` | `shape` (`octagon`/`square`/`circle`, default `octagon`), `padType` (`bondpad`/`probepad`), `diameter` (default `80u`), `topMetal` (`TM1`/`TM2`), `bottomMetal` (`4`/`5`/`TM1`), `stack` (`nil`/`t`), `addFillerEx` (`nil`/`t`), `passEncl`, `hwquota` | Top-metal pad (TopMetal2 134/0 or TopMetal1 126/0) with a Passiv 9/0 opening inset by the enclosure (`Pas_c`, or `passEncl` for `probepad`). `bondpad` draws a dfpad 41/0 marker over the pad so bond-pad rules apply -- the deliberate complement of `CuPillarPad`, which rides the pillar purpose to avoid them; `probepad` draws none. `stack='t'` adds Metal4/Metal5/TopMetal1 rings tied together with Via4 66/0, TopVia1 125/0 and TopVia2 133/0 arrays; `addFillerEx='t'` adds nofill exclusion on the interposer metals. Reduced from the SG13G2 open PDK `bondpad` to the passive BEOL stack (no FEOL layers); `intm4tm2_tests/test_bondpad_pcell.py` pins the drawn layer set and proves no base-PDK layer leaks. |

The cmim device is supported across the full stack: ngspice simulation model
with corners (`../ngspice/models/`, subckt `cap_cmim`), xschem symbol
(`../xschem/intm4tm2_pr/cap_cmim.sym`, KiCad counterpart specified in
`../kicad/symbols/README.md`), LVS device extraction (`cap_cmim`, see LVS
above) and the complete MIM rule table in the `mim` DRC deck.

Programmatic use (headless):

```python
import pya
tech = pya.Technology.create_technology('intm4tm2')
tech.load('tech/intm4tm2.lyt')
# with python/ and python/pycell4klayout-api/source/python on sys.path:
import intm4tm2_pycell_lib
layout = pya.Layout()
layout.technology_name = 'intm4tm2'
cell = layout.create_cell('CuPillarPad', 'IntM4TM2',
                          {'diameter': '35u', 'passEncl': '7.5u'})
```

The PCell is the single source of the Cu-pillar pad fabrication geometry:
`bump_mirror.CuPillarGenerator` is a thin placer that instantiates
`CuPillarPad` (flattened to static geometry in the output GDS) and only adds
the 3D interconnect bodies itself.
`intm4tm2_tests/test_cupillar_pcell_parity.py` remains as the regression that
pins the placed output to a directly-instantiated PCell (per-layer XOR empty
for every Table 6.1 option); parameter values keep coming from the
interconnect PDK manifest in the assembly flow.

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

Two pytest suites in `intm4tm2_tests/`, plus the DRC and LVS rule-deck regressions
that live with their decks under the IHP `testing/` convention.

```bash
# pytest suites (bump_mirror + layer-map parity):
pytest intm4tm2_tests/test_bump_mirror.py
pytest intm4tm2_tests/test_layer_parity.py

# DRC rule-deck unit tests (IHP testcases/ convention): run the regression on the
# committed testcase GDS. One table per rule deck (via4, metaln, mim, copperpillar,
# solderbump, sealring, fillers, metalslits, lbe, pad, offgrid, angle, density, ...);
# each table has a <table>_viol and a <table>_clean top cell compared against the
# GOLDEN expectations. Regenerate a testcase with its gen_*.py before editing it.
python tech/drc/testing/run_regression.py                 # all tables
python tech/drc/testing/run_regression.py --table copperpillar

# LVS connectivity unit tests (IHP lvs/testing/ convention): run the LVS deck on the
# committed clean/open/short fixtures and compare the verdict against the golden.
python tech/lvs/testing/run_regression.py                 # all fixtures
python tech/lvs/testing/run_regression.py --case open
```
