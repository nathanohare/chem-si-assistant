"""The project-wide Warnings panel (SPEC §8): every automatic warning and every flag,
grouped by section and compound. Double-click to go there; dismiss or restore warnings."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import checks
from ..core import fields as F
from ..core.project import Project

ROLE = Qt.ItemDataRole.UserRole  # (kind, compound id, field key, warning key)
DIM = QBrush(QColor(127, 127, 127))


def field_label(compound, key: str) -> str:
    if key == checks.ID_KEY:
        return "ID"
    entry = next((e for e in compound.fields if e["key"] == key), {"key": key})
    defn = F.field_def(entry)
    return defn.label if defn else key


def ask_reason(parent: QWidget, message: str) -> str | None:
    """Optional reason for dismissing; None if cancelled."""
    reason, ok = QInputDialog.getText(
        parent, "Dismiss warning",
        f"{message}\n\nWhy is this OK? (optional — saved with the project)")
    return reason if ok else None


class WarningsPanel(QWidget):
    open_requested = Signal(str, str)   # compound id, field key
    open_place = Signal(str, str)       # tree item kind ("front", "section", "scheme"), id
    changed = Signal()                  # something was dismissed/restored

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.project: Project | None = None
        self.summary = QLabel()
        self.show_dismissed = QCheckBox("Show dismissed")
        self.show_dismissed.toggled.connect(self.refresh)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.dismiss_btn = QPushButton("Dismiss…")
        self.dismiss_btn.clicked.connect(self._dismiss_selected)
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.clicked.connect(self._restore_selected)
        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.show_dismissed)
        top.addWidget(self.dismiss_btn)
        top.addWidget(self.restore_btn)
        top.addWidget(refresh)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.itemDoubleClicked.connect(self._open)
        self.tree.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        hint = QLabel("Double-click to open the compound at that field (or the page a "
                      "warning is about). Warnings never block you; dismiss any that don't "
                      "apply.")
        hint.setStyleSheet("color: palette(placeholder-text);")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(hint)
        self._warnings: dict[tuple[str, str], checks.Warning] = {}

    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        self._warnings = {}
        if self.project is None:
            self.summary.setText("")
            self._update_buttons()
            return
        report = self.project.id_report()
        total_w = total_f = 0
        show_dismissed = self.show_dismissed.isChecked()
        # Not about one compound: labels used twice, deleted items mentioned in texts.
        project_items = checks.project_warnings(self.project)
        if project_items:
            group = QTreeWidgetItem([f"Figures, tables, schemes and texts    ⚠ "
                                     f"{len(project_items)}"])
            f = group.font(0)
            f.setBold(True)
            group.setFont(0, f)
            self.tree.addTopLevelItem(group)
            for w in project_items:
                item = QTreeWidgetItem([f"⚠ {w.message}"])
                kind, item_id = w.place
                if kind == "compound":
                    item.setData(0, ROLE, ("compound", item_id, w.field, ""))
                else:
                    item.setData(0, ROLE, ("place", kind, item_id, ""))
                item.setToolTip(0, w.message)
                item.setForeground(0, QBrush(QColor("#d35400")))
                group.addChild(item)
            total_w += len(project_items)
        for section in self.project.data_sections():
            sec_item = None
            for c in section.compounds:
                active = checks.active_warnings(self.project, c, report)
                dismissed = checks.dismissed_warnings(self.project, c, report) if show_dismissed else []
                flags = list(c.flags.items())
                if not (active or dismissed or flags):
                    continue
                if sec_item is None:
                    sec_item = QTreeWidgetItem([section.name])
                    f = sec_item.font(0)
                    f.setBold(True)
                    sec_item.setFont(0, f)
                    self.tree.addTopLevelItem(sec_item)
                counts = []
                if active:
                    counts.append(f"⚠ {len(active)}")
                if flags:
                    counts.append(f"⚑ {len(flags)}")
                c_item = QTreeWidgetItem([f"{report.display_id(c.id)}  {c.name or '(unnamed)'}"
                                          f"    {'  '.join(counts)}"])
                c_item.setData(0, ROLE, ("compound", c.id, "", ""))
                sec_item.addChild(c_item)
                for w in active:
                    item = QTreeWidgetItem([f"⚠ {field_label(c, w.field)}: {w.message}"])
                    item.setData(0, ROLE, ("warning", c.id, w.field, w.key))
                    item.setToolTip(0, w.message)
                    item.setForeground(0, QBrush(QColor("#d35400")))
                    c_item.addChild(item)
                    self._warnings[(c.id, w.key)] = w
                for key, flag in flags:
                    who = f" — {flag['by']}" if flag.get("by") else ""
                    item = QTreeWidgetItem([f"⚑ {field_label(c, key)}: "
                                            f"{flag.get('comment') or '(flagged)'}{who}"])
                    item.setData(0, ROLE, ("flag", c.id, key, ""))
                    c_item.addChild(item)
                for w, info in dismissed:
                    reason = f" — “{info['reason']}”" if info.get("reason") else ""
                    item = QTreeWidgetItem([f"✓ dismissed · {field_label(c, w.field)}: "
                                            f"{w.message}{reason}"])
                    item.setData(0, ROLE, ("dismissed", c.id, w.field, w.key))
                    item.setForeground(0, DIM)
                    c_item.addChild(item)
                total_w += len(active)
                total_f += len(flags)
        self.tree.expandAll()
        if total_w == 0 and total_f == 0:
            self.summary.setText("✔ No warnings or flags.")
        else:
            self.summary.setText(f"⚠ {total_w} warning{'s' if total_w != 1 else ''} · "
                                 f"⚑ {total_f} flag{'s' if total_f != 1 else ''}")
        self._update_buttons()

    def _selected(self):
        item = self.tree.currentItem()
        return item.data(0, ROLE) if item is not None else None

    def _update_buttons(self) -> None:
        data = self._selected()
        ro = self.project is None or self.project.read_only
        self.dismiss_btn.setEnabled(not ro and bool(data) and data[0] == "warning")
        self.restore_btn.setEnabled(not ro and bool(data) and data[0] == "dismissed")

    def _open(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        data = item.data(0, ROLE)
        if data and data[0] == "place":
            if data[1]:
                self.open_place.emit(data[1], data[2])
        elif data:
            field = data[2] if data[2] != checks.ID_KEY else ""
            self.open_requested.emit(data[1], field)

    def _dismiss_selected(self) -> None:
        data = self._selected()
        if not data or data[0] != "warning" or self.project is None:
            return
        w = self._warnings.get((data[1], data[3]))
        if w is None:
            return
        reason = ask_reason(self, w.message)
        if reason is None:
            return
        with self.project.undo_step("Dismiss a warning", where=[("compound", data[1])]):
            self.project.dismiss_warning(self.project.compound(data[1]), w, reason)
        self.refresh()
        self.changed.emit()

    def _restore_selected(self) -> None:
        data = self._selected()
        if not data or data[0] != "dismissed" or self.project is None:
            return
        with self.project.undo_step("Restore a dismissed warning",
                                    where=[("compound", data[1])]):
            self.project.restore_warning(self.project.compound(data[1]), data[3])
        self.refresh()
        self.changed.emit()

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        data = item.data(0, ROLE)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self._open(item))
        if data and data[0] == "warning" and not self.project.read_only:
            menu.addAction("Dismiss…", self._dismiss_selected)
        if data and data[0] == "dismissed" and not self.project.read_only:
            menu.addAction("Restore", self._restore_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
