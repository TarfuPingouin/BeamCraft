import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from beamcraft_core import load_svg, Settings, build_job, write_gcode, View  # noqa: E402
from beamcraft_core.toolpath import machine_matrix, placement, _apply  # noqa: E402
from beamcraft_core.geometry import fit_polyline, discretize  # noqa: E402
from beamcraft_core.hatch import hatch_rings  # noqa: E402
from beamcraft_core.colors import parse_color, classify  # noqa: E402
from beamcraft_core.settings import load_settings, save_settings  # noqa: E402
import gcode_check  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def write(tmp_path, name, txt):
    p = tmp_path / name
    p.write_text(txt, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- units ----

def test_affinity_pixels_use_dpi():
    d = load_svg(os.path.join(FIX, "affinity_like.svg"), dpi=300)
    assert d.page_w == pytest.approx(1181 * 25.4 / 300)
    # circle r=118.11px @300dpi = 10 mm, centre (200+100, 200+100) px
    circle = d.shapes[0]
    pts = circle.rings[0][0]
    c = complex((pts.real.max() + pts.real.min()) / 2, (pts.imag.max() + pts.imag.min()) / 2)
    assert c.real == pytest.approx(300 * 25.4 / 300, abs=0.01)
    assert np.abs(pts - c).max() == pytest.approx(10.0, abs=0.01)


def test_physical_units_mm(tmp_path):
    p = write(tmp_path, "mm.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" '
              'viewBox="0 0 210 297"><rect x="10" y="20" width="30" height="40" stroke="black" fill="none"/></svg>')
    d = load_svg(p, dpi=300)
    assert (d.page_w, d.page_h) == (pytest.approx(210), pytest.approx(297))
    pts = d.shapes[0].rings[0][0]
    assert pts.real.min() == pytest.approx(10) and pts.imag.max() == pytest.approx(60)


def test_inches_and_viewbox_scale(tmp_path):
    p = write(tmp_path, "in.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="2in" height="1in" '
              'viewBox="0 0 200 100"><line x1="0" y1="0" x2="200" y2="0" stroke="#f00"/></svg>')
    d = load_svg(p)
    pts = d.shapes[0].rings[0][0]
    assert abs(pts[1] - pts[0]) == pytest.approx(50.8)


# ------------------------------------------------ transforms / styles ------

def test_group_transform_and_inherited_style(tmp_path):
    p = write(tmp_path, "g.svg", '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
              'width="100mm" height="100mm" viewBox="0 0 100 100">'
              '<defs><rect id="r" width="10" height="10"/></defs>'
              '<g transform="translate(20,30) scale(2)" style="stroke:rgb(255,0,0);fill:none">'
              '<path d="M0 0 L5 0"/><use xlink:href="#r" x="1" y="1"/></g>'
              '<g style="display:none"><path d="M0 0 L1 1" stroke="black"/></g>'
              '<clipPath id="c"><rect width="5" height="5"/></clipPath>'
              '</svg>')
    d = load_svg(p)
    assert len(d.shapes) == 2
    a = d.shapes[0].rings[0][0]
    assert a[0] == pytest.approx(20 + 30j) and a[-1] == pytest.approx(30 + 30j)
    assert d.shapes[0].stroke == "#ff0000" and d.shapes[0].fill is None
    r = d.shapes[1].rings[0][0]
    assert r.real.min() == pytest.approx(22) and r.real.max() == pytest.approx(42)


def test_css_classes(tmp_path):
    p = write(tmp_path, "css.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10">'
              '<style>.st0{fill:none;stroke:#FF0000;}</style><path class="st0" d="M0 0 L5 5"/></svg>')
    d = load_svg(p)
    assert d.shapes[0].stroke == "#ff0000" and d.shapes[0].fill is None


def test_colors():
    assert parse_color("rgb(35,31,32)") == "#231f20"
    assert parse_color("#F00") == "#ff0000"
    assert parse_color("none") is None
    assert classify("#231f20") == "engrave"
    assert classify("#e30613") == "cut"
    assert classify("#0000ff") == "other"
    assert classify("#ffffff") == "white"


def test_stroke_none_is_not_engraved(tmp_path):
    p = write(tmp_path, "s.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10">'
              '<rect width="5" height="5" style="fill:#000;stroke:none"/></svg>')
    d = load_svg(p)
    job = build_job(d, Settings())
    # no outline: only the fill layer, hatched
    assert [L.name for L in job.layers] == ["fill:#000000"]
    assert all(len(pr) == 3 and pr[0][0] == "O" for st, pr in job.layers[0].paths)


def test_other_colors_policy(tmp_path):
    p = write(tmp_path, "o.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10">'
              '<path d="M1 1 L5 5" stroke="blue"/></svg>')
    d = load_svg(p)
    s = Settings()
    assert build_job(d, s).layers == []
    assert "stroke:#0000ff" in s.layers           # listed, but not output by default
    s.layer("stroke:#0000ff").enabled = True
    assert build_job(d, s).layers[0].name == "stroke:#0000ff"


# ----------------------------------------------------- arc fitting --------

def test_circle_becomes_two_arcs():
    t = np.linspace(0, 2 * np.pi, 400)
    pts = 10 * np.exp(1j * t)
    start, prims = fit_polyline(pts, 0.02, True)
    assert [p[0] for p in prims] == ["A", "A"]


def test_polygon_stays_lines():
    hexa = 5 * np.exp(1j * np.linspace(0, 2 * np.pi, 7))
    start, prims = fit_polyline(hexa, 0.02, True)
    assert all(p[0] == "L" for p in prims) and len(prims) == 6


def test_lines_only_mode():
    t = np.linspace(0, np.pi, 200)
    start, prims = fit_polyline(10 * np.exp(1j * t), 0.02, False)
    assert all(p[0] == "L" for p in prims)
    assert len(prims) < 60


# --------------------------------------------------------- hatching -------

def test_hatch_hole_evenodd_and_nonzero():
    outer = np.array([0, 10, 10 + 10j, 10j, 0])
    inner_same_dir = np.array([3 + 3j, 7 + 3j, 7 + 7j, 3 + 7j, 3 + 3j])
    inner_rev = inner_same_dir[::-1]
    # evenodd: hole whatever the direction
    segs = hatch_rings([outer, inner_same_dir], 1.0, 0, "evenodd")
    mid = [s for s in segs if abs(s[0].imag - 5) < 0.6]
    assert len(mid) == 2
    # nonzero: hole only if the inner ring turns the other way
    segs = hatch_rings([outer, inner_rev], 1.0, 0, "nonzero")
    assert len([s for s in segs if abs(s[0].imag - 5) < 0.6]) == 2
    segs = hatch_rings([outer, inner_same_dir], 1.0, 0, "nonzero")
    assert len([s for s in segs if abs(s[0].imag - 5) < 0.6]) == 1


def test_hatch_bidirectional():
    sq = np.array([0, 10, 10 + 10j, 10j, 0])
    segs = hatch_rings([sq], 1.0, 0, "nonzero", True)
    assert segs[0][0].real < segs[0][1].real and segs[1][0].real > segs[1][1].real


# ------------------------------------------- rotation preview = machine ----

def test_default_placement_keeps_page_position(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    job = build_job(d, Settings())
    # page 100 x 100 mm, part from x 10..50, y 10..50 (Y down) -> work area y 50..90
    assert job.bounds[0] == pytest.approx(10) and job.bounds[3] == pytest.approx(90)
    assert job.ref == 0


@pytest.mark.parametrize("angle", [0, 30, 90, 137.5])
def test_free_rotation_is_rigid(tmp_path, angle):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    M0, c0, _ = placement(d, View())
    M, c, box = placement(d, View(rotation=angle, x=200, y=150))
    assert c == pytest.approx(200 + 150j)
    pts = np.array([10 + 10j, 50 + 10j, 30 + 50j])
    a, b = _apply(M0, pts), _apply(M, pts)
    assert np.allclose(np.abs(np.diff(a)), np.abs(np.diff(b)))            # distances kept
    # counter-clockwise: a vector pointing +X turns by +angle
    v = (b[1] - b[0])
    assert math.degrees(math.atan2(v.imag, v.real)) % 360 == pytest.approx(angle % 360, abs=1e-6)
    # box centred on the requested position
    assert np.mean(box) == pytest.approx(200 + 150j)


def test_rotated_job_gcode_valid(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings()
    job = build_job(d, s, View(rotation=33, x=120, y=80))
    sim = gcode_check.simulate(write_gcode(job, s))
    assert sim["arc_errors"] == [] and sim["state_changes"] == []
    burn = np.concatenate([np.array(p) for p in sim["burn"]])
    assert np.mean([burn.real.min(), burn.real.max()]) == pytest.approx(120, abs=6)


# ---------------------------------------------------- G-code output -------

@pytest.mark.parametrize("arcs", [True, False])
@pytest.mark.parametrize("rot", [0, 90, 27])
def test_gcode_valid_and_accurate(arcs, rot):
    d = load_svg(os.path.join(FIX, "affinity_like.svg"), dpi=300)
    s = Settings()
    s.use_arcs = arcs
    s.layer("fill:#000000").hatch_spacing = 1.0
    s.layer("stroke:#000000").passes = 2
    job = build_job(d, s, View(rotation=rot))
    lines = write_gcode(job, s, "x.svg", "test")
    sim = gcode_check.simulate(lines)
    assert sim["arc_errors"] == []
    assert sim["state_changes"] == []           # no M3/M4 in the middle of the job
    assert sim["spindle"] == "M5"
    assert sim["end"] == (0.0, 0.0)
    # only one M4 in the whole file
    assert sum(1 for l in lines if l.startswith("M4")) == 1
    # the burned path follows the source geometry
    src = []
    for L in job.layers:
        if L.name.startswith("fill"):
            continue
        for st, pr in L.paths:
            src.append(discretize(st, pr, 1))
    burned_outlines = [p for p in sim["burn"] if len(p) > 2]   # hatch lines have 2 points
    assert gcode_check.max_deviation(burned_outlines, src) < s.tolerance + 0.01


def test_fit_close_to_svg_geometry():
    d = load_svg(os.path.join(FIX, "affinity_like.svg"), dpi=300)
    s = Settings()
    s.layer("fill:#000000").enabled = False
    job = build_job(d, s)
    lines = write_gcode(job, s)
    sim = gcode_check.simulate(lines)
    # compare with the dense SVG rings transformed into machine coordinates
    Mm, w, h = machine_matrix(d.page_w, d.page_h, View())
    rings = [_apply(Mm, r) for sh in d.shapes if sh.stroke in ("#ff0000", "#000000", "#231f20")
             for r, _ in sh.rings]
    assert gcode_check.max_deviation(sim["burn"], rings) < s.tolerance + 0.005
    assert gcode_check.max_deviation(rings, sim["burn"]) < s.tolerance + 0.005


def test_bezier_far_fewer_lines_than_before():
    d = load_svg(os.path.join(FIX, "affinity_like.svg"), dpi=300)
    s = Settings()
    s.layer("fill:#000000").enabled = False
    job = build_job(d, s)
    lines = [l for l in write_gcode(job, s) if l and l[0] in "GXY"]
    assert len(lines) < 60


def test_origin_options():
    d = load_svg(os.path.join(FIX, "affinity_like.svg"), dpi=300)
    s = Settings(); s.origin = "center"; s.start_from = "current"
    job = build_job(d, s)
    b = job.bounds
    assert (b[0] + b[2]) / 2 == pytest.approx(job.ref.real, abs=1e-6)
    assert (b[1] + b[3]) / 2 == pytest.approx(job.ref.imag, abs=1e-6)
    lines = write_gcode(job, s)
    assert any(l.startswith("G10 L20 P1 X0 Y0") for l in lines)
    burn = np.concatenate([np.array(p) for p in gcode_check.simulate(lines)["burn"]])
    assert (burn.real.min() + burn.real.max()) / 2 == pytest.approx(0, abs=0.05)
    # absolute mode: no G10, work-area coordinates
    s.start_from = "absolute"
    assert not any(l.startswith("G10") for l in write_gcode(build_job(d, s), s))


def test_settings_roundtrip(tmp_path):
    s = Settings(); s.layer('stroke:#ff0000').passes = 3; s.origin = "top left"
    save_settings(s, {"last_dir": "x"}, tmp_path / "s.json")
    s2, extra = load_settings(tmp_path / "s.json")
    assert s2.layer("stroke:#ff0000").passes == 3 and s2.origin == "top left" and extra["last_dir"] == "x"


def test_polyline_dense_points_are_compressed(tmp_path):
    import math as m
    pts = " ".join(f"{i * 0.1:.3f},{20 + 10 * m.sin(i / 50):.4f}" for i in range(2000))
    p = write(tmp_path, "pl.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="40mm" '
              f'viewBox="0 0 200 40"><polyline points="{pts}" style="fill:none;stroke:#000"/></svg>')
    d = load_svg(p)
    s = Settings()
    job = build_job(d, s)
    lines = write_gcode(job, s)
    motion = [l for l in lines if l and l[0] in "GXY"]
    assert len(motion) < 120          # 2000 input points
    sim = gcode_check.simulate(lines)
    assert sim["arc_errors"] == []


# ------------------------------------------------------ kerf / tabs -------

PART_WITH_HOLE = ('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'
                  '<rect x="10" y="10" width="40" height="40" style="fill:none;stroke:#f00"/>'
                  '<rect x="20" y="20" width="10" height="10" style="fill:none;stroke:#f00"/>'
                  '<path d="M60 60 L90 60" style="fill:none;stroke:#f00"/></svg>')


def _burn_extents(lines):
    sim = gcode_check.simulate(lines)
    return [np.array(p) for p in sim["burn"]], sim


def test_kerf_offset_part_grows_hole_shrinks(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings(); s.layer('stroke:#ff0000').kerf = 0.1
    job = build_job(d, s)
    polys, sim = _burn_extents(write_gcode(job, s))
    assert sim["arc_errors"] == []
    sizes = sorted((p.real.max() - p.real.min()) for p in polys)
    # open line unchanged (30), hole 10 -> 9.8, part 40 -> 40.2
    assert sizes[0] == pytest.approx(9.8, abs=0.01)
    assert sizes[-1] == pytest.approx(40.2, abs=0.01)
    assert any("open path" in w for w in job.warnings)


def test_kerf_outer_corners_are_rounded(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings(); s.layer('stroke:#ff0000').kerf = 0.5
    job = build_job(d, s)
    cut = [L for L in job.layers if L.name == "stroke:#ff0000"][0]
    kinds = [p[0] for st, pr in cut.paths for p in pr]
    assert "A" in kinds          # round joins become G2/G3 arcs


def test_inner_contours_cut_first(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings()
    job = build_job(d, s)
    cut = [L for L in job.layers if L.name == "stroke:#ff0000"][0]
    first = np.array(discretize(*cut.paths[0], 5))
    assert first.real.max() - first.real.min() == pytest.approx(10, abs=0.01)


def test_tabs(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings(); s.layer('stroke:#ff0000').tabs_count = 4; s.layer('stroke:#ff0000').tab_width = 1.0
    job = build_job(d, s)
    polys, sim = _burn_extents(write_gcode(job, s))
    lengths = [float(np.sum(np.abs(np.diff(p)))) for p in polys]
    # hole (perimeter 40, outer only) stays whole, part 160 -> 4 pieces of 39
    assert sorted(round(L, 2) for L in lengths) == [30.0, 39.0, 39.0, 39.0, 39.0, 40.0]


def test_tabs_on_holes_and_min_length(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings(); s.layer('stroke:#ff0000').tabs_count = 2; s.layer('stroke:#ff0000').tab_width = 1.0
    s.layer('stroke:#ff0000').tabs_outer_only = False; s.layer('stroke:#ff0000').tabs_min_length = 50
    job = build_job(d, s)
    polys, _ = _burn_extents(write_gcode(job, s))
    lengths = sorted(round(float(np.sum(np.abs(np.diff(p)))), 2) for p in polys)
    assert lengths == [30.0, 40.0, 79.0, 79.0]


# ------------------------------------------------------------- layers ------

LAYERS_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'
              '<rect x="5" y="5" width="20" height="20" style="fill:#00ff00;stroke:#0000ff"/>'
              '<rect x="15" y="15" width="20" height="20" style="fill:#00ff00;stroke:none"/>'
              '<path d="M50 50 L90 50" style="stroke:#f00"/>'
              '<path d="M50 60 L90 60" style="stroke:#000"/></svg>')


def test_layers_by_colour_and_default_order(tmp_path):
    d = load_svg(write(tmp_path, "l.svg", LAYERS_SVG))
    s = Settings()
    s.layer("stroke:#0000ff").enabled = True
    job = build_job(d, s)
    names = [L.name for L in job.layers]
    assert names[0] == "fill:#00ff00" and names[-1] == "stroke:#ff0000"
    assert set(names) == {"fill:#00ff00", "stroke:#0000ff", "stroke:#000000", "stroke:#ff0000"}


def test_layer_modes_and_custom_order(tmp_path):
    d = load_svg(write(tmp_path, "l.svg", LAYERS_SVG))
    s = Settings()
    s.layer("fill:#00ff00").mode = "line+fill"
    s.move_layer("stroke:#ff0000", -10, ["fill:#00ff00", "stroke:#000000", "stroke:#ff0000"])
    job = build_job(d, s)
    assert job.layers[0].name == "stroke:#ff0000"
    fill = [L for L in job.layers if L.name == "fill:#00ff00"][0]
    kinds = {pr[0][0] for st, pr in fill.paths}
    assert kinds == {"O", "L"}      # hatch lines (with overscan) + outlines


def test_fill_merge_overlaps(tmp_path):
    d = load_svg(write(tmp_path, "l.svg", LAYERS_SVG))
    lengths = {}
    for merge in (True, False):
        s = Settings()
        L = s.layer("fill:#00ff00"); L.hatch_merge = merge; L.hatch_spacing = 0.5; L.overscan = 0
        job = build_job(d, s)
        lengths[merge] = [x for x in job.layers if x.name == "fill:#00ff00"][0].length
    # two 20x20 squares overlapping on 10x10: union 700 mm2, separate 800 mm2
    assert lengths[True] * 0.5 == pytest.approx(700, rel=0.03)
    assert lengths[False] * 0.5 == pytest.approx(800, rel=0.03)


def test_settings_migration_from_v020():
    s = Settings.from_dict({"engrave": {"power_pct": 12, "feed": 800, "passes": 2},
                            "cut": {"power_pct": 90, "feed": 200, "passes": 3, "kerf": 0.1}})
    assert s.layer("stroke:#000000").power_pct == 12
    assert s.layer("stroke:#ff0000").passes == 3 and s.layer("stroke:#ff0000").kerf == 0.1


def test_overscan_in_gcode(tmp_path):
    d = load_svg(write(tmp_path, "l.svg", LAYERS_SVG))
    s = Settings()
    s.layer("fill:#00ff00").overscan = 2.0
    lines = write_gcode(build_job(d, s), s)
    assert any("S0" in l and l.startswith(("G1", "X")) for l in lines)
    sim = gcode_check.simulate(lines)
    assert sim["state_changes"] == []


# ------------------------------------------------------------- raster ------

def _test_png(tmp_path, dpi=254):
    from PIL import Image
    # 300 x 100 px @ 254 dpi = 30 x 10 mm : black square 10x10 mm + gradient 20 mm
    arr = np.full((100, 300), 255, np.uint8)
    arr[:, :100] = 0
    arr[:, 100:] = np.linspace(0, 255, 200)[None, :].astype(np.uint8)
    p = tmp_path / "img.png"
    Image.fromarray(arr, "L").save(p, dpi=(dpi, dpi))
    return p


def test_bitmap_threshold(tmp_path):
    from beamcraft_core import load_bitmap
    d = load_bitmap(str(_test_png(tmp_path)), dpi=300)
    assert d.page_w == pytest.approx(30.0) and d.page_h == pytest.approx(10.0)
    s = Settings()
    L = s.layer("image"); L.image_mode = "threshold"; L.threshold = 128; L.line_interval = 0.1
    L.overscan = 2.0
    job = build_job(d, s)
    assert job.layers[0].kind == "raster"
    paths = job.layers[0].paths
    assert len(paths) == 100                      # 10 mm / 0.1 mm
    lines = write_gcode(job, s)
    sim = gcode_check.simulate(lines)
    assert sim["state_changes"] == [] and sim["arc_errors"] == []
    # burned width per line: black square (10 mm) + dark half of the gradient (~10 mm)
    widths = [abs(p[-1] - p[0]) for p in sim["burn"]]
    assert np.median(widths) == pytest.approx(20.0, abs=0.3)
    # overscan: the head goes 2 mm beyond the image with S0
    xs = [float(l.split("X")[1].split()[0]) for l in lines if l.startswith(("G0", "G1", "X")) and "X" in l]
    assert min(xs) == pytest.approx(-2.0, abs=0.06)


def test_bitmap_grayscale_power_range(tmp_path):
    from beamcraft_core import load_bitmap
    d = load_bitmap(str(_test_png(tmp_path)), dpi=300)
    s = Settings()
    L = s.layer("image"); L.image_mode = "grayscale"; L.power_pct = 80; L.min_power_pct = 10
    job = build_job(d, s)
    pcts = {p[2] for st, pr in job.layers[0].paths for p in pr if p[0] == "P"}
    assert max(pcts) == pytest.approx(80) and min(pcts) >= 10
    assert len(pcts) > 50                          # many grey levels


def test_bitmap_dither_and_negative(tmp_path):
    from beamcraft_core import load_bitmap
    d = load_bitmap(str(_test_png(tmp_path)), dpi=300)
    s = Settings()
    L = s.layer("image"); L.image_mode = "dither"
    a = build_job(d, s).layers[0].length
    L.negative = True
    b = build_job(d, s).layers[0].length
    # 30 x 10 mm image, 2/3 dark (square + half of the gradient): 200 mm2 / 0.1 mm = 2000 mm
    # of burn; the negative burns the other third: 1000 mm
    assert a == pytest.approx(2000, rel=0.05) and b == pytest.approx(1000, rel=0.08)


def test_svg_embedded_image_rotated(tmp_path):
    import base64
    png = _test_png(tmp_path).read_bytes()
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    p = write(tmp_path, "i.svg", '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
              'width="100mm" height="100mm" viewBox="0 0 100 100">'
              f'<image x="0" y="0" width="30" height="10" transform="translate(50,20) rotate(90)" xlink:href="{uri}"/>'
              '</svg>')
    d = load_svg(p)
    assert len(d.images) == 1
    s = Settings()
    L = s.layer("image"); L.image_mode = "threshold"; L.overscan = 0
    job = build_job(d, s)
    b = job.bounds
    # rotated: 10 mm wide, 30 mm tall
    assert b[2] - b[0] == pytest.approx(10, abs=0.15) and b[3] - b[1] == pytest.approx(30, abs=0.15)
    # the black square is at the top after a 90 deg rotation (y = 20..30 in SVG -> top in machine)
    rows = [st.imag for st, pr in job.layers[0].paths]
    assert max(rows) > b[3] - 0.2


# ------------------------------------------- simulation / placement / rotary

def test_simulated_time_straight_and_corners():
    from beamcraft_core.simulate import simulate_time
    s = Settings(); s.accel_x = s.accel_y = 100.0
    # 100 mm at 600 mm/min (10 mm/s): 10 s + accel/decel overhead v/a = 0.1 s
    t = simulate_time(["G1 X100 F600"], s)
    assert t == pytest.approx(10.1, abs=0.01)
    # same length split in 2 collinear blocks: no slowdown in the middle
    assert simulate_time(["G1 X50 F600", "X100"], s) == pytest.approx(10.1, abs=0.01)
    # a 90 deg corner forces a slowdown
    corner = simulate_time(["G1 X50 F600", "Y50"], s)
    assert corner > 10.1
    # G0 uses $110 max rate
    assert simulate_time(["G0 X100"], s) == pytest.approx(100 / 100 + 1.0, rel=0.05)


def test_simulated_time_arc():
    from beamcraft_core.simulate import simulate_time
    s = Settings(); s.accel_x = s.accel_y = 5000.0
    # half circle r=10 at 600 mm/min: pi*10/10 = 3.14 s
    t = simulate_time(["G0 X10", "G3 X-10 I-10 J0 F600"], s)
    t0 = simulate_time(["G0 X10"], s)
    assert t - t0 == pytest.approx(math.pi, rel=0.02)


def test_scale_and_position(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings()
    job = build_job(d, s, View(x=100, y=60, scale=50))
    b = job.bounds
    assert b[2] - b[0] == pytest.approx(40)       # 80 mm wide drawing at 50 %
    assert np.mean(job.box) == pytest.approx(100 + 60j)


def test_rotary_scales_y(tmp_path):
    p = write(tmp_path, "r.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
              'viewBox="0 0 100 100"><circle cx="50" cy="50" r="10" style="fill:none;stroke:#000"/></svg>')
    d = load_svg(p)
    s = Settings()
    s.rotary_enabled = True; s.rotary_type = "chuck"; s.rotary_object_diameter = 50
    s.rotary_mm_per_rotation = 25 * math.pi       # 25*pi / (pi * 50) = 0.5
    kr = s.rotary_factor()
    assert kr == pytest.approx(0.5)
    job = build_job(d, s)
    b = job.bounds
    assert b[2] - b[0] == pytest.approx(20, abs=0.05)
    assert b[3] - b[1] == pytest.approx(20 * kr, abs=0.05)
    sim = gcode_check.simulate(write_gcode(job, s))
    assert sim["arc_errors"] == []
    burn = np.concatenate([np.array(p) for p in sim["burn"]])
    assert burn.imag.max() - burn.imag.min() == pytest.approx(20 * kr, abs=0.06)


def test_out_of_bed_warning(tmp_path):
    d = load_svg(write(tmp_path, "k.svg", PART_WITH_HOLE))
    s = Settings(); s.bed_width = 50; s.bed_height = 50
    assert any("outside the work area" in w for w in build_job(d, s).warnings)
    s.bed_width = s.bed_height = 400
    assert not any("outside the work area" in w for w in build_job(d, s).warnings)


# --------------------------------------------------------------- text ------

def _have_font(name):
    from beamcraft_core.fonts import get_index
    e, exact = get_index().find([name])
    return exact


@pytest.mark.skipif(not _have_font("Liberation Sans"), reason="Liberation Sans not installed")
def test_text_to_outlines(tmp_path, monkeypatch):
    p = write(tmp_path, "t.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" '
              'viewBox="0 0 200 100"><g transform="translate(100,50)">'
              '<text x="0" y="0" style="font-family:\'LiberationSans\',\'Liberation Sans\',sans-serif;'
              'font-size:20px;text-anchor:middle;fill:#000">Hel<tspan style="fill:#f00">lo</tspan></text>'
              '</g></svg>')
    d = load_svg(p)
    assert not any("ignored" in w for w in d.warnings)
    fills = {sh.fill for sh in d.shapes}
    assert fills == {"#000000", "#ff0000"}
    pts = np.concatenate([r for sh in d.shapes for r, _ in sh.rings])
    # "Hello" at 20 px Liberation Sans is about 44 px wide, centred on x=100
    assert (pts.real.min() + pts.real.max()) / 2 == pytest.approx(100, abs=1.5)
    assert 38 < pts.real.max() - pts.real.min() < 50
    # cap height ~ 14.5 px above the baseline (y = 50)
    assert pts.imag.min() == pytest.approx(50 - 14.5, abs=1.0) and pts.imag.max() <= 50.5
    # letters are hatched with holes ('e', 'o')
    job = build_job(d, Settings())
    assert {L.name for L in job.layers} == {"fill:#000000", "fill:#ff0000"}


def test_text_missing_font_falls_back(tmp_path):
    from beamcraft_core.fonts import get_index
    if not get_index().load():
        pytest.skip("no font installed")
    p = write(tmp_path, "t.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
              'viewBox="0 0 100 100"><text x="10" y="50" font-family="NoSuchFont123" font-size="10">A</text></svg>')
    d = load_svg(p)
    assert d.shapes and any("NoSuchFont123" in w for w in d.warnings)


def test_text_per_character_positions(tmp_path):
    from beamcraft_core.fonts import get_index
    if not get_index().load():
        pytest.skip("no font installed")
    p = write(tmp_path, "t.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
              'viewBox="0 0 100 100"><text x="10 50 90" y="50" font-size="10" fill="#000">III</text></svg>')
    d = load_svg(p)
    pts = np.concatenate([r for sh in d.shapes for r, _ in sh.rings])
    assert pts.real.min() < 12 and pts.real.max() > 90


# ---------------------------------------------------------------- CLI ------

def test_cli(tmp_path, capsys):
    from beamcraft_core.cli import main
    out = tmp_path / "o.gcode"
    rc = main([os.path.join(FIX, "affinity_like.svg"), "-o", str(out), "--no-user-settings",
               "--set", "origin=center", "--layer", "stroke:#ff0000,passes=3,kerf=0.1", "--rotate", "90"])
    assert rc == 0
    txt = out.read_text()
    assert "x3 pass(es)" in txt and "Grbl planner simulation" in txt
    sim = gcode_check.simulate(txt.splitlines())
    assert sim["arc_errors"] == [] and sim["state_changes"] == []
    assert main([os.path.join(FIX, "affinity_like.svg"), "--no-user-settings", "--list-layers"]) == 0
    assert "stroke:#ff0000" in capsys.readouterr().out
    assert main([os.path.join(FIX, "affinity_like.svg"), "--no-user-settings", "--set", "nope=1"]) == 1
    assert main([os.path.join(FIX, "affinity_like.svg"), "--no-user-settings",
                 "--layer", "stroke:#ff0000,power_pct=150"]) == 1


def test_gcode_path_marks():
    d = load_svg(os.path.join(FIX, "affinity_like.svg"), dpi=300)
    s = Settings()
    job = build_job(d, s)
    marks = []
    lines = write_gcode(job, s, marks=marks)
    assert len(marks) == sum(len(L.paths) * L.settings.passes for L in job.layers)
    ends = [m[0] for m in marks]
    assert ends == sorted(ends) and ends[-1] < len(lines)
    # the line just before each mark belongs to the path (a motion line)
    assert all(lines[e - 1][:1] in "GXY" for e in ends)
