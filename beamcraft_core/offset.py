# BeamCraft - kerf offset, tabs (bridges), nesting depth, boolean union
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Operations on closed polylines (complex numpy arrays, machine mm).

Kerf offset
    The laser removes a strip of material (the kerf).  To get parts at the
    exact size, the path is moved OUTSIDE of the parts by half the kerf and
    INSIDE of the holes.  Whether a closed contour is a part or a hole is
    given by its nesting depth inside the other contours of the same layer:
    depth 0, 2, 4... = outer contour (part), depth 1, 3... = hole.

Tabs / bridges
    Small uncut gaps left on closed cut contours so the part stays attached
    to the sheet until the end of the job.
"""


import numpy as np
import pyclipper

SCALE = 10000.0      # clipper works with integers: 0.1 micron resolution


def _to_clip(ring):
    r = np.asarray(ring)
    if len(r) > 1 and abs(r[0] - r[-1]) < 1e-9:
        r = r[:-1]
    return [(int(round(p.real * SCALE)), int(round(p.imag * SCALE))) for p in r]


def _from_clip(path):
    arr = np.array([complex(x / SCALE, y / SCALE) for x, y in path])
    if len(arr):
        arr = np.append(arr, arr[0])
    return arr


def signed_area(ring):
    r = np.asarray(ring)
    x, y = r.real, r.imag
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def point_in_ring(pt, ring):
    xs, ys = ring.real, ring.imag
    x2, y2 = np.roll(xs, -1), np.roll(ys, -1)
    cond = (ys > pt.imag) != (y2 > pt.imag)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = xs + (pt.imag - ys) * (x2 - xs) / (y2 - ys)
    return bool(np.count_nonzero(cond & (pt.real < xint)) % 2)


def nesting_depths(rings, max_rings=3000):
    """Depth of each closed ring inside the others (0 = outermost)."""
    n = len(rings)
    if n < 2 or n > max_rings:
        return [0] * n
    bbs = np.array([(r.real.min(), r.imag.min(), r.real.max(), r.imag.max()) for r in rings])
    areas = np.array([abs(signed_area(r)) for r in rings])
    depths = []
    for i, r in enumerate(rings):
        bi = bbs[i]
        cand = np.nonzero((bbs[:, 0] <= bi[0] + 1e-9) & (bbs[:, 1] <= bi[1] + 1e-9) &
                          (bbs[:, 2] >= bi[2] - 1e-9) & (bbs[:, 3] >= bi[3] - 1e-9) &
                          (areas > areas[i]))[0]
        d = 0
        if len(cand):
            # a point of ring i that is not shared with the candidate
            pt = r[len(r) // 2]
            for j in cand:
                if j != i and point_in_ring(pt, rings[j]):
                    d += 1
        depths.append(d)
    return depths


def offset_ring(ring, delta, tol=0.01):
    """Offset one closed ring by delta mm (>0 grows, <0 shrinks). Returns list of rings."""
    if abs(delta) < 1e-9:
        return [ring]
    path = _to_clip(ring)
    if len(path) < 3:
        return []
    pco = pyclipper.PyclipperOffset()
    pco.ArcTolerance = max(tol * 0.25, 0.0005) * SCALE
    pco.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    out = pco.Execute(delta * SCALE)
    return [_from_clip(p) for p in out if len(p) >= 3]


def kerf_offset(rings, kerf, depths=None, tol=0.01):
    """
    rings: closed rings of one layer. Returns (new_rings, new_depths, n_vanished).
    Outer contours grow by kerf, holes shrink by kerf.
    """
    if depths is None:
        depths = nesting_depths(rings)
    out, out_d = [], []
    vanished = 0
    for r, d in zip(rings, depths):
        res = offset_ring(r, kerf if d % 2 == 0 else -kerf, tol)
        if not res:
            vanished += 1
        for rr in res:
            out.append(rr)
            out_d.append(d)
    return out, out_d, vanished


def add_tabs(ring, count, width):
    """
    Cut `count` gaps of `width` mm in a closed ring, evenly spaced.
    Returns a list of open polylines (in cutting order), or [ring] if the
    ring is too short.
    """
    r = np.asarray(ring)
    if count <= 0 or width <= 0 or len(r) < 3:
        return [r]
    if abs(r[0] - r[-1]) > 1e-9:
        r = np.append(r, r[0])
    seg = np.abs(np.diff(r))
    cum = np.concatenate(([0.0], np.cumsum(seg)))
    L = cum[-1]
    if L <= count * width * 2:
        return [r]
    spacing = L / count
    # tab centres, half a spacing after the start point
    gaps = [((k + 0.5) * spacing - width / 2, (k + 0.5) * spacing + width / 2) for k in range(count)]

    def point_at(s):
        i = int(np.searchsorted(cum, s, side="right") - 1)
        i = min(max(i, 0), len(seg) - 1)
        t = (s - cum[i]) / seg[i] if seg[i] > 0 else 0.0
        return r[i] + (r[i + 1] - r[i]) * t

    def sub(s0, s1):
        idx = np.nonzero((cum > s0) & (cum < s1))[0]
        pts = [point_at(s0)] + [r[i] for i in idx] + [point_at(s1)]
        return np.array(pts)

    pieces = []
    # the piece before the first gap and the piece after the last gap are
    # joined (they meet at the start point of the ring)
    first_end = gaps[0][0]
    last_start = gaps[-1][1]
    for k in range(len(gaps) - 1):
        pieces.append(sub(gaps[k][1], gaps[k + 1][0]))
    tail = sub(last_start, L)
    head = sub(0.0, first_end)
    joined = np.concatenate((tail, head[1:]))
    pieces.append(joined)
    return [p for p in pieces if len(p) >= 2]


def union_rings(rings, fill_rule="nonzero"):
    """Boolean union of closed rings -> list of rings (outer + holes)."""
    paths = [_to_clip(r) for r in rings if len(r) >= 3]
    paths = [p for p in paths if len(p) >= 3]
    if not paths:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    ft = pyclipper.PFT_EVENODD if fill_rule == "evenodd" else pyclipper.PFT_NONZERO
    out = pc.Execute(pyclipper.CT_UNION, ft, ft)
    return [_from_clip(p) for p in out if len(p) >= 3]
