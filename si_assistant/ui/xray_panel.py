"""The page of an X-ray structure section (SPEC §13v): its CIF, the compound it shows,
the drawing (ui/xray_viewer.XrayEditor, saved as it is changed), its caption, and its
crystal data table — printed or not, values typed over or filled in, rows left out.

Everything automatic can be replaced by hand (SPEC §2.7): untick "Automatic caption" to
type your own; double-click a value of the table to type over it ("Put back the CIF's
value" undoes that); untick a row to leave it out.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import crystal, labels
from ..core import xray as xr
from ..core.project import Project, ProjectError, Section
from .panels import AUTOSAVE_DELAY_MS, SectionPanel
from .rich_text import RichTextEditor
from .xray_viewer import XrayEditor

KEY_ROLE = Qt.ItemDataRole.UserRole
PRINT_COL, ITEM_COL, VALUE_COL = 0, 1, 2


class XraySectionPanel(SectionPanel):
    """An X-ray structure section (see the module's text)."""

    moved = Signal()            # put under another compound: the tree is rebuilt
    labels_changed = Signal()   # figure/table numbers or captions' compound changed

    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(project, section, parent)
        ro = project.read_only
        self.setAcceptDrops(not ro)
        self._loading = False
        self._unsaved = False   # a save failed: tried again on flush (see MainWindow)
        self.loaded = xr.Loaded()

        # ---- the CIF and the compound
        form = QFormLayout()
        file_row = QHBoxLayout()
        self.file_label = QLabel()
        self.file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.block_box = QComboBox()
        self.block_box.setToolTip("This CIF holds several structures: the one shown")
        self.block_box.activated.connect(self._block_chosen)
        self.choose_btn = QPushButton("Choose CIF…")
        self.choose_btn.setToolTip("The structure's CIF file (from the crystallographer or "
                                   "the CCDC). A copy is kept in the project folder. You "
                                   "can also drop a .cif file on this page.")
        self.choose_btn.clicked.connect(self._choose)
        file_row.addWidget(self.file_label)
        file_row.addWidget(self.block_box)
        file_row.addWidget(self.choose_btn)
        form.addRow("CIF file:", file_row)
        compound_row = QHBoxLayout()
        self.compound_box = QComboBox()
        self.compound_box.setMinimumContentsLength(18)
        self.compound_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.compound_box.setToolTip("The compound this structure is of: its ID is in the "
                                     "captions (and follows renumbering)")
        self.compound_box.activated.connect(self._compound_chosen)
        self.place_note = QLabel()
        self.place_note.setWordWrap(True)
        self.place_note.setStyleSheet("color: palette(placeholder-text);")
        compound_row.addWidget(self.compound_box)
        compound_row.addWidget(self.place_note, 1)
        form.addRow("Compound:", compound_row)
        self.layout_.addLayout(form)

        # ---- the drawing, its caption under it
        self.editor = XrayEditor()
        self.editor.view_changed.connect(self._view_changed)
        self.caption_auto = QCheckBox("Automatic caption")
        self.caption_auto.setToolTip("Written from the drawing (ellipsoid probability, "
                                     "hydrogens left out…) and the CCDC number. Untick it to "
                                     "type your own.")
        self.caption_auto.toggled.connect(lambda on: self._auto_toggled("caption", on))
        self.caption_edit = RichTextEditor(self, headings=False, height=48)
        self.caption_edit.changed.connect(self._caption_timer_start)
        head = QHBoxLayout()
        self.figure_label = QLabel("Caption:")
        head.addWidget(self.figure_label)
        head.addStretch()
        head.addWidget(self.caption_auto)
        self.editor.under_canvas.addLayout(head)
        self.editor.under_canvas.addWidget(self.caption_edit)

        # ---- the crystal data table
        page = QWidget()
        tl = QVBoxLayout(page)
        self.print_table = QCheckBox("Print the crystal data table (a numbered table after "
                                     "the figure)")
        self.print_table.toggled.connect(self._print_table_toggled)
        tl.addWidget(self.print_table)
        head = QHBoxLayout()
        self.table_label = QLabel("Table caption:")
        head.addWidget(self.table_label)
        head.addStretch()
        self.table_caption_auto = QCheckBox("Automatic caption")
        self.table_caption_auto.toggled.connect(lambda on: self._auto_toggled("table_caption", on))
        head.addWidget(self.table_caption_auto)
        tl.addLayout(head)
        self.table_caption_edit = RichTextEditor(self, headings=False, height=48)
        self.table_caption_edit.changed.connect(self._caption_timer_start)
        tl.addWidget(self.table_caption_edit)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Print", "Item", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setToolTip("Untick a row to leave it out. Double-click a value to type "
                              "over it, or to fill in one the CIF does not have (e.g. the "
                              "CCDC number). Sub- and superscript digits can be typed as "
                              "₀₁₂… and ⁰¹²…")
        self.table.itemChanged.connect(self._table_edited)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        tl.addWidget(self.table, 1)
        row = QHBoxLayout()
        note = QLabel("Values typed over the CIF's are shown in bold.")
        note.setStyleSheet("color: palette(placeholder-text);")
        row.addWidget(note, 1)
        self.restore_btn = QPushButton("Put back the CIF's value")
        self.restore_btn.clicked.connect(self._restore_values)
        row.addWidget(self.restore_btn)
        tl.addLayout(row)
        self.editor.tabs.addTab(page, "Crystal data table")
        self.layout_.addWidget(self.editor, 1)

        # Typing (captions) and turning (the view) are saved a moment after they stop.
        self._caption_timer = QTimer(self)
        self._caption_timer.setSingleShot(True)
        self._caption_timer.setInterval(AUTOSAVE_DELAY_MS)
        self._caption_timer.timeout.connect(self._save_captions)
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(AUTOSAVE_DELAY_MS)
        self._view_timer.timeout.connect(self._save_view)

        for w in (self.choose_btn, self.compound_box, self.block_box, self.caption_auto,
                  self.print_table, self.table_caption_auto):
            w.setEnabled(not ro)
        if ro:
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._load()

    # ------------------------------------------------------------------ showing

    def _load(self) -> None:
        """Show the section as stored: its file, compound, drawing, captions, table."""
        self._loading = True
        try:
            s = self.section
            self.loaded = xr.load(self.project, s)
            path = self.project.section_file_path(s)
            self.file_label.setText(f"<b>{path.name}</b>" if path is not None else
                                    "No CIF file chosen yet")
            self.choose_btn.setText("Replace…" if path is not None else "Choose CIF…")
            blocks = self.loaded.blocks or []
            self.block_box.clear()
            for name in blocks:
                self.block_box.addItem(name or "(no name)", name)
            if self.loaded.block is not None:
                self.block_box.setCurrentIndex(max(0, self.block_box.findData(
                    self.loaded.block.name)))
            self.block_box.setVisible(len(blocks) > 1)
            self._fill_compounds()
            if self.loaded.grown is None:
                self.editor.clear(self.loaded.error + ("" if path is not None else
                                  "\n\nChoose the structure's CIF file (Choose CIF… above), "
                                  "or drop it on this page."))
            else:
                self.editor.set_structure(self.loaded.structure, self.loaded.grown,
                                          xr.view_of(s, self.loaded.grown))
                self.editor.picture_name = s.name
            self.print_table.setChecked(bool(s.data.get("table", True)))
            self._fill_table()
            self._show_captions()
        finally:
            self._loading = False
        self._sync_buttons()

    def _fill_compounds(self) -> None:
        s, p = self.section, self.project
        report = p.id_report()
        self.compound_box.clear()
        anchored = s.anchored_to
        if not anchored:
            self.compound_box.addItem("(none)", "")
        for c in p.all_compounds():
            self.compound_box.addItem(f"{report.display_id(c.id)} — {c.name or '(unnamed)'}",
                                      c.id)
        index = self.compound_box.findData(s.data.get("compound") or "")
        self.compound_box.setCurrentIndex(max(0, index))
        if anchored:
            shown = report.display_id(anchored)
            self.place_note.setText(f"Printed right after {shown} (it sits under it in the "
                                    "tree). Drag it elsewhere in the tree to print it there.")
        else:
            self.place_note.setText("Printed where it sits in the tree. Drag it onto its "
                                    "compound in the tree to print it right after that "
                                    "compound's page.")

    def _labels(self) -> tuple[str, str]:
        """The figure's and table's labels ("Figure S3", "Table S1"), or ""."""
        from . import rich_text
        ctx = rich_text.link_context()
        try:
            if ctx is not None and ctx.project is self.project:
                ctx.invalidate()   # (shared with the text boxes: may have changed)
                found = ctx.labels()
            else:
                found = labels.compute(self.project)
        except (OSError, ValueError, KeyError):   # (only a label on the page)
            return "", ""
        figure, table = found.at(self.section.id, 0), found.at(self.section.id, 1)
        return (figure.label if figure is not None else "",
                table.label if table is not None else "")

    def _show_captions(self) -> None:
        figure, table = self._labels()
        self.figure_label.setText(f"Caption of {figure}:" if figure else "Caption:")
        self.table_label.setText(f"Caption of {table}:" if table else "Table caption:")
        for key, edit, box, auto in (
                ("caption", self.caption_edit, self.caption_auto, self._auto_caption()),
                ("table_caption", self.table_caption_edit, self.table_caption_auto,
                 xr.auto_table_caption(self.project, self.section))):
            typed = str(self.section.data.get(key) or "")
            box.blockSignals(True)
            box.setChecked(not typed.strip())
            box.blockSignals(False)
            edit.set_markup(typed if typed.strip() else auto)
            edit.set_read_only(self.project.read_only or not typed.strip())

    def _auto_caption(self) -> str:
        return xr.auto_caption(self.project, self.section, self.editor.view(), self.loaded.block)

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        rows = xr.table_rows(self.section, self.loaded.block, all_rows=True)
        typed = xr.typed_values(self.section)
        hidden = self.section.data.get("table_hidden")
        hidden = set(hidden) if isinstance(hidden, list) else set()
        ro = self.project.read_only
        self.table.setRowCount(len(rows))
        for r, (key, label, value, _printed) in enumerate(rows):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.NoItemFlags if ro else
                                                        Qt.ItemFlag.ItemIsUserCheckable))
            tick.setCheckState(Qt.CheckState.Unchecked if key in hidden
                               else Qt.CheckState.Checked)
            tick.setData(KEY_ROLE, key)
            item = QTableWidgetItem(xr.plain(label))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            cell = QTableWidgetItem(xr.plain(value))
            cell.setData(KEY_ROLE, key)
            if key in typed:
                font = QFont(cell.font())
                font.setBold(True)
                cell.setFont(font)
                cell.setToolTip("Typed over the CIF's value")
            elif not value:
                cell.setToolTip("Not in the CIF: double-click to type it in")
            if ro:
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r, PRINT_COL, tick)
            self.table.setItem(r, ITEM_COL, item)
            self.table.setItem(r, VALUE_COL, cell)
        self.table.resizeColumnToContents(PRINT_COL)
        self.table.resizeColumnToContents(ITEM_COL)
        self.table.blockSignals(False)
        self._sync_buttons()

    def _refill_later(self) -> None:
        """The table shown again once the edit that changed it is over (its items are
        replaced: not while one of them is still sending its change)."""
        QTimer.singleShot(0, self, self._fill_table)

    def _sync_buttons(self) -> None:
        typed = xr.typed_values(self.section)
        keys = {self.table.item(i.row(), VALUE_COL).data(KEY_ROLE)
                for i in self.table.selectedIndexes() if self.table.item(i.row(), VALUE_COL)}
        self.restore_btn.setEnabled(not self.project.read_only and bool(keys & set(typed)))
        on = self.print_table.isChecked()
        for w in (self.table_caption_auto, self.table_caption_edit, self.table):
            w.setEnabled(on)
        self.table_caption_auto.setEnabled(on and not self.project.read_only)

    # ------------------------------------------------------------------ saving

    def _save(self, label: str | None, change, after=None) -> bool:
        """Change section.data (`change(data)`) and save it: one undo step named `label`
        (None: typing or turning, merged with the page's next saves). The change is taken
        back if it could not be saved."""
        if self.project.read_only:
            return False
        before = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list)
                      else v) for k, v in self.section.data.items()}
        change(self.section.data)
        try:
            with (self.project.undo_step(label) if label else self.project.autosave()):
                self.project.save_section(self.section)
        except (ProjectError, OSError) as e:
            self.section.data.clear()
            self.section.data.update(before)
            self._unsaved = label is None
            QMessageBox.warning(self, "Could not save", str(e))
            return False
        self._unsaved = False
        if after is not None:
            after()
        self.saved.emit("Saved")
        return True

    def _view_changed(self) -> None:
        if not self._loading and not self.project.read_only:
            self._view_timer.start()
        self._refresh_auto_caption()

    def _refresh_auto_caption(self) -> None:
        """The automatic caption follows the drawing (e.g. hydrogens hidden)."""
        if self.caption_auto.isChecked():
            self.caption_edit.set_markup(self._auto_caption())

    def _save_view(self) -> None:
        self._view_timer.stop()
        if self.loaded.grown is None:
            return
        view = self.editor.view().to_json()
        if view != self.section.data.get("view"):
            self._save(None, lambda d: d.__setitem__("view", view))

    def _caption_timer_start(self) -> None:
        if not self._loading:
            self._caption_timer.start()

    def _save_captions(self) -> None:
        self._caption_timer.stop()
        new = {}
        for key, edit, box in (("caption", self.caption_edit, self.caption_auto),
                               ("table_caption", self.table_caption_edit,
                                self.table_caption_auto)):
            if not box.isChecked():
                new[key] = edit.markup()
        if any(self.section.data.get(k) != v for k, v in new.items()):
            self._save(None, lambda d: d.update(new))

    def _auto_toggled(self, key: str, on: bool) -> None:
        """Automatic caption ticked (the typed one is dropped) or unticked (typing starts
        from the automatic text)."""
        if self._loading:
            return
        self._save_captions()
        auto = self._auto_caption() if key == "caption" else \
            xr.auto_table_caption(self.project, self.section)
        what = "caption" if key == "caption" else "table caption"
        label = f"Automatic {what} of “{self.section.name}”" if on else \
            f"Type the {what} of “{self.section.name}”"
        if not self._save(label, lambda d: d.__setitem__(key, "" if on else auto)):
            self._show_captions()
            return
        self._show_captions()
        if not on:
            edit = self.caption_edit if key == "caption" else self.table_caption_edit
            edit.edit.setFocus()

    def _print_table_toggled(self, on: bool) -> None:
        if self._loading:
            return
        what = "Print" if on else "Leave out"
        if self._save(f"{what} the crystal data table of “{self.section.name}”",
                      lambda d: d.__setitem__("table", on)):
            self.labels_changed.emit()
            self._show_captions()
        else:
            self.print_table.blockSignals(True)
            self.print_table.setChecked(not on)
            self.print_table.blockSignals(False)
        self._sync_buttons()

    def _table_edited(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        key = item.data(KEY_ROLE)
        if item.column() == PRINT_COL:
            hide = item.checkState() != Qt.CheckState.Checked

            def change(d):
                hidden = [k for k in (d.get("table_hidden") or []) if k != key]
                d["table_hidden"] = hidden + ([key] if hide else [])
            label = self.table.item(item.row(), ITEM_COL).text()
            self._save(f"{'Leave out' if hide else 'Print'} “{label}” of “{self.section.name}”",
                       change)
            self._refill_later()
            return
        if item.column() != VALUE_COL:
            return
        text = item.text().strip()
        cif_value = next((v for k, _l, v in crystal.crystal_table(
            self.loaded.block, keep_empty=True) if k == key), "") if self.loaded.block else ""
        stored = "" if not text or text == xr.plain(cif_value) else xr.from_plain(text)

        def change(d):
            values = dict(d.get("table_values") or {})
            if stored:
                values[key] = stored
            else:
                values.pop(key, None)
            d["table_values"] = values
        label = self.table.item(item.row(), ITEM_COL).text()
        self._save(f"Change “{label}” of “{self.section.name}”", change,
                   after=self._refresh_auto_caption)
        self._refill_later()

    def _restore_values(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        keys = [self.table.item(r, VALUE_COL).data(KEY_ROLE) for r in sorted(rows)]

        def change(d):
            d["table_values"] = {k: v for k, v in (d.get("table_values") or {}).items()
                                 if k not in keys}
        if self._save(f"Put back the CIF's values in “{self.section.name}”", change,
                      after=self._refresh_auto_caption):
            self._fill_table()

    def _compound_chosen(self, _index: int) -> None:
        cid = self.compound_box.currentData() or ""
        s = self.section
        if cid == (s.data.get("compound") or ""):
            return
        self.flush()
        report = self.project.id_report()
        if s.anchored_to and cid:
            try:
                with self.project.undo_step(f"Put “{s.name}” under {report.display_id(cid)}"):
                    self.project.anchor_section(s.id, cid)
            except (ProjectError, OSError, KeyError) as e:
                QMessageBox.warning(self, "Could not save", str(e))
                self._fill_compounds()
                return
            self.saved.emit("Saved")
            self.moved.emit()
        elif not self._save(f"Show {report.display_id(cid)} in “{s.name}”" if cid else
                            f"No compound for “{s.name}”",
                            lambda d: d.__setitem__("compound", cid)):
            self._fill_compounds()
            return
        self._fill_compounds()
        self._show_captions()
        self.labels_changed.emit()

    def _block_chosen(self, _index: int) -> None:
        name = self.block_box.currentData()
        if name is None or self.loaded.block is None or name == self.loaded.block.name:
            return
        self.flush()
        if self._save(f"Show the structure {name or '(no name)'} in “{self.section.name}”",
                      lambda d: d.update({"block": name, "view": {}})):
            self._load()

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the structure's CIF file", "",
                                              "CIF files (*.cif);;All files (*)")
        if path:
            self.attach(Path(path))

    def attach(self, path: Path) -> None:
        """Use `path` as the section's CIF (a copy goes into the project folder; the one
        it replaces goes to the Trash). The drawing starts again from the best view; typed
        table values are cleared (they were for the other structure)."""
        if self.project.read_only:
            return
        if self.project.is_section_file(self.section, path):
            QMessageBox.information(self, "Already attached",
                                    f"“{path.name}” is already the CIF of this section; "
                                    "nothing was changed.")
            return
        self.flush()
        before = dict(self.section.data)
        try:
            with self.project.undo_step(f"Attach {path.name} to “{self.section.name}”"):
                self.project.attach_file(self.section, path)
                self.section.data.update({"block": "", "view": {}, "table_values": {}})
                self.project.save_section(self.section)
        except (ProjectError, OSError) as e:
            if self.section.data.get("file") == before.get("file"):
                self.section.data.clear()     # (nothing attached: as it was)
                self.section.data.update(before)
            QMessageBox.warning(self, "Could not attach the CIF", str(e))
            self._load()
            return
        self.saved.emit("CIF attached (a copy is stored in the project folder)")
        self._load()
        self.labels_changed.emit()

    def flush(self) -> None:
        super().flush()
        if self._view_timer.isActive() or self._unsaved:
            self._save_view()
        if self._caption_timer.isActive():
            self._save_captions()

    # ------------------------------------------------------------------ dropping a CIF

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._dropped_path(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._dropped_path(event)
        if path is not None:
            event.acceptProposedAction()
            self.attach(path)

    def _dropped_path(self, event) -> Path | None:
        if self.project.read_only or not event.mimeData().hasUrls():
            return None
        urls = event.mimeData().urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            return None
        path = Path(urls[0].toLocalFile())
        return path if path.suffix.lower() == ".cif" else None
