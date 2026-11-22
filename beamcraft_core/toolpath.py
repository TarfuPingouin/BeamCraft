# BeamCraft - job building: transforms, layers, hatching, ordering
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Builds the complete laser job (machine coordinates, mm, Y up) from an
SvgDocument.  The same Job object feeds both the G-code writer and the
G-code preview, so the preview always shows exactly what will be sent.

Layers are made by colour:
    stroke:#rrggbb  -> outlines of the shapes drawn with that stroke colour
    fill:#rrggbb    -> shapes filled with that colour
Each layer has a mode (line / fill / line+fill) and its own settings.
"""

import math
from dataclasses import dataclass, field

import numpy as np

from .geometry import (fit_polyline, reverse_toolpath, rotate_closed,
                       prim_points, toolpath_length, discretize)
from .hatch import hatch_rings
from .offset import nesting_depths, kerf_offset, add_tabs, union_rings, point_in_ring
from .settings import layer_label


@dataclass
class View:
    """Placement of the design on the work area."""
    rotation: float = 0.0    # degrees, counter-clockwise, about the design centre
    flip_h: bool = False
    flip_v: bool = False
    x: float = None          # work-area position (mm) of the design centre,
    y: float = None          #   None = where it is on the page (page bottom-left = 0,0)
    scale: float = 100.0     # %


@dataclass
class LayerJob:
    name: str                # layer key
    settings: object         # LayerSettings
    paths: list              # list of (start, prims)
    length: float = 0.0      # mm, one pass (laser on)
    kind: str = "vector"     # "vector" or "raster"
    rasters: list = field(default_factory=list)   # raster previews (image layers)


@dataclass
class Job:
    layers: list
    box: list                # 4 corners of the placed design (work-area coords)
    bounds: tuple            # (minx, miny, maxx, maxy) of all toolpaths (work-area coords)
    ref: complex = 0j        # G-code coordinates = work-area coordinates - ref
    center: complex = 0j     # design centre on the work area
    warnings: list = field(default_factory=list)
    travel_length: float = 0.0
    est_time_s: float = 0.0
    n_paths: int = 0


# --------------------------------------------------------------------------
# transforms
# --------------------------------------------------------------------------

def _apply(m, pts):
    x, y = pts.real, pts.imag
    return (m[0, 0] * x + m[0, 1] * y + m[0, 2]) + 1j * (m[1, 0] * x + m[1, 1] * y + m[1, 2])


def content_bounds(doc):
    """Bounds (page coords, mm, Y down) of everything drawn in the document."""
    cached = getattr(doc, "_content_bounds", None)
    if cached is not None:
        return cached
    pts = [r for sh in doc.shapes for r, _c in sh.rings]
    for im in getattr(doc, "images", []) or []:
        iw, ih = im.size
        pts.append(_apply(im.matrix, np.array([0, iw, iw + 1j * ih, 1j * ih])))
    if pts:
        a = np.concatenate(pts)
        b = (float(a.real.min()), float(a.imag.min()), float(a.real.max()), float(a.imag.max()))
    else:
        b = (0.0, 0.0, doc.page_w, doc.page_h)
    try:
        doc._content_bounds = b
    except AttributeError:
        pass
    return b


def placement(doc, view):
    """
    Matrix page (mm, Y down) -> work area (mm, Y up) for a View, the design
    centre on the work area and the 4 corners of the placed design box.
    """
    x0, y0, x1, y1 = content_bounds(doc)
    H = doc.page_h
    Y = np.array([[1, 0, 0], [0, -1, H], [0, 0, 1.0]])           # page -> Y up, page bottom-left = 0,0
    c0 = complex((x0 + x1) / 2, H - (y0 + y1) / 2)                # centre of the design (Y up)
    T0 = np.array([[1, 0, -c0.real], [0, 1, -c0.imag], [0, 0, 1.0]])
    F = np.diag([-1.0 if view.flip_h else 1.0, -1.0 if view.flip_v else 1.0, 1.0])
    k = (view.scale or 100.0) / 100.0
    th = math.radians(view.rotation or 0.0)
    c, s_ = math.cos(th), math.sin(th)
    if abs(c) < 1e-12:
        c = 0.0
    if abs(s_) < 1e-12:
        s_ = 0.0
    R = np.array([[c * k, -s_ * k, 0], [s_ * k, c * k, 0], [0, 0, 1.0]])
    px = c0.real if view.x is None else view.x
    py = c0.imag if view.y is None else view.y
    T1 = np.array([[1, 0, px], [0, 1, py], [0, 0, 1.0]])
    local = T1 @ R @ F @ T0
    box = _apply(local, np.array([complex(x0, H - y1), complex(x1, H - y1), complex(x1, H - y0),
                                  complex(x0, H - y0)]))
    return local @ Y, complex(px, py), list(box)


def machine_matrix(page_w, page_h, view):
    """Page -> work area for a document page only (kept for tests / tools)."""
    class _D:
        pass
    d = _D()
    d.page_w, d.page_h, d.shapes, d.images = page_w, page_h, [], []
    d._content_bounds = (0.0, 0.0, page_w, page_h)
    M, _c, _b = placement(d, view)
    return M, page_w, page_h


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------

class _Item:
    __slots__ = ("paths", "closed", "start", "end", "depth")

    def __init__(self, paths, closed=False):
        self.paths = paths
        self.closed = closed
        self.start = paths[0][0]
        self.end = prim_points(*paths[-1])[-1]
        self.depth = 0

    def reversed(self):
        new = [reverse_toolpath(s, p) for s, p in reversed(self.paths)]
        it = _Item(new, self.closed)
        it.depth = self.depth
        return it


def _nearest_order(items, pos):
    """Greedy nearest-neighbour; items may be reversed, closed loops re-started."""
    if not items:
        return [], pos
    remaining = list(items)
    starts = np.array([it.start for it in remaining])
    ends = np.array([it.end for it in remaining])
    alive = np.ones(len(remaining), dtype=bool)
    out = []
    for _ in range(len(remaining)):
        ds = np.abs(starts - pos)
        de = np.abs(ends - pos)
        ds[~alive] = np.inf
        de[~alive] = np.inf
        i_s, i_e = int(np.argmin(ds)), int(np.argmin(de))
        if de[i_e] < ds[i_s]:
            idx, rev = i_e, True
        else:
            idx, rev = i_s, False
        alive[idx] = False
        it = remaining[idx]
        if rev:
            it = it.reversed()
        if it.closed and len(it.paths) == 1:
            s, prims = it.paths[0]
            pts = np.array(prim_points(s, prims)[:-1])
            if len(pts) > 1:
                k = int(np.argmin(np.abs(pts - pos)))
                if k > 0:
                    d = it.depth
                    it = _Item([rotate_closed(s, prims, k)], True)
                    it.depth = d
        out.append(it)
        pos = it.end
    return out, pos


def order_items(items, pos, inner_first=False):
    if inner_first:
        out = []
        for d in sorted({it.depth for it in items}, reverse=True):
            grp = [it for it in items if it.depth == d]
            o, pos = _nearest_order(grp, pos)
            out.extend(o)
        return out, pos
    return _nearest_order(items, pos)


# --------------------------------------------------------------------------
# per layer geometry
# --------------------------------------------------------------------------

def _line_items(key, g, L, s, warnings):
    """Kerf offset + tabs + line/arc fitting for the outlines of one layer."""
    out = []
    closed = g["closed"]
    depths = nesting_depths(closed) if closed else []
    label = layer_label(key)
    if L.kerf and closed:
        closed, depths, vanished = kerf_offset(closed, L.kerf, depths, s.tolerance)
        if vanished:
            warnings.append(f"{label}: {vanished} contour(s) too small for the kerf offset were removed.")
    if L.kerf and g["open"]:
        warnings.append(f"{label}: kerf offset not applied to {len(g['open'])} open path(s).")
    for ring, d in zip(closed, depths):
        pieces = None
        if L.tabs_count > 0 and (d % 2 == 0 or not L.tabs_outer_only):
            perim = float(np.sum(np.abs(np.diff(ring))))
            if perim >= L.tabs_min_length:
                pieces = add_tabs(ring, int(L.tabs_count), L.tab_width)
                if len(pieces) <= 1:
                    pieces = None
        if pieces:
            tps = [fit_polyline(p, s.tolerance, s.use_arcs) for p in pieces]
            tps = [tp for tp in tps if tp and tp[1]]
            if tps:
                it = _Item(tps, False)
                it.depth = d
                out.append(it)
            continue
        tp = fit_polyline(ring, s.tolerance, s.use_arcs)
        if tp and tp[1]:
            it = _Item([tp], True)
            it.depth = d
            out.append(it)
    for pts in g["open"]:
        tp = fit_polyline(pts, s.tolerance, s.use_arcs)
        if tp and tp[1]:
            out.append(_Item([tp], False))
    return out


def _hatch_paths(segs, overscan):
    paths = []
    for a, b in segs:
        if overscan > 0:
            d = b - a
            u = d / abs(d)
            paths.append((a - u * overscan, [("O", a), ("L", b), ("O", b + u * overscan)]))
        else:
            paths.append((a, [("L", b)]))
    return paths


def _fill_items(g, L, s):
    out = []
    angles = [L.hatch_angle] + ([L.hatch_angle + 90.0] if L.hatch_cross else [])
    shapes = g["fills"]
    if L.hatch_merge and len(shapes) > 1:
        # union of overlapping shapes: hatched once, no double burn
        merged = []
        for rings, rule in shapes:
            merged.extend(union_rings(rings, rule))
        merged = union_rings(merged, "nonzero")
        # hatch each separate island on its own to keep travel short
        shapes = _split_islands(merged) if merged else []
    for rings, rule in shapes:
        for ang in angles:
            segs = hatch_rings(rings, L.hatch_spacing, ang, rule, L.hatch_bidirectional)
            if segs:
                out.append(_Item(_hatch_paths(segs, L.overscan)))
    return out


def _split_islands(rings):
    """Group rings (outer + its holes) after a union -> [(rings, 'evenodd')]."""
    depths = nesting_depths(rings)
    outers = [i for i, d in enumerate(depths) if d % 2 == 0]
    groups = {i: [rings[i]] for i in outers}
    for i, d in enumerate(depths):
        if d % 2 == 1:
            # attach the hole to the smallest outer ring containing it
            best = None
            for o in outers:
                if depths[o] == d - 1 and point_in_ring(rings[i][0], rings[o]):
                    best = o
                    break
            if best is not None:
                groups[best].append(rings[i])
    return [(g, "evenodd") for g in groups.values()]


# --------------------------------------------------------------------------
# job
# --------------------------------------------------------------------------

def document_layers(doc):
    """Layer keys present in the document -> number of shapes (in order of appearance)."""
    out = {}
    for sh in doc.shapes:
        if sh.stroke:
            k = "stroke:" + sh.stroke
            out[k] = out.get(k, 0) + 1
        if sh.fill:
            k = "fill:" + sh.fill
            out[k] = out.get(k, 0) + 1
    if getattr(doc, "images", None):
        out["image"] = len(doc.images)
    return out


def build_job(doc, settings, view=None):
    view = view or View()
    s = settings
    M, center, box = placement(doc, view)
    warnings = list(doc.warnings)

    groups = {}

    def grp(key):
        if key not in groups:
            groups[key] = {"open": [], "closed": [], "fills": []}
        return groups[key]

    for shape in doc.shapes:
        rings = [(_apply(M, r), closed) for r, closed in shape.rings]
        closed_rings = [r for r, _c in rings if len(r) >= 3]
        if shape.stroke:
            g = grp("stroke:" + shape.stroke)
            for pts, closed in rings:
                (g["closed"] if closed and len(pts) >= 4 else g["open"]).append(pts)
            if closed_rings:
                g["fills"].append((closed_rings, shape.fill_rule))
        if shape.fill:
            g = grp("fill:" + shape.fill)
            for pts in closed_rings:
                ring = pts if abs(pts[0] - pts[-1]) < 1e-9 else np.append(pts, pts[0])
                if len(ring) >= 4:
                    g["closed"].append(ring)
            if closed_rings:
                g["fills"].append((closed_rings, shape.fill_rule))

    layer_items = []
    for key in s.ordered(list(groups.keys())):
        L = s.layer(key)
        if not L.enabled:
            continue
        g = groups[key]
        fill_items = _fill_items(g, L, s) if "fill" in L.mode else []
        line_items = _line_items(key, g, L, s, warnings) if "line" in L.mode else []
        if fill_items or line_items:
            layer_items.append((key, L, fill_items, line_items))

    # ---- raster images (filled in by raster.py when present) ----
    raster_layers = []
    if getattr(doc, "images", None):
        from .raster import build_raster_layer
        L = s.layer("image")
        if L.enabled:
            rl = build_raster_layer(doc, L, s, M, warnings)
            if rl is not None:
                raster_layers.append(rl)

    # ---- bounds and G-code origin ----
    all_pts = []
    for _k, _L, fi, li in layer_items:
        for it in fi + li:
            for st, pr in it.paths:
                all_pts.extend(discretize(st, pr, 4))
    for rl in raster_layers:
        all_pts.extend(rl.bounds_pts)
    if all_pts:
        arr = np.array(all_pts)
        bmin = complex(arr.real.min(), arr.imag.min())
        bmax = complex(arr.real.max(), arr.imag.max())
    else:
        bx = np.array(box)
        bmin = complex(bx.real.min(), bx.imag.min())
        bmax = complex(bx.real.max(), bx.imag.max())
    if s.start_from == "current":
        lo, hi = bmin, bmax
        o = s.origin
        if o == "bottom left":
            ref = lo
        elif o == "top left":
            ref = complex(lo.real, hi.imag)
        elif o == "bottom right":
            ref = complex(hi.real, lo.imag)
        elif o == "top right":
            ref = hi
        else:
            ref = (lo + hi) / 2
    else:
        ref = 0j

    # ---- ordering, statistics ----
    pos = ref
    layers = []
    travel = 0.0
    est = 0.0
    n_paths = 0
    entries = [(k, L, fi, li, None) for k, L, fi, li in layer_items]
    entries += [("image", rl.settings, [], [], rl) for rl in raster_layers]
    entries = [entries[i] for i in _order_index([e[0] for e in entries], s)]
    for key, L, fill_items, line_items, rl in entries:
        if rl is not None:
            paths = rl.paths
            lj = LayerJob(key, L, paths, kind="raster", rasters=list(rl.previews))
        else:
            if s.optimize_order:
                fo, pos_f = order_items(fill_items, pos)
                lo_, _ = order_items(line_items, pos_f, inner_first=True)
            else:
                fo, lo_ = fill_items, line_items
            paths = [p for it in fo + lo_ for p in it.paths]
            lj = LayerJob(key, L, paths)
        if not paths:
            continue
        lj.length = sum(_burn_length(st, pr) for st, pr in paths)
        tr = 0.0
        cur = pos
        moving = 0.0
        for st, pr in paths:
            tr += abs(st - cur)
            moving += toolpath_length(st, pr)
            cur = prim_points(st, pr)[-1]
        passes = max(1, int(L.passes))
        travel += tr * passes
        est += passes * (moving / L.feed * 60.0 + tr / s.travel_feed * 60.0)
        n_paths += len(paths)
        pos = cur
        layers.append(lj)

    travel += abs(pos - ref) if s.return_to_origin else 0.0
    bounds = (bmin.real, bmin.imag, bmax.real, bmax.imag)

    # ---- rotary: the Y axis turns the object (scaled about the G-code origin) ----
    kr = s.rotary_factor()
    if abs(kr - 1.0) > 1e-12:
        oy = ref.imag
        for lj in layers:
            lj.paths = [_rotary_path(st, pr, kr, s, oy) for st, pr in lj.paths]
            for r in lj.rasters:
                r.y1 = oy + (r.y1 - oy) * kr
                r.dy *= kr
        box = [complex(c.real, oy + (c.imag - oy) * kr) for c in box]
        bounds = (bounds[0], oy + (bounds[1] - oy) * kr, bounds[2], oy + (bounds[3] - oy) * kr)

    if s.start_from == "absolute" and any(L.paths for L in layers):
        if bounds[0] < -1e-6 or bounds[1] < -1e-6 or bounds[2] > s.bed_width + 1e-6 \
                or bounds[3] > s.bed_height + 1e-6:
            warnings.append(f"The job ({bounds[0]:.1f}, {bounds[1]:.1f}) - ({bounds[2]:.1f}, {bounds[3]:.1f}) mm "
                            f"goes outside the work area (0, 0) - ({s.bed_width:g}, {s.bed_height:g}) mm.")

    return Job(layers=layers, box=box, bounds=bounds, ref=ref, center=center,
               warnings=warnings, travel_length=travel, est_time_s=est, n_paths=n_paths)


def _rotary_path(st, pr, kr, s, oy=0.0):
    """Scale Y by kr about y = oy. Arcs become ellipses: re-fitted with lines / arcs."""
    def sc(p):
        return complex(p.real, oy + (p.imag - oy) * kr)
    if any(p[0] == "A" for p in pr):
        pts = np.array([sc(p) for p in discretize(st, pr, 2.0)])
        tp = fit_polyline(pts, s.tolerance, s.use_arcs)
        if tp:
            return tp
    return sc(st), [(p[0], sc(p[1])) + tuple(p[2:]) for p in pr]


def _order_index(keys, s):
    order = s.ordered(keys)
    return sorted(range(len(keys)), key=lambda i: order.index(keys[i]))


def _burn_length(st, pr):
    L = 0.0
    cur = st
    for p in pr:
        if p[0] != "O" and not (p[0] == "P" and p[2] <= 0):
            L += toolpath_length(cur, [p])
        cur = p[1]
    return L
