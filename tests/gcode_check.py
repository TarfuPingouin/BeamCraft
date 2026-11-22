"""Tiny Grbl v1.1 G-code checker / simulator used by the tests."""

import math
import re

import numpy as np

GRBL_G = {"G0", "G1", "G2", "G3", "G4", "G10", "G17", "G18", "G19", "G20", "G21",
          "G28", "G30", "G38.2", "G38.3", "G38.4", "G38.5", "G40", "G43.1", "G49",
          "G53", "G54", "G55", "G56", "G57", "G58", "G59", "G80", "G90", "G91",
          "G91.1", "G92", "G92.1", "G93", "G94", "G28.1", "G30.1"}
GRBL_M = {"M0", "M1", "M2", "M30", "M3", "M4", "M5", "M7", "M8", "M9", "M56"}
WORDS = set("FIJKLNPRSTXYZ")


def parse(lines):
    """Returns list of dicts: {'line': n, 'words': {...}, 'g': [...], 'm': [...]}"""
    out = []
    for n, raw in enumerate(lines):
        txt = re.sub(r"\(.*?\)", "", raw.split(";", 1)[0]).strip().upper()
        if not txt:
            continue
        toks = re.findall(r"([A-Z])\s*([-+]?\d*\.?\d+)", txt)
        rest = re.sub(r"([A-Z])\s*([-+]?\d*\.?\d+)", "", txt).strip()
        assert not rest, f"line {n}: garbage {rest!r} in {raw!r}"
        g, m, w = [], [], {}
        for letter, val in toks:
            if letter == "G":
                code = "G" + (val.lstrip("0") or "0") if "." not in val else "G" + str(float(val)).rstrip("0").rstrip(".")
                g.append(code)
            elif letter == "M":
                m.append("M" + str(int(float(val))))
            else:
                assert letter in WORDS, f"line {n}: invalid word {letter}"
                assert letter not in w, f"line {n}: repeated word {letter}"
                w[letter] = float(val)
        for c in g:
            assert c in GRBL_G, f"line {n}: unsupported {c}"
        for c in m:
            assert c in GRBL_M, f"line {n}: unsupported {c}"
        out.append({"line": n, "raw": raw, "g": g, "m": m, "w": w})
    return out


def simulate(lines, arc_step_deg=2.0):
    """
    Returns dict with 'burn' (list of polylines while laser could fire),
    'state_changes' (lines with M3/M4/M5 after first motion), 'arc_errors'.
    """
    blocks = parse(lines)
    x = y = 0.0
    motion = "G0"
    s_val = 0.0
    spindle = "M5"
    burn = []
    cur = None
    first_motion_seen = False
    state_changes = []
    arc_errors = []
    for b in blocks:
        for c in b["g"]:
            if c in ("G0", "G1", "G2", "G3"):
                motion = c
        for c in b["m"]:
            if c in ("M3", "M4", "M5"):
                if first_motion_seen and c != "M5":
                    state_changes.append(b["raw"])
                spindle = c
        if "S" in b["w"]:
            s_val = b["w"]["S"]
        if "G10" in b["g"]:
            continue
        has_xy = "X" in b["w"] or "Y" in b["w"]
        if not has_xy:
            continue
        nx = b["w"].get("X", x)
        ny = b["w"].get("Y", y)
        first_motion_seen = True
        fire = motion != "G0" and spindle in ("M3", "M4") and s_val > 0
        if motion in ("G0", "G1"):
            pts = [complex(nx, ny)]
        else:
            i, j = b["w"].get("I", 0.0), b["w"].get("J", 0.0)
            cx, cy = x + i, y + j
            r0 = math.hypot(i, j)
            r1 = math.hypot(nx - cx, ny - cy)
            dr = abs(r0 - r1)
            if dr > 0.005 and (dr > 0.5 or dr > 0.001 * r0):
                arc_errors.append(b["raw"])
            if abs(nx - x) < 1e-9 and abs(ny - y) < 1e-9:
                arc_errors.append("FULL CIRCLE: " + b["raw"])
            a0 = math.atan2(y - cy, x - cx)
            a1 = math.atan2(ny - cy, nx - cx)
            sw = a1 - a0
            if motion == "G3":
                while sw <= 0:
                    sw += 2 * math.pi
            else:
                while sw >= 0:
                    sw -= 2 * math.pi
            n = max(2, int(abs(math.degrees(sw)) / arc_step_deg))
            pts = [complex(cx, cy) + r0 * complex(math.cos(a0 + sw * k / n), math.sin(a0 + sw * k / n))
                   for k in range(1, n)] + [complex(nx, ny)]
        if fire:
            if cur is None:
                cur = [complex(x, y)]
                burn.append(cur)
            cur.extend(pts)
        else:
            cur = None
        x, y = nx, ny
    return {"burn": burn, "state_changes": state_changes, "arc_errors": arc_errors,
            "end": (x, y), "spindle": spindle}


def _densify(poly, step=0.05):
    out = [poly[0]]
    for a, b in zip(poly[:-1], poly[1:]):
        n = max(1, int(abs(b - a) / step))
        out.extend(a + (b - a) * k / n for k in range(1, n + 1))
    return np.array(out)


def max_deviation(polys_a, polys_b):
    """Max distance from points of A (densified) to polylines B."""
    segs_a = []
    for p in polys_b:
        p = np.asarray(p)
        segs_a.append((p[:-1], p[1:]))
    A = np.concatenate([_densify(list(p)) for p in polys_a])
    S0 = np.concatenate([s[0] for s in segs_a])
    S1 = np.concatenate([s[1] for s in segs_a])
    d = S1 - S0
    L2 = np.maximum((d * d.conj()).real, 1e-18)
    worst = 0.0
    for pt in A:
        t = np.clip(((pt - S0) * d.conj()).real / L2, 0, 1)
        dist = np.abs(pt - (S0 + t * d)).min()
        worst = max(worst, dist)
    return worst
