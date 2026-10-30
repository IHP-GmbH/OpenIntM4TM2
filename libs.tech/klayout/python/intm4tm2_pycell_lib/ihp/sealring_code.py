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
"""Seal ring for the IntM4TM2 interposer.

The outline given by l (X) and w (Y) is the sealring boundary: it is drawn
on EdgeSeal.boundary (39/4) and the ring sits inside it. l and w are the
whole outline, unlike the SG13G2 sealring whose l and w leave out its edge
box. The scribe lane is outside the outline and is added by the fab, so this
cell has no scribe parameter. The origin is the lower-left corner of the
outline.

The ring is the backend-only part of the IHP seal ring, drawn as the open
SG13G2 magic generator draws it (ihp-sg13g2/libs.tech/magic/sealring_corner.tcl
and sealring_side.tcl in IHP-Open-PDK): EdgeSeal plus Metal4, Metal5,
TopMetal1 and TopMetal2 on one footprint, Seal.a wide on the straight sides;
continuous Via4, TopVia1 and TopVia2 rings Seal.d from its outer edge; and a
Passiv ring Seal.e wide, 3 um outside it. Each corner is a four-step
staircase in a Seal.k square: the outer edge steps by (Seal.k - Seal.a) / 4
along each axis, and the inner edge is the outer one moved by Seal.a in X and
one step in Y, ending at (Seal.k, Seal.a) where it meets the straight. The
corner is the same piece turned by 90 degrees at each corner, so the ring is
not mirror-symmetric. Every shape is orthogonal and on the 5 nm grid.

addSlit='t' cuts the ring once, at the middle of the left side, for designs
that cannot have a closed metal loop around the board. Each metal is cut by
its minimum space (Mn.e, TM1.b, TM2.b); the via rings stop Seal.d short of
both sides of a 0.21 um notch (SG13G2 Act.b), so their cut is wider than
every metal cut. EdgeSeal and Passiv stay closed.

Two TEXT labels follow the SG13G2 sealring: the registration size (outline
x and y, and its area in mm^2) and the PDK version. The version label names
the declared version and the commit the tree is at; pdk_version.py says
where each comes from.

Widths are the public SG13G2 section 6.10 values, read from the DRC rule
file rather than from techParams (as bondpad does) because they are rule
values: Seal.a ring width, Seal.c1/c2/c3 via ring widths, Seal.e Passiv ring
width, Seal.k corner span; and the metal spaces that size the slit.
"""

__version__ = '$Revision: #1 $'

import json
import os
import re

from cni.dlo import *
from .geometry import *
from .pdk_version import pdk_version
from .utility_functions import *

# Section 6.10 introduction and Fig. 6.12: EdgeSeal sits 30 um from the
# sealring boundary. The value has no rule ID and no DRC check; it is drawn
# as the minimum.
BOUNDARY_TO_EDGESEAL_NM = 30000
# Drawing values without a rule of their own on this stack. Seal.d (1.30) is
# the SG13G2 EdgeSeal-Activ enclosure of the via rings; with no Activ it is
# drawn as the via rings' offset from the outer edge of the ring metal.
# Seal.f is a 1.00 minimum; the ring is drawn 3.00 from Passiv, the value of
# the open SG13G2 rule file and of its magic generator.
VIA_OFFSET_NM = 1300
PASSIV_SPACE_NM = 3000
# SG13G2 Act.b, the notch the via rings are cut around in a slit.
SLIT_VIA_NOTCH_NM = 210

CORNER_STEPS = 4
GRID_NM = 5

RULES_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.realpath(__file__)), '..', '..', '..',
    'tech', 'drc', 'rule_decks', 'interposer_tech_default.json'))
RULE_KEYS = ('Seal_a', 'Seal_c1', 'Seal_c2', 'Seal_c3', 'Seal_e', 'Seal_k',
             'Mn_e', 'TM1_b', 'TM2_b')
METALS = (('Metal4', 'Mn_e'), ('Metal5', 'Mn_e'), ('TopMetal1', 'TM1_b'), ('TopMetal2', 'TM2_b'))
VIAS = (('Via4', 'Seal_c1'), ('TopVia1', 'Seal_c2'), ('TopVia2', 'Seal_c3'))
FLAGS = ('nil', 't')


