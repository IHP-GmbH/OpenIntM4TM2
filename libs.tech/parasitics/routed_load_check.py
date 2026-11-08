"""The GDS check behind a routed-load solve, run as `routed_load.py check`.

Reads the finished interposer GDS against the halo record, with klayout.db only:

(i) lane metal, traced from one landing pad of each lane through TopMetal2, TopVia2,
    TopMetal1, TopVia1, Metal5, Via4 and Metal4. It must reach every pad of the lane and
    cover every segment and TopVia2_C landing the traces give the lane, each of its vias
    must have a cut, and it must reach no other net's pad, segment or via, nor any layer
    below TopMetal1.
(ii) metal within window_um of a lane outside every net's footprints (segments with half
    a width past each end, TopVia2_C landings, landing squares). Each such polygon on
    Metal4, Metal5, TopMetal1 or TopMetal2 must touch the footprint, and the drawn metal,
    of exactly one net on its own layer, and be Manhattan. It is then a patch of that
    net, named in the report and modelled by the solve. Anything else is a finding, as
    is TopVia2 outside every via landing.
    Inside a lane's landing square, the pad metal beyond the solve's pad disc and outside
    every segment and via footprint must be lane metal. Each piece is a patch of the lane,
    named by its bounding box, which covers it; a piece, or its box, that touches another
    net's metal or footprint is a finding. Inside another net's landing square, which the
    solve holds whole at that net's potential, lane metal there is a finding.
(iii) dummy fill (x/22) overlapping a lane footprint grown by that metal's halo_um.

Layer numbers come from the PDK's layer map, intm4tm2_layers.txt. Exit 0 pass, 3 any
finding, 2 refused (an input that does not match the record's sha, or that cannot be
read). The report is written on 0 and 3, never on 2.
"""
import math
import os
import sys
from pathlib import Path

import klayout
import klayout.db as kdb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import routed_load as rl  # noqa: E402

LAYER_MAP = rl.LAYER_MAP
PAD, CUT = 'TopMetal2', 'TopVia2'
LANDING = ('TopMetal1', CUT, PAD)                  # a TopVia2_C landing's layers
CHAIN = (PAD, CUT, 'TopMetal1', 'TopVia1', 'Metal5', 'Via4', 'Metal4')  # top down
BELOW = ('TopVia1', 'Metal5', 'Via4', 'Metal4')    # no lane is routed here
CHECKED = ('Metal4', 'Metal5', 'TopMetal1', CUT, PAD)  # drawn metal and cuts read by (ii)
DISC_POINTS = 2048


def layer_map(path=LAYER_MAP):
    """{(name, purpose): (layer, datatype)} from the layer map's 'name purpose layer
    datatype' lines."""
    out = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as exc:
        raise rl.Refused('%s: %s' % (path, exc))
    for line in lines:
        f = line.split()
        if not f or f[0].startswith('#'):
            continue
        if len(f) != 4:
            raise rl.Refused('%s: %r is not "name purpose layer datatype"' % (path, line))
        out[(f[0], f[1])] = (int(f[2]), int(f[3]))
    return out


def disc(x, y, r2):
    """A polygon inside the circle of radius r2 / 2 nm about (x, y), its vertices the
    grid points nearest the circle from inside."""
    pts = []
    for k in range(DISC_POINTS):
        a = 2 * math.pi * k / DISC_POINTS
        fx, fy = r2 / 2 * math.cos(a), r2 / 2 * math.sin(a)
        best = None
        for px in (math.floor(fx), math.floor(fx) + 1):
            for py in (math.floor(fy), math.floor(fy) + 1):
                if 4 * (px * px + py * py) <= r2 * r2 and (
                        best is None or px * px + py * py > best[0] ** 2 + best[1] ** 2):
                    best = (px, py)
        pts.append(kdb.Point(x + best[0], y + best[1]))
    return kdb.Polygon(pts)


def beyond(poly, x, y, r2):
    """Whether a vertex of a simple polygon lies outside the circle of radius r2 / 2 nm
    about (x, y). A polygon with every vertex inside lies inside: the disc is convex."""
    return any(4 * ((p.x - x) ** 2 + (p.y - y) ** 2) > r2 * r2 for p in poly.each_point())


def box_um(b):
    return [b.left / 1000, b.bottom / 1000, b.right / 1000, b.top / 1000]


