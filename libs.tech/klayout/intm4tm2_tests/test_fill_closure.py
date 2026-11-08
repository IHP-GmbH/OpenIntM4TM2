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
"""Regression for the four-metal density-feedback fill closure (python/fill_closure.py).

close_fill drives Metal4, Metal5, TopMetal1 and TopMetal2 through the density deck each
iteration and adjusts each metal's lattice from the verdict. Three behaviors are proven
end to end against the real deck:

  - a design already fillable to band closes in a single iteration, with no needless
    adjustment on any of the four metals;
  - a Metal4/Metal5 that comes in under the floor (forced with a large nofill box) is
    densified into band, and the densified fill is still geometrically legal;
  - a TopMetal that is already dense in drawn metal, so the default fill would cross the
    70% cap, is relaxed (its lattice thinned) back into band. TopMetal is the metal that
    can only be relaxed, never densified, so this exercises that branch specifically.

In the last two, one generator's lattice never moves, and its fill from the first pass
is kept rather than generated again. A board that cannot reach the band stalls once its
lattice stops moving; the iterations after that repeat the last record without running
a generator or the deck.

Every gap a densify or relax step can reach stays on the 5 nm manufacturing grid, and
one and two steps on each metal pass the offgrid deck.

The density deck runs on the threads the caller names, or by default on the main deck's
own four, lowered to the CPUs the process may use: the affinity set, capped by the
tightest cgroup cpu.max on the process's own path, read here from a fake cgroup tree.
"""

import itertools
import os
import re
import shutil
import sys
from pathlib import Path

import klayout.db as kdb
import pytest

REPO_KLAYOUT = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO_KLAYOUT / "python"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"

KLAYOUT_BIN = shutil.which("klayout")

sys.path.insert(0, str(PYTHON_DIR))
import fill_closure as fc  # noqa: E402

MN_MIN, MN_MAX = 35.0, 60.0            # Metal4/Metal5 global band (Mn_j/Mn_k)
TM_MAX = 70.0                          # TopMetal global cap (TM(n)_d)


def _seal(top, ly):
    ring = kdb.Region(kdb.Box(0, 0, 200000, 200000)) - kdb.Region(kdb.Box(5000, 5000, 195000, 195000))
    top.shapes(ly.layer(39, 0)).insert(ring)


def _box(ly, top, layer, dt, x0, y0, x1, y1):
    top.shapes(ly.layer(layer, dt)).insert(kdb.DBox(x0, y0, x1, y1))


def _write_normal(path):
    """Open interior with a modest block on each metal: fillable to band directly."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 0, 20, 20, 60, 60)
    _box(ly, top, 67, 0, 120, 120, 170, 170)
    _box(ly, top, 126, 0, 20, 120, 60, 160)
    _box(ly, top, 134, 0, 120, 20, 170, 60)
    ly.write(str(path))


def _write_sparse(path):
    """A large nofill box on Metal4/Metal5 starves their default fill below the floor."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 23, 10, 10, 130, 130)
    _box(ly, top, 67, 23, 10, 10, 130, 130)
    _box(ly, top, 50, 0, 150, 150, 160, 160)
    _box(ly, top, 67, 0, 150, 150, 160, 160)
    ly.write(str(path))


def _write_dense_topmetal(path):
    """Drawn TopMetal1 near 62% of the chip: the default dense fill would cross the 70% cap."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 0, 20, 20, 60, 60)          # small M4/M5 blocks, easily in band
    _box(ly, top, 67, 0, 120, 120, 160, 160)
    _box(ly, top, 126, 0, 5, 5, 195, 135)         # 190 x 130 = 24700 um^2 over a 40000 chip
    ly.write(str(path))


def _write_starved(path):
    """A nofill box over most of the interior: Metal4/Metal5 stay under the floor even
    at the densest lattice, so the closure stalls before its iteration budget."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _seal(top, ly)
    _box(ly, top, 50, 23, 10, 10, 185, 185)
    _box(ly, top, 67, 23, 10, 10, 185, 185)
    ly.write(str(path))


def _count_generator_runs(monkeypatch):
    """Count the Metal4/Metal5 and TopMetal generator runs close_fill makes."""
    runs = {"m45": 0, "tm": 0}
    run_m45, run_tm = fc._run_generator, fc._run_topmetal

    def m45(*args, **kwargs):
        runs["m45"] += 1
        return run_m45(*args, **kwargs)

    def tm(*args, **kwargs):
        runs["tm"] += 1
        return run_tm(*args, **kwargs)

    monkeypatch.setattr(fc, "_run_generator", m45)
    monkeypatch.setattr(fc, "_run_topmetal", tm)
    return runs


