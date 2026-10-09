"""References (SPEC §9.5): the project's reference list, and the References field on a
compound page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import links
from ..core import references as refs
from ..core.fields import KNOWN_COMPOUND_SENTENCE
from ..core.project import Project, ProjectError
from .field_widgets import FieldEditor

HINT = "color: palette(placeholder-text);"


def _busy(func, *args):
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        return func(*args)
    finally:
        QApplication.restoreOverrideCursor()


class ReferenceEditDialog(QDialog):
    """Edit one reference by hand (or check one fetched from Crossref)."""

    def __init__(self, ref: dict, parent: QWidget | None = None, title: str = "Reference"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 520)
        self.ref = refs.clean(ref)
        self.type_box = QComboBox()
        for key, label in refs.TYPES.items():
            self.type_box.addItem(label, key)
        self.type_box.setCurrentIndex(max(0, self.type_box.findData(self.ref["type"])))
        self.authors = QPlainTextEdit("\n".join(self.ref["authors"]))
        self.authors.setPlaceholderText("One per line, Last, First — e.g.\nFulmer, Gregory R.\n"
                                        "Miller, Alexander J. M.")
        self.authors.setFixedHeight(90)
        self.edits: dict[str, QLineEdit] = {}
        form = QFormLayout()
        form.addRow("Type:", self.type_box)
        form.addRow("Authors:", self.authors)
        placeholders = {"title": "", "journal": "e.g. Org. Lett. (abbreviated)", "year": "2024",
                        "volume": "", "issue": "", "pages": "e.g. 2176–2179 or e202401234",
                        "doi": "10.1021/…", "url": "", "publisher": "for books",
                        "note": "anything else to print"}
        for key in ("title", "journal", "year", "volume", "issue", "pages", "doi", "url",
                    "publisher", "note"):
            edit = QLineEdit(self.ref[key])
            edit.setPlaceholderText(placeholders[key])
            self.edits[key] = edit
            label = {"journal": "Journal / book:", "doi": "DOI:", "url": "Web address:"}.get(
                key, key.capitalize() + ":")
            if key == "doi":
                row = QHBoxLayout()
                row.addWidget(edit, 1)
                look = QPushButton("Fill in from Crossref")
                look.setToolTip("Look this DOI up online and fill in the other boxes")
                look.clicked.connect(self._lookup)
                row.addWidget(look)
                form.addRow(label, row)
            else:
                form.addRow(label, edit)
        note = QLabel("The journal style decides how references are printed; enter them "
                      "plainly. Journal names are printed as entered, so use the abbreviation "
                      "(e.g. J. Org. Chem.).")
        note.setWordWrap(True)
        note.setStyleSheet(HINT)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _lookup(self) -> None:
        try:
            found = _busy(refs.fetch_doi, self.edits["doi"].text())
        except refs.ReferenceError as e:
            QMessageBox.warning(self, "Could not look up the DOI", str(e))
            return
        self.type_box.setCurrentIndex(max(0, self.type_box.findData(found["type"])))
        self.authors.setPlainText("\n".join(found["authors"]))
        for key, edit in self.edits.items():
            if found.get(key):
                edit.setText(found[key])

    def value(self) -> dict:
        out = dict(self.ref)
        out["type"] = self.type_box.currentData()
        out["authors"] = [a.strip() for a in self.authors.toPlainText().splitlines() if a.strip()]
        for key, edit in self.edits.items():
            out[key] = edit.text().strip()
        return refs.clean(out)


class ReferencesWidget(QWidget):
    """The project's reference list: add (DOI, by hand, BibTeX/RIS), edit, delete. Shown
    on the References page of the tree (SPEC §13n.10) and in the References window opened
    from a text box's Cite list. Each change is one step of Edit ▸ Undo."""

    changed = Signal(str)   # something was saved: a status-bar message

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        ro = project.read_only
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Reference", "Journal / book", "Title", "Cited in"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(lambda *_: self._edit())

        buttons = QHBoxLayout()
        for text, slot, tip in (
                ("Add by DOI…", self._add_doi, "Paste a DOI; the details are fetched from Crossref"),
                ("Add by hand…", self._add_manual, ""),
                ("Import BibTeX / RIS…", self._import, "e.g. exported from Zotero, EndNote or a "
                                                       "journal's “Export citation”"),
                ("Edit…", self._edit, ""), ("Delete…", self._delete, "")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            b.setEnabled(not ro)
            buttons.addWidget(b)
        buttons.addStretch()
        self.buttons = buttons
        intro = QLabel("Cite references with “Cite” in any text, or in a compound's References "
                       "field. Only cited references are printed, numbered in order of first "
                       "citation, in the journal style's format. Stored in references.json.")
        intro.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(intro)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        self._fill()

    def refresh(self) -> None:
        """Fill the list again (e.g. the "Cited in" column after texts were edited)."""
        ref = self._current()
        self._fill(ref["id"] if ref else None)

    def _step(self, label: str):
        return self.project.undo_step(label)

    def _fill(self, select: str | None = None) -> None:
        uses = links.citation_uses(self.project)
        header = self.table.horizontalHeader()
        # Measuring the auto-sized columns after every cell made a refill slow (seconds
        # with a few hundred references): they are measured once, after the fill.
        modes = [header.sectionResizeMode(c) for c in range(header.count())]
        for c in range(header.count()):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        try:
            self.table.setRowCount(len(self.project.references))
            for row, r in enumerate(self.project.references):
                where = uses.get(r["id"], [])
                journal = " ".join(x for x in (r.get("journal", ""), r.get("year", "")) if x)
                cells = [refs.short_label(r), journal, r.get("title", ""),
                         f"{len(where)} place{'s' if len(where) != 1 else ''}" if where
                         else "not cited"]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, r["id"])
                    if col == 3 and where:
                        item.setToolTip("\n".join(where))
                    self.table.setItem(row, col, item)
                if r["id"] == select:
                    self.table.selectRow(row)
        finally:
            for c, mode in enumerate(modes):
                header.setSectionResizeMode(c, mode)

    def _current(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.project.reference(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def _save(self, func, *args):
        """func(*args), or None if it could not be saved (the user is told why). A func
        that returns nothing gives True when it worked."""
        try:
            result = func(*args)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return None
        return True if result is None else result

    def _add_all(self, new: list[dict]) -> list[dict]:
        """Add several references with one save of references.json (an import)."""
        self.project.references = self.project.references + new
        self.project.save_references()
        return new

    def _change(self, func, *args):
        """func(*args), a change to the reference list. If it cannot be saved, the list is
        put back as it was (and the error raised), so the change is neither shown nor
        written by a later save of the list."""
        before = list(self.project.references)
        try:
            return func(*args)
        except BaseException:
            self.project.references = before
            raise

    def _add(self, ref: dict) -> None:
        existing = refs.find_duplicate(self.project.references, ref)
        if existing is not None:
            QMessageBox.information(self, "Already in the list",
                                    f"This reference is already in the list "
                                    f"({refs.short_label(existing)}).")
            self._fill(existing["id"])
            return
        with self._step(f"Add reference {refs.short_label(ref)}"):
            added = self._save(self._change, self.project.add_reference, ref)
        if added:
            self._fill(added["id"])
            self.changed.emit("Reference added")

    def _add_doi(self) -> None:
        doi, ok = QInputDialog.getText(self, "Add by DOI", "DOI (e.g. 10.1021/om100106e, or a "
                                                         "doi.org link):")
        if not ok or not doi.strip():
            return
        try:
            ref = _busy(refs.fetch_doi, doi)
        except refs.ReferenceError as e:
            if QMessageBox.question(self, "Could not look up the DOI",
                                    f"{e}\n\nEnter the reference by hand instead?"
                                    ) == QMessageBox.StandardButton.Yes:
                manual = refs.blank()
                manual["doi"] = refs.normalise_doi(doi)
                self._add_manual(manual)
            return
        dlg = ReferenceEditDialog(ref, self, "Check the reference")
        if dlg.exec():
            self._add(dlg.value())

    def _add_manual(self, start: dict | None = None) -> None:
        dlg = ReferenceEditDialog(start or refs.blank(), self, "New reference")
        if dlg.exec():
            self._add(dlg.value())

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import references", "",
                                              "BibTeX or RIS (*.bib *.ris *.txt);;All files (*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            QMessageBox.warning(self, "Could not read the file", str(e))
            return
        found = refs.parse_file_text(text)
        new = refs.new_references(self.project.references, found)
        if new:
            with self._step(f"Import references from {Path(path).name}"):
                saved = self._save(self._change, self._add_all, new)
            if saved is None:   # (could not be saved: the warning was shown, nothing added)
                self._fill()
                return
        added, skipped = len(new), len(found) - len(new)
        self._fill()
        if added:
            self.changed.emit(f"Imported {added} reference(s)")
        QMessageBox.information(self, "Import", f"Added {added} reference(s)."
                                + (f" {skipped} were already in the list." if skipped else "")
                                + ("" if found else " No references were found in that file."))

    def _edit(self) -> None:
        ref = self._current()
        if ref is None or self.project.read_only:
            return
        dlg = ReferenceEditDialog(ref, self, "Edit reference")
        if dlg.exec():
            with self._step(f"Edit reference {refs.short_label(ref)}"):
                ok = self._save(self._change, self.project.update_reference, dlg.value())
            self._fill(ref["id"])
            if ok:
                self.changed.emit("Reference saved")

    def _delete(self) -> None:
        ref = self._current()
        if ref is None:
            return
        where = links.citation_uses(self.project).get(ref["id"], [])
        extra = ("\n\nIt is cited in:\n• " + "\n• ".join(where[:8])
                 + ("\n…" if len(where) > 8 else "")
                 + "\nThose citations will show a warning and be left out of the SI."
                 ) if where else ""
        if QMessageBox.question(self, "Delete reference",
                                f"Delete {refs.short_label(ref)}?{extra}"
                                ) == QMessageBox.StandardButton.Yes:
            with self._step(f"Delete reference {refs.short_label(ref)}"):
                ok = self._save(self._change, self.project.delete_reference, ref["id"])
            self._fill()
            if ok:
                self.changed.emit("Reference deleted")


class ReferencesDialog(QDialog):
    """The reference list in a window of its own: opened from a text box's Cite list
    ("Add or edit references…"), so the page being typed on stays as it is."""

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("References")
        self.resize(900, 520)
        self.project = project
        self.widget = ReferencesWidget(project, self)
        self.table = self.widget.table
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.widget.buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.widget)


class ReferencesPage(QWidget):
    """The References item at the bottom of the tree (SPEC §13n.10): the reference list on
    a page of its own. It is always there and can't be moved or deleted."""

    saved = Signal(str)   # status-bar message (as the other pages)

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        title = QLabel("<h2>References</h2>")
        self.widget = ReferencesWidget(project, self)
        self.widget.changed.connect(self.saved)
        self.table = self.widget.table
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.widget, 1)

    def flush(self) -> None:
        """Nothing waits to be saved here: every change is saved when it is made."""

    def showEvent(self, event) -> None:  # noqa: N802
        self.widget.refresh()   # (citations may have been added since)
        super().showEvent(event)


