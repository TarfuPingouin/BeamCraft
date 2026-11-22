# BeamCraft - main window
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import copy
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path as FilePath
from tkinter import filedialog, messagebox, ttk

import numpy as np

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

from beamcraft_core import (APP_NAME, APP_VERSION, load_svg, build_job, write_gcode, View,
                            load_settings, save_settings, load_bitmap, BITMAP_EXT)
from beamcraft_core.geometry import prim_points, burn_polylines
from beamcraft_core.machine import GrblConnection
from beamcraft_core.simulate import estimate_job_time

from .theme import APP, apply_theme, Toast, Tooltip, pill, set_pill
from .page_design import DesignPage
from .page_machine import MachinePage
from .page_settings import SettingsPage
from .page_about import AboutPage

PAGES = (("design", "Files"), ("machine", "Machine"), ("settings", "Settings"), ("about", "About"))


def resource_dir():
    """Folder of the bundled resources (works with PyInstaller)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ring_depths(rings):
    """Nesting depth of each ring inside the others (for drawing holes)."""
    from beamcraft_core.offset import point_in_ring
    n = len(rings)
    if n <= 1:
        return [0] * n
    return [sum(1 for j, q in enumerate(rings) if j != i and len(q) >= 3 and point_in_ring(r[0], q))
            for i, r in enumerate(rings)]


class BeamCraftApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1366x820")
        self.root.minsize(1100, 660)
        try:
            self.root.iconbitmap(os.path.join(resource_dir(), "icone.ico"))
        except Exception:
            pass
        apply_theme(self.root)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

        # state
        self.settings, self.ui_state = load_settings()
        self.doc = None
        self.svg_path = None
        self.view = View()
        self.job = None
        self.job_cache = None
        self.job_time = 0.0
        self.warnings = []
        self._build_token = 0
        self._results = queue.Queue()
        self.preview_dark = self.ui_state.get("preview_dark", True)
        self.show_travel = tk.BooleanVar(value=self.ui_state.get("show_travel", True))
        self.grbl = GrblConnection()

        self.toast = Toast(self.root)
        self._icons = {}
        self._load_icons()
        self._build_header()
        self._build_pages()
        self._build_status()
        self._bind_keys()
        self.show_page(self.ui_state.get("page", "design") if self.ui_state.get("page") != "about" else "design")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(30, self._poll)
        from . import theme as _th
        if "fallback" in _th.THEME_NAME:
            self.root.after(800, lambda: self.toast.show(
                "The bundled Sun Valley theme could not be loaded: simplified dark theme used.", "warn", 6000))

    def run(self):
        self.root.mainloop()

    # ------------------------------------------------------------- build ---
    def _load_icons(self):
        if Image is None:
            return
        base = resource_dir()
        for key, name, size in (("flip_h", "flipH.png", (16, 16)), ("flip_v", "flipV.png", (16, 16)),
                                ("rot_cw", "rotateCW.png", (16, 16)), ("rot_ccw", "rotateCCW.png", (16, 16)),
                                ("home", "home.png", (22, 22)), ("aim", "aim.png", (16, 16)),
                                ("pause", "pause.png", (14, 14)), ("play", "play.png", (14, 14)),
                                ("stop", "stop.png", (14, 14))):
            try:
                img = Image.open(os.path.join(base, name)).convert("RGBA").resize(size, Image.LANCZOS)
                self._icons[key] = ImageTk.PhotoImage(img)
            except Exception:
                pass
        # dark variant of the aim icon, readable on the blue "selected" toggle
        try:
            img = Image.open(os.path.join(base, "aim.png")).convert("RGBA").resize((16, 16), Image.LANCZOS)
            r, g, b, a = img.split()
            dark = Image.merge("RGBA", (r.point(lambda v: 12), g.point(lambda v: 14), b.point(lambda v: 18), a))
            self._icons["aim_on"] = ImageTk.PhotoImage(dark)
        except Exception:
            pass
        try:
            logo = Image.open(os.path.join(base, "logo_wht.png")).convert("RGBA")
            h = 30
            w = int(logo.width * h / logo.height)
            self._icons["logo"] = ImageTk.PhotoImage(logo.resize((w, h), Image.LANCZOS))
            self._icons["logo_big"] = ImageTk.PhotoImage(logo.resize((int(logo.width * 90 / logo.height), 90),
                                                                     Image.LANCZOS))
        except Exception:
            pass

    def aim_image(self):
        """Image option for the Aim toggle buttons (dark icon when selected)."""
        if "aim" not in self._icons:
            return ""
        if "aim_on" in self._icons:
            return (self._icons["aim"], "selected", self._icons["aim_on"])
        return self._icons["aim"]

    def _build_header(self):
        hdr = ttk.Frame(self.root, style="Header.TFrame", padding=(14, 8, 14, 6))
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(2, weight=1)
        if "logo" in self._icons:
            ttk.Label(hdr, image=self._icons["logo"]).grid(row=0, column=0, padx=(0, 18))
        else:
            ttk.Label(hdr, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, padx=(0, 18))
        nav = ttk.Frame(hdr)
        nav.grid(row=0, column=1, sticky="w")
        self.page_var = tk.StringVar(value="design")
        for i, (key, label) in enumerate(PAGES):
            ttk.Radiobutton(nav, text=label, value=key, variable=self.page_var, style="Nav.Toggle.TButton",
                            command=lambda k=key: self.show_page(k)).grid(row=0, column=i, padx=3)
        right = ttk.Frame(hdr)
        right.grid(row=0, column=3, sticky="e")
        self.header_state = tk.StringVar(value="Disconnected")
        self.header_pill = pill(right, self.header_state, width=12)
        self.header_pill.grid(row=0, column=0, padx=(0, 8))
        self.header_pill.bind("<Button-1>", lambda e: self.show_page("machine"))
        Tooltip(self.header_pill, "Laser state - click to open the Machine page")
        tk.Frame(self.root, bg=APP["border"], height=1).grid(row=0, column=0, sticky="sew")

    def _build_pages(self):
        host = ttk.Frame(self.root)
        host.grid(row=1, column=0, sticky="nsew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.pages = {
            "design": DesignPage(host, self),
            "machine": MachinePage(host, self),
            "settings": SettingsPage(host, self),
            "about": AboutPage(host, self),
        }
        for p in self.pages.values():
            p.grid(row=0, column=0, sticky="nsew")

    def _build_status(self):
        bar = ttk.Frame(self.root, style="Status.TFrame", padding=(14, 4))
        bar.grid(row=2, column=0, sticky="ew")
        tk.Frame(self.root, bg=APP["border"], height=1).grid(row=2, column=0, sticky="new")
        bar.columnconfigure(2, weight=1)
        self.file_var = tk.StringVar(value="No file - Ctrl+O to open an SVG or an image")
        self.info_var = tk.StringVar(value="")
        self.busy_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.file_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(bar, textvariable=self.info_var, style="Status.TLabel").grid(row=0, column=1, sticky="w",
                                                                              padx=16)
        ttk.Label(bar, textvariable=self.busy_var, style="Status.TLabel",
                  foreground=APP["accent"]).grid(row=0, column=2, sticky="e", padx=8)
        self.warn_btn = ttk.Button(bar, text="", style="Small.TButton", command=self.show_warnings)
        ttk.Label(bar, text=f"{APP_NAME} {APP_VERSION}", style="Status.TLabel").grid(row=0, column=4,
                                                                                    sticky="e", padx=(8, 0))

    def _bind_keys(self):
        r = self.root
        r.bind_all("<Control-o>", lambda e: self.open_file())
        r.bind_all("<Control-g>", lambda e: self.export_gcode())
        r.bind_all("<F5>", lambda e: self.reload())
        r.bind_all("<Escape>", lambda e: self.pointer_off())
        for i, (key, _l) in enumerate(PAGES, start=1):
            r.bind_all(f"<Control-Key-{i}>", lambda e, k=key: self.show_page(k))

    # ------------------------------------------------------------- pages ---
    def show_page(self, key):
        if key not in self.pages:
            key = "design"
        self.page_var.set(key)
        page = self.pages[key]
        page.tkraise()
        if hasattr(page, "on_show"):
            page.on_show()
        self.ui_state["page"] = key

    # -------------------------------------------------------------- files ---
    def open_file(self):
        exts = " ".join("*" + e for e in BITMAP_EXT)
        path = filedialog.askopenfilename(
            parent=self.root, title="Open an SVG or an image",
            initialdir=self.ui_state.get("last_svg_dir") or None,
            filetypes=[("SVG and images", "*.svg " + exts), ("SVG", "*.svg"), ("Images", exts),
                       ("All files", "*.*")])
        if path:
            self.ui_state["last_svg_dir"] = os.path.dirname(path)
            self.open_path(path)

    def reload(self):
        if self.svg_path:
            self.open_path(self.svg_path, keep_view=True)

    def open_path(self, path, keep_view=False):
        s = self.settings
        self.busy_var.set("● Loading…")
        self.root.update_idletasks()
        try:
            if path.lower().endswith(BITMAP_EXT):
                doc = load_bitmap(path, dpi=s.dpi)
            else:
                doc = load_svg(path, dpi=s.dpi, force_dpi=s.force_dpi, tol=min(0.005, s.tolerance / 4.0))
        except Exception as e:
            self.busy_var.set("")
            messagebox.showerror("Error", f"Impossible to read the file:\n{e}")
            return
        self.doc = doc
        self.svg_path = path
        self.file_var.set(os.path.basename(path))
        if not keep_view:
            self.view = View()
            self.pointer_off()
        if not doc.shapes and not doc.images:
            self.toast.show("No usable path or image found in the file.", "warn")
        self.rebuild_job(fit=not keep_view)

    # ---------------------------------------------------------------- job ---
    def rebuild_job(self, fit=False):
        """Builds the job in a background thread: the UI stays responsive."""
        if self.doc is None:
            for p in self.pages.values():
                if hasattr(p, "on_job"):
                    p.on_job(False)
            return
        self._build_token += 1
        token = self._build_token
        doc, view = self.doc, copy.deepcopy(self.view)
        settings = copy.deepcopy(self.settings)
        self.busy_var.set("● Computing…")

        def work():
            try:
                job = build_job(doc, settings, view)
                caches = self._make_caches(doc, job, view, settings)
                t, _sim = estimate_job_time(job, settings, max_prims=150_000)
                self._results.put(("job", token, (job, caches, t, settings, fit)))
            except Exception as e:  # pragma: no cover - shown to the user
                self._results.put(("job_error", token, e))

        threading.Thread(target=work, daemon=True).start()

    def _make_caches(self, doc, job, view, s):
        layers, travel = [], []
        pos = job.ref
        for L in job.layers:
            if L.kind == "raster":
                if L.paths:
                    travel.append((pos, L.paths[0][0]))
                    pos = prim_points(*L.paths[-1])[-1]
                continue
            polys = []
            for st, pr in L.paths:
                travel.append((pos, st))
                polys.extend(np.array(p) for p in burn_polylines(st, pr, 8))
                pos = prim_points(st, pr)[-1]
            layers.append((L.name, polys))
        if s.return_to_origin:
            travel.append((pos, job.ref))
        return None, (layers, travel, job.bounds)

    def _apply_job(self, job, caches, t, settings, fit):
        # layers discovered by the worker are remembered in the real settings
        for k, L in settings.layers.items():
            self.settings.layers.setdefault(k, L)
        self.job = job
        _unused, self.job_cache = caches
        if self.view.x is None or self.view.y is None:
            self.view.x, self.view.y = round(job.center.real, 2), round(job.center.imag, 2)
        self.job_time = t
        self.warnings = job.warnings
        self.busy_var.set("")
        t = int(round(t))
        b = job.bounds
        self.info_var.set(f"{b[2] - b[0]:.1f} × {b[3] - b[1]:.1f} mm   ·   {job.n_paths} paths   ·   "
                          f"≈ {t // 3600}h{(t % 3600) // 60:02d}m{t % 60:02d}s   ·   {self.doc.size_source}")
        if self.warnings:
            self.warn_btn.configure(text=f"⚠ {len(self.warnings)}")
            self.warn_btn.grid(row=0, column=3, sticky="e", padx=4)
        else:
            self.warn_btn.grid_remove()
        for p in self.pages.values():
            if hasattr(p, "on_job"):
                p.on_job(fit)

    def show_warnings(self):
        if self.warnings:
            messagebox.showwarning("Warnings", "\n\n".join(self.warnings))

    # ------------------------------------------------------------- export ---
    def has_output(self):
        return self.job is not None and any(L.paths for L in self.job.layers)

    def gcode_lines(self, marks=None):
        return write_gcode(self.job, self.settings, self.svg_path or "", APP_VERSION, marks=marks)

    def export_gcode(self):
        if not self.has_output():
            self.toast.show("Nothing to engrave or cut: open a file first.", "warn")
            return
        initial = FilePath(self.svg_path).with_suffix(".gcode").name
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save G-code", defaultextension=".gcode",
            initialdir=self.ui_state.get("last_gcode_dir") or os.path.dirname(self.svg_path),
            initialfile=initial, filetypes=[("G-code", "*.gcode *.nc *.gc"), ("All files", "*.*")])
        if not path:
            return
        self.ui_state["last_gcode_dir"] = os.path.dirname(path)
        lines = self.gcode_lines()
        try:
            FilePath(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Error", f"Impossible to write the G-code file:\n{e}")
            return
        self.save()
        self.toast.show(f"G-code saved: {os.path.basename(path)}  ({len(lines)} lines)", "ok")

    # ---------------------------------------------------------- placement ---
    def rotate(self, deg):
        self.view.rotation = ((self.view.rotation or 0.0) + deg) % 360
        self.rebuild_job()

    def flip(self, h):
        if h:
            self.view.flip_h = not self.view.flip_h
        else:
            self.view.flip_v = not self.view.flip_v
        self.rebuild_job()

    def center_job(self):
        if self.doc is None:
            return
        self.set_placement(x=self.settings.bed_width / 2, y=self.settings.bed_height / 2)

    def set_placement(self, **kw):
        for k, v in kw.items():
            if k == "rotation":
                v = v % 360.0
            setattr(self.view, k, v)
        self.pages["design"].show_placement()
        self.rebuild_job()

    def preview_placement(self, **kw):
        """Live values while dragging on the work area."""
        self.pages["design"].show_placement(**kw)

    # ------------------------------------------------------------ pointer ---
    def aim_at(self, gpos, wpos):
        """gpos: G-code / work coordinates, wpos: work-area point (for display)."""
        if not self.grbl.connected:
            self.toast.show("Connect to the laser (Machine page) to aim at a point.", "warn")
            return
        s = self.settings
        from beamcraft_core.settings import POINTER_MAX_PCT
        pct = min(s.pointer_power_pct, POINTER_MAX_PCT)
        try:
            self.grbl.pointer_at(gpos.real, gpos.imag, s.max_power * pct / 100.0,
                                 feed=self.pages["machine"].feed(), timeout=s.pointer_timeout)
        except Exception as e:
            self.toast.show(str(e), "err")
            return
        self._aim_world = wpos
        for p in self._workspaces():
            p.set_pointer(False)
            p.target_marker(wpos)

    def pointer_off(self):
        if self.grbl.connected and self.grbl.pointer_active:
            self.grbl.pointer_off()

    def _workspaces(self):
        return [self.pages["design"].ws, self.pages["machine"].view]

    def set_preview_dark(self, dark):
        self.preview_dark = dark
        self.ui_state["preview_dark"] = dark
        for p in self.pages.values():
            if hasattr(p, "set_dark"):
                p.set_dark(dark)

    # ------------------------------------------------------------ polling ---
    def _poll(self):
        try:
            while True:
                kind, token, payload = self._results.get_nowait()
                if token != self._build_token:
                    continue        # a newer computation is running
                if kind == "job":
                    self._apply_job(*payload)
                else:
                    self.busy_var.set("")
                    messagebox.showerror("Error", f"Computation failed:\n{payload}")
        except queue.Empty:
            pass
        try:
            for _ in range(500):
                kind, payload = self.grbl.events.get_nowait()
                self.pages["machine"].on_event(kind, payload)
                if kind == "status" and payload.get("WPos"):
                    wp = payload["WPos"]
                    for w in self._workspaces():
                        w.set_head(complex(wp[0], wp[1]))
                elif kind == "pointer":
                    for w in self._workspaces():
                        w.set_pointer(payload, getattr(self, "_aim_world", None))
                        w.target_marker(None)
                    if payload:
                        self.toast.show("Aiming dot ON (low power) - Esc or Aim off to stop.", "warn", ms=2500)
                elif kind == "disconnected":
                    for w in self._workspaces():
                        w.set_head(None)
                        w.set_pointer(False)
                if kind == "status":
                    st = payload.get("state", "?")
                    if st != self.header_state.get():
                        self.header_state.set(st)
                        set_pill(self.header_pill, st)
                elif kind == "disconnected":
                    self.header_state.set("Disconnected")
                    set_pill(self.header_pill, "Disconnected")
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    # -------------------------------------------------------------- close ---
    def save(self):
        self.ui_state["show_travel"] = bool(self.show_travel.get())
        for p in self.pages.values():
            if hasattr(p, "store_ui"):
                p.store_ui(self.ui_state)
        save_settings(self.settings, self.ui_state)

    def on_close(self):
        try:
            if self.grbl.connected:
                self.grbl.disconnect("application closed")
        finally:
            self.save()
            self.root.destroy()


def main():
    app = BeamCraftApp()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.root.after(200, lambda: app.open_path(sys.argv[1]))
    app.run()
