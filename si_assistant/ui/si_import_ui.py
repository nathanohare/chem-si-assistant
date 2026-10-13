"""File ▸ Import SI… (SPEC §13u): reading the file with a progress window, and the review
window that shows everything recognised (core/si_import.py) before anything is written
(core/si_import_apply.py).

Every value can be changed in the review window, and every item can be left out; the
compounds' text that was not recognised is shown and goes to their private Notes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressDialog,
                               QRadioButton, QScrollArea, QSplitter, QStackedWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import fields as F
from ..core.markup import parse
from ..core import si_import, si_import_read
from ..core.si_import import ImportedCompound, ImportedGP, ImportPlan
from ..core.si_import_apply import PRINT_AS_IMPORTED, PRINT_FROM_FIELDS

NEW_SECTION = "__new__"
WARN = "⚠ "


def read_with_progress(parent: QWidget, path: Path) -> si_import_read.ReadResult | None:
    """Read the file, showing a progress window for a PDF; None if the user stopped it."""
    dialog = QProgressDialog(f"Reading “{path.name}”…", "Stop", 0, 100, parent)
    dialog.setWindowTitle("Import SI")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(400)

    def progress(done: int, total: int) -> bool:
        dialog.setMaximum(max(total, 1))
        dialog.setValue(done)
        QApplication.processEvents()
        return not dialog.wasCanceled()
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        result = si_import_read.read_file(path, progress)
    finally:
        QApplication.restoreOverrideCursor()
        dialog.reset()
        dialog.deleteLater()
    if result.stopped:
        return None
    return result


def _plain_edit(text: str, read_only: bool = False, height: int = 80) -> QPlainTextEdit:
    """A text box about as tall as its text (between 2 and 10 lines)."""
    edit = QPlainTextEdit(text)
    edit.setReadOnly(read_only)
    edit.setTabChangesFocus(True)
    line = edit.fontMetrics().lineSpacing()
    lines = sum(1 + len(t) // 110 for t in (text or "").split("\n"))
    edit.setFixedHeight(int(min(max(lines, 2), max(10, height // line)) * line + 12))
    return edit


def _plain_markup(text: str) -> str:
    """Stored text without its formatting marks, to show in a label."""
    return "\n".join("".join(r.text for r in p.runs) for p in parse(text))


class _GpPage(QWidget):
    """One general procedure: its name, title, compound sentence and text."""

    def __init__(self, gp: ImportedGP, users: list[str]):
        super().__init__()
        self.gp = gp
        form = QFormLayout(self)
        self.name = QLineEdit(gp.name)
        self.title = QLineEdit(gp.title)
        self.sentence = QLineEdit(gp.sentence)
        self.sentence.setToolTip(
            "The sentence printed in each compound made by this procedure. Words in "
            "{braces} are filled in per compound; {GP}, {product}, {scale} and {yield} are "
            "filled in by the program.")
        self.text = _plain_edit(gp.text, height=160)
        form.addRow("Name:", self.name)
        form.addRow("Title:", self.title)
        form.addRow("Compound sentence:", self.sentence)
        form.addRow("Text:", self.text)
        used = QLabel(("Used by: " + ", ".join(users)) if users else
                      "No compound was found citing this procedure.")
        used.setWordWrap(True)
        form.addRow("", used)
        form.addRow("As printed:", _plain_edit(gp.original, read_only=True, height=100))
        self.name.textChanged.connect(lambda t: setattr(gp, "name", t.strip()))
        self.title.textChanged.connect(lambda t: setattr(gp, "title", t.strip()))
        self.sentence.textChanged.connect(lambda t: setattr(gp, "sentence", t))
        self.text.textChanged.connect(lambda: setattr(gp, "text", self.text.toPlainText()))


class _Thumb(QLabel):
    """A spectrum's picture, drawn the first time it is shown (there can be hundreds)."""

    def __init__(self, spectrum, width: int = 240, height: int = 150):
        super().__init__("…")
        self.spectrum = spectrum
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: white; border: 1px solid palette(mid);")
        self._drawn = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._drawn:
            return
        self._drawn = True
        from PySide6.QtGui import QPixmap
        try:
            if self.spectrum.kind == "emf":
                from .clipboard_image import render_emf_bytes
                image = render_emf_bytes(self.spectrum.data, 50)
            else:
                image = _pdf_image(self.spectrum.data)
        except Exception:  # noqa: BLE001 — a picture that can't be drawn here is still kept
            image = None
        if image is None or image.isNull():
            self.setText("(no preview here)")
            return
        self.setPixmap(QPixmap.fromImage(image).scaled(
            self.width() - 4, self.height() - 4, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))


class _SpectrumRow(QWidget):
    """One spectrum: its picture, its type and compound (both changeable, SPEC §2.7), how
    it was matched, and for a matched one a tick box. The compound list holds the
    compounds themselves, so a choice follows an ID changed in the review window."""

    def __init__(self, s, compounds: list, matched: bool = True):
        super().__init__()
        from PySide6.QtWidgets import QCheckBox
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.addWidget(_Thumb(s))
        side = QVBoxLayout()
        self.kind = kind = QComboBox()
        for t in F.SPECTRUM_TYPES:
            kind.addItem(F.SPECTRUM_TYPE_LABELS.get(t, t), t)
        kind.setCurrentIndex(max(0, kind.findData(s.type)))
        kind.currentIndexChanged.connect(lambda _i: setattr(s, "type", kind.currentData()))
        row = QHBoxLayout()
        row.addWidget(QLabel("Type:"))
        row.addWidget(kind)
        self.which = which = QComboBox()
        which.addItem("— not imported —", None)
        for c in compounds:
            which.addItem(f"{c.cid}  {c.name}".strip()[:60], c)
            if c.cid == s.cid:
                which.setCurrentIndex(which.count() - 1)
        which.currentIndexChanged.connect(
            lambda _i: setattr(s, "cid", which.currentData().cid if which.currentData() else ""))
        row.addWidget(QLabel("Compound:"))
        row.addWidget(which, 1)
        side.addLayout(row)
        info = QLabel("; ".join(x for x in (s.title, s.where, s.how) if x))
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(placeholder-text);")
        side.addWidget(info)
        if matched:
            keep = QCheckBox("Import this spectrum")
            keep.setChecked(s.keep)
            keep.toggled.connect(lambda on: setattr(s, "keep", on))
            side.addWidget(keep)
        side.addStretch()
        lay.addLayout(side, 1)


