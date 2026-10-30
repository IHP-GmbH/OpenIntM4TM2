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
"""Regression for intm4tm2_pycell_lib/ihp/pdk_version.py, the PDK version
the seal ring writes on TEXT 63/0.

Each case builds a tree with its own pyproject.toml and, where it needs one,
its own git repository, and checks the whole label: the commit comes from
the tree's own checkout, else from the install stamp, else it reads 'commit
unknown'. Neither a repository the tree only sits in nor GIT_* variables in
the environment can lend the tree another commit, and a stamp never speaks
for a tree with .git.
"""

import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "python" / "intm4tm2_pycell_lib" / "ihp" / "pdk_version.py"
GIT = shutil.which("git")

# The library package registers itself with KLayout when imported; this
# module needs only the standard library, so it is loaded on its own.
_spec = importlib.util.spec_from_file_location("pdk_version", MODULE)
pdk_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pdk_version)

needs_git = pytest.mark.skipif(GIT is None, reason="git not on PATH")

STAMPED = "0123456789abcdef0123456789abcdef01234567"
# Only the [project] table's version counts.
PYPROJECT = ('[build-system]\nversion = "0.0.1"\n\n'
             '[project]\nname = "intm4tm2"\nversion = "9.8.7"\n\n'
             '[tool.other]\nversion = "0.0.2"\n')


def _label(commit, version="9.8.7"):
    return f"intm4tm2 {version} ({commit})"


def _tree(path, stamp=None):
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(PYPROJECT)
    if stamp is not None:
        (path / ".shipped-ref").write_text(stamp)
    return path


def _git(path, *args):
    # The user's git configuration (hooks, signing) stays out of these repositories.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run([GIT, "-C", str(path), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(path):
    """Make `path` a repository with everything in it committed; its HEAD."""
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--allow-empty", "-m", "test")
    return _git(path, "rev-parse", "HEAD")


def test_declared_version_is_the_project_tables(tmp_path):
    assert pdk_version.declared_version(_tree(tmp_path)) == "9.8.7"


def test_this_tree_declares_a_version():
    assert pdk_version.declared_version() is not None


def test_undeclared_version(tmp_path):
    assert pdk_version.pdk_version(tmp_path) == _label("commit unknown", "unknown")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "intm4tm2"\n')
    assert pdk_version.pdk_version(tmp_path) == _label("commit unknown", "unknown")


def test_install_without_git(tmp_path):
    assert pdk_version.pdk_version(_tree(tmp_path)) == _label("commit unknown")


@pytest.mark.parametrize("stamp", [STAMPED, STAMPED + "\n", STAMPED + "-dirty\n"],
                         ids=["bare", "newline", "dirty"])
def test_install_with_stamp(tmp_path, stamp):
    assert pdk_version.pdk_version(_tree(tmp_path, stamp)) == _label(stamp.strip())


@pytest.mark.parametrize("stamp", ["", STAMPED[:7], STAMPED.upper(), f"{STAMPED}\n{STAMPED}\n", "v0.2.0"],
                         ids=["empty", "short", "upper", "two", "tag"])
def test_stamp_that_is_not_one_commit_id(tmp_path, stamp):
    assert pdk_version.pdk_version(_tree(tmp_path, stamp)) == _label("commit unknown")


@needs_git
def test_own_checkout(tmp_path):
    root = _tree(tmp_path / "pdk")
    head = _commit(root)
    assert pdk_version.pdk_version(root) == _label(head)


@needs_git
def test_tracked_change_is_dirty_untracked_file_is_not(tmp_path):
    root = _tree(tmp_path / "pdk")
    head = _commit(root)
    (root / "untracked.txt").write_text("not in the PDK\n")
    assert pdk_version.pdk_version(root) == _label(head)
    (root / "pyproject.toml").write_text(PYPROJECT + "# local change\n")
    assert pdk_version.pdk_version(root) == _label(head + "-dirty")


@needs_git
def test_checkout_wins_over_stamp(tmp_path):
    root = _tree(tmp_path / "pdk", STAMPED)
    head = _commit(root)
    assert pdk_version.pdk_version(root) == _label(head)


@needs_git
def test_repository_without_commits(tmp_path):
    root = _tree(tmp_path / "pdk")
    _git(root, "init", "-q")
    assert pdk_version.pdk_version(root) == _label("commit unknown")


@needs_git
@pytest.mark.parametrize("stamp", [None, STAMPED], ids=["unstamped", "stamped"])
def test_tree_inside_another_repository(tmp_path, stamp):
    # A PDK copied into a design's repository is not at that repository's commit.
    root = _tree(tmp_path / "design" / "pdk", stamp)
    _commit(tmp_path / "design")
    assert pdk_version.pdk_version(root) == _label(stamp or "commit unknown")


@needs_git
def test_broken_git_dir_inside_another_repository(tmp_path):
    # git passes over a .git that is not a repository and finds the one the
    # tree sits in.
    root = _tree(tmp_path / "design" / "pdk")
    _commit(tmp_path / "design")
    (root / ".git").mkdir()
    assert pdk_version.pdk_version(root) == _label("commit unknown")


@needs_git
def test_git_variables_do_not_redirect(tmp_path, monkeypatch):
    # As in a git hook of another repository that draws a ring.
    other = _tree(tmp_path / "other")
    (other / "design.txt").write_text("another repository\n")
    other_head = _commit(other)
    root = _tree(tmp_path / "pdk")
    head = _commit(root)
    assert head != other_head
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root))
    assert pdk_version.pdk_version(root) == _label(head)


@needs_git
def test_git_not_installed(tmp_path, monkeypatch):
    # A stamp left in a checkout is not the checkout's commit.
    root = _tree(tmp_path / "pdk", STAMPED)
    _commit(root)
    monkeypatch.setenv("PATH", str(tmp_path / "no-git"))
    assert pdk_version.pdk_version(root) == _label("commit unknown")


def test_hung_git(tmp_path, monkeypatch):
    root = _tree(tmp_path / "pdk", STAMPED)
    (root / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(f"#!/bin/sh\nexec {shutil.which('sleep')} 30\n")
    (bin_dir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(pdk_version, "GIT_TIMEOUT_S", 0.5)
    start = time.monotonic()
    assert pdk_version.pdk_version(root) == _label("commit unknown")
    assert 0.5 <= time.monotonic() - start < 5
