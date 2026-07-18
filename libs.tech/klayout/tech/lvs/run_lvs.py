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
"""

import argparse
import os
import shlex
import sys
import logging
import klayout.db
from datetime import datetime, timezone
from subprocess import check_call, run as _run
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
        sys.exit(1)

    klayout_v_ = klayout_v_.split("\n")[0].strip()

    if klayout_v_ == "":
        logging.error("Klayout is not found. Please make sure klayout is installed.")
        sys.exit(1)

    version_str = klayout_v_.split(" ")[-1]
    # Tolerate dev/packaging suffixes on each component (e.g. '0.29.11-dev').
    try:
        parts = [int(p.split("-")[0]) for p in version_str.split(".")]
    except (ValueError, IndexError):
        logging.error(f"Was not able to parse klayout version: '{klayout_v_}'")
        sys.exit(1)

    if not 1 <= len(parts) <= 3:
        logging.error("Was not able to get klayout version properly.")
        sys.exit(1)

    major = parts[0]
    minor = parts[1] if len(parts) > 1 else 0
    patch = parts[2] if len(parts) > 2 else 0
    if (major, minor, patch) < (0, 29, 0):
        logging.error("Prerequisites at a minimum: KLayout 0.29.0")
        logging.error(
            "Using this klayout version has not been assessed. Limits are unknown"
        )
        sys.exit(1)

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
        sys.exit(1)

    if ".gds" not in layout_path and ".oas" not in layout_path:
        logging.error(
            f"Layout {layout_path} is not in GDS2 or OASIS format, please recheck."
        )
        sys.exit(1)

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
        if len(layout_topcells) > 1:
            logging.error(
                "Layout has multiple topcells. Use --topcell to determine which topcell you want."
            )
            sys.exit(1)
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
        sys.exit(1)

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

    missing = [f for f in results_files if not os.path.isfile(f)]
    if missing:
        logging.error(
            f"Klayout did not generate the expected results: {missing}. Please check run logs"
        )
        sys.exit(1)


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
    list
        Paths of the results the run is expected to produce.

    """

    logging.info(
        f'Running IntM4TM2 interposer LVS on design {path} on cell {sws["topcell"]}'
    )

    layout_base_name = os.path.splitext(os.path.basename(path))[0]
    new_sws = sws.copy()
    report_path = os.path.join(run_dir, f"{layout_base_name}.lvsdb")
    log_path = os.path.join(run_dir, f"{layout_base_name}.log")
    ext_net_path = os.path.join(run_dir, f"{layout_base_name}_extracted.cir")
    new_sws["report"] = report_path
    new_sws["log"] = log_path
    new_sws["target_netlist"] = ext_net_path

    # Build argv directly (no shell): each `-rd key=value` is one token, so a
    # spaceful run dir / layout path and the derived report/log/netlist paths
    # are passed verbatim and cannot be word-split or shell-interpreted.
    cmd = ["klayout", "-b", "-r", str(lvs_file)] + build_switches_args(new_sws)
    logging.debug("Command: %s", " ".join(shlex.quote(c) for c in cmd))
    check_call(cmd)

    # The KLayout LVS database is only written when a schematic compare runs;
    # connectivity-only runs produce the extracted netlist.
    expected = [ext_net_path]
    if "schematic" in sws:
        expected.append(report_path)

    return expected


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
        sys.exit(1)

    # Check layout type
    layout_path = check_layout_type(layout_path)

    # Check optional reference netlist existence
    netlist_path = arguments["--netlist"]
    if netlist_path:
        netlist_path = os.path.abspath(os.path.expanduser(netlist_path))
        if not os.path.exists(netlist_path):
            logging.error(
                f"The input netlist file path {netlist_path} doesn't exist, please recheck."
            )
            sys.exit(1)
    else:
        logging.info("No reference netlist provided - connectivity check only.")

    lvs_rule_deck = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "intm4tm2.lvs"
    )

    # Get run switches
    switches = generate_klayout_switches(arguments, layout_path, netlist_path)

    # Run LVS check
    results_files = run_check(lvs_rule_deck, layout_path, lvs_run_dir, switches)

    # Check run
    check_lvs_results(results_files)


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

    os.makedirs(lvs_run_dir, exist_ok=True)

    # logs format
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[
            logging.FileHandler(os.path.join(lvs_run_dir, "{}.log".format(now_str))),
            logging.StreamHandler(),
        ],
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%d-%b-%Y %H:%M:%S",
    )

    # Start of execution time
    t0 = time.time()

    # Calling main function
    main(lvs_run_dir, arguments)

    #  End of execution time
    logging.info("Total execution time {}s".format(time.time() - t0))
