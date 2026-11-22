# BeamCraft - Files page: toolbar, work area, placement, layers
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import tkinter as tk
from tkinter import ttk

from beamcraft_core.settings import MODES, IMAGE_MODES, LayerSettings, validate_layer
from beamcraft_core.toolpath import document_layers

from .layer_list import LayerList, layer_name
from .theme import APP, Card, ScrollFrame, Tooltip, fmt_num, parse_num
from .workspace import Workspace

SIDEBAR_W = 300


class DesignPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=(12, 10, 12, 8))
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._editor_key = None
        self._editor_vars = {}
        self._editor_widgets = {}
        self._apply_job = None
        self.aim = tk.BooleanVar(value=False)
        self._build_toolbar()
        self._build_body()

    # ------------------------------------------------------------ toolbar ---
    def _build_toolbar(self):
        app = self.app
        tb = ttk.Frame(self)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tb.columnconfigure(20, weight=1)
        col = [0]

        def add(w, padx=2, tip=None):
            w.grid(row=0, column=col[0], padx=padx)
            col[0] += 1
            if tip:
                Tooltip(w, tip)
            return w

        def sep():
            add(ttk.Separator(tb, orient="vertical"), padx=8).grid_configure(sticky="ns", pady=2)

        add(ttk.Button(tb, text="Open…", command=app.open_file), tip="Open an SVG or an image (Ctrl+O)")
        add(ttk.Button(tb, text="Reload", command=app.reload), tip="Reload the file after editing it (F5)")
        sep()
        icons = app._icons
        for key, txt, tip, cmd in (("rot_ccw", "⟲", "Rotate 90° left", lambda: app.rotate(90)),
                                   ("rot_cw", "⟳", "Rotate 90° right", lambda: app.rotate(-90)),
                                   ("flip_h", "⇋", "Flip horizontally", lambda: app.flip(True)),
                                   ("flip_v", "⇵", "Flip vertically", lambda: app.flip(False))):
            if key in icons:
                w = ttk.Button(tb, image=icons[key], style="Tool.TButton", command=cmd)
            else:
                w = ttk.Button(tb, text=txt, width=3, style="Tool.TButton", command=cmd)
            add(w, tip=tip)
        add(ttk.Button(tb, text="Center", command=app.center_job), tip="Center the design on the work area")
        sep()
        add(ttk.Button(tb, text="Fit", command=lambda: self.ws.fit()), tip="Fit the view (or double-click)")
        add(ttk.Checkbutton(tb, text="Aim", image=app.aim_image(), compound="left", variable=self.aim,
                            style="Aim.Toggle.TButton", command=self._aim_mode), padx=4,
            tip="Click on the work area: the head goes there and lights a low-power dot")
        add(ttk.Checkbutton(tb, text="Travel moves", style="Switch.TCheckbutton", variable=app.show_travel,
                            command=lambda: self.ws.redraw()), padx=(10, 0))
        col[0] = 21
        add(ttk.Button(tb, text="Send to laser  ▶", command=lambda: app.show_page("machine")), padx=4,
            tip="Open the Machine page to frame and run the job")
        add(ttk.Button(tb, text="Generate G-code", style="Accent.TButton", command=app.export_gcode),
            padx=(4, 0), tip="Save the G-code file (Ctrl+G)")

    def _aim_mode(self):
        self.ws.set_mode("aim" if self.aim.get() else "select")
        if not self.aim.get():
            self.app.pointer_off()

    # --------------------------------------------------------------- body ---
    def _build_body(self):
        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        c = Card(body, "Work area", pad=8)
        c.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.ws_hint = tk.StringVar(value="drag: move  ·  corners: resize  ·  ● handle: rotate (Shift = 15°)  ·  "
                                          "right-drag: pan")
        ttk.Label(c.header, textvariable=self.ws_hint, style="Panel.Muted.TLabel").grid(row=0, column=1, sticky="e")
        self.ws = Workspace(c.body, self.app, editable=True)
        self.ws.pack(fill="both", expand=True)

        side = ttk.Frame(body, width=SIDEBAR_W)
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(2, weight=1)

        # ---- placement ----
        pc = Card(side, "Placement", pad=10)
        pc.grid(row=0, column=0, sticky="ew")
        b = pc.body
        for i in range(4):
            b.columnconfigure(i, weight=1 if i % 2 else 0)
        self.pv = {k: tk.StringVar() for k in ("x", "y", "w", "h", "rotation", "scale")}
        self.pe = {}
        for r, ((k1, l1), (k2, l2)) in enumerate(((("x", "X"), ("y", "Y")), (("w", "W"), ("h", "H")),
                                                    (("rotation", "∠"), ("scale", "%")))):
            for c_, (k, lab) in enumerate(((k1, l1), (k2, l2))):
                ttk.Label(b, text=lab, style="Panel.Muted.TLabel").grid(row=r, column=2 * c_, sticky="w",
                                                                       padx=(0 if c_ == 0 else 10, 4), pady=3)
                e = ttk.Entry(b, textvariable=self.pv[k], width=8, justify="right")
                e.grid(row=r, column=2 * c_ + 1, sticky="ew", pady=3)
                e.bind("<Return>", lambda ev, k=k: self._commit(k))
                e.bind("<FocusOut>", lambda ev, k=k: self._commit(k))
                self.pe[k] = e
        Tooltip(self.pe["x"], "X of the design centre on the work area (mm)")
        Tooltip(self.pe["y"], "Y of the design centre on the work area (mm)")
        Tooltip(self.pe["w"], "Width (mm) - keeps the proportions")
        Tooltip(self.pe["h"], "Height (mm) - keeps the proportions")
        Tooltip(self.pe["rotation"], "Rotation (degrees, counter-clockwise)")
        Tooltip(self.pe["scale"], "Scale (%)")

        # ---- layers ----
        lc = Card(side, "Layers", pad=8)
        lc.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        hb = ttk.Frame(lc.header, style="Panel.TFrame")
        hb.grid(row=0, column=1, sticky="e")
        for i, (t, d, tip) in enumerate((("▲", -1, "Output earlier"), ("▼", 1, "Output later"))):
            w = ttk.Button(hb, text=t, width=2, style="Small.TButton", command=lambda d=d: self.move_layer(d))
            w.grid(row=0, column=i, padx=1)
            Tooltip(w, tip)
        self.layers = LayerList(lc.body, self._select_layer, self.toggle_layer)
        self.layers.pack(fill="x")

        self.editor_card = Card(side, "Layer settings", pad=8)
        self.editor_card.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self.editor_scroll = ScrollFrame(self.editor_card.body, bg=APP["card"])
        self.editor_scroll.pack(fill="both", expand=True)
        self.editor = self.editor_scroll.inner
        self._editor_placeholder()

    # ---------------------------------------------------------- placement ---
    def show_placement(self, x=None, y=None, rotation=None, scale=None):
        """Updates the fields (live while dragging, or after a computation)."""
        app = self.app
        v = app.view
        if app.job is None:
            for var in self.pv.values():
                var.set("")
            return
        x = v.x if x is None else x
        y = v.y if y is None else y
        rotation = v.rotation if rotation is None else rotation
        scale = v.scale if scale is None else scale
        box = app.job.box
        w = abs(box[1] - box[0]) * scale / (v.scale or 100.0)
        h = abs(box[3] - box[0]) * scale / (v.scale or 100.0)
        focus = self.focus_get()
        for k, val in (("x", x), ("y", y), ("w", w), ("h", h), ("rotation", rotation), ("scale", scale)):
            if focus is not self.pe[k] and val is not None:
                self.pv[k].set(f"{val:.2f}".rstrip("0").rstrip("."))

    def _commit(self, key):
        app = self.app
        if app.job is None:
            return
        try:
            val = parse_num(self.pv[key].get())
        except ValueError:
            self.pe[key].state(["invalid"])
            return
        self.pe[key].state(["!invalid"])
        v = app.view
        box = app.job.box
        if key in ("x", "y", "rotation"):
            cur = getattr(v, key)
            if cur is None or abs(cur - val) > 1e-9:
                app.set_placement(**{key: val})
        elif key == "scale":
            if val > 0 and abs(val - v.scale) > 1e-9:
                app.set_placement(scale=val)
        else:
            cur = abs(box[1] - box[0]) if key == "w" else abs(box[3] - box[0])
            if val > 0 and cur > 0 and abs(val - cur) > 1e-6:
                app.set_placement(scale=v.scale * val / cur)

    # ------------------------------------------------------------ drawing ---
    def set_dark(self, dark):
        self.ws.set_dark(dark)

    def on_job(self, fit):
        if fit:
            self.ws.vp.reset()
        self.ws.redraw()
        self.refresh_layers()
        self.show_placement()

    # ------------------------------------------------------------- layers ---
    def refresh_layers(self):
        app = self.app
        if app.doc is None:
            self.layers.set_layers([])
            self._editor_placeholder()
            return
        keys = app.settings.ordered(list(document_layers(app.doc)))
        self.layers.set_layers([(k, app.settings.layer(k)) for k in keys])
        sel = self.layers.selected
        if sel and sel != self._editor_key:
            self._show_editor()
        elif sel is None:
            self._editor_placeholder()

    def _select_layer(self, key):
        self._show_editor()

    def _selected(self):
        return self.layers.selected

    def move_layer(self, delta):
        key = self._selected()
        if key and self.app.doc is not None:
            self.app.settings.move_layer(key, delta, list(document_layers(self.app.doc)))
            self.app.rebuild_job()

    def toggle_layer(self, key=None):
        key = key or self._selected()
        if key:
            L = self.app.settings.layer(key)
            L.enabled = not L.enabled
            if "enabled" in self._editor_vars and self._editor_key == key:
                self._editor_vars["enabled"].set(L.enabled)
            self.app.rebuild_job()

    # ------------------------------------------------------------- editor ---
    def _clear_editor(self):
        for w in self.editor.winfo_children():
            w.destroy()
        self._editor_vars = {}
        self._editor_widgets = {}

    def _editor_placeholder(self):
        self._clear_editor()
        self._editor_key = None
        ttk.Label(self.editor, text="Select a layer to edit\nits power, speed and mode.",
                  style="Panel.Muted.TLabel", justify="left").pack(anchor="w", pady=4)

    def _show_editor(self):
        key = self._selected()
        if not key:
            return
        self._clear_editor()
        self._editor_key = key
        L = self.app.settings.layer(key)
        f = self.editor
        f.columnconfigure(1, weight=1)
        V, Wd = self._editor_vars, self._editor_widgets
        row = [0]

        def section(title):
            ttk.Label(f, text=title, style="Panel.Muted.TLabel").grid(row=row[0], column=0, columnspan=2,
                                                                     sticky="w", pady=(10, 2))
            row[0] += 1

        def entry(label, name, tip=None):
            ttk.Label(f, text=label, style="Panel.TLabel").grid(row=row[0], column=0, sticky="w", pady=2)
            V[name] = tk.StringVar(value=fmt_num(getattr(L, name)))
            e = ttk.Entry(f, width=8, textvariable=V[name])
            e.grid(row=row[0], column=1, sticky="e", pady=2)
            Wd[name] = e
            if tip:
                Tooltip(e, tip)
            V[name].trace_add("write", lambda *a: self._schedule_apply())
            row[0] += 1

        def switch(label, name):
            V[name] = tk.BooleanVar(value=getattr(L, name))
            ttk.Checkbutton(f, text=label, variable=V[name], style="Panel.Switch.TCheckbutton",
                            command=self._schedule_apply).grid(row=row[0], column=0, columnspan=2,
                                                               sticky="w", pady=2)
            row[0] += 1

        def combo(label, name, values, rebuild=False):
            ttk.Label(f, text=label, style="Panel.TLabel").grid(row=row[0], column=0, sticky="w", pady=2)
            V[name] = tk.StringVar(value=getattr(L, name))
            cb = ttk.Combobox(f, textvariable=V[name], values=list(values), state="readonly", width=9)
            cb.grid(row=row[0], column=1, sticky="e", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e: self._apply_now(rebuild_editor=rebuild))
            row[0] += 1

        title = layer_name(key) + ("" if key == "image" else
                                   ("  ·  stroke" if key.startswith("stroke") else "  ·  fill"))
        ttk.Label(f, text=title, style="Panel.Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        row[0] = 1
        switch("Output this layer", "enabled")
        if key == "image":
            combo("Mode", "image_mode", IMAGE_MODES, rebuild=True)
            entry("Max. power %", "power_pct")
            if L.image_mode == "grayscale":
                entry("Min. power %", "min_power_pct", "Power used for white areas")
            entry("Speed mm/min", "feed")
            entry("Passes", "passes")
            section("RASTER")
            entry("Line interval mm", "line_interval", "0.1 mm = 254 DPI")
            entry("Overscan mm", "overscan", "The head starts / stops outside the image, laser off")
            if L.image_mode == "threshold":
                entry("Threshold 0-255", "threshold")
            entry("Brightness", "brightness", "-100 .. 100")
            entry("Contrast", "contrast", "-100 .. 100")
            switch("Negative", "negative")
            switch("Bidirectional", "hatch_bidirectional")
        else:
            combo("Mode", "mode", MODES, rebuild=True)
            entry("Power %", "power_pct", "% of the max. power ($30)")
            entry("Speed mm/min", "feed")
            entry("Passes", "passes")
            if "line" in L.mode:
                section("CUT · KERF & TABS")
                entry("Kerf offset mm", "kerf", "Half the kerf width. Parts grow, holes shrink. 0 = off")
                entry("Tabs / contour", "tabs_count", "Small uncut bridges that hold the part. 0 = none")
                entry("Tab width mm", "tab_width")
                entry("Min. contour mm", "tabs_min_length", "Shorter contours get no tabs")
                switch("Tabs on outer contours only", "tabs_outer_only")
            if "fill" in L.mode:
                section("FILL · HATCHING")
                entry("Line spacing mm", "hatch_spacing", "0.1 - 0.2 mm for diode lasers")
                entry("Angle °", "hatch_angle", "0 = horizontal")
                entry("Overscan mm", "overscan")
                switch("Cross-hatching", "hatch_cross")
                switch("Bidirectional", "hatch_bidirectional")
                switch("Merge overlapping shapes", "hatch_merge")
        self.editor_scroll.canvas.yview_moveto(0)

    def _schedule_apply(self):
        if self._apply_job is not None:
            self.after_cancel(self._apply_job)
        self._apply_job = self.after(450, self._apply_now)

    def _apply_now(self, rebuild_editor=False):
        self._apply_job = None
        key = self._editor_key
        if not key:
            return
        cur = self.app.settings.layer(key)
        new = LayerSettings(**{f: getattr(cur, f) for f in cur.__dataclass_fields__})
        bad = []
        for name, var in self._editor_vars.items():
            old = getattr(new, name)
            try:
                if isinstance(old, bool):
                    val = bool(var.get())
                elif isinstance(old, int):
                    val = parse_num(var.get(), int)
                elif isinstance(old, float):
                    val = parse_num(var.get())
                else:
                    val = var.get()
                setattr(new, name, val)
            except (ValueError, tk.TclError):
                bad.append(name)
        if not bad:
            tmp = LayerSettings(**{f: getattr(new, f) for f in new.__dataclass_fields__})
            for name in self._editor_widgets:
                single = LayerSettings(**{f: getattr(cur, f) for f in cur.__dataclass_fields__})
                setattr(single, name, getattr(tmp, name))
                if validate_layer(single):
                    bad.append(name)
        for name, w in self._editor_widgets.items():
            w.state(["invalid"] if name in bad else ["!invalid"])
        if bad:
            return
        if new == cur:
            return
        self.app.settings.layers[key] = new
        if rebuild_editor:
            self._show_editor()
        self.app.rebuild_job()
