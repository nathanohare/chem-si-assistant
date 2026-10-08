"""The scheme editor on a scheme page (SPEC §13n.4), and its layout settings.

A strip laid out like the printed scheme:

    [starting material] + [starting material] → [product] + [product] → [product] …
                                            (conditions above and under each arrow)

Each box is a click-to-select paste target (blue outline, Ctrl+V, right-click), like the
Structure field: a ChemDraw copy gives its picture and structure, SMILES or a MOL file the
structure (drawn by the program). A product can be marked "Compound": it then gets a
sub-compound page (ID, data page) whose structure and picture it shows. Starting materials
(and products that are not compounds) can have an ID label: typed, or a mention of a
compound elsewhere. IDs are drawn under the structures and can be dragged.

The conditions boxes are rich text (bold, italic, sub/superscript, formula toggle,
symbols, mentions, citations; no tables or figures); the box above the arrow also takes
pasted structures, shown small in the text. Everything is saved at once (Edit ▸ Undo
undoes it); typing is saved after a short pause.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetricsF, QPainter, QPalette, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import chem
from ..core import drawing_scale
from ..core import scheme_editor as se
from ..core.project import PICTURE_FILE_EXTENSIONS, ProjectError
from .rich_text import (ALIGN_SHOWN, MARKUP_MIME, PickerDialog, RichTextEditor, _is_clipboard,
                        alignment_icon)
from .structure_widgets import (HIGHLIGHT, STRUCTURE_FILE_EXTENSIONS, PasteTargetMixin,
                                PictureScaleDialog, clipboard_picture_for,
                                clipboard_structure_text, drawing_settings, mol_pixmap,
                                picture_pixmap, paste_key)

SCREEN_PX_PER_IN = 90.0      # structures are shown at about their printed size
MAX_BOX = QSize(260, 200)    # … but no bigger than this on screen
MIN_BOX = QSize(110, 70)
INLINE_MAX = QSizeF(140, 90)
AUTOSAVE_MS = 800
STEP_WIDTH = 250
ALIGN_WORDS = {"left": "Left", "center": "Centre", "right": "Right"}


# ---------------------------------------------------------------------- sizes

def natural_in(path: Path) -> tuple[float, float] | None:
    """A picture's natural size in inches (SVG/PDF size, PNG at its resolution)."""
    from ..compile.document import pdf_size_in, svg_size_in
    from ..compile.qt_images import image_info
    ext = path.suffix.lower()
    try:
        if ext == ".svg":
            return svg_size_in(path.read_text(encoding="utf-8", errors="replace"))
        if ext == ".pdf":
            return pdf_size_in(path)
    except OSError:
        return None
    info = image_info(path)
    return (info[0] / info[2], info[1] / info[2]) if info else None


def print_size_in(project, picture: Path | None, info: dict, mol,
                  factor: float = 1.0) -> tuple[float, float] | None:
    """About how big a structure prints (inches): its picture at the project's bond
    length (pictures mode, or no structure), else the program's drawing."""
    use_picture = picture is not None and (
        project.settings.get("compile_structures", "pictures") == "pictures" or mol is None)
    if use_picture:
        size = natural_in(picture)
        if size:
            f = se.box_print_factor(project.settings, info, picture) * factor
            return size[0] * f, size[1] * f
    if mol is not None:
        from ..compile.document import drawn_structure
        _text, size = drawn_structure(mol, drawing_settings(project))
        if size:
            return size[0] * factor, size[1] * factor
    return None


def structure_pixmap(project, picture: Path | None, mol, size: QSize, dpr: float) -> QPixmap:
    use_picture = picture is not None and (
        project.settings.get("compile_structures", "pictures") == "pictures" or mol is None)
    if use_picture:
        pixmap = picture_pixmap(picture, size, dpr)
        if not pixmap.isNull():
            return pixmap
    if mol is not None:
        return mol_pixmap(mol, size, drawing_settings(project), dpr)
    return QPixmap()


def choose_compound(parent: QWidget, project) -> str | None:
    """Pick any compound in the project (for a starting material's ID label)."""
    report = project.id_report()
    items: list[tuple[str | None, str]] = []
    for section in project.data_sections():
        if section.compounds:
            items.append((None, section.name))
            items += [(c.id, f"{report.display_id(c.id)}   {c.name or '(unnamed)'}")
                      for c in section.compounds]
    dlg = PickerDialog("ID label: mention a compound", items, parent,
                       hint="The label is printed as the compound's current ID, so it stays "
                            "right when compounds are renumbered. It gets no data page here.")
    if dlg.exec() and dlg.values():
        return dlg.values()[0]
    return None


# ---------------------------------------------------------------------- a structure box

