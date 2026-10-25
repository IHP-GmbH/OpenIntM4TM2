# ==========================================================================
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
# SPDX-License-Identifier: Apache-2.0
# ==========================================================================

"""Run IntM4TM2 interposer LVS (KLayout).

The LVS deck extracts metal-stack connectivity, MIM capacitor devices
(cap_cmim, from the MIM/Vmim layers between Metal5 and TopMetal1), names
nets from text labels, and checks label consistency (opens/shorts). A
reference netlist is optional: when provided, KLayout's netlist compare
runs in addition to the label checks. Reference netlists carry cap_cmim
devices as 'C1 <top> <btm> cap_cmim w=.. l=.. m=..' (the value-first
'C1 <top> <btm> <value> $[cap_cmim] w=.. l=..' form is accepted too).

The command line is parsed with the standard library ``argparse`` (no
third-party dependency, so a standalone PDK checkout runs as-is); invoke
with ``--help`` for the full option list.

Exit 0 means the requested scoped check completed and passed, 1 means
completed checks found an open, short or reference mismatch, and 2 means
invalid input, execution failure, incomplete evidence or an empty label census.
"""

import argparse
import os
import re
import shlex
import sys
import logging
from datetime import datetime, timezone
from subprocess import STDOUT, PIPE, SubprocessError, run as _run
import time


def check_klayout_version():
    """
    Check klayout version and makes sure it would work with the LVS.
    """
    # ======= Checking Klayout version =======
    try:
        klayout_v_ = _run(
            ["klayout", "-b", "-v"], capture_output=True, text=True
        ).stdout
    except Exception as e:
        logging.error(f"Error while checking KLayout version: {e}")
        sys.exit(2)

    klayout_v_ = klayout_v_.split("\n")[0].strip()

    if klayout_v_ == "":
        logging.error("Klayout is not found. Please make sure klayout is installed.")
        sys.exit(2)

    version_str = klayout_v_.split(" ")[-1]
    # Tolerate dev/packaging suffixes on each component (e.g. '0.29.11-dev').
    try:
        parts = [int(p.split("-")[0]) for p in version_str.split(".")]
    except (ValueError, IndexError):
        logging.error(f"Was not able to parse klayout version: '{klayout_v_}'")
        sys.exit(2)

    if not 1 <= len(parts) <= 3:
        logging.error("Was not able to get klayout version properly.")
        sys.exit(2)

    major = parts[0]
    minor = parts[1] if len(parts) > 1 else 0
    patch = parts[2] if len(parts) > 2 else 0
    if (major, minor, patch) < (0, 29, 0):
        logging.error("Prerequisites at a minimum: KLayout 0.29.0")
        logging.error(
            "Using this klayout version has not been assessed. Limits are unknown"
        )
        sys.exit(2)

    logging.info(f"Your Klayout version is: {klayout_v_}")


def check_layout_type(layout_path):
    """
    Checks if the layout provided is GDS2 or OASIS. Otherwise, kill the process.

    Parameters
    ----------
    layout_path : string
        string that represent the path of the layout.

    Returns
    -------
    string
        string that represent full absolute layout path.
    """

    if not os.path.isfile(layout_path):
        logging.error(
            f"GDS file path {layout_path} provided doesn't exist or not a file."
        )
        sys.exit(2)

    if ".gds" not in layout_path and ".oas" not in layout_path:
        logging.error(
            f"Layout {layout_path} is not in GDS2 or OASIS format, please recheck."
        )
        sys.exit(2)

    return layout_path


def get_top_cell_names(gds_path):
    """
    Get the top cell names from the GDS file.

    Parameters
    ----------
    gds_path : string
        Path to the target GDS file.

    Returns
    -------
    List of string
        Names of the top cell in the layout.
    """
    import klayout.db

    layout = klayout.db.Layout()
    layout.read(gds_path)
    top_cells = [t.name for t in layout.top_cells()]

    return top_cells


