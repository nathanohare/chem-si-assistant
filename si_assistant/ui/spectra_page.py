"""A compound's spectra page (SPEC §13k.12), under the Spectra section in the tree.

It lists the compound's spectra (each page of a PDF on its own) and draws the chosen one
as it will be printed: the spectrum (auto-cropped if that is on) at the proportions of its
slot on the page, with the structure + ID box on top. The box can be dragged anywhere on
the spectrum and resized with its corner handles. Next to it, every layout setting of the
Spectra section can be changed for this spectrum; each shows whether it follows the
section's default and can be reset.

Settings are saved on the spectrum's entry in compound.json (core/spectra.py): a dragged
position always for its page ("pages"), everything else for this page, or, for a PDF with
several pages, for every page of the file ("layout") if chosen. The order the files are
printed in can be changed under the list ("spectra_order" in compound.json).
"""

from __future__ import annotations

import math
import re
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPainter, QPen,
                           QShortcut)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QSplitter, QToolButton, QVBoxLayout, QWidget)

from ..compile.document import drawn_structure
from ..compile.qt_images import content_box, image_info, render_page
from ..core import chem
from ..core import spectra as S
from ..core.project import Compound, Project, ProjectError
from .spectra_panel import ColourButton
from .structure_widgets import HIGHLIGHT, paste_key

SAVE_DELAY_MS = 300
SCREEN_DPI = 110           # spectra are drawn at about this resolution on screen
HANDLE_PX = 9              # the box's corner handles
STRUCTURE_PX = 700         # a structure picture is read at most this big for the screen
DESK = "#d9dce1"           # around the spectrum

# (field key, file, page) of the spectrum last shown for each compound, so its page opens
# on the same spectrum after being rebuilt (e.g. after Project settings).
_last_shown: dict[str, tuple] = {}

_pages: OrderedDict[tuple, QImage] = OrderedDict()   # rendered spectrum pages
PAGE_CACHE_SIZE = 24


def page_image(path: Path, page: int | None) -> QImage:
    """A spectrum page drawn for the screen (kept until the file changes)."""
    try:
        st = path.stat()
    except OSError:
        return QImage()
    key = (str(path), st.st_mtime_ns, st.st_size, page)
    image = _pages.get(key)
    if image is None:
        image = render_page(path, page, SCREEN_DPI)
        _pages[key] = image
        while len(_pages) > PAGE_CACHE_SIZE:
            _pages.popitem(last=False)
    _pages.move_to_end(key)
    return image


_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def pretty(text: str) -> str:
    """"1H NMR (CDCl3)" -> "¹H NMR (CDCl₃)" for the screen (the PDF does this properly)."""
    text = re.sub(r"(?<![\w])(\d+)([A-Z][a-z]?)(?= NMR)",
                  lambda m: m.group(1).translate(_SUP) + m.group(2), text)
    return re.sub(r"(?<=[A-Za-z)])\d+", lambda m: m.group(0).translate(_SUB), text)


def pretty_header(text: str) -> str:
    """As pretty(), but leaving the compound ID before " — " alone (e.g. "S12")."""
    head, sep, rest = text.partition(" — ")
    return head + sep + pretty(rest) if sep else pretty(text)


def structure_source(project: Project, compound: Compound):
    """What prints as the structure on a spectrum, as compile/document.structure_image
    chooses it: ("picture", path, natural size in inches or None) or ("svg", SVG text,
    natural size); None if the compound has neither."""
    picture = project.structure_image_path(compound)
    has_picture = picture is not None and picture.exists()

    def as_picture():
        info = image_info(picture)
        return ("picture", picture, (info[0] / info[2], info[1] / info[2]) if info else None)

    if project.settings.get("compile_structures", "pictures") == "pictures" and has_picture:
        return as_picture()
    mol = project.read_structure(compound)
    if mol is None:
        return as_picture() if has_picture else None
    text, size = drawn_structure(mol, chem.DrawingSettings.from_settings(project.settings))
    return ("svg", text, size)


# ---------------------------------------------------------------------- the spectrum