class _UnmatchedSpectraPage(QScrollArea):
    """The spectra whose compound could not be told: choose it, or leave them out."""

    def __init__(self, spectra: list, compounds: list):
        super().__init__()
        self.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        intro = QLabel("These spectra could not be matched to a compound (no ID next to them, "
                       "and their peaks fit no compound's peak report clearly). Choose the "
                       "compound for those you want; the others are not imported.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        for s in spectra:
            lay.addWidget(_SpectrumRow(s, compounds, matched=False))
        lay.addStretch()
        self.setWidget(inner)


class _CompoundPage(QScrollArea):
    """One compound: ID, name, procedure, each recognised field, what was not recognised,
    the text as printed, and its spectra."""

    def _id_changed(self, old: list[str], new: str, spectra: list) -> None:
        for s in spectra:
            if s.cid == old[0]:
                s.cid = new.strip()
        old[0] = new.strip()

    def __init__(self, c: ImportedCompound, gp_name: str, spectra: list | None = None,
                 compounds: list | None = None):
        spectra = spectra if spectra is not None else []
        compounds = compounds if compounds is not None else [c]
        super().__init__()
        self.c = c
        self.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        self.cid = QLineEdit(c.cid)
        self.name = QLineEdit(c.name)
        self.cid.textChanged.connect(lambda t: setattr(c, "cid", t.strip()))
        self.name.textChanged.connect(lambda t: setattr(c, "name", t.strip()))
        form.addRow("ID:", self.cid)
        form.addRow("Name:", self.name)
        if c.structure or c.picture_pdf:
            form.addRow("Structure:", self._structure_row(c))
        if c.warnings:
            warn = QLabel("\n".join(WARN + w for w in c.warnings))
            warn.setWordWrap(True)
            form.addRow("", warn)
        if gp_name:
            gp = QLabel(f"Made by {gp_name}; its sentence: {_plain_markup(c.gp_sentence)}"
                        + ("" if not c.gp_values else "\nIts values: " + "; ".join(
                            f"{k} = {v}" for k, v in c.gp_values.items())))
            gp.setWordWrap(True)
            form.addRow("General procedure:", gp)
        self.procedure = _plain_edit(c.procedure, height=90)
        self.procedure.textChanged.connect(
            lambda: setattr(c, "procedure", self.procedure.toPlainText()))
        form.addRow("Procedure:", self.procedure)
        for key in list(c.values):
            defn = F.ALL_FIELDS.get(key)
            if defn is None:
                continue
            if key == "nmr":
                for report in c.values["nmr"]:
                    form.addRow(self._nmr_row(report))
            elif key == "references":
                ref = c.values["references"]
                edit = QLineEdit(ref.get("text", ""))
                edit.textChanged.connect(lambda t, r=ref: r.__setitem__("text", t))
                note = (f" (cites {', '.join(c.cites)}: linked in a later version)"
                        if c.cites else "")
                form.addRow(defn.label + ":", edit)
                if note:
                    form.addRow("", QLabel(note.strip()))
            elif defn.kind == "parts":
                form.addRow(defn.label.replace("&", "&&") + ":", self._parts_row(defn, c.values[key]))
        self.unrecognised = _plain_edit("\n".join(c.unrecognised), height=60)
        self.unrecognised.setToolTip("Text of this compound that is neither its procedure nor "
                                     "a recognised value. It is kept in the compound's private "
                                     "Notes (never printed); delete what you don't need.")
        self.unrecognised.textChanged.connect(lambda: setattr(
            c, "unrecognised", [t for t in self.unrecognised.toPlainText().split("\n")
                                if t.strip()]))
        form.addRow("Not recognised\n(→ Notes):", self.unrecognised)
        mine = [s for s in spectra if s.cid == c.cid]
        if mine:
            box = QWidget()
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            for s in mine:
                lay.addWidget(_SpectrumRow(s, compounds))
            form.addRow("Spectra:", box)
        # (an ID changed here: its spectra follow)
        self.cid.textChanged.connect(lambda t, old=[c.cid]: self._id_changed(old, t, spectra))
        form.addRow("As printed:", _plain_edit(c.original, read_only=True, height=120))
        self.setWidget(inner)

    @staticmethod
    def _structure_row(c: ImportedCompound) -> QWidget:
        """The structure found (SPEC §13u.7), drawn, with where it came from and a tick box
        to leave it out."""
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QCheckBox
        from ..core import chem
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        picture = QLabel()
        formula = ""
        try:
            if c.structure:
                mol = chem.parse_structure(c.structure)
                pixmap = QPixmap()
                pixmap.loadFromData(chem.structure_png(mol, 260, 160))
                formula = chem.mol_info(mol).formula
            else:                                # a picture cut out of a PDF
                pixmap = QPixmap.fromImage(_pdf_image(c.picture_pdf)).scaled(
                    260, 160, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            picture.setPixmap(pixmap)
        except Exception:  # noqa: BLE001 — a structure that can't be drawn is still shown
            picture.setText("(can't be drawn)")
        lay.addWidget(picture)
        side = QVBoxLayout()
        note = QLabel((f"{formula}, " if formula else "") + (c.structure_note or "found")
                      + (" (with ChemDraw's picture)" if c.structure_picture else ""))
        note.setWordWrap(True)
        use = QCheckBox("Import this structure")
        use.setChecked(c.use_structure)
        use.toggled.connect(lambda on: setattr(c, "use_structure", on))
        side.addWidget(note)
        side.addWidget(use)
        side.addStretch()
        lay.addLayout(side, 1)
        return box

    def _parts_row(self, defn: F.FieldDef, value: dict) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        for part in defn.parts:
            edit = QLineEdit(str(value.get(part.key, "") or ""))
            edit.setPlaceholderText(part.label or part.key)
            edit.setToolTip(part.label or part.key)
            edit.textChanged.connect(lambda t, k=part.key: value.__setitem__(k, t))
            if part.label:
                lay.addWidget(QLabel(part.label + ":"))
            lay.addWidget(edit, 1)
            if part.suffix:
                lay.addWidget(QLabel(part.suffix))
        return row

    @staticmethod
    def _nmr_row(report: dict) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"NMR ({F.nucleus_label(report.get('nucleus') or '')}) — label:"))
        label = QLineEdit(report.get("label", ""))
        label.setPlaceholderText("e.g. major rotamer")
        label.textChanged.connect(lambda t: report.__setitem__("label", t))
        head.addWidget(label, 1)
        lay.addLayout(head)
        text = _plain_edit(report.get("text", ""), height=60)
        text.textChanged.connect(lambda: report.__setitem__("text", text.toPlainText()))
        lay.addWidget(text)
        return box


