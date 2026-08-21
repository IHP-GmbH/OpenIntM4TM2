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
"""Regression for the one-click density report (python/density_report.py).

density_report.measure reproduces the numbers density.drc computes (global metal
coverage, LBE coverage, per-plate slit adequacy) so a designer can read them directly
instead of parsing a violation database. These tests pin the coverage formula against
hand-computed geometry, the chip-area priority, the band/slit classification, and that
measure accepts an in-memory layout (the path a KLayout menu macro takes). They use only
klayout.db, so they need no klayout binary and no DRC deck run; the deck cross-check that
these percentages equal the deck's own log lives in the slit-generator suite.
"""

import sys
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_KLAYOUT / "python"))
import density_report as dr  # noqa: E402


def _build(path=None, prb=(0, 0, 200, 200), esb=None, m4=None, m4_filler=None,
           m4_slit=None, m4_pillar=None, m5=None, tm1=None, tm2=None, lbe=None):
    """A layout with the requested boxes; returns the Layout (and writes it if `path`)."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("INTERPOSER")

    def put(layer, dt, box):
        if box is not None:
            top.shapes(ly.layer(layer, dt)).insert(kdb.DBox(*box))

    put(235, 0, prb)
    put(39, 4, esb)
    put(50, 0, m4)
    put(50, 22, m4_filler)
    put(50, 24, m4_slit)
    put(41, 35, m4_pillar)          # dfpad_pillar marker (a Cu-pillar landing pad)
    put(67, 0, m5)
    put(126, 0, tm1)
    put(134, 0, tm2)
    put(157, 0, lbe)
    if path is not None:
        ly.write(str(path))
    return ly


def test_global_density_matches_hand_computed(tmp_path):
    # chip 200x200 = 40000; M4 150x150 = 22500 -> 56.25%; TM1 100x100 -> 25.0% (== min).
    design = tmp_path / "d.gds"
    _build(design, m4=(20, 20, 170, 170), tm1=(20, 20, 120, 120), lbe=(0, 0, 50, 50))
    rep = dr.measure(str(design), "INTERPOSER")

    assert rep["chip_area_um2"] == 40000.0
    assert rep["chip_source"].startswith("prBoundary")
    assert rep["metals"]["M4"]["coverage_pct"] == pytest.approx(56.25)
    assert rep["metals"]["M4"]["state"] == "ok"
    assert rep["metals"]["TM1"]["coverage_pct"] == pytest.approx(25.0)
    assert rep["metals"]["TM1"]["state"] == "ok"                 # inclusive lower bound
    assert rep["metals"]["M5"]["state"] == "under"               # no Metal5 at all
    assert rep["lbe"]["coverage_pct"] == pytest.approx(6.25)
    assert rep["lbe"]["state"] == "ok"


def test_filler_and_slit_enter_the_metal_coverage(tmp_path):
    """Coverage is (drawn + filler) minus slit, exactly as the deck derives it."""
    # M4 drawn 100x100 (10000) + filler 100x100 disjoint (10000) - slit 20x20 (400)
    # over 40000 -> (20000-400)/40000 = 49.0%.
    design = tmp_path / "d.gds"
    _build(design, m4=(10, 10, 110, 110), m4_filler=(120, 10, 220, 110),
           m4_slit=(20, 20, 40, 40), prb=(0, 0, 200, 220))
    rep = dr.measure(str(design), "INTERPOSER")
    # chip is 200x220 = 44000; covered = (10000 + 10000 - 400) = 19600 -> 44.55%
    assert rep["chip_area_um2"] == 44000.0
    assert rep["metals"]["M4"]["coverage_pct"] == pytest.approx(100.0 * 19600 / 44000, abs=0.01)


def test_slit_adequacy_classifies_plates(tmp_path):
    # M4: a wide 150x150 plate with a 50x50 slit (~11% of the opened plate) -> ok.
    # M5: a wide 150x150 plate with no slit -> under (0%).
    # TM1: a 30x30 plate, below Slt_i_w -> no wide plate -> none.
    design = tmp_path / "d.gds"
    ly = _build(m4=(20, 20, 170, 170), m4_slit=(60, 60, 110, 110),
                m5=(20, 20, 170, 170), tm1=(20, 20, 50, 50))
    ly.write(str(design))
    rep = dr.measure(str(design), "INTERPOSER")

    assert rep["slits"]["M4"]["wide_plates"] == 1
    # 50x50 slit (2500) inside the 150x150 opened plate (22500) -> exactly 11.11%.
    assert rep["slits"]["M4"]["min_ratio_pct"] == pytest.approx(100.0 * 2500 / 22500, abs=0.05)
    assert rep["slits"]["M4"]["state"] == "ok"

    assert rep["slits"]["M5"]["wide_plates"] == 1
    assert rep["slits"]["M5"]["min_ratio_pct"] == 0.0
    assert rep["slits"]["M5"]["state"] == "under"

    assert rep["slits"]["TM1"]["wide_plates"] == 0
    assert rep["slits"]["TM1"]["min_ratio_pct"] is None
    assert rep["slits"]["TM1"]["state"] == "none"


def test_slit_adequacy_exempts_pads(tmp_path):
    """A wide plate covered by a Cu-pillar pad marker is exempt, not reported "under".

    The deck's Slt.c exempts pads and the slit generator leaves them unslit, so an unslit
    pad is deck-clean; the report must not flag it. A 150x150 Metal4 plate fully under a
    41/35 pillar marker has no eligible wide plate left after the exemption.
    """
    design = tmp_path / "d.gds"
    _build(design, m4=(20, 20, 170, 170), m4_pillar=(20, 20, 170, 170))
    rep = dr.measure(str(design), "INTERPOSER")
    assert rep["slits"]["M4"]["wide_plates"] == 0
    assert rep["slits"]["M4"]["state"] == "none", "an exempt pad was reported as under-slit"


def test_lbe_over_the_maximum(tmp_path):
    # LBE 100x100 (10000) over 40000 = 25% > LBE_i 20% -> over.
    design = tmp_path / "d.gds"
    _build(design, lbe=(0, 0, 100, 100))
    rep = dr.measure(str(design), "INTERPOSER")
    assert rep["lbe"]["coverage_pct"] == pytest.approx(25.0)
    assert rep["lbe"]["state"] == "over"


def test_chip_source_priority(tmp_path):
    # No prBoundary: fall back to the EdgeSeal boundary (39/4); its area is used.
    design = tmp_path / "d.gds"
    _build(design, prb=None, esb=(0, 0, 100, 100), m4=(0, 0, 50, 50))
    rep = dr.measure(str(design), "INTERPOSER")
    assert rep["chip_source"].startswith("EdgeSeal boundary")
    assert rep["chip_area_um2"] == 10000.0
    assert rep["metals"]["M4"]["coverage_pct"] == pytest.approx(25.0)   # 2500/10000

    # Neither boundary: the layout extent (here the M4 box itself).
    design2 = tmp_path / "d2.gds"
    _build(design2, prb=None, esb=None, m4=(0, 0, 100, 100))
    rep2 = dr.measure(str(design2), "INTERPOSER")
    assert rep2["chip_source"] == "layout extent"
    assert rep2["metals"]["M4"]["coverage_pct"] == pytest.approx(100.0)


def test_measure_accepts_layout_object(tmp_path):
    """A KLayout menu macro passes the live layout, not a path."""
    ly = _build(m4=(20, 20, 170, 170))
    rep = dr.measure(ly, "INTERPOSER")
    assert rep["metals"]["M4"]["coverage_pct"] == pytest.approx(56.25)


def test_zero_chip_area_is_graceful(tmp_path):
    ly = kdb.Layout()
    ly.dbu = 0.001
    ly.create_cell("INTERPOSER")                                # empty, no shapes
    rep = dr.measure(ly, "INTERPOSER")
    assert rep["chip_area_um2"] == 0.0
    assert rep["metals"] == {}
    # format_report must not raise on an empty report.
    assert "nothing measured" in dr.format_report(rep)


def test_format_report_is_readable(tmp_path):
    design = tmp_path / "d.gds"
    _build(design, m4=(20, 20, 170, 170), tm1=(20, 20, 120, 120))
    text = dr.format_report(dr.measure(str(design), "INTERPOSER"))
    for token in ("Interposer density report", "M4", "M5", "TM1", "TM2", "LBE", "Slit adequacy"):
        assert token in text
