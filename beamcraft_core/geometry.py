# BeamCraft - geometry: line / arc fitting
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Turns dense polylines into a small number of G1 lines and G2/G3 arcs.

Why: sending thousands of tiny G1 segments starves Grbl's planner buffer
(the serial link cannot keep up), the machine slows down / stops between
blocks and the laser burns dots.  Grbl generates arcs on-board from a single
G2/G3 line, so fitting arcs drastically reduces the amount of data to stream
and lets the planner keep its speed.

Coordinates are complex numbers (x + 1j*y), machine frame (Y up), mm.

A toolpath is (start, prims) where prims is a list of
    ('L', end)                      straight line
    ('A', end, center, ccw)         circular arc, ccw=True -> G3, False -> G2
    ('O', end)                      straight move at cutting speed, laser OFF
                                    (overscan lead-in / lead-out)
    ('P', end, power_pct)           straight line at its own power (raster)
"""

import math
import numpy as np

TWO_PI = 2.0 * math.pi


def dedup(pts, eps=1e-6):
    if len(pts) < 2:
        return pts
    keep = np.concatenate(([True], np.abs(np.diff(pts)) > eps))
    return pts[keep]


def _circle(a, b, c):
    """Center and radius of circle through 3 complex points (None if collinear)."""
    ax, ay, bx, by, cx, cy = a.real, a.imag, b.real, b.imag, c.real, c.imag
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-14:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    center = complex(ux, uy)
    return center, abs(a - center)


class _Fitter:
    def __init__(self, pts, tol, arcs, corner_deg=30.0, rmax=3000.0, rmin=0.02):
        self.p = pts
        self.n = len(pts)
        self.tol = tol
        self.arcs = arcs
        self.rmax = rmax
        self.rmin = rmin
        n = self.n
        corner = np.zeros(n, dtype=bool)
        if n >= 3:
            d = np.diff(pts)
            turn = np.abs(np.angle(d[1:] / d[:-1]))
            corner[1:-1] = turn > math.radians(corner_deg)
        corner[-1] = True
        # next_corner[i] = smallest index > i that is a corner (or n-1)
        nxt = np.empty(n, dtype=np.int64)
        last = n - 1
        for i in range(n - 1, -1, -1):
            nxt[i] = last
            if corner[i]:
                last = i
        self.next_corner = nxt
        self.arc_cache = None

    # ---- validity tests -------------------------------------------------
    def line_ok(self, i, j):
        if j <= i + 1:
            return True
        a, b = self.p[i], self.p[j]
        seg = self.p[i + 1:j]
        ab = b - a
        L2 = (ab * ab.conjugate()).real
        if L2 < 1e-18:
            return bool(np.all(np.abs(seg - a) <= self.tol))
        t = ((seg - a) * ab.conjugate()).real / L2
        t = np.clip(t, 0.0, 1.0)
        dist = np.abs(seg - (a + t * ab))
        return bool(np.all(dist <= self.tol))

    def arc_ok(self, i, j):
        if j < i + 2:
            return None
        p = self.p
        k = (i + j) // 2
        circ = _circle(p[i], p[k], p[j])
        if circ is None:
            return None
        c, r = circ
        if r > self.rmax or r < self.rmin:
            return None
        span = p[i:j + 1]
        d = np.abs(span - c)
        if np.any(np.abs(d - r) > self.tol):
            return None
        mids = 0.5 * (span[1:] + span[:-1])
        if np.any(np.abs(np.abs(mids - c) - r) > self.tol):
            return None
        ang = np.unwrap(np.angle(span - c))
        da = np.diff(ang)
        if np.all(da > 1e-12):
            ccw = True
        elif np.all(da < -1e-12):
            ccw = False
        else:
            return None
        # max 270 deg per arc: a full circle = 2 arcs (never a ~360 deg arc,
        # which is numerically fragile for Grbl)
        sweep = abs(ang[-1] - ang[0])
        if sweep > 1.5 * math.pi + 1e-9:
            return None
        return (c, ccw)

    # ---- greedy extension (galloping + binary search) -------------------
    def _extend(self, i, lim, ok):
        if lim <= i:
            return i, None
        best, best_val = i, None
        step = 1
        lo = i
        j = i + 1
        hi = None
        while j <= lim:
            v = ok(i, j)
            if v:
                best, best_val, lo = j, v, j
                step *= 2
                j = i + step
                if j > lim and lo < lim:
                    j = lim
                    v = ok(i, j)
                    if v:
                        return j, v
                    hi = j
                    break
            else:
                hi = j
                break
        if hi is None:
            return best, best_val
        # binary search in (lo, hi)
        a, b = lo, hi
        while b - a > 1:
            mid = (a + b) // 2
            v = ok(i, mid)
            if v:
                a, best, best_val = mid, mid, v
            else:
                b = mid
        return best, best_val

    def run(self):
        prims = []
        p = self.p
        i = 0
        n = self.n
        while i < n - 1:
            lim = int(self.next_corner[i])
            jl, _ = self._extend(i, lim, self.line_ok)
            ja, aval = (i, None)
            if self.arcs and lim - i >= 2:
                # arcs need at least 3 points: start search from i+2
                def ok(a, b):
                    if b < a + 2:
                        return True
                    return self.arc_ok(a, b)
                ja, aval = self._extend(i, lim, ok)
                if ja < i + 2 or not isinstance(aval, tuple):
                    ja, aval = i, None
            if aval is not None and ja > jl:
                c, ccw = aval
                prims.append(("A", complex(p[ja]), complex(c), ccw))
                i = ja
            else:
                jl = max(jl, i + 1)
                prims.append(("L", complex(p[jl])))
                i = jl
        return prims


def fit_polyline(pts, tol=0.02, arcs=True):
    """pts: complex ndarray. Returns (start, prims)."""
    pts = dedup(np.asarray(pts, dtype=complex))
    if len(pts) < 2:
        return None
    prims = _Fitter(pts, tol, arcs).run()
    return complex(pts[0]), prims


# --------------------------------------------------------------------------
# toolpath helpers
# --------------------------------------------------------------------------

def prim_points(start, prims):
    """End points of each primitive, prefixed by start."""
    out = [start]
    for pr in prims:
        out.append(pr[1])
    return out


def arc_sweep(start, end, center, ccw):
    a0 = math.atan2(start.imag - center.imag, start.real - center.real)
    a1 = math.atan2(end.imag - center.imag, end.real - center.real)
    s = a1 - a0
    if ccw:
        while s <= 0:
            s += TWO_PI
    else:
        while s >= 0:
            s -= TWO_PI
    return s


def toolpath_length(start, prims):
    L = 0.0
    cur = start
    for pr in prims:
        if pr[0] != "A":
            L += abs(pr[1] - cur)
        else:
            r = abs(cur - pr[2])
            L += r * abs(arc_sweep(cur, pr[1], pr[2], pr[3]))
        cur = pr[1]
    return L


def reverse_toolpath(start, prims):
    pts = prim_points(start, prims)
    new_prims = []
    for k in range(len(prims) - 1, -1, -1):
        pr = prims[k]
        dest = pts[k]
        if pr[0] != "A":
            new_prims.append((pr[0], dest) + tuple(pr[2:]))
        else:
            new_prims.append(("A", dest, pr[2], not pr[3]))
    return pts[-1], new_prims


def rotate_closed(start, prims, k):
    """Start a closed toolpath at the end point of primitive k-1 (k in 0..n-1)."""
    if k <= 0 or k >= len(prims):
        return start, prims
    pts = prim_points(start, prims)
    return pts[k], prims[k:] + prims[:k]


def burn_polylines(start, prims, max_step_deg=6.0):
    """Polylines where the laser fires (overscan moves removed)."""
    out = []
    cur_poly = None
    cur = start
    for pr in prims:
        if pr[0] == "O" or (pr[0] == "P" and pr[2] <= 0):
            cur_poly = None
        else:
            if cur_poly is None:
                cur_poly = (cur, [])
                out.append(cur_poly)
            cur_poly[1].append(pr)
        cur = pr[1]
    return [discretize(st, pp, max_step_deg) for st, pp in out]


def discretize(start, prims, max_step_deg=6.0):
    """Points for drawing (lines exact, arcs sampled)."""
    out = [start]
    cur = start
    for pr in prims:
        if pr[0] != "A":
            out.append(pr[1])
        else:
            end, c, ccw = pr[1], pr[2], pr[3]
            s = arc_sweep(cur, end, c, ccw)
            n = max(2, int(abs(math.degrees(s)) / max_step_deg) + 1)
            r = abs(cur - c)
            a0 = math.atan2(cur.imag - c.imag, cur.real - c.real)
            for i in range(1, n):
                a = a0 + s * i / n
                out.append(c + r * complex(math.cos(a), math.sin(a)))
            out.append(end)
        cur = pr[1]
    return out
