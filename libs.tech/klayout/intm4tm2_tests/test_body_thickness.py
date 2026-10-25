"""The interposer's physical stack has one source, libs.tech/intm4tm2_stack.json.

Every copy of its values in this PDK is checked against the key here:
- the substrate and offset of the two EM stackups that model the carrier (the
  _nosub variants replace the carrier by design and are left out);
- the Palace backside ground sheet;
- the metal z bounds and the oxide and silicon materials in all four EM stackups;
- the passivation over the attach plane in all four EM stackups: where the
  SiO2 slab ends and where the Passive slab starts and ends, and its material;
- the parasitics ITF, read the way an extractor reads it: each metal's z, the
  oxide around and between the metals, and the passivation films over them;
- the KiCad template's board thickness and stackup;
- Chiplet Studio's 3D stackup: the attach plane, the substrate slab, and every
  metal, its filler and the vias between them, at the GDS numbers of the layer
  map, plus nothing the key does not describe.
One test reads them all and names each copy that disagrees, so a correction to
the key lists every file in this PDK it still has to reach. Consumers in other
repositories carry their own check. The self-tests below feed the check one
wrong copy at a time to prove it would catch each one.

The template stackup is a constrained picture of the stack. KiCad allows a
dielectric only between two copper layers, and B.Cu (Metal4) is the bottom of
the back-end, so the silicon under it has one slot that survives Board Setup and
KiCad's stackup sync: B.Mask, below B.Cu. It carries the substrate plus the base
oxide under Metal4. The board thickness pcbnew reports, and the stackup sum the
Physical Stackup page writes back over it, both equal the body. The back-end
items are z differences of the key's metals at KiCad's 1 nm resolution.

Pure stdlib; no KLayout or KiCad needed.
"""
import json
import re
import shutil
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
KEY = ROOT / "libs.tech/intm4tm2_stack.json"
OPENEMS = ROOT / "libs.tech/openems/openems_intm4tm2/workflow/INTM4TM2.xml"
PALACE = ROOT / "libs.tech/palace/workflow/INTM4TM2.xml"
OPENEMS_NOSUB = ROOT / "libs.tech/openems/openems_intm4tm2/workflow/INTM4TM2_nosub.xml"
PALACE_NOSUB = ROOT / "libs.tech/palace/workflow/INTM4TM2_nosub.xml"
TEMPLATE = ROOT / "libs.tech/kicad/interposer_template.kicad_pcb"
STUDIO = ROOT / "libs.tech/chiplet_studio/intm4tm2.stackup.yaml"
LAYERS = ROOT / "libs.tech/klayout/tech/intm4tm2_layers.txt"
ITF = ROOT / "libs.tech/parasitics/itf/intm4tm2_typ.itf"

# The EM files write four decimals.
EM_TOL_UM = 1e-4

# The template's copper layers, top down; each dielectric between two of them is
# the oxide between their metals.
COPPER = [("F.Cu", "TopMetal2"), ("In1.Cu", "TopMetal1"), ("In2.Cu", "Metal5"), ("B.Cu", "Metal4")]


def load_key(path):
    key = json.loads(Path(path).read_text())
    assert key["schema_version"] == "1.1", f"{path}: unknown schema_version"
    sub, beol = key["substrate"], key["beol"]
    assert sub["thickness_um"] > 0 and sub["source"]
    assert sub["citation"]["title"] and sub["citation"]["venue"]
    metals = beol["metals"]
    bounds = [z for m in metals for z in (m["zmin_um"], m["zmax_um"])]
    assert 0 < bounds[0] and bounds == sorted(bounds), f"{path}: metals overlap or are out of order"
    assert beol["attachment_surface_z_um"] == metals[-1]["zmax_um"], \
        f"{path}: the attach plane is not the top of {metals[-1]['name']}"
    films = beol["passivation"]
    assert films and beol["passivation_source"], f"{path}: passivation without a source"
    assert len({f["name"] for f in films}) == len(films), f"{path}: passivation film named twice"
    for film in films:
        assert film["thickness_um"] > 0 and film["epsilon_r"] >= 1, \
            f"{path}: passivation film {film['name']} is not a film"
    return key


def body_um(key):
    return key["substrate"]["thickness_um"] + key["beol"]["attachment_surface_z_um"]


def _metals(key):
    return {m["name"]: (m["zmin_um"], m["zmax_um"]) for m in key["beol"]["metals"]}


