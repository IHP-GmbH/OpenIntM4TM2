# =========================================================================================
# Copyright 2026 IHP PDK Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========================================================================================

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Run the focused interposer smoke suite.")
    parser.add_argument("--case", action="append", default=[], help="Run only the named smoke case(s).")
    parser.add_argument("--keep", action="store_true", help="Keep per-case run directories after completion.")
    return parser.parse_args()


def main():
    args = parse_args()
    testing_dir = Path(__file__).resolve().parent
    drc_dir = testing_dir.parent
    manifest_path = testing_dir / "interposer_smoke_tests.json"
    run_drc_path = drc_dir / "run_drc.py"
    smoke_root = testing_dir / "smoke_runs"

    with manifest_path.open() as fh:
        manifest = json.load(fh)

    cases = manifest["cases"]
    if args.case:
        requested = set(args.case)
        cases = [case for case in cases if case["name"] in requested]

    if not cases:
        print("No smoke cases selected.", file=sys.stderr)
        return 2

    smoke_root.mkdir(exist_ok=True)
    failures = []

    for case in cases:
        case_run_dir = smoke_root / case["name"]
        if case_run_dir.exists():
            shutil.rmtree(case_run_dir)

        command = [sys.executable, str(run_drc_path), "--path", str(drc_dir / case["layout"])]
        for deck in case["decks"]:
            command.extend(["--deck", deck])
        command.extend(case.get("flags", []))
        command.extend(["--run_dir", str(case_run_dir)])

        result = subprocess.run(command, cwd=drc_dir)
        if result.returncode != case["expect_exit"]:
            failures.append((case["name"], case["expect_exit"], result.returncode))
        elif not args.keep and case_run_dir.exists():
            shutil.rmtree(case_run_dir)

    if failures:
        for name, expected, actual in failures:
            print(f"Smoke case '{name}' expected exit {expected} but got {actual}", file=sys.stderr)
        return 1

    print(f"Executed {len(cases)} interposer smoke case(s) successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
