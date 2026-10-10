"""The manual-test data really shows the drawing order on the map.

Renders tests/data/test.qgz headless and samples pixels: whatever the
order, the centre has the colour of the top layer and every disc shows its
own colour in its outer part. Also checks how Advanced Layer Order first sees
the project. With --snapshots DIR, writes the reference images used in
docs/TEST_SCENARIO.md.
"""
import math
import os
import shutil
import sys
import tempfile

from harness import FakeIface, check, finish, pump
from qgis.core import QgsMapRendererParallelJob, QgsMapSettings, QgsProject, QgsRectangle, QgsVectorLayer
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor

from advanced_layer_order.controller import LayerOrderController
from advanced_layer_order.model import LayerOrderModel

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
sys.path.insert(0, DATA)
import make_test_project as spec  # noqa: E402

WORK = tempfile.mkdtemp()
for f in ("test.qgz", "test_layers.gpkg"):
    shutil.copy(os.path.join(DATA, f), WORK)       # never touch the committed data

EXTENT = QgsRectangle(-8500, -8000, 16500, 8000)
SIZE = QSize(1000, 640)


def render(layers):
    settings = QgsMapSettings()
    settings.setLayers(layers)            # first = drawn on top
    settings.setDestinationCrs(QgsProject.instance().crs())
    settings.setExtent(EXTENT)
    settings.setOutputSize(SIZE)
    settings.setBackgroundColor(QColor("white"))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    return job.renderedImage(), settings


def colour_at(image, settings, x, y):
    pixel = settings.mapToPixel().transform(x, y)
    return QColor(image.pixel(int(pixel.x()), int(pixel.y()))).name()


def outer_point(angle, distance=6000.0):
    a = math.radians(angle)
    return distance * math.cos(a), distance * math.sin(a)


QgsProject.instance().read(os.path.join(WORK, "test.qgz"))
proj = QgsProject.instance()
by_name = {lyr.name(): lyr for lyr in proj.mapLayers().values()}
check(sorted(by_name) == list("ABCDE"), f"project layers {sorted(by_name)}")
root = proj.layerTreeRoot()
check([n.name() for n in root.findLayers()] == list("ABCDE"), "Layers panel order A..E")
check(not root.hasCustomLayerOrder(), "control off in the saved project")
check("".join(lyr.name() for lyr in root.customLayerOrder()) == "EDCBA", "stale custom order E..A")

snapshots = sys.argv[sys.argv.index("--snapshots") + 1] if "--snapshots" in sys.argv else None
for order in ("ABCDE", "EDCBA", "CEADB", "BADCE"):
    image, settings = render([by_name[n] for n in order])
    centre = colour_at(image, settings, 0, 0)
    check(centre == spec.LAYERS[order[0]][0], f"{order}: centre {centre}, expected {order[0]}")
    for name, (colour, angle) in spec.LAYERS.items():
        seen = colour_at(image, settings, *outer_point(angle))
        check(seen == colour, f"{order}: outer part of {name} shows {seen}")
    if snapshots and order in ("ABCDE", "CEADB"):
        image.save(os.path.join(snapshots, f"test-map-{order}.png"))

# Layer F (to be added during the tests) overlaps C
f_layer = QgsVectorLayer(f"{os.path.join(WORK, 'test_layers.gpkg')}|layername=F", "F", "ogr")
check(f_layer.isValid() and f_layer.renderer() is not None, "layer F with its stored style")
f_colour = spec.EXTRA["F"][0]
for order, expected in (("ABFCDE", f_colour), ("ABCFDE", spec.LAYERS["C"][0])):
    image, settings = render([f_layer if n == "F" else by_name[n] for n in order])
    seen = colour_at(image, settings, *spec.F_OVER_C)
    check(seen == expected, f"{order}: F/C overlap shows {seen}, expected {expected}")
    if snapshots and order == "ABFCDE":
        image.save(os.path.join(snapshots, "test-map-ABFCDE.png"))

# How Advanced Layer Order first sees this project: no saved tree → the
# Layers-panel order, control off
model = LayerOrderModel()
ctl = LayerOrderController(model, FakeIface())
ctl.load_project()
pump()
names = "".join(proj.mapLayer(i).name() for i in model.get_flattened_layer_ids())
check(names == "ABCDE" and not model.get_control_enabled(), f"ALO initial state {names}")
finish()