class BoxCanvas(QWidget):
    """A box's structure at about its printed size, with the ID (and yield) under it. The
    ID can be dragged; where it goes is saved in pt from its usual place."""

    label_moved = Signal(float, float)   # dx, dy in pt

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.pixmap = QPixmap()
        self.struct_size = QSize(0, 0)
        self.label = ""
        self.label_bold = True
        self.yield_text = ""
        self.placeholder = ""
        self.offset = QPointF(0, 0)      # pt
        self.px_per_pt = SCREEN_PX_PER_IN / 72
        self.editable = True
        self.active = False
        self.highlight_label = False
        self.row_height = 0       # px: the tallest structure of the strip (IDs lined up)
        self._drag: QPointF | None = None
        self._start = QPointF(0, 0)
        self.setMouseTracking(True)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.update()

    def _fonts(self) -> tuple[QFont, QFont]:
        f = QFont(self.font())
        f.setBold(self.label_bold)
        y = QFont(self.font())
        y.setPointSizeF(max(7.0, self.font().pointSizeF() * 0.9))
        return f, y

    def _label_size(self) -> QSizeF:
        lf, yf = self._fonts()
        w = h = 0.0
        if self.label:
            m = QFontMetricsF(lf)
            w, h = m.horizontalAdvance(self.label) + 8, m.height()
        if self.yield_text:
            m = QFontMetricsF(yf)
            w = max(w, m.horizontalAdvance(self.yield_text) + 8)
            h += m.height()
        return QSizeF(w, h)

    def update_size(self) -> None:
        ls = self._label_size()
        s = self.struct_size if not self.pixmap.isNull() else MIN_BOX
        w = max(s.width(), ls.width(), MIN_BOX.width()) + 12
        h = self._area_height() + ls.height() + 18
        h += max(0.0, self.offset.y() * self.px_per_pt)
        self.setFixedSize(int(w), int(h))

    def _area_height(self) -> float:
        """The structure's area: as tall as the tallest structure of the strip when the
        IDs are lined up (the structure centred in it, as on the arrow's line)."""
        own = max(self.struct_size.height(), 40) if not self.pixmap.isNull() else \
            MIN_BOX.height()
        return max(own, self.row_height)

    def _struct_rect(self) -> QRectF:
        area = self._area_height()
        if self.pixmap.isNull():
            return QRectF(6, 6, self.width() - 12, area)
        s = self.struct_size
        h = max(s.height(), 40)
        return QRectF((self.width() - s.width()) / 2, 6 + (area - h) / 2, s.width(), h)

    def _label_top(self) -> float:
        return 6 + self._area_height()

    def label_rect(self) -> QRectF:
        ls = self._label_size()
        r = self._struct_rect()
        x = r.center().x() - ls.width() / 2 + self.offset.x() * self.px_per_pt
        y = (self._label_top() if self.row_height else r.bottom()) + 3 \
            + self.offset.y() * self.px_per_pt
        return QRectF(x, y, ls.width(), ls.height())

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        p.setPen(QPen(QColor(HIGHLIGHT) if self.active else QColor("#bbb"),
                      3 if self.active else 1))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        r = self._struct_rect()
        if self.pixmap.isNull():
            p.setPen(QColor("#777"))
            p.drawText(self.rect().adjusted(8, 4, -8, -4),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
            return
        p.drawPixmap(r.toRect(), self.pixmap)
        if not self.label and not self.yield_text:
            return
        lr = self.label_rect()   # (lined up with the strip's other IDs: Layout)
        lf, yf = self._fonts()
        if self.editable and self.label:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 236, 179, 230) if self.highlight_label
                       else QColor(232, 240, 254, 200))
            p.drawRoundedRect(lr, 3, 3)
        y = lr.top()
        if self.label:
            p.setFont(lf)
            p.setPen(QColor("#1a3d7c") if self.editable else QColor("black"))
            h = QFontMetricsF(lf).height()
            p.drawText(QRectF(lr.left(), y, lr.width(), h), Qt.AlignmentFlag.AlignCenter,
                       self.label)
            y += h
        if self.yield_text:
            p.setFont(yf)
            p.setPen(QColor("#333"))
            p.drawText(QRectF(lr.left(), y, lr.width(), QFontMetricsF(yf).height()),
                       Qt.AlignmentFlag.AlignCenter, self.yield_text)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (self.editable and event.button() == Qt.MouseButton.LeftButton
                and (self.label or self.yield_text)
                and self.label_rect().contains(event.position())):
            self._drag = event.position()
            self._start = QPointF(self.offset)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            d = (event.position() - self._drag) / self.px_per_pt
            limit = 85.0   # pt (3 cm) either way
            self.offset = QPointF(min(max(self._start.x() + d.x(), -limit), limit),
                                  min(max(self._start.y() + d.y(), -limit), limit))
            self.update()
            return
        over = (self.editable and (self.label or self.yield_text)
                and self.label_rect().contains(event.position()))
        self.setCursor(Qt.CursorShape.OpenHandCursor if over else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.label_moved.emit(self.offset.x(), self.offset.y())
        super().mouseReleaseEvent(event)


class BoxWidget(PasteTargetMixin, QFrame):
    """One starting material or product: its structure, ID label and yield."""

    def __init__(self, editor: "SchemeEditorWidget", box: dict, where: str):
        super().__init__(editor.strip)
        self.editor = editor
        self.project = editor.project
        self.scheme = editor.scheme
        self.box_id = box["id"]
        self.where = where
        self._saving = False      # (a typed ID label or yield is being saved)
        self.setFrameShape(QFrame.Shape.NoFrame)
        locked = editor.locked or self.project.read_only
        starting = where == se.START

        title = QLabel("Starting material" if starting else "Product")
        title.setStyleSheet("color: palette(placeholder-text);")
        self.remove_btn = QToolButton()
        self.remove_btn.setText("✕")
        self.remove_btn.setAutoRaise(True)
        self.remove_btn.setToolTip("Remove this box from the scheme")
        self.remove_btn.clicked.connect(self.remove)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(title, 1)
        head.addWidget(self.remove_btn)

        self.canvas = BoxCanvas()
        self.canvas.label_moved.connect(self._label_moved)
        hint = self._init_paste_target(self.canvas, [], scope=self)
        # (one short line, the same in every box, so the structures and IDs line up)
        hint.setWordWrap(False)
        hint.setMaximumWidth(MAX_BOX.width())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addLayout(head)
        layout.addWidget(hint)
        layout.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignHCenter)

        # Products: "Compound" (an ID from the ID table and a data page).
        self.compound_box = None
        if not starting:
            self.compound_box = QCheckBox("Compound (gets an ID and data page)")
            self.compound_box.setToolTip(
                "Make this product a compound of the scheme page: it gets the next ID from "
                "the section's ID table and its own data page (a tab below). Its structure "
                "is shown and printed from that page.")
            self.compound_box.toggled.connect(self._compound_toggled)
            layout.addWidget(self.compound_box)
        # Boxes that are not compounds: an ID label (typed, or a mention of a compound).
        self.label_row = QWidget()
        row = QHBoxLayout(self.label_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("ID label:"))
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("none")
        self.label_edit.setToolTip(
            "A label printed under the structure, e.g. “1” or “S3” (no data page). Use @ to "
            "mention a compound elsewhere in the project: it then follows renumbering.")
        self.label_edit.editingFinished.connect(self._label_typed)
        self.mention_btn = QToolButton()
        self.mention_btn.setText("@")
        self.mention_btn.setToolTip("Use the ID of a compound elsewhere in the project")
        self.mention_btn.clicked.connect(self._mention)
        row.addWidget(self.label_edit, 1)
        row.addWidget(self.mention_btn)
        layout.addWidget(self.label_row)
        # Products: the yield (a compound's comes from its Yield field unless typed here).
        self.yield_row = QWidget()
        yrow = QHBoxLayout(self.yield_row)
        yrow.setContentsMargins(0, 0, 0, 0)
        yrow.addWidget(QLabel("Yield:"))
        self.yield_edit = QLineEdit()
        self.yield_edit.editingFinished.connect(self._yield_typed)
        yrow.addWidget(self.yield_edit, 1)
        layout.addWidget(self.yield_row)
        self.yield_row.setVisible(not starting)
        # The last product of a step: "+ Product" under it (SPEC §13q.6a).
        self.add_product_btn = None
        if not starting and se.find_step(self.scheme, where)["products"][-1]["id"] == self.box_id:
            self.add_product_btn = QPushButton("+ Product", self)
            self.add_product_btn.setToolTip("Add another product (or by-product) to this step")
            self.add_product_btn.clicked.connect(lambda: editor.add_box(where))
            self.add_product_btn.setVisible(not locked)
            layout.addWidget(self.add_product_btn, 0, Qt.AlignmentFlag.AlignLeft)
        for w in (self.remove_btn, self.label_edit, self.mention_btn, self.yield_edit):
            w.setEnabled(not locked)
        if self.compound_box is not None:
            self.compound_box.setEnabled(not locked)
        self.remove_btn.setVisible(not locked)
        self.set_paste_locked(locked)
        self.refresh()

    # ---- data

    @property
    def box(self) -> dict:
        return se.find_box(self.scheme, self.box_id)[0]

    def refresh(self, redraw: bool = True) -> None:
        """Show the box again (`redraw`: its structure too, else only the ID and yield)."""
        box = self.box
        view = se.box_view(self.project, self.scheme, box)
        layout = self.editor.layout_values()
        scale = float(layout["scale_pct"]) / 100.0
        size_in = (print_size_in(self.project, view.picture, view.scale_info, view.mol, scale)
                   if redraw else None)
        c = self.canvas
        c.px_per_pt = SCREEN_PX_PER_IN / 72 * scale
        if not redraw:
            pass
        elif size_in:
            size = QSize(round(size_in[0] * SCREEN_PX_PER_IN),
                         round(size_in[1] * SCREEN_PX_PER_IN))
            size = size.scaled(MAX_BOX, Qt.AspectRatioMode.KeepAspectRatio) \
                if size.width() > MAX_BOX.width() or size.height() > MAX_BOX.height() else size
            c.struct_size = size
            c.pixmap = structure_pixmap(self.project, view.picture, view.mol, size,
                                        self.devicePixelRatioF())
        else:
            c.pixmap = QPixmap()
        locked = self.editor.locked or self.project.read_only
        c.placeholder = "" if locked else f"No structure — {paste_key()}"
        c.setToolTip("" if locked or not view.empty else
                     f"Copy the structure in ChemDraw, click this box and press {paste_key()}"
                     " — or right-click for SMILES, a MOL file or a picture file.")
        compound = view.compound
        report = self.project.id_report()
        if compound is not None:
            c.label = report.display_id(compound.id)
            c.label_bold = True
        else:
            from ..core import links
            text = str(box.get("label") or "")
            c.label = links.plain_text(self.project, text) if text else ""
            c.label_bold = True
        place = layout["yields"]
        y = se.box_yield(self.project, self.scheme, box) if self.where != se.START else ""
        c.yield_text = y if place == "product" else ""
        dx, dy = se.label_offset(box)
        c.offset = QPointF(dx, dy)
        c.editable = not (self.editor.locked or self.project.read_only)
        c.update_size()
        c.update()
        # The controls under it.
        if self.compound_box is not None:
            self.compound_box.blockSignals(True)
            self.compound_box.setChecked(compound is not None)
            self.compound_box.blockSignals(False)
        self.label_row.setVisible(compound is None)
        text = str(box.get("label") or "")
        mention = _single_mention(text)
        self.label_edit.blockSignals(True)
        if mention:
            self.label_edit.setText(c.label)
            self.label_edit.setReadOnly(True)
            self.label_edit.setStyleSheet("QLineEdit { color: #1a4f8b; font-weight: bold; }")
            self.label_edit.setToolTip("The ID of a compound elsewhere in the project (it "
                                       "follows renumbering). Clear it with right-click ▸ "
                                       "Remove the ID label.")
        else:
            self.label_edit.setText(text)
            self.label_edit.setReadOnly(self.editor.locked or self.project.read_only)
            self.label_edit.setStyleSheet("")
        self.label_edit.blockSignals(False)
        auto = se.compound_yield(compound)
        self.yield_edit.blockSignals(True)
        self.yield_edit.setText(str(box.get("yield", "")) if se.yield_is_typed(box) else "")
        self.yield_edit.setPlaceholderText(
            f"{auto} (from its Yield field)" if auto else
            "none" if se.yield_is_typed(box) or compound is None else "from its Yield field")
        self.yield_edit.setToolTip(
            "The yield printed with this product (Layout: under the product, under the "
            "step's conditions, or not printed). A compound's comes from its Yield field "
            "unless you type one here; right-click for “automatic” or “no yield”.")
        self.yield_edit.blockSignals(False)

    # ---- pasting

    def _set_paste_active(self, active: bool) -> None:
        super()._set_paste_active(active)
        if not self.project.read_only and not active:
            self.paste_hint.setText(f"Click, then {paste_key()} — or right-click")

    def _show_paste_menu(self, pos) -> None:
        if self.project.read_only:
            return
        self.setFocus()
        menu = QMenu(self)
        self.fill_menu(menu)
        menu.exec(self.mapToGlobal(pos))

    def fill_menu(self, menu: QMenu) -> None:
        box = self.box
        compound = se.box_compound(self.project, self.scheme, box)
        if self.editor.locked:
            menu.addAction("Edit scheme (unlock it)", lambda: self.editor.set_locked(False))
            return
        menu.addAction(f"Paste structure ({paste_key()})", self.paste_from_clipboard)
        menu.addAction("Paste picture only (e.g. a screenshot)", self.paste_picture)
        menu.addAction("Type SMILES…", self.type_smiles)
        menu.addAction("Open MOL/SDF file…", self.open_file)
        menu.addAction("Use own picture file…", self.choose_picture)
        view = se.box_view(self.project, self.scheme, box)
        size = menu.addAction("Printed size of the picture…", self.picture_size)
        size.setEnabled(view.picture is not None)
        clear = menu.addAction("Clear structure", self.clear)
        clear.setEnabled(compound is None and not view.empty)
        if compound is not None:
            clear.setToolTip("Change the compound's structure on its page")
        menu.addSeparator()
        reset = menu.addAction("Reset the ID's position", lambda: self.set_offset(0.0, 0.0))
        reset.setEnabled(any(se.label_offset(box)))
        if compound is None and str(box.get("label") or ""):
            menu.addAction("Remove the ID label", lambda: self.set_label(""))
        if self.where != se.START:
            menu.addAction("Yield: automatic (a compound's Yield field)",
                           lambda: self.set_yield(None))
            menu.addAction("Yield: none for this product", lambda: self.set_yield(""))
        menu.addSeparator()
        menu.addAction("Move left", lambda: self.editor.move_box(self.box_id, -1))
        menu.addAction("Move right", lambda: self.editor.move_box(self.box_id, 1))
        if self.where != se.START:
            free = se.unlinked_children(self.scheme)
            if compound is None and free:
                sub = menu.addMenu("Show a compound of this page")
                report = self.project.id_report()
                for c in free:
                    sub.addAction(f"{report.display_id(c.id)}  {c.name or '(unnamed)'}",
                                  lambda cid=c.id: self.editor.link_existing(self.box_id, cid))
        menu.addSeparator()
        menu.addAction("Remove this box…", self.remove)

    def paste_from_clipboard(self) -> None:
        """Ctrl+V: the copied structure (with ChemDraw's picture of it), or a picture."""
        text = clipboard_structure_text()
        if text.strip() and _parses(text):
            picture = clipboard_picture_for(self.project)
            self.editor.act("Paste a structure into the scheme",
                            lambda: se.set_box_structure(self.project, self.scheme,
                                                         self.box_id, text, picture))
        else:
            self.paste_picture()

    def paste_picture(self) -> None:
        picture = clipboard_picture_for(self.project)
        if picture is None:
            self.editor.say("⚠ The clipboard has no structure or picture. Copy the structure "
                            "in ChemDraw (Ctrl+C) first, or type SMILES (right-click).")
            return
        self.editor.act("Paste a picture into the scheme",
                        lambda: se.set_box_picture(self.project, self.scheme, self.box_id,
                                                   data=picture.data, ext=picture.ext,
                                                   vector=picture.source,
                                                   vector_ext=picture.source_ext))

    def type_smiles(self) -> None:
        text, ok = QInputDialog.getText(self, "Structure", "SMILES:")
        if ok and text.strip():
            self.set_structure_text(text.strip())

    def set_structure_text(self, text: str) -> None:
        self.editor.act("Change a structure of the scheme",
                        lambda: se.set_box_structure(self.project, self.scheme, self.box_id,
                                                     text))

    def open_file(self) -> None:
        patterns = " ".join(f"*{e}" for e in STRUCTURE_FILE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Open a structure file", "",
                                              f"Structure files ({patterns})")
        if path:
            try:
                text = Path(path).read_bytes().decode("utf-8", errors="replace")
            except OSError as e:
                self.editor.say(f"⚠ Could not read {Path(path).name}: {e}")
                return
            self.set_structure_text(text)

    def choose_picture(self) -> None:
        patterns = " ".join(f"*{e}" for e in PICTURE_FILE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture", "",
                                              f"Images ({patterns})")
        if path:
            self.editor.act("Use a picture file in the scheme",
                            lambda: se.set_box_picture(self.project, self.scheme, self.box_id,
                                                       source=Path(path)))

    def picture_size(self) -> None:
        box = self.box
        compound = se.box_compound(self.project, self.scheme, box)
        if compound is not None:
            measured, own = self.project.structure_picture_bond(compound)
        else:
            view = se.box_view(self.project, self.scheme, box)
            measured = drawing_scale.picture_bond_pt(
                {k: v for k, v in box.items() if k != drawing_scale.OVERRIDE_KEY},
                view.picture)
            own = box.get(drawing_scale.OVERRIDE_KEY)
            own = float(own) if isinstance(own, (int, float)) else None
        dialog = PictureScaleDialog(self, self.project, compound, bond=(measured, own))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        choice = dialog.choice()
        if compound is not None:
            self.editor.act("Change the printed size of a picture",
                            lambda: self.project.set_structure_picture_bond(compound, choice))
        else:
            self.editor.act("Change the printed size of a picture",
                            lambda: se.set_box_bond(self.project, self.scheme, self.box_id,
                                                    choice))

    def clear(self) -> None:
        self.editor.act("Clear a structure of the scheme",
                        lambda: se.clear_box(self.project, self.scheme, self.box_id))

    # ---- label, yield, compound

    def _label_typed(self) -> None:
        # (saving calls the editor's flush(), which would save this field again while its
        # focus is still in it: once is enough)
        if self.label_edit.isReadOnly() or self._saving:
            return
        try:
            text = self.label_edit.text().strip()
            stored = str(self.box.get("label") or "")
        except KeyError:   # (the box has gone, e.g. undone)
            return
        if text != stored:
            # (typed "@": the stored text is escaped so it prints as typed)
            from ..core.markup import Paragraph, Run, serialize
            self._saving = True
            try:
                self.set_label(serialize([Paragraph([Run(text)])]) if text else "")
            finally:
                self._saving = False

    def _mention(self) -> None:
        cid = choose_compound(self, self.project)
        if cid:
            self.set_label("@{" + cid + "}")

    def set_label(self, label: str) -> None:
        self.editor.act("Change an ID label of the scheme",
                        lambda: se.set_box_label(self.project, self.scheme, self.box_id,
                                                 label))

    def _yield_typed(self) -> None:
        if self._saving:
            return
        try:
            text = self.yield_edit.text().strip()
            box = self.box
        except KeyError:
            return
        if text == (str(box.get("yield", "")) if se.yield_is_typed(box) else ""):
            return
        self._saving = True
        try:
            self.set_yield(text if text else None)
        finally:
            self._saving = False

    def set_yield(self, text: str | None) -> None:
        self.editor.act("Change a yield on the scheme",
                        lambda: se.set_box_yield(self.project, self.scheme, self.box_id, text))

    def _label_moved(self, dx: float, dy: float) -> None:
        self.set_offset(dx, dy)

    def set_offset(self, dx: float, dy: float) -> None:
        self.editor.act("Move an ID on the scheme",
                        lambda: se.set_label_offset(self.project, self.scheme, self.box_id,
                                                    dx, dy), message="ID position saved")

    def _compound_toggled(self, on: bool) -> None:
        if on:
            self.editor.mark_compound(self.box_id)
        else:
            self.editor.unmark_compound(self.box_id)

    def remove(self) -> None:
        self.editor.remove_box(self.box_id)


