# BeamCraft

**BeamCraft** is an open-source application that converts **SVG** files into **G-Code** for
**Grbl v1.1 laser engravers / cutters** (Arduino + CNC Shield and compatible boards).

Designed to be simple, clear — and **free forever**.

> ✔️ Open-source  
> ✔️ Built for Grbl v1.1 laser mode  
> ✔️ Licensed under **GPL-3**

---

## ✨ Features

### 🖼️ Import
- **SVG** (Affinity Designer, Illustrator, Inkscape, ...): group / layer **transforms**, inherited
  styles, `<use>` clones, CSS classes, hidden layers
- **Text** converted to outlines with the fonts installed on the computer (family or PostScript
  name, bold / italic, variable fonts, `tspan`, per-character positions, `text-anchor`)
- **Bitmaps**: `<image>` in the SVG (embedded or linked) or PNG / JPG / BMP / GIF / TIFF / WEBP files
- Real size: physical units (`mm`, `cm`, `in`, `pt`) when present, otherwise a **DPI** value
  (Affinity Designer exports `width="100%"` + a viewBox in pixels)

### 🎨 Layers by colour
- One layer per **stroke colour** (L) and per **fill colour** (F), plus one **Image** layer
- Mode **line**, **fill** (hatching) or **line + fill**, own **power / speed / passes**, output on/off
- Output order chosen by the user (default: images, fills, engraving, cuts last)
- Layer settings are remembered per colour between files
- Defaults: black = engrave, red = cut, other stroke colours off, filled shapes hatched

### ✂️ Cutting
- **Kerf offset**: parts grow, holes shrink (nesting detection), round corners
- **Tabs / bridges**: number per contour, width, minimum contour length, outer contours only
- Inner contours always cut before the outer ones

### ▦ Fill (hatching)
- Correct **holes** (letters, compound shapes, `fill-rule`), **merge of overlapping shapes**
- Spacing, any angle, cross-hatching, bidirectional, **overscan** (laser off outside the area)

### 📷 Raster engraving
- Modes **threshold**, **dithering** (Floyd–Steinberg), **grayscale** (min / max power)
- Line interval, overscan, bidirectional, negative, brightness / contrast, any rotation

### 🔥 G-code for Grbl v1.1 (no "dotting" in curves)
- Laser armed once with **M4 (dynamic power)** or M3, power with `S` on the motion lines,
  `G0` between paths, **no M3/M4/M5 in the middle of the job**
- Curves fitted with **G2/G3 arcs** and merged lines (tolerance 0.02 mm)
- Compact modal output, optional air assist (`M8` / `M9`)
- **Time estimate** with a simulation of Grbl's planner (accelerations `$120/$121`,
  junction deviation `$11`, arcs `$12`)

### 🧭 Work area & placement
- Work area of your size (Settings ▸ Work area), drawn with a millimetre grid
- Place the design **directly on the work area**: drag to move, corners to resize, round handle
  to **rotate at any angle** (Shift = 15° steps), or type X / Y / W / H / angle / scale
- Job start: **work-area coordinates** (G-code in work-area coordinates, the job is engraved where
  it is placed) or **current laser position** (`G10 L20`, anchor = corner or center of the job)
- **Rotary** axis (roller or chuck): Y scaled from the diameter and the Y travel per rotation

### ◎ Aiming dot
- *Aim* mode (Files or Machine page): click on the work area, the head goes there (laser off) then
  lights a **low-power dot** to check the position physically
- Never above 5 % of `$30` (default 1 %), automatic switch-off (default 20 s), Esc / jog / job = off
- ⚠ For users wearing laser safety glasses suited to their laser's wavelength

### 🖥️ Interface
- Dark theme (sun-valley), compact header, pages **Files · Machine · Settings · About** in one window
- One central view: the work area (dark or light), instant zoom at the cursor, right-drag pan,
  computations in the background
- Layers list (colour, mode, power, speed, on/off) with an inline editor applied automatically

