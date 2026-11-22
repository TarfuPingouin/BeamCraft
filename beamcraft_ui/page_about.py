# BeamCraft - About page
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later

import webbrowser
from tkinter import ttk

from beamcraft_core import APP_NAME, APP_VERSION

from .theme import APP, Card

URL = "https://github.com/TarfuPingouin/BeamCraft"


class AboutPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=20)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        card = Card(self, pad=28)
        card.grid(row=0, column=0)
        b = card.body
        if "logo_big" in app._icons:
            ttk.Label(b, image=app._icons["logo_big"], style="Panel.TLabel").pack(pady=(0, 14))
        else:
            ttk.Label(b, text=APP_NAME, style="Panel.Title.TLabel").pack(pady=(0, 14))
        ttk.Label(b, text=f"{APP_NAME} {APP_VERSION}", style="Panel.Title.TLabel").pack()
        ttk.Label(b, text="SVG and images to G-code for Grbl v1.1 laser engravers and cutters.",
                  style="Panel.TLabel").pack(pady=(8, 2))
        ttk.Label(b, text="One layer per colour · kerf & tabs · raster engraving · laser control",
                  style="Panel.Muted.TLabel").pack(pady=2)
        ttk.Label(b, text="Free software, GNU GPL v3 - © 2026 Tarfu Air", style="Panel.Muted.TLabel").pack(pady=(10, 2))
        from . import theme
        ttk.Label(b, text=f"Interface theme: {theme.THEME_NAME}  ·  Sun Valley theme © rdbende (MIT)",
                  style="Panel.Muted.TLabel").pack(pady=(2, 2))
        link = ttk.Label(b, text=URL, style="Panel.TLabel", foreground=APP["accent"], cursor="hand2")
        link.pack(pady=(2, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(URL))
