"""Project settings, Trash, lock, ID-pattern and custom-field dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QProgressDialog, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ..core import fields as F
from ..core.compound_ids import LETTERS, NUMBERS, pattern_ids
from ..core.ids import new_id
from ..core.lock import LockInfo
from ..core.project import JOURNAL_STYLES, Project, ProjectError
from .clipboard_image import DEFAULT_DPI, PASTE_DPIS, paste_dpi


class EditableTable(QWidget):
    """A small table with Add / Remove / Move up / Move down buttons."""

    changed = Signal()   # any cell edited, or a row added, removed or moved

    def __init__(self, headers: list[str], checkbox_columns: set[int] = frozenset(),
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.checkbox_columns = set(checkbox_columns)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(lambda *_: self.changed.emit())

        buttons = QVBoxLayout()
        self.buttons: list[QPushButton] = []
        for text, slot in (("Add", lambda: self.add_row()), ("Remove", self._remove),
                           ("Move up", lambda: self._move(-1)),
                           ("Move down", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.clicked.connect(lambda *_: self.changed.emit())
            buttons.addWidget(b)
            self.buttons.append(b)
        buttons.addStretch()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

    def add_row(self, values: list | None = None) -> None:
        values = values or [""] * self.table.columnCount()
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_row(row, values)

    def _set_row(self, row: int, values: list) -> None:
        for col, value in enumerate(values):
            item = QTableWidgetItem()
            if col in self.checkbox_columns:
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                              | Qt.ItemFlag.ItemIsSelectable)
                item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
            else:
                item.setText(str(value))
            self.table.setItem(row, col, item)

    def commit_edit(self) -> None:
        """Commit a cell that is still being typed in (no Enter or click elsewhere yet),
        so its text is not lost when the page is saved or closed."""
        t = self.table
        if t.state() != QAbstractItemView.State.EditingState:
            return
        current = t.currentIndex()
        # Moving the current cell makes Qt commit and close the open editor.
        t.setCurrentIndex(t.model().index(-1, -1))
        if current.isValid():
            t.setCurrentIndex(current)

    def rows(self) -> list[list]:
        self.commit_edit()
        out = []
        for row in range(self.table.rowCount()):
            values = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if col in self.checkbox_columns:
                    values.append(item is not None and item.checkState() == Qt.CheckState.Checked)
                else:
                    values.append(item.text().strip() if item else "")
            out.append(values)
        return out

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _move(self, step: int) -> None:
        row = self.table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        all_rows = self.rows()
        all_rows[row], all_rows[target] = all_rows[target], all_rows[row]
        for r in (row, target):
            self._set_row(r, all_rows[r])
        self.table.selectRow(target)


class ProjectSettingsDialog(QDialog):
    def __init__(self, project: Project, user_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Project settings")
        self.resize(760, 520)
        self.project = project
        s = project.settings

        tabs = QTabWidget()
        # (Title, authors and affiliations are on Front matter ▸ Cover page, SPEC §13k.4.)

        # ---- Output
        output = QWidget()
        oform = QFormLayout(output)
        self.style_box = QComboBox()
        self.style_box.addItems(JOURNAL_STYLES)
        self.style_box.setCurrentText(s.get("journal_style", "ACS"))
        oform.addRow("Journal style:", self.style_box)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.1, 100.0)
        self.tolerance.setDecimals(1)
        self.tolerance.setSuffix(" ppm")
        self.tolerance.setValue(float(s.get("hrms_tolerance_ppm", 5.0)))
        oform.addRow("HRMS tolerance:", self.tolerance)
        spectra_note = QLabel("Spectra per page, the structure's position on them and the rest "
                              "of the spectra layout are set on the Spectra section's page in "
                              "the tree.")
        spectra_note.setWordWrap(True)
        spectra_note.setStyleSheet("color: palette(placeholder-text);")
        oform.addRow("Spectra:", spectra_note)
        self.auto_31p = QCheckBox("Automatically add a ³¹P NMR report when the structure contains P")
        self.auto_31p.setChecked(bool(s.get("auto_nmr_31P", False)))
        self.auto_11b = QCheckBox("Automatically add an ¹¹B NMR report when the structure contains B")
        self.auto_11b.setChecked(bool(s.get("auto_nmr_11B", False)))
        oform.addRow(QLabel("¹⁹F NMR reports are always added automatically for compounds containing F."))
        oform.addRow(self.auto_31p)
        oform.addRow(self.auto_11b)

        # Pasted ChemDraw pictures (SPEC §13k.6)
        self.paste_dpi = QComboBox()
        for dpi in PASTE_DPIS:
            self.paste_dpi.addItem(f"{dpi} dpi" + (" (recommended)" if dpi == DEFAULT_DPI
                                                  else ""), dpi)
        self.paste_dpi.setCurrentIndex(max(0, self.paste_dpi.findData(paste_dpi(s))))
        self.paste_dpi.setToolTip("Resolution of pictures pasted from ChemDraw (and other "
                                  "vector pictures). 1200 dpi prints sharper; 600 dpi makes "
                                  "smaller files.")
        self.rerender_btn = QPushButton("Re-render pasted pictures…")
        self.rerender_btn.setToolTip(
            "Draw every pasted ChemDraw picture again from its saved original, at the "
            "resolution chosen here, and convert every spectrum pasted from Mnova again from "
            "its saved original (as a vector drawing where possible). Pictures pasted before "
            "the original was saved (and screenshots or image files) are left as they are.")
        self.rerender_btn.clicked.connect(self._rerender)
        self.rerender_btn.setEnabled(not project.read_only)
        self.pictures_changed = False
        dpi_row = QHBoxLayout()
        dpi_row.addWidget(self.paste_dpi)
        dpi_row.addWidget(self.rerender_btn)
        dpi_row.addStretch()
        oform.addRow("Pasted picture resolution:", dpi_row)
        tabs.addTab(output, "Output")

        # ---- Structures (how the program draws structures, SPEC §13k.7)
        self.structures = StructureDrawingTab(s)
        tabs.addTab(self.structures, "Structures")

        # ---- Page (header, footer and page numbers, SPEC §13k.2a)
        self.page_tab = PageTab(s, self._page_context)
        self._style_now = self.style_box.currentText()
        self.style_box.currentTextChanged.connect(self._style_changed)
        tabs.addTab(self.page_tab, "Page")

        # ---- Labels (figure, table and scheme numbering, SPEC §13k.5a)
        self.labels_tab = LabelsTab(s)
        tabs.addTab(self.labels_tab, "Labels")

        # ---- NMR impurities (the table behind the solvent/impurity-peak check)
        self.impurities = ImpurityTableEditor(s)
        tabs.addTab(self.impurities, "NMR impurities")

        # ---- You
        you = QWidget()
        yform = QFormLayout(you)
        self.user_edit = QLineEdit(user_name)
        yform.addRow("Your name:", self.user_edit)
        yform.addRow(QLabel("Your name is shown to others when you have a project open, and on "
                            "flag comments.\nIt is stored on this computer, not in the project, "
                            "so each person sharing a project keeps their own."))
        tabs.addTab(you, "You")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if project.read_only:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            self.setWindowTitle("Project settings (read-only)")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def user_name(self) -> str:
        return self.user_edit.text().strip()

    def _page_context(self) -> dict:
        """What the header/footer example needs: the title and authors (Front matter ▸
        Cover page) and the journal style chosen here (not yet saved)."""
        s = self.project.settings
        return {"title": str(s.get("title", "")).strip(), "authors": s.get("authors", []),
                "journal_style": self.style_box.currentText()}

    def _style_changed(self, style: str) -> None:
        self.page_tab.journal_style_changed(self._style_now, style)
        self.labels_tab.journal_style_changed(self._style_now, style)
        self._style_now = style

    def _rerender(self) -> None:
        from .clipboard_image import rerender_pasted_pictures
        from .spectrum_paste import reconvert_pasted_spectra
        dpi = self.paste_dpi.currentData()
        # Redrawing takes about 0.1 s per picture: show progress, and allow stopping.
        bar = QProgressDialog("Redrawing pasted pictures…", "Stop", 0, 1, self)
        bar.setWindowTitle("Re-render pasted pictures")
        bar.setWindowModality(Qt.WindowModality.WindowModal)
        bar.setMinimumDuration(400)
        n_pictures = len(self.project.pasted_pictures())
        n_spectra = len(self.project.pasted_spectra())

        def progress(i: int, total: int, what: str = "picture", offset: int = 0) -> bool:
            whole = n_pictures + n_spectra
            bar.setMaximum(max(1, whole))
            bar.setValue(offset + i)
            bar.setLabelText(f"Redrawing {what} {i + 1} of {total}…")
            QApplication.processEvents()
            return not bar.wasCanceled()

        spectra = (0, 0, 0)
        try:
            done, without, failed = rerender_pasted_pictures(self.project, dpi, progress)
            if not bar.wasCanceled():   # pasted spectra, converted again (SPEC §13l.1)
                spectra = reconvert_pasted_spectra(
                    self.project, lambda i, total: progress(i, total, "spectrum", n_pictures))
            # Keep the setting in step with the pictures, even if the dialog is cancelled.
            self.project.settings["paste_dpi"] = dpi
            self.project.save_settings()
        except (ProjectError, OSError) as e:
            bar.close()
            QMessageBox.warning(self, "Re-render pasted pictures", str(e))
            return
        except Exception as e:  # noqa: BLE001 — say so rather than failing silently
            bar.close()
            self.pictures_changed = True   # (some may have been redrawn before it stopped)
            QMessageBox.warning(
                self, "Re-render pasted pictures",
                "Re-rendering stopped because of an unexpected problem; the pictures and "
                "spectra done until then were kept, the rest left as they were.\n\n"
                f"Details: {type(e).__name__}: {e}")
            return
        stopped = bar.wasCanceled()
        bar.close()
        self.pictures_changed = self.pictures_changed or bool(done) or bool(spectra[0])
        lines = [f"Re-rendered {done} pasted picture(s) at {dpi} dpi."]
        if spectra[0] or n_spectra:
            lines.append(f"Converted {spectra[0]} pasted spectrum/spectra again from Mnova's "
                         f"original ({spectra[1]} kept as vector drawings, the rest as "
                         "pictures).")
        if spectra[2]:
            lines.append(f"{spectra[2]} spectrum original(s) could not be converted here (a "
                         "Windows metafile can only be converted on Windows) and were left as "
                         "they are.")
        if stopped:
            lines.append("Stopped before the end: the remaining pictures were left as they "
                         "are. Click “Re-render pasted pictures” again to finish them.")
        if without:
            lines.append(f"{without} picture(s) have no saved ChemDraw original (pasted before "
                         "this version, screenshots or image files) and were left as they "
                         "are. Paste them again to change their resolution.")
        if failed:
            lines.append(f"{failed} original(s) could not be drawn here (an EMF picture can "
                         "only be drawn on Windows) and were left as they are.")
        QMessageBox.information(self, "Re-render pasted pictures", "\n\n".join(lines))

    def apply(self) -> None:
        """Copy the dialog's values into the project and save."""
        s = self.project.settings
        s["journal_style"] = self.style_box.currentText()
        s["hrms_tolerance_ppm"] = self.tolerance.value()
        s["auto_nmr_31P"] = self.auto_31p.isChecked()
        s["auto_nmr_11B"] = self.auto_11b.isChecked()
        s["paste_dpi"] = self.paste_dpi.currentData()
        self.structures.apply(s)
        self.page_tab.apply(s)   # after journal_style: the footer is compared with its default
        self.labels_tab.apply(s)   # (likewise the figure word, "Fig." for RSC)
        self.impurities.apply(s)
        self.project.save_settings()