def loadSealRules():
    """Seal rule values in integer nm. A missing file or key, or a value that
    is not a positive multiple of the grid, raises."""
    with open(RULES_FILE, 'r') as rulesFile:
        rules = json.load(rulesFile)['rules']
    missing = [key for key in RULE_KEYS if key not in rules]
    if missing:
        raise ValueError(f"sealring: {RULES_FILE} lacks {', '.join(missing)}")
    values = {key: int(round(rules[key] * 1000)) for key in RULE_KEYS}
    for key, nm in values.items():
        if abs(rules[key] * 1000 - nm) > 1e-6 or nm % GRID_NM or nm <= 0:
            raise ValueError(f"sealring: {key}={rules[key]} is not a positive multiple of {GRID_NM} nm")
    return values


def outlineToNm(value, name):
    """Parse an outline parameter such as '5000.000u' to integer nm.

    Only plain micrometres with up to three decimals are accepted: a bare
    '5000' would otherwise be read as metres.
    """
    match = re.fullmatch(r'([0-9]+)(?:\.([0-9]{1,3}))?u', value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"sealring: {name} must be a string such as '5000.000u', got {value!r}")
    nm = int(match.group(1)) * 1000 + int((match.group(2) or '0').ljust(3, '0'))
    if nm % GRID_NM:
        raise ValueError(f"sealring: {name}={value} is not on the {GRID_NM} nm grid")
    return nm


def floorToGrid(nm):
    return nm // GRID_NM * GRID_NM


def cornerStaircase(ringWidth, span):
    """Outer edge of the lower-left corner (nm), from the left side (x = 0)
    to the bottom side (y = 0), origin at the outer corner of the ring metal.
    """
    step = (span - ringWidth) // CORNER_STEPS
    points = []
    for i in range(CORNER_STEPS):
        y = ringWidth + (CORNER_STEPS - 1 - i) * step
        points += [(i * step, y), ((i + 1) * step, y)]
    points.append((CORNER_STEPS * step, 0))
    return points


def ringContour(corner, x0, y0, x1, y1):
    """Closed contour through the four corners of the box (nm).

    `corner` runs from the left side to the bottom side of the lower-left
    corner; the same piece is turned by 90 degrees at each next corner,
    counterclockwise, and the straight sides join consecutive pieces.
    """
    turns = (lambda x, y: (x0 + x, y0 + y),
             lambda x, y: (x1 - y, y0 + x),
             lambda x, y: (x1 - x, y1 - y),
             lambda x, y: (x0 + y, y1 - x))
    return [turn(x, y) for turn in turns for x, y in corner]


def shifted(points, dx, dy):
    return [(x + dx, y + dy) for x, y in points]