def passivation(key):
    """[(film, zmin, zmax)]: the films stacked from the attach plane up."""
    z, out = key["beol"]["attachment_surface_z_um"], []
    for film in key["beol"]["passivation"]:
        out.append((film, z, z + film["thickness_um"]))
        z += film["thickness_um"]
    return out


def _nm(um):
    """KiCad stores every length as whole nanometres."""
    return round(um * 1000)


def _em(xml_path):
    """(values, layer z bounds). The dielectrics are listed top down and stack up
    from the bottom of the box; the substrate offset puts z = 0 at the substrate
    top, the key's frame, so each dielectric's Zmin and Zmax are in that frame."""
    root = ET.parse(xml_path).getroot()
    found = {}
    for d in root.iter("Dielectric"):
        if d.attrib["Name"] == "Substrate":
            found["Substrate Thickness"] = float(d.attrib["Thickness"])
    for s in root.iter("Substrate"):
        found["Substrate Offset"] = float(s.attrib["Offset"])
    z = -found.get("Substrate Offset", 0.0)
    for d in reversed(list(root.iter("Dielectric"))):
        top = z + float(d.attrib["Thickness"])
        found[f"{d.attrib['Name']} Zmin"], found[f"{d.attrib['Name']} Zmax"] = z, top
        found[f"{d.attrib['Name']} Material"] = d.attrib["Material"]
        z = top
    for m in root.iter("Material"):
        if "Permittivity" in m.attrib:
            found[f"{m.attrib['Name']} Permittivity"] = float(m.attrib["Permittivity"])
            found[f"{m.attrib['Name']} DielectricLossTangent"] = float(m.attrib["DielectricLossTangent"])
    layers = {l.attrib["Name"]: (float(l.attrib["Zmin"]), float(l.attrib["Zmax"]))
              for l in root.iter("Layer")}
    return found, layers


# The EM stacks lump the back-end oxide and the oxide film over TopMetal2 into one
# SiO2 slab from the substrate top, so that slab ends where the oxide film ends;
# the nitride is the Passive slab on top of it.
EM_FILMS = {"oxide": "SiO2", "nitride": "Passive"}


def _em_disagreements(xml_path, key, label, carrier, backside_sheet=False):
    sub, ox = key["substrate"], key["beol"]["dielectric"]
    found, layers = _em(xml_path)
    want = {"SiO2 Permittivity": ox["epsilon_r"], "SiO2 DielectricLossTangent": ox["loss_tangent"]}
    out = []
    for film, zmin, zmax in passivation(key):
        slab = EM_FILMS.get(film["name"])
        if slab is None:
            out.append(f"{label}: passivation film {film['name']} has no slab here")
            continue
        if slab != "SiO2":  # the oxide film's slab starts at the substrate top
            want[f"{slab} Zmin"] = zmin
        want[f"{slab} Zmax"] = zmax
        material = found.get(f"{slab} Material")
        for what, value in (("Permittivity", film["epsilon_r"]),
                            ("DielectricLossTangent", film["loss_tangent"])):
            found[f"{slab} {what} ({film['name']})"] = found.get(f"{material} {what}")
            want[f"{slab} {what} ({film['name']})"] = value
    for name, (zmin, zmax) in _metals(key).items():
        if name in layers:
            found[f"{name} Zmin"], found[f"{name} Zmax"] = layers[name]
        want[f"{name} Zmin"], want[f"{name} Zmax"] = zmin, zmax
    if "BACKSIDEGND" in layers:
        found["BACKSIDEGND Zmin"], found["BACKSIDEGND Zmax"] = layers["BACKSIDEGND"]
    if carrier:
        want["Substrate Thickness"] = want["Substrate Offset"] = sub["thickness_um"]
        want["Substrate Permittivity"] = sub["epsilon_r"]
        want["Substrate DielectricLossTangent"] = sub["loss_tangent"]
    if backside_sheet:
        want["BACKSIDEGND Zmin"] = want["BACKSIDEGND Zmax"] = -sub["thickness_um"]
    for what, value in want.items():
        got = found.get(what)
        if got is None:
            out.append(f"{label}: {what} missing, key says {value:g}")
        elif abs(got - value) > EM_TOL_UM:
            out.append(f"{label}: {what} is {got:g}, key says {value:g}")
    return out


def _sexpr(text):
    """Nested lists from KiCad's s-expression format."""
    stack, cur = [], []
    for tok in re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text):
        if tok == "(":
            stack.append(cur)
            cur = []
        elif tok == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(tok[1:-1] if tok.startswith('"') else tok)
    return cur[0]


