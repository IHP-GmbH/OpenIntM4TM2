#!/usr/bin/env python3
"""The capacitance a routed interposer net loads its driver with: halo, check and solve.

usage:
  routed_load.py halo  --traces T --bumps B --nets n1,n2,.. --halo-um M4=w,M5=w,TM1=w,TM2=w
                       --halo-source <sha256> --out <record.json> --sidecar <nofill.json>
  routed_load.py check --record R --gds <interposer.gds> --chiplet <design.chiplet>
                       --out <check.json> [--traces T --bumps B]
  routed_load.py solve --record R --check C --traces T --bumps B --chiplet D --methods M
                       --pins U1=P,U2=P --out <load.json> [--nets n1,..]
                       [--budget-ff F --outside-ff F]
  routed_load.py sections --record R --check C --traces T --bumps B --chiplet D --methods M
                       --pins U1=P,U2=P [--nets n1,..]

The traces and bumps are interposer-pnr's records of one route, in interposer GDS um with
the origin at the board's lower-left corner, Y up.

halo (stdlib only, no solve) names the bounded nets (every lane) and writes the keep-out
the fill must leave around them: one region per footprint and fill metal, each footprint
grown by that metal's halo_um plus one grid step and rounded outward onto the grid. A
footprint is a segment (its centreline extent, its width across, half the width past each
end), a TopVia2_C landing (a square of the cut TV2_a plus the enclosure TV2_d on each side)
or a landing pad (a landing_um square on the bump). The grid and the via rules come from
the PDK's pycell tech file. The record carries the widths, the window and the sidecar's sha.

check (routed_load_check.py, klayout.db) reads the finished GDS against the record, with
the layer numbers of the PDK's layer map: (i) lane metal traced from the landing pads,
(ii) metal within window_um of a lane outside every net's footprints, and own metal in a
lane's landing square beyond the pad disc, and (iii) dummy fill inside the halo. Metal
outside the footprints that touches one net only and is Manhattan is a patch of that net,
named in the report and modelled by the solve; so is a lane's metal in its landing square
beyond the disc, by its bounding box. Exit 0 pass, 3 a finding, 2 refused.

solve gives, for each bounded net, an upper value of the capacitance its driver sees on
the interposer, under these potentials: the net at +1 V; signal nets of the route at -1 V
(they switch the other way, which counts each coupling twice); rails at 0 V; the dummy
fill and the dies at -1 V. Lowering any region other than the net to -1 V can only raise
the net's charge (comparison principle), so the fill and dies need no model of their
own potentials. Removing the net's own metal from a section can only raise it too.
Each piece is solved as a 2D cross-section (field2d.py):

- wires: every segment at its layer and width, half a width past each end, with every
  conductor within WINDOW_UM of it: wires of every net, other nets' landing pads,
  TopVia2_C columns and pillar columns, and the patches. A wire of another net that runs
  across enters over its own width grown by its distance from the section's wire.
- vias: each TopVia2_C of the net, a column of its landing square (TV2_a + 2 TV2_d)
  from TopMetal1 to TopMetal2, solved the same way along its host wire. A via's sections
  leave out its host wires and the wires' sections leave out the via: via and host are
  counted on their own, their overlap twice.
- fill: on each fill metal, everything outside the halo, less every modelled conductor
  grown by the fill clearance of interposer_tech_default.json, at -1 V out to the box.
- dies: a plane at -1 V over each die's outline, from the attach plane plus the rigid
  part of the connection stack (the stack without its solder) up: the lowest standoff.
- pads: each landing pad of the net is a disc on the top metal, d/2 plus one grid step,
  with its pillar up to the full stack, solved as a body of revolution. Every band of the
  stack holds -1 V from the nearest conductor of another net, the halo's edge or the
  window, whichever is nearest, out to the box; the die face holds -1 V outside the die's
  opening, and inside it from the nearest other pillar outward. The own conductor at any
  standoff between the lowest and the full stack lies inside this one, and every -1 V
  region inside this geometry's, so one solve bounds them all.
- fictitious metal (a crossing wire's grown parts, another net's pillar as a square of the
  stack's widest diameter) holds -1 V whatever its net, so every enlargement is one-signed.

Every across coordinate of a section is snapped to the 0.1 nm grid, and one more than
1e-9 um off it is refused: drawn metal is on the 1 nm grid, so the snap removes float noise
and moves nothing. Same-net metal that touches or overlaps a wire is one conductor with it
and is never dropped from its sections.

The silicon carrier top is the ground plane. The dielectric is the stack key's, flat:
BEOL oxide up to the attach plane, each passivation film, then air.

With --budget-ff and --outside-ff, each net is checked against the room the budget
leaves for the interposer: the budget minus the physical load outside the interposer.
Exit status of solve: 0 written and no net over; 3 written and a net over; 2 refused.

sections reads what solve reads and builds every section solve would solve, with no solve
and nothing written: each passes the solve's refusals, and the same-net metal along each
piece is checked to be in its sections. It prints each net's section count and smallest
mesh step. Exit 0 every section built; 2 refused: an input, two conductors that overlap,
same-net metal left out, or a mesh step under 1e-9 um.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import field2d  # noqa: E402

FORMAT, VERSION = 'intm4tm2.routed_load', 2
CHECK_FORMAT, CHECK_VERSION = 'intm4tm2.routed_load.check', 1
TRACES_FORMAT, BUMPS_FORMAT = 'interposer_pnr.routed_traces', 'interposer_pnr.bumps'
BUMPS_FRAME = 'interposer canonical um, GDS origin at (0, 0), Y up'
SIDECAR_FRAME = 'traces_um_y_up'
KEY = HERE.parent / 'intm4tm2_stack.json'
TECH = HERE.parent / 'klayout' / 'tech' / 'drc' / 'rule_decks' / 'interposer_tech_default.json'
PYCELL_TECH = HERE.parent / 'klayout' / 'python' / 'intm4tm2_pycell_lib' / 'intm4tm2_tech.json'
LAYER_MAP = HERE.parent / 'klayout' / 'tech' / 'intm4tm2_layers.txt'
WINDOW_UM = 60.0      # neighbours this close to an edge enter the section
BOX_UM = 300.0        # grounded box margin around the section's conductors
MILLER = 2.0          # a signal neighbour switching the other way
RAIL_CLASSES = ('ground', 'power')
VIA = 'TopVia2_C'
#: every across coordinate of a section sits on the 0.1 nm grid; one farther than
#: SNAP_TOL_UM off it is not float noise and is refused, never moved
SNAP_DECIMALS, SNAP_TOL_UM = 4, 1e-9


class Refused(Exception):
    """An input this tool cannot read as given."""


def tech_geometry(path=PYCELL_TECH):
    """(grid, TopVia2 cut, TopVia2_C landing) in nm, from the PDK's pycell tech: the
    landing is the cut TV2_a with the enclosure TV2_d on each side. Refused when the file
    cannot be read or does not give them as sizes."""
    try:
        p = json.loads(Path(path).read_text())['techParams']
        grid, cut, enc = p['grid'], p['TV2_a'], p['TV2_d']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused('%s: no grid, TV2_a and TV2_d under techParams (%s)' % (path, exc))
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (grid, cut, enc)) \
            or grid <= 0 or cut <= 0 or enc < 0:
        raise Refused('%s: grid %r, TV2_a %r and TV2_d %r are not sizes' % (path, grid, cut, enc))
    return round(grid * 1000), round(cut * 1000), round((cut + 2 * enc) * 1000)


try:
    GRID_NM, VIA_CUT_NM, VIA_LANDING_NM = tech_geometry()
    TECH_REFUSED = None
except Refused as exc:
    # every mode refuses with it before reading anything (main)
    GRID_NM = VIA_CUT_NM = VIA_LANDING_NM = None
    TECH_REFUSED = exc

ROUTE_LAYERS = ('TopMetal1', 'TopMetal2')
#: the fill metals, as the sidecar names them
ROLES = ('M4', 'M5', 'TM1', 'TM2')
ROLE_METAL = {'M4': 'Metal4', 'M5': 'Metal5', 'TM1': 'TopMetal1', 'TM2': 'TopMetal2'}
ROLE_CLEARANCE = {'M4': 'MFil_c', 'M5': 'MFil_c', 'TM1': 'TM1Fil_c', 'TM2': 'TM2Fil_c'}
FOOTPRINT = None if TECH_REFUSED else (
    'segment: centreline extent, width across, half the width past each end; '
    'via: TopVia2_C landing, a square of TV2_a + 2 TV2_d (%g um); '
    'pad: landing_um square on the bump; '
    'each grown by halo_um plus one grid step (%g um), rounded outward onto the grid; '
    'one region per footprint and metal, none merged'
    % (VIA_LANDING_NM / 1000, GRID_NM / 1000))
#: connection-stack materials: the rigid ones hold the die face up, solder reflows;
#: any other is refused
RIGID = ('Cu', 'Ni')
SOLDER = ('SAC305', 'SnAg')
#: the solve's terms; tests switch them off one at a time
TERMS = ('caps', 'vias', 'perpendicular', 'envelope', 'die', 'pillars', 'patches')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused('%s: %s' % (path, exc))


# ----------------------------------------------------------------------------
# footprints and the halo

def nm(v):
    """um to nm; refuses a value off the 1 nm traces grid."""
    n = round(v * 1000)
    if abs(n - v * 1000) > 1e-6:
        raise Refused('%r um is not on the 1 nm grid' % v)
    return n


def snap(v):
    """um onto the 0.1 nm grid of a section's across coordinates. Drawn metal sits on the
    1 nm grid, so this removes float noise and nothing else; a value farther than
    SNAP_TOL_UM off the grid is refused."""
    s = round(v, SNAP_DECIMALS)
    if abs(s - v) > SNAP_TOL_UM:
        raise Refused('%r um is off the 0.1 nm grid of a section' % v)
    return s


def footprints(traces, bumps, nets, pads_required=True):
    """[(kind, net, layer, x0, y0, x1, y1)] in nm, before growth; layer None means both metals."""
    by_name = {n['name']: n for n in traces['nets']}
    out = []
    for name in nets:
        net = by_name.get(name)
        if net is None:
            raise Refused('%s is not in traces.json' % name)
        for s in net['segments']:
            if s['layer'] not in ROUTE_LAYERS:
                raise Refused('%s: segment on %s' % (name, s['layer']))
            x0, x1 = sorted((nm(s['x0']), nm(s['x1'])))
            y0, y1 = sorted((nm(s['y0']), nm(s['y1'])))
            if x0 != x1 and y0 != y1:
                raise Refused('%s: segment off the axes' % name)
            h = -(-nm(s['width']) // 2)
            out.append(('segment', name, s['layer'], x0 - h, y0 - h, x1 + h, y1 + h))
        for v in net.get('vias', []):
            if v['via'] != VIA or (v['bottom_layer'], v['top_layer']) != ROUTE_LAYERS:
                raise Refused('%s: via %s' % (name, v['via']))
            h = VIA_LANDING_NM // 2
            out.append(('via', name, None, nm(v['x']) - h, nm(v['y']) - h, nm(v['x']) + h, nm(v['y']) + h))
        pads = [b for b in bumps['bumps'] if b['net'] == name]
        if not pads and pads_required:
            raise Refused('%s has no landing pad in bumps.json' % name)
        for b in pads:
            h = -(-nm(b['landing_um']) // 2)
            out.append(('pad', name, None, nm(b['x_um']) - h, nm(b['y_um']) - h,
                        nm(b['x_um']) + h, nm(b['y_um']) + h))
    return out


def grown(fp, halo_nm):
    """A footprint grown by halo_nm plus one grid step, rounded outward onto the grid."""
    g = halo_nm + GRID_NM
    x0, y0, x1, y1 = fp[-4:]
    return ((x0 - g) // GRID_NM * GRID_NM, (y0 - g) // GRID_NM * GRID_NM,
            -(-(x1 + g) // GRID_NM) * GRID_NM, -(-(y1 + g) // GRID_NM) * GRID_NM)


def records(fps, halo_um):
    """Sidecar regions, {"role", "polygon_um"}, for halo_um {role: um}."""
    out = []
    for role in ROLES:
        w = nm(halo_um[role])
        if w == 0:
            continue
        for fp in fps:
            x0, y0, x1, y1 = grown(fp, w)
            out.append({'role': role, 'polygon_um': [[x0 / 1000, y0 / 1000], [x1 / 1000, y0 / 1000],
                                                     [x1 / 1000, y1 / 1000], [x0 / 1000, y1 / 1000]]})
    return out


def read_route(traces_path, bumps_path):
    traces = read_json(traces_path)
    if traces.get('format') != TRACES_FORMAT or traces.get('version') != 1:
        raise Refused('%s: not %s version 1' % (traces_path, TRACES_FORMAT))
    bumps = read_json(bumps_path)
    if bumps.get('format') != BUMPS_FORMAT or bumps.get('version') != 1:
        raise Refused('%s: not %s version 1' % (bumps_path, BUMPS_FORMAT))
    if bumps.get('frame') != BUMPS_FRAME:
        raise Refused('%s: frame %r, not %r' % (bumps_path, bumps.get('frame'), BUMPS_FRAME))
    return traces, bumps


def parse_halo_um(text):
    """'M4=20,M5=20,TM1=60,TM2=60' -> {role: um}; every role once, each >= 0 on the grid."""
    out = {}
    for item in text.split(','):
        role, _, value = item.partition('=')
        if role not in ROLES or role in out:
            raise Refused('--halo-um: %r is not one of %s, or comes twice' % (role, ', '.join(ROLES)))
        try:
            w = float(value)
        except ValueError:
            raise Refused('--halo-um: %s=%r is not a number' % (role, value))
        if not w >= 0 or nm(w) % GRID_NM:
            raise Refused('--halo-um: %s=%r is not a width >= 0 on the %g um grid'
                          % (role, value, GRID_NM / 1000))
        out[role] = w
    if set(out) != set(ROLES):
        raise Refused('--halo-um: give all of %s' % ', '.join(ROLES))
    return out


def halo(traces_path, bumps_path, nets, halo_um, halo_source, out, sidecar):
    """Write the sidecar and the halo record. Raises Refused before writing anything."""
    for p in (out, sidecar):
        if os.path.lexists(p):
            raise Refused('%s exists; give a new path' % p)
    if Path(out).resolve() == Path(sidecar).resolve():
        raise Refused('the record and the sidecar need two paths')
    if not re.fullmatch(r'[0-9a-f]{64}', halo_source or ''):
        raise Refused('--halo-source is not a sha256')
    if not nets or len(set(nets)) != len(nets):
        raise Refused('name each net once')
    traces, bumps = read_route(traces_path, bumps_path)
    by_name = {n['name']: n for n in traces['nets']}
    for name in nets:
        if name in by_name and not by_name[name]['segments']:
            raise Refused('%s has no segment in traces.json' % name)
    regions = records(footprints(traces, bumps, nets), halo_um)
    if not regions:
        raise Refused('every halo width is 0: there is nothing to keep out')
    side = {'version': 1, 'frame': SIDECAR_FRAME, 'regions': regions}
    with open(sidecar, 'x') as f:
        json.dump(side, f, indent=1)
        f.write('\n')
    record = {
        'format': FORMAT,
        'version': VERSION,
        'mode': 'halo',
        'inputs': {name: {'path': str(p), 'sha256': sha256(p)}
                   for name, p in (('traces', traces_path), ('bumps', bumps_path),
                                   ('pycell_tech', PYCELL_TECH),
                                   ('routed_load', Path(__file__)),
                                   ('field2d', HERE / 'field2d.py'))},
        'nets': list(nets),
        'method': {'window_um': WINDOW_UM, 'halo_um': dict(halo_um), 'grid_um': GRID_NM / 1000,
                   'footprint': FOOTPRINT, 'halo_source': halo_source},
        'sidecar': {'path': str(sidecar), 'sha256': sha256(sidecar), 'regions': len(regions)},
    }
    with open(out, 'x') as f:
        json.dump(record, f, indent=1)
        f.write('\n')
    return record


# ----------------------------------------------------------------------------
# the stack

def load_stack(path=KEY):
    """Metal z bounds and the flat dielectric bands, from the stack key."""
    key = read_json(path)
    beol = key['beol']
    metals = {m['name']: (m['zmin_um'], m['zmax_um']) for m in beol['metals']}
    top = beol['attachment_surface_z_um']
    bands = [(0.0, top, beol['dielectric']['epsilon_r'])]
    z = top
    for film in beol.get('passivation', []):
        bands.append((z, z + film['thickness_um'], film['epsilon_r']))
        z += film['thickness_um']
    if z >= BOX_UM:
        raise Refused('the stack reaches the box top')
    bands.append((z, BOX_UM + z, 1.0))
    pad_metal = beol['metals'][-1]['name']
    return {'metals': metals, 'bands': bands, 'ztop': BOX_UM + z, 'attach': top,
            'eps_ox': beol['dielectric']['epsilon_r'], 'pad_metal': pad_metal}


# ----------------------------------------------------------------------------
# the route

class Wire:
    """A Manhattan segment: centreline from a0 to a1 along its axis, at c across it."""

    def __init__(self, net, layer, orient, c, a0, a1, w):
        self.net, self.layer, self.orient = net, layer, orient
        self.c, self.a0, self.a1, self.w = c, min(a0, a1), max(a0, a1), w

    def metal(self):
        """(along lo, along hi, across lo, across hi) of the drawn metal."""
        h = self.w / 2
        return self.a0 - h, self.a1 + h, self.c - h, self.c + h

    def plan(self):
        """(x0, y0, x1, y1) of the drawn metal."""
        a0, a1, c0, c1 = self.metal()
        return (a0, c0, a1, c1) if self.orient == 'h' else (c0, a0, c1, a1)


class Pad:
    def __init__(self, net, x, y, d, layer, component=None, pad=None):
        self.net, self.x, self.y, self.d, self.layer = net, x, y, d, layer
        self.component, self.pad = component, pad

    def as_wire(self, orient):
        """The pad's bounding square as a wire of no length and the pad's width."""
        if orient == 'h':
            return Wire(self.net, self.layer, 'h', self.y, self.x, self.x, self.d)
        return Wire(self.net, self.layer, 'v', self.x, self.y, self.y, self.d)


class Column:
    """A conductor box: plan (x0, y0, x1, y1) between z0 and z1."""

    def __init__(self, net, kind, x0, y0, x1, y1, z0, z1):
        self.net, self.kind = net, kind
        self.x0, self.y0, self.x1, self.y1, self.z0, self.z1 = x0, y0, x1, y1, z0, z1

    def plan(self):
        return self.x0, self.y0, self.x1, self.y1


def wire_of_rect(net, layer, x0, y0, x1, y1):
    """A rectangle of metal as the segment that draws it: along its longer side."""
    if x1 - x0 >= y1 - y0:
        w = y1 - y0
        return Wire(net, layer, 'h', (y0 + y1) / 2, x0 + w / 2, x1 - w / 2, w)
    w = x1 - x0
    return Wire(net, layer, 'v', (x0 + x1) / 2, y0 + w / 2, y1 - w / 2, w)


def read_traces(path, stack, data=None):
    """Wires (not yet merged), vias as (net, x, y), net classes and drawn lengths."""
    data = read_json(path) if data is None else data
    if data.get('format') != TRACES_FORMAT or data.get('version') != 1:
        raise Refused('%s: not %s version 1' % (path, TRACES_FORMAT))
    wires, vias, classes, raw = [], [], {}, {}
    for net in data['nets']:
        name = net['name']
        classes[name] = net.get('net_class')
        raw[name] = {}
        for s in net['segments']:
            layer = s['layer']
            if layer not in stack['metals']:
                raise Refused('net %s: layer %s is not in the stack key' % (name, layer))
            x0, y0, x1, y1, w = s['x0'], s['y0'], s['x1'], s['y1'], s['width']
            if not w or w <= 0:
                raise Refused('net %s: a segment with no width' % name)
            length = abs(x1 - x0) + abs(y1 - y0)
            raw[name][layer] = raw[name].get(layer, 0.0) + length
            if x0 != x1 and y0 != y1:
                raise Refused('net %s: a segment off the axes, (%g, %g) to (%g, %g)'
                              % (name, x0, y0, x1, y1))
            if y0 == y1:
                wires.append(Wire(name, layer, 'h', y0, x0, x1, w))
            else:
                wires.append(Wire(name, layer, 'v', x0, y0, y1, w))
        for v in net.get('vias', []):
            if v['via'] != VIA or (v['bottom_layer'], v['top_layer']) != ROUTE_LAYERS:
                raise Refused('net %s: via %s from %s to %s is not modelled'
                              % (name, v['via'], v.get('bottom_layer'), v.get('top_layer')))
            vias.append((name, v['x'], v['y']))
    return wires, vias, classes, raw


def merge(wires):
    """Join collinear wires of one net, layer and width that overlap or touch."""
    groups = {}
    for w in wires:
        groups.setdefault((w.net, w.layer, w.orient, w.c, w.w), []).append(w)
    out = []
    for (net, layer, orient, c, width), ws in sorted(groups.items()):
        ws.sort(key=lambda w: w.a0)
        cur = ws[0]
        for w in ws[1:]:
            if w.a0 <= cur.a1:
                cur = Wire(net, layer, orient, c, cur.a0, max(cur.a1, w.a1), width)
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return out


def read_bumps(path, stack, data=None):
    data = read_json(path) if data is None else data
    if data.get('format') != BUMPS_FORMAT or data.get('version') != 1:
        raise Refused('%s: not %s version 1' % (path, BUMPS_FORMAT))
    pads = []
    for b in data['bumps']:
        if not b.get('landing_um'):
            raise Refused('bump %s %s of net %s has no landing_um'
                          % (b['component'], b.get('pad'), b['net']))
        pads.append(Pad(b['net'], b['x_um'], b['y_um'], b['landing_um'], stack['pad_metal'],
                        b.get('component'), b.get('pad')))
    return pads


# ----------------------------------------------------------------------------
# the assembly: what the solve needs beyond the route

class Assembly:
    """Dies, fill and patches around the route.

    dies: {component: {'rect': (x0, y0, x1, y1), 'face': {'low': z, 'high': z},
    'pillar_um': diameter}}; openings: {(component, pad): radius of the die's opening};
    halo_um and clearance_um: {role: um}; lanes: the nets the halo was grown around;
    patches: [{'net', 'layer', 'rects_um'}].
    """

    def __init__(self, dies=None, openings=None, halo_um=None, clearance_um=None, lanes=(),
                 patches=()):
        self.dies = dies or {}
        self.openings = openings or {}
        self.halo_um = halo_um or {}
        self.clearance_um = clearance_um or {}
        self.lanes = list(lanes)
        self.patches = list(patches)


# ----------------------------------------------------------------------------
# sections

class Solver:
    """Cached cross-section solves on one stack."""

    def __init__(self, stack):
        self.stack, self.cache, self.solves = stack, {}, 0

    def section(self, target, others, x0, x1):
        """Charge on a target strip at +1 V: (static, Miller) in F/m.

        target: (xlo, xhi, zlo, zhi). others: ((xlo, xhi, zlo, zhi, role), ...) with role
        'own' (+1 V), 'signal' (-1 V under Miller), 'rail' (0 V), or 'low' (-1 V under
        Miller: fill, dies, fictitious metal). Static holds everything but the net at 0 V.
        Own metal that touches the target joins it: field2d gives the nodes they share to
        one of them, so the target's charge is read as the sum over both.
        """
        conductors, names, joined = section_conductors(target, others)
        # exact: a rounded key hands one lane's solve to another lane's slightly different
        # section, and the value then depends on the order the lanes run in
        key = ('section', target, tuple(others), x0, x1)
        if key not in self.cache:
            sec = field2d.Section(x0, x1, self.stack['ztop'], self.stack['bands'], conductors)
            hi = dict({'t': 1.0}, **{n: 1.0 for n in names['own']})
            q = sec.solve(hi)[0]
            static = sum(q[n] for n in joined)
            down = names['signal'] + names['low']
            if down:
                q = sec.solve(dict(hi, **{n: -1.0 for n in down}))[0]
                miller = sum(q[n] for n in joined)
                self.solves += 2
            else:
                miller = static
                self.solves += 1
            self.cache[key] = (static, miller)
        return self.cache[key]

    def revolution(self, target, lows, x1):
        """Charge, in F, on a body of revolution at +1 V: (static, Miller).

        target: ((rlo, rhi, zlo, zhi), ...) at +1 V; lows: the same, at -1 V under Miller.
        """
        conductors = revolution_conductors(target, lows)
        key = ('revolution', target, lows, x1)
        if key not in self.cache:
            sec = field2d.Section(0.0, x1, self.stack['ztop'], self.stack['bands'], conductors,
                                  axisymmetric=True)
            hi = {'t%d' % k: 1.0 for k in range(len(target))}
            q = sec.solve(hi)[0]
            static = sum(q[n] for n in hi)
            if lows:
                q = sec.solve(dict(hi, **{'n%d' % k: -1.0 for k in range(len(lows))}))[0]
                miller = sum(q[n] for n in hi)
                self.solves += 2
            else:
                miller = static
                self.solves += 1
            self.cache[key] = (static, miller)
        return self.cache[key]


class Survey:
    """The solve's sections built and checked, not solved: the sections mode.

    Each section passes the solve's own refusals (a conductor at another potential that
    touches the bounded one, two conductors that overlap, a mesh step under field2d.MEET_UM);
    the smallest mesh steps are kept.
    """

    def __init__(self, stack):
        self.stack, self.sections, self.revolutions = stack, 0, 0
        self.dx = self.dz = math.inf

    def _mesh(self, x0, x1, conductors):
        xs, zs = field2d.mesh(x0, x1, self.stack['ztop'], self.stack['bands'], conductors)
        self.dx = min([self.dx] + [b - a for a, b in zip(xs, xs[1:])])
        self.dz = min([self.dz] + [b - a for a, b in zip(zs, zs[1:])])

    def section(self, target, others, x0, x1):
        self._mesh(x0, x1, section_conductors(target, others)[0])
        self.sections += 1
        return 0.0, 0.0

    def revolution(self, target, lows, x1):
        self._mesh(0.0, x1, revolution_conductors(target, lows))
        self.revolutions += 1
        return 0.0, 0.0


def section_conductors(target, others):
    """field2d's conductors for a section, their names by role, and the names joined with the
    target. Refuses a conductor at another potential that touches the target."""
    for o in others:
        if o[4] != 'own' and touches(target, o):
            raise Refused('a conductor at another potential touches the bounded one: %r' % (o,))
    conductors = [('t',) + target]
    names = {'own': [], 'signal': [], 'rail': [], 'low': []}
    joined = ['t']
    for k, (lo, hi, zlo, zhi, role) in enumerate(others):
        name = 'n%d' % k
        if role == 'own' and touches(target, (lo, hi, zlo, zhi)):
            name = 't%d' % k
            joined.append(name)
        conductors.append((name, lo, hi, zlo, zhi))
        names[role].append(name)
    return conductors, names, joined


def revolution_conductors(target, lows):
    """field2d's conductors for a body of revolution. Refuses a -1 V one touching the target."""
    for t in target:
        for o in lows:
            if touches(t, o):
                raise Refused('a conductor at -1 V touches the bounded one: %r' % (o,))
    return [('t%d' % k,) + c for k, c in enumerate(target)] + \
        [('n%d' % k,) + c for k, c in enumerate(lows)]