def _single_mention(text: str) -> str:
    """The compound ID if a label is just one mention, else ""."""
    from ..core.markup import parse
    paragraphs = parse(text or "")
    runs = [r for p in paragraphs for r in p.runs]
    return runs[0].mention if len(runs) == 1 and runs[0].mention else ""


def _parses(text: str) -> bool:
    try:
        chem.parse_structure(text)
        return True
    except chem.ChemError:
        return False


# ---------------------------------------------------------------------- a step

class ArrowWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        p.setPen(QPen(QColor("black"), 1.4))
        p.drawLine(QPointF(4, y), QPointF(self.width() - 10, y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("black"))
        w = self.width() - 3
        p.drawPolygon(QPolygonF([QPointF(w, y), QPointF(w - 11, y - 4.5),
                                 QPointF(w - 8.5, y), QPointF(w - 11, y + 4.5)]))


class InlineStructures:
    """Structures in the conditions above an arrow (@struct{…}): their pictures for the
    text box, and pasting new ones (rich_text.RichTextEdit.inline_structures)."""

    def __init__(self, editor: "SchemeEditorWidget"):
        self.editor = editor

    def _info(self, sid: str):
        scheme = self.editor.scheme
        entry = se.inline_entry(scheme, sid)
        return (se.inline_path(scheme, sid), entry,
                se.read_mol(se.path_of(scheme, entry.get("structure"))))

    def size(self, sid: str) -> QSizeF | None:
        picture, entry, mol = self._info(sid)
        lay = self.editor.layout_values()
        factor = float(lay["cond_structure_pct"]) / 100 * float(lay["scale_pct"]) / 100
        size = print_size_in(self.editor.project, picture, entry, mol, factor)
        if not size:
            return None
        s = QSizeF(size[0] * SCREEN_PX_PER_IN, size[1] * SCREEN_PX_PER_IN)
        if s.width() > INLINE_MAX.width() or s.height() > INLINE_MAX.height():
            s = s.scaled(INLINE_MAX, Qt.AspectRatioMode.KeepAspectRatio)
        return s

    def pixmap(self, sid: str, size: QSizeF, dpr: float) -> QPixmap | None:
        picture, _entry, mol = self._info(sid)
        px = QSize(max(1, round(size.width())), max(1, round(size.height())))
        pixmap = structure_pixmap(self.editor.project, picture, mol, px, dpr)
        return pixmap if not pixmap.isNull() else None

    def has_picture(self, sid: str) -> bool:
        return se.inline_path(self.editor.scheme, sid) is not None

    def picture_size(self, sid: str, parent: QWidget | None = None) -> None:
        """Right-click ▸ Printed size of the picture… on a structure in the conditions:
        the same choice as for boxes and compounds (§2.7), kept with it in scheme.json."""
        editor = self.editor
        picture = se.inline_path(editor.scheme, sid)
        if picture is None or editor.project.read_only:
            return
        entry = se.inline_entry(editor.scheme, sid)
        measured = drawing_scale.picture_bond_pt(
            {k: v for k, v in entry.items() if k != drawing_scale.OVERRIDE_KEY}, picture)
        own = entry.get(drawing_scale.OVERRIDE_KEY)
        own = float(own) if isinstance(own, (int, float)) and not isinstance(own, bool) \
            else None
        dialog = PictureScaleDialog(parent or editor, editor.project, None,
                                    bond=(measured, own))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        choice = dialog.choice()
        editor.act("Change the printed size of a structure in the conditions of",
                   lambda: se.set_inline_bond(editor.project, editor.scheme, sid, choice))

    def export(self, sid: str) -> dict | None:
        """Its files, for copying conditions into another scheme (rich_text copy)."""
        return se.export_inline(self.editor.scheme, sid)

    def adopt(self, sid: str, info: dict | None) -> str | None:
        """A structure in pasted conditions: kept if it is this scheme's, else its files
        are copied into this scheme (a new ID); None: left out."""
        editor = self.editor
        if editor.project.read_only:
            return None
        try:
            with editor.project.undo_step("Paste conditions with a structure",
                                          then_merge=True):
                return se.paste_inline(editor.project, editor.scheme, sid, info)
        except (ProjectError, OSError) as e:
            editor.say(f"⚠ A structure in the pasted text could not be copied: {e}")
        return None

    def wants(self, source) -> bool:
        """A copied structure or picture (not text): it goes in as a small structure."""
        if source.hasFormat(MARKUP_MIME):
            return False
        formats = [f.lower() for f in source.formats()]
        if any("chemdraw" in f or "cdx" in f or "mdlct" in f or "mdl-molfile" in f
               for f in formats):
            return True
        if source.hasText() and source.text().strip():
            return chem.looks_like_molblock(source.text())
        if source.hasImage():
            return True
        if formats and not _is_clipboard(source):
            return False
        from .clipboard_image import clipboard_has_vector
        return clipboard_has_vector()

    def paste(self, source) -> str | None:
        editor = self.editor
        project = editor.project
        if _is_clipboard(source) or not source.formats():
            picture = clipboard_picture_for(project)
            text = clipboard_structure_text()
        else:
            picture = None
            text = source.text() if source.hasText() else ""
            if source.hasImage():
                from .clipboard_image import PastedPicture, image_to_png
                from PySide6.QtGui import QImage
                image = QImage(source.imageData())
                if not image.isNull():
                    picture = PastedPicture(image_to_png(image))
        try:
            # (one Edit ▸ Undo step with the text's autosave that follows)
            with project.undo_step("Add a structure to the conditions", then_merge=True):
                return se.add_inline_structure(project, editor.scheme, text, picture)
        except se.SchemeEditorError as e:
            editor.say(f"⚠ {e}")
        except (ProjectError, OSError) as e:
            QMessageBox.warning(editor, "Could not add the structure", str(e))
        return None


class ConditionsBox(QWidget):
    """One conditions text (above or under an arrow) with its own alignment button."""

    changed = Signal()

    def __init__(self, step_widget: "StepWidget", which: str):
        super().__init__(step_widget)
        self.step_widget = step_widget
        editor = step_widget.editor
        self.which = which
        self.text = RichTextEditor(self, headings=False, height=58)
        self.text.edit.setPlaceholderText(
            "Conditions above the arrow (reagents, catalyst; paste a structure here too)"
            if which == "top" else "Conditions under the arrow (solvent, temperature, time)")
        if which == "top":
            self.text.edit.inline_structures = editor.inline
            act = QAction("Structure", self.text)
            act.setToolTip("Put the structure copied in ChemDraw into this text (or just "
                           f"paste it here with {paste_key()}). It is printed small, at the "
                           "size set in Layout (structure size in conditions).")
            act.triggered.connect(self.paste_structure)
            self.text.toolbar.addAction(act)
            self.structure_act = act
        self.align_btn = QToolButton(self)
        self.align_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.align_btn.setAutoRaise(True)
        menu = QMenu(self.align_btn)
        menu.addAction("As in the scheme's Layout", lambda: self.set_align(""))
        for name, word in ALIGN_WORDS.items():
            menu.addAction(word, lambda n=name: self.set_align(n))
        self.align_btn.setMenu(menu)
        self.text.toolbar.insertWidget(self.text.toolbar.actions()[0], self.align_btn)
        self.text.set_markup(str(step_widget.step.get(which) or ""))
        self.text.set_read_only(editor.locked or editor.project.read_only)
        self.align_btn.setEnabled(not (editor.locked or editor.project.read_only))
        self.text.changed.connect(self.changed)
        self.show_align()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.text)

    def show_align(self) -> None:
        editor = self.step_widget.editor
        step = self.step_widget.step
        name = se.conditions_align(editor.project.settings, editor.scheme, step, self.which)
        own = step.get("top_align" if self.which == "top" else "bottom_align") in se.ALIGNS
        self.align_btn.setIcon(alignment_icon(name, self.palette().color(
            QPalette.ColorRole.ButtonText)))
        self.align_btn.setToolTip(
            f"Alignment of these conditions: {ALIGN_SHOWN[name]}"
            + (" (this box's own)" if own else " (as in the scheme's Layout)"))
        flags = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
                 "right": Qt.AlignmentFlag.AlignRight}[name]
        option = self.text.edit.document().defaultTextOption()
        option.setAlignment(flags)
        self.text.edit.document().setDefaultTextOption(option)

    def set_align(self, name: str) -> None:
        editor = self.step_widget.editor
        editor.act("Change the alignment of conditions",
                   lambda: se.set_conditions_align(editor.project, editor.scheme,
                                                   self.step_widget.step_id, self.which, name),
                   rebuild=False)
        self.show_align()

    def paste_structure(self) -> None:
        from PySide6.QtGui import QGuiApplication
        source = QGuiApplication.clipboard().mimeData()
        editor = self.step_widget.editor
        if source is None or not editor.inline.wants(source):
            editor.say("⚠ The clipboard has no structure. Copy it in ChemDraw (Ctrl+C) "
                       "first.")
            return
        sid = editor.inline.paste(source)
        if sid:
            self.text.edit.insert_link("struct:" + sid)
            self.text.edit.setFocus()


