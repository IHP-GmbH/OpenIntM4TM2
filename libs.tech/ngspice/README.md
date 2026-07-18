# ngspice models

Simulation models for the interposer device library, ported from the
IHP-Open-PDK (SG13G2 open PDK) and trimmed to the devices the interposer
carries. Each file keeps its upstream Apache-2.0 header.

## Contents

| File | Purpose |
|---|---|
| `models/capacitors_mod.lib` | `cap_cmim` MIM capacitor subckt (`PLUS MINUS`, params `w`, `l` in meters, `mm_ok`, `ic`) with the `cmim_core` capacitor model (area 1.5 fF/um^2 via `cap_carea`, perimeter 40 aF/um) and 55 mOhm series resistance |
| `models/capacitors_mod_mismatch.lib` | Mismatch variant (`agauss` on the area capacitance, enabled per instance with `mm_ok=1`) |
| `models/capacitors_stat.lib` | Statistical (Monte Carlo) parameter set |
| `models/cornerCAP.lib` | Corner wrapper: sections `cap_typ`, `cap_bcs` (0.9x), `cap_wcs` (1.1x) plus `_mismatch`/`_stat` variants |

The RF variant (`cap_rfcmim`) is not part of the interposer device set and was
removed from the ports; everything else is byte-identical to upstream.

## Usage

```spice
.lib /path/to/libs.tech/ngspice/models/cornerCAP.lib cap_typ

X1 n_plus n_minus cap_cmim w=6.99u l=6.99u m=1
```

The device is a subcircuit: instantiate with an `X` prefix, never as a plain
`C` element. Default plate size 6.99 um x 6.99 um gives about 74.4 fF at the
typical corner.

`intm4tm2_tests/test_cmim_model.py` (under `../klayout/`) measures the model
with ngspice against the analytic capacitance for every corner section.
