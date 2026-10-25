# SPDX-License-Identifier: Apache-2.0
"""Pure tests for the interposer LVS verdict boundary; no KLayout process."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


LVS_DIR = Path(__file__).resolve().parents[1] / "tech" / "lvs"
SPEC = importlib.util.spec_from_file_location("interposer_run_lvs", LVS_DIR / "run_lvs.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def completed(reference=False, status="PASS", labeled=2, opens=0, shorts=0,
              match=True):
    mode = "reference" if reference else "connectivity"
    compare = ("LVS netlists match" if match else "LVS netlists do NOT match - see report database")
    return ("Connectivity OPEN: test\n" * opens + "Connectivity SHORT: test\n" * shorts +
            (compare if reference else "No schematic provided - connectivity check only") +
            "\n" + runner.COMPLETION + "\n" +
            f"INTERPOSER_LVS_RESULT mode={mode} status={status} labeled_nets={labeled} "
            f"opens={opens} shorts={shorts}\n")


@pytest.mark.parametrize("reference, options, code", [
    (False, {}, 0),
    (True, {}, 0),
    (False, {"opens": 1, "status": "FAIL"}, 1),
    (False, {"shorts": 1, "status": "FAIL"}, 1),
    (True, {"match": False, "status": "FAIL"}, 1),
    (True, {"opens": 1, "status": "FAIL"}, 1),
    (False, {"labeled": 0, "status": "INCONCLUSIVE"}, 2),
    (True, {"labeled": 0, "status": "INCONCLUSIVE"}, 2),
])
def test_completed_scoped_verdict(reference, options, code):
    assert runner.result_exit_code(completed(reference, **options), reference) == code


@pytest.mark.parametrize("output, reference", [
    ("", False),
    (completed() + completed(), False),
    (completed() + "INTERPOSER_LVS_RESULT malformed\n", False),
    (completed().replace(runner.COMPLETION, ""), False),
    (completed().replace("INTERPOSER_LVS_RESULT", "missing"), False),
    (completed(), True),
    (completed(True), False),
    (completed(labeled=0), False),
    (completed(opens=1), False),
    (completed(True, match=False), True),
    (completed(status="FAIL"), False),
    (completed().replace("opens=0", "opens=1"), False),
    (completed().replace("shorts=0", "shorts=1"), False),
    (completed().replace("labeled_nets=2", "labeled_nets=-1"), False),
    (completed(True).replace("LVS netlists match", ""), True),
    (completed(True) + "LVS netlists do NOT match\n", True),
    (completed() + "LVS netlists match\n", False),
    (completed().replace("No schematic provided - connectivity check only", ""), False),
])
def test_missing_ambiguous_or_contradictory_evidence_refuses(output, reference):
    with pytest.raises(RuntimeError):
        runner.result_exit_code(output, reference)


def test_spaceful_paths_remain_single_argv_values():
    assert runner.build_switches_args({"input": "/a b/layout.gds", "schematic": "/x y/ref.cir"}) == [
        "-rd", "input=/a b/layout.gds", "-rd", "schematic=/x y/ref.cir"]


@pytest.mark.parametrize("reference, options, code", [
    (False, {}, 0),
    (False, {"opens": 1, "status": "FAIL"}, 1),
    (True, {}, 0),
    (True, {"match": False, "status": "FAIL"}, 1),
])
def test_run_check_uses_current_artifacts_and_scoped_result(tmp_path, monkeypatch, reference, options, code):
    run_dir = tmp_path / "a space"
    run_dir.mkdir()
    output = completed(reference, **options)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        (run_dir / "layout_extracted.cir").write_text(".SUBCKT TOP VDD\n.ENDS\n")
        if reference:
            (run_dir / "layout.lvsdb").write_text("report")
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(runner, "_run", fake_run)
    switches = {"topcell": "TOP"}
    if reference:
        switches["schematic"] = "ref.cir"
    assert runner.run_check("deck.lvs", "layout.gds", str(run_dir), switches) == code
    assert (run_dir / "layout.log").read_text() == output
    assert f"target_netlist={run_dir / 'layout_extracted.cir'}" in calls[0][0]
    assert "shell" not in calls[0][1]


@pytest.mark.parametrize("failure", ["process", "missing", "empty", "incomplete", "stale"])
def test_run_check_never_passes_on_bad_execution_or_artifacts(tmp_path, monkeypatch, failure):
    called = []
    if failure == "stale":
        (tmp_path / "layout_extracted.cir").write_text("old")

    def fake_run(*args, **kwargs):
        called.append(True)
        if failure != "missing":
            (tmp_path / "layout_extracted.cir").write_text("" if failure == "empty" else "netlist")
        return SimpleNamespace(stdout="" if failure == "incomplete" else completed(),
                               returncode=9 if failure == "process" else 0)

    monkeypatch.setattr(runner, "_run", fake_run)
    with pytest.raises(RuntimeError):
        runner.run_check("deck.lvs", "layout.gds", str(tmp_path), {"topcell": "TOP"})
    assert bool(called) == (failure != "stale")


def test_missing_reference_report_cannot_pass(tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        (tmp_path / "layout_extracted.cir").write_text("netlist")
        return SimpleNamespace(stdout=completed(True), returncode=0)
    monkeypatch.setattr(runner, "_run", fake_run)
    with pytest.raises(RuntimeError):
        runner.run_check("deck.lvs", "layout.gds", str(tmp_path),
                         {"topcell": "TOP", "schematic": "reference.cir"})


def test_missing_subdeck_fails_closed_in_runset():
    source = (LVS_DIR / "intm4tm2.lvs").read_text()
    assert 'raise "Missing LVS rule deck: #{path}" unless File.file?(path)' in source
    assert 'File.exist?(path) ? File.read(path) : ""' not in source


@pytest.mark.parametrize("bad_reference", ["", "missing", "empty", "directory"])
def test_explicit_invalid_reference_never_downgrades_to_connectivity(tmp_path, monkeypatch, bad_reference):
    layout = tmp_path / "layout.gds"
    layout.write_bytes(b"input validation only")
    empty = tmp_path / "empty"
    empty.touch()
    directory = tmp_path / "directory"
    directory.mkdir()
    reference = str(tmp_path / bad_reference) if bad_reference else ""
    monkeypatch.setattr(runner, "check_klayout_version", lambda: None)
    monkeypatch.setattr(runner, "generate_klayout_switches",
                        lambda *args: pytest.fail("Invalid reference reached extraction"))
    with pytest.raises(SystemExit) as error:
        runner.main(str(tmp_path), {"--layout": str(layout), "--netlist": reference})
    assert error.value.code == 2