class StepWidget(QFrame):
    """An arrow with its conditions above and under it."""

    def __init__(self, editor: "SchemeEditorWidget", step: dict, number: int):
        super().__init__(editor.strip)
        self.editor = editor
        self.step_id = step["id"]
        self.setFixedWidth(STEP_WIDTH)
        locked = editor.locked or editor.project.read_only
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        title = QLabel(f"Step {number}")
        title.setStyleSheet("color: palette(placeholder-text);")
        head.addWidget(title, 1)
        # (every widget gets its parent straight away: one shown before it is in a layout
        # would flash up as a little window of its own)
        self.remove_btn = QToolButton(self)
        self.remove_btn.setText("✕")
        self.remove_btn.setAutoRaise(True)
        self.remove_btn.setToolTip("Remove this step (its arrow, conditions and products)")
        self.remove_btn.clicked.connect(lambda: editor.remove_step(self.step_id))
        self.remove_btn.setVisible(not locked)
        head.addWidget(self.remove_btn)
        self.top = ConditionsBox(self, "top")
        self.bottom = ConditionsBox(self, "bottom")
        self.arrow = ArrowWidget(self)
        self.yields_note = QLabel(self)
        self.yields_note.setWordWrap(True)
        self.yields_note.setStyleSheet("color: #333;")
        # "+ Product" is under the step's last product (SPEC §13q.6a); here only while the
        # step has no product.
        self.add_btn = QPushButton("+ Product", self)
        self.add_btn.setToolTip("Add a product to this step")
        self.add_btn.clicked.connect(lambda: editor.add_box(self.step_id))
        self.add_btn.setVisible(not locked and not step["products"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addLayout(head)
        layout.addWidget(self.top)
        layout.addWidget(self.arrow)
        layout.addWidget(self.bottom)
        layout.addWidget(self.yields_note)
        layout.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self._timers = {}
        for box in (self.top, self.bottom):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(AUTOSAVE_MS)
            timer.timeout.connect(lambda b=box: self.save(b))
            box.changed.connect(timer.start)
            self._timers[box.which] = timer
        self.refresh_yields()

    @property
    def step(self) -> dict:
        return se.find_step(self.editor.scheme, self.step_id)

    def refresh_yields(self) -> None:
        """With yields under the conditions: show them there, as they will print."""
        editor = self.editor
        if editor.layout_values()["yields"] != "conditions":
            self.yields_note.hide()
            return
        report = editor.project.id_report()
        parts = []
        boxes = self.step["products"]
        for box in boxes:
            y = se.box_yield(editor.project, editor.scheme, box)
            if not y:
                continue
            compound = se.box_compound(editor.project, editor.scheme, box)
            label = report.display_id(compound.id) if compound is not None else ""
            parts.append(y + (f" ({label})" if label and len(boxes) > 1 else ""))
        self.yields_note.setText(", ".join(parts))
        self.yields_note.setVisible(bool(parts))

    def save(self, box: ConditionsBox) -> None:
        self._timers[box.which].stop()
        editor = self.editor
        if editor.project.read_only:
            return
        text = box.text.markup()
        try:
            if text == str(self.step.get(box.which) or ""):
                return
        except KeyError:   # (the step has gone, e.g. undone)
            return
        with editor.project.autosave():   # (typing: merged into one undo step)
            se.set_conditions(editor.project, editor.scheme, self.step_id, box.which, text)
        editor.saved.emit("Saved")

    def flush(self) -> None:
        for box in (self.top, self.bottom):
            if self._timers[box.which].isActive():
                self.save(box)


# ---------------------------------------------------------------------- the editor

class StripScroll(QScrollArea):
    """The scrolling strip: it asks for exactly its content's height (`need`), and can be
    made shorter by the page's divider (it then scrolls up and down too); its horizontal
    scroll bar is always at its bottom edge, so it stays reachable."""

    MIN_HEIGHT = 150
    need = 200
    on_resize = None   # called when its width changes (the scroll bar may come or go)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.on_resize is not None and event.size().width() != event.oldSize().width():
            self.on_resize()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().sizeHint().width(), self.need)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().minimumSizeHint().width(), min(self.need, self.MIN_HEIGHT))


