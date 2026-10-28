# =========================================================================================
# Copyright 2026 IHP PDK Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========================================================================================

"""Add the IntM4TM2 seal ring to a layout, or write a layout holding only the
ring, in KLayout batch mode:

klayout -zz -r sealring.py \\
        -rd length=1000.0 -rd width=800.0 -rd output=sealring.gds

length (X) and width (Y) are the whole outline in um, the 39/4 sealring
boundary, as in the SG13G2 tech/scripts/sealring.py. Optional:
  -rd input=<layout>         add the ring to the only top cell of this layout
                             (otherwise to a new 'sealring_top' cell)
  -rd offset_x=, offset_y=   the outline's lower-left corner in um (default 0)
  -rd slit=true              cut the slit (default false)
Sizes and offsets must be on the 5 nm grid.

The PCell is taken from the library of the tree this script belongs to; a
library, or a module it imports, already loaded from another tree is refused
(run with -rx to skip the autorun macros that load it). The output carries no
PCell context. The script exits 0 after one 'sealring: wrote' line, or 1 after
one 'sealring: error:' line.
"""
# pylint: disable=import-error
import builtins
import decimal
import pathlib
import sys

import pya

LIB = 'IntM4TM2'
TECH = 'intm4tm2'
PCELL = 'sealring'
DBU = 0.001
GRID_NM = 5

KLAYOUT_DIR = pathlib.Path(__file__).resolve().parents[2]
PYTHON_DIR = KLAYOUT_DIR / 'python'
API_DIR = PYTHON_DIR / 'pycell4klayout-api' / 'source' / 'python'
LYT = KLAYOUT_DIR / 'tech' / 'intm4tm2.lyt'

# The library and the modules it imports, and the directory each comes from.
# cni is a package without __init__.py, so its modules are named instead.
SOURCES = {'intm4tm2_pycell_lib': PYTHON_DIR, 'cni.tech': API_DIR, 'cni.dlo': API_DIR,
           'pypreprocessor.pypreprocessor': PYTHON_DIR}


class SealringError(Exception):
    """A reason to stop, printed as the one error line."""


def define(name, default=None):
    """The value of '-rd name=value', or `default`. '-rd input' shadows the
    built-in input function, which therefore means "not given"."""
    value = globals().get(name, getattr(builtins, name, default))
    return default if callable(value) else value


def to_nm(name, text):
    """A length in um on the 5 nm grid, in nm."""
    try:
        value = decimal.Decimal(str(text).strip())
    except decimal.InvalidOperation:
        raise SealringError(f"{name}={text!r} is not a length in um") from None
    if not value.is_finite():
        raise SealringError(f"{name}={text!r} is not a length in um")
    nm = value * 1000
    if nm != nm.to_integral_value() or int(nm) % GRID_NM:
        raise SealringError(f"{name}={text} um is not on the {GRID_NM} nm grid")
    return int(nm)


def to_flag(name, text):
    if text not in ('true', 'false'):
        raise SealringError(f"{name} must be true or false, got {text!r}")
    return text == 'true'


def pcell_param(nm):
    return '%d.%03du' % divmod(nm, 1000)


def check_sources():
    """Refuse any module of SOURCES that is loaded from another directory."""
    for name, home in SOURCES.items():
        module = sys.modules.get(name)
        if module is None:
            continue
        directory = pathlib.Path(module.__file__).resolve().parent
        if home not in directory.parents:
            raise SealringError(f"module {name} is loaded from {directory}, not from {home}; "
                                "run with -rx or without the KLAYOUT_PATH entry that loads it")


def load_library():
    """Register the technology and the PCell library of this tree, and refuse
    a library, or a module it imports, that another tree loaded first."""
    if TECH not in pya.Technology.technology_names():
        pya.Technology.create_technology(TECH).load(str(LYT))
    # First on sys.path, ahead of any other tree's copy.
    for path in (API_DIR, PYTHON_DIR):
        while str(path) in sys.path:
            sys.path.remove(str(path))
        sys.path.insert(0, str(path))
    try:
        import intm4tm2_pycell_lib  # pylint: disable=import-outside-toplevel,unused-import
        failure = None
    except Exception as error:  # pylint: disable=broad-except
        failure = error
    # After the import, failed or not: a module imported earlier stays in
    # use, a foreign module may be what made it fail, and a package without
    # __init__.py, like this tree's pypreprocessor, loses to one with it
    # anywhere on sys.path.
    check_sources()
    if failure is not None:
        raise SealringError(f"cannot import the {LIB} library: {failure!r}")


def add_sealring(length_nm, width_nm, input_file, output, offset_nm, slit):
    """Write `output`: the layout read from `input_file` (or a new one) with
    the ring's outline at `offset_nm` in its only top cell."""
    layout = pya.Layout()
    if input_file:
        if not pathlib.Path(input_file).is_file():
            raise SealringError(f"input {input_file} is not a file")
        try:
            layout.read(input_file)
        except RuntimeError as error:
            raise SealringError(f"cannot read {input_file}: {error}") from None
        if abs(layout.dbu - DBU) > 1e-9:
            raise SealringError(f"{input_file} has dbu {layout.dbu} um, not {DBU}")
        tops = layout.top_cells()
        if len(tops) != 1:
            raise SealringError(f"{input_file} has {len(tops)} top cells, not one")
        top = tops[0]
    else:
        layout.dbu = DBU
        top = layout.create_cell('sealring_top')
    layout.technology_name = TECH

    ring = layout.create_cell(PCELL, LIB, {'l': pcell_param(length_nm), 'w': pcell_param(width_nm),
                                           'addSlit': 't' if slit else 'nil'})
    if ring is None:
        raise SealringError(f"the {LIB} library has no {PCELL} PCell")
    if ring.bbox().empty():
        raise SealringError("the PCell drew nothing; its reason is printed above")
    top.insert(pya.CellInstArray(ring.cell_index(), pya.Trans(pya.Vector(*offset_nm))))

    # No "$$$CONTEXT_INFO$$$": a reader with another PDK loaded would try to
    # rebuild the PCell from it.
    options = pya.SaveLayoutOptions()
    options.write_context_info = False
    try:
        pathlib.Path(output).parent.mkdir(parents=True, exist_ok=True)
        layout.write(output, options)
    except (OSError, RuntimeError) as error:
        raise SealringError(f"cannot write {output}: {error}") from None


def main():
    missing = [name for name in ('length', 'width', 'output') if define(name) is None]
    if missing:
        raise SealringError("missing " + ", ".join(f"-rd {name}=" for name in missing))
    for name in ('input', 'output'):
        if define(name) == '':
            raise SealringError(f"-rd {name}= is empty")
    length_nm = to_nm('length', define('length'))
    width_nm = to_nm('width', define('width'))
    offset_nm = (to_nm('offset_x', define('offset_x', '0')), to_nm('offset_y', define('offset_y', '0')))
    slit = to_flag('slit', define('slit', 'false'))
    output = define('output')
    load_library()
    add_sealring(length_nm, width_nm, define('input'), output, offset_nm, slit)
    print(f"sealring: wrote {output}: outline {length_nm / 1000} x {width_nm / 1000} um "
          f"at ({offset_nm[0] / 1000}, {offset_nm[1] / 1000}) um, slit {str(slit).lower()}")


try:
    main()
except SealringError as error:
    print(f"sealring: error: {error}")
    sys.exit(1)
