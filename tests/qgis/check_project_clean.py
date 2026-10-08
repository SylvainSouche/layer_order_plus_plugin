"""Opening a saved project must not mark it modified (no spurious
"Save changes?" on close), whether Plus controls the order or not."""
import os
import tempfile

from harness import Rig, check, finish, pump
from qgis.core import QgsProject

r = Rig()
path = os.path.join(tempfile.mkdtemp(), "clean.qgz")
for control in (True, False):
    if r.model.get_control_enabled() != control:
        r.view.chk_control.click()
        pump()
    QgsProject.instance().write(path)
    QgsProject.instance().read(path)
    r.ctl.load_project()
    pump()
    check(r.model.get_control_enabled() == control, f"control {control} restored")
    check(not QgsProject.instance().isDirty(), f"project dirty after opening (control {control})")
finish()
