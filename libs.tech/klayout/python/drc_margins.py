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
"""How close a layout comes to each interposer DRC rule.

A clean DRC run writes no markers, so it says nothing about margins. This tool runs a
copy of the interposer deck in which each minimum-distance rule of drc_margins.json is
widened by a window W, and records per rule the thinnest distance the check finds
inside that window, where it is, and how many edge pairs fall inside it. The margin is
that distance minus the floor, the threshold the main DRC applies. The list puts the
flagged rules first (below), then the smallest margin.

The copy holds intm4tm2.drc, intm4tm2_decks.rb and rule_decks/. In it the tool raises
the table's keys in interposer_tech_default.json and puts an output hook at the top of
the main deck. The original deck is not touched. Most rules are measured at their output.
The few whose deck output is polygons (TAPS below) are measured on the deck's own lines,
found from the line that outputs the rule: the edge pairs of the check are recorded
before its .polygons, and the shapes of the branch the deck joins to it, which it flags
whatever the distance (an opening or a MIM not wholly on its metal, a slit over a MIM),
are counted as the rule's flagged shapes. The copy fails unless each of those lines is
found exactly once and in that shape. The hook keeps only the thinnest pair of each
table rule and drops every output, since a raised key can also move the derived geometry
of rules outside the table (Vn_b sizes the V4.b1 arrays).

A rule with no pair inside its window reads ">= W nm". A rule whose joined branch has
shapes reads "flagged". The rules measured against the seal ring read "no seal ring"
when the layout has none, since they then measure nothing. A negative margin is below
the floor, a violation the main DRC reports too, and so is a flagged rule. The other
enclosure rules measure a via only where it has some metal around it: a via with no
landing at all is the main DRC's report.

The list never decides pass or fail; the main DRC does.

Usage:
    python drc_margins.py layout.gds -o out_dir [--topcell TOP] [--threads 1]
writes out_dir/margins.json and out_dir/margins.txt.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRC_DIR = HERE.parent / "tech" / "drc"
TABLE = HERE / "drc_margins.json"
TECH_JSON = "interposer_tech_default.json"
# The format of margins.json: it moves when one of its fields is renamed, dropped or
# changes meaning.
FORMAT = 1

# The rules whose deck output is polygons, by name as the deck writes it (Ruby, so the
# slit loop's name interpolates), and their deck file. The deck's lines for each are a
# contract: "<join>.output('<name>', ...)", "<join> = <check>.join(<branch>)" with a
# variable on each side, and "<check> = <expr>.polygons", each a line of its own, the
# last two with no '#' (no comment, no interpolation). In the copy the check records
# the edge pairs of <expr> and the join counts the shapes of <branch>.
TAPS = {
    "Pas.c": "5_27_passiv.drc",
    "Padc.c": "6_9_copperpillar.drc",
    "Padb.c": "6_9_solderbump.drc",
    "MIM.c": "6_11_mim.drc",
    "Slt.g_#{slt_abbrev}": "7_3_metalslits.drc",
}

# Put at the top of the copy's main deck. A table rule's edge pairs are scanned for
# the thinnest one; every output is then dropped, so the copy writes no markers.
HOOK = """\
# drc_margins: record the thinnest pair of each table rule, drop every output.
require 'json'
$margin_rules = JSON.parse(File.read($margin_rules_file))
$margin_names = {}
$margin_found = {}
$margin_tapped = {}
$margin_flagged = {}
class DRC::DRCLayer
  def output(*args)
    name = args[0].to_s
    $margin_names[name] = true
    margin_record(name) if $margin_rules.include?(name) && !$margin_tapped[name]
    nil
  end

  def margin_record(name)
    data = self.data
    unless data.is_a?(RBA::EdgePairs)
      raise "drc_margins: #{name} gives #{data.class.name}, not edge pairs"
    end
    entry = ($margin_found[name] ||= { 'pairs' => 0, 'min_dbu' => nil, 'at_dbu' => nil })
    data.each do |pair|
      distance = pair.distance
      entry['pairs'] += 1
      next unless entry['min_dbu'].nil? || distance < entry['min_dbu']
      entry['min_dbu'] = distance
      center = pair.bbox.center
      entry['at_dbu'] = [center.x, center.y]
    end
    self
  end

  # On the tapped lines of the copy: the check records its pairs (its output then
  # records nothing), the branch counts its shapes.
  def margin_tap(name)
    $margin_tapped[name] = true
    margin_record(name)
  end

  def margin_branch(name)
    $margin_flagged[name] = ($margin_flagged[name] || 0) + self.data.count
    self
  end
end

"""

# Appended to the copy's main deck: it runs only if every deck finished.
FINISH = """
# drc_margins: what the hook saw.
File.write($margin_out, JSON.generate({ 'dbu' => dbu, 'names' => $margin_names.keys,
                                        'found' => $margin_found, 'seal' => $margin_seal,
                                        'flagged' => $margin_flagged }))