### 🔌 Laser control (Grbl v1.1)
- USB serial or network (`socket://ip:port`, ESP32 boards)
- **Send the job** with the character-counting protocol (Grbl's 128-byte buffer always full)
- **Live view** (like CNCjs): the laser head moves smoothly on the job preview, executed paths
  are drawn in full colour, Ctrl+click = go to point
- **Jog pad**: Y+ / Y− / X− / X+ around a ⌂ home button, step presets, arrow keys
- **Move** panel: X / Y distances + Go = relative move (X 10 → 10 mm to the right)
- Home, unlock, zero X / Y / XY, go to zero, **framing** (laser off), pause / resume / stop
  (feed hold then reset: the position is kept)
- **Live overrides** of speed and power, **console**, machine settings read from `$$`,
  laser head position shown on the preview

### ⌨️ Command line
```
python beamcraft_cli.py drawing.svg -o drawing.gcode
python beamcraft_cli.py drawing.svg --list-layers
python beamcraft_cli.py box.svg --layer "stroke:#ff0000,kerf=0.08,tabs_count=4,passes=2" --pos 150 100 --rotate 30
python beamcraft_cli.py photo.jpg --layer "image,image_mode=grayscale,power_pct=70" --set dpi=254
```
Uses the settings of the GUI (or `--no-user-settings`, `--settings file.json`).

---

## ⚙️ Grbl setup

```
$32=1      (laser mode - REQUIRED)
$30=1000   (max spindle/laser value; must match "Max. laser power (S)")
$31=0
```
Settings ▸ Machine dynamics ▸ *Read from the machine ($$)* copies `$30 $110 $111 $120 $121 $11 $12`
and warns if `$32=0`.

## 🎨 Exporting from Affinity Designer

`File ▸ Export ▸ SVG`, then *More*:
- Note the document DPI (*Use DPI*) and enter the same value in *Settings ▸ SVG DPI* (default 300)
- Text can stay as text if the fonts are installed; otherwise use *Export text as curves*
- Colours become layers: use one colour per operation (e.g. black engrave, red cut)

---

## 🖥️ Installation

### ⚡ Pre-built executable

If you use the `.exe` version:

1. Download
2. Run
3. Enjoy 🙂

> No Python required.

### 🐍 From source (Python ≥ 3.10)

```
pip install -r requirements.txt
python beamcraft.py [file.svg]
```

Shortcuts: `Ctrl+O` open, `F5` reload, `Ctrl+G` save G-code, `Ctrl+1…4` pages.
Previews: mouse wheel = zoom at cursor, drag = pan, double-click = reset view.

### 📦 Building the executable

```
pip install pyinstaller
pyinstaller --onefile --windowed --name BeamCraft --icon icone.ico ^
  --add-data "icone.ico;." --add-data "logo_wht.png;." ^
  --add-data "flipH.png;." --add-data "flipV.png;." ^
  --add-data "rotateCW.png;." --add-data "rotateCCW.png;." ^
  --add-data "home.png;." --add-data "aim.png;." --add-data "pause.png;." ^
  --add-data "play.png;." --add-data "stop.png;." ^
  --add-data "beamcraft_ui/themes;beamcraft_ui/themes" ^
  --hidden-import fontTools.varLib.instancer beamcraft.py
```
(on Linux / macOS replace `;` by `:` and `^` by `\`)

### 🧪 Tests

```
pip install pytest
python -m pytest tests
```

---

## 📝 Changelog

### v1.0.2
- The Sun Valley dark theme (MIT, rdbende) is bundled and loaded by BeamCraft itself: the
  TKinterModernThemes package is no longer needed. If it cannot be loaded, a complete dark
  fallback theme is used. Text colours are only set where the background is set too
  (fixes unreadable button texts on Windows)
- About page shows the interface theme in use

### v1.0.1
- Readable texts on Windows (explicit text colour on every style)
- New icons in the style of the original ones: home, aim, pause, resume, stop
  (`tools/make_icons.py`)

### v1.0.0
- One central view: the work area (size in Settings) with grid; the design is placed directly on
  it (move, resize, free rotation) or with the Placement fields
- G-code in work-area coordinates by default, or from the current laser position
- Aiming dot: click a point, the head goes there and lights a low-power dot
- Machine page: jog pad (Y+ / Y− / X− / X+, home), relative Move panel
- New layers list (readable on every OS), lighter colours

### v0.3.1
- New interface: darker theme, compact header, pages instead of windows, narrower layers
  panel with inline editor, dark / light previews, smoother previews (background computation)
- Machine page: position display, jog per axis in mm, live view with the moving head and
  the executed paths

### v0.3.0
- Kerf offset and tabs / bridges
- Layers by colour (line / fill / line+fill, per-layer settings, order)
- Raster image engraving (threshold, dithering, grayscale)
- Laser control: streaming, jog, framing, overrides, console
- Grbl planner time simulation, merge of overlapping fills, SVG text,
  rotary axis, scale / offset / bed, command line interface

### v0.2.0
- Pen plotter mode removed: BeamCraft is now laser-only
- New Grbl v1.1 laser G-code writer (M4 dynamic power, G2/G3 arc fitting, modal output)
  that removes the dotting in curves
- New SVG loader: transforms, inherited styles, `<use>`, CSS, physical units
- Fixed: rotations were mirrored between preview and G-code
- Fixed: ellipses were output as circles; near-360° arcs
- Fixed: shapes without stroke were engraved, unknown colours were burned at 100 %
- Fixed: hatching of shapes with holes
- Fixed: page frame not rotated in the preview
- Per-layer speed / power / passes, configurable origin, air assist, settings saved,
  path ordering, time estimate, warnings (ignored colours, text, images)
