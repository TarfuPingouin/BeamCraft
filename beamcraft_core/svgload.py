# BeamCraft - SVG loader
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
SVG reader for laser work.

Unlike svgpathtools.svg2paths2, this loader:
  * applies every ``transform`` (groups, layers, <use>, nested elements),
  * resolves inherited styles (fill / stroke set on a parent <g>),
  * understands simple <style> blocks (.class, #id, tag selectors),
  * resolves <use> references and skips <defs>, <clipPath>, <mask>...,
  * converts the document to millimetres using the width/height units when
    they are physical (mm, cm, in, pt, pc, Q), otherwise with a DPI value
    (Affinity Designer writes width="100%" and a viewBox in pixels at the
    document resolution).

Every visible shape is flattened into dense polylines ("rings") expressed in
millimetres, page coordinates, Y pointing DOWN, origin at the top-left corner
of the page.  The flattening tolerance is small (a few microns) and the
G-code generator re-fits lines and G2/G3 arcs on top of these points.
"""

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np
from svgpathtools import parse_path, Line
from svgpathtools.parser import parse_transform
from svgpathtools.svg_to_paths import ellipse2pathd, rect2pathd

from .colors import parse_color
from .raster import open_href, image_matrix, DocImage

MM_PER_UNIT = {
    "mm": 1.0,
    "cm": 10.0,
    "q": 0.25,
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
}

SKIP_TAGS = {
    "defs", "clipPath", "mask", "symbol", "metadata", "title", "desc",
    "style", "pattern", "marker", "linearGradient", "radialGradient",
    "filter", "script", "font", "font-face", "foreignObject", "switch-ignored",
}
SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}

INHERITED = ("fill", "stroke", "fill-rule", "visibility", "color",
             "fill-opacity", "stroke-opacity", "font-family", "font-size", "font-weight",
             "font-style", "text-anchor", "letter-spacing")
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


@dataclass
class Shape:
    rings: list            # list of (np.ndarray complex points, closed: bool)
    stroke: str | None     # "#rrggbb" or None
    fill: str | None       # "#rrggbb" or None
    fill_rule: str = "nonzero"
    elem_id: str = ""


@dataclass
class SvgDocument:
    path: str
    page_w: float          # mm
    page_h: float          # mm
    shapes: list = field(default_factory=list)
    size_source: str = ""
    warnings: list = field(default_factory=list)
    images: list = field(default_factory=list)      # raster.DocImage


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _local(tag):
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


def _parse_length(txt):
    """Returns (value, unit) - unit is '' for unit-less numbers, '%' ..."""
    if txt is None:
        return None, None
    m = re.match(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)\s*$", txt)
    if not m:
        return None, None
    return float(m.group(1)), m.group(2).lower()


def _num(txt, default=0.0):
    v, _ = _parse_length(txt)
    return default if v is None else v


def _parse_style_attr(txt):
    out = {}
    if not txt:
        return out
    for part in txt.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _parse_css(text):
    """Very small CSS parser: returns list of (selector, declarations)."""
    rules = []
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", text):
        decl = _parse_style_attr(body)
        for s in sel.split(","):
            s = s.strip()
            if s:
                rules.append((s, decl))
    return rules


def _css_matches(sel, tag, elem_id, classes):
    # supports: tag, .class, #id, tag.class
    m = re.match(r"^([a-zA-Z][\w-]*)?((?:[.#][\w-]+)*)$", sel)
    if not m:
        return False
    if m.group(1) and m.group(1) != tag:
        return False
    for kind, name in re.findall(r"([.#])([\w-]+)", m.group(2) or ""):
        if kind == "." and name not in classes:
            return False
        if kind == "#" and name != elem_id:
            return False
    return bool(m.group(1) or m.group(2))


def _translate(x, y):
    return np.array([[1.0, 0, x], [0, 1.0, y], [0, 0, 1.0]])


def _apply(m, pts):
    """Apply 3x3 affine matrix to complex numpy array."""
    x = pts.real
    y = pts.imag
    return (m[0, 0] * x + m[0, 1] * y + m[0, 2]) + 1j * (m[1, 0] * x + m[1, 1] * y + m[1, 2])


# --------------------------------------------------------------------------
# flattening
# --------------------------------------------------------------------------

def _flatten_segment(seg, m, tol, out):
    """Append the mapped points of seg (excluding its start) to out."""
    if isinstance(seg, Line):
        out.append(_apply(m, np.array([seg.end]))[0])
        return

    def P(t):
        return _apply(m, np.array([seg.point(t)]))[0]

    # initial uniform split so that S-curves / loops are not missed
    n0 = 8
    ts = [i / n0 for i in range(n0 + 1)]
    pts = [P(t) for t in ts]

    def rec(t0, p0, t1, p1, depth):
        tm = 0.5 * (t0 + t1)
        pm = P(tm)
        q1 = P(0.5 * (t0 + tm))
        q3 = P(0.5 * (tm + t1))
        chord = p1 - p0
        L = abs(chord)

        def dist(p):
            if L < 1e-12:
                return abs(p - p0)
            return abs(((p - p0) * chord.conjugate()).imag) / L

        if depth >= 18 or max(dist(pm), dist(q1), dist(q3)) <= tol:
            out.append(p1)
            return
        rec(t0, p0, tm, pm, depth + 1)
        rec(tm, pm, t1, p1, depth + 1)

    for k in range(n0):
        rec(ts[k], pts[k], ts[k + 1], pts[k + 1], 0)


def path_to_rings(path, m, tol):
    rings = []
    for sub in path.continuous_subpaths():
        if len(sub) == 0:
            continue
        pts = [_apply(m, np.array([sub[0].start]))[0]]
        for seg in sub:
            try:
                if seg.length(error=1e-6) == 0:
                    continue
            except Exception:
                pass
            _flatten_segment(seg, m, tol, pts)
        arr = np.array(pts, dtype=complex)
        if len(arr) >= 2:
            keep = np.concatenate(([True], np.abs(np.diff(arr)) > 1e-7))
            arr = arr[keep]
        if len(arr) < 2:
            continue
        closed = bool(sub.isclosed()) or abs(arr[0] - arr[-1]) < 1e-6
        if closed:
            arr[-1] = arr[0]
        rings.append((arr, closed))
    return rings


# --------------------------------------------------------------------------
# loader
# --------------------------------------------------------------------------

def _shape_d(tag, a):
    if tag == "path":
        return a.get("d", "")
    if tag == "rect":
        w = _num(a.get("width"))
        h = _num(a.get("height"))
        if w <= 0 or h <= 0:
            return ""
        d = {"x": str(_num(a.get("x"))), "y": str(_num(a.get("y"))),
             "width": str(w), "height": str(h)}
        rx, ry = a.get("rx"), a.get("ry")
        if rx is not None or ry is not None:
            rxv = _num(rx if rx is not None else ry)
            ryv = _num(ry if ry is not None else rx)
            rxv = min(rxv, w / 2)
            ryv = min(ryv, h / 2)
            if rxv > 0 and ryv > 0:
                d["rx"] = str(rxv)
                d["ry"] = str(ryv)
        return rect2pathd(d)
    if tag in ("circle", "ellipse"):
        d = {"cx": str(_num(a.get("cx"))), "cy": str(_num(a.get("cy")))}
        if tag == "circle":
            r = _num(a.get("r"))
            if r <= 0:
                return ""
            d["r"] = str(r)
        else:
            rx, ry = _num(a.get("rx")), _num(a.get("ry"))
            if rx <= 0 or ry <= 0:
                return ""
            d["rx"], d["ry"] = str(rx), str(ry)
        return ellipse2pathd(d)
    if tag == "line":
        return "M{},{} L{},{}".format(_num(a.get("x1")), _num(a.get("y1")),
                                      _num(a.get("x2")), _num(a.get("y2")))
    if tag in ("polyline", "polygon"):
        nums = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", a.get("points", ""))
        vals = [float(v) for v in nums]
        if len(vals) < 4:
            return ""
        pairs = ["{},{}".format(vals[k], vals[k + 1]) for k in range(0, len(vals) - 1, 2)]
        d = "M" + " L".join(pairs)
        return d + (" Z" if tag == "polygon" else "")
    return ""


def _root_matrix(root, dpi, force_dpi, warnings):
    """Returns (matrix user->mm, page_w_mm, page_h_mm, description)."""
    wv, wu = _parse_length(root.get("width"))
    hv, hu = _parse_length(root.get("height"))
    vb = root.get("viewBox") or root.get("viewbox")
    vbox = None
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            try:
                vbox = [float(p) for p in parts]
                if vbox[2] <= 0 or vbox[3] <= 0:
                    vbox = None
            except ValueError:
                vbox = None

    physical = (not force_dpi and wv and hv and wu in MM_PER_UNIT and hu in MM_PER_UNIT)
    mm_per_px = 25.4 / float(dpi)

    if physical:
        W = wv * MM_PER_UNIT[wu]
        H = hv * MM_PER_UNIT[hu]
        if vbox:
            sx = W / vbox[2]
            sy = H / vbox[3]
            par = (root.get("preserveAspectRatio") or "xMidYMid meet").split()
            if par[0] == "none":
                tx, ty = 0.0, 0.0
            else:
                s = min(sx, sy) if (len(par) < 2 or par[1] == "meet") else max(sx, sy)
                align = par[0]
                fx = 0.0 if "xMin" in align else (1.0 if "xMax" in align else 0.5)
                fy = 0.0 if "YMin" in align else (1.0 if "YMax" in align else 0.5)
                tx = (W - vbox[2] * s) * fx
                ty = (H - vbox[3] * s) * fy
                sx = sy = s
            m = np.array([[sx, 0, tx - vbox[0] * sx], [0, sy, ty - vbox[1] * sy], [0, 0, 1.0]])
        else:
            s = 25.4 / 96.0   # user unit = CSS px
            m = np.array([[s, 0, 0], [0, s, 0], [0, 0, 1.0]])
        desc = f"size from file: {W:.2f} x {H:.2f} mm"
        return m, W, H, desc

    # Pixel based document (Affinity Designer, Illustrator, unit-less px...)
    px_x = px_y = 1.0
    if vbox:
        if wv and wu in ("", "px") and hv and hu in ("", "px"):
            px_x = wv / vbox[2]
            px_y = hv / vbox[3]
        W = vbox[2] * px_x * mm_per_px
        H = vbox[3] * px_y * mm_per_px
        m = np.array([[px_x * mm_per_px, 0, -vbox[0] * px_x * mm_per_px],
                      [0, px_y * mm_per_px, -vbox[1] * px_y * mm_per_px],
                      [0, 0, 1.0]])
    else:
        W = (wv if wv and wu in ("", "px") else 0) * mm_per_px
        H = (hv if hv and hu in ("", "px") else 0) * mm_per_px
        m = np.array([[mm_per_px, 0, 0], [0, mm_per_px, 0], [0, 0, 1.0]])
    why = "forced" if force_dpi else "no physical size in file"
    desc = f"size from {dpi} DPI ({why}): {W:.2f} x {H:.2f} mm"
    return m, W, H, desc


def load_svg(filepath, dpi=96, force_dpi=False, tol=0.005):
    tree = ET.parse(filepath)
    root = tree.getroot()
    warnings = []

    m_root, W, H, desc = _root_matrix(root, dpi, force_dpi, warnings)

    # id map + css rules
    ids = {}
    css = []
    for el in root.iter():
        i = el.get("id")
        if i:
            ids[i] = el
        if _local(el.tag) == "style" and el.text:
            css.extend(_parse_css(el.text))

    shapes = []
    images = []
    counters = {"text": 0, "image": 0}
    missing_fonts = set()

    def text_shapes(el, m, st):
        from .fonts import layout_text, _num_list
        chunks = []

        def collect(node, style, preserve):
            preserve = (node.get(XML_SPACE) or ("preserve" if preserve else "")) == "preserve"
            chunks.append({"text": node.text, "style": style, "preserve": preserve,
                           "x": _num_list(node.get("x")), "y": _num_list(node.get("y")),
                           "dx": _num_list(node.get("dx")), "dy": _num_list(node.get("dy"))})
            for child in node:
                ctag = _local(child.tag)
                if ctag in ("tspan", "a"):
                    cst = computed_style(child, ctag, style)
                    if cst["_display"] != "none":
                        collect(child, cst, preserve)
                if child.tail:
                    chunks.append({"text": child.tail, "style": style, "preserve": preserve,
                                   "x": [], "y": [], "dx": [], "dy": []})

        collect(el, st, False)
        try:
            glyphs, missing = layout_text(chunks)
        except Exception as e:
            warnings.append(f"Text could not be converted ({e}).")
            counters["text"] += 1
            return
        missing_fonts.update(missing)
        groups = {}
        for d, gst, shift in glyphs:
            fill = parse_color(gst.get("fill", "black"))
            stroke = parse_color(gst.get("stroke", "none"))
            if fill is None and stroke is None:
                continue
            try:
                path = parse_path(d)
            except Exception:
                continue
            rings = path_to_rings(path, m @ _translate(shift, 0.0), tol)
            groups.setdefault((stroke, fill), []).extend(rings)
        for (stroke, fill), rings in groups.items():
            if rings:
                shapes.append(Shape(rings=rings, stroke=stroke, fill=fill, fill_rule="nonzero",
                                    elem_id=el.get("id") or "text"))

    def computed_style(el, tag, parent):
        st = {k: parent[k] for k in INHERITED if k in parent}
        own = {}
        for k in INHERITED + ("display", "opacity"):
            if el.get(k) is not None:
                own[k] = el.get(k).strip()
        if css:
            classes = (el.get("class") or "").split()
            for sel, decl in css:
                if _css_matches(sel, tag, el.get("id"), classes):
                    own.update(decl)
        own.update(_parse_style_attr(el.get("style")))
        for k, v in own.items():
            if v != "inherit":
                st[k] = v
        # 'currentColor'
        for k in ("fill", "stroke"):
            if st.get(k, "").lower() == "currentcolor":
                st[k] = st.get("color", "black")
        st["_opacity"] = parent.get("_opacity", 1.0) * _num(own.get("opacity"), 1.0)
        st["_display"] = own.get("display", "inline")
        return st

    def walk(el, ctm, parent_style, depth=0):
        if depth > 64:
            return
        tag = _local(el.tag)
        if tag in SKIP_TAGS:
            return
        st = computed_style(el, tag, parent_style)
        if st["_display"] == "none" or st["_opacity"] <= 0:
            return

        tf = el.get("transform")
        m = ctm
        if tf:
            try:
                m = m @ parse_transform(tf)
            except Exception:
                warnings.append(f"Unreadable transform ignored: {tf!r}")

        if tag in ("svg", "g", "a", "switch") or (tag == "svg" and depth > 0):
            if tag == "svg" and depth > 0:
                m = m @ _translate(_num(el.get("x")), _num(el.get("y")))
            for child in el:
                walk(child, m, st, depth + 1)
            return

        if tag == "use":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
            target = ids.get(href.lstrip("#"))
            if target is None:
                return
            m2 = m @ _translate(_num(el.get("x")), _num(el.get("y")))
            ttag = _local(target.tag)
            if ttag == "symbol":
                st2 = computed_style(target, ttag, st)
                for child in target:
                    walk(child, m2, st2, depth + 1)
            else:
                walk(target, m2, st, depth + 1)
            return

        if tag == "text":
            text_shapes(el, m, st)
            return
        if tag == "image":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
            img = open_href(href.strip(), os.path.dirname(os.path.abspath(filepath)))
            if img is None:
                counters["image"] += 1
                return
            iw, ih = img.size
            w = _num(el.get("width"), iw)
            h = _num(el.get("height"), ih)
            if w <= 0 or h <= 0:
                return
            pm = image_matrix(iw, ih, _num(el.get("x")), _num(el.get("y")), w, h,
                              el.get("preserveAspectRatio"))
            images.append(DocImage(img, m @ pm, el.get("id") or os.path.basename(href[:40])))
            return
        if tag not in SHAPE_TAGS:
            return
        if st.get("visibility", "visible") in ("hidden", "collapse"):
            return

        fill = parse_color(st.get("fill", "black"))
        stroke = parse_color(st.get("stroke", "none"))
        if _num(st.get("fill-opacity"), 1.0) <= 0:
            fill = None
        if _num(st.get("stroke-opacity"), 1.0) <= 0:
            stroke = None
        if tag in ("line", "polyline"):
            fill = None if tag == "line" else fill
        if fill is None and stroke is None:
            return

        d = _shape_d(tag, el.attrib)
        if not d:
            return
        try:
            path = parse_path(d)
        except Exception:
            warnings.append(f"Unreadable path ignored (id={el.get('id')})")
            return
        rings = path_to_rings(path, m, tol)
        if not rings:
            return
        rule = st.get("fill-rule", "nonzero").strip().lower()
        shapes.append(Shape(rings=rings, stroke=stroke, fill=fill,
                            fill_rule="evenodd" if rule == "evenodd" else "nonzero",
                            elem_id=el.get("id") or ""))

    base_style = {"_opacity": 1.0}
    for child in root:
        walk(child, m_root, computed_style(root, "svg", base_style), 1)

    if counters["text"]:
        warnings.append(f"{counters['text']} text element(s) ignored: convert text to curves before exporting.")
    if missing_fonts:
        warnings.append("Font(s) not found, replaced by a similar font: " + ", ".join(sorted(missing_fonts))
                        + ". Install the font or export text as curves.")
    if counters["image"]:
        warnings.append(f"{counters['image']} bitmap image(s) could not be read (missing linked file?).")

    if W <= 0 or H <= 0:
        # no page size: use drawing bounds
        allp = [r for s in shapes for r, _ in s.rings]
        for im in images:
            iw, ih = im.size
            allp.append(_apply(im.matrix, np.array([0, iw, iw + 1j * ih, 1j * ih])))
        if allp:
            pts = np.concatenate(allp)
            W = max(1e-3, float(pts.real.max()))
            H = max(1e-3, float(pts.imag.max()))
        else:
            W = H = 1.0
        desc += " (page = drawing bounds)"

    return SvgDocument(path=filepath, page_w=W, page_h=H, shapes=shapes,
                       size_source=desc, warnings=warnings, images=images)
