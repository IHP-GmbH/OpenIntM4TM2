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
- from git, when the tree is the top of its own work tree, with '-dirty'
  when a tracked file differs from that commit;
- otherwise from STAMP at the top of the tree, which an install without
  .git writes: the commit id alone on one line;
- otherwise 'commit unknown'.
A tree that only sits inside another repository never takes that
repository's commit, and GIT_* variables in the environment are ignored.

Only the standard library is imported, so the module loads without KLayout.
"""

import os
import pathlib
import re
import subprocess

# The top of the tree: libs.tech/klayout/python/intm4tm2_pycell_lib/ihp/.
ROOT = pathlib.Path(__file__).resolve().parents[5]
STAMP = '.shipped-ref'
NAME = 'intm4tm2'
COMMIT_ID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def declared_version(root=ROOT):
    """The version in the [project] table of root/pyproject.toml, or None."""
    try:
        text = (pathlib.Path(root) / 'pyproject.toml').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
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
    """git's output in `root`, or None when git fails or is not installed."""
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    try:
        return subprocess.run(['git', '-C', str(root), *args], env=env, check=True, text=True,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_commit(root=ROOT):
    """The commit of the work tree whose top is `root`, '-dirty' appended when
    a tracked file differs from it; None when `root` is not such a top or git
    cannot read it."""
    top = _git(root, 'rev-parse', '--show-toplevel')
    if not top or pathlib.Path(top).resolve() != pathlib.Path(root).resolve():
        return None
    commit = _git(root, 'rev-parse', '--verify', 'HEAD')
    changes = _git(root, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no')
    if commit is None or changes is None or not COMMIT_ID.fullmatch(commit):
        return None
    return commit + ('-dirty' if changes else '')


def stamped_commit(root=ROOT):
    """The commit id in root/STAMP, or None when the file is missing or holds
    anything else."""
    try:
        commit = (pathlib.Path(root) / STAMP).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return None
    return commit if COMMIT_ID.fullmatch(commit) else None


def pdk_version(root=ROOT):
    version = declared_version(root) or 'unknown'
    commit = git_commit(root) or stamped_commit(root) or 'commit unknown'
    return f'{NAME} {version} ({commit})'