SAMPLE_SMILES = "C[C@H](NC(C)=O)c1ccc(F)cc1"   # stereocentre, F, N–H and methyls


class StructureDrawingTab(QWidget):
    """Project settings ▸ Structures: how the program draws structures (SPEC §13k.7),
    with a live sample, and which structures are used when compiling."""

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        from ..core import chem
        self._chem = chem
        current = chem.DrawingSettings.from_settings(settings)

        self.compile_choice = QComboBox()
        self.compile_choice.addItem("Pasted ChemDraw (or your own) pictures where available",
                                    "pictures")
        self.compile_choice.addItem("The program's drawings for every compound", "acs")
        self.compile_choice.setCurrentIndex(
            max(0, self.compile_choice.findData(settings.get("compile_structures", "pictures"))))
        self.compile_choice.setToolTip("Also chosen in the Compile SI window. Compounds without "
                                       "a picture always get the program's drawing.")

        self.bond = QDoubleSpinBox()
        self.bond.setRange(0.2, 2.0)
        self.bond.setDecimals(3)
        self.bond.setSingleStep(0.01)
        self.bond.setSuffix(" cm")
        self.line = QDoubleSpinBox()
        self.line.setRange(0.1, 3.0)
        self.line.setDecimals(2)
        self.line.setSingleStep(0.1)
        self.line.setSuffix(" pt")
        self.font_box = QComboBox()
        for name in chem.available_fonts():
            self.font_box.addItem(name, name)
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(4.0, 24.0)
        self.font_size.setDecimals(1)
        self.font_size.setSingleStep(0.5)
        self.font_size.setSuffix(" pt")
        self.colour = QCheckBox("Coloured atoms (O red, N blue, …) instead of all black")
        self.methyls = QCheckBox("Write terminal methyl groups as CH₃")
        self.stereo = QCheckBox("Show stereo labels: (R)/(S) and (E)/(Z)")
        self.hetero_h = QCheckBox("Show H on heteroatoms (NH, OH, SH)")
        reset = QPushButton("Reset to ACS 1996 style")
        reset.clicked.connect(lambda: self._load(chem.DrawingSettings()))

        font_row = QHBoxLayout()
        font_row.addWidget(self.font_box, 1)
        font_row.addWidget(QLabel("size:"))
        font_row.addWidget(self.font_size)
        form = QFormLayout()
        form.addRow("When compiling, use:", self.compile_choice)
        form.addRow(QLabel("<b>The program's structure drawings</b> (in the SI and on screen)"))
        form.addRow("Bond length:", self.bond)
        form.addRow("Line width:", self.line)
        form.addRow("Atom labels font:", font_row)
        for box in (self.colour, self.methyls, self.stereo, self.hetero_h):
            form.addRow(box)
        reset_row = QHBoxLayout()
        reset_row.addWidget(reset)
        reset_row.addStretch()
        form.addRow(reset_row)

        self.sample = QLabel()
        self.sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sample.setMinimumSize(300, 200)
        self.sample.setStyleSheet("QLabel { background: white; border: 1px solid #999; }")
        self.sample_note = QLabel()
        self.sample_note.setStyleSheet("color: palette(placeholder-text);")
        self.sample_note.setWordWrap(True)
        right = QVBoxLayout()
        right.addWidget(QLabel("Sample:"))
        right.addWidget(self.sample, 1)
        right.addWidget(self.sample_note)
        note = QLabel("ACS 1996 settings: bond length 0.508 cm, line width 0.6 pt, 10 pt "
                      "labels (Arial or Helvetica in ChemDraw; the built-in font looks the "
                      "same and works on every computer). Pasted ChemDraw pictures are not "
                      "affected.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")

        top = QHBoxLayout()
        top.addLayout(form, 3)
        top.addLayout(right, 2)
        layout = QVBoxLayout(self)
        layout.addLayout(top, 1)
        layout.addWidget(note)

        self._load(current)
        for w in (self.bond, self.line, self.font_size):
            w.valueChanged.connect(self.update_sample)
        self.font_box.currentIndexChanged.connect(self.update_sample)
        for box in (self.colour, self.methyls, self.stereo, self.hetero_h):
            box.toggled.connect(self.update_sample)
        self.update_sample()

    def _load(self, d) -> None:
        for w in (self.bond, self.line, self.font_size, self.font_box, self.colour,
                  self.methyls, self.stereo, self.hetero_h):
            w.blockSignals(True)
        self.bond.setValue(d.bond_length_cm)
        self.line.setValue(d.line_width_pt)
        if self.font_box.findData(d.font) < 0:  # chosen on another computer
            self.font_box.addItem(f"{d.font} (not on this computer: built-in font used)", d.font)
        self.font_box.setCurrentIndex(self.font_box.findData(d.font))
        self.font_size.setValue(d.font_size_pt)
        self.colour.setChecked(d.colour_atoms)
        self.methyls.setChecked(d.label_methyls)
        self.stereo.setChecked(d.stereo_labels)
        self.hetero_h.setChecked(d.heteroatom_h)
        for w in (self.bond, self.line, self.font_size, self.font_box, self.colour,
                  self.methyls, self.stereo, self.hetero_h):
            w.blockSignals(False)
        self.update_sample()

    def values(self):
        return self._chem.DrawingSettings(
            bond_length_cm=round(self.bond.value(), 3), line_width_pt=round(self.line.value(), 2),
            font=self.font_box.currentData() or self._chem.DEFAULT_FONT,
            font_size_pt=round(self.font_size.value(), 1), colour_atoms=self.colour.isChecked(),
            label_methyls=self.methyls.isChecked(), stereo_labels=self.stereo.isChecked(),
            heteroatom_h=self.hetero_h.isChecked())

    def update_sample(self) -> None:
        """Draw the sample molecule at its printed size (as it will appear in the SI)."""
        from PySide6.QtCore import QByteArray, QRectF
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer
        chem = self._chem
        svg = chem.structure_svg(chem.parse_structure(SAMPLE_SMILES), settings=self.values())
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        size = renderer.defaultSize()           # in points (1 SVG unit = 1 pt)
        zoom = self.logicalDpiX() / 72          # true size on this screen
        box_w, box_h = max(self.sample.width() - 12, 280), max(self.sample.height() - 12, 180)
        w, h = size.width() * zoom, size.height() * zoom
        shrink = min(1.0, box_w / w, box_h / h) if w and h else 1.0
        w, h = max(1, round(w * shrink)), max(1, round(h * shrink))
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(w * ratio), round(h * ratio))
        pixmap.fill(QColor("white"))
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        self.sample.setPixmap(pixmap)
        self.sample_note.setText("Shown at its printed size." if shrink >= 0.999 else
                                 f"Shrunk to {shrink:.0%} of its printed size to fit here.")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update_sample()

    def apply(self, settings: dict) -> None:
        settings["compile_structures"] = self.compile_choice.currentData()
        values = self.values()
        if values == self._chem.DrawingSettings():
            settings.pop("structure_drawing", None)   # unchanged: follow the ACS defaults
        else:
            settings["structure_drawing"] = values.to_dict()


