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
"""One PDK version names one content of the files the PDK draws and checks
with.

The seal ring writes only the name and the version into a layout, so the
version has to rise whenever those files change. The files are the ones this
module selects: every file git lists under libs.tech, less the patterns
below. CONTENT holds one row per version, oldest first, with the digest of
those files; the newest row is the tree's. Adding, removing or changing one
of the files, comments included, needs a new version and a new row (see
CHANGELOG.md). A row is never edited or removed; that is the reviewer's to
hold, as this module reads only the order of the rows and the newest one.
"""

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[3]
GIT_TIMEOUT_S = 60
WITHOUT_GIT = "INTM4TM2_TREE_WITHOUT_GIT"

# (version, sha256 of the selected files), oldest first.
CONTENT = [
    ("0.8.0", "3f56cb59e2babff92d814cd7c09c66c3e73d94696fab6e1318b8fed856d0c8dd"),
]

DOCUMENTS = ("*.md", "*.rst", "*.pdf")
LICENCES = ("LICENSE*", "LICENCE*", "COPYING*", "NOTICE*")
OWN_TESTS = {"intm4tm2_tests", "testing"}
VENDORED = ("libs.tech/klayout/python/pycell4klayout-api", "libs.tech/klayout/python/pypreprocessor")
VENDORED_OUT_DIRS = {"tests", "Examples", "doc"}
VENDORED_OUT_FILES = {"setup.py", "MANIFEST.in", ".pylintrc"}


def selected(path):
    """Whether the file at `path` (relative to the top, POSIX) is one the
    version covers."""
    p = PurePosixPath(path)
    if p.parts[0] != "libs.tech":
        return False
    if any(p.match(pattern) for pattern in DOCUMENTS + LICENCES) or p.name == ".gitignore":
        return False
    if OWN_TESTS & set(p.parts[:-1]):
        return False
    for tree in VENDORED:
        if path.startswith(tree + "/"):
            rest = PurePosixPath(path[len(tree) + 1:])
            if VENDORED_OUT_DIRS & set(rest.parts[:-1]) or rest.name in VENDORED_OUT_FILES:
                return False
    return True


def _listed():
    """The files git lists under libs.tech. Fails when it cannot list them."""
    if not os.path.lexists(ROOT / ".git"):
        reason = os.environ.get(WITHOUT_GIT, "").strip()
        if reason:
            pytest.skip(f"tree without .git, declared by {WITHOUT_GIT}: {reason}")
        pytest.fail(
            "This tree has no .git, so the files the PDK version covers cannot be listed and the "
            "version check cannot run. Run the tests from a git clone. INTM4TM2_TREE_WITHOUT_GIT is "
            "for a copy of one commit made without its .git, such as an image or an install, whose "
            "commit already passed this check in its own CI: set it to a short reason and the check "
            "is skipped with that reason. Do not set it for a tree that has been changed since that "
            "commit.", pytrace=False)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git = shutil.which("git")
    try:
        if git is None:
            raise OSError("git is not on PATH")
        out = subprocess.run([git, "-C", str(ROOT), "ls-files", "-z", "--", "libs.tech"], env=env,
                             check=True, timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL,
                             capture_output=True).stdout
    except (OSError, subprocess.SubprocessError) as error:
        cause = getattr(error, "stderr", None)
        cause = os.fsdecode(cause).strip() if cause else str(error)
        pytest.fail(
            f"git could not list the files the PDK version covers ({cause}). This tree has a .git, "
            "so the check needs a working git: install git or repair the checkout, then run the "
            "tests again.", pytrace=False)
    return [os.fsdecode(name) for name in out.split(b"\0") if name]


def content_digest():
    """The sha256 over '<sha256 of the file>  <path>' lines of the selected
    files, sorted by path, read from the work tree. A listed file deleted
    from the work tree counts as removed."""
    lines = []
    for path in sorted(p for p in _listed() if selected(p)):
        if not os.path.lexists(ROOT / path):
            continue
        data = (ROOT / path).read_bytes()
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {path}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _declared_version():
    path = ROOT / "libs.tech/klayout/python/intm4tm2_pycell_lib/ihp/pdk_version.py"
    spec = importlib.util.spec_from_file_location("pdk_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.declared_version(ROOT)


def _release(version):
    found = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    assert found, f"{version!r} is not a MAJOR.MINOR.PATCH version"
    return tuple(int(part) for part in found.groups())


def test_rows_are_oldest_first_once_each():
    releases = [_release(version) for version, _ in CONTENT]
    assert releases, "CONTENT has no row"
    assert releases == sorted(set(releases)), f"CONTENT is not oldest first, once each: {CONTENT}"


def test_newest_row_names_this_tree():
    digest = content_digest()
    assert CONTENT and CONTENT[-1][1] == digest, (
        f"the files the PDK version covers changed since the newest row of CONTENT: raise the "
        f"version as CHANGELOG.md says, with the revision in libs.tech/adk/technology.json among "
        f"the edits, then run this test again and add the row it prints; as the tree stands now "
        f"the row is (\"<new version>\", \"{digest}\")")


def test_newest_row_is_the_declared_version():
    declared = _declared_version()
    assert CONTENT and CONTENT[-1][0] == declared, (
        f"pyproject.toml declares {declared}, the newest row of CONTENT is "
        f"{CONTENT[-1][0] if CONTENT else 'missing'}: add the row the failing "
        f"test_newest_row_names_this_tree prints, under {declared}")


@pytest.mark.parametrize("path, covered", [
    ("libs.tech/klayout/python/intm4tm2_pycell_lib/ihp/sealring_code.py", True),
    ("libs.tech/klayout/tech/drc/intm4tm2.drc", True),
    ("libs.tech/klayout/python/pycell4klayout-api/source/python/cni/box.py", True),
    ("libs.tech/klayout/README.md", False),
    ("libs.tech/openems/openems_intm4tm2/LICENSE", False),
    ("libs.tech/klayout/python/pypreprocessor/.gitignore", False),
    ("libs.tech/klayout/intm4tm2_tests/test_pdk_version.py", False),
    ("libs.tech/klayout/tech/drc/testing/run_regression.py", False),
    ("libs.tech/klayout/python/pypreprocessor/tests/maintest.py", False),
    ("libs.tech/klayout/python/pypreprocessor/Examples/debug.py", False),
    ("libs.tech/klayout/python/pypreprocessor/setup.py", False),
    ("libs.tech/klayout/python/pycell4klayout-api/doc/pycell4klayout.pdf", False),
    ("libs.ref/intm4tm2_examples/gds/T591_BEOL_D1_300u.gds", False),
])
def test_what_the_version_covers(path, covered):
    assert selected(path) is covered
