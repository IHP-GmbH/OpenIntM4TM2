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
│   ├── macros/
│   │   ├── interposer_filler_metal.lym     # Metal4/Metal5 filler macro
│   │   ├── interposer_filler_topmetal.lym  # TopMetal filler macro
│   │   ├── interposer_filler_slit.lym      # stress-relief slit generator (wide plates -> x/24)
│   │   ├── interposer_nofill.lym           # designer no-fill emitter (160/0 -> x/23)
│   │   └── interposer_density_report.lym   # one-click density report (menu)
│   └── scripts/
│       └── sealring.py                     # add the seal ring to a layout (headless)
├── python/
│   ├── bump_mirror.py               # Cu-pillar GDS generation + mirroring (CLI)
│   ├── density_report.py           # density report: global + LBE + slit adequacy (CLI/menu)
│   ├── drc_margins.py               # DRC margin list: thinnest distance per rule (CLI)
│   ├── drc_margins.json             # its table: rule -> key, floor offset, not-measured reasons
│   └── fill_closure.py              # fill driver: fill_stack + 4-metal density closure (CLI)
└── intm4tm2_tests/
    ├── test_bump_mirror.py          # pytest suite for bump_mirror
    ├── test_density_report.py       # pytest: density report matches the deck math
    ├── test_drc_margins.py          # pytest: margin list reads known distances, floors match the deck
    ├── test_fill_closure.py         # pytest: fill closure converges density into band
    ├── test_fill_stack.py           # pytest: fill_stack fills all four metals, both modes
    ├── test_filler_metal.py         # pytest: Metal4/Metal5 fill keeps metalnfiller and M4/M5 density clean, density in band
    ├── test_filler_slit.py          # pytest: slit generator clears Slt.c/Slt.i and 7.3 geometry
    ├── test_filler_topmetal.py      # pytest: TopMetal fill keeps TMnFil.b clean, density in band
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

### Configure and run in KLayout

With the PDK technology installed, open **IntM4TM2 Interposer > Verification >
Interposer DRC… / Interposer LVS…**. Each dialog combines options and execution;
**Save options** remembers settings in KLayout's configuration, and **Reset
options** restores the PDK defaults. These settings are separate for DRC and LVS
and do not change the decks or another PDK's options.

1. Open the **interposer-only** GDS/OASIS with technology `intm4tm2`, select its
   top cell and save any edits. The dialogs refuse unsaved/modified layouts.
