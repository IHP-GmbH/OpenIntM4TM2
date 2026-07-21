# IntM4TM2 interposer - EM / extraction technology stackup

Vertical (z) technology stackups for the IHP **IntM4TM2** interposer module,
for electromagnetic simulation and parasitic extraction. These files give the
interposer PDK the per-layer thickness / height information that the geometry
files (`.lyp`, DRC, LVS) do not carry.

## Files

| Path | Tool | Notes |
|---|---|---|
| `libs.tech/openems/openems_intm4tm2/workflow/INTM4TM2.xml` | openEMS | 300 um Si carrier (default) |
| `libs.tech/openems/openems_intm4tm2/workflow/INTM4TM2_nosub.xml` | openEMS | no lossy substrate (2 um SiO2 spacer) |
| `libs.tech/palace/workflow/INTM4TM2.xml` | Palace | 300 um Si carrier, backside ground sheet |
| `libs.tech/palace/workflow/INTM4TM2_nosub.xml` | Palace | no substrate |
| `libs.tech/parasitics/itf/intm4tm2_typ.itf` | RC extraction | typical corner, single stack |

## Physical model: reduced back-end

IntM4TM2 is a **reduced back-end**: the interposer routing uses only the upper
metals **Metal4, Metal5, TopMetal1, TopMetal2** (2 thin + 2 thick Al layers),
plus the MIM capacitor. There are no transistors and no Metal1..Metal3 - which
is exactly what the interposer layer map, DRC and LVS already encode.

Metal4 is therefore the *lowest* routing metal and sits on the carrier base
oxide, not on top of a Metal1..Metal3 stack. The M4..TM2 block keeps the SG13G2
layer thicknesses and inter-metal spacings unchanged (they are identical in the
base process) and is shifted down by **3.42 um** so Metal4 starts ~0.64 um above
the carrier surface.

## Stack (z = 0 at carrier top surface)

| Layer | GDS | Zmin (um) | Zmax (um) | Thickness (um) |
|---|---|---|---|---|
| TopMetal2 | 134 | 7.8103 | 10.8103 | 3.000 |
| TopVia2   | 133 | 5.0103 | 7.8103  | 2.800 |
| TopMetal1 | 126 | 3.0103 | 5.0103  | 2.000 |
| TopVia1   | 125 | 2.1600 | 3.0103  | 0.850 |
| Metal5    | 67  | 1.6700 | 2.1600  | 0.490 |
| Via4      | 66  | 1.1300 | 1.6700  | 0.540 |
| Metal4    | 50  | 0.6400 | 1.1300  | 0.490 |
| MIM       | 36  | 2.16   | 3.0103  | (cap between M5 and TM1) |

Passivation above TopMetal2: 1.5 um oxide + 0.4 um SiN. Total build-up from the
carrier surface to the top of passivation is ~12.71 um. The inter-metal gaps
reproduce the module spec exactly: Metal4->Metal5 = 0.54, Metal5->TopMetal1 =
0.85, TopMetal1->TopMetal2 = 2.80 um.

## Substrate variants

- **`INTM4TM2.xml`** - 300 um bulk Si carrier (the IHP post-thinning value).
- **`INTM4TM2_nosub.xml`** - no substrate; the carrier is replaced by a 2 um
  SiO2 spacer. Use when a ground plane shields the substrate, for a smaller,
  faster mesh. openEMS models the carrier as an open/floating lossy dielectric;
  the Palace 300 um variant terminates the carrier backside with a ground sheet,
  following each solver's convention.

Substrate resistivity uses the public SG13G2 value (11.9 relative permittivity,
2.0 S/m); the module spec lists the interposer carrier resistivity as *tbd*, so
adjust `Substrate` conductivity if a high-resistivity carrier is used.

## Running

The XML is the technology input for the standard IHP openEMS / Palace Python
workflows - this directory ships only the stackup, not a fork of the (technology
independent) workflow code. Point the upstream workflow at the XML above; the GDS
layer numbers in the `Layer="..."` attributes match the IntM4TM2 layer map.

## Provenance

All values are public. Ported from the Apache-2.0 IHP-Open-PDK:
- openEMS: `ihp-sg13g2/libs.tech/openems/openems_ihp_sg13g2/workflow/SG13G2*.xml`
- Palace:  `ihp-sg13g2/libs.tech/palace/workflow/SG13G2*.xml`
- ITF:     `ihp-sg13g2/libs.tech/parasitics/itf/sg13g2_typ.itf`

Layer thicknesses are also documented in the public SG13G2 process specification.