def touches(a, b):
    """Whether two closed rectangles (lo, hi, zlo, zhi, ...) share a point. Conductors that
    touch share their boundary nodes in field2d, which would short them."""
    return a[0] <= b[1] and b[0] <= a[1] and a[2] <= b[3] and b[2] <= a[3]


def lateral(a, b):
    """Edge-to-edge gap across two parallel wires; negative if they overlap."""
    return abs(a.c - b.c) - (a.w + b.w) / 2


def interval_gap(lo0, hi0, lo1, hi1):
    """Gap between two intervals; negative if they overlap."""
    return max(lo1 - hi0, lo0 - hi1)


def union(spans):
    """Merge intervals that overlap or touch."""
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [tuple(s) for s in out]


def subtract(spans, cuts):
    """The parts of spans outside every cut."""
    out = []
    cuts = union(cuts)
    for lo, hi in spans:
        cur = lo
        for clo, chi in cuts:
            if chi <= cur or clo >= hi:
                continue
            if clo > cur:
                out.append((cur, clo))
            cur = max(cur, chi)
        if cur < hi:
            out.append((cur, hi))
    return out


def bands_union(rects):
    """Non-overlapping rectangles covering rects (lo, hi, zlo, zhi) of one conductor.

    Band by band in z, intervals that overlap or touch are merged; bands with the same
    intervals that touch are joined.
    """
    zs = sorted({r[2] for r in rects} | {r[3] for r in rects})
    rows = []
    for za, zb in zip(zs, zs[1:]):
        spans = union([(r[0], r[1]) for r in rects if r[2] <= za and r[3] >= zb])
        if not spans:
            continue
        if rows and rows[-1][2] == za and rows[-1][0] == spans:
            rows[-1][2] = zb
        else:
            rows.append([spans, za, zb])
    return [(lo, hi, za, zb) for spans, za, zb in rows for lo, hi in spans]


