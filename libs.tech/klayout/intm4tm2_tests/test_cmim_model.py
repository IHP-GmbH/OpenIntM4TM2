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
"""Simulation smoke test for the cap_cmim ngspice model.

The model files under libs.tech/ngspice/models/ are ported from the
IHP-Open-PDK (SG13G2 open PDK), trimmed to the cmim device (the RF
variant is not part of the interposer device set). This test proves:

1. Every .LIB section of cornerCAP.lib resolves with the trimmed
   includes and simulates (typ/bcs/wcs, each plain, _mismatch, _stat).
2. The simulated capacitance matches the analytic value of the model
   card for the typical corner (square and rectangular device).
3. The bcs/wcs corners rescale cap_carea by the factors the corner
   library defines (0.9 and 1.1) while leaving CJSW untouched.

Capacitance is measured deterministically with a single-point AC
analysis: a 1 V AC source drives the capacitor at f = 1 MHz and
C = |I| / (2*pi*f*V). The 55 mOhm series resistor R1 inside cap_cmim
is negligible against 1/(2*pi*f*C) ~ 2 MOhm at that frequency.

Expected-value derivation (from the model equations as ported):
the cmim_core model card is a semiconductor capacitor model
    .model cmim_core C (... CJ=cap_carea CJSW=40E-18)
and ngspice computes for such a device
    C = CJ*(l - NARROW)*(w - NARROW) + 2*CJSW*(l + w - 2*NARROW)
with NARROW defaulting to 0. The cap_cmim subcircuit instantiates it
with l=l/sf and w=w/sf (sf=1E-6), i.e. the geometric length values
expressed as plain micron numbers, so with cap_carea = 1.5E-15 the
area term is 1.5 fF per um^2 and the perimeter term 0.04 fF per um
of circumference:
    C(w, l) = cap_carea*w*l + 2*40E-18*(w + l)   [w, l in um]
For w = l = 6.99 um: 73.29015 fF + 1.1184 fF = 74.40855 fF.
"""

import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Model files live at libs.tech/ngspice/models/, resolved relative to
# this test file (libs.tech/klayout/intm4tm2_tests/).
MODELS_DIR = Path(__file__).resolve().parents[2] / "ngspice" / "models"
CORNER_LIB = MODELS_DIR / "cornerCAP.lib"

FREQ_HZ = 1.0e6
CJSW = 40.0e-18       # F per um of edge, from the cmim_core model card
CAP_CAREA = 1.5e-15   # F per um^2, typical corner value in cornerCAP.lib

# Relative tolerance for the deterministic corners. The AC measurement
# reproduces the model equation to numerical precision (observed error
# < 1e-6 relative); 1.5% comfortably covers solver noise.
# Observed AC-measurement error is below 1e-6 relative; 0.1% leaves a
# wide safety margin while still catching a dropped perimeter term
# (CJSW contributes ~1.5% for the default plate).
RTOL = 0.001

ALL_SECTIONS = [
    "cap_typ",
    "cap_typ_mismatch",
    "cap_typ_stat",
    "cap_bcs",
    "cap_bcs_mismatch",
    "cap_wcs",
    "cap_wcs_mismatch",
]

pytestmark = pytest.mark.skipif(
    shutil.which("ngspice") is None, reason="ngspice not on PATH")


def _analytic_c(w_um, l_um, carea_factor=1.0):
    """C(w, l) per the cmim_core model card; see module docstring."""
    return (carea_factor * CAP_CAREA * w_um * l_um
            + 2.0 * CJSW * (w_um + l_um))