class SchemeEditorWidget(QWidget):
    """The scheme editor on a scheme page."""

    saved = Signal(str)               # something was saved (status bar, live preview)
    compounds_changed = Signal(str)   # sub-compounds added/removed/reordered (cid to show)
    message = Signal(str)
    height_changed = Signal()         # the strip's content got taller or shorter
    layout_changed = Signal()         # its Layout… was changed (e.g. label and caption)

    def __init__(self, project, scheme, parent: QWidget | None = None,
                 own_bar: bool = True):
        """`own_bar`: its buttons above the strip; False: the scheme page puts `self.bar`
        (a FlowLayout with the buttons) in its own top row (SPEC §13q.6b)."""
        super().__init__(parent)
        self.project = project
        self.scheme = scheme
        self.locked = se.is_locked(scheme)
        self.folded = False   # the scheme page's ▸ Scheme: its buttons are hidden too
        self.inline = InlineStructures(self)
        self.boxes: list[BoxWidget] = []
        self.steps: list[StepWidget] = []

        self.add_sm_btn = QPushButton("+ Starting material", self)
        self.add_sm_btn.setToolTip("Add another starting material (they are joined by “+”)")
        self.add_sm_btn.clicked.connect(lambda: self.add_box(se.START))
        self.add_step_btn = QPushButton("+ Step", self)
        self.add_step_btn.setToolTip("Add a step at the end: an arrow with its conditions and "
                                     "a product. Each step starts from the products before it.")
        self.add_step_btn.clicked.connect(lambda: self.add_step())
        self.layout_btn = QPushButton("Layout…", self)
        self.layout_btn.setToolTip("Text size, alignment, arrow length, what happens when the "
                                   "scheme is too wide, scale, yields, and its label and "
                                   "caption — for this scheme (the defaults are in Project "
                                   "settings ▸ Schemes)")
        self.layout_btn.clicked.connect(self.edit_layout)
        self.save_btn = QPushButton("Save scheme", self)
        self.save_btn.setToolTip("Done with this scheme: hide the editing controls and lock "
                                 "the IDs (use Edit scheme to change it later)")
        self.save_btn.clicked.connect(lambda: self.set_locked(True))
        self.edit_btn = QPushButton("Edit scheme", self)
        self.edit_btn.setToolTip("Show the editing controls again")
        self.edit_btn.clicked.connect(lambda: self.set_locked(False))
        from .flow_layout import FlowLayout
        self.bar = bar = FlowLayout()
        for b in (self.add_sm_btn, self.add_step_btn, self.layout_btn, self.save_btn,
                  self.edit_btn):
            bar.addWidget(b)

        self.strip = QWidget()
        self.strip_layout = QHBoxLayout(self.strip)
        self.strip_layout.setContentsMargins(4, 4, 4, 4)
        self.strip_layout.setSpacing(4)
        self.scroll = StripScroll()
        self.scroll.setWidget(self.strip)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()   # (only when it has something to say: no empty line under it)
        self.scroll.on_resize = self._fit_height

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if own_bar:
            layout.addLayout(bar)
        layout.addWidget(self.scroll)
        layout.addWidget(self.status)
        self.rebuild()

    # ---- building

    def layout_values(self) -> dict:
        return se.scheme_layout(self.project.settings, self.scheme)

    def rebuild(self) -> None:
        """Lay the boxes and steps out again from scheme.json."""
        h = self.scroll.horizontalScrollBar().value()
        # (the ID label or yield being typed in keeps the keyboard in the new box)
        from PySide6.QtWidgets import QApplication
        focus = QApplication.focusWidget()
        keep = next(((b.box_id, name) for b in self.boxes
                     for name in ("label_edit", "yield_edit")
                     if focus is not None and focus is getattr(b, name)), None)
        while self.strip_layout.count():
            item = self.strip_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.boxes, self.steps = [], []
        data = se.editor_data(self.scheme)

        def plus():
            label = QLabel("+")
            f = label.font()
            f.setPointSizeF(f.pointSizeF() * 1.6)
            label.setFont(f)
            self.strip_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)

        def add_boxes(boxes: list[dict], where: str):
            for i, box in enumerate(boxes):
                if i:
                    plus()
                w = BoxWidget(self, box, where)
                self.boxes.append(w)
                self.strip_layout.addWidget(w, 0, Qt.AlignmentFlag.AlignTop)

        add_boxes(data["starting"], se.START)
        for n, step in enumerate(data["steps"], start=1):
            w = StepWidget(self, step, n)
            self.steps.append(w)
            self.strip_layout.addWidget(w, 0, Qt.AlignmentFlag.AlignTop)
            add_boxes(step["products"], step["id"])
        self.strip_layout.addStretch(1)
        self._show_buttons()
        if keep is not None:
            QTimer.singleShot(0, self, lambda: self._focus_field(*keep))
        self.line_up_ids()
        self._fit_height()
        # (measured again once the new boxes are laid out and the old ones are gone)
        QTimer.singleShot(0, self, self._fit_height)
        QTimer.singleShot(0, self, lambda: self.scroll.horizontalScrollBar().setValue(h))

    def _show_buttons(self) -> None:
        locked = self.locked or self.project.read_only
        shown = not self.folded
        for b in (self.add_sm_btn, self.add_step_btn):
            b.setVisible(shown and not locked)
        self.layout_btn.setVisible(shown)
        self.layout_btn.setEnabled(not self.project.read_only)
        self.save_btn.setVisible(shown and not self.locked and not self.project.read_only)
        self.edit_btn.setVisible(shown and self.locked and not self.project.read_only)

    def set_folded(self, folded: bool) -> None:
        """The scheme is folded away on its page: hide the buttons as well."""
        self.folded = folded
        self._show_buttons()

    def _focus_field(self, box_id: str, name: str) -> None:
        box = next((b for b in self.boxes if b.box_id == box_id), None)
        field = getattr(box, name, None) if box is not None else None
        if field is not None and field.isEnabled() and field.isVisible():
            field.setFocus()

    def _fit_height(self) -> None:
        """As tall as the boxes and steps need (no empty space under them, nothing cut
        off). Measured after the strip's layout is brought up to date."""
        self.strip_layout.invalidate()
        self.strip_layout.activate()
        # Room for the sideways scroll bar only when the boxes don't fit across.
        wide = self.strip.sizeHint().width() > self.scroll.width() - 2 * self.scroll.frameWidth()
        bar = self.scroll.horizontalScrollBar().sizeHint().height() if wide else 0
        need = (self.strip.sizeHint().height() + bar + 2 * self.scroll.frameWidth() + 2)
        if need != self.scroll.need or self.scroll.maximumHeight() != need:
            self.scroll.need = need
            self.scroll.setMaximumHeight(need)
            self.scroll.setMinimumHeight(min(need, StripScroll.MIN_HEIGHT))
            self.scroll.updateGeometry()
            self.updateGeometry()
            self.height_changed.emit()

    def refresh_boxes(self, redraw: bool = True) -> None:
        """Structures (`redraw`), IDs or yields changed elsewhere (a compound tab)."""
        for w in self.boxes:
            try:
                w.refresh(redraw)
            except KeyError:
                pass
        for s in self.steps:
            try:
                s.refresh_yields()
            except KeyError:
                pass
        self.line_up_ids()
        self._fit_height()

    def line_up_ids(self) -> None:
        """As printed with Layout ▸ "Line up compound IDs in each row": every ID under
        the tallest structure (the screen shows the scheme as one row)."""
        on = bool(self.layout_values()["align_ids"])
        tallest = max((max(b.canvas.struct_size.height(), 40) for b in self.boxes
                       if not b.canvas.pixmap.isNull()), default=0)
        for b in self.boxes:
            c = b.canvas
            want = tallest if on else 0
            if c.row_height != want:
                c.row_height = want
                c.update_size()
                c.update()

    def say(self, text: str) -> None:
        self.status.setText(text)
        if self.status.isHidden() == bool(text):
            self.status.setVisible(bool(text))
            self.height_changed.emit()

    def flush(self) -> None:
        for s in self.steps:
            s.flush()
        for w in self.boxes:
            if w.label_edit.hasFocus():
                w._label_typed()
            if w.yield_edit.hasFocus():
                w._yield_typed()

    def set_locked(self, locked: bool) -> None:
        if self.project.read_only:
            return
        self.flush()
        se.set_locked(self.project, self.scheme, locked)
        self.locked = locked
        self.rebuild()
        self.saved.emit("Scheme saved" if locked else "Scheme unlocked for editing")

    # ---- actions

    def act(self, label: str, fn, message: str = "Saved", rebuild: bool = True,
            compounds: bool = False, show: str = "") -> bool:
        """Do one change as one Edit ▸ Undo step; then show the scheme again."""
        if self.project.read_only:
            return False
        self.flush()
        ok = True
        try:
            with self.project.undo_step(f"{label} “{self.scheme.name}”"):
                fn()
        except chem.ChemError as e:
            self.say(f"⚠ {e} Nothing was changed.")
            ok = False
        except se.SchemeEditorError as e:
            self.say(f"⚠ {e}")
            ok = False
        except (ProjectError, OSError, KeyError) as e:
            QMessageBox.warning(self, "Could not change the scheme", str(e))
            ok = False
        if ok:
            self.say("")
        if rebuild:
            self.rebuild()
        self.saved.emit(message)
        if compounds:
            self.compounds_changed.emit(show)
        return ok

    def add_box(self, where: str) -> None:
        self.act("Add a structure to", lambda: se.add_box(self.project, self.scheme, where))

    def add_step(self) -> None:
        self.act("Add a step to", lambda: se.add_step(self.project, self.scheme))

    def move_box(self, box_id: str, delta: int) -> None:
        linked = bool(se.find_box(self.scheme, box_id)[0].get("compound"))
        self.act("Move a structure in", lambda: se.move_box(self.project, self.scheme, box_id,
                                                            delta), compounds=linked)

    def mark_compound(self, box_id: str) -> None:
        made = {}

        def do():
            made["c"] = se.link_new_compound(self.project, self.scheme, box_id)
        if self.act("Mark a product as a compound in", do):
            self.compounds_changed.emit(made["c"].id)
        else:
            self.compounds_changed.emit("")

    def link_existing(self, box_id: str, compound_id: str) -> None:
        self.act("Show a compound in", lambda: se.link_compound(
            self.project, self.scheme, box_id, compound_id), compounds=True, show=compound_id)

    def _ask_compound(self, compound, what: str) -> str | None:
        """"delete", "keep" or None (cancel): what happens to a compound page."""
        shown = f"{self.project.id_report().display_id(compound.id)} {compound.name or ''}"
        box = QMessageBox(self)
        box.setWindowTitle("Compound page")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"{what} The compound “{shown.strip()}” has its own data page.")
        box.setInformativeText(
            "Delete the page too? It goes to the project Trash and can be restored; the "
            "compounds after it are renumbered.\n\nOr keep the page: it stays on this scheme "
            "page (a tab below), just no longer drawn in the scheme.")
        delete = box.addButton("Delete the page", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep the page", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep)
        box.exec()
        clicked = box.clickedButton()
        return "delete" if clicked is delete else "keep" if clicked is keep else None

    def unmark_compound(self, box_id: str) -> None:
        box = se.find_box(self.scheme, box_id)[0]
        compound = se.box_compound(self.project, self.scheme, box)
        if compound is None:
            self.rebuild()
            return
        choice = self._ask_compound(compound, "This product will no longer be a compound.")
        if choice is None:
            self.rebuild()   # (the tick goes back)
            return
        self.act("Unmark a compound in", lambda: se.unlink_box(
            self.project, self.scheme, box_id, delete_compound=choice == "delete"),
            compounds=True)

    def remove_box(self, box_id: str) -> None:
        box = se.find_box(self.scheme, box_id)[0]
        compound = se.box_compound(self.project, self.scheme, box)
        delete = False
        if compound is not None:
            choice = self._ask_compound(compound, "This box will be removed from the scheme.")
            if choice is None:
                return
            delete = choice == "delete"
        self.act("Remove a structure from", lambda: se.remove_box(
            self.project, self.scheme, box_id, delete_compound=delete),
            compounds=compound is not None)

    def remove_step(self, step_id: str) -> None:
        compounds = se.step_compounds(self.project, self.scheme, step_id)
        delete = False
        if compounds:
            names = ", ".join(self.project.id_report().display_id(c.id) for c in compounds)
            box = QMessageBox(self)
            box.setWindowTitle("Remove step")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f"This step's products include compounds with their own data pages "
                        f"({names}).")
            box.setInformativeText(
                "Delete those pages too? They go to the project Trash and can be restored.\n\n"
                "Or keep them: they stay on this scheme page (tabs below), no longer drawn.")
            d = box.addButton("Delete the pages", QMessageBox.ButtonRole.DestructiveRole)
            k = box.addButton("Keep the pages", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(k)
            box.exec()
            if box.clickedButton() not in (d, k):
                return
            delete = box.clickedButton() is d
        self.act("Remove a step from", lambda: se.remove_step(
            self.project, self.scheme, step_id, delete_compounds=delete),
            compounds=bool(compounds))

    def edit_layout(self) -> None:
        dialog = SchemeLayoutDialog(self, self.project, self.scheme)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.act("Change the layout of", lambda: se.set_scheme_layout(
            self.project, self.scheme, values))
        self.layout_changed.emit()


# ---------------------------------------------------------------------- layout settings

class LayoutExample(QWidget):
    """A small picture of one step laid out with the settings (about printed size)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.values = dict(se.DEFAULT_LAYOUT)
        self.setMinimumSize(360, 120)

    def set_values(self, values: dict) -> None:
        self.values = values
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        v = self.values
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        p.setPen(QColor("#ccc"))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        mm = 3.2 * float(v["scale_pct"]) / 100   # pixels per mm in this example
        font = QFont(self.font())
        font.setPointSizeF(max(5.0, float(v["cond_size_pt"]) * float(v["scale_pct"]) / 100))
        p.setFont(font)
        m = QFontMetricsF(font)
        top = ["Pd(OAc)₂ (5 mol%)", "K₂CO₃"]
        bottom = ["DMF, 80 °C"]
        room = max(m.horizontalAdvance(t) for t in top + bottom)
        length = max(room + (float(v["arrow_before_mm"]) + float(v["arrow_after_mm"])) * mm,
                     float(v["arrow_min_mm"]) * mm)
        gap = float(v["gap_mm"]) * mm
        box = QSizeF(18 * mm, 13 * mm)
        total = box.width() * 2 + length + 2 * gap
        x = max(4.0, (self.width() - total) / 2)
        axis = self.height() / 2 - 6
        pen = QPen(QColor("#555"), 1, Qt.PenStyle.DashLine)

        def molecule(x0: float, name: str, label: str, yld: str):
            p.setPen(pen)
            p.drawRect(QRectF(x0, axis - box.height() / 2, box.width(), box.height()))
            p.setPen(QColor("black"))
            p.drawText(QRectF(x0, axis - box.height() / 2, box.width(), box.height()),
                       Qt.AlignmentFlag.AlignCenter, name)
            y = axis + box.height() / 2 + 2
            bold = QFont(font)
            bold.setBold(True)
            p.setFont(bold)
            p.drawText(QRectF(x0 - 20, y, box.width() + 40, m.height()),
                       Qt.AlignmentFlag.AlignCenter, label)
            p.setFont(font)
            if yld:
                p.drawText(QRectF(x0 - 20, y + m.height(), box.width() + 40, m.height()),
                           Qt.AlignmentFlag.AlignCenter, yld)

        molecule(x, "A", "1", "")
        ax = x + box.width() + gap
        p.setPen(QPen(QColor("black"), 1.2))
        p.drawLine(QPointF(ax, axis), QPointF(ax + length - 6, axis))
        p.setBrush(QColor("black"))
        p.drawPolygon(QPolygonF([QPointF(ax + length, axis), QPointF(ax + length - 8, axis - 3),
                                 QPointF(ax + length - 6, axis),
                                 QPointF(ax + length - 8, axis + 3)]))
        p.setBrush(Qt.BrushStyle.NoBrush)   # (else the product's box is filled black)
        x0 = ax + float(v["arrow_before_mm"]) * mm
        area = length - (float(v["arrow_before_mm"]) + float(v["arrow_after_mm"])) * mm
        x0 += (area - room) / 2
        flags = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
                 "right": Qt.AlignmentFlag.AlignRight}
        lines = list(bottom)
        if v["yields"] == "conditions":
            lines.append("92%")
        for i, t in enumerate(reversed(top)):
            p.drawText(QRectF(x0, axis - 3 - (i + 1) * m.height(), room, m.height()),
                       flags[v["top_align"]] | Qt.AlignmentFlag.AlignVCenter, t)
        for i, t in enumerate(lines):
            p.drawText(QRectF(x0, axis + 3 + i * m.height(), max(room, m.horizontalAdvance(t)),
                              m.height()),
                       flags[v["bottom_align"]] | Qt.AlignmentFlag.AlignVCenter, t)
        molecule(ax + length + gap, "B", "2a", "92%" if v["yields"] == "product" else "")


class LayoutForm(QWidget):
    """The scheme layout settings: Project settings ▸ Schemes, and a scheme's Layout…"""

    changed = Signal()

    def __init__(self, values: dict, parent: QWidget | None = None):
        super().__init__(parent)
        form = QFormLayout()

        def number(lo, hi, decimals, suffix, step):
            box = QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setDecimals(decimals)
            box.setSuffix(suffix)
            box.setSingleStep(step)
            box.valueChanged.connect(lambda _v: self._changed())
            return box

        def choice(options: list[tuple[str, str]]):
            box = QComboBox()
            for value, text in options:
                box.addItem(text, value)
            box.currentIndexChanged.connect(lambda _i: self._changed())
            return box

        L = se.LIMITS
        self.fields = {
            "cond_size_pt": number(*L["cond_size_pt"], 1, " pt", 0.5),
            "cond_structure_pct": number(*L["cond_structure_pct"], 0, " %", 5),
            "top_align": choice([(k, w) for k, w in ALIGN_WORDS.items()]),
            "bottom_align": choice([(k, w) for k, w in ALIGN_WORDS.items()]),
            "arrow_before_mm": number(*L["arrow_before_mm"], 1, " mm", 0.5),
            "arrow_after_mm": number(*L["arrow_after_mm"], 1, " mm", 0.5),
            "arrow_min_mm": number(*L["arrow_min_mm"], 1, " mm", 1),
            "gap_mm": number(*L["gap_mm"], 1, " mm", 0.5),
            "fit": choice([("wrap", "Continue on a new row (the row starts with the arrow)"),
                           ("shrink", "Shrink to fit one line")]),
            "scale_pct": number(*L["scale_pct"], 0, " %", 5),
            "yields": choice([("product", "Under each product"),
                              ("conditions", "Under that step's conditions"),
                              ("none", "Not printed")]),
            "align_ids": QCheckBox("Line up compound IDs in each row"),
            "show_label": QCheckBox("Print “Scheme S1.” and the scheme's name under it"),
        }
        for key in se.SWITCHES:
            self.fields[key].toggled.connect(lambda _on: self._changed())
        rows = [("cond_size_pt", "Conditions text size:",
                 "The size of the text above and under the arrows."),
                ("cond_structure_pct", "Structures in the conditions:",
                 "How big a structure pasted into the conditions prints, as a percentage of "
                 "its size in the scheme."),
                ("top_align", "Text above the arrow:", "Left, centred or right over the arrow."),
                ("bottom_align", "Text under the arrow:", "Left, centred or right under it."),
                ("arrow_before_mm", "Arrow before the text:",
                 "How far the arrow starts before the conditions text."),
                ("arrow_after_mm", "Arrow after the text:",
                 "How far the arrow (with its head) goes on after the conditions text."),
                ("arrow_min_mm", "Shortest arrow:",
                 "An arrow with little or no text is at least this long."),
                ("gap_mm", "Space between items:",
                 "Between structures, “+” signs and arrows."),
                ("fit", "Too wide for the page:",
                 "Continue on a new row, or shrink the whole scheme to fit one line."),
                ("scale_pct", "Scheme size:",
                 "The whole scheme (structures, text and arrows) at this percentage."),
                ("yields", "Yields:",
                 "Where each product's yield is printed (a compound's comes from its Yield "
                 "field unless one is typed on the scheme)."),
                ("align_ids", "IDs:",
                 "On: the IDs (and yields) of a row are printed on one line, under the "
                 "lowest structure of the row. Off: each ID right under its own structure. "
                 "IDs dragged on the scheme keep their offset."),
                ("show_label", "Label and caption:",
                 "Off (the default): the scheme is printed on its own, with no label or "
                 "caption, and is not numbered or listed in the List of Schemes. On: it is "
                 "numbered (Scheme S1…) with its name as the caption, and can be mentioned "
                 "in texts.")]
        for key, text, tip in rows:
            w = self.fields[key]
            w.setToolTip(tip)
            label = QLabel(text)
            label.setToolTip(tip)
            form.addRow(label, w)
        self.example = LayoutExample()
        note = QLabel("Example: one step at about the printed size.")
        note.setStyleSheet("color: palette(placeholder-text);")
        # The conditions' printed size (shrinking to fit can make it smaller still: Compile
        # SI then warns below MIN_TEXT_PT).
        self.size_note = QLabel()
        self.size_note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.size_note)
        layout.addWidget(note)
        layout.addWidget(self.example)
        self.set_values(values)

    def set_values(self, values: dict) -> None:
        for key, w in self.fields.items():
            w.blockSignals(True)
            value = values.get(key, se.DEFAULT_LAYOUT[key])
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(value)))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            else:
                w.setValue(float(value))
            w.blockSignals(False)
        self._changed()

    def values(self) -> dict:
        out = {}
        for key, w in self.fields.items():
            if isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            else:
                out[key] = w.currentData() if isinstance(w, QComboBox) \
                    else round(w.value(), 3)
        return out

    def _changed(self) -> None:
        values = self.values()
        self.example.set_values(values)
        printed = float(values["cond_size_pt"]) * float(values["scale_pct"]) / 100
        small = printed < se.MIN_TEXT_PT - 0.05
        self.size_note.setText(
            (f"⚠ The conditions print at {printed:.1f} pt: too small to read comfortably "
             f"(under {se.MIN_TEXT_PT:g} pt)." if small else
             f"The conditions print at {printed:.1f} pt")
            + ("" if small else
               " — smaller if the scheme is shrunk to fit the page (“Shrink to fit one "
               "line”, or a row too wide); Compile SI warns below "
               f"{se.MIN_TEXT_PT:g} pt."))
        self.size_note.setStyleSheet("color: #b36b00;" if small
                                     else "color: palette(placeholder-text);")
        self.changed.emit()


