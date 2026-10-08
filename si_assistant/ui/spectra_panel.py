"""The Spectra section's page (SPEC §13k.12): the layout defaults for every spectrum, a
small sketch of a page set out that way, "Apply defaults to all spectra", and the links
in the PDF between the peak reports and the spectra (SPEC §13l.7)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from ..core import spectra as S
from ..core.project import Project, ProjectError, Section
from .panels import SectionPanel

SAVE_DELAY_MS = 300
TEXT_SIZES = {"ACS": 11.0, "Wiley": 11.0, "RSC": 10.5}   # the styles' ID/header size


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


class ColourButton(QPushButton):
    """A button showing a colour; clicking it opens the colour chooser."""

    changed = Signal(str)

    def __init__(self, colour: str, title: str, parent: QWidget | None = None,
                 compact: bool = False):
        super().__init__(parent)
        self.title = title
        self.compact = compact   # just the colour (its code is in the tooltip)
        self._colour = colour
        if compact:
            self.setFixedWidth(self.fontMetrics().horizontalAdvance("MMM") + 8)
        self.clicked.connect(self._choose)
        self.set_colour(colour)

    def colour(self) -> str:
        return self._colour

    def set_colour(self, colour: str) -> None:
        self._colour = colour.lower()
        text = "#000000" if QColor(colour).lightness() > 140 else "#ffffff"
        self.setText("" if self.compact else self._colour.upper())
        self.setStyleSheet(f"QPushButton {{ background: {colour}; color: {text}; "
                           "border: 1px solid palette(mid); padding: 2px 6px; }")
        self.setToolTip(f"{self.title}: {self._colour.upper()} (click to choose)")

    def _choose(self) -> None:
        colour = QColorDialog.getColor(QColor(self._colour), self, self.title)
        if colour.isValid():
            self.set_colour(colour.name())
            self.changed.emit(self._colour)


class SpectraSample(QWidget):
    """A sketch of one page of spectra set out as the defaults say (how many on a page,
    orientation, header lines, frames, the structure + ID box). The live preview on the
    right shows the real thing."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.values = dict(S.DEFAULTS)
        self.setFixedSize(150, 170)
        self.setToolTip("A sketch of a page of spectra with these defaults (not to scale). "
                        "The live preview shows the real page.")

    def set_values(self, values: dict) -> None:
        self.values = dict(values)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        v = self.values
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        landscape = v["orientation"] == "landscape"
        ratio = 11 / 8.5 if not landscape else 8.5 / 11   # page height / width
        avail = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        w = min(avail.width(), avail.height() / ratio)
        page = QRectF(0, 0, w, w * ratio)
        page.moveCenter(avail.center())
        p.setPen(QPen(QColor("#999999"), 1))
        p.setBrush(QColor("white"))
        p.drawRect(page)
        inner = page.adjusted(w * 0.1, w * 0.1, -w * 0.1, -w * 0.12)
        n = v["per_page"]
        cols, rows = (2, 2) if n == 4 else ((2, 1) if landscape else (1, 2)) if n == 2 else (1, 1)
        gap = w * 0.03
        sw = (inner.width() - gap * (cols - 1)) / cols
        sh = (inner.height() - gap * (rows - 1)) / rows
        for r in range(rows):
            for c in range(cols):
                self._slot(p, QRectF(inner.left() + c * (sw + gap), inner.top() + r * (sh + gap),
                                     sw, sh), w)
        p.end()

    def _slot(self, p: QPainter, slot: QRectF, page_w: float) -> None:
        v = self.values
        top = slot.top()
        if v["header_line"]:
            bar = QRectF(0, 0, slot.width() * 0.5, max(2.0, page_w * 0.012))
            bar.moveCenter(QPointF(slot.center().x(), top + bar.height()))
            p.fillRect(bar, QColor("#444444"))
            top += bar.height() * 3
        # A typical spectrum is wider than tall; auto-crop cuts its white margins, so it
        # is shown larger and fills more of its space.
        shape = 0.5 if v["auto_crop"] else 0.773
        room = QRectF(slot.left(), top, slot.width(), slot.bottom() - top)
        sw = min(room.width(), room.height() / shape)
        spec = QRectF(0, 0, sw, sw * shape)
        spec.moveTopLeft(QPointF(room.center().x() - sw / 2, room.top()))
        margin = 0.0 if v["auto_crop"] else 0.12
        plot = spec.adjusted(sw * margin, spec.height() * margin, -sw * margin,
                             -spec.height() * margin)
        p.setPen(QPen(QColor("#1f4e9a"), 1))
        base = plot.bottom() - plot.height() * 0.08
        p.drawLine(QPointF(plot.left(), base), QPointF(plot.right(), base))
        for i, height in enumerate((0.3, 0.8, 0.45, 0.6, 0.25, 0.9, 0.35, 0.5, 0.2, 0.7)):
            x = plot.left() + plot.width() * (0.08 + i * 0.09)
            p.drawLine(QPointF(x, base), QPointF(x, base - plot.height() * 0.85 * height))
        if v["spectrum_frame"]:
            p.setPen(QPen(QColor(v["spectrum_frame_color"]), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(spec)
        if v.get("struct_box", True):
            self._label(p, spec, page_w)

    def _label(self, p: QPainter, spec: QRectF, page_w: float) -> None:
        v = self.values
        scale = v["struct_scale"]
        bw = page_w * 1.5 / 8.5 * scale
        bh = bw * 0.95 + (bw * 0.12 if v["caption"] else 0)
        bw, bh = min(bw, spec.width() * 0.9), min(bh, spec.height() * 0.9)
        inset = page_w * 0.4 / 21.6
        x = spec.left() + inset if v["corner"].endswith("left") else spec.right() - inset - bw
        y = spec.top() + inset if v["corner"].startswith("top") else spec.bottom() - inset - bh
        box = QRectF(x, y, bw, bh)
        p.setBrush(QColor(v["struct_bg"]))
        p.setPen(QPen(QColor(v["struct_frame_color"]), 1) if v["struct_frame"]
                 else Qt.PenStyle.NoPen)
        p.drawRect(box)
        # A benzene ring for the structure.
        p.setPen(QPen(QColor("black"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        centre = QPointF(box.center().x(), box.top() + box.height() * 0.35)
        radius = min(bw, bh) * 0.25
        points = [QPointF(centre.x() + radius * math.cos(math.radians(30 + 60 * k)),
                          centre.y() + radius * math.sin(math.radians(30 + 60 * k)))
                  for k in range(7)]
        for a, b in zip(points, points[1:]):
            p.drawLine(a, b)
        font = QFont(self.font())
        size = v["id_size"] or TEXT_SIZES.get("ACS")
        font.setPixelSize(max(6, round(page_w * size / 612 * 1.1)))
        font.setBold(v["id_bold"])
        p.setFont(font)
        id_box = QRectF(box.left(), centre.y() + radius * 1.2, box.width(), font.pixelSize() * 1.3)
        p.drawText(id_box, Qt.AlignmentFlag.AlignCenter, "3a")
        if v["caption"]:
            line = QRectF(0, 0, box.width() * 0.7, max(1.5, font.pixelSize() * 0.25))
            line.moveCenter(QPointF(box.center().x(), id_box.bottom() + line.height() * 2))
            p.fillRect(line, QColor("#666666"))


class SpectraSectionPanel(SectionPanel):
    """The Spectra section: where every compound's spectra are printed. Its defaults are
    saved straight away (in section.json, "defaults")."""

    def __init__(self, project: Project, section: Section, parent: QWidget | None = None):
        super().__init__(project, section, parent)
        ro = project.read_only
        v = project.spectra_defaults()
        self._loading = True

        self.layout_.addWidget(_hint(
            "Every compound's spectra are printed here, compound by compound in tree order "
            "(¹H, ¹³C, ¹⁹F, other nuclei, 2D, then HPLC/SFC, IR and other files, unless the "
            "order was changed on the compound's spectra page): the files "
            "in each compound's Spectra field, and Additional files set to “Spectra section”. "
            "Each page of a PDF is a spectrum of its own. These are the defaults for every "
            "spectrum; each compound with spectra has a page of its own under this section in "
            "the tree, where its structures can be dragged and single spectra changed. This "
            "section can be moved anywhere in the tree, but not deleted; to leave the spectra "
            "out of the SI, untick it in the Compile SI window."))

        # ---- the page
        self.per_page = QComboBox()
        for n, text in ((1, "1 (a whole page each)"), (2, "2"), (4, "4 (2 × 2)")):
            self.per_page.addItem(text, n)
        self.per_page.setCurrentIndex(max(0, self.per_page.findData(v["per_page"])))
        self.per_page.setToolTip("Org. Lett. asks for each spectrum to be at least half a page.")
        self.orientation = QComboBox()
        for key, text in S.ORIENTATIONS.items():
            self.orientation.addItem(text, key)
        self.orientation.setCurrentIndex(max(0, self.orientation.findData(v["orientation"])))
        self.header_line = QCheckBox("Header line above each spectrum")
        self.header_line.setToolTip("e.g. “2 — ¹H NMR (400 MHz, CDCl₃)”, or the header typed "
                                    "for the file on the compound's page")
        self.header_line.setChecked(v["header_line"])
        self.spectrum_frame = QCheckBox("Frame")
        self.spectrum_frame.setChecked(v["spectrum_frame"])
        self.spectrum_colour = ColourButton(v["spectrum_frame_color"], "Colour of the frame "
                                            "around each spectrum")
        self.spectrum_width = self._width_box(v["spectrum_frame_width"])
        self.auto_crop = QCheckBox("Cut off white margins (auto-crop)")
        self.auto_crop.setChecked(v["auto_crop"])
        self.auto_crop.setToolTip("Only blank white is cut, so the axes, scale and labels "
                                  "stay. Each spectrum is measured once and remembered.")

        page_box = QGroupBox("Pages")
        pform = QFormLayout(page_box)
        pform.addRow("Spectra per page:", self.per_page)
        pform.addRow("Orientation:", self.orientation)
        pform.addRow(self.header_line)
        pform.addRow("Spectrum frame:",
                     self._row(self.spectrum_frame, self.spectrum_colour, self.spectrum_width))
        pform.addRow(self.auto_crop)
        pform.addRow(_hint("Only blank white is cut, so axes, scale and labels stay; each "
                           "spectrum is then enlarged to fill its space."))

        # ---- the structure + ID box
        self.struct_box = QCheckBox("Show the structure and ID on each spectrum")
        self.struct_box.setChecked(v["struct_box"])
        self.struct_box.setToolTip("Untick to print the spectra without them. Single spectra "
                                   "can still show or hide them on their compound's spectra "
                                   "page.")
        self.corner = QComboBox()
        for key, text in S.CORNERS.items():
            self.corner.addItem(text, key)
        self.corner.setCurrentIndex(max(0, self.corner.findData(v["corner"])))
        self.corner.setToolTip("Where the structure and ID go on each spectrum (inside the "
                               "plot, 0.4 cm from its edges). On a compound's spectra page "
                               "they can be dragged anywhere.")
        self.scale = QSpinBox()
        self.scale.setRange(round(S.SCALE_RANGE[0] * 100), round(S.SCALE_RANGE[1] * 100))
        self.scale.setSingleStep(10)
        self.scale.setSuffix(" %")
        self.scale.setValue(round(v["struct_scale"] * 100))
        self.scale.setToolTip("100 % = the structure's normal size (made smaller if needed to fit "
                              "1.5 × 1.1 inches); 200 % prints it twice as large, 50 % half.")
        style = project.settings.get("journal_style", "ACS")
        self.same_size = QCheckBox("Same as the text")
        self.same_size.setChecked(v["id_size"] <= 0)
        self.id_size = QDoubleSpinBox()
        self.id_size.setRange(*S.ID_SIZE_RANGE)
        self.id_size.setDecimals(1)
        self.id_size.setSingleStep(0.5)
        self.id_size.setSuffix(" pt")
        self.id_size.setValue(v["id_size"] if v["id_size"] > 0 else TEXT_SIZES.get(style, 11.0))
        self.id_bold = QCheckBox("Bold")
        self.id_bold.setChecked(v["id_bold"])
        self.struct_frame = QCheckBox("Frame")
        self.struct_frame.setChecked(v["struct_frame"])
        self.struct_colour = ColourButton(v["struct_frame_color"], "Colour of the frame around "
                                          "the structure and ID")
        self.struct_width = self._width_box(v["struct_frame_width"])
        self.background = ColourButton(v["struct_bg"], "Background behind the structure and ID")
        self.caption = QCheckBox("Caption under them")
        self.caption.setToolTip("e.g. “¹H NMR (CDCl₃, 500 MHz)”: nucleus, solvent and "
                                "frequency from the compound's peak report (NMR spectra), or the "
                                "caption typed for the file on the compound's page")
        self.caption.setChecked(v["caption"])
        self.caption_size = QDoubleSpinBox()
        self.caption_size.setRange(*S.CAPTION_SIZE_RANGE)
        self.caption_size.setDecimals(1)
        self.caption_size.setSingleStep(0.5)
        self.caption_size.setSuffix(" pt")
        self.caption_size.setToolTip("The caption's text size (9 pt unless changed; each "
                                     "spectrum can have its own on its compound's spectra page)")
        self.caption_size.setValue(v["caption_size"])

        label_box = QGroupBox("Structure and compound ID on each spectrum")
        lform = QFormLayout(label_box)
        lform.addRow(self.struct_box)
        lform.addRow("Position:", self.corner)
        lform.addRow("Structure size:", self.scale)
        lform.addRow("ID text:", self._row(self.same_size, self.id_size, self.id_bold))
        lform.addRow("Frame:", self._row(self.struct_frame, self.struct_colour, self.struct_width))
        lform.addRow("Background:", self._row(self.background))
        lform.addRow(self._row(self.caption, QLabel("Size:"), self.caption_size))
        lform.addRow(_hint("NMR spectra get a caption such as “¹H NMR (CDCl₃, 500 MHz)” from "
                           "the peak report. To change it, or to give an HPLC trace a caption, "
                           "type it on the compound's spectra page (or in “Caption under the "
                           "structure” in its Spectra field). Every kind of spectrum gets the "
                           "structure and ID, HPLC/SFC traces and IR spectra too; to leave them "
                           "off one spectrum, untick “Structure + ID” on its compound's "
                           "spectra page."))

        # ---- links in the PDF (SPEC §13l.7)
        self.nmr_links = QCheckBox("Link each NMR report to its spectrum (in the PDF)")
        self.nmr_links.setChecked(v["nmr_links"])
        self.nmr_links.setToolTip("Clicking “¹H NMR” at the start of a compound's peak report "
                                  "in the PDF jumps to its ¹H spectrum (the same for ¹³C, ¹⁹F "
                                  "and other nuclei)")
        self.link_style = QComboBox()
        for key, text in S.LINK_STYLES.items():
            self.link_style.addItem(text, key)
        self.link_style.setCurrentIndex(max(0, self.link_style.findData(v["link_style"])))
        self.link_style.setToolTip("How the links look in the PDF. “Like normal text” prints "
                                   "them exactly as before; they can still be clicked.")
        self.link_back = QCheckBox("Link each spectrum's header back to the compound's data")
        self.link_back.setChecked(v["link_back"])
        self.link_back.setToolTip("Clicking the header line above a spectrum (e.g. “2 — ¹H NMR "
                                  "(400 MHz, CDCl₃)”) jumps back to the compound's data. It "
                                  "needs the header line (Pages, above).")
        links_box = QGroupBox("Links in the PDF")
        kform = QFormLayout(links_box)
        kform.addRow(self.nmr_links)
        kform.addRow("Link appearance:", self.link_style)
        kform.addRow(self.link_back)
        kform.addRow(_hint("The “¹H NMR”, “¹³C NMR”… at the start of each peak report becomes a "
                           "link to the compound's spectrum of that nucleus (the first one, if "
                           "it has several). There is no link when the compound has no such "
                           "spectrum, or when the Spectra section is not printed. Links work in "
                           "PDF readers; on paper they look as chosen above."))

        # ---- HRMS report files (SPEC §13n.3)
        self.hrms_reports = QCheckBox("Print HRMS reports")
        self.hrms_reports.setChecked(v["hrms_reports"])
        self.hrms_reports.setToolTip("Print the report files kept in each compound's HRMS "
                                     "field, in a sub-section “HRMS reports” after all the "
                                     "spectra")
        hrms_box = QGroupBox("HRMS reports")
        hform = QFormLayout(hrms_box)
        hform.addRow(self.hrms_reports)
        hform.addRow(_hint("Off by default. When on, the report files in each compound's HRMS "
                           "field are printed in a sub-section “HRMS reports” at the end of "
                           "this section, compound by compound: PDFs page by page, pictures "
                           "as they are, text and table files as a listing, data files "
                           "(mzML…) as a table of their largest peaks. Raw data is never "
                           "printed. Each compound can say otherwise in its HRMS field or "
                           "on its spectra page."))

        # ---- the sketch, and resetting the spectra
        self.sample = SpectraSample()
        self.apply_btn = QPushButton("Apply defaults to all spectra…")
        self.apply_btn.setToolTip("Remove the settings changed for single spectra (and the "
                                  "positions their structures were dragged to), so every "
                                  "spectrum follows these defaults.")
        self.apply_btn.clicked.connect(self.apply_to_all)
        self.overridden = _hint("")

        for form in (pform, lform, kform, hform):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        # The sketch sits beside the page settings (one column, so narrow windows work).
        sketch = QVBoxLayout()
        sketch.addWidget(self.sample, 0, Qt.AlignmentFlag.AlignHCenter)
        sketch.addWidget(_hint("A sketch of one page\n(not to scale)"), 0,
                         Qt.AlignmentFlag.AlignHCenter)
        sketch.addStretch()
        top = QHBoxLayout()
        top.addWidget(page_box, 1)
        top.addLayout(sketch)
        column = QVBoxLayout()
        column.addLayout(top)
        column.addWidget(label_box)
        column.addWidget(links_box)
        column.addWidget(hrms_box)
        apply_row = QHBoxLayout()
        apply_row.addWidget(self.apply_btn)
        apply_row.addWidget(self.overridden, 1)
        column.addLayout(apply_row)
        column.addStretch()
        holder = QWidget()
        holder.setLayout(column)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # A narrow window (e.g. with the live preview open) scrolls sideways rather than
        # hiding the controls on the right.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(holder)
        self.layout_.addWidget(scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self._save)
        for box in (self.per_page, self.orientation, self.corner, self.link_style):
            box.currentIndexChanged.connect(self._changed)
        for check in (self.header_line, self.spectrum_frame, self.auto_crop, self.same_size,
                      self.id_bold, self.struct_frame, self.caption, self.struct_box,
                      self.nmr_links, self.link_back, self.hrms_reports):
            check.toggled.connect(self._changed)
        for spin in (self.scale, self.id_size, self.spectrum_width, self.struct_width,
                     self.caption_size):
            spin.valueChanged.connect(self._changed)
        for button in (self.spectrum_colour, self.struct_colour, self.background):
            button.changed.connect(self._changed)
        for w in (self.per_page, self.orientation, self.header_line, self.spectrum_frame,
                  self.struct_box,
                  self.spectrum_colour, self.spectrum_width, self.auto_crop, self.corner,
                  self.scale, self.same_size, self.id_size, self.id_bold, self.struct_frame,
                  self.struct_colour, self.struct_width, self.background, self.caption,
                  self.caption_size, self.apply_btn, self.nmr_links, self.link_style, self.link_back,
                  self.hrms_reports):
            w.setEnabled(not ro)
        self._loading = False
        self._update_enabled()
        self.sample.set_values(self.values())
        self._show_overridden()

    @staticmethod
    def _width_box(value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(*S.FRAME_WIDTH_RANGE)
        box.setDecimals(2)
        box.setSingleStep(0.25)
        box.setSuffix(" pt")
        box.setValue(value)
        box.setToolTip("Line width")
        return box

    @staticmethod
    def _row(*widgets) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for w in widgets:
            row.addWidget(w)
        row.addStretch()
        holder = QWidget()
        holder.setLayout(row)
        return holder

    def values(self) -> dict:
        """The defaults as set on the page."""
        return {
            "per_page": self.per_page.currentData(),
            "orientation": self.orientation.currentData(),
            "header_line": self.header_line.isChecked(),
            "struct_box": self.struct_box.isChecked(),
            "corner": self.corner.currentData(),
            "struct_frame": self.struct_frame.isChecked(),
            "struct_frame_color": self.struct_colour.colour(),
            "struct_frame_width": round(self.struct_width.value(), 2),
            "struct_bg": self.background.colour(),
            "id_size": 0.0 if self.same_size.isChecked() else round(self.id_size.value(), 1),
            "id_bold": self.id_bold.isChecked(),
            "struct_scale": round(self.scale.value() / 100, 2),
            "spectrum_frame": self.spectrum_frame.isChecked(),
            "spectrum_frame_color": self.spectrum_colour.colour(),
            "spectrum_frame_width": round(self.spectrum_width.value(), 2),
            "auto_crop": self.auto_crop.isChecked(),
            "caption": self.caption.isChecked(),
            "caption_size": round(self.caption_size.value(), 1),
            "nmr_links": self.nmr_links.isChecked(),
            "link_style": self.link_style.currentData(),
            "link_back": self.link_back.isChecked(),
            "hrms_reports": self.hrms_reports.isChecked(),
        }

    def _update_enabled(self) -> None:
        ro = self.project.read_only
        linked = self.nmr_links.isChecked()
        for w in (self.link_style, self.link_back):   # (only matter with links)
            w.setEnabled(not ro and linked)
        shown = self.struct_box.isChecked()
        for w in (self.corner, self.scale, self.same_size, self.id_bold, self.struct_frame,
                  self.background, self.caption):
            w.setEnabled(not ro and shown)
        self.id_size.setEnabled(not ro and shown and not self.same_size.isChecked())
        self.caption_size.setEnabled(not ro and shown and self.caption.isChecked())
        for w in (self.spectrum_colour, self.spectrum_width):
            w.setEnabled(not ro and self.spectrum_frame.isChecked())
        for w in (self.struct_colour, self.struct_width):
            w.setEnabled(not ro and shown and self.struct_frame.isChecked())

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._update_enabled()
        self.sample.set_values(self.values())
        if not self.project.read_only:
            self._timer.start()

    def _save(self) -> None:
        self._timer.stop()
        if self.project.read_only:
            return
        values = self.values()
        if values == self.project.spectra_defaults():
            return
        try:
            self.project.set_spectra_defaults(values)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self.saved.emit("Saved")

    def flush(self) -> None:
        super().flush()
        if self._timer.isActive():
            self._save()

    def _show_overridden(self) -> None:
        n = S.count_overridden(self.project)
        self.overridden.setText(
            "Every spectrum follows these defaults." if not n else
            f"{n} spectrum file(s) have settings of their own.")

    def apply_to_all(self) -> None:
        """Every spectrum back to the defaults, after asking."""
        if self.project.read_only:
            return
        self.flush()
        n = S.count_overridden(self.project)
        if not n:
            QMessageBox.information(self, "Apply defaults to all spectra",
                                    "Every spectrum already follows these defaults.")
            return
        if QMessageBox.question(
                self, "Apply defaults to all spectra",
                f"{n} spectrum file(s) have settings of their own (for example a different "
                "number per page, or a structure dragged to another place).\n\nRemove them, "
                "so every spectrum follows these defaults? The structures' dragged positions "
                "are reset too.") != QMessageBox.StandardButton.Yes:
            return
        try:
            changed = S.apply_defaults_to_all(self.project)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self._show_overridden()
        self.saved.emit(f"{changed} spectrum file(s) now follow the defaults")
