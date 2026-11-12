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

class SolderBumpPad(DloGen):

    @classmethod
    def defineParamSpecs(self, specs):
        # define parameters and default values
        techparams = specs.tech.getTechParams()

        CDFVersion = techparams['CDFVersion']

        specs('cdf_version', CDFVersion, 'CDF Version')
        specs('Display', 'Selected', 'Display', ChoiceConstraint(['All', 'Selected']))
        specs('diameter', '60u', 'Diameter')
        specs('passEncl', '10u', 'Passiv enclosure in TM2')
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

        # The same 256-point circles as CuPillarPad, for the same reason: the
        # discretization bump_mirror's generator places and the solderbump
        # DRC deck's circle tolerances assume.
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

        # The solder-bump site rides on the sbump purposes, as the pillar
        # site rides on the pillar ones: dfpad:sbump 41/36 and Recog:sbump
        # 99/36 at pad size, Passiv:sbump 9/36 at the opening. The
        # solderbump DRC deck recognizes SBumpPad as Passiv:sbump AND
        # dfpad:sbump; dfpad on the drawing purpose is not produced, so
        # bond-pad rules never fire on a solder-bump pad.
        circle(Layer('TopMetal2'), rad + passEncl)
        circle(Layer('dfpad', 'sbump'), rad + passEncl)
        circle(Layer('Recog', 'sbump'), rad + passEncl)
        circle(Layer('Passiv', 'sbump'), rad)
