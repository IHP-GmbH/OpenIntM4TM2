#!/usr/bin/env python3

# =========================================================================================
# Copyright 2026 IHP PDK Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========================================================================================

import math
from pathlib import Path

import klayout.db as kdb


DBU = 0.001
SEGMENTS = 64

DEEPVIA = kdb.LayerInfo(152, 0)
METAL1 = kdb.LayerInfo(8, 0)
PWELL_BLOCK = kdb.LayerInfo(46, 21)
PRBOUNDARY = kdb.LayerInfo(235, 0)

OUTER_RADIUS = 12.5
INNER_RADIUS = 9.5
METAL1_RADIUS = OUTER_RADIUS + 1.5
PWELL_BLOCK_RADIUS = OUTER_RADIUS + 2.5
BOUNDARY_HALF = 500.0


def dbu(value_um: float) -> int:
    return int(round(value_um / DBU))


def circle_points(radius_um: float) -> list[kdb.Point]:
    points = []
    for idx in range(SEGMENTS):
        angle = 2.0 * math.pi * idx / SEGMENTS
        points.append(kdb.Point(dbu(radius_um * math.cos(angle)), dbu(radius_um * math.sin(angle))))
    return points


def ring_polygon() -> kdb.Polygon:
    polygon = kdb.Polygon(circle_points(OUTER_RADIUS))
    polygon.insert_hole(list(reversed(circle_points(INNER_RADIUS))))
    return polygon


def box(center_x: float, center_y: float, half_size: float) -> kdb.Box:
    return kdb.Box(
        dbu(center_x - half_size),
        dbu(center_y - half_size),
        dbu(center_x + half_size),
        dbu(center_y + half_size),
    )


def add_tsv(cell: kdb.Cell, layer_map: dict[str, int], center_x: float, center_y: float,
            with_metal1: bool = True, with_pwell_block: bool = True) -> None:
    transform = kdb.Trans(dbu(center_x), dbu(center_y))
    cell.shapes(layer_map["deepvia"]).insert(ring_polygon().transformed(transform))
    if with_metal1:
        cell.shapes(layer_map["metal1"]).insert(box(center_x, center_y, METAL1_RADIUS))
    if with_pwell_block:
        cell.shapes(layer_map["pwell_block"]).insert(box(center_x, center_y, PWELL_BLOCK_RADIUS))


def write_layout(output_path: Path, topcell_name: str, placements: list[dict]) -> None:
    layout = kdb.Layout()
    layout.dbu = DBU

    layer_map = {
        "deepvia": layout.layer(DEEPVIA),
        "metal1": layout.layer(METAL1),
        "pwell_block": layout.layer(PWELL_BLOCK),
        "prboundary": layout.layer(PRBOUNDARY),
    }

    top = layout.create_cell(topcell_name)
    top.shapes(layer_map["prboundary"]).insert(box(0.0, 0.0, BOUNDARY_HALF))

    for placement in placements:
        add_tsv(top, layer_map, **placement)

    layout.write(str(output_path))


def main() -> None:
    out_dir = Path(__file__).resolve().parent

    write_layout(
        out_dir / "tsv_clean.gds",
        "tsv_clean",
        [{"center_x": 0.0, "center_y": 0.0}],
    )

    write_layout(
        out_dir / "tsv_spacing_violation.gds",
        "tsv_spacing_violation",
        [
            {"center_x": -17.5, "center_y": 0.0},
            {"center_x": 17.5, "center_y": 0.0},
        ],
    )

    write_layout(
        out_dir / "tsv_pwell_miss_violation.gds",
        "tsv_pwell_miss_violation",
        [{"center_x": 0.0, "center_y": 0.0, "with_pwell_block": False}],
    )

    write_layout(
        out_dir / "tsv_metal1_miss_violation.gds",
        "tsv_metal1_miss_violation",
        [{"center_x": 0.0, "center_y": 0.0, "with_metal1": False}],
    )


if __name__ == "__main__":
    main()
