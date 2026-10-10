"""Undo command for Advanced Layer Order document edits.

A TreeStateCommand holds the document JSON before and after one user
action. Undo/redo restore it through Model.restore_structure(), which keeps
the current layer set and QGIS-mirrored state, so undo can never resurrect a
deleted layer or drop one QGIS still has.
"""
from qgis.PyQt.QtGui import QUndoCommand


class TreeStateCommand(QUndoCommand):
    def __init__(self, model, before_json: str, after_json: str, text: str):
        super().__init__(text)
        self._model = model
        self._before = before_json
        self._after = after_json
        # QUndoStack.push() calls redo() immediately, but the edit has
        # already been applied when the command is pushed.
        self._applied = True

    def undo(self):
        self._model.restore_structure(self._before)

    def redo(self):
        if self._applied:
            self._applied = False
            return
        self._model.restore_structure(self._after)