"""


def load_table(path=TABLE):
    table = json.loads(Path(path).read_text())
    both = set(table["rules"]) & set(table["not_measured"])
    if both:
        raise ValueError(f"{path}: {sorted(both)} both measured and not measured")
    return table


def window_nm(value_nm, window):
    """W for a rule value: a share of it, kept between the two bounds (integer nm)."""
    return max(window["min_nm"], min(window["max_nm"], value_nm * window["share_pct"] // 100))


def _nm(value_um):
    return round(value_um * 1000)


def _tap(text, name, deck):
    """text, the source of deck, with the lines of rule name tapped (see TAPS)."""
    def line(pattern, shape, note=""):
        found = list(re.finditer(pattern, text, re.M))
        if len(found) != 1:
            raise ValueError(f"drc_margins: {deck}: {name}: {len(found)} lines read "
                             f"'{shape}'{note}, not one; keep the line in that shape or "
                             f"update TAPS in drc_margins.py")
        return found[0]

    quoted = f"['\"]{re.escape(name)}['\"]"
    output = line(rf"^[ \t]*(?P<join>\w+)\.output\([ \t]*{quoted}", f"<join>.output('{name}'")
    join = line(rf"^[ \t]*{output['join']} = (?P<check>\w+)\.join\((?P<branch>\w+)\)[ \t]*$",
                f"{output['join']} = <check>.join(<branch>)", " with no '#'")
    check = line(rf"^[ \t]*{join['check']} = [^#\n]+(?P<polygons>\.polygons)[ \t]*$",
                 f"{join['check']} = <expr>.polygons", " with no '#'")
    # Inserted from the end of the text back, so neither moves the other's offset.
    for at, tap in sorted([(join.end("branch"), f'.margin_branch("{name}")'),
                           (check.start("polygons"), f'.margin_tap("{name}")')], reverse=True):
        text = text[:at] + tap + text[at:]
    return text


def build_copy(dest, table, drc_dir=DRC_DIR):
    """Copy the deck into dest with the table's keys raised and the TAPS lines tapped;
    return the copy's main deck."""
    dest = Path(dest)
    shutil.copytree(drc_dir / "rule_decks", dest / "rule_decks")
    shutil.copy2(drc_dir / "intm4tm2_decks.rb", dest / "intm4tm2_decks.rb")
    main = dest / "intm4tm2.drc"
    main.write_text(HOOK + (drc_dir / "intm4tm2.drc").read_text() + FINISH)

    tech_path = dest / "rule_decks" / TECH_JSON
    tech = json.loads(tech_path.read_text())
    values = tech["rules"]
    for key in sorted({rule["key"] for rule in table["rules"].values()}):
        value_nm = _nm(float(values[key]))
        values[key] = (value_nm + window_nm(value_nm, table["window"])) / 1000
    tech_path.write_text(json.dumps(tech, indent=4) + "\n")

    for name, deck in TAPS.items():
        path = dest / "rule_decks" / deck
        path.write_text(_tap(path.read_text(), name, deck))
    with open(dest / "rule_decks" / "layers_def.drc", "a") as handle:
        handle.write("\n# drc_margins: whether there is a seal ring.\n"
                     "$margin_seal = !edgeseal_drw.is_empty?\n")
    return main


