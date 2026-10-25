# SPDX-License-Identifier: Apache-2.0
"""Report-completeness checks without launching KLayout."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


DRC_DIR = Path(__file__).resolve().parents[1] / "tech" / "drc"
CLEAN = ("<report-database><description/><original-file/><generator/><top-cell/>"
         "<tags/><categories/><cells/><items/></report-database>")


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(DRC_DIR))
    spec = importlib.util.spec_from_file_location("drc_completeness_runner", DRC_DIR / "run_drc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("missing_kind", ["absent", "empty", "directory"])
def test_clean_report_cannot_hide_missing_density(runner, monkeypatch, tmp_path, missing_kind):
    clean = tmp_path / "all.lyrdb"
    clean.write_text(CLEAN)
    density = tmp_path / "density.lyrdb"
    if missing_kind == "empty":
        density.touch()
    elif missing_kind == "directory":
        density.mkdir()

    def must_not_merge(*args):
        pytest.fail("incomplete reports reached the merge")

    monkeypatch.setattr(runner, "merge_klayout_drc_reports", must_not_merge)
    with pytest.raises(SystemExit) as error:
        runner.check_drc_results([str(clean), str(density)], tmp_path, "chip.gds", "TOP")
    assert error.value.code == 2
    assert clean.read_text() == CLEAN


def test_no_report_is_not_a_clean_run(runner, tmp_path):
    with pytest.raises(SystemExit) as error:
        runner.check_drc_results([], tmp_path, "chip.gds", "TOP")
    assert error.value.code == 2


def test_single_complete_clean_report_still_passes(runner, tmp_path):
    clean = tmp_path / "all.lyrdb"
    clean.write_text(CLEAN)
    assert runner.check_drc_results([str(clean)], tmp_path, "chip.gds", "TOP") == set()


@pytest.mark.parametrize("content", [
    "not a report",                              # not XML
    "<report-database><categories/><cells/>",    # truncated
    "<report-database/>",                        # XML, but not a report
])
@pytest.mark.parametrize("alone", [True, False])
def test_unreadable_report_is_not_a_clean_run(runner, monkeypatch, tmp_path, content, alone):
    # Alone it used to raise (exit 1, which reads as markers); after a clean
    # report the merge skipped or absorbed it and the run said PASSED.
    def must_not_merge(*args):
        pytest.fail("an unreadable report reached the merge")

    monkeypatch.setattr(runner, "merge_klayout_drc_reports", must_not_merge)
    bad = tmp_path / "density.lyrdb"
    bad.write_text(content)
    reports = [bad]
    if not alone:
        clean = tmp_path / "all.lyrdb"
        clean.write_text(CLEAN)
        reports.insert(0, clean)
    with pytest.raises(SystemExit) as error:
        runner.check_drc_results(list(map(str, reports)), tmp_path, "chip.gds", "TOP")
    assert error.value.code == 2
    assert bad.read_text() == content


def test_merge_never_skips_an_unreadable_report(runner, tmp_path):
    # Defence in depth: the merge used to log and skip a malformed report
    # after the first one, so the merged result could read clean.
    clean = tmp_path / "all.lyrdb"
    clean.write_text(CLEAN)
    bad = tmp_path / "density.lyrdb"
    bad.write_text("not a report")
    with pytest.raises(runner.ET.ParseError):
        runner.merge_klayout_drc_reports([str(clean), str(bad)], str(tmp_path / "full.lyrdb"))


def test_all_complete_reports_reach_merge(runner, monkeypatch, tmp_path):
    reports = [tmp_path / "all.lyrdb", tmp_path / "density.lyrdb"]
    for report in reports:
        report.write_text(CLEAN)
    calls = []

    def merge(inputs, output):
        calls.append(inputs)
        Path(output).write_text(CLEAN)

    monkeypatch.setattr(runner, "merge_klayout_drc_reports", merge)
    assert runner.check_drc_results(list(map(str, reports)), tmp_path, "chip.gds", "TOP") == set()
    assert calls == [list(map(str, reports))]
    assert (tmp_path / "chip_TOP_full.lyrdb").is_file()


# Stand-in for the klayout binary: answers the version probe, and for a deck
# run writes a clean report unless that deck is the one told to crash, or the
# one told to write a report that is not XML.
FAKE_KLAYOUT = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    -v) echo "KLayout 0.30.5"; exit 0 ;;
    report=*) report="${arg#report=}" ;;
    deck=*) deck="${arg#deck=}" ;;
  esac
done
[ -n "$CRASH_DECK" ] && [ "$deck" = "$CRASH_DECK" ] && exit 1
[ -n "$CORRUPT_DECK" ] && [ "$deck" = "$CORRUPT_DECK" ] && { printf 'not a report' > "$report"; exit 0; }
printf '%s' '""" + CLEAN + """' > "$report"
"""


@pytest.mark.parametrize("mp", ["1", "2"])
@pytest.mark.parametrize("crash_deck", ["", "via4"])
def test_run_fails_when_a_deck_crashes(tmp_path, crash_deck, mp):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "klayout"
    fake.write_text(FAKE_KLAYOUT)
    fake.chmod(0o755)
    layout = tmp_path / "chip.gds"
    layout.touch()  # never read: the top cell is given
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
               CRASH_DECK=crash_deck, CORRUPT_DECK="")
    result = subprocess.run(
        [sys.executable, str(DRC_DIR / "run_drc.py"), "--path", str(layout), "--topcell", "TOP",
         "--deck", "offgrid", "--deck", "via4", "--mp", mp, "--run_dir", str(tmp_path / "run")],
        capture_output=True, text=True, env=env, timeout=120)
    if crash_deck:
        assert result.returncode == 2, result.stderr
        assert "Deck 'via4' failed" in result.stderr
        assert "Incomplete DRC results" in result.stderr
    else:
        # The same run with nothing crashing passes, so the stand-in is sound.
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mp", ["1", "2"])
def test_run_fails_on_an_unreadable_report(tmp_path, mp):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "klayout"
    fake.write_text(FAKE_KLAYOUT)
    fake.chmod(0o755)
    layout = tmp_path / "chip.gds"
    layout.touch()  # never read: the top cell is given
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
               CRASH_DECK="", CORRUPT_DECK="via4")
    result = subprocess.run(
        [sys.executable, str(DRC_DIR / "run_drc.py"), "--path", str(layout), "--topcell", "TOP",
         "--deck", "offgrid", "--deck", "via4", "--mp", mp, "--run_dir", str(tmp_path / "run")],
        capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 2, result.stderr
    assert "unreadable reports" in result.stderr
    assert "DRC PASSED" not in result.stderr


@pytest.mark.parametrize("as_script", [True, False])
def test_import_failure_is_no_verdict(tmp_path, as_script):
    # A klayout module that cannot load stands in for any broken dependency.
    stub = tmp_path / "stub" / "klayout"
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text("")
    (stub / "db.py").write_text("raise ImportError('stub: klayout.db unavailable')\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        filter(None, [str(tmp_path / "stub"), os.environ.get("PYTHONPATH")])))
    if as_script:
        # Run as a script it used to exit 1, which reads as markers.
        cmd = [sys.executable, str(DRC_DIR / "run_drc.py"), "--path", "chip.gds"]
    else:
        # Imported, the error reaches the caller instead of ending its process.
        cmd = [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(DRC_DIR)!r}); import run_drc"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert "stub: klayout.db unavailable" in result.stderr
    assert result.returncode == (2 if as_script else 1), result.stderr
