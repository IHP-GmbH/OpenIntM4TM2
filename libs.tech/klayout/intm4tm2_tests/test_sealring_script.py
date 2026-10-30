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
"""Regression for tech/scripts/sealring.py, the headless command that adds
the seal ring to a layout.

The script runs as a user runs it, 'klayout -zz -r' with -rd defines. Its
sizes, offset and input handling are checked on an IHP-made ring: an example
layout with its ring removed gets the ring back from the script at the
example's own outline, and the result equals the example except for the
ring's version marks and labels. Every refusal exits 1 with one error line
and writes nothing, including a library module loaded from another tree and
another layer table. A copy of the tree without .git, as an install has it,
labels the ring with the commit its install stamp names, or 'commit unknown'.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import klayout.db as kdb

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_KLAYOUT / "tech" / "scripts" / "sealring.py"
LYP = REPO_KLAYOUT / "tech" / "intm4tm2.lyp"
EXAMPLE = REPO_KLAYOUT.parents[1] / "libs.ref" / "intm4tm2_examples" / "gds" / "T591_BEOL_D1_300u.gds"
KLAYOUT_BIN = shutil.which("klayout")

EDGESEAL = (39, 0)
BOUNDARY = (39, 4)
METAL4 = (50, 0)
VERSION_MARKS = (50, 22)
TEXT = (63, 0)

pytestmark = [pytest.mark.live_tool,
              pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")]


def _run(tmp_path, klayout_path=None, lyp=None, script=SCRIPT, **defines):
    home = tmp_path / "klayout_home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, KLAYOUT_HOME=str(home))
    env.pop("KLAYOUT_LYP_FILE", None)
    env.pop("KLAYOUT_PATH", None)
    if klayout_path is not None:
        env["KLAYOUT_PATH"] = str(klayout_path)
    if lyp is not None:
        env["KLAYOUT_LYP_FILE"] = str(lyp)
    args = [KLAYOUT_BIN, "-zz", "-r", str(script)]
    for name, value in defines.items():
        args += ["-rd", f"{name}={value}"]
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=300)


def _read(path):
    layout = kdb.Layout()
    layout.read(str(path))
    return layout


def _region(layout, cell, key):
    index = layout.find_layer(*key)
    region = kdb.Region() if index is None else kdb.Region(cell.begin_shapes_rec(index))
    region.merge()
    return region


def _keys(*layouts):
    return {(info.layer, info.datatype) for layout in layouts for info in layout.layer_infos()}


def _um(nm):
    return "%d.%03d" % divmod(nm, 1000) if nm >= 0 else "-" + _um(-nm)


def test_ring_only_layout(tmp_path):
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, length="1000", width="800.0", output=output)
    assert result.returncode == 0, result.stdout + result.stderr
    last = result.stdout.splitlines()[-1]
    assert last.startswith(f"sealring: wrote {output}: outline 1000.0 x 800.0 um") and last.endswith("slit false")
    assert b"$$$CONTEXT_INFO$$$" not in output.read_bytes()
    layout = _read(output)
    top = layout.top_cell()
    assert top.name == "sealring_top"
    assert _region(layout, top, BOUNDARY).bbox() == kdb.Box(0, 0, 1000000, 800000)
    assert not _region(layout, top, EDGESEAL).holes().is_empty()
    assert not _region(layout, top, METAL4).holes().is_empty()


def test_slit(tmp_path):
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, length="1000", width="800", output=output, slit="true")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1].endswith("slit true")
    layout = _read(output)
    top = layout.top_cell()
    assert not _region(layout, top, EDGESEAL).holes().is_empty()
    assert _region(layout, top, METAL4).holes().is_empty()


def test_ring_restores_an_example_ring(tmp_path):
    # D1's outline is not square and its lower-left corner is negative.
    original = _read(EXAMPLE)
    top = original.top_cell()
    boundary = original.find_layer(*BOUNDARY)
    rings = [inst for inst in top.each_inst() if not inst.cell.bbox_per_layer(boundary).empty()]
    assert len(rings) == 1
    outline = rings[0].bbox()
    marks = kdb.Region(rings[0].cell.begin_shapes_rec(original.find_layer(*VERSION_MARKS)))
    marks = marks.transformed(rings[0].cplx_trans)
    expected = {key: _region(original, top, key) for key in _keys(original)}
    expected[VERSION_MARKS] -= marks
    assert outline.left < 0 and outline.bottom < 0 and outline.width() != outline.height()

    # A second copy to strip: regions read from a layout follow its edits.
    work = _read(EXAMPLE)
    ring = [inst for inst in work.top_cell().each_inst() if inst.bbox() == outline]
    assert len(ring) == 1
    ring_cell = ring[0].cell_index
    ring[0].delete()
    work.prune_cell(ring_cell, -1)
    stripped = tmp_path / "stripped.gds"
    work.write(str(stripped))
    restored_path = tmp_path / "restored.gds"
    result = _run(tmp_path, input=stripped, output=restored_path,
                  length=_um(outline.width()), width=_um(outline.height()),
                  offset_x=_um(outline.left), offset_y=_um(outline.bottom))
    assert result.returncode == 0, result.stdout + result.stderr

    restored = _read(restored_path)
    tops = restored.top_cells()
    same_top = len(tops) == 1 and tops[0].name == top.name
    assert same_top, "the ring went into a new top cell"
    for key in _keys(original, restored) - {TEXT}:
        assert (_region(restored, tops[0], key) ^ expected.get(key, kdb.Region())).is_empty(), key
    texts = tops[0].begin_shapes_rec(restored.find_layer(*TEXT))
    labels = [it.shape().text_string for it in texts if it.shape().is_text()]
    size = f"x={outline.width() / 1000:.1f} um ; y={outline.height() / 1000:.1f} um"
    assert any(size in label for label in labels)


def _two_tops(path):
    layout = kdb.Layout()
    layout.dbu = 0.001
    for name in ("A", "B"):
        layout.create_cell(name).shapes(layout.layer(*METAL4)).insert(kdb.Box(0, 0, 1000, 1000))
    layout.write(str(path))


def _coarse_dbu(path):
    layout = kdb.Layout()
    layout.dbu = 0.005
    layout.create_cell("A").shapes(layout.layer(*METAL4)).insert(kdb.Box(0, 0, 1000, 1000))
    layout.write(str(path))


REFUSALS = [
    ("missing", {}, "missing -rd length=, -rd width=, -rd output="),
    ("bad_slit", {"slit": "yes"}, "slit must be true or false, got 'yes'"),
    ("not_a_number", {"length": "abc"}, "length='abc' is not a length in um"),
    ("infinite", {"width": "inf"}, "width='inf' is not a length in um"),
    ("offgrid_size", {"length": "1000.002"}, "length=1000.002 um is not on the 5 nm grid"),
    ("offgrid_offset", {"offset_y": "0.001"}, "offset_y=0.001 um is not on the 5 nm grid"),
    ("too_small", {"length": "114.245"}, "the PCell drew nothing; its reason is printed above"),
    ("no_input", {"input": "missing.gds"}, "is not a file"),
    ("empty_input", {"input": ""}, "-rd input= is empty"),
    ("empty_output", {"output": ""}, "-rd output= is empty"),
    ("not_a_layout", {"input": "garbage.gds"}, "cannot read"),
    ("unwritable", {"output": "two_tops.gds/ring.gds"}, "cannot write"),
    ("two_tops", {"input": "two_tops.gds"}, "has 2 top cells, not one"),
    ("coarse_dbu", {"input": "coarse.gds"}, "has dbu 0.005 um, not 0.001"),
]


@pytest.mark.parametrize("name,defines,message", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_refusals(tmp_path, name, defines, message):
    _two_tops(tmp_path / "two_tops.gds")
    _coarse_dbu(tmp_path / "coarse.gds")
    (tmp_path / "garbage.gds").write_text("not a layout\n")
    output = tmp_path / "ring.gds"
    if name != "missing":
        defines = {"length": "1000", "width": "800", "output": output} | defines
    for key in ("input", "output"):
        if defines.get(key):
            defines[key] = tmp_path / defines[key]
    result = _run(tmp_path, **defines)
    assert result.returncode == 1, result.stdout + result.stderr
    last = result.stdout.splitlines()[-1]
    assert last.startswith("sealring: error: ") and message in last, last
    assert not output.exists()
    if name == "too_small":
        assert "below the 114.25 um minimum" in result.stdout + result.stderr


AUTORUN = """\
<?xml version="1.0" encoding="utf-8"?>
<klayout-macro>
 <autorun>true</autorun>
 <interpreter>python</interpreter>
 <text>{text}</text>
