# BeamCraft - look & feel (sun-valley dark, darker background) and small widgets
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

APP = {
    "bg": "#18191c",        # window background
    "card": "#212226",      # panels
    "card2": "#2b2c31",     # hovered rows / inner panels
    "sel": "#2c3b4d",       # selected row
    "border": "#34363c",
    "fg": "#f2f2f2",
    "muted": "#a0a4ac",
    "accent": "#57c8ff",    # sun-valley accent
    "ok": "#3ddc84",
    "warn": "#f2c94c",
    "err": "#ff5f57",
    "info": "#57c8ff",
}

PREVIEW = {
    "dark": {"bg": "#1e1f23", "page": "#2a2c31", "page_border": "#4d525b", "travel": "#626975",
             "origin": "#3ddc84", "head": "#ff4d4f", "trail": "#ff9f0a", "bed": "#5b86b5",
             "grid": "#31333a", "grid_major": "#3c3f47", "text": "#9aa0a8", "sel": "#57c8ff"},
    "light": {"bg": "#e9ebee", "page": "#ffffff", "page_border": "#a8adb5", "travel": "#b8b8b8",
              "origin": "#00a000", "head": "#ff3b30", "trail": "#ff8c00", "bed": "#6a8caf",
              "grid": "#eef0f3", "grid_major": "#dde1e6", "text": "#6b7079", "sel": "#1f7fd1"},
}

STATE_COLORS = {"Idle": "#3ddc84", "Run": "#57c8ff", "Jog": "#57c8ff", "Hold": "#f2c94c",
                "Alarm": "#ff5f57", "Door": "#ff5f57", "Home": "#b38cff", "Check": "#b38cff",
                "Sleep": "#8b8f97", "Disconnected": "#4a4d55"}


def _lum(col):
    r, g, b = int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b, (r, g, b)


def draw_color(col, dark):
    """Colour of a layer on the preview: keeps contrast on dark and light backgrounds."""
    try:
        lum, (r, g, b) = _lum(col)
    except (ValueError, TypeError, IndexError):
        return "#9aa0a6"
    if dark:
        if lum < 70:                       # black / very dark -> light grey
            return "#ececec" if max(r, g, b) - min(r, g, b) < 40 else \
                "#{:02x}{:02x}{:02x}".format(*(min(255, int(c * 1.8 + 60)) for c in (r, g, b)))
        return col
    if lum > 200:
        return "#{:02x}{:02x}{:02x}".format(int(r * 0.6), int(g * 0.6), int(b * 0.6))
    return col


def layer_color(key, dark):
    col = key.partition(":")[2] or "#9aa0a6"
    return draw_color(col, dark)


# --------------------------------------------------------------------------
# fonts & styles
# --------------------------------------------------------------------------

FONTS = {}


THEME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes")
THEME_NAME = ""     # theme actually in use (shown in About, for diagnostics)


def _load_sun_valley(root, st):
    """Loads the bundled Sun Valley dark theme (MIT, rdbende). Returns True on success."""
    if "sun-valley-dark" not in st.theme_names():
        path = os.path.join(THEME_DIR, "sun-valley", "theme", "dark.tcl")
        if not os.path.isfile(path):
            return False
        try:
            root.tk.call("source", path)
        except tk.TclError:
            return False
    try:
        st.theme_use("sun-valley-dark")
    except tk.TclError:
        return False
    return True


def _clam_dark(st):
    """
    Fallback when the Sun Valley theme cannot be loaded: the 'clam' theme
    (available everywhere) with every background / text pair set here, so no
    widget can end up with light text on a light surface.
    """
    st.theme_use("clam")
    bg, card2, fg, border, acc = APP["bg"], APP["card2"], APP["fg"], APP["border"], APP["accent"]
    hover, press, dark_txt = "#383a40", "#43464d", "#0b0b0c"
    st.configure(".", background=bg, foreground=fg, fieldbackground=card2, troughcolor=bg,
                 bordercolor=border, lightcolor=card2, darkcolor=card2, focuscolor=acc,
                 selectbackground=acc, selectforeground=dark_txt, insertcolor=fg, arrowcolor=fg)
    st.map(".", foreground=[("disabled", "#6b6f78")])
    st.configure("TButton", background=card2, foreground=fg, padding=(10, 4), anchor="center")
    st.map("TButton", background=[("pressed", press), ("active", hover)], foreground=[("disabled", "#6b6f78")])
    st.configure("Accent.TButton", background=acc, foreground=dark_txt)
    st.map("Accent.TButton", background=[("pressed", "#3fb2ea"), ("active", "#7fd6ff")],
           foreground=[("disabled", "#2b4a5a")])
    st.configure("Toggle.TButton", background=card2, foreground=fg, padding=(10, 4), anchor="center")
    st.map("Toggle.TButton", background=[("selected", acc), ("pressed", press), ("active", hover)],
           foreground=[("selected", dark_txt)])
    for n in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(n, fieldbackground=card2, foreground=fg, background=card2, insertcolor=fg, arrowcolor=fg)
        st.map(n, fieldbackground=[("invalid", "#4a2326"), ("readonly", card2)],
               foreground=[("readonly", fg)], bordercolor=[("invalid", APP["err"]), ("focus", acc)])
    for n in ("TCheckbutton", "TRadiobutton"):
        st.configure(n, indicatorbackground=card2, indicatorforeground=dark_txt)
        st.map(n, indicatorbackground=[("selected", acc)])
    st.configure("TScrollbar", background=card2, troughcolor=bg, arrowcolor=fg)
    st.configure("TProgressbar", background=acc, troughcolor=card2)
    st.configure("TSeparator", background=border)
    # clam imposes a minimum button width: let the small buttons take their natural size
    for n in ("Nav.Toggle.TButton", "Small.TButton", "Tool.TButton", "Aim.Toggle.TButton", "Pad.TButton"):
        st.configure(n, width=0)


