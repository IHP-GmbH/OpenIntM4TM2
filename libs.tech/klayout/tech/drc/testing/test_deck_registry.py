#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Self-test for check_deck_registry.py.

No real rule deck is committed here. Every case builds a MINI fake drc tree in
tmp_path, runs the checker against it as a subprocess, and asserts BOTH the exit
code AND the assertion id that must appear in the message, so no case can pass
vacuously.

One case is not mini: ``test_contract_faithful_fixture`` mirrors the real
post-merge emitted API of the interposer PDK (the 21 name => file pairs, the 22
rule_decks filenames, the 17 GOLDEN decks, the 4 known-untested decks) and
asserts exit 0. That is the proof the checker goes green once the producer side
lands.

`ruby` is a hard requirement: the baseline green comes out of the ruby
subprocess, so a missing ruby fails the suite rather than skipping it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parent / "check_deck_registry.py"


@pytest.fixture(scope="session", autouse=True)
def ruby_is_present():
    if shutil.which("ruby") is None:
        pytest.fail(
            "`ruby` is not on PATH. This suite must not skip: the baseline green "
            "verdict is produced by the ruby subprocess that emits ALL_DECKS, so "
            "skipping would pass vacuously. Install ruby (CI's static job already "
            "does) and re-run."
        )


# ---------------------------------------------------------------------------
# mini tree
# ---------------------------------------------------------------------------

CLEAN_RUN_DRC = """\
from intm4tm2_decks import AVAILABLE_DECKS, DEFAULT_SKIP_DECKS, RELOCATED_DECKS


def cli():
    return sorted(AVAILABLE_DECKS), set(DEFAULT_SKIP_DECKS), dict(RELOCATED_DECKS)
"""

# The shipped runset's own consumption form: require the module, then compose
# through the emitted deck_files(). ASSERT-8's ruby positive is exclusively this
# call, so it is also the baseline here.
CLEAN_MAIN_DRC = """\
# stub intm4tm2.drc
require File.join(File.dirname(__FILE__), 'intm4tm2_decks')
drc_files, unknown_decks = IntM4TM2Decks.deck_files(requested)
"""

# Aliases the constant and composes inline, never calling deck_files(). No
# `all_decks = {` literal (NEG passes) and the token is present (TOKEN passes),
# so only the exclusive deck_files( positive can catch it.
ALIAS_ONLY_MAIN_DRC = """\
# stub intm4tm2.drc
require File.join(File.dirname(__FILE__), 'intm4tm2_decks')
all_decks = IntM4TM2Decks::ALL_DECKS
preamble = IntM4TM2Decks::PREAMBLE
skip = IntM4TM2Decks::DEFAULT_SKIP_DECKS
drc_files = preamble + all_decks.reject { |n, _| skip.include?(n) }.values
"""

GOOD_DECK_FILES = """\
  def self.deck_files(requested)
    if requested.nil?
      return [PREAMBLE + ALL_DECKS.reject { |n, _| DEFAULT_SKIP_DECKS.include?(n) }.values, []]
    end
    unknown = requested.reject { |d| ALL_DECKS.key?(d) }
    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }.map { |d| ALL_DECKS[d] }, unknown]
  end
"""

# hand-broken: drops the PREAMBLE from the "all decks" composition
NO_PREAMBLE_DECK_FILES = """\
  def self.deck_files(requested)
    if requested.nil?
      return [ALL_DECKS.reject { |n, _| DEFAULT_SKIP_DECKS.include?(n) }.values, []]
    end
    unknown = requested.reject { |d| ALL_DECKS.key?(d) }
    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }.map { |d| ALL_DECKS[d] }, unknown]
  end
"""

# ---------------------------------------------------------------------------
# hand-broken REQUESTED branches. The nil branch of every one of these is the
# good one, so nothing but the requested-branch probe can see them.
# ---------------------------------------------------------------------------


def _requested_branch(body: str) -> str:
    """A deck_files whose nil branch is correct and whose requested branch is `body`."""
    return (
        "  def self.deck_files(requested)\n"
        "    if requested.nil?\n"
        "      return [PREAMBLE + ALL_DECKS.reject { |n, _| "
        "DEFAULT_SKIP_DECKS.include?(n) }.values, []]\n"
        "    end\n"
        f"{body}"
        "  end\n"
    )


# Fable's verified repro, verbatim: `.reverse` on the map of the selected files.
REVERSED_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }.reverse, unknown]\n"
)

# Silently drops the last selected file.
DROPPING_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    sel = requested.select { |d| ALL_DECKS.key?(d) }.map { |d| ALL_DECKS[d] }\n"
    "    [PREAMBLE + sel[0..-2], unknown]\n"
)

# Maps every requested name to the same (first) rule file.
MISMAPPING_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |_d| ALL_DECKS.values.first }, unknown]\n"
)

# Wrongly applies DEFAULT_SKIP to an EXPLICIT request: `-rd deck=density` would
# silently run no density rules at all.
SKIPPING_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    sel = requested.select { |d| ALL_DECKS.key?(d) && "
    "!DEFAULT_SKIP_DECKS.include?(d) }\n"
    "    [PREAMBLE + sel.map { |d| ALL_DECKS[d] }, unknown]\n"
)

# Never reports an unknown deck: a typo'd -rd deck=... runs silently.
EMPTY_UNKNOWN_REQUESTED = _requested_branch(
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, []]\n"
)

# Reports every requested name as unknown, known ones included.
OVERBROAD_UNKNOWN_REQUESTED = _requested_branch(
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, requested]\n"
)

# Shape breakage on the requested branch: `unknown` is not a list of strings.
UNKNOWN_NOT_A_LIST_REQUESTED = _requested_branch(
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, 'nope']\n"
)

UNKNOWN_NON_STRING_REQUESTED = _requested_branch(
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, [7]]\n"
)

# ---------------------------------------------------------------------------
# in-place mutation of the argument. The emitter hands deck_files the very array
# it is about to describe, so without a pre-call snapshot the callee can rewrite
# the expectation it is judged against: a destructive reorder would agree with
# itself at exit 0, and a correct-but-destructive filter would be blamed for an
# unknown name the callee itself removed from the emitted request.
# ---------------------------------------------------------------------------

# Same reorder bug as REVERSED_REQUESTED, written destructively on the argument.
DESTRUCTIVE_REVERSE_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    requested.reverse!\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
)

# Re-sorts the request into registry order IN PLACE: `-rd deck=b,a` would run the
# decks as a,b. Order is the contract, so this is a real break.
DESTRUCTIVE_RESORT_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    requested.sort_by! { |d| ALL_DECKS.keys.index(d) || -1 }\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
)

# SEMANTICALLY CORRECT and destructive: it computes the unknown names first, then
# filters the caller's array in place before mapping. Same [files, unknown] as
# the shipped deck_files; only the argument is left changed. This must be GREEN.
CORRECT_DESTRUCTIVE_SELECT_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    requested.select! { |d| ALL_DECKS.key?(d) }\n"
    "    [PREAMBLE + requested.map { |d| ALL_DECKS[d] }, unknown]\n"
)

# Treats an EMPTY request as "no request": a SEPARATOR-ONLY `-rd deck=,` (the
# runset splits on ',' and `",".split(",")` is []) would silently run the whole
# default deck set instead of nothing. A bare `-rd deck=` does NOT reach this: the
# runset's `!$deck.empty?` guard short-circuits it to nil, the default run. Every
# non-empty probe agrees with this producer, so only the empty probe can see it.
EMPTY_FALLBACK_REQUESTED = _requested_branch(
    "    return deck_files(nil) if requested.empty?\n"
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
)

# CASE-FOLDS an explicit deck name: `-rd deck=PAD` resolves to pad. The contract
# is exact-name matching, so an upcased name must come back unknown. Every real
# deck name is lowercase, so no probe made of registry names alone can construct
# the input that discriminates this; the case-fold probe upcases one on purpose.
CASE_FOLDING_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d.downcase) }\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d.downcase) }"
    ".map { |d| ALL_DECKS[d.downcase] }, unknown]\n"
)

# Expands a PARTIAL request to the whole registry ("pull the prerequisites in").
# A request that already names every deck cannot see this; only a strict subset
# can, which is why a second, subset probe is emitted.
SUBSET_EXPANDING_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    known = requested.select { |d| ALL_DECKS.key?(d) }\n"
    "    known = ALL_DECKS.keys if known.length < ALL_DECKS.size\n"
    "    [PREAMBLE + known.map { |d| ALL_DECKS[d] }, unknown]\n"
)


def _rb_list(items) -> str:
    return "[" + ", ".join(f"'{i}'" for i in items) + "]"


@dataclass
class Tree:
    """
    A synthesizable fake <drc> directory.

    The default registry is {'a' => a.drc, 'bb' => b.drc}. The two-character name
    is load-bearing, not decoration: the MANGLED-NAME probe needs a name that can
    be truncated into a form the registry does not carry ('bb' -> 'b'), and a
    registry of one-character, separator-free names cannot express one at all,
    which the checker reports as a named probe-degenerate exit 2 rather than as a
    vacuous green (test_57 pins that).
    """

    decks: list = field(default_factory=lambda: [("a", "a.drc"), ("bb", "b.drc")])
    preamble: list = field(default_factory=lambda: ["layers_def.drc"])
    py_available: list = field(default_factory=lambda: ["a", "bb"])
    py_skip: list = field(default_factory=lambda: ["bb"])
    rb_skip: list = field(default_factory=lambda: ["bb"])
    relocated: dict = field(
        default_factory=lambda: {"assembly": "Promoted to the ADK."}
    )
    disk: list = field(default_factory=lambda: ["a.drc", "b.drc", "layers_def.drc"])
    golden: dict = field(default_factory=lambda: {"a": "a"})
    untested: list = field(default_factory=lambda: ["bb"])
    deck_files_body: str = GOOD_DECK_FILES
    rb_tail: str = ""
    run_drc: str = CLEAN_RUN_DRC
    main_drc: str = CLEAN_MAIN_DRC

    def write(self, root: Path) -> Path:
        drc = root / "drc"
        (drc / "rule_decks").mkdir(parents=True, exist_ok=True)
        (drc / "testing").mkdir(parents=True, exist_ok=True)

        pairs = ",\n    ".join(f"'{n}'" for n in self.py_available)
        reloc = ",\n    ".join(f"'{k}': {v!r}" for k, v in self.relocated.items())
        (drc / "intm4tm2_decks.py").write_text(
            "# SPDX-License-Identifier: Apache-2.0\n"
            f"AVAILABLE_DECKS = (\n    {pairs},\n)\n\n"
            f"DEFAULT_SKIP_DECKS = frozenset({{{', '.join(repr(s) for s in self.py_skip)}}})\n\n"
            f"RELOCATED_DECKS = {{\n    {reloc},\n}}\n",
            encoding="utf-8",
        )

        rows = "\n".join(f"    '{n}' => '{f}'," for n, f in self.decks)
        (drc / "intm4tm2_decks.rb").write_text(
            "# SPDX-License-Identifier: Apache-2.0\n"
            "module IntM4TM2Decks\n"
            f"  ALL_DECKS = {{\n{rows}\n  }}.freeze\n"
            f"  PREAMBLE = {_rb_list(self.preamble)}.freeze\n"
            f"  DEFAULT_SKIP_DECKS = {_rb_list(self.rb_skip)}.freeze\n"
            f"{self.deck_files_body}"
            "end\n" + self.rb_tail,
            encoding="utf-8",
        )

        golden = "\n".join(
            f"    {table!r}: {{'deck': {deck!r}, 'cells': {{}}}}," for table, deck in self.golden.items()
        )
        (drc / "testing" / "run_regression.py").write_text(
            "# SPDX-License-Identifier: Apache-2.0\n"
            f"GOLDEN = {{\n{golden}\n}}\n\n"
            f"KNOWN_UNTESTED_DECKS = frozenset({{{', '.join(repr(u) for u in self.untested)}}})\n\n"
            "\ndef main():\n"
            "    import klayout.db as db  # noqa: F401 - runtime only, never at import\n"
            "    return db\n",
            encoding="utf-8",
        )

        (drc / "run_drc.py").write_text(self.run_drc, encoding="utf-8")
        (drc / "intm4tm2.drc").write_text(self.main_drc, encoding="utf-8")
        for name in self.disk:
            (drc / "rule_decks" / name).write_text("# stub\n", encoding="utf-8")
        return drc


