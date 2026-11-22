# BeamCraft - command line interface
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Usage examples:

    python beamcraft_cli.py drawing.svg                       -> drawing.gcode
    python beamcraft_cli.py drawing.svg -o out.nc --list-layers
    python beamcraft_cli.py photo.jpg --layer "image,image_mode=grayscale,power_pct=70"
    python beamcraft_cli.py box.svg --set dpi=96 --pos 100 80 --rotate 30 \\
        --layer "stroke:#ff0000,kerf=0.08,tabs_count=4,passes=2"
    python beamcraft_cli.py box.svg --set start_from=current --set origin="top left"

The user settings of the GUI are used by default (--no-user-settings to start
from the defaults, --settings FILE to use another settings file).
"""

import argparse
import os
import sys
from dataclasses import fields

from . import (APP_VERSION, load_svg, load_bitmap, BITMAP_EXT, build_job, write_gcode, View,
               Settings, load_settings)
from .settings import layer_label, validate_layer
from .simulate import estimate_job_time
from .toolpath import document_layers


def _convert(value, current):
    if isinstance(current, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(float(value))
    if isinstance(current, float):
        return float(value.replace(",", "."))
    return value


def _apply_set(obj, assignment, what):
    if "=" not in assignment:
        raise ValueError(f"{what}: expected name=value, got {assignment!r}")
    name, value = assignment.split("=", 1)
    name = name.strip()
    names = {f.name for f in fields(obj)}
    if name not in names or name in ("layers", "layer_order"):
        raise ValueError(f"{what}: unknown setting {name!r}")
    setattr(obj, name, _convert(value.strip().strip('"'), getattr(obj, name)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="beamcraft",
                                 description="SVG / image to G-code for Grbl v1.1 lasers (BeamCraft)")
    ap.add_argument("input", help="SVG or bitmap file (PNG, JPG, BMP...)")
    ap.add_argument("-o", "--output", help="output G-code file (default: input name + .gcode, '-' = stdout)")
    ap.add_argument("--settings", help="settings JSON file (default: the GUI settings)")
    ap.add_argument("--no-user-settings", action="store_true", help="start from the default settings")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="global setting, e.g. dpi=96, origin=\"top left\", laser_mode=M3")
    ap.add_argument("--layer", action="append", default=[], metavar="KEY,NAME=VALUE,...",
                    help="layer setting, e.g. \"stroke:#ff0000,power_pct=90,passes=2\" or \"image,image_mode=dither\"")
    ap.add_argument("--rotate", type=float, default=0.0, help="rotation counter-clockwise (degrees, any angle)")
    ap.add_argument("--pos", type=float, nargs=2, metavar=("X", "Y"),
                    help="position of the design centre on the work area (mm)")
    ap.add_argument("--scale", type=float, default=100.0, help="scale (%%)")
    ap.add_argument("--flip-h", action="store_true")
    ap.add_argument("--flip-v", action="store_true")
    ap.add_argument("--list-layers", action="store_true", help="print the layers and exit")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--version", action="version", version=f"BeamCraft {APP_VERSION}")
    a = ap.parse_args(argv)

    def say(*x):
        if not a.quiet:
            print(*x, file=sys.stderr)

    try:
        if a.no_user_settings:
            s = Settings()
        else:
            s, _ui = load_settings(a.settings)
        for item in a.set:
            _apply_set(s, item, "--set")
        s.validate()

        if a.input.lower().endswith(BITMAP_EXT):
            doc = load_bitmap(a.input, dpi=s.dpi)
        else:
            doc = load_svg(a.input, dpi=s.dpi, force_dpi=s.force_dpi, tol=min(0.005, s.tolerance / 4))

        for spec in a.layer:
            parts = [p.strip() for p in spec.split(",") if p.strip()]
            if not parts:
                continue
            key = parts[0].lower()
            L = s.layer(key)
            for item in parts[1:]:
                _apply_set(L, item, f"--layer {key}")
            errs = validate_layer(L)
            if errs:
                raise ValueError(f"{key}: " + " ".join(errs))

        present = document_layers(doc)
        if a.list_layers:
            print(f"{os.path.basename(a.input)}: {doc.size_source}")
            for key in s.ordered(list(present)):
                L = s.layer(key)
                mode = L.image_mode if key == "image" else L.mode
                print(f"  {key:<18} {layer_label(key):<16} {'ON ' if L.enabled else 'off'} {mode:<10} "
                      f"{L.power_pct:g}%  F{L.feed:g}  x{L.passes}  ({present[key]} element(s))")
            return 0

        pos = a.pos or (None, None)
        job = build_job(doc, s, View(a.rotate, a.flip_h, a.flip_v, pos[0], pos[1], a.scale))
        for w in job.warnings:
            say("warning:", w)
        if not any(L.paths for L in job.layers):
            say("error: nothing to engrave or cut with these settings.")
            return 2
        lines = write_gcode(job, s, a.input, APP_VERSION)
        out = a.output or os.path.splitext(a.input)[0] + ".gcode"
        text = "\n".join(lines) + "\n"
        if out == "-":
            sys.stdout.write(text)
        else:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        t, sim = estimate_job_time(job, s)
        b = job.bounds
        say(f"{len(lines)} lines -> {out}")
        say(f"size {b[2] - b[0]:.2f} x {b[3] - b[1]:.2f} mm, {job.n_paths} paths, "
            f"time {int(t) // 3600}h{int(t) % 3600 // 60:02d}m{int(t) % 60:02d}s"
            f"{'' if sim else ' (rough)'}")
        return 0
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
