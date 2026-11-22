# BeamCraft - layer list (custom drawn: same look on every OS)
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import tkinter as tk

from .theme import APP, FONTS, Tooltip, fmt_num

COLOR_NAMES = {"#000000": "Black", "#ff0000": "Red", "#0000ff": "Blue", "#00ff00": "Green",
               "#ffff00": "Yellow", "#ff00ff": "Magenta", "#00ffff": "Cyan", "#ffffff": "White",
               "#808080": "Grey", "#ffa500": "Orange", "#008000": "Green"}
MODE_TEXT = {"line": "Line", "fill": "Fill", "line+fill": "Line + Fill",
             "threshold": "Threshold", "dither": "Dithering", "grayscale": "Grayscale"}


def layer_name(key):
    if key == "image":
        return "Image"
    col = key.partition(":")[2]
    return COLOR_NAMES.get(col, col.upper())


class LayerList(tk.Frame):
    """Rows: swatch | name + mode | power · speed | output toggle."""

    def __init__(self, parent, on_select, on_toggle):
        super().__init__(parent, bg=APP["card"])
        self.columnconfigure(0, weight=1)
        self.on_select = on_select
        self.on_toggle = on_toggle
        self.rows = {}
        self.selected = None

    def set_layers(self, items):
        """items: list of (key, LayerSettings) in output order."""
        for w in self.winfo_children():
            w.destroy()
        self.rows = {}
        if not items:
            tk.Label(self, text="No layer yet: open a file.", bg=APP["card"], fg=APP["muted"],
                     font=FONTS.get("small")).grid(row=0, column=0, sticky="w", pady=6)
            return
        for i, (key, L) in enumerate(items):
            self._row(i, key, L)
        if self.selected not in self.rows:
            self.selected = items[0][0]
        self._paint()

    def _row(self, i, key, L):
        on = L.enabled
        row = tk.Frame(self, bg=APP["card"], cursor="hand2")
        row.grid(row=i, column=0, sticky="ew", pady=1)
        row.columnconfigure(2, weight=1)
        bar = tk.Frame(row, bg=APP["card"], width=3)
        bar.grid(row=0, column=0, rowspan=2, sticky="ns")
        sw = tk.Canvas(row, width=18, height=18, highlightthickness=0, bd=0, bg=APP["card"])
        sw.grid(row=0, column=1, rowspan=2, padx=(8, 10), pady=6)
        col = key.partition(":")[2] if key != "image" else None
        if key == "image":
            for k in range(4):
                g = 70 + k * 50
                sw.create_rectangle(1 + k * 4, 1, 5 + k * 4, 17, fill=f"#{g:02x}{g:02x}{g:02x}", outline="")
            sw.create_rectangle(1, 1, 17, 17, outline="#8b8f97")
        elif key.startswith("fill:"):
            sw.create_rectangle(1, 1, 17, 17, fill=col, outline="#8b8f97")
        else:
            sw.create_rectangle(2, 2, 16, 16, outline=col, width=3)
        fg = APP["fg"] if on else APP["muted"]
        name = tk.Label(row, text=layer_name(key), bg=APP["card"], fg=fg, font=FONTS.get("section"), anchor="w")
        name.grid(row=0, column=2, sticky="w", pady=(5, 0))
        kind = "image" if key == "image" else ("fill colour" if key.startswith("fill:") else "stroke colour")
        mode = MODE_TEXT.get(L.image_mode if key == "image" else L.mode, "")
        sub = tk.Label(row, text=f"{mode}  ·  {kind}" if on else "not output", bg=APP["card"],
                       fg=APP["muted"], font=FONTS.get("small"), anchor="w")
        sub.grid(row=1, column=2, sticky="w", pady=(0, 5))
        val = tk.Label(row, text=f"{fmt_num(L.power_pct)} %\n{fmt_num(L.feed)}", bg=APP["card"], fg=fg,
                       font=FONTS.get("small"), justify="right", anchor="e")
        val.grid(row=0, column=3, rowspan=2, sticky="e", padx=(4, 8))
        tog = tk.Canvas(row, width=30, height=16, highlightthickness=0, bd=0, bg=APP["card"], cursor="hand2")
        tog.grid(row=0, column=4, rowspan=2, padx=(0, 8))
        self._draw_toggle(tog, on)
        Tooltip(tog, "Output this layer on / off")
        widgets = (row, bar, sw, name, sub, val)
        for w in widgets:
            w.bind("<Button-1>", lambda e, k=key: self._click(k))
            w.bind("<Double-Button-1>", lambda e, k=key: self.on_toggle(k))
            w.bind("<Enter>", lambda e, k=key: self._hover(k, True))
            w.bind("<Leave>", lambda e, k=key: self._hover(k, False))
        tog.bind("<Button-1>", lambda e, k=key: self.on_toggle(k))
        self.rows[key] = (widgets, tog)

    def _draw_toggle(self, c, on):
        c.delete("all")
        bg = APP["accent"] if on else "#4a4d55"
        c.create_oval(0, 0, 16, 16, fill=bg, outline="")
        c.create_oval(14, 0, 30, 16, fill=bg, outline="")
        c.create_rectangle(8, 0, 22, 16, fill=bg, outline="")
        x = 22 if on else 8
        c.create_oval(x - 6, 2, x + 6, 14, fill="#ffffff" if on else "#c9ccd2", outline="")

    def _click(self, key):
        self.selected = key
        self._paint()
        self.on_select(key)

    def _hover(self, key, inside):
        if key == self.selected:
            return
        self._color(key, APP["card2"] if inside else APP["card"])

    def _color(self, key, bg, bar=None):
        widgets, tog = self.rows[key]
        for w in widgets:
            w.configure(bg=bg)
        widgets[1].configure(bg=bar or bg)
        tog.configure(bg=bg)

    def _paint(self):
        for key in self.rows:
            if key == self.selected:
                self._color(key, APP["sel"], APP["accent"])
            else:
                self._color(key, APP["card"])

    def select(self, key):
        if key in self.rows:
            self.selected = key
            self._paint()
