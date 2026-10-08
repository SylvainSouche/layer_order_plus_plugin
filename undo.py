"""Undo command for Layer Order Plus tree state changes.

A single TreeStateCommand captures before/after JSON snapshots of the
ViewController's tree. undo() and redo() both call back into the VC's
apply-tree-state-from-undo path, which rebuilds the tree and forces a
custom-layer-order apply.
"""
from qgis.PyQt.QtGui import QUndoCommand


class TreeStateCommand(QUndoCommand):
    def __init__(self, view_controller, before_json: str, after_json: str, text: str):
        super().__init__(text)
        self._vc = view_controller
        self._before = before_json
        self._after = after_json

    def undo(self):
        self._vc._apply_tree_state_from_undo(self._before)

    def redo(self):
        self._vc._apply_tree_state_from_undo(self._after)
