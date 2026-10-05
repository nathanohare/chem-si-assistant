"""Scheme page (SPEC §6.3.5): a reaction scheme picture with draggable compound ID
labels, and its compounds as tabs (sub-pages) underneath."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QSizePolicy, QTabBar, QToolButton, QVBoxLayout,
                               QWidget)

from ..compile.qt_images import read_image
from ..core import chem
from ..core.project import STRUCTURE_IMAGE_EXTENSIONS, Project, ProjectError, SchemePage
from ..core.scheme_layout import label_positions
from .compound_panel import CompoundPanel
from .flow_layout import FlowLayout
from .structure_widgets import (HIGHLIGHT, PasteTargetMixin, clipboard_picture_for,
                                clipboard_structure_text)


SCREEN_SIDE = 3000  # pixels: the scheme is shown (and searched for ink) at most this big


def ink_mask(path: Path) -> np.ndarray | None:
    """Dark pixels of a picture, as a boolean array (rows x columns). Large pictures are
    shrunk first (label placement only needs positions as fractions of the picture)."""
    image = read_image(path, SCREEN_SIDE)
    if image.isNull():
        return None
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h = gray.width(), gray.height()
    arr = np.frombuffer(gray.constBits(), np.uint8, count=gray.sizeInBytes())
    return arr.reshape(h, gray.bytesPerLine())[:, :w] < 160


def median_bond_length(mols) -> float:
    lengths = []
    for m in mols:
        pos = m.GetConformer().GetPositions()
        for b in m.GetBonds():
            lengths.append(float(np.linalg.norm(pos[b.GetBeginAtomIdx()] - pos[b.GetEndAtomIdx()])))
    return float(np.median(lengths)) if lengths else 1.0


# ---------------------------------------------------------------------- the picture

class SchemeCanvas(QWidget):
    """Shows the scheme picture with ID labels that can be dragged."""

    label_moved = Signal(str, float, float)  # compound id, x, y (fractions of the picture)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.pixmap: QPixmap | None = None
        self.labels: list[dict] = []  # {"id", "text", "x", "y", "manual", "placed"}
        self.placeholder = ""
        self.editable = True
        self.active = False
        self._drag: dict | None = None
        self.setMouseTracking(True)

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(800, 280)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.update()

    def _image_rect(self) -> QRectF:
        if self.pixmap is None or self.pixmap.isNull():
            return QRectF(self.rect().adjusted(6, 6, -6, -6))
        area = QRectF(self.rect().adjusted(8, 8, -8, -8))
        pw, ph = self.pixmap.width(), self.pixmap.height()
        s = min(area.width() / pw, area.height() / ph)
        w, h = pw * s, ph * s
        return QRectF(area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h)

    def _label_rect(self, label: dict) -> QRectF:
        r = self._image_rect()
        font = self._font()
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(font)
        w = fm.horizontalAdvance(label["text"]) + 10
        h = fm.height() + 4
        cx, cy = r.x() + label["x"] * r.width(), r.y() + label["y"] * r.height()
        return QRectF(cx - w / 2, cy - h / 2, w, h)

    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(max(9.0, self.font().pointSizeF() * 1.1))
        return f

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        p.setPen(QPen(QColor(HIGHLIGHT) if self.active else QColor("#999"), 3 if self.active else 1))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        if self.pixmap is None or self.pixmap.isNull():
            p.setPen(QColor("#666"))
            p.drawText(self.rect().adjusted(20, 10, -20, -10),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
            return
        p.drawPixmap(self._image_rect().toRect(), self.pixmap)
        p.setFont(self._font())
        for label in self.labels:
            rect = self._label_rect(label)
            unplaced = not label.get("placed", True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 236, 179, 230) if unplaced else QColor(232, 240, 254, 220))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor("#b06000") if unplaced else QColor("#1a3d7c"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label["text"])

    def _label_at(self, pos) -> dict | None:
        for label in reversed(self.labels):
            if self._label_rect(label).contains(QPointF(pos)):
                return label
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setFocus()
        if self.editable and event.button() == Qt.MouseButton.LeftButton:
            self._drag = self._label_at(event.position())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            r = self._image_rect()
            self._drag["x"] = min(max((event.position().x() - r.x()) / r.width(), 0.0), 1.0)
            self._drag["y"] = min(max((event.position().y() - r.y()) / r.height(), 0.0), 1.0)
            self._drag["placed"] = True
            self.update()
        else:
            over = self.editable and self._label_at(event.position()) is not None
            self.setCursor(Qt.CursorShape.OpenHandCursor if over else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            d, self._drag = self._drag, None
            self.label_moved.emit(d["id"], d["x"], d["y"])
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------- the page

class SchemePanel(PasteTargetMixin, QWidget):
    EMPTY_TEXT = ("No scheme yet. In ChemDraw, select the whole scheme and press Ctrl+C, "
                  "then click here and press Ctrl+V.\n\nThe compounds' IDs are placed on the "
                  "scheme automatically; drag any label to adjust it.")

    labels_changed = Signal()            # IDs/names may have changed: refresh the tree
    saved = Signal(str)
    compound_selected = Signal(str)      # tab changed: keep the tree selection in step
    view_spectra = Signal(str)           # a compound's "View spectra page": compound id

    def __init__(self, project: Project, scheme: SchemePage, user_name: Callable[[], str],
                 select_compound: str | None = None, collapsed: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.scheme = scheme
        self.user_name = user_name
        self.compound_panel: CompoundPanel | None = None
        ro = project.read_only

        self.name_edit = QLineEdit(scheme.name)
        f = self.name_edit.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        self.name_edit.setFont(f)
        self.name_edit.setReadOnly(ro)
        self.name_edit.editingFinished.connect(self._commit_name)
        # Next to the live preview the box can be narrow: keep room for a few words, show
        # the start of the name (not its end) and the whole name in the tooltip.
        self.name_edit.setMinimumWidth(self.name_edit.fontMetrics().averageCharWidth() * 16)
        self._show_name_start()
        # Kept short (the live preview pane shares the width); the rest is in the tooltip.
        kind = QLabel("Scheme page")
        kind.setToolTip("A scheme page has no ID of its own; its compounds take IDs from the "
                        "section's ID table")
        kind.setStyleSheet("color: palette(placeholder-text);")
        self.collapse_btn = QToolButton()
        self.collapse_btn.setCheckable(True)
        self.collapse_btn.setToolTip("Hide or show the scheme picture (more room for the "
                                     "compounds' data)")
        self.collapse_btn.toggled.connect(self.set_collapsed)
        # The label printed with the scheme (SPEC §13k.5a): automatic unless typed over.
        self.label_edit = QLineEdit(str(scheme.data.get("label", "")))
        self.label_edit.setReadOnly(ro)
        self.label_edit.setToolTip(
            "The label printed under the scheme (and wherever it is mentioned). Leave empty "
            "to number it automatically (Project settings ▸ Labels); its name is the caption.")
        self.label_edit.editingFinished.connect(self._commit_label)
        self._update_label_placeholder()
        top = QHBoxLayout()
        top.addWidget(self.collapse_btn)
        top.addWidget(self.name_edit, 1)
        top.addWidget(QLabel("Label:"))
        top.addWidget(self.label_edit)
        top.addWidget(kind)

        self.canvas = SchemeCanvas()
        self.canvas.editable = not ro
        self._shown: tuple | None = None   # which file self.canvas.pixmap was read from
        self.canvas.placeholder = self.EMPTY_TEXT
        self.canvas.label_moved.connect(self._label_moved)
        # The picture block (hint, picture, buttons, status) is the paste target; the
        # compound tab below has its own paste targets.
        self.picture_block = QWidget()
        hint = self._init_paste_target(self.canvas, [
            ("Paste scheme", self.paste_from_clipboard),
            ("Choose image file…", self._choose)], scope=self.picture_block)

        buttons = FlowLayout()   # (wraps onto a second line on a narrow page)
        self.buttons: dict[str, QPushButton] = {}
        for text, tip, slot in (
                ("Paste scheme", "Paste a scheme copied from ChemDraw (picture and structures)",
                 self.paste_from_clipboard),
                ("Choose image file…", "Use an image file of the scheme", self._choose),
                ("Auto-place labels", "Place the compound IDs under their structures again "
                 "(labels you dragged by hand are kept)", lambda: self.auto_place(False)),
                ("Remove picture", "Remove the scheme picture (it goes to the project Trash)",
                 self._remove)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setEnabled(not ro)
            b.clicked.connect(slot)
            buttons.addWidget(b)
            self.buttons[text] = b
        # "Save scheme": hide the paste controls and stop labels being dragged by accident.
        self.save_btn = QPushButton("Save scheme")
        self.save_btn.setToolTip("Done with this scheme: hide the paste controls and lock the "
                                 "labels (use Edit or Remove to change it later)")
        self.save_btn.clicked.connect(lambda: self.set_scheme_saved(True))
        buttons.addWidget(self.save_btn)
        self.edit_btn = QToolButton()
        self.edit_btn.setText("Edit or Remove")
        self.edit_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.edit_menu = QMenu(self.edit_btn)
        self.edit_menu.aboutToShow.connect(lambda: (self.edit_menu.clear(),
                                                    self.locked_menu(self.edit_menu)))
        self.edit_btn.setMenu(self.edit_menu)
        buttons.addWidget(self.edit_btn)
        self.status = QLabel()
        self.status.setWordWrap(True)

        # Sub-pages
        self.tabs = QTabBar()
        self.tabs.setTabsClosable(not ro)
        self.tabs.setMovable(not ro)
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.tabMoved.connect(self._tab_moved)
        self.add_btn = QToolButton()
        self.add_btn.setText("+ Add compound")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_btn.setEnabled(not ro)
        self.add_menu = QMenu(self.add_btn)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_btn.setMenu(self.add_menu)
        tab_row = QHBoxLayout()
        tab_row.addWidget(self.tabs, 1)
        tab_row.addWidget(self.add_btn)

        self.body = QVBoxLayout()
        self.empty = QLabel("No compounds in this scheme yet. Use “+ Add compound” — you can "
                            "start from a structure in the pasted scheme.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.empty)

        block = QVBoxLayout(self.picture_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.addWidget(hint)
        block.addWidget(self.canvas)
        block.addLayout(buttons)
        block.addWidget(self.status)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.picture_block)
        layout.addLayout(tab_row)
        layout.addLayout(self.body, 1)

        self._fill_tabs(select_compound)
        self.refresh_canvas()
        self.collapse_btn.setChecked(collapsed)
        self.set_collapsed(collapsed)

    # ------------------------------------------------------------------ collapse / save

    def set_collapsed(self, collapsed: bool) -> None:
        self.picture_block.setVisible(not collapsed)
        self.collapse_btn.blockSignals(True)
        self.collapse_btn.setChecked(collapsed)
        self.collapse_btn.blockSignals(False)
        self.collapse_btn.setText("▸ Scheme" if collapsed else "▾ Scheme")

    def is_saved(self) -> bool:
        return bool(self.scheme.data.get("saved"))

    def set_scheme_saved(self, saved: bool) -> None:
        if self.project.read_only:
            return
        self.scheme.data["saved"] = saved
        self.project.save_scheme(self.scheme)
        self._apply_saved()
        self.saved.emit("Scheme saved" if saved else "Scheme unlocked for editing")

    def _apply_saved(self) -> None:
        saved = self.is_saved() and self.project.scheme_image_path(self.scheme) is not None
        for b in self.buttons.values():
            b.setVisible(not saved)
        self.save_btn.setVisible(not saved and not self.project.read_only
                                 and self.project.scheme_image_path(self.scheme) is not None)
        self.edit_btn.setVisible(saved and not self.project.read_only)
        self.status.setVisible(not saved)
        self.canvas.editable = not saved and not self.project.read_only
        self.set_paste_locked(saved)

    def locked_menu(self, menu: QMenu) -> None:
        menu.addAction("Edit scheme (show paste controls, move labels)",
                       lambda: self.set_scheme_saved(False))
        menu.addAction("Replace with the copied scheme", self._replace_saved)
        menu.addSeparator()
        menu.addAction("Remove picture…", self._remove_confirmed)

    def _replace_saved(self) -> None:
        self.set_scheme_saved(False)
        self.paste_from_clipboard()

    def _remove_confirmed(self) -> None:
        if QMessageBox.question(self, "Remove scheme picture",
                                "Remove the scheme picture? It will be moved to the project "
                                "Trash.") == QMessageBox.StandardButton.Yes:
            self.set_scheme_saved(False)
            self._remove()

    # ------------------------------------------------------------------ name

    def _show_name_start(self) -> None:
        self.name_edit.setCursorPosition(0)
        self.name_edit.setToolTip(self.scheme.name)

    def _commit_name(self) -> None:
        name = self.name_edit.text().strip()
        if not self.name_edit.hasFocus():
            self.name_edit.setCursorPosition(0)   # (clicked away: show the start again)
        if self.project.read_only or name == self.scheme.name:
            return
        if not name:
            self.name_edit.setText(self.scheme.name)
            self._show_name_start()
            return
        self.project.rename_scheme(self.scheme.id, name)
        self.name_edit.setToolTip(self.scheme.name)
        self.labels_changed.emit()
        self.saved.emit("Saved")

    def _update_label_placeholder(self) -> None:
        from ..core.labels import compute
        labels = compute(self.project)
        item = labels.by_id.get(self.scheme.id)
        self.label_edit.setPlaceholderText(f"{item.auto} (automatic)" if item else "(automatic)")
        self.label_edit.setFixedWidth(self.label_edit.fontMetrics().horizontalAdvance(
            self.label_edit.placeholderText() + "M") + 16)   # (fits beside the live preview)
        duplicate = item is not None and labels.is_duplicate(item.label)
        self.label_edit.setStyleSheet("QLineEdit { color: #d35400; }" if duplicate else "")

    def _commit_label(self) -> None:
        label = self.label_edit.text().strip()
        if self.project.read_only or label == str(self.scheme.data.get("label", "")):
            return
        try:
            self.project.set_scheme_label(self.scheme.id, label)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save the label", str(e))
            return
        self._update_label_placeholder()
        self.labels_changed.emit()
        self.saved.emit("Saved")

    # ------------------------------------------------------------------ canvas and labels

    def _label_text(self, compound) -> str:
        return self.project.id_report().display_id(compound.id)

    def refresh_canvas(self) -> None:
        path = self.project.scheme_image_path(self.scheme)
        self.canvas.pixmap = self._scheme_pixmap(path)
        unreadable = path is not None and path.exists() and self.canvas.pixmap is None
        self.canvas.placeholder = (
            f"⚠ The scheme picture ({path.name}) could not be shown: it may be damaged or "
            "too large. Paste the scheme again, or choose another image file."
            if unreadable else self.EMPTY_TEXT)
        labels = []
        for i, child in enumerate(self.scheme.children):
            pos = self.scheme.labels.get(child.id)
            labels.append({"id": child.id, "text": self._label_text(child),
                           "x": pos["x"] if pos else (i + 1) / (len(self.scheme.children) + 1),
                           "y": pos["y"] if pos else 0.95, "placed": pos is not None})
        self.canvas.labels = labels if self.canvas.pixmap is not None else []
        self.canvas.update()
        if hasattr(self, "save_btn"):
            self._apply_saved()
        has = path is not None
        if not self.project.read_only:
            self.buttons["Remove picture"].setEnabled(has)
            self.buttons["Auto-place labels"].setEnabled(has and bool(self.scheme.children))

    def _scheme_pixmap(self, path: Path | None) -> QPixmap | None:
        """The scheme picture for the screen (at most SCREEN_SIDE pixels: a 1200 dpi scheme
        can be 100 megapixels). Read again only when the file changes, not on every
        label drag. None if there is no picture or it can't be read."""
        try:
            st = path.stat() if path is not None else None
        except OSError:
            st = None
        if st is None:
            self._shown = None
            return None
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key == self._shown and self.canvas.pixmap is not None:
            return self.canvas.pixmap
        image = read_image(path, SCREEN_SIDE)
        self._shown = key if not image.isNull() else None
        return QPixmap.fromImage(image) if not image.isNull() else None

    def _label_moved(self, cid: str, x: float, y: float) -> None:
        if self.project.read_only:
            return
        self.project.set_label_position(self.scheme, cid, x, y, manual=True)
        self.saved.emit("Label position saved")
        self.refresh_canvas()

    def auto_place(self, quiet: bool = True) -> None:
        """Put each compound's ID under its structure in the scheme. Labels dragged by hand
        are kept; compounds not found in the scheme get a spot along the bottom."""
        if self.project.read_only:
            return
        path = self.project.scheme_image_path(self.scheme)
        ink = ink_mask(path) if path is not None and path.exists() else None
        if ink is None:
            if path is not None and path.exists():
                self.status.setText("⚠ Could not read the scheme picture, so the labels were not "
                                    "placed. Paste the scheme again or choose another image file.")
            return
        pieces = self.project.scheme_fragments(self.scheme)
        matches = self.project.match_scheme_structures(self.scheme)
        positions, score = ([], 0.0)
        if pieces:
            positions, score = label_positions(
                ink, [m.GetConformer().GetPositions() for m in pieces], median_bond_length(pieces))
        placed = unmatched = 0
        trusted = pieces and score >= 0.8
        spare = [c for c in self.scheme.children if c.id not in matches or not trusted]
        for child in self.scheme.children:
            current = self.scheme.labels.get(child.id)
            if current and current.get("manual"):
                continue
            if trusted and child.id in matches:
                x, y = positions[matches[child.id]]
                placed += 1
            else:
                i = spare.index(child)
                x, y = (i + 1) / (len(spare) + 1), 0.95
                unmatched += 1
            self.project.set_label_position(self.scheme, child.id, x, y, manual=False)
        self.refresh_canvas()
        if not quiet or unmatched:
            if pieces and not trusted:
                msg = ("Could not line the copied structures up with the picture, so the labels "
                       "were put along the bottom — drag them into place.")
            elif unmatched:
                msg = (f"Placed {placed} label(s) under their structures. {unmatched} compound(s) "
                       "were not found in the scheme (or have no structure yet): their labels "
                       "are along the bottom, highlighted — drag them into place.")
            else:
                msg = f"Placed {placed} label(s) under their structures."
            self.status.setText(msg)

    # ------------------------------------------------------------------ pasting

    def paste_from_clipboard(self) -> None:
        if self.project.read_only:
            return
        image = clipboard_picture_for(self.project)
        structures = clipboard_structure_text()
        if image is None and not structures.strip():
            self.status.setText("⚠ The clipboard is empty. In ChemDraw, select the whole scheme "
                                "and press Ctrl+C first.")
            return
        self.flush()
        with self.project.undo_step(f"Paste the scheme of “{self.scheme.name}”"):
            self._paste(image, structures)

    def _paste(self, image, structures: str) -> None:
        messages = []
        try:
            if image is not None:
                self.project.set_scheme_picture(self.scheme, data=image.png, ext=".png",
                                                vector=image.source, vector_ext=image.source_ext)
            else:
                messages.append("⚠ The clipboard had structures but no picture; choose an "
                                "image file for the scheme.")
            count = 0
            if structures.strip():
                try:
                    count = self.project.set_scheme_structures(self.scheme, structures)
                except chem.ChemError:
                    count = 0
            if count:
                messages.append(f"Found {count} structure(s) in the scheme.")
                self._fill_first_structure()
            else:
                messages.append("Only a picture was pasted (no structures came with it): set "
                                "each compound's structure yourself, then drag the ID labels.")
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not paste scheme", str(e))
        self.status.setText(" ".join(messages))
        self.auto_place()
        self._reload_current()

    def _fill_first_structure(self) -> None:
        """A scheme with one compound that has no structure yet: give it the right-most one."""
        pieces = self.project.scheme_fragments(self.scheme)
        if len(self.scheme.children) == 1 and pieces:
            only = self.scheme.children[0]
            if self.project.read_structure(only) is None:
                self.project.set_structure(only, chem.mol_to_block(pieces[-1]), from_scheme=True)

    def _choose(self) -> None:
        patterns = " ".join(f"*{e}" for e in STRUCTURE_IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a scheme image", "",
                                              f"Images ({patterns})")
        if not path:
            return
        self.flush()
        with self.project.undo_step(f"Use a picture file for “{self.scheme.name}”"):
            try:
                self.project.set_scheme_picture(self.scheme, source=Path(path))
                self.status.setText("Scheme image attached. An image file carries no "
                                    "structures, so drag each compound's ID label into place.")
            except (ProjectError, OSError) as e:
                QMessageBox.warning(self, "Could not use image", str(e))
            self.auto_place()

    def _remove(self) -> None:
        self.flush()
        try:
            with self.project.undo_step(f"Remove the picture of “{self.scheme.name}”"):
                self.project.remove_scheme_picture(self.scheme)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove picture", str(e))
        self.status.setText("")
        self.refresh_canvas()

    # ------------------------------------------------------------------ sub-pages (tabs)

    def _fill_tabs(self, select: str | None = None) -> None:
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        report = self.project.id_report()
        for child in self.scheme.children:
            i = self.tabs.addTab(f"{report.display_id(child.id)}  {child.name or '(unnamed)'}")
            self.tabs.setTabData(i, child.id)
        self.tabs.blockSignals(False)
        ids = [c.id for c in self.scheme.children]
        index = ids.index(select) if select in ids else (0 if ids else -1)
        if index >= 0:
            self.tabs.setCurrentIndex(index)
        self._show_compound(ids[index] if index >= 0 else None)

    def _refresh_tab_texts(self) -> None:
        report = self.project.id_report()
        for i in range(self.tabs.count()):
            cid = self.tabs.tabData(i)
            try:
                c = self.project.compound(cid)
            except KeyError:
                continue
            self.tabs.setTabText(i, f"{report.display_id(cid)}  {c.name or '(unnamed)'}")

    def undo_context(self) -> tuple:
        """(merge key, label) of the undo step for this page's saves (SPEC §13k.11): the
        compound tab's while one of its fields was just edited, else the scheme page's."""
        panel = self.compound_panel
        if panel is not None and panel._changed_keys:
            return panel.undo_context()
        scheme = self.scheme
        return ("scheme", scheme.id), lambda: f"Edit scheme page “{scheme.name}”"

    def current_compound_id(self) -> str | None:
        i = self.tabs.currentIndex()
        return self.tabs.tabData(i) if i >= 0 else None

    def has_compound(self, cid: str) -> bool:
        return any(self.tabs.tabData(i) == cid for i in range(self.tabs.count()))

    def select_compound(self, cid: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == cid:
                self.tabs.setCurrentIndex(i)
                return

    def _tab_changed(self, index: int) -> None:
        self.flush()
        cid = self.tabs.tabData(index) if index >= 0 else None
        self._show_compound(cid)
        if cid:
            self.compound_selected.emit(cid)

    def _show_compound(self, cid: str | None) -> None:
        if self.compound_panel is not None:
            self.body.removeWidget(self.compound_panel)
            self.compound_panel.deleteLater()
            self.compound_panel = None
        self.empty.setVisible(cid is None)
        if cid is None:
            return
        panel = CompoundPanel(self.project, self.project.compound(cid), self.user_name)
        panel.labels_changed.connect(self._compound_labels_changed)
        panel.saved.connect(self.saved)
        panel.structure_updated.connect(lambda _cid: self._structure_updated())
        panel.view_spectra.connect(self.view_spectra)
        self.compound_panel = panel
        self.body.addWidget(panel, 1)

    def _reload_current(self) -> None:
        self._show_compound(self.current_compound_id())

    def _compound_labels_changed(self, _cid: str) -> None:
        self._refresh_tab_texts()
        self.refresh_canvas()
        self.labels_changed.emit()

    def _structure_updated(self) -> None:
        self.auto_place()

    def _fill_add_menu(self) -> None:
        self.add_menu.clear()
        self.add_menu.addAction("Blank compound", lambda: self.add_compound(None))
        pieces = self.project.scheme_fragments(self.scheme)
        if pieces:
            used = set(self.project.match_scheme_structures(self.scheme).values())
            self.add_menu.addSeparator()
            header = self.add_menu.addAction("From the pasted scheme (left → right):")
            header.setEnabled(False)
            for i, mol in enumerate(pieces):
                info = chem.mol_info(mol)
                text = f"{i + 1}. {info.formula}  ({info.smiles})"
                if i in used:
                    text += "  — already added"
                act = self.add_menu.addAction(text, lambda i=i: self.add_compound(i))
                act.setEnabled(i not in used)

    def add_compound(self, piece_index: int | None) -> None:
        self.flush()
        index = self.tabs.currentIndex() + 1 if self.tabs.currentIndex() >= 0 else None
        try:
            compound = self.project.add_compound(self.scheme.section_id, index,
                                                 scheme_id=self.scheme.id)
            if piece_index is not None:
                mol = self.project.scheme_fragments(self.scheme)[piece_index]
                self.project.set_structure(compound, chem.mol_to_block(mol), from_scheme=True)
        except (ProjectError, OSError, chem.ChemError) as e:
            QMessageBox.warning(self, "Could not add compound", str(e))
            return
        self.auto_place()
        self._fill_tabs(compound.id)
        self.labels_changed.emit()
        if self.compound_panel is not None:
            self.compound_panel.focus_field("name")

    def _close_tab(self, index: int) -> None:
        cid = self.tabs.tabData(index)
        compound = self.project.compound(cid)
        if QMessageBox.question(
                self, "Delete compound",
                f"Delete “{self.project.id_report().display_id(cid)} "
                f"{compound.name or '(unnamed)'}” from this scheme?\n\nIt will be moved to the "
                "project Trash and can be restored. Compounds after it will be renumbered."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.flush()
        self._show_compound(None)
        try:
            self.project.delete_compound(cid)
            self.scheme.labels.pop(cid, None)
            self.project.save_scheme(self.scheme)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not delete", str(e))
        self._fill_tabs()
        self.refresh_canvas()
        self.labels_changed.emit()

    def _tab_moved(self, _from: int, to: int) -> None:
        cid = self.tabs.tabData(to)
        try:
            self.project.move_compound(cid, self.scheme.section_id, to, self.scheme.id)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not reorder", str(e))
        self._refresh_tab_texts()
        self.refresh_canvas()
        self.labels_changed.emit()

    # ------------------------------------------------------------------ saving

    def flush(self) -> None:
        self._commit_name()
        self._commit_label()
        if self.compound_panel is not None:
            self.compound_panel.flush()
