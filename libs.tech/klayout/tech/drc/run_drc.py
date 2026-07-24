# SPDX-License-Identifier: Apache-2.0
"""
Interposer PDK DRC runner.

Runs interposer DRC decks via KLayout batch mode with support for
selective deck execution, parallel runs, and report merging.
"""

import argparse
import os
import shlex
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
import logging
import klayout.db
from datetime import datetime, timezone
import time
from subprocess import check_call, CalledProcessError, run as _run
import concurrent.futures
import traceback
from typing import Dict, List, Set, Union, Tuple

# Available DRC decks (must match keys in intm4tm2.drc all_decks hash)
AVAILABLE_DECKS = [
    'offgrid', 'angle', 'forbidden',
    'metaln', 'metalnfiller',
    'via4', 'topvia1',
    'topmetal1', 'topmetal1filler',
    'topvia2', 'topmetal2', 'topmetal2filler',
    'passiv', 'pad', 'copperpillar', 'solderbump', 'ubm_floor',
    'sealring', 'mim', 'metalslits', 'pin', 'lbe', 'tsv_g',
    'density',
]

# Decks excluded from the default "all" run (must match intm4tm2.drc).
# Density carries global minimum-density rules that fail on partial layouts;
# opt in with --density or an explicit --deck density.
DEFAULT_SKIP_DECKS = {'density'}

# Decks that have been moved out of the interposer PDK. Recognised here so
# that a stale CLI invocation prints a useful redirect instead of "unknown".
RELOCATED_DECKS = {
    'assembly': "Promoted to the ADK. Use adk/klayout/drc/run_drc.py "
                "with --interposer-adapter <name>.",
}


# ================================================================
# -------------------- XML REPORT UTILITIES ----------------------
# ================================================================


def get_rules_with_violations(results_database: Union[str, Path]) -> Set[str]:
    """
    Parse a KLayout RDB file and return rule names that have violations.
    """
    results_database = Path(results_database)
    if not results_database.is_file():
        logging.error(f"Results database not found: {results_database}")
        raise FileNotFoundError(f"No such file: {results_database}")

    try:
        tree = ET.parse(results_database)
        root = tree.getroot()
    except ET.ParseError as e:
        logging.error(f"Failed to parse results database: {results_database}")
        raise e

    violating_rules = set()
    for rule in root[7]:  # root[7] : List rules with violations
        violating_rules.add(f"{rule[1].text}".replace("'", ""))

    return violating_rules


def _get_cell_key(cell: ET.Element) -> str:
    """Return a unique key for a <cell> element as name|variant."""
    name_elem = cell.find("name")
    cell_variant = cell.find("variant")
    if name_elem is None or not name_elem.text:
        return ""
    if cell_variant is not None and cell_variant.text:
        return f"{name_elem.text.strip()}|{cell_variant.text.strip()}"
    return f"{name_elem.text.strip()}|"


def _merge_categories(base_categories: ET.Element, new_root: ET.Element):
    categories = new_root.find("categories")
    if categories is not None:
        for category in categories.findall("category"):
            base_categories.append(category)


def _merge_cells(base_cells: ET.Element, new_root: ET.Element, existing_keys: set):
    cells = new_root.find("cells")
    if cells is not None:
        for cell in cells.findall("cell"):
            key = _get_cell_key(cell)
            if key and key not in existing_keys:
                base_cells.append(cell)
                existing_keys.add(key)


def _merge_items(base_items: ET.Element, new_root: ET.Element):
    items = new_root.find("items")
    if items is not None:
        for item in items.findall("item"):
            base_items.append(item)


def _group_cells_by_base(base_cells: ET.Element) -> Dict[str, List[Tuple[ET.Element, str]]]:
    grouped = {}
    for cell in base_cells.findall("cell"):
        name_elem = cell.find("name")
        variant_elem = cell.find("variant")
        if name_elem is None or not name_elem.text:
            continue
        base_name = name_elem.text.strip()
        variant = (variant_elem.text.strip() if (variant_elem is not None and variant_elem.text) else "")
        grouped.setdefault(base_name, []).append((cell, variant))
    return grouped


