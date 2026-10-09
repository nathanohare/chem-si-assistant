"""Data section panel (SPEC §6.3): dashboard, ID table, default fields, general procedures."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ..core import fields as F
from ..core.project import Project, ProjectError, Section
from .dashboard_view import DashboardView
from .dialogs import CustomFieldDialog, FillPatternDialog
from .panels import SectionPanel
from .procedure_widgets import GeneralProceduresEditor

WARN_BG = QBrush(QColor(230, 126, 34, 90))   # translucent orange: readable in light and dark
ID_ROLE = Qt.ItemDataRole.UserRole


def _ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class DataSectionPanel(SectionPanel):
    ids_changed = Signal()             # the tree should refresh compound labels
    open_compound = Signal(str, str)   # compound id, field key
    new_compound_requested = Signal()
    pages_requested = Signal(str)      # "Make pages for these IDs…": the section's id

    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(project, section, parent)
        self.tabs = QTabWidget()
        self.dashboard = DashboardView(project, section)
        self.dashboard.open_compound.connect(self.open_compound)
        self.dashboard.new_compound_requested.connect(self.new_compound_requested)
        self.dashboard.saved.connect(self.saved)
        self.tabs.addTab(self.dashboard, "Dashboard")
        self.id_page = self._build_id_table()
        self.tabs.addTab(self.id_page, "ID table")
        self.tabs.addTab(self._build_defaults(), "Default fields")
        self.procedures = GeneralProceduresEditor(project, section)
        self.procedures.changed.connect(lambda: self.saved.emit("Saved"))
        self.tabs.addTab(self.procedures, "General procedures")
        self.tabs.currentChanged.connect(self._show_tab)
        self.layout_.addWidget(self.tabs, 1)
        self.refresh()

    def refresh(self) -> None:
        self.procedures.flush()
        self.dashboard.refresh()
        self._fill_id_table(self.project.id_report())
        self.procedures._update_users()

    def _show_tab(self, _index: int | None = None) -> None:
        """Rebuild only the tab on show, from the project: the others are rebuilt when
        they are shown (rebuilding the dashboard on every tab switch was slow)."""
        self.procedures.flush()   # (typed GP text is saved before anything is rebuilt)
        page = self.tabs.currentWidget()
        if page is self.dashboard:
            self.dashboard.refresh()
        elif page is self.id_page:
            self._fill_id_table(self.project.id_report())
        elif page is self.procedures:
            self.procedures._update_users()

    def flush(self) -> None:
        self._commit_id_edit()
        super().flush()
        self.procedures.flush()

    def _commit_id_edit(self) -> None:
        """Save an ID cell still being typed in (no Enter yet) now, e.g. before Undo/Redo
        looks at the history; otherwise it is saved later, when the page is hidden."""
        t = self.id_table
        if t.state() != QAbstractItemView.State.EditingState:
            return
        row, col = t.currentRow(), t.currentColumn()
        # Moving the current cell makes Qt commit and close the open editor.
        t.setCurrentIndex(t.model().index(-1, -1))
        if 0 <= row < t.rowCount() and 0 <= col < t.columnCount():   # (saving may drop a row)
            t.setCurrentCell(row, col)

    # ------------------------------------------------------------------ ID table

    def _build_id_table(self) -> QWidget:
        w = QWidget()
        self.id_table = QTableWidget(0, 2)
        self.id_table.setHorizontalHeaderLabels(["ID", "Used by (compound at this position)"])
        self.id_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.id_table.cellChanged.connect(self._id_cell_changed)
        ro = self.project.read_only

        buttons = QHBoxLayout()
        for text, slot in (("Fill from pattern…", self._fill_pattern), ("Add row", self._add_row),
                           ("Remove last row", self._remove_row)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setEnabled(not ro)
            buttons.addWidget(b)
        self.pages_btn = QPushButton("Make pages for unused IDs…")
        self.pages_btn.setToolTip("A blank compound page, at the end of this section, for each "
                                  "ID in the table that no compound has yet")
        self.pages_btn.clicked.connect(lambda: self.pages_requested.emit(self.section.id))
        self.pages_btn.setEnabled(not ro)
        buttons.addWidget(self.pages_btn)
        buttons.addStretch()

        help_ = QLabel("A compound's ID is the entry in the row matching its position in this "
                       "section, so reordering compounds re-assigns IDs automatically. "
                       "Edit any cell by double-clicking it. Problems are marked ⚠ and shaded.")
        help_.setWordWrap(True)
        layout = QVBoxLayout(w)
        layout.addWidget(help_)
        layout.addLayout(buttons)
        layout.addWidget(self.id_table)
        return w

    def _fill_id_table(self, report) -> None:
        if not self.project.read_only:
            self.pages_btn.setEnabled(bool(self.project.ids_without_pages(self.section)))
        table = self.section.id_table
        compounds = self.section.compounds
        rows = max(len(table), len(compounds))
        self.id_table.blockSignals(True)
        self.id_table.setRowCount(rows)
        self.id_table.setVerticalHeaderLabels([str(i + 1) for i in range(rows)])
        for row in range(rows):
            value = table[row] if row < len(table) else ""
            id_item = QTableWidgetItem(value)
            if self.project.read_only:
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            problems = []
            warning = report.table_warnings.get((self.section.id, row))
            if warning:
                problems.append(warning)
            if row < len(compounds) and not value.strip() and not compounds[row].id_override:
                problems.append("This compound has no ID: fill in this cell.")
            if problems:
                id_item.setBackground(WARN_BG)
                id_item.setText(value)
                id_item.setToolTip("\n".join(problems))
            self.id_table.setItem(row, 0, id_item)

            if row < len(compounds):
                c = compounds[row]
                used = c.name or "(unnamed)"
                if c.id_override:
                    used += f"   🔒 manual ID “{c.id_override}” used instead"
            else:
                used = "—"
            if problems:
                used = "⚠ " + "  ".join(problems) + "   ·   " + used
            used_item = _ro_item(used)
            if problems:
                used_item.setBackground(WARN_BG)
            self.id_table.setItem(row, 1, used_item)
        self.id_table.blockSignals(False)

    def _table_values(self) -> list[str]:
        values = []
        for row in range(self.id_table.rowCount()):
            item = self.id_table.item(row, 0)
            values.append(item.text().strip() if item else "")
        # Trailing empty rows that no compound needs are not kept.
        while values and not values[-1] and len(values) > len(self.section.id_table):
            values.pop()
        return values

    def _save_table(self, values: list[str]) -> None:
        try:
            with self.project.undo_step(f"Edit the ID table of “{self.section.name}”"):
                self.project.set_id_table(self.section.id, values)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
        self._fill_id_table(self.project.id_report())
        if self.tabs.currentWidget() is not self.id_page:
            self._show_tab()   # (a cell committed just after switching to another tab)
        self.ids_changed.emit()
        self.saved.emit("Saved")

    def _id_cell_changed(self, row: int, col: int) -> None:
        if col == 0 and not self.project.read_only:
            self._save_table(self._table_values())

    def _fill_pattern(self) -> None:
        dlg = FillPatternDialog(max(len(self.section.compounds), len(self.section.id_table), 26),
                                self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if any(v.strip() for v in self.section.id_table) and QMessageBox.question(
                self, "Replace ID table", "Replace the current ID table?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._save_table(dlg.ids())

    def _add_row(self) -> None:
        self._save_table(self._table_values() + [""])
        self.id_table.setCurrentCell(len(self.section.id_table) - 1, 0)
        self.id_table.editItem(self.id_table.currentItem())

    def _remove_row(self) -> None:
        values = list(self.section.id_table)
        if values:
            self._save_table(values[:-1])

    # ------------------------------------------------------------------ default fields

    def _build_defaults(self) -> QWidget:
        w = QWidget()
        self.defaults = QTableWidget(0, 3)
        self.defaults.setHorizontalHeaderLabels(["Field", "Use by default", "Required"])
        self.defaults.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.defaults.verticalHeader().setVisible(False)
        self.defaults.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.defaults.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.defaults.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ro = self.project.read_only

        buttons = QHBoxLayout()
        for text, slot in (("Move up", lambda: self._move_default(-1)),
                           ("Move down", lambda: self._move_default(1)),
                           ("Add custom field…", self._add_custom_default),
                           ("Apply to existing compounds…", self._apply_defaults)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setEnabled(not ro)
            buttons.addWidget(b)
        buttons.addStretch()

        help_ = QLabel("New compounds in this section start with the ticked fields, in this "
                       "order. Required fields decide whether a compound counts as complete. "
                       "Individual compounds can still add or remove fields.")
        help_.setWordWrap(True)
        layout = QVBoxLayout(w)
        layout.addWidget(help_)
        layout.addLayout(buttons)
        layout.addWidget(self.defaults)
        self._fill_defaults()
        return w

    def _default_rows(self) -> list[dict]:
        """Defaults first (in order), then every other available field, unticked."""
        current = [dict(e, use=True) for e in self.section.default_fields if e["key"] != "notes"]
        present = {e["key"] for e in current}
        others = [{"key": d.key, "required": False, "use": False}
                  for d in F.CORE_FIELDS + F.LIBRARY_FIELDS
                  if d.removable and d.key not in present]
        return current + others

    def _fill_defaults(self, rows: list[dict] | None = None, select: int | None = None) -> None:
        self._rows = rows if rows is not None else self._default_rows()
        self.defaults.setRowCount(len(self._rows))
        for r, entry in enumerate(self._rows):
            defn = F.field_def(entry)
            label = defn.label if defn else entry["key"]
            if entry.get("custom"):
                label += f"  (custom: {F.CUSTOM_TYPES.get(entry['custom'].get('type'), '')})"
            self.defaults.setItem(r, 0, _ro_item(label))
            for col, key in ((1, "use"), (2, "required")):
                box = QCheckBox()
                box.setChecked(bool(entry.get(key)))
                box.setEnabled(not self.project.read_only and (key == "use" or entry.get("use")))
                box.toggled.connect(lambda on, r=r, key=key: self._default_toggled(r, key, on))
                holder = QWidget()
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                hl.addWidget(box)
                self.defaults.setCellWidget(r, col, holder)
        if select is not None:
            self.defaults.selectRow(select)

    def _default_toggled(self, row: int, key: str, on: bool) -> None:
        self._rows[row][key] = on
        if key == "use" and not on:
            self._rows[row]["required"] = False
        self._save_defaults(select=row)

    def _save_defaults(self, select: int | None = None) -> None:
        entries = [{k: v for k, v in e.items() if k != "use"} for e in self._rows if e.get("use")]
        try:
            with self.project.undo_step(f"Change the default fields of “{self.section.name}”"):
                self.project.set_default_fields(self.section.id, entries)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
        # Custom fields that were unticked disappear from the list (they only live in
        # defaults). Rebuild after this click has finished: the checkbox that was clicked
        # is one of the widgets being replaced.
        rows = [e for e in self._rows if e.get("use") or not e.get("custom")]
        QTimer.singleShot(0, lambda: self._fill_defaults(rows, select))
        self.saved.emit("Default fields saved")

    def _move_default(self, step: int) -> None:
        row = self.defaults.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < len(self._rows):
            return
        self._rows[row], self._rows[target] = self._rows[target], self._rows[row]
        self._save_defaults(select=target)

    def _add_custom_default(self) -> None:
        dlg = CustomFieldDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            used = sum(1 for e in self._rows if e.get("use"))
            self._rows.insert(used, dict(dlg.entry(), required=False, use=True))
            self._save_defaults(select=used)

    def _apply_defaults(self) -> None:
        n = len(self.section.compounds)
        if not n:
            QMessageBox.information(self, "Apply defaults", "There are no compounds yet.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Apply default fields")
        box.setText(f"Give all {n} compounds in “{self.section.name}” the default fields, in "
                    "the default order?")
        box.setInformativeText("Missing default fields are added. Values already entered are "
                               "never deleted.")
        remove = QCheckBox("Also remove fields that are not defaults (their data is kept and "
                           "comes back if the field is added again)")
        box.setCheckBox(remove)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        try:
            with self.project.undo_step(f"Apply the default fields in “{self.section.name}”"):
                changed = self.project.apply_default_fields(self.section.id, remove.isChecked())
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not apply defaults", str(e))
            return
        self.refresh()
        self.saved.emit(f"Updated {changed} compound(s)")