class PageTab(QWidget):
    """Project settings ▸ Page: header, footer and page numbers (SPEC §13k.2a)."""

    MIN_NUMBER_SIZE = 5.0   # pt; smaller page numbers would be unreadable
    # Body text size of each journal style (styles/*.typ): where "own size" starts.
    TEXT_SIZES = {"ACS": 11.0, "Wiley": 11.0, "RSC": 10.5}

    def __init__(self, settings: dict, context, parent: QWidget | None = None):
        """`context()` gives the title, authors and journal style currently in the dialog."""
        super().__init__(parent)
        from ..core import page
        self._page = page
        self._context = context
        v = page.page_settings(settings)

        grid = QGridLayout()
        for col, text in enumerate(("Left", "Centre", "Right"), start=1):
            label = QLabel(f"<b>{text}</b>")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(label, 0, col)
        self.header: dict[str, QLineEdit] = {}
        self.footer: dict[str, QLineEdit] = {}
        for row, (name, edits, texts) in enumerate((("Header", self.header, v["header"]),
                                                    ("Footer", self.footer, v["footer"])),
                                                   start=1):
            grid.addWidget(QLabel(f"{name}:"), row, 0)
            for col, pos in enumerate(page.POSITIONS, start=1):
                edit = QLineEdit(texts[pos])
                edit.textChanged.connect(self.update_example)
                grid.addWidget(edit, row, col)
                edits[pos] = edit
        for col in (1, 2, 3):
            grid.setColumnStretch(col, 1)
        hint = QLabel("Fill-ins: <b>{page}</b> page number · <b>{pages}</b> number of pages · "
                      "<b>{title}</b> manuscript title · <b>{authors}</b> author names · "
                      "<b>{date}</b> date of compiling.<br>"
                      "Example: <i>Page {page} of {pages}</i> prints “Page S2 of S20”.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        style_footer = QPushButton("Use the journal style's footer")
        style_footer.setToolTip("ACS and RSC: page number centred. Wiley: on the right.")
        style_footer.clicked.connect(self._use_style_footer)

        self.prefix = QLineEdit(v["number_prefix"])
        self.prefix.setMaximumWidth(80)
        self.prefix.setToolTip("Text before every page number, e.g. S for S1, S2… "
                               "(leave empty for 1, 2…)")
        self.number_style = QComboBox()
        for key, text in page.NUMBER_STYLES.items():
            self.number_style.addItem(text, key)
        self.number_style.setCurrentIndex(max(0, self.number_style.findData(v["number_style"])))
        # Number size: "Same as the text" (stored as 0), or a size of its own.
        self.same_size = QCheckBox("Same as the text")
        self.same_size.setChecked(v["number_size"] <= 0)
        self.size = QDoubleSpinBox()
        self.size.setRange(self.MIN_NUMBER_SIZE, 24.0)
        self.size.setDecimals(1)
        self.size.setSingleStep(0.5)
        self.size.setSuffix(" pt")
        self.size.setValue(v["number_size"] if v["number_size"] > 0
                           else self.TEXT_SIZES.get(settings.get("journal_style", "ACS"), 10.0))
        self.size.setEnabled(not self.same_size.isChecked())
        self.same_size.toggled.connect(lambda same: self.size.setEnabled(not same))
        self.bold = QCheckBox("Bold")
        self.bold.setChecked(v["number_bold"])
        self.on_cover = QCheckBox("Show the page number (and header and footer) on the cover page")
        self.on_cover.setChecked(v["number_on_cover"])
        for w in (self.prefix,):
            w.textChanged.connect(self.update_example)
        self.number_style.currentIndexChanged.connect(self.update_example)

        numbers = QHBoxLayout()
        numbers.addWidget(QLabel("Prefix:"))
        numbers.addWidget(self.prefix)
        numbers.addSpacing(12)
        numbers.addWidget(QLabel("Style:"))
        numbers.addWidget(self.number_style)
        numbers.addSpacing(12)
        numbers.addWidget(QLabel("Size:"))
        numbers.addWidget(self.same_size)
        numbers.addWidget(self.size)
        numbers.addSpacing(12)
        numbers.addWidget(self.bold)
        numbers.addStretch()

        self.example = QLabel()
        self.example.setWordWrap(True)
        self.example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.example.setStyleSheet("QLabel { background: palette(base); border: 1px solid #999;"
                                   " padding: 6px; }")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Header and footer</b>"))
        layout.addLayout(grid)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(style_footer)
        row.addStretch()
        layout.addLayout(row)
        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>Page numbers</b> (the size and bold apply to {page} and "
                                "{pages})"))
        layout.addLayout(numbers)
        layout.addWidget(self.on_cover)
        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>Example</b>"))
        layout.addWidget(self.example)
        layout.addStretch()
        self.update_example()

    def values(self) -> dict:
        return {"header": {p: e.text() for p, e in self.header.items()},
                "footer": {p: e.text() for p, e in self.footer.items()},
                "number_prefix": self.prefix.text(),
                "number_style": self.number_style.currentData(),
                "number_size": 0.0 if self.same_size.isChecked() else self.size.value(),
                "number_bold": self.bold.isChecked(),
                "number_on_cover": self.on_cover.isChecked()}

    def update_example(self) -> None:
        self.example.setText(self._page.example(self._context(), self.values()))

    def _set_footer(self, texts: dict[str, str]) -> None:
        for pos, edit in self.footer.items():
            edit.setText(texts.get(pos, ""))

    def _use_style_footer(self) -> None:
        self._set_footer(self._page.style_footer(self._context()["journal_style"]))

    def journal_style_changed(self, old: str, new: str) -> None:
        """A footer still at the old style's default follows the new style."""
        current = {p: e.text() for p, e in self.footer.items()}
        if current == self._page.style_footer(old):
            self._set_footer(self._page.style_footer(new))
        self.update_example()

    def apply(self, settings: dict) -> None:
        self._page.store_page_settings(settings, self.values())


