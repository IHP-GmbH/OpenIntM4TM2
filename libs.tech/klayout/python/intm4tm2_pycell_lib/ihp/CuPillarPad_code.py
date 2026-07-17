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
__version__ = '$Revision: #1 $'

from cni.dlo import *
from .utility_functions import *
from .geometry import *

import math

class CuPillarPad(DloGen):

    @classmethod
    def defineParamSpecs(self, specs):
        # define parameters and default values
        techparams = specs.tech.getTechParams()

        CDFVersion = techparams['CDFVersion']

        specs('cdf_version', CDFVersion, 'CDF Version')
        specs('Display', 'Selected', 'Display', ChoiceConstraint(['All', 'Selected']))
        specs('diameter', '35u', 'Diameter')
        specs('passEncl', '7.5u', 'Passiv enclosure in TM2')
        specs('addFillerEx', 'nil', 'Metal Filler Exclusion', ChoiceConstraint(['nil', 't']))

    def setupParams(self, params):
        # process parameter values entered by user
        self.params = params
        self.diameter = params['diameter']
        self.passEncl = params['passEncl']
        self.addFillerEx = params['addFillerEx']

    def genLayout(self):
        noFillerEnc = 10.0
        rad = tog(Numeric(self.diameter)*1e6/2)
        passEncl = Numeric(self.passEncl)*1e6

        dbReplaceProp(self, 'pin#', 1)
        dbReplaceProp(self, 'ignore', 'TRUE')

        # Circles are drawn directly on their final layer as 256-point
        # polygons -- the exact discretization the assembly flow uses
        # (CuPillarGenerator in bump_mirror.py); the copperpillar DRC deck's
        # 10 nm tolerances and its circle exemptions assume it. The shim's
        # ellipse idioms are avoided on purpose: dbCopyShape cannot retarget
        # a shape through its layer attribute, and the ellipse-to-polygon
        # helpers leak their intermediate shape.
        def circle(layer, r):
            points = PointList()
            for i in range(256):
                a = 2*math.pi*i/256
                points.append(Point(r*math.cos(a), r*math.sin(a)))
            dbCreatePolygon(self, layer, points)

        # Interposer metal stack only (this PDK has no FEOL layers)
        if checkForYes(self.addFillerEx):
            noFillerStack = ['Metal4', 'Metal5', 'TopMetal1', 'TopMetal2']
            for noFiller in noFillerStack:
                circle(Layer(noFiller, 'nofill'), rad + noFillerEnc)

        # Pillar recognition rides on the pillar purposes (dfpad:pillar
        # 41/35 and Recog:pillar 99/35 at pad size, Passiv:pillar 9/35 at
        # the opening) -- the convention the copperpillar DRC deck and the
        # assembly flow key on. dfpad on the drawing purpose is deliberately
        # not produced so bond-pad rules never fire on pillar pads.
        circle(Layer('TopMetal2'), rad + passEncl)
        circle(Layer('dfpad', 'pillar'), rad + passEncl)
        circle(Layer('Recog', 'pillar'), rad + passEncl)
        circle(Layer('Passiv', 'pillar'), rad)