def get_run_top_cell_name(arguments, layout_path):
    """
    Get the top cell name to use for running. If it's provided by the user, we use the user input.
    If not, we get it from the GDS file.

    Parameters
    ----------
    arguments : dict
        Dictionary that holds the user inputs for the script (parsed CLI args).
    layout_path : string
        Path to the target layout.

    Returns
    -------
    string
        Name of the topcell to use in run.

    """

    if arguments["--topcell"]:
        topcell = arguments["--topcell"]
    else:
        layout_topcells = get_top_cell_names(layout_path)
        if len(layout_topcells) != 1:
            logging.error(
                "Layout must have exactly one topcell, or select one with --topcell."
            )
            sys.exit(2)
        else:
            topcell = layout_topcells[0]

    return topcell


def generate_klayout_switches(arguments, layout_path, netlist_path):
    """
    Parse all the args from input to prepare switches for LVS run.

    Parameters
    ----------
    arguments : dict
        Dictionary that holds the arguments used by user in the run command.
        This is populated from the parsed CLI arguments (argparse).
    layout_path : string
        Path to the layout file that we will run LVS on.
    netlist_path : string or None
        Path to the optional reference netlist; None for connectivity-only runs.

    Returns
    -------
    dict
        Dictionary that represent all run switches passed to klayout.
    """
    switches = dict()

    if arguments["--run_mode"] in ["flat", "deep"]:
        run_mode = arguments["--run_mode"]
    else:
        logging.error("Allowed klayout modes are (flat , deep) only")
        sys.exit(2)

    switches = {
        "run_mode": run_mode,
        "top_lvl_pins": "false" if arguments.get("--no_top_lvl_pins") else "true",
        "combine_devices": "true" if arguments.get("--combine_devices") else "false",
        "verbose": "true" if arguments.get("--verbose") else "false",
        "topcell": get_run_top_cell_name(arguments, layout_path),
        "input": os.path.abspath(layout_path),
    }

    if netlist_path:
        switches["schematic"] = os.path.abspath(netlist_path)

    return switches


def build_switches_args(sws: dict):
    """
    Build the KLayout ``-rd key=value`` argv tokens from a switch dict.

    Returns a flat list (``["-rd", "k=v", ...]``) so each value is a single
    argv token and no shell quoting is required.

    Parameters
    ----------
    sws : dict
        Dictionary that holds the run switches.
    """
    args = []
    for k, v in sws.items():
        args += ["-rd", f"{k}={v}"]
    return args


def check_lvs_results(results_files: list):
    """
    Checks that the expected run results were generated.

    Parameters
    ----------
    results_files : list
        A list of paths that the LVS run must have produced.
    """

    missing = [f for f in results_files
               if not os.path.isfile(f) or os.path.getsize(f) == 0]
    if missing:
        logging.error(
            f"Klayout did not generate the expected results: {missing}. Please check run logs"
        )
        raise RuntimeError("Missing or empty LVS artifacts")


COMPLETION = "LVS check complete for interposer backend layers and MIM capacitors"
RESULT = re.compile(
    r"^INTERPOSER_LVS_RESULT mode=(reference|connectivity) "
    r"status=(PASS|FAIL|INCONCLUSIVE) labeled_nets=(\d+) opens=(\d+) shorts=(\d+)$",
    re.MULTILINE,
)