def apply_theme(root):
    global THEME_NAME
    st = ttk.Style(root)
    if _load_sun_valley(root, st):
        THEME_NAME = "sun-valley-dark (bundled)"
    else:
        _clam_dark(st)
        THEME_NAME = "clam dark (fallback)"

    base = tkfont.nametofont("TkDefaultFont")
    fam = base.actual("family")
    if sys.platform.startswith("win") and "Segoe UI" in tkfont.families(root):
        fam = "Segoe UI"
        base.configure(family=fam, size=10)
    size = base.actual("size") or 10
    mono = "Consolas" if "Consolas" in tkfont.families(root) else "TkFixedFont"
    FONTS.update({
        "base": base,
        "small": tkfont.Font(root, family=fam, size=max(8, size - 1)),
        "title": tkfont.Font(root, family=fam, size=size + 1, weight="bold"),
        "section": tkfont.Font(root, family=fam, size=size, weight="bold"),
        "dro": tkfont.Font(root, family=mono, size=size + 7, weight="bold"),
        "dro_small": tkfont.Font(root, family=mono, size=size),
        "mono": tkfont.Font(root, family=mono, size=max(8, size - 1)),
    })
    bg, card, fg, muted = APP["bg"], APP["card"], APP["fg"], APP["muted"]
    # Colour rule: a text colour is only set where the background is set too.
    # Buttons, entries and comboboxes are drawn by the theme: their text colour
    # is left to the theme (it matches its own button images).
    root.configure(bg=bg)
    root.tk_setPalette(background=bg, foreground=fg, highlightColor=APP["accent"],
                       selectBackground=APP["accent"], selectForeground="#0b0b0c",
                       activeBackground=APP["card2"], activeForeground=fg)
    st.configure(".", background=bg, foreground=fg, font=base)
    for name in ("TFrame", "TNotebook", "TLabelframe", "Header.TFrame", "Status.TFrame"):
        st.configure(name, background=bg)
    for name in ("TLabel", "TCheckbutton", "TRadiobutton", "Switch.TCheckbutton", "TLabelframe.Label"):
        st.configure(name, background=bg, foreground=fg)
        st.map(name, foreground=[("disabled", muted)], background=[("active", bg)])
    st.configure("Panel.TFrame", background=card)
    for name in ("TLabel", "TCheckbutton", "TRadiobutton", "Switch.TCheckbutton"):
        st.configure("Panel." + name, background=card, foreground=fg)
        st.map("Panel." + name, foreground=[("disabled", muted)], background=[("active", card)])
    st.configure("Muted.TLabel", background=bg, foreground=muted, font=FONTS["small"])
    st.configure("Panel.Muted.TLabel", background=card, foreground=muted, font=FONTS["small"])
    st.configure("Title.TLabel", background=bg, foreground=fg, font=FONTS["title"])
    st.configure("Panel.Title.TLabel", background=card, foreground=fg, font=FONTS["section"])
    st.configure("Panel.DRO.TLabel", background=card, foreground=fg, font=FONTS["dro"])
    st.configure("Panel.DROsmall.TLabel", background=card, foreground=muted, font=FONTS["dro_small"])
    st.configure("Status.TLabel", background=bg, foreground=muted, font=FONTS["small"])
    # button sizes only (no colours)
    st.configure("Nav.Toggle.TButton", padding=(14, 4))
    st.configure("Small.TButton", padding=(6, 2))
    st.configure("Tool.TButton", padding=(6, 3))
    st.configure("Chip.Toggle.TButton", padding=(6, 1), width=0)
    st.configure("Aim.Toggle.TButton", padding=(8, 3))
    st.configure("Pad.TButton", padding=(4, 4), font=FONTS["section"])
    return st


