# BeamCraft - SVG <text> to outlines with the system fonts
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Converts SVG text into glyph outlines using the fonts installed on the
computer (fontTools).  Fonts are looked up by family name or PostScript name
(Affinity Designer writes e.g. font-family:'ArialMT','Arial',sans-serif),
weight and style.  The font index is cached in the settings folder.

Supported: <text>/<tspan>, x/y/dx/dy (lists = per character positions),
font-family, font-size, font-weight, font-style, text-anchor,
letter-spacing, xml:space.  Not supported: kerning tables, textPath,
vertical text, complex scripts shaping.
"""

import json
import os
import re
import sys
from pathlib import Path

try:
    from fontTools.ttLib import TTFont, TTCollection
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
except ImportError:   # pragma: no cover
    TTFont = None

FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")
GENERIC = {
    "sans-serif": ["arial", "helvetica", "liberation sans", "dejavu sans", "open sans", "noto sans",
                   "roboto", "segoe ui", "verdana"],
    "serif": ["times new roman", "times", "liberation serif", "dejavu serif", "noto serif", "georgia"],
    "monospace": ["courier new", "consolas", "liberation mono", "dejavu sans mono", "noto sans mono"],
    "cursive": ["comic sans ms", "brush script mt"],
    "fantasy": ["impact"],
}


def font_dirs():
    home = Path.home()
    if sys.platform.startswith("win"):
        win = Path(os.environ.get("WINDIR", "C:/Windows"))
        return [win / "Fonts", Path(os.environ.get("LOCALAPPDATA", home)) / "Microsoft" / "Windows" / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts",
            home / ".local" / "share" / "fonts"]


def _names(font):
    name = font["name"]

    def get(*ids):
        for i in ids:
            rec = name.getDebugName(i)
            if rec:
                return rec
        return ""
    weight = 400
    italic = False
    if "OS/2" in font:
        weight = int(getattr(font["OS/2"], "usWeightClass", 400) or 400)
        italic = bool(font["OS/2"].fsSelection & 1)
    sub = get(17, 2)
    if "italic" in sub.lower() or "oblique" in sub.lower():
        italic = True
    return {"family": get(16, 1), "sub": sub, "ps": get(6), "full": get(4),
            "weight": weight, "italic": italic}


class FontIndex:
    def __init__(self, cache_path=None):
        self.cache_path = Path(cache_path) if cache_path else None
        self.entries = None
        self._fonts = {}

    def _signature(self, files):
        return [len(files), max((os.path.getmtime(f) for f in files), default=0)]

    def load(self):
        if self.entries is not None:
            return self.entries
        files = []
        for d in font_dirs():
            if d.is_dir():
                for root, _dirs, names in os.walk(d):
                    files.extend(os.path.join(root, n) for n in names if n.lower().endswith(FONT_EXT))
        sig = self._signature(files)
        if self.cache_path and self.cache_path.is_file():
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if data.get("sig") == sig:
                    self.entries = data["fonts"]
                    return self.entries
            except Exception:
                pass
        entries = []
        for f in files:
            try:
                if f.lower().endswith((".ttc", ".otc")):
                    coll = TTCollection(f, lazy=True)
                    for i, font in enumerate(coll.fonts):
                        e = _names(font)
                        e.update(path=f, index=i)
                        entries.append(e)
                else:
                    font = TTFont(f, lazy=True)
                    e = _names(font)
                    e.update(path=f, index=0)
                    entries.append(e)
            except Exception:
                continue
        self.entries = entries
        if self.cache_path:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps({"sig": sig, "fonts": entries}), encoding="utf-8")
            except Exception:
                pass
        return entries

    def find(self, families, weight=400, italic=False):
        """Returns (entry, exact) - exact False when a fallback font was used."""
        entries = self.load()
        if not entries:
            return None, False
        wanted = []
        concrete = False      # a real family name was requested (not only "sans-serif"...)
        for f in families:
            f = f.strip().strip("'\"").strip()
            if not f:
                continue
            fl = f.lower()
            if fl in GENERIC:
                for g in GENERIC[fl]:
                    wanted.append((g, not concrete))
            else:
                concrete = True
                wanted.append((fl, True))

        def score(e):
            return abs(e["weight"] - weight) + (0 if e["italic"] == italic else 1000)

        for fl, exact in wanted:
            nospace = fl.replace(" ", "")
            cands = [e for e in entries
                     if e["family"].lower() == fl or e["ps"].lower() == fl or e["full"].lower() == fl
                     or e["family"].lower().replace(" ", "") == nospace]
            if cands:
                # a PostScript name designates one exact face
                ps = [e for e in cands if e["ps"].lower() == fl]
                if ps:
                    return ps[0], exact
                return min(cands, key=score), exact
        for g in GENERIC["sans-serif"]:
            cands = [e for e in entries if e["family"].lower() == g]
            if cands:
                return min(cands, key=score), not concrete
        return min(entries, key=score), not concrete

    def font(self, entry, weight=None):
        key = (entry["path"], entry["index"], weight)
        f = self._fonts.get(key)
        if f is None:
            f = TTFont(entry["path"], fontNumber=entry["index"]) if entry["path"].lower().endswith(
                (".ttc", ".otc")) else TTFont(entry["path"])
            # variable font (e.g. OpenSans[wdth,wght].ttf): instantiate the weight
            if weight is not None and "fvar" in f:
                axes = {a.axisTag: a for a in f["fvar"].axes}
                if "wght" in axes:
                    a = axes["wght"]
                    w = min(max(weight, a.minValue), a.maxValue)
                    if abs(w - a.defaultValue) > 1:
                        try:
                            from fontTools.varLib.instancer import instantiateVariableFont
                            f = instantiateVariableFont(f, {"wght": w})
                        except Exception:
                            pass
            self._fonts[key] = f
        return f


_INDEX = None


def get_index():
    global _INDEX
    if _INDEX is None:
        from .settings import config_path
        _INDEX = FontIndex(config_path().parent / "fonts-index.json")
    return _INDEX


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------

def _num_list(txt):
    if not txt:
        return []
    out = []
    for v in re.split(r"[\s,]+", txt.strip()):
        m = re.match(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", v)
        if m:
            out.append(float(m.group(1)))
    return out


def _font_size(txt, default=16.0):
    if not txt:
        return default
    m = re.match(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*([a-z%]*)", txt.lower())
    if not m:
        return default
    v, u = float(m.group(1)), m.group(2)
    return {"": v, "px": v, "pt": v * 96 / 72, "mm": v * 96 / 25.4, "pc": v * 16,
            "in": v * 96, "cm": v * 96 / 2.54}.get(u, default)


def _weight(txt):
    t = (txt or "normal").strip().lower()
    if t in ("bold", "bolder"):
        return 700
    if t in ("normal", "lighter"):
        return 400 if t == "normal" else 300
    try:
        return int(float(t))
    except ValueError:
        return 400


def layout_text(chunks, index=None):
    """
    chunks: list of dicts {text, style, x, y, dx, dy, preserve} in document order
            (x, y, dx, dy are lists of numbers, possibly empty).
    Returns (list of (d_path, style), missing_families).
    """
    if TTFont is None:
        return [], {"fontTools not installed"}
    index = index or get_index()
    cx = cy = 0.0
    glyphs = []          # (chunk_id, x, y, width, d, style)
    anchors = []         # text-anchor of each anchored chunk
    chunk_id = -1
    missing = set()

    # whitespace handling (xml:space="default")
    texts = []
    for c in chunks:
        t = c["text"] or ""
        if not c.get("preserve"):
            t = re.sub(r"[\n\r]", "", t).replace("\t", " ")
            t = re.sub(r" +", " ", t)
        texts.append(t)
    if texts and not chunks[0].get("preserve"):
        texts[0] = texts[0].lstrip()
        texts[-1] = texts[-1].rstrip()

    for c, text in zip(chunks, texts):
        st = c["style"]
        size = _font_size(st.get("font-size"))
        fams = [f for f in re.split(r",", st.get("font-family", "serif"))]
        entry, exact = index.find(fams, _weight(st.get("font-weight")),
                                  st.get("font-style", "normal").strip().lower() in ("italic", "oblique"))
        if entry is None:
            missing.add(st.get("font-family", "?"))
            continue
        if not exact:
            missing.add(st.get("font-family", "?").split(",")[0].strip().strip("'\""))
        font = index.font(entry, _weight(st.get("font-weight")))
        upm = font["head"].unitsPerEm
        cmap = font.getBestCmap() or {}
        gs = font.getGlyphSet()
        hmtx = font["hmtx"]
        scale = size / upm
        spacing = _font_size(st.get("letter-spacing"), 0.0) if st.get("letter-spacing", "normal") != "normal" else 0
        xs, ys, dxs, dys = c["x"], c["y"], c["dx"], c["dy"]
        anchor = st.get("text-anchor", "start").strip()
        for i, ch in enumerate(text):
            new_chunk = False
            if i < len(xs):
                cx = xs[i]
                new_chunk = True
            if i < len(ys):
                cy = ys[i]
                new_chunk = True
            if i < len(dxs):
                cx += dxs[i]
            if i < len(dys):
                cy += dys[i]
            if new_chunk or chunk_id < 0:
                chunk_id += 1
                anchors.append(anchor)
            gname = cmap.get(ord(ch)) or ".notdef"
            adv = hmtx[gname][0] * scale if gname in hmtx.metrics else size * 0.5
            if not ch.isspace() and gname in gs:
                pen = SVGPathPen(gs)
                gs[gname].draw(TransformPen(pen, (scale, 0, 0, -scale, cx, cy)))
                d = pen.getCommands()
                if d:
                    glyphs.append([chunk_id, cx, adv, d, st])
            cx += adv + spacing

    # text-anchor: shift each anchored chunk
    out = []
    if glyphs:
        ext = {}
        for cid, x, adv, d, st in glyphs:
            lo, hi = ext.get(cid, (x, x + adv))
            ext[cid] = (min(lo, x), max(hi, x + adv))
        for cid, x, adv, d, st in glyphs:
            a = anchors[cid] if cid < len(anchors) else "start"
            lo, hi = ext[cid]
            shift = -(hi - lo) / 2 if a == "middle" else (-(hi - lo) if a == "end" else 0.0)
            out.append((d, st, shift))
    return out, missing
