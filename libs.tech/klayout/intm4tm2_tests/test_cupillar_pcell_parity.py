########################################################################
#
# Copyright 2026 IHP PDK Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
########################################################################
"""Parity regression: CuPillarPad PCell output == CuPillarGenerator output.

The PCell (intm4tm2_pycell_lib) and the programmatic generator
(bump_mirror.CuPillarGenerator) both draw Cu-pillar pad geometry. While
both exist, this regression pins them together: for every Table 6.1
option the fabrication layers produced by the two paths must be
geometrically identical (per-layer XOR is empty).

The PCell side runs inside a headless klayout batch process because the
PCell library registers through pya; the generator side uses the klayout
Python module directly and needs the interconnect PDK (sibling repo or
INTERCONNECT_PDK_ROOT) for its 3D body manifest -- the test skips when
that dependency is unavailable. 3D visualization layers (500/35, 501/35,
502/36) are generator-only by design and excluded from the comparison.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import klayout.db as kdb

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"

sys.path.insert(0, str(PYTHON_DIR))

from bump_mirror import BumpLocation, CuPillarGenerator  # noqa: E402

# (body_diameter_um, pcell diameter param, pcell passEncl param,
#  generator enclosure_um) -- one row per Table 6.1 option plus the
# 25 um custom option, which carries a 10 um enclosure.
PARITY_CASES = [
    (44, "35u", "7.5u", 7.5),
    (49, "40u", "7.5u", 7.5),
    (54, "45u", "7.5u", 7.5),
    (35, "25u", "10u", 10.0),
]

FAB_LAYERS = [(134, 0), (41, 35), (99, 35), (9, 35)]
NOFILL_LAYERS = [(50, 23), (67, 23), (126, 23), (134, 23)]

KLAYOUT_BIN = shutil.which("klayout")

PCELL_BATCH_SCRIPT = textwrap.dedent("""\
    import sys
    import pya

    # The library binds to the 'intm4tm2' technology; register it from the
    # repo .lyt so the batch session resolves the library and its layers.
    tech = pya.Technology.create_technology('intm4tm2')
    tech.load({lyt_path!r})

    sys.path.insert(0, {python_dir!r})
    sys.path.insert(0, {api_dir!r})

    import intm4tm2_pycell_lib  # registers the 'IntM4TM2' library

    cases = {cases!r}

    for name, diameter, passEncl, fillerEx in cases:
        layout = pya.Layout()
        layout.dbu = 0.001
        layout.technology_name = 'intm4tm2'
        cell = layout.create_cell("CuPillarPad", "IntM4TM2",
                                  {{"diameter": diameter,
                                    "passEncl": passEncl,
                                    "addFillerEx": fillerEx}})
        assert cell is not None, "create_cell returned None for " + name
        top = layout.create_cell("TOP")
        top.insert(pya.DCellInstArray(cell.cell_index(), pya.DTrans()))
        opts = pya.SaveLayoutOptions()
        opts.write_context_info = False
        layout.write({out_dir!r} + "/pcell_" + name + ".gds", opts)
""")


def _region_of(layout, cell, layer, datatype):
    idx = layout.layer(layer, datatype)
    region = kdb.Region(cell.begin_shapes_rec(idx))
    region.merge()
    return region


def _drawn_layers(layout, cell):
    drawn = set()
    for idx in layout.layer_indexes():
        if not cell.bbox_per_layer(idx).empty():
            info = layout.get_info(idx)
            drawn.add((info.layer, info.datatype))
    return drawn


def _run_pcell_batch(out_dir, cases):
    """Generate one PCell GDS per case in a single headless klayout run."""
    script = out_dir / "gen_pcells.py"
    script.write_text(PCELL_BATCH_SCRIPT.format(
        lyt_path=str(REPO_KLAYOUT / "tech" / "intm4tm2.lyt"),
        python_dir=str(PYTHON_DIR),
        api_dir=str(PYTHON_DIR / "pycell4klayout-api" / "source" / "python"),
        cases=cases,
        out_dir=str(out_dir)))

    empty_home = out_dir / "klayout_home"
    empty_home.mkdir()
    env = dict(os.environ, KLAYOUT_HOME=str(empty_home))
    # A stale KLAYOUT_LYP_FILE would replace the layer table with another
    # PDK's; the tech class honors it, so keep it out of the batch.
    env.pop("KLAYOUT_LYP_FILE", None)
    result = subprocess.run(
        [KLAYOUT_BIN, "-zz", "-rx", "-r", str(script)],
        capture_output=True, text=True, env=env, timeout=300)
    assert result.returncode == 0, (
        f"pcell batch failed:\n{result.stdout}\n{result.stderr}")


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_pcell_matches_generator():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)

        cases = [(f"body{body}", diameter, passEncl, "nil")
                 for body, diameter, passEncl, _ in PARITY_CASES]
        _run_pcell_batch(out_dir, cases)

        for body, diameter, passEncl, enclosure in PARITY_CASES:
            # Generator side (needs the interconnect PDK for its 3D bodies)
            try:
                gen = CuPillarGenerator(enclosure_um=enclosure)
                assert gen.add_bumps([BumpLocation("U1", "P1", 0.0, 0.0)],
                                     body_diameter_um=body) == 1
            except RuntimeError as exc:
                pytest.skip(f"interconnect PDK unavailable: {exc}")
            gen_gds = out_dir / f"gen_body{body}.gds"
            gen.write(str(gen_gds))

            gen_layout = kdb.Layout()
            gen_layout.read(str(gen_gds))
            gen_top = gen_layout.top_cell()

            pcell_layout = kdb.Layout()
            pcell_layout.read(str(out_dir / f"pcell_body{body}.gds"))
            pcell_top = pcell_layout.top_cell()

            # The PCell must draw exactly the four fab layers -- nothing
            # extra (in particular no dfpad on the drawing purpose).
            assert _drawn_layers(pcell_layout, pcell_top) == set(FAB_LAYERS), (
                f"unexpected layer set for body {body} um")

            for layer, datatype in FAB_LAYERS:
                gen_region = _region_of(gen_layout, gen_top, layer, datatype)
                pcell_region = _region_of(pcell_layout, pcell_top, layer,
                                          datatype)
                assert not gen_region.is_empty(), (
                    f"generator drew nothing on {layer}/{datatype} "
                    f"(body {body})")
                assert not pcell_region.is_empty(), (
                    f"pcell drew nothing on {layer}/{datatype} (body {body})")
                xor = gen_region ^ pcell_region
                assert xor.is_empty(), (
                    f"layer {layer}/{datatype} differs for body {body} um: "
                    f"XOR area {xor.area()} dbu^2")


@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_pcell_nofill_exclusion():
    """addFillerEx='t' adds opening/2 + 10 um circles on the metal stack."""
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _run_pcell_batch(out_dir, [("nofill35", "35u", "7.5u", "t")])

        layout = kdb.Layout()
        layout.read(str(out_dir / "pcell_nofill35.gds"))
        top = layout.top_cell()

        assert _drawn_layers(layout, top) == set(FAB_LAYERS + NOFILL_LAYERS)

        for layer, datatype in NOFILL_LAYERS:
            region = _region_of(layout, top, layer, datatype)
            box = region.bbox()
            # opening radius 17.5 um + 10 um exclusion enclosure, in nm dbu
            assert (box.width(), box.height()) == (55000, 55000), (
                f"nofill circle on {layer}/{datatype} has bbox "
                f"{box.width()}x{box.height()} dbu, expected 55000x55000")
