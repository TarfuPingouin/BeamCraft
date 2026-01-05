# BeamCraft

**BeamCraft** is an open-source application that converts **SVG** files into **G-Code** compatible with **GRBL**, for:

- laser engravers  
- pen plotters  
- DIY CNC machines with a **CNC Shield**

Designed to be simple, clear — and **free forever**.

> ✔️ Open-source  
> ✔️ Built for GRBL  
> ✔️ Licensed under **GPL-3**

---

## ✨ Features

### 🖼️ SVG → G-Code
- Import SVG files
- SVG preview
- G-Code toolpath preview
- Keeps drawing position relative to the page (A4 / A3)

### ⚙️ Machine settings
- Origin at **bottom-left (0,0)**  
  X+ → right  
  Y+ → up
- DPI (pixels per inch)
- Cutting speed (mm/min)
- Travel speed (mm/min)

### 🔥 Laser / ✒️ Plotter modes

#### Laser mode
- engraving power ( % )
- cutting power ( % )
- engraving passes
- cutting passes
- color rules:
  - **black (#000000)** → engraving  
  - **red (#FF0000)** → cutting

#### Plotter mode
- servo control for pen-up / pen-down (PWM)

### 🧩 Transformations
- rotate 90° left / right
- flip horizontal / vertical
- hatching for filled areas:
  - enable / disable
  - spacing
  - direction
  - cross-hatching

### 🧭 GRBL origin
Optional (enabled by default): 
G10 L20 P1 X0
G10 L20 P1 Y0


Sets the working origin to (0,0) automatically.

---

## 🖥️ Installation

### ⚡ Pre-built executable

If you use the `.exe` version:

1. Download  
2. Run  
3. Enjoy 🙂

> No Python required.

---
