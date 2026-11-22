# BeamCraft - hatching (fill) of closed shapes
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Scan-line hatching that handles holes and compound shapes (letters 'O', 'A',
'B'...): all sub-paths of one SVG element are hatched together, using the
element's fill-rule (nonzero or evenodd).
"""

import math
import numpy as np


def hatch_rings(rings, spacing, angle_deg=0.0, fill_rule="nonzero",
                bidirectional=True):
    """
    rings: list of complex ndarrays (closed or not - they are closed here).
    Returns a list of (p_start, p_end) complex tuples, in engraving order
    (scan line by scan line, alternating direction if bidirectional).
    """
    if spacing <= 0 or not rings:
        return []
    rot = complex(math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg)))
    inv = rot.conjugate()

    xs1, ys1, xs2, ys2 = [], [], [], []
    for r in rings:
        if len(r) < 3:
            continue
        rr = np.asarray(r) * inv
        if abs(rr[0] - rr[-1]) > 1e-9:
            rr = np.append(rr, rr[0])
        a, b = rr[:-1], rr[1:]
        xs1.append(a.real); ys1.append(a.imag); xs2.append(b.real); ys2.append(b.imag)
    if not xs1:
        return []
    x1 = np.concatenate(xs1); y1 = np.concatenate(ys1)
    x2 = np.concatenate(xs2); y2 = np.concatenate(ys2)
    horiz = np.abs(y2 - y1) < 1e-12
    x1, y1, x2, y2 = x1[~horiz], y1[~horiz], x2[~horiz], y2[~horiz]
    if len(x1) == 0:
        return []
    direction = np.where(y2 > y1, 1, -1)
    ylo = np.minimum(y1, y2)
    yhi = np.maximum(y1, y2)

    miny, maxy = ylo.min(), yhi.max()
    n_lines = int(math.floor((maxy - miny) / spacing))
    # centre the scan lines inside the shape's extent
    offset = ((maxy - miny) - n_lines * spacing) / 2.0
    out = []
    flip = False
    for k in range(n_lines + 1):
        y = miny + offset + k * spacing
        mask = (y >= ylo) & (y < yhi)
        if not np.any(mask):
            continue
        t = (y - y1[mask]) / (y2[mask] - y1[mask])
        xi = x1[mask] + t * (x2[mask] - x1[mask])
        di = direction[mask]
        order = np.argsort(xi, kind="stable")
        xi, di = xi[order], di[order]
        spans = []
        if fill_rule == "evenodd":
            for q in range(0, len(xi) - 1, 2):
                spans.append((xi[q], xi[q + 1]))
        else:
            w = 0
            start = None
            for xv, dv in zip(xi, di):
                prev = w
                w += dv
                if prev == 0 and w != 0:
                    start = xv
                elif prev != 0 and w == 0 and start is not None:
                    spans.append((start, xv))
                    start = None
        spans = [(a, b) for a, b in spans if b - a > 1e-6]
        if not spans:
            continue
        if bidirectional and flip:
            spans = [(b, a) for a, b in reversed(spans)]
        for a, b in spans:
            out.append((complex(a, y) * rot, complex(b, y) * rot))
        flip = not flip
    return out