</klayout-macro>
"""


def _other_tree(tmp_path, module, load):
    """A KLAYOUT_PATH entry with a package `module` (dotted) of its own, as
    another checkout of a PDK has, whose autorun imports it ('import'), only
    appends its directory to sys.path ('append'), or puts it on sys.path
    ahead of this tree's directories ('ahead'). Returns the entry and the
    module's directory."""
    python = tmp_path / "other" / "python"
    directory = python
    for part in module.split("."):
        directory = directory / part
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("")
    macros = tmp_path / "other" / "klayout" / "pymacros"
    macros.mkdir(parents=True)
    ours = [str(REPO_KLAYOUT / "python"),
            str(REPO_KLAYOUT / "python" / "pycell4klayout-api" / "source" / "python")]
    text = {"import": f"sys.path.insert(0, {str(python)!r})\nimport {module}",
            "append": f"sys.path.append({str(python)!r})",
            "ahead": f"sys.path.insert(0, {str(python)!r})\nsys.path += {ours!r}"}[load]
    (macros / "autorun.lym").write_text(AUTORUN.format(text="import sys\n" + text))
    return macros.parent, directory


OTHER_TREES = [("intm4tm2_pycell_lib", "import"),
               # Makes the library's own import fail; the refusal still names it.
               ("cni.tech", "import"),
               # A package with __init__.py wins over this tree's, which has none.
               ("pypreprocessor.pypreprocessor", "append")]


