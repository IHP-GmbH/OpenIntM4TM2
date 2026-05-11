#!/usr/bin/env bash
#
# flip-chip.sh -- thin wrapper around flip_chip_gds.py
#
# Usage:
#   flip-chip.sh --input_gds <path> --output_gds <path> [options]
#
# Options (forwarded verbatim to flip_chip_gds.py):
#   --mode flatten|hierarchy   flatten (default) or preserve hierarchy
#   --top-cell NAME            top cell name in the input GDS
#   --output-cell NAME         name for the flipped cell in output
#   --rotation DEG             extra rotation baked into the flip
#
# Examples:
#   flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds
#   flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds --mode hierarchy
#   flip-chip.sh --input_gds die.gds --output_gds die_flipped.gds --rotation 90

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/flip_chip_gds.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
    echo "ERROR: flip_chip_gds.py not found next to flip-chip.sh (${PY_SCRIPT})" >&2
    exit 1
fi

PYTHON_BIN="${PYTHON:-python3}"

if ! "${PYTHON_BIN}" -c "import klayout.db" >/dev/null 2>&1; then
    if command -v klayout >/dev/null 2>&1; then
        # Fall back to klayout's bundled interpreter.
        exec klayout -zz -r "${PY_SCRIPT}" -- "$@"
    fi
    echo "ERROR: klayout python bindings not available for '${PYTHON_BIN}' and "\
         "'klayout' executable not on PATH." >&2
    echo "Install with: pip install klayout" >&2
    exit 1
fi

exec "${PYTHON_BIN}" "${PY_SCRIPT}" "$@"
