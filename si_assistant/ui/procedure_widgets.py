"""General procedures (SPEC §6.3.3, §13j): the "General procedures" tab of a Data section,
and the chooser on a compound's Procedure field."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ..core import links, procedures
from ..core.markup import Run
from ..core.project import DEFAULT_GP_SENTENCE, Project, ProjectError, Section
from .rich_text import RichTextEditor

SAVE_DELAY_MS = 800
HINT = "color: palette(placeholder-text);"


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
            "compound's ID). Anything else, e.g. {starting material} or {temperature}, is "
            "typed on each compound page.")
        self.users = QLabel()
        self.users.setStyleSheet(HINT)
        self.text = RichTextEditor(self)
        self.text.set_read_only(ro)
        for w in (self.name, self.title, self.sentence):
            w.setReadOnly(ro)
            w.textEdited.connect(self._edited)
        self.text.changed.connect(self._edited)

        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("Title:", self.title)
        form.addRow("Compound sentence:", self.sentence)
        sentence_hint = QLabel("Printed in each compound that uses this procedure. {GP}, {scale}, "
                               "{yield} and {product} fill themselves in; other {words} are "
                               "typed on each compound page.")
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
        self.list.blockSignals(False)
        ids = [gp["id"] for gp in self.project.general_procedures(self.section)]
        target = select if select in ids else (ids[0] if ids else None)
        if target is not None:
            self.list.setCurrentRow(ids.index(target))
        self._show(target)

    def _selected(self, item, _prev=None) -> None:
        self.flush()
        if self.project.journal is not None:
            self.project.journal.break_merge()   # (typing in another procedure: a new step)
        self._show(item.data(Qt.ItemDataRole.UserRole) if item else None)

    def _show(self, gp_id: str | None) -> None:
        self.current = gp_id
        found = self.project.general_procedure(gp_id) if gp_id else None
        self.right.setEnabled(found is not None)
        self.remove_btn.setEnabled(found is not None and not self.project.read_only)
        gp = found[1] if found else {}
        self.name.setText(gp.get("name", ""))
        self.title.setText(gp.get("title", ""))
        self.sentence.setText(gp.get("sentence", DEFAULT_GP_SENTENCE) if found else "")
        self.text.set_markup(self.project.read_gp_text(gp_id) if found else "")
        self._update_users()

    def _update_users(self) -> None:
        if not self.current:
            self.users.setText("Click Add to write a general procedure.")
            return
        report = self.project.id_report()
        users = [report.display_id(c.id) for c in self.project.gp_users(self.current)]
        self.users.setText("Used by: " + (", ".join(users) if users else "no compounds yet"))

    def _edited(self, *_args) -> None:
        if not self.project.read_only and self.current:
            self._timer.start()
            item = self.list.currentItem()
            if item is not None:
                item.setText(self.name.text().strip() or "(unnamed)")

    def flush(self) -> None:
        """Save the procedure on screen (if edited)."""
        if not self._timer.isActive() or self.project.read_only or not self.current:
            self._timer.stop()
            return
        self._timer.stop()
        try:
            with self.project.autosave():   # (typing: merged into one undo step)
                self.project.update_general_procedure(
                    self.current, name=self.name.text().strip() or "General Procedure",
                    title=self.title.text().strip(),
                    sentence=self.sentence.text().strip() or DEFAULT_GP_SENTENCE)
                self.project.write_gp_text(self.current, self.text.markup())
        except (ProjectError, OSError, KeyError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self.changed.emit()

    def _add(self) -> None:
        self.flush()
        with self.project.undo_step("Add a general procedure"):
            gp = self.project.add_general_procedure(self.section.id)
        self._fill_list(gp["id"])
        self.name.setFocus()
        self.name.selectAll()
        self.changed.emit()

    def _remove(self) -> None:
        if not self.current:
            return
        self.flush()
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
        if self.current:
            self.flush()
            _section, gp = self.project.general_procedure(self.current)
            with self.project.undo_step(f"Move general procedure “{gp.get('name')}”"):
                self.project.move_general_procedure(self.current, step)
            self._fill_list(self.current)
            self.changed.emit()


class GpChooser(QWidget):
    """On a compound's Procedure field: choose a general procedure and fill in its
    compound sentence. Everything automatic can be typed over (SPEC §2.7)."""

    changed = Signal()

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
                shown = "".join(r.text for r in links.display_runs(self.project, auto.get(k, []),
                                                                     report))
                w.setPlaceholderText(f"automatic: {shown}" if shown else
                                     f"automatic ({procedures.AUTO[k]}) — empty")
                w.setToolTip("Leave empty to use the automatic value, or type your own.")
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
