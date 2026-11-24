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
"""Two commits of one PDK version draw one seal ring, byte for byte.

The ring is drawn with tech/scripts/sealring.py, as a user draws it, in
copies of the tree committed to their own repositories. Two commits that
differ only in a README, made at different dates, give layouts equal once
the GDS timestamps (the BGNLIB and BGNSTR records) are set aside; a version
rise gives another label and other bytes.
"""

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

import klayout.db as kdb

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
SCRIPT = Path("libs.tech") / "klayout" / "tech" / "scripts" / "sealring.py"
KLAYOUT_BIN = shutil.which("klayout")
GIT = shutil.which("git")
TEXT = (63, 0)
# GDS record types whose bodies hold the modification and access times.
BGNLIB, BGNSTR = 0x01, 0x05

pytestmark = [pytest.mark.live_tool,
              pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH"),
              pytest.mark.skipif(GIT is None, reason="git not on PATH")]


def _git(path, *args, date=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    if date is not None:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run([GIT, "-C", str(path), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def _checkout(root, version, readme, date):
    """A copy of libs.tech/klayout with a pyproject.toml declaring `version`,
    committed at `date` in its own repository; its commit."""
    shutil.copytree(REPO_KLAYOUT, root / "libs.tech" / "klayout",
                    ignore=shutil.ignore_patterns("__pycache__", "intm4tm2_tests", "regression_run"))
    (root / "pyproject.toml").write_text(f'[project]\nname = "intm4tm2"\nversion = "{version}"\n')
    (root / "README.md").write_text(readme)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
         "commit", "-q", "--no-verify", "-m", "copy", date=date)
    return _git(root, "rev-parse", "HEAD")


def _draw(root, tmp_path):
    """The seal ring the copy at `root` draws, as GDS bytes."""
    home = tmp_path / f"klayout_home_{root.name}"
    home.mkdir()
    env = dict(os.environ, KLAYOUT_HOME=str(home))
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)
    output = tmp_path / f"{root.name}.gds"
    result = subprocess.run([KLAYOUT_BIN, "-zz", "-r", str(root / SCRIPT), "-rd", "length=1000",
                             "-rd", "width=800", "-rd", f"output={output}"],
                            capture_output=True, text=True, env=env, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    return output.read_bytes()


def _without_times(data):
    """`data` with the bodies of its BGNLIB and BGNSTR records zeroed."""
    out = bytearray(data)
    at = 0
    while at + 4 <= len(out):
        length = struct.unpack(">H", out[at:at + 2])[0]
        if length == 0:  # zero padding after ENDLIB
            assert not any(out[at:]), f"a zero-length GDS record at byte {at} before the end"
            break
        assert length >= 4 and at + length <= len(out), f"malformed GDS record at byte {at}"
        if out[at + 2] in (BGNLIB, BGNSTR):
            out[at + 4:at + length] = bytes(length - 4)
        at += length
    return bytes(out)


def _label(data, tmp_path):
    path = tmp_path / "read.gds"
    path.write_bytes(data)
    layout = kdb.Layout()
    layout.read(str(path))
    texts = layout.top_cell().begin_shapes_rec(layout.find_layer(*TEXT))
    return [it.shape().text_string for it in texts
            if it.shape().is_text() and it.shape().text_string.startswith("PDK version: ")]


def test_two_commits_one_version_one_layout(tmp_path):
    a = _checkout(tmp_path / "a", "9.8.7", "first\n", "2026-01-01T00:00:00Z")
    b = _checkout(tmp_path / "b", "9.8.7", "second\n", "2026-01-02T00:00:00Z")
    assert a != b
    first, second = _draw(tmp_path / "a", tmp_path), _draw(tmp_path / "b", tmp_path)
    assert _label(first, tmp_path) == _label(second, tmp_path) == ["PDK version: intm4tm2 9.8.7"]
    assert _without_times(first) == _without_times(second)


def test_version_rise_moves_layout(tmp_path):
    _checkout(tmp_path / "a", "9.8.7", "first\n", "2026-01-01T00:00:00Z")
    _checkout(tmp_path / "c", "9.9.0", "first\n", "2026-01-01T00:00:00Z")
    first, risen = _draw(tmp_path / "a", tmp_path), _draw(tmp_path / "c", tmp_path)
    assert _label(first, tmp_path) != _label(risen, tmp_path)
    assert _without_times(first) != _without_times(risen)