class LabelsTab(QWidget):
    """Project settings ▸ Labels: how figures, tables and schemes are numbered (SPEC
    §13k.5a), with a live example."""

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        from ..core import labels
        self._labels = labels
        self._style = settings.get("journal_style", "ACS")
        rules = labels.label_rules(settings)
        grid = QGridLayout()
        for col, text in enumerate(("Word", "Prefix", "Numbers", "Example"), start=1):
            grid.addWidget(QLabel(f"<b>{text}</b>"), 0, col)
        self.word: dict[str, QLineEdit] = {}
        self.prefix: dict[str, QLineEdit] = {}
        self.style: dict[str, QComboBox] = {}
        self.examples: dict[str, QLabel] = {}
        for row, kind in enumerate(labels.KINDS, start=1):
            grid.addWidget(QLabel(f"{labels.KIND_NAMES[kind]}:"), row, 0)
            word = QLineEdit(rules[kind]["word"])
            word.setToolTip("The word before the number, e.g. Figure, Fig., Table, Scheme "
                            "(leave empty for the number alone)")
            prefix = QLineEdit(rules[kind]["prefix"])
            prefix.setMaximumWidth(70)
            prefix.setToolTip("Text just before the number: S for S1, S2… (leave empty for "
                              "1, 2…)")
            style = QComboBox()
            for key, text in labels.NUMBER_STYLES.items():
                style.addItem(text, key)
            style.setCurrentIndex(max(0, style.findData(rules[kind]["style"])))
            example = QLabel()
            example.setMinimumWidth(220)
            word.textChanged.connect(self.update_examples)
            prefix.textChanged.connect(self.update_examples)
            style.currentIndexChanged.connect(self.update_examples)
            grid.addWidget(word, row, 1)
            grid.addWidget(prefix, row, 2)
            grid.addWidget(style, row, 3)
            grid.addWidget(example, row, 4)
            self.word[kind], self.prefix[kind], self.style[kind] = word, prefix, style
            self.examples[kind] = example
        grid.setColumnStretch(4, 1)
        hint = QLabel(
            "Figures, tables and schemes are numbered in the order they appear in the SI "
            "(General Information first, then the sections in order). Figures are pictures in "
            "custom sections and General Information, and files shown “Inline with compound”.\n\n"
            "To give one item a label of its own (e.g. “Table S2a”), right-click it in the "
            "text (figures and tables), type it on the scheme page, or in the file's Label box. "
            "It keeps its number, so the others are not renumbered. Two items with the same "
            "label are shown in orange and listed in the Warnings panel.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        reset = QPushButton("Reset to the journal style's labels")
        reset.clicked.connect(self.reset)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(hint)
        layout.addStretch()
        self.update_examples()

    def values(self) -> dict[str, dict[str, str]]:
        return {kind: {"word": self.word[kind].text().strip(),
                       "prefix": self.prefix[kind].text().strip(),
                       "style": self.style[kind].currentData()} for kind in self.word}

    def update_examples(self) -> None:
        values = self.values()
        for kind, label in self.examples.items():
            label.setText(self._labels.example(values, kind))

    def reset(self) -> None:
        defaults = self._labels.default_rules(self._style)
        for kind in self.word:
            self.word[kind].setText(defaults[kind]["word"])
            self.prefix[kind].setText(defaults[kind]["prefix"])
            self.style[kind].setCurrentIndex(self.style[kind].findData(defaults[kind]["style"]))

    def journal_style_changed(self, old: str, new: str) -> None:
        """A word still at the old style's default follows the new style (Fig. for RSC)."""
        for kind, edit in self.word.items():
            if edit.text().strip() == self._labels.style_word(kind, old):
                edit.setText(self._labels.style_word(kind, new))
        self._style = new

    def apply(self, settings: dict) -> None:
        self._labels.store_label_rules(settings, self.values())


NUCLEI = {"1h": "1H", "¹h": "1H", "h": "1H", "13c": "13C", "¹³c": "13C", "c": "13C"}


class ImpurityTableEditor(QWidget):
    """View and edit the solvent/impurity shifts used by the impurity-peak check.
    An unedited table is not stored, so the project keeps using the built-in one."""

    HEADERS = ["Use", "Solvent", "Nucleus", "δ (ppm)", "What it is"]

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        from ..core import impurities
        self._impurities = impurities
        intro = QLabel(
            "A ¹H peak reported as a <b>singlet</b>, or any ¹³C peak, at one of these shifts "
            "(in the same solvent) gets a “may be a solvent or impurity” warning. "
            "Built-in values are from Fulmer <i>et al.</i>, <i>Organometallics</i> 2010, 29, 2176. "
            "Untick a row to stop checking it; edit, add or remove rows freely. "
            "Changes apply to this project only.")
        intro.setWordWrap(True)
        self.filter = QComboBox()
        self.filter.currentIndexChanged.connect(self._apply_filter)
        self.tol_h = QDoubleSpinBox()
        self.tol_c = QDoubleSpinBox()
        tol_h, tol_c = impurities.tolerances(settings)
        for box, value in ((self.tol_h, tol_h), (self.tol_c, tol_c)):
            box.setDecimals(3)
            box.setRange(0.0, 1.0)
            box.setSingleStep(0.005)
            box.setSuffix(" ppm")
            box.setValue(value)
        self.tol_h.setToolTip(f"Default {impurities.PROTON_TOLERANCE:.3f} ppm")
        self.tol_c.setToolTip(f"Default {impurities.CARBON_TOLERANCE:.3f} ppm")
        reset = QPushButton("Reset to built-in table")
        reset.clicked.connect(self._reset)
        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.filter)
        top.addSpacing(16)
        top.addWidget(QLabel("Match within ¹H:"))
        top.addWidget(self.tol_h)
        top.addWidget(QLabel("¹³C:"))
        top.addWidget(self.tol_c)
        top.addStretch()
        top.addWidget(reset)

        self.table = EditableTable(self.HEADERS, checkbox_columns={0})
        header = self.table.table.horizontalHeader()
        for col in (0, 1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.table.itemChanged.connect(lambda *_: self._refresh_filter_choices())
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        note = QLabel("Rows without a valid number in δ are ignored. Solvents are recognised "
                      "however the NMR report writes them (e.g. CD3OD, MeOD or methanol-d4; "
                      "DMSO-d6 or (CD3)2SO; CDCl3 or chloroform-d) and matched to the rows "
                      "for that solvent (CDCl3, DMSO-d6, CD3OD, acetone-d6, C6D6, CD2Cl2, "
                      "CD3CN, D2O, THF-d8, toluene-d8, pyridine-d5).")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)
        self._load(impurities.project_table(settings))

    def _load(self, rows: list[dict]) -> None:
        t = self.table.table
        t.blockSignals(True)
        t.setRowCount(0)
        for r in rows:
            self.table.add_row([r.get("on", True), r.get("solvent", ""), r.get("nucleus", ""),
                                f"{float(r['shift']):g}" if _is_number(r.get("shift")) else
                                str(r.get("shift", "")), r.get("what", "")])
        t.blockSignals(False)
        self._refresh_filter_choices()

    def _refresh_filter_choices(self) -> None:
        current = self.filter.currentData()
        solvents = sorted({row[1] for row in self.table.rows() if row[1]}, key=str.lower)
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("All solvents", "")
        for s in solvents:
            self.filter.addItem(s, s)
        i = self.filter.findData(current)
        self.filter.setCurrentIndex(max(i, 0))
        self.filter.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self) -> None:
        wanted = self.filter.currentData() or ""
        t = self.table.table
        for row in range(t.rowCount()):
            item = t.item(row, 1)
            text = item.text().strip() if item is not None else ""
            # New rows (no solvent yet) stay visible so you can fill them in.
            t.setRowHidden(row, bool(wanted) and bool(text) and text != wanted)

    def _reset(self) -> None:
        if QMessageBox.question(self, "Reset impurity table",
                                "Replace this table with the built-in one? Your edits to it "
                                "will be lost.") == QMessageBox.StandardButton.Yes:
            self._load(self._impurities.default_table())
            self.tol_h.setValue(self._impurities.PROTON_TOLERANCE)
            self.tol_c.setValue(self._impurities.CARBON_TOLERANCE)

    def table_rows(self) -> list[dict]:
        out = []
        for on, solvent, nucleus, shift, what in self.table.rows():
            if not _is_number(shift):
                continue
            out.append({"solvent": self._impurities.normalise_solvent(solvent),
                        "nucleus": NUCLEI.get(nucleus.lower().replace(" ", ""), nucleus),
                        "shift": float(shift.replace("−", "-")), "what": what, "on": bool(on)})
        return out

    def apply(self, settings: dict) -> None:
        rows = self.table_rows()
        if rows == self._impurities.default_table():
            settings.pop("impurity_table", None)   # unedited: keep following the built-in table
        else:
            settings["impurity_table"] = rows
        # (Values equal to the defaults are not stored: the project follows the defaults.)
        self._impurities.set_tolerances(settings, self.tol_h.value(), self.tol_c.value())