def _child(node, name):
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def _value(item, name, cast=str):
    node = _child(item, name)
    return cast(node[1]) if node else None


def stackup_items(board):
    """{layer name: (thickness nm, material, epsilon_r, loss_tangent)} per item with a thickness."""
    items = {}
    for item in _child(_child(board, "setup"), "stackup")[1:]:
        if isinstance(item, list) and item[0] == "layer" and _child(item, "thickness"):
            items[item[1]] = (_nm(_value(item, "thickness", float) * 1000), _value(item, "material"),
                              _value(item, "epsilon_r", float), _value(item, "loss_tangent", float))
    return items


def expected_stackup(key):
    """{layer name: (thickness nm, material, epsilon_r, loss_tangent)} the key implies.

    Copper carries no material, and F.Mask none either: it is 0 because the
    passivation above TopMetal2 is not part of the body.
    """
    z = _metals(key)
    sub, ox = key["substrate"], key["beol"]["dielectric"]
    want = {cu: (_nm(z[m][1] - z[m][0]), None, None, None) for cu, m in COPPER}
    for n, ((_, upper), (_, lower)) in enumerate(zip(COPPER, COPPER[1:]), start=1):
        want[f"dielectric {n}"] = (_nm(z[upper][0] - z[lower][1]), ox["material"],
                                   ox["epsilon_r"], ox["loss_tangent"])
    want["F.Mask"] = (0, None, None, None)
    lowest = key["beol"]["metals"][0]["name"]
    want["B.Mask"] = (_nm(sub["thickness_um"] + z[lowest][0]), sub["material"],
                      sub["epsilon_r"], sub["loss_tangent"])
    return want


def _template_disagreements(pcb_path, key, label):
    board = _sexpr(Path(pcb_path).read_text())
    body = body_um(key) * 1000
    out = []
    general = _child(_child(board, "general"), "thickness")
    got = _nm(float(general[1]) * 1000) if general else None
    if got != round(body):
        out.append(f"{label}: (general (thickness)) is {got} nm, key body is {round(body)} nm")
    items = stackup_items(board)
    # Each item is rounded to 1 nm on its own, so the sum may drift by half a
    # nanometre per item from the exact body.
    total = sum(item[0] for item in items.values())
    if abs(total - body) > 0.5 * len(items):
        out.append(f"{label}: stackup sums to {total} nm, key body is {round(body)} nm")
    fields = ("thickness", "material", "epsilon_r", "loss_tangent")
    for name, wanted in expected_stackup(key).items():
        if name not in items:
            out.append(f"{label}: stackup item {name} missing")
            continue
        for field, w, g in zip(fields, wanted, items[name]):
            if w is not None and g != w:
                out.append(f"{label}: stackup item {name} {field} is {g}, key implies {w}")
    for name in sorted(set(items) - set(expected_stackup(key))):
        out.append(f"{label}: stackup item {name} is not part of the stack")
    return out


# The ITF keeps the SG13G2 ITF's thicknesses, written to the nanometre; from
# TopMetal1 up they sit 0.3 nm below the EM stacks' (and the key's) z.
ITF_TOL_UM = 5e-4
ITF_READ = {"DIELECTRIC": {"THICKNESS", "ER"},
            "CONDUCTOR": {"THICKNESS", "WMIN", "SMIN", "RPSQ"}}


def itf_stack(path):
    """(dielectrics [(name, zmin, zmax, ER)], conductors {name: (zmin, zmax)}, unread).

    The ITF lists its layers top down. Read bottom up, the way an extractor
    reads it: each dielectric starts on the top of the dielectric below it, and
    each conductor sits on the top of the dielectric listed just below it, so it
    is embedded in the dielectrics listed above it. z = 0 is the Si, the
    extraction ground, which is the key's substrate top.

    Every film is read as flat. A parameter outside ITF_READ (a film that
    follows the metal, say) would change that reading, so it is returned in
    ``unread`` instead of being dropped.
    """
    entries = re.findall(r"^(DIELECTRIC|CONDUCTOR)\s+(\w+)\s*\{([^}]*)\}",
                         Path(path).read_text(), re.M)
    z, dielectrics, conductors, unread = 0.0, [], {}, []
    for kind, name, body in reversed(entries):
        raw = dict(re.findall(r"(\w+)\s*=\s*(\S+)", body))
        unread += [(name, k) for k in raw if k not in ITF_READ[kind]]
        params = {k: float(v) for k, v in raw.items() if k in ITF_READ[kind]}
        if kind == "DIELECTRIC":
            dielectrics.append((name, z, z + params["THICKNESS"], params["ER"]))
            z += params["THICKNESS"]
        else:
            conductors[name] = (z, z + params["THICKNESS"])
    return dielectrics, conductors, unread


