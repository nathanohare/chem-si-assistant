"""The X-ray structure viewer (SPEC §13v): a CIF's structure drawn as ORTEP ellipsoids or
ball-and-stick, turned with the mouse, with its crystal data table; the drawing saved as
a picture (SVG, PNG, PDF) or copied (e.g. into PowerPoint).

`XrayCanvas` draws core/xray_draw.py's shapes with Qt — the same shapes the SVG is made
of, so what is seen is what is saved. Dragging turns the structure (with Shift, or the
right button: turns it in the page's plane); the wheel zooms; dragging a label moves it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QMimeData, QPointF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QImage, QPainter,
                           QPainterPath, QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..core import cif, crystal, xray
from ..core import xray_draw as X

PX_PER_PT = 96 / 72                 # the drawing's points on the screen at zoom 1
EXPORT_DPI = 600


def paint_shapes(painter: QPainter, items: list, font_family: str = "Arial") -> None:
    """Draw the shapes (core/xray_draw.py) with Qt, in the painter's coordinates (pt)."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    for sh in items:
        pen = QPen(QColor(sh.stroke), sh.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if sh.kind == "ellipse":
            painter.save()
            painter.translate(*sh.centre)
            painter.rotate(sh.angle)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(sh.fill)) if sh.fill != "none" else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), sh.axes[0], sh.axes[1])
            painter.restore()
        elif sh.kind == "ball":
            x, y = sh.centre
            r = sh.axes[0]
            g = QRadialGradient(QPointF(x - 0.3 * r, y - 0.3 * r), 1.3 * r)
            g.setColorAt(0, QColor("white"))
            g.setColorAt(1, QColor(sh.fill))
            painter.setPen(pen)
            painter.setBrush(QBrush(g))
            painter.drawEllipse(QPointF(x, y), r, r)
        elif sh.kind in ("polyline", "polygon"):
            points = QPolygonF([QPointF(x, y) for x, y in sh.points])
            painter.setPen(pen)
            if sh.kind == "polygon":
                painter.setBrush(QBrush(QColor(sh.fill)))
                painter.drawPolygon(points)
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(points)
        elif sh.kind == "text":
            font = QFont(font_family)
            font.setPointSizeF(sh.size)
            path = QPainterPath()
            path.addText(QPointF(*sh.centre), font, sh.text)   # (as outlines: scales exactly)
            halo = QPen(QColor("white"), sh.size * 0.25)        # (legible across a bond)
            halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(halo)
            painter.setBrush(QBrush(QColor("white")))
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("black")))
            painter.drawPath(path)


def picture(items: list, dpi: float = EXPORT_DPI, margin: float = 6.0,
            transparent: bool = False) -> QImage:
    """The drawing as a picture at `dpi` (white background, or transparent)."""
    x0, y0, x1, y1 = X.bounds(items)
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    k = dpi / 72
    image = QImage(max(1, int((x1 - x0) * k)), max(1, int((y1 - y0) * k)),
                   QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0) if transparent else QColor("white"))
    image.setDotsPerMeterX(round(dpi / 0.0254))
    image.setDotsPerMeterY(round(dpi / 0.0254))
    painter = QPainter(image)
    painter.scale(k, k)
    painter.translate(-x0, -y0)
    paint_shapes(painter, items)
    painter.end()
    return image