def _is_number(value) -> bool:
    try:
        float(str(value).replace("−", "-"))
        return True
    except ValueError:
        return False


class TrashDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Trash")
        self.resize(820, 380)
        self.project = project
        self.restored_any = False

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Item", "Kind", "Deleted", "By"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):   # always show these in full; the item name takes the rest
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        self.restore_btn = QPushButton("Restore")
        self.restore_btn.clicked.connect(self._restore)
        self.empty_btn = QPushButton("Empty Trash…")
        self.empty_btn.clicked.connect(self._empty)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        for b in (self.restore_btn, self.empty_btn):
            b.setEnabled(not project.read_only)

        buttons = QHBoxLayout()
        buttons.addWidget(self.restore_btn)
        buttons.addWidget(self.empty_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Deleted sections and removed files are kept here until the "
                                "Trash is emptied."))
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self._fill()

    def _fill(self) -> None:
        self.entries = self.project.list_trash()
        self.table.setRowCount(len(self.entries))
        for row, e in enumerate(self.entries):
            subs = int(e.info.get("subsections", 0) or 0)
            kind = {
                "section": "Section" + (f" (with {subs} sub-section{'s' if subs > 1 else ''})"
                                        if subs else ""),
                "file": f"File (from “{e.info.get('section_name', '?')}”)",
                "compound": f"Compound (from “{e.info.get('section_name', '?')}”)",
                "compound_file": f"File (from compound “{e.info.get('compound_name') or 'unnamed'}”)",
                "scheme": f"Scheme page (from “{e.info.get('section_name', '?')}”)",
                "scheme_file": f"Scheme picture (from “{e.info.get('scheme_name', '?')}”)",
                "gp": f"General procedure (from “{e.info.get('section_name', '?')}”)",
                "incomplete": "Unfinished deletion (no description)",
            }.get(e.kind, e.kind)
            for col, text in enumerate([e.name, kind, e.deleted_at.replace("T", " "), e.deleted_by]):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)   # (the full text, if a column is ever too narrow)
                self.table.setItem(row, col, cell)

    def _restore(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        entry = self.entries[row]
        try:
            with self.project.undo_step(f"Restore “{entry.name}” from the Trash"):
                message = self.project.restore_from_trash(entry.id)
            self.restored_any = True
            QMessageBox.information(self, "Restored", message)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not restore", str(e))
        self._fill()

    def _empty(self) -> None:
        if not self.entries:
            return
        if QMessageBox.warning(
                self, "Empty Trash",
                f"Permanently delete the {len(self.entries)} item(s) in the Trash?\n"
                + ("Until the project is closed, Edit ▸ Undo can still bring them back."
                   if self.project.journal is not None else "This cannot be undone."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            try:
                with self.project.undo_step("Empty the Trash"):
                    self.project.empty_trash()
            except (ProjectError, OSError) as e:
                QMessageBox.warning(self, "Could not empty the Trash", str(e))
            self._fill()


def ask_about_lock(parent: QWidget, lock: LockInfo, same_person: bool) -> str:
    """Returns "read_only", "take_over" or "cancel"."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Project is in use")
    text = f"{lock.describe()}."
    if same_person:
        text += ("\n\nThis looks like your own earlier session — perhaps the program closed "
                 "unexpectedly. If so, it is safe to take over.")
    box.setText(text)
    box.setInformativeText(
        "Only one person can edit a project at a time.\n\n"
        "• Open read-only: look without changing anything.\n"
        "• Take over: edit it yourself. Only do this if you are sure the other person "
        "is not working on it, otherwise their changes may be lost.")
    ro = box.addButton("Open read-only", QMessageBox.ButtonRole.AcceptRole)
    take = box.addButton("Take over", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(ro)
    box.exec()
    clicked = box.clickedButton()
    if clicked is ro:
        return "read_only"
    if clicked is take:
        return "take_over"
    return "cancel"


class FillPatternDialog(QDialog):
    """"Fill from pattern" for a Data section's ID table (SPEC §6.3.1)."""

    def __init__(self, count: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Fill ID table from pattern")
        self.prefix = QLineEdit("3")
        self.prefix.setToolTip("Text before the changing part, e.g. 3 for 3a, 3b, 3c…")
        self.style_box = QComboBox()
        self.style_box.addItem("Letters: a … z, aa, ab …", LETTERS)
        self.style_box.addItem("Numbers: 1, 2, 3 …", NUMBERS)
        self.start = QLineEdit("a")
        self.count = QSpinBox()
        self.count.setRange(1, 2000)
        self.count.setValue(max(count, 1))
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.style_box.currentIndexChanged.connect(self._style_changed)
        for w in (self.prefix, self.start):
            w.textChanged.connect(self._update_preview)
        self.count.valueChanged.connect(self._update_preview)

        form = QFormLayout()
        form.addRow("Prefix:", self.prefix)
        form.addRow("Sequence:", self.style_box)
        form.addRow("Start at:", self.start)
        form.addRow("Number of rows:", self.count)
        form.addRow("Preview:", self.preview)
        form.addRow(QLabel("This replaces the whole table. You can then edit any cell by hand "
                           "(e.g. to skip a letter)."))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self._update_preview()

    def _style_changed(self) -> None:
        self.start.setText("a" if self.style_box.currentData() == LETTERS else "1")

    def ids(self) -> list[str]:
        return pattern_ids(self.prefix.text().strip(), self.style_box.currentData(),
                           self.start.text().strip(), self.count.value())

    def _update_preview(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok) if hasattr(self, "buttons") else None
        try:
            ids = self.ids()
            shown = ", ".join(ids[:5]) + (f", … {ids[-1]}" if len(ids) > 5 else "")
            self.preview.setText(shown)
            valid = True
        except ValueError as e:
            self.preview.setText(f"⚠ {e}")
            valid = False
        if ok is not None:
            ok.setEnabled(valid)


class CustomFieldDialog(QDialog):
    """Define a custom field: a label and a type (SPEC §7.2)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Custom field")
        self.label = QLineEdit()
        self.label.setPlaceholderText("e.g. Catalyst batch")
        self.type_box = QComboBox()
        for key, text in F.CUSTOM_TYPES.items():
            self.type_box.addItem(text, key)
        form = QFormLayout()
        form.addRow("Label:", self.label)
        form.addRow("Type:", self.type_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if self.label.text().strip():
            self.accept()

    def entry(self) -> dict:
        return {"key": new_id("custom"),
                "custom": {"label": self.label.text().strip(), "type": self.type_box.currentData()}}