@pytest.mark.parametrize("module,load", OTHER_TREES, ids=[m for m, _ in OTHER_TREES])
def test_module_of_another_tree_is_refused(tmp_path, module, load):
    entry, directory = _other_tree(tmp_path, module, load)
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, klayout_path=entry, length="1000", width="800", output=output)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"module {module} is loaded from {directory}" in result.stdout.splitlines()[-1]
    assert not output.exists()


def test_this_tree_goes_first_on_sys_path(tmp_path):
    # Another tree's library ahead of this tree's directories, not imported.
    entry, _ = _other_tree(tmp_path, "intm4tm2_pycell_lib", "ahead")
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, klayout_path=entry, length="1000", width="800", output=output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.exists()


def test_library_of_this_tree_on_klayout_path(tmp_path):
    # The installed form: KLAYOUT_PATH names this tree and its autorun has
    # already registered the technology and the library.
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, klayout_path=REPO_KLAYOUT, length="1000", width="800", output=output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _region(_read(output), _read(output).top_cell(), BOUNDARY).bbox() == kdb.Box(0, 0, 1000000, 800000)


def test_other_layer_table_is_refused(tmp_path):
    # The library takes its layer table from KLAYOUT_LYP_FILE when the file
    # exists. Any other file is refused, even a copy: the script cannot tell
    # what another table would retag.
    other = tmp_path / "other.lyp"
    shutil.copy(LYP, other)
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, lyp=other, length="1000", width="800", output=output)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1] == (
        f"sealring: error: KLAYOUT_LYP_FILE={other} would replace the IntM4TM2 layer table; unset it")
    assert not output.exists()


@pytest.mark.parametrize("lyp", ["this_tree", "link", "missing"])
def test_layer_table_that_changes_nothing_is_accepted(tmp_path, lyp):
    # A link to this tree's table is that table; the library ignores a
    # KLAYOUT_LYP_FILE that does not exist.
    path = {"this_tree": LYP, "link": tmp_path / "link.lyp",
            "missing": tmp_path / "missing.lyp"}[lyp]
    if lyp == "link":
        path.symlink_to(LYP)
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, lyp=path, length="1000", width="800", output=output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.exists()


@pytest.mark.parametrize("stamp", [None, "0123456789abcdef0123456789abcdef01234567"],
                         ids=["unstamped", "stamped"])
def test_install_without_git(tmp_path, stamp):
    # The tree as an install has it: no .git, and the commit, if any, in the
    # stamp the install writes at the top of the tree.
    root = tmp_path / "pdk"
    shutil.copytree(REPO_KLAYOUT, root / "libs.tech" / "klayout",
                    ignore=shutil.ignore_patterns("__pycache__", "intm4tm2_tests"))
    (root / "pyproject.toml").write_text('[project]\nname = "intm4tm2"\nversion = "9.8.7"\n')
    if stamp is not None:
        (root / ".shipped-ref").write_text(stamp + "\n")
    output = tmp_path / "ring.gds"
    result = _run(tmp_path, script=root / SCRIPT.relative_to(REPO_KLAYOUT.parents[1]),
                  length="1000", width="800", output=output)
    assert result.returncode == 0, result.stdout + result.stderr
    layout = _read(output)
    texts = layout.top_cell().begin_shapes_rec(layout.find_layer(*TEXT))
    labels = [it.shape().text_string for it in texts if it.shape().is_text()]
    versions = [label for label in labels if label.startswith("PDK version: ")]
    assert versions == [f"PDK version: intm4tm2 9.8.7 ({stamp or 'commit unknown'})"]