def _violations(gds, deck, run_dir):
    if str(DRC_DIR) not in sys.path:
        sys.path.insert(0, str(DRC_DIR))
    from run_drc import run_deck, get_rules_with_violations
    report = run_deck(str(DRC_DIR / "intm4tm2.drc"), deck, str(gds), "TOP",
                      run_dir, threads=2, run_mode="flat")
    return get_rules_with_violations(report)


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_converges_in_one_iteration_when_already_fillable(tmp_path):
    design = tmp_path / "normal.gds"
    out = tmp_path / "normal_out.gds"
    _write_normal(design)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=6, workdir=tmp_path)

    assert converged
    assert len(history) == 1, "an already-fillable design must not be adjusted"
    for layer in fc.STACK:
        assert history[-1]["state"][layer] == "ok"
    for layer in fc.METALS:
        assert MN_MIN <= history[-1]["density"][layer] <= MN_MAX
    for layer in fc.TOPMETALS:
        assert history[-1]["density"][layer] <= TM_MAX
    # The densities are measured on the merged layout in memory: they must be the ones
    # the written output gives when read back.
    for layer in fc.STACK:
        assert history[-1]["density"][layer] == fc._density(out, layer)


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_densifies_underfilled_metal_into_band(tmp_path, monkeypatch):
    design = tmp_path / "sparse.gds"
    out = tmp_path / "sparse_out.gds"
    _write_sparse(design)
    runs = _count_generator_runs(monkeypatch)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=8, workdir=tmp_path)

    # Metal4 starts under the floor, needs more than one pass, and reaches the band.
    assert history[0]["state"][50] == "under"
    assert len(history) >= 2, "under-filled Metal4/Metal5 should have required densification"
    assert converged
    # Only the Metal4/Metal5 lattice moved, so the TopMetal fill of the first pass is kept.
    assert all(h["gaps"][l] == fc.TM_DEFAULT_GAP for h in history for l in fc.TOPMETALS)
    assert runs == {"m45": len(history), "tm": 1}, runs
    for layer in fc.STACK:
        assert history[-1]["state"][layer] == "ok"
    for layer in fc.METALS:
        assert history[-1]["density"][layer] >= MN_MIN

    # the densified fill is still legal geometry, on the manufacturing grid too.
    run_dir = tmp_path / "final_drc"
    run_dir.mkdir()
    for deck in ("metalnfiller", "offgrid"):
        found = _violations(out, deck, run_dir)
        assert not found, f"densified fill tripped the {deck} deck: {sorted(found)}"


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_relaxes_overfilled_topmetal_into_band(tmp_path, monkeypatch):
    """A TopMetal that overshoots the 70% cap at the dense default is thinned back into band."""
    design = tmp_path / "tmdense.gds"
    out = tmp_path / "tmdense_out.gds"
    _write_dense_topmetal(design)
    runs = _count_generator_runs(monkeypatch)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=8, workdir=tmp_path)

    # Only the TopMetal lattice moved, so the Metal4/Metal5 fill of the first pass is kept.
    assert all(h["gaps"][l] == fc.DEFAULT_GAPS for h in history for l in fc.METALS)
    assert runs == {"m45": 1, "tm": len(history)}, runs

    # TopMetal1 starts over the cap, its lattice is grown, and it lands back in band.
    assert history[0]["state"][126] == "over", "the dense TopMetal design should start over the cap"
    assert history[0]["gaps"][126] == fc.TM_DEFAULT_GAP
    assert len(history) >= 2, "an over-cap TopMetal should have required a relax pass"
    assert converged
    assert history[-1]["gaps"][126] > history[0]["gaps"][126], "the TopMetal lattice was not relaxed"
    assert history[-1]["state"][126] == "ok"
    assert history[-1]["density"][126] <= TM_MAX
    # the lower metals were not disturbed by the TopMetal relax.
    for layer in (50, 67, 134):
        assert history[-1]["state"][layer] == "ok"


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
def test_closure_repeats_a_stalled_iteration_without_the_deck(tmp_path, monkeypatch):
    """Once no lattice moves, an iteration repeats the last record and runs nothing."""
    design = tmp_path / "starved.gds"
    out = tmp_path / "starved_out.gds"
    _write_starved(design)
    runs = _count_generator_runs(monkeypatch)
    decks = []
    deck_state = fc._deck_state

    def counted(*args, **kwargs):
        decks.append(args)
        return deck_state(*args, **kwargs)

    monkeypatch.setattr(fc, "_deck_state", counted)

    converged, history = fc.close_fill(design, out, topcell="TOP", max_iter=6, workdir=tmp_path)

    assert not converged and len(history) == 6
    assert history[-1]["state"][50] == "under"
    # Iterations whose gaps differ from the one before (the first included) did work.
    moved = [i for i, entry in enumerate(history)
             if i == 0 or entry["gaps"] != history[i - 1]["gaps"]]
    assert len(moved) < len(history), f"the closure never stalled: {history}"
    assert moved == list(range(len(moved))), "a lattice moved again after stalling"
    assert len(decks) == len(moved), (len(decks), moved)
    assert runs == {"m45": len(moved), "tm": 1}, runs
    stalled = {key: history[moved[-1]][key] for key in ("gaps", "density", "state")}
    for entry in history[len(moved):]:
        assert {key: entry[key] for key in ("gaps", "density", "state")} == stalled
    for layer in fc.STACK:
        assert history[-1]["density"][layer] == fc._density(out, layer)


