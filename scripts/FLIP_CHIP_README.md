# flip-chip GDS utility

Mirror-X (face-down) a GDS file. Extracted from `hyp_to_gds.py::_place_die_flipped()`
so the same flip used in the interposer assembly flow can be applied standalone.

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
| `--mode {flatten,hierarchy}` | `flatten` | see below |
| `--top-cell NAME` | autodetect | top cell in the input GDS |
| `--output-cell NAME` | `<top>_flipped` | name for the flipped cell |
| `--rotation DEG` | `0` | extra rotation baked into the flip |

## Modes

- **flatten** (default): per-layer shape extraction from the top cell
  (recursive, flattens hierarchy), mirror-X applied, single flat output cell.
  Use this for EM / thermal simulators that do not honor instance-level
  mirroring.

- **hierarchy**: preserves the original cell tree and emits a wrapper that
  instantiates the top cell with mirror-X at the instance level. Smaller
  file, faster to write, consumer must honor the mirror flag on the
  instance.

In both modes the transform is mirror-X = negate X (mirror around the Y-axis).
Y coordinates are unchanged. Layer numbers are preserved.

## Examples

Basic flip (flatten):

```bash
./flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds
```

Preserve hierarchy:

```bash
./flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds --mode hierarchy
```

Flip and rotate 90 degrees in one transform:

```bash
./flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds --rotation 90
```

Multi-top GDS -- pick the cell explicitly:

```bash
./flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds --top-cell MyTop
```

## Notes

- The flip is purely geometric in the X-Y plane. GDS has no z information,
  so the z-order inversion of a flipped BEOL stack is NOT captured by this
  tool. Handle z-inversion downstream via your stackup YAML.
- `flip_chip_gds.py` can also be imported directly:

  ```python
  from flip_chip_gds import flip_chip_gds
  flip_chip_gds(Path("die.gds"), Path("die_flipped.gds"), mode="flatten")
  ```
