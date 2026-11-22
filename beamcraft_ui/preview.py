# BeamCraft - preview canvas (zoom / pan / smooth redraw) and drawing helpers
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import tkinter as tk

import numpy as np

try:
    from PIL import Image, ImageTk
except ImportError:   # pragma: no cover
    Image = ImageTk = None

from .theme import PREVIEW, layer_color


class Viewport:
    """World (mm) <-> screen mapping with zoom and pan."""

    def __init__(self, canvas, y_up):
        self.canvas = canvas
        self.y_up = y_up
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.bounds = (0.0, 0.0, 1.0, 1.0)
        self.pad = 18

    def reset(self):
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0

    def _base(self):
        w = max(2, self.canvas.winfo_width())
        h = max(2, self.canvas.winfo_height())
        minx, miny, maxx, maxy = self.bounds
        bw = max(maxx - minx, 1e-6)
        bh = max(maxy - miny, 1e-6)
        scale = min((w - 2 * self.pad) / bw, (h - 2 * self.pad) / bh)
        ox = (w - bw * scale) / 2.0
        oy = (h - bh * scale) / 2.0
        return scale, ox, oy, w, h

    def affine(self):
        scale, ox, oy, w, h = self._base()
        minx, miny, maxx, maxy = self.bounds
        cx, cy = w / 2.0, h / 2.0
        z = self.zoom
        ax = scale * z
        bx = cx + (ox - minx * scale - cx) * z + self.pan_x
        if self.y_up:
            ay = -scale * z
            by = cy + (oy + maxy * scale - cy) * z + self.pan_y
        else:
            ay = scale * z
            by = cy + (oy - miny * scale - cy) * z + self.pan_y
        return np.array([[ax, 0, bx], [0, ay, by], [0, 0, 1.0]])

    def to_screen(self, pts):
        A = self.affine()
        pts = np.asarray(pts)
        out = np.empty(2 * len(pts))
        out[0::2] = A[0, 0] * pts.real + A[0, 2]
        out[1::2] = A[1, 1] * pts.imag + A[1, 2]
        return out.tolist()

    def to_world(self, x, y):
        A = self.affine()
        return complex((x - A[0, 2]) / A[0, 0], (y - A[1, 2]) / A[1, 1])

    def mm_per_px(self):
        return 1.0 / abs(self.affine()[0, 0])

    def zoom_at(self, ex, ey, factor):
        new_zoom = max(0.05, min(self.zoom * factor, 500.0))
        factor = new_zoom / self.zoom
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        cx, cy = w / 2.0, h / 2.0
        self.pan_x = (ex - cx) - (ex - cx - self.pan_x) * factor
        self.pan_y = (ey - cy) - (ey - cy - self.pan_y) * factor
        self.zoom = new_zoom
        return factor


class PreviewCanvas(tk.Canvas):
    """
    Canvas with smooth navigation:
      * wheel: the current drawing is scaled instantly, a clean redraw follows
        when the wheel stops (debounced);
      * drag (left or middle button): pan;
      * double-click: fit;
      * resize: debounced redraw.
    `draw(canvas)` is called to (re)draw the content.
    """

    def __init__(self, parent, y_up, draw, dark=True, **kw):
        super().__init__(parent, highlightthickness=0, bd=0, **kw)
        self.vp = Viewport(self, y_up)
        self._draw = draw
        self.dark = dark
        self._redraw_job = None
        self._drag = None
        self.photos = []
        self.on_view_change = None
        self.configure(bg=self.pal["bg"])
        self.bind("<Configure>", lambda e: self.schedule(60))
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", self._wheel)
        self.bind("<Button-5>", self._wheel)
        for b in (1, 2):
            self.bind(f"<ButtonPress-{b}>", self._press)
            self.bind(f"<B{b}-Motion>", self._motion)
            self.bind(f"<ButtonRelease-{b}>", self._release)
        self.bind("<Double-Button-1>", lambda e: self.fit())

    @property
    def pal(self):
        return PREVIEW["dark" if self.dark else "light"]

    def set_dark(self, dark):
        self.dark = dark
        self.configure(bg=self.pal["bg"])
        self.redraw()

    def fit(self):
        self.vp.reset()
        self.redraw()

    def schedule(self, ms=140):
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(ms, self.redraw)

    def redraw(self):
        self._redraw_job = None
        if self.winfo_width() <= 2:
            return
        self.delete("all")
        self.photos = []
        self._draw(self)
        if self.on_view_change:
            self.on_view_change()

    def _wheel(self, e):
        if getattr(e, "num", None) == 4 or getattr(e, "delta", 0) > 0:
            f = 1.2
        elif getattr(e, "num", None) == 5 or getattr(e, "delta", 0) < 0:
            f = 1 / 1.2
        else:
            return
        f = self.vp.zoom_at(e.x, e.y, f)
        self.scale("all", e.x, e.y, f, f)          # instant feedback
        if self.on_view_change:
            self.on_view_change()
        self.schedule(140)                          # crisp redraw when the wheel stops

    def _press(self, e):
        self._drag = (e.x, e.y)
        self.configure(cursor="fleur")

    def _motion(self, e):
        if self._drag is None:
            return
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self.vp.pan_x += dx
        self.vp.pan_y += dy
        self._drag = (e.x, e.y)
        self.move("all", dx, dy)

    def _release(self, e):
        if self._drag is not None:
            self._drag = None
            self.configure(cursor="")
            self.schedule(10)


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------

