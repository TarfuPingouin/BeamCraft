# BeamCraft - Settings page
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import copy
import tkinter as tk
from tkinter import messagebox, ttk

from beamcraft_core import Settings
from beamcraft_core.settings import ORIGINS, config_path

from .theme import APP, Card, ScrollFrame, Tooltip, fmt_num, parse_num

POWER_MODES = ("M4 - dynamic (recommended)", "M3 - constant")
START_MODES = ("Work area coordinates", "Current laser position")

# (card, kind, key, label, hint/values)
FIELDS = [
    ("machine", "num", "max_power", "Max. power S", "= Grbl $30"),
    ("machine", "combo", "laser_mode", "Power mode", POWER_MODES),
    ("machine", "switch", "air_assist", "Air assist (M8 / M9)", None),
    ("machine", "switch", "return_to_origin", "Return to 0,0 at the end", None),
    ("machine", "switch", "rapid_g1", "Travel with G1 S0 instead of G0", None),
    ("machine", "num", "travel_feed", "Travel speed mm/min", "G1 travel"),
    ("origin", "num", "bed_width", "Width mm", "X travel of the laser"),
    ("origin", "num", "bed_height", "Height mm", "Y travel of the laser"),
    ("origin", "combo", "start_from", "Job starts from", START_MODES),
    ("origin", "combo", "origin", "Anchor (current pos.)", ORIGINS),
    ("pointer", "num", "pointer_power_pct", "Dot power %", "1 % recommended, max. 5 %"),
    ("pointer", "num", "pointer_timeout", "Auto off after s", None),
    ("geometry", "int", "dpi", "SVG DPI", "files without mm / in size"),
    ("geometry", "switch", "force_dpi", "Always use the DPI", None),
    ("geometry", "num", "tolerance", "Curve tolerance mm", "0.01 - 0.05"),
    ("geometry", "switch", "use_arcs", "Output arcs G2 / G3 (no dotting)", None),
    ("geometry", "switch", "optimize_order", "Optimise path order", None),
    ("dynamics", "num", "max_rate_x", "Max. rate X  $110", "mm/min"),
    ("dynamics", "num", "max_rate_y", "Max. rate Y  $111", "mm/min"),
    ("dynamics", "num", "accel_x", "Accel. X  $120", "mm/s²"),
    ("dynamics", "num", "accel_y", "Accel. Y  $121", "mm/s²"),
    ("dynamics", "num", "junction_deviation", "Junction dev.  $11", "mm"),
    ("dynamics", "num", "arc_tolerance", "Arc tolerance  $12", "mm"),
    ("rotary", "switch", "rotary_enabled", "Rotary enabled", None),
    ("rotary", "combo", "rotary_type", "Type", ("roller", "chuck")),
    ("rotary", "num", "rotary_mm_per_rotation", "Y mm per rotation", "one turn of roller / chuck"),
    ("rotary", "num", "rotary_roller_diameter", "Roller Ø mm", "roller type"),
    ("rotary", "num", "rotary_object_diameter", "Object Ø mm", "chuck type"),
]
CARDS = [("machine", "Laser · Grbl v1.1", 0), ("origin", "Work area", 0), ("pointer", "Aiming dot", 0),
         ("geometry", "SVG & geometry", 1), ("dynamics", "Machine dynamics · time estimate", 1),
         ("rotary", "Rotary axis", 1)]
RELOAD_KEYS = ("dpi", "force_dpi", "tolerance")
NOTES = {"origin": "Work area coordinates: 0,0 = work-area origin (front left after homing, "
                   "or zero it with Zero XY). Current laser position: the job anchor is placed "
                   "where the head is.",
         "pointer": "Use only with laser safety glasses. The dot never exceeds 5 % of $30. "
                    "Aim mode: Files or Machine page, then click on the work area. Esc = off."}


class SettingsPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=(12, 10, 12, 8))
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.vars = {}
        self.widgets = {}
        self._loading = False

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Settings", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.dirty_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.dirty_var, foreground=APP["warn"]).grid(row=0, column=1, sticky="w",
                                                                                 padx=14)
        ttk.Button(top, text="Defaults", command=self.defaults).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Revert", command=self.load).grid(row=0, column=3, padx=4)
        ttk.Button(top, text="Apply", style="Accent.TButton", command=self.apply).grid(row=0, column=4,
                                                                                     padx=(4, 0))

        sf = ScrollFrame(self)
        sf.grid(row=1, column=0, sticky="nsew")
        grid = sf.inner
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="col", minsize=300)
        cols = [ttk.Frame(grid) for _ in range(3)]
        for i, c in enumerate(cols):
            c.grid(row=0, column=i, sticky="new", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
            c.columnconfigure(0, weight=1)
        bodies = {}
        counts = [0, 0, 0]
        for key, title, col in CARDS:
            card = Card(cols[col], title)
            card.grid(row=counts[col], column=0, sticky="ew", pady=(0, 12))
            counts[col] += 1
            card.body.columnconfigure(1, weight=1)
            bodies[key] = card.body
        rows = {k: 0 for k in bodies}
        for card, kind, key, label, extra in FIELDS:
            self._field(bodies[card], rows[card], kind, key, label, extra)
            rows[card] += 1
        for key, txt in NOTES.items():
            ttk.Label(bodies[key], text=txt, style="Panel.Muted.TLabel", wraplength=270, justify="left").grid(
                row=rows[key], column=0, columnspan=3, sticky="w", pady=(6, 0))
            rows[key] += 1
        b = bodies["dynamics"]
        w = ttk.Button(b, text="Read from the laser  ($$)", command=self.read_machine)
        w.grid(row=rows["dynamics"], column=0, columnspan=3, sticky="w", pady=(8, 0))
        Tooltip(w, "Copies $30 $110 $111 $120 $121 $11 $12 from the connected laser")

        # appearance
        card = Card(cols[2], "Appearance")
        card.grid(row=counts[2], column=0, sticky="ew", pady=(0, 12))
        b = card.body
        ttk.Label(b, text="Preview background", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.dark_var = tk.StringVar(value="dark" if app.preview_dark else "light")
        seg = ttk.Frame(b, style="Panel.TFrame")
        seg.grid(row=0, column=1, sticky="e")
        for i, (v, t) in enumerate((("dark", "Dark"), ("light", "Light"))):
            ttk.Radiobutton(seg, text=t, value=v, variable=self.dark_var, style="Chip.Toggle.TButton",
                            command=lambda: app.set_preview_dark(self.dark_var.get() == "dark")
                            ).grid(row=0, column=i, padx=1)
        ttk.Checkbutton(b, text="Show travel moves", variable=app.show_travel, style="Panel.Switch.TCheckbutton",
                        command=lambda: app.pages["design"].out.redraw()).grid(row=1, column=0, columnspan=2,
                                                                              sticky="w", pady=(8, 0))
        counts[2] += 1

        card = Card(cols[2], "Keyboard")
        card.grid(row=counts[2], column=0, sticky="ew", pady=(0, 12))
        for i, (k, t) in enumerate((("Ctrl+O", "open a file"), ("F5", "reload"), ("Ctrl+G", "save G-code"),
                                    ("Ctrl+1…4", "switch page"), ("Arrows", "jog (live view)"),
                                    ("Aim + click", "aiming dot at a point"), ("Esc", "aiming dot off"),
                                    ("Wheel / right-drag", "zoom / pan"), ("Double-click", "fit"))):
            ttk.Label(card.body, text=k, style="Panel.TLabel").grid(row=i, column=0, sticky="w", pady=1)
            ttk.Label(card.body, text=t, style="Panel.Muted.TLabel").grid(row=i, column=1, sticky="w", padx=10)
        counts[2] += 1
        ttk.Label(cols[2], text=f"Settings file:\n{config_path()}", style="Muted.TLabel",
                  wraplength=290, justify="left").grid(row=counts[2], column=0, sticky="w", pady=(0, 10))
        self.load()

    # ------------------------------------------------------------------
    def _field(self, parent, row, kind, key, label, extra):
        if kind == "switch":
            var = tk.BooleanVar()
            w = ttk.Checkbutton(parent, text=label, variable=var, style="Panel.Switch.TCheckbutton")
            w.grid(row=row, column=0, columnspan=3, sticky="w", pady=3)
        else:
            ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=3)
            if kind == "combo":
                var = tk.StringVar()
                w = ttk.Combobox(parent, textvariable=var, values=list(extra), state="readonly",
                                 width=24 if key in ("laser_mode", "start_from") else 12)
                w.grid(row=row, column=1, columnspan=2, sticky="e", pady=3)
            else:
                var = tk.StringVar()
                w = ttk.Entry(parent, textvariable=var, width=9, justify="right")
                w.grid(row=row, column=1, sticky="e", pady=3)
                if extra:
                    Tooltip(w, extra)
        var.trace_add("write", lambda *a: self._mark_dirty())
        self.vars[key] = (kind, var)
        self.widgets[key] = w

    def _mark_dirty(self):
        if not self._loading:
            self.dirty_var.set("●  Unsaved changes")

    def on_show(self):
        if not self.dirty_var.get():
            self.load()

    def load(self, s=None):
        s = s or self.app.settings
        self._loading = True
        for key, (kind, var) in self.vars.items():
            val = getattr(s, key)
            if key == "laser_mode":
                var.set(POWER_MODES[0] if val == "M4" else POWER_MODES[1])
            elif key == "start_from":
                var.set(START_MODES[0] if val == "absolute" else START_MODES[1])
            elif kind == "switch":
                var.set(bool(val))
            elif kind == "combo":
                var.set(val)
            else:
                var.set(fmt_num(val))
            w = self.widgets[key]
            if isinstance(w, ttk.Entry) and not isinstance(w, ttk.Combobox):
                w.state(["!invalid"])
        self._loading = False
        self.dirty_var.set("")

    def collect(self):
        s = copy.deepcopy(self.app.settings)
        bad = []
        for key, (kind, var) in self.vars.items():
            try:
                if key == "laser_mode":
                    val = "M4" if var.get().startswith("M4") else "M3"
                elif key == "start_from":
                    val = "absolute" if var.get() == START_MODES[0] else "current"
                elif kind == "switch":
                    val = bool(var.get())
                elif kind == "combo":
                    val = var.get()
                elif kind == "int":
                    val = parse_num(var.get(), int)
                else:
                    val = parse_num(var.get())
                setattr(s, key, val)
                if kind in ("num", "int"):
                    self.widgets[key].state(["!invalid"])
            except ValueError:
                bad.append(key)
                self.widgets[key].state(["invalid"])
        if bad:
            raise ValueError("Some values are not numbers (highlighted in red).")
        s.validate()
        return s

    def apply(self):
        try:
            new = self.collect()
        except ValueError as e:
            messagebox.showerror("Settings", str(e))
            return
        old = self.app.settings
        reload_doc = any(getattr(new, k) != getattr(old, k) for k in RELOAD_KEYS)
        self.app.settings = new
        self.app.save()
        self.dirty_var.set("")
        self.app.toast.show("Settings applied", "ok", ms=1800)
        self.app.pages["machine"].view.schedule(10)
        if reload_doc and self.app.svg_path:
            self.app.open_path(self.app.svg_path, keep_view=True)
        else:
            self.app.rebuild_job()

    def defaults(self):
        if not messagebox.askyesno("Settings", "Restore the default machine and geometry settings?\n"
                                   "(layer settings are kept)"):
            return
        d = Settings()
        d.layers, d.layer_order = self.app.settings.layers, self.app.settings.layer_order
        self.load(d)
        self._mark_dirty()

    def read_machine(self):
        mp = self.app.pages["machine"]

        def got(d):
            if not d:
                self.app.toast.show("No answer to $$ from the laser.", "err")
                return
            mapping = {110: "max_rate_x", 111: "max_rate_y", 120: "accel_x", 121: "accel_y",
                       11: "junction_deviation", 12: "arc_tolerance", 30: "max_power"}
            for n, key in mapping.items():
                if n in d:
                    self.vars[key][1].set(f"{d[n]:g}")
            if d.get(32, 1) == 0:
                messagebox.showwarning("Laser mode is OFF",
                                       "$32=0 : Grbl stops at every power change (dots, burns).\n"
                                       "Send $32=1 in the console of the Machine page.")
            self.app.toast.show("Laser settings read - press Apply to keep them.", "ok")
        try:
            mp.read_dollars(got)
        except Exception as e:
            self.app.toast.show(str(e), "warn")
