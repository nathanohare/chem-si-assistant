"""Editors for each kind of compound field (SPEC §7), plus the box around each
field with its flag toggle and remove button (SPEC §7.4).

Every editor:
  * emits `changed` when the user edits something;
  * `store()` copies what's on screen into the compound (in memory, or into
    its .txt file for long text). The Compound page then saves compound.json.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRegularExpression, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QRegularExpressionValidator
from PySide6.QtWidgets import (QApplication, QComboBox, QCompleter, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..core import fields as F
from ..core.ids import new_id
from ..core.project import ATTACHMENT_EXTENSIONS, Compound, Project, ProjectError
from .rich_text import RichTextEditor

# Short name of the storage-only field, used in hints ("Store additional files and folders").
STORE_FIELD_SHORT = F.ALL_FIELDS["private_files"].label.split(" (")[0]

# Numbers: digits with an optional sign and decimal point. Partial input is allowed.
NUMBER_RE = QRegularExpression(r"^[+\-−±]?\d*[.,]?\d*$")
COMBO_MIN_CHARS = 6   # an editable drop-down is at least this many characters wide


class FieldEditor(QWidget):
    changed = Signal()

    def __init__(self, project: Project, compound: Compound, defn: F.FieldDef,
                 before_write: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.compound = compound
        self.defn = defn
        # Called before any operation that saves compound.json itself (e.g. adding a
        # file), so on-screen edits elsewhere on the page are stored first.
        self.before_write = before_write

    def store(self) -> None:
        raise NotImplementedError

    def shown_id(self) -> str:
        return self.project.id_report().display_id(self.compound.id)

    def undo_step(self, label: str):
        """One step of Edit ▸ Undo for an action on this field (call before_write()
        first, so typing not saved yet is a step of its own)."""
        return self.project.undo_step(label)


# ---------------------------------------------------------------------- small inputs

CHOICE_KEY_ROLE = Qt.ItemDataRole.UserRole + 7   # an editable drop-down's choice, lower case


class SuggestingCompleter(QCompleter):
    """For an editable drop-down: while typing, the choices that contain what is typed
    (whatever its capitals: "ad-h" finds "Chiralpak AD-H") are suggested in a list under
    the box; one is used only when it is clicked (or picked with the arrow keys and
    Enter). Nothing is filled in by itself, and the text stays exactly as typed — also
    its capitals: the box does not swap "chiralpak ad-h" for an existing "Chiralpak AD-H"
    (it would with a case-insensitive completer, which Qt's drop-down also uses to match
    the typed text against its choices), so a choice's capitals can be corrected."""

    def __init__(self, box: QComboBox):
        super().__init__(box.model(), box)
        for i in range(box.count()):
            box.setItemData(i, box.itemText(i).casefold(), CHOICE_KEY_ROLE)
        self.setCompletionRole(CHOICE_KEY_ROLE)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
        self.setFilterMode(Qt.MatchFlag.MatchContains)

    def splitPath(self, path: str) -> list[str]:  # noqa: N802 (Qt's name)
        return [path.casefold()]

    def pathFromIndex(self, index) -> str:  # noqa: N802 — the choice as written
        return str(index.data(Qt.ItemDataRole.DisplayRole) or "")


def make_part_widget(part: F.Part, choices: list[str] | None = None) -> QWidget:
    """The input for one part of a field. `choices` replaces the part's own list (a
    drop-down that learns: its built-in choices plus the values used in the project)."""
    if part.kind in ("choice", "combo"):
        box = QComboBox()
        box.setEditable(part.kind == "combo")
        for choice in (part.choices if choices is None else choices):
            box.addItem(part.choice_labels.get(choice, choice), choice)
        if part.kind == "combo":
            box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            box.lineEdit().setPlaceholderText(part.placeholder)
            # What is typed is kept exactly as typed (SPEC §13l.13): matching choices are
            # only suggested in a list under the box, never filled in. (Qt's own
            # completion would complete "Chiralpak AD" to "Chiralpak AD-H" or "UV" to
            # "UV (254 nm)" by itself.)
            box.setCompleter(SuggestingCompleter(box))
            if part.learn:
                # Long learned choices must not make the page wider than the window: the
                # box is at least a few characters wide and grows with the page.
                box.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                box.setMinimumContentsLength(COMBO_MIN_CHARS)
        else:
            box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return box
    edit = QLineEdit()
    edit.setPlaceholderText(part.placeholder)
    if part.kind == "number":
        edit.setValidator(QRegularExpressionValidator(NUMBER_RE, edit))
        edit.setMaximumWidth(110)
    return edit