def _rename_plain_variants(base_cells: ET.Element, base_items: ET.Element) -> None:
    """Rename plain variants to :org if other variants exist."""
    grouped = _group_cells_by_base(base_cells)
    rename_map = {}

    for base_name, variants in grouped.items():
        unique_variants = set(v for _, v in variants)
        if "" in unique_variants and len(unique_variants) > 1:
            for cell, variant in variants:
                if variant == "":
                    name_elem = cell.find("name")
                    if name_elem is not None and name_elem.text:
                        old = name_elem.text.strip()
                        new = f"{old}:org"
                        rename_map[old] = new
                        name_elem.text = new

    for ref in base_cells.findall(".//ref"):
        parent_elem = ref.find("parent")
        if parent_elem is not None and parent_elem.text:
            pname = parent_elem.text.strip()
            if pname in rename_map:
                parent_elem.text = rename_map[pname]

    for item in base_items.findall("item"):
        cell_elem = item.find("cell")
        if cell_elem is not None and cell_elem.text:
            cname = cell_elem.text.strip()
            if cname in rename_map:
                cell_elem.text = rename_map[cname]


def merge_klayout_drc_reports(input_files: List[str], output_file: str):
    """Merge multiple KLayout DRC report XML files into one."""
    base_tree = ET.parse(input_files[0])
    base_root = base_tree.getroot()

    base_categories = base_root.find("categories")
    base_cells = base_root.find("cells")
    base_items = base_root.find("items")

    if base_categories is None or base_cells is None or base_items is None:
        raise ValueError(
            f"Base file '{input_files[0]}' is missing required elements."
        )

    existing_keys = {_get_cell_key(c) for c in base_cells.findall("cell")}
    for file_path in input_files[1:]:
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            _merge_categories(base_categories, root)
            _merge_cells(base_cells, root, existing_keys)
            _merge_items(base_items, root)
        except ET.ParseError as e:
            logging.error(f"Error parsing '{file_path}': {e}. Skipping.")

    _rename_plain_variants(base_cells, base_items)
    base_tree.write(output_file, encoding="utf-8", xml_declaration=True)


# ================================================================
# -------------------- LAYOUT UTILITIES --------------------------
# ================================================================


def get_top_cell_names(gds_path: str) -> List[str]:
    """Get top cell names from a GDS file."""
    layout = klayout.db.Layout()
    layout.read(gds_path)
    return [t.name for t in layout.top_cells()]


def check_klayout_version():
    """Check that KLayout >= 0.29.11 is available."""
    try:
        klayout_version_output = _run(
            ["klayout", "-b", "-v"], capture_output=True, text=True
        ).stdout.strip()
    except Exception as e:
        logging.error(f"Error while checking KLayout version: {e}")
        sys.exit(1)

    if not klayout_version_output:
        logging.error("KLayout not found. Make sure it is installed and in PATH.")
        sys.exit(1)

    version_str = klayout_version_output.split()[-1]
    version_parts = version_str.split(".")

    try:
        major = int(version_parts[0])
        minor = int(version_parts[1]) if len(version_parts) > 1 else 0
        patch = int(version_parts[2]) if len(version_parts) > 2 else 0
    except ValueError:
        logging.error(f"Failed to parse KLayout version: '{klayout_version_output}'")
        sys.exit(1)

    if (major, minor, patch) < (0, 29, 11):
        logging.error(f"Minimum KLayout version is 0.29.11. Found: {version_str}")
        sys.exit(1)

    logging.info(f"KLayout version: {version_str}")


def check_layout_path(layout_path: str) -> str:
    """Validate layout file exists and is GDS/OAS format. Returns absolute path."""
    path = Path(layout_path)

    if not path.is_file():
        logging.error(f"Layout file '{layout_path}' does not exist.")
        sys.exit(1)

    if not layout_path.lower().endswith((".gds", ".gds.gz", ".gds2", ".gds2.gz", ".oas")):
        logging.error(f"Layout '{layout_path}' is not GDS or OAS format.")
        sys.exit(1)

    return str(path.resolve())


def get_run_top_cell_name(topcell_arg: str, layout_path: str) -> str:
    """Resolve top cell name: use provided value or auto-detect from layout."""
    if topcell_arg:
        return topcell_arg

    top_cells = get_top_cell_names(layout_path)
    if len(top_cells) > 1:
        logging.error("Layout has multiple top cells. Specify one with --topcell.")
        sys.exit(1)
    elif not top_cells:
        logging.error("No top cell found in layout.")
        sys.exit(1)
    return top_cells[0]