def outside(r, t):
    """The parts of rectangle r (lo, hi, zlo, zhi) outside rectangle t, as rectangles that
    touch t; r itself when the two do not overlap."""
    lo, hi, zlo, zhi = r
    if not (lo < t[1] and t[0] < hi and zlo < t[3] and t[2] < zhi):
        return [r]
    out = []
    if lo < t[0]:
        out.append((lo, t[0], zlo, zhi))
    if t[1] < hi:
        out.append((t[1], hi, zlo, zhi))
    a, b = max(lo, t[0]), min(hi, t[1])
    if zlo < t[2]:
        out.append((a, b, zlo, t[2]))
    if t[3] < zhi:
        out.append((a, b, t[3], zhi))
    return out


def covered(r, rects):
    """Whether rectangle r (lo, hi, zlo, zhi) lies inside the union of rects."""
    zs = sorted({r[2], r[3]} | {v for q in rects for v in q[2:4] if r[2] < v < r[3]})
    for za, zb in zip(zs, zs[1:]):
        spans = union([(q[0], q[1]) for q in rects if q[2] <= za and zb <= q[3]])
        if not any(lo <= r[0] and r[1] <= hi for lo, hi in spans):
            return False
    return True


class Target:
    """A piece of the bounded net solved in sections along `orient`: at c across, w wide,
    from zlo to zhi, over the span s0..s1."""

    def __init__(self, net, orient, c, w, zlo, zhi, s0, s1, src=None):
        self.net, self.orient, self.c, self.w = net, orient, c, w
        self.zlo, self.zhi, self.s0, self.s1, self.src = zlo, zhi, s0, s1, src

    def across(self):
        """(lo, hi) across, relative to c, on the section grid."""
        return snap(-self.w / 2), snap(self.w / 2)


