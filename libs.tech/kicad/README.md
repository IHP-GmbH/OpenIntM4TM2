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
   in its board format). Copy `interposer_template.kicad_dru` and
   `interposer_template.kicad_pro` beside it under the same name,
   `<board>.kicad_dru` and `<board>.kicad_pro`, so KiCad checks the metal rules
   and uses the board setup and netclasses below.
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

## Board thickness and stackup

The board thickness is the interposer body: the silicon substrate plus the
back-end up to the top of TopMetal2, both taken from
`libs.tech/intm4tm2_stack.json`. The exporter reads it as the interposer's
`dimensions.thickness`.

KiCad places a dielectric only between two copper layers, and `B.Cu` (Metal4)
is the bottom of the back-end, so the silicon sits in the one slot below it,
`B.Mask`. That item carries the substrate plus the base oxide under Metal4. The
copper items and the three oxide dielectrics are the real back-end thicknesses
at KiCad's 1 nm resolution, and the dielectrics are locked. `F.Mask` is 0: the
passivation above TopMetal2 is not part of the body.

The Physical Stackup page in Board Setup writes the stackup sum back over the
board thickness, so the stackup has to keep summing to the body. Keep `B.Mask`
enabled under Board Editor Layers (disabling it drops the silicon), and do not
redistribute the dielectric thicknesses. `test_body_thickness.py` checks the
template against the key.

## Design rules (`interposer_template.kicad_dru`, `interposer_template.kicad_pro`)

KiCad reads its custom design rules from the `.kicad_dru` file named after the
board. The PDK ships one for the template, written by
`scripts/kicad_rules_gen.py` from
`../klayout/tech/drc/rule_decks/interposer_tech_default.json`, the file the
KLayout decks read. It carries the minimum track width and the minimum
clearance of each metal (TM2.a/b, TM1.a/b, M5.a/b, M4.a/b), on the copper layer
the template names after that metal, and two width-dependent rules that rest
on one item's own size:

- TM2.bR as a TopMetal2 clearance from another net's track wider than TM2_bR_w
  and from a pad wider than TM2_bR_w and longer than TM2_bR_cr (a narrower pad
  is no wide line, and a shorter one cannot hold a parallel run that long).
  Both are warnings and physical clearances, a check of their own: KiCad gives
  a pair the severity of the last rule that matches it, so as a plain
  clearance either warning would take over a TM2.b error beside it. Both hold
  TopMetal2 copper to other TopMetal2 copper only: a physical clearance also
  holds copper to the board outline, whose drawn stroke the deck does not see.
  KiCad 9.99 reads an arc as a track here. The pad rule's length gate fits
  rectangular pads. A round pad, or an octagonal one like the default 80 um
  bond pad with sides of about 33 um, may have no side as long as TM2_bR_cr,
  and then a track between TM2_b and TM2_bR from it is a warning the deck does
  not raise. KiCad 9.99 raises it beside such an octagon drawn as a chamfered
  rectangle, or as a custom pad sized to it, since the rule reads Size_X and
  Size_Y, which for a custom pad is the anchor's; a custom pad on a small
  anchor raises nothing. The probe board (below) holds all three.
- Slt.c as a maximum track width on every metal (a warning: a wider track
  needs slits, or a width of Slt_c or less). No flow step cuts slits: that is
  the designer's step, with the KLayout slit macro
  (`interposer_filler_slit.lym`) on the GDS. The full deck, not the
  precheck, reports unslit copper wider than Slt_c outside the pad and Recog
  regions it exempts (and MIM on Metal5 and TopMetal1). The rule matches only
  tracks wider than Slt_c, so a track under the width minimum stays an error.

Its header lists every other rule key as KLayout only: the other wide-line
and the 45-degree spacing, vias (the exporter draws their cuts), pad and
passivation openings, LBE, density, fill, the other slit rules, the seal ring
and the MIM rules.

KiCad's check is an aid while drawing, and it misses violations:

- It flags a clearance only below the rule less its DRC epsilon (0.5 um by
  default). The Metal5 and Metal4 space (0.21 um) is therefore never caught, and
  a TopMetal1 or TopMetal2 gap only once it is 0.5 um under the rule.
- It enforces the width minimum at the rule value, but its report may name the
  limit "board setup constraints" instead of the rule.
- It measures clearance between different nets only, and it sees the board,
  not the exported GDS.
- It has no parallel-run condition, so both TM2.bR rules also warn on runs
  the deck accepts. It checks one track or pad at a time, so merged copper,
  zones and graphic shapes on copper stay KLayout checks for TM2.bR and Slt.c.
  A board KiCad reports with no error can still fail TM2.bR in KLayout.

The KLayout decks on the GDS are the DRC that counts.

kicad-cli drops the whole rules file, with no message, when one token in it is
unknown (a unit other than mm, an item property KiCad does not know), and then
checks the board against its printed-board defaults. The generator writes mm,
the properties Type, Width, Size_X, Size_Y and Net, and the function
existsOnLayer only, and a live test in `test_kicad_rules.py` runs kicad-cli
9.99 on a probe board that breaks each rule. Each probe item states what it
breaks from its own geometry and the rule values, so the test checks that
KiCad reports each rule as often as the copper breaks it. A wide track and a
wide pad near the outline break nothing, so a TM2.bR rule that holds copper to
the outline would raise counts the test does not expect.

A board takes this file and the project beside it, and a consumer may pin
their digests, so one intm4tm2 version names one set of their bytes.
`test_kicad_rules.py` holds the digests each version from 0.3.0 carries, and
fails when the files differ from the newest row or pyproject.toml declares
another version: new bytes need a new row and the version moved to it.

The project file carries the board setup and the netclasses, from the same
JSON, so the template does not open against KiCad's printed-board defaults
(below). A KiCad via stands for the via array the
exporter draws: its drill for one cut and its diameter for one cut with the
metal enclosure on both sides. The board setup floors are the smallest values
any metal or via level allows (Mn.a, Mn.b; one Via4 cut, its enclosure, the
cut space). Each metal has a netclass with its width and space and a via of
the level that lands on it from below (Via4 for Metal4). `Default` takes the
TopMetal2 width and space and a TopVia2 via, which are legal on every layer.
The exporter draws a via's landing at least as large as the KiCad via, and
larger whenever the via does not hold a whole number of cuts, so the space
around a via stays a KLayout check.

Each netclass carries every key KiCad 9.99 writes for one. KiCad ignores a
class that lacks one of them (a priority or a tuning profile was seen to
matter), and then opens the board at its printed-board defaults: a 200 um
track and clearance and a 600 um via for the interactive router, and a 0.3 mm
via minimum. A live test checks that kicad-cli holds none of the template's
vias to that minimum.

The deck states no metal-to-edge rule, so the copper-to-edge floor is 0.001
mm: it keeps copper inside the outline (with 0, KiCad lets copper that only
touches the outline through). It sets no keepout from a placed seal ring;
the sealring deck checks the ring.

Do not edit either file. After a change to the JSON, run

```
python scripts/kicad_rules_gen.py
```

`--check` exits 1 when a committed file is stale, and
`libs.tech/klayout/intm4tm2_tests/test_kicad_rules.py` fails on it, and on any
value that is not its JSON keys'.

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
