# SPDX-License-Identifier: Apache-2.0
"""Small real-Qt menu/runner/report integration smoke; no assembly rebuild."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


def test_all_shipped_menu_macros_are_valid_xml():
    macros = Path(__file__).resolve().parents[1] / "tech" / "macros"
    for macro in macros.glob("*.lym"):
        assert ET.parse(macro).getroot().tag == "klayout-macro", str(macro)


@pytest.mark.live_tool
@pytest.mark.skipif(not shutil.which("klayout"), reason="KLayout binary required")
def test_configure_run_and_open_reports_in_real_klayout(tmp_path):
    pytest.importorskip("klayout.db", reason="CLI runners require klayout.db")
    home = tmp_path / "klayout-config"
    home.mkdir()
    config = home / "klayoutrc"
    # A fresh GUI opens its hierarchy tip before executing -r/-rr scripts.
    # Set only that test-local preference and enter edit mode; -nc would ignore
    # it and hang offscreen at the startup dialog before the smoke can run.
    config.write_text("<config><full-hierarchy-new-cell>true</full-hierarchy-new-cell></config>\n")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", KLAYOUT_HOME=str(home),
               INTERPOSER_TEST_OUTPUT=str(tmp_path / "results with spaces"),
               INTERPOSER_TEST_PYTHON=sys.executable)
    env.pop("KLAYOUT_PATH", None)
    env.pop("KLAYOUT_LYP_FILE", None)
    script = Path(__file__).with_name("verification_gui_smoke.py")
    # -j takes the project root containing macros/, not that child directory.
    macro_project = Path(__file__).resolve().parents[1] / "tech"
    log = tmp_path / "gui.log"
    with log.open("w") as stream:
        result = subprocess.run([shutil.which("klayout"), "-e", "-c", str(config), "-t",
                                 "-j", str(macro_project), "-r", str(script)],
                                env=env, stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=90)
    output = log.read_text()
    assert result.returncode == 0, output
    assert "INTERPOSER_GUI_SMOKE_PASS" in output
