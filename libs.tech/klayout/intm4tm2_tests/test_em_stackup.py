"""Regression tests for the IntM4TM2 EM / extraction technology stackups.

Validates the openEMS and Palace XML stackups and the parasitics ITF against
the interposer's reduced back-end model: Metal4 is the lowest routing metal,
the M4..TM2 thicknesses/spacings match the SG13G2 process, GDS layer numbers
match the layer map, and no base-node layers (Metal1..3, Activ, GatPoly, ...)
leak in. Pure-Python (stdlib only); no KLayout dependency.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


def _repo_root():
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "libs.tech").is_dir():
            return parent
    raise RuntimeError("could not locate repo root (libs.tech not found)")


ROOT = _repo_root()

OPENEMS = ROOT / "libs.tech/openems/openems_intm4tm2/workflow"
PALACE = ROOT / "libs.tech/palace/workflow"
ITF = ROOT / "libs.tech/parasitics/itf/intm4tm2_typ.itf"

XML_FILES = [
    OPENEMS / "INTM4TM2.xml",
    OPENEMS / "INTM4TM2_nosub.xml",
    PALACE / "INTM4TM2.xml",
    PALACE / "INTM4TM2_nosub.xml",
]

# Reduced back-end: name -> (gds_layer, zmin, zmax). Absolute z after the
# -3.42 um shift of the SG13G2 M4..TM2 block onto the carrier.
EXPECTED_LAYERS = {
    "Metal4": (50, 0.6400, 1.1300),
    "Metal5": (67, 1.6700, 2.1600),
    "TopMetal1": (126, 3.0103, 5.0103),
    "TopMetal2": (134, 7.8103, 10.8103),
    "TopVia2": (133, 5.0103, 7.8103),
    "TopVia1": (125, 2.1600, 3.0103),
    "Via4": (66, 1.1300, 1.6700),
}

# GDS layer numbers of layers that must NOT appear (base-node back-end).
FORBIDDEN_GDS = {8, 10, 30, 49, 29, 19, 6, 1}  # M1,M2,M3,Via3,Via2,Via1,Cont,Activ

TOL = 1e-4


def _layers(xml_path):
    root = ET.parse(xml_path).getroot()
    out = {}
    for layer in root.iter("Layer"):
        out[layer.attrib["Name"]] = (
            int(layer.attrib["Layer"]),
            float(layer.attrib["Zmin"]),
            float(layer.attrib["Zmax"]),
        )
    return out


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_xml_is_wellformed_schema2(xml_path):
    root = ET.parse(xml_path).getroot()
    assert root.tag == "Stackup"
    assert root.attrib.get("schemaVersion") == "2.0"


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_expected_layers_present_with_correct_z(xml_path):
    layers = _layers(xml_path)
    for name, (gds, zmin, zmax) in EXPECTED_LAYERS.items():
        assert name in layers, f"{name} missing in {xml_path.name}"
        g, zlo, zhi = layers[name]
        assert g == gds, f"{name} layer number {g} != {gds}"
        assert abs(zlo - zmin) < TOL, f"{name} Zmin {zlo} != {zmin}"
        assert abs(zhi - zmax) < TOL, f"{name} Zmax {zhi} != {zmax}"


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_metal4_is_lowest_routing_metal(xml_path):
    layers = _layers(xml_path)
    routing = [n for n in ("Metal4", "Metal5", "TopMetal1", "TopMetal2") if n in layers]
    lowest = min(routing, key=lambda n: layers[n][1])
    assert lowest == "Metal4"
    assert abs(layers["Metal4"][1] - 0.64) < TOL, "Metal4 must sit ~0.64 um above carrier"


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_metal_thicknesses(xml_path):
    layers = _layers(xml_path)
    expect = {"Metal4": 0.49, "Metal5": 0.49, "TopMetal1": 2.0, "TopMetal2": 3.0}
    for name, t in expect.items():
        _, zlo, zhi = layers[name]
        assert abs((zhi - zlo) - t) < TOL, f"{name} thickness {zhi - zlo} != {t}"


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_vias_bridge_adjacent_metals(xml_path):
    layers = _layers(xml_path)
    # (via, lower metal top, upper metal bottom)
    bridges = [
        ("Via4", "Metal4", "Metal5"),
        ("TopVia1", "Metal5", "TopMetal1"),
        ("TopVia2", "TopMetal1", "TopMetal2"),
    ]
    for via, lower, upper in bridges:
        _, vlo, vhi = layers[via]
        assert abs(vlo - layers[lower][2]) < TOL, f"{via} bottom != {lower} top"
        assert abs(vhi - layers[upper][1]) < TOL, f"{via} top != {upper} bottom"


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_no_conductor_overlap(xml_path):
    layers = _layers(xml_path)
    metals = [(n, lo, hi) for n, (_, lo, hi) in layers.items()
              if n in ("Metal4", "Metal5", "TopMetal1", "TopMetal2")]
    metals.sort(key=lambda t: t[1])
    for (n0, _, hi0), (n1, lo1, _) in zip(metals, metals[1:]):
        assert hi0 <= lo1 + TOL, f"{n0} overlaps {n1}"


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_no_base_node_layers(xml_path):
    layers = _layers(xml_path)
    present_gds = {gds for gds, _, _ in layers.values()}
    leaked = present_gds & FORBIDDEN_GDS
    assert not leaked, f"base-node layers leaked into {xml_path.name}: {sorted(leaked)}"
    for bad in ("Metal1", "Metal2", "Metal3", "Activ", "GatPoly", "Via1", "Via2", "Via3", "Cont"):
        assert bad not in layers, f"{bad} must not be present in {xml_path.name}"


@pytest.mark.parametrize("xml_path", XML_FILES, ids=lambda p: p.parent.parent.name + "/" + p.name)
def test_lumped_beol_oxide_height(xml_path):
    # SiO2 lumped dielectric = base 15.7303 shifted by -3.42 = 12.3103
    root = ET.parse(xml_path).getroot()
    sio2 = [float(d.attrib["Thickness"]) for d in root.iter("Dielectric")
            if d.attrib["Name"] == "SiO2"]
    assert sio2 and abs(sio2[0] - 12.3103) < TOL


def test_itf_reduced_backend():
    text = ITF.read_text()
    cond = dict(re.findall(r"CONDUCTOR\s+(\w+)\s*\{THICKNESS=([\d.]+)", text))
    assert set(cond) == {"Metal4", "Metal5", "TopMetal1", "TopMetal2"}, \
        f"ITF conductors should be exactly M4/M5/TM1/TM2, got {set(cond)}"
    assert abs(float(cond["Metal4"]) - 0.490) < TOL
    assert abs(float(cond["Metal5"]) - 0.490) < TOL
    assert abs(float(cond["TopMetal1"]) - 2.0) < TOL
    assert abs(float(cond["TopMetal2"]) - 3.0) < TOL
    # base-node metals must be gone
    for bad in ("Metal1", "Metal2", "Metal3", "GatPoly", "Activ"):
        assert bad not in cond
    # carrier base oxide present, vias restricted to the interposer set
    assert "dummyOx" in text
    vias = set(re.findall(r"VIA\s+(\w+)", text))
    assert vias == {"TopVia2", "TopVia1", "Via4"}, f"unexpected ITF vias: {vias}"


def test_no_vendor_or_private_leak():
    # Forbidden vendor/private tokens are assembled from fragments so this guard
    # file does not itself contain the plaintext strings it forbids (public repo
    # policy: none of these names may appear in tracked files, including here).
    fragments = [
        ("cad", "ence"),
        ("cal", "ibre"),
        ("virt", "uoso"),
        ("p", "vs"),
        ("quan", "tus"),
        ("spec", "tre"),
        ("peg", "asus"),
        ("orig", "inal_", "cad", "ence"),
    ]
    forbidden = re.compile("|".join(re.escape("".join(p)) for p in fragments),
                           re.IGNORECASE)
    for f in list(XML_FILES) + [ITF, OPENEMS.parent / "README.md"]:
        assert not forbidden.search(f.read_text()), f"vendor/private token leaked in {f}"
