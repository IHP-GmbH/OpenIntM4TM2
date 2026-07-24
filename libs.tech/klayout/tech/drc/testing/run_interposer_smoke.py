#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Run focused interposer smoke tests.")
    parser.add_argument("--case", action="append", default=[], help="Run only the named smoke case(s).")
    parser.add_argument("--keep", action="store_true", help="Keep per-case output directories.")
    return parser.parse_args()


def main():
    args = parse_args()
    testing_dir = Path(__file__).resolve().parent
    drc_dir = testing_dir.parent
    smoke_manifest = testing_dir / "interposer_smoke_tests.json"
    run_drc = drc_dir / "run_drc.py"
    smoke_runs = testing_dir / "smoke_runs"

    with smoke_manifest.open() as fh:
        manifest = json.load(fh)

    cases = manifest["cases"]
    if args.case:
        wanted = set(args.case)
        cases = [case for case in cases if case["name"] in wanted]

    if not cases:
        print("No smoke cases selected.", file=sys.stderr)
        return 2

    smoke_runs.mkdir(exist_ok=True)
    failures = []

    for case in cases:
        case_dir = smoke_runs / case["name"]
        if case_dir.exists():
            shutil.rmtree(case_dir)

        cmd = [sys.executable, str(run_drc), "--path", str(drc_dir / case["layout"])]
        if case.get("topcell"):
            cmd.extend(["--topcell", case["topcell"]])
        for deck in case["decks"]:
            cmd.extend(["--deck", deck])
        cmd.extend(["--run_dir", str(case_dir)])
        result = subprocess.run(cmd, cwd=drc_dir)

        if result.returncode != case["expect_exit"]:
            failures.append((case["name"], case["expect_exit"], result.returncode))
        elif not args.keep and case_dir.exists():
            shutil.rmtree(case_dir)

    if failures:
        for name, expected, actual in failures:
            print(f"Smoke case '{name}' expected exit {expected} but got {actual}", file=sys.stderr)
        return 1

    print(f"Executed {len(cases)} interposer smoke case(s) successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
