"""Electrostatic capacitance of a cross-section, by first-order finite elements.

Planar mode solves a 2D section, per unit length. Axisymmetric mode solves a
body of revolution about x = 0, with x as the radius. The mesh is a tensor grid
whose lines pass through every conductor edge and dielectric interface, with
each cell split into two right triangles. On that mesh the first-order element
stiffness reduces to a five-point stencil: each cell gives eps/2 * h_perp/h_par
to each of its four edges, times 2*pi*r at the centroid of the triangle holding
that edge in axisymmetric mode. The diagonals carry nothing. So the solver needs
no matrix library, and the CI's bare Python runs it.

The box is grounded on every side, except the axis in axisymmetric mode. Hold
one conductor at 1 V and every other at 0 V. The finite element space and the
box both restrict the potential, so the solved energy is at least the true one
(Dirichlet's principle). That conductor's charge is twice that energy, so it
comes out at or above the open-domain value. Charges under any other drive are
mesh estimates. The tests check that they converge from above.

Lengths are in um. Charges come out in F/m (planar) or F (axisymmetric).
"""
import math

EPS0 = 8.8541878128e-12   # F/m, CODATA 2018

#: Default mesh: 0.25 um next to every edge, growing by 1.35 per cell up to 40 um.
H0_UM, HMAX_UM, GROWTH = 0.25, 40.0, 1.35
#: Two grid lines closer than this are one line: the distance is float noise, never a drawn
#: one. No mesh has a step this small.
MEET_UM = 1e-9


def graded(a, b, h0, hmax, growth):
    """Grid points strictly between a and b: h0 at both ends, growing to hmax.

    Where the steps from the two ends meet, within MEET_UM, they leave one point there.
    An interval of 2 h0 plus float noise would otherwise get two points 1e-16 um apart,
    a cell no iterative solve converges on.
    """
    left, right = [], []
    h, x, y = h0, a, b
    while y - x > 2 * h:
        x += h
        y -= h
        left.append(x)
        right.append(y)
        h = min(h * growth, hmax)
    gap = y - x
    if left and gap <= MEET_UM:
        right.pop()
    n = max(1, int(math.ceil(gap / hmax)))
    mid = [x + gap * k / n for k in range(1, n)]
    return [p for p in left + mid + right[::-1] if a < p < b]


def grid(breaks, h0, hmax, growth):
    """Grid lines through every break, graded between consecutive breaks. Refuses a step
    under MEET_UM."""
    b = sorted(set(round(v, 6) for v in breaks))
    out = []
    for lo, hi in zip(b, b[1:]):
        out.append(lo)
        out.extend(graded(lo, hi, h0, hmax, growth))
    out.append(b[-1])
    for p, q in zip(out, out[1:]):
        if q - p < MEET_UM:
            raise ValueError('a mesh step of %g um at %r um' % (q - p, p))
    return out


def mesh(x0, x1, ztop, layers, conductors, lines=(), h0=None, hmax=None, growth=None):
    """The grid lines (xs, zs) of a section, as Section builds them. Refuses a conductor
    named twice, two conductors that overlap and a step under MEET_UM."""
    h0 = H0_UM if h0 is None else h0
    hmax = HMAX_UM if hmax is None else hmax
    growth = GROWTH if growth is None else growth
    names = [c[0] for c in conductors]
    if len(set(names)) != len(names):
        raise ValueError('conductor named twice')
    for i, a in enumerate(conductors):
        for b in conductors[i + 1:]:
            if (a[1] < b[2] and b[1] < a[2]) and (a[3] < b[4] and b[3] < a[4]):
                raise ValueError('conductors %s and %s overlap' % (a[0], b[0]))
    xb = [x0, x1] + [v for c in conductors for v in c[1:3]]
    zb = [0.0, ztop] + [v for c in conductors for v in c[3:5]]
    xb += [v for c in lines for v in c[0:2]]
    zb += [v for c in lines for v in c[2:4]]
    zb += [v for lay in layers for v in lay[:2]]
    return (grid([v for v in xb if x0 <= v <= x1], h0, hmax, growth),
            grid([v for v in zb if 0.0 <= v <= ztop], h0, hmax, growth))


