# BeamCraft - colour helpers
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import re

NAMED = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "lime": "#00ff00",
    "green": "#008000", "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff",
    "aqua": "#00ffff", "magenta": "#ff00ff", "fuchsia": "#ff00ff", "gray": "#808080",
    "grey": "#808080", "silver": "#c0c0c0", "maroon": "#800000", "olive": "#808000",
    "navy": "#000080", "purple": "#800080", "teal": "#008080", "orange": "#ffa500",
    "darkred": "#8b0000",
}


def parse_color(txt):
    """Returns '#rrggbb' or None (none / transparent / gradient / unknown)."""
    if txt is None:
        return None
    c = txt.strip().lower()
    if not c or c in ("none", "transparent") or c.startswith("url("):
        # url(#gradient) : treat gradients as black so fills are still engraved
        return "#000000" if c.startswith("url(") else None
    if c in NAMED:
        return NAMED[c]
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4):
            return "#" + "".join(ch * 2 for ch in h[:3])
        if len(h) in (6, 8):
            return "#" + h[:6]
        return None
    m = re.match(r"rgba?\(([^)]*)\)", c)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",") if p.strip()]
        if len(parts) < 3:
            return None
        vals = []
        for p in parts[:3]:
            try:
                v = float(p[:-1]) * 2.55 if p.endswith("%") else float(p)
            except ValueError:
                return None
            vals.append(int(round(max(0.0, min(255.0, v)))))
        if len(parts) >= 4:
            try:
                a = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
                if a <= 0:
                    return None
            except ValueError:
                pass
        return "#{:02x}{:02x}{:02x}".format(*vals)
    return None


def rgb(color):
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def classify(color):
    """
    'engrave' for (near) black, 'cut' for (near) red, 'white' for (near) white,
    'other' otherwise, None if no colour.

    A tolerance is used because design tools working in CMYK or with colour
    profiles rarely export exactly #000000 / #ff0000 (e.g. rgb(35,31,32)).
    """
    if not color:
        return None
    r, g, b = rgb(color)
    if max(r, g, b) <= 64:
        return "engrave"
    if r >= 190 and g <= 70 and b <= 70:
        return "cut"
    if min(r, g, b) >= 235:
        return "white"
    return "other"