def frame(plan, orient):
    """(along lo, along hi, across lo, across hi) of a plan box for sections along orient."""
    x0, y0, x1, y1 = plan
    return (x0, x1, y0, y1) if orient == 'h' else (y0, y1, x0, x1)


class Route:
    """Everything a section reads: conductors by kind, roles, the assembly."""

    def __init__(self, wires, pads, vias, columns, roles, stack, asm, terms, lane_fps):
        self.wires, self.pads, self.vias, self.columns = wires, pads, vias, columns
        self.roles, self.stack, self.asm, self.terms = roles, stack, asm, terms
        self.lane_fps = lane_fps

    def role(self, net, own):
        return 'own' if net == own else ('rail' if self.roles.get(net) == 'rail' else 'signal')


def zone(t, route, neighbours=None):
    """What may enter t's sections: items and cut points.

    Items are (net, kind, lo, hi, zlo, zhi, along lo, along hi), lo and hi across relative
    to t.c; kind 'body' is drawn there, 'grown' is another net's crossing wire grown along,
    'pillar' another net's pillar as a square of the stack's widest diameter. Holes are
    (role, lo, hi, along lo, along hi) of the halo; dies (lo, hi, z, along lo, along hi),
    z being the die face.
    """
    st, terms = route.stack, route.terms
    metals = st['metals']
    t_lo, t_hi = t.across()
    items, holes, dies = [], [], []

    def take(net, kind, lo, hi, zlo, zhi, al, ah):
        al, ah = max(t.s0, al), min(t.s1, ah)
        if ah > al:
            items.append((net, kind, lo, hi, zlo, zhi, al, ah))

    def overlaps(lo, hi, zlo, zhi):
        return interval_gap(t_lo, t_hi, lo, hi) < 0 and zlo < t.zhi and t.zlo < zhi

    parallel = [w for w in route.wires if w.orient == t.orient]
    parallel += [p.as_wire(t.orient) for p in route.pads if p.net != t.net]
    for o in parallel:
        if o is t.src:
            continue
        gap = snap(abs(t.c - o.c) - (t.w + o.w) / 2)
        if gap > WINDOW_UM:
            continue
        lo, hi = o.metal()[:2]
        lo, hi = max(t.s0, lo), min(t.s1, hi)
        if hi <= lo:
            continue
        zlo, zhi = metals[o.layer]
        if gap < 0 and zlo < t.zhi and t.zlo < zhi:
            if o.net != t.net:
                raise Refused('nets %s and %s overlap on %s' % (t.net, o.net, o.layer))
            if t.src is None:
                # the via's host: via and host are counted on their own
                continue
            # own metal over a wire: one conductor with it (section_at), never dropped
        items.append((o.net, 'body', snap(o.c - t.c - o.w / 2), snap(o.c - t.c + o.w / 2),
                      zlo, zhi, lo, hi))
        if neighbours is not None and o.net != t.net and o.layer == t.src.layer:
            n = neighbours.setdefault((o.net, o.layer), [math.inf, 0.0])
            n[0] = min(n[0], gap)
            n[1] += hi - lo
    for o in route.wires:
        if o.orient == t.orient or o.net == t.net:
            continue
        oa, ob, oc0, oc1 = o.metal()
        lo, hi = snap(oa - t.c), snap(ob - t.c)
        zlo, zhi = metals[o.layer]
        if interval_gap(t_lo, t_hi, lo, hi) < 0 and oc0 < t.s1 and t.s0 < oc1 \
                and zlo < t.zhi and t.zlo < zhi:
            raise Refused('nets %s and %s cross on %s' % (t.net, o.net, o.layer))
        if 'perpendicular' not in terms:
            continue
        gx = max(interval_gap(t_lo, t_hi, lo, hi), 0.0)
        if gx > WINDOW_UM:
            continue
        gz = max(zlo - t.zhi, t.zlo - zhi, 0.0)
        g = math.hypot(gx, gz)
        take(o.net, 'body', lo, hi, zlo, zhi, oc0, oc1)
        take(o.net, 'grown', lo, hi, zlo, zhi, oc0 - g, oc0)
        take(o.net, 'grown', lo, hi, zlo, zhi, oc1, oc1 + g)
    for col in route.columns:
        if col.net == t.net:
            continue
        al, ah, lo, hi = frame(col.plan(), t.orient)
        lo, hi = snap(lo - t.c), snap(hi - t.c)
        if interval_gap(t_lo, t_hi, lo, hi) > WINDOW_UM:
            continue
        if overlaps(lo, hi, col.z0, col.z1) and al < t.s1 and t.s0 < ah:
            raise Refused('net %s: a %s of net %s overlaps it' % (t.net, col.kind, col.net))
        if col.kind == 'pillar':
            # above the face the die body holds -1 V around it
            z1 = col.face if 'die' in terms else col.z1
            take(col.net, 'pillar', lo, hi, col.z0, z1, al, ah)
        else:
            take(col.net, 'body', lo, hi, col.z0, col.z1, al, ah)
    if 'envelope' in terms:
        reach = (t_lo - WINDOW_UM, t_hi + WINDOW_UM)
        for role in ROLES:
            h = route.asm.halo_um[role]
            if h <= 0:
                continue
            for fp in route.lane_fps:
                al, ah, lo, hi = frame(fp, t.orient)
                lo, hi, al, ah = snap(lo - h - t.c), snap(hi + h - t.c), al - h, ah + h
                if interval_gap(reach[0], reach[1], lo, hi) > 0:
                    continue
                al, ah = max(t.s0, al), min(t.s1, ah)
                if ah > al:
                    holes.append((role, lo, hi, al, ah))
    if 'die' in terms:
        for comp, die in sorted(route.asm.dies.items()):
            al, ah, lo, hi = frame(die['rect'], t.orient)
            al, ah = max(t.s0, al), min(t.s1, ah)
            if ah > al:
                dies.append((snap(lo - t.c), snap(hi - t.c), die['face']['low'], al, ah))
    cuts = {t.s0, t.s1}
    for seq in (items, holes, dies):
        for it in seq:
            cuts.update(v for v in it[-2:] if t.s0 < v < t.s1)
    return items, holes, dies, sorted(cuts)


