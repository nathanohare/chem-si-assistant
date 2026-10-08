"""Stop the mouse wheel from changing dropdowns and number boxes by accident.

Dropdowns (combo boxes) and number boxes (spin boxes) never react to the wheel, even when
they have been clicked into or are selected: the wheel always scrolls the page
(SPEC §13n.6). Their values are changed by clicking or typing (or with the arrow keys).
The list a dropdown opens still scrolls with the wheel.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox,
                               QWidget)

GUARDED = (QComboBox, QAbstractSpinBox)


def _owner(obj: QObject) -> QWidget | None:
    """The dropdown or number box `obj` belongs to: itself, or the dropdown whose typing
    box it is (an editable dropdown's typing box gets the wheel first; a number box's own
    text field passes the wheel on to the box, where it is caught)."""
    if isinstance(obj, GUARDED):
        return obj
    parent = obj.parent() if isinstance(obj, QWidget) else None
    if isinstance(parent, QComboBox) and obj is parent.lineEdit():
        return parent
    return None


class WheelGuard(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel:
            return False
        box = _owner(obj)
        if box is None:
            return False
        if box.focusPolicy() == Qt.FocusPolicy.WheelFocus:
            box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the wheel must not grab focus
        page = _scroll_area(box)
        target = page.viewport() if page is not None else box.parentWidget()
        if isinstance(target, QWidget):
            # Scroll the page instead. (Sent straight to the page's scrolling area: Qt
            # passes a forwarded wheel event on to no one else.)
            QApplication.sendEvent(target, event)
        return True


def _scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    """The nearest scrolling area (the page) the widget sits in, or None."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea) and parent.viewport().isAncestorOf(widget):
            return parent
        parent = parent.parentWidget()
    return None


def install(app: QApplication) -> WheelGuard:
    # One guard for the application, however many windows ask for it (every event passes
    # through it, so several would slow everything down).
    guard = app.findChild(WheelGuard)
    if guard is None:
        guard = WheelGuard(app)
        app.installEventFilter(guard)
    return guard
