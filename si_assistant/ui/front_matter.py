"""Front matter pages (SPEC §13k.4): the fixed "Front matter" item at the top of the tree
with its Cover page, Table of contents and General Information pages."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from ..core import front
from ..core.project import GENERAL_OWNER, Project
from ..core.spectra import STYLE_TEXT_PT
from .dialogs import EditableTable
from .rich_text import RichTextEditor

AUTOSAVE_DELAY_MS = 800


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


def _wrapped(text: str) -> QLabel:
    """A label that wraps, so it never makes the page wider than its column."""
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class FrontPanel(QWidget):
    """Common parts: a title, a short explanation, and saving shortly after each edit."""

    saved = Signal(str)   # status-bar message

    def __init__(self, project: Project, title: str, intro: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        heading = QLabel(title)
        f = heading.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        heading.setFont(f)
        kind = QLabel("Front matter")
        kind.setStyleSheet("color: palette(placeholder-text);")
        top = QHBoxLayout()
        top.addWidget(heading, 1)
        top.addWidget(kind)
        self.layout_ = QVBoxLayout(self)
        self.layout_.addLayout(top)
        if intro:
            self.layout_.addWidget(_note(intro))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_DELAY_MS)
        self._timer.timeout.connect(self._save)
        self._choice = False   # a choice (not typing) is waiting to be saved

    def _changed(self, *_args) -> None:
        # A tick box, list or number changed (not typing): the save is an undo step of its
        # own, rather than merging with the typing on this page.
        if isinstance(self.sender(), (QAbstractButton, QComboBox, QAbstractSpinBox)):
            self._choice = True
        if not self.project.read_only:
            self._timer.start()

    def _save(self) -> None:
        self._timer.stop()
        choice, self._choice = self._choice, False
        if self.project.read_only:
            return
        with nullcontext() if choice else self.project.autosave():
            self.save()
        self.saved.emit("Saved")

    def save(self) -> None:
        """Write this page's values to the project (subclasses)."""

    def commit_edits(self) -> None:
        """Finish any edit still being typed (e.g. an open table cell) so it is saved."""

    def flush(self) -> None:
        """Save anything pending. Called before switching away, compiling or closing."""
        self.commit_edits()
        if self._timer.isActive():
            self._save()


class FrontMatterOverview(FrontPanel):
    """Shown when the "Front matter" item itself is selected."""

    open_page = Signal(str)   # "cover", "toc" or "general"

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(project, "Front matter",
                         "The first pages of the SI, always in this order. They cannot be "
                         "moved or deleted; untick any of them in the Compile SI window to "
                         "leave it out.", parent)
        about = {"cover": "Title, authors, affiliations and corresponding emails, on a page "
                          "of their own.",
                 "toc": "Starts on a new page after the cover. Choose how much it lists.",
                 "general": "Your General Information text (materials, instruments, methods). "
                            "Starts on a new page after the table of contents."}
        for key, name in front.FRONT_PAGES.items():
            row = QHBoxLayout()
            button = QPushButton(f"Open {name}")
            button.setMinimumWidth(220)
            button.clicked.connect(lambda _=False, k=key: self.open_page.emit(k))
            row.addWidget(button)
            row.addWidget(_note(about[key]), 1)
            self.layout_.addLayout(row)
        self.layout_.addStretch()


