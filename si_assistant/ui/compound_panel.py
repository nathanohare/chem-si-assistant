"""The Compound Page (SPEC §7): header with ID and completion, then one box per field."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from ..core import chem, checks, procedures
from ..core import fields as F
from ..core.project import Compound, Project, ProjectError
from .dialogs import CustomFieldDialog
from .field_widgets import AttachmentsEditor, FieldBox, get_part_value, set_part_value
from .structure_widgets import (HrmsEditor, StructureEditor, drawing_settings, formula_html,
                                generic_structure, mol_pixmap, paste_key, picture_pixmap)

AUTOSAVE_DELAY_MS = 800
FIELD_SPACING = 3   # pixels between the field boxes


def scheme_note(scheme, compound) -> str:
    """The note on a scheme page's compound: whether its structure prints with its data
    or in the scheme (SPEC §13n.4: a compound the scheme editor doesn't draw prints its
    own)."""
    from ..core import scheme_editor
    if compound.id not in scheme_editor.linked_compound_ids(scheme):
        return (f"Part of scheme page “{scheme.name}”, not drawn in the scheme.\nIts "
                "structure is printed with its data.")
    return (f"Part of scheme page “{scheme.name}”.\nIts structure is not printed (the "
            "scheme is).")


def make_read_only(root: QWidget) -> None:
    """Make every input under `root` read-only, keeping text selectable/readable."""
    for w in root.findChildren(QWidget):
        if w.property("keep_enabled"):
            continue
        if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            w.setReadOnly(True)
        elif isinstance(w, (QComboBox, QAbstractButton)):
            w.setEnabled(False)
    root.setAcceptDrops(False)
    for w in root.findChildren(QWidget):
        w.setAcceptDrops(False)


class CompoundPanel(QWidget):
    THUMB_SIZE = QSize(96, 64)   # (the compact header, SPEC §13q.6c)

    labels_changed = Signal(str)     # compound id: its name or ID changed (refresh the tree)
    saved = Signal(str)              # status-bar message
    structure_updated = Signal(str)  # compound id: its structure (or picture) changed
    convert_requested = Signal(str)  # compound id: "Change to reaction scheme" was clicked
    view_spectra = Signal(str)       # compound id: "View spectra page" was clicked

    def __init__(self, project: Project, compound: Compound, user_name: Callable[[], str],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.compound = compound
        self.user_name = user_name
        self.boxes: list[FieldBox] = []
        # Fields edited since the last save: they name the undo step ("Edit 3a ▸ Yield").
        self._changed_keys: set[str] = set()
        # Edits not written yet: set by every edit, cleared only once the save worked (a
        # failed autosave is tried again by flush).
        self._dirty = False

        # ---- header
        self.id_label = QLabel()
        f = self.id_label.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setBold(True)
        self.id_label.setFont(f)
        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        f2 = self.name_label.font()
        f2.setPointSizeF(f2.pointSizeF() * 1.15)
        self.name_label.setFont(f2)

        self.override_edit = QLineEdit(compound.id_override)
        self.override_edit.setMaximumWidth(120)
        # (beside the live preview the name wraps; this box keeps room for "auto: 12ab")
        self.override_edit.setMinimumWidth(self.override_edit.fontMetrics().horizontalAdvance(
            "auto: 12ab") + 16)
        self.override_edit.setToolTip(
            "Type an ID here to override the ID table for this compound.\n"
            "Leave empty to use the automatic ID. The compound still uses up its row "
            "in the table, so the compounds after it keep their IDs.")
        self.override_edit.textEdited.connect(self._schedule_save)
        self.override_edit.textEdited.connect(lambda _t: self._changed_keys.add("id_override"))

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #d35400;")
        # A failed autosave stays said here (the status bar message soon goes) until a
        # save works.
        self.unsaved_label = QLabel()
        self.unsaved_label.setWordWrap(True)
        self.unsaved_label.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.unsaved_label.hide()
        # "◐ 1 / 8 required": the missing fields in its tooltip; clicking it opens the
        # first missing field (SPEC §13q.6c).
        self.completion_label = QLabel()
        self.completion_label.setTextFormat(Qt.TextFormat.RichText)
        self.completion_label.linkActivated.connect(lambda _l: self.focus_first_missing())
        self.thumb = QLabel()
        self.thumb.setFixedSize(self.THUMB_SIZE)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet("QLabel { background: white; color: #666; border-radius: 3px; "
                                 "border: 2px solid transparent; } "
                                 "QLabel:focus { border: 2px solid #2f80ed; }")
        # Click it, then Ctrl+V: pastes into the Structure field, as clicking the field
        # does (also while the field is collapsed). Once there is a structure the field's
        # Edit or Remove menu replaces it (SPEC §13h.3), so nothing is replaced by accident.
        self.thumb.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        thumb_paste = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self.thumb)
        thumb_paste.setContext(Qt.ShortcutContext.WidgetShortcut)
        thumb_paste.activated.connect(self.paste_structure)
        self.formula_label = QLabel()

        # ---- the compact header (SPEC §13q.6c): the structure on the left; beside it two
        # lines: ID, name … Manual ID, and formula, MW, required fields … collapse/expand.
        collapse_all = QToolButton()
        collapse_all.setText("▸ Collapse all")
        collapse_all.setToolTip("Fold every field to its title line")
        collapse_all.setAutoRaise(True)
        collapse_all.setProperty("keep_enabled", True)
        collapse_all.clicked.connect(lambda: self.set_all_collapsed(True))
        expand_all = QToolButton()
        expand_all.setText("▾ Expand all")
        expand_all.setToolTip("Open every field")
        expand_all.setAutoRaise(True)
        expand_all.setProperty("keep_enabled", True)
        expand_all.clicked.connect(lambda: self.set_all_collapsed(False))
        line1 = QHBoxLayout()
        line1.addWidget(self.id_label)
        line1.addWidget(self.name_label, 1)
        line1.addWidget(QLabel("Manual ID:"))
        line1.addWidget(self.override_edit)
        if compound.scheme_id:
            # The scheme note as a small ⓘ with the explanation in its tooltip.
            scheme = project.scheme(compound.scheme_id)
            self.scheme_info = QLabel("ⓘ")
            self.scheme_info.setToolTip(scheme_note(scheme, compound))
            self.scheme_info.setStyleSheet("color: palette(placeholder-text);")
            line1.addWidget(self.scheme_info)
        else:
            convert = QPushButton("Change to reaction scheme…")
            convert.setToolTip("Make a scheme page with this compound as its first sub-page. "
                               "You can then draw the scheme and add the other compounds "
                               "(starting materials, intermediates) as more sub-pages.")
            convert.setEnabled(not project.read_only)
            convert.clicked.connect(lambda: self.convert_requested.emit(self.compound.id))
            line1.addWidget(convert)
        line2 = QHBoxLayout()
        line2.addWidget(self.formula_label)
        line2.addSpacing(12)
        line2.addWidget(self.completion_label, 1)
        line2.addWidget(collapse_all)
        line2.addWidget(expand_all)
        lines = QVBoxLayout()
        lines.setSpacing(2)
        lines.addLayout(line1)
        lines.addLayout(line2)
        id_row = QHBoxLayout()
        id_row.addWidget(self.thumb)
        id_row.addLayout(lines, 1)

        # ---- fields
        self.fields_holder = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_holder)
        self.fields_layout.setContentsMargins(0, 0, 4, 0)
        self.fields_layout.setSpacing(FIELD_SPACING)   # (SPEC §13n.7c: fields close together)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.fields_holder)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self.add_btn = QToolButton()
        self.add_btn.setText("+ Add field")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_menu = QMenu(self.add_btn)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_btn.setMenu(self.add_menu)

        layout = QVBoxLayout(self)
        layout.addLayout(id_row)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.unsaved_label)
        layout.addWidget(self.scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_DELAY_MS)
        self._timer.timeout.connect(self.save)

        self._build_fields()
        self._refresh_header()

    # ------------------------------------------------------------------ building

    def _build_fields(self) -> None:
        while self.fields_layout.count():
            item = self.fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.boxes = []
        required = self.project.section(self.compound.section_id).required_keys()
        for defn in self.project.field_defs(self.compound):
            if defn.key == "notes":
                continue  # always last, after the Add-field button
            self._add_box(defn, defn.key in required)
        add_row = QHBoxLayout()
        add_row.addWidget(self.add_btn)
        add_row.addStretch()
        holder = QWidget()
        holder.setLayout(add_row)
        self.fields_layout.addWidget(holder)
        self._add_box(F.ALL_FIELDS["notes"], False)
        self.fields_layout.addStretch()
        if self.project.read_only:
            make_read_only(self.fields_holder)
            self.override_edit.setReadOnly(True)

    def _add_box(self, defn: F.FieldDef, required: bool) -> None:
        box = FieldBox(self.project, self.compound, defn, required, self.save_pending)
        box.changed.connect(self._schedule_save)
        box.changed.connect(lambda *_a, k=defn.key: self._changed_keys.add(k))
        box.dismiss_requested.connect(self._dismiss_warning)
        if isinstance(box.editor, StructureEditor):
            box.editor.structure_changed.connect(self._on_structure_changed)
        if isinstance(box.editor, AttachmentsEditor):
            # "add it now" link in the Additional files note. The page is rebuilt, so wait
            # until the click on the link has finished.
            box.editor.add_field_requested.connect(
                lambda key: QTimer.singleShot(0, lambda: self._add_field({"key": key})))
            # Files are saved at once; the tree's spectra pages may change with them.
            box.editor.files_changed.connect(lambda: self.labels_changed.emit(self.compound.id))
            # (e.g. "a ¹H report but no ¹H spectrum" goes once one is added or pasted)
            box.editor.files_changed.connect(self._refresh_header)
            if box.editor.has_labels():
                # (the inline files of the page's other file fields are numbered after these)
                box.editor.files_changed.connect(self._refresh_file_labels)
            box.editor.view_spectra_requested.connect(self._view_spectra)
            if hasattr(box.editor, "pasted"):   # the Spectra field (SPEC §13l.1)
                box.editor.pasted.connect(self.saved.emit)
        if isinstance(box.editor, HrmsEditor):
            # Report files are saved at once (SPEC §13n.3): warnings and the tree follow.
            box.editor.files_changed.connect(lambda: self.labels_changed.emit(self.compound.id))
            box.editor.files_changed.connect(self._refresh_header)
            box.editor.status.connect(self.saved.emit)
        if hasattr(box.editor, "refresh_structure") and hasattr(box.editor, "lookup_btn"):
            box.editor.status.connect(self.saved.emit)   # the Name field (SPEC §13n.5)
        if getattr(box.editor, "gp", None) is not None:   # the Experimental procedure field
            box.editor.gp.scale_typed.connect(self._scale_from_gp)
        box.remove_requested.connect(self._remove_field)
        self.boxes.append(box)
        self.fields_layout.addWidget(box)

    def _view_spectra(self) -> None:
        self._save_waiting()   # (a header or caption typed in the Spectra field)
        self.view_spectra.emit(self.compound.id)

    def focus_field(self, key: str) -> None:
        for box in self.boxes:
            if box.defn.key == key:
                box.set_collapsed(False)  # opening a field from the dashboard shows it
                self.scroll.ensureWidgetVisible(box, 0, 40)
                box.editor.setFocus()
                return

    def _scale_from_gp(self, typed: str) -> None:
        """A scale typed in the general procedure's {scale} box (e.g. "0.20 mmol") fills the
        Reaction scale field if it is empty or still holds what was filled in this way
        before (remembered in compound.json as "scale_from_gp"). A value the user typed
        in the field is left alone; a warning then says they differ (SPEC §13n.9)."""
        if self.project.read_only:
            return
        box = next((b for b in self.boxes if b.defn.key == "scale"), None)
        widgets = getattr(box.editor, "widgets", {}) if box is not None else {}
        if "value" not in widgets or "unit" not in widgets:
            return
        before = self.compound.data.get("scale_from_gp")
        before = (str(before.get("value", "")), str(before.get("unit", ""))) \
            if isinstance(before, dict) else None
        field = (get_part_value(widgets["value"]), get_part_value(widgets["unit"]))
        new = procedures.scale_to_fill(typed, field, before)
        if new is None:
            return
        value, unit = new
        set_part_value(widgets["value"], F.Part("value"), value)
        set_part_value(widgets["unit"], F.Part("unit", kind="choice"), unit)
        self.compound.values["scale"] = {**(self.compound.values.get("scale") or {}),
                                         "value": value, "unit": unit}
        self.compound.data["scale_from_gp"] = {"value": value, "unit": unit}
        self._changed_keys.update({"scale", "procedure"})
        if box.collapsed:
            box.refresh_summary()
        self._schedule_save()

    def structure_editor(self) -> StructureEditor | None:
        return next((b.editor for b in self.boxes if isinstance(b.editor, StructureEditor)),
                    None)

    def paste_structure(self) -> None:
        """Ctrl+V on the header's structure picture: the same as Ctrl+V on the Structure
        field (a structure or a picture). Once there is one, it is replaced only from the
        field's Edit or Remove menu, so this says where that is instead."""
        editor = self.structure_editor()
        if editor is None or self.project.read_only:
            return
        if editor.is_compact():
            self.saved.emit("This compound already has a structure: to replace it, use the "
                            "Structure field's “Edit or Remove” menu")
            return
        editor.paste_from_clipboard()

    def _dismiss_warning(self, warning) -> None:
        from .warnings_panel import ask_reason
        reason = ask_reason(self, warning.message)
        if reason is None:
            return
        self._save_waiting()
        try:
            with self.project.undo_step("Dismiss a warning",
                                        where=[("compound", self.compound.id)]):
                self.project.dismiss_warning(self.compound, warning, reason)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not dismiss the warning",
                                f"The warning could not be saved as dismissed ({e}). "
                                "Try again in a moment.")
            return
        self._refresh_header()
        self.labels_changed.emit(self.compound.id)
        self.saved.emit("Warning dismissed")

    def set_all_collapsed(self, collapsed: bool) -> None:
        # The summaries should reflect what's on screen. (If that can't be saved just now,
        # the page says so; folding the fields loses nothing, so it still goes on.)
        try:
            self._save_pending_or_raise()
        except (ProjectError, OSError):
            pass
        for box in self.boxes:
            box.set_collapsed(collapsed)

    # ------------------------------------------------------------------ header

    def _refresh_header(self) -> None:
        report = self.project.id_report()
        cid = self.compound.id
        shown = report.display_id(cid)
        lock = " 🔒" if self.compound.id_override else ""
        self.id_label.setText(shown + lock)
        self.id_label.setToolTip("Manual ID (overrides the ID table)" if lock else
                                 "Automatic ID from the section's ID table")
        self.name_label.setText(self.compound.name or "<i>unnamed compound</i>")
        mol = self.project.read_structure(self.compound)
        # A structure with a generic atom (R) is read as none: drawn all the same, with a
        # note instead of the formula.
        generic_mol, generic = ((None, []) if mol is not None
                                 else generic_structure(self.project, self.compound))
        # The structure (or its picture) shows here, drawn for this screen's resolution so
        # it is sharp (SPEC §13n.7b); the Structure field itself is one line once it has one.
        dpr = self.thumb.devicePixelRatioF()
        picture = self.project.structure_image_path(self.compound)
        pixmap = (picture_pixmap(picture, self.THUMB_SIZE - QSize(6, 6), dpr)
                  if picture is not None and picture.exists() else None)
        if pixmap is not None and not pixmap.isNull():
            self.thumb.setPixmap(pixmap)
        elif mol is not None or generic_mol is not None:
            self.thumb.setPixmap(mol_pixmap(mol if mol is not None else generic_mol,
                                            self.THUMB_SIZE, drawing_settings(self.project),
                                            dpr))
        else:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("no structure")
        has_any = mol is not None or (pixmap is not None and not pixmap.isNull())
        self.thumb.setToolTip(
            "" if self.project.read_only else
            "To replace the structure, use the Structure field's “Edit or Remove” menu."
            if has_any else f"Click here, then press {paste_key()} to paste the structure "
                            "(copied in ChemDraw), SMILES or a picture.")
        if mol is not None:
            try:
                info = chem.mol_info(mol)
                self.formula_label.setText(
                    f"{formula_html(info.formula)} &nbsp; MW {info.mw:.2f}")
            except chem.ChemError:   # e.g. a generic atom (R): no formula
                self.formula_label.setText("no formula (generic atom)")
        elif generic:
            self.formula_label.setText(f"no formula (generic atom {', '.join(generic)})")
        else:
            self.formula_label.setText("")
        table_id = self._table_id()
        self.override_edit.setPlaceholderText(f"auto: {table_id or 'none'}")
        # Automatic checks (SPEC §8.2): ID problems at the top, the rest on their fields.
        active = checks.active_warnings(self.project, self.compound, report)
        id_warnings = [w.message for w in active if w.field == checks.ID_KEY]
        self.warning_label.setText("\n".join(f"⚠ {w}" for w in id_warnings))
        self.warning_label.setVisible(bool(id_warnings))
        by_field: dict[str, list] = {}
        in_editor: dict[str, list] = {}   # (the Structure box shows these words itself)
        for w in active:
            (in_editor if w.key == checks.GENERIC_STRUCTURE_KEY
             else by_field).setdefault(w.field, []).append(w)
        for box in self.boxes:
            box.set_warnings(by_field.get(box.defn.key, []), in_editor.get(box.defn.key, []))

        filled, total, missing = self.project.completion(self.compound)
        if total == 0:
            text, tip = "No required fields", "No required fields set for this section."
        elif not missing:
            text = f"✔ All {total} required"
            tip = f"Complete — all {total} required fields filled."
        else:
            text = f'<a href="missing">◐ {filled} / {total} required</a>'
            tip = ("Missing: " + ", ".join(missing) + ".\nClick to open the first missing "
                   "field.")
        flags = len(self.compound.flags)
        if flags:
            text += f" &nbsp; ⚑ {flags} flag{'s' if flags != 1 else ''}"
        if active:
            text += f" &nbsp; ⚠ {len(active)} warning{'s' if len(active) != 1 else ''}"
        self.completion_label.setText(text)
        self.completion_label.setToolTip(tip)
        self.missing_labels = missing

    def focus_first_missing(self) -> None:
        """Open the first required field that is still empty (the header's "◐ … required")."""
        missing = getattr(self, "missing_labels", [])
        for box in self.boxes:
            if box.defn.label in missing:
                self.focus_field(box.defn.key)
                return

    def _table_id(self) -> str:
        section = self.project.section(self.compound.section_id)
        pos = section.compounds.index(self.compound)
        return section.id_table[pos] if pos < len(section.id_table) else ""

    # ------------------------------------------------------------------ saving

    def _schedule_save(self, *_args) -> None:
        if not self.project.read_only:
            self._dirty = True
            self._timer.start()

    def save_pending(self) -> bool:
        """Before a separate action on this page (a structure drawn, a file added, a field
        added…): typing not saved yet is saved first, as its own undo step, so undoing the
        action does not undo the typing too.

        False if that typing could not be saved (e.g. a file held by OneDrive): a message
        says so, the typing stays on screen (tried again later), and the action should not
        go on, since it may rebuild the page from the saved files and lose the typing."""
        try:
            self._save_pending_or_raise()
        except (ProjectError, OSError) as e:
            QMessageBox.warning(
                self, "Not saved yet",
                f"Your typing on this page could not be saved yet ({e}), so nothing was "
                "changed. It is kept on screen: try again in a moment.")
            return False
        return True

    def _save_pending_or_raise(self) -> None:
        """save_pending's saving (raises ProjectError/OSError if it fails; the page then
        says the typing is not saved yet, and it stays waiting)."""
        if self.project.read_only:
            return
        self.flush()
        try:
            with self.project.autosave():
                self.store_all()
        except (ProjectError, OSError) as e:
            self._dirty = True   # (tried again with the next edit or when the page is left)
            self.unsaved_label.setText(f"⚠ Not saved yet ({e}). Your typing is kept on "
                                       "screen; saving is tried again with your next edit "
                                       "or when you leave this page.")
            self.unsaved_label.show()
            raise

    def _shown_id(self) -> str:
        return self.project.id_report().display_id(self.compound.id)

    def store_all(self) -> None:
        """Copy everything on screen into the compound (writes .txt files, not compound.json)."""
        user = self.user_name()
        for box in self.boxes:
            box.store(user)
        self.compound.data["id_override"] = self.override_edit.text().strip()

    def save(self) -> None:
        """The autosave. If the files can't be written (e.g. compound.json held by OneDrive
        or a virus scanner, a full disk), the status bar says so and the edits stay waiting:
        they are saved with the next edit, or when the page is left (flush)."""
        try:
            self._write()
        except (ProjectError, OSError) as e:
            # (No quick retry: each attempt on a locked file waits about a second.)
            self.saved.emit(f"Could not save ({e}). It is tried again with your next edit "
                            "or when you leave this page.")

    def _write(self) -> None:
        """Save what is on screen (raises ProjectError/OSError if it can't be written)."""
        self._timer.stop()
        if self.project.read_only:
            return
        try:
            with self.project.autosave():   # (typing: merged into one undo step per field)
                self.store_all()
                self.project.save_compound(self.compound)
        except (ProjectError, OSError) as e:
            self.unsaved_label.setText(f"⚠ Not saved yet ({e}). Your typing is kept on "
                                       "screen; saving is tried again with your next edit "
                                       "or when you leave this page.")
            self.unsaved_label.show()
            raise
        self._dirty = False
        self.unsaved_label.hide()
        # The saved data is safe from here on: a hint that can't be updated just now (e.g. a
        # text file held by OneDrive while the figure labels are counted) keeps its old
        # text, and is not reported as a failed save.
        try:
            self._refresh_after_save()
        except (ProjectError, OSError):
            pass
        self._changed_keys.clear()
        # Name, manual ID or flags may have changed; the tree shows all three.
        self.labels_changed.emit(self.compound.id)
        self.saved.emit("Saved")

    def _refresh_after_save(self) -> None:
        """The header, the collapsed summaries and the automatic hints after a save."""
        self._refresh_header()
        for box in self.boxes:
            if box.collapsed:
                box.refresh_summary()
            if isinstance(box.editor, AttachmentsEditor):
                # (an Additional file set to "Spectra section" gives a spectra page)
                box.editor.refresh_view_button()
            gp = getattr(box.editor, "gp", None)
            if gp is not None:
                gp.refresh_auto()   # (Yield, Reaction scale, Purification or the ID changed)
            if box.defn.key == "purification":
                # (a general procedure chosen that places {purification} itself, or taken off)
                getattr(box.editor, "_show_purification", lambda: None)()
        if any(isinstance(b.editor, AttachmentsEditor) and b.defn.key in self._changed_keys
               for b in self.boxes):
            self._refresh_file_labels()   # (a placement or a typed figure label changed)

    def undo_context(self) -> tuple:
        """(merge key, label) of the undo step for this page's saves (SPEC §13k.11):
        saves of the same field merge into one step, "Edit 3a ▸ Yield"."""
        keys = tuple(sorted(self._changed_keys))
        cid = self.compound.id
        names = {b.defn.key: b.defn.label for b in self.boxes}
        names["id_override"] = "Manual ID"

        def label() -> str:
            shown = self.project.id_report().display_id(cid)
            return f"Edit {shown}" + (f" ▸ {names.get(keys[0], keys[0])}"
                                      if len(keys) == 1 else "")
        return ("compound", cid, keys), label

    def _save_waiting(self) -> None:
        """Save edits still waiting, before an action on this page (a failed save is said in
        the status bar; the action goes on)."""
        if self._dirty or self._timer.isActive():
            self.save()

    def has_unsaved(self) -> bool:
        """Edits whose save is still waiting: not tried yet, or tried and failed (kept to
        be tried again)."""
        return self._dirty or self._timer.isActive()

    def flush(self) -> None:
        """Save edits still waiting (also those whose autosave failed). Raises
        ProjectError/OSError if that fails, so the main window says so."""
        if self._dirty or self._timer.isActive():
            self._write()

    def _refresh_file_labels(self) -> None:
        """The automatic figure labels shown in the file fields (inline files are numbered
        across all of them, so a change in one field moves the numbers in the others)."""
        editors = [b.editor for b in self.boxes if isinstance(b.editor, AttachmentsEditor)
                   and b.editor.has_labels()]
        if editors:
            from ..core.labels import compute
            labels = compute(self.project)   # (once for the page)
            for editor in editors:
                editor.refresh_labels(labels)

    def _on_structure_changed(self, added: list) -> None:
        if added:
            # New NMR report boxes are needed. Rebuild once this click has finished.
            labels = ", ".join(f"{F.nucleus_label(n)} NMR" for n in added)
            self.saved.emit(f"Added {labels} report(s) — the structure contains "
                            f"{', '.join(n.lstrip('0123456789') for n in added)}")
            QTimer.singleShot(0, self._rebuild_keep_scroll)
        else:
            self.saved.emit("Structure saved")
        for box in self.boxes:
            if isinstance(box.editor, HrmsEditor):
                box.editor.structure_changed()
            elif isinstance(box.editor, StructureEditor):
                box.editor.refresh()
            elif hasattr(box.editor, "lookup_btn"):   # the Name field's PubChem button
                box.editor.refresh_structure()
            elif box.defn.key == "ea":   # the formula shown in its empty Formula box
                getattr(box.editor, "_show_ea_formula", lambda: None)()
        self._refresh_header()
        self.labels_changed.emit(self.compound.id)
        self.structure_updated.emit(self.compound.id)

    def refresh_structure(self) -> None:
        """The structure was changed from outside this page (e.g. by the scheme page)."""
        self._on_structure_changed([])

    def _rebuild_keep_scroll(self) -> None:
        pos = self.scroll.verticalScrollBar().value()
        self._build_fields()
        self._refresh_header()   # the new boxes need their warnings (as in _add_field)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(pos))

    # ------------------------------------------------------------------ adding/removing fields

    def _fill_add_menu(self) -> None:
        self.add_menu.clear()
        present = set(self.compound.field_keys)
        section = self.project.section(self.compound.section_id)
        core = [d for d in F.CORE_FIELDS if d.removable and d.key not in present]
        library = [d for d in F.LIBRARY_FIELDS if d.key not in present]
        custom = [e for e in section.default_fields
                  if e.get("custom") and e["key"] not in present]
        for group in (core, library):
            for d in group:
                self.add_menu.addAction(d.label, lambda d=d: self._add_field({"key": d.key}))
            if group:
                self.add_menu.addSeparator()
        for e in custom:
            # (A "&" in a typed name would otherwise underline the next letter as a shortcut.)
            self.add_menu.addAction(str(e["custom"].get("label") or e["key"]).replace("&", "&&"),
                                    lambda e=e: self._add_field(e))
        if custom:
            self.add_menu.addSeparator()
        self.add_menu.addAction("Custom field…", self._add_custom_field)

    def _add_custom_field(self) -> None:
        dlg = CustomFieldDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._add_field(dlg.entry())

    def _field_name(self, entry: dict) -> str:
        key = entry.get("key", "")
        if entry.get("custom"):
            return entry["custom"].get("label") or key
        defn = F.ALL_FIELDS.get(key)
        return defn.label if defn is not None else key

    def _add_field(self, entry: dict) -> None:
        if not self.save_pending():   # (the page is rebuilt below: typing would be lost)
            return
        try:
            with self.project.undo_step(f"Add field “{self._field_name(entry)}” to "
                                        f"{self._shown_id()}"):
                self.project.add_field(self.compound, entry)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not add field", str(e))
        self._build_fields()
        self._refresh_header()
        self.focus_field(entry["key"])

    def _remove_field(self, key: str) -> None:
        if not self.save_pending():   # (the page is rebuilt below: typing would be lost)
            return
        name = next((b.defn.label for b in self.boxes if b.defn.key == key), key)
        try:
            with self.project.undo_step(f"Remove field “{name}” from {self._shown_id()}"):
                self.project.remove_field(self.compound, key)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove field", str(e))
        self._build_fields()
        self._refresh_header()
        self.saved.emit("Field removed (its data is kept if you add it back)")
