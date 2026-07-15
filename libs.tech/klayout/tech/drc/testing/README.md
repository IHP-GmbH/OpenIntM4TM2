# Interposer DRC Testing

Unit tests for the interposer DRC rule decks, following the IHP-SG13G2
`testing/` convention: one testcase GDS per rule table under
`testcases/unit/<table>.gds`, exercised through the project's own `run_drc.py`
so the rules are checked by the same runner as sign-off.

## Layout

```text
testing/
├── run_regression.py                 # runner: runs each top cell, compares vs GOLDEN
└── testcases/
    └── unit/
        ├── via4.gds                  # committed testcase (Via4: V4.b1, V4.c1, M5.c1)
        └── gen_via4_testcase.py      # regenerates via4.gds
```

Each testcase GDS has two top cells that are checked as a whole:

- `<table>_viol`  — one violating structure per rule; must produce exactly that rule set.
- `<table>_clean` — the corresponding legal structures; must be clean (this also guards
  against false positives on the other rules of the deck).

Structures are laid out several um apart so they never interact, and are labeled by
text on layer `63/0` (`<rule> PASS` / `<rule> FAIL`) for readability. The expected
("golden") violated-rule set per top cell is declared in `run_regression.py:GOLDEN`.

## Usage

```bash
# Run the regression (one klayout -b invocation per top cell):
python run_regression.py                 # all tables
python run_regression.py --table via4    # a single table

# Regenerate a testcase GDS after changing its geometry:
python testcases/unit/gen_via4_testcase.py
```

The testcase GDS files are committed (a `.gitignore` exception re-includes
`testcases/**/*.gds` despite the global `*.gds` ignore). Regenerate and re-commit
the GDS whenever you change the corresponding `gen_*` script.

## Adding a table

1. Add `testcases/unit/gen_<table>_testcase.py` that writes `<table>.gds` with
   `<table>_viol` / `<table>_clean` top cells.
2. Generate the GDS and commit it.
3. Add a `GOLDEN['<table>']` entry (deck name + expected set per top cell) in
   `run_regression.py`.