def _run_copy(main, layout, topcell, workdir, threads, table, env):
    rules_file = workdir / "rules.json"
    rules_file.write_text(json.dumps(sorted(table["rules"])))
    raw = workdir / "seen.json"
    klayout = shutil.which("klayout", path=(env or {}).get("PATH"))
    if klayout is None:
        raise RuntimeError("klayout binary not on PATH")
    cmd = [klayout, "-b", "-r", str(main),
           "-rd", f"input={layout}", "-rd", f"topcell={topcell}",
           "-rd", f"report={workdir / 'copy.lyrdb'}",
           "-rd", f"threads={threads}", "-rd", "run_mode=flat",
           "-rd", f"deck={','.join(table['decks'])}",
           "-rd", f"margin_rules_file={rules_file}", "-rd", f"margin_out={raw}"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", env=env)
    if result.returncode != 0 or not raw.exists():
        raise RuntimeError(f"the deck copy failed (exit {result.returncode})\n"
                           f"{result.stdout[-2000:]}")
    return json.loads(raw.read_text())


def _rows(table, seen, tech_values):
    dbu = seen["dbu"]
    rows = []
    for name, rule in table["rules"].items():
        value_nm = _nm(float(tech_values[rule["key"]]))
        floor_nm = value_nm + rule.get("offset_nm", 0)
        found = seen["found"][name]
        row = {"rule": name, "key": rule["key"], "kind": rule["kind"],
               "floor_um": floor_nm / 1000,
               "window_um": window_nm(value_nm, table["window"]) / 1000,
               "pairs": found["pairs"], "thinnest_um": None, "margin_nm": None,
               "margin_pct": None, "at_um": None,
               "flagged_shapes": seen["flagged"].get(name, 0),
               "unmeasured": "no seal ring" if rule.get("needs_seal") and not seen["seal"]
               else None}
        if found["min_dbu"] is not None:
            thinnest_nm = round(found["min_dbu"] * dbu * 1000, 3)
            margin_nm = round(thinnest_nm - floor_nm, 3)
            row.update(thinnest_um=round(thinnest_nm / 1000, 6), margin_nm=margin_nm,
                       margin_pct=round(100 * margin_nm / floor_nm, 1),
                       at_um=[round(c * dbu, 6) for c in found["at_dbu"]])
        rows.append(row)
    rows.sort(key=lambda r: (not r["flagged_shapes"], r["margin_nm"] is None,
                             r["unmeasured"] is not None,
                             r["margin_nm"] if r["margin_nm"] is not None else 0, r["rule"]))
    return rows


def measure(layout, out_dir, topcell="TOP", threads=1, table_path=TABLE, drc_dir=DRC_DIR,
            workdir=None, env=None):
    """Measure the margins of layout; write margins.json and margins.txt to out_dir and
    return the record. Raises when a table rule never reached the hook (a renamed rule,
    a deck left out), so a rule cannot drop out of the list silently."""
    table = load_table(table_path)
    tech_values = json.loads((Path(drc_dir) / "rule_decks" / TECH_JSON).read_text())["rules"]
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        tmp = Path(tmp)
        main = build_copy(tmp / "drc", table, Path(drc_dir))
        seen = _run_copy(main, Path(layout).resolve(), topcell, tmp, threads, table, env)

    # A rule that reached its output was recorded, at the output or, since the output
    # reads the join and the join the check, on its tapped check; so every table rule
    # past this check has its entry in seen["found"], which _rows reads directly.
    names = set(seen["names"])
    missing = sorted(set(table["rules"]) - names)
    if missing:
        raise RuntimeError(f"rules of drc_margins.json the deck no longer measures: "
                           f"{', '.join(missing)}")
    record = {
        "format": FORMAT, "layout": Path(layout).name, "topcell": topcell,
        "seal_ring": seen["seal"],
        "window": table["window"], "decks": table["decks"],
        "rules": _rows(table, seen, tech_values),
        "not_measured": table["not_measured"], "decks_not_run": table["decks_not_run"],
        "unclassified": sorted(names - set(table["rules"]) - set(table["not_measured"])),
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "margins.json").write_text(json.dumps(record, indent=2) + "\n")
    (out_dir / "margins.txt").write_text(format_table(record))
    return record


def format_table(record):
    window = record["window"]
    lines = [f"DRC margins of {record['layout']} (top cell {record['topcell']}), "
             "flagged rules first, then the smallest margin.",
             f"The window W of a rule is {window['share_pct']}% of its value, at least "
             f"{window['min_nm']} nm and at most {window['max_nm']} nm.",
             "A rule with nothing inside its window reads \">= W nm\". A negative margin is a",
             "violation the main DRC reports too, and so is a rule that reads \"flagged\": the",
             "deck flags its shapes whatever the distance (an opening or a MIM not wholly on",
             "its metal, a slit over a MIM).", ""]
    if not record["seal_ring"]:
        lines[-1:-1] = ["The layout has no seal ring, so the rules measured against it measure "
                        "nothing."]
    header = ("Rule", "Floor um", "Thinnest um", "Margin nm", "Margin %", "Pairs", "At um")
    body = []
    for row in record["rules"]:
        measured = row["margin_nm"] is not None
        status = ("flagged" if row["flagged_shapes"] else row["unmeasured"] or
                  (f"{row['margin_nm']:g}" if measured
                   else f">= {round(row['window_um'] * 1000)}"))
        plain = measured and not row["flagged_shapes"] and not row["unmeasured"]
        body.append((row["rule"], f"{row['floor_um']:.3f}",
                     f"{row['thinnest_um']:.3f}" if measured else "-", status,
                     f"{row['margin_pct']:.1f}" if plain else "-", str(row["pairs"]),
                     "{:.3f}, {:.3f}".format(*row["at_um"]) if measured else "-"))
    widths = [max(len(r[i]) for r in [header, *body]) for i in range(len(header))]
    for cells in [header, *body]:
        lines.append("  ".join([cells[0].ljust(widths[0])]
                               + [c.rjust(w) for c, w in zip(cells[1:-1], widths[1:-1])]
                               + [cells[-1]]).rstrip())
    lines += ["", "Not measured:"]
    reasons = {}
    for name, reason in record["not_measured"].items():
        reasons.setdefault(reason, []).append(name)
    lines += [f"  {reason}: {', '.join(names)}" for reason, names in reasons.items()]
    lines += [f"  deck {deck}: {reason}" for deck, reason in record["decks_not_run"].items()]
    if record["unclassified"]:
        lines += ["", f"Rules the table does not classify: {', '.join(record['unclassified'])}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="How close a layout comes to each "
                                                 "interposer DRC rule")
    parser.add_argument("input", help="input layout (GDS/OAS)")
    parser.add_argument("-o", "--output", required=True,
                        help="directory for margins.json and margins.txt")
    parser.add_argument("--topcell", default="TOP")
    parser.add_argument("--threads", type=int, default=1,
                        help="thread count handed to the deck copy as $threads (default 1)")
    args = parser.parse_args(argv)
    record = measure(args.input, args.output, topcell=args.topcell, threads=args.threads)
    sys.stdout.write(format_table(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
