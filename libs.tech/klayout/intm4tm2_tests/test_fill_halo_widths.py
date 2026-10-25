"""The measured fill-halo widths, libs.tech/parasitics/fill_halo_widths.json.

Assembly flows read the file through the PDK root and compare a route's halo with it,
so it must parse, name its format and version, and give one width for each fill metal
routed_load.py grows a halo on, in a form that tool's own --halo-um parser accepts.

Pure stdlib; no KLayout needed.
"""
import json
import sys
from pathlib import Path


def _repo_root():
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "libs.tech").is_dir():
            return parent
    raise RuntimeError("could not locate repo root (libs.tech not found)")


ROOT = _repo_root()
sys.path.insert(0, str(ROOT / "libs.tech/parasitics"))
import routed_load as rl  # noqa: E402

WIDTHS = ROOT / "libs.tech/parasitics/fill_halo_widths.json"


def test_the_file_holds_one_width_per_fill_metal():
    data = json.loads(WIDTHS.read_text())
    assert (data["format"], data["version"]) == ("intm4tm2.fill_halo_widths", 1)
    assert data["note"].strip()
    halo = data["halo_um"]
    assert sorted(halo) == sorted(rl.ROLES) == ["M4", "M5", "TM1", "TM2"]
    assert all(type(w) in (int, float) and w > 0 for w in halo.values()), halo
    text = ",".join("%s=%s" % (role, halo[role]) for role in rl.ROLES)
    assert rl.parse_halo_um(text) == halo
