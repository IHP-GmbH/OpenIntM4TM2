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
its own git repository, and checks the whole label. The label is the name and
the declared version, and nothing that changes between two commits of one
version: two commits of one version write one label, a version rise writes
another. A tree that is the top of its own work tree with a tracked file
changed adds '(local changes)'. Neither a repository the tree only sits in
nor GIT_* variables in the environment can lend the tree another state, and
an install stamp is never read.
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
CLEAN = "intm4tm2 9.8.7"
LOCAL = "intm4tm2 9.8.7 (local changes)"


def _tree(path, stamp=None):
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(PYPROJECT)
    (path / "cell.py").write_text("# a file the PDK draws with\n")
    if stamp is not None:
        (path / ".shipped-ref").write_text(stamp)
    return path


def _git(path, *args, date=None):
    # The user's git configuration (hooks, signing) stays out of these repositories.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    if date is not None:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run([GIT, "-C", str(path), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


IDENTITY = ("-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false")


def _commit(path, date=None, message="test"):
    """Commit everything in `path`, making it a repository first if it is
    not one; its HEAD."""
    if not (path / ".git").exists():
        _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, *IDENTITY, "commit", "-q", "--no-verify", "--allow-empty", "-m", message, date=date)
    return _git(path, "rev-parse", "HEAD")


def _edit(path):
    """Change a tracked file of the tree at `path`."""
    (path / "cell.py").write_text("# a file the PDK draws with, changed\n")


def test_declared_version_is_the_project_tables(tmp_path):
    assert pdk_version.declared_version(_tree(tmp_path)) == "9.8.7"


def test_this_tree_declares_a_version():
    assert pdk_version.declared_version() is not None


def test_undeclared_version(tmp_path):
    assert pdk_version.pdk_version(tmp_path) == "intm4tm2 unknown"
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "intm4tm2"\n')
    assert pdk_version.pdk_version(tmp_path) == "intm4tm2 unknown"


@pytest.mark.parametrize("tree", [pytest.param("checkout", marks=needs_git), "without_git",
                                  "without_git_stamped"])
def test_label_is_name_and_version(tmp_path, tree):
    root = _tree(tmp_path / "pdk", STAMPED + "\n" if tree == "without_git_stamped" else None)
    if tree == "checkout":
        _commit(root)
    assert pdk_version.pdk_version(root) == CLEAN


@pytest.mark.parametrize("stamp", [STAMPED, STAMPED + "\n", STAMPED + "-dirty\n", "", STAMPED[:7],
                                   STAMPED.upper(), f"{STAMPED}\n{STAMPED}\n", "v0.2.0"],
                         ids=["bare", "newline", "dirty", "empty", "short", "upper", "two", "tag"])
def test_stamp_is_not_read(tmp_path, stamp):
    assert pdk_version.pdk_version(_tree(tmp_path, stamp)) == CLEAN


@needs_git
def test_stamp_beside_a_checkout_is_not_read(tmp_path):
    root = _tree(tmp_path / "pdk", STAMPED)
    _commit(root)
    assert pdk_version.pdk_version(root) == CLEAN


@needs_git
def test_two_commits_one_version_one_label(tmp_path):
    # A, then B with only a README changed, then C with A's tree on top of B:
    # three commits, each at its own date, of one version.
    root = _tree(tmp_path / "pdk")
    a = _commit(root, date="2026-01-01T00:00:00Z")
    (root / "README.md").write_text("changed\n")
    b = _commit(root, date="2026-01-02T00:00:00Z")
    c = _git(root, *IDENTITY, "commit-tree", f"{a}^{{tree}}", "-p", b, "-m", "c",
             date="2026-01-03T00:00:00Z")
    assert len({a, b, c}) == 3
    labels = []
    for commit in (a, b, c):
        _git(root, "checkout", "-q", "--detach", commit)
        labels.append(pdk_version.pdk_version(root))
    assert labels == [CLEAN] * 3


