# BeamCraft - Grbl v1.1 laser G-code writer
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
G-code written for Grbl v1.1 in laser mode ($32=1).

Anti "dotting" rules applied (see Grbl v1.1 Laser Mode wiki):
  * the laser is armed ONCE (M4 S0 or M3 S0) and turned off ONCE (M5) at the
    end: in laser mode any M3/M4/M5 state change forces Grbl to stop motion;
  * power is changed with the S word on the motion lines, never alone;
  * G0 is used between paths: Grbl disables the laser on G0 automatically;
  * M4 (dynamic power) is the default: power follows the real speed, so
    accelerations / decelerations in curves and corners do not burn dots;
  * curves are output as G2/G3 arcs (computed on-board by Grbl) instead of
    hundreds of tiny G1 segments that starve the planner buffer;
  * modal words (G1, F, S, unchanged X/Y) are not repeated and numbers are
    written without trailing zeros, to keep the serial stream short.
"""

import math
import os


from .settings import layer_label


def _label(L):
    if L.kind == "raster":
        return f"Image ({L.settings.image_mode})"
    return f"{layer_label(L.name)} ({L.settings.mode})"


def fmt(v, nd=3):
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


class _Emitter:
    def __init__(self):
        self.lines = []
        self.motion = None
        self.x = None
        self.y = None
        self.f = None
        self.s = None

    def raw(self, txt):
        self.lines.append(txt)

    def _xy(self, x, y):
        out = []
        xs, ys = fmt(x), fmt(y)
        if self.x is None or xs != fmt(self.x):
            out.append("X" + xs)
        if self.y is None or ys != fmt(self.y):
            out.append("Y" + ys)
        return out

    def _same_pos(self, x, y):
        return (self.x is not None and fmt(x) == fmt(self.x) and fmt(y) == fmt(self.y))

    def rapid(self, x, y):
        if self._same_pos(x, y):
            return
        words = []
        if self.motion != "G0":
            words.append("G0")
            self.motion = "G0"
        words += self._xy(x, y)
        self.raw(" ".join(words))
        self.x, self.y = float(fmt(x)), float(fmt(y))

    def line(self, x, y, s=None, f=None, force=False):
        if self._same_pos(x, y) and not force:
            return
        words = []
        if self.motion != "G1":
            words.append("G1")
            self.motion = "G1"
        words += self._xy(x, y)
        if not words or words == ["G1"]:
            return
        if s is not None and (self.s is None or fmt(s, 1) != fmt(self.s, 1)):
            words.append("S" + fmt(s, 1))
            self.s = s
        if f is not None and (self.f is None or fmt(f, 1) != fmt(self.f, 1)):
            words.append("F" + fmt(f, 1))
            self.f = f
        self.raw(" ".join(words))
        self.x, self.y = float(fmt(x)), float(fmt(y))

    def arc(self, x, y, cx, cy, ccw, s=None, f=None):
        # A G2/G3 whose (rounded) end point equals its start is a FULL circle
        # for Grbl: never emit that, use a straight line instead.
        if self._same_pos(x, y):
            return
        ex, ey = float(fmt(x)), float(fmt(y))
        sx, sy = self.x, self.y
        i = cx - sx
        j = cy - sy
        # Grbl rejects arcs whose start/end radii differ by > 0.005 mm AND
        # > 0.1 % of the radius (error 33). Check with the rounded values.
        r0 = math.hypot(float(fmt(i)), float(fmt(j)))
        r1 = math.hypot(ex - (sx + float(fmt(i))), ey - (sy + float(fmt(j))))
        if abs(r0 - r1) > 0.002 and abs(r0 - r1) > 0.0005 * r0:
            self.line(x, y, s, f)
            return
        g = "G3" if ccw else "G2"
        words = []
        if self.motion != g:
            words.append(g)
            self.motion = g
        words += self._xy(x, y)
        words += ["I" + fmt(i), "J" + fmt(j)]
        if s is not None and (self.s is None or fmt(s, 1) != fmt(self.s, 1)):
            words.append("S" + fmt(s, 1))
            self.s = s
        if f is not None and (self.f is None or fmt(f, 1) != fmt(self.f, 1)):
            words.append("F" + fmt(f, 1))
            self.f = f
        self.raw(" ".join(words))
        self.x, self.y = ex, ey


def _hms(sec):
    sec = int(round(sec))
    return f"{sec // 3600:d}h{(sec % 3600) // 60:02d}m{sec % 60:02d}s"


def write_gcode(job, settings, source_name="", version="", estimate=True, marks=None):
    """
    Returns the G-code lines.  If `marks` is a list, (line_count, layer_index,
    path_index) is appended after each path: it lets a sender know which path
    has been executed from the number of acknowledged lines.
    """
    s = settings
    e = _Emitter()
    e.raw(f"; BeamCraft {version} - Grbl v1.1 laser G-code")
    if source_name:
        e.raw(f"; Source: {os.path.basename(source_name)}")
    e.raw("; Requires Grbl laser mode: $32=1  (max power S = $30)")
    e.raw(f"; Power mode: {s.laser_mode} ({'dynamic' if s.laser_mode == 'M4' else 'constant'}), "
          f"max S{fmt(s.max_power, 1)}")
    if s.start_from == "current":
        e.raw(f"; Start: current laser position = {s.origin} of the job")
    else:
        e.raw("; Start: work-area coordinates (0,0 = work-area origin)")
    for L in job.layers:
        ls = L.settings
        e.raw(f"; {_label(L)}: {fmt(ls.power_pct, 1)}% "
              f"F{fmt(ls.feed, 1)} x{ls.passes} pass(es), {len(L.paths)} path(s), "
              f"{L.length / 1000.0:.2f} m")
    time_line = len(e.lines)
    e.raw("; Estimated time: ...")
    if s.rotary_enabled:
        e.raw(f"; Rotary {s.rotary_type}: Y scaled x{s.rotary_factor():.5f}")
    e.raw("")
    if s.start_from == "current":
        e.raw("G10 L20 P1 X0 Y0 ; current position becomes work zero (G54)")
    o = getattr(job, "ref", 0j)
    e.raw("G21 G90 G17 G94 ; mm, absolute, XY plane, feed in mm/min")
    e.raw("G91.1 ; arc centre I,J relative to start point")
    e.raw(f"{s.laser_mode} S0 ; arm laser ({'dynamic' if s.laser_mode == 'M4' else 'constant'} power), "
          "G0 moves keep it off")
    if s.air_assist:
        e.raw("M8 ; air assist on")

    for li, L in enumerate(job.layers):
        ls = L.settings
        power = s.max_power * ls.power_pct / 100.0
        feed = ls.feed
        for p in range(max(1, int(ls.passes))):
            e.raw(f"; --- {_label(L)} - pass {p + 1}/{ls.passes} ---")
            for pi, (start, prims) in enumerate(L.paths):
                start = start - o
                if s.rapid_g1:
                    if not e._same_pos(start.real, start.imag):
                        e.line(start.real, start.imag, s=0, f=s.travel_feed)
                else:
                    e.rapid(start.real, start.imag)
                for pr in prims:
                    end = pr[1] - o
                    if pr[0] == "L":
                        e.line(end.real, end.imag, s=power, f=feed)
                    elif pr[0] == "O":
                        e.line(end.real, end.imag, s=0, f=feed)
                    elif pr[0] == "P":
                        e.line(end.real, end.imag, s=s.max_power * pr[2] / 100.0, f=feed)
                    else:
                        c = pr[2] - o
                        e.arc(end.real, end.imag, c.real, c.imag, pr[3], s=power, f=feed)
                if marks is not None:
                    marks.append((len(e.lines), li, pi))

    e.raw("; --- end ---")
    if s.air_assist:
        e.raw("M9 ; air assist off")
    e.raw("M5 ; laser off")
    if s.return_to_origin:
        e.motion = None
        e.rapid(0.0, 0.0)
    e.raw("M2 ; end of program")
    if estimate:
        from .simulate import simulate_time
        t = simulate_time(e.lines, s)
        e.lines[time_line] = (f"; Estimated time: {_hms(t)} (Grbl planner simulation, "
                              f"$120/$121={s.accel_x:g}/{s.accel_y:g} mm/s2)")
    else:
        e.lines[time_line] = f"; Estimated time (without accelerations): {_hms(job.est_time_s)}"
    return e.lines