# --------------------------------------------------------------------------
# widgets
# --------------------------------------------------------------------------

class Card(tk.Frame):
    """Rounded-looking panel with an optional title; content goes in .body."""

    def __init__(self, parent, title=None, pad=10, **kw):
        super().__init__(parent, bg=APP["card"], highlightthickness=1,
                         highlightbackground=APP["border"], highlightcolor=APP["border"], bd=0, **kw)
        self.columnconfigure(0, weight=1)
        r = 0
        if title:
            self.header = ttk.Frame(self, style="Panel.TFrame")
            self.header.grid(row=0, column=0, sticky="ew", padx=pad, pady=(pad - 2, 0))
            self.header.columnconfigure(0, weight=1)
            ttk.Label(self.header, text=title, style="Panel.Title.TLabel").grid(row=0, column=0, sticky="w")
            r = 1
        self.body = ttk.Frame(self, style="Panel.TFrame")
        self.body.grid(row=r, column=0, sticky="nsew", padx=pad, pady=(6 if title else pad, pad))
        self.rowconfigure(r, weight=1)


class ScrollFrame(ttk.Frame):
    """Vertical scrolling container; put widgets in .inner."""

    def __init__(self, parent, bg=None, **kw):
        super().__init__(parent, **kw)
        bg = bg or APP["bg"]
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)

    def _on_inner(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_bar()

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        self._update_bar()

    def _update_bar(self):
        need = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1
        if need:
            self.vsb.grid()
        else:
            self.vsb.grid_remove()
            self.canvas.yview_moveto(0)

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel, add=False)
        self.canvas.bind_all("<Button-4>", self._wheel, add=False)
        self.canvas.bind_all("<Button-5>", self._wheel, add=False)

    def _unbind_wheel(self, _e=None):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _wheel(self, e):
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if getattr(e, "num", None) == 4 or getattr(e, "delta", 0) > 0:
            self.canvas.yview_scroll(-2, "units")
        else:
            self.canvas.yview_scroll(2, "units")


class Toast:
    """Small notification in the bottom-right corner, fades away."""

    def __init__(self, root):
        self.root = root
        self._items = []

    def show(self, text, kind="info", ms=3200):
        col = APP.get(kind, APP["info"])
        f = tk.Frame(self.root, bg=APP["card2"], highlightthickness=1, highlightbackground=col)
        tk.Frame(f, bg=col, width=4).pack(side="left", fill="y")
        tk.Label(f, text=text, bg=APP["card2"], fg=APP["fg"], justify="left", wraplength=380,
                 padx=12, pady=8, font=FONTS.get("base")).pack(side="left")
        self._items.append(f)
        self._layout()
        f.bind("<Button-1>", lambda e: self._close(f))
        for c in f.winfo_children():
            c.bind("<Button-1>", lambda e: self._close(f))
        self.root.after(ms, lambda: self._close(f))

    def _layout(self):
        y = -40
        for f in reversed(self._items):
            f.place(relx=1.0, rely=1.0, x=-16, y=y, anchor="se")
            f.lift()
            f.update_idletasks()
            y -= f.winfo_reqheight() + 8

    def _close(self, f):
        if f in self._items:
            self._items.remove(f)
            f.destroy()
            self._layout()


class Tooltip:
    def __init__(self, widget, text, delay=600):
        self.w, self.text, self.delay = widget, text, delay
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._job = self.w.after(self.delay, self._show)

    def _show(self):
        if self.tip or not self.w.winfo_exists():
            return
        x = self.w.winfo_rootx() + 8
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=APP["card2"], fg=APP["fg"], padx=8, pady=4,
                 highlightthickness=1, highlightbackground=APP["border"],
                 font=FONTS.get("small")).pack()

    def _hide(self, _e=None):
        if self._job:
            self.w.after_cancel(self._job)
            self._job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def pill(parent, textvariable, width=11):
    """Coloured state label (tk.Label so its background can change)."""
    return tk.Label(parent, textvariable=textvariable, width=width, fg="#0b0b0c",
                    bg=STATE_COLORS["Disconnected"], font=FONTS.get("section"), padx=6, pady=2)


def set_pill(lbl, state):
    key = (state or "Disconnected").split(":")[0]
    col = STATE_COLORS.get(key, "#8b8f97")
    lbl.configure(bg=col, fg="#0b0b0c" if key != "Disconnected" else APP["fg"])


def fmt_num(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def parse_num(txt, cast=float):
    t = str(txt).strip().replace(",", ".")
    v = float(t)
    return int(v) if cast is int else v