def section_at(t, mid, route, items, holes, dies):
    """The conductors of t's section at mid: (target, others, x0, x1).

    Every across coordinate is on the 0.1 nm grid (zone snaps the items), the target's edges
    included, so metal that abuts on the drawn grid touches instead of overlapping by a
    rounding error. Own metal that overlaps the target is one conductor with it: its parts
    outside the target touch it and join it.
    """
    st = route.stack
    target = t.across() + (t.zlo, t.zhi)
    by_net = {}
    fictitious = []
    for net, kind, lo, hi, zlo, zhi, al, ah in items:
        if al <= mid <= ah:
            if kind == 'body' or (kind == 'pillar' and route.role(net, t.net) != 'rail'):
                by_net.setdefault(net, []).append((lo, hi, zlo, zhi))
            else:
                fictitious.append((lo, hi, zlo, zhi))
    real = []
    for net, rects in by_net.items():
        role = route.role(net, t.net)
        for r in bands_union(rects):
            for lo, hi, zlo, zhi in (outside(r, target) if role == 'own' else [r]):
                real.append((zlo, zhi, lo, hi, role))
    real = [(lo, hi, zlo, zhi, role) for zlo, zhi, lo, hi, role in sorted(real)]
    # Fictitious metal (grown parts, a rail's pillar square) holds -1 V whatever its net, so
    # the enlargement only raises the charge. It stays off the drawn conductors and comes
    # first: a later conductor takes the nodes two of them share, so drawn metal keeps its
    # potential on its edge.
    solid = [target] + [r[:4] for r in real]
    others = []
    for zlo, zhi in sorted({(f[2], f[3]) for f in fictitious}):
        spans = union([(f[0], f[1]) for f in fictitious if f[2:] == (zlo, zhi)])
        block = [(s[0], s[1]) for s in solid if s[2] < zhi and zlo < s[3]]
        block += [(o[0], o[1]) for o in others if o[2] < zhi and zlo < o[3]]
        for lo, hi in subtract(spans, block):
            others.append((lo, hi, zlo, zhi, 'low'))
    others += real
    x0 = snap(min([target[0]] + [o[0] for o in others]) - BOX_UM)
    x1 = snap(max([target[1]] + [o[1] for o in others]) + BOX_UM)
    if 'envelope' in route.terms:
        conductors = [target] + [o[:4] for o in others]
        for role in ROLES:
            zlo, zhi = st['metals'][ROLE_METAL[role]]
            c = route.asm.clearance_um[role]
            block = [(lo, hi) for r, lo, hi, al, ah in holes if r == role and al <= mid <= ah]
            block += [(snap(o[0] - c), snap(o[1] + c)) for o in conductors
                      if o[2] <= zhi and zlo <= o[3]]
            for lo, hi in subtract([(x0, x1)], block):
                others.append((lo, hi, zlo, zhi, 'low'))
    for lo, hi, z, al, ah in dies:
        if al <= mid <= ah:
            lo, hi = max(lo, x0), min(hi, x1)
            if hi > lo:
                others.append((lo, hi, z, st['ztop'], 'low'))
    return target, tuple(others), x0, x1


def targets_of(net, route, wires):
    """The wire pieces of the net, then its via columns."""
    st, terms = route.stack, route.terms
    out = []
    for w in wires:
        if w.net != net:
            continue
        zlo, zhi = st['metals'][w.layer]
        s0, s1 = (w.a0 - w.w / 2, w.a1 + w.w / 2) if 'caps' in terms else (w.a0, w.a1)
        if s1 > s0:
            out.append(('wire', Target(net, w.orient, w.c, w.w, zlo, zhi, s0, s1, w)))
    if 'vias' in terms:
        z0 = st['metals'][ROUTE_LAYERS[0]][0]
        z1 = st['metals'][ROUTE_LAYERS[1]][1]
        h = VIA_LANDING_NM / 2000
        for n, x, y in route.vias:
            if n != net:
                continue
            host = [w for w in wires if w.net == net and w.layer in ROUTE_LAYERS
                    and w.plan()[0] <= x <= w.plan()[2] and w.plan()[1] <= y <= w.plan()[3]]
            host.sort(key=lambda w: (w.layer != ROUTE_LAYERS[1], w.orient))
            orient = host[0].orient if host else 'h'
            a, c = (x, y) if orient == 'h' else (y, x)
            out.append(('via', Target(net, orient, c, 2 * h, z0, z1, a - h, a + h)))
    return out


def pad_radius(d):
    """The radius of a landing pad's disc: d/2 plus one grid step. The drawn disc's
    vertices sit on the grid, up to half a grid diagonal outside d/2."""
    return d / 2 + GRID_NM / 1000


def pad_room(p, asm, terms, stack):
    """(die, pillar radius, opening radius) of a pad of the net, each None when its term is
    off. Refuses a pad whose die, or whose opening in the die face, leaves no room; a pad
    the fill may reach; and a pad within the window of a die whose face sits at another
    height, which the pad's one face plane cannot hold."""
    if 'envelope' in terms:
        for role in ROLES:
            if ROLE_METAL[role] == stack['pad_metal'] and asm.halo_um[role] * 1000 <= GRID_NM + 1e-6:
                raise Refused('pad %s %s of net %s: halo_um %s = %g um lets the fill reach the pad. '
                              'The fill stays outside the pad disc, d/2 plus one grid step, so the '
                              '%s halo must exceed one grid step (%g um)'
                              % (p.component, p.pad, p.net, role, asm.halo_um[role], role,
                                 GRID_NM / 1000))
    if 'die' not in terms and 'pillars' not in terms:
        return None, None, None
    die = asm.dies.get(p.component)
    if die is None:
        raise Refused('pad %s %s of net %s: component %s is not a die of the assembly'
                      % (p.component, p.pad, p.net, p.component))
    r_p = die['pillar_um'] / 2 if 'pillars' in terms else None
    r_hole = None
    if 'die' in terms:
        r_hole = asm.openings.get((p.component, p.pad))
        if r_hole is None:
            raise Refused('pad %s %s: no opening in the die\'s pins.json' % (p.component, p.pad))
        if r_p is not None and r_hole <= r_p:
            raise Refused('pad %s %s: the die\'s opening (%g um) is no wider than the pillar'
                          % (p.component, p.pad, 2 * r_hole))
        for comp, other in sorted(asm.dies.items()):
            if comp == p.component or other['face']['low'] == die['face']['low']:
                continue
            x0, y0, x1, y1 = other['rect']
            gap = math.hypot(max(x0 - p.x, 0.0, p.x - x1), max(y0 - p.y, 0.0, p.y - y1))
            if gap < p.d / 2 + WINDOW_UM:
                raise Refused('pad %s %s of net %s: die %s, whose face sits at another height, '
                              'lies %g um from it, within the window'
                              % (p.component, p.pad, p.net, comp, gap))
    return die, r_p, r_hole


