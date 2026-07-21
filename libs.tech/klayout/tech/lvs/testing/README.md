# Interposer LVS Testing

Unit tests for the interposer LVS deck, following the IHP-SG13G2 `lvs/testing/`
convention: small connectivity fixtures under `testcases/unit/` exercised through
the interposer LVS deck (`tech/lvs/intm4tm2.lvs`), so the deck is checked by the
same runner path as sign-off.

The interposer LVS deck is connectivity-only (labeled-net extraction, open/short
detection, optional netlist match), so the regression checks the deck's textual
verdict per fixture rather than a full device netlist compare.

## Layout

```text
testing/
├── run_regression.py                        # runner: runs each fixture, compares vs GOLDEN
└── testcases/
    └── unit/
        ├── lvs_clean.gds                     # VDD net + isolated FLOATPAD net (clean + match)
        ├── lvs_open.gds                      # VDD label split across disconnected nets (OPEN)
        ├── lvs_short.gds                     # VDD/SIG labels bridged on one net (SHORT)
        ├── lvs_clean_reference.cir           # reference netlist for the clean fixture
        └── gen_lvs_connectivity_testcase.py  # regenerates the .gds/.cir
```

Each fixture is a self-contained top cell built from TopMetal2 pads joined through
TopVia2 / TopMetal1, with net labels on `134/25`. The expected ("golden") verdict
per fixture (open / short / clean+match) is declared in `run_regression.py:GOLDEN`.

## Usage

```bash
# Run the regression (one klayout -b invocation per fixture):
python run_regression.py                 # all fixtures
python run_regression.py --case open     # a single fixture

# Regenerate the fixtures after changing their geometry:
python testcases/unit/gen_lvs_connectivity_testcase.py
```

The fixture `.gds` files are committed (a `.gitignore` exception re-includes
`testcases/**/*.gds` despite the global `*.gds` ignore). Regenerate and re-commit
them whenever you change `gen_lvs_connectivity_testcase.py`.

## Adding a fixture

1. Add a generator (or extend `gen_lvs_connectivity_testcase.py`) that writes a
   `<name>.gds` top cell exercising the connectivity case.
2. Generate the GDS and commit it.
3. Add a `GOLDEN['<name>']` entry (gds + expected open/short/match) in
   `run_regression.py`.