def pdf_bytes(items: list, margin: float = 6.0) -> bytes:
    """The drawing as a one-page vector PDF of its own size."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter
    x0, y0, x1, y1 = X.bounds(items)
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setResolution(72)
    writer.setPageLayout(QPageLayout(QPageSize(QSizeF(x1 - x0, y1 - y0), QPageSize.Unit.Point),
                                     QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
    writer.setCreator("Chem SI Assistant")
    painter = QPainter(writer)
    painter.translate(-x0, -y0)
    paint_shapes(painter, items)
    painter.end()
    buffer.close()
    return bytes(data)


class XrayCanvas(QWidget):
    """The structure, drawn and turned with the mouse (see the module's text)."""

    changed = Signal()                  # the view changed (turned, zoomed, a label moved)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 260)
        self.setMouseTracking(False)
        self.grown: crystal.Grown | None = None
        self.view = X.ViewSettings()
        self._items: list = []
        self._press = None
        self._label: str | None = None
        # How the drawing (pt) is shown: screen pixels per point, and the point at the
        # window's centre. Fitted on opening and for a standard view; the wheel magnifies.
        # (Only the screen: the drawing's own size is view.zoom.)
        self._scale = PX_PER_PT
        self._centre = (0.0, 0.0)
        self.empty_text = "Open a CIF file to see its structure."
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_structure(self, grown: crystal.Grown | None, view: X.ViewSettings) -> None:
        self.grown, self.view = grown, view
        self.redraw()
        self.fit()

    def redraw(self) -> None:
        self._items = X.shapes(self.grown, self.view) if self.grown is not None else []
        self.update()

    def fit(self) -> None:
        """Show the whole drawing, as large as the window allows."""
        if not self._items:
            return
        x0, y0, x1, y1 = X.bounds(self._items)
        w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
        self._scale = max(0.05, min((self.width() - 40) / w, (self.height() - 40) / h))
        self._centre = ((x0 + x1) / 2, (y0 + y1) / 2)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def items(self) -> list:
        return self._items

    def _to_drawing(self, pos) -> tuple[float, float]:
        return (self._centre[0] + (pos.x() - self.width() / 2) / self._scale,
                self._centre[1] + (pos.y() - self.height() / 2) / self._scale)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        if not self._items:
            painter.setPen(QColor("#888888"))
            painter.drawText(self.rect().adjusted(20, 20, -20, -20),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self.empty_text)
            return
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(self._scale, self._scale)
        painter.translate(-self._centre[0], -self._centre[1])
        paint_shapes(painter, self._items)
        painter.end()

    def _label_at(self, pos) -> str | None:
        x, y = self._to_drawing(pos)
        for sh in reversed(self._items):
            if sh.kind != "text":
                continue
            w = 0.62 * sh.size * len(sh.text)
            if sh.centre[0] - 2 <= x <= sh.centre[0] + w + 2 and \
                    sh.centre[1] - sh.size <= y <= sh.centre[1] + 3:
                return sh.key
        return None

    def mousePressEvent(self, event) -> None:
        self._press = event.position()
        self._label = self._label_at(event.position()) if \
            event.button() == Qt.MouseButton.LeftButton else None

    def mouseMoveEvent(self, event) -> None:
        if self._press is None or self.grown is None:
            return
        pos = event.position()
        dx, dy = pos.x() - self._press.x(), pos.y() - self._press.y()
        self._press = pos
        if self._label is not None:
            label = next((sh for sh in self._items if sh.kind == "text" and sh.key == self._label),
                         None)
            if label is not None:
                # the offset is from the atom: the label's place now, minus where the atom is
                current = self.view.label_offsets.get(self._label)
                if current is None:
                    current = self._default_offset(self._label)
                self.view.label_offsets[self._label] = (current[0] + dx / self._scale,
                                                        current[1] + dy / self._scale)
        elif event.buttons() & Qt.MouseButton.RightButton or \
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.view.rotation = X.turn(self.view.rotation, 0, 0, dx * 0.5).tolist()
        else:
            self.view.rotation = X.turn(self.view.rotation, dy * 0.5, dx * 0.5).tolist()
        self.redraw()

    def _default_offset(self, key: str) -> tuple[float, float]:
        """Where a label is from its atom now (before it is first dragged)."""
        moved = X.ViewSettings.from_json(self.view.to_json())
        moved.label_offsets[key] = (0.0, 0.0)
        at_atom = next((sh.centre for sh in X.shapes(self.grown, moved)
                        if sh.kind == "text" and sh.key == key), (0.0, 0.0))
        now = next((sh.centre for sh in self._items if sh.kind == "text" and sh.key == key),
                   at_atom)
        return now[0] - at_atom[0], now[1] - at_atom[1]

    def mouseReleaseEvent(self, _event) -> None:
        if self._press is not None:
            self._press = None
            self._label = None
            self.changed.emit()

    def wheelEvent(self, event) -> None:
        """Magnify the screen's view (not the drawing: its size is the Drawing size)."""
        steps = event.angleDelta().y() / 120
        self._scale = float(min(40.0, max(0.05, self._scale * 1.15 ** steps)))
        self.update()


class XrayEditor(QWidget):
    """The structure and how it is drawn — shared by the viewer window and the X-ray
    structure section's page (ui/xray_panel.py): the canvas (in a "Structure" tab, with
    room under it: `under_canvas`), and beside it the Drawing, View, Shown and Picture
    boxes (the page's own controls go above them: `top`).

    `view_changed` is emitted whenever the user changes the drawing (turned, a box
    ticked, a label moved…), for the page to save it."""

    view_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.structure: crystal.Structure | None = None
        self.grown: crystal.Grown | None = None
        self.picture_name = "structure"       # Save picture…: the file name offered
        self.picture_folder: Path | None = None
        self._base_zoom = 1.0                 # the zoom of "100 %" (about 12 cm wide)

        self.canvas = XrayCanvas()
        self.canvas.changed.connect(self.view_changed)
        structure_page = QWidget()
        sp = QVBoxLayout(structure_page)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.addWidget(self.canvas, 1)
        self.under_canvas = QVBoxLayout()
        sp.addLayout(self.under_canvas)
        self.tabs = QTabWidget()
        self.tabs.addTab(structure_page, "Structure")

        side = QWidget()
        form = QVBoxLayout(side)
        self.top = QVBoxLayout()
        self.top.setContentsMargins(0, 0, 0, 0)
        form.addLayout(self.top)

        style_box = QGroupBox("Drawing")
        sl = QFormLayout(style_box)
        self.ortep = QRadioButton("ORTEP (ellipsoids)")
        self.ball = QRadioButton("Ball-and-stick")
        group = QButtonGroup(self)
        group.addButton(self.ortep)
        group.addButton(self.ball)
        self.ortep.setChecked(True)
        self.ortep.toggled.connect(self._settings_changed)
        sl.addRow(self.ortep)
        sl.addRow(self.ball)
        self.probability = QComboBox()
        for p in (30, 50, 70, 90):
            self.probability.addItem(f"{p}%", p)
        self.probability.setCurrentIndex(1)
        self.probability.currentIndexChanged.connect(self._settings_changed)
        sl.addRow("Ellipsoids at:", self.probability)
        self.hydrogens = QCheckBox("Hydrogen atoms")
        self.colour = QCheckBox("Colour (else black and white)")
        self.principal = QCheckBox("Principal ellipses")
        self.labels = QCheckBox("Atom labels")
        for w in (self.hydrogens, self.colour, self.principal, self.labels):
            w.setChecked(True)
            w.toggled.connect(self._settings_changed)
            sl.addRow(w)
        self.label_size = QDoubleSpinBox()
        self.label_size.setRange(4, 30)
        self.label_size.setValue(9)
        self.label_size.setSuffix(" pt")
        self.label_size.valueChanged.connect(self._settings_changed)
        sl.addRow("Label size:", self.label_size)
        self.size_box = QSpinBox()
        self.size_box.setRange(20, 400)
        self.size_box.setSingleStep(10)
        self.size_box.setValue(100)
        self.size_box.setSuffix(" %")
        self.size_box.setToolTip("How big the structure is drawn, against its labels and "
                                 "lines (100% ≈ 12 cm wide). The wheel only magnifies the "
                                 "view on the screen.")
        self.size_box.valueChanged.connect(self._size_changed)
        sl.addRow("Drawing size:", self.size_box)
        form.addWidget(style_box)

        view_box = QGroupBox("View")
        vl = QVBoxLayout(view_box)
        tip = QLabel("Drag to turn; Shift+drag to turn in the page; wheel to magnify; drag a "
                     "label to move it.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: palette(placeholder-text);")
        vl.addWidget(tip)
        row = QHBoxLayout()
        self.view_buttons = []
        for text, how in (("Best", "best"), ("Along a", 0), ("Along b", 1), ("Along c", 2)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, h=how: self.standard_view(h))
            row.addWidget(b)
            self.view_buttons.append(b)
        vl.addLayout(row)
        self.reset_labels_btn = QPushButton("Put the labels back")
        self.reset_labels_btn.clicked.connect(self._reset_labels)
        vl.addWidget(self.reset_labels_btn)
        form.addWidget(view_box)

        self.parts_box = QGroupBox("Shown")
        pl = QVBoxLayout(self.parts_box)
        self.parts = QListWidget()
        self.parts.setMaximumHeight(110)
        self.parts.itemChanged.connect(self._parts_changed)
        pl.addWidget(self.parts)
        self.parts_box.setVisible(False)
        form.addWidget(self.parts_box)

        out_box = QGroupBox("Picture")
        ol = QHBoxLayout(out_box)
        self.save_btn = QPushButton("Save picture…")
        self.save_btn.setToolTip("Save the drawing as a PNG picture (600 dpi), or as an SVG "
                                 "or PDF drawing (sharp at any size)")
        self.save_btn.clicked.connect(self.save_picture)
        self.copy_btn = QPushButton("Copy picture")
        self.copy_btn.setToolTip("Copies the drawing (600 dpi) to paste into PowerPoint, Word…")
        self.copy_btn.clicked.connect(self.copy_picture)
        ol.addWidget(self.save_btn)
        ol.addWidget(self.copy_btn)
        form.addWidget(out_box)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        form.addStretch()
        self._controls = [style_box, view_box, self.parts_box, out_box]

        scroll = QScrollArea()                 # (a short window: the boxes scroll)
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # (never narrower than its boxes, with room for the scroll bar: nothing cut off)
        scroll.setMinimumWidth(side.minimumSizeHint().width()
                               + scroll.verticalScrollBar().sizeHint().width() + 4)
        self.split = QSplitter()
        self.split.addWidget(self.tabs)
        self.split.addWidget(scroll)
        self.split.setStretchFactor(0, 1)
        self.split.setCollapsible(0, False)
        self.split.setCollapsible(1, False)
        self.split.setSizes([780, scroll.minimumWidth()])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.split)
        self._enable(False)

    # ---- the structure shown

    def set_structure(self, structure: crystal.Structure | None, grown: crystal.Grown | None,
                      view: X.ViewSettings | None = None) -> None:
        """Show a structure as `view` draws it (None: as the boxes are set, from the best
        view, about 12 cm wide)."""
        self.structure, self.grown = structure, grown
        if grown is None or not grown.atoms:
            self.clear()
            return
        best = X.ViewSettings.from_json((view or self.settings()).to_json())
        best.rotation = X.best_rotation(np.array([a.xyz for a in grown.atoms])).tolist()
        self._base_zoom = X.fitting_zoom(grown, best)
        if view is None:
            view = best
            view.label_offsets, view.hidden_molecules, view.hidden_parts = {}, [], []
            view.zoom = self._base_zoom
        self._show_settings(view)
        self.canvas.set_structure(grown, X.ViewSettings.from_json(view.to_json()))
        self._fill_parts()
        n = len({a.label for a in grown.atoms})
        self.status.setText(f"{len(grown.atoms)} atoms ({n} in the file), "
                            f"{len(grown.bonds)} bonds, {grown.molecules} "
                            f"molecule(s)." + ("" if grown.complete else
                                               " Stopped growing at "
                                               f"{crystal.MAX_ATOMS} atoms (a polymer?)."))
        self._enable(True)

    def clear(self, message: str = "") -> None:
        """Nothing to draw (`message` says why, on the canvas)."""
        self.structure = self.grown = None
        if message:
            self.canvas.empty_text = message
        self.canvas.set_structure(None, X.ViewSettings())
        self.parts.clear()
        self.parts_box.setVisible(False)
        self.status.setText("")
        self._enable(False)

    def view(self) -> X.ViewSettings:
        """How the structure is drawn now (a copy)."""
        return X.ViewSettings.from_json(self.canvas.view.to_json())

    def _enable(self, on: bool) -> None:
        for w in self._controls:
            w.setEnabled(on)

    def _show_settings(self, v: X.ViewSettings) -> None:
        """The boxes set as `v` is (without redrawing)."""
        boxes = [self.ortep, self.ball, self.probability, self.hydrogens, self.colour,
                 self.principal, self.labels, self.label_size, self.size_box]
        for w in boxes:
            w.blockSignals(True)
        (self.ball if v.style == "ball" else self.ortep).setChecked(True)
        self.probability.setCurrentIndex(max(0, self.probability.findData(v.probability)))
        self.hydrogens.setChecked(v.hydrogens != "hide")
        self.colour.setChecked(v.colour)
        self.principal.setChecked(v.principal)
        self.labels.setChecked(v.labels)
        self.label_size.setValue(v.label_size)
        self.size_box.setValue(round(v.zoom / self._base_zoom * 100) if self._base_zoom else 100)
        for w in boxes:
            w.blockSignals(False)
        self.probability.setEnabled(v.style == "ortep")
        self.principal.setEnabled(v.style == "ortep")

    def _fill_parts(self) -> None:
        """Molecules and disorder parts that can be hidden (shown only when there are
        several)."""
        g, view = self.grown, self.canvas.view
        self.parts.blockSignals(True)
        self.parts.clear()
        if g.molecules > 1:
            for m in range(g.molecules):
                atoms = [a for a in g.atoms if a.molecule == m]
                item = QListWidgetItem(f"Molecule {m + 1}: {_formula(atoms)}")
                item.setData(Qt.ItemDataRole.UserRole, ("molecule", m))
                item.setCheckState(Qt.CheckState.Unchecked if m in view.hidden_molecules
                                   else Qt.CheckState.Checked)
                self.parts.addItem(item)
        for part in sorted({a.atom.part for a in g.atoms if a.atom.part}):
            item = QListWidgetItem(f"Disorder part {part}")
            item.setData(Qt.ItemDataRole.UserRole, ("part", part))
            item.setCheckState(Qt.CheckState.Unchecked if part in view.hidden_parts
                               else Qt.CheckState.Checked)
            self.parts.addItem(item)
        self.parts.blockSignals(False)
        self.parts_box.setVisible(self.parts.count() > 0)

    # ---- the user's changes

    def settings(self) -> X.ViewSettings:
        """The drawing as the boxes set it."""
        v = self.view()
        v.style = "ball" if self.ball.isChecked() else "ortep"
        v.probability = self.probability.currentData()
        v.hydrogens = "spheres" if self.hydrogens.isChecked() else "hide"
        v.colour = self.colour.isChecked()
        v.principal = self.principal.isChecked()
        v.labels = self.labels.isChecked()
        v.label_size = self.label_size.value()
        return v

    def _settings_changed(self, *_args) -> None:
        self.probability.setEnabled(self.ortep.isChecked())
        self.principal.setEnabled(self.ortep.isChecked())
        if self.grown is None:
            return
        restyled = self.canvas.view.style != ("ball" if self.ball.isChecked() else "ortep")
        self.canvas.view = self.settings()
        self.canvas.redraw()
        if restyled:              # (balls are larger than ellipsoids: all of it in view)
            self.canvas.fit()
        self.view_changed.emit()

    def _size_changed(self, percent: int) -> None:
        if self.grown is None:
            return
        self.canvas.view.zoom = self._base_zoom * percent / 100
        self.canvas.redraw()
        self.canvas.fit()
        self.view_changed.emit()

    def _parts_changed(self, _item) -> None:
        view = self.canvas.view
        view.hidden_molecules, view.hidden_parts = [], []
        for k in range(self.parts.count()):
            item = self.parts.item(k)
            kind, value = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() != Qt.CheckState.Checked:
                (view.hidden_molecules if kind == "molecule" else view.hidden_parts).append(value)
        self.canvas.redraw()
        self.view_changed.emit()

    def standard_view(self, how) -> None:
        if self.grown is None:
            return
        if how == "best":
            rotation = X.best_rotation(np.array([a.xyz for a in self.grown.atoms]))
        else:
            rotation = X.axis_rotation(self.structure.orth[:, how])
        self.canvas.view.rotation = rotation.tolist()
        self.canvas.redraw()
        self.canvas.fit()
        self.view_changed.emit()

    def _reset_labels(self) -> None:
        self.canvas.view.label_offsets = {}
        self.canvas.redraw()
        self.view_changed.emit()

    # ---- the picture

    def save_picture(self) -> None:
        items = self.canvas.items()
        if not items:
            return
        folder = self.picture_folder or Path.home()
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save the picture", str(folder / f"{self.picture_name}.png"),
            "PNG picture, 600 dpi (*.png);;SVG drawing (*.svg);;PDF drawing (*.pdf)")
        if not path:
            return
        path = Path(path)
        try:
            if path.suffix.lower() == ".svg" or chosen.startswith("SVG"):
                path = path.with_suffix(".svg")
                path.write_text(X.to_svg(items), encoding="utf-8")
            elif path.suffix.lower() == ".pdf" or chosen.startswith("PDF"):
                path = path.with_suffix(".pdf")
                path.write_bytes(pdf_bytes(items))
            else:
                path = path.with_suffix(".png")
                if not picture(items).save(str(path), "PNG"):
                    raise OSError("the picture could not be written")
        except OSError as e:
            QMessageBox.warning(self, "Could not save the picture", f"{path.name}: {e}")
            return
        self.picture_folder = path.parent
        self.status.setText(f"Saved {path.name}.")

    def copy_picture(self) -> None:
        items = self.canvas.items()
        if not items:
            return
        data = QMimeData()
        data.setImageData(picture(items))
        data.setData("image/svg+xml", X.to_svg(items).encode("utf-8"))
        QGuiApplication.clipboard().setMimeData(data)
        self.status.setText("Copied: paste it into PowerPoint, Word or another program.")


