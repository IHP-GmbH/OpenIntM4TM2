#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Emission-based deck-registry checker for the IntM4TM2 interposer PDK.

Every operand this checker compares is a value *emitted* by the thing that owns
it, never a value re-parsed out of a large source file:

  * Python truth  -- ``<drc>/intm4tm2_decks.py`` imported for AVAILABLE_DECKS,
    DEFAULT_SKIP_DECKS and RELOCATED_DECKS. That module is klayout-free and
    holds literals only, so importing it is cheap and side-effect free.
  * Ruby truth    -- ``<drc>/intm4tm2_decks.rb`` required by a ``ruby``
    subprocess that prints ALL_DECKS, DEFAULT_SKIP_DECKS, PREAMBLE and BOTH
    branches of the composition function as JSON: BOTH halves of
    ``deck_files(nil)`` -- the file list AND the unknown list -- and FIVE
    requested-branch probes: ``deck_files(req)`` over every registry name,
    ``deck_files(sub)`` over a STRICT subset of them, ``deck_files([])`` over the
    EMPTY request, ``deck_files(cf)`` over an UPCASED real deck name followed by
    its exact-case twin, and ``deck_files(mg)`` over derived-but-ABSENT MANGLINGS
    of real deck names. Every probe request is built inside ruby out of the
    registry itself -- ``ALL_DECKS.keys.reverse`` whole for the first, its first
    two entries for the second, each a scrambled selection of real deck names
    equal to neither the registry order nor to its own reverse, with synthetic
    unknown names interleaved rather than appended -- so they use real names and
    adapt to whatever decks exist, with nothing about the deck set hardcoded here.
    Each probe's request is SNAPSHOTTED before the call and the snapshot, not the
    array handed to ``deck_files``, is what the expectation is computed from, so a
    producer that mutates its argument in place (``reverse!``, ``sort_by!``,
    ``select!``) is judged against the request that was actually made.
    The nil branch is emitted whole because half a return value is half a check:
    the contract literal is ``return [PREAMBLE + selected, []]``, so its unknown
    half is asserted EMPTY. A nil branch that put a name there would make
    intm4tm2.drc's ``unknown_decks.each { |name| logger.warn(...) }`` warn on
    EVERY default run, and no legitimate producer puts a name there, so the
    expectation is a constant the shipped producer already satisfies.
    The second, strict subset probe exists because a full-registry request is the
    least discriminating one for any bug that EXPANDS a selection: a
    ``deck_files`` that auto-adds a prerequisite deck, or falls back to every deck
    when asked for fewer, is invisible when the request already names them all.
    The third asks for NOTHING: ``requested == []`` is a defined input of the
    contract, and the reachable form of it is a SEPARATOR-ONLY ``-rd deck=,``
    (the runset does ``$deck.split(',')`` and ``",".split(",")`` is ``[]``), not a
    bare ``-rd deck=``, which the runset's ``!$deck.empty?`` guard short-circuits
    to ``nil``. It means exactly zero decks -- ``[PREAMBLE, []]`` -- which is a
    different contract from ``requested == nil``, the default all-decks run; a
    producer that falls back to the full run on an empty list agrees with every
    non-empty probe. The fourth interleaves ``k0.upcase`` ahead of ``k0``: name
    matching is EXACT, so the upcased twin must come back UNKNOWN while its
    exact-case twin maps to its file, and a producer that case-folds an explicit
    deck name (``ALL_DECKS.key?(name.downcase)``) instead maps both to the same
    file and reports no unknown at all. The fifth requests names DERIVED from real
    ones and verified absent from the registry -- a prefix truncation
    (``k[0..-2]``), a suffix truncation (``k[1..-1]``) and, where a name carries
    one, a separator-stripped variant (``ubm_floor`` -> ``ubmfloor``) -- each
    expected UNKNOWN: in this registry every exact key precedes its own
    extensions, so no probe made of registry names alone constructs a strict
    partial, and a producer matching a PREFIX/SUFFIX/SEPARATOR-normalised form
    (``ALL_DECKS.keys.any? { |k| k.start_with?(name) }``, where ``-rd deck=top``
    silently runs topvia1) is otherwise green. Every probe's expectation is
    computed from the emitted registry, so none of them can false-fail a faithful
    producer.
    Because the composition *function* is emitted rather than re-derived here,
    and both of its branches are validated whole against the emitted registry, a
    ``deck_files`` that drops a file, reorders the requested selection (in either
    the non-destructive or the in-place form), maps a name to the wrong file,
    wrongly applies DEFAULT_SKIP to an explicit request, expands a partial
    request, falls back to the full run on an empty list, case-folds an explicit
    deck name, matches a name leniently instead of exactly (a prefix, a suffix or
    a separator-stripped form), reports a bogus unknown name from the DEFAULT
    run, or mis-classifies OR drops an unknown name (the second and later unknown
    names included) is caught.
    Known residual classes (NOT an exhaustive list): a REPEATED name
    (contract-ambiguous -- collapsing ``-rd deck=pad,pad`` to one deck is arguably
    hardening rather than a defect, so it is deliberately not probed and a
    ``.uniq`` producer stays green here); the ORDER of the unknown/warning list
    (cosmetic rather than contractual -- the runset consumes it only as a per-name
    ``logger.warn`` loop, so the unknown half is compared as a MULTISET and a
    producer that sorts or reverses its warnings stays green); behaviour
    conditional on a request LENGTH or composition that the finite probe set never
    makes (no finite set of probes closes that statically); UNICODE case-folding
    on non-ASCII input (unreachable here -- every real deck name is ASCII and the
    probes cannot construct a non-ASCII collision); a NON-STRING element (refused
    by the shape validator, so it is not part of the request model at all); and
    anything reachable only by EXECUTING the runset or run_drc.py, which import
    klayout and are outside this model -- whether the composed file list is really
    the one concatenated into the ``eval()``, and the ASSERT-8a
    --help/diagnostic-read residual described below.
  * Golden truth  -- ``<drc>/testing/run_regression.py`` imported for GOLDEN and
    KNOWN_UNTESTED_DECKS. That module is klayout-free at import time (it moves
    ``import klayout.db`` inside ``run_table``), and it imports this checker only
    inside its own ``main()``, so there is no import cycle.
  * Disk truth    -- the actual ``*.drc`` files in ``<drc>/rule_decks/``.