def set_part_value(widget: QWidget, part: F.Part, value: str) -> None:
    if isinstance(widget, QComboBox):
        index = widget.findData(value)
        if index >= 0:
            widget.setCurrentIndex(index)
        elif widget.isEditable():
            widget.setCurrentIndex(-1)
            widget.setEditText(value)
        elif value:
            widget.addItem(value, value)  # unknown stored value: keep it visible
            widget.setCurrentIndex(widget.count() - 1)
    else:
        widget.setText(value)


def get_part_value(widget: QWidget) -> str:
    if isinstance(widget, QComboBox):
        if widget.isEditable():
            text = widget.currentText().strip()
            index = widget.findText(text)
            return widget.itemData(index) if index >= 0 else text
        data = widget.currentData()
        return data if data is not None else widget.currentText()
    return widget.text().strip()


def connect_changed(widget: QWidget, slot) -> None:
    if isinstance(widget, QComboBox):
        widget.currentIndexChanged.connect(slot)
        if widget.isEditable():
            widget.editTextChanged.connect(slot)
    else:
        widget.textEdited.connect(slot)


class PartsEditor(FieldEditor):
    PER_ROW = 4
    # Fields whose parts are wide (labels and drop-downs) have fewer per row, so the page
    # fits beside the live preview at 1200 × 800 (ee: value and method, column and
    # eluent, flow and λ, the two retention times).
    PER_ROW_BY_FIELD = {"ee": 2}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        self.widgets: dict[str, QWidget] = {}
        value = self.compound.values.get(self.defn.key) or {}
        self.per_row = self.PER_ROW_BY_FIELD.get(self.defn.key, self.PER_ROW)
        for i, part in enumerate(self.defn.parts):
            cell = QHBoxLayout()
            if part.label:
                cell.addWidget(QLabel(part.label))
            choices = None
            if part.learn and part.kind == "combo":
                # A drop-down that learns (SPEC §13l.13): worked out each time the page
                # opens, so a value nothing uses any more is no longer offered.
                choices = F.learned_choices(
                    part, self.project.used_part_values(self.defn.key, part.key))
            w = make_part_widget(part, choices)
            if part.learn and part.kind == "combo":
                w.setToolTip("Pick from the list or type your own. The list also offers what "
                             "other compounds in this project use.")
            set_part_value(w, part, str(value.get(part.key, part.default)))
            connect_changed(w, lambda *_: self.changed.emit())
            if self.defn.key == "ee" and part.key == "value" and isinstance(w, QLineEdit):
                # "99%" -> "99% ee", "97:3" -> "97:3 er" when the box is left (SPEC §13l.12a).
                w.editingFinished.connect(lambda w=w: self._ee_unit(w))
            cell.addWidget(w, 1)
            if part.suffix:
                cell.addWidget(QLabel(part.suffix))
            self.widgets[part.key] = w
            grid.addLayout(cell, i // self.per_row, i % self.per_row)
        # Only columns holding free-text inputs grow; numbers and fixed lists stay compact.
        columns = min(len(self.defn.parts), self.per_row)
        grows = [any(p.kind in ("text", "combo") for p in self.defn.parts[c::self.per_row])
                 for c in range(columns)]
        for col, grow in enumerate(grows):
            grid.setColumnStretch(col, 1 if grow else 0)
        if not any(grows):
            grid.setColumnStretch(columns, 1)  # empty spacer column on the right

    def _ee_unit(self, edit: QLineEdit) -> None:
        """Add " ee" to a bare percentage and " er" to a bare ratio. The result is in the
        box, so it can still be changed by hand."""
        if edit.isReadOnly() or self.project.read_only:
            return
        text = edit.text()
        fixed = F.ee_with_unit(text)
        if fixed != text.strip():
            edit.setText(fixed)
            self.changed.emit()

    def store(self) -> None:
        self.compound.values[self.defn.key] = {k: get_part_value(w) for k, w in self.widgets.items()}


# ---------------------------------------------------------------------- long text

class RichTextFieldEditor(FieldEditor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = RichTextEditor(self, headings=False, height=150)
        self._saved = self.project.read_compound_text(self.compound, self.defn.key)
        self.editor.set_markup(self._saved)
        self.editor.changed.connect(self.changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.gp = None
        if self.defn.key == "procedure":
            from .procedure_widgets import GpChooser
            self.gp = GpChooser(self.project, self.compound, self)
            self.gp.changed.connect(self.changed)
            layout.addWidget(self.gp)
            self.own_label = QLabel("Further text, e.g. purification (added after the "
                                    "general procedure sentence):")
            self.own_label.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(self.own_label)
            self.gp.combo.currentIndexChanged.connect(self._show_own_label)
            self._show_own_label()
        if self.defn.key == "si_text":
            fill = QPushButton("Fill with generated text")
            fill.setToolTip("Start from the text the program would print for this compound, "
                            "then edit it. (Changes to the fields above will no longer reach "
                            "the SI while this text is filled in.)")
            fill.clicked.connect(self._fill_generated)
            note = QLabel("When this has text, it is printed instead of the generated text.")
            note.setStyleSheet("color: palette(placeholder-text);")
            row = QHBoxLayout()
            row.addWidget(fill)
            row.addWidget(note, 1)
            layout.addLayout(row)
        layout.addWidget(self.editor)

    def store(self) -> None:
        if self.gp is not None:
            self.gp.store()
        text = self.editor.markup()
        if text != self._saved:
            self.project.write_compound_text(self.compound, self.defn.key, text)
            self._saved = text

    def _show_own_label(self) -> None:
        self.own_label.setVisible(bool(self.gp.combo.currentData()))

    def _fill_generated(self) -> None:
        from ..compile.blocks import generated_paragraphs  # compile builds on the UI's data
        from ..core.markup import serialize
        if self.editor.markup().strip() and QMessageBox.question(
                self, "Replace text", "Replace the current SI text with the generated text?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.before_write()  # the generated text must reflect what's on screen now
        paragraphs = generated_paragraphs(self.project, self.compound,
                                          bool(self.project.settings.get("format_text", True)))
        self.editor.set_markup(serialize(paragraphs))
        self.changed.emit()


class NotesEditor(FieldEditor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Private notes and comments — never printed in the SI.")
        self.edit.setFixedHeight(90)
        from .text_checks import install_spelling
        install_spelling(self.edit)   # spelling only: notes are never printed (SPEC §13k.1a)
        self._saved = self.project.read_compound_text(self.compound, "notes")
        self.edit.setPlainText(self._saved)
        self.edit.textChanged.connect(self.changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)

    def store(self) -> None:
        text = self.edit.toPlainText()
        if text != self._saved:
            self.project.write_compound_text(self.compound, "notes", text)
            self._saved = text



# ---------------------------------------------------------------------- NMR reports

class NmrReportRow(QFrame):
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, report: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.report_id = report.get("id") or new_id("r")
        self.nucleus = QComboBox()
        self.nucleus.setEditable(True)
        self.nucleus.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for n in F.NUCLEI:
            self.nucleus.addItem(F.nucleus_label(n), n)
        self.nucleus.setMaximumWidth(90)
        set_part_value(self.nucleus, F.Part("nucleus"), report.get("nucleus", "1H"))
        self.label = QLineEdit(report.get("label", ""))
        self.label.setPlaceholderText("label (optional), e.g. major rotamer")
        remove = QToolButton()
        remove.setText("✕")
        remove.setToolTip("Remove this report")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        self.text = RichTextEditor(self, headings=False, height=70, links=False)
        self.text.set_markup(report.get("text", ""))

        top = QHBoxLayout()
        top.addWidget(QLabel("Nucleus"))
        top.addWidget(self.nucleus)
        top.addWidget(self.label, 1)
        top.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.addLayout(top)
        layout.addWidget(self.text)

        connect_changed(self.nucleus, lambda *_: self.changed.emit())
        self.label.textEdited.connect(lambda *_: self.changed.emit())
        self.text.changed.connect(self.changed)

    def value(self) -> dict:
        return {"id": self.report_id, "nucleus": get_part_value(self.nucleus),
                "label": self.label.text().strip(), "text": self.text.markup()}


class NmrEditor(FieldEditor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows: list[NmrReportRow] = []
        for report in self.compound.values.get(self.defn.key) or []:
            self._add_row(report)

        add = QToolButton()
        add.setText("+ Add report")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        for n in F.NUCLEI:
            menu.addAction(f"{F.nucleus_label(n)} NMR", lambda n=n: self._new_report(n))
        add.setMenu(menu)
        hint = QLabel("Use a label for repeated reports (e.g. major/minor rotamer).")
        hint.setStyleSheet("color: palette(placeholder-text);")
        bottom = QHBoxLayout()
        bottom.addWidget(add)
        bottom.addWidget(hint, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.rows_layout)
        layout.addLayout(bottom)

    def _add_row(self, report: dict) -> NmrReportRow:
        row = NmrReportRow(report, self)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._remove_row)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        return row

    def _new_report(self, nucleus: str) -> None:
        row = self._add_row({"nucleus": nucleus})
        row.text.edit.setFocus()
        self.changed.emit()

    def _remove_row(self, row: NmrReportRow) -> None:
        if row.text.markup().strip() and QMessageBox.question(
                self, "Remove NMR report",
                "Remove this report? Its text will be lost.") != QMessageBox.StandardButton.Yes:
            return
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self.changed.emit()

    def store(self) -> None:
        self.compound.values[self.defn.key] = [r.value() for r in self.rows]


# ---------------------------------------------------------------------- attached files

class AttachmentsEditor(FieldEditor):
    # A field to add to this compound (the "add it now" link in the note): field key.
    add_field_requested = Signal(str)
    files_changed = Signal()            # a file was added or removed (saved already)
    view_spectra_requested = Signal()   # "View spectra page" (the Spectra field)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(not self.project.read_only)
        self.rows_layout = QGridLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.row_widgets: list[tuple[str, dict[str, QWidget]]] = []

        add = QPushButton("Add files…")
        add.clicked.connect(self._choose)
        self.add_btn = add
        hint = QLabel("…or drag PDFs/images here. A copy is stored in the project.")
        hint.setStyleSheet("color: palette(placeholder-text);")
        hint.setWordWrap(True)
        self.drop_hint = hint
        bottom = QHBoxLayout()
        bottom.addWidget(add)
        bottom.addWidget(hint, 1)
        self.bottom_row = bottom
        self.view_btn: QPushButton | None = None
        if self.defn.key in ("spectra", "files"):
            # SPEC §13k.12: the compound's page under the Spectra section, where its
            # spectra are laid out (structure dragged, single spectra changed). Additional
            # files show it only when one of them is set to "Spectra section".
            self.view_btn = QPushButton("View spectra page")
            self.view_btn.setProperty("keep_enabled", True)   # (read-only projects too)
            self.view_btn.clicked.connect(self.view_spectra_requested)
            bottom.addWidget(self.view_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout = layout
        if self.defn.key == "files":
            layout.addWidget(self._placement_note())
        # The files' rows sit in a frame (the Spectra field outlines it while it is the
        # paste target, ui/spectrum_paste.py).
        self.rows_frame = self._rows_frame()
        self.rows_frame.setLayout(self.rows_layout)
        layout.addWidget(self.rows_frame)
        layout.addLayout(bottom)
        self._build_rows()

    def _rows_frame(self) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        return frame

    def _placement_note(self) -> QLabel:
        """What the Placement choices mean (SPEC §13k.9). When this compound doesn't have
        the storage field yet, the note says how to add it, with a link that adds it."""
        p = F.PLACEMENTS
        text = (f"Files set to “{p['inline']}” or “{p['appendix']}” are printed in the SI; "
                f"“{p['exclude']}” files are only stored. For raw data and other files that "
                f"are only stored, use the “{STORE_FIELD_SHORT}” field instead")
        if "private_files" in self.compound.field_keys:
            text += "."
        elif self.project.read_only:
            text += " (added with “+ Add field”)."
        else:
            text += (" — this page doesn't have it yet: "
                     "<a href='add'>add it now</a> (or use “+ Add field”).")
        note = QLabel(text)
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setStyleSheet("color: palette(placeholder-text);")
        note.linkActivated.connect(lambda _href: self.add_field_requested.emit("private_files"))
        self.placement_note = note
        return note

    def _build_rows(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout() is not None:   # a file's line of text boxes
                inner = item.layout()
                while inner.count():
                    child = inner.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
                inner.deleteLater()
        self.row_widgets = []
        labels = None
        if any(p.key == "label" for p in self.defn.item_parts):
            from ..core.labels import compute
            labels = compute(self.project)   # the automatic labels of inline files
        inline_index = 0
        for r, item in enumerate(self.compound.values.get(self.defn.key) or []):
            name = item.get("file", "")
            link = QPushButton(name)
            link.setFlat(True)
            link.setStyleSheet("text-align: left; text-decoration: underline;")
            link.setToolTip(f"{name}\nOpen in the default viewer")
            link.setProperty("keep_enabled", True)
            link.clicked.connect(lambda _=False, n=name: self._open(n))
            # (A long file name must not make the page wider than the window: it is cut
            # off on narrow pages, and shown whole in the tooltip.)
            link.setMinimumWidth(80)
            if not self.project.attachment_path(self.compound, self.defn.key, name).exists():
                link.setText(f"⚠ {name} (missing)")
            # Each file takes two lines: the file name with its choices and ✕, then its
            # text boxes underneath, so the field fits next to the live preview.
            top, below = 2 * r, 2 * r + 1
            texts = QHBoxLayout()
            texts.setContentsMargins(16, 0, 0, 6)
            self.rows_layout.addWidget(link, top, 0)
            widgets = {}
            inline = item.get("placement", "inline") == "inline"
            column = 1
            for part in self.defn.item_parts:
                w = make_part_widget(part)
                if part.label and part.kind == "text":
                    w.setPlaceholderText(part.label)
                    # Wide enough to read the start of what the box is for (the whole of
                    # it is in the tooltip); the boxes share the line's width.
                    w.setMinimumWidth(min(150, w.fontMetrics().horizontalAdvance(
                        part.label) + 16))
                set_part_value(w, part, str(item.get(part.key, part.default)))
                w.setToolTip(part.label)
                if part.key == "label":   # SPEC §13k.5a: the figure label of an inline file
                    found = labels.at(self.compound.id, inline_index) if (
                        labels is not None and inline) else None
                    w.setPlaceholderText(f"{found.auto} (automatic)" if found
                                         else "(label: only for inline files)")
                    w.setToolTip("The figure label printed with this file when it is shown "
                                 "inline. Leave empty to number it automatically.")
                    w.setMinimumWidth(min(180, w.fontMetrics().horizontalAdvance(
                        w.placeholderText() + "MM") + 16))
                    if found is not None and labels.is_duplicate(found.label):
                        w.setStyleSheet("QLineEdit { color: #d35400; }")
                connect_changed(w, lambda *_: self.changed.emit())
                if part.kind == "text":
                    texts.addWidget(w, 1)
                else:
                    self.rows_layout.addWidget(w, top, column)
                    column += 1
                widgets[part.key] = w
            remove = QToolButton()
            remove.setText("✕")
            remove.setToolTip("Remove (moves the file to the project Trash)")
            remove.clicked.connect(lambda _=False, n=name: self._remove(n))
            self.rows_layout.addWidget(remove, top, len(self.defn.item_parts) + 1)
            if texts.count():
                self.rows_layout.addLayout(texts, below, 0, 1, len(self.defn.item_parts) + 2)
            self.row_widgets.append((name, widgets))
            inline_index += 1 if inline else 0
        self.rows_layout.setColumnStretch(0, 1)
        self.refresh_view_button()

    def refresh_view_button(self) -> None:
        """"View spectra page": enabled when anything of this compound is printed in the
        Spectra section (its Spectra field, or Additional files set to "Spectra section");
        on the Additional files field it is shown only when one of its files is."""
        if self.view_btn is None:
            return
        from ..core import spectra as spectra_mod
        has = bool(spectra_mod.spectrum_items(self.project, self.compound))
        section = self.project.spectra_section is not None
        if self.defn.key != "spectra":
            self.view_btn.setVisible(any(
                isinstance(i, dict) and i.get("placement") == "appendix"
                for i in self.compound.values.get(self.defn.key) or []))
        self.view_btn.setEnabled(has and section)
        self.view_btn.setToolTip(
            "Show how this compound's spectra are laid out in the Spectra section: drag "
            "the structure, or change single spectra" if has and section else
            "Add a spectrum first (or set an Additional file to “Spectra section”)"
            if section else
            "This project has no Spectra section yet (it is added when the project is "
            "opened for editing)")

    def store(self) -> None:
        items = self.compound.values.get(self.defn.key) or []
        by_file = {i.get("file"): i for i in items}
        for name, widgets in self.row_widgets:
            if name in by_file:
                by_file[name].update({k: get_part_value(w) for k, w in widgets.items()})
        if self.defn.key != "spectra":   # files shown inline are figures: an ID to mention
            for item in items:
                if not item.get("id"):
                    item["id"] = new_id("f")

    def _open(self, name: str) -> None:
        path = self.project.attachment_path(self.compound, self.defn.key, name)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _choose(self) -> None:
        patterns = " ".join(f"*{e}" for e in ATTACHMENT_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(self, f"Add files to {self.defn.label}", "",
                                                f"PDF or image ({patterns})")
        self.add_files([Path(p) for p in paths])

    def add_files(self, paths: list[Path]) -> None:
        if not paths:
            return
        self.before_write()
        errors = []
        names = paths[0].name if len(paths) == 1 else f"{len(paths)} files"
        with self.undo_step(f"Add {names} to {self.shown_id()} ▸ {self.defn.label}"):
            for path in paths:
                guess = ({"type": guess_spectrum_type(path.name)}
                         if self.defn.key == "spectra" else None)
                try:
                    self.project.attach_compound_file(self.compound, self.defn.key, path, guess)
                except (ProjectError, OSError) as e:
                    errors.append(str(e))
        self._build_rows()
        self.files_changed.emit()
        if errors:
            QMessageBox.warning(self, "Some files were not added", "\n\n".join(errors))

    def _remove(self, name: str) -> None:
        if QMessageBox.question(self, "Remove file",
                                f"Remove {name}? It will be moved to the project Trash."
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.before_write()
        try:
            with self.undo_step(f"Remove {name} from {self.shown_id()} ▸ {self.defn.label}"):
                self.project.remove_compound_file(self.compound, self.defn.key, name)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove file", str(e))
        self._build_rows()
        self.files_changed.emit()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._dropped(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = self._dropped(event)
        if paths:
            event.acceptProposedAction()
            self.add_files(paths)

    def _dropped(self, event) -> list[Path]:
        if self.project.read_only or not event.mimeData().hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in event.mimeData().urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in ATTACHMENT_EXTENSIONS]


class PrivateFilesEditor(FieldEditor):
    """Any files or folders kept with the compound (raw data etc.). Never printed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(not self.project.read_only)
        self.rows_layout = QGridLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.notes: dict[str, QLineEdit] = {}
        add_files = QPushButton("Add files…")
        add_files.clicked.connect(self._choose_files)
        add_folder = QPushButton("Add folder…")
        add_folder.clicked.connect(self._choose_folder)
        open_dir = QPushButton("Open storage folder")
        open_dir.setProperty("keep_enabled", True)
        open_dir.clicked.connect(self._open_dir)
        hint = QLabel("…or drag files/folders here. Copies are kept in the project; "
                      "nothing here is printed.")
        hint.setStyleSheet("color: palette(placeholder-text);")
        hint.setWordWrap(True)
        bottom = QHBoxLayout()
        for w in (add_files, add_folder, open_dir):
            bottom.addWidget(w)
        bottom.addWidget(hint, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.rows_layout)
        layout.addLayout(bottom)
        self._build_rows()

    def _build_rows(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.notes = {}
        for r, item in enumerate(self.compound.values.get(self.defn.key) or []):
            name = item.get("file", "")
            icon = "📁" if item.get("kind") == "folder" else "📄"
            link = QPushButton(f"{icon} {name}")
            link.setFlat(True)
            link.setStyleSheet("text-align: left; text-decoration: underline;")
            link.setToolTip("Open")
            link.setProperty("keep_enabled", True)
            link.clicked.connect(lambda _=False, n=name: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.project.private_dir(self.compound) / n))))
            if not (self.project.private_dir(self.compound) / name).exists():
                link.setText(f"⚠ {name} (missing)")
            note = QLineEdit(item.get("note", ""))
            note.setPlaceholderText("note (optional)")
            note.textEdited.connect(lambda *_: self.changed.emit())
            remove = QToolButton()
            remove.setText("✕")
            remove.setToolTip("Remove (moves it to the project Trash)")
            remove.clicked.connect(lambda _=False, n=name: self._remove(n))
            self.rows_layout.addWidget(link, r, 0)
            self.rows_layout.addWidget(note, r, 1)
            self.rows_layout.addWidget(remove, r, 2)
            self.notes[name] = note
        self.rows_layout.setColumnStretch(0, 1)
        self.rows_layout.setColumnStretch(1, 1)

    def store(self) -> None:
        for item in self.compound.values.get(self.defn.key) or []:
            if item.get("file") in self.notes:
                item["note"] = self.notes[item["file"]].text().strip()

    def add_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        self.before_write()
        errors = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)  # folders can be large
        names = paths[0].name if len(paths) == 1 else f"{len(paths)} items"
        try:
            with self.undo_step(f"Store {names} with {self.shown_id()}"):
                for path in paths:
                    try:
                        self.project.add_private_item(self.compound, path)
                    except (ProjectError, OSError) as e:
                        errors.append(f"{path.name}: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        self._build_rows()
        if errors:
            QMessageBox.warning(self, "Some items were not added", "\n\n".join(errors))

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files to keep with this compound")
        self.add_paths([Path(p) for p in paths])

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a folder to keep with this compound")
        if folder:
            self.add_paths([Path(folder)])

    def _open_dir(self) -> None:
        folder = self.project.private_dir(self.compound)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _remove(self, name: str) -> None:
        if QMessageBox.question(self, "Remove", f"Remove {name}? It will be moved to the "
                                "project Trash.") != QMessageBox.StandardButton.Yes:
            return
        self.before_write()
        try:
            with self.undo_step(f"Remove {name} from {self.shown_id()}"):
                self.project.remove_private_item(self.compound, name)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove", str(e))
        self._build_rows()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if not self.project.read_only and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)


def guess_spectrum_type(file_name: str) -> str:
    """Guess the spectrum type from a file name like "3a_13C.pdf" or "3a-HSQC.pdf"."""
    name = file_name.lower().replace(" ", "")
    for key, words in (("13C", ("13c", "carbon")), ("19F", ("19f",)), ("31P", ("31p",)),
                       ("11B", ("11b",)), ("2D NMR", ("hsqc", "hmbc", "cosy", "noesy", "roesy")),
                       ("HPLC/SFC", ("hplc", "sfc")), ("IR", ("_ir", "-ir", "ftir")),
                       ("1H", ("1h", "proton"))):
        if any(w in name for w in words):
            return key
    return "1H"


def editor_type(defn: F.FieldDef) -> type[FieldEditor]:
    """The editor class for a field. The structure/HRMS editors live in structure_widgets,
    which builds on this module, so they are looked up here when needed."""
    from .structure_widgets import HrmsEditor, StructureEditor
    if defn.key == "hrms":
        return HrmsEditor
    if defn.key == "spectra" and defn.kind == "attachments":
        from .spectrum_paste import SpectraEditor   # (a paste target, SPEC §13l.1)
        return SpectraEditor
    if defn.kind == "citations":
        from .references_ui import CitationsEditor
        return CitationsEditor
    return {"parts": PartsEditor, "richtext": RichTextFieldEditor, "nmr": NmrEditor,
            "attachments": AttachmentsEditor, "structure": StructureEditor,
            "private": PrivateFilesEditor, "notes": NotesEditor}[defn.kind]

# ---------------------------------------------------------------------- field box

# Fields the user collapsed, by field key. Kept for the whole session so that moving to
# the next compound keeps the same fields collapsed.
COLLAPSED: set[str] = set()


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class FieldBox(QFrame):
    """A field's title, required marker, flag toggle, remove button and editor.
    It can be collapsed to one line showing a short summary of its value."""

    changed = Signal()
    remove_requested = Signal(str)
    dismiss_requested = Signal(object)  # a checks.Warning

    def __init__(self, project: Project, compound: Compound, defn: F.FieldDef, required: bool,
                 before_write: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.warnings: list = []
        self.compound = compound
        self.defn = defn
        self.required = required
        self.setObjectName("FieldBox")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.toggle = QToolButton()
        self.toggle.setAutoRaise(True)
        self.toggle.setToolTip("Collapse or expand this field")
        self.toggle.setProperty("keep_enabled", True)
        self.toggle.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        title = ClickableLabel(f"<b>{defn.label}</b>" + (
            ' <span style="color:#c0392b" title="Required">* required</span>' if required else ""))
        title.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        if defn.description:
            title.setToolTip(defn.description)
        self.summary = QLabel()
        self.summary.setStyleSheet("color: palette(placeholder-text);")

        flag = compound.flags.get(defn.key)
        self.flag_btn = QToolButton()
        self.flag_btn.setText("⚑ Flag")
        self.flag_btn.setCheckable(True)
        self.flag_btn.setChecked(flag is not None)
        self.flag_btn.setToolTip("Mark this field as needing attention (never printed)")
        self.flag_btn.toggled.connect(self._flag_toggled)
        self.flag_comment = QLineEdit(flag.get("comment", "") if flag else "")
        self.flag_comment.setPlaceholderText("What needs attention? e.g. re-run ¹³C, poor S/N")
        self.flag_comment.setVisible(flag is not None)
        self.flag_comment.textEdited.connect(lambda *_: self.changed.emit())

        header = QHBoxLayout()
        header.addWidget(self.toggle)
        header.addWidget(title)
        header.addWidget(self.summary, 1)
        if defn.key != "notes":
            header.addWidget(self.flag_btn)
        if defn.removable:
            remove = QToolButton()
            remove.setText("✕")
            remove.setToolTip("Remove this field from this compound "
                              "(its data is kept and comes back if you add the field again)")
            remove.clicked.connect(lambda: self.remove_requested.emit(defn.key))
            header.addWidget(remove)

        self.editor = editor_type(defn)(project, compound, defn, before_write, self)
        self.editor.changed.connect(self.changed)

        self.warn_box = QWidget()
        self.warn_layout = QVBoxLayout(self.warn_box)
        self.warn_layout.setContentsMargins(0, 0, 0, 0)
        self.warn_box.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addLayout(header)
        layout.addWidget(self.warn_box)
        layout.addWidget(self.flag_comment)
        layout.addWidget(self.editor)
        self._style_flag()
        self.collapsed = False
        self.set_collapsed(defn.key in COLLAPSED)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        (COLLAPSED.add if collapsed else COLLAPSED.discard)(self.defn.key)
        self.editor.setVisible(not collapsed)
        self.flag_comment.setVisible(not collapsed and self.flag_btn.isChecked())
        self.toggle.setText("▸" if collapsed else "▾")
        self.summary.setVisible(collapsed)
        if collapsed:
            self.refresh_summary()

    def refresh_summary(self) -> None:
        """One line shown while collapsed, e.g. "✔ 92% · 28.4 mg" or "✖ empty"."""
        from ..core import dashboard as D
        try:
            filled = self.project.field_filled(self.compound, self.defn)
            text = D.field_summary(self.project, self.compound, self.defn)
        except Exception:  # noqa: BLE001 — a summary must never break the page
            filled, text = False, ""
        icon = "✔" if filled else ("✖" if self.required else "○")
        flag = "  ⚑" if self.flag_btn.isChecked() else ""
        flag += "  ⚠" if self.warnings else ""
        self.summary.setText(f"{icon} {text or ('empty' if not filled else '')}{flag}")

    def set_warnings(self, warnings: list) -> None:
        """Show the automatic warnings about this field, each with a Dismiss button."""
        self.warnings = list(warnings)
        while self.warn_layout.count():
            item = self.warn_layout.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        for w in self.warnings:
            label = QLabel(f"⚠ {w.message}")
            label.setWordWrap(True)
            label.setStyleSheet("color: #d35400;")
            row = QHBoxLayout()
            row.addWidget(label, 1)
            if not self.project.read_only:
                dismiss = QToolButton()
                dismiss.setText("Dismiss…")
                dismiss.setToolTip("Hide this warning for this compound (it comes back if the "
                                   "data changes)")
                dismiss.clicked.connect(lambda _=False, w=w: self.dismiss_requested.emit(w))
                row.addWidget(dismiss, 0, Qt.AlignmentFlag.AlignTop)
            self.warn_layout.addLayout(row)
        self.warn_box.setVisible(bool(self.warnings))
        if self.collapsed:
            self.refresh_summary()

    def _flag_toggled(self, on: bool) -> None:
        self.flag_comment.setVisible(on and not self.collapsed)
        if on:
            self.flag_comment.setFocus()
        self._style_flag()
        self.changed.emit()

    def _style_flag(self) -> None:
        flagged = self.flag_btn.isChecked()
        self.setStyleSheet("QFrame#FieldBox { border: 2px solid #e67e22; border-radius: 4px; }"
                           if flagged else "")
        self.flag_btn.setText("⚑ Flagged" if flagged else "⚑ Flag")

    def store(self, user: str) -> None:
        self.editor.store()
        if self.flag_btn.isChecked():
            old = self.compound.flags.get(self.defn.key, {})
            self.compound.flags[self.defn.key] = {
                "comment": self.flag_comment.text().strip(),
                "by": old.get("by") or user,
                "at": old.get("at") or datetime.now().isoformat(timespec="seconds")}
        else:
            self.compound.flags.pop(self.defn.key, None)