2. Choose a results parent and Python interpreter that can import `klayout.db`
   (for example your toolchain venv's `bin/python`). `klayout` must be on PATH.
   Leave the top-cell field blank to use the selected cell, or name another cell
   in the layout. Each run creates a new child directory; existing results are
   never deleted or reused.
3. For DRC select the default physical rules (**density excluded**), all rules
   **including density and boundary sanity**, or one named deck. Full mode
   always enables density boundary sanity. Only one deck runs at a time; the
   thread count is configurable. Density requires a suitable full-chip boundary.
4. For LVS choose **Reference SPICE comparison** and supply the intended
   interposer schematic, or explicitly choose **Labeled connectivity only**.
   A missing reference never silently changes a comparison into connectivity.
5. Run, inspect the status and log, then **Open report**. KLayout's own marker/LVS
   browser opens against the original saved layout, even if another tab is now
   active. If the layout/reference changed or that view closed, attachment is
   refused; the evidence remains on disk. Connectivity-only has an extracted
   netlist and log, not a schematic-comparison database.

The current dialog runner targets Linux/POSIX hosts. The dialog stays responsive.
**Cancel run** stops only that dialog's process
group; cancel or finish a run before closing its dialog or exiting KLayout.
LVS deck output is buffered until completion, so a quiet live log is not a
completion or failure signal. `run.log` records the command, input hashes, scope
and runner output. PASS is restricted to the selected scope, not fabrication
signoff. LVS models the drawing/pin metal stack and MIM devices; filler `/22`,
slits `/24`, resistor and inductor extraction are **not** covered electrically.

### Shared invocation contract (menu, Mosaic and automation)

Use the same existing Python runners in all callers, with an unused output
directory and explicit top cell. Do not infer PASS from a bare KLayout process
exit or recreate the runset command line in the GUI. Paths below are relative
to `libs.tech/klayout`; `<python>` must supply `klayout.db`.

```text
<python> tech/drc/run_drc.py --path carrier.gds --topcell TOP --run_dir NEW_DIR --run_mode flat --mp 1 --threads 2 --density --density_sanity
<python> tech/lvs/run_lvs.py --layout carrier.gds --topcell TOP --netlist intended_interposer.cir --run_dir NEW_DIR --run_mode deep
```

| Check | Evidence | Verdict and scope |
| --- | --- | --- |
| Interposer DRC | `<layout-stem>_<top>_full.lyrdb` for multiple decks; `<layout-stem>_<top>_<deck>.lyrdb` for one | Exit 0: no markers in requested rules. 1: markers found, or the run was refused before any deck ran (see the log). 2: no verdict; a deck crashed, a report is missing or unreadable, an option was not recognised, or the run failed unexpectedly (see the log). |
| Interposer LVS | `<layout-stem>.lvsdb`, `<layout-stem>_extracted.cir`, `<layout-stem>.log` | Exit 0: scoped completed PASS; 1: open/short/reference mismatch; 2: invalid input, execution error, incomplete evidence or zero labeled nets. |
| Assembly DRC | ADK-owned runner/report | Separate check, not provided by these PDK menus. |
| Assembly LVS | Assembly contract plus its runner/report | Separate check, not a synonym for interposer netlist comparison. |

Interposer DRC report categories are the deck's rule identifiers (for example
`TM2.a`, `DEN.*`), **not** the assembly report's `ASM.*`/`IXN.*` prefixes. Readers
must preserve the actual rule names rather than filter for assembly prefixes.
LVS is a KLayout LVS database, not a DRC category report. The wrapper validates
the runset's unique final `INTERPOSER_LVS_RESULT mode=... status=... labeled_nets=...
opens=... shorts=...` record, completion and fresh nonempty outputs. In
connectivity-only mode omit `--netlist`, retain that restricted scope, and do not
expect `.lvsdb`. Reference SPICE must describe intended **interposer** connectivity
and supported devices; neither a die CDL nor a `.chiplet`/assembly contract is
automatically a valid reference. A schematic extracted from the same input is
not an independent design-intent check.

Unwired integrations, absent reference netlists and unavailable checks must stay
unavailable/inconclusive, never PASS. The GUI wiring does not by itself wire
these checks into Mosaic or a full-chip flow.

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
| `sealring` | 6.10 | Seal ring integrity, ring and Passiv ring widths, corners, uniqueness, outside structures |
| `mim` | 6.11 | MIM capacitor: width, space, enclosures, per-device min/max area, via coverage, total area |
| `metalslits` | 7.3 | Slit size/coverage on wide metal plates |
| `lbe` | 9.1 | Local back etch size, spacing, keep-outs |
| `density` | - | Global/local metal, slit and LBE density (opt-in) |

With no `--deck`, all decks except `density` run. Density carries global
minimum-density rules that only make sense on a finished full-chip layout, so
it is opt-in via `--density` (or an explicit `--deck density`); its DEN.BND.*
boundary sanity rules are enabled with `--density_sanity`.

Without a seal ring, Seal.l is skipped (it needs 39/4) and the rules that
measure to EdgeSeal (Seal.n, Pas.c, Pad.dR, Padc.d, Padb.d, LBE.d) have nothing
to measure and pass. For a layout that must carry one, `--require_sealring`
(`-rd require_sealring=true`) turns on the SEAL.REQ.* rules of the `sealring`
deck:

| Rule | Fails when |
| --- | --- |
| `SEAL.REQ.1` | no EdgeSeal 39/0 is drawn |
| `SEAL.REQ.2` | no sealring boundary 39/4 is drawn (Seal.l cannot run) |
| `SEAL.REQ.3_<L>` | EdgeSeal (`ES`) or a ring layer inside it (`M4`, `V4`, `M5`, `TV1`, `TM1`, `TV2`, `TM2`) is not a closed loop; a point contact or a neck of 0.18 um or less counts as open. A slit is accepted: every metal and via ring cut once by at most 2.81 um, every via ring under both of its metals (so the cuts line up), EdgeSeal closed |
| `SEAL.REQ.4` | a fabricated shape (metal, filler or mask data, via, pad opening, MIM, ThinFilmRes, LBE, or Passiv other than the ring around EdgeSeal) lies outside the ring; checked only once EdgeSeal is closed. Metal4 filler 50/22 is allowed in the corners that a stepped Passiv ring cuts off its bounding box, within Seal.k of the box corners, where IHP-made rings carry their 2 x 2 um version marks |

These are not SG13G2 rules. The run stops with an error if the value is not
`true` or `false` (an empty value included), or if the sealring deck is not
part of the run.

With or without the define, a closed EdgeSeal ring is measured by the SG13G2
ring rules ported to this stack: `Seal.a_<L>` (Metal4..TopMetal2 at least
3.50 um), `Seal.c1.Via4`, `Seal.c2` and `Seal.c3` (each via ring exactly its
width), `Seal.e` (Passiv ring at least 4.20 um) and `Seal.f.<L>` (Passiv ring
at least 1.00 um from the ring metals). The via offset (Seal.d, measured to
Activ in SG13G2), the 30 um from EdgeSeal to the boundary and the spacing from
design metal to the ring are not checked.

```bash
python tech/drc/run_drc.py --path <your_layout.gds>                       # all default decks
python tech/drc/run_drc.py --path <your_layout.gds> --deck pad            # pad only
python tech/drc/run_drc.py --path <your_layout.gds> --deck lbe --deck pad # two decks, merged
python tech/drc/run_drc.py --path <your_layout.gds> --mp 5                # parallel, one worker per deck
python tech/drc/run_drc.py --path <your_layout.gds> --density --density_sanity  # full deck including density
python tech/drc/run_drc.py --path <your_layout.gds> --require_sealring    # default decks, seal ring required
```

Other flags: `--topcell` (auto-detected if omitted), `--run_dir` (default: a
timestamped subdir), `--threads` (per KLayout invocation, default 4, so `--mp N`
can use N times that; it speeds up the tiled operations, the density windows above
all), `--run_mode` (`tiling`, `deep` or `flat` are accepted, but every deck runs flat
whatever it says).

The `assembly` deck is no longer here; if requested, `run_drc.py` redirects you to
the ADK assembly DRC (`adk/klayout/drc/run_drc.py --interposer-adapter <name>`).

### Metal fill

Two KLayout menu macros generate dummy fill to satisfy the density rules:
`tech/macros/interposer_filler_metal.lym` for Metal4/Metal5 and
`interposer_filler_topmetal.lym` for TopMetal1/TopMetal2. Run them on the open
layout (menu `IntM4TM2 Interposer > filler`); both fill the prBoundary, else the
layout extent, cut to the EdgeSeal (39/0) interior when a seal ring is drawn, and
honor drawn metal, vias, the `nofill` purpose (datatype 23) and NoMetFiller
(160/0). With a ring they stop, rather than fill, when the ring has no interior,
when their own drawn metal lies outside it, or when prBoundary misses it. The
filler-to-metal and filler-to-filler clearances (`MFil_c`, `TM(n)Fil_c/b`) are
read from `tech/drc/rule_decks/interposer_tech_default.json`, the same file the
sign-off decks use, so the generators and the checker cannot drift apart.

For a batch flow that also verifies the result, `python/fill_closure.py` runs both
generators, checks the density deck, and adjusts each metal's lattice until all four
are in band (or the iteration budget is spent): Metal4/Metal5 densify or relax, and
TopMetal relaxes to hold under its 70% cap (it is already at maximum density, so an
under-band TopMetal is a drawn-metal issue, reported not chased):

```bash
python python/fill_closure.py in.gds -o out.gds            # design + closed fill
python python/fill_closure.py in.gds -o out.gds --max-iter 8
python python/fill_closure.py in.gds -o out.gds --threads 8   # density deck threads
```

By default the density deck runs on 4 threads, or on fewer when the process may use
fewer CPUs (its affinity set, or the CPU quota of its container or systemd scope);
`--threads` (or `threads=` in Python) sets the count outright.

For a single call that fills the whole stack (Metal4, Metal5, TopMetal1, TopMetal2),
`fill_closure.fill_stack(design, output, topcell="INTERPOSER", mode="single-pass"|"closure", threads=None)`
is the in-process entry point (used by the KiCad chiplet_export plugin). It merges each
generator's output, honors keep-outs already in the design (`<metal>/23`, `160/0`), is
safe when `output` equals the input, and returns a per-metal coverage/band/state report;
`single-pass` grades by area (no deck), `closure` deck-verifies all four metals. When a
generator stops (see above), it raises `RuntimeError` with the generator's reason.

To keep fill away from RF structures, matched devices, pillar pads or probe areas,
`tech/macros/interposer_nofill.lym` turns a region marked on NoMetFiller (160/0)
into per-metal nofill (`x/23`), grown by a designer-chosen clearance (`-rd
clearance=<um>`, `-rd metals=50,67,126,134`); both generators already honor `x/23`
and `160/0` as absolute keep-outs, so the halo becomes real clearance. Run it before
the generators.

### Slits

Wide power/ground planes need stress-relief slits: rule Slt.c flags any metal wider
than 30 um that carries no slit, and Slt.i demands at least 6% slit coverage on any
plate bigger than 35 x 35 um. `tech/macros/interposer_filler_slit.lym` cuts a grid of
small square slits (`x/24`) into every wide drawn plate on Metal4, Metal5, TopMetal1
and TopMetal2. It opens the metal by Slt_c/2 to catch every plate Slt.c flags (a
superset of the Slt.i plates, so one pass clears both), sizes the cells so the 7.3
geometry rules (Slt.a min width, Slt.b max edge, Slt.f enclosure, Slt.h via spacing)
hold by construction, keeps slits off pads, probe recognition, MIM and the designer's
`x/23` / `160/0` keep-outs, and anchors the lattice per plate so the per-plate ratio
clears the 6% floor with margin (default ~10-13%; `-rd slit_gap=<um>` tunes it, clamped
non-negative). Run it before the fill generators, which treat `x/24` as a keep-out.

### Density report

`python/density_report.py` measures the same quantities the density deck computes,
global Metal4/Metal5/TopMetal1/TopMetal2 coverage, LBE coverage and per-plate slit
adequacy, and prints them as a plain table (thresholds from the same tech JSON, so it
cannot drift from sign-off):

```bash
python python/density_report.py in.gds --topcell INTERPOSER   # human-readable table
python python/density_report.py in.gds --json                 # raw dict
```

`tech/macros/interposer_density_report.lym` is the one-click menu entry point: it runs
the report on the active layout and shows it in a dialog. The windowed local-density
rules (MnFil.h/k) and the authoritative pass/fail still come from running the density
deck (`run_drc.py --density`).

### DRC margins

A clean DRC run writes no markers, so it does not say how close the layout comes to each
rule. `python/drc_margins.py` runs a copy of the deck with each minimum-distance rule of
`python/drc_margins.json` widened by a window (25% of the rule value, between 50 nm and
5 um) and lists per rule the floor, the thinnest distance inside the window, the margin
in nm and as a share of the floor, the number of pairs inside the window, and its
location, smallest margin first. A rule the deck flags whatever the distance (a pad or
Passiv opening or a MIM not wholly on its metal, a slit over a MIM) reads "flagged" and
comes first; `flagged_shapes` in `margins.json` counts those shapes. The rules it does
not measure (exact sizes, maxima, areas, derived geometry, shapes) are listed with the
reason, and on a layout without a seal ring the rules measured against it read "no seal
ring". The original deck is not touched, and the list never decides pass or fail. It
needs the `klayout` binary on PATH; `--threads N` hands the copy N threads.
`margins.json` carries a `format` number that moves when one of its fields is renamed,
dropped or changes meaning.

```bash
python python/drc_margins.py in.gds -o out_dir --topcell INTERPOSER   # margins.txt, margins.json
```

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
| `sealring` | `l`, `w` (outline size in X and Y, strings such as `5000.000u` on the 5 nm grid, each at least 114.25 um; the whole outline, unlike the SG13G2 `sealring` whose `l`/`w` leave out its edge box), `addSlit` (`nil`/`t`) | Origin at the outline's lower-left corner. EdgeSeal.boundary 39/4 fills the outline (the sealring boundary; the fab adds the scribe outside it). The ring is drawn as the open SG13G2 magic seal-ring generator draws it, 30 um inside the outline: EdgeSeal 39/0 plus Metal4 50/0, Metal5 67/0, TopMetal1 126/0 and TopMetal2 134/0, 3.5 um wide on the straight sides (Seal.a); Via4 66/0, TopVia1 125/0 and TopVia2 133/0 as continuous rings 1.30 um from the outer edge (Seal.c1/c2/c3 widths, Seal.d offset); a Passiv 9/0 ring 4.2 um wide (Seal.e) and 3.0 um outside it, above the 1.00 Seal.f floor. Each corner is a four-step Manhattan staircase in a Seal.k square, the same piece turned at every corner. `addSlit='t'` cuts the metals at the middle of the left side by their minimum space (Mn.e, TM1.b, TM2.b) and the via rings by 2.81 um; EdgeSeal and Passiv stay closed. TEXT 63/0 labels give the registration size and area and the PDK commit. Widths and spaces are read from `tech/drc/rule_decks/interposer_tech_default.json`; a missing or off-grid value, or a bad outline or flag, leaves the cell empty. `intm4tm2_tests/test_sealring_pcell.py` checks the geometry, XORs it against the rings of the reference interposer layouts in `libs.ref/intm4tm2_examples`, measures the slit, runs the default decks on it and the seal deck with the presence rules on it and on every example ring, and fills a carrier with it (fill stays inside prBoundary and the ring interior; the seal, filler and density decks pass). |

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

To add the ring to a layout without the GUI, `tech/scripts/sealring.py` takes
the arguments of the SG13G2 script of the same name, the outline size in um,
plus the slit:

```bash
klayout -zz -r tech/scripts/sealring.py -rd length=1000 -rd width=800 \
        -rd input=<layout.gds> -rd offset_x=0 -rd offset_y=0 -rd slit=false \
        -rd output=<with_ring.gds>