def result_exit_code(output, reference):
    """Validate the deck's completed, non-vacuous verdict, never a process exit alone."""
    rows = RESULT.findall(output)
    if (output.count(COMPLETION) != 1 or len(rows) != 1 or
            output.count("INTERPOSER_LVS_RESULT") != 1):
        raise RuntimeError("Missing or ambiguous interposer LVS completion/result")
    mode, status, labeled, opens, shorts = rows[0]
    if mode != ("reference" if reference else "connectivity"):
        raise RuntimeError("Interposer LVS result mode differs from the requested check")
    labeled, opens, shorts = int(labeled), int(opens), int(shorts)
    if (opens != output.count("Connectivity OPEN:") or
            shorts != output.count("Connectivity SHORT:")):
        raise RuntimeError("Interposer LVS finding census disagrees with the log")
    matched = output.count("LVS netlists match")
    mismatched = output.count("LVS netlists do NOT match")
    if (matched + mismatched != int(reference) or
            output.count("No schematic provided - connectivity check only") != int(not reference)):
        raise RuntimeError("Missing or inconsistent interposer reference-comparison result")
    expected = ("INCONCLUSIVE" if not labeled else
                "FAIL" if opens or shorts or mismatched else "PASS")
    if status != expected:
        raise RuntimeError("Interposer LVS verdict contradicts its population/findings")
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[status]


def run_check(lvs_file: str, path: str, run_dir: str, sws: dict):
    """
    Run LVS check.

    Parameters
    ----------
    lvs_file : str
        String that has the file full path to run.
    path : str
        String that holds the full path of the layout.
    run_dir : str
        String that holds the full path of the run location.
    sws : dict
        Dictionary that holds all switches that needs to be passed to the run.

    Returns
    -------
    int
        Scoped verdict: 0 pass, 1 findings, 2 inconclusive.

    """

    logging.info(
        f'Running IntM4TM2 interposer LVS on design {path} on cell {sws["topcell"]}'
    )

    layout_base_name = os.path.splitext(os.path.basename(path))[0]
    new_sws = sws.copy()
    report_path = os.path.join(run_dir, f"{layout_base_name}.lvsdb")
    log_path = os.path.join(run_dir, f"{layout_base_name}.log")
    ext_net_path = os.path.join(run_dir, f"{layout_base_name}_extracted.cir")
    for output in (report_path, log_path, ext_net_path):
        if os.path.lexists(output):
            raise RuntimeError(f"Refusing to reuse LVS output {output}; choose a fresh run directory")
    new_sws["report"] = report_path
    new_sws["log"] = log_path
    new_sws["target_netlist"] = ext_net_path

    # Build argv directly (no shell): each `-rd key=value` is one token, so a
    # spaceful run dir / layout path and the derived report/log/netlist paths
    # are passed verbatim and cannot be word-split or shell-interpreted.
    cmd = ["klayout", "-b", "-r", str(lvs_file)] + build_switches_args(new_sws)
    logging.debug("Command: %s", " ".join(shlex.quote(c) for c in cmd))
    proc = _run(cmd, stdout=PIPE, stderr=STDOUT, text=True)
    with open(log_path, "w") as stream:
        stream.write(proc.stdout)
    print(proc.stdout, end="", flush=True)
    if proc.returncode:
        raise RuntimeError(f"KLayout LVS execution failed with exit {proc.returncode}; see {log_path}")

    # The KLayout LVS database is only written when a schematic compare runs;
    # connectivity-only runs produce the extracted netlist.
    expected = [ext_net_path]
    if "schematic" in sws:
        expected.append(report_path)

    check_lvs_results(expected)
    return result_exit_code(proc.stdout, reference="schematic" in sws)


def main(lvs_run_dir: str, arguments: dict):
    """
    Main function to run the LVS.

    Parameters
    ----------
    lvs_run_dir : str
        String with absolute path of the full run dir.
    arguments : dict
        Dictionary that holds the arguments used by user in the run command.
        This is populated from the parsed CLI arguments (argparse).
    """

    # Check Klayout version
    check_klayout_version()

    # Check layout file existence
    layout_path = arguments["--layout"]
    layout_path = os.path.abspath(os.path.expanduser(layout_path))
    if not os.path.exists(layout_path):
        logging.error(
            f"The input GDS file path {layout_path} doesn't exist, please recheck."
        )
        sys.exit(2)

    # Check layout type
    layout_path = check_layout_type(layout_path)

    # Check optional reference netlist existence
    netlist_path = arguments["--netlist"]
    if netlist_path is not None:
        if not netlist_path:
            logging.error("An explicit --netlist must name a nonempty SPICE file.")
            sys.exit(2)
        netlist_path = os.path.abspath(os.path.expanduser(netlist_path))
        if not os.path.isfile(netlist_path) or os.path.getsize(netlist_path) == 0:
            logging.error(
                f"The input netlist {netlist_path} is missing, empty or not a file."
            )
            sys.exit(2)
    else:
        logging.info("No reference netlist provided - connectivity check only.")

    lvs_rule_deck = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "intm4tm2.lvs"
    )

    # Get run switches
    switches = generate_klayout_switches(arguments, layout_path, netlist_path)

    # Run LVS check
    return run_check(lvs_rule_deck, layout_path, lvs_run_dir, switches)


