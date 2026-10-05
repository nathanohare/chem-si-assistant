"""A layout that puts its widgets side by side and wraps onto a new line when the row is
too narrow (Qt's standard "flow layout" example), so a row of buttons never makes a page
wider than the window."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = -1):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    # ---- QLayout

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        # Everything on one line when there is room.
        w = h = 0
        for item in self._visible():
            hint = item.sizeHint()
            w += hint.width() + (self._gap(item, Qt.Orientation.Horizontal) if w else 0)
            h = max(h, hint.height())
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ---- helpers

    def _visible(self) -> list:
        return [i for i in self._items if i.widget() is None or not i.widget().isHidden()]

    def _gap(self, item, orientation) -> int:
        if self._spacing >= 0:
            return self._spacing
        widget = item.widget()
        style = widget.style() if widget is not None else None
        if style is None:
            return 6
        gap = style.layoutSpacing(QSizePolicy.ControlType.PushButton,
                                  QSizePolicy.ControlType.PushButton, orientation)
        return gap if gap >= 0 else 6

    def _arrange(self, rect: QRect, move: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._visible():
            hint = item.sizeHint()
            dx = self._gap(item, Qt.Orientation.Horizontal)
            dy = self._gap(item, Qt.Orientation.Vertical)
            if x + hint.width() > area.right() + 1 and line > 0:   # wrap
                x, y = area.x(), y + line + dy
                line = 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + dx
            line = max(line, hint.height())
        return y + line - rect.y() + m.bottom()
