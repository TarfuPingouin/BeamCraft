# BeamCraft - SVG → GCode
# Copyright (c) 2026  Tarfu Air
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.


import math
import os
import sys
from pathlib import Path as FilePath
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

import TKinterModernThemes as TKMT  # thèmes modernes

from svgpathtools import svg2paths2, Path, Line, QuadraticBezier, CubicBezier, Arc

# Images pour les boutons (icônes)
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


# ==========================
# Paramètres par défaut
# ==========================

DEFAULT_DPI = 300        # ppp / dpi par défaut

DEFAULT_FEED = 250        # mm/min (vitesse de coupe)
DEFAULT_TRAVEL_FEED = 500
DEFAULT_LASER_POWER = 1000  # S (0..$30)
DEFAULT_SAMPLE_PER_UNIT = 1.0  # interne, plus d’UI pour ça

# Pour séparer éventuellement preview / GCode
PREVIEW_SAMPLE_PER_UNIT = DEFAULT_SAMPLE_PER_UNIT
GCODE_SAMPLE_PER_UNIT = DEFAULT_SAMPLE_PER_UNIT

# Pen plotter (servo PWM via M3 Sxxx)
DEFAULT_PEN_UP = 90     # valeur S pour "stylo levé"
DEFAULT_PEN_DOWN = 60   # valeur S pour "stylo baissé"

APP_NAME = "BeamCraft"
APP_VERSION = "v0.1.0"


# ==========================
# Fonctions "métier"
# ==========================

def discretize_path(path, sample_per_unit, max_points=2000):
    """
    Converts a path (svgpathtools) into a list of points [(x, y), ...]
    sample_per_unit = number of segments per unit length of the path.
    max_points limits the number of points for the PREVIEW (smoothness).
    """
    L = path.length()
    if L == 0:
        return []

    n_samples = int(L * sample_per_unit) + 1
    n_samples = max(2, min(n_samples, max_points))

    points = []
    for i in range(n_samples):
        t = i / (n_samples - 1)
        pt = path.point(t)
        points.append((pt.real, pt.imag))
    return points


def compute_bounds(polylines):
    xs = []
    ys = []
    for poly in polylines:
        for x, y in poly:
            xs.append(x)
            ys.append(y)
    if not xs:
        return (0, 0, 1, 1)
    return (min(xs), min(ys), max(xs), max(ys))


def world_to_canvas(x, y, bounds, canvas_width, canvas_height,
                    padding=10, zoom=1.0, pan_x=0.0, pan_y=0.0):
    minx, miny, maxx, maxy = bounds
    w = maxx - minx
    h = maxy - miny
    if w == 0:
        w = 1
    if h == 0:
        h = 1

    base_scale = min((canvas_width - 2 * padding) / w,
                     (canvas_height - 2 * padding) / h)
    scale = base_scale * zoom

    X = padding + (x - minx) * scale + pan_x
    Y = padding + (y - miny) * scale + pan_y

    return X, Y


def transform_svg_point(x, y, bounds, rot_deg, flip_h, flip_v):
    minx, miny, maxx, maxy = bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0

    x0 = x - cx
    y0 = y - cy

    if flip_h:
        x0 = -x0
    if flip_v:
        y0 = -y0

    if rot_deg % 360 == 90:
        xr = y0
        yr = -x0
    elif rot_deg % 360 == 270:
        xr = -y0
        yr = x0
    elif rot_deg % 360 == 180:
        xr = -x0
        yr = -y0
    else:
        angle = math.radians(rot_deg)
        xr = x0 * math.cos(angle) - y0 * math.sin(angle)
        yr = x0 * math.sin(angle) + y0 * math.cos(angle)

    return xr + cx, yr + cy


def circle_center_from_3_points(p1, p2, p3, eps=1e-9):
    (x1, y1) = p1
    (x2, y2) = p2
    (x3, y3) = p3

    A = x1 * (y2 - y3) - y1 * (x2 - x3) + x2 * y3 - x3 * y2
    if abs(A) < eps:
        return None

    B = (x1**2 + y1**2) * (y3 - y2) + (x2**2 + y2**2) * (y1 - y3) + (x3**2 + y3**2) * (y2 - y1)
    C = (x1**2 + y1**2) * (x2 - x3) + (x2**2 + y2**2) * (x3 - x1) + (x3**2 + y3**2) * (x1 - x2)

    cx = -B / (2 * A)
    cy = -C / (2 * A)
    r = math.hypot(x1 - cx, y1 - cy)
    return cx, cy, r


def get_page_bounds(svg_attr, polylines):
    vb = svg_attr.get("viewBox") or svg_attr.get("viewbox")
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) >= 4:
            try:
                minx = float(parts[0])
                miny = float(parts[1])
                w = float(parts[2])
                h = float(parts[3])
                return (minx, miny, minx + w, miny + h)
            except ValueError:
                pass

    return compute_bounds(polylines)


def normalize_color(col):
    if not col:
        return None
    col = col.strip().lower()
    if col in ("none", "transparent"):
        return None

    if col == "black":
        return "#000000"
    if col == "red":
        return "#ff0000"

    if col.startswith("rgb"):
        try:
            inside = col[col.index("(")+1:col.index(")")]
            r_str, g_str, b_str = [x.strip() for x in inside.split(",")]
            r = max(0, min(255, int(r_str)))
            g = max(0, min(255, int(g_str)))
            b = max(0, min(255, int(b_str)))
            return "#{:02x}{:02x}{:02x}".format(r, g, b)
        except Exception:
            return None

    if col.startswith("#"):
        if len(col) == 4:  # #rgb
            r = col[1]
            g = col[2]
            b = col[3]
            return f"#{r}{r}{g}{g}{b}{b}"
        if len(col) == 7:
            return col
    return None


def parse_style(attr):
    """
    Returns (stroke_color, width_float, fill_color).

    Important rule:
    - fill:none   -> no fill (fill_color = None)
    - no fill  -> default fill is black (#000000)
    """
    stroke = attr.get("stroke")
    width = attr.get("stroke-width")
    fill = attr.get("fill")
    style = attr.get("style", "")

    if style:
        parts = style.split(";")
        for p in parts:
            if ":" not in p:
                continue
            k, v = p.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            if k == "stroke" and stroke is None:
                stroke = v
            elif k == "stroke-width" and width is None:
                width = v
            elif k == "fill" and fill is None:
                fill = v

    stroke_color = normalize_color(stroke) or "#000000"

    fill_color = normalize_color(fill)
    if fill_color is None and fill is None:
        fill_color = "#000000"

    if not width:
        width = "0.1"
    try:
        width_val = float(width)
    except ValueError:
        width_val = 0.1

    return stroke_color, width_val, fill_color