def run_checker(drc: Path):
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(drc)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, proc.stdout + proc.stderr


def expect(tmp_path: Path, tree: Tree, code: int, needle: str | None = None):
    drc = tree.write(tmp_path)
    rc, out = run_checker(drc)
    assert rc == code, f"expected exit {code}, got {rc}\n{out}"
    if needle is not None:
        assert needle in out, f"expected {needle!r} in output\n{out}"
    return out


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def test_baseline_mini_tree_is_green(tmp_path):
    out = expect(tmp_path, Tree(), 0)
    assert "all assertions hold" in out


# ---------------------------------------------------------------------------
# the registry mutations
# ---------------------------------------------------------------------------


def test_01_deck_in_ruby_not_in_python(tmp_path):
    tree = Tree(decks=[("a", "a.drc"), ("bb", "b.drc"), ("cc", "c.drc")],
                disk=["a.drc", "b.drc", "c.drc", "layers_def.drc"])
    out = expect(tmp_path, tree, 1, "[ASSERT-2]")
    assert "not in AVAILABLE_DECKS (python)" in out


def test_02_deck_in_python_not_in_ruby(tmp_path):
    tree = Tree(py_available=["a", "bb", "cc"], untested=["bb", "cc"])
    out = expect(tmp_path, tree, 1, "[ASSERT-2]")
    assert "not in ALL_DECKS (ruby)" in out


def test_02b_same_decks_different_order(tmp_path):
    """
    Set-equal, order-divergent: ALL_DECKS order is the concatenation order of the
    runset's single shared-locals eval() and AVAILABLE_DECKS order is run_drc's
    parallel / --help order. One canonical order, so a one-sided reorder is a
    drift, and it is exactly the drift set-equality cannot see.
    """
    tree = Tree(
        decks=[("a", "a.drc"), ("cc", "c.drc"), ("bb", "b.drc")],
        py_available=["a", "bb", "cc"],
        disk=["a.drc", "b.drc", "c.drc", "layers_def.drc"],
        golden={"a": "a"},
        untested=["bb", "cc"],
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-2]")
    assert "do not carry the decks in the same order" in out
    assert "position 1" in out
    assert "'bb'" in out and "'cc'" in out
    # proof it is the ORDER check that fired, not a set difference
    assert "but not in ALL_DECKS (ruby)" not in out
    assert "but not in AVAILABLE_DECKS (python)" not in out


def test_03_skip_mismatch(tmp_path):
    expect(tmp_path, Tree(py_skip=["a"]), 1, "[ASSERT-3]")


def test_04_orphan_drc_on_disk(tmp_path):
    tree = Tree(disk=["a.drc", "b.drc", "layers_def.drc", "orphan.drc"])
    out = expect(tmp_path, tree, 1, "[ASSERT-4]")
    assert "orphan.drc" in out


def test_05_registered_file_absent_from_disk(tmp_path):
    tree = Tree(disk=["a.drc", "layers_def.drc"])
    out = expect(tmp_path, tree, 1, "[ASSERT-4]")
    assert "b.drc" in out


def test_06_deck_in_neither_golden_nor_untested(tmp_path):
    expect(tmp_path, Tree(untested=[]), 1, "[ASSERT-6]")


def test_07_deck_in_both_golden_and_untested(tmp_path):
    tree = Tree(golden={"a": "a", "bb": "bb"}, untested=["a", "bb"])
    out = expect(tmp_path, tree, 1, "[ASSERT-6]")
    assert "at once" in out


def test_08_relocated_collides_with_available(tmp_path):
    tree = Tree(
        decks=[("a", "a.drc"), ("bb", "b.drc"), ("assembly", "assembly.drc")],
        py_available=["a", "bb", "assembly"],
        disk=["a.drc", "b.drc", "assembly.drc", "layers_def.drc"],
        untested=["bb", "assembly"],
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-7]")
    assert "assembly" in out


def test_09_duplicate_in_available(tmp_path):
    expect(tmp_path, Tree(py_available=["a", "bb", "bb"]), 1, "[ASSERT-1]")


def test_10_ruby_syntax_error(tmp_path):
    tree = Tree(rb_tail="def broken(  \n")
    out = expect(tmp_path, tree, 2)
    assert "ruby-exit-nonzero" in out


def test_11_require_token_removed_from_main_drc(tmp_path):
    tree = Tree(
        main_drc=(
            "# stub intm4tm2.drc\n"
            "drc_files, unknown_decks = IntM4TM2Decks.deck_files(requested)\n"
        )
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-8]")
    assert "does not mention intm4tm2_decks" in out


def test_12_deck_files_drops_the_preamble(tmp_path):
    tree = Tree(deck_files_body=NO_PREAMBLE_DECK_FILES)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(nil)[0]" in out


# ---------------------------------------------------------------------------
# ASSERT-5, the REQUESTED branch of deck_files()
#
# `deck_files(nil)` is not the whole contract: `-rd deck=<name>` goes down the
# other branch, which selects the known names IN THE REQUESTED ORDER, maps each
# to its rule file, prepends PREAMBLE, applies NO default skip, and returns the
# unknown names. While only the nil branch was emitted, every one of the cases
# below sat at exit 0. The emitter builds FOUR requests out of the registry, so
# all of them are made of real deck names and need no hardcoding here: the full
# one, ALL_DECKS.keys.reverse, a strict subset of it (each with one synthetic
# unknown interleaved after the first entry), the empty request, and an upcased
# deck name ahead of its exact-case twin. Every one is snapshotted before the
# call and every one goes through the same single mirror assertion.
# ---------------------------------------------------------------------------


def test_13_requested_branch_reverses_the_selection(tmp_path):
    """
    Fable's repro: `.reverse` on the map of the selected files. The nil branch is
    untouched, so ASSERT-5's nil half stays silent and only the requested-branch
    check can fire. The probe order (keys.reverse) is deliberately not symmetric,
    so reversing it really does diverge.
    """
    out = expect(tmp_path, Tree(deck_files_body=REVERSED_REQUESTED), 1, "[ASSERT-5]")
    assert "deck_files(<explicit request>)[0]" in out
    assert "REQUESTED order" in out
    # the nil half is NOT what fired
    assert "deck_files(nil)[0]" not in out


def test_14_requested_branch_drops_a_selected_file(tmp_path):
    out = expect(tmp_path, Tree(deck_files_body=DROPPING_REQUESTED), 1, "[ASSERT-5]")
    assert "deck_files(<explicit request>)[0]" in out
    assert "deck_files(nil)[0]" not in out


def test_15_requested_branch_mismaps_a_name_to_a_file(tmp_path):
    """Right count, right order, wrong name -> file mapping."""
    out = expect(tmp_path, Tree(deck_files_body=MISMAPPING_REQUESTED), 1, "[ASSERT-5]")
    assert "deck_files(<explicit request>)[0]" in out
    assert "mis-maps a name to a file" in out


def test_16_requested_branch_wrongly_applies_default_skip(tmp_path):
    """
    A deck in DEFAULT_SKIP_DECKS is skipped by the default run and MUST still be
    selectable explicitly. This deck_files rejects it on the requested branch
    too, so `-rd deck=<skipped>` would run nothing while reporting success.
    """
    out = expect(tmp_path, Tree(deck_files_body=SKIPPING_REQUESTED), 1, "[ASSERT-5]")
    assert "deck_files(<explicit request>)[0]" in out
    assert "wrongly applies" in out and "DEFAULT_SKIP" in out
    assert "deck_files(nil)[0]" not in out


def test_17_requested_branch_wrongly_applies_default_skip_on_the_real_deck_set(tmp_path):
    """
    The same break on the contract-faithful fixture, where the skipped deck is
    the real one: density's file must appear in the expected list, because
    `-rd deck=density` is the documented way to run the density rules.
    """
    tree = replace(_contract_faithful_tree(), deck_files_body=SKIPPING_REQUESTED)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit request>)[0]" in out
    assert "density.drc" in out
    assert "deck_files(nil)[0]" not in out


def test_18_requested_branch_returns_no_unknown_decks(tmp_path):
    """A typo'd deck name would be swallowed instead of reported."""
    out = expect(tmp_path, Tree(deck_files_body=EMPTY_UNKNOWN_REQUESTED), 1, "[ASSERT-5]")
    assert "deck_files(<explicit request>)[1] mis-classifies unknown deck names" in out
    # the files half is correct here: only the unknown half fired
    assert "deck_files(<explicit request>)[0]" not in out


def test_19_requested_branch_calls_known_decks_unknown(tmp_path):
    out = expect(
        tmp_path, Tree(deck_files_body=OVERBROAD_UNKNOWN_REQUESTED), 1, "[ASSERT-5]"
    )
    assert "deck_files(<explicit request>)[1] mis-classifies unknown deck names" in out


def test_20_requested_branch_green_on_the_real_deck_set(tmp_path):
    """
    The positive: the contract-faithful fixture carries the shipped deck_files,
    so the probe (21 real names scrambled, one synthetic unknown interleaved,
    density included) composes exactly as the contract says and the run is green.
    """
    out = expect(tmp_path, _contract_faithful_tree(), 0)
    assert "all assertions hold" in out


def test_21_requested_branch_unknown_is_not_a_list(tmp_path):
    """A mis-shaped requested branch is no verdict, never a green."""
    tree = Tree(deck_files_body=UNKNOWN_NOT_A_LIST_REQUESTED)
    out = expect(tmp_path, tree, 2)
    assert "ruby-bad-shape" in out
    assert "'requested.unknown' is str" in out
    assert "internal-error" not in out


