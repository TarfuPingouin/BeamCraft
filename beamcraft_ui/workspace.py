# BeamCraft - work area canvas: grid, placed job, direct manipulation, aiming, laser head
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import math
import time

import numpy as np

from beamcraft_core.machine import POLL_HZ

from .preview import PreviewCanvas, draw_job, draw_origin
from .theme import FONTS

HANDLE = 5          # px, half size of the corner handles
ROT_OFFSET = 26     # px, distance of the rotation handle above the box


def _pip(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


class Workspace(PreviewCanvas):
    """
    The work area as seen from above (X right, Y up, 0,0 = work-area origin).

    editable=True (Files page): drag the design to move it, drag a corner to
    resize it, drag the round handle to rotate it (Shift = 15° steps).
    In both pages: right / middle drag = pan, wheel = zoom, double-click = fit,
    "aim" mode: a click sends the head to the point and lights the aiming dot.
    """

    def __init__(self, parent, app, editable, extra_draw=None, **kw):
        self.app = app
        self.editable = editable
        self.extra_draw = extra_draw
        self.mode = "select"
        self.head = None
        self._head_from = self._head_to = None
        self._head_t0 = 0.0
        self._anim = None
        self._op = None
        self.pointer_pos = None
        super().__init__(parent, y_up=True, draw=self._draw_all, dark=app.preview_dark, **kw)
        # replace the default left-button pan by the manipulation logic
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            self.unbind(seq)
        self.bind("<ButtonPress-1>", self._b1_press)
        self.bind("<B1-Motion>", self._b1_motion)
        self.bind("<ButtonRelease-1>", self._b1_release)
        for b in (2, 3):
            self.bind(f"<ButtonPress-{b}>", self._press)
            self.bind(f"<B{b}-Motion>", self._motion)
            self.bind(f"<ButtonRelease-{b}>", self._release)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("coords"))
        self.on_view_change = self._place_head

    # ------------------------------------------------------------ geometry ---
    def world_bounds(self):
        s = self.app.settings
        x0, y0, x1, y1 = 0.0, 0.0, s.bed_width, s.bed_height
        job = self.app.job
        if job is not None:
            xs = [c.real for c in job.box] + [job.bounds[0], job.bounds[2]]
            ys = [c.imag for c in job.box] + [job.bounds[1], job.bounds[3]]
            x0, y0, x1, y1 = min(x0, *xs), min(y0, *ys), max(x1, *xs), max(y1, *ys)
        m = 0.03 * max(x1 - x0, y1 - y0)
        return (x0 - m, y0 - m, x1 + m, y1 + m)

    def to_gcode(self, w):
        """Work-area point -> G-code / work coordinates."""
        job = self.app.job
        return w - job.ref if job is not None else w

    def from_gcode(self, p):
        job = self.app.job
        return p + job.ref if job is not None else p

    # ------------------------------------------------------------- drawing ---
    def _draw_all(self, c):
        app = self.app
        s = app.settings
        pal = self.pal
        self.vp.bounds = self.world_bounds()
        W, H = s.bed_width, s.bed_height
        vp = self.vp
        # bed + grid
        c.create_polygon(vp.to_screen(np.array([0, W, complex(W, H), 1j * H])), fill=pal["page"], outline="")
        ppm = 1.0 / vp.mm_per_px()
        minor = 10.0 if ppm * 10 >= 7 else (50.0 if ppm * 50 >= 7 else 100.0)
        major = 50.0 if minor < 50 else (100.0 if minor == 50 else 500.0)
        A = vp.affine()
        x_scr = lambda x: A[0, 0] * x + A[0, 2]
        y_scr = lambda y: A[1, 1] * y + A[1, 2]
        top, bot = y_scr(H), y_scr(0)
        left, right = x_scr(0), x_scr(W)
        k = minor
        while k < W - 1e-6:
            maj = abs(k / major - round(k / major)) < 1e-6
            c.create_line(x_scr(k), top, x_scr(k), bot, fill=pal["grid_major" if maj else "grid"])
            k += minor
        k = minor
        while k < H - 1e-6:
            maj = abs(k / major - round(k / major)) < 1e-6
            c.create_line(left, y_scr(k), right, y_scr(k), fill=pal["grid_major" if maj else "grid"])
            k += minor
        c.create_rectangle(left, top, right, bot, outline=pal["page_border"])
        f = FONTS.get("small")
        k = 0.0
        while k <= W + 1e-6:
            c.create_text(x_scr(k), bot + 3, text=f"{k:g}", anchor="n", fill=pal["text"], font=f)
            k += major
        k = major
        while k <= H + 1e-6:
            c.create_text(left - 4, y_scr(k), text=f"{k:g}", anchor="e", fill=pal["text"], font=f)
            k += major
        draw_origin(c)
        # job
        if app.job is not None and app.job_cache is not None:
            draw_job(c, app.job, app.job_cache, show_travel=app.show_travel.get() and self.editable,
                     dim=not self.editable, tag="job")
            if self.extra_draw:
                self.extra_draw(c)
            if self.editable:
                self._draw_selection()
            if app.settings.start_from == "current":
                r = app.job.ref
                x, y = vp.to_screen(np.array([r]))
                c.create_oval(x - 5, y - 5, x + 5, y + 5, outline=pal["origin"], width=2, tags="job")
        if self.pointer_pos is not None:
            self.set_pointer(True, self.pointer_pos)
        # head
        c.create_oval(0, 0, 0, 0, outline=pal["head"], width=2, tags=("head", "head_ring"))
        c.create_line(0, 0, 0, 0, fill=pal["head"], tags=("head", "head_h"))
        c.create_line(0, 0, 0, 0, fill=pal["head"], tags=("head", "head_v"))
        self._place_head()
        self.configure(cursor="crosshair" if self.mode == "aim" else "")

    def _box_screen(self, box=None):
        box = box if box is not None else self.app.job.box
        xy = self.vp.to_screen(np.array(box))
        return [(xy[i], xy[i + 1]) for i in range(0, 8, 2)]

    def _rot_handle(self, pts):
        cx = sum(p[0] for p in pts) / 4
        cy = sum(p[1] for p in pts) / 4
        mx = (pts[2][0] + pts[3][0]) / 2
        my = (pts[2][1] + pts[3][1]) / 2
        dx, dy = mx - cx, my - cy
        d = math.hypot(dx, dy) or 1.0
        return (mx, my), (mx + dx / d * ROT_OFFSET, my + dy / d * ROT_OFFSET), (cx, cy)

    def _draw_selection(self, box=None, tag="sel"):
        pal = self.pal
        pts = self._box_screen(box)
        flat = [v for p in pts + [pts[0]] for v in p]
        self.create_line(flat, fill=pal["sel"], dash=(5, 3), tags=tag)
        for x, y in pts:
            self.create_rectangle(x - HANDLE, y - HANDLE, x + HANDLE, y + HANDLE, fill=pal["page"],
                                  outline=pal["sel"], width=2, tags=(tag, "handle"))
        (mx, my), (hx, hy), (cx, cy) = self._rot_handle(pts)
        self.create_line(mx, my, hx, hy, fill=pal["sel"], tags=tag)
        self.create_oval(hx - 6, hy - 6, hx + 6, hy + 6, fill=pal["sel"], outline="", tags=(tag, "rot"))
        self.create_line(cx - 5, cy, cx + 5, cy, fill=pal["sel"], tags=tag)
        self.create_line(cx, cy - 5, cx, cy + 5, fill=pal["sel"], tags=tag)

    # ----------------------------------------------------------- mouse ops ---
    def set_mode(self, mode):
        self.mode = mode
        self.configure(cursor="crosshair" if mode == "aim" else "")

    def _hit(self, x, y):
        job = self.app.job
        if not self.editable or job is None:
            return None
        pts = self._box_screen()
        _m, (hx, hy), _c = self._rot_handle(pts)
        if math.hypot(x - hx, y - hy) <= 9:
            return ("rotate", None)
        for i, (px, py) in enumerate(pts):
            if abs(x - px) <= HANDLE + 3 and abs(y - py) <= HANDLE + 3:
                return ("scale", i)
        if _pip(x, y, pts):
            return ("move", None)
        return None

    def _hover(self, e):
        w = self.vp.to_world(e.x, e.y)
        self.delete("coords")
        self.create_text(self.winfo_width() - 8, self.winfo_height() - 6, anchor="se",
                         text=f"X {w.real:7.1f}   Y {w.imag:7.1f}", fill=self.pal["text"],
                         font=FONTS.get("small"), tags="coords")
        if self.mode == "aim" or self._op:
            return
        h = self._hit(e.x, e.y)
        cur = {"rotate": "exchange", "scale": "sizing", "move": "fleur"}.get(h[0] if h else None, "")
        try:
            self.configure(cursor=cur)
        except Exception:
            self.configure(cursor="")

    def _b1_press(self, e):
        self.focus_set()
        if self.mode == "aim":
            self.app.aim_at(self.to_gcode(self.vp.to_world(e.x, e.y)), self.vp.to_world(e.x, e.y))
            return
        h = self._hit(e.x, e.y)
        if h is None:
            self._op = ("pan", None)
            self._press(e)
            return
        kind, idx = h
        job, view = self.app.job, self.app.view
        pts = self._box_screen()
        _m, _h, (cx, cy) = self._rot_handle(pts)
        self._op = (kind, {"x": e.x, "y": e.y, "cx": cx, "cy": cy, "center": job.center,
                           "rot": view.rotation or 0.0, "scale": view.scale or 100.0,
                           "a0": math.atan2(-(e.y - cy), e.x - cx), "d0": math.hypot(e.x - cx, e.y - cy),
                           "box": list(job.box), "last": (e.x, e.y)})

    def _b1_motion(self, e):
        if not self._op:
            return
        kind, d = self._op
        if kind == "pan":
            self._motion(e)
            return
        if kind == "move":
            dx, dy = e.x - d["last"][0], e.y - d["last"][1]
            d["last"] = (e.x, e.y)
            self.move("job", dx, dy)
            self.move("sel", dx, dy)
            w0 = self.vp.to_world(d["x"], d["y"])
            w1 = self.vp.to_world(e.x, e.y)
            c = d["center"] + (w1 - w0)
            self.app.preview_placement(x=c.real, y=c.imag)
            return
        self.delete("ghost")
        center = d["center"]
        if kind == "rotate":
            a = math.atan2(-(e.y - d["cy"]), e.x - d["cx"])
            delta = math.degrees(a - d["a0"])
            new = d["rot"] + delta
            new = round(new / 15.0) * 15.0 if (e.state & 0x0001) else round(new, 1)
            delta = new - d["rot"]
            rot = complex(math.cos(math.radians(delta)), math.sin(math.radians(delta)))
            box = [center + (p - center) * rot for p in d["box"]]
            d["new"] = new % 360.0
            self.app.preview_placement(rotation=d["new"])
        else:
            f = max(0.01, math.hypot(e.x - d["cx"], e.y - d["cy"]) / max(d["d0"], 1e-6))
            box = [center + (p - center) * f for p in d["box"]]
            d["new"] = max(1.0, min(10000.0, round(d["scale"] * f, 1)))
            self.app.preview_placement(scale=d["new"])
        self.itemconfigure("sel", state="hidden")
        pts = self._box_screen(box)
        flat = [v for p in pts + [pts[0]] for v in p]
        self.create_line(flat, fill=self.pal["sel"], width=2, tags="ghost")
        label = f"{d['new']:.1f}°" if kind == "rotate" else f"{d['new']:g} %"
        self.create_text(e.x + 14, e.y - 14, text=label, fill=self.pal["sel"], anchor="w",
                         font=FONTS.get("section"), tags="ghost")

    def _b1_release(self, e):
        if not self._op:
            return
        kind, d = self._op
        self._op = None
        if kind == "pan":
            self._release(e)
            return
        self.delete("ghost")
        if kind == "move":
            w0 = self.vp.to_world(d["x"], d["y"])
            w1 = self.vp.to_world(e.x, e.y)
            if abs(w1 - w0) > 1e-9:
                c = d["center"] + (w1 - w0)
                self.app.set_placement(x=round(c.real, 2), y=round(c.imag, 2))
        elif "new" in d:
            self.app.set_placement(**{"rotation" if kind == "rotate" else "scale": d["new"]})
        else:
            self.itemconfigure("sel", state="normal")

    # --------------------------------------------------------------- head ---
    def set_head(self, pos):
        """pos: G-code / work coordinates (None = unknown)."""
        if pos is None:
            self.head = self._head_to = None
            self._place_head()
            return
        pos = self.from_gcode(pos)
        if self.head is None:
            self.head = self._head_from = self._head_to = pos
            self._place_head()
            return
        self._head_from = self.head
        self._head_to = pos
        self._head_t0 = time.monotonic()
        if self._anim is None:
            self._anim = self.after(16, self._animate)

    def _animate(self):
        self._anim = None
        if self._head_to is None:
            return
        t = min(1.0, (time.monotonic() - self._head_t0) * POLL_HZ)
        self.head = self._head_from + (self._head_to - self._head_from) * t
        self._place_head()
        if t < 1.0:
            self._anim = self.after(16, self._animate)

    def _place_head(self):
        if not self.find_withtag("head"):
            return
        if self.head is None:
            for tag in ("head_ring", "head_h", "head_v"):
                self.coords(tag, -20, -20, -20, -20)
            return
        x, y = self.vp.to_screen(np.array([self.head]))
        r = 7
        self.coords("head_ring", x - r, y - r, x + r, y + r)
        self.coords("head_h", x - r - 6, y, x + r + 6, y)
        self.coords("head_v", x, y - r - 6, x, y + r + 6)
        self.tag_raise("head")
        if getattr(self, "follow", None) is not None and self.follow.get():
            w, h = self.winfo_width(), self.winfo_height()
            if not (40 < x < w - 40 and 40 < y < h - 40):
                self.vp.pan_x += w / 2 - x
                self.vp.pan_y += h / 2 - y
                self.schedule(1)

    def target_marker(self, pos):
        """Where the head is going for the aiming dot (shown until the dot is on)."""
        self.delete("target")
        if pos is None:
            return
        x, y = self.vp.to_screen(np.array([pos]))
        col = self.pal["head"]
        self.create_line(x - 12, y, x - 4, y, fill=col, tags="target")
        self.create_line(x + 4, y, x + 12, y, fill=col, tags="target")
        self.create_line(x, y - 12, x, y - 4, fill=col, tags="target")
        self.create_line(x, y + 4, x, y + 12, fill=col, tags="target")

    def set_pointer(self, on, pos=None):
        self.delete("aimdot")
        self.pointer_pos = pos if on else None
        if on and pos is not None:
            x, y = self.vp.to_screen(np.array([pos]))
            for r, w in ((11, 1), (4, 0)):
                if w:
                    self.create_oval(x - r, y - r, x + r, y + r, outline=self.pal["head"], width=1, tags="aimdot")
                else:
                    self.create_oval(x - r, y - r, x + r, y + r, fill=self.pal["head"], outline="", tags="aimdot")