def pad_load(p, route, solver, bodies):
    """(static, Miller) in F of one pad of the net with its pillar, in one geometry: the die
    face at the lowest standoff, the pillars up to the full stack."""
    st, asm, terms = solver.stack, route.asm, route.terms
    tz0, tz1 = st['metals'][st['pad_metal']]
    attach = st['attach']
    rd = pad_radius(p.d)
    r_zone = p.d / 2 + WINDOW_UM
    x1 = r_zone + BOX_UM
    die, r_p, r_hole = pad_room(p, asm, terms, st)
    face = top = None
    if die is not None:
        face, top = die['face']['low'], die['face']['high']
    near = []
    for b in bodies:
        x0, y0, xb, yb = b.plan()
        d = math.hypot(max(x0 - p.x, 0.0, p.x - xb), max(y0 - p.y, 0.0, p.y - yb))
        if d < r_zone:
            near.append((d, b))
    target = [(0.0, rd, tz0, tz1)]
    if r_p is not None:
        target.append((0.0, r_p, attach, top))
    if 'die' in terms:
        ceiling = top if r_p is not None else face
    else:
        ceiling = st['ztop']
    breaks = {0.0, ceiling, attach, tz0, tz1}
    breaks.update(v for m in st['metals'].values() for v in m)
    breaks.update(v for b in st['bands'] for v in b[:2])
    if die is not None:
        breaks.update((face, top))
    for _, b in near:
        breaks.update((b.z0, b.z1))
    zs = sorted(v for v in breaks if 0.0 <= v <= ceiling)
    lows = []
    for za, zb in zip(zs, zs[1:]):
        inside = 'die' in terms and za >= face     # in the die body, within its opening
        rout = r_hole if inside else x1
        r = rout if inside else r_zone
        if 'envelope' in terms and not inside:
            for role in ROLES:
                m0, m1 = st['metals'][ROLE_METAL[role]]
                if za < m1 and m0 < zb:
                    h = asm.halo_um[role]
                    r = min(r, p.d / 2 + h if h > 0 else 0.0)
        for d, b in near:
            if b.z0 < zb and za < b.z1 and d < r:
                r = d
        for (lo, hi, z0, z1) in target:
            if r <= hi and za <= z1 and z0 <= zb:
                who = [b.net for d, b in near if d <= hi and b.z0 < zb and za < b.z1]
                raise Refused('net %s comes within the pad of net %s at (%g, %g)'
                              % (who[0] if who else 'fill', p.net, p.x, p.y))
        if r < rout:
            if lows and lows[-1][:2] == (r, rout) and lows[-1][3] == za:
                lows[-1] = (r, rout, lows[-1][2], zb)
            else:
                lows.append((r, rout, za, zb))
    if r_hole is not None:
        lows.append((r_hole, x1, face, st['ztop']))
    return solver.revolution(tuple(target), tuple(lows), x1)


def pillar_columns(pads, asm, st):
    """Each pad's pillar as a square of the stack's widest diameter, from the attach plane
    up to the full stack; .face is its die's face, above which the die body holds it."""
    out = []
    for p in pads:
        die = asm.dies.get(p.component)
        if die is None:
            raise Refused('bump %s %s of net %s: component %s is not a die of the assembly'
                          % (p.component, p.pad, p.net, p.component))
        x0, y0, x1, y1 = die['rect']
        if not (x0 <= p.x <= x1 and y0 <= p.y <= y1):
            raise Refused('bump %s %s lies outside its die' % (p.component, p.pad))
        h = die['pillar_um'] / 2
        col = Column(p.net, 'pillar', p.x - h, p.y - h, p.x + h, p.y + h, st['attach'],
                     die['face']['high'])
        col.face = die['face']['low']
        out.append(col)
    return out


def own_left_out(t, a, b, mine, target, others, metals):
    """The first wire of the net that runs along t over part of the section's span a..b
    within the window, yet lies in neither the target nor the section's own metal; None if
    there is none. The span, not only its mid: a wire left out of the zone leaves its ends
    out of the cuts too. A via's host wires, the net's metal over its column, are left out
    by the via model and not looked at."""
    plus = [target] + [o[:4] for o in others if o[4] == 'own']
    for o in mine:
        lo, hi = o.metal()[:2]
        if not (max(t.s0, lo) < b and a < min(t.s1, hi)):
            continue
        if snap(abs(t.c - o.c) - (t.w + o.w) / 2) > WINDOW_UM:
            continue
        r = (snap(o.c - t.c - o.w / 2), snap(o.c - t.c + o.w / 2)) + tuple(metals[o.layer])
        if t.src is None and outside(r, target) != [r]:
            continue
        if not covered(r, plus):
            return o
    return None


def net_load(net, route, solver):
    """The load record of one net."""
    mine = [w for w in route.wires if w.net == net]
    if not mine:
        raise Refused('net %s has no routed segment' % net)
    st, terms = route.stack, route.terms
    static = miller = vias = 0.0
    pieces = 0
    neighbours = {}
    for kind, t in targets_of(net, route, route.wires):
        items, holes, dies, cuts = zone(t, route, neighbours if kind == 'wire' else None)
        along = [w for w in mine if w.orient == t.orient and w is not t.src]
        for a, b in zip(cuts, cuts[1:]):
            mid = (a + b) / 2
            target, others, x0, x1 = section_at(t, mid, route, items, holes, dies)
            left = own_left_out(t, a, b, along, target, others, st['metals'])
            if left is not None:
                raise Refused('net %s: its %s wire at %g leaves the section at %g along the '
                              '%s piece at %g; same-net metal is never dropped'
                              % (net, left.layer, left.c, mid, kind, t.c))
            s, m = solver.section(target, others, x0, x1)
            pieces += 1
            if kind == 'wire':
                static += s * (b - a) * 1e-6
                miller += m * (b - a) * 1e-6
            else:
                vias += m * (b - a) * 1e-6
    # pads: every conductor of another net, in plan, for the bands of the stack
    bodies = []
    for w in route.wires:
        if w.net != net:
            z0, z1 = st['metals'][w.layer]
            bodies.append(Column(w.net, 'wire', *w.plan(), z0, z1))
    for p in route.pads:
        if p.net != net:
            z0, z1 = st['metals'][p.layer]
            bodies.append(Column(p.net, 'pad', p.x - p.d / 2, p.y - p.d / 2, p.x + p.d / 2,
                                 p.y + p.d / 2, z0, z1))
    bodies += [c for c in route.columns if c.net != net]
    pad_static = pad_miller = 0.0
    my_pads = [p for p in route.pads if p.net == net]
    for p in my_pads:
        s, m = pad_load(p, route, solver, bodies)
        pad_static += s
        pad_miller += m
    return {
        'pads': len(my_pads),
        'vias': sum(1 for v in route.vias if v[0] == net),
        'pieces': pieces,
        'c_wires_static_f': static,
        'c_wires_f': miller,
        'c_vias_f': vias,
        'c_pads_static_f': pad_static,
        'c_pads_f': pad_miller,
        'c_total_f': miller + vias + pad_miller,
        'neighbours': [{'net': n, 'layer': lay, 'min_gap_um': round(g, 4),
                        'alongside_um': round(run, 4)}
                       for (n, lay), (g, run) in sorted(neighbours.items(),
                                                        key=lambda kv: (kv[1][0], kv[0]))],
    }


def route_of(traces, bumps, nets, asm=None, terms=TERMS, key=KEY):
    """The route the named nets are solved on, and each net's drawn length by layer. Raises
    Refused on an input it cannot read."""
    asm = asm or Assembly()
    terms = tuple(terms)
    unknown = set(terms) - set(TERMS)
    if unknown:
        raise Refused('no term %s' % ', '.join(sorted(unknown)))
    if not nets or len(set(nets)) != len(nets):
        raise Refused('name each net once')
    stack = load_stack(key)
    tdata, bdata = read_json(traces), read_json(bumps)
    wires, vias, classes, raw = read_traces(traces, stack, tdata)
    pads = read_bumps(bumps, stack, bdata)
    missing = [n for n in nets if n not in classes]
    if missing:
        raise Refused('not in the traces: %s' % ', '.join(missing))
    if 'caps' not in terms:
        wires = [w for w in wires if w.a1 > w.a0]
    if 'patches' in terms:
        for p in asm.patches:
            if p['layer'] not in stack['metals']:
                raise Refused('patch of net %s: layer %s is not in the stack key'
                              % (p['net'], p['layer']))
            for x0, y0, x1, y1 in p['rects_um']:
                wires.append(wire_of_rect(p['net'], p['layer'], x0, y0, x1, y1))
    wires = merge(wires)
    columns = []
    if 'vias' in terms:
        z0 = stack['metals'][ROUTE_LAYERS[0]][0]
        z1 = stack['metals'][ROUTE_LAYERS[1]][1]
        h = VIA_LANDING_NM / 2000
        columns += [Column(n, 'via', x - h, y - h, x + h, y + h, z0, z1) for n, x, y in vias]
    if 'pillars' in terms:
        columns += pillar_columns(pads, asm, stack)
    lane_fps = []
    if 'envelope' in terms:
        if set(asm.halo_um) != set(ROLES) or set(asm.clearance_um) != set(ROLES):
            raise Refused('the envelope needs halo_um and the fill clearance on %s' % ', '.join(ROLES))
        lane_fps = [tuple(v / 1000 for v in fp[-4:])
                    for fp in footprints(tdata, bdata, asm.lanes, pads_required=False)]
    for p in pads:
        if p.net in nets:
            pad_room(p, asm, terms, stack)     # before any solve, not an hour into one
    roles = {n: ('rail' if c in RAIL_CLASSES else 'signal') for n, c in classes.items()}
    return Route(wires, pads, vias, columns, roles, stack, asm, terms, lane_fps), raw