def test_22_requested_branch_unknown_holds_a_non_string(tmp_path):
    tree = Tree(deck_files_body=UNKNOWN_NON_STRING_REQUESTED)
    out = expect(tmp_path, tree, 2)
    assert "ruby-bad-shape" in out
    assert "'requested.unknown' contains 7" in out
    assert "internal-error" not in out


def _fake_ruby(tmp_path: Path, payload: str) -> dict:
    """A `ruby` on PATH that ignores its arguments and prints `payload`."""
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "ruby"
    shim.write_text("#!/bin/sh\ncat <<'PAYLOAD_EOF'\n" + payload + "\nPAYLOAD_EOF\n",
                    encoding="utf-8")
    shim.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    return env


def _run_with_fake_ruby(tmp_path: Path, payload):
    """
    Runs the checker against a normal mini tree while a stub stands in for ruby.

    The emitter lives inside the checker, so a real .rb cannot make it omit or
    mis-type the whole "requested" object; this is the only way to reach those
    branches of the shape validator end to end, exit code included.
    """
    drc = Tree().write(tmp_path)
    env = _fake_ruby(tmp_path, payload if isinstance(payload, str) else json.dumps(payload))
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(drc)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _well_shaped_payload() -> dict:
    """
    What the real emitter prints for the default mini tree.

    Five requested-branch probes: the full-registry one (with BOTH synthetic
    unknowns interleaved), the strict-subset one, the empty request, the
    case-folding pair and the mangled-name one. The mini tree carries two decks,
    so the subset probe's `ks.first(2)` covers the whole registry there and the
    first two probes nearly coincide; on any bigger registry (the contract-faithful
    fixture, the real tree) they differ. The case-fold pair is ['A', 'a']: 'a' is
    the first registry name whose upcased form is both different and absent, which
    is what `casefold_meta` announces. The mangling is 'b', the prefix (and
    suffix) truncation of 'bb', which the registry does not carry.
    """
    return {
        "decks": {"a": "a.drc", "bb": "b.drc"},
        "skip": ["bb"],
        "preamble": ["layers_def.drc"],
        "all_files": ["layers_def.drc", "a.drc"],
        "all_unknown": [],
        "casefold_meta": ["a", "A"],
        "mangled_meta": ["b"],
        "requested": {
            "input": [
                "bb",
                "__deck_check_bogus_zzz__",
                "a",
                "__deck_check_bogus_two__",
            ],
            "files": ["layers_def.drc", "b.drc", "a.drc"],
            "unknown": ["__deck_check_bogus_zzz__", "__deck_check_bogus_two__"],
        },
        "requested_subset": {
            "input": ["bb", "__deck_check_bogus_zzz__", "a"],
            "files": ["layers_def.drc", "b.drc", "a.drc"],
            "unknown": ["__deck_check_bogus_zzz__"],
        },
        "requested_empty": {
            "input": [],
            "files": ["layers_def.drc"],
            "unknown": [],
        },
        "requested_casefold": {
            "input": ["A", "a"],
            "files": ["layers_def.drc", "a.drc"],
            "unknown": ["A"],
        },
        "requested_mangled": {
            "input": ["b", "a"],
            "files": ["layers_def.drc", "a.drc"],
            "unknown": ["b"],
        },
    }


def test_23_fake_ruby_control_payload_is_green(tmp_path):
    """
    The control for the three cases below: this payload is the well-shaped one,
    so any exit 2 they produce comes from the mutation, not from the stub.
    """
    rc, out = _run_with_fake_ruby(tmp_path, _well_shaped_payload())
    assert rc == 0, out
    assert "all assertions hold" in out


def test_24_emitted_requested_object_missing(tmp_path):
    payload = _well_shaped_payload()
    del payload["requested"]
    rc, out = _run_with_fake_ruby(tmp_path, payload)
    assert rc == 2, out
    assert "ruby-bad-shape" in out
    assert "'requested' missing from the emitted object" in out


def test_25_emitted_requested_object_is_not_an_object(tmp_path):
    payload = _well_shaped_payload()
    payload["requested"] = ["b", "a"]
    rc, out = _run_with_fake_ruby(tmp_path, payload)
    assert rc == 2, out
    assert "ruby-bad-shape" in out
    assert "'requested' is list, expected an object" in out


@pytest.mark.parametrize("key", ["input", "files", "unknown"])
def test_26_emitted_requested_member_missing(tmp_path, key):
    payload = _well_shaped_payload()
    del payload["requested"][key]
    rc, out = _run_with_fake_ruby(tmp_path, payload)
    assert rc == 2, out
    assert "ruby-bad-shape" in out
    assert f"key 'requested.{key}' missing from the emitted object" in out


@pytest.mark.parametrize("key", ["input", "files", "unknown"])
def test_27_emitted_requested_member_is_not_an_array(tmp_path, key):
    payload = _well_shaped_payload()
    payload["requested"][key] = {"nope": 1}
    rc, out = _run_with_fake_ruby(tmp_path, payload)
    assert rc == 2, out
    assert "ruby-bad-shape" in out
    assert f"'requested.{key}' is dict, expected an array" in out


def test_28_probe_name_is_a_real_deck_is_no_verdict(tmp_path):
    """
    Degeneracy guard. If the registry ever really carried the probe's synthetic
    unknown name, the unknown half of the probe would prove nothing. That is a
    named exit 2, never a green.
    """
    bogus = "__deck_check_bogus_zzz__"
    tree = Tree(
        decks=[("a", "a.drc"), ("bb", "b.drc"), (bogus, "z.drc")],
        py_available=["a", "bb", bogus],
        disk=["a.drc", "b.drc", "z.drc", "layers_def.drc"],
        untested=["bb", bogus],
    )
    out = expect(tmp_path, tree, 2)
    assert "probe-degenerate" in out
    assert bogus in out
    assert "internal-error" not in out


# ---------------------------------------------------------------------------
# ASSERT-5, requested branch: the argument is ALIASED, so the probe request must
# be snapshotted before the call
#
# The emitter passes the probe array to deck_files and then reports it as the
# request that was made. While that was the SAME object the callee got, a
# producer mutating it in place moved the expectation with the bug: the two
# destructive reorders below both sat at exit 0, and the correct-but-destructive
# filter below was a false exit 1. A `snap = req.dup` taken before the call fixes
# both directions at once.
# ---------------------------------------------------------------------------


def test_29_requested_branch_reverses_the_argument_in_place(tmp_path):
    """
    `requested.reverse!`: the same reorder bug as test_13, written destructively.
    Emitting the post-call array made this self-consistent and green; the emitted
    "input" is the pre-call snapshot, so the reorder diverges from it.
    """
    out = expect(tmp_path, Tree(deck_files_body=DESTRUCTIVE_REVERSE_REQUESTED), 1,
                 "[ASSERT-5]")
    assert "deck_files(<explicit request>)[0]" in out
    assert "REQUESTED order" in out
    # the nil branch is untouched: only the requested half fired
    assert "deck_files(nil)[0]" not in out


def test_30_requested_branch_resorts_the_argument_in_place(tmp_path):
    """
    `requested.sort_by! { registry order }`: an explicit `-rd deck=b,a` would run
    a then b. Destructive, so it too was green before the snapshot.
    """
    out = expect(tmp_path, Tree(deck_files_body=DESTRUCTIVE_RESORT_REQUESTED), 1,
                 "[ASSERT-5]")
    assert "deck_files(<explicit request>)[0]" in out
    assert "REQUESTED order" in out
    assert "deck_files(nil)[0]" not in out


def test_31_correct_but_destructive_requested_branch_is_green(tmp_path):
    """
    The symmetric false FAIL, and the reason the fix is a dup and not a freeze.

    This deck_files is semantically correct: it classifies the unknown names
    first, then filters the caller's array in place and maps it. Its [files,
    unknown] pair is exactly the shipped one. Only the argument is left changed,
    and while the emitter reported that post-call array as the request, the
    unknown name had been filtered out of it and the checker blamed the producer
    for "mis-classifying" a name the emitted request no longer contained.
    """
    out = expect(tmp_path, Tree(deck_files_body=CORRECT_DESTRUCTIVE_SELECT_REQUESTED), 0)
    assert "all assertions hold" in out


def test_32_correct_but_destructive_requested_branch_is_green_on_the_real_deck_set(
    tmp_path,
):
    """The same producer on the contract-faithful fixture: still a clean green."""
    tree = replace(
        _contract_faithful_tree(), deck_files_body=CORRECT_DESTRUCTIVE_SELECT_REQUESTED
    )
    out = expect(tmp_path, tree, 0)
    assert "all assertions hold" in out


# ---------------------------------------------------------------------------
# ASSERT-5, requested branch: the SUBSET probe
#
# The full-registry probe names every deck, which makes it the least
# discriminating request possible for a bug that EXPANDS a selection. A second
# probe over a strict, non-empty subset closes that.
# ---------------------------------------------------------------------------


def _three_deck_tree(**kw) -> Tree:
    """
    A mini tree with three decks, so `ks.first(2)` really is a STRICT subset.

    The default two-deck tree cannot discriminate here: the subset probe would
    cover the whole registry and coincide with the full probe.
    """
    base = dict(
        decks=[("a", "a.drc"), ("bb", "b.drc"), ("cc", "c.drc")],
        py_available=["a", "bb", "cc"],
        disk=["a.drc", "b.drc", "c.drc", "layers_def.drc"],
        golden={"a": "a"},
        untested=["bb", "cc"],
    )
    base.update(kw)
    return Tree(**base)


def test_33_three_deck_tree_is_green(tmp_path):
    """The control for the case below: the same tree with the shipped deck_files."""
    out = expect(tmp_path, _three_deck_tree(), 0)
    assert "all assertions hold" in out


def test_34_requested_branch_expands_a_partial_request(tmp_path):
    """
    `known = ALL_DECKS.keys if known.length < ALL_DECKS.size`: asking for two
    decks silently runs all three, so `-rd deck=offgrid` would run the whole
    registry. The full-registry probe cannot see it (its request already names
    every deck, so the conditional never fires); only the subset probe does.
    """
    tree = _three_deck_tree(deck_files_body=SUBSET_EXPANDING_REQUESTED)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit subset request>)[0]" in out
    assert "expands the request" in out
    # proof it is the SUBSET probe that caught it: the full probe stayed silent
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(nil)[0]" not in out


def test_35_requested_branch_expands_a_partial_request_on_the_real_deck_set(tmp_path):
    """The same producer on the 21-deck contract-faithful fixture."""
    tree = replace(_contract_faithful_tree(), deck_files_body=SUBSET_EXPANDING_REQUESTED)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit subset request>)[0]" in out
    assert "deck_files(<explicit request>)[0]" not in out


