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
from .geometry import *
from .utility_functions import *


class sealring(DloGen):

    # Keep the public parameter interface compatible with the IHP SG13G2 sealring
    # PCell, but generate only the interposer stack here.
    INTERPOSER_METAL_LAYERS = [
        'Passiv',
        'EdgeSeal',
        'Metal4',
        'Metal5',
        'TopMetal1',
        'TopMetal2',
    ]

    INTERPOSER_VIA_LAYERS = [
        'Via4',
        'TopVia1',
        'TopVia2',
    ]

    @classmethod
    def defineParamSpecs(cls, specs):
        tech_params = specs.tech.getTechParams()

        specs('cdf_version', tech_params['CDFVersion'], 'CDF Version')
        specs('Display', 'Selected', 'Display', ChoiceConstraint(['All', 'Selected']))

        specs('l', tech_params['sealring_complete_defL'], 'Length(X-Axis)')
        specs('w', tech_params['sealring_complete_defW'], 'Width(Y-Axis)')
        specs('addLabel', tech_params['sealring_complete_addLabel'], 'Add sub! label', ChoiceConstraint(['nil', 't']))
        specs('addSlit', 'nil', 'Add Slit', ChoiceConstraint(['nil', 't']))

        specs('Lmin', tech_params['sealring_complete_minL'], 'Lmin')
        specs('Wmin', tech_params['sealring_complete_minW'], 'Wmin')

        specs('edgeBox', tech_params['sealring_complete_edgeBox'], 'EdgeSeal.boundary box away from the outer EdgeSeal.drawing')

    def setupParams(self, params):
        self.params = params

        self.registration_length = params['l']
        self.registration_width = params['w']
        self.add_label = params['addLabel']
        self.add_slit = params['addSlit']
        self.edge_box = params['edgeBox']
        self.minimum_length = params['Lmin']
        self.minimum_width = params['Wmin']

    def _create_rect(self, layer_name, left, bottom, right, top, purpose='drawing'):
        return dbCreateRect(self, Layer(layer_name, purpose), Box(left, bottom, right, top))

    def _append_shape(self, layer_shapes, layer_name, shape):
        layer_shapes.setdefault(layer_name, []).append(shape)

    def _append_rect(self, layer_shapes, layer_name, left, bottom, right, top):
        self._append_shape(layer_shapes, layer_name, self._create_rect(layer_name, left, bottom, right, top))

    def _append_rectangular_ring(self, layer_shapes, layer_name, outer_left, outer_bottom, outer_right, outer_top, ring_width_x, ring_width_y):
        inner_left = outer_left + ring_width_x
        inner_right = outer_right - ring_width_x
        inner_bottom = outer_bottom + ring_width_y
        inner_top = outer_top - ring_width_y

        self._append_rect(layer_shapes, layer_name, outer_left, outer_bottom, outer_right, inner_bottom)
        self._append_rect(layer_shapes, layer_name, outer_left, inner_top, outer_right, outer_top)
        self._append_rect(layer_shapes, layer_name, outer_left, inner_bottom, inner_left, inner_top)
        self._append_rect(layer_shapes, layer_name, inner_right, inner_bottom, outer_right, inner_top)

    def _copy_quadrant(self, layer_shapes, layer_name, source_shapes, outer_length_um, outer_width_um):
        for shape in source_shapes:
            self._append_shape(layer_shapes, layer_name, dbCopyShape(shape, Point(outer_length_um, outer_width_um), 'R180'))
            self._append_shape(layer_shapes, layer_name, dbCopyShape(shape, Point(outer_length_um, 0), 'R90'))
            self._append_shape(layer_shapes, layer_name, dbCopyShape(shape, Point(0, outer_width_um), 'R270'))

    def _merge_layer_shapes(self, layer_shapes):
        for layer_name, shapes in layer_shapes.items():
            combineLayerAndDelete(self, shapes, [], layer_name)

    def _build_corner_shapes(self, outer_length_um, outer_width_um, edge_box_um):
        layer_shapes = {}

        corner_width_um = 4.2
        corner_step_count = 4
        edge_to_metal_gap_um = 3.0
        edge_to_via_gap_um = 5.1
        via_track_length_um = 4.2

        metal_offset_um = edge_to_metal_gap_um + corner_width_um + edge_box_um
        via_offset_um = edge_to_via_gap_um + corner_width_um + edge_box_um
        corner_extent_um = corner_width_um * (corner_step_count + 2) + edge_to_metal_gap_um + edge_box_um
        corner_start_x_um = corner_extent_um - corner_width_um * (corner_step_count + 1)
        corner_length_um = corner_width_um * 2

        metal_corner_start_x_um = corner_width_um * (corner_step_count + 1) + metal_offset_um
        for metal_layer in self.INTERPOSER_METAL_LAYERS[1:]:
            metal_seed = generateCorner(
                self,
                metal_corner_start_x_um,
                0,
                corner_width_um,
                corner_length_um,
                corner_step_count,
                corner_extent_um,
                metal_offset_um,
                metal_layer,
            )
            for shape in metal_seed:
                self._append_shape(layer_shapes, metal_layer, shape)
            self._copy_quadrant(layer_shapes, metal_layer, metal_seed, outer_length_um, outer_width_um)

        via_sizes_um = {
            'Via4': self.techparams['Vn_a'],
            'TopVia1': self.techparams['TV1_a'],
            'TopVia2': self.techparams['TV2_a'],
        }

        for via_layer in self.INTERPOSER_VIA_LAYERS:
            via_width_um = via_sizes_um[via_layer]
            via_seed = []
            via_start_x_um = corner_width_um * (corner_step_count + 1) + metal_offset_um - corner_width_um / 2 - 0.1
            via_seed_rect = self._create_rect(
                via_layer,
                via_start_x_um,
                via_offset_um,
                via_start_x_um + via_width_um,
                via_offset_um + via_track_length_um,
            )
            via_seed.append(via_seed_rect)

            for corner_index in range(1, corner_step_count + 1):
                via_seed.append(
                    dbCopyShape(
                        via_seed_rect,
                        Point(2 * via_offset_um + via_track_length_um * corner_index + via_width_um - 0.1,
                              -via_track_length_um * (corner_index - 1)),
                        'R90',
                    )
                )
                via_seed.append(
                    dbCopyShape(
                        via_seed_rect,
                        Point(-corner_width_um * (corner_index - 1), corner_width_um * (corner_index - 1) - 0.1),
                        'R0',
                    )
                )

            via_seed.append(
                self._create_rect(
                    via_layer,
                    via_start_x_um,
                    via_offset_um - 0.1,
                    corner_extent_um,
                    via_offset_um - 0.1 + via_width_um,
                )
            )
            via_seed.append(
                self._create_rect(
                    via_layer,
                    via_offset_um - 0.1,
                    corner_extent_um - corner_width_um / 2 - 0.1,
                    via_offset_um - 0.1 + via_width_um,
                    corner_extent_um,
                )
            )

            for shape in via_seed:
                self._append_shape(layer_shapes, via_layer, shape)
            self._copy_quadrant(layer_shapes, via_layer, via_seed, outer_length_um, outer_width_um)

        return {
            'layer_shapes': layer_shapes,
            'corner_width_um': corner_width_um,
            'corner_extent_um': corner_extent_um,
            'metal_offset_um': metal_offset_um,
            'via_offset_um': via_offset_um,
        }

    def _append_straight_sections(self, layer_shapes, outer_length_um, outer_width_um, edge_box_um, geometry):
        corner_width_um = geometry['corner_width_um']
        corner_extent_um = geometry['corner_extent_um']
        metal_offset_um = geometry['metal_offset_um']
        via_offset_um = geometry['via_offset_um']

        self._append_rectangular_ring(
            layer_shapes,
            'Passiv',
            edge_box_um,
            edge_box_um,
            outer_length_um - edge_box_um,
            outer_width_um - edge_box_um,
            corner_width_um,
            corner_width_um,
        )

        for metal_layer in self.INTERPOSER_METAL_LAYERS[1:]:
            self._append_rect(layer_shapes, metal_layer, metal_offset_um, corner_extent_um, metal_offset_um + corner_width_um, outer_width_um - corner_extent_um)
            self._append_rect(layer_shapes, metal_layer, corner_extent_um, metal_offset_um, outer_length_um - corner_extent_um, metal_offset_um + corner_width_um)
            self._append_rect(layer_shapes, metal_layer, outer_length_um - metal_offset_um, corner_extent_um, outer_length_um - corner_width_um - metal_offset_um, outer_width_um - corner_extent_um)
            self._append_rect(layer_shapes, metal_layer, corner_extent_um, outer_width_um - metal_offset_um, outer_length_um - corner_extent_um, outer_width_um - corner_width_um - metal_offset_um)

        via_sizes_um = {
            'Via4': self.techparams['Vn_a'],
            'TopVia1': self.techparams['TV1_a'],
            'TopVia2': self.techparams['TV2_a'],
        }
        for via_layer in self.INTERPOSER_VIA_LAYERS:
            via_width_um = via_sizes_um[via_layer]
            self._append_rect(layer_shapes, via_layer, via_offset_um - 0.1, corner_extent_um, via_offset_um + via_width_um - 0.1, outer_width_um - corner_extent_um)
            self._append_rect(layer_shapes, via_layer, corner_extent_um, via_offset_um - 0.1, outer_length_um - corner_extent_um, via_offset_um + via_width_um - 0.1)
            self._append_rect(layer_shapes, via_layer, outer_length_um - via_offset_um + 0.1, corner_extent_um, outer_length_um - via_width_um - via_offset_um + 0.1, outer_width_um - corner_extent_um)
            self._append_rect(layer_shapes, via_layer, corner_extent_um, outer_width_um - via_offset_um + 0.1, outer_length_um - corner_extent_um, outer_width_um - via_width_um - via_offset_um + 0.1)

    def _add_labels(self, outer_length_um, outer_width_um):
        if self.add_label != 't':
            return

        outer_area_mm2 = (outer_length_um * outer_width_um) / 1e12
        size_label = (
            'Interposer seal ring\n'
            'Outer X={0:.1f} um ; Outer Y={1:.1f} um\n'
            'Calculated area: {2:.1e} sq mm'
        ).format(outer_length_um, outer_width_um, outer_area_mm2)
        dbCreateLabel(self, Layer('TEXT', 'drawing'), Point(5.0, 5.0), size_label, 'lowerLeft', 'R0', Font.EURO_STYLE, 5.0)

        version_label = 'PDK version: ' + get_git_commit_version()
        dbCreateLabel(self, Layer('TEXT', 'drawing'), Point(5.0, outer_width_um - 10.0), version_label, 'lowerLeft', 'R0', Font.EURO_STYLE, 5.0)

    def genLayout(self):
        self.techparams = self.tech.getTechParams()
        self.epsilon = self.techparams['epsilon1']

        edge_box_um = Numeric(self.edge_box) * 1e6
        registration_length_um = Numeric(self.registration_length) * 1e6
        registration_width_um = Numeric(self.registration_width) * 1e6
        minimum_length_um = Numeric(self.minimum_length) * 1e6
        minimum_width_um = Numeric(self.minimum_width) * 1e6

        if registration_length_um < minimum_length_um:
            raise ValueError('Sealring length is smaller than Lmin')
        if registration_width_um < minimum_width_um:
            raise ValueError('Sealring width is smaller than Wmin')

        outer_length_um = registration_length_um + edge_box_um * 2
        outer_width_um = registration_width_um + edge_box_um * 2

        # Keep the legacy public parameter for compatibility. The current interposer
        # sealring generator does not synthesize dedicated slit geometry yet.
        _ = self.add_slit

        geometry = self._build_corner_shapes(outer_length_um, outer_width_um, edge_box_um)
        layer_shapes = geometry['layer_shapes']
        self._append_straight_sections(layer_shapes, outer_length_um, outer_width_um, edge_box_um, geometry)
        self._merge_layer_shapes(layer_shapes)

        self._create_rect('EdgeSeal', 0, 0, outer_length_um, outer_width_um, purpose='boundary')
        self._add_labels(outer_length_um, outer_width_um)