def check(record_path, gds_path, chiplet_path, traces_path=None, bumps_path=None):
    """The report. Raises rl.Refused when an input cannot be read or does not match."""
    record = rl.read_json(record_path)
    if (record.get('format'), record.get('version'), record.get('mode')) != \
            (rl.FORMAT, rl.VERSION, 'halo'):
        raise rl.Refused('%s: not a %s version %d halo record' % (record_path, rl.FORMAT, rl.VERSION))
    ins = record['inputs']
    paths = {'traces': Path(traces_path or ins['traces']['path']),
             'bumps': Path(bumps_path or ins['bumps']['path'])}
    for name, p in paths.items():
        if rl.sha256(p) != ins[name]['sha256']:
            raise rl.Refused('%s does not match the record\'s %s' % (p, name))
    traces, bumps = rl.read_route(paths['traces'], paths['bumps'])
    lanes = record['nets']
    window = rl.nm(record['method']['window_um'])
    halo_um = record['method']['halo_um']
    inputs = {name: {'path': str(p), 'sha256': rl.sha256(p)}
              for name, p in (('record', record_path), ('gds', gds_path), ('chiplet', chiplet_path),
                              ('traces', paths['traces']), ('bumps', paths['bumps']),
                              ('layers', LAYER_MAP), ('pycell_tech', rl.PYCELL_TECH),
                              ('routed_load_check', Path(__file__)),
                              ('routed_load', HERE / 'routed_load.py'))}
    lmap = layer_map()
    for name in CHAIN:
        if (name, 'drawing') not in lmap:
            raise rl.Refused('%s: no %s drawing layer' % (LAYER_MAP, name))
    for role in rl.ROLES:
        if (rl.ROLE_METAL[role], 'filler') not in lmap:
            raise rl.Refused('%s: no %s filler layer' % (LAYER_MAP, rl.ROLE_METAL[role]))

    ly = kdb.Layout()
    try:
        ly.read(str(gds_path))
    except RuntimeError as exc:
        raise rl.Refused('%s: %s' % (gds_path, exc))
    if abs(ly.dbu - 0.001) > 1e-12:
        raise rl.Refused('%s: dbu %g, not 0.001 um' % (gds_path, ly.dbu))
    tops = ly.top_cells()
    if len(tops) != 1:
        raise rl.Refused('%s: %d top cells' % (gds_path, len(tops)))
    top = tops[0]

    def region(name, purpose):
        li = ly.find_layer(*lmap[(name, purpose)])
        r = kdb.Region() if li is None else kdb.Region(top.begin_shapes_rec(li))
        r.merge()
        return r

    drawn = {ln: region(ln, 'drawing') for ln in CHAIN}

    # Footprints (where each net's metal may be) and cores (where it must be), per layer.
    names = [n['name'] for n in traces['nets']]
    fps = rl.footprints(traces, {'bumps': []}, names, pads_required=False)
    fp, core = {}, {}

    def put(table, net, lns, b):
        for ln in lns:
            table.setdefault(net, {}).setdefault(ln, kdb.Region()).insert(b)

    sv = kdb.Region()      # segment and via footprints of every net on the pad metal
    for kind, net, layer, x0, y0, x1, y1 in fps:
        put(fp, net, (layer,) if kind == 'segment' else LANDING, kdb.Box(x0, y0, x1, y1))
        if kind == 'via' or layer == PAD:
            sv.insert(kdb.Box(x0, y0, x1, y1))
    sv.merge()
    pad_boxes = {}
    for b in bumps['bumps']:
        x, y, h = rl.nm(b['x_um']), rl.nm(b['y_um']), -(-rl.nm(b['landing_um']) // 2)
        pad_boxes.setdefault(b['net'], []).append((x, y, kdb.Box(x - h, y - h, x + h, y + h),
                                                   b['landing_um']))
        put(fp, b['net'], (PAD,), kdb.Box(x - h, y - h, x + h, y + h))
        put(core, b['net'], (PAD,), kdb.Box(x - 1, y - 1, x + 1, y + 1))
    for n in traces['nets']:
        for s in n['segments']:
            x0, x1 = sorted((rl.nm(s['x0']), rl.nm(s['x1'])))
            y0, y1 = sorted((rl.nm(s['y0']), rl.nm(s['y1'])))
            put(core, n['name'], (s['layer'],), kdb.Box(x0 - 1, y0 - 1, x1 + 1, y1 + 1))
        for v in n.get('vias', []):
            x, y = rl.nm(v['x']), rl.nm(v['y'])
            put(core, n['name'], LANDING, kdb.Box(x - 1, y - 1, x + 1, y + 1))
    for table in (fp, core):
        for per in table.values():
            for r in per.values():
                r.merge()
    fp_all = {}
    for per in fp.values():
        for ln, r in per.items():
            fp_all.setdefault(ln, kdb.Region()).insert(r)
    for r in fp_all.values():
        r.merge()

    findings = []

    def finding(letter, what, net, ln, bbox):
        findings.append({'letter': letter, 'what': what, 'net': net,
                         'layer': ln, 'bbox_um': box_um(bbox)})

    # (i) each lane, traced from the GDS
    lane_fps = rl.footprints(traces, bumps, lanes)
    comps = kdb.Region()
    comp_of = {}
    for lane in lanes:
        seeds = []
        for x, y, b, _ in pad_boxes.get(lane, []):
            s = drawn[PAD].interacting(kdb.Region(kdb.Box(x - 1, y - 1, x + 1, y + 1)))
            if s.is_empty():
                finding('i', 'no %s at the landing pad' % PAD, lane, PAD, b)
            seeds.append((s, b))
        reached = [s for s, b in seeds if not s.is_empty()]
        if not reached:
            continue
        comp = {ln: kdb.Region() for ln in CHAIN}
        comp[PAD] = reached[0].dup()
        comp_of[lane] = comp
        area = -1
        while True:
            for k, ln in enumerate(CHAIN):
                for nb in CHAIN[max(k - 1, 0):k] + CHAIN[k + 1:k + 2]:
                    comp[ln] += drawn[ln].overlapping(comp[nb])
                comp[ln].merge()
            now = sum(r.area() for r in comp.values())
            if now == area:
                break
            area = now
        for s, b in seeds:
            if not s.is_empty() and (s & comp[PAD]).is_empty():
                finding('i', 'lane metal does not reach this landing pad', lane, PAD, b)
        for kind, net, layer, x0, y0, x1, y1 in lane_fps:
            if net != lane or kind == 'pad':
                continue
            box = kdb.Region(kdb.Box(x0, y0, x1, y1))
            for ln in (layer,) if kind == 'segment' else (LANDING[0], LANDING[2]):
                for p in (box - comp[ln]).each():
                    finding('i', 'no lane metal where the traces put it', lane, ln, p.bbox())
            if kind == 'via' and (box & comp[CUT]).is_empty():
                finding('i', 'no %s cut at the via' % CUT, lane, CUT, box.bbox())
        for other, per in sorted(core.items()):
            if other == lane:
                continue
            for ln, r in sorted(per.items()):
                hit = comp[ln].interacting(r)
                if not hit.is_empty():
                    finding('i', 'lane metal reaches net %s' % other, lane, ln, hit.bbox())
        for ln in BELOW:
            if not comp[ln].is_empty():
                finding('i', 'lane metal reaches %s' % ln, lane, ln, comp[ln].bbox())
        for r in comp.values():
            comps += r

    # (ii) metal near a lane outside every net's footprints
    zone = kdb.Region()
    for kind, net, layer, x0, y0, x1, y1 in lane_fps:
        zone.insert(kdb.Box(x0, y0, x1, y1))
    zone += comps
    zone.merge()
    zone = zone.sized(window)
    patches = []
    owned = {}
    for ln in CHECKED:
        rest = drawn[ln] - fp_all.get(ln, kdb.Region())
        rest.merge()
        for poly in rest.interacting(zone).each():
            here = kdb.Region(poly)
            if ln == CUT:
                finding('ii', '%s outside every via landing' % CUT, None, ln, poly.bbox())
                continue
            touch = [n for n, per in sorted(fp.items()) if ln in per and not here.interacting(per[ln]).is_empty()]
            metal = []
            for n in touch:
                if (n, ln) not in owned:
                    owned[(n, ln)] = drawn[ln] & fp[n][ln]
                if not here.interacting(owned[(n, ln)]).is_empty():
                    metal.append(n)
            manhattan = all(e.dx() == 0 or e.dy() == 0 for e in poly.each_edge())
            rects = [t.bbox() for t in poly.decompose_trapezoids()]
            manhattan = manhattan and sum(r.area() for r in rects) == poly.area()
            if len(touch) == 1 and metal == touch and manhattan:
                patches.append({'net': touch[0], 'layer': ln, 'where': 'outside the footprints',
                                'rects_um': sorted(box_um(r) for r in rects),
                                'area_um2': poly.area() / 1e6})
                continue
            if not touch:
                what = 'metal outside every footprint that touches no net'
            elif len(touch) > 1:
                what = 'metal outside the footprints that touches nets %s' % ', '.join(touch)
            elif metal != touch:
                what = 'metal outside the footprints that touches no metal of net %s' % touch[0]
            else:
                what = 'metal outside the footprints of net %s that is not Manhattan' % touch[0]
            finding('ii', what, touch[0] if len(touch) == 1 else None, ln, poly.bbox())

    # (ii) pad metal beyond the solve's pad disc in every landing square lane metal reaches.
    # In another net's square, which the solve holds whole at that net's potential, any
    # lane metal there is a finding.
    lane_metal = kdb.Region()
    for comp in comp_of.values():
        lane_metal += comp[PAD]
    for net, boxes in sorted(pad_boxes.items()):
        if net in comp_of:
            continue
        for x, y, b, landing in boxes:
            square = kdb.Region(b)
            if (lane_metal & square).is_empty():
                continue
            r2 = round(2 * rl.pad_radius(landing) * 1000)
            out = square - sv - kdb.Region(disc(x, y, r2))
            for lane in lanes:
                if lane not in comp_of:
                    continue
                rest = comp_of[lane][PAD] & out
                rest.merge()
                for poly in rest.each():
                    if any(beyond(t, x, y, r2) for t in poly.decompose_trapezoids()):
                        finding('ii', 'lane metal in the landing square of net %s beyond its pad '
                                'disc' % net, lane, PAD, poly.bbox())
    for lane in lanes:
        if lane not in comp_of:
            continue
        own = comp_of[lane][PAD]
        foreign = drawn[PAD] - own
        for n, per in fp.items():
            if n != lane and PAD in per:
                foreign += per[PAD]
        for x, y, b, landing in pad_boxes.get(lane, []):
            r2 = round(2 * rl.pad_radius(landing) * 1000)
            rest = (drawn[PAD] & kdb.Region(b)) - sv - kdb.Region(disc(x, y, r2))
            rest.merge()
            for poly in rest.each():
                # the disc polygon sits inside the solve's circle: keep only what lies beyond it
                traps = [t for t in poly.decompose_trapezoids() if beyond(t, x, y, r2)]
                if not traps:
                    continue
                # one box per piece: the piece's slabs along the arc would each be a target
                box = traps[0].bbox()
                for t in traps[1:]:
                    box += t.bbox()
                if not (kdb.Region(poly) - own).is_empty() or \
                        not kdb.Region(box).interacting(foreign).is_empty():
                    finding('ii', 'metal in the landing square of net %s beyond its pad disc that '
                            'is not its own or touches another net' % lane, lane, PAD, poly.bbox())
                    continue
                patches.append({'net': lane, 'layer': PAD, 'where': 'landing square',
                                'rects_um': [box_um(box)],
                                'area_um2': sum(t.area() for t in traps) / 1e6})
    patches.sort(key=lambda p: (p['net'], p['layer'], p['rects_um']))

    # (iii) fill inside the halo
    for role in rl.ROLES:
        ln = rl.ROLE_METAL[role]
        h = rl.nm(halo_um[role])
        if h <= 0:
            continue
        grown = {}
        for kind, net, layer, x0, y0, x1, y1 in lane_fps:
            grown.setdefault(net, kdb.Region()).insert(kdb.Box(x0 - h, y0 - h, x1 + h, y1 + h))
        whole = kdb.Region()
        for r in grown.values():
            r.merge()
            whole += r
        whole.merge()
        bad = region(ln, 'filler') & whole
        bad.merge()
        for poly in bad.each():
            here = kdb.Region(poly)
            near = [n for n in lanes if n in grown and not (here & grown[n]).is_empty()]
            finding('iii', 'dummy fill inside the halo', ', '.join(near), ln, poly.bbox())

    findings.sort(key=lambda f: (f['letter'], f['net'] or '', f['layer'], f['bbox_um'], f['what']))
    summary = {}
    for p in patches:
        s = summary.setdefault(p['net'], {'patches': 0, 'area_um2': 0.0, 'largest_um2': 0.0})
        s['patches'] += 1
        s['area_um2'] += p['area_um2']
        s['largest_um2'] = max(s['largest_um2'], p['area_um2'])
    return {
        'format': rl.CHECK_FORMAT,
        'version': rl.CHECK_VERSION,
        'inputs': inputs,
        'klayout_db': klayout.__version__,
        'nets': lanes,
        'window_um': record['method']['window_um'],
        'halo_um': halo_um,
        'top_cell': top.name,
        'pad_disc': 'landing_um / 2 plus one grid step (%g um)' % (rl.GRID_NM / 1000),
        'findings': findings,
        'patches': patches,
        'patch_summary': summary,
        'result': 'fail' if findings else 'pass',
    }


def main(args):
    """routed_load.py check: exit 0 pass, 3 a finding, 2 refused (nothing written)."""
    import json
    if os.path.lexists(args.out):
        print('routed_load check: %s exists; give a new path' % args.out, file=sys.stderr)
        return 2
    try:
        report = check(args.record, args.gds, args.chiplet, args.traces, args.bumps)
    except (rl.Refused, ValueError, KeyError, TypeError, OSError) as exc:
        print('routed_load check: %s' % exc, file=sys.stderr)
        return 2
    with open(args.out, 'x') as f:
        json.dump(report, f, indent=1)
        f.write('\n')
    for f in report['findings']:
        print('(%s) %s: %s on %s at %s' % (f['letter'], f['net'], f['what'], f['layer'], f['bbox_um']))
    print('%s: %d findings, %d patches' % (report['result'], len(report['findings']),
                                          len(report['patches'])))
    return 0 if report['result'] == 'pass' else 3