def test_36_subset_probe_is_emitted_and_is_a_strict_subset(tmp_path):
    """
    Direct evidence about the second probe rather than about a mutant: run the
    emitter itself over the contract-faithful .rb and read what it printed.

    The subset request must be non-empty, strictly smaller than the registry, use
    real deck names in the full probe's order, carry the same interleaved
    synthetic unknown, and be the PRE-CALL snapshot (the shipped deck_files is
    non-destructive, so pre- and post-call agree there; the destructive cases
    above are what prove the snapshot is load-bearing).
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    drc = _contract_faithful_tree().write(tmp_path)
    rb = chk.load_ruby_truth(drc / "intm4tm2_decks.rb")
    full = rb["requested"]["input"]
    sub = rb["requested_subset"]["input"]
    names = list(rb["decks"].keys())
    rev = list(reversed(names))
    assert full == (
        rev[:1]
        + [chk.PROBE_UNKNOWN_NAME]
        + rev[1:2]
        + [chk.PROBE_UNKNOWN_NAME2]
        + rev[2:]
    )
    assert sub == [names[-1], chk.PROBE_UNKNOWN_NAME, names[-2]]
    real_in_sub = [n for n in sub if n != chk.PROBE_UNKNOWN_NAME]
    assert real_in_sub, "the subset probe must not be empty"
    assert set(real_in_sub) < set(names), "the subset probe must be a STRICT subset"
    assert rb["requested_subset"]["unknown"] == [chk.PROBE_UNKNOWN_NAME]
    assert rb["requested_subset"]["files"] == list(rb["preamble"]) + [
        rb["decks"][n] for n in real_in_sub
    ]


@pytest.mark.parametrize("count", [1, 2])
def test_37_subset_probe_stays_total_on_a_tiny_registry(tmp_path, count):
    """
    Degenerate registries, end to end. With one deck the subset degrades to the
    one deck that exists; with two it coincides with the full probe. Neither pads
    with a nil, neither crashes, and both still run every assertion.

    The emitter-level half pins the subset's SIZE at each boundary: it must carry
    min(2, len(registry)) real names, so a subset probe quietly narrowed to a
    single name (which can no longer see an order slip inside the subset) is a
    failure here rather than a silent weakening.
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    names = ["aa", "bb"][:count]
    tree = Tree(
        decks=[(n, f"{n}.drc") for n in names],
        py_available=names,
        py_skip=[],
        rb_skip=[],
        disk=[f"{n}.drc" for n in names] + ["layers_def.drc"],
        golden={n: n for n in names},
        untested=[],
    )
    out = expect(tmp_path, tree, 0)
    assert "all assertions hold" in out

    rb = chk.load_ruby_truth(tmp_path / "drc" / "intm4tm2_decks.rb")
    sub = rb["requested_subset"]["input"]
    real_in_sub = [n for n in sub if n != chk.PROBE_UNKNOWN_NAME]
    assert real_in_sub == list(reversed(names))[: min(2, count)]
    assert None not in sub and chk.PROBE_UNKNOWN_NAME in sub


def test_38_both_probes_are_well_formed_on_an_empty_registry(tmp_path):
    """
    The 0-deck edge, taken at the emitter rather than end to end (a registry with
    no decks cannot be written as a syntactically valid AVAILABLE_DECKS tuple).

    `ks.first([2, 0].min)` is [] and `insert([1, 0].min, ...)` inserts at 0, so
    the subset probe is exactly the synthetic unknown and the full probe is
    exactly the two of them: no nil padding, no crash, and the same shape the
    validator demands of every probe.
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    drc = Tree(decks=[], rb_skip=[], disk=["layers_def.drc"]).write(tmp_path)
    rb = chk.load_ruby_truth(drc / "intm4tm2_decks.rb")
    assert rb["decks"] == {}
    expected_input = {
        "requested": [chk.PROBE_UNKNOWN_NAME, chk.PROBE_UNKNOWN_NAME2],
        "requested_subset": [chk.PROBE_UNKNOWN_NAME],
    }
    for key, expected in expected_input.items():
        assert rb[key]["input"] == expected
        assert rb[key]["files"] == ["layers_def.drc"]
        assert rb[key]["unknown"] == expected
    # nothing to mangle in an empty registry, and an empty registry is vacuous for
    # every probe anyway, so the mangled probe is empty rather than degenerate
    assert rb["mangled_meta"] == []
    assert rb["requested_mangled"]["input"] == []


# ---------------------------------------------------------------------------
# ASSERT-5, requested branch: the EMPTY-REQUEST probe
#
# `requested == []` is a defined contract input, not a synonym for nil: it names
# exactly zero decks, so the answer is [PREAMBLE, []], while nil is the default
# all-decks run. A real consumer reaches it with a SEPARATOR-ONLY `-rd deck=,`,
# because intm4tm2.drc does `$deck.split(',')` and `",".split(",")` is []; a bare
# `-rd deck=` does not, since `!$deck.empty?` short-circuits that to nil. Every
# non-empty probe agrees with a producer that promotes an empty list into the full
# run, so it took a third probe, routed through the same mirror assertion, to see it.
# ---------------------------------------------------------------------------


def test_39_requested_branch_falls_back_to_the_full_run_on_an_empty_request(tmp_path):
    """
    `return deck_files(nil) if requested.empty?`: asking for no decks runs every
    non-skipped one. The nil branch is correct, the full and subset probes are
    non-empty and agree, so nothing but the empty-request probe can fire.
    """
    out = expect(tmp_path, Tree(deck_files_body=EMPTY_FALLBACK_REQUESTED), 1, "[ASSERT-5]")
    assert "deck_files(<explicit empty request>)[0]" in out
    # the expectation really is PREAMBLE alone, and the producer emitted the run
    assert "expected ['layers_def.drc']" in out
    assert "a.drc" in out
    # proof it is the EMPTY probe that caught it: every other probe stayed silent
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(<explicit subset request>)[0]" not in out
    assert "deck_files(<explicit case-folding request>)[0]" not in out
    assert "deck_files(nil)[0]" not in out


def test_40_empty_request_fallback_on_the_real_deck_set(tmp_path):
    """
    The same producer on the 21-deck contract-faithful fixture: `-rd deck=,` would
    run the whole default deck set while the contract says it must run none.
    """
    tree = replace(_contract_faithful_tree(), deck_files_body=EMPTY_FALLBACK_REQUESTED)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit empty request>)[0]" in out
    assert "3_1_offgrid.drc" in out
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(<explicit subset request>)[0]" not in out
    assert "deck_files(nil)[0]" not in out


# ---------------------------------------------------------------------------
# ASSERT-5, requested branch: the CASE-FOLD probe
#
# Deck-name matching is exact: `-rd deck=PAD` is not `-rd deck=pad`, and the
# runset is reachable directly (`klayout -rd deck=PAD`). Every real deck name is
# lowercase, so a probe built out of registry names alone can never construct the
# input that separates an exact-match producer from a downcasing one. The fourth
# probe upcases one real name and puts it ahead of its exact-case twin; the
# checker derives "known" from the emitted EXACT-match registry, so the upcased
# name is expected UNKNOWN and its twin KNOWN.
# ---------------------------------------------------------------------------


def test_41_requested_branch_case_folds_an_explicit_deck_name(tmp_path):
    """
    `ALL_DECKS.key?(d.downcase)`: the upcased name resolves to a real deck, so
    the producer emits its file twice and reports nothing unknown. Both halves of
    the mirror assertion diverge, and only on the case-fold probe.
    """
    out = expect(tmp_path, Tree(deck_files_body=CASE_FOLDING_REQUESTED), 1, "[ASSERT-5]")
    assert "deck_files(<explicit case-folding request>)[0]" in out
    assert "deck_files(<explicit case-folding request>)[1] mis-classifies" in out
    # the upcased name was requested and must have been expected unknown
    assert "'A'" in out
    # proof it is the CASE-FOLD probe that caught it: the lowercase probes agree
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(<explicit subset request>)[0]" not in out
    assert "deck_files(<explicit empty request>)[0]" not in out
    assert "deck_files(nil)[0]" not in out


def test_42_case_folding_on_the_real_deck_set(tmp_path):
    """
    The same producer on the contract-faithful fixture: `-rd deck=OFFGRID` would
    silently run the offgrid deck instead of reporting an unknown deck name.
    """
    tree = replace(_contract_faithful_tree(), deck_files_body=CASE_FOLDING_REQUESTED)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit case-folding request>)[0]" in out
    assert "deck_files(<explicit case-folding request>)[1] mis-classifies" in out
    assert "OFFGRID" in out
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(<explicit subset request>)[0]" not in out
    assert "deck_files(nil)[0]" not in out


def test_43_empty_and_casefold_probes_are_emitted_and_discriminating(tmp_path):
    """
    Direct evidence about the two added probes rather than about a mutant: run the
    emitter over the contract-faithful .rb and read what it printed.

    The empty probe must carry a genuinely EMPTY request and come back as PREAMBLE
    with nothing unknown. The case-fold probe must carry [k0.upcase, k0] for a real
    deck k0, and against the shipped deck_files the upcased name must be classified
    UNKNOWN while its exact-case twin maps to its rule file.
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    drc = _contract_faithful_tree().write(tmp_path)
    rb = chk.load_ruby_truth(drc / "intm4tm2_decks.rb")
    names = list(rb["decks"].keys())

    empty = rb["requested_empty"]
    assert empty["input"] == [], "the empty-request probe must ask for nothing"
    assert empty["files"] == list(rb["preamble"])
    assert empty["unknown"] == []

    k0, upcased = rb["casefold_meta"]
    assert k0 == names[0] == "offgrid"
    assert upcased == "OFFGRID"
    assert upcased not in rb["decks"], "the upcased twin must be an unknown name"
    cf = rb["requested_casefold"]
    assert cf["input"] == [upcased, k0]
    # the upcased name is UNKNOWN, its exact-case twin is KNOWN and maps to its file
    assert cf["unknown"] == [upcased]
    assert cf["files"] == list(rb["preamble"]) + [rb["decks"][k0]]


def test_44_casefold_probe_that_cannot_discriminate_is_no_verdict(tmp_path):
    """
    Degeneracy guard, the same discipline as the synthetic-unknown one: a registry
    whose names all upcase to themselves gives the case-fold probe nothing to
    discriminate with, so there is no verdict at all rather than a vacuous green.
    """
    tree = Tree(
        decks=[("A", "a.drc"), ("B_2", "b.drc")],
        py_available=["A", "B_2"],
        py_skip=[],
        rb_skip=[],
        disk=["a.drc", "b.drc", "layers_def.drc"],
        golden={"A": "A"},
        untested=["B_2"],
    )
    out = expect(tmp_path, tree, 2)
    assert "probe-degenerate" in out
    assert "cannot discriminate" in out
    assert "internal-error" not in out


