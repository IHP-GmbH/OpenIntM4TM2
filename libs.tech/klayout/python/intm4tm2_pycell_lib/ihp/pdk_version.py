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
"""The PDK version a layout records: 'intm4tm2 <version>'.

The version is the one the [project] table of the tree's pyproject.toml
declares, or 'unknown'. The label names nothing else that changes between
two commits of one version, so a layout drawn at any commit of a version is
the layout drawn at any other; CHANGELOG.md says when the version rises.

' (local changes)' is appended when the tree is the top of its own git work
tree and a tracked file differs from its commit. Nothing is appended when
the tree has no .git, when it only sits inside another repository, when its
.git cannot be followed, or when git is missing, fails or hangs: the label
says only what it knows. GIT_* variables in the environment are ignored.

In a checkout each call starts two git processes, each bounded by
GIT_TIMEOUT_S; a tree without .git starts none. The seal ring calls it on
every evaluation, so its label follows the tree as its widths follow the
rule file.

Only the standard library is imported, so the module loads without KLayout.
"""

import os
import pathlib
import re
import subprocess

# The top of the tree, five directories above this file's own
# (libs.tech/klayout/python/intm4tm2_pycell_lib/ihp).
ROOT = pathlib.Path(__file__).resolve().parents[5]
NAME = 'intm4tm2'
LOCAL_CHANGES = ' (local changes)'
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


def _has_local_changes(root):
    """Whether `root` is the top of its own work tree with a tracked file
    differing from its commit; False when it is not such a top or git cannot
    tell."""
    top = _git(root, 'rev-parse', '--show-toplevel')
    # os.path.realpath does not raise on a loop; Path.resolve does up to
    # Python 3.12.
    if not top or os.path.realpath(top) != os.path.realpath(root):
        return False
    changes = _git(root, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no')
    return bool(changes)


def pdk_version(root=ROOT):
    """The label text for the tree at `root`. A path that is missing,
    unreadable or loops reads 'unknown' rather than raising."""
    version = declared_version(root) or 'unknown'
    # os.path.lexists, unlike Path.exists, does not raise on a directory it
    # may not search, and it counts a .git it cannot follow.
    has_git = os.path.lexists(os.path.join(root, '.git'))
    local = has_git and _has_local_changes(root)
    return f'{NAME} {version}' + (LOCAL_CHANGES if local else '')