class sealring(DloGen):

    @classmethod
    def defineParamSpecs(self, specs):
        specs('l', '1000.000u', 'Outline size X (sealring boundary)')
        specs('w', '1000.000u', 'Outline size Y (sealring boundary)')
        specs('addSlit', 'nil', 'Add Slit', ChoiceConstraint(list(FLAGS)))

    def setupParams(self, params):
        self.l = params['l']
        self.w = params['w']
        self.addSlit = params['addSlit']

    def genLayout(self):
        rules = loadSealRules()
        ringWidth = rules['Seal_a']
        span = rules['Seal_k']

        sizeX = outlineToNm(self.l, 'l')
        sizeY = outlineToNm(self.w, 'w')
        if self.addSlit not in FLAGS:
            raise ValueError(f"sealring: addSlit must be 'nil' or 't', got {self.addSlit!r}")
        # The published 114.25 um minimum, which callers compute from these
        # constants too. The straight between two corners of a side is then
        # at least 2 Seal.a + Seal.k / 4 long, longer than any slit cut.
        minSize = 2 * (BOUNDARY_TO_EDGESEAL_NM + span + ringWidth) + span // CORNER_STEPS
        if min(sizeX, sizeY) < minSize:
            raise ValueError(f"sealring: outline {sizeX / 1000} x {sizeY / 1000} um is below "
                             f"the {minSize / 1000} um minimum")
        if (span - ringWidth) % (CORNER_STEPS * GRID_NM):
            raise ValueError(f"sealring: Seal_k - Seal_a does not split into {CORNER_STEPS} grid steps")
        step = (span - ringWidth) // CORNER_STEPS

        x0 = y0 = BOUNDARY_TO_EDGESEAL_NM
        x1 = sizeX - BOUNDARY_TO_EDGESEAL_NM
        y1 = sizeY - BOUNDARY_TO_EDGESEAL_NM
        outer = cornerStaircase(ringWidth, span)
        inner = shifted(outer, ringWidth, step)[:-1] + [(span, ringWidth)]

        sideLength = (y1 - y0) - 2 * span

        def slitCut(left, right, height):
            """The slit's cut box across [left, right] of the ring, or None
            without a slit. The cut is centred on the left straight, its top
            end on the grid and rounded towards the top corner of the side."""
            if self.addSlit != 't':
                return None
            top = y1 - span - floorToGrid((sideLength - height) // 2)
            if top - height <= y0 + span:
                raise ValueError("sealring: the left side is too short for the slit")
            return (x0 + left, top - height, x0 + right, top)

        # (layer, outer corner, inner corner, slit cut box or None)
        rings = [(Layer('EdgeSeal', 'drawing'), outer, inner, None)]
        for name, key in METALS:
            rings.append((Layer(name, 'drawing'), outer, inner, slitCut(0, ringWidth, rules[key])))
        for name, key in VIAS:
            width = rules[key]
            if VIA_OFFSET_NM + width > min(ringWidth, step):
                raise ValueError(f"sealring: {key} does not fit Seal.d inside Seal_a")
            cut = slitCut(VIA_OFFSET_NM, VIA_OFFSET_NM + width, SLIT_VIA_NOTCH_NM + 2 * VIA_OFFSET_NM)
            rings.append((Layer(name, 'drawing'),
                          shifted(outer, VIA_OFFSET_NM, VIA_OFFSET_NM),
                          shifted(outer, VIA_OFFSET_NM + width, VIA_OFFSET_NM + width), cut))
        passivOuter = PASSIV_SPACE_NM + rules['Seal_e']
        rings.append((Layer('Passiv', 'drawing'), shifted(outer, -passivOuter, -passivOuter),
                      shifted(outer, -PASSIV_SPACE_NM, -PASSIV_SPACE_NM), None))
        boundary = Layer('EdgeSeal', 'boundary')
        text = Layer('TEXT', 'drawing')

        area = sizeX * sizeY / 1e12
        sizeLabel = ('Device registration size: x={0:.1f} um ; y={1:.1f} um\n'
                     'Calculated area: {2:.1e} sq mm').format(sizeX / 1000.0, sizeY / 1000.0, area)
        versionLabel = 'PDK version: ' + pdk_version()

        # Every check and layer lookup happens before the first shape, so a
        # failure leaves an empty cell rather than a partial ring.
        def polygon(layer, corner):
            pts = ringContour(corner, x0, y0, x1, y1)
            return dbCreatePolygon(self, layer, PointList([Point(x / 1000.0, y / 1000.0) for x, y in pts]))

        def box(layer, left, bottom, right, top):
            return dbCreateRect(self, layer, Box(left / 1000.0, bottom / 1000.0, right / 1000.0, top / 1000.0))

        dbCreateRect(self, boundary, Box(0, 0, sizeX / 1000.0, sizeY / 1000.0))
        for layer, outerCorner, innerCorner, cut in rings:
            outerShape = polygon(layer, outerCorner)
            innerShape = polygon(layer, innerCorner)
            ringShape = dbLayerXor(layer, outerShape, innerShape)
            dbDeleteObject(outerShape)
            dbDeleteObject(innerShape)
            if cut is not None:
                cutShape = box(layer, *cut)
                dbLayerNot(layer, ringShape, cutShape)
                dbDeleteObject(ringShape)
                dbDeleteObject(cutShape)
        dbCreateLabel(self, text, Point(5.0, 5.0), sizeLabel, 'lowerLeft', 'R0', Font.EURO_STYLE, 5.0)
        dbCreateLabel(self, text, Point(5.0, sizeY / 1000.0 - 10.0), versionLabel,
                      'lowerLeft', 'R0', Font.EURO_STYLE, 5.0)
