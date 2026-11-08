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
"""The DRC regression's provenance pin must read the artefact that runs.

GOLDEN marker sets are produced by the `klayout` BINARY: run_drc.run_deck
spawns `["klayout", "-b", "-r", ...]` and every deck runs in that subprocess.
The pip `klayout` Python module is a different artefact that merely shares the
name, and the two routinely differ on one host. A pin read from the module
therefore proves nothing about the run: it can refuse a correct setup (module
0.30.3 with the pinned 0.30.5 binary) and it can pass a mismatched one (module
0.30.5 with any other binary on PATH), which is exactly the hazard the pin
exists to close.

These tests discriminate that, rather than restating it. The first one fails
against a module-reading implementation by construction: the fake binary and
the real module cannot both be the version the probe returns. The second
covers the other half of the rule, that an identity which cannot be determined
is a refusal and never a recorded value.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

DRC_TESTING_DIR = (Path(__file__).resolve().parents[1]
                   / "tech" / "drc" / "testing")
sys.path.insert(0, str(DRC_TESTING_DIR))

import run_regression  # noqa: E402


def _fake_klayout(directory, version_line, exit_code=0):
    """Write a fake `klayout` on PATH that answers -v with version_line."""
    script = directory / "klayout"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "{version_line}"\n'
        f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return script


def test_probe_reads_the_binary_not_the_like_named_module(tmp_path, monkeypatch):
    """The version must come from the spawned binary, not from `import klayout`.

    The fake binary reports a version no pip module would report, so an
    implementation that reads the module cannot return it.
    """
    _fake_klayout(tmp_path, "KLayout 1.2.3")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert run_regression._detect_klayout_version() == "1.2.3"


def test_probe_disagreement_with_the_module_resolves_to_the_binary(tmp_path,
                                                                   monkeypatch):
    """With module and binary disagreeing, the binary wins and the pin fires.

    This is the shape that was actually broken: the host had the pinned binary
    and an older module, and the pin refused a correct setup.
    """
    _fake_klayout(tmp_path, "KLayout 0.29.99")
    monkeypatch.setenv("PATH", str(tmp_path))

    detected = run_regression._detect_klayout_version()
    assert detected == "0.29.99"
    with pytest.raises(RuntimeError) as excinfo:
        run_regression.assert_canonical_config(detected)
    message = str(excinfo.value)
    assert "0.29.99" in message
    assert run_regression.CANONICAL_KLAYOUT_VERSION in message


def test_probe_returns_none_when_no_binary_is_reachable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir, no klayout

    assert run_regression._detect_klayout_version() is None


def test_probe_returns_none_on_empty_output(tmp_path, monkeypatch):
    _fake_klayout(tmp_path, "")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert run_regression._detect_klayout_version() is None


def test_undeterminable_version_is_refused_not_recorded():
    """An identity that cannot be read is a refusal, never a value.

    Fail-open here would let a probe failure of any kind, a missing binary or a
    stray PATH, yield a run that compares against GOLDEN under an unknown
    KLayout.
    """
    with pytest.raises(RuntimeError) as excinfo:
        run_regression.assert_canonical_config(None)
    assert "could not determine" in str(excinfo.value)


def test_canonical_version_is_accepted():
    """Control: the pinned version passes, so the tests above fail for the
    stated reason and not because the assertion refuses everything."""
    run_regression.assert_canonical_config(
        run_regression.CANONICAL_KLAYOUT_VERSION)


def test_run_mode_and_threads_are_pinned_by_construction():
    """They are not asserted because nothing can vary them.

    run_table passes the constants to every deck run and the CLI exposes no
    override, so a check against those same constants could never fail. If this
    test starts failing, run_mode or threads became configurable and the
    assertion must take the value the run will actually use.
    """
    source = (DRC_TESTING_DIR / "run_regression.py").read_text()
    assert "threads=CANONICAL_THREADS" in source
    assert "run_mode=CANONICAL_RUN_MODE" in source

    completed = subprocess.run(
        [sys.executable, str(DRC_TESTING_DIR / "run_regression.py"), "--help"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(DRC_TESTING_DIR)},
    )
    assert "--run-mode" not in completed.stdout
    assert "--threads" not in completed.stdout