def _er_at(dielectrics, z):
    for _, zmin, zmax, er in dielectrics:
        if zmin < z < zmax:
            return er
    return None


def _itf_disagreements(path, key, label):
    dielectrics, conductors, unread = itf_stack(path)
    out = [f"{label}: {name} sets {param}, which this check does not read"
           for name, param in unread]
    metals = _metals(key)
    for name, (zmin, zmax) in metals.items():
        if name not in conductors:
            out.append(f"{label}: {name} missing")
            continue
        for what, got, want in (("Zmin", conductors[name][0], zmin),
                                ("Zmax", conductors[name][1], zmax)):
            if abs(got - want) > ITF_TOL_UM:
                out.append(f"{label}: {name} {what} is {got:g}, key says {want:g}")
    # Oxide everywhere from the Si up to the attach plane, around and between the
    # metals: probed in each gap between two metal bounds.
    oxide = key["beol"]["dielectric"]["epsilon_r"]
    bounds = sorted({0.0} | {z for pair in metals.values() for z in pair})
    for lo, hi in zip(bounds, bounds[1:]):
        er = _er_at(dielectrics, (lo + hi) / 2)
        if er != oxide:
            out.append(f"{label}: the dielectric at z {(lo + hi) / 2:g} has ER {er}, "
                       f"the key's oxide is {oxide:g}")
    # Each passivation film: its material in the middle, and a dielectric top
    # where the film ends.
    tops = [zmax for _, _, zmax, _ in dielectrics]
    for film, zmin, zmax in passivation(key):
        er = _er_at(dielectrics, (zmin + zmax) / 2)
        if er != film["epsilon_r"]:
            out.append(f"{label}: passivation {film['name']} has ER {er} in its middle, "
                       f"key says {film['epsilon_r']:g}")
        if not any(abs(top - zmax) <= ITF_TOL_UM for top in tops):
            out.append(f"{label}: passivation {film['name']} ends at {zmax:g} in the key; "
                       "no ITF dielectric ends there")
    # The probes above sit in the middle of each span; a thin film between two
    # of them shows only as an interface where the key has none.
    films = passivation(key)
    known = bounds + [z for _, zmin, zmax in films for z in (zmin, zmax)]
    for name, zmin, zmax, _ in dielectrics:
        for z in (zmin, zmax):
            if z <= films[-1][2] + ITF_TOL_UM and \
                    not any(abs(z - k) <= ITF_TOL_UM for k in known):
                out.append(f"{label}: {name} has an interface at z {z:g}, "
                           "where the key has none")
    return out


# The Studio stackup is written to 4 decimals, like the key.
STUDIO_TOL_UM = 1e-6

# Which via cut sits between which two metals, bottom up.
VIAS = [("Via4", "Metal4", "Metal5"), ("TopVia1", "Metal5", "TopMetal1"),
        ("TopVia2", "TopMetal1", "TopMetal2")]

# The cu-pillar pad opening is drawn on these layers' pillar datatype; Studio
# renders it as the top metal so it merges with the pad it opens onto.
PILLAR_LAYERS = ["Passiv", "dfpad", "Recog"]

# The slab Studio draws for the substrate and finds by name; not an IntM4TM2 layer.
SUBSTRATE_GDS = (40, 0)


def layer_map(path=LAYERS):
    """{(name, purpose): (layer, datatype)} from the canonical layer map."""
    out = {}
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, purpose, layer, datatype = line.split()
            out[(name, purpose)] = (int(layer), int(datatype))
    return out