@needs_git
def test_version_rise_moves_label(tmp_path):
    root = _tree(tmp_path / "pdk")
    _commit(root)
    before = pdk_version.pdk_version(root)
    (root / "pyproject.toml").write_text(PYPROJECT.replace('version = "9.8.7"', 'version = "9.9.0"'))
    _commit(root)
    assert (before, pdk_version.pdk_version(root)) == (CLEAN, "intm4tm2 9.9.0")


@needs_git
@pytest.mark.parametrize("change, label", [
    ("tracked", LOCAL),
    ("untracked", CLEAN),
    ("pyproject_removed", "intm4tm2 unknown (local changes)"),
])
def test_local_changes(tmp_path, change, label):
    root = _tree(tmp_path / "pdk")
    _commit(root)
    if change == "tracked":
        _edit(root)
    elif change == "untracked":
        (root / "untracked.txt").write_text("not in the PDK\n")
    else:
        (root / "pyproject.toml").unlink()
    assert pdk_version.pdk_version(root) == label


@needs_git
def test_repository_without_commits(tmp_path):
    root = _tree(tmp_path / "pdk")
    _git(root, "init", "-q")
    assert pdk_version.pdk_version(root) == CLEAN


@needs_git
@pytest.mark.parametrize("stamp", [None, STAMPED], ids=["unstamped", "stamped"])
def test_tree_inside_another_repository(tmp_path, stamp):
    # A PDK copied into a design's repository does not take that repository's
    # state, even with a tracked file under the PDK changed.
    root = _tree(tmp_path / "design" / "pdk", stamp)
    _commit(tmp_path / "design")
    _edit(root)
    assert pdk_version.pdk_version(root) == CLEAN


@needs_git
def test_broken_git_dir_inside_another_repository(tmp_path):
    # git passes over a .git that is not a repository and finds the one the
    # tree sits in, whose tracked file under the tree is changed.
    root = _tree(tmp_path / "design" / "pdk")
    _commit(tmp_path / "design")
    _edit(root)
    (root / ".git").mkdir()
    assert pdk_version.pdk_version(root) == CLEAN


@needs_git
def test_git_variables_do_not_redirect(tmp_path, monkeypatch):
    # As in a git hook of another repository that draws a ring: the tree's
    # own state counts, in both directions.
    other = _tree(tmp_path / "other")
    (other / "design.txt").write_text("another repository\n")
    _commit(other)
    root = _tree(tmp_path / "pdk")
    _commit(root)
    _edit(other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root))
    assert pdk_version.pdk_version(root) == CLEAN
    _git(other, "checkout", "-q", "--", "cell.py")
    _edit(root)
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert pdk_version.pdk_version(root) == LOCAL


@needs_git
def test_git_not_installed(tmp_path, monkeypatch):
    root = _tree(tmp_path / "pdk", STAMPED)
    _commit(root)
    _edit(root)
    monkeypatch.setenv("PATH", str(tmp_path / "no-git"))
    assert pdk_version.pdk_version(root) == CLEAN


def test_directory_it_may_not_search(tmp_path):
    # Path.exists raises PermissionError here, up to Python 3.13.
    root = _tree(tmp_path / "pdk")
    root.chmod(0o600)
    try:
        if os.access(root / "pyproject.toml", os.R_OK):
            pytest.skip("this user reads through the mode bits")
        assert pdk_version.pdk_version(root) == "intm4tm2 unknown"
    finally:
        root.chmod(0o755)


def test_git_entry_it_cannot_follow(tmp_path):
    root = _tree(tmp_path / "pdk", STAMPED)
    (root / ".git").symlink_to(tmp_path / "gone.git")
    assert pdk_version.pdk_version(root) == CLEAN


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
    assert pdk_version.pdk_version(root) == CLEAN
    assert 0.5 <= time.monotonic() - start < 5