class Section:
    """The box [x0, x1] x [0, ztop]. z = 0 is the grounded carrier top.

    layers: (zlo, zhi, eps_r) bands covering [0, ztop].
    conductors: (name, xlo, xhi, zlo, zhi) rectangles. Two of them may not
    overlap; ones that touch share their boundary nodes, which go to the later one.
    lines: (xlo, xhi, zlo, zhi) rectangles whose edges the grid passes through, with no
    conductor there: another section's mesh without one of its conductors.
    """

    def __init__(self, x0, x1, ztop, layers, conductors, axisymmetric=False,
                 h0=None, hmax=None, growth=None, lines=()):
        self.axisym = axisymmetric
        self.conductors = list(conductors)
        self.xs, self.zs = mesh(x0, x1, ztop, layers, self.conductors, lines, h0, hmax, growth)
        self._build(layers)

    def _build(self, layers):
        xs, zs = self.xs, self.zs
        nx, nz = len(xs), len(zs)
        n = nx * nz
        owner = [None] * n
        for name, xlo, xhi, zlo, zhi in self.conductors:
            ks = [k for k in range(nz) if zlo - 1e-9 <= zs[k] <= zhi + 1e-9]
            iis = [i for i in range(nx) if xlo - 1e-9 <= xs[i] <= xhi + 1e-9]
            for k in ks:
                for i in iis:
                    owner[k * nx + i] = name
        ground = [False] * n
        for k in range(nz):
            for i in range(nx):
                if k in (0, nz - 1) or i == nx - 1 or (i == 0 and not self.axisym):
                    ground[k * nx + i] = owner[k * nx + i] is None

        def eps(z):
            for lo, hi, er in layers:
                if lo <= z <= hi:
                    return er
            raise ValueError('no dielectric at z = %g um' % z)

        edges = {}
        for k in range(nz - 1):
            hz = zs[k + 1] - zs[k]
            er = eps(0.5 * (zs[k] + zs[k + 1]))
            for i in range(nx - 1):
                hx = xs[i + 1] - xs[i]
                if self.axisym:
                    w1 = 2 * math.pi * (xs[i] + hx / 3.0)
                    w2 = 2 * math.pi * (xs[i] + 2 * hx / 3.0)
                else:
                    w1 = w2 = 1.0
                gx, gz = er * hz / (2 * hx), er * hx / (2 * hz)
                p00 = k * nx + i
                p10, p01, p11 = p00 + 1, p00 + nx, p00 + nx + 1
                for p, q, g in ((p00, p10, gx * w1), (p00, p01, gz * w1),
                                (p01, p11, gx * w2), (p10, p11, gz * w2)):
                    edges[(p, q)] = edges.get((p, q), 0.0) + g
        self.owner, self.edges = owner, edges
        fixed = [owner[p] is not None or ground[p] for p in range(n)]
        free = [p for p in range(n) if not fixed[p]]
        slot = {p: j for j, p in enumerate(free)}
        diag = [0.0] * len(free)
        cols = [[] for _ in free]
        drive = [[] for _ in free]       # (conductor, conductance) per free node
        for (p, q), g in edges.items():
            for a, b in ((p, q), (q, p)):
                if a in slot:
                    j = slot[a]
                    diag[j] += g
                    if b in slot:
                        cols[j].append((slot[b], g))
                    elif owner[b] is not None:
                        drive[j].append((owner[b], g))
        self.free, self.diag, self.cols, self.drive = free, diag, cols, drive

    def solve(self, volts, tol=1e-10, maxit=50000):
        """Charge on every conductor for the given potentials.

        volts maps a conductor name to its potential; the others sit at 0 V.
        Returns (charges, iterations).
        """
        unknown = set(volts) - {c[0] for c in self.conductors}
        if unknown:
            raise ValueError('no conductor %s' % sorted(unknown))
        diag, cols = self.diag, self.cols
        lower = [[(c, g) for c, g in row if c < j] for j, row in enumerate(cols)]
        upper = [[(c, g) for c, g in row if c > j] for j, row in enumerate(cols)]
        m = len(diag)

        def precondition(r):
            # symmetric Gauss-Seidel: a forward sweep, then a backward one
            y = [0.0] * m
            for j in range(m):
                y[j] = (r[j] + sum(g * y[c] for c, g in lower[j])) / diag[j]
            for j in range(m - 1, -1, -1):
                y[j] += sum(g * y[c] for c, g in upper[j]) / diag[j]
            return y

        b = [sum(g * volts.get(c, 0.0) for c, g in self.drive[j]) for j in range(m)]
        x = [0.0] * m
        r = b[:]
        z = precondition(r)
        d = z[:]
        rz = sum(r[j] * z[j] for j in range(m))
        stop = tol * (math.sqrt(sum(v * v for v in b)) or 1.0)
        it = 0
        while True:
            if it >= maxit:
                raise RuntimeError('CG did not converge in %d iterations' % maxit)
            it += 1
            ad = [diag[j] * d[j] - sum(g * d[c] for c, g in cols[j]) for j in range(m)]
            alpha = rz / sum(d[j] * ad[j] for j in range(m))
            for j in range(m):
                x[j] += alpha * d[j]
                r[j] -= alpha * ad[j]
            if math.sqrt(sum(v * v for v in r)) < stop:
                break
            z = precondition(r)
            rz, rz_old = sum(r[j] * z[j] for j in range(m)), rz
            beta = rz / rz_old
            d = [z[j] + beta * d[j] for j in range(m)]
        u = [volts.get(o, 0.0) if o is not None else 0.0 for o in self.owner]
        for j, p in enumerate(self.free):
            u[p] = x[j]
        charge = {c[0]: 0.0 for c in self.conductors}
        owner = self.owner
        for (p, q), g in self.edges.items():
            op, oq = owner[p], owner[q]
            if op == oq:
                continue
            f = g * (u[p] - u[q])
            if op is not None:
                charge[op] += f
            if oq is not None:
                charge[oq] -= f
        scale = EPS0 * (1e-6 if self.axisym else 1.0)
        return {k: v * scale for k, v in charge.items()}, it
