#!/usr/bin/env python3
"""
flip_chip_gds.py -- Standalone Flip-Chip GDS Mirror-X Utility

Reads an input GDS and writes a flipped (mirror-X, face-down) copy.

Wraps the top cell with an M180 transform (mirror-X = negate X coordinates)
and flattens to a single cell, so EM/thermal simulators that do not honor
instance-level mirroring see correct per-layer geometry.

The transform itself is trivial in KLayout (M180 / `ICplxTrans(1, 180, True, ...)`);
this script exists so the same convention used by the interposer assembly
pipeline (hyp_to_gds.py::_place_die_flipped) is available standalone.
"""

import argparse
import sys
from pathlib import Path

try:
    import klayout.db as db
except ImportError:
    print("ERROR: klayout python bindings not found. Install with 'pip install klayout' "
          "or run inside KLayout.", file=sys.stderr)
    sys.exit(1)


def find_top_cell(layout: db.Layout, requested: str = None) -> db.Cell:
    """Return the top cell to flip. Use 'requested' if given, else autodetect."""
    if requested:
        cell = layout.cell(requested)
        if cell is None:
            available = ", ".join(c.name for c in layout.each_cell())
            raise ValueError(f"Cell '{requested}' not found. Available: {available}")
        return cell

    top_cells = layout.top_cells()
    if not top_cells:
        raise ValueError("GDS has no cells")
    if len(top_cells) > 1:
        names = ", ".join(c.name for c in top_cells)
        raise ValueError(
            f"GDS has multiple top cells ({names}); pass --top-cell to disambiguate")
    return top_cells[0]


def flip_chip_gds(input_gds: Path, output_gds: Path,
                  top_cell_name: str = None,
                  output_cell_name: str = None,
                  rotation: float = 0.0) -> None:
    """Flip a GDS file along the X axis (mirror around Y-axis) and flatten.

    Args:
        input_gds: Path to source GDS.
        output_gds: Path to write flipped GDS.
        top_cell_name: Optional name of the top cell in input_gds. Autodetect
            if omitted (fails on multi-top GDS).
        output_cell_name: Name for the flipped cell in the output. Defaults
            to '<original>_flipped'.
        rotation: Additional rotation in degrees, baked into the same transform.
    """
    if not input_gds.exists():
        raise FileNotFoundError(f"Input GDS not found: {input_gds}")

    layout = db.Layout()
    layout.read(str(input_gds))

    template = find_top_cell(layout, top_cell_name)
    out_name = output_cell_name or f"{template.name}_flipped"

    if layout.cell(out_name) is not None:
        raise ValueError(
            f"Output cell name '{out_name}' already exists; "
            "pass --output-cell to choose a different name.")

    wrapper = layout.create_cell(out_name)
    # M180 = mirror=True + rotate 180 deg -> negate X only. Optional device
    # rotation is baked into the same transform.
    flip = db.DCplxTrans(1.0, rotation + 180.0, True, db.DVector(0, 0))
    wrapper.insert(db.DCellInstArray(template.cell_index(), flip))
    wrapper.flatten(-1, False)

    output_gds.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(output_gds))

    print(f"  Input  : {input_gds}")
    print(f"  Output : {output_gds}")
    print(f"  Source top cell : {template.name}")
    print(f"  Flipped cell    : {out_name}")
    print(f"  Transform       : M180 (mirror-X, negate X), rotation={rotation} deg")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input_gds', required=True, metavar='PATH',
        help='Path to the input GDS file to flip.',
    )
    parser.add_argument(
        '--output_gds', required=True, metavar='PATH',
        help='Path where the flipped GDS will be written.',
    )
    parser.add_argument(
        '--top-cell', default=None, metavar='NAME',
        help='Top cell name in the input GDS (autodetected if omitted).',
    )
    parser.add_argument(
        '--output-cell', default=None, metavar='NAME',
        help="Name for the flipped cell in the output (default: '<top>_flipped').",
    )
    parser.add_argument(
        '--rotation', type=float, default=0.0, metavar='DEG',
        help='Additional rotation in degrees, baked into the flip transform (default: 0).',
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        flip_chip_gds(
            input_gds=Path(args.input_gds),
            output_gds=Path(args.output_gds),
            top_cell_name=args.top_cell,
            output_cell_name=args.output_cell,
            rotation=args.rotation,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