def test_45_casefold_probe_falls_back_past_a_caseless_name(tmp_path):
    """
    The fallback that keeps the guard above from firing on a registry that merely
    STARTS with a caseless name: the emitter picks the first name that really does
    discriminate, not blindly the first key, so this tree is a clean green.
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    tree = Tree(
        decks=[("A", "a.drc"), ("bb", "b.drc")],
        py_available=["A", "bb"],
        py_skip=[],
        rb_skip=[],
        disk=["a.drc", "b.drc", "layers_def.drc"],
        golden={"A": "A"},
        untested=["bb"],
    )
    out = expect(tmp_path, tree, 0)
    assert "all assertions hold" in out

    rb = chk.load_ruby_truth(tmp_path / "drc" / "intm4tm2_decks.rb")
    assert rb["casefold_meta"] == ["bb", "BB"], "the caseless first name must be skipped"
    assert rb["requested_casefold"]["input"] == ["BB", "bb"]
    assert rb["requested_casefold"]["unknown"] == ["BB"]


def test_46_emitted_casefold_meta_that_contradicts_the_probe_is_no_verdict(tmp_path):
    """
    The announced pair and the request actually made must agree. A stub emitter
    that claims to have upcased a name while requesting only lowercase ones would
    otherwise report a green for a probe that exercised nothing.
    """
    payload = _well_shaped_payload()
    payload["requested_casefold"]["input"] = ["a", "a"]
    rc, out = _run_with_fake_ruby(tmp_path, payload)
    assert rc == 2, out
    assert "probe-degenerate" in out
    assert "not the announced" in out
    assert "internal-error" not in out


def test_47_emitted_empty_probe_that_is_not_empty_is_no_verdict(tmp_path):
    """The same discipline for the empty probe: a non-empty request there means
    `deck_files([])` was never exercised, which is a no-verdict, not a green."""
    payload = _well_shaped_payload()
    payload["requested_empty"]["input"] = ["a"]
    rc, out = _run_with_fake_ruby(tmp_path, payload)
    assert rc == 2, out
    assert "probe-degenerate" in out
    assert "did not carry an empty request" in out
    assert "internal-error" not in out


# ---------------------------------------------------------------------------
# the residual the probes deliberately do NOT cover: a REPEATED name
#
# `-rd deck=pad,pad` collapsed to one deck is arguably hardening, not a defect,
# so the contract does not settle it and no duplicate-name probe is emitted. This
# case PINS that choice rather than testing a behaviour: a `.uniq` producer is
# green, and the module docstring names it as a known residual.
# ---------------------------------------------------------------------------

UNIQUING_REQUESTED = _requested_branch(
    "    unknown = requested.uniq.reject { |d| ALL_DECKS.key?(d) }\n"
    "    [PREAMBLE + requested.uniq.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
)


def test_48_deduplicating_producer_is_green_and_documented_as_residual(tmp_path):
    """
    No probe repeats a name, so `.uniq` on both halves is invisible here and the
    run is green. That is deliberate, and the docstring says so; this test fails
    the day the residual clause stops naming it.
    """
    out = expect(tmp_path, Tree(deck_files_body=UNIQUING_REQUESTED), 0)
    assert "all assertions hold" in out
    doc = CHECKER.read_text(encoding="utf-8")
    # the residual clause is a NON-exhaustive list of classes (an exhaustive
    # enumeration went stale the moment a residual was closed), and a REPEATED
    # name must still be named in it
    assert "Known residual classes (NOT an exhaustive list)" in doc
    assert "a REPEATED name" in doc
    assert "deliberately not probed" in doc


# ---------------------------------------------------------------------------
# ASSERT-5a, the NIL branch: BOTH halves of the return value
#
# `deck_files(nil)` returns a PAIR, and while only its first element was emitted
# the second was never looked at: a nil branch that reported a bogus unknown name
# sat at exit 0 while intm4tm2.drc's `unknown_decks.each { |name|
# logger.warn(...) }` warned about a deck nobody asked for on EVERY default run.
# The contract literal is `return [PREAMBLE + selected, []]`, so the expectation
# is a constant the shipped producer already satisfies: no legitimate producer
# puts a name there, hence no false-fail risk.
# ---------------------------------------------------------------------------

NIL_BRANCH_BOGUS_UNKNOWN = (
    "  def self.deck_files(requested)\n"
    "    if requested.nil?\n"
    "      return [PREAMBLE + ALL_DECKS.reject { |n, _| "
    "DEFAULT_SKIP_DECKS.include?(n) }.values, ['bogus-unknown']]\n"
    "    end\n"
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
    "  end\n"
)


def test_49_nil_branch_reports_a_bogus_unknown_deck(tmp_path):
    """
    The nil branch's FILE list is correct and its requested branch is the shipped
    one, so every requested-branch probe agrees and only the nil branch's second
    element diverges. While that element was not emitted at all this was green.
    """
    out = expect(tmp_path, Tree(deck_files_body=NIL_BRANCH_BOGUS_UNKNOWN), 1, "[ASSERT-5]")
    assert "deck_files(nil)[1] must be empty" in out
    assert "bogus-unknown" in out
    # the file half of the nil branch is correct, and every requested probe agrees
    assert "deck_files(nil)[0]" not in out
    assert "<explicit" not in out


def test_50_nil_branch_unknown_half_is_emitted_and_empty(tmp_path):
    """
    Direct evidence rather than a mutant: the emitter prints the nil branch's
    unknown half, and against the contract-faithful producer it is EMPTY, which is
    exactly the contract literal `return [PREAMBLE + selected, []]`.
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    drc = _contract_faithful_tree().write(tmp_path)
    rb = chk.load_ruby_truth(drc / "intm4tm2_decks.rb")
    assert "all_unknown" in rb, "the nil branch must be emitted WHOLE, not half"
    assert rb["all_unknown"] == []
    assert rb["all_files"][0] == "layers_def.drc"


# ---------------------------------------------------------------------------
# ASSERT-5, requested branch: the unknown half's MULTIPLICITY
#
# Every probe used to interleave at most ONE synthetic unknown, so a producer
# that keeps only the FIRST unknown name it finds agreed with all of them:
# `-rd deck=typo1,typo2` would warn about one bad name and silently drop the
# other deck. The full-registry probe now interleaves TWO distinct synthetic
# unknowns, and the unknown half is compared as a MULTISET: the drop changes the
# length, so it is caught, while a producer that merely SORTS or REVERSES its
# warnings is not failed (the runset consumes that list only as a per-name
# logger.warn loop, so its order is cosmetic; the .rb doc attaches
# "order-preserving" to the FILES half, which stays order-exact).
# ---------------------------------------------------------------------------

FIRST_UNKNOWN_ONLY_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }.first(1)\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
)

SORTED_UNKNOWN_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }.sort\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
)

REVERSED_UNKNOWN_REQUESTED = _requested_branch(
    "    unknown = requested.reject { |d| ALL_DECKS.key?(d) }.reverse\n"
    "    [PREAMBLE + requested.select { |d| ALL_DECKS.key?(d) }"
    ".map { |d| ALL_DECKS[d] }, unknown]\n"
)


def test_51_requested_branch_keeps_only_the_first_unknown_name(tmp_path):
    """
    `.first(1)` on the unknown list. The files half is right, the first unknown is
    reported, and every single-unknown probe agrees; only the full probe, which
    interleaves TWO synthetic unknowns, sees the dropped one.
    """
    out = expect(tmp_path, Tree(deck_files_body=FIRST_UNKNOWN_ONLY_REQUESTED), 1,
                 "[ASSERT-5]")
    assert "deck_files(<explicit request>)[1] mis-classifies" in out
    assert "__deck_check_bogus_two__" in out
    # the files half is correct, and no single-unknown probe can see the drop
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(<explicit subset request>)[1]" not in out
    assert "deck_files(<explicit case-folding request>)[1]" not in out
    assert "deck_files(<explicit mangled-name request>)[1]" not in out
    assert "deck_files(nil)" not in out


def test_52_first_unknown_only_on_the_real_deck_set(tmp_path):
    """The same producer on the 21-deck contract-faithful fixture."""
    tree = replace(_contract_faithful_tree(), deck_files_body=FIRST_UNKNOWN_ONLY_REQUESTED)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit request>)[1] mis-classifies" in out
    assert "__deck_check_bogus_two__" in out
    assert "deck_files(<explicit request>)[0]" not in out


@pytest.mark.parametrize(
    "body", [SORTED_UNKNOWN_REQUESTED, REVERSED_UNKNOWN_REQUESTED],
    ids=["sorted", "reversed"],
)
def test_53_reordering_the_unknown_half_is_green(tmp_path, body):
    """
    The false-FAIL side of the multiset comparison, and the reason it is not an
    order-exact one. The runset only loops `logger.warn` over the unknown names,
    so their order carries nothing; a producer that sorts or reverses its warnings
    is legitimate and must stay green on every probe, on the real deck set too.
    """
    out = expect(
        tmp_path / "real", replace(_contract_faithful_tree(), deck_files_body=body), 0
    )
    assert "all assertions hold" in out
    out = expect(tmp_path / "mini", Tree(deck_files_body=body), 0)
    assert "all assertions hold" in out