def _on_grid(value):
    steps = value / fc.GRID
    return abs(steps - round(steps)) < 1e-6


def test_every_gap_the_closure_reaches_is_on_the_grid():
    """The lattice pitch is an on-grid cell plus a gap, so every gap the closure can
    reach from its defaults in up to eight steps must be a whole number of 5 nm steps,
    the small gaps too."""
    rules = fc._rules()
    starts = [*fc.DEFAULT_GAPS, *fc.GAP_FLOOR, *fc.GAP_CAP, fc.TM_DEFAULT_GAP,
              fc.TM_GAP_CAP, fc.TM_GAP_STEP, float(rules["TM1Fil_b"]), float(rules["TM2Fil_b"])]
    assert all(_on_grid(value) for value in starts), starts
    for path in itertools.product(("under", "over"), repeat=8):
        gaps = {layer: fc.DEFAULT_GAPS if layer in fc.METALS else fc.TM_DEFAULT_GAP
                for layer in fc.STACK}
        for state in path:
            gaps = {layer: fc._adjust(layer, gap, state) for layer, gap in gaps.items()}
            flat = [g for layer in fc.METALS for g in gaps[layer]]
            flat += [gaps[layer] for layer in fc.TOPMETALS]
            assert all(_on_grid(g) for g in flat), (path, gaps)


@pytest.mark.live_tool
@pytest.mark.skipif(KLAYOUT_BIN is None, reason="klayout binary not on PATH")
@pytest.mark.parametrize("steps", [(), ("under",), ("over",), ("under", "over")],
                         ids=["ok", "under", "over", "under-over"])
def test_closure_steps_pass_the_offgrid_deck(tmp_path, steps):
    """Densify and relax steps on all four metals ('ok' is the unadjusted control), then
    the offgrid deck, which checks <metal>/22 on the 5 nm grid. The small Metal4/Metal5
    gaps first leave the grid on a second step, hence 'under-over'. Metal stripes on
    Metal4/Metal5 leave slots the large cell cannot take, so the small lattice is placed."""
    design = tmp_path / "board.gds"
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _box(ly, top, 235, 0, 0, 0, 60, 60)
    for x in (10.0, 14.3, 19.1, 24.4, 30.2, 36.5, 43.3):
        for layer in fc.METALS:
            _box(ly, top, layer, 0, x, 5, x + 1.0, 55)
    ly.write(str(design))

    params = {layer: fc.DEFAULT_GAPS if layer in fc.METALS else fc.TM_DEFAULT_GAP
              for layer in fc.STACK}
    for state in steps:
        params = {layer: fc._adjust(layer, gap, state) for layer, gap in params.items()}
    filled = tmp_path / "filled.gds"
    fc._run_stack(design, params, tmp_path, tmp_path / "fill_m45.gds",
                  tmp_path / "fill_tm.gds", tmp_path / "m45.gds", filled)

    out = kdb.Layout()
    out.read(str(filled))
    for layer, name in fc.STACK.items():
        fill = kdb.Region(out.top_cell().begin_shapes_rec(out.layer(layer, 22)))
        assert not fill.is_empty(), f"{name}: no fill placed"
        if layer in fc.METALS:
            small = [p for p in fill.each() if p.bbox().width() == 1000]
            assert small, f"{name}: no small fill cell placed"
    run_dir = tmp_path / "drc"
    run_dir.mkdir()
    offgrid = _violations(filled, "offgrid", run_dir)
    assert not offgrid, f"fill after {steps} is off grid: {sorted(offgrid)}"


def test_density_takes_the_chip_area_the_deck_takes(tmp_path):
    # Only a 39/4 boundary: the deck and density_report use its area, and
    # so must the closure's estimate, not the larger layout extent.
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    _box(ly, top, 39, 4, 0, 0, 100, 100)
    _box(ly, top, 50, 0, 0, 0, 50, 50)
    _box(ly, top, 67, 0, 150, 150, 200, 200)       # stretches the extent to 200 x 200
    path = tmp_path / "boundary.gds"
    ly.write(str(path))
    assert fc._density(path, 50) == pytest.approx(25.0)