class CoverPagePanel(FrontPanel):
    """Front matter ▸ Cover page: heading, title, authors, affiliations, extra text.
    Title/authors/affiliations keep their settings keys from the old Manuscript tab."""

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(project, "Cover page",
                         "Printed on the first page of the SI, on a page of its own. The "
                         "corresponding authors' emails are printed under the affiliations.",
                         parent)
        s = project.settings
        ro = project.read_only
        self.heading_edit = QLineEdit(front.cover_heading(s))
        self.heading_edit.setToolTip(
            "The heading at the top of the cover. Leave it as it is (or empty) to follow the "
            "journal style: “Supporting Information” (ACS, Wiley) or “Electronic "
            "Supplementary Information” (RSC).")
        self.heading_edit.setPlaceholderText(
            front.style_cover_heading(s.get("journal_style", "ACS")) + "  (journal style)")
        self.title_edit = QLineEdit(str(s.get("title", "") or ""))
        for edit in (self.heading_edit, self.title_edit):
            edit.setReadOnly(ro)
            edit.textEdited.connect(self._changed)
            edit.editingFinished.connect(self.flush)

        self.authors = EditableTable(["Name", "Affiliations", "Corresponding", "Email"],
                                     checkbox_columns={2})
        for a in front.authors(s):
            self.authors.add_row([str(a.get("name", "") or ""), ",".join(a["affiliations"]),
                                  bool(a.get("corresponding", False)),
                                  str(a.get("email", "") or "")])
        self.affiliations = EditableTable(["Marker", "Affiliation"])
        self.affiliations.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        for a in front.affiliations(s):
            self.affiliations.add_row([str(a.get("marker", "") or ""),
                                       str(a.get("text", "") or "")])
        for table in (self.authors, self.affiliations):
            table.setMinimumHeight(130)
            table.changed.connect(self._changed)
            table.setEnabled(not ro)

        self.extra = RichTextEditor(self, headings=False, height=110, alignment=True)
        self.extra.set_markup(project.read_front_text("cover"))
        self.extra.set_read_only(ro)
        self.extra.changed.connect(self._changed)

        form = QFormLayout()
        form.addRow("Heading:", self.heading_edit)
        form.addRow("Manuscript title:", self.title_edit)
        self.layout_.addLayout(form)
        self.layout_.addWidget(_wrapped("<b>Authors</b> — affiliation markers separated by "
                                        "commas, e.g. <i>a,b</i>. Tick the corresponding "
                                        "author(s) and give an email."))
        self.layout_.addWidget(self.authors, 2)
        self.layout_.addWidget(QLabel("<b>Affiliations</b>"))
        self.layout_.addWidget(self.affiliations, 1)
        self.layout_.addWidget(_wrapped("<b>Extra text</b> (optional, printed under the "
                                        "affiliations)"))
        self.layout_.addWidget(self.extra)

    def commit_edits(self) -> None:
        # A cell still being typed in (no Enter yet) is committed here; its itemChanged
        # then starts the save timer, which flush() runs straight away.
        for table in (self.authors, self.affiliations):
            table.commit_edit()

    def save(self) -> None:
        s = self.project.settings
        front.store_cover_heading(s, self.heading_edit.text())
        s["title"] = self.title_edit.text().strip()
        s["authors"] = [
            {"name": name, "affiliations": [m.strip() for m in affs.split(",") if m.strip()],
             "corresponding": bool(corr), "email": email}
            for name, affs, corr, email in self.authors.rows() if name]
        s["affiliations"] = [{"marker": m, "text": t}
                             for m, t in self.affiliations.rows() if m or t]
        self.project.save_settings()
        self.project.write_front_text("cover", self.extra.markup())


class TocPanel(FrontPanel):
    """Front matter ▸ Table of contents."""

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(project, "Table of contents",
                         "Made automatically when compiling, with page numbers. It starts on a "
                         "new page after the cover page.", parent)
        v = front.toc_settings(project.settings)
        self.title_edit = QLineEdit(v["title"])
        self.title_edit.setPlaceholderText("(no title)")
        self.title_edit.textEdited.connect(self._changed)
        self.title_edit.editingFinished.connect(self.flush)
        self.depth = QComboBox()
        for key, text in front.TOC_DEPTHS.items():
            self.depth.addItem(text, key)
        self.depth.setCurrentIndex(max(0, self.depth.findData(v["depth"])))
        self.depth.setToolTip("Headings typed inside a section's text are listed with the "
                              "sub-sections.")
        self.lists = {}
        for key, (title, _kind) in front.TOC_LISTS.items():
            box = QCheckBox(f"Add a {title.lower()} after the contents")
            box.setChecked(v[key])
            self.lists[key] = box
        self.dots = QCheckBox("Dot leaders ( . . . . . ) between each entry and its page number")
        self.dots.setChecked(v["dots"])
        self.same_size = QCheckBox("Same as the text")
        self.same_size.setChecked(v["font_size"] <= 0)
        self.size = QDoubleSpinBox()
        self.size.setRange(6.0, 16.0)
        self.size.setDecimals(1)
        self.size.setSingleStep(0.5)
        self.size.setSuffix(" pt")
        # (While "Same as the text" is ticked, the box shows the journal style's text
        # size, so unticking it starts from the size the contents already have.)
        style_pt = STYLE_TEXT_PT.get(project.settings.get("journal_style", "ACS"), 11.0)
        self.size.setValue(v["font_size"] if v["font_size"] > 0 else style_pt)
        self.size.setEnabled(not self.same_size.isChecked())
        self.same_size.toggled.connect(lambda same: self.size.setEnabled(not same))
        for box in (*self.lists.values(), self.dots, self.same_size):
            box.toggled.connect(self._changed)
        self.depth.currentIndexChanged.connect(self._changed)
        self.size.valueChanged.connect(self._changed)
        for w in (self.title_edit, self.depth, *self.lists.values(), self.dots, self.same_size,
                  self.size):
            w.setEnabled(w.isEnabled() and not project.read_only)

        size_row = QHBoxLayout()
        size_row.addWidget(self.same_size)
        size_row.addWidget(self.size)
        size_row.addStretch()
        form = QFormLayout()
        form.addRow("Title:", self.title_edit)
        form.addRow("List:", self.depth)
        lists = QVBoxLayout()
        for box in self.lists.values():
            lists.addWidget(box)
        lists.addWidget(_note("Figures, tables and schemes are listed with their labels "
                              "(Figure S1, Table S1…; set in Project settings ▸ Labels). A list "
                              "with nothing in it is left out."))
        form.addRow("Also:", lists)
        form.addRow("Layout:", self.dots)
        form.addRow("Font size:", size_row)
        self.layout_.addLayout(form)
        self.layout_.addStretch()

    def values(self) -> dict:
        return {"title": self.title_edit.text().strip(), "depth": self.depth.currentData(),
                **{k: box.isChecked() for k, box in self.lists.items()},
                "dots": self.dots.isChecked(),
                "font_size": 0.0 if self.same_size.isChecked() else self.size.value()}

    def save(self) -> None:
        front.store_toc(self.project.settings, self.values())
        self.project.save_settings()


