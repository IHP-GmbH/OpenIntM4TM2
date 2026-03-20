########################################################################
#
# Copyright 2024 IHP PDK Authors
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

__version__ = '$Revision: #3 $'

from cni.dlo import *
from .geometry import *
from .utility_functions import *

import math

class cupillar(DloGen):

    @classmethod
    def defineParamSpecs(self, specs):
        techparams = specs.tech.getTechParams()

        CDFVersion = techparams['CDFVersion']

#ifdef KLAYOUT
#else
        specs('cdf_version', CDFVersion, 'CDF Version')
        specs('Display', 'Selected', 'Display', ChoiceConstraint(['All', 'Selected']))
#endif
        specs('diameter', '35u', 'Diameter')
        specs('passEncl', '7.5u', 'Passiv enclosure in TM2')
        specs('padType', 'pillar', 'Pad type', ChoiceConstraint(['pillar', 'sbump']))
        specs('shape', 'circle', 'Shape', ChoiceConstraint(['circle', 'octagon']))
        specs('addFillerEx', 'nil', 'Metal Filler Exclusion', ChoiceConstraint(['nil', 't']))

    def setupParams(self, params):
        self.params = params
        self.diameter = params['diameter']
        self.passEncl = params['passEncl']
        self.padType = params['padType']
        self.shape = params['shape']
        self.addFillerEx = params['addFillerEx']

    def genLayout(self):
        diameter = self.diameter
        passEncl = self.passEncl
        padType = self.padType
        shape = self.shape
        addFillerEx = self.addFillerEx
        grid = self.tech.getGridResolution()

        noFillerEnc = 10.0
        diameter = Numeric(diameter)*1e6/2;
        passEncl = Numeric(passEncl)*1e6;

        dbReplaceProp(self, 'pin#', 1)
        dbReplaceProp(self, 'ignore', 'TRUE')

        noFillerStack = ['Activ', 'GatPoly', 'Metal1', 'Metal2', 'Metal3', 'Metal4', 'Metal5', 'TopMetal1', 'TopMetal2']
        rad = tog(diameter)
        diameter = rad*2
        radx = rad
        rady = rad

        # Layer purpose determined by pad type:
        #   pillar -> datatype 35 for Passiv, dfpad, Recog
        #   sbump  -> datatype 36 for Passiv, dfpad, Recog
        passivLayer = Layer('Passiv', padType)
        dfpadLayer = Layer('dfpad', padType)
        recogLayer = Layer('Recog', padType)

        # Filler exclusion
        if checkForYes(addFillerEx) :
            oradx = radx+noFillerEnc
            orady = rady+noFillerEnc
            id = bondpadStretchedCircle(self, Layer(noFillerStack[0], 'nofill'), oradx, orady, grid)
            for noFiller in noFillerStack[1:] :
                id1 = dbCopyShape(id, Point(0, 0), 'R0')
                id1.layer = Layer(noFiller, 'nofill')

        # TopMetal2 pad (enclosure around passivation opening)
        # TopMetal2 stays at datatype 0 (drawing) regardless of pad type
        if shape == 'circle' :
            id = dbCreateEllipse(self, 'TopMetal2', Box(-diameter-passEncl, -diameter-passEncl, diameter+passEncl, diameter+passEncl))
            # dfpad layer -- marks this as a pad area
            id1 = dbCopyShape(id, Point(0, 0), 'R0')
            id1.layer = dfpadLayer
            # Recog layer -- recognition marker
            id1 = dbCopyShape(id, Point(0, 0), 'R0')
            id1.layer = recogLayer
            # Passivation opening (the actual cu-pillar or sbump opening)
            dbCreateEllipse(self, passivLayer, Box(-diameter, -diameter, diameter, diameter))
        elif shape == 'octagon' :
            # Octagon shape only allowed for sbump (Padb.f), not pillar (Padc.f)
            if padType == 'pillar' :
                print('Warning: Cu-pillar requires circle shape (Padc.f). Forcing circle.')
                id = dbCreateEllipse(self, 'TopMetal2', Box(-diameter-passEncl, -diameter-passEncl, diameter+passEncl, diameter+passEncl))
                id1 = dbCopyShape(id, Point(0, 0), 'R0')
                id1.layer = dfpadLayer
                id1 = dbCopyShape(id, Point(0, 0), 'R0')
                id1.layer = recogLayer
                dbCreateEllipse(self, passivLayer, Box(-diameter, -diameter, diameter, diameter))
            else :
                # Sbump allows octagon
                offset = tog(min(radx, rady)*(1-1/(sqrt(2)+1)))
                oradx = radx+passEncl
                orady = rady+passEncl
                ooff = tog(min(oradx, orady)*(1-1/(sqrt(2)+1)))
                poly = bondpadOctagonPoints(oradx, orady, ooff)
                dbCreatePolygon(self, 'TopMetal2', poly)
                dbCreatePolygon(self, dfpadLayer, poly)
                dbCreatePolygon(self, recogLayer, poly)
                # Passivation opening
                poly_pass = bondpadOctagonPoints(radx, rady, offset)
                dbCreatePolygon(self, passivLayer, poly_pass)