```

Without `input` it writes the ring alone under a `sealring_top` cell; with it,
the ring goes into that layout's only top cell, its outline's lower-left corner
at the offset. Sizes and offsets must be on the 5 nm grid. The PCell comes from
this tree; the library or a module it imports loaded first from another tree
is refused (`-rx` skips the autorun macros that load them), and so is a
`KLAYOUT_LYP_FILE` other than this tree's `tech/intm4tm2.lyp`, which would
give the library another layer table. The output carries no PCell context. The script exits 0 after one `sealring: wrote` line
and 1 after one `sealring: error:` line (`intm4tm2_tests/test_sealring_script.py`).

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

Pytest suites live in `intm4tm2_tests/`; the DRC and LVS rule-deck regressions
live beside their decks under the IHP `testing/` convention. Use a virtual
environment with `pytest` and `klayout.db`, plus the native `klayout` binary.
The menu integration test launches its own offscreen Qt application with
isolated settings; it does not edit the user's configuration or current layout.

A bare `pytest` starts no external tool. The cases that run the `klayout` binary,
ngspice or xschem carry the `live_tool` marker and are deselected by the default
options in `pyproject.toml`; `pytest -m live_tool` runs them, and `pytest -m ""`
runs every case. The routed-load field solves that take more than about 1.5 s at
one CPU carry `slow` and are deselected the same way; `pytest -m slow` runs them.
The `test_bump_mirror.py` cases that read gds2kicad or the
interconnect PDK take each sibling from `GDS_TO_KICAD_ROOT` and
`INTERCONNECT_PDK_ROOT`, or else from `$HCDP_ROOT/gds_to_kicad` and
`$HCDP_ROOT/interconnect_pdk`. With neither set they skip, and a root that does not
hold the sibling fails.

```bash
# pytest suites (bump_mirror + layer-map parity):
pytest intm4tm2_tests/test_bump_mirror.py
pytest intm4tm2_tests/test_layer_parity.py

# Menu configuration/verdict boundaries and real menu/run/report integration
# (-m "" keeps the live klayout case):
python -m pytest -q -m "" intm4tm2_tests/test_verification_menu.py \
  intm4tm2_tests/test_lvs_runner.py intm4tm2_tests/test_drc_report_completeness.py \
  intm4tm2_tests/test_verification_gui.py

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
