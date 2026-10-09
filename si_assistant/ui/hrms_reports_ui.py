"""HRMS report files on screen (SPEC §13n.3): the list in the HRMS field, and the read-only
"HRMS reports" page under the Spectra section in the tree.

The files are kept in the compound's hrms/ folder (core/project.py add_hrms_report); what
is read from them, and the found m/z picked, is core/ms_reports.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import ms_reports
from ..core.project import Compound, Project, ProjectError
from .structure_widgets import paste_key

WARN_COLOUR = "#d35400"


def clipboard_paths() -> list[Path]:
    """Files or folders copied in File Explorer or Finder."""
    data = QGuiApplication.clipboard().mimeData()
    if data is None or not data.hasUrls():
        return []
    return [Path(u.toLocalFile()) for u in data.urls() if u.isLocalFile()]


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


def print_choice_text(project: Project) -> str:
    on = bool(project.spectra_defaults().get("hrms_reports", False))
    return f"As the Spectra section says (now: {'print' if on else 'don’t print'})"


class HrmsReportsBox(QFrame):
    """The report files of one compound's HRMS field: one line each (open, what was read,
    ✕ to the Trash), "Add report files…", "Paste copied files", drag and drop or Ctrl+V of
    copied files, and whether they are printed in the SI."""

    files_changed = Signal()   # a file was added or removed (saved already)
    saved = Signal(str)        # status-bar message

    def __init__(self, project: Project, compound: Compound,
                 before_write: Callable[[], bool | None],
                 shown_id: Callable[[], str], parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.compound = compound
        self.before_write = before_write
        self.shown_id = shown_id
        ro = project.read_only
        self.setObjectName("hrmsReports")
        self.setAcceptDrops(not ro)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)   # (a click here lets Ctrl+V paste)

        title = QLabel("Report files from the instrument:")
        self.rows = QGridLayout()
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setHorizontalSpacing(6)
        self.add_btn = QPushButton("Add report files…")
        self.add_btn.setToolTip("Any file the instrument software made: a PDF report, CSV/TXT "
                                "or Excel peak list, mzML/mzXML/mzData, MGF, JCAMP-DX, netCDF, "
                                "a picture, or the raw data. A copy is stored with the "
                                "compound.")
        self.add_btn.clicked.connect(self._choose)
        self.paste_btn = QPushButton("Paste copied files")
        self.paste_btn.setToolTip("Add files (or folders) copied in File Explorer or Finder. "
                                  f"Click this box and press {paste_key()} does the same.")
        self.paste_btn.clicked.connect(self.paste)
        for b in (self.add_btn, self.paste_btn):
            b.setEnabled(not ro)
        self.drop_hint = _hint("…or drag files here. The found m/z is read from PDF, text, "
                               "table and data files.")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.paste_btn)
        buttons.addWidget(self.drop_hint, 1)

        self.print_label = QLabel("Print them in the SI:")
        self.print_box = QComboBox()
        self.print_box.setToolTip("Printed in the Spectra section, in its sub-section “HRMS "
                                  "reports” after all the spectra. The Spectra section's page "
                                  "sets this for every compound (off unless you turn it on).")
        self.print_box.setEnabled(not ro)
        self.print_box.activated.connect(self._print_chosen)
        print_row = QHBoxLayout()
        print_row.addWidget(self.print_label)
        print_row.addWidget(self.print_box)
        print_row.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(title)
        layout.addLayout(self.rows)
        layout.addLayout(buttons)
        layout.addLayout(print_row)
        paste = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self)
        paste.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        paste.activated.connect(self.paste)
        self.rebuild()

    # ------------------------------------------------------------------ the list

    def rebuild(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        items = ms_reports.report_items(self.compound)
        self.row_names: list[str] = []
        for r, item in enumerate(items):
            name = str(item.get("file", ""))
            path = self.project.hrms_report_path(self.compound, name)
            folder = item.get("kind") == "folder" or path.is_dir()
            # ("&&": a "&" in a file name is shown, not taken as an Alt+ shortcut)
            link = QPushButton(("📁 " if folder else "📄 ") + name.replace("&", "&&"))
            link.setFlat(True)
            link.setStyleSheet("text-align: left; text-decoration: underline;")
            link.setToolTip(f"{name}\nOpen" + (" the folder" if folder else
                                               " in the default program"))
            link.setProperty("keep_enabled", True)
            link.setMinimumWidth(80)
            link.clicked.connect(lambda _=False, p=path: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(p))))
            data = ms_reports.read_report(path)
            note = QLabel(data.message)
            note.setWordWrap(True)
            note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            note.setStyleSheet(f"color: {WARN_COLOUR};" if not data.readable or not data.count
                               else "color: palette(placeholder-text);")
            if not path.exists():
                link.setText(f"⚠ {name.replace('&', '&&')} (missing)")
            remove = QToolButton()
            remove.setText("✕")
            remove.setToolTip("Remove (moves it to the project Trash)")
            remove.setEnabled(not self.project.read_only)
            remove.clicked.connect(lambda _=False, n=name: self.remove(n))
            self.rows.addWidget(link, r, 0)
            self.rows.addWidget(note, r, 1)
            self.rows.addWidget(remove, r, 2, Qt.AlignmentFlag.AlignTop)
            self.row_names.append(name)
        self.rows.setColumnStretch(0, 0)
        self.rows.setColumnStretch(1, 1)
        has = bool(items)
        self.print_label.setVisible(has)
        self.print_box.setVisible(has)
        self.print_box.blockSignals(True)
        self.print_box.clear()
        self.print_box.addItem(print_choice_text(self.project), None)
        self.print_box.addItem("Print them", True)
        self.print_box.addItem("Don’t print them", False)
        own = (self.compound.values.get("hrms") or {}).get(ms_reports.PRINT_KEY)
        self.print_box.setCurrentIndex({True: 1, False: 2}.get(own, 0)
                                       if isinstance(own, bool) else 0)
        self.print_box.blockSignals(False)

    # ------------------------------------------------------------------ adding and removing

    def _choose(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add HRMS report files")
        self.add_paths([Path(p) for p in paths])

    def paste(self) -> None:
        if self.project.read_only:
            return
        paths = clipboard_paths()
        if not paths:
            self.saved.emit("Nothing to paste: copy the report files in File Explorer or "
                            "Finder first")
            return
        self.add_paths(paths)

    def add_paths(self, paths: list[Path]) -> None:
        paths = [Path(p) for p in paths if p]
        if not paths or self.project.read_only:
            return
        if self.before_write() is False:   # (the page's typing couldn't be saved: said so)
            return
        errors = []
        added = 0
        names = paths[0].name if len(paths) == 1 else f"{len(paths)} files"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)   # (raw data can be big)
        try:
            with self.project.undo_step(f"Add {names} to {self.shown_id()} ▸ HRMS"):
                for path in paths:
                    try:
                        self.project.add_hrms_report(self.compound, path)
                        added += 1
                    except (ProjectError, OSError) as e:
                        errors.append(f"{path.name}: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        self.rebuild()
        self.files_changed.emit()
        if added:   # (when none was added, the warning below says why)
            self.saved.emit(f"Added {names}" if not errors else
                            f"Added {added} of {len(paths)} files")
        if errors:
            QMessageBox.warning(self, "Some files were not added", "\n\n".join(errors))

    def remove(self, name: str) -> None:
        if QMessageBox.question(self, "Remove report file",
                                f"Remove {name}? It will be moved to the project Trash."
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.remove_now(name)

    def remove_now(self, name: str) -> None:
        if self.before_write() is False:
            return
        try:
            with self.project.undo_step(f"Remove {name} from {self.shown_id()} ▸ HRMS"):
                self.project.remove_hrms_report(self.compound, name)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove file", str(e))
        self.rebuild()
        self.files_changed.emit()

    def _print_chosen(self, index: int) -> None:
        if self.project.read_only:
            return
        choice = self.print_box.itemData(index)
        if self.before_write() is False:   # (show the stored choice again)
            value = self.compound.values.get("hrms")
            own = value.get(ms_reports.PRINT_KEY) if isinstance(value, dict) else None
            self.print_box.blockSignals(True)
            self.print_box.setCurrentIndex({True: 1, False: 2}.get(own, 0)
                                           if isinstance(own, bool) else 0)
            self.print_box.blockSignals(False)
            return
        try:
            with self.project.undo_step(f"Change the printing of {self.shown_id()}'s HRMS "
                                        "reports"):
                self.project.set_hrms_print(self.compound, choice)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
        self.saved.emit("Saved")

    # ------------------------------------------------------------------ dropping

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if not self.project.read_only and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths and not self.project.read_only:
            event.acceptProposedAction()
            self.add_paths(paths)


# ---------------------------------------------------------------------- the tree page

HRMS_ITEM = "hrms_reports"   # the tree item's kind and id (under the Spectra section)


def any_reports(project: Project) -> bool:
    return any(ms_reports.report_items(c) and "hrms" in c.field_keys
               for c in project.all_compounds())


DAMAGED = "could not be read: a note prints"


def describe_file(path: Path) -> str:
    """How one report file is printed, in a few words (short, so the page's "Printed"
    column is not cut off). Never raises: a damaged file says so."""
    try:
        return _describe_file(Path(path))
    except Exception:  # noqa: BLE001 — a damaged file must not break the page
        return DAMAGED


def _describe_file(path: Path) -> str:
    kind = ms_reports.print_kind(path)
    if not path.exists():
        return "missing: not printed"
    if kind == "pdf":
        from ..compile import document
        try:
            n = document.pdf_page_count(path)
            return f"PDF: {n} page{'s' if n != 1 else ''}, each as a page"
        except document.PdfUnreadable as e:
            if e.encrypted:
                return "password-protected PDF: not printed"
            return "PDF that can't be opened: not printed"
        except Exception:  # noqa: BLE001
            return "PDF that can't be opened: not printed"
    if kind == "picture":
        return "picture, at its own size"
    if kind == "listing":
        lines, left = ms_reports.listing_lines(path)
        if ms_reports.listing_failed(lines):
            return DAMAGED
        if left:
            return f"listing: first {len(lines)} of {len(lines) + left} lines"
        return f"listing of {len(lines)} line{'s' if len(lines) != 1 else ''}"
    if kind == "peaks":
        return "table of the peaks near the ion"
    return "raw data: not printed"


class HrmsReportsPage(QWidget):
    """Read-only: which compounds' HRMS reports are printed in the Spectra section's
    "HRMS reports" sub-section, and how each file prints."""

    saved = Signal(str)
    open_compound = Signal(str)

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        title = QLabel("HRMS reports")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        f.setBold(True)
        title.setFont(f)
        on = bool(project.spectra_defaults().get("hrms_reports", False))
        self.hint = _hint(
            "The compounds' HRMS report files are printed here, in a sub-section “HRMS "
            "reports” at the end of the Spectra section, compound by compound in tree order. "
            f"The Spectra section's “Print HRMS reports” is now {'on' if on else 'off'}; "
            "each compound can say otherwise in its HRMS field (or on its spectra page). "
            "This page only shows what will print: add or remove report files in each "
            "compound's HRMS field (double-click a compound to open it).")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Compound / file", "Printed"])
        self.tree.setColumnWidth(0, 240)
        self.tree.setWordWrap(True)          # (the "Printed" column wraps, never cut off)
        self.tree.setUniformRowHeights(False)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemDoubleClicked.connect(self._open)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.hint)
        layout.addWidget(self.tree, 1)
        self.fill()

    def fill(self) -> None:
        self.tree.clear()
        report = self.project.id_report()
        for c in self.project.all_compounds():
            items = ms_reports.report_items(c)
            if not items or "hrms" not in c.field_keys:
                continue
            printed = ms_reports.prints_reports(self.project, c)
            own = (c.values.get("hrms") or {}).get(ms_reports.PRINT_KEY)
            why = "its own choice" if isinstance(own, bool) else "the Spectra section's setting"
            top = QTreeWidgetItem([f"{report.display_id(c.id)} — {c.name or '(unnamed)'}",
                                   f"{'yes' if printed else 'no'} ({why})"])
            top.setData(0, Qt.ItemDataRole.UserRole, c.id)
            for item in items:
                name = str(item.get("file", ""))
                path = self.project.hrms_report_path(c, name)
                child = QTreeWidgetItem([name, describe_file(path) if printed else "—"])
                child.setToolTip(1, child.text(1))
                child.setData(0, Qt.ItemDataRole.UserRole, c.id)
                child.setToolTip(0, ms_reports.read_report(path).message)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        if not self.tree.topLevelItemCount():
            self.tree.addTopLevelItem(QTreeWidgetItem(["No compound has HRMS report files.",
                                                       ""]))

    def _open(self, item: QTreeWidgetItem, _column: int) -> None:
        cid = item.data(0, Qt.ItemDataRole.UserRole)
        if cid:
            self.open_compound.emit(cid)

    def flush(self) -> None:
        pass
