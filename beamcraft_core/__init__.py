# BeamCraft core (no GUI) - GPL-3.0-or-later
from .svgload import load_svg, SvgDocument, Shape
from .settings import Settings, LayerSettings, load_settings, save_settings
from .toolpath import build_job, View, placement, machine_matrix, content_bounds, Job
from .gcode import write_gcode
from .raster import load_bitmap, BITMAP_EXT

APP_NAME = "BeamCraft"
APP_VERSION = "v1.0.2"