class CitationsEditor(FieldEditor):
    """The References field: a sentence, then the cited references (SPEC §7.1 #11)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        value = self.compound.values.get(self.defn.key) or {}
        self.sentence = QLineEdit(str(value.get("text", KNOWN_COMPOUND_SENTENCE)))
        self.sentence.setPlaceholderText(KNOWN_COMPOUND_SENTENCE)
        self.sentence.setToolTip("Printed before the citations. Leave empty for the citations "
                                 "alone.")
        self.sentence.textEdited.connect(lambda *_: self.changed.emit())
        self.list = QListWidget()
        self.list.setFixedHeight(76)
        for ref_id in value.get("refs") or []:
            self._add_item(ref_id)
        add = QPushButton("Add citation…")
        add.clicked.connect(self._choose)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        for b in (add, remove):
            b.setEnabled(not self.project.read_only)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.sentence)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def _add_item(self, ref_id: str) -> None:
        ref = self.project.reference(ref_id)
        item = QListWidgetItem(refs.describe(ref) if ref else
                               "⚠ a reference that was deleted from the References list")
        item.setData(Qt.ItemDataRole.UserRole, ref_id)
        self.list.addItem(item)

    def ids(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _choose(self) -> None:
        from .rich_text import choose_references
        have = set(self.ids())
        for ref_id in choose_references(self):
            if ref_id not in have:
                self._add_item(ref_id)
                have.add(ref_id)
                self.changed.emit()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self.changed.emit()

    def store(self) -> None:
        self.compound.values[self.defn.key] = {"text": self.sentence.text().strip(),
                                               "refs": self.ids()}
