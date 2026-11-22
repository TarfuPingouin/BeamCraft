# BeamCraft - raster (bitmap) engraving
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Raster engraving of bitmap images (SVG <image> elements or PNG/JPG/BMP files).

The image is resampled on a regular grid aligned with the machine X axis
(one engraving line every `line_interval` mm, square dots), whatever its
rotation / flip / transform.  Each line is engraved at constant speed:
    * overscan: the head starts and stops `overscan` mm outside the image
      with the laser off (S0), so the burn is not darker where the axis
      accelerates;
    * runs of identical power are merged into one G1 move, power changes are
      written as S words on G1 lines (Grbl laser mode keeps moving);
    * bidirectional engraving (zig-zag) halves the travel.

Modes:
    threshold   black / white with a threshold
    dither      Floyd-Steinberg dithering (best for photos on diode lasers)
    grayscale   power proportional to darkness, between min and max power
"""

import base64
import io
import math
import os
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import numpy as np

try:
    from PIL import Image
except ImportError:   # pragma: no cover
    Image = None

BITMAP_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")


@dataclass
class DocImage:
    image: object          # PIL image, mode "L" (white background)
    matrix: np.ndarray     # 3x3: pixel coords (continuous, pixel k = [k, k+1]) -> page mm (Y down)
    name: str = ""

    @property
    def size(self):
        return self.image.size


@dataclass
class RasterPreview:
    image: object          # PIL "L" image: 0 = no burn, 255 = full power
    x0: float              # machine coords of the left edge
    y1: float              # machine coords of the TOP edge (row 0)
    dx: float
    dy: float

    def shifted(self, ref):
        return RasterPreview(self.image, self.x0 - ref.real, self.y1 - ref.imag, self.dx, self.dy)


@dataclass
class RasterLayer:
    settings: object
    paths: list
    previews: list
    bounds_pts: list


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _to_gray(img):
    """Any PIL image -> 'L' on a white background."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg
    return img.convert("L")


def open_href(href, base_dir):
    """Returns a PIL image from a data URI or a (relative) file path, or None."""
    if Image is None or not href:
        return None
    try:
        if href.startswith("data:"):
            header, _, data = href.partition(",")
            raw = base64.b64decode(data) if ";base64" in header else unquote(data).encode("latin-1")
            img = Image.open(io.BytesIO(raw))
        else:
            if href.startswith("file:"):
                path = unquote(urlparse(href).path)
                if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
                    path = path[1:]
            else:
                path = os.path.join(base_dir, unquote(href))
            img = Image.open(path)
        img.load()
        return _to_gray(img)
    except Exception:
        return None


def image_matrix(img_w, img_h, x, y, w, h, par="xMidYMid meet"):
    """Pixel -> user units matrix for an <image> placed in the (x, y, w, h) box."""
    sx, sy = w / img_w, h / img_h
    tx, ty = x, y
    p = (par or "xMidYMid meet").split()
    if p and p[0] != "none":
        s = min(sx, sy) if (len(p) < 2 or p[1] == "meet") else max(sx, sy)
        align = p[0]
        fx = 0.0 if "xMin" in align else (1.0 if "xMax" in align else 0.5)
        fy = 0.0 if "YMin" in align else (1.0 if "YMax" in align else 0.5)
        tx += (w - img_w * s) * fx
        ty += (h - img_h * s) * fy
        sx = sy = s
    return np.array([[sx, 0, tx], [0, sy, ty], [0, 0, 1.0]])


def load_bitmap(path, dpi=300):
    """Opens a PNG/JPG/... file as a document containing one image."""
    from .svgload import SvgDocument
    img = Image.open(path)
    img.load()
    file_dpi = img.info.get("dpi")
    use_dpi = dpi
    src = f"{dpi} DPI (setting)"
    if file_dpi:
        try:
            d = float(file_dpi[0])
            if 10 <= d <= 10000 and abs(d - 72.0) > 1e-6:   # 72 = often meaningless default
                use_dpi = d
                src = f"{d:g} DPI (from file)"
        except (TypeError, ValueError, IndexError):
            pass
    gray = _to_gray(img)
    mm = 25.4 / use_dpi
    m = np.array([[mm, 0, 0], [0, mm, 0], [0, 0, 1.0]])
    W, H = gray.size[0] * mm, gray.size[1] * mm
    return SvgDocument(path=path, page_w=W, page_h=H, shapes=[],
                       size_source=f"image {gray.size[0]}x{gray.size[1]} px @ {src}: {W:.2f} x {H:.2f} mm",
                       warnings=[], images=[DocImage(gray, m, os.path.basename(path))])


# --------------------------------------------------------------------------
# rasterisation
# --------------------------------------------------------------------------

def _adjust(gray, L):
    g = gray.astype(np.float32)
    if L.contrast:
        g = (g - 128.0) * (1.0 + L.contrast / 100.0) + 128.0
    if L.brightness:
        g = g + L.brightness * 2.55
    return np.clip(g, 0, 255)