# ---------------------------------------------------------------------- phase I4 pages

KIND_NOTES = {"general": "Goes to the front matter's General Information page",
              "text": "A section", "data": "A section of compounds",
              "references": "The reference list (see “References”)",
              "spectra": "Spectra: not imported (the program prints its own Spectra section)",
              "toc": "Table of contents: not imported (the program makes its own)"}


class _FigureThumb(QLabel):
    """A figure's picture (PNG/JPEG…, a drawing's EMF, or a PDF cut out of a page),
    drawn the first time it is shown."""

    def __init__(self, fig, width: int = 220, height: int = 150):
        super().__init__("…")
        self.fig = fig
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: white; border: 1px solid palette(mid);")
        self._drawn = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._drawn:
            return
        self._drawn = True
        from PySide6.QtGui import QImage, QPixmap
        fig, image = self.fig, None
        try:
            if fig.kind == "table":
                self.setText(f"Table: {len(fig.table.get('rows', []))} rows"
                             if fig.table else "Table")
                return
            if fig.data is None:
                self.setText("(not cut out: its caption\nis imported as text)")
                return
            if fig.ext == ".pdf":
                image = _pdf_image(fig.data)
            elif fig.ext in (".emf", ".wmf"):
                from .clipboard_image import render_emf_bytes
                image = render_emf_bytes(fig.data, 60)
            else:
                image = QImage.fromData(fig.data)
        except Exception:  # noqa: BLE001 — a picture that can't be drawn here is still kept
            image = None
        if image is None or image.isNull():
            self.setText("(no preview here)")
            return
        self.setPixmap(QPixmap.fromImage(image).scaled(
            self.width() - 4, self.height() - 4, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))


class _SectionPage(QScrollArea):
    """One section of the SI's other text (SPEC §13u.16): its name, what it becomes, its
    text, and its figures and tables (each can be left out)."""

    def __init__(self, s, on_rename=None):
        super().__init__()
        from PySide6.QtWidgets import QCheckBox
        self.s = s
        self.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        self.name = QLineEdit(s.name)

        def renamed(text: str) -> None:
            s.name = text.strip()
            if on_rename is not None:
                on_rename()
        self.name.editingFinished.connect(lambda: renamed(self.name.text()))
        self.name.textChanged.connect(lambda t: setattr(s, "name", t.strip()))
        form.addRow("Name:", self.name)
        note = QLabel(KIND_NOTES.get(s.kind, "") + (f". {s.note}" if s.note and
                                                    s.note not in KIND_NOTES.get(s.kind, "")
                                                    else ""))
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        form.addRow("", note)
        if s.heading:
            form.addRow("Heading as printed:", QLabel(s.heading))
        text = "\n".join(_line_for_review(line, s.figures) for line in s.text.split("\n"))
        form.addRow("Text:", _plain_edit(text, read_only=True, height=260))
        for fig in s.figures.values():
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 6)
            lay.addWidget(_FigureThumb(fig))
            side = QVBoxLayout()
            label = QLineEdit(fig.label)
            label.setPlaceholderText("automatic (Figure S1…)")
            label.setToolTip("The figure's label as printed; empty = numbered by the program.")
            label.textChanged.connect(lambda t, f=fig: setattr(f, "label", t.strip()))
            head = QHBoxLayout()
            head.addWidget(QLabel("Label:"))
            head.addWidget(label, 1)
            side.addLayout(head)
            caption = QLabel(_plain_markup(fig.caption) or "(no caption)")
            caption.setWordWrap(True)
            side.addWidget(caption)
            keep = QCheckBox("Import this table" if fig.kind == "table" else "Import this figure")
            keep.setChecked(fig.keep)
            keep.toggled.connect(lambda on, f=fig: setattr(f, "keep", on))
            side.addWidget(keep)
            side.addStretch()
            lay.addLayout(side, 1)
            form.addRow("Table:" if fig.kind == "table" else "Figure:", row)
        self.setWidget(inner)


def _line_for_review(line: str, figures: dict) -> str:
    """A line of a section's text as the review window shows it (figures named)."""
    import re as _re
    m = _re.match(r"^!(figure|table)\{(\w+)\}\s*(.*)$", line)
    if m and m.group(2) in figures:
        fig = figures[m.group(2)]
        what = "table" if m.group(1) == "table" else "figure"
        return f"[{fig.label or what}: {_plain_markup(m.group(3))}]"
    return _plain_markup(line)


