"""Stop the mouse wheel from changing dropdowns and number boxes by accident.

A dropdown (or spin box) only reacts to the wheel once it has been clicked
into. Otherwise the wheel scrolls the page, as you'd expect while reading.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QWidget


class WheelGuard(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox)):
            if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the wheel must not grab focus
            if not obj.hasFocus():
                parent = obj.parentWidget()
                if isinstance(parent, QWidget):
                    QApplication.sendEvent(parent, event)  # scroll the page instead
                return True
        return False


def install(app: QApplication) -> WheelGuard:
    # One guard for the application, however many windows ask for it (every event passes
    # through it, so several would slow everything down).
    guard = app.findChild(WheelGuard)
    if guard is None:
        guard = WheelGuard(app)
        app.installEventFilter(guard)
    return guard