def classify_color(color):
    if not color:
        return "other"
    c = color.strip().lower()
    if c in ("#000000", "black", "rgb(0,0,0)", "rgb(0, 0, 0)"):
        return "engrave"
    if c in ("#ff0000", "red", "rgb(255,0,0)", "rgb(255, 0, 0)"):
        return "cut"
    return "other"


def hatch_polygon(poly_points, spacing, angle_deg=0.0):
    if len(poly_points) < 3 or spacing <= 0:
        return []

    if (abs(poly_points[0][0] - poly_points[-1][0]) < 1e-9 and
            abs(poly_points[0][1] - poly_points[-1][1]) < 1e-9):
        pts = poly_points[:-1]
    else:
        pts = poly_points[:]

    angle = math.radians(angle_deg)
    cos_a = math.cos(-angle)
    sin_a = math.sin(-angle)

    rot_pts = []
    for (x, y) in pts:
        xr = x * cos_a - y * sin_a
        yr = x * sin_a + y * cos_a
        rot_pts.append((xr, yr))

    xs = [p[0] for p in rot_pts]
    ys = [p[1] for p in rot_pts]
    miny = min(ys)
    maxy = max(ys)

    n_lines = int((maxy - miny) / spacing) + 2

    segments = []
    n = len(rot_pts)
    for i in range(n_lines):
        y = miny + i * spacing
        inter_x = []

        for j in range(n):
            x1, y1 = rot_pts[j]
            x2, y2 = rot_pts[(j + 1) % n]

            if abs(y2 - y1) < 1e-12:
                continue

            if (y >= min(y1, y2)) and (y < max(y1, y2)):
                t = (y - y1) / (y2 - y1)
                x = x1 + t * (x2 - x1)
                inter_x.append(x)

        if len(inter_x) < 2:
            continue

        inter_x.sort()
        for k in range(0, len(inter_x) - 1, 2):
            x_start = inter_x[k]
            x_end = inter_x[k + 1]
            p1r = (x_start, y)
            p2r = (x_end, y)

            cos_b = math.cos(angle)
            sin_b = math.sin(angle)
            x1o = p1r[0] * cos_b - p1r[1] * sin_b
            y1o = p1r[0] * sin_b + p1r[1] * cos_b
            x2o = p2r[0] * cos_b - p2r[1] * sin_b
            y2o = p2r[0] * sin_b + p2r[1] * cos_b

            segments.append(((x1o, y1o), (x2o, y2o)))

    return segments