class _CoverPage(QScrollArea):
    """The cover page found: title, authors and affiliations, all changeable. Used where
    the project has none of its own yet."""

    def __init__(self, cover):
        super().__init__()
        from PySide6.QtWidgets import QAbstractItemView, QPushButton, QTableWidget
        self.cover = cover
        self.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        intro = QLabel("For the project's own cover page (Front matter ▸ Cover page), where "
                       "the project has no title, authors or affiliations yet.")
        intro.setWordWrap(True)
        form.addRow(intro)
        self.title = QLineEdit(cover.title)
        self.title.textChanged.connect(lambda t: setattr(cover, "title", t.strip()))
        form.addRow("Title:", self.title)
        self.authors = QTableWidget(0, 4)
        self.authors.setHorizontalHeaderLabels(["Name", "Affiliations (marks)", "Corresponding",
                                                "E-mail"])
        self.authors.horizontalHeader().setStretchLastSection(True)
        self.authors.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for a in cover.authors:
            self._add_author_row(a)
        self.authors.itemChanged.connect(self._author_changed)
        buttons = QHBoxLayout()
        add, remove = QPushButton("Add author"), QPushButton("Remove selected")
        add.clicked.connect(lambda: self._new_author())
        remove.clicked.connect(self._remove_author)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        box = QVBoxLayout()
        box.addWidget(self.authors)
        box.addLayout(buttons)
        holder = QWidget()
        holder.setLayout(box)
        form.addRow("Authors:", holder)
        self.affiliations = QTableWidget(0, 2)
        self.affiliations.setHorizontalHeaderLabels(["Mark", "Affiliation"])
        self.affiliations.horizontalHeader().setStretchLastSection(True)
        self.affiliations.verticalHeader().setVisible(False)
        from PySide6.QtWidgets import QTableWidgetItem
        for a in cover.affiliations:
            r = self.affiliations.rowCount()
            self.affiliations.insertRow(r)
            self.affiliations.setItem(r, 0, QTableWidgetItem(a.get("marker", "")))
            self.affiliations.setItem(r, 1, QTableWidgetItem(a.get("text", "")))
        self.affiliations.itemChanged.connect(self._affiliation_changed)
        form.addRow("Affiliations:", self.affiliations)
        form.addRow("As printed:", _plain_edit("\n".join(cover.lines), read_only=True,
                                               height=120))
        self.setWidget(inner)

    def _add_author_row(self, a: dict) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        t = self.authors
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(a.get("name", "")))
        t.setItem(r, 1, QTableWidgetItem(", ".join(a.get("affiliations", []))))
        star = QTableWidgetItem("")
        star.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                      | Qt.ItemFlag.ItemIsSelectable)
        star.setCheckState(Qt.CheckState.Checked if a.get("corresponding")
                           else Qt.CheckState.Unchecked)
        t.setItem(r, 2, star)
        t.setItem(r, 3, QTableWidgetItem(a.get("email", "")))

    def _new_author(self) -> None:
        a = {"name": "", "affiliations": [], "corresponding": False, "email": ""}
        self.cover.authors.append(a)
        self.authors.blockSignals(True)
        self._add_author_row(a)
        self.authors.blockSignals(False)

    def _remove_author(self) -> None:
        rows = sorted({i.row() for i in self.authors.selectedIndexes()}, reverse=True)
        for r in rows:
            self.authors.removeRow(r)
            if r < len(self.cover.authors):
                del self.cover.authors[r]

    def _author_changed(self, item) -> None:
        r, c = item.row(), item.column()
        if r >= len(self.cover.authors):
            return
        a = self.cover.authors[r]
        if c == 0:
            a["name"] = item.text().strip()
        elif c == 1:
            a["affiliations"] = [m.strip() for m in item.text().split(",") if m.strip()]
        elif c == 2:
            a["corresponding"] = item.checkState() == Qt.CheckState.Checked
        else:
            a["email"] = item.text().strip()

    def _affiliation_changed(self, item) -> None:
        r = item.row()
        if r < len(self.cover.affiliations):
            key = "marker" if item.column() == 0 else "text"
            self.cover.affiliations[r][key] = item.text().strip()


REF_COLUMNS = [("number", "No."), ("authors", "Authors"), ("title", "Title"),
               ("journal", "Journal"), ("year", "Year"), ("volume", "Volume"),
               ("pages", "Pages"), ("doi", "DOI")]


