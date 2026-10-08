"""The Manuscript check page (SPEC §13o): a fixed item in the tree, just above References.

Add the manuscript (PDF or Word) and its ChemDraw files (.cdxml); the program reads them
and lists, compound by compound, where the manuscript and the SI disagree. Double-click a
row (or "Open compound") to go to the compound at the field concerned. Every result is a
warning: dismiss the ones that don't apply.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressDialog,
                               QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import manuscript as M
from ..core import ms_read
from ..core.project import Project, ProjectError

HINT = "color: palette(placeholder-text);"
ORANGE = QColor("#d35400")
GREEN = QColor("#2e7d32")
DIM = QColor(127, 127, 127)
COLUMNS = ("Compound", "What", "Manuscript", "SI", "Where", "Status")
STATUS_TEXT = {M.PROBLEM: "⚠ Check", M.NOTE: "ℹ Note", M.OK: "✓ Agrees",
               M.DISMISSED: "Dismissed"}
FILTERS = (("Problems only", "problems"), ("Problems and notes", "notes"),
           ("All results (also what agrees)", "all"))
ROLE = Qt.ItemDataRole.UserRole
FILES_SHOWN = 8           # the files list grows with the files up to this many rows
TABLE_MIN_HEIGHT = 190    # the results table never gets smaller (the page scrolls)


class ManuscriptPage(QWidget):
    saved = Signal(str)                   # status-bar message (as the other pages)
    open_compound = Signal(str, str)      # compound internal ID, field key
    changed = Signal()                    # files or dismissals changed (Warnings panel)

    def __init__(self, project: Project, parent: QWidget | None = None, auto: bool = True):
        super().__init__(parent)
        self.project = project
        self.result = M.Result()
        self._rows: list[M.Finding] = []
        ro = project.read_only

        title = QLabel("<h2>Manuscript check</h2>")
        intro = QLabel(
            "Add the manuscript and its ChemDraw files: the program compares them with the "
            "SI compound by compound (IDs, structures, yields, ee/er, dr, names and the "
            "conditions in footnotes). The files stay in the project's “manuscript” folder "
            "and are never printed. Nothing is sent anywhere.")
        intro.setWordWrap(True)
        intro.setStyleSheet(HINT)

        self.files = QListWidget()
        self.files.setSpacing(0)
        self.files.setMinimumHeight(self._list_height(2))
        self.files.itemDoubleClicked.connect(lambda *_: self._open_file())
        self.files.currentRowChanged.connect(lambda *_: self._update_buttons())
        self.add_ms_btn = QPushButton("Add manuscript (PDF or Word)…")
        self.add_ms_btn.clicked.connect(lambda: self.add_files("manuscript"))
        self.add_cd_btn = QPushButton("Add ChemDraw file (.cdxml)…")
        self.add_cd_btn.setToolTip("The ChemDraw files of the schemes give exact structures, "
                                   "labels and values. A .cdx file must first be saved as "
                                   ".cdxml in ChemDraw (File ▸ Save As).")
        self.add_cd_btn.clicked.connect(lambda: self.add_files("chemdraw"))
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setToolTip("Move the selected file to the project Trash")
        self.remove_btn.clicked.connect(lambda: self.remove_selected())
        self.open_file_btn = QPushButton("Open file")
        self.open_file_btn.clicked.connect(self._open_file)
        for b in (self.add_ms_btn, self.add_cd_btn, self.remove_btn):
            b.setEnabled(not ro)
        file_buttons = QHBoxLayout()
        for b in (self.add_ms_btn, self.add_cd_btn, self.remove_btn, self.open_file_btn):
            file_buttons.addWidget(b)
        file_buttons.addStretch()

        self.check_btn = QPushButton("Check again")
        self.check_btn.setToolTip("Read the files again and compare them with the SI")
        self.check_btn.clicked.connect(lambda: self.run_check(force=True))
        self.filter = QComboBox()
        for label, key in FILTERS:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(lambda *_: self._fill_table())
        self.show_dismissed = QCheckBox("Show dismissed")
        self.show_dismissed.setToolTip("Also list the results you dismissed (select one and "
                                       "click “Restore” to bring it back)")
        self.show_dismissed.toggled.connect(lambda *_: self._fill_table())
        self.open_btn = QPushButton("Open compound")
        self.open_btn.clicked.connect(self._open_selected)
        self.dismiss_btn = QPushButton("Dismiss…")
        self.dismiss_btn.clicked.connect(lambda: self.dismiss_selected())
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.clicked.connect(lambda: self.restore_selected())
        actions = QHBoxLayout()
        actions.addWidget(self.check_btn)
        actions.addWidget(QLabel("Show:"))
        actions.addWidget(self.filter)
        actions.addWidget(self.show_dismissed)
        actions.addStretch()
        for b in (self.open_btn, self.dismiss_btn, self.restore_btn):
            actions.addWidget(b)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        font = self.summary.font()
        font.setBold(True)
        self.summary.setFont(font)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color: #d35400;")
        self.notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents,
                                    QHeaderView.ResizeMode.ResizeToContents,
                                    QHeaderView.ResizeMode.Stretch,
                                    QHeaderView.ResizeMode.Stretch,
                                    QHeaderView.ResizeMode.Interactive,
                                    QHeaderView.ResizeMode.ResizeToContents)):
            header.setSectionResizeMode(col, mode)
        self.table.setColumnWidth(4, 220)
        self.table.setMinimumHeight(TABLE_MIN_HEIGHT)
        self.table.itemDoubleClicked.connect(lambda *_: self._open_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        hint = QLabel("Double-click a row to open the compound at that field. A result shows "
                      "what was found; select one and click “Dismiss…” if it doesn't apply "
                      "(it comes back if the values change). Dismissed results are hidden: "
                      "tick “Show dismissed” to see them, and “Restore” brings one back. "
                      "Results are also in the Warnings panel.")
        hint.setWordWrap(True)
        hint.setStyleSheet(HINT)

        # The files above, the results below, with a handle between them to give either
        # more room; the page scrolls when the window is too small for both.
        files_box = QWidget()
        files_layout = QVBoxLayout(files_box)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.addWidget(QLabel("<b>Files</b>"))
        files_layout.addWidget(self.files, 1)
        files_layout.addLayout(file_buttons)
        results_box = QWidget()
        results_layout = QVBoxLayout(results_box)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addLayout(actions)
        results_layout.addWidget(self.summary)
        results_layout.addWidget(self.notes)
        results_layout.addWidget(self.table, 1)
        results_layout.addWidget(hint)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(files_box)
        self.splitter.addWidget(results_box)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        content = QWidget()
        inner = QVBoxLayout(content)
        inner.addWidget(title)
        inner.addWidget(intro)
        inner.addWidget(self.splitter, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)
        self._fill_files()
        self._show_result()
        if auto:
            QTimer.singleShot(0, self, self.run_check)   # (not if the page is gone)

    # ------------------------------------------------------------------ files

    def flush(self) -> None:
        """Nothing waits to be saved here: every change is saved when it is made."""

    def _fill_files(self) -> None:
        self.files.clear()
        notes = {}
        for src in self._sources() or []:
            notes[src.file] = "\n".join([src.error] + src.notes if src.error else src.notes)
        for path in M.files(self.project):
            ext = path.suffix.lower()
            kind = {".pdf": "PDF", ".docx": "Word", ".cdxml": "ChemDraw", ".cdx": "ChemDraw (.cdx)",
                    ".doc": "Word (.doc)"}.get(ext, ext.lstrip("."))
            item = QListWidgetItem(f"{path.name}    ({kind})")
            item.setData(ROLE, path.name)
            note = notes.get(path.name, "")
            if ext in (".cdx", ".doc") or (note and any(
                    s.file == path.name and s.error for s in self._sources() or [])):
                item.setForeground(QBrush(ORANGE))
                item.setText("⚠ " + item.text())
            item.setToolTip(note or str(path))
            self.files.addItem(item)
        if not self.files.count():
            item = QListWidgetItem("No files yet: add the manuscript (PDF or Word) and, "
                                   "best, its ChemDraw files (.cdxml).")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.files.addItem(item)
        self._size_files()
        self._update_buttons()

    def _list_height(self, rows: int) -> int:
        row = max(self.files.sizeHintForRow(0) if self.files.count() else 0,
                  self.fontMetrics().lineSpacing() + 6)
        return rows * row + 2 * self.files.frameWidth() + 4

    def _size_files(self) -> None:
        """The files list is as tall as its files (up to FILES_SHOWN rows; with more,
        drag the handle under it to see more at once) and the results table gets the rest
        of the room; when the window is too small for both, the page scrolls."""
        n = max(2, self.files.count())
        if getattr(self, "_sized_for", None) == n:
            return      # (keep where the user dragged the handle)
        self._sized_for = n
        self.files.setMaximumHeight(self._list_height(n))
        self.files.setMinimumHeight(self._list_height(min(n, FILES_SHOWN)))
        top = self.splitter.widget(0)
        want = self._list_height(min(n, FILES_SHOWN)) + \
            top.sizeHint().height() - self.files.sizeHint().height()
        total = sum(self.splitter.sizes()) or (want + TABLE_MIN_HEIGHT + 120)
        self.splitter.setSizes([want, max(total - want, TABLE_MIN_HEIGHT)])

    def _sources(self):
        return M.cached_sources(self.project) if M.files(self.project) else []

    def _selected_file(self) -> str:
        item = self.files.currentItem()
        return item.data(ROLE) if item is not None and item.data(ROLE) else ""

    def add_files(self, which: str, paths: list[str] | None = None) -> None:
        if self.project.read_only:
            return
        if paths is None:
            if which == "chemdraw":
                flt = "ChemDraw files (*.cdxml *.cdx)"
                caption = "Add ChemDraw files"
            else:
                flt = "Manuscript (*.pdf *.docx *.doc)"
                caption = "Add the manuscript"
            paths, _ = QFileDialog.getOpenFileNames(self, caption, "", flt)
        if not paths:
            return
        added = []
        for p in paths:
            path = Path(p)
            if path.suffix.lower() not in M.MANUSCRIPT_EXTENSIONS + M.CHEMDRAW_EXTENSIONS:
                QMessageBox.information(self, "Manuscript check",
                                        f"“{path.name}” is not a PDF, Word or ChemDraw file.")
                continue
            try:
                with self.project.undo_step(f"Add manuscript file {path.name}",
                                            where=[("manuscript", "manuscript")]):
                    added.append(M.add_file(self.project, path).name)
            except (OSError, ProjectError) as e:
                QMessageBox.warning(self, "Manuscript check",
                                    f"“{path.name}” could not be added:\n{e}")
        if any(Path(a).suffix.lower() == ".cdx" for a in added):
            QMessageBox.information(
                self, "ChemDraw .cdx file",
                "This program can read ChemDraw files saved as .cdxml, not .cdx. Open the "
                "file in ChemDraw, use File ▸ Save As…, choose “CDXML” and add that file.")
        if added:
            self.saved.emit("Added " + ", ".join(added))
            self.changed.emit()
            self._fill_files()
            self.run_check()

    def remove_selected(self) -> None:
        name = self._selected_file()
        if not name or self.project.read_only:
            return
        if QMessageBox.question(self, "Remove file",
                                f"Move “{name}” to the project Trash? (It can be restored "
                                "from File ▸ Trash, or with Undo.)") != \
                QMessageBox.StandardButton.Yes:
            return
        try:
            with self.project.undo_step(f"Remove manuscript file {name}",
                                        where=[("manuscript", "manuscript")]):
                M.remove_file(self.project, name)
        except (OSError, ProjectError) as e:
            QMessageBox.warning(self, "Remove file", str(e))
            return
        self.saved.emit(f"Moved {name} to the Trash")
        self.changed.emit()
        self._fill_files()
        self.run_check()

    def _open_file(self) -> None:
        name = self._selected_file()
        if name:
            from .compile_ui import open_file
            open_file(M.folder(self.project) / name)

    # ------------------------------------------------------------------ checking

    def run_check(self, force: bool = False) -> None:
        """Read the files (unless nothing changed and not `force`) and compare."""
        if not M.files(self.project):
            M.forget(self.project)
            self.result = M.Result()
            self._show_result()
            return
        sources = None if force else M.cached_sources(self.project)
        if sources is None:
            sources = self._read_with_progress(force)
            if sources is None:      # stopped
                return
        self.result = M.check(self.project, sources)
        self._fill_files()
        self._show_result()
        self.changed.emit()

    def _read_with_progress(self, force: bool):
        dialog = QProgressDialog("Reading the manuscript…", "Stop", 0, 0, self)
        dialog.setWindowTitle("Manuscript check")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(400)
        dialog.setAutoClose(False)

        def progress(message: str) -> bool:
            dialog.setLabelText(message)
            QApplication.processEvents()
            return not dialog.wasCanceled()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return M.read_all(self.project, progress, force=force)
        except ms_read.Stopped:
            self.summary.setText("The check was stopped. Click “Check again” to finish it.")
            return None
        finally:
            QApplication.restoreOverrideCursor()
            dialog.close()
            dialog.deleteLater()

    def _show_result(self) -> None:
        self.summary.setText(self.result.summary())
        notes = list(dict.fromkeys(self.result.notes))
        self.notes.setText("\n".join(f"• {n}" for n in notes))
        self.notes.setVisible(bool(notes))
        self._fill_table()

    def _fill_table(self) -> None:
        mode = self.filter.currentData()
        keep = {"problems": (M.PROBLEM,), "notes": (M.PROBLEM, M.NOTE),
                "all": (M.PROBLEM, M.NOTE, M.OK)}[mode or "problems"]
        if self.show_dismissed.isChecked():
            keep += (M.DISMISSED,)
        rows = [f for f in self.result.findings if f.status in keep]
        self._rows = rows
        self.table.setRowCount(len(rows))
        for r, f in enumerate(rows):
            cells = (f.label, f.what, f.ms_value, f.si_value, f.where,
                     STATUS_TEXT.get(f.status, f.status))
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                tip = f.message + (f"\n\nWhere: {f.where}" if f.where else "") + \
                    (f"\n\nDismissed: “{f.reason}”" if f.reason else "")
                item.setToolTip(tip)
                if f.status == M.PROBLEM:
                    if col == 5:
                        item.setForeground(QBrush(ORANGE))
                elif f.status == M.OK:
                    if col == 5:
                        item.setForeground(QBrush(GREEN))
                else:
                    item.setForeground(QBrush(DIM))
                self.table.setItem(r, col, item)
        self.table.resizeRowsToContents()
        self._update_buttons()

    # ------------------------------------------------------------------ results

    def _selected_finding(self) -> M.Finding | None:
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _update_buttons(self) -> None:
        f = self._selected_finding()
        ro = self.project.read_only
        self.open_btn.setEnabled(f is not None and bool(f.compound_id))
        self.dismiss_btn.setEnabled(not ro and f is not None and f.status == M.PROBLEM)
        self.restore_btn.setEnabled(not ro and f is not None and f.status == M.DISMISSED)
        self.remove_btn.setEnabled(not ro and bool(self._selected_file()))
        self.open_file_btn.setEnabled(bool(self._selected_file()))
        self.check_btn.setEnabled(bool(M.files(self.project)))

    def _open_selected(self) -> None:
        f = self._selected_finding()
        if f is not None and f.compound_id:
            cid, field = f.compound_id, f.field
            # (after the click has finished: the page is replaced)
            QTimer.singleShot(0, self, lambda: self.open_compound.emit(cid, field))

    def dismiss_selected(self, reason: str | None = None) -> None:
        f = self._selected_finding()
        if f is None or f.status != M.PROBLEM or self.project.read_only:
            return
        if reason is None:
            from .warnings_panel import ask_reason
            reason = ask_reason(self, f.message)
            if reason is None:
                return
        with self.project.undo_step("Dismiss a manuscript warning",
                                    where=[("manuscript", "manuscript")]):
            M.dismiss(self.project, f, reason)
        self.result = M.check(self.project, self._sources())
        self._show_result()
        self.changed.emit()

    def restore_selected(self) -> None:
        f = self._selected_finding()
        if f is None or f.status != M.DISMISSED or self.project.read_only:
            return
        with self.project.undo_step("Restore a manuscript warning",
                                    where=[("manuscript", "manuscript")]):
            M.restore(self.project, f.key)
        self.result = M.check(self.project, self._sources())
        self._show_result()
        self.changed.emit()
