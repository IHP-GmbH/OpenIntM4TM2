# SPDX-License-Identifier: Apache-2.0
"""
Single source of truth for the interposer DRC deck registry (Python side).

The runset ``intm4tm2.drc`` owns the deck-name -> rule-file mapping in Ruby
(``intm4tm2_decks.rb``); this module mirrors the deck NAMES and the skip /
relocation policy for the Python tooling (``run_drc.py``, the regression
runner, the registry CI check) so the two language halves cannot drift
silently: the registry check imports both and asserts they agree.

Kept import-light on purpose (stdlib only, no klayout) so the CI registry check
can import it without a KLayout install.
"""

from types import MappingProxyType

# Deck names, in load order. Must match the keys of IntM4TM2Decks::ALL_DECKS in
# intm4tm2_decks.rb (the registry check asserts set-equality both ways).
AVAILABLE_DECKS = (
    'offgrid', 'angle',
    'metaln', 'metalnfiller',
    'via4', 'topvia1',
    'topmetal1', 'topmetal1filler',
    'topvia2', 'topmetal2', 'topmetal2filler',
    'passiv', 'pad', 'copperpillar', 'solderbump', 'ubm_floor',
    'sealring', 'mim', 'metalslits', 'lbe',
    'density',
)

# Decks excluded from the default "all" run (must match the Ruby
# DEFAULT_SKIP_DECKS). Density carries global minimum-density rules that fail on
# partial layouts; opt in with --density or an explicit --deck density.
DEFAULT_SKIP_DECKS = frozenset({'density'})

# Decks moved out of the interposer PDK. Recognised so a stale CLI invocation
# prints a useful redirect instead of "unknown". Read-only so a caller cannot
# mutate the shared registry.
RELOCATED_DECKS = MappingProxyType({
    'assembly': "Promoted to the ADK. Use adk/klayout/drc/run_drc.py "
                "with --interposer-adapter <name>.",
})