# ================================================================
# -------------------- DRC EXECUTION -----------------------------
# ================================================================


def run_deck(drc_script: str, deck_name: str, layout_path: str,
             topcell: str, run_dir: Path, threads: int = 4,
             run_mode: str = "tiling",
             extra_defines: Dict[str, str] = None) -> str:
    """Run a single DRC deck (or all) via klayout -b.

    extra_defines are forwarded as additional `-rd key=value` pairs
    (e.g. {"density_sanity": "true"}).

    Returns path to the generated .lyrdb report.
    """
    layout_stem = Path(layout_path).stem
    report_path = run_dir / f"{layout_stem}_{topcell}_{deck_name}.lyrdb"

    # Build argv directly (no shell): KLayout accepts each `-rd name=value` as a
    # separate token, so layout/report paths and the GDS-derived topcell name are
    # passed verbatim and cannot be word-split or shell-interpreted.
    cmd = [
        "klayout", "-b", "-r", str(drc_script),
        "-rd", f"input={layout_path}",
        "-rd", f"topcell={topcell}",
        "-rd", f"report={report_path}",
        "-rd", f"threads={threads}",
        "-rd", f"run_mode={run_mode}",
    ]
    if deck_name != "all":
        cmd += ["-rd", f"deck={deck_name}"]
    for key, value in (extra_defines or {}).items():
        cmd += ["-rd", f"{key}={value}"]

    logging.info(f"Running deck '{deck_name}' on {Path(layout_path).name} (topcell: {topcell})")
    logging.debug("Command: %s", " ".join(shlex.quote(c) for c in cmd))

    try:
        check_call(cmd)
    except CalledProcessError as e:
        logging.error(f"Deck '{deck_name}' failed with exit code {e.returncode}")
        raise

    return str(report_path)


def check_drc_results(report_files: List[str], run_dir: Path,
                      layout_path: str, topcell: str):
    """Check and report DRC results, merge reports if multiple."""
    report_files = [Path(f) for f in report_files if Path(f).is_file()]

    if not report_files:
        logging.error("No result databases generated. Check the logs.")
        sys.exit(1)

    if len(report_files) > 1:
        layout_stem = Path(layout_path).stem
        merged_report = run_dir / f"{layout_stem}_{topcell}_full.lyrdb"
        merge_klayout_drc_reports(
            [str(f) for f in report_files], str(merged_report)
        )
        # Remove partial reports
        for f in report_files:
            if f != merged_report and f.exists():
                os.remove(f)
        report_path = merged_report
    else:
        report_path = report_files[0]

    violating_rules = get_rules_with_violations(report_path)

    if violating_rules:
        logging.warning("=" * 70)
        logging.warning("DRC FAILED: Violations detected")
        logging.warning("=" * 70)
        logging.warning(f"Violated rules: {sorted(violating_rules)}")
    else:
        logging.info("=" * 70)
        logging.info("DRC PASSED: No violations detected")
        logging.info("=" * 70)

    logging.info(f"Report: {report_path}")
    return violating_rules


# ================================================================
# -------------------- CLI & MAIN --------------------------------
# ================================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run interposer PDK DRC checks via KLayout",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available decks: {', '.join(AVAILABLE_DECKS)}

Examples:
  %(prog)s --path design.gds                       # all decks
  %(prog)s --path design.gds --deck pad             # pad only
  %(prog)s --path design.gds --deck lbe --deck pad  # LBE + pad, merged
  %(prog)s --path design.gds --mp 5                 # parallel (one per deck)