# ================================================================
# -------------------------- MAIN --------------------------------
# ================================================================


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="run_lvs.py",
        description="Run IntM4TM2 interposer LVS (KLayout).",
    )
    parser.add_argument(
        "--version", action="version", version="RUN LVS: 1.0"
    )
    parser.add_argument(
        "--layout", required=True,
        help="File path of the input GDS/OASIS layout.",
    )
    parser.add_argument(
        "--netlist", default=None,
        help="Optional reference netlist (SPICE). If omitted, a "
             "connectivity-only check is performed.",
    )
    parser.add_argument(
        "--run_dir", default="pwd",
        help="Run directory to save all generated results [default: pwd].",
    )
    parser.add_argument(
        "--topcell", default=None,
        help="Name of the top cell to be used.",
    )
    parser.add_argument(
        "--run_mode", default="deep",
        help="Allowed KLayout mode (flat, deep) [default: deep].",
    )
    parser.add_argument(
        "--no_top_lvl_pins", action="store_true",
        help="Do not create pins for named top-level nets.",
    )
    parser.add_argument(
        "--combine_devices", action="store_true",
        help="Combine parallel devices before compare (e.g. two equal "
             "cap_cmim in parallel become one with m=2).",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable detailed rule execution logs.",
    )
    # Expose the parsed args as a dict keyed by the CLI flag names so the
    # rest of the module reads them unchanged (main(),
    # generate_klayout_switches(), get_run_top_cell_name).
    ns = parser.parse_args()
    arguments = {
        "--layout": ns.layout,
        "--netlist": ns.netlist,
        "--run_dir": ns.run_dir,
        "--topcell": ns.topcell,
        "--run_mode": ns.run_mode,
        "--no_top_lvl_pins": ns.no_top_lvl_pins,
        "--combine_devices": ns.combine_devices,
        "--verbose": ns.verbose,
    }

    # Generate a timestamped run directory name
    now_str = datetime.now(timezone.utc).strftime("lvs_run_%Y_%m_%d_%H_%M_%S")

    if (
        arguments["--run_dir"] == "pwd"
        or arguments["--run_dir"] == ""
        or arguments["--run_dir"] is None
    ):
        lvs_run_dir = os.path.join(os.path.abspath(os.getcwd()), now_str)
    else:
        lvs_run_dir = os.path.abspath(arguments["--run_dir"])

    try:
        os.makedirs(lvs_run_dir, exist_ok=True)
        log_handler = logging.FileHandler(os.path.join(lvs_run_dir, "{}.log".format(now_str)))
    except OSError as exc:
        print(f"Interposer LVS output directory is unusable: {exc}", file=sys.stderr)
        sys.exit(2)

    # logs format
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[
            log_handler,
            logging.StreamHandler(),
        ],
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%d-%b-%Y %H:%M:%S",
    )

    # Start of execution time
    t0 = time.time()

    # Calling main function
    try:
        status = main(lvs_run_dir, arguments)
    except (OSError, RuntimeError, ValueError, ImportError, SubprocessError) as exc:
        logging.error("Interposer LVS is undecidable: %s", exc)
        status = 2

    #  End of execution time
    logging.info("Total execution time {}s".format(time.time() - t0))
    sys.exit(status)
