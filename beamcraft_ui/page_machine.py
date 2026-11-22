# BeamCraft - Machine page: connection, position, jog, live view, job, console
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import re
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import numpy as np

from beamcraft_core.machine import list_serial_ports, clean_gcode
from beamcraft_core.geometry import burn_polylines

from .workspace import Workspace
from .theme import APP, FONTS, Card, ScrollFrame, Tooltip, pill, set_pill, parse_num, layer_color

BAUDS = ("115200", "250000", "230400", "57600", "38400", "19200", "9600")
STEPS = ("0.1", "1", "10", "50", "100")
COL_W = 316


class MachinePage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=(12, 10, 12, 8))
        self.app = app
        self.grbl = app.grbl
        ui = app.ui_state
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.exec_marks = []          # (acknowledged lines, layer index, path index) per path
        self.exec_job = None          # job being executed (to draw its executed paths)
        self.done_n = 0               # number of paths already executed
        self.status = {}
        self._job_t0 = None
        self.dollars = None           # collector for "$$" answers
        self._dollars_cb = None
        self.v = {k: tk.StringVar(value=v) for k, v in (
            ("port", ui.get("port", "")), ("baud", ui.get("baud", "115200")),
            ("step", ui.get("step", "10")),
            ("jog_feed", ui.get("jog_feed", "3000")), ("move_x", "0"), ("move_y", "0"),
            ("state", "Disconnected"), ("wx", "—"), ("wy", "—"), ("mpos", "MPos  —"),
            ("fs", "F —   S —"), ("ov_feed", "100 %"), ("ov_power", "100 %"),
            ("progress", "No job running"), ("cmd", ""))}
        self.follow = tk.BooleanVar(value=ui.get("follow_head", False))
        self.aim = tk.BooleanVar(value=False)
        self._build_left()
        self._build_center()
        self._build_right()
        self._update_connected()

    # ================================================================ left ===
    def _build_left(self):
        sf = ScrollFrame(self)
        sf.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        sf.configure(width=COL_W)
        sf.canvas.configure(width=COL_W - 16)
        col = sf.inner
        col.columnconfigure(0, weight=1)
        v = self.v

        # ---- connection ----
        c = Card(col, "Connection")
        c.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        b = c.body
        b.columnconfigure(0, weight=1)
        self.port_cb = ttk.Combobox(b, textvariable=v["port"], width=18)
        self.port_cb.grid(row=0, column=0, sticky="ew")
        Tooltip(self.port_cb, "Serial port (COM3, /dev/ttyUSB0) or network: socket://192.168.x.x:23")
        w = ttk.Button(b, text="↻", width=3, style="Small.TButton", command=self.refresh_ports)
        w.grid(row=0, column=1, padx=(4, 0))
        Tooltip(w, "Refresh the list of ports")
        ttk.Combobox(b, textvariable=v["baud"], values=BAUDS, width=8).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.connect_btn = ttk.Button(b, text="Connect", style="Accent.TButton", command=self.toggle_connect)
        self.connect_btn.grid(row=1, column=0, columnspan=2, sticky="e", pady=(6, 0))

        # ---- position ----
        c = Card(col, "Position")
        c.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.state_pill = pill(c.header, v["state"], width=11)
        self.state_pill.grid(row=0, column=1, sticky="e")
        b = c.body
        b.columnconfigure(1, weight=1)
        for r, (ax, var) in enumerate((("X", "wx"), ("Y", "wy"))):
            ttk.Label(b, text=ax, style="Panel.Muted.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 8))
            ttk.Label(b, textvariable=v[var], style="Panel.DRO.TLabel", anchor="e").grid(row=r, column=1, sticky="ew")
            ttk.Button(b, text=f"Zero {ax}", style="Small.TButton",
                       command=lambda ax=ax: self._cmd(f"G10 L20 P1 {ax}0")).grid(row=r, column=2, padx=(8, 0))
        ttk.Label(b, textvariable=v["mpos"], style="Panel.DROsmall.TLabel").grid(row=2, column=0, columnspan=3,
                                                                              sticky="w", pady=(2, 0))
        bb = ttk.Frame(b, style="Panel.TFrame")
        bb.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        for i in range(3):
            bb.columnconfigure(i, weight=1)
        for i, (t, fn, tip) in enumerate((
                ("Zero XY", lambda: self._cmd("G10 L20 P1 X0 Y0"), "Current position becomes the work zero"),
                ("Go to 0", lambda: self.grbl.goto_zero(self.feed()), "Move to the work zero (laser off)"),
                ("Home", lambda: self.grbl.home(), "Homing cycle ($H) - needs limit switches"),
                ("Unlock", lambda: self.grbl.unlock(), "Clear an alarm ($X)"),
                ("Frame", self.frame, "Trace the job rectangle with the laser off"),
                ("Reset", lambda: self.grbl.stop_job(reset=True), "Soft reset (stops everything)"))):
            w = ttk.Button(bb, text=t, style="Small.TButton", command=lambda fn=fn: self._safe(fn))
            w.grid(row=i // 3, column=i % 3, sticky="ew", padx=2, pady=2)
            Tooltip(w, tip)

        # ---- jog: D-pad ----
        c = Card(col, "Jog")
        c.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(c.header, text="laser off", style="Panel.Muted.TLabel").grid(row=0, column=1, sticky="e")
        b = c.body
        b.columnconfigure(0, weight=1)
        pad = ttk.Frame(b, style="Panel.TFrame")
        pad.grid(row=0, column=0, pady=(0, 6))
        big = {"width": 4, "style": "Pad.TButton"}
        for (r, cc, txt, dx, dy, tip) in ((0, 1, "Y+", 0, 1, "Jog Y+"), (1, 0, "X−", -1, 0, "Jog X−"),
                                          (1, 2, "X+", 1, 0, "Jog X+"), (2, 1, "Y−", 0, -1, "Jog Y−")):
            w = ttk.Button(pad, text=txt, command=lambda dx=dx, dy=dy: self.jog_step(dx, dy), **big)
            w.grid(row=r, column=cc, padx=2, pady=2, ipady=2)
            Tooltip(w, tip + " by the step below (arrow keys on the live view)")
        icons = self.app._icons
        if "home" in icons:
            w = ttk.Button(pad, image=icons["home"], command=lambda: self._safe(self.grbl.home), style="Pad.TButton")
        else:
            w = ttk.Button(pad, text="Home", command=lambda: self._safe(self.grbl.home), **big)
        w.grid(row=1, column=1, padx=2, pady=2, ipady=2)
        Tooltip(w, "Home ($H) - needs limit switches. Without them use 'Go to 0'.")
        chips = ttk.Frame(b, style="Panel.TFrame")
        chips.grid(row=1, column=0)
        ttk.Label(chips, text="Step mm", style="Panel.Muted.TLabel").pack(side="left", padx=(0, 6))
        for st in STEPS:
            ttk.Radiobutton(chips, text=st, value=st, variable=v["step"], style="Chip.Toggle.TButton").pack(
                side="left", padx=1)
        row = ttk.Frame(b, style="Panel.TFrame")
        row.grid(row=2, column=0, pady=(6, 0))
        ttk.Label(row, text="Speed", style="Panel.Muted.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=v["jog_feed"], width=7, justify="center").pack(side="left", padx=6)
        ttk.Label(row, text="mm/min", style="Panel.Muted.TLabel").pack(side="left")

        # ---- move by X / Y ----
        c = Card(col, "Move")
        c.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(c.header, text="relative", style="Panel.Muted.TLabel").grid(row=0, column=1, sticky="e")
        b = c.body
        for i, ax in enumerate(("x", "y")):
            ttk.Label(b, text=ax.upper(), style="Panel.Title.TLabel").grid(row=0, column=2 * i, padx=(0 if i == 0 else 10, 4))
            e = ttk.Entry(b, textvariable=v["move_" + ax], width=7, justify="center")
            e.grid(row=0, column=2 * i + 1)
            e.bind("<Return>", lambda ev: self.move_rel())
        w = ttk.Button(b, text="Go", style="Accent.TButton", width=5, command=self.move_rel)
        w.grid(row=0, column=4, padx=(10, 0))
        Tooltip(w, "Moves by X and Y from the current position (e.g. X 10 = 10 mm to the right)")

    # ============================================================== center ===
    def _build_center(self):
        c = Card(self, "Live view", pad=8)
        c.grid(row=0, column=1, sticky="nsew")
        h = ttk.Frame(c.header, style="Panel.TFrame")
        h.grid(row=0, column=1, sticky="e")
        w = ttk.Checkbutton(h, text="Aim", image=self.app.aim_image(), compound="left",
                            variable=self.aim, style="Aim.Toggle.TButton", command=self._aim_mode)
        w.grid(row=0, column=0, padx=(0, 8))
        Tooltip(w, "Click on the work area: the head goes there and lights a low-power dot")
        ttk.Checkbutton(h, text="Follow head", variable=self.follow, style="Panel.Switch.TCheckbutton"
                        ).grid(row=0, column=1, padx=(0, 6))
        w = ttk.Button(h, text="Clear", style="Small.TButton", command=self.clear_progress)
        w.grid(row=0, column=2, padx=2)
        Tooltip(w, "Hide the executed paths (shown in full colour during the job)")
        ttk.Button(h, text="Fit", style="Small.TButton", command=lambda: self.view.fit()).grid(row=0, column=3,
                                                                                            padx=2)
        self.view = Workspace(c.body, self.app, editable=False, extra_draw=lambda cv: self._draw_done(cv, 0, self.done_n))
        self.view.follow = self.follow
        self.view.pack(fill="both", expand=True)
        for key, d in (("<Left>", (-1, 0)), ("<Right>", (1, 0)), ("<Up>", (0, 1)), ("<Down>", (0, -1))):
            self.view.bind(key, lambda e, d=d: self.jog_step(*d))

    def _aim_mode(self):
        self.view.set_mode("aim" if self.aim.get() else "select")
        if not self.aim.get():
            self.app.pointer_off()

    # =============================================================== right ===
    def _build_right(self):
        col = ttk.Frame(self, width=COL_W)
        col.grid(row=0, column=2, sticky="nse", padx=(10, 0))
        col.grid_propagate(False)
        col.columnconfigure(0, weight=1)
        col.rowconfigure(2, weight=1)
        v = self.v

        c = Card(col, "Job")
        c.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        b = c.body
        b.columnconfigure((0, 1, 2), weight=1)
        self.start_btn = ttk.Button(b, text="Start job", style="Accent.TButton", command=self.start_job)
        self.start_btn.grid(row=0, column=0, columnspan=3, sticky="ew")
        icons = self.app._icons
        for i, (t, ic, fn, tip) in enumerate((("Pause", "pause", lambda: self.grbl.pause(), "Feed hold"),
                                              ("Resume", "play", lambda: self.grbl.resume(), "Cycle start"),
                                              ("Stop", "stop", self.stop_job, "Stop and reset (position kept)"))):
            w = ttk.Button(b, text=t, image=icons.get(ic, ""), compound="left", style="Small.TButton",
                           command=lambda fn=fn: self._safe(fn))
            w.grid(row=1, column=i, sticky="ew", padx=2, pady=(6, 0))
            Tooltip(w, tip)
        self.pbar = ttk.Progressbar(b, maximum=100)
        self.pbar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        ttk.Label(b, textvariable=v["progress"], style="Panel.Muted.TLabel", justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w")

        c = Card(col, "Live adjustments")
        c.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        b = c.body
        b.columnconfigure(2, weight=1)
        for r, (label, var, fn) in enumerate((("Speed", "ov_feed", self.grbl.feed_override),
                                              ("Power", "ov_power", self.grbl.power_override))):
            ttk.Label(b, text=label, style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=3)
            ttk.Button(b, text="−10", width=4, style="Small.TButton",
                       command=lambda fn=fn: self._safe(lambda: fn("-10"))).grid(row=r, column=1, padx=2)
            ttk.Label(b, textvariable=v[var], style="Panel.Title.TLabel", anchor="center").grid(row=r, column=2,
                                                                                         sticky="ew")
            ttk.Button(b, text="+10", width=4, style="Small.TButton",
                       command=lambda fn=fn: self._safe(lambda: fn("+10"))).grid(row=r, column=3, padx=2)
            ttk.Button(b, text="100", width=4, style="Small.TButton",
                       command=lambda fn=fn: self._safe(lambda: fn("reset"))).grid(row=r, column=4, padx=2)

        c = Card(col, "Console")
        c.grid(row=2, column=0, sticky="nsew")
        b = c.body
        b.rowconfigure(0, weight=1)
        b.columnconfigure(0, weight=1)
        self.console = ScrolledText(b, height=8, width=30, state="disabled", relief="flat", bd=0,
                                    bg="#16171a", fg="#d6d9df", insertbackground="white",
                                    font=FONTS.get("mono"), highlightthickness=0, padx=6, pady=4)
        self.console.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.console.tag_configure("sent", foreground=APP["accent"])
        self.console.tag_configure("err", foreground=APP["err"])
        self.console.tag_configure("info", foreground=APP["muted"])
        e = ttk.Entry(b, textvariable=v["cmd"])
        e.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        e.bind("<Return>", lambda ev: self.send_console())
        ttk.Button(b, text="Send", style="Small.TButton", command=self.send_console).grid(row=1, column=1,
                                                                                         padx=(4, 0), pady=(6, 0))
        ttk.Label(b, text="$$ settings · $I info · $G modes", style="Panel.Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

    # ============================================================ helpers ===
    def on_show(self):
        self.refresh_ports()
        self.view.schedule(10)

    def set_dark(self, dark):
        self.view.set_dark(dark)

    def on_job(self, fit):
        if fit:
            self.view.vp.reset()
        self.view.schedule(10)

    def store_ui(self, ui):
        for k in ("port", "baud", "step", "jog_feed"):
            ui[k] = self.v[k].get()
        ui["follow_head"] = bool(self.follow.get())

    def _safe(self, fn):
        try:
            if not self.grbl.connected:
                raise RuntimeError("Not connected to the laser.")
            fn()
        except Exception as e:
            self.app.toast.show(str(e), "err")

    def _cmd(self, line):
        self._safe(lambda: self.grbl.command(line))

    def log(self, txt, tag=None):
        c = self.console
        c.configure(state="normal")
        c.insert("end", txt + "\n", tag or ())
        n = int(c.index("end-1c").split(".")[0])
        if n > 3000:
            c.delete("1.0", f"{n - 3000}.0")
        c.see("end")
        c.configure(state="disabled")

    def refresh_ports(self):
        ports = list_serial_ports()
        self.port_cb.configure(values=ports)
        if not self.v["port"].get() and ports:
            self.v["port"].set(ports[0])

    def toggle_connect(self):
        if self.grbl.connected:
            self.grbl.disconnect("closed by user")
            return
        port = self.v["port"].get().strip()
        if not port:
            self.app.toast.show("Choose a port first.", "warn")
            return
        try:
            self.grbl.connect(port, int(self.v["baud"].get()))
        except Exception as e:
            messagebox.showerror("Connection", f"Cannot open {port}:\n{e}")
            return
        self.log(f"connected to {port}", "info")
        self.app.toast.show(f"Connected to {port}", "ok")
        self._update_connected()

    def _update_connected(self):
        on = self.grbl.connected
        self.connect_btn.configure(text="Disconnect" if on else "Connect",
                                   style="TButton" if on else "Accent.TButton")
        if not on:
            self.v["state"].set("Disconnected")
            set_pill(self.state_pill, "Disconnected")

    # ---------------------------------------------------------------- jog ---
    def feed(self):
        try:
            return max(1.0, parse_num(self.v["jog_feed"].get()))
        except ValueError:
            return 3000.0

    def jog_step(self, dx, dy):
        def go():
            step = parse_num(self.v["step"].get())
            self.grbl.jog(dx * step, dy * step, self.feed())
        self._safe(go)

    def move_rel(self):
        def go():
            x = parse_num(self.v["move_x"].get() or 0)
            y = parse_num(self.v["move_y"].get() or 0)
            if x or y:
                self.grbl.jog(x, y, self.feed())
        try:
            self._safe(go)
        except ValueError:
            self.app.toast.show("Invalid distance.", "err")

    def frame(self):
        if not self.app.has_output():
            raise RuntimeError("Open a file first.")
        b, r = self.app.job.bounds, self.app.job.ref
        self.grbl.frame((b[0] - r.real, b[1] - r.imag, b[2] - r.real, b[3] - r.imag), self.feed())

    # ---------------------------------------------------------------- job ---
    def start_job(self):
        app = self.app
        if not self.grbl.connected:
            app.toast.show("Connect to the laser first.", "warn")
            return
        if not app.has_output():
            app.toast.show("Nothing to send: open a file first.", "warn")
            return
        st = self.status.get("state", "")
        if st and not st.startswith("Idle"):
            app.toast.show(f"The laser is not idle ({st}).", "warn")
            return
        t = int(app.job_time)
        if not messagebox.askokcancel(
                "Start the job",
                f"{app.job.n_paths} paths, about {t // 60} min {t % 60:02d} s.\n\n"
                "• Laser focused, material in place\n"
                "• Safety glasses on, extraction / air assist on\n"
                "• Work zero set (Zero XY) or G10 option enabled in Settings"):
            return
        self.clear_progress()
        marks = []
        lines = app.gcode_lines(marks)
        # line numbers -> number of streamed (cleaned) lines
        clean_before = []
        n = 0
        for raw in lines:
            clean_before.append(n)
            if clean_gcode([raw]):
                n += 1
        clean_before.append(n)
        self.exec_marks = [(clean_before[end], li, pi) for end, li, pi in marks]
        self.exec_job = app.job
        self.grbl.start_job(lines)
        self._job_t0 = time.monotonic()

    def stop_job(self):
        self.grbl.stop_job(reset=True)

    def send_console(self):
        txt = self.v["cmd"].get().strip()
        if not txt:
            return
        self.v["cmd"].set("")
        if txt in ("?", "!", "~"):
            self._safe(lambda: self.grbl.realtime(txt.encode()))
        else:
            self._cmd(txt)

    def read_dollars(self, callback):
        """Sends $$ and calls callback(dict) with the numeric settings."""
        if not self.grbl.connected:
            raise RuntimeError("Connect to the laser first (Machine page).")
        self.dollars = {}
        self._dollars_cb = callback
        self.grbl.command("$$")

        def done():
            d, self.dollars = self.dollars, None
            cb, self._dollars_cb = self._dollars_cb, None
            if cb:
                cb(d or {})
        self.after(1500, done)

    # ------------------------------------------------------------- events ---
    def on_event(self, kind, payload):
        v = self.v
        if kind == "status":
            self.status = st = payload
            state = st.get("state", "?")
            if state != v["state"].get():
                v["state"].set(state)
                set_pill(self.state_pill, state)
            wp, mp = st.get("WPos"), st.get("MPos")
            if wp:
                v["wx"].set(f"{wp[0]:.3f}")
                v["wy"].set(f"{wp[1]:.3f}")
            if mp:
                v["mpos"].set(f"MPos {mp[0]:.2f}, {mp[1]:.2f}   ·   F {st.get('feed', 0):g}   S {st.get('power', 0):g}")
            if "Ov" in st:
                v["ov_feed"].set(f"{st['Ov'][0]} %")
                v["ov_power"].set(f"{st['Ov'][2]} %")
        elif kind == "line":
            if self.dollars is not None and payload.startswith("$"):
                m = re.match(r"^\$(\d+)=([-+]?\d*\.?\d+)", payload)
                if m:
                    self.dollars[int(m.group(1))] = float(m.group(2))
            if payload != "ok" or not self.grbl.job_running:
                self.log(payload)
        elif kind == "sent":
            self.log("> " + payload, "sent")
        elif kind in ("error", "alarm"):
            self.log(payload, "err")
            if kind == "alarm":
                self.app.toast.show(payload + " - use Unlock or Home.", "err", ms=6000)
        elif kind in ("msg", "welcome"):
            self.log(payload, "info")
        elif kind == "progress":
            done, total = payload
            if total:
                self.pbar["value"] = 100.0 * done / total
                el = time.monotonic() - (self._job_t0 or time.monotonic())
                rem = el * (total - done) / done if done else 0
                v["progress"].set(f"{100.0 * done / total:.0f} %  ·  {done} / {total} lines\n"
                                  f"{int(el) // 60}:{int(el) % 60:02d} elapsed  ·  "
                                  f"{int(rem) // 60}:{int(rem) % 60:02d} left")
                first = self.done_n
                while self.done_n < len(self.exec_marks) and self.exec_marks[self.done_n][0] <= done:
                    self.done_n += 1
                if self.done_n > first:
                    self._draw_done(self.view, first, self.done_n)
        elif kind == "job":
            self.log(f"job {payload}", "info")
            if payload == "paused-error":
                messagebox.showwarning(
                    "Job paused", f"Grbl reported: {self.grbl.last_error}\n\nThe lines already in Grbl's "
                    "buffer are still executed. Press Resume to continue, or Stop.")
            elif payload == "done":
                self.pbar["value"] = 100
                self.app.toast.show("Job finished", "ok")
            elif payload == "stopped":
                self.app.toast.show("Job stopped", "warn")
        elif kind == "disconnected":
            self.log(f"disconnected ({payload})", "info")
            self._update_connected()
            self.aim.set(False)
            self.view.set_mode("select")

    # ------------------------------------------------------- live view ---
    def _draw_done(self, c, first, last):
        job = self.exec_job
        if job is None:
            return
        for k in range(first, last):
            _n, li, pi = self.exec_marks[k]
            L = job.layers[li]
            col = layer_color(L.name, c.dark) if L.kind != "raster" else c.pal["trail"]
            st, pr = L.paths[pi]
            for poly in burn_polylines(st, pr, 8):
                if len(poly) >= 2:
                    c.create_line(c.vp.to_screen(np.array(poly)), fill=col, width=2, tags=("done", "job"))
        c.tag_raise("head")

    def clear_progress(self):
        self.exec_job = None
        self.exec_marks = []
        self.done_n = 0
        self.view.delete("done")