def generate_gcode_from_paths(paths, path_styles,
                              mode, scale, feed, travel_feed,
                              laser_power_max, pen_up, pen_down,
                              offset_x, offset_y,
                              sample_per_unit,
                              bounds_svg,
                              rot_deg, flip_h, flip_v,
                              engrave_power=None, cut_power=None,
                              engrave_passes=1, cut_passes=1):
    if path_styles is None or len(path_styles) != len(paths):
        path_styles = [("#000000", 0.1, None)] * len(paths)

    lines = []
    lines.append("(************************************************)")
    lines.append("(*          BeamCraft Generated GCode           *)")
    lines.append("(************************************************)")
    lines.append("")
    lines.append("G21 ; units in mm")
    lines.append("G90 ; absolute coordinates (X,Y)")
    lines.append("G17 ; XY plan")
    lines.append("G91.1 ; incremental I,J arcs")
    lines.append(f"G0 F{travel_feed}")
    lines.append("")

    def process_path_laser(path, power):
        if len(path) == 0:
            return

        first_seg = path[0]
        sx_svg = first_seg.start.real
        sy_svg = first_seg.start.imag

        sx_t, sy_t = transform_svg_point(sx_svg, sy_svg, bounds_svg, rot_deg, flip_h, flip_v)
        sx = sx_t * scale + offset_x
        sy = sy_t * scale + offset_y

        lines.append("M5 ; laser OFF")
        lines.append(f"G0 X{sx:.3f} Y{sy:.3f} ; rapid movement at the beginning of the path")
        lines.append(f"M3 S{power:.1f} ; laser ON")
        lines.append(f"G1 F{feed}")

        current_x = sx
        current_y = sy

        for seg in path:
            start_svg = seg.start
            end_svg = seg.end

            sxt, syt = transform_svg_point(start_svg.real, start_svg.imag,
                                           bounds_svg, rot_deg, flip_h, flip_v)
            ex_t, ey_t = transform_svg_point(end_svg.real, end_svg.imag,
                                             bounds_svg, rot_deg, flip_h, flip_v)

            sx_mm = sxt * scale + offset_x
            sy_mm = syt * scale + offset_y
            ex_mm = ex_t * scale + offset_x
            ey_mm = ey_t * scale + offset_y

            if (abs(current_x - sx_mm) > 1e-6) or (abs(current_y - sy_mm) > 1e-6):
                lines.append("M5 ; laser OFF (path jumping)")
                lines.append(f"G0 X{sx_mm:.3f} Y{sy_mm:.3f}")
                lines.append(f"M3 S{power:.1f} ; laser ON")
                lines.append(f"G1 F{feed}")
                current_x, current_y = sx_mm, sy_mm

            if isinstance(seg, Line):
                lines.append(f"G1 X{ex_mm:.3f} Y{ey_mm:.3f}")
                current_x, current_y = ex_mm, ey_mm

            elif isinstance(seg, Arc):
                mid_svg = seg.point(0.5)
                mx_t, my_t = transform_svg_point(mid_svg.real, mid_svg.imag,
                                                 bounds_svg, rot_deg, flip_h, flip_v)
                mx_mm = mx_t * scale + offset_x
                my_mm = my_t * scale + offset_y

                center = circle_center_from_3_points(
                    (sx_mm, sy_mm),
                    (mx_mm, my_mm),
                    (ex_mm, ey_mm)
                )

                if center is None:
                    lines.append(f"G1 X{ex_mm:.3f} Y{ey_mm:.3f}")
                    current_x, current_y = ex_mm, ey_mm
                else:
                    cx, cy, r = center
                    I = cx - sx_mm
                    J = cy - sy_mm

                    vx1 = mx_mm - sx_mm
                    vy1 = my_mm - sy_mm
                    vx2 = ex_mm - sx_mm
                    vy2 = ey_mm - sy_mm
                    cross = vx1 * vy2 - vy1 * vx2

                    if abs(cross) < 1e-9:
                        lines.append(f"G1 X{ex_mm:.3f} Y{ey_mm:.3f}")
                    else:
                        if cross < 0:
                            lines.append(
                                f"G2 X{ex_mm:.3f} Y{ey_mm:.3f} I{I:.3f} J{J:.3f}"
                            )
                        else:
                            lines.append(
                                f"G3 X{ex_mm:.3f} Y{ey_mm:.3f} I{I:.3f} J{J:.3f}"
                            )

                    current_x, current_y = ex_mm, ey_mm

            else:
                try:
                    seg_len = seg.length(error=1e-4)
                except Exception:
                    seg_len = abs(end_svg - start_svg)

                n_steps = max(2, int(seg_len * sample_per_unit) + 1)

                for i in range(1, n_steps):
                    t = i / (n_steps - 1)
                    pt = seg.point(t)
                    px_t, py_t = transform_svg_point(pt.real, pt.imag,
                                                     bounds_svg, rot_deg, flip_h, flip_v)
                    px = px_t * scale + offset_x
                    py = py_t * scale + offset_y
                    lines.append(f"G1 X{px:.3f} Y{py:.3f}")
                    current_x, current_y = px, py

        lines.append("M5 ; laser OFF")

    def process_path_pen(path):
        if len(path) == 0:
            return

        first_seg = path[0]
        sx_svg = first_seg.start.real
        sy_svg = first_seg.start.imag

        sx_t, sy_t = transform_svg_point(sx_svg, sy_svg, bounds_svg, rot_deg, flip_h, flip_v)
        sx = sx_t * scale + offset_x
        sy = sy_t * scale + offset_y

        lines.append(f"M3 S{pen_up:.1f} ; pen UP")
        lines.append(f"G0 X{sx:.3f} Y{sy:.3f} ; rapid movement at the beginning of the path")
        lines.append(f"M3 S{pen_down:.1f} ; pen DOWN")
        lines.append(f"G1 F{feed}")

        current_x = sx
        current_y = sy

        for seg in path:
            start_svg = seg.start
            end_svg = seg.end

            sxt, syt = transform_svg_point(start_svg.real, start_svg.imag,
                                           bounds_svg, rot_deg, flip_h, flip_v)
            ex_t, ey_t = transform_svg_point(end_svg.real, end_svg.imag,
                                             bounds_svg, rot_deg, flip_h, flip_v)

            sx_mm = sxt * scale + offset_x
            sy_mm = syt * scale + offset_y
            ex_mm = ex_t * scale + offset_x
            ey_mm = ey_t * scale + offset_y

            if (abs(current_x - sx_mm) > 1e-6) or (abs(current_y - sy_mm) > 1e-6):
                lines.append(f"M3 S{pen_up:.1f} ; pen UP (path jumping)")
                lines.append(f"G0 X{sx_mm:.3f} Y{sy_mm:.3f}")
                lines.append(f"M3 S{pen_down:.1f} ; pen DOWN")
                lines.append(f"G1 F{feed}")
                current_x, current_y = sx_mm, sy_mm

            if isinstance(seg, Line) or isinstance(seg, Arc):
                lines.append(f"G1 X{ex_mm:.3f} Y{ey_mm:.3f}")
                current_x, current_y = ex_mm, ey_mm
            else:
                try:
                    seg_len = seg.length(error=1e-4)
                except Exception:
                    seg_len = abs(end_svg - start_svg)

                n_steps = max(2, int(seg_len * sample_per_unit) + 1)

                for i in range(1, n_steps):
                    t = i / (n_steps - 1)
                    pt = seg.point(t)
                    px_t, py_t = transform_svg_point(pt.real, pt.imag,
                                                     bounds_svg, rot_deg, flip_h, flip_v)
                    px = px_t * scale + offset_x
                    py = py_t * scale + offset_y
                    lines.append(f"G1 X{px:.3f} Y{py:.3f}")
                    current_x, current_y = px, py

        lines.append(f"M3 S{pen_up:.1f} ; stylo levé")

    if mode == "laser":
        for wanted_class, power, passes in [
            ("engrave", engrave_power or laser_power_max, max(1, int(engrave_passes))),
            ("cut",     cut_power or laser_power_max,      max(1, int(cut_passes))),
            ("other",   laser_power_max,                   1),
        ]:
            for idx, path in enumerate(paths):
                if path_styles is not None and idx < len(path_styles):
                    stroke_color = path_styles[idx][0]
                else:
                    stroke_color = None
                cls = classify_color(stroke_color)
                if cls != wanted_class:
                    continue
                for _ in range(passes):
                    process_path_laser(path, power)
    else:
        for path in paths:
            process_path_pen(path)

    lines.append("")
    lines.append("G0 X0 Y0 ; homing")
    if mode == "laser":
        lines.append("M5 ; laser OFF")
    else:
        lines.append(f"M3 S{pen_up:.1f} ; pen UP")
    lines.append("M2 ; End of GCode")

    return lines


# ==========================
# Interface graphique
# ==========================

