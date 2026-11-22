# BeamCraft - Grbl motion planner simulation (job time estimate)
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Estimates the real duration of a G-code program on Grbl, taking into account
the machine settings:
    $110/$111  max rate X/Y (mm/min)    -> G0 speed, feed rate cap
    $120/$121  acceleration X/Y (mm/s2)
    $11        junction deviation (mm)  -> speed allowed in corners
    $12        arc tolerance (mm)       -> G2/G3 split into segments

Model (same principles as Grbl's planner.c):
    * every motion is a block with a nominal speed and an acceleration
      limited by the axes that move;
    * the speed through a junction is limited with the junction-deviation
      formula  v^2 = a * jd * sin(theta/2) / (1 - sin(theta/2));
    * backward then forward passes give trapezoidal / triangular profiles.
The planner look-ahead is assumed unlimited (Grbl uses 16 blocks) and the
serial link is assumed fast enough, so the result is a slightly optimistic,
but realistic, estimate (far better than length / feed).
"""

import math
import re

import numpy as np

_WORD = re.compile(r"([A-Z])([-+]?\d*\.?\d+)")


def _blocks(lines, s):
    """G-code -> list of (dx, dy, nominal_mm_s) straight blocks."""
    x = y = 0.0
    motion = 0
    feed = None
    rapid = min(s.max_rate_x, s.max_rate_y) / 60.0
    blocks = []
    for raw in lines:
        txt = re.sub(r"\(.*?\)", "", raw.split(";", 1)[0]).strip().upper()
        if not txt or txt.startswith("$"):
            continue
        w = {}
        gs = []
        for k, v in _WORD.findall(txt):
            if k == "G":
                gs.append(float(v))
            else:
                w[k] = float(v)
        for g in gs:
            if g in (0, 1, 2, 3):
                motion = int(g)
        if 10 in gs or 92 in gs:
            continue
        if "F" in w:
            feed = w["F"] / 60.0
        if "X" not in w and "Y" not in w:
            continue
        nx, ny = w.get("X", x), w.get("Y", y)
        v = rapid if motion == 0 else (feed or rapid)
        if motion in (0, 1):
            blocks.append((nx - x, ny - y, v))
        else:
            cx, cy = x + w.get("I", 0.0), y + w.get("J", 0.0)
            r = math.hypot(x - cx, y - cy)
            a0 = math.atan2(y - cy, x - cx)
            a1 = math.atan2(ny - cy, nx - cx)
            sw = a1 - a0
            if motion == 3:
                while sw <= 0:
                    sw += 2 * math.pi
            else:
                while sw >= 0:
                    sw -= 2 * math.pi
            tol = max(s.arc_tolerance, 1e-4)
            n = 1
            if r > tol:
                n = max(1, int(abs(0.5 * sw * r) / math.sqrt(tol * (2 * r - tol))))
            px, py = x, y
            for k in range(1, n + 1):
                if k == n:
                    qx, qy = nx, ny
                else:
                    a = a0 + sw * k / n
                    qx, qy = cx + r * math.cos(a), cy + r * math.sin(a)
                blocks.append((qx - px, qy - py, v))
                px, py = qx, qy
        x, y = nx, ny
    return blocks


def simulate_time(lines, s):
    """Returns the estimated duration in seconds (s = Settings)."""
    blocks = [b for b in _blocks(lines, s) if abs(b[0]) + abs(b[1]) > 1e-9]
    n = len(blocks)
    if n == 0:
        return 0.0
    arr = np.array(blocks, dtype=float)
    dx, dy, vnom = arr[:, 0], arr[:, 1], arr[:, 2]
    L = np.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    # axis limited acceleration and speed
    with np.errstate(divide="ignore"):
        acc = np.minimum(np.where(np.abs(ux) > 1e-9, s.accel_x / np.abs(ux), np.inf),
                         np.where(np.abs(uy) > 1e-9, s.accel_y / np.abs(uy), np.inf))
        vmax = np.minimum(np.where(np.abs(ux) > 1e-9, s.max_rate_x / 60.0 / np.abs(ux), np.inf),
                          np.where(np.abs(uy) > 1e-9, s.max_rate_y / 60.0 / np.abs(uy), np.inf))
    v = np.minimum(vnom, vmax)

    # junction speed limits (entry of block i)
    vj = np.zeros(n + 1)            # vj[i] = max speed at the start of block i; vj[n] = 0 (end)
    cos_t = -(ux[1:] * ux[:-1] + uy[1:] * uy[:-1])
    jd = s.junction_deviation
    lim = np.empty(n - 1)
    straight = cos_t < -0.999999
    sharp = cos_t > 0.999999
    mid = ~(straight | sharp)
    sin_d2 = np.sqrt(np.clip(0.5 * (1.0 - cos_t[mid]), 0, 1))
    a_j = np.minimum(acc[1:], acc[:-1])[mid]
    lim[mid] = np.sqrt(a_j * jd * sin_d2 / (1.0 - sin_d2))
    lim[straight] = np.inf
    lim[sharp] = 0.0
    vj[1:n] = np.minimum(lim, np.minimum(v[1:], v[:-1]))
    vj[0] = 0.0
    vj[n] = 0.0

    # backward pass
    for i in range(n - 1, -1, -1):
        vj[i] = min(vj[i], math.sqrt(vj[i + 1] ** 2 + 2 * acc[i] * L[i]))
    # forward pass
    for i in range(n):
        vj[i + 1] = min(vj[i + 1], math.sqrt(vj[i] ** 2 + 2 * acc[i] * L[i]))

    # trapezoid time of each block
    v0, v1 = vj[:-1], vj[1:]
    a = acc
    d_acc = np.maximum(0, (v ** 2 - v0 ** 2) / (2 * a))
    d_dec = np.maximum(0, (v ** 2 - v1 ** 2) / (2 * a))
    cruise = L - d_acc - d_dec
    t = np.empty(n)
    full = cruise >= 0
    t[full] = ((v[full] - v0[full]) / a[full] + (v[full] - v1[full]) / a[full] + cruise[full] / v[full])
    tri = ~full
    # triangular profile: peak speed
    vp = np.sqrt(np.maximum(0, (2 * a[tri] * L[tri] + v0[tri] ** 2 + v1[tri] ** 2) / 2))
    t[tri] = (vp - v0[tri]) / a[tri] + (vp - v1[tri]) / a[tri]
    return float(np.sum(t))


def estimate_job_time(job, settings, max_prims=400_000):
    """(seconds, simulated) - falls back to length / feed for huge jobs."""
    n = sum(len(pr) for L in job.layers for _st, pr in L.paths)
    if n > max_prims:
        return job.est_time_s, False
    from .gcode import write_gcode
    return simulate_time(write_gcode(job, settings, estimate=False), settings), True
