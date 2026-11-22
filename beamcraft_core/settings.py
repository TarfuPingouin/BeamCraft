# BeamCraft - settings (with persistence)
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Settings are made of global machine/geometry options and of one
LayerSettings per layer.  A layer is identified by a key:

    "stroke:#rrggbb"   outlines drawn with that stroke colour
    "fill:#rrggbb"     shapes filled with that colour
    "image"            bitmap images (raster engraving)

Layer settings are remembered between files: the same colour gets the same
power / speed / mode the next time.
"""

import json
import os
import sys
from dataclasses import dataclass, asdict, fields, field
from pathlib import Path

from .colors import classify

ORIGINS = ("bottom left", "top left", "bottom right", "top right", "center")
POINTER_MAX_PCT = 5.0     # the aiming dot is never fired above 5 % of $30
MODES = ("line", "fill", "line+fill")
IMAGE_MODES = ("threshold", "dither", "grayscale")


@dataclass
class LayerSettings:
    enabled: bool = True          # output this layer
    mode: str = "line"            # line / fill / line+fill  (image layers: ignored)
    power_pct: float = 30.0       # % of max power ($30)
    feed: float = 1000.0          # mm/min
    passes: int = 1
    # line options
    kerf: float = 0.0             # mm, >0 = path moved outside parts / inside holes
    tabs_count: int = 0           # tabs per closed contour (0 = no tabs)
    tab_width: float = 0.5        # mm
    tabs_min_length: float = 20.0 # mm, contours shorter than this get no tabs
    tabs_outer_only: bool = True  # no tabs on holes (the scrap can fall)
    # fill (hatching) options
    hatch_spacing: float = 0.2    # mm
    hatch_angle: float = 0.0      # degrees
    hatch_cross: bool = False     # second pass at angle + 90
    hatch_bidirectional: bool = True
    hatch_merge: bool = True      # union of overlapping shapes before hatching
    overscan: float = 2.0         # mm, fill and image: start/stop outside the area
    # image (raster) options
    image_mode: str = "dither"    # threshold / dither / grayscale
    min_power_pct: float = 0.0    # grayscale: power for white
    line_interval: float = 0.1    # mm between raster lines
    threshold: int = 128          # 0..255 (threshold mode)
    negative: bool = False
    brightness: float = 0.0       # -100..100
    contrast: float = 0.0         # -100..100


def layer_defaults(key):
    src, _, col = key.partition(":")
    if src == "image":
        return LayerSettings(mode="line", power_pct=60.0, feed=2000.0, overscan=3.0)
    cls = classify(col) if col else None
    if src == "fill":
        return LayerSettings(mode="fill", power_pct=30.0, feed=2000.0, enabled=(cls != "white"))
    if cls == "cut":
        return LayerSettings(mode="line", power_pct=100.0, feed=300.0)
    if cls == "engrave":
        return LayerSettings(mode="line", power_pct=30.0, feed=1000.0)
    # other stroke colours: present in the table but not output by default
    return LayerSettings(mode="line", power_pct=30.0, feed=1000.0, enabled=False)


def default_priority(key):
    """Default order: images, fills, engraving lines, cuts (always last)."""
    src, _, col = key.partition(":")
    if src == "image":
        return 0
    if src == "fill":
        return 1
    if classify(col) == "cut":
        return 3
    return 2


def layer_label(key):
    src, _, col = key.partition(":")
    if src == "image":
        return "Image"
    return f"{col.upper()} {'stroke' if src == 'stroke' else 'fill'}"


@dataclass
class Settings:
    # --- machine / Grbl ---
    max_power: float = 1000.0         # S value matching Grbl $30
    laser_mode: str = "M4"            # "M4" dynamic (recommended) or "M3" constant
    air_assist: bool = False          # M8 at start, M9 at end
    return_to_origin: bool = True     # G0 X0 Y0 at the end
    rapid_g1: bool = False            # travel with G1 S0 at travel_feed instead of G0
    travel_feed: float = 3000.0       # used for G1 travel and time estimate

    # --- machine dynamics (Grbl $110 $111 $120 $121 $11 $12): time estimate ---
    max_rate_x: float = 6000.0        # mm/min
    max_rate_y: float = 6000.0
    accel_x: float = 1000.0           # mm/s2
    accel_y: float = 1000.0
    junction_deviation: float = 0.01  # mm
    arc_tolerance: float = 0.002      # mm

    # --- work area ---
    bed_width: float = 400.0          # mm, size of the work area
    bed_height: float = 400.0
    start_from: str = "absolute"      # "absolute": G-code in work-area coordinates (job placed
                                      #   on the work area; zero = work-area origin)
                                      # "current": the job starts at the current laser position,
                                      #   at its `origin` anchor (G10 L20 P1 X0 Y0)
    origin: str = "bottom left"       # anchor of the job for start_from = "current"

    # --- aiming pointer (low power dot to check a position) ---
    pointer_power_pct: float = 1.0    # % of max power, capped at POINTER_MAX_PCT
    pointer_timeout: float = 20.0     # s, the pointer switches off automatically

    # --- rotary (Y axis drives a roller or a chuck) ---
    rotary_enabled: bool = False
    rotary_type: str = "roller"       # "roller" or "chuck"
    rotary_mm_per_rotation: float = 100.0   # Y travel (mm) for one turn of the roller / chuck
    rotary_roller_diameter: float = 20.0    # mm (roller type)
    rotary_object_diameter: float = 70.0    # mm (chuck type)

    # --- layers ---
    layers: dict = field(default_factory=dict)      # key -> LayerSettings
    layer_order: list = field(default_factory=list)  # keys, first = output first

    # --- geometry ---
    dpi: int = 300                    # used when the SVG has no physical size
    force_dpi: bool = False
    tolerance: float = 0.02           # mm, max deviation of fitted lines/arcs
    use_arcs: bool = True             # output G2/G3
    optimize_order: bool = True

    # ------------------------------------------------------------------
    def rotary_factor(self):
        """Y scale applied by the rotary: machine Y mm per mm on the object surface."""
        if not self.rotary_enabled:
            return 1.0
        d = self.rotary_roller_diameter if self.rotary_type == "roller" else self.rotary_object_diameter
        return self.rotary_mm_per_rotation / (3.141592653589793 * d)

    def layer(self, key):
        """Settings of a layer (created with defaults and remembered)."""
        L = self.layers.get(key)
        if L is None:
            L = layer_defaults(key)
            self.layers[key] = L
        return L

    def ordered(self, keys):
        """Sort layer keys: user order first, then default priority."""
        keys = list(dict.fromkeys(keys))
        known = [k for k in self.layer_order if k in keys]
        new = sorted((k for k in keys if k not in self.layer_order), key=default_priority)
        # insert new keys according to their default priority
        out = list(known)
        for k in new:
            p = default_priority(k)
            pos = len(out)
            for i, kk in enumerate(out):
                if default_priority(kk) > p:
                    pos = i
                    break
            out.insert(pos, k)
        return out

    def move_layer(self, key, delta, present):
        order = self.ordered(present)
        if key not in order:
            return
        i = order.index(key)
        j = max(0, min(len(order) - 1, i + delta))
        order.insert(j, order.pop(i))
        rest = [k for k in self.layer_order if k not in order]
        self.layer_order = order + rest

    # ------------------------------------------------------------------
    def to_dict(self):
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d):
        s = cls()
        d = dict(d or {})
        # --- migration from v0.2.0 (fixed engrave / cut / fill layers) ---
        legacy = {"engrave": "stroke:#000000", "cut": "stroke:#ff0000"}
        for old, key in legacy.items():
            if isinstance(d.get(old), dict):
                s.layers[key] = _layer_from_dict(d[old], layer_defaults(key))
        if isinstance(d.get("fill"), dict):
            s.layers["fill:#000000"] = _layer_from_dict(d["fill"], layer_defaults("fill:#000000"))
        names = {f.name for f in fields(cls)}
        for k, v in d.items():
            if k not in names:
                continue
            if k == "layers" and isinstance(v, dict):
                for key, ld in v.items():
                    if isinstance(ld, dict):
                        s.layers[key] = _layer_from_dict(ld, layer_defaults(key))
            elif k == "layer_order" and isinstance(v, list):
                s.layer_order = [str(x) for x in v]
            else:
                try:
                    cur = getattr(s, k)
                    setattr(s, k, type(cur)(v))
                except (TypeError, ValueError):
                    pass
        s.validate(clamp=True)
        return s

    def validate(self, clamp=False):
        """Raises ValueError with a readable message (or clamps to defaults)."""
        errors = []
        D = Settings()
        checks = [
            ("max_power", lambda v: v > 0, "Max. laser power must be > 0."),
            ("dpi", lambda v: v > 0, "DPI must be > 0."),
            ("tolerance", lambda v: 0 < v <= 1, "Curve tolerance must be between 0 and 1 mm."),
            ("travel_feed", lambda v: v > 0, "Travel speed must be > 0."),
            ("laser_mode", lambda v: v in ("M3", "M4"), "Laser mode must be M3 or M4."),
            ("origin", lambda v: v in ORIGINS, "Unknown origin."),
            ("start_from", lambda v: v in ("absolute", "current"), "Unknown start mode."),
            ("pointer_power_pct", lambda v: 0 < v <= POINTER_MAX_PCT,
             f"Pointer power must be between 0 and {POINTER_MAX_PCT} %."),
            ("pointer_timeout", lambda v: 1 <= v <= 600, "Pointer timeout must be between 1 and 600 s."),
            ("max_rate_x", lambda v: v > 0, "Max. rate X must be > 0."),
            ("max_rate_y", lambda v: v > 0, "Max. rate Y must be > 0."),
            ("accel_x", lambda v: v > 0, "Acceleration X must be > 0."),
            ("accel_y", lambda v: v > 0, "Acceleration Y must be > 0."),
            ("junction_deviation", lambda v: 0 < v < 5, "Junction deviation must be between 0 and 5 mm."),
            ("arc_tolerance", lambda v: 0 < v < 1, "Arc tolerance must be between 0 and 1 mm."),
            ("bed_width", lambda v: v > 0, "Work area width must be > 0."),
            ("bed_height", lambda v: v > 0, "Work area height must be > 0."),
            ("rotary_type", lambda v: v in ("roller", "chuck"), "Rotary type must be roller or chuck."),
            ("rotary_mm_per_rotation", lambda v: v > 0, "Rotary mm per rotation must be > 0."),
            ("rotary_roller_diameter", lambda v: v > 0, "Roller diameter must be > 0."),
            ("rotary_object_diameter", lambda v: v > 0, "Object diameter must be > 0."),
        ]
        for name, ok, msg in checks:
            if not ok(getattr(self, name)):
                if clamp:
                    setattr(self, name, getattr(D, name))
                else:
                    errors.append(msg)
        for key, L in self.layers.items():
            for e in validate_layer(L, clamp=clamp, default=layer_defaults(key)):
                errors.append(f"{layer_label(key)}: {e}")
        if errors and not clamp:
            raise ValueError("\n".join(errors))


LAYER_CHECKS = [
    ("mode", lambda v: v in MODES, "unknown mode."),
    ("power_pct", lambda v: 0 <= v <= 100, "power must be between 0 and 100 %."),
    ("min_power_pct", lambda v: 0 <= v <= 100, "min. power must be between 0 and 100 %."),
    ("feed", lambda v: v > 0, "speed must be > 0."),
    ("passes", lambda v: v >= 1, "passes must be >= 1."),
    ("kerf", lambda v: abs(v) <= 5, "kerf offset must be between -5 and 5 mm."),
    ("tabs_count", lambda v: v >= 0, "tabs must be >= 0."),
    ("tab_width", lambda v: v >= 0, "tab width must be >= 0."),
    ("tabs_min_length", lambda v: v >= 0, "min. contour length must be >= 0."),
    ("hatch_spacing", lambda v: v > 0, "line spacing must be > 0."),
    ("overscan", lambda v: 0 <= v <= 50, "overscan must be between 0 and 50 mm."),
    ("image_mode", lambda v: v in IMAGE_MODES, "unknown image mode."),
    ("line_interval", lambda v: 0.01 <= v <= 5, "line interval must be between 0.01 and 5 mm."),
    ("threshold", lambda v: 0 <= v <= 255, "threshold must be between 0 and 255."),
    ("brightness", lambda v: -100 <= v <= 100, "brightness must be between -100 and 100."),
    ("contrast", lambda v: -100 <= v <= 100, "contrast must be between -100 and 100."),
]


def validate_layer(L, clamp=False, default=None):
    errors = []
    default = default or LayerSettings()
    for name, ok, msg in LAYER_CHECKS:
        if not ok(getattr(L, name)):
            if clamp:
                setattr(L, name, getattr(default, name))
            else:
                errors.append(msg)
    return errors


def _layer_from_dict(d, base):
    for k, v in d.items():
        if hasattr(base, k):
            try:
                setattr(base, k, type(getattr(base, k))(v))
            except (TypeError, ValueError):
                pass
    return base


def config_path():
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "BeamCraft" / "settings.json"


def load_settings(path=None):
    p = Path(path) if path else config_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return Settings(), {}
    extra = data.pop("_ui", {}) if isinstance(data, dict) else {}
    return Settings.from_dict(data), extra


def save_settings(settings, extra=None, path=None):
    p = Path(path) if path else config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        d = settings.to_dict()
        if extra:
            d["_ui"] = extra
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
