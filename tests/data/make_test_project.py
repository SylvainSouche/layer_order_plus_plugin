"""Build the manual-test data: tests/data/test_layers.gpkg + tests/data/test.qgz.

Run with QGIS's Python (headless is fine), from anywhere:

    make test-data
    # or, in the QGIS Python console (it replaces the open project):
    p = "/path/to/tests/data/make_test_project.py"
    exec(open(p).read(), {"__file__": p, "__name__": "__main__"})

Design — the drawing order can be read off the map:

* Five opaque discs A–E, all overlapping in the centre of the map, each
  with its own labelled outer part (A at the top, then clockwise).
  **The centre shows the colour of the layer drawn on top**, and each
  overlap between two neighbouring discs shows which of the two is above.
* Layer F (purple disc, lower right) is in the GeoPackage but not in the
  project: it is the layer to add during the tests. It overlaps the outer
  part of C (and the edge of B): the F/C overlap shows whether F was
  placed above C.

Project state (chosen to exercise the known traps):

* Layers panel order, top to bottom: A B C D E  → map centre is red (A).
* A stale custom order E D C B A is saved with "Control rendering order"
  OFF — the state in which QGIS's Layer Order panel used to show the wrong
  order after activation (fixed in 1.2.27).
* No Advanced Layer Order tree is saved: ALO starts from the QGIS order.
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GPKG = os.path.join(HERE, "test_layers.gpkg")
PROJECT = os.path.join(HERE, "test.qgz")

# name: (fill colour, angle of the disc around the centre, in degrees)
LAYERS = {
    "A": ("#e41a1c", 90),     # red, top
    "B": ("#ff7f00", 18),     # orange, upper right
    "C": ("#ffd92f", -54),    # yellow, lower right
    "D": ("#4daf4a", -126),   # green, lower left
    "E": ("#377eb8", 162),    # blue, upper left
}
EXTRA = {"F": ("#984ea3", None)}   # purple, not in the project
RING, RADIUS, LABEL_AT = 2500.0, 4000.0, 5200.0   # metres (EPSG:3857)


def _qgis_app():
    from qgis.core import QgsApplication
    app = QgsApplication.instance()
    if app is None:   # headless run
        app = QgsApplication([], False)
        app.initQgis()
    return app


F_CENTER, F_LABEL = (7000.0, -3000.0), (9000.0, -3500.0)
F_OVER_C = (4300.0, -4000.0)   # a point inside F and C only


def disc_center(angle):
    if angle is None:
        return F_CENTER
    a = math.radians(angle)
    return RING * math.cos(a), RING * math.sin(a)


def label_point(angle):
    if angle is None:
        return F_LABEL
    a = math.radians(angle)
    return LABEL_AT * math.cos(a), LABEL_AT * math.sin(a)


def build():
    from qgis.core import (
        Qgis,
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsField,
        QgsFillSymbol,
        QgsGeometry,
        QgsPalLayerSettings,
        QgsPointXY,
        QgsProject,
        QgsProperty,
        QgsRectangle,
        QgsReferencedRectangle,
        QgsSingleSymbolRenderer,
        QgsTextFormat,
        QgsVectorFileWriter,
        QgsVectorLayer,
        QgsVectorLayerSimpleLabeling,
    )
    from qgis.PyQt.QtCore import QMetaType
    from qgis.PyQt.QtGui import QColor, QFont

    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    if os.path.exists(GPKG):
        os.remove(GPKG)

    # --- data: one disc per layer, label position stored on the feature
    for i, (name, (_colour, angle)) in enumerate({**LAYERS, **EXTRA}.items()):
        mem = QgsVectorLayer("Polygon?crs=EPSG:3857", name, "memory")
        prov = mem.dataProvider()
        prov.addAttributes([QgsField("name", QMetaType.Type.QString),
                            QgsField("lx", QMetaType.Type.Double),
                            QgsField("ly", QMetaType.Type.Double)])
        mem.updateFields()
        cx, cy = disc_center(angle)
        lx, ly = label_point(angle)
        feat = QgsFeature(mem.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(cx, cy)).buffer(RADIUS, 48))
        feat.setAttributes([name, lx, ly])
        prov.addFeatures([feat])
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = name
        opts.actionOnExistingFile = (QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile if i == 0
                                     else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
        context = QgsProject.instance().transformContext()
        err = QgsVectorFileWriter.writeAsVectorFormatV3(mem, GPKG, context, opts)
        if err[0] != QgsVectorFileWriter.WriterError.NoError:
            raise RuntimeError(f"writing {name}: {err}")

    def styled(name):
        colour, _angle = {**LAYERS, **EXTRA}[name]
        lyr = QgsVectorLayer(f"{GPKG}|layername={name}", name, "ogr")
        if not lyr.isValid():
            raise RuntimeError(f"cannot open layer {name}")
        lyr.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
            {"color": colour, "outline_color": "#222222", "outline_width": "0.4"})))
        text = QgsTextFormat()
        text.setFont(QFont("Arial"))
        text.setSize(22)
        text.setColor(QColor("#111111"))
        text.buffer().setEnabled(True)
        text.buffer().setColor(QColor("white"))
        text.buffer().setSize(1.2)
        label = QgsPalLayerSettings()
        label.fieldName = "name"
        label.setFormat(text)
        label.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.PositionX,
                                                  QgsProperty.fromField("lx"))
        label.dataDefinedProperties().setProperty(QgsPalLayerSettings.Property.PositionY,
                                                  QgsProperty.fromField("ly"))
        label.placement = QgsPalLayerSettings.Placement.OverPoint
        lyr.setLabeling(QgsVectorLayerSimpleLabeling(label))
        lyr.setLabelsEnabled(True)
        # Store the style in the GeoPackage too: F looks right when added
        lyr.saveStyleToDatabaseV2(name, "Advanced Layer Order test style", True, "")
        return lyr

    for name in EXTRA:
        styled(name)

    # --- project
    proj = QgsProject.instance()
    proj.clear()
    proj.setCrs(crs)
    proj.setTitle("Advanced Layer Order — manual test")
    layers = {name: styled(name) for name in LAYERS}
    root = proj.layerTreeRoot()
    for name in LAYERS:                        # A B C D E, top to bottom
        proj.addMapLayer(layers[name], False)
        root.addLayer(layers[name])
    root.setCustomLayerOrder([layers[n] for n in reversed(LAYERS)])   # stale E..A
    root.setHasCustomLayerOrder(False)
    proj.viewSettings().setDefaultViewExtent(
        QgsReferencedRectangle(QgsRectangle(-8500, -8000, 16500, 8000), crs))
    proj.setFilePathStorage(Qgis.FilePathType.Relative)
    if not proj.write(PROJECT):
        raise RuntimeError(f"cannot write {PROJECT}")
    proj.clear()          # release the GeoPackage before compacting it
    _single_file(GPKG)
    print(f"wrote {GPKG}\nwrote {PROJECT}")


def _single_file(path):
    """Fold SQLite's write-ahead log into the GeoPackage so it is one file."""
    import sqlite3
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("VACUUM")
    finally:
        con.close()


if __name__ == "__main__":   # also true for exec() in the QGIS Python console
    _app = _qgis_app()
    build()