There is exactly one guard that is not emission-based, ASSERT-8, and it exists
because nothing else can speak about the *producers* at all: they cannot be run
here (run_drc.py imports klayout, intm4tm2.drc is a KLayout runset). It is
text/AST only and never executes the producer files. Its two halves are:

  * python -- exactly what it proves, no more: the three registry names are
    (a) bound by the single authorized, ABSOLUTE
    ``from intm4tm2_decks import ...``, (b) not rebound by any other binding
    form anywhere in run_drc.py, and (c) each read at least once, i.e. each
    appears somewhere in run_drc.py's AST as an ``ast.Name`` in Load context.
    (c) closes the "imported but never consumed" gap that (a)+(b) alone leave
    open: a run_drc.py could keep the import, rebind nothing, and still drive
    the run off a private curated deck list.
    RESIDUAL, out of this model: a run_drc.py that reads a name only on a
    ``--help``/diagnostic path while a private curated list actually drives the
    run still passes. Proving that the read is the one feeding the run needs
    dataflow through argument parsing, i.e. executing run_drc.py, which imports
    klayout and is outside the emission model. (c) narrows the gap; it does not
    close it.
  * ruby   -- intm4tm2.drc requires the module, keeps no ``all_decks = {`` hash
    of its own, and composes through ``IntM4TM2Decks.deck_files(``. That last
    one is exclusive on purpose: assert 5 validates what deck_files() emits on
    BOTH of its branches, so a runset that merely aliased ALL_DECKS and composed
    inline would leave assert 5 validating a function nothing runs.

Exit codes:
  0  every assertion held
  1  one or more assertions failed (all of them are collected and printed)
  2  no verdict was possible (see the named reason on stderr)
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

# The two names in the requested-branch probes that must NOT be decks. Kept
# obviously synthetic; if a registry ever really carries one the probes would be
# testing nothing, which is why the shape validator refuses to give a verdict
# in that case rather than reporting a green. One guard covers every probe that
# uses them: they share these names.
#
# There are TWO of them, and the full-registry probe interleaves BOTH, because a
# single unknown name cannot see a producer that keeps only the FIRST unknown it
# finds (`requested.reject { ... }.first(1)`): `-rd deck=typo1,typo2` would warn
# about one bad name and silently drop the other. They are distinct, so no probe
# repeats a name and the deliberately-unprobed dedup question (see the residual
# list in the module docstring) is not raised here.
PROBE_UNKNOWN_NAME = "__deck_check_bogus_zzz__"
PROBE_UNKNOWN_NAME2 = "__deck_check_bogus_two__"

# Emits both branches of the composition function WHOLE (the nil branch's unknown
# half included), the requested branch FIVE times. Every requested-branch probe is
# built inside ruby from the registry, so they always use real deck names and
# adapt to whatever decks exist:
#   ks   = ALL_DECKS.keys.reverse  -- a scrambled selection of every real name,
#          equal to neither the registry order nor to its own reverse, so a
#          `.reverse` bug and a "re-sort to registry order" bug both diverge.
#   req  = ks with TWO distinct synthetic unknowns interleaved, after the first
#          and after the third entry (not appended: a tail-only unknown would not
#          show an order slip in the unknown/known split). The FULL-registry
#          probe. Two rather than one because a producer that keeps only the
#          first unknown name it finds (`.first(1)`) agrees with every
#          single-unknown probe while `-rd deck=typo1,typo2` silently drops a
#          deck; with two interleaved the unknown half differs in LENGTH, which
#          the multiset comparison in assert 5 sees.
#   sub  = the first two entries of ks, same unknown interleaved -- a STRICT,
#          non-empty subset of the registry. The full probe names every deck, so
#          it is the least discriminating request there is for a bug that
#          EXPANDS a selection (auto-adding a prerequisite deck, or falling back
#          to ALL_DECKS when asked for fewer); against a strict subset such a
#          producer emits more files than the request asked for and diverges.
#   emp  = [] -- the EMPTY request. `requested == []` is a defined contract input,
#          not a synonym for `nil`: it names exactly zero decks, so the answer is
#          [PREAMBLE, []], while `nil` is the default all-decks run. A user really
#          can reach it, with a SEPARATOR-ONLY `-rd deck=,`: the runset splits on
#          ',' and `",".split(",")` is []. (A bare `-rd deck=` does NOT reach it;
#          `!$deck.empty?` short-circuits that to nil.) A
#          `return deck_files(nil) if requested.empty?` producer
#          silently promotes "no decks" into "every deck" and agrees with all
#          three non-empty probes; only this one sees it. No synthetic unknown is
#          interleaved here -- the empty list itself is the whole point.
#   cf   = [k0.upcase, k0] -- the CASE-FOLD probe, k0 being the first registry
#          name whose upcased form is both different from it and absent from the
#          registry. Name matching is EXACT (`-rd deck=PAD` is not `-rd deck=pad`),
#          so the checker, deriving "known" from the emitted exact-match ALL_DECKS,
#          expects k0.upcase in the unknown half and k0 mapped to its file. A
#          producer that case-folds (`ALL_DECKS.key?(name.downcase)`) instead maps
#          both entries to k0's file and reports nothing unknown, so it diverges
#          on both halves. Every real deck name is lowercase, so no other probe
#          can construct this input. `find` rather than `keys.first` so a registry
#          whose first name happens to be caseless falls back to a later name that
#          does discriminate; when no name at all does, `cf` is empty and
#          "casefold_meta" is empty, which the shape validator turns into a named
#          probe-degenerate exit 2 rather than a vacuous green.
#   mgq  = the MANGLED-NAME probe: names derived from real deck names, each
#          verified ABSENT from ALL_DECKS before use, interleaved with real deck
#          names. The forms are the realistic drift ones: a PREFIX truncation
#          (k[0..-2]), a SUFFIX truncation (k[1..-1]) and, when a name carries an
#          '_', a SEPARATOR-stripped variant (`ubm_floor` -> `ubmfloor`). Name
#          matching is EXACT in both producers (`ALL_DECKS.key?` here,
#          `d not in AVAILABLE_DECKS` in run_drc.py), but in this registry every
#          exact key precedes its own extensions, so no probe made of registry
#          names alone ever constructs a strict partial and a producer matching a
#          normalised form -- `ALL_DECKS.keys.any? { |k| k.start_with?(name) }`,
#          under which `-rd deck=top` silently runs topvia1 with no warning --
#          stays green. "known" is derived from the emitted EXACT-match
#          ALL_DECKS, so every mangling is expected UNKNOWN: the shipped producer
#          passes and a lenient one diverges on both halves. `find` per form and
#          `.uniq` over the result, so a registry that cannot express one form
#          (no '_' anywhere, say) still emits the others and no name is repeated;
#          when NO form discriminates -- a registry of one-character,
#          separator-free names -- `mgq` and "mangled_meta" are both empty, which
#          the shape validator turns into a named probe-degenerate exit 2 rather
#          than a vacuous green. (Unicode case-folding on non-ASCII input is a
#          named residual, not a probe: every real deck name is ASCII, so the
#          input that would discriminate it cannot be constructed here.)
# Each request is snapshotted (`snap`, `snap2`, `snap3`, `snap4`, `snap5`) BEFORE its
# deck_files call and the snapshot is what gets emitted as "input". The arrays
# themselves are handed to the producer, so an in-place mutation of the argument
# (`requested.reverse!`, `sort_by!`, `select!`) would otherwise rewrite the very
# expectation it is being judged against: a destructive reorder would hide at exit
# 0, and a correct but destructive filter would be reported as a false failure.
# The snapshot is the request that was actually made; it cannot be edited by the
# callee. (Freezing the argument instead would be wrong: it turns the
# correct-but-destructive producer into a ruby exception, i.e. exit 2, rather than
# the green it deserves.)
# [1, ks.length].min, [3, req.length].min and [1, sub.length].min are ordinary
# interior positions for every registry of a useful size; they only keep an empty
# or one-deck ALL_DECKS from making Ruby's insert() pad with a nil.
# ks.first([2, ks.length].min) likewise degrades to whatever exists, so a 0- or
# 1-deck registry still emits a well-formed second probe instead of crashing, and
# an empty registry simply yields an empty `cf` and an empty `mgq`.
RUBY_EMITTER = (
    'require ARGV[0]; '
    'ks = IntM4TM2Decks::ALL_DECKS.keys.reverse; '
    'req = ks.dup; '
    f'req.insert([1, ks.length].min, {PROBE_UNKNOWN_NAME!r}); '
    f'req.insert([3, req.length].min, {PROBE_UNKNOWN_NAME2!r}); '
    'snap = req.dup; '
    'sub = ks.first([2, ks.length].min); '
    f'sub.insert([1, sub.length].min, {PROBE_UNKNOWN_NAME!r}); '
    'snap2 = sub.dup; '
    'emp = []; '
    'snap3 = emp.dup; '
    'k0 = IntM4TM2Decks::ALL_DECKS.keys.find { |k| k.upcase != k && '
    '!IntM4TM2Decks::ALL_DECKS.key?(k.upcase) }; '
    'mt = k0.nil? ? [] : [k0, k0.upcase]; '
    'cf = k0.nil? ? [] : [k0.upcase, k0]; '
    'snap4 = cf.dup; '
    'mg = []; '
    'kpre = IntM4TM2Decks::ALL_DECKS.keys.find { |k| k.length > 1 && '
    '!IntM4TM2Decks::ALL_DECKS.key?(k[0..-2]) }; '
    'mg << kpre[0..-2] unless kpre.nil?; '
    'ksuf = IntM4TM2Decks::ALL_DECKS.keys.find { |k| k.length > 1 && '
    '!IntM4TM2Decks::ALL_DECKS.key?(k[1..-1]) }; '
    'mg << ksuf[1..-1] unless ksuf.nil?; '
    'ksep = IntM4TM2Decks::ALL_DECKS.keys.find { |k| k.include?(\'_\') && '
    'k.delete(\'_\').length > 0 && '
    '!IntM4TM2Decks::ALL_DECKS.key?(k.delete(\'_\')) }; '
    'mg << ksep.delete(\'_\') unless ksep.nil?; '
    'mg = mg.uniq; '
    'mgq = []; '
    'mg.each_with_index { |m, i| mgq << m; '
    'kk = IntM4TM2Decks::ALL_DECKS.keys[i]; mgq << kk unless kk.nil? }; '
    'snap5 = mgq.dup; '
    'nf, nu = IntM4TM2Decks.deck_files(nil); '
    'rf, ru = IntM4TM2Decks.deck_files(req); '
    'sf, su = IntM4TM2Decks.deck_files(sub); '
    'ef, eu = IntM4TM2Decks.deck_files(emp); '
    'cf_f, cf_u = IntM4TM2Decks.deck_files(cf); '
    'mf, mu = IntM4TM2Decks.deck_files(mgq); '
    'puts({"decks"=>IntM4TM2Decks::ALL_DECKS,'
    '"skip"=>IntM4TM2Decks::DEFAULT_SKIP_DECKS,'
    '"preamble"=>IntM4TM2Decks::PREAMBLE,'
    '"all_files"=>nf,'
    '"all_unknown"=>nu,'
    '"casefold_meta"=>mt,'
    '"mangled_meta"=>mg,'
    '"requested"=>{"input"=>snap,"files"=>rf,"unknown"=>ru},'
    '"requested_subset"=>{"input"=>snap2,"files"=>sf,"unknown"=>su},'
    '"requested_empty"=>{"input"=>snap3,"files"=>ef,"unknown"=>eu},'
    '"requested_casefold"=>{"input"=>snap4,"files"=>cf_f,"unknown"=>cf_u},'
    '"requested_mangled"=>{"input"=>snap5,"files"=>mf,"unknown"=>mu}}.to_json)'
)

# The five emitted requested-branch probes: JSON key -> the label used when a
# failure names which request was made. Every one of them goes through the SAME
# mirror assertion in check_all(); none has an expectation of its own.
REQUESTED_PROBES = (
    ("requested", "<explicit request>"),
    ("requested_subset", "<explicit subset request>"),
    ("requested_empty", "<explicit empty request>"),
    ("requested_casefold", "<explicit case-folding request>"),
    ("requested_mangled", "<explicit mangled-name request>"),
)

PRODUCER_NAMES = ("AVAILABLE_DECKS", "DEFAULT_SKIP_DECKS", "RELOCATED_DECKS")


class NoVerdict(Exception):
    """Cannot enumerate the registry; the run has no verdict at all (exit 2)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# truth sources
# ---------------------------------------------------------------------------


def _import_module(path: Path, mod_name: str, reason: str):
    if not path.is_file():
        raise NoVerdict(reason, f"{path} is absent")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise NoVerdict(reason, f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    # A checker must not write into the tree it inspects. Without this, importing
    # the producer modules drops __pycache__/*.pyc next to them.
    previously = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - any import failure is exit 2
        sys.modules.pop(mod_name, None)
        raise NoVerdict(reason, f"{path} failed to import: {exc!r}") from exc
    finally:
        sys.dont_write_bytecode = previously
    return module


def _require_attr(module, attr: str, reason: str, where: Path):
    if not hasattr(module, attr):
        raise NoVerdict(reason, f"{where} does not define {attr}")
    return getattr(module, attr)


def load_python_truth(decks_py: Path) -> dict:
    mod = _import_module(decks_py, "_intm4tm2_decks_truth", "python-module-unloadable")
    reason = "python-constant-missing"
    available = _require_attr(mod, "AVAILABLE_DECKS", reason, decks_py)
    skip = _require_attr(mod, "DEFAULT_SKIP_DECKS", reason, decks_py)
    relocated = _require_attr(mod, "RELOCATED_DECKS", reason, decks_py)
    try:
        available = list(available)
        skip = set(skip)
        relocated_keys = set(relocated.keys())
    except (TypeError, AttributeError) as exc:
        raise NoVerdict(
            "python-constant-mis-shaped",
            f"{decks_py}: AVAILABLE_DECKS/DEFAULT_SKIP_DECKS must be iterable and "
            f"RELOCATED_DECKS a mapping ({exc!r})",
        ) from exc
    # Element types, checked here at the read: every downstream operand is a deck
    # NAME, and a non-string one only surfaces much later inside _fmt()'s
    # sorted() as a bare TypeError, which would escape as an unnamed exit 2.
    for label, entries in (
        ("AVAILABLE_DECKS", available),
        ("DEFAULT_SKIP_DECKS", sorted(skip, key=repr)),
        ("RELOCATED_DECKS keys", sorted(relocated_keys, key=repr)),
    ):
        for entry in entries:
            if not isinstance(entry, str):
                raise NoVerdict(
                    "python-constant-mis-shaped",
                    f"{decks_py}: {label} contains {entry!r} "
                    f"({type(entry).__name__}); a deck name must be a string",
                )
    return {"available": available, "skip": skip, "relocated": relocated_keys}


def load_ruby_truth(decks_rb: Path) -> dict:
    if not decks_rb.is_file():
        raise NoVerdict("ruby-module-absent", f"{decks_rb} is absent")
    cmd = ["ruby", "-r", "json", "-e", RUBY_EMITTER, "--", str(decks_rb.resolve())]
    try:
        # text=False on purpose: the emitter's bytes are decoded here as UTF-8,
        # not by the process locale. Under LC_ALL=C a text=True run would raise
        # UnicodeDecodeError out of subprocess itself and the failure would have
        # no named reason of its own.
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError as exc:
        raise NoVerdict("ruby-absent", "no `ruby` on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise NoVerdict("ruby-timeout", "the ruby emitter did not finish") from exc
    # stderr is only ever quoted back at a human: never let decoding it raise.
    stderr_text = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise NoVerdict(
            "ruby-exit-nonzero",
            f"ruby exited {proc.returncode}: {stderr_text.strip()[:800]}",
        )
    try:
        stdout_text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NoVerdict(
            "ruby-bad-encoding",
            f"the ruby emitter wrote bytes that are not UTF-8 ({exc}); "
            f"first bytes {proc.stdout[:120]!r}",
        ) from exc
    try:
        raw = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise NoVerdict(
            "ruby-bad-json", f"emitter stdout is not JSON ({exc}); got {stdout_text[:400]!r}"
        ) from exc
    return _validate_ruby_shape(raw)


def _validate_ruby_shape(raw) -> dict:
    bad = lambda detail: NoVerdict("ruby-bad-shape", detail)  # noqa: E731
    if not isinstance(raw, dict):
        raise bad(f"top level is {type(raw).__name__}, expected an object")
    for key in (
        "decks",
        "skip",
        "preamble",
        "all_files",
        "all_unknown",
        "casefold_meta",
        "mangled_meta",
    ) + tuple(k for k, _ in REQUESTED_PROBES):
        if key not in raw:
            raise bad(f"key {key!r} missing from the emitted object")
    decks = raw["decks"]
    if not isinstance(decks, dict):
        raise bad(f"'decks' is {type(decks).__name__}, expected an object")
    for name, file_ in decks.items():
        if not isinstance(name, str) or not isinstance(file_, str):
            raise bad(f"'decks' entry {name!r} => {file_!r} is not str => str")
    lists = {}
    for key in (
        "skip",
        "preamble",
        "all_files",
        "all_unknown",
        "casefold_meta",
        "mangled_meta",
    ):
        value = raw[key]
        if not isinstance(value, list):
            raise bad(f"{key!r} is {type(value).__name__}, expected an array")
        for item in value:
            if not isinstance(item, str):
                raise bad(f"{key!r} contains {item!r}, expected only strings")
        lists[key] = list(value)
    # Each requested-branch probe: {"input": [...], "files": [...], "unknown": [...]},
    # every member an array of strings, same style as the top-level lists above.
    probes = {}
    for probe_key, _label in REQUESTED_PROBES:
        requested = raw[probe_key]
        if not isinstance(requested, dict):
            raise bad(
                f"{probe_key!r} is {type(requested).__name__}, expected an object"
            )
        probe = {}
        for key in ("input", "files", "unknown"):
            if key not in requested:
                raise bad(f"key '{probe_key}.{key}' missing from the emitted object")
            value = requested[key]
            if not isinstance(value, list):
                raise bad(
                    f"'{probe_key}.{key}' is {type(value).__name__}, expected an array"
                )
            for item in value:
                if not isinstance(item, str):
                    raise bad(
                        f"'{probe_key}.{key}' contains {item!r}, expected only strings"
                    )
            probe[key] = list(value)
        probes[probe_key] = probe
    # The probes only prove anything while their synthetic names really are
    # unknown. A registry that carries one would make the unknown half vacuous, so
    # there is no verdict at all here; it is never reported as a green. The probes
    # share these two names, so this single guard covers all of them.
    for synthetic in (PROBE_UNKNOWN_NAME, PROBE_UNKNOWN_NAME2):
        if synthetic in decks:
            raise NoVerdict(
                "probe-degenerate",
                f"ALL_DECKS carries {synthetic!r}, one of the names the "
                "requested-branch probes use as a guaranteed-unknown deck; the "
                "unknown half of the probes would test nothing",
            )
    # ...and the full probe only sees a producer that keeps just the FIRST unknown
    # name while it really requests BOTH synthetic names. An emitter drift that
    # dropped one would silently reopen that gap.
    full_input = probes["requested"]["input"]
    missing_synthetic = [
        n for n in (PROBE_UNKNOWN_NAME, PROBE_UNKNOWN_NAME2) if n not in full_input
    ]
    if missing_synthetic:
        raise NoVerdict(
            "probe-degenerate",
            f"the full-registry probe requested {full_input!r}, which is missing the "
            f"synthetic unknown name(s) {missing_synthetic!r}; with fewer than two "
            "unknown names in one request a producer that keeps only the first "
            "unknown it finds is not exercised",
        )
    # The empty-request probe only means anything while the request really is
    # empty: an emitter drift that put a name in it would leave `requested == []`
    # unexercised again, which is the very gap the probe exists to close.
    if probes["requested_empty"]["input"]:
        raise NoVerdict(
            "probe-degenerate",
            "the empty-request probe did not carry an empty request; it emitted "
            f"{probes['requested_empty']['input']!r}, so `deck_files([])` is not "
            "exercised at all",
        )
    # ...and the case-fold probe only means anything while its upcased name is a
    # name the registry does NOT carry and really differs from its exact-case twin.
    # "casefold_meta" is [k0, k0.upcase], or [] when the emitter found no name that
    # discriminates. An empty registry has nothing to upcase and is vacuous for
    # every probe anyway; a NON-empty registry in which no name discriminates (all
    # of them caseless or upcased-colliding) would make this probe prove nothing,
    # so it is a named no-verdict, never a green.
    meta = lists["casefold_meta"]
    cf_input = probes["requested_casefold"]["input"]
    if decks and not meta:
        raise NoVerdict(
            "probe-degenerate",
            "no deck name upcases to a name the registry does not already carry, so "
            "the case-folding probe cannot discriminate an exact-match producer from "
            f"a downcasing one; ALL_DECKS names are {sorted(decks)!r}",
        )
    if meta:
        if len(meta) != 2:
            raise bad(
                f"'casefold_meta' is {meta!r}, expected [name, name.upcase] or []"
            )
        k0, upcased = meta
        if k0 not in decks or upcased == k0 or upcased in decks:
            raise NoVerdict(
                "probe-degenerate",
                f"the case-folding probe pair {meta!r} does not discriminate: its "
                "first name must be a real deck and its upcased form must differ "
                "from it and be absent from ALL_DECKS",
            )
        if cf_input != [upcased, k0]:
            raise NoVerdict(
                "probe-degenerate",
                f"the case-folding probe emitted {cf_input!r}, not the announced "
                f"{[upcased, k0]!r}; the upcased name is not being requested",
            )
    elif cf_input:
        raise NoVerdict(
            "probe-degenerate",
            f"'casefold_meta' is empty but the case-folding probe emitted {cf_input!r}",
        )
    # ...and the mangled-name probe only means anything while every name it
    # requests is a MANGLING: derived from a real deck name (a prefix truncation,
    # a suffix truncation or a separator-stripped form) and absent from the
    # registry, so an exact-match producer must call it unknown while a
    # prefix/suffix/separator-lenient one resolves it to a real deck.
    # "mangled_meta" is that list, or [] when the registry is too small to have
    # one (every name a single character, no separator anywhere). An empty
    # registry is vacuous for every probe anyway; a NON-empty registry that yields
    # no mangling cannot discriminate lenient matching from exact matching at all,
    # so it is a named no-verdict, never a green.
    mangled = lists["mangled_meta"]
    mg_input = probes["requested_mangled"]["input"]
    if decks and not mangled:
        raise NoVerdict(
            "probe-degenerate",
            "no deck name yields a truncated or separator-stripped form that the "
            "registry does not already carry, so the mangled-name probe cannot "
            "discriminate an exact-match producer from a prefix/suffix/separator "
            f"matching one; ALL_DECKS names are {sorted(decks)!r}",
        )
    for name in mangled:
        if not name or name in decks:
            raise NoVerdict(
                "probe-degenerate",
                f"the mangled-name probe announced {name!r}, which is empty or is a "
                "real deck; every mangling must be a non-empty name ALL_DECKS does "
                "NOT carry, otherwise it is expected unknown while being known",
            )
        derived = any(
            k != name
            and (k.startswith(name) or k.endswith(name) or k.replace("_", "") == name)
            for k in decks
        )
        if not derived:
            raise NoVerdict(
                "probe-degenerate",
                f"the mangled-name probe announced {name!r}, which is not a prefix "
                "truncation, a suffix truncation or a separator-stripped form of any "
                "deck name; a name no lenient matcher could resolve discriminates "
                "nothing",
            )
        if name not in mg_input:
            raise NoVerdict(
                "probe-degenerate",
                f"the mangled-name probe announced {name!r} but requested "
                f"{mg_input!r}; the mangled name is not being requested",
            )
    if not mangled and mg_input:
        raise NoVerdict(
            "probe-degenerate",
            f"'mangled_meta' is empty but the mangled-name probe emitted {mg_input!r}",
        )
    # dict preserves the emitter's insertion order, which is the .rb literal order
    return {"decks": dict(decks), **lists, **probes}


def load_golden_truth(run_regression: Path) -> dict:
    mod = _import_module(
        run_regression, "_intm4tm2_run_regression_truth", "run-regression-unimportable"
    )
    reason = "golden-constant-missing"
    golden = _require_attr(mod, "GOLDEN", reason, run_regression)
    untested = _require_attr(mod, "KNOWN_UNTESTED_DECKS", reason, run_regression)
    if not isinstance(golden, dict):
        raise NoVerdict("golden-mis-shaped", "GOLDEN is not a dict of table => spec")
    decks = set()
    for table, spec in golden.items():
        if not isinstance(spec, dict) or "deck" not in spec:
            raise NoVerdict(
                "golden-mis-shaped", f"GOLDEN[{table!r}] has no 'deck' key"
            )
        try:
            decks.add(spec["deck"])
        except TypeError as exc:
            raise NoVerdict(
                "golden-bad-shape",
                f"GOLDEN[{table!r}]['deck'] is unhashable "
                f"({type(spec['deck']).__name__}); a deck name must be a string ({exc!r})",
            ) from exc
        # Hashable but not a string (an int, say) survives the set, then blows up
        # inside _fmt()'s sorted() as an unnamed exit 2. Name it here instead.
        if not isinstance(spec["deck"], str):
            raise NoVerdict(
                "golden-bad-shape",
                f"GOLDEN[{table!r}]['deck'] is {spec['deck']!r} "
                f"({type(spec['deck']).__name__}); a deck name must be a string",
            )
    try:
        untested_set = set(untested)
    except TypeError as exc:
        raise NoVerdict(
            "golden-mis-shaped", f"KNOWN_UNTESTED_DECKS is not iterable ({exc!r})"
        ) from exc
    for entry in sorted(untested_set, key=repr):
        if not isinstance(entry, str):
            raise NoVerdict(
                "golden-bad-shape",
                f"KNOWN_UNTESTED_DECKS contains {entry!r} "
                f"({type(entry).__name__}); a deck name must be a string",
            )
    return {"golden_decks": decks, "untested": untested_set}


def load_disk_truth(rule_decks: Path) -> list:
    if not rule_decks.is_dir():
        raise NoVerdict("rule-decks-absent", f"{rule_decks} is not a directory")
    return sorted(p.name for p in rule_decks.glob("*.drc") if p.is_file())


def parse_python_source(run_drc: Path) -> ast.Module:
    if not run_drc.is_file():
        raise NoVerdict("run-drc-absent", f"{run_drc} is absent")
    try:
        # ast.parse does NOT execute: klayout.db is never imported by this.
        return ast.parse(run_drc.read_text(encoding="utf-8"), filename=str(run_drc))
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        raise NoVerdict("run-drc-unparseable", f"{run_drc}: {exc!r}") from exc


def read_drc_text(main_drc: Path) -> str:
    if not main_drc.is_file():
        raise NoVerdict("main-drc-absent", f"{main_drc} is absent")
    try:
        return main_drc.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NoVerdict("main-drc-unreadable", f"{main_drc}: {exc!r}") from exc


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------


def _fmt(names) -> str:
    return ", ".join(sorted(names)) if names else "(none)"


def _target_names(t):
    """Every Name an assignment-like target binds, tuple/list/starred unpacked."""
    if isinstance(t, ast.Name):
        yield t.id
    elif isinstance(t, (ast.Tuple, ast.List)):
        for elt in t.elts:
            yield from _target_names(elt)
    elif isinstance(t, ast.Starred):
        yield from _target_names(t.value)
    # ast.Attribute / ast.Subscript targets bind an attribute or an item, never
    # the module-level name itself, and are deliberately out of model.


def _arg_names(args: ast.arguments):
    for a in (
        list(args.posonlyargs)
        + list(args.args)
        + list(args.kwonlyargs)
        + [args.vararg, args.kwarg]
    ):
        if a is not None:
            yield a.arg


def _bindings(node):
    """
    (name, form) for every name THIS ONE node binds; not recursive.

    The model is "a name is bound here", not "a name is assigned here": the
    intent of ASSERT-8a is that the three registry names are bound by the single
    authorized ``from intm4tm2_decks import ...`` and nowhere else, so every
    binding form Python has counts, not just the assignment family.
    """
    if isinstance(node, ast.Assign):
        for t in node.targets:
            for n in _target_names(t):
                yield n, "assignment"
    elif isinstance(node, ast.AnnAssign):
        for n in _target_names(node.target):
            yield n, "annotated assignment"
    elif isinstance(node, ast.AugAssign):
        for n in _target_names(node.target):
            yield n, "augmented assignment"
    elif isinstance(node, ast.NamedExpr):
        for n in _target_names(node.target):
            yield n, "walrus assignment"
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        for n in _target_names(node.target):
            yield n, "for-loop target"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield node.name, "function definition"
        for n in _arg_names(node.args):
            yield n, "function parameter"
    elif isinstance(node, ast.Lambda):
        for n in _arg_names(node.args):
            yield n, "lambda parameter"
    elif isinstance(node, ast.ClassDef):
        yield node.name, "class definition"
    elif isinstance(node, ast.Import):
        for alias in node.names:
            yield (alias.asname or alias.name.split(".")[0]), "import alias"
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name != "*":
                yield (alias.asname or alias.name), "from-import alias"
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                for n in _target_names(item.optional_vars):
                    yield n, "with ... as target"
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            yield node.name, "except ... as target"
    elif isinstance(node, ast.comprehension):
        for n in _target_names(node.target):
            yield n, "comprehension target"
    elif isinstance(node, ast.MatchAs):
        if node.name:
            yield node.name, "match capture"
    elif isinstance(node, ast.MatchStar):
        if node.name:
            yield node.name, "match star capture"
    elif isinstance(node, ast.MatchMapping):
        if node.rest:
            yield node.rest, "match mapping rest"


def check_all(drc_dir: Path) -> list:
    """Run every assertion, collecting all failures. Raises NoVerdict on exit-2."""
    decks_py = drc_dir / "intm4tm2_decks.py"
    decks_rb = drc_dir / "intm4tm2_decks.rb"
    run_regression = drc_dir / "testing" / "run_regression.py"
    run_drc = drc_dir / "run_drc.py"
    main_drc = drc_dir / "intm4tm2.drc"
    rule_decks = drc_dir / "rule_decks"

    py = load_python_truth(decks_py)
    rb = load_ruby_truth(decks_rb)
    gold = load_golden_truth(run_regression)
    disk = load_disk_truth(rule_decks)
    run_drc_ast = parse_python_source(run_drc)
    main_drc_text = read_drc_text(main_drc)

    failures: list[str] = []

    def fail(assert_id: int, message: str) -> None:
        failures.append(f"[ASSERT-{assert_id}] {message}")

    available = py["available"]
    available_set = set(available)
    ruby_names = set(rb["decks"].keys())
    ruby_files = list(rb["decks"].values())
    preamble = rb["preamble"]
    ruby_skip = set(rb["skip"])

    # 1 -- AVAILABLE_DECKS has no duplicates
    if len(available) != len(available_set):
        dupes = sorted({n for n in available if available.count(n) > 1})
        fail(1, f"AVAILABLE_DECKS has duplicate entries: {_fmt(dupes)}")

    # 2 -- python names == ruby names, both directions AND in the same order
    only_py = available_set - ruby_names
    only_rb = ruby_names - available_set
    if only_py:
        fail(2, f"deck(s) in AVAILABLE_DECKS (python) but not in ALL_DECKS (ruby): {_fmt(only_py)}")
    if only_rb:
        fail(2, f"deck(s) in ALL_DECKS (ruby) but not in AVAILABLE_DECKS (python): {_fmt(only_rb)}")
    # Order is load-bearing, and set-equality cannot see a one-sided reorder:
    # ALL_DECKS order is the concatenation order of the single shared-locals
    # eval() in intm4tm2.drc, AVAILABLE_DECKS order is run_drc's parallel and
    # --help order. There is one canonical deck order, so the two must agree.
    ruby_order = list(rb["decks"].keys())
    if available != ruby_order:
        end = "(end of list)"
        pos, py_at, rb_at = 0, end, end
        for pos in range(max(len(available), len(ruby_order))):
            py_at = available[pos] if pos < len(available) else end
            rb_at = ruby_order[pos] if pos < len(ruby_order) else end
            if py_at != rb_at:
                break
        fail(
            2,
            "AVAILABLE_DECKS (python) and ALL_DECKS (ruby) do not carry the decks in the "
            f"same order: they first diverge at position {pos}, where python has {py_at!r} "
            f"and ruby has {rb_at!r} (the deck order is canonical: it is the concatenation "
            "order of the runset's single shared-locals eval())",
        )

    # 3 -- skip lists agree, and the ruby skip list names real decks
    if py["skip"] != ruby_skip:
        fail(
            3,
            "DEFAULT_SKIP_DECKS differ: python-only "
            f"{_fmt(py['skip'] - ruby_skip)}; ruby-only {_fmt(ruby_skip - py['skip'])}",
        )
    stray_skip = ruby_skip - ruby_names
    if stray_skip:
        fail(3, f"ruby DEFAULT_SKIP_DECKS names deck(s) absent from ALL_DECKS: {_fmt(stray_skip)}")

    # 4 -- registered files vs the files on disk
    dupe_files = sorted({f for f in ruby_files if ruby_files.count(f) > 1})
    if dupe_files:
        fail(4, f"ALL_DECKS maps more than one deck to the same file: {_fmt(dupe_files)}")
    overlap = set(ruby_files) & set(preamble)
    if overlap:
        fail(4, f"file(s) are both a PREAMBLE entry and an ALL_DECKS value: {_fmt(overlap)}")
    registered = set(ruby_files) | set(preamble)
    disk_set = set(disk)
    missing_on_disk = registered - disk_set
    orphan_on_disk = disk_set - registered
    if missing_on_disk:
        fail(4, f"registered file(s) with no *.drc in rule_decks/: {_fmt(missing_on_disk)}")
    if orphan_on_disk:
        fail(4, f"*.drc file(s) in rule_decks/ that no deck registers: {_fmt(orphan_on_disk)}")
    not_regular = sorted(f for f in registered if not (rule_decks / f).is_file())
    if not_regular:
        fail(4, f"registered file(s) that are not a regular file on disk: {_fmt(not_regular)}")

    # 5 -- the emitted composition function against the emitted data, on BOTH of
    # its branches. Every expectation below is computed from the emitted registry
    # and PREAMBLE, never hardcoded, so it mirrors the contract for whatever deck
    # set exists.
    #
    # 5a, the nil branch: the default "all" run, DEFAULT_SKIP applied. BOTH halves
    # of the return value, because half a return value is half a check: the
    # contract literal is `return [PREAMBLE + selected, []]`, so the unknown half
    # of the DEFAULT run is a constant the shipped producer already satisfies and
    # no legitimate producer puts a name there. A nil branch that did would make
    # intm4tm2.drc's `unknown_decks.each { |name| logger.warn(...) }` warn about a
    # deck nobody asked for on EVERY default run.
    expected_all = list(preamble) + [f for n, f in rb["decks"].items() if n not in ruby_skip]
    if rb["all_files"] != expected_all:
        fail(
            5,
            "deck_files(nil)[0] does not equal PREAMBLE + the non-skipped ALL_DECKS values "
            f"in order; emitted {rb['all_files']!r}, expected {expected_all!r}",
        )
    if rb["all_unknown"] != []:
        fail(
            5,
            "deck_files(nil)[1] must be empty: the default run requests nothing by "
            "name, so nothing can be an unknown deck name, and every name it "
            "reports is warned about by the runset on every default run; emitted "
            f"{rb['all_unknown']!r}, expected []",
        )

    # 5b..5f, the requested branch, once per emitted probe and through this ONE
    # mirror assertion: exactly the KNOWN requested decks, mapped to their files,
    # IN THE REQUESTED ORDER, behind PREAMBLE, with NO skip applied (an explicit
    # request can name a default-skipped deck, density above all), and exactly the
    # unknown names classified as unknown. Without this the whole branch was
    # unexercised: order, name -> file mapping and the unknown split were all free
    # to be wrong at exit 0.
    #
    # "input" is the pre-call SNAPSHOT of each request, so the expectation is
    # computed from what was asked for and not from whatever the producer may
    # have left in the array. The probes differ only in the request they make, and
    # each one covers what the others structurally cannot: the second asks for a
    # strict subset, so a producer that expands a partial request (auto-adding a
    # prerequisite, or falling back to every deck) diverges there even though the
    # full-registry probe cannot see it; the third asks for NOTHING, where a
    # producer that treats [] as nil emits the whole default run against an
    # expectation of PREAMBLE alone; the fourth asks for an upcased deck name
    # ahead of its exact-case twin, and because "known" is derived from the
    # emitted EXACT-match registry, a case-folding producer both maps a file too
    # many and empties the unknown half; the fifth asks for MANGLED names -- a
    # truncated or separator-stripped form of a real deck name, verified absent
    # from the registry -- which no probe made of registry names alone can
    # construct, and a producer matching a prefix, a suffix or a normalised form
    # rather than the exact name diverges on both halves there. Every expectation
    # stays self-consistent with the emitted registry, so a faithful producer
    # passes all five.
    for probe_key, label in REQUESTED_PROBES:
        req = rb[probe_key]["input"]
        known_in_order = [n for n in req if n in rb["decks"]]
        expected_req_files = list(preamble) + [rb["decks"][n] for n in known_in_order]
        expected_req_unknown = [n for n in req if n not in rb["decks"]]
        if rb[probe_key]["files"] != expected_req_files:
            fail(
                5,
                f"deck_files({label})[0] does not equal PREAMBLE + the requested known "
                "decks mapped to their ALL_DECKS files in the REQUESTED order: the requested "
                "branch drops, reorders, mis-maps a name to a file, expands the request, or "
                f"wrongly applies DEFAULT_SKIP to an explicit request; probe input {req!r}, "
                f"emitted {rb[probe_key]['files']!r}, expected {expected_req_files!r}",
            )
        # The unknown half is compared as a MULTISET (sorted lists), the files half
        # order-exact. The runset consumes the unknown list only as a per-name
        # `unknown_decks.each { |name| logger.warn(...) }` loop, so its ORDER is
        # cosmetic, and the .rb doc attaches "order-preserving" to the FILES, not
        # to the unknown names: a producer that sorts or reverses its warnings is
        # legitimate and must not be failed here. A multiset still catches a DROP
        # (`.first(1)` on the unknown list: `-rd deck=typo1,typo2` warns about one
        # bad name and silently drops the other deck) because the length and the
        # multiplicities differ, and it still catches a mis-classification because
        # the membership differs. A SET would not: it would miss a dropped
        # duplicate. Repeated names are a deliberately unprobed residual, so no
        # probe requests one.
        if sorted(rb[probe_key]["unknown"]) != sorted(expected_req_unknown):
            fail(
                5,
                f"deck_files({label})[1] mis-classifies unknown deck names: it must be "
                "exactly the requested names absent from ALL_DECKS, as a multiset (their "
                "order is cosmetic, their number and membership are not); probe "
                f"input {req!r}, emitted {rb[probe_key]['unknown']!r}, expected "
                f"{expected_req_unknown!r}",
            )

    # 6 -- every deck is either regression-covered or knowingly untested, never both
    golden_decks = gold["golden_decks"]
    untested = gold["untested"]
    covered = golden_decks | untested
    uncovered = available_set - covered
    surplus = covered - available_set
    both = golden_decks & untested
    if uncovered:
        fail(6, f"deck(s) in neither GOLDEN nor KNOWN_UNTESTED_DECKS: {_fmt(uncovered)}")
    if surplus:
        fail(
            6,
            "deck(s) named by GOLDEN or KNOWN_UNTESTED_DECKS that are not in AVAILABLE_DECKS: "
            f"{_fmt(surplus)}",
        )
    if both:
        fail(
            6,
            "deck(s) in GOLDEN and in KNOWN_UNTESTED_DECKS at once (a deck that gained a "
            f"regression table must leave KNOWN_UNTESTED_DECKS): {_fmt(both)}",
        )

    # 7 -- relocated decks are gone from every live registry
    relocated = py["relocated"]
    for label, other in (
        ("AVAILABLE_DECKS", available_set),
        ("the ruby ALL_DECKS", ruby_names),
        ("GOLDEN + KNOWN_UNTESTED_DECKS", covered),
    ):
        clash = relocated & other
        if clash:
            fail(7, f"RELOCATED_DECKS key(s) still present in {label}: {_fmt(clash)}")

    # 8 -- the producers against the modules. Python side: the three names are
    # imported absolutely from intm4tm2_decks, are not rebound anywhere, and are
    # read at least once (a private curated list that never reads them is out of
    # model; see the module docstring). Ruby side: the runset requires the
    # module, keeps no all_decks hash of its own, and composes through
    # IntM4TM2Decks.deck_files().
    failures.extend(check_no_shadow(run_drc_ast, main_drc_text, run_drc, main_drc))

    return failures


def check_no_shadow(run_drc_ast, main_drc_text, run_drc: Path, main_drc: Path) -> list:
    out = []

    def fail(message: str) -> None:
        out.append(f"[ASSERT-8] {message}")

    # The single authorized binding site: the first ABSOLUTE
    # `from intm4tm2_decks import ...` that brings in at least one tracked name.
    authorized = None
    relative_imports = []
    for node in ast.walk(run_drc_ast):
        if not (isinstance(node, ast.ImportFrom) and node.module == "intm4tm2_decks"):
            continue
        if node.level != 0:
            relative_imports.append(node)
            continue
        names = {a.name for a in node.names}
        if authorized is None and (
            "*" in names or names & set(PRODUCER_NAMES)
        ):
            authorized = node

    for node in relative_imports:
        fail(
            f"{run_drc.name} imports intm4tm2_decks with a relative import "
            f"(`from {'.' * node.level}intm4tm2_decks import ...`); run_drc.py runs as a "
            "script and would fail at runtime, so the import must be absolute"
        )

    imported = set()
    if authorized is not None:
        for alias in authorized.names:
            if alias.name == "*":
                imported.update(PRODUCER_NAMES)
            elif alias.asname in (None, alias.name):
                imported.add(alias.name)
    not_imported = [n for n in PRODUCER_NAMES if n not in imported]
    if not_imported:
        fail(
            f"{run_drc.name} does not import {', '.join(not_imported)} "
            "from intm4tm2_decks (the module is not the source of truth)"
        )

    # An import that nothing reads is not consumption. Without this, run_drc.py
    # could keep the authorized import, rebind nothing (so the checks above are
    # silent) and drive the run off a private curated deck list, leaving the
    # effective deck set free to diverge from the registry at exit 0. This is
    # the python-side counterpart of the ruby exclusive deck_files( positive:
    # importing the module is not the same as composing through it.
    # Read = the name occurs as an ast.Name in Load context anywhere in the
    # module (an f-string field counts; ast.walk descends into JoinedStr).
    loaded = {
        node.id
        for node in ast.walk(run_drc_ast)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    for name in PRODUCER_NAMES:
        if name in imported and name not in loaded:
            fail(
                f"{run_drc.name} imports {name} from intm4tm2_decks but never reads it; "
                "the registry is imported, not consumed"
            )

    # ...and nowhere else. Every other binding form of a tracked name anywhere in
    # the module (loop target, def/class name, import alias, with/except target,
    # comprehension target, match capture, or any assignment) shadows the import.
    exempt = {id(authorized)} | {id(n) for n in relative_imports}
    rebound = []
    for node in ast.walk(run_drc_ast):
        if id(node) in exempt:
            continue
        for name, form in _bindings(node):
            if name in PRODUCER_NAMES and (name, form) not in rebound:
                rebound.append((name, form))
    for name, form in sorted(rebound):
        fail(
            f"{run_drc.name} rebinds {name} ({form}) outside the single authorized "
            "`from intm4tm2_decks import ...`, shadowing the import"
        )

    if re.search(r"all_decks\s*=\s*\{", main_drc_text):
        fail(f"{main_drc.name} re-inlines an `all_decks = {{` hash literal")
    if "intm4tm2_decks" not in main_drc_text:
        fail(f"{main_drc.name} does not mention intm4tm2_decks (the require is gone)")
    # The module must be COMPOSED THROUGH, not merely required or aliased.
    # deck_files() is the only sanctioned composition path, and assert 5 validates
    # exactly that function's output; a runset that aliases ALL_DECKS and composes
    # inline would leave assert 5 checking a function the runset never runs, which
    # is the composition drift this guard exists to close.
    if not re.search(r"IntM4TM2Decks\.deck_files\s*\(", main_drc_text):
        fail(
            f"{main_drc.name} never calls IntM4TM2Decks.deck_files(); the emitted "
            "composition function is not consumed (requiring or aliasing the module "
            "without composing through deck_files() is not enough)"
        )
    return out


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _emit(lines) -> None:
    """Write to stdout. Any encoding failure here must NOT become a verdict."""
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main(argv) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: check_deck_registry.py <drc_dir>\n")
        return 2
    drc_dir = Path(argv[1])
    if not drc_dir.is_dir():
        sys.stderr.write(f"NO VERDICT (drc-dir-absent): {drc_dir} is not a directory\n")
        return 2
    try:
        failures = check_all(drc_dir)
    except NoVerdict as exc:
        try:
            sys.stderr.write(f"NO VERDICT ({exc.reason}): {exc.detail}\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001 - reporting must never change the verdict
            pass
        return 2

    try:
        if failures:
            _emit(
                [f"deck registry: {len(failures)} assertion failure(s)", ""]
                + list(failures)
            )
            return 1
        _emit(["deck registry: all assertions hold"])
        return 0
    except Exception as exc:  # noqa: BLE001 - an un-emittable name is exit 2, not 1
        try:
            sys.stderr.write(f"NO VERDICT (report-unemittable): {exc!r}\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - never traceback into exit 1
        try:
            sys.stderr.write(f"NO VERDICT (internal-error): {exc!r}\n")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(2)