class SpectrumCanvas(QWidget):
    """One spectrum drawn in its printed proportions, with the structure + ID box on it.
    Drag the box to move it; drag a corner handle to resize it (keeping its shape)."""

    box_moved = Signal(float, float)   # the box's new centre (fractions of the spectrum)
    box_resized = Signal(float)        # its new structure size (struct_scale)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(260, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.image = QImage()
        self.message = ""
        self.values: dict = dict(S.DEFAULTS)   # the spectrum's effective settings
        self.slot_in = S.slot_size_in("ACS", 1, "portrait")
        self.crop: tuple | None = None
        self.header = ""
        self.id_text = ""
        self.caption = ""
        self.id_pt = 11.0
        self.editable = True
        self._structure = None      # ("svg", QSvgRenderer, size) / ("picture", QImage, size)
        self._mode = ""             # "move" or "resize" while the mouse is down
        self._moved = False         # (the mouse moved since it was pressed)
        self._grab = QPointF()
        self._anchor = QPointF()
        self._start_scale = 1.0
        self._start_dist = 1.0
        self._hover = False

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(560, 420)

    def set_structure(self, source) -> None:
        """source: as structure_source() returns."""
        self._structure = None
        if source is None:
            pass
        elif source[0] == "svg":
            renderer = QSvgRenderer(QByteArray(source[1].encode("utf-8")))
            if renderer.isValid():
                self._structure = ("svg", renderer, source[2])
        else:
            from .structure_widgets import picture_pixmap
            pixmap = picture_pixmap(source[1], QSize(STRUCTURE_PX, STRUCTURE_PX))
            if not pixmap.isNull():
                self._structure = ("picture", pixmap, source[2])
        self.update()

    # ------------------------------------------------------------------ geometry

    def aspect(self) -> float | None:
        if self.image.isNull() or not self.image.width():
            return None
        return self.image.height() / self.image.width()

    def shown_in(self) -> tuple[float, float]:
        """The printed size (inches) of the part of the spectrum that is shown: as large as
        fits in the slot below the header line (as the styles' si-spectrum does)."""
        return S.shown_size_in(self.slot_in, self.aspect(), self.crop,
                               header=bool(self.values.get("header_line")))

    def spectrum_rect(self) -> tuple[QRectF, float]:
        """(where the shown spectrum is drawn, pixels per printed inch)."""
        w_in, h_in = self.shown_in()
        head_in = S.HEADER_IN if self.values.get("header_line") else 0.0
        area = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        k = max(1e-3, min(area.width() / w_in, area.height() / (h_in + head_in)))
        w, h = w_in * k, h_in * k
        top = area.y() + (area.height() - h - head_in * k) / 2 + head_in * k
        return QRectF(area.x() + (area.width() - w) / 2, top, w, h), k

    def _id_font(self, units: float) -> QFont:
        """The ID's font, `units` pixels per point."""
        f = QFont(self.font())
        f.setPixelSize(max(1, round(self.id_pt * units)))
        f.setBold(bool(self.values.get("id_bold", True)))
        return f

    def _caption_font(self, units: float) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(max(1, round(S.CAPTION_PT * units)))
        return f

    def _lines_pt(self) -> list[tuple[str, float, float]]:
        """The box's lines, top to bottom: (what, width pt, height pt). None when the
        structure + ID are left off this spectrum."""
        out = []
        if not self.values.get("struct_box", True):
            return out
        if self._structure is not None:
            w, h = S.structure_size_in(self._structure[2], self.values.get("struct_scale", 1.0))
            out.append(("structure", w * 72, h * 72))
        units = 10.0   # measure at 10 pixels per point, for precision
        slack = 1.06   # (small screen fonts are rounded up to whole pixels)
        if self.id_text:
            fm = QFontMetricsF(self._id_font(units))
            out.append(("id", fm.horizontalAdvance(self.id_text) / units * slack,
                        self.id_pt * 1.25))
        if self.caption:
            fm = QFontMetricsF(self._caption_font(units))
            out.append(("caption", fm.horizontalAdvance(self.caption) / units * slack,
                        S.CAPTION_PT * 1.25))
        return out

    def box_size_pt(self) -> tuple[float, float]:
        """The structure + ID box's printed size, in points (with its padding)."""
        lines = self._lines_pt()
        if not lines:
            return 0.0, 0.0
        gap = 0.4 * self.id_pt
        w = max(line[1] for line in lines)
        h = sum(line[2] for line in lines) + gap * (len(lines) - 1)
        return w + 2 * S.BOX_INSET_PT, h + 2 * S.BOX_INSET_PT

    def box_rect(self) -> QRectF:
        """Where the structure + ID box is drawn (empty if there is nothing in it)."""
        spec, k = self.spectrum_rect()
        w_pt, h_pt = self.box_size_pt()
        if not w_pt:
            return QRectF()
        w, h = w_pt / 72 * k, h_pt / 72 * k
        v = self.values
        if "x" in v and "y" in v:
            cx, cy = spec.x() + v["x"] * spec.width(), spec.y() + v["y"] * spec.height()
            return QRectF(cx - w / 2, cy - h / 2, w, h)
        inset = S.CORNER_INSET_IN * k
        corner = v.get("corner", "top-left")
        x = spec.right() - inset - w if corner.endswith("right") else spec.x() + inset
        y = spec.bottom() - inset - h if corner.startswith("bottom") else spec.y() + inset
        return QRectF(x, y, w, h)

    def _handles(self) -> dict[str, QRectF]:
        box = self.box_rect()
        if box.isEmpty():
            return {}
        s = HANDLE_PX
        return {name: QRectF(p.x() - s / 2, p.y() - s / 2, s, s) for name, p in (
            ("top-left", box.topLeft()), ("top-right", box.topRight()),
            ("bottom-left", box.bottomLeft()), ("bottom-right", box.bottomRight()))}

    # ------------------------------------------------------------------ drawing

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(DESK))
        if self.image.isNull():
            p.setPen(QColor("#555"))
            p.drawText(self.rect().adjusted(20, 10, -20, -10),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.message or "No spectrum to show.")
            p.end()
            return
        spec, k = self.spectrum_rect()
        v = self.values
        if v.get("header_line") and self.header:
            f = QFont(self.font())
            f.setPixelSize(max(1, round(11 * k / 72)))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("black"))
            head = QRectF(spec.x() - 200, spec.y() - S.HEADER_IN * k, spec.width() + 400,
                          S.HEADER_IN * k)
            p.drawText(head, Qt.AlignmentFlag.AlignCenter, self.header)
        l, t, r, b = self.crop or (0.0, 0.0, 1.0, 1.0)
        iw, ih = self.image.width(), self.image.height()
        source = QRectF(l * iw, t * ih, (r - l) * iw, (b - t) * ih)
        p.fillRect(spec, QColor("white"))
        p.drawImage(spec, self.image, source)
        if v.get("spectrum_frame"):
            p.setPen(QPen(QColor(v["spectrum_frame_color"]),
                          max(1.0, v["spectrum_frame_width"] * k / 72)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(spec)
        self._paint_box(p, k)
        p.end()

    def _paint_box(self, p: QPainter, k: float) -> None:
        box = self.box_rect()
        if box.isEmpty():
            return
        v = self.values
        p.setBrush(QColor(v.get("struct_bg", "#ffffff")))
        if v.get("struct_frame"):
            p.setPen(QPen(QColor(v["struct_frame_color"]),
                          max(1.0, v["struct_frame_width"] * k / 72)))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(box)
        units = k / 72   # pixels per point
        y = box.y() + S.BOX_INSET_PT * units
        gap = 0.4 * self.id_pt * units
        for what, w_pt, h_pt in self._lines_pt():
            w, h = w_pt * units, h_pt * units
            line = QRectF(box.center().x() - w / 2, y, w, h)
            if what == "structure":
                kind, thing, _size = self._structure
                if kind == "svg":
                    thing.render(p, line)
                else:
                    p.drawPixmap(line.toRect(), thing)
            else:
                p.setFont(self._id_font(units) if what == "id" else self._caption_font(units))
                p.setPen(QColor("black"))
                wide = line.adjusted(-w, 0, w, 0)
                p.drawText(wide, Qt.AlignmentFlag.AlignCenter,
                           self.id_text if what == "id" else self.caption)
            y += h + gap
        if self.editable and (self._hover or self._mode):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(HIGHLIGHT), 1.5, Qt.PenStyle.DashLine))
            p.drawRect(box)
            p.setPen(QPen(QColor(HIGHLIGHT), 1))
            p.setBrush(QColor("white"))
            for handle in self._handles().values():
                p.drawRect(handle)

    # ------------------------------------------------------------------ the mouse

    def _handle_at(self, pos: QPointF) -> str | None:
        for name, rect in self._handles().items():
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return name
        return None

    def start_resize(self, handle: str, pos: QPointF) -> None:
        """Start resizing from a corner handle at `pos`: the box keeps its centre when it
        was dragged to a place of its own, or else its corner nearest the spectrum's."""
        box = self.box_rect()
        v = self.values
        if "x" in v and "y" in v:
            self._anchor = box.center()
        else:
            corner = v.get("corner", "top-left")
            self._anchor = QPointF(box.right() if corner.endswith("right") else box.left(),
                                   box.bottom() if corner.startswith("bottom") else box.top())
        self._start_scale = float(v.get("struct_scale", 1.0))
        self._start_dist = max(4.0, math.dist((pos.x(), pos.y()),
                                              (self._anchor.x(), self._anchor.y())))
        self._mode = "resize"
        self._moved = False

    def start_move(self, pos: QPointF) -> None:
        self._grab = pos - self.box_rect().center()
        self._mode = "move"
        self._moved = False

    def drag_to(self, pos: QPointF) -> None:
        """The mouse moved to `pos` while moving or resizing the box."""
        if self._mode == "move":
            spec, _k = self.spectrum_rect()
            centre = pos - self._grab
            self.values["x"] = round(min(1.0, max(0.0, (centre.x() - spec.x()) / spec.width())), 4)
            self.values["y"] = round(min(1.0, max(0.0, (centre.y() - spec.y()) / spec.height())), 4)
        elif self._mode == "resize":
            d = math.dist((pos.x(), pos.y()), (self._anchor.x(), self._anchor.y()))
            lo, hi = S.SCALE_RANGE
            self.values["struct_scale"] = round(min(hi, max(lo, self._start_scale * d / self._start_dist)), 2)
        else:
            return
        self._moved = True
        self.update()

    def finish(self) -> None:
        """The mouse was let go: report the new place or size (nothing for a click that
        did not move it)."""
        mode, self._mode = self._mode, ""
        moved, self._moved = self._moved, False
        if moved and mode == "move" and "x" in self.values and "y" in self.values:
            self.box_moved.emit(self.values["x"], self.values["y"])
        elif moved and mode == "resize":
            self.box_resized.emit(self.values["struct_scale"])
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self.editable and event.button() == Qt.MouseButton.LeftButton:
            handle = self._handle_at(pos)
            if handle is not None:
                self.start_resize(handle, pos)
            elif self.box_rect().contains(pos):
                self.start_move(pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self._mode:
            self.drag_to(pos)
            return
        handle = self._handle_at(pos) if self.editable else None
        over = self.editable and self.box_rect().contains(pos)
        if handle in ("top-left", "bottom-right"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle is not None:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if over else Qt.CursorShape.ArrowCursor)
        if (over or handle is not None) != self._hover:
            self._hover = over or handle is not None
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._mode:
            self.finish()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover and not self._mode:
            self._hover = False
            self.update()
        super().leaveEvent(event)


# ---------------------------------------------------------------------- the page

# The settings on the page, one row each: (row key, label, the settings it covers).
ROWS = (
    ("per_page", "Spectra per page:", ("per_page",)),
    ("orientation", "Orientation:", ("orientation",)),
    ("header_line", "Header line:", ("header_line",)),
    ("spectrum_frame", "Spectrum frame:", ("spectrum_frame", "spectrum_frame_color",
                                           "spectrum_frame_width")),
    ("auto_crop", "Auto-crop:", ("auto_crop",)),
    ("struct_box", "Structure + ID:", ("struct_box",)),
    ("position", "Structure position:", ("corner", "x", "y")),
    ("struct_scale", "Structure size:", ("struct_scale",)),
    ("id", "ID text:", ("id_size", "id_bold")),
    ("struct_frame", "Frame around them:", ("struct_frame", "struct_frame_color",
                                            "struct_frame_width")),
    ("struct_bg", "Background:", ("struct_bg",)),
    ("caption", "Caption under them:", ("caption",)),
)
DRAGGED = "dragged"   # the position choice shown once the box has been dragged


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    return a == b


class SpectraPagePanel(QWidget):
    """The spectra of one compound, under the Spectra section in the tree."""

    saved = Signal(str)            # status-bar message (the live preview updates too)
    open_compound = Signal(str)    # "Compound page" was clicked: compound id

    def __init__(self, project: Project, compound: Compound, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.compound = compound
        self.read_only = project.read_only
        self.defaults = project.spectra_defaults()
        self.crops = S.CropCache(project, content_box)
        self.style_name = project.settings.get("journal_style", "ACS")
        problems: list[str] = []
        # (a PDF that can't be read stays in the list, so it can be told what is wrong)
        self.spectra = S.spectra_of(project, compound, problems=problems, keep_unreadable=True)
        self.current: S.Spectrum | None = None
        self._several = False      # the current spectrum's file has several pages
        self._scope = "page"       # what the controls change: "page" or "file" (see scope_key)
        self._loading = False
        report = project.id_report()
        self.display_id = report.display_id(compound.id)

        # ---- header
        title = QLabel(f"Spectra of {self.display_id}")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        f.setBold(True)
        title.setFont(f)
        name = QLabel(compound.name or "(unnamed compound)")
        name.setWordWrap(True)
        name.setStyleSheet("color: palette(placeholder-text);")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        go = QPushButton("Compound page")
        go.setToolTip("Open this compound's page (add or remove spectra in its Spectra field)")
        go.clicked.connect(lambda: self.open_compound.emit(self.compound.id))
        # SPEC §13l.1: paste a spectrum copied in Mnova straight onto this page.
        self.paste_btn = QPushButton("Paste spectrum")
        self.paste_btn.setToolTip(f"Paste a spectrum copied in Mnova (or a picture, or copied "
                                  f"PDF/picture files) into {self.display_id}'s spectra. "
                                  f"{paste_key()} on this page (or right-click) does the same.")
        self.paste_btn.clicked.connect(self.paste_spectrum)
        self.paste_btn.setEnabled(not self.read_only)
        paste = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self)
        paste.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        paste.activated.connect(self.paste_spectrum)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addWidget(name, 1)
        head.addWidget(self.paste_btn)
        head.addWidget(go)
        hint = QLabel("Drag the structure and ID to move them, or a corner handle to resize "
                      "them. Changes here apply to the chosen spectrum only; the Spectra "
                      "section's page sets the defaults for all of them.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        if problems:
            hint.setText(hint.text() + "\n⚠ " + "\n⚠ ".join(problems))

        # ---- the list and the spectrum
        self.list = QListWidget()
        self.list.setMinimumWidth(110)
        self.list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.list.setWordWrap(True)   # (long names wrap rather than scroll sideways)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._show_spectrum)
        self.canvas = SpectrumCanvas()
        self.canvas.editable = not self.read_only
        self.canvas.box_moved.connect(self._box_moved)
        self.canvas.box_resized.connect(self._box_resized)
        self.canvas.set_structure(structure_source(project, compound))
        self.canvas.id_text = self.display_id
        for widget in (self.canvas, self.list):   # right-click ▸ Paste spectrum
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget: self._paste_menu(w, pos))
        # A click on the drawing (or anywhere on the page) lets Ctrl+V paste here.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: palette(placeholder-text);")
        canvas_box = QVBoxLayout()
        canvas_box.setContentsMargins(0, 0, 0, 0)
        canvas_box.addWidget(self.canvas, 1)
        canvas_box.addWidget(self.note)
        canvas_holder = QWidget()
        canvas_holder.setLayout(canvas_box)
        # The order the files are printed in: automatic (by type) unless changed here.
        self.up_btn = QToolButton()
        self.up_btn.setText("▲ Up")
        self.up_btn.setToolTip("Print this file (all its pages) before the one above it")
        self.up_btn.clicked.connect(lambda: self.move_file(-1))
        self.down_btn = QToolButton()
        self.down_btn.setText("▼ Down")
        self.down_btn.setToolTip("Print this file (all its pages) after the one below it")
        self.down_btn.clicked.connect(lambda: self.move_file(1))
        self.auto_order_btn = QToolButton()
        self.auto_order_btn.setText("Automatic order")
        self.auto_order_btn.setToolTip("Print this compound's spectra in the automatic order "
                                       "again (¹H, ¹³C, other nuclei, 2D, HPLC/SFC, IR, other, "
                                       "then Additional files)")
        self.auto_order_btn.clicked.connect(self.automatic_order)
        order_row = QHBoxLayout()
        order_row.setContentsMargins(0, 0, 0, 0)
        order_row.setSpacing(2)
        order_row.addWidget(self.up_btn)
        order_row.addWidget(self.down_btn)
        order_row.addStretch()
        list_box = QVBoxLayout()
        list_box.setContentsMargins(0, 0, 0, 0)
        list_box.addWidget(self.list, 1)
        list_box.addLayout(order_row)
        list_box.addWidget(self.auto_order_btn)
        list_holder = QWidget()
        list_holder.setLayout(list_box)
        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(list_holder)
        top.addWidget(canvas_holder)
        top.setStretchFactor(1, 1)
        top.setCollapsible(1, False)
        top.setSizes([140, 520])

        # ---- the settings
        settings = self._build_settings()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(settings)
        body = QSplitter(Qt.Orientation.Vertical)
        body.addWidget(top)
        body.addWidget(scroll)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        body.setCollapsible(0, False)
        self.splitter = body

        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(hint)
        layout.addWidget(body, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self._save)

        self._fill_list()
        if not self.spectra:
            self.canvas.message = ("This compound has no spectra yet. Add them in the Spectra "
                                   "field of its compound page.")
            # (greyed out, showing the defaults rather than whatever the controls hold)
            self.scope_label.setVisible(False)
            self.scope.setVisible(False)
            self._set_controls(dict(self.defaults), dragged=False)
            self.settings_box.setEnabled(False)
            self.text_box.setEnabled(False)

    # ------------------------------------------------------------------ building

    def _build_settings(self) -> QWidget:
        ro = self.read_only
        # Which spectrum the settings change: shown for PDFs with several pages.
        self.scope = QComboBox()
        self.scope.addItem("This page only", "page")
        self.scope.addItem("Every page of this file", "file")
        self.scope.setToolTip("A PDF with several pages: change one page, or all its pages at "
                              "once. A dragged position is always for its own page.")
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.scope_label = QLabel("Changes apply to:")

        self.per_page = QComboBox()
        for n, text in ((1, "1 (a whole page)"), (2, "2"), (4, "4 (2 × 2)")):
            self.per_page.addItem(text, n)
        self.per_page.setToolTip("A spectrum with another number per page than the one before "
                                 "it starts a new page.")
        self.orientation = QComboBox()
        for key, text in S.ORIENTATIONS.items():
            self.orientation.addItem(text, key)
        self.orientation.setToolTip("The orientation of the page this spectrum is printed on")
        self.header_line = QCheckBox("Shown")
        self.spectrum_frame = QCheckBox("Frame")
        self.spectrum_colour = ColourButton("#000000", "Colour of the frame around the spectrum",
                                            compact=True)
        self.spectrum_width = self._width_box()
        self.auto_crop = QCheckBox("Cut off white margins")
        self.struct_box = QCheckBox("Shown on this spectrum")
        self.struct_box.setToolTip("Untick to print this spectrum without the structure and ID "
                                   "(e.g. a crowded HPLC trace)")
        self.corner = QComboBox()
        for key, text in S.CORNERS.items():
            self.corner.addItem(text, key)
        self.corner.setToolTip("A corner of the spectrum, or where the box was dragged")
        self.corner.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.scale = QSpinBox()
        self.scale.setRange(round(S.SCALE_RANGE[0] * 100), round(S.SCALE_RANGE[1] * 100))
        self.scale.setSingleStep(10)
        self.scale.setSuffix(" %")
        self.scale.setToolTip("100 % = the structure's normal size (at most 1.5 × 1.1 inches); "
                              "or drag a corner handle of the box")
        self.same_size = QCheckBox("Same as the text")
        self.id_size = QDoubleSpinBox()
        self.id_size.setRange(*S.ID_SIZE_RANGE)
        self.id_size.setDecimals(1)
        self.id_size.setSingleStep(0.5)
        self.id_size.setSuffix(" pt")
        self.id_bold = QCheckBox("Bold")
        self.struct_frame = QCheckBox("Frame")
        self.struct_colour = ColourButton("#000000", "Colour of the frame around the structure "
                                                     "and ID", compact=True)
        self.struct_width = self._width_box()
        self.background = ColourButton("#ffffff", "Background behind the structure and ID",
                                       compact=True)
        self.caption = QCheckBox("Shown")

        controls = {
            "per_page": (self.per_page,), "orientation": (self.orientation,),
            "header_line": (self.header_line,),
            "spectrum_frame": (self.spectrum_frame, self.spectrum_colour, self.spectrum_width),
            "auto_crop": (self.auto_crop,), "struct_box": (self.struct_box,),
            "position": (self.corner,),
            "struct_scale": (self.scale,), "id": (self.same_size, self.id_size, self.id_bold),
            "struct_frame": (self.struct_frame, self.struct_colour, self.struct_width),
            "struct_bg": (self.background,), "caption": (self.caption,)}

        self.settings_box = QGroupBox("Settings for this spectrum")
        grid = QGridLayout(self.settings_box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.addWidget(self.scope_label, 0, 1)
        grid.addWidget(self.scope, 0, 2, Qt.AlignmentFlag.AlignLeft)
        self.states: dict[str, QLabel] = {}
        self.resets: dict[str, QToolButton] = {}
        # Each row: ↺ (back to the default), its label (which shows where the value comes
        # from: blue and bold = changed here; hover for details), its controls.
        for r, (row, label, _keys) in enumerate(ROWS, start=1):
            reset = QToolButton()
            reset.setText("↺")
            reset.setAutoRaise(True)
            reset.setToolTip("Back to the default for this setting")
            reset.clicked.connect(lambda _=False, row=row: self.reset_row(row))
            grid.addWidget(reset, r, 0)
            state = QLabel(label)
            grid.addWidget(state, r, 1)
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            for w in controls[row]:
                line.addWidget(w)
            line.addStretch()
            holder = QWidget()
            holder.setLayout(line)
            grid.addWidget(holder, r, 2)
            self.states[row], self.resets[row] = state, reset
        grid.setColumnStretch(2, 1)
        legend = QLabel(f"<span style='color:{HIGHLIGHT}; font-weight:bold'>Blue</span>: "
                        "changed for this spectrum (↺ puts it back to the default); "
                        "<i>italic</i>: changed for every page of this file; "
                        "<span style='color:#d35400'><i>orange</i></span>: this page's own "
                        "setting, which wins over the one for every page.")
        legend.setWordWrap(True)
        legend.setStyleSheet("color: palette(placeholder-text);")
        grid.addWidget(legend, len(ROWS) + 1, 0, 1, 3)
        self.reset_btn = QPushButton("Reset this spectrum to the defaults")
        self.reset_btn.setToolTip("Remove everything changed for this spectrum, including where "
                                  "its structure was dragged")
        self.reset_btn.clicked.connect(self.reset_spectrum)
        # Shown when the greyed-out reset has nothing to do here but the file has settings.
        self.reset_hint = QLabel("Settings for every page of this file: choose “Every page of "
                                 "this file” above to change or reset them.")
        self.reset_hint.setWordWrap(True)
        self.reset_hint.setStyleSheet("color: palette(placeholder-text);")
        self.reset_hint.setVisible(False)
        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(0, 0, 0, 0)
        reset_row.addWidget(self.reset_btn)
        reset_row.addWidget(self.reset_hint, 1)
        grid.addLayout(reset_row, len(ROWS) + 2, 0, 1, 3)

        # The header and caption typed for the file (also on the compound page's Spectra
        # field); empty = automatic.
        self.text_box = QGroupBox("Text for this file")
        form = QGridLayout(self.text_box)
        self.header_edit = QLineEdit()
        self.header_edit.setToolTip("The line printed above the spectrum. Leave empty for the "
                                    "automatic one.")
        self.caption_edit = QLineEdit()
        self.caption_edit.setToolTip("Printed under the structure when “Caption under them” is "
                                     "on. Leave empty for the automatic one (NMR spectra).")
        self.header_edit.editingFinished.connect(lambda: self._commit_text(S.HEADER_KEY))
        self.caption_edit.editingFinished.connect(lambda: self._commit_text(S.CAPTION_KEY))
        form.addWidget(QLabel("Header line:"), 0, 0)
        form.addWidget(self.header_edit, 0, 1)
        form.addWidget(QLabel("Caption:"), 1, 0)
        form.addWidget(self.caption_edit, 1, 1)
        self.text_note = QLabel()
        self.text_note.setWordWrap(True)
        self.text_note.setStyleSheet("color: palette(placeholder-text);")
        form.addWidget(self.text_note, 2, 0, 1, 2)

        for box in (self.per_page, self.orientation, self.corner):
            box.currentIndexChanged.connect(self._changed)
        for check in (self.header_line, self.spectrum_frame, self.auto_crop, self.same_size,
                      self.id_bold, self.struct_frame, self.caption, self.struct_box):
            check.toggled.connect(self._changed)
        for spin in (self.scale, self.id_size, self.spectrum_width, self.struct_width):
            spin.valueChanged.connect(self._changed)
        for button in (self.spectrum_colour, self.struct_colour, self.background):
            button.changed.connect(self._changed)
        self._controls = controls
        if ro:
            for w in (*[w for ws in controls.values() for w in ws], self.reset_btn,
                      *self.resets.values()):
                w.setEnabled(False)
            self.header_edit.setReadOnly(True)
            self.caption_edit.setReadOnly(True)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.settings_box)
        column.addWidget(self.text_box)
        column.addStretch()
        holder = QWidget()
        holder.setLayout(column)
        return holder

    @staticmethod
    def _width_box() -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(*S.FRAME_WIDTH_RANGE)
        box.setDecimals(2)
        box.setSingleStep(0.25)
        box.setSuffix(" pt")
        box.setToolTip("Line width")
        return box

    def _fill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        counts: dict[tuple, int] = {}
        for sp in self.spectra:
            counts[(sp.field_key, sp.item.get("file"))] = sp.page_no
        wanted = _last_shown.get(self.compound.id)
        row = 0
        for i, sp in enumerate(self.spectra):
            n = counts[(sp.field_key, sp.item.get("file"))]
            text = pretty(S.spectrum_name(sp.item, self.compound))
            if n > 1:
                text += f" · page {sp.page_no} of {n}"
            if sp.problem:
                text += " (could not be read)"
            elif not sp.path.exists():
                text += " (missing)"
            item = QListWidgetItem(text)
            missing = "" if sp.path.exists() else " (missing)"
            item.setToolTip(f"{sp.item.get('file', '')}{missing}")
            self.list.addItem(item)
            if self._spectrum_key(sp) == wanted:
                row = i
        self._mark_list()
        if self.spectra:
            self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        if self.spectra:
            self._show_spectrum(row)
        self._update_order_buttons()

    # ------------------------------------------------------------------ pasting (SPEC §13l.1)

    def _paste_menu(self, widget: QWidget, pos) -> None:
        if self.read_only:
            return
        menu = QMenu(self)
        menu.addAction("Paste spectrum", self.paste_spectrum)
        menu.exec(widget.mapToGlobal(pos))

    def paste_spectrum(self) -> None:
        """Paste a spectrum copied in Mnova (or a picture, or copied files) into this
        compound's spectra, and show it."""
        if self.read_only:
            return
        from .spectrum_paste import paste_spectrum
        self.flush()
        outcome = paste_spectrum(self, self.project, self.compound)
        if outcome is None:
            return
        first = outcome.items[0]
        name = str(first.get("file", ""))
        _last_shown[self.compound.id] = ("spectra", name,
                                         1 if name.lower().endswith(".pdf") else None)
        if not self.read_only:   # (the page may have been empty: settings greyed)
            self.settings_box.setEnabled(True)
            self.text_box.setEnabled(True)
        self._reload_spectra()
        self.saved.emit(outcome.message)

    # ------------------------------------------------------------------ the order of the files

    def _files(self) -> list[tuple[str, str]]:
        """The compound's spectrum files in print order: (field key, file name)."""
        out: list[tuple[str, str]] = []
        for sp in self.spectra:
            key = (sp.field_key, str(sp.item.get("file", "")))
            if key not in out:
                out.append(key)
        return out

    def _file_index(self) -> int:
        if self.current is None:
            return -1
        files = self._files()
        key = (self.current.field_key, str(self.current.item.get("file", "")))
        return files.index(key) if key in files else -1

    def _update_order_buttons(self) -> None:
        n, i = len(self._files()), self._file_index()
        manual = bool(S.manual_order(self.compound))
        editable = not self.read_only
        for btn in (self.up_btn, self.down_btn):
            btn.setVisible(n > 1)
        self.up_btn.setEnabled(editable and i > 0)
        self.down_btn.setEnabled(editable and 0 <= i < n - 1)
        self.auto_order_btn.setVisible(n > 1 or manual)
        self.auto_order_btn.setEnabled(editable and manual)

    def _reload_spectra(self) -> None:
        """Read the compound's spectra again (after the order changed) and show the same one."""
        self.spectra = S.spectra_of(self.project, self.compound, keep_unreadable=True)
        self.current = None
        self._fill_list()

    def move_file(self, step: int) -> None:
        """Print the shown spectrum's file (all its pages) one place earlier (-1) or later
        (+1) among this compound's spectra; the order is then kept as set by hand."""
        if self.read_only or self.current is None:
            return
        self.flush()
        order = S.moved(self.project, self.compound, self._file_index(), step)
        if order is None:
            return
        try:
            self.project.set_spectra_order(self.compound, order)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self._reload_spectra()
        self.saved.emit("Order of the spectra changed")

    def automatic_order(self) -> None:
        """Back to the automatic order (by type)."""
        if self.read_only or not S.manual_order(self.compound):
            return
        self.flush()
        try:
            self.project.set_spectra_order(self.compound, None)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self._reload_spectra()
        self.saved.emit("The spectra are in the automatic order again")

    def _mark_list(self) -> None:
        """Spectra with settings of their own are shown in bold with a dot."""
        for i, sp in enumerate(self.spectra):
            item = self.list.item(i)
            own = bool(S.overrides(sp.item, sp.key))
            text = item.text().removeprefix("● ")
            item.setText(("● " if own else "") + text)
            f = item.font()
            f.setBold(own)
            item.setFont(f)

    @staticmethod
    def _spectrum_key(sp: S.Spectrum) -> tuple:
        return (sp.field_key, sp.item.get("file"), sp.page)

    def _pages_of_file(self, sp: S.Spectrum) -> list[S.Spectrum]:
        return [s for s in self.spectra
                if s.field_key == sp.field_key and s.item.get("file") == sp.item.get("file")]

    # ------------------------------------------------------------------ showing one spectrum

    def _show_spectrum(self, row: int) -> None:
        self.flush()
        if not 0 <= row < len(self.spectra):
            return
        sp = self.spectra[row]
        self.current = sp
        _last_shown[self.compound.id] = self._spectrum_key(sp)
        several = len(self._pages_of_file(sp)) > 1
        self._several = several
        self.scope_label.setVisible(several)
        self.scope.setVisible(several)
        self._loading = True
        self.scope.setCurrentIndex(0)
        self._scope = "page"
        self._loading = False
        self._load_controls()
        name = sp.item.get("file", "")
        if sp.problem:
            self.canvas.image = QImage()
            self.canvas.message = sp.problem
        elif not sp.path.exists():
            self.canvas.image = QImage()
            self.canvas.message = (f"The file {name} is missing from the project folder. "
                                   "Add it again on the compound page.")
        else:
            self.canvas.image = page_image(sp.path, sp.page)
            self.canvas.message = ("" if not self.canvas.image.isNull() else
                                   f"{name} could not be opened (it may be damaged). Export it "
                                   "again and replace it on the compound page.")
        self._load_texts()
        self._update_canvas()
        self._update_order_buttons()

    def scope_key(self) -> str:
        """"page" or "file": what the settings now change (a file with one page: "page").
        It changes only once what was changed before has been saved (see _scope_changed),
        so a change waiting to be saved goes where it was made."""
        return self._scope if self._several else "page"

    def _layout(self) -> dict:
        return S.clean(self.current.item.get("layout"), spectrum=True) if self.current else {}

    def _page_own(self) -> dict:
        if self.current is None:
            return {}
        pages = self.current.item.get("pages")
        return S.clean(pages.get(self.current.key), positions=True) if isinstance(pages, dict) \
            else {}

    def inherited(self) -> dict:
        """What the settings would be without this scope's own: the defaults, plus the
        file's settings when changing one page."""
        if self.scope_key() == "file":
            return dict(self.defaults)
        return {**self.defaults, **self._layout()}

    def own(self) -> dict:
        """This scope's own settings (the page's include a dragged position)."""
        return self._layout() if self.scope_key() == "file" else self._page_own()

    def effective(self) -> dict:
        """The settings the spectrum prints with."""
        return S.effective(self.defaults, self.current.item, self.current.key) \
            if self.current else dict(self.defaults)

    def _load_controls(self) -> None:
        if self.current is None:
            return
        # (a dragged position belongs to its page, so it shows only when changing the page)
        dragged = self.scope_key() == "page" and self._page_own().keys() >= {"x", "y"}
        self._set_controls({**self.inherited(), **self.own()}, dragged)
        self._update_states()

    def _set_controls(self, v: dict, dragged: bool) -> None:
        """Show the settings `v` in the controls (without saving anything)."""
        self._loading = True
        self.per_page.setCurrentIndex(max(0, self.per_page.findData(v["per_page"])))
        self.orientation.setCurrentIndex(max(0, self.orientation.findData(v["orientation"])))
        self.header_line.setChecked(v["header_line"])
        self.spectrum_frame.setChecked(v["spectrum_frame"])
        self.spectrum_colour.set_colour(v["spectrum_frame_color"])
        self.spectrum_width.setValue(v["spectrum_frame_width"])
        self.auto_crop.setChecked(v["auto_crop"])
        self.struct_box.setChecked(v["struct_box"])
        index = self.corner.findData(DRAGGED)
        if dragged and index < 0:
            self.corner.addItem("Where dragged", DRAGGED)
        elif not dragged and index >= 0:
            self.corner.removeItem(index)
        self.corner.setCurrentIndex(self.corner.findData(DRAGGED) if dragged
                                    else max(0, self.corner.findData(v["corner"])))
        self.scale.setValue(round(v["struct_scale"] * 100))
        self.same_size.setChecked(v["id_size"] <= 0)
        self.id_size.setValue(v["id_size"] if v["id_size"] > 0
                              else S.STYLE_TEXT_PT.get(self.style_name, 11.0))
        self.id_bold.setChecked(v["id_bold"])
        self.struct_frame.setChecked(v["struct_frame"])
        self.struct_colour.set_colour(v["struct_frame_color"])
        self.struct_width.setValue(v["struct_frame_width"])
        self.background.set_colour(v["struct_bg"])
        self.caption.setChecked(v["caption"])
        self._loading = False

    def control_values(self) -> dict:
        """The settings as the controls now show them (every key of DEFAULTS)."""
        corner = self.corner.currentData()
        if corner == DRAGGED:   # the corner underneath, for when the box goes back to one
            corner = {**self.inherited(), **self.own()}["corner"]
        return {
            "per_page": self.per_page.currentData(),
            "orientation": self.orientation.currentData(),
            "header_line": self.header_line.isChecked(),
            "struct_box": self.struct_box.isChecked(),
            "corner": corner,
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
        }

    def _wanted_own(self) -> dict:
        """This scope's own settings as the controls say: only what differs from what it
        would inherit (so a setting put back to the default follows the default again),
        plus a dragged position (pages only) unless a corner was chosen."""
        inherited = self.inherited()
        out = {k: v for k, v in self.control_values().items() if not _same(v, inherited[k])}
        if self.scope_key() == "page" and self.corner.currentData() == DRAGGED:
            page = self._page_own()
            for k in S.POSITION_KEYS:
                if k in page:
                    out[k] = page[k]
        return out

    def row_state(self, row: str) -> str:
        """Where a row's value comes from: "default", "changed" (by what the settings now
        change), "this file" (changed for every page) or "this page" (the page's own)."""
        return self.states[row].property("state") or "default"

    def _update_states(self) -> None:
        """Each row says where its value comes from; ↺ resets what this scope changed."""
        if self.current is None:
            return
        own = self.own()
        layout = self._layout() if self.scope_key() == "page" else {}
        page = self._page_own() if self.scope_key() == "file" else {}
        for row, _label, keys in ROWS:
            state, reset = self.states[row], self.resets[row]
            if any(k in page for k in keys):
                # Changing every page, but this page has a setting of its own (which wins).
                found, style, tip = ("this page", "color: #d35400; font-style: italic;",
                                     "This page has its own setting, which wins over the one "
                                     "for every page. Choose “This page only” to change or "
                                     "reset it.")
            elif any(k in own for k in keys):
                found, style = "changed", f"color: {HIGHLIGHT}; font-weight: bold;"
                tip = ("Changed for this spectrum (↺ puts it back)" if self.scope_key() == "page"
                       else "Changed for every page of this file (↺ puts it back)")
            elif any(k in layout for k in keys):
                found, style, tip = ("this file", "font-style: italic;",
                                     "Changed for every page of this file (choose “Every page "
                                     "of this file” to change or reset it)")
            else:
                found, style, tip = "default", "", "The default, as set on the Spectra section's page"
            state.setProperty("state", found)
            state.setStyleSheet(style)
            state.setToolTip(tip)
            reset.setEnabled(not self.read_only and any(k in own for k in keys))
        ro = self.read_only
        shown = self.struct_box.isChecked()   # (the box's settings matter only when shown)
        for w in (self.corner, self.scale, self.same_size, self.id_bold, self.struct_frame,
                  self.background, self.caption):
            w.setEnabled(not ro and shown)
        self.id_size.setEnabled(not ro and shown and not self.same_size.isChecked())
        for w in (self.spectrum_colour, self.spectrum_width):
            w.setEnabled(not ro and self.spectrum_frame.isChecked())
        for w in (self.struct_colour, self.struct_width):
            w.setEnabled(not ro and shown and self.struct_frame.isChecked())
        file_scope = self.scope_key() == "file"
        one_page = len(self._pages_of_file(self.current)) == 1
        self.reset_btn.setText("Reset every page of this file to the defaults" if file_scope
                               else "Reset this spectrum to the defaults")
        self.reset_btn.setEnabled(not ro and (bool(own) or (
            not file_scope and one_page and bool(layout))))
        # Nothing of this page's own to reset, but settings for every page of the file:
        # say where they are changed.
        self.reset_hint.setVisible(not ro and not file_scope and not one_page and not own
                                   and bool(layout))

    def _update_canvas(self) -> None:
        """Draw the spectrum with the settings as they now are (also before saving)."""
        sp = self.current
        if sp is None:
            return
        if self._loading:
            v = self.effective()
        else:
            # As it will print once the controls (perhaps not saved yet) are saved.
            v = {**self.inherited(), **self._wanted_own()}
            if self.scope_key() == "file":
                v.update(self._page_own())   # (this page's own settings still win)
        canvas = self.canvas
        canvas.values = v
        canvas.slot_in = S.slot_size_in(self.style_name, v["per_page"], v["orientation"])
        canvas.crop = self.crops.box(sp.path, sp.page) if (
            v["auto_crop"] and sp.path.exists() and not sp.problem) else None
        self.crops.save()
        canvas.header = pretty_header("".join(S.header_parts(self.compound, sp.item,
                                                              self.display_id, sp.page_no)))
        canvas.caption = pretty(S.caption_text(self.compound, sp.item)) if v["caption"] else ""
        canvas.id_pt = v["id_size"] or S.STYLE_TEXT_PT.get(self.style_name, 11.0)
        canvas.update()
        where = ("dragged to " + f"{v['x'] * 100:.0f} % across, {v['y'] * 100:.0f} % down"
                 if "x" in v and "y" in v else S.CORNERS[v["corner"]].lower() + " corner")
        box = (f"structure {where}, {v['struct_scale'] * 100:.0f} %" if v["struct_box"]
               else "no structure or ID")
        self.note.setText(
            f"Drawn in its printed proportions: {v['per_page']} per page, {v['orientation']}"
            f"{', auto-cropped' if canvas.crop else ''}; {box}. The live preview shows the "
            "real pages.")

    def _load_texts(self) -> None:
        sp = self.current
        several = len(self._pages_of_file(sp)) > 1
        auto_header = "".join(S.header_parts(self.compound, {**sp.item, S.HEADER_KEY: ""},
                                             self.display_id, 1))
        auto_caption = S.automatic_caption(self.compound, sp.item)
        self.header_edit.setText(str(sp.item.get(S.HEADER_KEY, "") or ""))
        self.header_edit.setPlaceholderText(f"automatic: {pretty_header(auto_header)}")
        self.caption_edit.setText(str(sp.item.get(S.CAPTION_KEY, "") or ""))
        self.caption_edit.setPlaceholderText(f"automatic: {pretty(auto_caption)}" if auto_caption
                                             else "none (automatic captions are for NMR "
                                                  "spectra)")
        note = "Also on the compound page. Leave empty for the automatic text."
        if several:
            note = "For every page of this file. " + note
        if not self.effective()["caption"]:
            note += " The caption is printed only when “Caption under them” is on."
        self.text_note.setText(note)

    # ------------------------------------------------------------------ changing

    def _scope_changed(self, *_args) -> None:
        if self._loading:
            return
        self.flush()   # (saved for the scope it was made in, before switching)
        self._scope = self.scope.currentData() or "page"
        self._load_controls()
        self._update_canvas()

    def _changed(self, *_args) -> None:
        if self._loading or self.current is None:
            return
        if self.sender() is self.corner and self.corner.currentData() != DRAGGED:
            index = self.corner.findData(DRAGGED)   # a corner chosen: no longer dragged
            if index >= 0:
                self._loading = True
                self.corner.removeItem(index)
                self._loading = False
        self._update_states()
        self._update_canvas()
        if not self.read_only:
            self._timer.start()

    def _write(self, values: dict, scope: str | None = None) -> bool:
        """Store `values` as the chosen scope's own settings."""
        sp = self.current
        scope = scope or self.scope_key()
        try:
            self.project.set_spectrum_overrides(self.compound, sp.field_key,
                                                sp.item.get("file", ""), values,
                                                page=sp.key if scope == "page" else None)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return False
        return True

    def _save(self) -> None:
        self._timer.stop()
        if self.read_only or self.current is None:
            return
        wanted = self._wanted_own()
        if wanted == self.own():
            return
        if self._write(wanted):
            self._after_save()

    def _after_save(self, message: str = "Saved") -> None:
        self._update_states()
        self._mark_list()
        self._update_canvas()
        self.saved.emit(message)

    def flush(self) -> None:
        if self._timer.isActive():
            self._save()
        if self.current is not None and not self.read_only:
            for key, edit in ((S.HEADER_KEY, self.header_edit),
                              (S.CAPTION_KEY, self.caption_edit)):
                if edit.text().strip() != str(self.current.item.get(key, "") or "").strip():
                    self._commit_text(key)

    def _box_moved(self, x: float, y: float) -> None:
        """The box was dragged: its new centre is this page's own (whatever the scope)."""
        if self.read_only or self.current is None:
            return
        self.flush()
        page = self._page_own()
        page.update({"x": x, "y": y})
        if self._write(page, "page"):
            self._load_controls()
            self._after_save("Structure moved")

    def _box_resized(self, scale: float) -> None:
        """Resized with a handle: the same as typing the size in "Structure size". When
        changing every page of the file but this page has a size of its own (which wins),
        the handle changes this page's own size, so the box drawn keeps its new size."""
        if self.read_only or self.current is None:
            return
        if self.scope_key() == "file" and "struct_scale" in self._page_own():
            self.flush()
            page = self._page_own()
            page["struct_scale"] = scale
            if self._write(page, "page"):
                self._load_controls()
                self._after_save("Structure resized (for this page)")
            return
        self._loading = True
        self.scale.setValue(round(scale * 100))
        self._loading = False
        self._timer.stop()
        wanted = self._wanted_own()
        if self._write(wanted):
            self._after_save("Structure resized")

    def reset_row(self, row: str) -> None:
        """One setting back to what it inherits (the default, or the file's)."""
        if self.read_only or self.current is None:
            return
        self.flush()
        keys = next(k for r, _l, k in ROWS if r == row)
        own = {k: v for k, v in self.own().items() if k not in keys}
        if self._write(own):
            self._load_controls()
            self._after_save()

    def reset_spectrum(self) -> None:
        """Everything this spectrum changed back to the defaults: this page (and, for a file
        with one page, the file's settings too), or every page of the file."""
        if self.read_only or self.current is None:
            return
        self._timer.stop()
        sp = self.current
        pages = self._pages_of_file(sp)
        if self.scope_key() == "file":
            if QMessageBox.question(
                    self, "Reset every page",
                    f"Remove the settings changed for every page of {sp.item.get('file')}, "
                    "including where their structures were dragged?"
            ) != QMessageBox.StandardButton.Yes:
                return
            ok = self._write({}, "file") and all(
                self._write_page(p, {}) for p in pages)
        else:
            ok = self._write({}, "page")
            if ok and len(pages) == 1:
                ok = self._write({}, "file")
        if ok:
            self._load_controls()
            self._after_save("This spectrum now follows the defaults")

    def _write_page(self, sp: S.Spectrum, values: dict) -> bool:
        try:
            self.project.set_spectrum_overrides(self.compound, sp.field_key,
                                                sp.item.get("file", ""), values, page=sp.key)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return False
        return True

    def _commit_text(self, key: str) -> None:
        sp = self.current
        if self.read_only or sp is None:
            return
        edit = self.header_edit if key == S.HEADER_KEY else self.caption_edit
        text = edit.text().strip()
        if text == str(sp.item.get(key, "") or "").strip():
            return
        try:
            self.project.set_spectrum_text(self.compound, sp.field_key, sp.item.get("file", ""),
                                           key, text)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self._update_canvas()
        self.saved.emit("Saved")

    def select_spectrum(self, row: int) -> None:
        self.list.setCurrentRow(row)
