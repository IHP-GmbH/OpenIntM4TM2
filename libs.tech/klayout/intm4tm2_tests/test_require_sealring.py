# SPDX-License-Identifier: Apache-2.0
"""The --require_sealring switch: how the runner passes it on and how the
runset rejects a bad value or a run without the sealring deck.

The runner tests use a stand-in klayout that records its arguments. The
runset tests launch the real klayout on a tiny layout.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import klayout.db as kdb

DRC_DIR = Path(__file__).resolve().parents[1] / "tech" / "drc"
CLEAN = ("<report-database><description/><original-file/><generator/><top-cell/>"
         "<tags/><categories/><cells/><items/></report-database>")

# Answers the version probe; for a deck run, saves its arguments next to the
# report and writes a clean report.
FAKE_KLAYOUT = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    -v) echo "KLayout 0.30.5"; exit 0 ;;
    report=*) report="${arg#report=}" ;;
  esac
done
printf '%s\\n' "$@" > "$report.args"
printf '%s' '""" + CLEAN + """' > "$report"
"""


def _run_cli(tmp_path, *args):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "klayout"
    fake.write_text(FAKE_KLAYOUT)
    fake.chmod(0o755)
    layout = tmp_path / "chip.gds"
    layout.touch()  # never read: the top cell is given
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        [sys.executable, str(DRC_DIR / "run_drc.py"), "--path", str(layout), "--topcell", "TOP",
         "--run_dir", str(tmp_path / "run"), *args],
        capture_output=True, text=True, env=env, timeout=120)
    runs = {}
    for path in (tmp_path / "run").glob("*.args"):
        argv = path.read_text().split("\n")
        deck = next((a[len("deck="):] for a in argv if a.startswith("deck=")), "all")
        runs[deck] = "require_sealring=true" in argv
    return result, runs


def test_flag_reaches_the_single_run(tmp_path):
    result, runs = _run_cli(tmp_path, "--require_sealring")
    assert result.returncode == 0, result.stderr
    assert runs == {"all": True}


def test_flag_reaches_only_the_sealring_deck_in_parallel(tmp_path):
    result, runs = _run_cli(tmp_path, "--require_sealring", "--deck", "pad",
                            "--deck", "sealring", "--mp", "2")
    assert result.returncode == 0, result.stderr
    assert runs == {"pad": False, "sealring": True}


def test_flag_skips_the_density_run(tmp_path):
    # The default decks plus density, as in the shared DRC invocation in the README.
    result, runs = _run_cli(tmp_path, "--require_sealring", "--density")
    assert result.returncode == 0, result.stderr
    assert runs == {"all": True, "density": False}


def test_no_flag_no_define(tmp_path):
    result, runs = _run_cli(tmp_path, "--deck", "sealring")
    assert result.returncode == 0, result.stderr
    assert runs == {"sealring": False}


def test_flag_without_the_sealring_deck_stops_the_runner(tmp_path):
    result, runs = _run_cli(tmp_path, "--require_sealring", "--deck", "pad")
    assert result.returncode == 1
    assert "--require_sealring needs the sealring deck" in result.stderr
    assert runs == {}


# --- the runset, with the real klayout -------------------------------------

KLAYOUT_BIN = shutil.which("klayout")


@pytest.fixture
def run_runset(tmp_path):
    if KLAYOUT_BIN is None:
        pytest.skip("klayout binary not on PATH")
    layout = kdb.Layout()
    top = layout.create_cell("TOP")
    top.shapes(layout.layer(50, 0)).insert(kdb.Box(0, 0, 10000, 10000))
    gds = tmp_path / "no_seal.gds"
    layout.write(str(gds))
    home = tmp_path / "klayout_home"
    home.mkdir()
    env = dict(os.environ, KLAYOUT_HOME=str(home))
    env.pop("KLAYOUT_PATH", None)

    def run(*defines):
        report = tmp_path / "report.lyrdb"
        cmd = [KLAYOUT_BIN, "-b", "-r", str(DRC_DIR / "intm4tm2.drc"),
               "-rd", f"input={gds}", "-rd", "topcell=TOP", "-rd", f"report={report}",
               "-rd", "run_mode=flat", "-rd", "threads=1"]
        for define in defines:
            cmd += ["-rd", define]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
        return result, report
    return run


@pytest.mark.live_tool
@pytest.mark.parametrize("value", ["yes", ""])
def test_runset_rejects_a_bad_value(run_runset, value):
    result, _ = run_runset("deck=sealring", f"require_sealring={value}")
    assert result.returncode != 0
    assert f"require_sealring must be true or false, got '{value}'" in result.stdout + result.stderr


@pytest.mark.live_tool
def test_runset_rejects_the_flag_without_the_sealring_deck(run_runset):
    result, _ = run_runset("deck=pad", "require_sealring=true")
    assert result.returncode != 0
    assert "require_sealring=true needs the sealring deck" in result.stdout + result.stderr


@pytest.mark.live_tool
@pytest.mark.parametrize("value, fires", [("TRUE", True), ("False", False)])
def test_runset_reads_the_value_case_insensitively(run_runset, value, fires):
    result, report = run_runset("deck=sealring", f"require_sealring={value}")
    assert result.returncode == 0, result.stdout + result.stderr
    sys.path.insert(0, str(DRC_DIR))
    try:
        from run_drc import get_rules_with_violations
    finally:
        sys.path.remove(str(DRC_DIR))
    assert ("SEAL.REQ.1" in get_rules_with_violations(report)) == fires