def test_54_full_probe_interleaves_two_distinct_synthetic_unknowns(tmp_path):
    """
    Direct evidence about the second synthetic name: the full probe must request
    BOTH, they must differ, both must be absent from the registry, and neither may
    be appended at the tail (an interior unknown is what shows an order slip in
    the known/unknown split).
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    drc = _contract_faithful_tree().write(tmp_path)
    rb = chk.load_ruby_truth(drc / "intm4tm2_decks.rb")
    full = rb["requested"]["input"]
    assert chk.PROBE_UNKNOWN_NAME != chk.PROBE_UNKNOWN_NAME2
    for name in (chk.PROBE_UNKNOWN_NAME, chk.PROBE_UNKNOWN_NAME2):
        assert name in full
        assert name not in rb["decks"]
        assert full.index(name) < len(full) - 1, "an unknown must be interleaved"
    assert sorted(rb["requested"]["unknown"]) == sorted(
        [chk.PROBE_UNKNOWN_NAME, chk.PROBE_UNKNOWN_NAME2]
    )
    # no probe repeats a name: dedup stays the deliberately unprobed residual
    assert len(full) == len(set(full))


def test_55_second_probe_name_is_a_real_deck_is_no_verdict(tmp_path):
    """
    The degeneracy guard covers BOTH synthetic names, not just the first: a
    registry that really carried the second one would leave the unknown half of
    the full probe unable to see a dropped name, which is a named exit 2.
    """
    bogus2 = "__deck_check_bogus_two__"
    tree = Tree(
        decks=[("a", "a.drc"), ("bb", "b.drc"), (bogus2, "z.drc")],
        py_available=["a", "bb", bogus2],
        disk=["a.drc", "b.drc", "z.drc", "layers_def.drc"],
        untested=["bb", bogus2],
    )
    out = expect(tmp_path, tree, 2)
    assert "probe-degenerate" in out
    assert bogus2 in out
    assert "internal-error" not in out


# ---------------------------------------------------------------------------
# ASSERT-5, requested branch: the MANGLED-NAME probe
#
# Name matching is EXACT in both producers (`ALL_DECKS.key?` in the .rb,
# `d not in AVAILABLE_DECKS` in run_drc.py), but in this registry every exact key
# precedes its own extensions, so no probe built out of registry names alone ever
# requests a strict partial of one. A producer that matches a PREFIX, a SUFFIX or
# a separator-stripped form was therefore green: `-rd deck=top` would silently run
# topvia1 with no unknown-deck warning at all. The fifth probe requests manglings
# derived from real names and verified absent from the registry, each expected
# UNKNOWN, so the shipped exact-match producer passes and a lenient one diverges.
# ---------------------------------------------------------------------------

PREFIX_MATCHING_REQUESTED = _requested_branch(
    "    hit = lambda { |d| ALL_DECKS.keys.find { |k| k.start_with?(d) } }\n"
    "    unknown = requested.reject { |d| hit.call(d) }\n"
    "    known = requested.select { |d| hit.call(d) }\n"
    "    [PREAMBLE + known.map { |d| ALL_DECKS[hit.call(d)] }, unknown]\n"
)

SEPARATOR_NORMALISING_REQUESTED = _requested_branch(
    "    hit = lambda { |d| ALL_DECKS.keys.find "
    "{ |k| k.delete('_') == d.delete('_') } }\n"
    "    unknown = requested.reject { |d| hit.call(d) }\n"
    "    known = requested.select { |d| hit.call(d) }\n"
    "    [PREAMBLE + known.map { |d| ALL_DECKS[hit.call(d)] }, unknown]\n"
)


def test_56_requested_branch_matches_a_prefix_instead_of_the_exact_name(tmp_path):
    """
    `ALL_DECKS.keys.any? { |k| k.start_with?(name) }`: the truncated name resolves
    to a real deck, so the producer emits a file too many and reports nothing
    unknown for it. Every probe made of exact registry names agrees with this
    producer; only the mangled-name probe sees it.
    """
    out = expect(tmp_path, Tree(deck_files_body=PREFIX_MATCHING_REQUESTED), 1,
                 "[ASSERT-5]")
    assert "deck_files(<explicit mangled-name request>)[0]" in out
    assert "deck_files(<explicit mangled-name request>)[1] mis-classifies" in out
    # proof it is the MANGLED probe that caught it: every exact-name probe agrees
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(<explicit subset request>)[0]" not in out
    assert "deck_files(<explicit empty request>)[0]" not in out
    assert "deck_files(<explicit case-folding request>)[0]" not in out
    assert "deck_files(nil)" not in out


def test_57_prefix_matching_on_the_real_deck_set(tmp_path):
    """
    The same producer on the contract-faithful fixture: `-rd deck=offgri` (or the
    documented `-rd deck=top`) would silently run a real deck instead of reporting
    an unknown deck name.
    """
    tree = replace(_contract_faithful_tree(), deck_files_body=PREFIX_MATCHING_REQUESTED)
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit mangled-name request>)[0]" in out
    assert "deck_files(<explicit mangled-name request>)[1] mis-classifies" in out
    assert "offgri" in out
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(nil)" not in out


def test_58_requested_branch_normalises_separators(tmp_path):
    """
    The second lenient form, and the reason the probe carries a separator-stripped
    mangling as well as truncations: `-rd deck=ubmfloor` must be an unknown deck,
    not a synonym for ubm_floor. Only a registry that HAS a separator in a name can
    express this, so it runs on the contract-faithful fixture.
    """
    tree = replace(
        _contract_faithful_tree(), deck_files_body=SEPARATOR_NORMALISING_REQUESTED
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-5]")
    assert "deck_files(<explicit mangled-name request>)[0]" in out
    assert "deck_files(<explicit mangled-name request>)[1] mis-classifies" in out
    assert "ubmfloor" in out
    assert "deck_files(<explicit request>)[0]" not in out
    assert "deck_files(nil)" not in out


def test_59_mangled_probe_is_emitted_and_absent_from_the_registry(tmp_path):
    """
    Direct evidence about the fifth probe: every mangling it requests must be a
    non-empty name DERIVED from a real deck name (a prefix truncation, a suffix
    truncation or a separator-stripped form) and ABSENT from ALL_DECKS, it must be
    interleaved with real deck names, and against the shipped deck_files every one
    of them must come back UNKNOWN while the real names map to their files.
    """
    import check_deck_registry as chk  # noqa: PLC0415 - local by design

    drc = _contract_faithful_tree().write(tmp_path)
    rb = chk.load_ruby_truth(drc / "intm4tm2_decks.rb")
    names = list(rb["decks"].keys())
    mangled = rb["mangled_meta"]
    assert mangled, "the mangled-name probe must not be vacuous on a real registry"
    # the three realistic drift forms are all present on this registry
    assert "offgri" in mangled and "ffgrid" in mangled and "ubmfloor" in mangled
    probe = rb["requested_mangled"]
    for m in mangled:
        assert m and m not in rb["decks"]
        assert any(
            k != m and (k.startswith(m) or k.endswith(m) or k.replace("_", "") == m)
            for k in names
        ), f"{m!r} is not derived from a real deck name"
        assert m in probe["input"]
    real_in_probe = [n for n in probe["input"] if n in rb["decks"]]
    assert real_in_probe, "the mangled probe must also carry real deck names"
    assert sorted(probe["unknown"]) == sorted(mangled)
    assert probe["files"] == list(rb["preamble"]) + [
        rb["decks"][n] for n in real_in_probe
    ]
    assert len(probe["input"]) == len(set(probe["input"]))


def test_60_mangled_probe_that_cannot_discriminate_is_no_verdict(tmp_path):
    """
    Degeneracy guard, the same discipline as the synthetic-unknown and case-fold
    ones: a registry of one-character, separator-free names cannot express a
    strict partial of any deck name at all, so the mangled probe would prove
    nothing. That is a named exit 2, never a vacuous green.
    """
    tree = Tree(
        decks=[("a", "a.drc"), ("b", "b.drc")],
        py_available=["a", "b"],
        py_skip=[],
        rb_skip=[],
        disk=["a.drc", "b.drc", "layers_def.drc"],
        golden={"a": "a"},
        untested=["b"],
    )
    out = expect(tmp_path, tree, 2)
    assert "probe-degenerate" in out
    assert "mangled-name probe cannot discriminate" in out
    assert "internal-error" not in out


@pytest.mark.parametrize(
    "meta, mg_input, needle",
    [
        (["b"], ["a"], "the mangled name is not being requested"),
        (["a"], ["a"], "is empty or is a real deck"),
        (["zz"], ["zz", "a"], "is not a prefix truncation"),
    ],
    ids=["not-requested", "real-deck", "not-derived"],
)
def test_61_emitted_mangled_meta_that_does_not_discriminate_is_no_verdict(
    tmp_path, meta, mg_input, needle
):
    """
    The announced manglings and the request actually made must agree, and every
    announced name must really be an absent, derived form. A stub emitter that
    claimed otherwise would report a green for a probe that exercised nothing.
    """
    payload = _well_shaped_payload()
    payload["mangled_meta"] = meta
    payload["requested_mangled"]["input"] = mg_input
    payload["requested_mangled"]["files"] = ["layers_def.drc", "a.drc"]
    payload["requested_mangled"]["unknown"] = [
        n for n in mg_input if n not in payload["decks"]
    ]
    rc, out = _run_with_fake_ruby(tmp_path, payload)
    assert rc == 2, out
    assert "probe-degenerate" in out
    assert needle in out
    assert "internal-error" not in out


# ---------------------------------------------------------------------------
# ASSERT-8 positive controls: every binding form an AST catches
#
# The rule is not "no assignment to the three names" but "the three names are
# bound by the single authorized `from intm4tm2_decks import ...` and NOWHERE
# else", so each Python binding form gets its own case here.
# ---------------------------------------------------------------------------

REINLINE_FORMS = {
    "annotated": (
        CLEAN_RUN_DRC + "\nAVAILABLE_DECKS: tuple = ('a', 'b')\n"
    ),
    "computed": (
        CLEAN_RUN_DRC + "\ndef _load():\n    return ['a', 'b']\n\n\n"
        "AVAILABLE_DECKS = tuple(_load())\n"
    ),
    "augmented": (
        CLEAN_RUN_DRC + "\nAVAILABLE_DECKS += ('c',)\n"
    ),
    "global_rebind": (
        CLEAN_RUN_DRC + "\ndef _reinline():\n    global AVAILABLE_DECKS\n"
        "    AVAILABLE_DECKS = ('a', 'b')\n"
    ),
    "tuple_unpack": (
        CLEAN_RUN_DRC + "\nAVAILABLE_DECKS, _rest = ('a', 'b'), None\n"
    ),
    "walrus": (
        CLEAN_RUN_DRC + "\n_x = [d for d in ['a'] if (AVAILABLE_DECKS := ('a', 'b'))]\n"
    ),
    # forms below are not assignments at all: they were false-greens while the
    # walk only modelled the assignment family.
    "for_target": (
        CLEAN_RUN_DRC + "\nfor AVAILABLE_DECKS in [('x',)]:\n    pass\n"
    ),
    "async_for_target": (
        CLEAN_RUN_DRC + "\nasync def _scan(src):\n"
        "    async for AVAILABLE_DECKS in src:\n        pass\n"
    ),
    "function_def": (
        CLEAN_RUN_DRC + "\ndef AVAILABLE_DECKS():\n    pass\n"
    ),
    "async_function_def": (
        CLEAN_RUN_DRC + "\nasync def AVAILABLE_DECKS():\n    pass\n"
    ),
    "class_def": (
        CLEAN_RUN_DRC + "\nclass AVAILABLE_DECKS:\n    pass\n"
    ),
    "import_as": (
        CLEAN_RUN_DRC + "\nimport intm4tm2_decks as AVAILABLE_DECKS\n"
    ),
    "from_import_as": (
        CLEAN_RUN_DRC + "\nfrom os import environ as AVAILABLE_DECKS\n"
    ),
    "with_as": (
        CLEAN_RUN_DRC + "\nwith open('x') as AVAILABLE_DECKS:\n    pass\n"
    ),
    "async_with_as": (
        CLEAN_RUN_DRC + "\nasync def _open(cm):\n"
        "    async with cm as AVAILABLE_DECKS:\n        pass\n"
    ),
    "except_as": (
        CLEAN_RUN_DRC + "\ntry:\n    pass\nexcept Exception as AVAILABLE_DECKS:\n    pass\n"
    ),
    "comprehension_target": (
        CLEAN_RUN_DRC + "\n_x = [AVAILABLE_DECKS for AVAILABLE_DECKS in ['a', 'b']]\n"
    ),
    "function_parameter": (
        CLEAN_RUN_DRC + "\ndef _pick(AVAILABLE_DECKS):\n    return AVAILABLE_DECKS\n"
    ),
    "lambda_parameter": (
        CLEAN_RUN_DRC + "\n_pick = lambda AVAILABLE_DECKS: AVAILABLE_DECKS\n"
    ),
    "match_capture": (
        CLEAN_RUN_DRC + "\ndef _m(x):\n    match x:\n"
        "        case AVAILABLE_DECKS:\n            return AVAILABLE_DECKS\n"
    ),
}


@pytest.mark.parametrize("form", sorted(REINLINE_FORMS))
def test_assert8_catches_reinline_form(tmp_path, form):
    out = expect(tmp_path, Tree(run_drc=REINLINE_FORMS[form]), 1, "[ASSERT-8]")
    assert "rebinds AVAILABLE_DECKS" in out


def test_assert8_names_the_binding_form(tmp_path):
    """The failure must say WHICH form bound the name, not just that one did."""
    out = expect(tmp_path, Tree(run_drc=REINLINE_FORMS["class_def"]), 1, "[ASSERT-8]")
    assert "rebinds AVAILABLE_DECKS (class definition)" in out


def test_assert8_relative_import_fails(tmp_path):
    """
    `from .intm4tm2_decks import ...` satisfies "module is intm4tm2_decks" but
    run_drc.py is executed as a script, where a relative import raises at import
    time. Only an absolute import is a real consumption of the module.
    """
    tree = Tree(
        run_drc=(
            "from .intm4tm2_decks import AVAILABLE_DECKS, DEFAULT_SKIP_DECKS, "
            "RELOCATED_DECKS\n\n\ndef cli():\n"
            "    return AVAILABLE_DECKS, DEFAULT_SKIP_DECKS, RELOCATED_DECKS\n"
        )
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-8]")
    assert "relative import" in out
    assert "runs as a script" in out


def test_assert8_clean_import_only_passes(tmp_path):
    expect(tmp_path, Tree(run_drc=CLEAN_RUN_DRC), 0)


def test_assert8_missing_import_fails(tmp_path):
    tree = Tree(run_drc="AVAILABLE_DECKS = ('a', 'b')\n")
    out = expect(tmp_path, tree, 1, "[ASSERT-8]")
    assert "does not import" in out


def test_assert8_import_kept_but_name_never_read_fails(tmp_path):
    """
    The python-side counterpart of the exclusive ruby deck_files( positive.

    This run_drc.py keeps the single authorized absolute import and rebinds
    nothing, so the binding half of ASSERT-8 is entirely silent; it then drives
    the run off its own curated list. Before the Load-usage check the checker
    exited 0 here while run_drc's effective deck set was free to diverge from the
    registry.
    """
    tree = Tree(
        run_drc=(
            "from intm4tm2_decks import AVAILABLE_DECKS, DEFAULT_SKIP_DECKS, "
            "RELOCATED_DECKS\n\n"
            "_DECKS_FOR_RUN = ('offgrid', 'angle')\n\n\n"
            "def cli():\n"
            "    return list(_DECKS_FOR_RUN)\n"
        )
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-8]")
    assert "imports AVAILABLE_DECKS from intm4tm2_decks but never reads it" in out
    assert "imports DEFAULT_SKIP_DECKS from intm4tm2_decks but never reads it" in out
    assert "imports RELOCATED_DECKS from intm4tm2_decks but never reads it" in out
    assert "the registry is imported, not consumed" in out
    # the binding half really is silent: only the consumption half fired
    assert "rebinds" not in out
    assert "does not import" not in out


def test_assert8_one_unread_name_of_three_fails(tmp_path):
    """Per-name, not all-or-nothing: two consumed names do not cover the third."""
    tree = Tree(
        run_drc=(
            "from intm4tm2_decks import AVAILABLE_DECKS, DEFAULT_SKIP_DECKS, "
            "RELOCATED_DECKS\n\n\n"
            "def cli():\n"
            "    return sorted(AVAILABLE_DECKS), set(DEFAULT_SKIP_DECKS)\n"
        )
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-8]")
    assert "imports RELOCATED_DECKS from intm4tm2_decks but never reads it" in out
    assert "imports AVAILABLE_DECKS" not in out
    assert "imports DEFAULT_SKIP_DECKS" not in out


def test_assert8_read_inside_an_fstring_counts_as_a_read(tmp_path):
    """
    The real run_drc.py reads AVAILABLE_DECKS twice inside f-strings (--help text
    and the --deck help string). ast.walk descends into JoinedStr, so those are
    ast.Name Load nodes like any other; a Load scan that missed them would
    false-fail the real tree.
    """
    tree = Tree(
        run_drc=(
            "from intm4tm2_decks import AVAILABLE_DECKS, DEFAULT_SKIP_DECKS, "
            "RELOCATED_DECKS\n\n\n"
            "def cli():\n"
            "    return (\n"
            "        f\"Available decks: {', '.join(AVAILABLE_DECKS)}\",\n"
            "        f\"skip {sorted(DEFAULT_SKIP_DECKS)}\",\n"
            "        f\"moved {sorted(RELOCATED_DECKS)}\",\n"
            "    )\n"
        )
    )
    expect(tmp_path, tree, 0)


def test_assert8_reinlined_ruby_hash_literal_fails(tmp_path):
    tree = Tree(main_drc=CLEAN_MAIN_DRC + "\nall_decks = {\n  'a' => 'a.drc',\n}\n")
    out = expect(tmp_path, tree, 1, "[ASSERT-8]")
    assert "re-inlines" in out


def test_assert8_require_without_reading_fails(tmp_path):
    """A bare require is not consumption: the guard must still bite."""
    tree = Tree(main_drc="require File.join(File.dirname(__FILE__), 'intm4tm2_decks')\n")
    out = expect(tmp_path, tree, 1, "[ASSERT-8]")
    assert "never calls IntM4TM2Decks.deck_files()" in out


def test_assert8_accepts_the_deck_files_composition_form(tmp_path):
    """
    The shipped intm4tm2.drc consumes the module as
    `drc_files, unknown = IntM4TM2Decks.deck_files(requested)` and keeps no local
    all_decks at all. That is the sanctioned composition path and the only form
    the ASSERT-8 positive accepts.
    """
    tree = Tree(
        main_drc=(
            "# stub intm4tm2.drc\n"
            "require File.join(File.dirname(__FILE__), 'intm4tm2_decks')\n"
            "drc_files, unknown_decks = IntM4TM2Decks.deck_files(requested)\n"
        )
    )
    expect(tmp_path, tree, 0)


def test_assert8_alias_without_deck_files_call_fails(tmp_path):
    """
    The composition drift the exclusive positive exists to close: the runset
    aliases IntM4TM2Decks::ALL_DECKS and rebuilds the file list inline, so it
    never runs deck_files() while assert 5 keeps validating deck_files' output.
    No `all_decks = {` literal (NEG is silent) and the token is there (TOKEN is
    silent), so nothing but the exclusive positive can catch it.
    """
    out = expect(tmp_path, Tree(main_drc=ALIAS_ONLY_MAIN_DRC), 1, "[ASSERT-8]")
    assert "never calls IntM4TM2Decks.deck_files()" in out
    assert "composition function is not consumed" in out


# ---------------------------------------------------------------------------
# exit-2 discipline
# ---------------------------------------------------------------------------


def test_exit2_ruby_absent(tmp_path):
    """PATH stripped of ruby: no verdict, never a green or a failed assertion."""
    drc = Tree().write(tmp_path)
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(drc)],
        capture_output=True,
        text=True,
        env={"PATH": str(empty_bin), "HOME": str(tmp_path)},
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ruby-absent" in proc.stderr


def test_exit2_rb_absent(tmp_path):
    drc = Tree().write(tmp_path)
    (drc / "intm4tm2_decks.rb").unlink()
    rc, out = run_checker(drc)
    assert rc == 2
    assert "ruby-module-absent" in out


def test_exit2_bad_json(tmp_path):
    tree = Tree()
    drc = tree.write(tmp_path)
    rb = drc / "intm4tm2_decks.rb"
    rb.write_text(
        rb.read_text(encoding="utf-8").replace(
            "module IntM4TM2Decks", "$stdout.puts 'not json at all'\nmodule IntM4TM2Decks"
        ),
        encoding="utf-8",
    )
    rc, out = run_checker(drc)
    assert rc == 2
    assert "ruby-bad-json" in out


def test_exit2_bad_shape(tmp_path):
    tree = Tree()
    drc = tree.write(tmp_path)
    rb = drc / "intm4tm2_decks.rb"
    rb.write_text(
        rb.read_text(encoding="utf-8").replace(
            "PREAMBLE = ['layers_def.drc'].freeze", "PREAMBLE = [123].freeze"
        ),
        encoding="utf-8",
    )
    rc, out = run_checker(drc)
    assert rc == 2
    assert "ruby-bad-shape" in out


def test_exit2_ruby_bad_encoding(tmp_path):
    """
    Bytes on the emitter's stdout that are not UTF-8 get their own named reason.
    Before, the decode blew up inside subprocess (text=True) and the run landed
    in the last-resort BaseException guard as an unnamed 'internal-error'.
    """
    tree = Tree(rb_tail="$stdout.binmode\n$stdout.write([0xff, 0xfe].pack('C*'))\n")
    out = expect(tmp_path, tree, 2)
    assert "ruby-bad-encoding" in out
    assert "internal-error" not in out


def test_ruby_emitter_decoding_is_not_locale_dependent(tmp_path):
    """
    A non-ASCII deck name under LC_ALL=C is valid UTF-8 on the wire, so it must
    produce a normal verdict. The checker decodes the emitter's bytes as UTF-8
    itself; letting subprocess decode with the process locale turned this into an
    unnamed exit 2 instead.
    """
    tree = Tree(
        decks=[("a", "a.drc"), ("wörter", "b.drc")],
        py_available=["a", "wörter"],
        py_skip=["wörter"],
        rb_skip=["wörter"],
        untested=["wörter"],
    )
    drc = tree.write(tmp_path)
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    # PEP 538 would coerce C to C.UTF-8 and hide the very thing under test, so
    # the child really does run with an ASCII locale encoding here.
    env["PYTHONCOERCECLOCALE"] = "0"
    env["PYTHONUTF8"] = "0"
    for var in ("LANG", "LC_CTYPE"):
        env.pop(var, None)
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(drc)], capture_output=True, env=env
    )
    out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    assert proc.returncode == 0, out
    assert "all assertions hold" in out


def test_exit2_golden_unhashable_deck(tmp_path):
    """
    A GOLDEN spec whose 'deck' is a list is a real authoring slip; it raised
    TypeError while building the deck set and reached exit 2 unnamed.
    """
    out = expect(tmp_path, Tree(golden={"a": ["a"]}), 2)
    assert "golden-bad-shape" in out
    assert "unhashable" in out
    assert "internal-error" not in out


def test_exit2_golden_non_string_deck(tmp_path):
    """
    Hashable but not a string: it survives the set that catches the unhashable
    case above and only blows up later inside _fmt()'s sorted(), which used to
    escape to the __main__ guard as an unnamed 'internal-error'.
    """
    out = expect(tmp_path, Tree(golden={"a": 7}), 2)
    assert "golden-bad-shape" in out
    assert "GOLDEN['a']['deck'] is 7 (int)" in out
    assert "internal-error" not in out


def test_exit2_python_available_decks_non_string_entry(tmp_path):
    """An int in AVAILABLE_DECKS: named at the read, never 'internal-error'."""
    drc = Tree().write(tmp_path)
    py = drc / "intm4tm2_decks.py"
    py.write_text(
        py.read_text(encoding="utf-8").replace("    'bb',\n)", "    7,\n)"),
        encoding="utf-8",
    )
    assert "    7,\n)" in py.read_text(encoding="utf-8")
    rc, out = run_checker(drc)
    assert rc == 2, out
    assert "python-constant-mis-shaped" in out
    assert "AVAILABLE_DECKS contains 7 (int)" in out
    assert "internal-error" not in out


def test_exit2_python_skip_set_non_string_entry(tmp_path):
    drc = Tree().write(tmp_path)
    py = drc / "intm4tm2_decks.py"
    py.write_text(
        py.read_text(encoding="utf-8").replace("frozenset({'bb'})", "frozenset({7})"),
        encoding="utf-8",
    )
    rc, out = run_checker(drc)
    assert rc == 2, out
    assert "python-constant-mis-shaped" in out
    assert "DEFAULT_SKIP_DECKS contains 7 (int)" in out
    assert "internal-error" not in out


def test_exit2_python_relocated_non_string_key(tmp_path):
    drc = Tree().write(tmp_path)
    py = drc / "intm4tm2_decks.py"
    py.write_text(
        py.read_text(encoding="utf-8").replace("    'assembly':", "    7:"),
        encoding="utf-8",
    )
    rc, out = run_checker(drc)
    assert rc == 2, out
    assert "python-constant-mis-shaped" in out
    assert "RELOCATED_DECKS keys contains 7 (int)" in out
    assert "internal-error" not in out


def test_exit2_known_untested_non_string_entry(tmp_path):
    """
    Same class as the GOLDEN deck value: KNOWN_UNTESTED_DECKS is read into the
    same `covered` set that _fmt() sorts, so a non-string entry there had the
    same unnamed-exit-2 path.
    """
    drc = Tree().write(tmp_path)
    rr = drc / "testing" / "run_regression.py"
    rr.write_text(
        rr.read_text(encoding="utf-8").replace(
            "KNOWN_UNTESTED_DECKS = frozenset({'bb'})",
            "KNOWN_UNTESTED_DECKS = frozenset({7})",
        ),
        encoding="utf-8",
    )
    rc, out = run_checker(drc)
    assert rc == 2, out
    assert "golden-bad-shape" in out
    assert "KNOWN_UNTESTED_DECKS contains 7 (int)" in out
    assert "internal-error" not in out


def test_exit2_python_module_absent(tmp_path):
    drc = Tree().write(tmp_path)
    (drc / "intm4tm2_decks.py").unlink()
    rc, out = run_checker(drc)
    assert rc == 2
    assert "python-module-unloadable" in out


def test_exit2_python_constant_missing(tmp_path):
    drc = Tree().write(tmp_path)
    py = drc / "intm4tm2_decks.py"
    py.write_text(
        py.read_text(encoding="utf-8").replace("RELOCATED_DECKS = {", "_RELOCATED = {"),
        encoding="utf-8",
    )
    rc, out = run_checker(drc)
    assert rc == 2
    assert "python-constant-missing" in out
    assert "RELOCATED_DECKS" in out


def test_exit2_golden_missing(tmp_path):
    drc = Tree().write(tmp_path)
    rr = drc / "testing" / "run_regression.py"
    rr.write_text(
        rr.read_text(encoding="utf-8").replace("GOLDEN = {", "_GOLDEN = {"), encoding="utf-8"
    )
    rc, out = run_checker(drc)
    assert rc == 2
    assert "golden-constant-missing" in out
    assert "GOLDEN" in out


def test_exit2_known_untested_missing(tmp_path):
    drc = Tree().write(tmp_path)
    rr = drc / "testing" / "run_regression.py"
    rr.write_text(
        rr.read_text(encoding="utf-8").replace("KNOWN_UNTESTED_DECKS = ", "_UNTESTED = "),
        encoding="utf-8",
    )
    rc, out = run_checker(drc)
    assert rc == 2
    assert "golden-constant-missing" in out
    assert "KNOWN_UNTESTED_DECKS" in out


def test_exit2_run_regression_unimportable(tmp_path):
    drc = Tree().write(tmp_path)
    (drc / "testing" / "run_regression.py").write_text("import klayout.db as db\n", encoding="utf-8")
    rc, out = run_checker(drc)
    assert rc == 2
    assert "run-regression-unimportable" in out or "golden-constant-missing" in out


def test_exit2_run_drc_unparseable(tmp_path):
    drc = Tree().write(tmp_path)
    (drc / "run_drc.py").write_text("def broken(:\n", encoding="utf-8")
    rc, out = run_checker(drc)
    assert rc == 2
    assert "run-drc-unparseable" in out


def test_exit2_main_drc_absent(tmp_path):
    drc = Tree().write(tmp_path)
    (drc / "intm4tm2.drc").unlink()
    rc, out = run_checker(drc)
    assert rc == 2
    assert "main-drc-absent" in out


def test_exit2_rule_decks_absent(tmp_path):
    drc = Tree().write(tmp_path)
    shutil.rmtree(drc / "rule_decks")
    rc, out = run_checker(drc)
    assert rc == 2
    assert "rule-decks-absent" in out


def test_exit2_drc_dir_absent(tmp_path):
    rc, out = run_checker(tmp_path / "nope")
    assert rc == 2
    assert "drc-dir-absent" in out


def test_checker_writes_nothing_into_the_tree_it_inspects(tmp_path):
    """Importing the producer modules must not drop __pycache__ in the repo."""
    drc = Tree().write(tmp_path)
    before = sorted(p.relative_to(drc).as_posix() for p in drc.rglob("*"))
    rc, out = run_checker(drc)
    assert rc == 0, out
    after = sorted(p.relative_to(drc).as_posix() for p in drc.rglob("*"))
    assert after == before, f"checker created {sorted(set(after) - set(before))}"


def test_all_failures_collected_not_first_fail(tmp_path):
    """Assertion failures accumulate; the checker does not stop at the first."""
    tree = Tree(py_skip=["a"], disk=["a.drc", "b.drc", "layers_def.drc", "orphan.drc"],
                untested=[])
    out = expect(tmp_path, tree, 1)
    for needle in ("[ASSERT-3]", "[ASSERT-4]", "[ASSERT-6]"):
        assert needle in out, out


# ---------------------------------------------------------------------------
# the contract-faithful fixture: the real post-merge emitted API
# ---------------------------------------------------------------------------

# 21 name => file pairs, verbatim from intm4tm2.drc's all_decks hash.
REAL_DECKS = [
    ("offgrid", "3_1_offgrid.drc"),
    ("angle", "3_2_angle.drc"),
    ("metaln", "5_17_metaln.drc"),
    ("metalnfiller", "5_18_metalnfiller.drc"),
    ("via4", "5_20_via4.drc"),
    ("topvia1", "5_21_topvia1.drc"),
    ("topmetal1", "5_22_topmetal1.drc"),
    ("topmetal1filler", "5_23_topmetal1filler.drc"),
    ("topvia2", "5_24_topvia2.drc"),
    ("topmetal2", "5_25_topmetal2.drc"),
    ("topmetal2filler", "5_26_topmetal2filler.drc"),
    ("passiv", "5_27_passiv.drc"),
    ("pad", "6_9_pad.drc"),
    ("copperpillar", "6_9_copperpillar.drc"),
    ("solderbump", "6_9_solderbump.drc"),
    ("ubm_floor", "6_9_ubm_floor.drc"),
    ("sealring", "6_10_sealring.drc"),
    ("mim", "6_11_mim.drc"),
    ("metalslits", "7_3_metalslits.drc"),
    ("lbe", "9_1_lbe.drc"),
    ("density", "density.drc"),
]

# AVAILABLE_DECKS as run_drc.py carries it today; intm4tm2_decks.py inherits it.
REAL_AVAILABLE = [
    "offgrid", "angle",
    "metaln", "metalnfiller",
    "via4", "topvia1",
    "topmetal1", "topmetal1filler",
    "topvia2", "topmetal2", "topmetal2filler",
    "passiv", "pad", "copperpillar", "solderbump", "ubm_floor",
    "sealring", "mim", "metalslits", "lbe",
    "density",
]

REAL_RELOCATED = {
    "assembly": (
        "Promoted to the ADK. Use adk/klayout/drc/run_drc.py "
        "with --interposer-adapter <name>."
    ),
}

# rule_decks/*.drc as it stands on disk: the 21 deck files plus the preamble.
REAL_DISK = sorted([f for _, f in REAL_DECKS] + ["layers_def.drc"])

# GOLDEN spec['deck'] values in testing/run_regression.py: 17 decks.
REAL_GOLDEN_DECKS = [
    "angle", "copperpillar", "density", "lbe", "metaln", "metalnfiller",
    "metalslits", "mim", "offgrid", "pad", "sealring", "solderbump",
    "ubm_floor", "topmetal1filler", "topmetal2", "topmetal2filler", "via4",
]

REAL_UNTESTED = ["topvia1", "topmetal1", "topvia2", "passiv"]


def _contract_faithful_tree() -> Tree:
    return Tree(
        decks=REAL_DECKS,
        preamble=["layers_def.drc"],
        py_available=REAL_AVAILABLE,
        py_skip=["density"],
        rb_skip=["density"],
        relocated=REAL_RELOCATED,
        disk=REAL_DISK,
        golden={d: d for d in REAL_GOLDEN_DECKS},
        untested=REAL_UNTESTED,
    )


def test_contract_faithful_fixture_is_green(tmp_path):
    """The strongest positive test: the real values, the post-merge shape, exit 0."""
    assert len(REAL_DECKS) == 21
    assert len(REAL_AVAILABLE) == 21
    assert len(REAL_DISK) == 22
    assert len(REAL_GOLDEN_DECKS) == 17
    assert len(REAL_UNTESTED) == 4
    assert set(REAL_AVAILABLE) - set(REAL_GOLDEN_DECKS) == set(REAL_UNTESTED)
    out = expect(tmp_path, _contract_faithful_tree(), 0)
    assert "all assertions hold" in out


def test_contract_faithful_fixture_detects_a_real_drift(tmp_path):
    """Same fixture, one deck added to the ruby side only: caught, not tolerated."""
    tree = replace(
        _contract_faithful_tree(),
        decks=REAL_DECKS + [("newdeck", "9_9_newdeck.drc")],
        disk=sorted(REAL_DISK + ["9_9_newdeck.drc"]),
    )
    out = expect(tmp_path, tree, 1, "[ASSERT-2]")
    assert "newdeck" in out
