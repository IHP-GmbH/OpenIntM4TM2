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

The ring is the backend-only part of the SG13G2 seal ring: EdgeSeal plus
Metal4, Metal5, TopMetal1 and TopMetal2 on the same footprint, continuous
Via4, TopVia1 and TopVia2 rings centred in it, and a Passiv ring outside it.
Each corner is cut by a Manhattan staircase that stays inside the Seal.k
chamfer line, so every shape is orthogonal and on the 5 nm grid.

Widths are the public SG13G2 section 6.10 values, read from the DRC rule
file rather than from techParams (as bondpad does) because they are rule
values: Seal.a ring width, Seal.c1/c2/c3 via ring widths, Seal.e Passiv ring
width, Seal.f Passiv-to-ring space, Seal.k corner span.
"""

__version__ = '$Revision: #1 $'

import json
import os
import re

from cni.dlo import *
from .geometry import *
from .utility_functions import *

# Section 6.10 introduction and Fig. 6.12: EdgeSeal sits 30 um from the
# sealring boundary. The value has no rule ID and no DRC check; it is drawn
# as the minimum.
BOUNDARY_TO_EDGESEAL_NM = 30000

CORNER_STEPS = 4
GRID_NM = 5

RULES_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.realpath(__file__)), '..', '..', '..',
    'tech', 'drc', 'rule_decks', 'interposer_tech_default.json'))
RULE_KEYS = ('Seal_a', 'Seal_c1', 'Seal_c2', 'Seal_c3', 'Seal_e', 'Seal_f', 'Seal_k')


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


def staircaseRect(x0, y0, x1, y1, span, steps):
    """Counterclockwise points of a rectangle with stepped corners (nm).

    Each corner is cut by `steps` steps of span/steps along each axis. The
    convex vertices lie on the 45-degree line of leg `span` and the concave
    ones inside it, so the shape never reaches past that chamfer. Shrinking
    the rectangle by t with the same span equals sizing the polygon by -t.
    """
    s = span // steps
    corner = []
    for i in range(steps):
        corner.append((i * s, span - i * s))
        corner.append(((i + 1) * s, span - i * s))
    corner.append((span, 0))

    points = [(x0 + dx, y0 + dy) for dx, dy in corner]
    points += [(x1 - dx, y0 + dy) for dx, dy in reversed(corner)]
    points += [(x1 - dx, y1 - dy) for dx, dy in corner]
    points += [(x0 + dx, y1 - dy) for dx, dy in reversed(corner)]
    return points


class sealring(DloGen):

    @classmethod
    def defineParamSpecs(self, specs):
        specs('l', '1000.000u', 'Outline size X (sealring boundary)')
        specs('w', '1000.000u', 'Outline size Y (sealring boundary)')

    def setupParams(self, params):
        self.l = params['l']
        self.w = params['w']

    def genLayout(self):
        rules = loadSealRules()
        ringWidth = rules['Seal_a']
        span = rules['Seal_k']

        sizeX = outlineToNm(self.l, 'l')
        sizeY = outlineToNm(self.w, 'w')
        # Every straight side of the ring's inner edge must be at least one
        # corner step long; shorter sides leave slivers, and none at all turns
        # the ring solid.
        minSize = 2 * (BOUNDARY_TO_EDGESEAL_NM + span + ringWidth) + span // CORNER_STEPS
        if min(sizeX, sizeY) < minSize:
            raise ValueError(f"sealring: outline {sizeX / 1000} x {sizeY / 1000} um is below "
                             f"the {minSize / 1000} um minimum")
        if span % (CORNER_STEPS * GRID_NM):
            raise ValueError(f"sealring: Seal_k {span} nm does not split into {CORNER_STEPS} grid steps")

        # Every check and layer lookup happens before the first shape, so a
        # failure leaves an empty cell rather than a partial ring.
        rings = [(Layer(name, 'drawing'), 0, ringWidth)
                 for name in ('EdgeSeal', 'Metal4', 'Metal5', 'TopMetal1', 'TopMetal2')]
        for name, key in (('Via4', 'Seal_c1'), ('TopVia1', 'Seal_c2'), ('TopVia2', 'Seal_c3')):
            slack = ringWidth - rules[key]
            if slack < 0 or slack % (2 * GRID_NM):
                raise ValueError(f"sealring: {key} cannot be centred on the grid in Seal_a")
            rings.append((Layer(name, 'drawing'), slack // 2, rules[key]))
        passivGap = rules['Seal_f']
        passivWidth = rules['Seal_e']
        rings.append((Layer('Passiv', 'drawing'), -(passivGap + passivWidth), passivWidth))
        boundary = Layer('EdgeSeal', 'boundary')

        def shape(inset):
            # Outline shrunk by 30 um plus inset (negative grows), stepped corners.
            d = BOUNDARY_TO_EDGESEAL_NM + inset
            pts = staircaseRect(d, d, sizeX - d, sizeY - d, span, CORNER_STEPS)
            return PointList([Point(x / 1000.0, y / 1000.0) for x, y in pts])

        def ring(layer, inset, width):
            outer = dbCreatePolygon(self, layer, shape(inset))
            inner = dbCreatePolygon(self, layer, shape(inset + width))
            dbLayerXor(layer, outer, inner)
            dbDeleteObject(outer)
            dbDeleteObject(inner)

        dbCreateRect(self, boundary, Box(0, 0, sizeX / 1000.0, sizeY / 1000.0))
        for layer, inset, width in rings:
            ring(layer, inset, width)