class SchemesTab(QWidget):
    """Project settings ▸ Schemes: the defaults for every scheme made with the editor."""

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        intro = QLabel("How schemes made with the scheme editor are laid out. Each scheme "
                       "can change these for itself (its Layout… button).")
        intro.setWordWrap(True)
        self.form = LayoutForm(se.project_layout(settings))
        reset = QPushButton("Reset to the built-in defaults")
        reset.clicked.connect(lambda: self.form.set_values(se.DEFAULT_LAYOUT))
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.form)
        layout.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()

    def apply(self, settings: dict) -> None:
        se.set_project_layout(settings, self.form.values())


class SchemeLayoutDialog(QDialog):
    """A scheme's own layout (Layout… on the scheme page)."""

    def __init__(self, parent: QWidget | None, project, scheme):
        super().__init__(parent)
        self.setWindowTitle(f"Layout of “{scheme.name}”")
        self.project = project
        intro = QLabel("These settings are for this scheme only. Anything left as in Project "
                       "settings ▸ Schemes follows the project's defaults.")
        intro.setWordWrap(True)
        self.form = LayoutForm(se.scheme_layout(project.settings, scheme))
        reset = QPushButton("Use the project's defaults")
        reset.clicked.connect(lambda: self.form.set_values(se.project_layout(project.settings)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.form)
        layout.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return self.form.values()
