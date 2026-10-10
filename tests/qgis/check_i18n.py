"""The labels borrowed from QGIS's catalog (i18n.py) exist in this QGIS and
are translated; the panel shows them in the user's language; the verbose
logging box only appears with QGIS_DEBUG."""
import os

import qgis.core
from harness import check, finish, pump
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, QTranslator

from advanced_layer_order import i18n
from advanced_layer_order.view import LayerOrderView


def catalog(lang):
    """QGIS's .qm for `lang`: i18nPath() in a running QGIS; headless, found
    near the bindings (…/Contents/Resources[/qgis]/i18n in macOS apps)."""
    name = f"qgis_{lang}.qm"
    roots = [QgsApplication.i18nPath()]
    d = os.path.dirname(qgis.core.__file__)
    while d != os.path.dirname(d):
        roots += [os.path.join(d, *sub, "i18n") for sub in ((), ("Resources",), ("Resources", "qgis"))]
        d = os.path.dirname(d)
    return next((os.path.join(r, name) for r in roots if os.path.isfile(os.path.join(r, name))), name)


# Fully translated QGIS languages: every borrowed entry must be there
for lang in ("fr", "de", "es"):
    translator = QTranslator()
    path = catalog(lang)
    check(translator.load(path), f"QGIS catalog {path}")
    QCoreApplication.installTranslator(translator)   # as QGIS does; translate() as the plugin does
    missing = [e for e in i18n.ENTRIES if QCoreApplication.translate(*e) == e[1]]
    QCoreApplication.removeTranslator(translator)
    check(not missing, f"not in QGIS's {lang} catalog (renamed upstream?): {missing}")

french = QTranslator()
french.load(catalog("fr"))
QCoreApplication.installTranslator(french)
os.environ.pop("QGIS_DEBUG", None)
view = LayerOrderView()
view.show()
pump(0)
shown = (view.btn_add_group.toolTip(), view.chk_control.text(), view.btn_undo.toolTip())
check(shown == ("Ajouter un groupe", "Contrôler l'ordre de rendu", "Annuler"), f"French panel: {shown}")
check(not view.chk_verbose.isVisibleTo(view), "verbose logging box hidden without QGIS_DEBUG")
view.close()
QCoreApplication.removeTranslator(french)

os.environ["QGIS_DEBUG"] = "1"
debug_view = LayerOrderView()
debug_view.show()
pump(0)
check(debug_view.chk_verbose.isVisibleTo(debug_view), "verbose logging box shown with QGIS_DEBUG")
check(debug_view.btn_add_group.toolTip() == "Add Group", "English without a translator")
finish()