class GeneralInfoPanel(FrontPanel):
    """Front matter ▸ General Information: one large text (front/general_information.txt)
    with pictures (front/images/), mentions and citations."""

    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(project, "General Information",
                         "Materials, instruments and general methods. It starts on a new "
                         "page after the table of contents.", parent)
        ro = project.read_only
        self.heading_edit = QLineEdit(front.general_heading(project.settings))
        self.heading_edit.setPlaceholderText("(no heading printed)")
        self.heading_edit.setToolTip("The heading printed above the text (and listed in the "
                                     "table of contents). Leave empty for none.")
        self.heading_edit.setReadOnly(ro)
        self.heading_edit.textEdited.connect(self._changed)
        self.heading_edit.editingFinished.connect(self.flush)
        self.numbered = QCheckBox("Number this heading like a section (Wiley and RSC styles)")
        self.numbered.setChecked(front.general_numbered(project.settings))
        self.numbered.setToolTip(
            "Off: the heading is printed without a number and the first section is “1.”.\n"
            "On: it is printed as “1. General Information” and the sections after it start "
            "at 2. (ACS headings are never numbered.)")
        self.numbered.setEnabled(not ro)
        self.numbered.toggled.connect(self._changed)
        self.old_section_note = _note("")
        self.old_section_note.setVisible(False)
        self.editor = RichTextEditor(self)
        from .panels import text_with_objects
        text_with_objects(self.editor, project, GENERAL_OWNER, self)   # figures and tables
        self.editor.changed.connect(self._changed)
        form = QFormLayout()
        form.addRow("Printed heading:", self.heading_edit)
        form.addRow("", self.numbered)
        self.layout_.addLayout(form)
        self.layout_.addWidget(self.old_section_note)
        self.layout_.addWidget(self.editor, 1)
        self.update_old_section_note()

    def update_old_section_note(self) -> None:
        """While this page is empty, point to an older "General Information" custom
        section (projects from before the front matter existed) and how to move it here."""
        old = old_general_section(self.project)
        show = old is not None and not self.editor.markup().strip()
        if show:
            self.old_section_note.setText(
                f"This project still has its older “{old.name}” section in the section "
                "list. To move its text and pictures here, select it and choose "
                "Edit ▸ Use as General Information… (or right-click it).")
        self.old_section_note.setVisible(show)

    def save(self) -> None:
        from .panels import after_text_saved
        front.store_general_heading(self.project.settings, self.heading_edit.text(),
                                    self.numbered.isChecked())
        self.project.save_settings()
        self.project.write_front_text("general", self.editor.markup())
        after_text_saved(self.editor)
        self.update_old_section_note()


def old_general_section(project: Project):
    """A top-level custom section named "General Information" with no sub-sections (what
    projects made before the front matter page used), or None."""
    for s in project.top_sections:
        if (s.type == "text" and not s.children
                and s.name.strip().casefold() == front.DEFAULT_GENERAL_HEADING.casefold()):
            return s
    return None


PANELS = {"cover": CoverPagePanel, "toc": TocPanel, "general": GeneralInfoPanel}