def burn_map(gray, mask, L):
    """gray: float array 0..255 (white = 255). Returns power % per pixel (0 = off)."""
    g = _adjust(gray, L)
    if L.negative:
        g = 255.0 - g
    if L.image_mode == "threshold":
        p = np.where(g < L.threshold, L.power_pct, 0.0)
    elif L.image_mode == "dither":
        im = Image.fromarray(g.astype(np.uint8), "L").convert("1")   # Floyd-Steinberg
        p = np.where(np.asarray(im, dtype=bool), 0.0, L.power_pct)
    else:  # grayscale
        dark = 1.0 - g / 255.0
        p = L.min_power_pct + (L.power_pct - L.min_power_pct) * dark
        p = np.round(p * 2.0) / 2.0             # 0.5 % steps: fewer S changes
        p = np.where(dark <= 1.0 / 255.0, 0.0, p)   # pure white: laser off
    p = np.where(mask, p, 0.0)
    return p


def _affine(img, A, x0, y1, dx, dy, nx, ny, resample, fill):
    """Resample img on the machine grid: output pixel (i, j) = machine (x0 + i*dx, y1 - j*dy)."""
    inv = np.linalg.inv(A)
    data = (inv[0, 0] * dx, -inv[0, 1] * dy, inv[0, 0] * x0 + inv[0, 1] * y1 + inv[0, 2],
            inv[1, 0] * dx, -inv[1, 1] * dy, inv[1, 0] * x0 + inv[1, 1] * y1 + inv[1, 2])
    return img.transform((nx, ny), Image.AFFINE, data, resample=resample, fillcolor=fill)


def rasterize(doc_img, A, L):
    """
    doc_img: DocImage, A: pixel -> machine mm matrix.
    Returns (paths, preview, corners) for this image.
    """
    iw, ih = doc_img.size
    corners_px = np.array([[0, 0], [iw, 0], [iw, ih], [0, ih]], dtype=float)
    cm = [complex(A[0, 0] * x + A[0, 1] * y + A[0, 2], A[1, 0] * x + A[1, 1] * y + A[1, 2])
          for x, y in corners_px]
    xs = [c.real for c in cm]
    ys = [c.imag for c in cm]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    dx = dy = float(L.line_interval)
    nx = max(1, int(math.ceil((x1 - x0) / dx)))
    ny = max(1, int(math.ceil((y1 - y0) / dy)))
    if nx * ny > 40_000_000:
        raise ValueError("Image too large for this line interval (more than 40 Mpixels).")

    # If the image is much finer than the raster, reduce it first (area
    # average) so that bilinear sampling does not skip pixels.
    src, A_src = doc_img.image, A
    scale_px = math.hypot(A[0, 0], A[1, 0])          # mm per image pixel
    if scale_px < dx * 0.75:
        nw = max(1, int(round(iw * scale_px / dx)))
        nh = max(1, int(round(ih * scale_px / dx)))
        src = doc_img.image.resize((nw, nh), getattr(Image, "BOX", Image.BILINEAR))
        A_src = A @ np.array([[iw / nw, 0, 0], [0, ih / nh, 0], [0, 0, 1.0]])
    g = _affine(src, A_src, x0, y1, dx, dy, nx, ny, Image.BILINEAR, 255)
    mask = _affine(Image.new("L", doc_img.size, 255), A, x0, y1, dx, dy, nx, ny, Image.NEAREST, 0)
    gray = np.asarray(g, dtype=np.float32)
    msk = np.asarray(mask) > 127
    power = burn_map(gray, msk, L)

    preview = Image.fromarray(np.clip(power / max(L.power_pct, 1e-6) * 255.0, 0, 255).astype(np.uint8), "L")

    paths = []
    ov = float(L.overscan)
    flip = False
    # engrave from the bottom row (nearest to a bottom-left origin) upwards
    for j in range(ny - 1, -1, -1):
        row = power[j]
        nz = np.nonzero(row > 0)[0]
        if len(nz) == 0:
            continue
        i0, i1 = int(nz[0]), int(nz[-1])
        seg = row[i0:i1 + 1]
        # run boundaries
        change = np.nonzero(np.diff(seg) != 0)[0] + 1
        starts = np.concatenate(([0], change))
        ends = np.concatenate((change, [len(seg)]))
        y = y1 - (j + 0.5) * dy
        runs = [(x0 + (i0 + s0) * dx, x0 + (i0 + e0) * dx, float(seg[s0])) for s0, e0 in zip(starts, ends)]
        if L.hatch_bidirectional and flip:
            runs = [(b_, a_, p_) for a_, b_, p_ in reversed(runs)]
            sign = -1.0
        else:
            sign = 1.0
        xs_ = runs[0][0]
        xe_ = runs[-1][1]
        start = complex(xs_ - sign * ov, y)
        prims = []
        if ov > 0:
            prims.append(("O", complex(xs_, y)))
        for a_, b_, p_ in runs:
            prims.append(("P", complex(b_, y), p_) if p_ > 0 else ("O", complex(b_, y)))
        if ov > 0:
            prims.append(("O", complex(xe_ + sign * ov, y)))
        if ov <= 0:
            start = complex(xs_, y)
        paths.append((start, prims))
        flip = not flip
    return paths, RasterPreview(preview, x0, y1, dx, dy), cm


def build_raster_layer(doc, L, s, M, warnings):
    if Image is None:
        warnings.append("Pillow is not installed: images ignored.")
        return None
    paths, previews, pts = [], [], []
    for img in doc.images:
        A = M @ img.matrix
        try:
            p, prev, corners = rasterize(img, A, L)
        except ValueError as e:
            warnings.append(str(e))
            continue
        paths.extend(p)
        previews.append(prev)
        pts.extend(corners)
    if not paths:
        return None
    return RasterLayer(L, paths, previews, pts)