def draw_page(c, corners_world, filled=True):
    pal = c.pal
    pts = list(corners_world) + [corners_world[0]]
    xy = c.vp.to_screen(np.array(pts))
    if filled:
        c.create_polygon(xy, fill=pal["page"], outline="")
    c.create_line(xy, fill=pal["page_border"], dash=(3, 3))


def draw_bed(c, w, h):
    if w <= 0 or h <= 0:
        return
    pal = c.pal
    c.create_line(c.vp.to_screen(np.array([0, w, complex(w, h), 1j * h, 0])),
                  fill=pal["bed"], dash=(6, 3), width=2)


def draw_origin(c):
    pal = c.pal
    ox, oy = c.vp.to_screen(np.array([0j]))
    c.create_line(ox - 10, oy, ox + 10, oy, fill=pal["origin"], width=2)
    c.create_line(ox, oy - 10, ox, oy + 10, fill=pal["origin"], width=2)


def draw_images(c, items, dim=False, tag="img"):
    """items: (PIL 'L' image, 3x3 pixel -> screen matrix, is_burn_map). dim: pending job (machine view)."""
    if Image is None or not items:
        return
    W, H = c.winfo_width(), c.winfo_height()
    dark = c.dark
    page = c.pal["page"]
    pr, pg, pb = int(page[1:3], 16), int(page[3:5], 16), int(page[5:7], 16)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for img, A, burn in items:
        try:
            inv = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            continue
        data = (inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2])
        if burn:
            # burn map: 0 = nothing, 255 = full power
            if dim:
                img = img.point(lambda v: int(v * 0.4))
            if dark:
                g = img.point(lambda v: int(pr + (232 - pr) * v / 255))
                rgb = Image.merge("RGB", (g, img.point(lambda v: int(pg + (232 - pg) * v / 255)),
                                          img.point(lambda v: int(pb + (232 - pb) * v / 255))))
            else:
                g = img.point(lambda v: 255 - v)
                rgb = Image.merge("RGB", (g, g, g))
        else:
            rgb = img.convert("RGB")
        t = rgb.transform((W, H), Image.AFFINE, data, resample=Image.NEAREST, fillcolor=(0, 0, 0))
        m = Image.new("L", img.size, 255).transform((W, H), Image.AFFINE, data,
                                                    resample=Image.NEAREST, fillcolor=0)
        base.paste(t, mask=m)
    photo = ImageTk.PhotoImage(base)
    c.photos.append(photo)
    c.create_image(0, 0, image=photo, anchor="nw", tags=tag)


def draw_job(c, job, cache, show_travel=True, dim=False, max_travel=20000, tag="job"):
    """cache = (layers [(key, polys)], travel [(a, b)], bounds) prepared by the app.
    Everything drawn gets `tag` (so the job can be moved live on the canvas)."""
    layers, travel, _bounds = cache
    vp = c.vp
    pal = c.pal
    rasters = []
    for L in job.layers:
        for r in L.rasters:
            P = np.array([[r.dx, 0, r.x0], [0, -r.dy, r.y1], [0, 0, 1.0]])
            rasters.append((r.image, vp.affine() @ P, True))
    draw_images(c, rasters, dim=dim, tag=tag)
    if show_travel and len(travel) <= max_travel:
        for a, b in travel:
            if abs(b - a) > 1e-6:
                c.create_line(vp.to_screen(np.array([a, b])), fill=pal["travel"], dash=(4, 4), tags=tag)
    for key, polys in layers:
        col = layer_color(key, c.dark)
        if dim:
            col = _mix(col, pal["page"], 0.5)
        for p in polys:
            if len(p) >= 2:
                c.create_line(vp.to_screen(p), fill=col, tags=tag)


def _mix(a, b, t):
    try:
        ra, ga, ba = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        rb, gb, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    except ValueError:
        return a
    return "#{:02x}{:02x}{:02x}".format(int(ra + (rb - ra) * t), int(ga + (gb - ga) * t), int(ba + (bb - ba) * t))
