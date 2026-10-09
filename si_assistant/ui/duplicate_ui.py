"""The Duplicate… window for a compound page or a scheme page (SPEC §13q.3): how many
copies, where they go, and which fields' contents are copied (core/duplicate.py)."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core import duplicate as dup
from ..core.project import Project, SchemePage

COLUMNS = 3   # the field tick boxes, in this many columns


class DuplicateDialog(QDialog):
    def __init__(self, project: Project, item, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.item = item
        self.is_scheme = isinstance(item, SchemePage)
        report = project.id_report()
        compounds = list(item.children) if self.is_scheme else [item]
        if self.is_scheme:
            self.setWindowTitle(f"Duplicate scheme page “{item.name}”")
            what = (f"Copies the scheme (its drawing, conditions and layout) and its "
                    f"{len(compounds)} compound page(s), each linked to the same place in the "
                    "copied scheme." if compounds else
                    "Copies the scheme (its drawing, conditions and layout). It has no "
                    "compound pages.")
        else:
            shown = report.display_id(item.id)
            self.setWindowTitle(f"Duplicate {shown}")
            what = (f"Copies the page of {shown} {item.name or ''}".rstrip() + ". "
                    + ("It is in a scheme page: each copy becomes a compound page of its own."
                       if item.scheme_id else ""))
        intro = QLabel(what)
        intro.setWordWrap(True)

        self.copies = QSpinBox()
        self.copies.setRange(1, dup.MAX_COPIES)
        self.copies.setValue(1)
        self.copies.valueChanged.connect(self._update_note)
        copies_row = QHBoxLayout()
        copies_row.addWidget(QLabel("Number of copies:"))
        copies_row.addWidget(self.copies)
        copies_row.addStretch()

        self.at_end = QRadioButton("At the end of the section (no other compound is "
                                   "renumbered)")
        self.after = QRadioButton("Right after the original (the compounds after it get "
                                  "the next IDs)")
        self.at_end.setChecked(True)
        for b in (self.at_end, self.after):
            b.toggled.connect(self._update_note)
        place = QGroupBox("Where the copies go")
        pl = QVBoxLayout(place)
        pl.addWidget(self.at_end)
        pl.addWidget(self.after)
        self.ids_note = QLabel()
        self.ids_note.setWordWrap(True)
        self.ids_note.setStyleSheet("color: palette(placeholder-text);")
        pl.addWidget(self.ids_note)

        # The fields whose contents are copied (for a scheme: of any of its compounds).
        defs, never = {}, {}
        for c in compounds:
            for d in dup.copyable_fields(project, c):
                defs.setdefault(d.key, d)
            for d in dup.never_copied_fields(project, c):
                never.setdefault(d.key, d)
        self.boxes: dict[str, QCheckBox] = {}
        fields = QGroupBox("Copy the contents of these fields"
                           + (" (on every compound page)" if self.is_scheme else ""))
        grid = QGridLayout()
        for i, d in enumerate(defs.values()):
            box = QCheckBox(d.label.replace("&", "&&"))   # ("Yield & ee": no Alt+ key)
            box.setChecked(True)
            self.boxes[d.key] = box
            grid.addWidget(box, i // COLUMNS, i % COLUMNS)
        all_btn = QPushButton("Tick all")
        all_btn.clicked.connect(lambda: self._tick(True))
        none_btn = QPushButton("Untick all")
        none_btn.clicked.connect(lambda: self._tick(False))
        tick_row = QHBoxLayout()
        tick_row.addWidget(all_btn)
        tick_row.addWidget(none_btn)
        tick_row.addStretch()
        fl = QVBoxLayout(fields)
        fl.addLayout(grid)
        fl.addLayout(tick_row)
        note = QLabel("Unticked fields stay on the copies, empty. "
                      + ("Never copied (they belong to the original compound): "
                         + ", ".join(d.label for d in never.values())
                         + ", and HRMS report files. " if never else
                         "Attached files are never copied. ")
                      + "The manual ID and flags are not copied either.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        fl.addWidget(note)
        fields.setVisible(bool(defs))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Duplicate")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(copies_row)
        layout.addWidget(place)
        layout.addWidget(fields)
        layout.addWidget(buttons)
        self._update_note()

    def _tick(self, on: bool) -> None:
        for box in self.boxes.values():
            box.setChecked(on)

    def _update_note(self) -> None:
        """The IDs the copies will take (at the end of the section)."""
        per = len(self.item.children) if self.is_scheme else 1
        if per == 0:
            self.ids_note.setText("")
            return
        section = self.project.section(self.item.section_id)
        n = per * self.copies.value()
        if self.after.isChecked():
            self.ids_note.setText(f"The {n} new compound page(s) take the IDs after the "
                                  "original; every compound after them is renumbered.")
            return
        start = len(section.compounds)
        table = section.id_table
        ids = [table[i].strip() if i < len(table) and table[i].strip() else "ID?"
               for i in range(start, start + n)]
        shown = ", ".join(ids[:8]) + (", …" if len(ids) > 8 else "")
        missing = ids.count("ID?")
        self.ids_note.setText(
            f"The new compound page(s) will be {shown} (from the ID table)."
            + (f" The ID table has no row for {missing} of them: add rows on the section's "
               "ID table tab." if missing else ""))

    def keys(self) -> set[str]:
        return {k for k, box in self.boxes.items() if box.isChecked()}

    def place(self) -> str:
        return dup.AFTER if self.after.isChecked() else dup.END