class Svg2GcodeApp(TKMT.ThemedTKinterFrame):
    def __init__(self):
        super().__init__(
            APP_NAME,
            "sun-valley",
            "dark",
            False,
            False
        )

        self.root.geometry("1300x850")
        self.root.minsize(900, 600)
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.container = ttk.Frame(self.root, padding=10)
        self.container.grid(row=0, column=0, sticky="nsew")
        self.container.rowconfigure(1, weight=1)
        self.container.columnconfigure(0, weight=1)
        # Icône de la fenêtre (compatible PyInstaller)
        try:
            if getattr(sys, "frozen", False):
                base_dir = sys._MEIPASS
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))

            icon_path = os.path.join(base_dir, "icone.ico")
            self.root.iconbitmap(icon_path)
        except Exception as e:
            print("Impossible de charger l'icône :", e)


        # Données géométrie SVG
        self.paths_raw = []
        self.polylines_svg = []
        # (stroke_color, width, fill_color)
        self.path_styles = []
        self.page_bounds_svg = None

        # Données de preview GCode (incluant hachures)
        self.gcode_preview_polylines = []
        self.gcode_preview_colors = []

        # Origine de job (côté code : bot left)
        self.origin_mode = "bottom left"

        # Zoom / pan
        self.zoom_svg = 1.0
        self.pan_svg_x = 0.0
        self.pan_svg_y = 0.0
        self.zoom_gc = 1.0
        self.pan_gc_x = 0.0
        self.pan_gc_y = 0.0

        self._drag_svg_last = None
        self._drag_gc_last = None

        # Transformations
        self.rotation_deg = 0
        self.flip_h = False
        self.flip_v = False

        # Variables de settings
        self.mode_var = tk.StringVar(value="laser")

        self.dpi_var = tk.StringVar(value=str(int(DEFAULT_DPI)))
        self.feed_var = tk.StringVar(value=str(DEFAULT_FEED))
        self.travel_var = tk.StringVar(value=str(DEFAULT_TRAVEL_FEED))
        self.laser_var = tk.StringVar(value=str(DEFAULT_LASER_POWER))
        self.pen_up_var = tk.StringVar(value=str(DEFAULT_PEN_UP))
        self.pen_down_var = tk.StringVar(value=str(DEFAULT_PEN_DOWN))

        self.engrave_pct_var = tk.StringVar(value="30")
        self.cut_pct_var = tk.StringVar(value="100")
        self.engrave_passes_var = tk.StringVar(value="1")
        self.cut_passes_var = tk.StringVar(value="1")

        self.hatch_enabled_var = tk.BooleanVar(value=False)
        self.hatch_spacing_var = tk.StringVar(value="0.2")
        self.hatch_direction_var = tk.StringVar(value="Horizontal")
        
        # Nouvelle option : insérer G10 L20 P1 X0 / Y0 en début de GCode
        self.g10_origin_var = tk.BooleanVar(value=True)

        # Fenêtre de settings (Toplevel)
        self.settings_window = None

        # Références images Tk
        self.img_flip_h = None
        self.img_flip_v = None
        self.img_rot_left = None
        self.img_rot_right = None
        self.img_logo = None

        self._build_ui()

    def _read_float(self, var, label):
        txt = var.get().strip().replace(",", ".")
        try:
            return float(txt)
        except ValueError:
            raise ValueError(f"Invalid value for « {label} » : « {txt} »")

    def _read_int(self, var, label):
        txt = var.get().strip()
        try:
            val = int(txt)
        except ValueError:
            raise ValueError(f"Invalid integer value for « {label} » : « {txt} »")
        return val

    def _load_icons(self):
        """Load the flip/rotate/logo icons if Pillow is available."""
        if Image is None or ImageTk is None:
            return

        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))

            flip_h_path = os.path.join(base_dir, "flipH.png")
            flip_v_path = os.path.join(base_dir, "flipV.png")
            rot_cw_path = os.path.join(base_dir, "rotateCW.png")
            rot_ccw_path = os.path.join(base_dir, "rotateCCW.png")
            logo_path = os.path.join(base_dir, "logo_wht.png")

            flip_h_img = Image.open(flip_h_path).resize((15, 15), Image.LANCZOS)
            flip_v_img = Image.open(flip_v_path).resize((15, 15), Image.LANCZOS)
            rot_cw_img = Image.open(rot_cw_path).resize((15, 15), Image.LANCZOS)
            rot_ccw_img = Image.open(rot_ccw_path).resize((15, 15), Image.LANCZOS)
            logo_img = Image.open(logo_path).resize((254, 87), Image.LANCZOS)

            self.img_flip_h = ImageTk.PhotoImage(flip_h_img)
            self.img_flip_v = ImageTk.PhotoImage(flip_v_img)
            self.img_rot_right = ImageTk.PhotoImage(rot_cw_img)
            self.img_rot_left = ImageTk.PhotoImage(rot_ccw_img)
            self.img_logo = ImageTk.PhotoImage(logo_img)

        except Exception:
            # en cas d’erreur (fichier manquant, etc.) : pas d’icônes
            pass

    def _build_ui(self):
        # Charger les icônes
        self._load_icons()

        # ======================
        # Barre du haut : onglets + logo à droite
        # ======================
        top_bar = ttk.Frame(self.container)
        top_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        
        # Une seule colonne qui s'étire : la barre fait bien toute la largeur
        top_bar.columnconfigure(0, weight=1)
        
        # Le notebook occupe toute la largeur
        self.notebook = ttk.Notebook(top_bar)
        self.notebook.grid(row=0, column=0, sticky="ew")
        
        # Logo BeamCraft "posé" par-dessus la barre, collé à droite
        if self.img_logo is not None:
            self.logo_label = ttk.Label(top_bar, image=self.img_logo)
            self.logo_label.place(relx=1, rely=0.60, x=-16, anchor="e")
            self.logo_label.configure(padding=(8, 4))   # ← marge interne
        else:
            self.logo_label = ttk.Label(top_bar, text=APP_NAME)
            self.logo_label.place(relx=1.0, rely=0.60, x=-16, anchor="e")
            self.logo_label.configure(padding=(8, 4))



        # Onglets
        self.tab_files = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)
        self.tab_about = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_files, text="Files")
        self.notebook.add(self.tab_settings, text="Settings")
        self.notebook.add(self.tab_about, text="About")

        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        # ======================
        # Onglet Files : boutons + transformations
        # ======================
        files_frame = self.tab_files
        files_frame = self.tab_files
        files_frame.columnconfigure(10, weight=1)
        
        btn_load = ttk.Button(files_frame, text="Load SVG", command=self.load_svg)
        btn_load.grid(row=0, column=0, padx=4, pady=4)
        
        btn_gcode = ttk.Button(files_frame, text="Generate GCode", command=self.export_gcode)
        btn_gcode.grid(row=0, column=1, padx=4, pady=4)
        
        # Label "Transformations" juste à droite des deux boutons
        ttk.Label(files_frame, text="Transformations :").grid(row=0, column=2, sticky="w", padx=8, pady=4)
        
        # Rotation 90° gauche (rotateCCW.png)
        if self.img_rot_left is not None:
            btn_rot_left = ttk.Button(files_frame, image=self.img_rot_left, command=self.rotate_left)
        else:
            btn_rot_left = ttk.Button(files_frame, text="90° gauche", command=self.rotate_left)
        btn_rot_left.grid(row=0, column=3, padx=4, pady=4)
        
        # Rotation 90° droite (rotateCW.png)
        if self.img_rot_right is not None:
            btn_rot_right = ttk.Button(files_frame, image=self.img_rot_right, command=self.rotate_right)
        else:
            btn_rot_right = ttk.Button(files_frame, text="90° droite", command=self.rotate_right)
        btn_rot_right.grid(row=0, column=4, padx=4, pady=4)
        
        # Flip horizontal (flipH.png)
        if self.img_flip_h is not None:
            btn_flip_h = ttk.Button(files_frame, image=self.img_flip_h, command=self.flip_horizontal)
        else:
            btn_flip_h = ttk.Button(files_frame, text="Flip H", command=self.flip_horizontal)
        btn_flip_h.grid(row=0, column=5, padx=4, pady=4)
        
        # Flip vertical (flipV.png)
        if self.img_flip_v is not None:
            btn_flip_v = ttk.Button(files_frame, image=self.img_flip_v, command=self.flip_vertical)
        else:
            btn_flip_v = ttk.Button(files_frame, text="Flip V", command=self.flip_vertical)
        btn_flip_v.grid(row=0, column=6, padx=4, pady=4)


        # ======================
        # Onglet Settings : ouvre une nouvelle fenêtre
        # ======================
        ttk.Label(self.tab_settings, text="Open settings window").pack(pady=20)
        ttk.Button(self.tab_settings, text="Open settings",
                   command=self.open_settings_window).pack(pady=10)

        # ======================
        # Onglet About
        # ======================
        ttk.Label(self.tab_about, text=f"{APP_NAME} {APP_VERSION}").pack(pady=20)
        ttk.Button(self.tab_about, text="About BeamCraft",
                   command=self.open_about_window).pack(pady=10)

        # ======================
        # Zone de preview
        # ======================
        canvas_frame = ttk.Frame(self.container)
        canvas_frame.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.container.rowconfigure(1, weight=1)
        self.container.columnconfigure(0, weight=1)

        self.canvas_svg = tk.Canvas(canvas_frame, bg="white", highlightthickness=1, bd=0)
        self.canvas_gcode = tk.Canvas(canvas_frame, bg="white", highlightthickness=1, bd=0)

        self.canvas_svg.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4), pady=4)
        self.canvas_gcode.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)

        self.canvas_svg.bind("<MouseWheel>", self.on_mousewheel_svg)
        self.canvas_gcode.bind("<MouseWheel>", self.on_mousewheel_gc)
        self.canvas_svg.bind("<Button-4>", self.on_mousewheel_svg)
        self.canvas_svg.bind("<Button-5>", self.on_mousewheel_svg)
        self.canvas_gcode.bind("<Button-4>", self.on_mousewheel_gc)
        self.canvas_gcode.bind("<Button-5>", self.on_mousewheel_gc)

        self.canvas_svg.bind("<ButtonPress-2>", self.on_mid_press_svg)
        self.canvas_svg.bind("<ButtonRelease-2>", self.on_mid_release_svg)
        self.canvas_svg.bind("<B2-Motion>", self.on_mid_drag_svg)

        self.canvas_gcode.bind("<ButtonPress-2>", self.on_mid_press_gc)
        self.canvas_gcode.bind("<ButtonRelease-2>", self.on_mid_release_gc)
        self.canvas_gcode.bind("<B2-Motion>", self.on_mid_drag_gc)

        # ======================
        # Barre de statut : nom fichier + version
        # ======================
        label_frame = ttk.Frame(self.container)
        label_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self.svg_filename_var = tk.StringVar(value="No SVG file")
        ttk.Label(label_frame, textvariable=self.svg_filename_var).pack(side=tk.LEFT, padx=5)

        ttk.Label(label_frame, text=f"{APP_NAME} {APP_VERSION}").pack(side=tk.RIGHT, padx=5)

        self.canvas_svg.bind("<Configure>", lambda e: self.redraw_previews())
        self.canvas_gcode.bind("<Configure>", lambda e: self.redraw_previews())

    # ----------------------
    # Callbacks onglets
    # ----------------------
    def on_tab_changed(self, event):
        tab_text = event.widget.tab(event.widget.select(), "text")
        if tab_text == "Settings":
            self.open_settings_window()
            self.notebook.select(self.tab_files)
        elif tab_text == "About":
            self.open_about_window()
            self.notebook.select(self.tab_files)

    # ----------------------
    # Fenêtre Settings
    # ----------------------
    def open_settings_window(self):
        if self.settings_window is not None and tk.Toplevel.winfo_exists(self.settings_window):
            self.settings_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.grab_set()
        self.settings_window = win

        frm = ttk.Frame(win, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)

        row = 0

        # Mode : Laser / Plotter
        ttk.Label(frm, text="Mode :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        mode_frame = ttk.Frame(frm)
        mode_frame.grid(row=row, column=1, sticky="w", padx=4, pady=2)
        ttk.Radiobutton(mode_frame, text="Laser", variable=self.mode_var, value="laser").pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(mode_frame, text="Plotter", variable=self.mode_var, value="pen").pack(side=tk.LEFT, padx=2)

        row += 1
        ttk.Label(frm, text="DPI (ppp) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.dpi_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Cutting speed (mm/min) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.feed_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Travelling speed (mm/min) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.travel_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Max. laser power (S) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.laser_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Pen UP (S) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.pen_up_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Pen DOWN (S) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.pen_down_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, columnspan=2,
                                                    sticky="ew", pady=(6, 6))

        row += 1
        ttk.Label(frm, text="Engraving (black #000000 (%)) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.engrave_pct_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Cutting (red #FF0000 (%)) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.cut_pct_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Engraving passes :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.engrave_passes_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Cutting passes :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.cut_passes_var).grid(row=row, column=1, padx=4, pady=2)
        
        # Option G10 : remise à zéro de l'origine travail
        row += 1
        ttk.Checkbutton(
            frm,
            text="Set machine 0;0 at current position",
            variable=self.g10_origin_var
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        
        row += 1
        ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, columnspan=2,
                                                    sticky="ew", pady=(6, 6))

        row += 1
        ttk.Checkbutton(
            frm,
            text="Hatching solid areas",
            variable=self.hatch_enabled_var
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Hatching spacing (mm) :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, width=10, textvariable=self.hatch_spacing_var).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Label(frm, text="Hatching direction :").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(
            frm,
            textvariable=self.hatch_direction_var,
            state="readonly",
            values=["Horizontal", "Vertical", "Crossed"]
        ).grid(row=row, column=1, padx=4, pady=2)

        row += 1
        ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, columnspan=2,
                                                    sticky="ew", pady=(6, 6))

        row += 1
        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(4, 2))

        def apply_and_close():
            # Validation de base
            try:
                dpi_txt = self.dpi_var.get().strip()
                dpi_val = int(dpi_txt)
                if dpi_val <= 0:
                    raise ValueError("DPI must be a strictly positive integer.")

                self._read_float(self.feed_var, "Cutting speed (mm/min)")
                self._read_float(self.travel_var, "Travelling speed (mm/min)")
                self._read_float(self.laser_var, "Max laser power (S)")
                self._read_float(self.pen_up_var, "Pen UP (S)")
                self._read_float(self.pen_down_var, "Pen DOWN (S)")
                self._read_float(self.engrave_pct_var, "Engraving black (%))")
                self._read_float(self.cut_pct_var, "Engraving red (%)")
                self._read_int(self.engrave_passes_var, "Engraving passes")
                self._read_int(self.cut_passes_var, "Cutting passes")
                self._read_float(self.hatch_spacing_var, "Hatching spacing (mm)")
            except ValueError as e:
                messagebox.showerror("Erreur", str(e), parent=win)
                return

            # Rebuild preview avec les nouveaux réglages
            self.build_gcode_preview_data()
            self.redraw_previews()

            win.destroy()
            self.settings_window = None

        ttk.Button(btn_frame, text="Apply", command=apply_and_close).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_frame, text="Abort",
                   command=lambda: (win.destroy(), setattr(self, "settings_window", None))).pack(side=tk.RIGHT, padx=4)

    # ----------------------
    # Fenêtre About
    # ----------------------
    def open_about_window(self):
        win = tk.Toplevel(self.root)
        win.title("About")
        win.transient(self.root)
        win.grab_set()

        frm = ttk.Frame(win, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)

        ttk.Label(frm, text=f"{APP_NAME} {APP_VERSION}", font=("TkDefaultFont", 12, "bold")).pack(pady=4)
        ttk.Label(frm, text="CNC laser and plotter SVG to GCode generator.").pack(pady=4)
        ttk.Button(frm, text="Close", command=win.destroy).pack(pady=8)

    # ----------------------
    # Rotation & flips
    # ----------------------
    def rotate_left(self):
        self.rotation_deg = (self.rotation_deg + 90) % 360
        self.redraw_previews()

    def rotate_right(self):
        self.rotation_deg = (self.rotation_deg - 90) % 360
        self.redraw_previews()

    def flip_horizontal(self):
        self.flip_h = not self.flip_h
        self.redraw_previews()

    def flip_vertical(self):
        self.flip_v = not self.flip_v
        self.redraw_previews()

    # ----------------------
    # Zoom / pan
    # ----------------------
    def on_mousewheel_svg(self, event):
        if hasattr(event, "delta") and event.delta != 0:
            direction = 1 if event.delta > 0 else -1
        else:
            if getattr(event, "num", None) == 4:
                direction = 1
            elif getattr(event, "num", None) == 5:
                direction = -1
            else:
                direction = 0

        if direction == 0:
            return

        factor = 1.1 if direction > 0 else 1 / 1.1
        self.zoom_svg *= factor
        self.zoom_svg = max(0.1, min(self.zoom_svg, 20.0))
        self.redraw_previews()

    def on_mousewheel_gc(self, event):
        if hasattr(event, "delta") and event.delta != 0:
            direction = 1 if event.delta > 0 else -1
        else:
            if getattr(event, "num", None) == 4:
                direction = 1
            elif getattr(event, "num", None) == 5:
                direction = -1
            else:
                direction = 0

        if direction == 0:
            return

        factor = 1.1 if direction > 0 else 1 / 1.1
        self.zoom_gc *= factor
        self.zoom_gc = max(0.1, min(self.zoom_gc, 20.0))
        self.redraw_previews()

    def on_mid_press_svg(self, event):
        self._drag_svg_last = (event.x, event.y)

    def on_mid_release_svg(self, event):
        self._drag_svg_last = None

    def on_mid_drag_svg(self, event):
        if self._drag_svg_last is None:
            return
        dx = event.x - self._drag_svg_last[0]
        dy = event.y - self._drag_svg_last[1]
        self.pan_svg_x += dx
        self.pan_svg_y += dy
        self._drag_svg_last = (event.x, event.y)
        self.canvas_svg.move("all", dx, dy)

    def on_mid_press_gc(self, event):
        self._drag_gc_last = (event.x, event.y)

    def on_mid_release_gc(self, event):
        self._drag_gc_last = None

    def on_mid_drag_gc(self, event):
        if self._drag_gc_last is None:
            return
        dx = event.x - self._drag_gc_last[0]
        dy = event.y - self._drag_gc_last[1]
        self.pan_gc_x += dx
        self.pan_gc_y += dy
        self._drag_gc_last = (event.x, event.y)
        self.canvas_gcode.move("all", dx, dy)

    # ----------------------
    # Chargement SVG
    # ----------------------
    def load_svg(self):
        filepath = filedialog.askopenfilename(
            parent=self.root,
            title="Choose an SVG file",
            filetypes=[("SVG Files", "*.svg")]
        )
        if not filepath:
            return

        try:
            paths, attributes, svg_attr = svg2paths2(filepath)
        except Exception as e:
            messagebox.showerror("Error", f"Impossible to read SVG file:\n{e}")
            return

        self.svg_filename_var.set(os.path.basename(filepath))

        polylines = []
        path_styles = []
        for path, attr in zip(paths, attributes):
            pts = discretize_path(path, PREVIEW_SAMPLE_PER_UNIT)
            if len(pts) >= 2:
                polylines.append(pts)
            stroke_color, width, fill_color = parse_style(attr)
            path_styles.append((stroke_color, width, fill_color))

        if not polylines:
            messagebox.showwarning("Warning", "No usable path found in the SVG.")

        self.paths_raw = paths
        self.polylines_svg = polylines
        self.path_styles = path_styles

        self.page_bounds_svg = get_page_bounds(svg_attr, polylines)

        self.zoom_svg = 1.0
        self.pan_svg_x = 0.0
        self.pan_svg_y = 0.0
        self.zoom_gc = 1.0
        self.pan_gc_x = 0.0
        self.pan_gc_y = 0.0
        self.rotation_deg = 0
        self.flip_h = False
        self.flip_v = False

        self.build_gcode_preview_data()
        self.redraw_previews()

    # ----------------------
    # Construction des données de preview GCode
    # ----------------------
    def build_gcode_preview_data(self):
        """Builds polylines + colors for GCode preview (including hatching)."""
        self.gcode_preview_polylines = []
        self.gcode_preview_colors = []

        if not self.paths_raw:
            return

        mode = self.mode_var.get()

        # 1) Chemins de base (contours)
        for path, style in zip(self.paths_raw, self.path_styles):
            pts = discretize_path(path, PREVIEW_SAMPLE_PER_UNIT)
            if len(pts) < 2:
                continue
            self.gcode_preview_polylines.append(pts)
            stroke_color, width, fill_color = style
            cls = classify_color(stroke_color)
            if cls == "engrave":
                color = "#000000"
            elif cls == "cut":
                color = "#ff0000"
            else:
                color = stroke_color or "#0080ff"
            self.gcode_preview_colors.append(color)

        # 2) Hachures (si activé))
        if self.hatch_enabled_var.get():
            try:
                dpi_txt = self.dpi_var.get().strip()
                dpi = int(dpi_txt)
                if dpi <= 0:
                    return
                scale = 25.4 / float(dpi)
                hatch_spacing_mm = self._read_float(self.hatch_spacing_var, "Hatching spacing (mm)")
                if hatch_spacing_mm <= 0:
                    return
            except Exception:
                return

            spacing_svg = hatch_spacing_mm / scale

            hatch_dir = self.hatch_direction_var.get()
            if hatch_dir == "Horizontal":
                angles = [0.0]
            elif hatch_dir == "Vertical":
                angles = [90.0]
            else:
                angles = [0.0, 90.0]

            for path, style in zip(self.paths_raw, self.path_styles):
                stroke_color, width, fill_color = style
                if fill_color is None:
                    continue

                pts = discretize_path(path, PREVIEW_SAMPLE_PER_UNIT, max_points=4000)
                if len(pts) < 3:
                    continue
                poly = pts[:]
                if (abs(poly[0][0] - poly[-1][0]) > 1e-9) or (abs(poly[0][1] - poly[-1][1]) > 1e-9):
                    poly.append(poly[0])

                for ang in angles:
                    segments = hatch_polygon(poly, spacing_svg, angle_deg=ang)
                    for (p1, p2) in segments:
                        self.gcode_preview_polylines.append([p1, p2])
                        # hachures = gravure -> noir
                        self.gcode_preview_colors.append("#000000")

    # ----------------------
    # Redessin previews
    # ----------------------
    def redraw_previews(self):
        self.canvas_svg.delete("all")
        self.canvas_gcode.delete("all")

        if not self.polylines_svg:
            return

        page_bounds = self.page_bounds_svg or compute_bounds(self.polylines_svg)

        rot = self.rotation_deg
        flip_h = self.flip_h
        flip_v = self.flip_v

        minx_p, miny_p, maxx_p, maxy_p = page_bounds
        page_corners = [
            (minx_p, miny_p),
            (maxx_p, miny_p),
            (minx_p, maxy_p),
            (maxx_p, maxy_p),
        ]
        page_corners_tr = [
            transform_svg_point(x, y, page_bounds, rot, flip_h, flip_v)
            for (x, y) in page_corners
        ]
        xs = [p[0] for p in page_corners_tr]
        ys = [p[1] for p in page_corners_tr]
        page_bounds_tr = (min(xs), min(ys), max(xs), max(ys))

        polylines_transformed = []
        for poly in self.polylines_svg:
            new_poly = []
            for (x, y) in poly:
                xt, yt = transform_svg_point(x, y, page_bounds, rot, flip_h, flip_v)
                new_poly.append((xt, yt))
            polylines_transformed.append(new_poly)

        w_svg = self.canvas_svg.winfo_width()
        h_svg = self.canvas_svg.winfo_height()
        w_gc = self.canvas_gcode.winfo_width()
        h_gc = self.canvas_gcode.winfo_height()

        if w_svg <= 1 or h_svg <= 1 or w_gc <= 1 or h_gc <= 1:
            return

        # Cadre de page dans chaque preview
        x0_svg, y0_svg = world_to_canvas(minx_p, miny_p, page_bounds_tr,
                                         w_svg, h_svg,
                                         zoom=self.zoom_svg,
                                         pan_x=self.pan_svg_x,
                                         pan_y=self.pan_svg_y)
        x1_svg, y1_svg = world_to_canvas(maxx_p, maxy_p, page_bounds_tr,
                                         w_svg, h_svg,
                                         zoom=self.zoom_svg,
                                         pan_x=self.pan_svg_x,
                                         pan_y=self.pan_svg_y)
        self.canvas_svg.create_rectangle(x0_svg, y0_svg, x1_svg, y1_svg,
                                         outline="#aaaaaa", dash=(3, 3))

        x0_gc, y0_gc = world_to_canvas(minx_p, miny_p, page_bounds_tr,
                                       w_gc, h_gc,
                                       zoom=self.zoom_gc,
                                       pan_x=self.pan_gc_x,
                                       pan_y=self.pan_gc_y)
        x1_gc, y1_gc = world_to_canvas(maxx_p, maxy_p, page_bounds_tr,
                                       w_gc, h_gc,
                                       zoom=self.zoom_gc,
                                       pan_x=self.pan_gc_x,
                                       pan_y=self.pan_gc_y)
        self.canvas_gcode.create_rectangle(x0_gc, y0_gc, x1_gc, y1_gc,
                                           outline="#aaaaaa", dash=(3, 3))

        # --- Preview SVG : FILL + STROKE ---
        for poly, style in zip(polylines_transformed, self.path_styles):
            if len(poly) < 2:
                continue

            stroke_color, width, fill_color = style
            stroke = stroke_color or "#111111"

            # FILL (zones pleines)
            if fill_color is not None:
                coords = []
                for (x, y) in poly:
                    X, Y = world_to_canvas(x, y, page_bounds_tr,
                                           w_svg, h_svg,
                                           zoom=self.zoom_svg,
                                           pan_x=self.pan_svg_x,
                                           pan_y=self.pan_svg_y)
                    coords.extend((X, Y))
                if len(coords) >= 6:
                    self.canvas_svg.create_polygon(
                        coords,
                        fill=fill_color,
                        outline=""
                    )

            # STROKE par-dessus
            last = None
            for (x, y) in poly:
                X, Y = world_to_canvas(x, y, page_bounds_tr,
                                       w_svg, h_svg,
                                       zoom=self.zoom_svg,
                                       pan_x=self.pan_svg_x,
                                       pan_y=self.pan_svg_y)
                if last is not None:
                    self.canvas_svg.create_line(last[0], last[1], X, Y, fill=stroke)
                last = (X, Y)

        # --- Preview GCode : couleurs + hachures + déplacements "en l'air" ---
        last_end = None
        for poly, color in zip(self.gcode_preview_polylines, self.gcode_preview_colors):
            if len(poly) < 2:
                continue

            # Déplacement "en l'air" (outil OFF) entre la fin du chemin précédent
            # et le début de ce chemin : trait gris pointillé
            if last_end is not None:
                lx, ly = last_end
                sx, sy = poly[0]

                lxt, lyt = transform_svg_point(lx, ly, page_bounds, rot, flip_h, flip_v)
                sxt, syt = transform_svg_point(sx, sy, page_bounds, rot, flip_h, flip_v)

                LX, LY = world_to_canvas(lxt, lyt, page_bounds_tr,
                                         w_gc, h_gc,
                                         zoom=self.zoom_gc,
                                         pan_x=self.pan_gc_x,
                                         pan_y=self.pan_gc_y)
                SX, SY = world_to_canvas(sxt, syt, page_bounds_tr,
                                         w_gc, h_gc,
                                         zoom=self.zoom_gc,
                                         pan_x=self.pan_gc_x,
                                         pan_y=self.pan_gc_y)

                self.canvas_gcode.create_line(
                    LX, LY, SX, SY,
                    fill="#bbbbbb",
                    dash=(4, 4)
                )

            # Chemin "outil ON"
            last = None
            for (x, y) in poly:
                xt, yt = transform_svg_point(x, y, page_bounds, rot, flip_h, flip_v)
                X, Y = world_to_canvas(xt, yt, page_bounds_tr,
                                       w_gc, h_gc,
                                       zoom=self.zoom_gc,
                                       pan_x=self.pan_gc_x,
                                       pan_y=self.pan_gc_y)
                if last is not None:
                    self.canvas_gcode.create_line(last[0], last[1], X, Y, fill=color)
                last = (X, Y)

            last_end = poly[-1]

    # ----------------------
    # Export GCode
    # ----------------------
    def export_gcode(self):
        if not self.paths_raw:
            messagebox.showwarning("Warning", "No SVG loaded.")
            return

        try:
            dpi_txt = self.dpi_var.get().strip()
            dpi = int(dpi_txt)
            if dpi <= 0:
                raise ValueError("DPI must be a strictly positive integer.")
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        scale = 25.4 / float(dpi)

        try:
            feed = self._read_float(self.feed_var, "Cutting speed (mm/min)")
            travel_feed = self._read_float(self.travel_var, "Travelling speed (mm/min)")
            laser_power = self._read_float(self.laser_var, "Max. laser power (S)")
            pen_up = self._read_float(self.pen_up_var, "Pen UP (S)")
            pen_down = self._read_float(self.pen_down_var, "Pen DOWN (S)")
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        mode = self.mode_var.get()

        engrave_power = None
        cut_power = None
        engrave_passes = 1
        cut_passes = 1

        if mode == "laser":
            try:
                engrave_pct = self._read_float(self.engrave_pct_var, "Engraving black (%)")
                cut_pct = self._read_float(self.cut_pct_var, "Cutting red (%)")
                engrave_passes = max(1, self._read_int(self.engrave_passes_var, "Engraving passes"))
                cut_passes = max(1, self._read_int(self.cut_passes_var, "Cutting passes"))
            except ValueError as e:
                messagebox.showerror("Erreur", str(e))
                return

            engrave_pct = max(0.0, min(100.0, engrave_pct))
            cut_pct = max(0.0, min(100.0, cut_pct))

            engrave_power = laser_power * engrave_pct / 100.0
            cut_power = laser_power * cut_pct / 100.0

        page_bounds = self.page_bounds_svg or compute_bounds(self.polylines_svg)

        rot = self.rotation_deg
        flip_h = self.flip_h
        flip_v = self.flip_v

        minx_p, miny_p, maxx_p, maxy_p = page_bounds
        page_corners = [
            (minx_p, miny_p),
            (maxx_p, miny_p),
            (minx_p, maxy_p),
            (maxx_p, maxy_p),
        ]
        page_corners_tr = [
            transform_svg_point(x, y, page_bounds, rot, flip_h, flip_v)
            for (x, y) in page_corners
        ]
        xs = [p[0] for p in page_corners_tr]
        ys = [p[1] for p in page_corners_tr]
        minx_tr, miny_tr, maxx_tr, maxy_tr = min(xs), min(ys), max(xs), max(ys)

        origin_mode = self.origin_mode

        # Nous sommes maintenant dans un repère "Y vers le haut" côté GCode.
        # - bottom = miny_tr
        # - top    = maxy_tr
        if origin_mode == "bottom left":
            ref_x, ref_y = minx_tr, miny_tr
        elif origin_mode == "bottom right":
            ref_x, ref_y = maxx_tr, miny_tr
        elif origin_mode == "top right":
            ref_x, ref_y = maxx_tr, maxy_tr
        elif origin_mode == "center":
            ref_x = (minx_tr + maxx_tr) / 2.0
            ref_y = (miny_tr + maxy_tr) / 2.0
        else:  # "top left" par défaut
            ref_x, ref_y = minx_tr, maxy_tr

        offset_x = -ref_x * scale
        offset_y = -ref_y * scale

        paths_for_gcode = list(self.paths_raw)
        styles_for_gcode = list(self.path_styles)

        if self.hatch_enabled_var.get():
            try:
                hatch_spacing_mm = self._read_float(self.hatch_spacing_var, "Hatching spacing (mm)")
            except ValueError as e:
                messagebox.showerror("Error", str(e))
                return

            if hatch_spacing_mm <= 0:
                messagebox.showerror("Error", "Hatching spacing must be > 0.")
                return

            hatch_dir = self.hatch_direction_var.get()
            if hatch_dir == "Horizontal":
                angles = [0.0]
            elif hatch_dir == "Vertical":
                angles = [90.0]
            else:
                angles = [0.0, 90.0]

            spacing_svg = hatch_spacing_mm / scale

            for path, style in zip(self.paths_raw, self.path_styles):
                stroke_color, width, fill_color = style
                if fill_color is None:
                    continue

                pts = discretize_path(path, GCODE_SAMPLE_PER_UNIT, max_points=4000)
                if len(pts) < 3:
                    continue
                poly = pts[:]
                if (abs(poly[0][0] - poly[-1][0]) > 1e-9) or (abs(poly[0][1] - poly[-1][1]) > 1e-9):
                    poly.append(poly[0])

                for ang in angles:
                    segments = hatch_polygon(poly, spacing_svg, angle_deg=ang)
                    for (p1, p2) in segments:
                        p1c = complex(p1[0], p1[1])
                        p2c = complex(p2[0], p2[1])
                        hatch_path = Path(Line(p1c, p2c))
                        paths_for_gcode.append(hatch_path)
                        styles_for_gcode.append(("#000000", width, None))

        filepath = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save GCode",
            defaultextension=".gcode",
            filetypes=[("GCode file", "*.gcode"), ("All files type", "*.*")]
        )
        if not filepath:
            return

        gcode_lines = generate_gcode_from_paths(
            paths_for_gcode,
            styles_for_gcode,
            mode=mode,
            scale=scale,
            feed=feed,
            travel_feed=travel_feed,
            laser_power_max=laser_power,
            pen_up=pen_up,
            pen_down=pen_down,
            offset_x=offset_x,
            offset_y=offset_y,
            sample_per_unit=GCODE_SAMPLE_PER_UNIT,
            bounds_svg=page_bounds,
            rot_deg=rot,
            flip_h=flip_h,
            flip_v=not flip_v,
            engrave_power=engrave_power,
            cut_power=cut_power,
            engrave_passes=engrave_passes,
            cut_passes=cut_passes
        )
        
        if self.g10_origin_var.get():
            # On les insère juste après la première ligne (en général un commentaire)
            insert_pos = 3 if len(gcode_lines) > 3 else len(gcode_lines)
            gcode_lines.insert(insert_pos, "G10 L20 P1 Y0")
            gcode_lines.insert(insert_pos, "G10 L20 P1 X0")

        try:
            FilePath(filepath).write_text("\n".join(gcode_lines), encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Error", f"Impossible to write Gcode file:\n{e}")
            return

        # Met à jour la preview GCode (incluant hachures) avec les paramètres actuels
        self.build_gcode_preview_data()
        self.redraw_previews()

        messagebox.showinfo("BeamCraft exporter", f"GCode saved in :\n{filepath}")


# ==========================
# Main
# ==========================

if __name__ == "__main__":
    app = Svg2GcodeApp()
    app.run()