def load(traces, bumps, nets, asm=None, terms=TERMS, key=KEY):
    """Rows for the named nets and the solve count. Raises Refused on an input it cannot read."""
    route, raw = route_of(traces, bumps, nets, asm, terms, key)
    solver = Solver(route.stack)
    rows = {}
    for net in nets:
        row = {'length_um': round(sum(raw[net].values()), 4),
               'length_by_layer_um': {k: round(v, 4) for k, v in sorted(raw[net].items())}}
        row.update(net_load(net, route, solver))
        rows[net] = row
    return rows, solver.solves, route.stack


# ----------------------------------------------------------------------------
# solve: the inputs, by sha, and the record

APPROXIMATIONS = [
    '2D sections along every wire and via; corners and transitions are not solved in 3D',
    'via sections leave out their host wires; via and host are counted on their own',
    'a crossing wire of another net enters a section over its width grown by its distance '
    'from the bounded conductor, the grown part at -1 V',
    'charges under a mixed drive are mesh estimates; the mesh and the box sign only the '
    'one-conductor drive',
    'other nets\' landing pads are squares of side landing_um, which hold the drawn discs; '
    'for a rail that is not one-signed',
    'a lane\'s own metal in its landing square beyond the pad disc enters as 2D section pieces, '
    'its bounding boxes; their overlap with the disc is counted twice, upward',
    'the fill keeps the clearance of interposer_tech_default.json from every drawn conductor, '
    'as the filler decks check',
    'flat dielectric bands: BEOL oxide, each passivation film, then air',
    'the die face is a plane at the attach plane plus the rigid part of the connection stack; '
    'the die\'s passivation step and the reflowed cap are not modelled',
    'Miller factor 2 holds for aligned edges of equal slew; a faster aggressor edge can exceed it',
]
NOT_COUNTED = [
    'the die above its face, its pad included: the die\'s own load',
    'bond wires and the package',
    'via and pillar columns beyond window_um, left to the fill envelope and the die face',
]


def read_chiplet(path):
    try:
        import yaml
    except ImportError:
        raise Refused('reading design.chiplet needs PyYAML')
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise Refused('%s: %s' % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get('components'), list):
        raise Refused('%s: no components' % path)
    return data


def has_key(tree, word):
    if isinstance(tree, dict):
        return any(word in str(k).lower() or has_key(v, word) for k, v in tree.items())
    if isinstance(tree, list):
        return any(has_key(v, word) for v in tree)
    return False


def standoffs(cid, layers, attach):
    """The die face's heights: 'low' at the attach plane plus the rigid layers of the
    connection stack (solder reflows), 'high' plus the whole stack. A bonded standoff, once
    an input declares one, replaces both here; no input does today, so none is read."""
    rigid = full = 0.0
    for lay in layers:
        if lay['material'] in RIGID:
            rigid += lay['height']
        elif lay['material'] not in SOLDER:
            raise Refused('die %s: connection-stack material %r is neither rigid (%s) nor solder '
                          '(%s)' % (cid, lay['material'], ', '.join(RIGID), ', '.join(SOLDER)))
        full += lay['height']
    if not rigid > 0:
        raise Refused('die %s: the connection stack has no rigid part' % cid)
    return {'low': attach + rigid, 'high': attach + full}


def assembly_of(chiplet, methods, pins, stack):
    """Die outlines, faces, pillar diameters and openings from design.chiplet, the
    interconnect manifest and each die's pins.json."""
    if has_key(chiplet, 'underfill'):
        raise Refused('design.chiplet declares an underfill; its dielectric is not modelled')
    dies, interposer = {}, None
    for comp in chiplet['components']:
        if comp.get('type') == 'interposer':
            interposer = comp
            continue
        if comp.get('type') != 'die':
            continue
        cid = comp['id']
        if comp.get('anchor') != 'bbox_center':
            raise Refused('die %s: anchor %r, not bbox_center' % (cid, comp.get('anchor')))
        rot = float((comp.get('rotation') or {}).get('z', 0.0)) % 360
        if rot not in (0.0, 90.0, 180.0, 270.0):
            raise Refused('die %s: rotation %g is not a multiple of 90' % (cid, rot))
        w, h = comp['dimensions']['width'], comp['dimensions']['height']
        if rot in (90.0, 270.0):
            w, h = h, w
        x, y = comp['position']['x'], comp['position']['y']
        method = methods['methods'].get(comp.get('connection'))
        if method is None:
            raise Refused('die %s: connection %r is not in the manifest' % (cid, comp.get('connection')))
        layers = method['connection_stack']['layers']
        dies[cid] = {'rect': (x - w / 2, y - h / 2, x + w / 2, y + h / 2),
                     'face': standoffs(cid, layers, stack['attach']),
                     'pillar_um': max(lay['diameter'] for lay in layers),
                     'connection': comp.get('connection')}
    if interposer is None:
        raise Refused('design.chiplet has no interposer')
    if interposer.get('anchor') != 'bbox_center':
        raise Refused('interposer: anchor %r, not bbox_center' % interposer.get('anchor'))
    ix = interposer['position']['x'] - interposer['dimensions']['width'] / 2
    iy = interposer['position']['y'] - interposer['dimensions']['height'] / 2
    if abs(ix) > 1e-9 or abs(iy) > 1e-9:
        raise Refused('the interposer\'s lower-left corner is (%g, %g), not (0, 0)' % (ix, iy))
    openings = {}
    for comp, data in pins.items():
        dbu = data['dbu_um']
        names = [p['name'] for p in data['pins']]
        if len(set(names)) != len(names):
            raise Refused('pins.json of %s names a pin twice' % comp)
        for p in data['pins']:
            openings[(comp, p['name'])] = min(p['width_dbu'], p['height_dbu']) * dbu / 2
    return dies, openings


def tech_clearance(path):
    rules = read_json(path)['rules']
    return {role: float(rules[ROLE_CLEARANCE[role]]) for role in ROLES}


def solve_inputs(args):
    """What solve and sections read, each tied to the record and the check report:
    (assembly, nets, dies, pins paths). Raises Refused."""
    record = read_json(args.record)
    if (record.get('format'), record.get('version'), record.get('mode')) != (FORMAT, VERSION, 'halo'):
        raise Refused('%s: not a %s version %d halo record' % (args.record, FORMAT, VERSION))
    if record['method']['window_um'] != WINDOW_UM:
        raise Refused('the record\'s window is %r um, this tool\'s %r' % (record['method']['window_um'],
                                                                         WINDOW_UM))
    for name, path in (('traces', args.traces), ('bumps', args.bumps)):
        if sha256(path) != record['inputs'][name]['sha256']:
            raise Refused('%s does not match the record\'s %s' % (path, name))
    check = read_json(args.check)
    if (check.get('format'), check.get('version')) != (CHECK_FORMAT, CHECK_VERSION):
        raise Refused('%s: not a %s version %d report' % (args.check, CHECK_FORMAT, CHECK_VERSION))
    if check['inputs']['record']['sha256'] != sha256(args.record):
        raise Refused('the check report was made on another record')
    if check['inputs']['chiplet']['sha256'] != sha256(args.chiplet):
        raise Refused('the check report names another design.chiplet')
    if check.get('result') != 'pass':
        raise Refused('the check report does not pass: %r' % check.get('result'))
    for name, path in (('pycell_tech', PYCELL_TECH), ('layers', LAYER_MAP),
                       ('routed_load', Path(__file__)), ('routed_load_check', HERE / 'routed_load_check.py')):
        if check['inputs'].get(name, {}).get('sha256') != sha256(path):
            raise Refused('the check report was made with another %s than this tool\'s, %s'
                          % (name, path))
    bumps = read_json(args.bumps)
    if bumps['inputs']['chiplet']['sha256'] != sha256(args.chiplet):
        raise Refused('%s is not the design.chiplet bumps.json was made from' % args.chiplet)
    if bumps['rules_source']['interconnect_methods']['sha256'] != sha256(args.methods):
        raise Refused('%s is not the manifest bumps.json was made from' % args.methods)
    pins_paths = {}
    for item in (args.pins or '').split(','):
        comp, _, path = item.partition('=')
        if not comp or not path or comp in pins_paths:
            raise Refused('--pins: give COMPONENT=PATH once per die')
        pins_paths[comp] = Path(path)
    want = bumps['inputs']['pins']
    if set(pins_paths) != set(want):
        raise Refused('--pins names %s; bumps.json names %s' % (sorted(pins_paths), sorted(want)))
    for comp, path in pins_paths.items():
        if sha256(path) != want[comp]['sha256']:
            raise Refused('%s is not the pins.json of %s that bumps.json names' % (path, comp))
    stack = load_stack(args.key)
    dies, openings = assembly_of(read_chiplet(args.chiplet), read_json(args.methods),
                                 {c: read_json(p) for c, p in pins_paths.items()}, stack)
    lanes = record['nets']
    nets = [n for n in args.nets.split(',') if n] if args.nets else list(lanes)
    stray = [n for n in nets if n not in lanes]
    if stray:
        raise Refused('not among the record\'s nets: %s' % ', '.join(stray))
    asm = Assembly(dies=dies, openings=openings, halo_um=record['method']['halo_um'],
                   clearance_um=tech_clearance(args.tech), lanes=lanes,
                   patches=check.get('patches', []))
    return asm, nets, dies, pins_paths


