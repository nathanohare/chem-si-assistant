"""Where Ctrl+Z / Ctrl+Y go (SPEC §13k.11).

A text box keeps its own typing history: while it has something to undo (or redo), the
keys undo typing there, as in any program. Once it has nothing left, the keys go to the
window's Edit ▸ Undo / Redo, which undo saved changes of the whole project.

Qt normally lets a focused text box take Ctrl+Z even when it has nothing to undo, so the
window's shortcut never fires. This filter sees the "shortcut override" question Qt asks
the text box first and answers it for the box when the box has nothing to do.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QLineEdit,
                               QPlainTextEdit, QTextEdit, QWidget)


def key_text(action: QAction | None = None, redo: bool = False) -> str:
    """The keys of Edit ▸ Undo (or Redo) as this computer shows them: "Ctrl+Z", or "⌘Z"
    on macOS. From `action` when given, else the platform's standard keys."""
    seq = action.shortcut() if action is not None else QKeySequence(
        QKeySequence.StandardKey.Redo if redo else QKeySequence.StandardKey.Undo)
    return seq.toString(QKeySequence.SequenceFormat.NativeText) or (
        "Ctrl+Y" if redo else "Ctrl+Z")


def text_box(widget: QWidget | None):
    """The text box that gets the keys when `widget` has the focus, or None."""
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
        return widget
    if isinstance(widget, (QAbstractSpinBox, QComboBox)):
        return widget.findChild(QLineEdit)
    return None


def has_own(box, redo: bool) -> bool:
    """Whether the text box has typing of its own to undo (or redo)."""
    if box is None or box.isReadOnly():
        return False
    if isinstance(box, QLineEdit):
        return box.isRedoAvailable() if redo else box.isUndoAvailable()
    doc = box.document()
    return doc.isRedoAvailable() if redo else doc.isUndoAvailable()


class UndoKeys(QObject):
    """For one main window: `undo` / `redo` are its Edit ▸ Undo / Redo actions."""

    def __init__(self, window: QWidget, undo: QAction, redo: QAction):
        super().__init__(window)
        self.window, self.undo, self.redo = window, undo, redo
        _router().windows.add(self)

    def which(self, event) -> str | None:
        """"undo", "redo" or None for a key event."""
        seq = QKeySequence(event.keyCombination())
        if seq in self.undo.shortcuts():
            return "undo"
        if seq in self.redo.shortcuts():
            return "redo"
        return None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.ShortcutOverride:
            return False
        what = self.which(event)
        if what is None:
            return False
        focus = QApplication.focusWidget()
        if focus is None or focus.window() is not self.window:
            return False   # (a dialog: its text boxes keep the keys)
        box = text_box(focus)
        if box is None or has_own(box, what == "redo"):
            return False   # the text box undoes its own typing
        event.ignore()     # nothing to undo there: the window's Edit ▸ Undo / Redo
        return True


class _Router(QObject):
    """The one filter on the application (every event passes it, so there is only one,
    however many windows were made); it hands shortcut questions to the windows."""

    def __init__(self):
        super().__init__(QApplication.instance())
        self.windows: "weakref.WeakSet[UndoKeys]" = weakref.WeakSet()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.ShortcutOverride:
            return False
        for keys in list(self.windows):
            try:
                if keys.eventFilter(obj, event):
                    return True
            except RuntimeError:   # (its window was deleted)
                self.windows.discard(keys)
            except Exception:      # noqa: BLE001 — a shortcut question must never crash
                continue           # the program; the key then works as usual
        return False


_the_router: _Router | None = None


def _router() -> _Router:
    global _the_router
    if _the_router is None:
        _the_router = _Router()
        QApplication.instance().installEventFilter(_the_router)
    return _the_router


def install(window: QWidget, undo: QAction, redo: QAction) -> UndoKeys:
    return UndoKeys(window, undo, redo)
