#!/usr/bin/env python3
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
"""Density-feedback fill closure for all four interposer metals.

The fill generators (tech/macros/interposer_filler_metal.lym for Metal4/Metal5,
interposer_filler_topmetal.lym for TopMetal1/TopMetal2) place fixed-grid patterns; on
their own they do not know whether the result actually lands inside the density band.
This driver closes that loop: each iteration it runs both generators, merges the fill,
signs the result off with the interposer density deck (density.drc, the same fetch_rule
thresholds used at tape-out), and adjusts each metal's lattice until all four are in band
or the iteration budget is spent.

Per metal, from the deck verdict:
  - Metal4/Metal5 under the floor (Mn.j / MnFil.h) -> densify (shrink the lattice gap);
    over the cap (Mn.k / MnFil.k) -> relax (grow the lattice gap).
  - TopMetal over the cap (TMn.d) -> relax (grow the lattice gap). The TopMetal grid is
    already at maximum density, so an under-band TopMetal (TMn.c) cannot be helped by any
    pitch and is reported, not chased (that is a drawn-metal issue).
  - Metal4/Metal5 both under and over across windows -> a global pitch cannot fix it;
    reported as 'split', left for local fill (not yet implemented).

The deck stays the authority for pass/fail; this only steers the generators. The output
is the design with the accepted fill merged in (fill flattened; run the macros
interactively if you want the fill kept as cell instances).

Usage:
    python fill_closure.py in.gds -o out.gds [--topcell TOP] [--max-iter 6]
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import klayout.db as kdb

HERE = Path(__file__).resolve().parent
REPO_KLAYOUT = HERE.parent                      # libs.tech/klayout
MACRO = REPO_KLAYOUT / "tech" / "macros" / "interposer_filler_metal.lym"
TOPMETAL_MACRO = REPO_KLAYOUT / "tech" / "macros" / "interposer_filler_topmetal.lym"
DRC_DIR = REPO_KLAYOUT / "tech" / "drc"
DRC_SCRIPT = DRC_DIR / "intm4tm2.drc"
# The generator reads its DRC clearances (MFil_c) from this same JSON; pass it
# explicitly so the driven run uses the sign-off values, never a stale literal.
TECH_JSON = DRC_DIR / "rule_decks" / "interposer_tech_default.json"

METALS = {50: "M4", 67: "M5"}                   # lower metals (two-size lattice generator)
TOPMETALS = {126: "TM1", 134: "TM2"}            # top metals (single-cell lattice generator)
# The full interposer fill stack; the closure drives all four against the density deck.
STACK = {50: "M4", 67: "M5", 126: "TM1", 134: "TM2"}
# density.drc band per metal, as (min_key, max_key) in interposer_tech_default.json;
# Metal4/Metal5 share the Mn_j/Mn_k global band. Values there are fractions (0..1),
# scaled to percent for the report to match the measured coverage_pct.
BAND_KEYS = {50: ("Mn_j", "Mn_k"), 67: ("Mn_j", "Mn_k"),
             126: ("TM1_c", "TM1_d"), 134: ("TM2_c", "TM2_d")}

DEFAULT_GAPS = (0.84, 0.60)                      # (large_gap, small_gap), = macro defaults
# Densest allowed lattice: large gap 0.35 keeps the 2.0 um cell open-area coverage
# near 72%, under the 75% window cap, so densification cannot itself trip MnFil.k.
GAP_FLOOR = (0.35, 0.30)
GAP_CAP = (5.0, 5.0)
DENSIFY = 0.7
RELAX = 1.3

# TopMetal lattice: a single extra gap added to the TM(n)Fil.b minimum spacing. The
# generator is already at maximum density at 0.0, so the closure can only RELAX (thin
# the fill) when a design that is dense in drawn TopMetal crosses the 70% cap; it cannot
# densify below the minimum, so a TopMetal that comes in under 25% is a design issue and
# is reported, not chased. Grown additively (multiplying 0.0 would never move).
TM_DEFAULT_GAP = 0.0
TM_GAP_CAP = 30.0
TM_GAP_STEP = 3.0


def _macro_body(macro=MACRO):
    """The DRC-DSL body of a fill macro (KLayout un-escapes the XML entities)."""
    return ET.parse(macro).getroot().find("text").text


def _run_topmetal(design, fill_only, workdir, params=None):
    """Run the TopMetal1/TopMetal2 generator once, writing its fill to fill_only.

    `params` optionally maps a TopMetal GDS layer (126/134) to the extra lattice gap
    (um) the closure wants; unset, the macro uses its dense default (gap 0.0).
    """
    defines = ["-rd", f"tech_json={TECH_JSON}"]
    for layer, extra in (params or {}).items():
        key = TOPMETALS[layer].lower()          # tm1 / tm2
        defines += ["-rd", f"{key}_gap={extra:.4f}"]
    runner = workdir / "fill_topmetal_run.drc"
    runner.write_text(f'source("{design}")\ntarget("{fill_only}")\n' + _macro_body(TOPMETAL_MACRO))
    subprocess.run([shutil.which("klayout"), "-b", *defines, "-r", str(runner)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


@contextmanager
def _workdir(workdir):
    """Yield a working directory Path; make a temporary one when workdir is None."""
    if workdir is None:
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)
    else:
        wd = Path(workdir)
        wd.mkdir(parents=True, exist_ok=True)
        yield wd


def _rules():
    return json.loads(TECH_JSON.read_text())["rules"]


def _band(rules, layer):
    """[min, max] density band in percent for a metal layer, from the tech JSON."""
    lo_key, hi_key = BAND_KEYS[layer]
    return [float(rules[lo_key]) * 100.0, float(rules[hi_key]) * 100.0]


def _metal_entry(rules, layer, coverage, state):
    lo, hi = _band(rules, layer)
    return {"coverage_pct": round(coverage, 2), "band": [lo, hi],
            "state": state, "converged": state == "ok"}


def _area_entry(rules, layer, coverage):
    """Report entry for a metal graded by area coverage against its band (no deck)."""
    lo, hi = _band(rules, layer)
    state = "under" if coverage < lo else "over" if coverage > hi else "ok"
    return {"coverage_pct": round(coverage, 2), "band": [lo, hi],
            "state": state, "converged": lo <= coverage <= hi}


def _run_generator(design, fill_only, params, workdir):
    defines = ["-rd", f"tech_json={TECH_JSON}"]
    for layer, (large_gap, small_gap) in params.items():
        key = METALS[layer].lower()             # m4 / m5
        defines += ["-rd", f"{key}_large_gap={large_gap:.4f}",
                    "-rd", f"{key}_small_gap={small_gap:.4f}"]
    runner = workdir / "fill_run.drc"
    runner.write_text(f'source("{design}")\ntarget("{fill_only}")\n' + _macro_body())
    subprocess.run([shutil.which("klayout"), "-b", *defines, "-r", str(runner)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _merge(design, fill_only, combined):
    base = kdb.Layout()
    base.read(str(design))
    fill = kdb.Layout()
    fill.read(str(fill_only))
    base_top = base.top_cell()
    fill_top = fill.top_cell()
    for li in fill.layer_indexes():
        info = fill.get_info(li)
        target = base.layer(info.layer, info.datatype)
        base_top.shapes(target).insert(kdb.Region(fill_top.begin_shapes_rec(li)))
    base.write(str(combined))


def _density(gds, metal_layer):
    """Drawn+filler-minus-slit coverage over the chip area, as density.drc measures."""
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    dbu = ly.dbu

    def reg(layer, dt):
        li = ly.find_layer(layer, dt)
        return kdb.Region() if li is None else kdb.Region(top.begin_shapes_rec(li))

    # Chip-area priority matches the density deck: prBoundary (235/0) > EdgeSeal
    # boundary (39/4) > layout extent. Using a different denominator here than the
    # deck uses would let coverage_pct and the deck's in-band state disagree.
    prb = reg(235, 0)
    esb = reg(39, 4)
    if not prb.is_empty():
        chip = prb.area() * dbu * dbu
    elif not esb.is_empty():
        chip = esb.area() * dbu * dbu
    else:
        bb = top.bbox()
        chip = bb.width() * bb.height() * dbu * dbu
    if chip == 0:
        return 0.0
    covered = (reg(metal_layer, 0) + reg(metal_layer, 22) - reg(metal_layer, 24)).area() * dbu * dbu
    return 100.0 * covered / chip


def _deck_state(combined, topcell, run_dir):
    """Per-metal in-band classification straight from the density deck verdict.

    Metal4/Metal5 read the global band (Mn.j/Mn.k) and the 800 um windowed band
    (MnFil.h/k); TopMetal1/TopMetal2 have only the global band (TMn.c/TMn.d), no window.
    """
    if str(DRC_DIR) not in sys.path:
        sys.path.insert(0, str(DRC_DIR))
    from run_drc import run_deck, get_rules_with_violations
    report = run_deck(str(DRC_SCRIPT), "density", str(combined), topcell,
                      run_dir, threads=2, run_mode="flat")
    viol = get_rules_with_violations(report)
    state = {}
    for layer, name in STACK.items():
        if layer in METALS:
            under = (f"{name}.j" in viol) or (f"{name}Fil.h" in viol)
            over = (f"{name}.k" in viol) or (f"{name}Fil.k" in viol)
        else:                                    # TopMetal: global-only TMn.c / TMn.d
            under = f"{name}.c" in viol
            over = f"{name}.d" in viol
        state[layer] = "split" if (under and over) else "under" if under else "over" if over else "ok"
    return state


def _adjust(layer, gaps, state):
    """Next lattice parameters for one metal from its deck verdict.

    Metal4/Metal5 carry a (large_gap, small_gap) tuple, shrunk to densify and grown to
    relax. TopMetal carries a single extra gap that only grows (it is already at maximum
    density), so an 'under' TopMetal is left as is for the report rather than chased.
    'ok' and 'split' never move: a single global pitch cannot fix a window that is sparse
    and another that is dense.
    """
    if layer in METALS:
        large_gap, small_gap = gaps
        if state == "under":
            return (max(GAP_FLOOR[0], large_gap * DENSIFY), max(GAP_FLOOR[1], small_gap * DENSIFY))
        if state == "over":
            return (min(GAP_CAP[0], large_gap * RELAX), min(GAP_CAP[1], small_gap * RELAX))
        return gaps
    # TopMetal: grow the extra gap to thin an over-cap fill; cannot densify past 0.
    if state == "over":
        return min(TM_GAP_CAP, gaps + TM_GAP_STEP)
    return gaps


def close_fill(design, output, topcell="TOP", max_iter=6, workdir=None, log=lambda *_: None):
    """Iterate the generators to bring all four interposer metals into band.

    Each iteration runs the Metal4/Metal5 generator and the TopMetal generator, merges
    both fills onto the design, signs the result off with the density deck, and adjusts
    each metal's lattice from the deck verdict (Metal4/Metal5 densify or relax; TopMetal
    relaxes only). Returns (converged, history); history is one dict per iteration with
    the per-metal gaps used, the achieved density, and the deck state, keyed by the four
    STACK layers. `workdir` may be None, in which case a temporary directory is used.
    """
    with _workdir(workdir) as wd:
        return _close_fill(design, output, topcell, max_iter, wd, log)


def _run_stack(design, params, workdir, fill_m45, fill_tm, tmp, combined,
               regen_m45=True, regen_tm=True):
    """Fill Metal4/Metal5 and TopMetal for one iteration and merge into `combined`.

    A generator is only re-invoked when its group's parameters changed since the
    previous iteration (`regen_*`); an unchanged group reuses its prior fill GDS, so
    a run that only adjusts one group does not needlessly re-fill the other. The merge
    always consumes both fill files, so the skipped file must already exist (it does
    from the first iteration, where both flags are True).
    """
    if regen_m45:
        _run_generator(design, fill_m45, {l: params[l] for l in METALS}, workdir)
    if regen_tm:
        _run_topmetal(design, fill_tm, workdir, {l: params[l] for l in TOPMETALS})
    _merge(design, fill_m45, tmp)
    _merge(tmp, fill_tm, combined)


def _close_fill(design, output, topcell, max_iter, workdir, log):
    fill_m45 = workdir / "fill_m45.gds"
    fill_tm = workdir / "fill_tm.gds"
    tmp = workdir / "m45.gds"
    combined = workdir / "combined.gds"
    run_dir = workdir / "drc_run"
    run_dir.mkdir(exist_ok=True)

    params = {layer: DEFAULT_GAPS for layer in METALS}
    params.update({layer: TM_DEFAULT_GAP for layer in TOPMETALS})
    history = []
    converged = False
    prev = None

    for iteration in range(1, max_iter + 1):
        regen_m45 = prev is None or any(params[l] != prev[l] for l in METALS)
        regen_tm = prev is None or any(params[l] != prev[l] for l in TOPMETALS)
        _run_stack(design, params, workdir, fill_m45, fill_tm, tmp, combined,
                   regen_m45=regen_m45, regen_tm=regen_tm)
        prev = dict(params)
        state = _deck_state(combined, topcell, run_dir)
        density = {layer: _density(combined, layer) for layer in STACK}
        history.append({"iter": iteration, "gaps": dict(params),
                        "density": dict(density), "state": dict(state)})
        log("iter {}: {}".format(
            iteration, "  ".join(f"{STACK[l]}={density[l]:.1f}% [{state[l]}]" for l in STACK)))
        if all(state[layer] == "ok" for layer in STACK):
            converged = True
            break
        nxt = {layer: _adjust(layer, params[layer], state[layer]) for layer in STACK}
        if nxt == params:
            # Fixed point: no metal moved (an un-chased TopMetal 'under', a 'split'
            # window, or gaps clamped at their floor/cap). Further iterations would
            # re-run identical work for byte-identical results, so stop and report.
            break
        params = nxt

    Path(output).write_bytes(combined.read_bytes())
    return converged, history


def fill_stack(design, output, topcell="INTERPOSER", mode="single-pass",
               max_iter=6, workdir=None, log=lambda *_: None):
    """Fill all four interposer metals (Metal4, Metal5, TopMetal1, TopMetal2) in one call.

    mode "single-pass" (default): run each generator once, merge, and grade coverage by
    area against each metal's density band (fast, no DRC deck). mode "closure": drive all
    four metals through the density-feedback loop, deck-verified each iteration (Metal4/
    Metal5 densify or relax the lattice; TopMetal relaxes to hold under its 70% cap).

    Honors the keep-outs already in `design`: both generators subtract the per-metal
    nofill datatypes (<metal>/23) and NoMetFiller (160/0), and merging only adds fill,
    so anything stamped upstream (for example by the KiCad plugin) is preserved.

    Safe in place: `output` may be the same path as `design`; the design is read fully
    before `output` is written. `workdir` may be None (a temporary directory is used).

    Returns a report dict, coverage and band in percent:
        {"mode": ...,
         "converged": bool,                       # AND over the four metals
         "M4"/"M5"/"TM1"/"TM2": {"coverage_pct", "band": [min, max], "state", "converged"}}
    where "state" is "ok"/"under"/"over", plus "split" for a Metal4/Metal5 that the deck
    finds both under and over across windows (closure mode only). In closure mode every
    metal's state comes from the deck; in single-pass it is graded by area.
    """
    if shutil.which("klayout") is None:
        raise RuntimeError("klayout binary not on PATH")
    if mode not in ("single-pass", "closure"):
        raise ValueError(f"unknown mode {mode!r} (expected 'single-pass' or 'closure')")

    with _workdir(workdir) as wd:
        rules = _rules()
        report = {"mode": mode}

        if mode == "closure":
            # The closure drives all four metals against the density deck and writes the
            # merged design+fill straight to `output`; grade every metal by its verdict.
            _, history = close_fill(design, output, topcell, max_iter,
                                    workdir=wd / "closure", log=log)
            last = history[-1]
            for layer in STACK:
                report[STACK[layer]] = _metal_entry(rules, layer,
                                                    last["density"][layer], last["state"][layer])
        else:
            fill_m = wd / "fill_metal.gds"
            _run_generator(design, fill_m, {layer: DEFAULT_GAPS for layer in METALS}, wd)
            m45 = wd / "m45.gds"
            _merge(design, fill_m, m45)
            fill_tm = wd / "fill_topmetal.gds"
            _run_topmetal(m45, fill_tm, wd)
            combined = wd / "combined.gds"
            _merge(m45, fill_tm, combined)
            for layer in STACK:
                report[STACK[layer]] = _area_entry(rules, layer, _density(combined, layer))
            Path(output).write_bytes(combined.read_bytes())

        report["converged"] = all(report[STACK[layer]]["converged"] for layer in STACK)
        log("fill_stack ({}): {}".format(mode, "  ".join(
            f"{STACK[layer]}={report[STACK[layer]]['coverage_pct']:.1f}% "
            f"[{report[STACK[layer]]['state']}]" for layer in STACK)))
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Density-feedback fill closure for all four interposer metals")
    parser.add_argument("input", help="input layout (GDS/OAS)")
    parser.add_argument("-o", "--output", required=True, help="output layout (design + fill)")
    parser.add_argument("--topcell", default="TOP")
    parser.add_argument("--max-iter", type=int, default=6)
    args = parser.parse_args(argv)

    if shutil.which("klayout") is None:
        parser.error("klayout binary not on PATH")

    with tempfile.TemporaryDirectory() as td:
        converged, history = close_fill(args.input, args.output, args.topcell,
                                        args.max_iter, workdir=td, log=print)

    rules = _rules()
    last = history[-1]
    print("\nFill closure " + ("CONVERGED" if converged
                               else f"did NOT converge in {args.max_iter} iterations"))
    for layer, name in STACK.items():
        lo, hi = _band(rules, layer)
        print(f"  {name}: {last['density'][layer]:.1f}%  "
              f"band[{lo:.0f},{hi:.0f}]  [{last['state'][layer]}]")
    print(f"  output: {args.output}")
    return 0 if converged else 1


if __name__ == "__main__":
    sys.exit(main())