def _measure_c(tmp_path, section, w_um, l_um):
    """Run ngspice in batch mode and return the measured capacitance."""
    netlist = "\n".join([
        "* cap_cmim capacitance measurement testbench",
        f".lib {CORNER_LIB} {section}",
        "V1 a 0 dc 0 ac 1",
        f"X1 a 0 cap_cmim w={w_um}u l={l_um}u m=1",
        ".control",
        f"ac lin 1 {FREQ_HZ:.0f} {FREQ_HZ:.0f}",
        f"let cval = abs(v1#branch)/(2*{math.pi:.15f}*{FREQ_HZ:.0f})",
        "print cval",
        "quit 0",
        ".endcontrol",
        ".end",
        "",
    ])
    deck = tmp_path / f"tb_{section}_{w_um}x{l_um}.sp"
    deck.write_text(netlist)
    # -n skips the user's .spiceinit: a configured sourcepath would make
    # the relative .include lines inside cornerCAP.lib resolve against
    # another PDK's models instead of the ported ones. cwd pins relative
    # include resolution to our models directory.
    result = subprocess.run(
        ["ngspice", "-n", "-b", str(deck)],
        capture_output=True, text=True, timeout=120, cwd=str(MODELS_DIR))
    assert result.returncode == 0, (
        f"ngspice failed for section {section}:\n"
        f"{result.stdout}\n{result.stderr}")
    match = re.search(r"cval\s*=\s*([0-9.eE+-]+)", result.stdout)
    assert match, (
        f"no capacitance value in ngspice output for {section}:\n"
        f"{result.stdout}\n{result.stderr}")
    return float(match.group(1))


def test_models_present():
    for name in ("capacitors_mod.lib", "capacitors_mod_mismatch.lib",
                 "capacitors_stat.lib", "cornerCAP.lib"):
        assert (MODELS_DIR / name).is_file(), f"missing model file {name}"


def test_typ_square(tmp_path):
    # 6.99 x 6.99 um: 1.5e-15*6.99^2 + 2*40e-18*(6.99+6.99)
    #               = 73.29015 fF + 1.1184 fF = 74.40855 fF
    measured = _measure_c(tmp_path, "cap_typ", 6.99, 6.99)
    expected = _analytic_c(6.99, 6.99)
    assert measured == pytest.approx(expected, rel=RTOL, abs=0.0), (
        f"typ 6.99x6.99: measured {measured:.6e} F, "
        f"expected {expected:.6e} F")


def test_typ_rect(tmp_path):
    # 10 x 5 um: 1.5e-15*50 + 2*40e-18*15 = 75 fF + 1.2 fF = 76.2 fF
    measured = _measure_c(tmp_path, "cap_typ", 10.0, 5.0)
    expected = _analytic_c(10.0, 5.0)
    assert measured == pytest.approx(expected, rel=RTOL, abs=0.0), (
        f"typ 10x5: measured {measured:.6e} F, expected {expected:.6e} F")


@pytest.mark.parametrize("section,carea_factor", [
    # cornerCAP.lib: cap_bcs sets cap_carea = 0.9*1.5E-15,
    #                cap_wcs sets cap_carea = 1.1*1.5E-15.
    # Only the area coefficient is corner-scaled; the CJSW perimeter
    # term of the model card is corner-independent.
    ("cap_bcs", 0.9),
    ("cap_wcs", 1.1),
])
def test_corner_scaling(tmp_path, section, carea_factor):
    measured = _measure_c(tmp_path, section, 6.99, 6.99)
    expected = _analytic_c(6.99, 6.99, carea_factor)
    assert measured == pytest.approx(expected, rel=RTOL, abs=0.0), (
        f"{section} 6.99x6.99: measured {measured:.6e} F, "
        f"expected {expected:.6e} F")


@pytest.mark.parametrize("section", ALL_SECTIONS)
def test_corner_sections_resolve(tmp_path, section):
    """Every .LIB section simulates with the trimmed model includes.

    The _mismatch sections are deterministic at the default mm_ok=0
    (the mismatch scale factor collapses to 1), so they must reproduce
    the plain-corner value. The _stat section draws cap_carea from a
    gauss() distribution (3.3% one-sigma, num_sigmas=1), so it is only
    checked to land within a wide +-25% band around typical (> 7 sigma,
    deterministic in practice).
    """
    measured = _measure_c(tmp_path, section, 6.99, 6.99)
    assert math.isfinite(measured) and measured > 0.0
    if section.endswith("_stat"):
        expected = _analytic_c(6.99, 6.99)
        assert measured == pytest.approx(expected, rel=0.25, abs=0.0), (
            f"{section}: measured {measured:.6e} F implausibly far "
            f"from typical {expected:.6e} F")
    else:
        factor = 0.9 if "bcs" in section else 1.1 if "wcs" in section else 1.0
        expected = _analytic_c(6.99, 6.99, factor)
        assert measured == pytest.approx(expected, rel=RTOL, abs=0.0), (
            f"{section}: measured {measured:.6e} F, "
            f"expected {expected:.6e} F")