""",
    )

    parser.add_argument(
        "--path", type=str, required=True,
        help="Path to the input GDS/OAS file.",
    )
    parser.add_argument(
        "--topcell", type=str, default=None,
        help="Top-level cell name (auto-detected if omitted).",
    )
    parser.add_argument(
        "--deck", action="append", default=[],
        help=f"Deck(s) to run (repeatable). Available: {', '.join(AVAILABLE_DECKS)}. Default: all.",
    )
    parser.add_argument(
        "--run_dir", type=str, default=None,
        help="Output directory for reports (default: timestamped subdir).",
    )
    parser.add_argument(
        "--threads", type=int, default=4,
        help="Threads per KLayout invocation (default: 4).",
    )
    parser.add_argument(
        "--mp", type=int, default=1,
        help="Parallel deck execution workers (default: 1).",
    )
    parser.add_argument(
        "--run_mode", type=str, choices=["tiling", "deep", "flat"],
        default="tiling",
        help="KLayout execution mode (default: tiling).",
    )
    parser.add_argument(
        "--density", action="store_true",
        help="Also run the density deck (skipped by default: its global "
             "minimum-density rules only make sense on full-chip layouts).",
    )
    parser.add_argument(
        "--density_sanity", action="store_true",
        help="Enable the DEN.BND.* boundary sanity rules of the density deck.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # Timestamped run directory
    now_str = datetime.now(timezone.utc).strftime("drc_run_%Y_%m_%d_%H_%M_%S")
    if args.run_dir in ["pwd", "", None]:
        run_dir = Path.cwd().resolve() / now_str
    else:
        run_dir = Path(args.run_dir).resolve()
    os.makedirs(run_dir, exist_ok=True)

    # Logging to file + console
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[
            logging.FileHandler(run_dir / f"{now_str}.log"),
            logging.StreamHandler(),
        ],
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%d-%b-%Y %H:%M:%S",
    )

    time_start = time.time()

    # Validate inputs
    check_klayout_version()
    layout_path = check_layout_path(args.path)
    topcell = get_run_top_cell_name(args.topcell, layout_path)

    # Resolve DRC script path
    drc_script = str(Path(__file__).resolve().parent / "intm4tm2.drc")
    if not Path(drc_script).is_file():
        logging.error(f"DRC script not found: {drc_script}")
        sys.exit(1)

    # Validate requested decks
    decks_to_run = args.deck if args.deck else []
    for d in decks_to_run:
        if d in RELOCATED_DECKS:
            logging.error(
                f"Deck '{d}' is no longer in the interposer PDK. "
                f"{RELOCATED_DECKS[d]}"
            )
            sys.exit(1)
        if d not in AVAILABLE_DECKS:
            logging.error(f"Unknown deck '{d}'. Available: {', '.join(AVAILABLE_DECKS)}")
            sys.exit(1)

    # Defines forwarded to every deck invocation
    extra_defines = {}
    if args.density_sanity:
        extra_defines["density_sanity"] = "true"

    # Execution strategy
    report_files = []

    if not decks_to_run:
        # No specific decks: single invocation runs all default decks
        # (the runset itself skips DEFAULT_SKIP_DECKS in its "all" path).
        if args.mp > 1:
            # Parallel: one invocation per deck
            decks_to_run = [d for d in AVAILABLE_DECKS if d not in DEFAULT_SKIP_DECKS]
            if args.density:
                decks_to_run.append('density')
            logging.info(f"Parallel execution: {len(decks_to_run)} decks, {args.mp} workers")
        else:
            # Single invocation, all default decks
            logging.info("Running all default decks in single invocation")
            report = run_deck(drc_script, "all", layout_path, topcell,
                              run_dir, args.threads, args.run_mode,
                              extra_defines)
            report_files.append(report)
            if args.density:
                report = run_deck(drc_script, "density", layout_path, topcell,
                                  run_dir, args.threads, args.run_mode,
                                  extra_defines)
                report_files.append(report)
    elif args.density and 'density' not in decks_to_run:
        decks_to_run.append('density')

    if decks_to_run and not report_files:
        if args.mp > 1 and len(decks_to_run) > 1:
            # Parallel execution
            with concurrent.futures.ProcessPoolExecutor(max_workers=args.mp) as executor:
                futures = {
                    executor.submit(
                        run_deck, drc_script, deck, layout_path, topcell,
                        run_dir, args.threads, args.run_mode, extra_defines
                    ): deck
                    for deck in decks_to_run
                }
                for future in concurrent.futures.as_completed(futures):
                    deck = futures[future]
                    try:
                        report_files.append(future.result())
                    except Exception as e:
                        logging.error(f"Deck '{deck}' failed: {e}")
                        traceback.print_exc()
        else:
            # Sequential execution
            for deck in decks_to_run:
                report = run_deck(drc_script, deck, layout_path, topcell,
                                  run_dir, args.threads, args.run_mode,
                                  extra_defines)
                report_files.append(report)

    # Check results
    violations = check_drc_results(report_files, run_dir, layout_path, topcell)

    elapsed = time.time() - time_start
    logging.info(f"Total DRC time: {elapsed:.2f}s")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
