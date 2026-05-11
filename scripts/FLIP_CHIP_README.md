# flip-chip GDS utility

Mirror-X (face-down) a GDS file, flattened into a single cell. Matches the
convention used by the interposer assembly pipeline
(`hyp_to_gds.py::_place_die_flipped`).

> **Note.** The flip transform itself is trivial in KLayout:
> `ICplxTrans(1.0, 180.0, True, ...)`, also known as **M180** or
> "Flip Horizontally" in the GUI. This script exists so the same convention
> (M180 + flattening for downstream EM/thermal tools) is available standalone
> and stays consistent with the assembly pipeline.

## Requirements

- Python 3 with `klayout` bindings: `pip install klayout`
- Or a `klayout` executable on `PATH` (the shell wrapper falls back to it).

## Files

- `flip-chip.sh` -- shell wrapper, the entry point.
- `flip_chip_gds.py` -- python implementation (klayout.db).

Both must sit in the same directory.

## Usage

```bash
./flip-chip.sh --input_gds <in.gds> --output_gds <out.gds> [options]
```

Options:

| flag | default | meaning |
|---|---|---|
| `--top-cell NAME` | autodetect | top cell in the input GDS |
| `--output-cell NAME` | `<top>_flipped` | name for the flipped cell |
| `--rotation DEG` | `0` | extra rotation baked into the flip |

## What it does

1. Reads the input GDS.
2. Wraps the top cell with `M180 = ICplxTrans(1, 180, mirror=True)` --
   net effect: `(x, y) -> (-x, y)`.
3. Flattens the wrapper to a single cell so simulators that do not honor
   instance-level mirroring see correct per-layer geometry.
4. Writes the output GDS.

Layer numbers are preserved. The z-order inversion of a flipped BEOL stack
is **not** captured (GDS has no z); handle z-inversion downstream via the
stackup YAML.

## Examples

```bash
# basic flip
./flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds

# flip + rotate 90 deg in one transform
./flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds --rotation 90

# multi-top GDS -- pick the cell explicitly
./flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds --top-cell MyTop
```

## Programmatic use

```python
from pathlib import Path
from flip_chip_gds import flip_chip_gds
flip_chip_gds(Path("die.gds"), Path("die_flipped.gds"))
```