class XrayViewer(QDialog):
    """File ▸ X-ray Structure Viewer…: open a CIF, choose how it is drawn, save or copy
    the picture, see its crystal data table. Nothing is stored in the project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("X-ray Structure Viewer")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1100, 720)
        self.blocks: list[cif.CifBlock] = []
        self.path: Path | None = None

        self.editor = XrayEditor()
        self.canvas = self.editor.canvas
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.editor.tabs.addTab(self.table, "Crystal data")

        open_btn = QPushButton("Open CIF…")
        open_btn.clicked.connect(self.choose_file)
        self.file_label = QLabel("No file open.")
        self.file_label.setWordWrap(True)
        self.block_box = QComboBox()
        self.block_box.currentIndexChanged.connect(self._block_chosen)
        self.block_box.setVisible(False)
        for w in (open_btn, self.file_label, self.block_box):
            self.editor.top.addWidget(w)
        lay = QVBoxLayout(self)
        lay.addWidget(self.editor)

    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a CIF file", str(self.path.parent)
                                              if self.path else "", "CIF files (*.cif);;All files (*)")
        if path:
            self.open_file(Path(path))

    def open_file(self, path: Path) -> bool:
        try:
            blocks = cif.read_cif_file(path)
        except OSError as e:
            QMessageBox.warning(self, "Could not open the CIF", f"{path.name}: {e}")
            return False
        blocks = [b for b in blocks if b.rows("_atom_site_label")]
        if not blocks:
            QMessageBox.warning(self, "No structure in this file",
                                f"“{path.name}” holds no atoms (no _atom_site_ table). Is it "
                                "the structure's CIF (not a checkCIF report)?")
            return False
        self.path, self.blocks = path, blocks
        self.editor.picture_name, self.editor.picture_folder = path.stem, path.parent
        self.file_label.setText(path.name)
        self.block_box.blockSignals(True)
        self.block_box.clear()
        for b in blocks:
            self.block_box.addItem(b.name or "(no name)")
        self.block_box.setVisible(len(blocks) > 1)
        self.block_box.blockSignals(False)
        self._block_chosen(0)
        return True

    def _block_chosen(self, index: int) -> None:
        if not 0 <= index < len(self.blocks):
            return
        block = self.blocks[index]
        try:
            structure = crystal.structure_from_cif(block)
            grown = crystal.grow(structure)
        except crystal.CrystalError as e:
            QMessageBox.warning(self, "Could not draw this structure", str(e))
            self.editor.clear()
            return
        self.editor.set_structure(structure, grown)
        self._fill_table(block)

    def _fill_table(self, block: cif.CifBlock) -> None:
        rows = crystal.crystal_table(block)
        self.table.setRowCount(len(rows))
        for r, (_key, label, value) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(xray.plain(label)))
            self.table.setItem(r, 1, QTableWidgetItem(xray.plain(value)))
        self.table.resizeColumnToContents(0)


def _formula(atoms) -> str:
    counts: dict[str, int] = {}
    for a in atoms:
        counts[a.element] = counts.get(a.element, 0) + 1
    order = (["C", "H"] if "C" in counts else []) + sorted(k for k in counts if k not in ("C", "H")
                                                          or "C" not in counts)
    return "".join(f"{el}{counts[el] if counts[el] > 1 else ''}" for el in order if el in counts)