def _scalar(text):
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def blender_gds(path):
    """(top-level scalars, {layer entry: {field: value}}) from a Studio stackup.

    Reads only the shape that format uses, top-level `name:` maps of
    `field: scalar` plus top-level `key: scalar`, and refuses anything else, so
    no YAML library is needed.
    """
    scalars, entries, cur = {}, {}, None
    for n, raw in enumerate(Path(path).read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        m = re.fullmatch(r"( *)([\w.]+):(?: +(\S+))?", line)
        assert m, f"{Path(path).name}:{n}: not a BlenderGDS line: {raw!r}"
        indent, name, value = m.groups()
        if indent:
            assert cur is not None and value is not None, f"{Path(path).name}:{n}: stray field"
            assert name not in cur, f"{Path(path).name}:{n}: {name} twice"
            cur[name] = _scalar(value)
        else:
            assert name not in scalars and name not in entries, f"{Path(path).name}:{n}: {name} twice"
            if value is None:
                cur = entries[name] = {}
            else:
                cur, scalars[name] = None, _scalar(value)
    return scalars, entries


def expected_studio(key, layers):
    """{entry: (index, type, z, height, purpose)} the key implies for Studio."""
    z = _metals(key)
    thick = key["substrate"]["thickness_um"]
    want = {"Substrate": (*SUBSTRATE_GDS, -thick, thick, None)}
    for name, (zmin, zmax) in z.items():
        want[name] = (*layers[(name, "drawing")], zmin, zmax - zmin, None)
        want[f"{name}.filler"] = (*layers[(name, "filler")], zmin, zmax - zmin, "filler")
    for via, lower, upper in VIAS:
        want[via] = (*layers[(via, "drawing")], z[lower][1], z[upper][0] - z[lower][1], None)
    zmin, zmax = z[key["beol"]["metals"][-1]["name"]]
    for layer in PILLAR_LAYERS:
        want[f"{layer}.pillar"] = (*layers[(layer, "pillar")], zmin, zmax - zmin, None)
    return want


def _studio_disagreements(path, key, label):
    scalars, entries = blender_gds(path)
    out = []
    attach = key["beol"]["attachment_surface_z_um"]
    got = scalars.get("attachment_surface_z")
    if got is None or abs(got - attach) > STUDIO_TOL_UM:
        out.append(f"{label}: attachment_surface_z is {got}, key says {attach:g}")
    fields = ("index", "type", "z", "height", "purpose")
    want = expected_studio(key, layer_map())
    for name, wanted in want.items():
        if name not in entries:
            out.append(f"{label}: {name} missing")
            continue
        for field, w in zip(fields, wanted):
            g = entries[name].get(field)
            if isinstance(w, float):
                bad = not isinstance(g, (int, float)) or abs(g - w) > STUDIO_TOL_UM
            else:
                bad = g != w
            if bad:
                out.append(f"{label}: {name} {field} is {g}, key implies {w if w is not None else 'none'}")
    for name in sorted(set(entries) - set(want)):
        out.append(f"{label}: {name} is not part of the stack")
    return out


def disagreements(key_path=KEY, openems=OPENEMS, palace=PALACE, template=TEMPLATE,
                  openems_nosub=OPENEMS_NOSUB, palace_nosub=PALACE_NOSUB, studio=STUDIO,
                  itf=ITF):
    key = load_key(key_path)
    return (_em_disagreements(openems, key, "openems INTM4TM2.xml", carrier=True)
            + _em_disagreements(palace, key, "palace INTM4TM2.xml", carrier=True,
                                backside_sheet=True)
            + _em_disagreements(openems_nosub, key, "openems INTM4TM2_nosub.xml", carrier=False)
            + _em_disagreements(palace_nosub, key, "palace INTM4TM2_nosub.xml", carrier=False)
            + _template_disagreements(template, key, "interposer_template.kicad_pcb")
            + _studio_disagreements(studio, key, "chiplet_studio intm4tm2.stackup.yaml")
            + _itf_disagreements(itf, key, "intm4tm2_typ.itf"))


def test_every_copy_agrees_with_the_key():
    found = disagreements()
    assert not found, "copies that disagree with intm4tm2_stack.json:\n  " + "\n  ".join(found)


def test_body_is_derived_not_stored():
    """The key stores the two parts of the body, never the body itself."""
    key = load_key(KEY)
    names = set(key) | set(key["substrate"]) | set(key["beol"]) | set(key["beol"]["dielectric"])
    assert not any("body" in n for n in names), sorted(names)


def test_template_board_layers_enable_the_silicon_slot():
    """A disabled B.Mask drops the silicon on KiCad's next stackup sync."""
    board = _sexpr(TEMPLATE.read_text())
    names = {entry[1] for entry in _child(board, "layers")[1:] if isinstance(entry, list)}
    assert "B.Mask" in names


# ----------------------------------------------------------------------
# Self-tests: one wrong copy at a time, and the check names that copy.

SOURCES = {"key": KEY, "openems": OPENEMS, "palace": PALACE, "template": TEMPLATE,
           "openems_nosub": OPENEMS_NOSUB, "palace_nosub": PALACE_NOSUB, "studio": STUDIO,
           "itf": ITF}


def _copies(tmp_path):
    paths = {}
    for name, src in SOURCES.items():
        dst = tmp_path / name / src.name
        dst.parent.mkdir()
        shutil.copy(src, dst)
        paths[name] = dst
    return paths


def _edit(path, old, new):
    text = path.read_text()
    assert text.count(old) == 1, f"{old!r} not unique in {path.name}"
    path.write_text(text.replace(old, new))


def _layer(name, body):
    return f'\t\t\t(layer "{name}"\n{body}\t\t\t)\n'


TPL = "interposer_template.kicad_pcb"
TM2_Z = 'Zmin="7.8103" Zmax="10.8103"'
SUB = '<Dielectric Name="Substrate" Material="Substrate" Thickness="250.0000" />'
CU = '(layer "{}"\n\t\t\t\t(type "copper")\n\t\t\t\t(thickness {})'
B_CU = _layer("B.Cu", '\t\t\t\t(type "copper")\n\t\t\t\t(thickness 0.00049)\n')
STU = "chiplet_studio intm4tm2.stackup.yaml"
TM2_FILL = "TopMetal2.filler:\n  index: 134\n  type: 22\n  purpose: filler\n  z: 7.8103\n"
M4_FILL = "Metal4.filler:\n  index: 50\n  type: 22\n  purpose: filler\n"
BACKMETAL = "BackMetal1:\n  index: 20\n  type: 0\n  z: -250.0\n  height: 1.0\n"
SIO2_SLAB = '<Dielectric Name="SiO2" Material="SiO2" Thickness="12.3103" />'
PASSIVE_SLAB = '<Dielectric Name="Passive" Material="Passive" Thickness="0.4000" />'
ITF_LBL = "intm4tm2_typ.itf"

# (file, [(old, new), ...], the finding it must produce)
MUTATIONS = [
    ("openems", [(SUB, SUB.replace("250.0000", "300.0000"))],
     "openems INTM4TM2.xml: Substrate Thickness"),
    ("openems", [(SUB, "")], "openems INTM4TM2.xml: Substrate Thickness missing"),
    ("openems", [('<Substrate Offset="250.0"/>', '<Substrate Offset="300.0"/>')],
     "openems INTM4TM2.xml: Substrate Offset"),
    ("palace", [(SUB, SUB.replace("250.0000", "300.0000"))],
     "palace INTM4TM2.xml: Substrate Thickness"),
    ("palace", [('<Substrate Offset="250.0"/>', '<Substrate Offset="300.0"/>')],
     "palace INTM4TM2.xml: Substrate Offset"),
    ("palace", [('Zmin="-250" Zmax="-250"', 'Zmin="-300" Zmax="-300"')],
     "palace INTM4TM2.xml: BACKSIDEGND Zmin"),
    ("palace", [(TM2_Z, 'Zmin="10.8303" Zmax="13.8303"')], "palace INTM4TM2.xml: TopMetal2 Zmax"),
    ("openems_nosub", [(TM2_Z, 'Zmin="7.8103" Zmax="10.81"')],
     "openems INTM4TM2_nosub.xml: TopMetal2 Zmax"),
    ("template", [("(thickness 0.26081)", "(thickness 0.260811)")],
     f"{TPL}: (general (thickness))"),
    ("template", [("(thickness 0.25064)", "(thickness 0.20064)")], f"{TPL}: stackup sums to"),
    ("template", [('(material "Si")', '(material "FR4")')], f"{TPL}: stackup item B.Mask material"),
    # TopMetal2 and Metal4 swapped: the sum holds, the items do not.
    ("template", [(CU.format("F.Cu", "0.003"), CU.format("F.Cu", "0.00049")),
                  (CU.format("B.Cu", "0.00049"), CU.format("B.Cu", "0.003"))],
     f"{TPL}: stackup item F.Cu"),
    ("template", [(B_CU, B_CU + _layer("dielectric 4", '\t\t\t\t(type "core")\n\t\t\t\t(thickness 0)\n'))],
     f"{TPL}: stackup item dielectric 4 is not part"),
    ("template", [("(epsilon_r 11.9)", "(epsilon_r 4.5)")], f"{TPL}: stackup item B.Mask epsilon_r"),
    ("openems", [('Permittivity="4.1"', 'Permittivity="3.9"')],
     "openems INTM4TM2.xml: SiO2 Permittivity"),
    ("palace", [('Permittivity="11.9"', 'Permittivity="11.7"')],
     "palace INTM4TM2.xml: Substrate Permittivity"),
    # The passivation: where the oxide slab ends, the nitride slab, its material.
    ("openems", [(SIO2_SLAB, SIO2_SLAB.replace("12.3103", "12.8103"))],
     "openems INTM4TM2.xml: SiO2 Zmax"),
    ("palace_nosub", [(PASSIVE_SLAB, PASSIVE_SLAB.replace("0.4000", "0.5000"))],
     "palace INTM4TM2_nosub.xml: Passive Zmax"),
    ("palace", [('Name="Passive" Type="Dielectric" Permittivity="6.6"',
                 'Name="Passive" Type="Dielectric" Permittivity="7.0"')],
     "palace INTM4TM2.xml: Passive Permittivity (nitride)"),
    # The slab named Passive made of oxide: read through its Material, not its name.
    ("openems_nosub", [(PASSIVE_SLAB, PASSIVE_SLAB.replace('Material="Passive"', 'Material="SiO2"'))],
     "openems INTM4TM2_nosub.xml: Passive Permittivity (nitride)"),
    # The ITF's old oxide: the right numeral, 1.5, as the film's thickness, but read
    # as an extractor reads it, TopMetal2 then runs out of its oxide at 9.31 um.
    ("itf", [("{THICKNESS=4.5    ER=4.1  }", "{THICKNESS=1.5    ER=4.1  }")],
     f"{ITF_LBL}: passivation oxide has ER"),
    ("itf", [("{THICKNESS=0.4    ER=6.6  }", "{THICKNESS=0.4    ER=7.0  }")],
     f"{ITF_LBL}: passivation nitride has ER"),
    ("itf", [("CONDUCTOR TopMetal2         {THICKNESS=3.0", "CONDUCTOR TopMetal2         {THICKNESS=3.5")],
     f"{ITF_LBL}: TopMetal2 Zmax"),
    ("itf", [("DIELECTRIC ox5              {THICKNESS=1.34", "DIELECTRIC ox5              {THICKNESS=1.44")],
     f"{ITF_LBL}: TopMetal1 Zmin"),
    ("itf", [("DIELECTRIC ox4              {THICKNESS=1.03   ER=4.1 }",
              "DIELECTRIC ox4              {THICKNESS=1.03   ER=3.9 }")],
     f"{ITF_LBL}: the dielectric at z"),
    # A thin nitride on the attach plane: every mid-span probe still reads oxide.
    ("itf", [("DIELECTRIC oxTopMetal2      {THICKNESS=4.5    ER=4.1  }",
              "DIELECTRIC oxTopMetal2      {THICKNESS=1.4    ER=4.1  }\n"
              "DIELECTRIC liner            {THICKNESS=0.1    ER=6.6  }\n"
              "DIELECTRIC oxTopMetal2      {THICKNESS=3.0    ER=4.1  }")],
     f"{ITF_LBL}: liner has an interface at z"),
    # A passivation that follows the metal, which this check reads as flat.
    ("itf", [("{THICKNESS=0.4    ER=6.6  }", "{THICKNESS=0.4    ER=6.6  MEASURED_FROM=TopMetal2 }")],
     f"{ITF_LBL}: nitTop sets MEASURED_FROM, which this check does not read"),
    # The SG13G2 frame's attach plane, the one Studio bundled before this file.
    ("studio", [("attachment_surface_z: 10.8103", "attachment_surface_z: 13.83")],
     f"{STU}: attachment_surface_z"),
    ("studio", [("  z: -250.0\n  height: 250.0", "  z: -50.0\n  height: 50.0")],
     f"{STU}: Substrate z"),
    ("studio", [(TM2_FILL, TM2_FILL.replace("z: 7.8103", "z: 10.83"))],
     f"{STU}: TopMetal2.filler z"),
    ("studio", [(M4_FILL, M4_FILL.replace("type: 22", "type: 0"))], f"{STU}: Metal4.filler type"),
    ("studio", [(M4_FILL, M4_FILL.replace("  purpose: filler\n", ""))],
     f"{STU}: Metal4.filler purpose"),
    ("studio", [("TopVia2:\n  index: 133\n  type: 0\n  z: 5.0103\n  height: 2.8\n", "")],
     f"{STU}: TopVia2 missing"),
    ("studio", [(TM2_FILL, BACKMETAL + TM2_FILL)], f"{STU}: BackMetal1 is not part"),
]


@pytest.mark.parametrize("which,edits,named", MUTATIONS, ids=[m[2] for m in MUTATIONS])
def test_check_names_the_wrong_copy(tmp_path, which, edits, named):
    paths = _copies(tmp_path)
    assert not disagreements(*(paths[n] for n in SOURCES))
    for old, new in edits:
        _edit(paths[which], old, new)
    found = disagreements(*(paths[n] for n in SOURCES))
    assert any(f.startswith(named) for f in found), found
    assert all(f.startswith(named.split(":")[0]) for f in found), found


def test_a_new_key_value_names_every_copy(tmp_path):
    """Correcting the substrate in the key alone leaves every carrier copy behind."""
    paths = _copies(tmp_path)
    _edit(paths["key"], '"thickness_um": 250.0', '"thickness_um": 300.0')
    found = disagreements(*(paths[n] for n in SOURCES))
    for copy in ("openems INTM4TM2.xml: Substrate Thickness",
                 "openems INTM4TM2.xml: Substrate Offset",
                 "palace INTM4TM2.xml: Substrate Thickness",
                 "palace INTM4TM2.xml: Substrate Offset",
                 "palace INTM4TM2.xml: BACKSIDEGND Zmin",
                 f"{TPL}: (general (thickness))",
                 f"{TPL}: stackup sums to",
                 f"{TPL}: stackup item B.Mask thickness",
                 f"{STU}: Substrate z", f"{STU}: Substrate height"):
        assert any(f.startswith(copy) for f in found), (copy, found)


def test_a_moved_metal_in_the_key_names_every_copy(tmp_path):
    """A metal bound moved in the key alone leaves every EM file, the template and Studio behind."""
    paths = _copies(tmp_path)
    _edit(paths["key"], '{"name": "Metal5", "zmin_um": 1.67, "zmax_um": 2.16}',
          '{"name": "Metal5", "zmin_um": 1.67, "zmax_um": 2.26}')
    found = disagreements(*(paths[n] for n in SOURCES))
    for copy in ("openems INTM4TM2.xml: Metal5 Zmax", "palace INTM4TM2.xml: Metal5 Zmax",
                 "openems INTM4TM2_nosub.xml: Metal5 Zmax", "palace INTM4TM2_nosub.xml: Metal5 Zmax",
                 f"{TPL}: stackup item In2.Cu thickness",
                 f"{TPL}: stackup item dielectric 2 thickness",
                 f"{STU}: Metal5 height", f"{STU}: Metal5.filler height",
                 f"{STU}: TopVia1 z", f"{STU}: TopVia1 height", f"{ITF_LBL}: Metal5 Zmax"):
        assert any(f.startswith(copy) for f in found), (copy, found)


def test_a_new_passivation_value_names_every_copy(tmp_path):
    """A thicker oxide film in the key alone leaves every EM stack and the ITF behind."""
    paths = _copies(tmp_path)
    _edit(paths["key"], '"thickness_um": 1.5}', '"thickness_um": 2.0}')
    found = disagreements(*(paths[n] for n in SOURCES))
    for em in ("openems INTM4TM2.xml", "palace INTM4TM2.xml",
               "openems INTM4TM2_nosub.xml", "palace INTM4TM2_nosub.xml"):
        for copy in (f"{em}: SiO2 Zmax", f"{em}: Passive Zmin", f"{em}: Passive Zmax"):
            assert any(f.startswith(copy) for f in found), (copy, found)
    for copy in (f"{ITF_LBL}: passivation oxide ends", f"{ITF_LBL}: passivation nitride ends"):
        assert any(f.startswith(copy) for f in found), (copy, found)


@pytest.mark.parametrize("old,new", [
    ('"attachment_surface_z_um": 10.8103', '"attachment_surface_z_um": 13.83'),
    ('{"name": "Metal5", "zmin_um": 1.67, "zmax_um": 2.16}',
     '{"name": "Metal5", "zmin_um": 1.67, "zmax_um": 3.2}'),
    ('"thickness_um": 0.4}', '"thickness_um": 0.0}'),
], ids=["attach off the top metal", "metals overlap", "a film of no thickness"])
def test_an_inconsistent_key_is_refused(tmp_path, old, new):
    paths = _copies(tmp_path)
    _edit(paths["key"], old, new)
    with pytest.raises(AssertionError):
        load_key(paths["key"])