class _ReferencesPage(QWidget):
    """The reference list (SPEC §13u.9): each reference as read, every value changeable,
    each can be left out; DOIs can be looked up on Crossref (only when asked)."""

    def __init__(self, refs: list):
        super().__init__()
        from PySide6.QtWidgets import QPushButton, QTableWidget
        self.refs = refs
        lay = QVBoxLayout(self)
        intro = QLabel("Each reference as read from the list (hover over a row for the entry "
                       "as printed). Change anything that was read wrongly, untick what you "
                       "don't want. Numbered citations in the imported text (“…as reported."
                       "²”, “[2]”) are linked to them. References the project already has "
                       "(same DOI) are not added again.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(0, len(REF_COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels(["Import"] + [t for _k, t in REF_COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self._fill()
        self.table.itemChanged.connect(self._changed)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        with_doi = sum(1 for r in refs if r.ref.get("doi"))
        self.lookup = QPushButton(f"Look up the {with_doi} DOI(s) on Crossref")
        self.lookup.setToolTip("Fills each reference with a DOI from Crossref's record (needs "
                               "an internet connection; only the DOIs are sent).")
        self.lookup.setEnabled(with_doi > 0)
        self.lookup.clicked.connect(self._look_up)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        row.addWidget(self.lookup)
        row.addWidget(self.status, 1)
        lay.addLayout(row)

    def _fill(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        t = self.table
        t.blockSignals(True)
        t.setRowCount(0)
        for r in self.refs:
            row = t.rowCount()
            t.insertRow(row)
            keep = QTableWidgetItem("")
            keep.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            keep.setCheckState(Qt.CheckState.Checked if r.keep else Qt.CheckState.Unchecked)
            keep.setToolTip(r.text)
            t.setItem(row, 0, keep)
            for col, (key, _title) in enumerate(REF_COLUMNS, 1):
                if key == "number":
                    value = r.number
                elif key == "authors":
                    value = "; ".join(r.ref.get("authors", []))
                else:
                    value = str(r.ref.get(key, "") or "")
                item = QTableWidgetItem(value)
                item.setToolTip(r.text + (f"\n\nCrossref: {r.looked_up}" if r.looked_up and
                                          r.looked_up != "done" else ""))
                if key == "number":
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(row, col, item)
        t.resizeColumnsToContents()
        for col in (2, 3):
            t.setColumnWidth(col, min(t.columnWidth(col), 260))
        t.blockSignals(False)

    def _changed(self, item) -> None:
        r = self.refs[item.row()]
        if item.column() == 0:
            r.keep = item.checkState() == Qt.CheckState.Checked
            return
        key = REF_COLUMNS[item.column() - 1][0]
        if key == "authors":
            r.ref["authors"] = [a.strip() for a in item.text().split(";") if a.strip()]
        elif key != "number":
            r.ref[key] = item.text().strip()

    def _look_up(self) -> None:
        from ..core import references as refs
        todo = [r for r in self.refs if r.ref.get("doi") and r.looked_up != "done"]
        dialog = QProgressDialog("Looking up the DOIs on Crossref…", "Stop", 0, len(todo), self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        done = failed = 0
        for k, r in enumerate(todo):
            dialog.setValue(k)
            QApplication.processEvents()
            if dialog.wasCanceled():
                break
            try:
                found = refs.fetch_doi(r.ref["doi"])
            except refs.ReferenceError as e:
                r.looked_up = str(e)
                failed += 1
                if "connect" in str(e).lower():
                    break
                continue
            found["id"] = r.ref.get("id", found["id"])
            r.ref = found
            r.looked_up = "done"
            done += 1
        dialog.reset()
        self._fill()
        self.status.setText(f"{done} filled in from Crossref" + (
            f"; {failed} could not be (hover over the row to see why)." if failed else "."))


class _SectionsPage(QWidget):
    """What the SI's other text becomes (SPEC §13u.16): the sections made, and the
    headings found, each with its level (changeable: the sections are then worked out
    again)."""

    LEVELS = [(1, "Section"), (2, "Sub-section"), (3, "Heading in the text"),
              (0, "Not a heading")]

    def __init__(self, dialog: "ImportDialog"):
        super().__init__()
        from PySide6.QtWidgets import QTableWidget, QTreeWidget
        self.dialog = dialog
        lay = QVBoxLayout(self)
        intro = QLabel("The SI's text outside its compounds becomes these sections, in this "
                       "order. Untick what you don't want, and double-click a name to change "
                       "it. Each section's text and figures are shown under “Sections” on "
                       "the left. If a heading was read at the wrong level (or isn't a "
                       "heading), change it in the list below: the sections are worked out "
                       "again.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        split = QSplitter(Qt.Orientation.Vertical)
        self.made = QTreeWidget()
        self.made.setHeaderLabels(["Section", "What it becomes"])
        self.made.itemChanged.connect(self._made_changed)
        split.addWidget(self.made)
        self.heads = QTableWidget(0, 2)
        self.heads.setHorizontalHeaderLabels(["Heading found", "Read as"])
        self.heads.horizontalHeader().setStretchLastSection(False)
        self.heads.verticalHeader().setVisible(False)
        from PySide6.QtWidgets import QHeaderView
        self.heads.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.heads.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        split.addWidget(self.heads)
        split.setSizes([360, 300])
        lay.addWidget(split, 1)
        self.refresh()

    def refresh(self) -> None:
        plan = self.dialog.plan
        self.made.blockSignals(True)
        self.made.clear()
        items: dict[str, QTreeWidgetItem] = {}
        for s in plan.sections:
            item = QTreeWidgetItem([s.name, KIND_NOTES.get(s.kind, "")
                                    + (f" ({len([c for c in s.compounds if c.keep])} "
                                       "compounds)" if s.kind == "data" else "")])
            item.setData(0, Qt.ItemDataRole.UserRole, s)
            flags = item.flags() | Qt.ItemFlag.ItemIsEditable
            if s.kind not in ("data", "references"):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                item.setFlags(flags)
                item.setCheckState(0, Qt.CheckState.Checked if s.keep
                                   else Qt.CheckState.Unchecked)
            else:
                item.setFlags(flags)
            parent = items.get(s.parent)
            (parent.addChild(item) if parent is not None else self.made.addTopLevelItem(item))
            items[s.key] = item
        self.made.expandAll()
        self.made.resizeColumnToContents(0)
        self.made.blockSignals(False)
        from PySide6.QtWidgets import QTableWidgetItem
        heads = plan.headings[:500]
        self.heads.setRowCount(len(heads))
        for row, p in enumerate(heads):
            level = plan.levels.get(id(p), 0)
            item = QTableWidgetItem(("    " * max(0, level - 1)) + p.text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(p.text)
            self.heads.setItem(row, 0, item)
            combo = QComboBox()
            for value, text in self.LEVELS:
                combo.addItem(text, value)
            combo.setCurrentIndex(max(0, combo.findData(level)))
            # (after this signal: working the sections out again replaces this box)
            combo.currentIndexChanged.connect(
                lambda _i, p=p, c=combo: QTimer.singleShot(
                    0, lambda v=c.currentData(): self.dialog.set_level(p, v)))
            self.heads.setCellWidget(row, 1, combo)

    def _made_changed(self, item, column: int) -> None:
        s = item.data(0, Qt.ItemDataRole.UserRole)
        if s is None:
            return
        if column == 0:
            s.name = item.text(0).strip() or s.name
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                s.keep = item.checkState(0) == Qt.CheckState.Checked
        self.dialog.section_changed(s)


class ImportDialog(QDialog):
    """The review window."""

    def __init__(self, plan: ImportPlan, read: si_import_read.ReadResult, project,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.plan = plan
        self.read = read
        self.project = project
        self.setWindowTitle(f"Import SI — {read.file_name}")
        self.resize(1100, 760)

        n_c, n_g = len(plan.compounds), len(plan.gps)
        intro = QLabel(
            f"Found <b>{n_c} compound{'s' if n_c != 1 else ''}</b> and <b>{n_g} general "
            f"procedure{'s' if n_g != 1 else ''}</b> in “{read.file_name}”. Check them, "
            "change anything that was read wrongly, untick what you don't want, then "
            "import. Nothing is written until you click Import, and the import can be "
            "undone (Edit ▸ Undo).")
        intro.setWordWrap(True)
        notes = list(read.notes) + list(plan.warnings)
        if plan.spectra:
            matched = sum(1 for s in plan.spectra if s.cid)
            notes.append(f"{len(plan.spectra)} spectra found, {matched} matched to their "
                         "compound (shown on its page)"
                         + (f"; {len(plan.spectra) - matched} not matched: see “Spectra not "
                            "matched” at the end of the list." if matched < len(plan.spectra)
                            else "."))
        other = [s for s in plan.sections if s.kind not in ("data", "references")]
        if other or plan.references or plan.cover:
            bits = ([f"{len(other)} other section(s)"] if other else []) + \
                ([f"{len(plan.references)} reference(s)"] if plan.references else []) + \
                (["the cover page"] if plan.cover else [])
            notes.append("Also found: " + ", ".join(bits) + " (see “Sections”, “Cover page” "
                         "and “References” in the list).")
        note_label = QLabel("\n".join(WARN + n if n in plan.warnings else n for n in notes))
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: palette(placeholder-text);")

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(300)
        self.pages = QStackedWidget()
        self.pages.addWidget(QLabel("Select a general procedure or a compound to see what was "
                                    "read."))
        self._page_of: dict[int, int] = {}
        gp_names = {g.key: g.name for g in plan.gps}
        if plan.gps:
            top = QTreeWidgetItem(["General procedures"])
            self.tree.addTopLevelItem(top)
            for g in plan.gps:
                users = [c.cid for c in plan.compounds if c.gp_key == g.key]
                self._add_item(top, f"{g.name}" + (f": {g.title}" if g.title else ""), g,
                               _GpPage(g, users), g.keep)
            top.setExpanded(True)
        if plan.compounds:
            top = QTreeWidgetItem(["Compounds"])
            self.tree.addTopLevelItem(top)
            for c in plan.compounds:
                flag = WARN if (c.warnings or c.unrecognised) else ""
                self._add_item(top, f"{flag}{c.cid}  {c.name}".rstrip(), c,
                               _CompoundPage(c, gp_names.get(c.gp_key, ""), plan.spectra,
                                             plan.compounds),
                               c.keep)
            top.setExpanded(True)
        # the other text (phase I4): sections, cover page, references
        self.sections_item = None
        self.sections_page = None
        if plan.sections:
            self.sections_item = QTreeWidgetItem(["Sections"])
            self.tree.addTopLevelItem(self.sections_item)
            self.sections_page = _SectionsPage(self)
            self._page_of[id(self.sections_item)] = self.pages.addWidget(self.sections_page)
            self._section_items()
        if plan.cover is not None:
            self._add_item(self.tree.invisibleRootItem(), "Cover page", plan.cover,
                           _CoverPage(plan.cover), plan.cover.keep)
        if plan.references:
            item = QTreeWidgetItem([f"References ({len(plan.references)})"])
            self.tree.addTopLevelItem(item)
            self.references_page = _ReferencesPage(plan.references)
            self._page_of[id(item)] = self.pages.addWidget(self.references_page)
        unmatched = [s for s in plan.spectra if not s.cid]
        if unmatched:
            item = QTreeWidgetItem([f"{WARN}Spectra not matched ({len(unmatched)})"])
            self.tree.addTopLevelItem(item)
            self._page_of[id(item)] = self.pages.addWidget(
                _UnmatchedSpectraPage(unmatched, plan.compounds))
        self.tree.currentItemChanged.connect(self._show)
        self.tree.itemChanged.connect(self._ticked)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(self.pages)
        split.setStretchFactor(1, 1)

        # How the compounds are printed (§13u.4)
        self.from_fields = QRadioButton("From the fields, in the program's format "
                                        "(recommended)")
        self.as_imported = QRadioButton("Exactly as imported (in each compound's “SI text” "
                                        "field; the fields are filled too)")
        self.from_fields.setChecked(True)
        mode_box = QGroupBox("How the imported compounds are printed")
        ml = QVBoxLayout(mode_box)
        ml.addWidget(self.from_fields)
        ml.addWidget(self.as_imported)

        # Where it goes (§13u.6)
        self.to_new = QRadioButton("A new project")
        self.to_open = QRadioButton("The open project:")
        group = QButtonGroup(self)
        group.addButton(self.to_new)
        group.addButton(self.to_open)
        self.section = QComboBox()
        self.section.addItem("a new Data section", NEW_SECTION)
        usable = project is not None and not getattr(project, "read_only", True)
        if usable:
            for s in project.data_sections():
                self.section.addItem(f"“{s.name}” (compounds with the same ID: only "
                                     "their empty fields are filled)", s.id)
        self.to_open.setEnabled(usable)
        (self.to_open if usable else self.to_new).setChecked(True)
        self.to_open.toggled.connect(self._destination_changed)
        self.section.currentIndexChanged.connect(self._destination_changed)
        dest = QGroupBox("Import into")
        dl = QFormLayout(dest)
        dl.addRow(self.to_new)
        row = QHBoxLayout()
        row.addWidget(self.to_open)
        row.addWidget(self.section, 1)
        dl.addRow(row)
        self.dest_note = QLabel("")
        self.dest_note.setWordWrap(True)
        self.dest_note.setStyleSheet("color: palette(placeholder-text);")
        dl.addRow(self.dest_note)
        self._destination_changed()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_btn = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_btn.setDefault(True)
        self.import_btn.setEnabled(bool(plan.compounds or plan.gps))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        if notes:
            layout.addWidget(note_label)
        layout.addWidget(split, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(mode_box, 1)
        bottom.addWidget(dest, 1)
        layout.addLayout(bottom)
        layout.addWidget(buttons)
        first = self.tree.topLevelItem(0)
        if first is not None and first.childCount():
            self.tree.setCurrentItem(first.child(0))

    def _add_item(self, parent: QTreeWidgetItem, text: str, obj, page: QWidget,
                  keep: bool) -> None:
        item = QTreeWidgetItem([text])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if keep else Qt.CheckState.Unchecked)
        item.setData(0, Qt.ItemDataRole.UserRole, obj)
        if isinstance(obj, ImportedCompound) and (obj.warnings or obj.unrecognised):
            item.setToolTip(0, "\n".join(obj.warnings + (
                ["Some of its text was not recognised (see “Not recognised”)."]
                if obj.unrecognised else [])))
        parent.addChild(item)
        self._page_of[id(item)] = self.pages.addWidget(page)

    def _show(self, item: QTreeWidgetItem | None, _prev=None) -> None:
        index = self._page_of.get(id(item)) if item is not None else None
        self.pages.setCurrentIndex(index if index is not None else 0)

    def _ticked(self, item: QTreeWidgetItem, _column: int) -> None:
        obj = item.data(0, Qt.ItemDataRole.UserRole)
        if obj is not None and hasattr(obj, "keep"):
            keep = item.checkState(0) == Qt.CheckState.Checked
            if keep != obj.keep:
                obj.keep = keep
                if getattr(obj, "key", "").startswith("x") and self.sections_page is not None:
                    self.sections_page.refresh()      # (a section: its list follows)

    def _destination_changed(self) -> None:
        new_section = not self.to_open.isChecked() or self.section.currentData() == NEW_SECTION
        self.section.setEnabled(self.to_open.isChecked())
        n_data = sum(1 for s in self.plan.sections if s.kind == "data")
        self.dest_note.setText(
            (f"The compounds go into {n_data} new Data section(s), as in the SI (names: see "
             "“Sections”)." if n_data > 1 else "The compounds go into a new Data section "
             "(its name: see “Sections”).") if new_section else
            "All the compounds go into this section; the other sections are added as new "
            "sections.")

    # ---- the other text (phase I4)

    def _section_items(self) -> None:
        """The section pages under "Sections" (made again when the outline changes)."""
        item = self.sections_item
        if self.tree.currentItem() is not None and self.tree.currentItem().parent() is item:
            self.tree.setCurrentItem(item)
        for k in range(item.childCount() - 1, -1, -1):
            child = item.child(k)
            self._page_of.pop(id(child), None)    # (its page stays in the stack, unused,
            item.removeChild(child)               # so the other pages keep their places)
        self._section_of: dict[str, QTreeWidgetItem] = {}
        for s in self.plan.sections:
            if s.kind in ("data", "references"):
                continue
            page = _SectionPage(s, on_rename=lambda s=s: self.section_changed(s, page=False))
            self._add_item(item, s.name or "(no name)", s, page, s.keep)
            self._section_of[s.key] = item.child(item.childCount() - 1)
        item.setExpanded(True)

    def set_level(self, para, level: int) -> None:
        """A heading's level changed in the Sections page: the sections are worked out
        again (names and ticks changed so far are kept)."""
        from ..core import si_import_text
        self.plan.level_overrides[id(para)] = level
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            si_import_text.outline(self.plan, self.plan.body_size)
            if self.read.kind == "pdf" and self.read.path:
                from ..core import si_import_pdf
                si_import_pdf.add_figures(Path(self.read.path), self.plan)
            self.tree.blockSignals(True)
            self._section_items()
            self.tree.blockSignals(False)
            self.sections_page.refresh()
            self._destination_changed()
        finally:
            QApplication.restoreOverrideCursor()

    def section_changed(self, s, page: bool = True) -> None:
        """A section renamed or (un)ticked (on the Sections page, its own page or in the
        list): the others follow."""
        item = getattr(self, "_section_of", {}).get(s.key)
        if item is not None:
            self.tree.blockSignals(True)
            item.setText(0, s.name)
            item.setCheckState(0, Qt.CheckState.Checked if s.keep else Qt.CheckState.Unchecked)
            self.tree.blockSignals(False)
            index = self._page_of.get(id(item))
            own = self.pages.widget(index) if index is not None else None
            if page and isinstance(own, _SectionPage) and own.name.text().strip() != s.name:
                own.name.blockSignals(True)
                own.name.setText(s.name)
                own.name.blockSignals(False)
        if not page and self.sections_page is not None:
            self.sections_page.refresh()

    # ---- the choices

    def mode(self) -> str:
        return PRINT_AS_IMPORTED if self.as_imported.isChecked() else PRINT_FROM_FIELDS

    def into_open_project(self) -> bool:
        return self.to_open.isChecked()

    def section_id(self) -> str | None:
        """The existing Data section chosen, or None for a new one."""
        if not self.to_open.isChecked():
            return None
        data = self.section.currentData()
        return None if data == NEW_SECTION else data

    def new_section_name(self) -> str:
        """(The Data sections' names are on the Sections page now: "" = theirs.)"""
        return ""

    def _accept(self) -> None:
        kept = [c for c in self.plan.compounds if c.keep]
        ids = [c.cid for c in kept]
        missing = [c.name or "a compound" for c in kept if not c.cid]
        problems = []
        if missing:
            problems.append("These compounds have no ID: " + ", ".join(missing[:5]) + ".")
        doubled = sorted({i for i in ids if i and ids.count(i) > 1})
        if doubled:
            problems.append("These IDs are used by more than one ticked compound: "
                            + ", ".join(doubled) + ".")
        if problems and QMessageBox.question(
                self, "Import anyway?", "\n".join(problems) + "\n\nImport anyway? (You can "
                "correct the IDs afterwards, in the section's ID table.)"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.accept()


def summary(result, plan: ImportPlan) -> str:
    """What the import did, in a few lines."""
    lines = [f"{len(result.created)} compound page(s) made"
             + (f", {len(result.gps)} general procedure(s)" if result.gps else "") + "."]
    if result.merged:
        lines.append(f"{len(result.merged)} compound(s) already in the section: only their "
                     "empty fields were filled.")
        kept = [f"{cid}: {', '.join(labels)}" for cid, labels in result.merged.items() if labels]
        if kept:
            lines.append("Kept as they were (already filled): " + "; ".join(kept[:8])
                         + (" …" if len(kept) > 8 else ""))
    with_notes = sum(1 for c in plan.compounds if c.keep and c.unrecognised)
    if with_notes:
        lines.append(f"{with_notes} compound(s) have text that was not recognised: it is in "
                     "their Notes field.")
    lines.extend(result.notes)
    kept = [c for c in plan.compounds if c.keep]
    with_structure = sum(1 for c in kept if c.structure and c.use_structure)
    with_picture = sum(1 for c in kept if c.picture_pdf and not c.structure and c.use_structure)
    if kept:
        got = (f"{with_structure} of {len(kept)} compound(s) got their structure from the "
               "SI's ChemDraw drawings" if with_structure else "")
        pics = (f"{with_picture} got a picture of their structure cut out of the PDF (paste "
                "the structure itself from ChemDraw for its formula and masses)"
                if with_picture else "")
        missing = len(kept) - with_structure - with_picture
        lines.append("; ".join(x for x in (got, pics) if x) + (
            "." if not missing else
            ("; " if (got or pics) else "") + f"{missing} have none yet: paste them from "
            "ChemDraw."))
    if getattr(result, "spectra", 0):
        lines.append(f"{result.spectra} spectra attached to their compounds.")
    other = []
    if getattr(result, "general", False):
        other.append("the General Information page (Front matter)")
    n_sections = len(getattr(result, "sections", []))
    if n_sections:
        other.append(f"{n_sections} section(s)")
    if getattr(result, "figures", 0) or getattr(result, "tables", 0):
        other.append(f"{result.figures} figure(s) and {result.tables} table(s)")
    if other:
        lines.append("Made: " + ", ".join(other) + ".")
    if getattr(result, "references", 0) or getattr(result, "references_known", 0):
        lines.append(f"{result.references} reference(s) added"
                     + (f" ({result.references_known} the project already had)"
                        if result.references_known else "")
                     + "; numbered citations in the imported text are linked to them.")
    if getattr(result, "cover", None):
        lines.append("Cover page: " + ", ".join(result.cover) + " filled in.")
    return "\n\n".join(lines)


def _pdf_image(pdf: bytes):
    """A one-page PDF (a structure cut out of an SI) drawn on white, about 150 dpi."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtPdf import QPdfDocument
    data = QByteArray(pdf)
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    doc = QPdfDocument()
    doc.load(buffer)
    size = doc.pagePointSize(0)
    image = doc.render(0, QSize(max(1, int(size.width() * 2)), max(1, int(size.height() * 2))))
    doc.close()
    white = QImage(image.size(), QImage.Format.Format_RGB32)
    white.fill(0xFFFFFF)
    painter = QPainter(white)
    painter.drawImage(0, 0, image)
    painter.end()
    return white


def mnova_spectrum(emf: bytes):
    """A MestReNova picture (EMF) as the spectrum to store, converted as a pasted Mnova
    spectrum is (SVG when it passes its check, else its own bitmap or a picture): (data,
    ext, original EMF, ".emf"), or None."""
    from .spectrum_paste import convert_emf
    try:
        out = convert_emf(emf)
    except Exception:  # noqa: BLE001 — that spectrum is left out, with a note
        return None
    return None if out is None else (out.data, out.ext, out.original or emf,
                                     out.original_ext or ".emf")


class ImportProgress:
    """A progress window for writing the import (its spectra are the slow part)."""

    def __init__(self, parent: QWidget):
        self.dialog = QProgressDialog("Importing…", None, 0, 1, parent)
        self.dialog.setWindowTitle("Import SI")
        self.dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dialog.setMinimumDuration(300)

    def __call__(self, done: int, total: int, text: str) -> None:
        self.dialog.setMaximum(max(total, 1))
        self.dialog.setValue(done)
        self.dialog.setLabelText(text)
        QApplication.processEvents()

    def close(self) -> None:
        self.dialog.reset()
        self.dialog.deleteLater()


def chemdraw_picture(emf: bytes):
    """A ChemDraw drawing's picture (EMF) as the structure picture to store, made the way a
    pasted one is (an SVG when the conversion passes its check, else a PNG): (data, ext,
    vector, vector_ext), or None."""
    from .clipboard_image import picture_from_emf
    try:
        pic = picture_from_emf(emf)
    except Exception:  # noqa: BLE001 — the structure is imported without its picture
        return None
    return None if pic is None else (pic.data, pic.ext, pic.source, pic.source_ext)


def analyse(read: si_import_read.ReadResult) -> ImportPlan:
    """The import plan; from a PDF, with each compound's structure picture and each
    figure and table cut out of its page (SPEC §13u.7, §13u.16)."""
    plan = si_import.analyse(read.paras)
    if read.kind == "pdf" and read.path:
        from ..core import si_import_pdf
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            si_import_pdf.add_pictures(Path(read.path), plan, si_import.STEREO + si_import.ID)
            si_import_pdf.add_figures(Path(read.path), plan)
            if read.spectrum_pages:
                from ..core import si_import_spectra
                plan.spectra = si_import_spectra.pdf_spectra(Path(read.path), plan,
                                                             read.spectrum_pages)
        finally:
            QApplication.restoreOverrideCursor()
    return plan
