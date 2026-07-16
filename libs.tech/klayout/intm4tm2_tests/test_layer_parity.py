"""IHP IntM4TM2 module layer map - parity regression.

Asserts that tech/intm4tm2.lyp exposes exactly the canonical layer set listed
in tech/intm4tm2_layers.txt (name purpose layer datatype). Any layer added to
or removed from the technology must update both files together.
"""

import re
from pathlib import Path

TECH_DIR = Path(__file__).resolve().parent.parent / "tech"
LYP_FILE = TECH_DIR / "intm4tm2.lyp"
GOLDEN_FILE = TECH_DIR / "intm4tm2_layers.txt"


def _lyp_entries():
    txt = LYP_FILE.read_text()
    entries = set()
    for block in re.findall(r"<properties>.*?</properties>", txt, re.S):
        name = re.search(r"<name>([^<]*)</name>", block)
        source = re.search(r"<source>([^<]*)</source>", block)
        assert name and source, "layer entry without name/source"
        layer, datatype = source.group(1).split("/")
        entries.add((name.group(1), int(layer), int(datatype)))
    return entries


def _golden_entries():
    entries = set()
    for line in GOLDEN_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, purpose, layer, datatype = line.split()
        entries.add((f"{name}.{purpose}", int(layer), int(datatype)))
    return entries


def test_layer_count():
    assert len(_golden_entries()) == 158


def test_lyp_matches_canonical_layer_map():
    lyp = _lyp_entries()
    golden = _golden_entries()
    missing = golden - lyp
    extra = lyp - golden
    assert not missing, f"layers missing from intm4tm2.lyp: {sorted(missing)}"
    assert not extra, f"layers in intm4tm2.lyp beyond the canonical map: {sorted(extra)}"