def _cgroup(base, membership, quotas):
    """A fake cgroup v2 tree under `base`: `quotas` maps a group below the root ("" for
    the root itself) to its cpu.max line; `membership` is the /proc/self/cgroup line."""
    root = base / "cgroup"
    for group, quota in quotas.items():
        folder = root / group
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cpu.max").write_text(quota + "\n")
    proc = base / "proc_cgroup"
    proc.write_text(membership + "\n")
    return proc, root


def test_cpu_allowance_takes_the_tightest_quota_on_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(8)))
    # The tightest quota on the path binds, whether it sits on the process's own group
    # or on a parent.
    for name, parent, own in (("parent", "200000", "300000"), ("own", "300000", "200000")):
        proc, root = _cgroup(tmp_path / name, "0::/user.slice/run.scope",
                             {"": "max 100000", "user.slice": parent + " 100000",
                              "user.slice/run.scope": own + " 100000"})
        assert fc._cpu_allowance(proc, root) == 2
    # A share of a CPU rounds down, never below one thread.
    for name, quota, threads in (("fraction", "250000", 2), ("half", "50000", 1)):
        proc, root = _cgroup(tmp_path / name, "0::/run.scope",
                             {"run.scope": quota + " 100000"})
        assert fc._cpu_allowance(proc, root) == threads
    # A quota above the affinity set, none on the path (one on a group off it does not
    # count), or no cgroup v2 line: the affinity set alone.
    proc, root = _cgroup(tmp_path / "loose", "0::/run.scope", {"run.scope": "1600000 100000"})
    assert fc._cpu_allowance(proc, root) == 8
    proc, root = _cgroup(tmp_path / "none", "0::/run.scope",
                         {"run.scope": "max 100000", "other.scope": "100000 100000"})
    assert fc._cpu_allowance(proc, root) == 8
    proc, root = _cgroup(tmp_path / "v1", "12:cpu,cpuacct:/run.scope",
                         {"run.scope": "100000 100000"})
    assert fc._cpu_allowance(proc, root) == 8


def test_the_density_deck_runs_on_the_threads_the_caller_names(tmp_path, monkeypatch):
    """No generator or deck runs: the stack, densities and verdict are stubbed, and the
    deck stub keeps the threads it is handed."""
    seen = []

    def stack(design, params, workdir, fill_m45, fill_tm, tmp, combined, **_):
        combined.write_bytes(b"")

    def deck(combined, topcell, run_dir, threads):
        seen.append(threads)
        return {layer: "ok" for layer in fc.STACK}

    monkeypatch.setattr(fc, "_run_stack", stack)
    monkeypatch.setattr(fc, "_layout_density", lambda merged, layer: 50.0)
    monkeypatch.setattr(fc, "_deck_state", deck)
    # The default is the deck's own four, lowered to a tighter allowance; a count the
    # caller names stands either way.
    for case, (allowance, threads) in enumerate(((32, None), (2, None), (32, 7), (2, 7))):
        monkeypatch.setattr(fc, "_cpu_allowance", lambda cpus=allowance: cpus)
        fc.close_fill(tmp_path / "in.gds", tmp_path / "out.gds", workdir=tmp_path / str(case),
                      threads=threads)
    assert seen == [4, 2, 7, 7]


def test_the_default_threads_are_the_main_decks_own():
    """DECK_THREADS copies the main deck's $threads default; this keeps the two equal."""
    default = re.search(r"if \$threads\.nil\?\s*\$threads = (\d+)",
                        fc.DRC_SCRIPT.read_text())
    assert default, "the main deck no longer sets a $threads default this test can read"
    assert int(default.group(1)) == fc.DECK_THREADS


@pytest.mark.parametrize("threads", (0, -2))
def test_the_closure_refuses_fewer_than_one_thread(tmp_path, threads):
    with pytest.raises(ValueError, match="threads must be at least 1"):
        fc.close_fill(tmp_path / "in.gds", tmp_path / "out.gds", workdir=tmp_path,
                      threads=threads)


@pytest.mark.parametrize("threads", ("0", "-2"))
def test_the_cli_refuses_fewer_than_one_thread(threads, capsys):
    with pytest.raises(SystemExit):
        fc.main(["in.gds", "-o", "out.gds", "--threads", threads])
    assert "--threads must be at least 1" in capsys.readouterr().err


def test_fill_stack_hands_its_threads_to_the_closure(tmp_path, monkeypatch):
    seen = []

    def close_fill(*args, threads=None, **kwargs):
        seen.append(threads)
        record = {layer: 50.0 for layer in fc.STACK}
        return True, [{"density": record, "state": {layer: "ok" for layer in fc.STACK}}]

    monkeypatch.setattr(fc.shutil, "which", lambda name: name)
    monkeypatch.setattr(fc, "close_fill", close_fill)
    for threads in (None, 3):
        fc.fill_stack(tmp_path / "in.gds", tmp_path / "out.gds", mode="closure",
                      threads=threads)
    assert seen == [None, 3]
