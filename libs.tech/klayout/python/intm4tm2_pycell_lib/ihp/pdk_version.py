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
"""The PDK version a layout records: 'intm4tm2 <version> (<commit>)'.

The version is the one the [project] table of the tree's pyproject.toml
declares. The commit is the one the tree is at, so a layout names the exact
PDK it was made with:
- a tree with a .git entry, even one that cannot be followed, takes it
  from git, and only when the tree is the top of its own work tree, with
  '-dirty' when a tracked file differs from it;
- a tree without .git takes it from STAMP at its top: the commit id alone
  on one line, '-dirty' allowed;
- otherwise, or when git cannot read the tree, the label says 'commit
  unknown'.
A tree that only sits inside another repository never takes that
repository's commit, and GIT_* variables in the environment are ignored.

This repository never writes STAMP. Whatever installs the tree without .git
has to write it, and keep pyproject.toml beside it; otherwise the label reads
'unknown' for what is missing.

In a checkout each call starts up to two git processes, each bounded by
GIT_TIMEOUT_S. The seal ring calls it on every evaluation, so its label
follows the tree as its widths follow the rule file.

Only the standard library is imported, so the module loads without KLayout.
"""

import os
import pathlib
import re
import subprocess

# The top of the tree, five directories above this file's own
# (libs.tech/klayout/python/intm4tm2_pycell_lib/ihp).
ROOT = pathlib.Path(__file__).resolve().parents[5]
STAMP = '.shipped-ref'
NAME = 'intm4tm2'
COMMIT_ID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
STAMPED_ID = re.compile(rf'(?:{COMMIT_ID.pattern})(?:-dirty)?')
# A git that hangs holds the PCell for at most this long per call. Nothing in
# the process can stop one stuck in uninterruptible I/O on a dead hard mount.
GIT_TIMEOUT_S = 10


def declared_version(root=ROOT):
    """The version in the [project] table of root/pyproject.toml, or None.
    Read line by line rather than with tomllib, which Python 3.10 lacks: the
    form is 'version = "<version>"' on its own line."""
    try:
        text = (pathlib.Path(root) / 'pyproject.toml').read_text(encoding='utf-8')
    except (OSError, ValueError):
        return None
    table = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('['):
            table = line
        elif table == '[project]':
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"', line)
            if match:
                return match.group(1)
    return None


def _git(root, *args):
    """git's output in `root`, or None when git fails, hangs or is not
    installed."""
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    try:
        out = subprocess.run(['git', '-C', str(root), *args], env=env, check=True,
                             timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return os.fsdecode(out).strip()


def _git_commit(root):
    """The commit of the work tree whose top is `root`, '-dirty' appended when
    a tracked file differs from it; None when `root` is not such a top or git
    cannot read it."""
    out = _git(root, 'rev-parse', '--show-toplevel', 'HEAD')
    lines = out.splitlines() if out else []
    # os.path.realpath does not raise on a loop; Path.resolve does up to
    # Python 3.12.
    if len(lines) != 2 or os.path.realpath(lines[0]) != os.path.realpath(root):
        return None
    commit = lines[1]
    changes = _git(root, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no')
    if changes is None or not COMMIT_ID.fullmatch(commit):
        return None
    return commit + ('-dirty' if changes else '')


def _stamped_commit(root):
    """The commit id in root/STAMP, or None when the file is missing or holds
    anything else."""
    try:
        commit = (pathlib.Path(root) / STAMP).read_text(encoding='utf-8').strip()
    except (OSError, ValueError):
        return None
    return commit if STAMPED_ID.fullmatch(commit) else None


def pdk_version(root=ROOT):
    """The label text for the tree at `root`. A path that is missing,
    unreadable or loops reads 'unknown' rather than raising."""
    version = declared_version(root) or 'unknown'
    # os.path.lexists, unlike Path.exists, does not raise on a directory it
    # may not search, and it counts a .git it cannot follow.
    has_git = os.path.lexists(os.path.join(root, '.git'))
    commit = _git_commit(root) if has_git else _stamped_commit(root)
    return f'{NAME} {version} ({commit or "commit unknown"})'
