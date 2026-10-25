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
"""The runset refuses a deck request it cannot run in full.

A run with fewer decks than asked for reports zero markers for every rule it
skipped, which reads as clean. These tests launch the real klayout on
intm4tm2.drc directly, the path that does not go through run_drc.py's own
deck-name check.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import klayout.db as kdb

DRC_DIR = Path(__file__).resolve().parents[1] / "tech" / "drc"
KLAYOUT_BIN = shutil.which("klayout")
# Every case here starts the klayout binary.
pytestmark = pytest.mark.live_tool


@pytest.fixture
def run_runset(tmp_path):
    if KLAYOUT_BIN is None:
        pytest.skip("klayout binary not on PATH")
    layout = kdb.Layout()
    top = layout.create_cell("TOP")
    top.shapes(layout.layer(126, 0)).insert(kdb.Box(0, 0, 10000, 10000))
    gds = tmp_path / "tm1.gds"
    layout.write(str(gds))
    home = tmp_path / "klayout_home"
    home.mkdir()
    env = dict(os.environ, KLAYOUT_HOME=str(home))
    env.pop("KLAYOUT_PATH", None)

    def run(deck, runset=DRC_DIR / "intm4tm2.drc"):
        cmd = [KLAYOUT_BIN, "-b", "-r", str(runset),
               "-rd", f"input={gds}", "-rd", "topcell=TOP",
               "-rd", f"report={tmp_path / 'report.lyrdb'}",
               "-rd", "run_mode=flat", "-rd", "threads=1", "-rd", f"deck={deck}"]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    return run


def test_a_known_deck_runs(run_runset):
    result = run_runset("topmetal1")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("deck, reason", [
    ("nosuchdeck", "Unknown deck 'nosuchdeck'"),
    ("topmetal1,nosuchdeck", "Unknown deck 'nosuchdeck'"),
    (" ", "Unknown deck ''"),
    (",", "deck=',' names no deck"),
])
def test_the_runset_refuses_a_request_it_cannot_run(run_runset, deck, reason):
    result = run_runset(deck)
    assert result.returncode != 0
    assert reason in result.stdout + result.stderr


def test_the_runset_refuses_a_missing_rule_file(run_runset, tmp_path):
    # A copy of the runset with one registered deck file gone, as in a
    # damaged install.
    copy = tmp_path / "drc"
    copy.mkdir()
    shutil.copy(DRC_DIR / "intm4tm2.drc", copy)
    shutil.copy(DRC_DIR / "intm4tm2_decks.rb", copy)
    shutil.copytree(DRC_DIR / "rule_decks", copy / "rule_decks")
    missing = copy / "rule_decks" / "5_22_topmetal1.drc"
    missing.unlink()

    result = run_runset("topmetal1", runset=copy / "intm4tm2.drc")
    assert result.returncode != 0
    assert f"DRC rule deck not found: {missing}" in result.stdout + result.stderr
