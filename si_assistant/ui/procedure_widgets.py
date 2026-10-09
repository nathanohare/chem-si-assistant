"""General procedures (SPEC §6.3.3, §13j): the "General procedures" tab of a Data section,
and the chooser on a compound's Procedure field."""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ..core import links, procedures
from ..core.markup import Run
from ..core.project import DEFAULT_GP_SENTENCE, Project, ProjectError, Section
from .rich_text import RichTextEditor

SAVE_DELAY_MS = 800
HINT = "color: palette(placeholder-text);"
WARN = "color: #d35400;"


def _runs_html(runs: list[Run]) -> str:
    """Runs as simple HTML for a preview label."""
    import html
    out = []
    for r in runs:
        text = html.escape(r.text)
        if r.valign == "super":
            text = f"<sup>{text}</sup>"
        elif r.valign == "sub":
            text = f"<sub>{text}</sub>"
        if r.italic:
            text = f"<i>{text}</i>"
        if r.bold:
            text = f"<b>{text}</b>"
        if r.underline:
            text = f"<u>{text}</u>"
        out.append(text)
    return "".join(out)


class GeneralProceduresEditor(QWidget):
    """List of a section's general procedures, with the selected one's name, title,
    compound sentence and text."""

    changed = Signal()   # something was saved (tree / dashboard may need refreshing)

    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.section = section
        self.current: str | None = None
        # Edits on screen not written yet: cleared only once the save worked, so a failed
        # save is tried again (on the next flush), not forgotten.
        self._dirty = False
        ro = project.read_only

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)
        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._add)
        self.remove_btn = QPushButton("Remove…")
        self.remove_btn.clicked.connect(self._remove)
        up, down = QPushButton("▲"), QPushButton("▼")
        up.setToolTip("Move up (the order they are printed in)")
        down.setToolTip("Move down")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        for b in (self.add_btn, self.remove_btn, up, down):
            b.setEnabled(not ro)
            buttons.addWidget(b)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.list, 1)
        ll.addLayout(buttons)

        self.name = QLineEdit()
        self.name.setPlaceholderText("General Procedure A")
        self.title = QLineEdit()
        self.title.setPlaceholderText("optional, e.g. Synthesis of Enamides")
        self.sentence = QLineEdit()
        self.sentence.setToolTip(
            "Printed in each compound's block. Words in {braces} are filled in on the compound "
            "page. Automatic: {GP} (this procedure's name), {scale}, {yield}, {product} (the "
            "compound's ID), {purification} (from the Purification field, e.g. “flash column "
            "chromatography (SiO₂, 10% EtOAc/hexanes)”; the field then adds nothing at the "
            "end of the procedure). Anything else, e.g. {starting material} or "
            "{temperature}, is typed on each compound page.")
        self.users = QLabel()
        self.users.setStyleSheet(HINT)
        # Two procedures with the same name (SPEC §13n.8): said here and in the Warnings panel.
        self.duplicate = QLabel()
        self.duplicate.setWordWrap(True)
        self.duplicate.setStyleSheet(WARN)
        self.duplicate.hide()
        self.text = RichTextEditor(self)
        self.text.set_read_only(ro)
        for w in (self.name, self.title, self.sentence):
            w.setReadOnly(ro)
            w.textEdited.connect(self._edited)
        self.text.changed.connect(self._edited)

        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("", self.duplicate)
        form.addRow("Title:", self.title)
        form.addRow("Compound sentence:", self.sentence)
        sentence_hint = QLabel("Printed in each compound that uses this procedure. {GP}, {scale}, "
                               "{yield}, {product} and {purification} fill themselves in; "
                               "other {words} are typed on each compound page.")
        sentence_hint.setWordWrap(True)
        sentence_hint.setStyleSheet(HINT)
        form.addRow("", sentence_hint)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(form)
        rl.addWidget(QLabel("<b>Procedure text</b> (printed once, at the start of this section):"))
        rl.addWidget(self.text, 1)
        rl.addWidget(self.users)
        self.right = right

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        split.setSizes([220, 640])
        intro = QLabel("General procedures are printed under their own headings at the start "
                       "of this section. On a compound page, choose one in the Experimental "
                       "procedure field instead of (or as well as) writing a procedure.")
        intro.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(split, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)
        self._fill_list()

    def _fill_list(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for gp in self.project.general_procedures(self.section):
            item = QListWidgetItem(gp.get("name") or "(unnamed)")
            item.setData(Qt.ItemDataRole.UserRole, gp["id"])
            self.list.addItem(item)
        self._mark_duplicates()
        self.list.blockSignals(False)
        ids = [gp["id"] for gp in self.project.general_procedures(self.section)]
        target = select if select in ids else (ids[0] if ids else None)
        if target is not None:
            self.list.setCurrentRow(ids.index(target))
        self._show(target)

    def _selected(self, item, _prev=None) -> None:
        if not self.flush():
            # The typing could not be saved: it stays on screen (to be tried again), so the
            # list goes back to the procedure being edited instead of showing another.
            self._select_current_row()
            # (A click selects the clicked procedure after this returns: select the one
            # being edited again once the click is over, so the list does not mislead.)
            QTimer.singleShot(0, self, self._select_current_row)
            return
        if self.project.journal is not None:
            self.project.journal.break_merge()   # (typing in another procedure: a new step)
        self._show(item.data(Qt.ItemDataRole.UserRole) if item else None)

    def _select_current_row(self) -> None:
        """Select (only) the procedure on screen in the list, without showing it again."""
        row = next((r for r in range(self.list.count())
                    if self.list.item(r).data(Qt.ItemDataRole.UserRole) == self.current), -1)
        self.list.blockSignals(True)
        try:
            self.list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self.list.blockSignals(False)

    def _show(self, gp_id: str | None) -> None:
        self.current = gp_id
        self._dirty = False   # (what is on screen is replaced)
        found = self.project.general_procedure(gp_id) if gp_id else None
        self.right.setEnabled(found is not None)
        self.remove_btn.setEnabled(found is not None and not self.project.read_only)
        gp = found[1] if found else {}
        self.name.setText(gp.get("name", ""))
        self.title.setText(gp.get("title", ""))
        self.sentence.setText(gp.get("sentence", DEFAULT_GP_SENTENCE) if found else "")
        self.text.set_markup(self.project.read_gp_text(gp_id) if found else "")
        self._update_users()
        self._mark_duplicates()

    def _names(self) -> list[tuple[str, str, str]]:
        """(gp id, name, section name) of every GP in the project, with the name being typed
        for the one on show."""
        out = []
        for section, gp in self.project.all_general_procedures():
            name = gp.get("name", "")
            if gp["id"] == self.current and not self.project.read_only:
                name = self.name.text().strip() or "General Procedure"
            out.append((gp["id"], name, section.name))
        return out

    def _mark_duplicates(self) -> None:
        """A warning under the name, and ⚠ in the list, for procedures whose name is also
        used by another one anywhere in the project (ignoring capitals and spaces)."""
        names = self._names()
        keys = [procedures.name_key(n) for _i, n, _s in names]
        doubled = {i for (i, _n, _s), k in zip(names, keys) if k and keys.count(k) > 1}
        for row in range(self.list.count()):
            item = self.list.item(row)
            gp_id = item.data(Qt.ItemDataRole.UserRole)
            text = item.text().removeprefix("⚠ ")
            item.setText(("⚠ " + text) if gp_id in doubled else text)
            item.setToolTip("Another general procedure has the same name" if gp_id in doubled
                            else "")
        others = []
        if self.current in doubled:
            mine = procedures.name_key(next(n for i, n, _s in names if i == self.current))
            others = [f"“{n}” in “{s}”" for i, n, s in names
                      if i != self.current and procedures.name_key(n) == mine]
        self.duplicate.setText("⚠ Another general procedure has the same name: "
                               + "; ".join(others) + ". Give each its own name (they are "
                               "printed as headings and chosen by name on compound pages).")
        self.duplicate.setVisible(bool(others))

    def _update_users(self) -> None:
        if not self.current:
            self.users.setText("Click Add to write a general procedure.")
            return
        report = self.project.id_report()
        users = [report.display_id(c.id) for c in self.project.gp_users(self.current)]
        self.users.setText("Used by: " + (", ".join(users) if users else "no compounds yet"))

    def _edited(self, *_args) -> None:
        if not self.project.read_only and self.current:
            self._dirty = True
            self._timer.start()
            item = self.list.currentItem()
            if item is not None:
                item.setText(self.name.text().strip() or "(unnamed)")
            self._mark_duplicates()

    def has_unsaved(self) -> bool:
        """Edits whose save is still waiting: not tried yet, or tried and failed (kept to
        be tried again)."""
        return self._dirty or self._timer.isActive()

    def flush(self) -> bool:
        """Save the procedure on screen (if edited). False if that failed (said in a
        message box): the edits stay on screen and waiting, so an action that would replace
        what is on screen (another procedure, Add, Move, Remove) should not go on."""
        waiting = self._dirty or self._timer.isActive()
        if not waiting or self.project.read_only or not self.current:
            self._timer.stop()
            return True
        self._timer.stop()
        try:
            with self.project.autosave():   # (typing: merged into one undo step)
                self.project.update_general_procedure(
                    self.current, name=self.name.text().strip() or "General Procedure",
                    title=self.title.text().strip(),
                    sentence=self.sentence.text().strip() or DEFAULT_GP_SENTENCE)
                self.project.write_gp_text(self.current, self.text.markup())
        except (ProjectError, OSError, KeyError) as e:
            QMessageBox.warning(self, "Could not save",
                                f"{e}\n\nYour changes are still on screen; saving them is "
                                "tried again with your next edit or when you leave this page.")
            return False
        self._dirty = False
        self.changed.emit()
        return True

    def _add(self) -> None:
        if not self.flush():
            return
        with self.project.undo_step("Add a general procedure"):
            gp = self.project.add_general_procedure(self.section.id)
        self._fill_list(gp["id"])
        self.name.setFocus()
        self.name.selectAll()
        self.changed.emit()

    def _remove(self) -> None:
        if not self.current or not self.flush():
            return
        _section, gp = self.project.general_procedure(self.current)
        users = self.project.gp_users(self.current)
        extra = (f"\n\n{len(users)} compound(s) use it; they will show a warning until you "
                 "choose another procedure for them.") if users else ""
        if QMessageBox.question(self, "Remove general procedure",
                                f"Remove “{gp.get('name')}”? It goes to the project Trash and "
                                f"can be restored.{extra}") != QMessageBox.StandardButton.Yes:
            return
        with self.project.undo_step(f"Remove general procedure “{gp.get('name')}”"):
            self.project.delete_general_procedure(self.current)
        self._fill_list()
        self.changed.emit()

    def _move(self, step: int) -> None:
        if self.current and self.flush():
            _section, gp = self.project.general_procedure(self.current)
            with self.project.undo_step(f"Move general procedure “{gp.get('name')}”"):
                self.project.move_general_procedure(self.current, step)
            self._fill_list(self.current)
            self.changed.emit()


class GpChooser(QWidget):
    """On a compound's Procedure field: choose a general procedure and fill in its
    compound sentence. Everything automatic can be typed over (SPEC §2.7)."""

    changed = Signal()
    # Text typed in the {scale} box: the page may fill the Reaction scale field with it
    # (SPEC §13n.9).
    scale_typed = Signal(str)

    def __init__(self, project: Project, compound, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.compound = compound
        self.value_widgets: dict[str, QWidget] = {}
        choice = project.gp_choice(compound)
        # Values typed for any procedure are kept, so switching back and forth loses nothing.
        self._values: dict[str, str] = dict(choice.get("values") or {})

        self.combo = QComboBox()
        self.combo.addItem("None — write the procedure below", "")
        own = compound.section_id
        for section, gp in project.all_general_procedures():
            label = gp.get("name", "") + (f" — {gp['title']}" if gp.get("title") else "")
            if section.id != own:
                label += f"   (from “{section.name}”)"
            self.combo.addItem(label, gp["id"])
        current = choice.get("gp", "")
        if current and self.combo.findData(current) < 0:
            self.combo.addItem("⚠ a general procedure that was deleted", current)
        self.combo.setCurrentIndex(max(0, self.combo.findData(current)))
        self.combo.setEnabled(not project.read_only)
        self.combo.currentIndexChanged.connect(self._gp_changed)

        self.values_form = QFormLayout()
        self.values_form.setContentsMargins(12, 0, 0, 0)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.RichText)

        top = QHBoxLayout()
        top.addWidget(QLabel("General procedure:"))
        top.addWidget(self.combo, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addLayout(self.values_form)
        layout.addWidget(self.preview)
        self._build_values()

    def gp(self) -> dict | None:
        gp_id = self.combo.currentData()
        found = self.project.general_procedure(gp_id) if gp_id else None
        return found[1] if found else None

    def _gp_changed(self) -> None:
        self._collect()
        self._build_values()
        self.changed.emit()

    def _build_values(self) -> None:
        while self.values_form.rowCount():
            self.values_form.removeRow(0)
        self.value_widgets = {}
        gp = self.gp()
        self.preview.setVisible(gp is not None)
        if gp is None:
            return
        auto = procedures.auto_values(self.project, self.compound, gp)
        report = self.project.id_report()
        for name in procedures.placeholders(gp.get("sentence", "")):
            k = procedures.key(name)
            stored = next((v for n, v in self._values.items() if procedures.key(n) == k), "")
            if k in procedures.AUTO:
                w = QLineEdit(stored)
                w.setPlaceholderText(self._auto_placeholder(k, auto, report))
                w.setToolTip("Leave empty to use the automatic value, or type your own.")
                if k == "scale":
                    w.setToolTip("Type the scale, e.g. 0.20 mmol: it also fills the Reaction "
                                 "scale field if that is empty. Once the Reaction scale field "
                                 "is filled, its value is printed (a warning shows if the two "
                                 "differ).")
                    w.textEdited.connect(self.scale_typed.emit)
                w.textEdited.connect(self._value_edited)
            else:
                w = RichTextEditor(self, headings=False, height=30)
                w.toolbar.hide()
                w.set_markup(stored)
                w.edit.setToolTip("Type @ to mention a compound (printed as its current ID).")
                w.changed.connect(self._value_edited)
            w.setEnabled(not self.project.read_only)
            self.value_widgets[name] = w
            self.values_form.addRow(f"{{{name}}}:", w)
        self._update_preview()

    def _auto_placeholder(self, k: str, auto: dict, report) -> str:
        shown = "".join(r.text for r in links.display_runs(self.project, auto.get(k, []), report))
        return f"automatic: {shown}" if shown else f"automatic ({procedures.AUTO[k]}) — empty"

    def refresh_auto(self) -> None:
        """The compound was saved (Yield, Reaction scale, Purification or the ID may have
        changed): update the "automatic: …" hints and the "Printed as" line, without
        rebuilding the boxes (so focus and typing are kept)."""
        gp = self.gp()
        if gp is None:
            return
        auto = procedures.auto_values(self.project, self.compound, gp)
        report = self.project.id_report()
        for name, w in self.value_widgets.items():
            k = procedures.key(name)
            if k in procedures.AUTO and isinstance(w, QLineEdit):
                w.setPlaceholderText(self._auto_placeholder(k, auto, report))
        self._update_preview()

    def _value_edited(self, *_args) -> None:
        self._collect()
        self._update_preview()
        self.changed.emit()

    def _collect(self) -> None:
        for name, w in self.value_widgets.items():
            k = procedures.key(name)
            for n in [n for n in self._values if procedures.key(n) == k]:
                del self._values[n]
            text = w.text().strip() if isinstance(w, QLineEdit) else w.markup().strip()
            if text:
                self._values[name] = text

    def _update_preview(self) -> None:
        gp = self.gp()
        if gp is None:
            return
        runs = procedures.fill(gp.get("sentence", "") or DEFAULT_GP_SENTENCE, self._values,
                               procedures.auto_values(self.project, self.compound, gp))
        shown = links.display_runs(self.project, runs)
        self.preview.setText(f'<span style="color: palette(placeholder-text);">Printed as:</span> '
                             f"{_runs_html(shown)}")

    def store(self) -> None:
        self._collect()
        gp_id = self.combo.currentData() or ""
        if gp_id or self._values:
            self.compound.values["procedure"] = {"gp": gp_id, "values": dict(self._values)}
        else:
            self.compound.values.pop("procedure", None)
