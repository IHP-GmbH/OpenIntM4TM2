#!/usr/bin/env python3
"""
flip_chip_gds.py -- Standalone Flip-Chip GDS Mirror-X Utility

Reads an input GDS and writes a flipped (mirror-X, face-down) copy.

Two modes are supported:

  flatten   (default) -- per-layer shape extraction from the top cell,
                         recursively flattens the hierarchy, applies the
                         mirror-X transform, and emits a single flat cell.
                         Matches hyp_to_gds.py::_place_die_flipped(); intended
                         for EM/thermal simulators that do not honor
                         instance-level mirroring.

  hierarchy           -- preserves the original cell hierarchy and emits a
                         wrapper cell that instantiates the source top cell
                         with the mirror-X transform applied at the instance
                         level. Smaller files, faster to write, but the
                         consumer must honor mirrored instances.

In both modes the transform is mirror-X = negate X coordinates (mirror
around the Y-axis). The z-order inversion of a flipped BEOL stack is NOT
captured in GDS (layers have no z-height); layer numbers are preserved
and z-inversion must be handled downstream by the stackup YAML.
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


def _flip_flatten(src: db.Layout, template: db.Cell, out_name: str,
                  rotation: float) -> db.Layout:
    """Flatten + mirror-X: per-layer region extraction into a single cell."""
    dst = db.Layout()
    dst.dbu = src.dbu

    # Replicate every (layer, datatype) so layer indices align between src/dst.
    layer_map = {li: dst.layer(src.get_info(li)) for li in src.layer_indices()}

    wrapper = dst.create_cell(out_name)

    # Mirror-X = negate X only.
    # ICplxTrans(mag, angle, mirror, displacement):
    #   mirror=True mirrors around X-axis (Y -> -Y), then
    #   +180 deg rotation negates both X and Y -> net: negate X only.
    # Optional device rotation is baked into the same transform.
    mirror_trans = db.ICplxTrans(1.0, rotation + 180.0, True, db.Vector(0, 0))

    layers_with_shapes = 0
    for src_li, dst_li in layer_map.items():
        region = db.Region(template.begin_shapes_rec(src_li))
        if region.is_empty():
            continue
        wrapper.shapes(dst_li).insert(region.transformed(mirror_trans))
        layers_with_shapes += 1

    if layers_with_shapes == 0:
        raise RuntimeError(f"Cell '{template.name}' contains no shapes on any layer")

    print(f"  Layers flipped  : {layers_with_shapes}")
    return dst


def _flip_hierarchy(src: db.Layout, template: db.Cell, out_name: str,
                    rotation: float) -> db.Layout:
    """Preserve hierarchy: instantiate template under a wrapper with mirror-X.

    The source layout is reused as the destination (its full cell tree is
    already there). A new wrapper cell is added that places the original top
    via a DCplxTrans carrying the mirror-X flag and optional rotation.
    Consumers must honor the mirror flag on the instance.
    """
    if src.cell(out_name) is not None:
        raise ValueError(
            f"Output cell name '{out_name}' already exists in the input GDS; "
            "pass --output-cell to choose a different name.")

    wrapper = src.create_cell(out_name)
    mirror_trans = db.DCplxTrans(1.0, rotation + 180.0, True, db.DVector(0.0, 0.0))
    wrapper.insert(db.DCellInstArray(template.cell_index(), mirror_trans))

    print(f"  Cells preserved : {src.cells()}")
    return src


def flip_chip_gds(input_gds: Path, output_gds: Path,
                  mode: str = "flatten",
                  top_cell_name: str = None,
                  output_cell_name: str = None,
                  rotation: float = 0.0) -> None:
    """Flip a GDS file along the X axis (mirror around Y-axis).

    Args:
        input_gds: Path to source GDS.
        output_gds: Path to write flipped GDS.
        mode: 'flatten' (per-layer flat, matches hyp_to_gds.py) or
              'hierarchy' (instance-level mirror, hierarchy preserved).
        top_cell_name: Optional name of the top cell in input_gds. Autodetect
            if omitted (fails on multi-top GDS).
        output_cell_name: Name for the flipped cell in the output. Defaults
            to '<original>_flipped'.
        rotation: Additional rotation in degrees, baked into the same
            transform (matches hyp_to_gds.py behavior).
    """
    if mode not in ("flatten", "hierarchy"):
        raise ValueError(f"Unknown mode '{mode}'. Use 'flatten' or 'hierarchy'.")

    if not input_gds.exists():
        raise FileNotFoundError(f"Input GDS not found: {input_gds}")

    src = db.Layout()
    src.read(str(input_gds))

    template = find_top_cell(src, top_cell_name)
    out_name = output_cell_name or f"{template.name}_flipped"

    if mode == "flatten":
        dst = _flip_flatten(src, template, out_name, rotation)
    else:
        dst = _flip_hierarchy(src, template, out_name, rotation)

    output_gds.parent.mkdir(parents=True, exist_ok=True)
    dst.write(str(output_gds))

    print(f"  Input  : {input_gds}")
    print(f"  Output : {output_gds}")
    print(f"  Source top cell : {template.name}")
    print(f"  Flipped cell    : {out_name}")
    print(f"  Mode            : {mode}")
    print(f"  Transform       : mirror-X (negate X), rotation={rotation} deg")


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
        '--mode', choices=['flatten', 'hierarchy'], default='flatten',
        help=("'flatten' (default): per-layer flat cell, matches hyp_to_gds.py "
              "_place_die_flipped() -- safe for EM/thermal tools. "
              "'hierarchy': preserve cell tree, mirror at instance level."),
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
            mode=args.mode,
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