def sections(args):
    """{net: Survey}: every section the solve would solve, built and checked, no solve.
    Raises Refused or ValueError at the first section the solve would refuse."""
    asm, nets, _, _ = solve_inputs(args)
    route, _ = route_of(args.traces, args.bumps, nets, asm, TERMS, args.key)
    out = {}
    for net in nets:
        out[net] = Survey(route.stack)
        net_load(net, route, out[net])
    return out


def solve(args):
    """The solve record from the CLI arguments. Raises Refused before writing anything."""
    asm, nets, dies, pins_paths = solve_inputs(args)
    budget_f = None if args.budget_ff is None else args.budget_ff * 1e-15
    outside_f = None if args.outside_ff is None else args.outside_ff * 1e-15
    if (budget_f is None) != (outside_f is None):
        raise Refused('the budget and the load outside the interposer go together')
    rows, solves, stack = load(args.traces, args.bumps, nets, asm, TERMS, args.key)
    inputs = [('record', args.record), ('check', args.check), ('traces', args.traces),
              ('bumps', args.bumps), ('chiplet', args.chiplet), ('methods', args.methods)]
    inputs += [('pins_%s' % c, p) for c, p in sorted(pins_paths.items())]
    inputs += [('stack_key', args.key), ('tech', args.tech), ('pycell_tech', PYCELL_TECH),
               ('routed_load', Path(__file__)), ('field2d', HERE / 'field2d.py')]
    out = {
        'format': FORMAT,
        'version': VERSION,
        'mode': 'solve',
        'inputs': {name: {'path': str(p), 'sha256': sha256(p)} for name, p in inputs},
        'method': {
            'value': 'upper value: the comparison principle for every potential assignment, '
                     'with the approximations named here',
            'control': 'the 2D-section and mesh approximations are not signed; their control is '
                       'a refinement gate outside this tool, which accepts a lane only when a '
                       'finer mesh moves its value by no less than -3 % and no more than +0.1 %. '
                       'That is the gate\'s acceptance band, not a measured error: the shift '
                       'measured on each lane is in the record of the reading that applies the '
                       'gate, not in this one',
            'standoff': 'one pad geometry for every standoff: the die face at the attach plane '
                        'plus the rigid stack, the pillars up to the full stack; no bonded '
                        'standoff is read',
            'window_um': WINDOW_UM, 'box_um': BOX_UM, 'miller_factor': MILLER,
            'mesh_um': {'h0': field2d.H0_UM, 'hmax': field2d.HMAX_UM, 'growth': field2d.GROWTH},
            'dielectric_bands': [list(b) for b in stack['bands']],
            'ground': 'the silicon carrier top, z = 0',
            'halo_um': asm.halo_um,
            'fill_clearance_um': asm.clearance_um,
            'dies': {c: {'face_z_um': d['face'], 'pillar_um': d['pillar_um'],
                         'connection': d['connection']} for c, d in sorted(dies.items())},
            'patches': len(asm.patches),
            'terms': list(TERMS),
            'approximations': APPROXIMATIONS,
            'not_counted': NOT_COUNTED,
        },
        'solves': solves,
        'nets': rows,
    }
    if budget_f is not None:
        room = budget_f - outside_f
        over = [n for n in nets if rows[n]['c_total_f'] > room]
        for n in nets:
            rows[n]['headroom_f'] = room - rows[n]['c_total_f']
        out['budget'] = {
            'budget_f': budget_f, 'outside_f': outside_f, 'room_f': room,
            'reading': 'OVER' if over else 'WITHIN', 'over': over,
            'note': 'headroom_f must also hold whatever this record does not count, '
                    'on the dies and on the interposer',
        }
    return out


# ----------------------------------------------------------------------------
# the command line

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='mode', required=True)
    h = sub.add_parser('halo', help='write the keep-out halo and its record')
    h.add_argument('--traces', required=True, type=Path)
    h.add_argument('--bumps', required=True, type=Path)
    h.add_argument('--nets', required=True, help='comma-separated lane names')
    h.add_argument('--halo-um', required=True, help='M4=w,M5=w,TM1=w,TM2=w')
    h.add_argument('--halo-source', required=True, help='the density ladder record\'s sha256')
    h.add_argument('--out', required=True, type=Path, help='a new path for the record')
    h.add_argument('--sidecar', required=True, type=Path, help='a new path for the regions')
    c = sub.add_parser('check', help='check the finished GDS against the record')
    c.add_argument('--record', required=True, type=Path)
    c.add_argument('--gds', required=True, type=Path)
    c.add_argument('--chiplet', required=True, type=Path)
    c.add_argument('--out', required=True, type=Path, help='a new path for the report')
    c.add_argument('--traces', type=Path, help='where the record\'s traces are, if moved')
    c.add_argument('--bumps', type=Path, help='where the record\'s bumps are, if moved')
    s = sub.add_parser('solve', help='solve the load of each bounded net')
    x = sub.add_parser('sections', help='build and check every section the solve would solve')
    for p in (s, x):
        p.add_argument('--record', required=True, type=Path)
        p.add_argument('--check', required=True, type=Path)
        p.add_argument('--traces', required=True, type=Path)
        p.add_argument('--bumps', required=True, type=Path)
        p.add_argument('--chiplet', required=True, type=Path)
        p.add_argument('--methods', required=True, type=Path, help='interconnect_methods.json')
        p.add_argument('--pins', required=True, help='COMPONENT=pins.json,...')
        p.add_argument('--nets', help='a subset of the record\'s nets')
        p.add_argument('--key', type=Path, default=KEY, help=argparse.SUPPRESS)
        p.add_argument('--tech', type=Path, default=TECH, help=argparse.SUPPRESS)
    s.add_argument('--out', required=True, type=Path, help='a new path for the record')
    s.add_argument('--budget-ff', type=float, help='the declared load per net, fF')
    s.add_argument('--outside-ff', type=float,
                   help='the physical load per net outside the interposer, fF')
    args = ap.parse_args(argv)
    if TECH_REFUSED is not None:
        print('routed_load: %s' % TECH_REFUSED, file=sys.stderr)
        return 2
    if args.mode == 'check':
        import routed_load_check
        return routed_load_check.main(args)
    try:
        if args.mode == 'halo':
            rec = halo(args.traces, args.bumps, [n for n in args.nets.split(',') if n],
                       parse_halo_um(args.halo_um), args.halo_source, args.out, args.sidecar)
            print('%d regions, halo_um %s' % (rec['sidecar']['regions'], rec['method']['halo_um']))
            return 0
        if args.mode == 'sections':
            found = sections(args)
            for net, sv in found.items():
                print('%s: %d sections, %d pad solves, smallest step %.4g um across, %.4g um up'
                      % (net, sv.sections, sv.revolutions, sv.dx, sv.dz))
            print('%d sections on %d nets: no overlap, no own metal left out, no mesh step '
                  'under %g um' % (sum(sv.sections + sv.revolutions for sv in found.values()),
                                   len(found), field2d.MEET_UM))
            return 0
        if os.path.lexists(args.out):
            raise Refused('%s exists; give a new path' % args.out)
        record = solve(args)
    except (Refused, ValueError, KeyError, TypeError, OSError) as exc:
        print('routed_load: %s' % exc, file=sys.stderr)
        return 2
    with open(args.out, 'x') as f:
        json.dump(record, f, indent=1)
        f.write('\n')
    for net, row in record['nets'].items():
        line = '%s: %.1f fF over %.1f um' % (net, row['c_total_f'] * 1e15, row['length_um'])
        if 'headroom_f' in row:
            line += ', headroom %.1f fF' % (row['headroom_f'] * 1e15)
        print(line)
    if 'budget' in record:
        print(record['budget']['reading'])
        return 3 if record['budget']['over'] else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
