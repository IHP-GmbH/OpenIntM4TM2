# SPDX-License-Identifier: Apache-2.0
#
# Single source of truth for the interposer DRC deck registry (Ruby side).
#
# Owns the deck-name -> rule-file mapping consumed by the runset intm4tm2.drc,
# the shared preamble, the default-skip policy, and the pure deck_files()
# selection used to compose the concatenated DRC source. Kept free of any DRC /
# klayout API so it can be `require`d standalone (the registry CI check evals it
# with plain `ruby -e`). The Python side mirrors the deck NAMES and the skip
# policy in intm4tm2_decks.py; the check asserts the two halves agree.

module IntM4TM2Decks
  # Deck name -> rule-deck filename (under rule_decks/), in load order. Must
  # match AVAILABLE_DECKS in intm4tm2_decks.py.
  ALL_DECKS = {
    'offgrid'         => '3_1_offgrid.drc',
    'angle'           => '3_2_angle.drc',
    'metaln'          => '5_17_metaln.drc',
    'metalnfiller'    => '5_18_metalnfiller.drc',
    'via4'            => '5_20_via4.drc',
    'topvia1'         => '5_21_topvia1.drc',
    'topmetal1'       => '5_22_topmetal1.drc',
    'topmetal1filler' => '5_23_topmetal1filler.drc',
    'topvia2'         => '5_24_topvia2.drc',
    'topmetal2'       => '5_25_topmetal2.drc',
    'topmetal2filler' => '5_26_topmetal2filler.drc',
    'passiv'          => '5_27_passiv.drc',
    'pad'             => '6_9_pad.drc',
    'copperpillar'    => '6_9_copperpillar.drc',
    'solderbump'      => '6_9_solderbump.drc',
    'ubm_floor'       => '6_9_ubm_floor.drc',
    'sealring'        => '6_10_sealring.drc',
    'mim'             => '6_11_mim.drc',
    'metalslits'      => '7_3_metalslits.drc',
    'lbe'             => '9_1_lbe.drc',
    'density'         => 'density.drc',
  }.freeze

  # Shared preamble prepended to every run (layer definitions).
  PREAMBLE = ['layers_def.drc'].freeze

  # Decks skipped by the default "all" run (must match the Python
  # DEFAULT_SKIP_DECKS). Density carries global MINIMUM density rules that are
  # meaningless on partial layouts and unit fixtures; select it explicitly with
  # -rd deck=density (CLI: --density / --deck density).
  DEFAULT_SKIP_DECKS = ['density'].freeze

  # Resolve the ordered list of rule files to concatenate.
  #   requested == nil      : the default "all" run (every deck bar DEFAULT_SKIP_DECKS)
  #   requested == [names]  : exactly those known decks, in the given order
  # Returns [files, unknown]: files is PREAMBLE followed by the selected rule
  # files (order-preserving); unknown lists any requested names not in ALL_DECKS.
  def self.deck_files(requested)
    if requested.nil?
      selected = ALL_DECKS.reject { |name, _| DEFAULT_SKIP_DECKS.include?(name) }.values
      return [PREAMBLE + selected, []]
    end
    unknown = requested.reject { |name| ALL_DECKS.key?(name) }
    selected = requested.select { |name| ALL_DECKS.key?(name) }.map { |name| ALL_DECKS[name] }
    [PREAMBLE + selected, unknown]
  end
end
