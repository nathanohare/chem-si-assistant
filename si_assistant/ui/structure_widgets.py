"""Structure field and HRMS field editors (SPEC §7.1 fields 1 and 8, §8.1)."""

from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from ..compile.qt_images import read_image
from ..core import chem, checks
from ..core import fields as F
from ..core.project import STRUCTURE_IMAGE_EXTENSIONS, ProjectError
from .clipboard_image import PastedPicture, clipboard_picture, paste_dpi
from .field_widgets import FieldEditor, PartsEditor, get_part_value, set_part_value

STRUCTURE_FILE_EXTENSIONS = (".mol", ".sdf", ".sd", ".mdl")


def formula_html(formula: str) -> str:
    """"C8H11O3" -> C<sub>8</sub>H<sub>11</sub>O<sub>3</sub>; "[13C]" -> <sup>13</sup>C."""
    text = re.sub(r"\[(\d+)([A-Z][a-z]?)\]", r"<sup>\1</sup>\2", formula)
    return re.sub(r"(?<![\d>])(\d+)(?!</sup>)", r"<sub>\1</sub>", text)


_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def formula_plain(text: str) -> str:
    """"C12H12N+" -> "C₁₂H₁₂N⁺" for places that can't show rich text (dropdowns, hints)."""
    import re
    text = re.sub(r"\[(\d+)([A-Z][a-z]?)\]",
                  lambda m: m.group(1).translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
                  + m.group(2), text)
    body = re.sub(r"(?<=[A-Za-z)])(\d+)", lambda m: m.group(1).translate(_SUB), text)
    return body.replace("+", "⁺").replace("−", "⁻")


def mol_pixmap(mol, size: QSize, settings: "chem.DrawingSettings | None" = None) -> QPixmap:
    """The program's drawing of `mol`, filling `size`, in the project's drawing style
    (`settings`, from Project settings ▸ Structures)."""
    pixmap = QPixmap()
    pixmap.loadFromData(chem.structure_png(mol, size.width(), size.height(), settings), "PNG")
    return pixmap


def drawing_settings(project) -> "chem.DrawingSettings":
    return chem.DrawingSettings.from_settings(project.settings)


_picture_cache: OrderedDict[tuple, QPixmap] = OrderedDict()
PICTURE_CACHE_SIZE = 300  # small pictures (thumbnails, previews), a few MB in all


def picture_pixmap(path: Path, size: QSize) -> QPixmap:
    """A picture file shrunk to fit `size`, for showing on screen (a null QPixmap if it
    can't be read). Pasted pictures are large (1200 dpi), so the shrunk copy is kept
    until the file changes, instead of reading the whole picture every time."""
    try:
        st = Path(path).stat()
    except OSError:
        return QPixmap()
    key = (str(path), st.st_mtime_ns, st.st_size, size.width(), size.height())
    pixmap = _picture_cache.get(key)
    if pixmap is not None:
        _picture_cache.move_to_end(key)
        return pixmap
    image = read_image(Path(path))
    if image.isNull():
        return QPixmap()
    pixmap = QPixmap.fromImage(image.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
    del image
    _picture_cache[key] = pixmap
    while len(_picture_cache) > PICTURE_CACHE_SIZE:
        _picture_cache.popitem(last=False)
    return pixmap


def clipboard_structure_text() -> str:
    """Structure text from the clipboard: ChemDraw's MDLCT format, a MOL file, or plain text."""
    data = QGuiApplication.clipboard().mimeData()
    if data is None:
        return ""
    for fmt in data.formats():
        if "MDLCT" in fmt:
            raw = bytes(data.data(fmt))
            if raw:
                return chem.mdlct_to_molblock(raw)
        if "mdl-molfile" in fmt or "x-mdl" in fmt:
            raw = bytes(data.data(fmt))
            if raw:
                return raw.decode("latin-1")
    return data.text() if data.hasText() else ""


def clipboard_picture_for(project) -> PastedPicture | None:
    """The clipboard's picture (ChemDraw vector pictures are rendered at the project's
    "Pasted picture resolution", like PowerPoint's "Paste as Picture", and come with
    their vector original), or None if there is no picture."""
    return clipboard_picture(paste_dpi(project.settings))

# ---------------------------------------------------------------------- paste targets

HIGHLIGHT = "#2f80ed"


def paste_key() -> str:
    """"Ctrl+V" (or "⌘V" on a Mac). Only call this once the application is running:
    asking Qt for key names before then crashes on Windows."""
    return QKeySequence(QKeySequence.StandardKey.Paste).toString(
        QKeySequence.SequenceFormat.NativeText) or "Ctrl+V"


class PasteFrame(QFrame):
    """A frame around a paste target's content (an Attachment section's file, a compound's
    spectra): outlined in blue while it is selected for pasting."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PasteFrame")
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        border = f"3px solid {HIGHLIGHT}" if active else "1px solid transparent"
        self.setStyleSheet(f"QFrame#PasteFrame {{ border: {border}; }}")


class PasteTargetMixin:
    """Makes a field box a click-to-select paste target.

    Clicking anywhere in the box (picture, buttons, empty space) selects it: its
    picture gets a blue outline and the hint says "Ctrl+V pastes here". Ctrl+V
    then pastes into this box only. Right-click offers the same paste commands.
    Typing in a text box inside (e.g. SMILES) keeps normal text pasting.
    """

    def _init_paste_target(self, area: QWidget, menu: list[tuple[str, object]],
                           scope: QWidget | None = None) -> QLabel:
        """`scope` is the widget whose clicks select this paste target (default: self).
        Paste targets must not be nested, or Ctrl+V would be ambiguous."""
        self._paste_area = area
        self._paste_menu = menu
        self._paste_active = False
        self._paste_locked = False  # "saved": no Ctrl+V, no hint; right-click = Edit menu
        self._paste_scope = scope = scope or self
        scope.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        area.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.paste_hint = QLabel()
        shortcut = QShortcut(QKeySequence.StandardKey.Paste, scope)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._shortcut_paste)
        scope.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        scope.customContextMenuRequested.connect(self._show_paste_menu)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self._set_paste_active(False)
        return self.paste_hint

    def _on_focus_changed(self, _old, new) -> None:
        try:
            scope = self._paste_scope
            active = new is not None and (new is scope or scope.isAncestorOf(new))
            if active != self._paste_active:
                self._set_paste_active(active)
        except RuntimeError:  # this box has already been closed
            pass

    def _set_paste_active(self, active: bool) -> None:
        self._paste_active = active
        if hasattr(self._paste_area, "set_active"):
            self._paste_area.set_active(active)
        else:
            border = f"3px solid {HIGHLIGHT}" if active else "1px solid #999"
            self._paste_area.setStyleSheet(
                f"QLabel {{ background: white; color: #666; border: {border}; }}")
        self.paste_hint.setVisible(not self._paste_locked)
        if self.project.read_only:
            self.paste_hint.setText("")
        elif active:
            self.paste_hint.setText(f"▶ {paste_key()} pastes here")
            self.paste_hint.setStyleSheet(f"color: {HIGHLIGHT}; font-weight: bold;")
        else:
            self.paste_hint.setText(f"Click this box, then {paste_key()} to paste — or right-click")
            self.paste_hint.setStyleSheet("color: palette(placeholder-text);")

    def _shortcut_paste(self) -> None:
        if not self.project.read_only and not self._paste_locked:
            self.paste_from_clipboard()

    def set_paste_locked(self, locked: bool) -> None:
        self._paste_locked = locked
        self._set_paste_active(self._paste_active)

    def locked_menu(self, menu: QMenu) -> None:
        """Fill the right-click menu shown while locked (subclasses override)."""

    def _show_paste_menu(self, pos) -> None:
        if self.project.read_only:
            return
        self._paste_scope.setFocus()  # right-clicking also selects the box
        menu = QMenu(self)
        if self._paste_locked:
            self.locked_menu(menu)
        else:
            for text, slot in self._paste_menu:
                menu.addAction(text, slot)
        if not menu.isEmpty():
            menu.exec(self._paste_scope.mapToGlobal(pos))

    def paste_from_clipboard(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------- structure field

class StructureEditor(PasteTargetMixin, FieldEditor):
    # NMR nuclei added automatically (e.g. ["19F"]); the page rebuilds and recalculates.
    structure_changed = Signal(list)

    IMAGE_SIZE = QSize(300, 220)   # (so a scheme's compound tab fits beside the live preview)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(not self.project.read_only)

        self.image = QLabel()
        self.image.setFixedSize(self.IMAGE_SIZE)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = self._init_paste_target(self.image, [
            ("Paste structure (MOL / SMILES)", self._paste),
            ("Paste image (use a picture in the SI)", self._paste_image),
            ("Open MOL/SDF file…", self._open_file)])
        self.image_note = QLabel()
        self.image_note.setStyleSheet("color: palette(placeholder-text);")

        self.smiles = QLineEdit()
        self.smiles.setPlaceholderText("Type or paste SMILES and press Enter")
        self.smiles.returnPressed.connect(self._apply_smiles)

        buttons = QGridLayout()
        specs = [("Draw…", "Draw the structure (or edit this one) in the Ketcher editor",
                  self._draw),
                 ("Paste structure", "Paste SMILES or a MOL block from the clipboard "
                  "(in ChemDraw: select the structure and Copy, or Edit ▸ Copy As ▸ SMILES)",
                  self._paste),
                 ("Open MOL/SDF file…", "Read a .mol or .sdf file", self._open_file),
                 ("Copy SMILES", "Copy this structure as SMILES", self._copy_smiles),
                 ("Clear", "Remove the structure (it goes to the project Trash)", self._clear),
                 ("Paste image", "Paste a picture from the clipboard (e.g. copied from ChemDraw) "
                  "to use in the SI instead of the program's drawing. Formula and masses still "
                  "come from the structure.", self._paste_image),
                 ("Use own picture…", "Choose an image file as this structure's picture. "
                  "Formula and masses still come from the structure.", self._choose_image),
                 ("Remove picture", "Remove the picture (it goes to the project Trash) and "
                  "show the program's drawing", self._remove_image)]
        self.buttons: dict[str, QPushButton] = {}
        for i, (text, tip, slot) in enumerate(specs):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            # One column: two side by side made the page wider than the space beside the
            # live preview (1200x800).
            buttons.addWidget(b, i, 0)
            self.buttons[text] = b
        self.buttons["Copy SMILES"].setProperty("keep_enabled", True)
        self.controls = QWidget()
        buttons.setContentsMargins(0, 0, 0, 0)
        self.controls.setLayout(buttons)
        self.done_btn = QPushButton("Done editing")
        self.done_btn.setToolTip("Hide the editing buttons again")
        self.done_btn.clicked.connect(lambda: self.set_editing(False))

        # Once there is a structure, the buttons are tucked into this menu so the structure
        # isn't changed by accident (and the page is shorter).
        self.edit_btn = QToolButton()
        self.edit_btn.setText("Edit or Remove")
        self.edit_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.edit_menu = QMenu(self.edit_btn)
        self.edit_menu.aboutToShow.connect(lambda: self._fill_edit_menu(self.edit_menu))
        self.edit_btn.setMenu(self.edit_menu)
        self._editing = False

        self.info = QLabel()
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info.setWordWrap(True)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d35400;")
        self.error.hide()

        right = QVBoxLayout()
        right.addWidget(self.smiles)
        right.addWidget(self.controls)
        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.edit_btn)
        top_buttons.addWidget(self.done_btn)
        top_buttons.addStretch()
        right.addLayout(top_buttons)
        right.addWidget(self.info)
        right.addWidget(self.error)
        right.addStretch()
        left = QVBoxLayout()
        left.addWidget(hint)
        left.addWidget(self.image)
        left.addWidget(self.image_note)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(left)
        layout.addLayout(right, 1)
        self.refresh()

    # ---- display

    def refresh(self) -> None:
        mol = self.project.read_structure(self.compound)
        info = chem.mol_info(mol) if mol is not None else None
        override = self.project.structure_image_path(self.compound)
        if override is not None and override.exists():
            pixmap = picture_pixmap(override, self.IMAGE_SIZE - QSize(10, 10))
            if not pixmap.isNull():
                self.image.setPixmap(pixmap)
            else:
                self.image.setText(f"(cannot preview {override.name})")
            source = self.project.structure_picture_source(self.compound)
            who = "ChemDraw's picture" if source == "chemdraw" else "your own picture"
            self.image_note.setText(f"Showing {who}. When compiling the SI you can choose "
                                    "these pictures or ACS-style drawings.")
            self.image_note.setWordWrap(True)
        elif mol is not None:
            self.image.setPixmap(mol_pixmap(mol, self.IMAGE_SIZE, drawing_settings(self.project)))
            self.image_note.setText("Taken from the reaction scheme."
                                    if self.project.structure_from_scheme(self.compound) else "")
        else:
            self.image.setPixmap(QPixmap())
            self.image.setText("No structure yet.\n\nCopy it in ChemDraw, click here\n"
                               "and press Ctrl+V — or type SMILES,\nopen a .mol/.sdf file, "
                               "or drop one here.")
            self.image_note.setText("")
        self.smiles.setText(info.smiles if info else "")
        if info:
            charge = f" · net charge {info.charge:+d}" if info.charge else ""
            self.info.setText(
                f"<b>Formula</b> {formula_html(info.formula)} &nbsp; "
                f"<b>MW</b> {info.mw:.2f} &nbsp; <b>Exact mass</b> {info.exact_mass:.4f}{charge}")
        else:
            self.info.setText("")
        has_override = override is not None
        if not self.project.read_only:
            self.buttons["Clear"].setEnabled(mol is not None)
            self.buttons["Copy SMILES"].setEnabled(mol is not None)
            self.buttons["Remove picture"].setEnabled(has_override)
        self._apply_mode()

    # ---- compact ("saved") mode

    def has_content(self) -> bool:
        return (self.project.read_structure(self.compound) is not None
                or self.project.structure_image_path(self.compound) is not None)

    def set_editing(self, editing: bool) -> None:
        self._editing = editing
        self._apply_mode()

    def _apply_mode(self) -> None:
        """With a structure: compact (buttons in the Edit or Remove menu). Without one, or
        while editing: all buttons shown."""
        compact = self.has_content() and not self._editing and not self.project.read_only
        self.controls.setVisible(not compact and not self.project.read_only)
        self.edit_btn.setVisible(compact)
        self.done_btn.setVisible(self._editing and self.has_content())
        self.smiles.setReadOnly(compact or self.project.read_only)
        self.set_paste_locked(compact)

    def _fill_edit_menu(self, menu: QMenu) -> None:
        menu.clear()
        self.locked_menu(menu)

    def locked_menu(self, menu: QMenu) -> None:
        menu.addAction("Edit the drawing (Ketcher)…", self._draw)
        menu.addAction("Replace with the copied structure (MOL / SMILES)", self._paste)
        menu.addAction("Use the copied picture (image)", self._paste_image)
        menu.addAction("Open MOL/SDF file…", self._open_file)
        menu.addAction("Use own picture file…", self._choose_image)
        menu.addSeparator()
        menu.addAction("Copy SMILES", self._copy_smiles)
        menu.addSeparator()
        remove_pic = menu.addAction("Remove picture", self._remove_image)
        remove_pic.setEnabled(self.project.structure_image_path(self.compound) is not None)
        menu.addAction("Clear structure…", self._clear)
        menu.addSeparator()
        menu.addAction("Show all editing controls (type SMILES…)",
                       lambda: self.set_editing(True))

    def _show_error(self, message: str) -> None:
        self.error.setText(f"⚠ {message}")
        self.error.setVisible(bool(message))

    # ---- setting the structure

    def _undo_step(self, what: str):
        """One undo step for an action on the structure, e.g. "Change the structure of 2"."""
        shown = self.project.id_report().display_id(self.compound.id)
        return self.project.undo_step(f"{what} {shown}")

    def set_structure_text(self, text: str, picture: PastedPicture | None = None) -> None:
        self.before_write()  # save on-screen edits before set_structure writes compound.json
        try:
            with self._undo_step("Change the structure of"):
                if picture is not None:
                    added = self.project.set_structure(self.compound, text, picture=picture.png,
                                                       vector=picture.source,
                                                       vector_ext=picture.source_ext)
                else:
                    added = self.project.set_structure(self.compound, text)
        except chem.ChemError as e:
            self._show_error(f"{e} The structure was not changed.")
            return
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not save structure", str(e))
            return
        self._show_error("")
        self._editing = False  # back to compact once a structure is in
        self.refresh()
        self.structure_changed.emit(added)

    def _apply_smiles(self) -> None:
        text = self.smiles.text().strip()
        info = self.project.structure_info(self.compound)
        if text and (info is None or text != info.smiles):
            self.set_structure_text(text)

    def _paste(self) -> None:
        text = clipboard_structure_text()
        if not text.strip():
            self._show_error("The clipboard has no SMILES or MOL text.")
            return
        # ChemDraw copies a picture along with the structure: keep it for previews (and,
        # if chosen when compiling, for the SI).
        self.set_structure_text(text, picture=clipboard_picture_for(self.project))

    def paste_from_clipboard(self) -> None:
        """Ctrl+V: the copied structure if there is one (ChemDraw copies both the structure
        and a picture), otherwise a picture (e.g. a screenshot) as the image to use."""
        if clipboard_structure_text().strip():
            self._paste()
        else:
            self._paste_image()

    def _draw(self) -> None:
        """Draw in Ketcher, starting from the current structure if there is one."""
        from .ketcher_dialog import KetcherDialog, ensure_ketcher
        if self.project.read_only or not ensure_ketcher(self):
            return
        path = self.project.structure_path(self.compound)
        start = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        dlg = KetcherDialog(start, self)
        if not dlg.exec() or not dlg.molfile():
            return
        self.set_structure_text(dlg.molfile())
        if self.project.structure_image_path(self.compound) is not None and QMessageBox.question(
                self, "Picture",
                "This compound has its own picture, which may now differ from the drawing. "
                "Remove the picture so the SI uses the new structure?"
        ) == QMessageBox.StandardButton.Yes:
            self._remove_image()

    def _open_file(self) -> None:
        patterns = " ".join(f"*{e}" for e in STRUCTURE_FILE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Open a structure file", "",
                                              f"Structure files ({patterns})")
        if path:
            self._read_file(Path(path))

    def _read_file(self, path: Path) -> None:
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError as e:
            self._show_error(f"Could not read {path.name}: {e}")
            return
        self.set_structure_text(text)

    def _copy_smiles(self) -> None:
        if self.smiles.text():
            QGuiApplication.clipboard().setText(self.smiles.text())

    def _clear(self) -> None:
        if QMessageBox.question(self, "Clear structure",
                                "Remove this compound's structure? It will be moved to the "
                                "project Trash.") != QMessageBox.StandardButton.Yes:
            return
        self.before_write()
        try:
            with self._undo_step("Clear the structure of"):
                self.project.clear_structure(self.compound)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not clear structure", str(e))
        self.refresh()
        self.structure_changed.emit([])

    def _paste_image(self) -> None:
        picture = clipboard_picture_for(self.project)
        if picture is None:
            self._show_error("The clipboard has no picture. Copy the structure as a picture "
                             "first (in ChemDraw, select it and press Ctrl+C).")
            return
        self.before_write()
        try:
            with self._undo_step("Paste a picture of"):
                self.project.set_structure_image(self.compound, data=picture.png, ext=".png",
                                                 vector=picture.source,
                                                 vector_ext=picture.source_ext)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not use image", str(e))
        self._show_error("")
        self.refresh()
        self.structure_changed.emit([])

    def _choose_image(self) -> None:
        patterns = " ".join(f"*{e}" for e in STRUCTURE_IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a structure image", "",
                                              f"Images ({patterns})")
        if not path:
            return
        self.before_write()
        try:
            with self._undo_step("Use a picture file for"):
                self.project.set_structure_image(self.compound, Path(path))
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not use image", str(e))
        self.refresh()
        self.structure_changed.emit([])

    def _remove_image(self) -> None:
        self.before_write()
        try:
            with self._undo_step("Remove the picture of"):
                self.project.remove_structure_image(self.compound)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "Could not remove image", str(e))
        self.refresh()
        self.structure_changed.emit([])

    def store(self) -> None:
        pass  # the structure is saved the moment it changes

    # ---- drag and drop of .mol/.sdf files

    def _dropped(self, event) -> Path | None:
        if self.project.read_only or not event.mimeData().hasUrls():
            return None
        urls = [u for u in event.mimeData().urls() if u.isLocalFile()]
        if len(urls) == 1 and Path(urls[0].toLocalFile()).suffix.lower() in STRUCTURE_FILE_EXTENSIONS:
            return Path(urls[0].toLocalFile())
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._dropped(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._dropped(event)
        if path:
            event.acceptProposedAction()
            self._read_file(path)


# ---------------------------------------------------------------------- HRMS field

class HrmsEditor(PartsEditor):
    """HRMS inputs plus the calculated m/z and ppm error (SPEC §7.1 field 8, §8.1).

    M is normally the structure. For a salt, the user picks which piece (e.g. the
    cation) is M. Typing an M formula overrides both; the adduct is still applied.
    """

    MANUAL_STYLE = "QLineEdit { background: rgba(47, 128, 237, 45); }"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        adduct_box = self.widgets["adduct"]
        for i in range(adduct_box.count()):
            adduct_box.setItemText(i, chem.adduct_display(adduct_box.itemData(i)))
        value = self.compound.values.get(self.defn.key) or {}
        set_part_value(adduct_box, F.Part("adduct"), value.get("adduct", "[M+H]+"))
        adduct_box.setToolTip("Choose an adduct, or type your own, e.g. [M+Li]+, [2M+Na]+, "
                              "[M+H−H2O]+, [M+2H]2+")
        grid = self.layout()

        # Salt: which piece is M (hidden for ordinary structures).
        self.species_label = QLabel("M is:")
        self.species_box = QComboBox()
        self.species_box.setToolTip("This structure has more than one piece (e.g. a salt). "
                                    "Choose which one the m/z is calculated for.")
        self._species_key = str(value.get("species", ""))
        self.species_box.activated.connect(self._species_chosen)

        # Manual M formula.
        self.formula_edit = QLineEdit(str(value.get("formula", "")))
        self.formula_edit.setMaximumWidth(220)
        self.formula_edit.textEdited.connect(lambda *_: self.changed.emit())
        self.formula_preview = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.species_label)
        row.addWidget(self.species_box)
        row.addSpacing(12)
        row.addWidget(QLabel("M formula:"))
        row.addWidget(self.formula_edit)
        row.addWidget(self.formula_preview, 1)
        grid.addLayout(row, grid.rowCount(), 0, 1, -1)

        self.calc = QLabel()
        self.calc.setWordWrap(True)
        self.calc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.calc, grid.rowCount(), 0, 1, -1)
        self.changed.connect(self.refresh_calculation)
        self.refresh_calculation()

    def store(self) -> None:
        super().store()
        value = self.compound.values[self.defn.key]
        value["species"] = self._species_key
        value["formula"] = self.formula_edit.text().strip()

    def _species_chosen(self, index: int) -> None:
        self._species_key = self.species_box.itemData(index) or ""
        # A piece that is already charged (e.g. a pyridinium cation) is normally
        # reported as [M]+: fill that in (the adduct can still be changed afterwards).
        mol = self.project.read_structure(self.compound)
        sp = checks.hrms_species(mol, {"species": self._species_key})
        suggestion = checks.suggested_adduct(sp.info) if sp else None
        if suggestion:
            set_part_value(self.widgets["adduct"], F.Part("adduct"), suggestion)
        self.changed.emit()

    def _current_value(self) -> dict:
        return {"species": self._species_key, "formula": self.formula_edit.text()}

    def _fill_species(self, sp) -> None:
        choices = sp.choices if sp and sp.choices else None
        self.species_label.setVisible(bool(choices))
        self.species_box.setVisible(bool(choices))
        if not choices:
            return
        self.species_box.blockSignals(True)
        self.species_box.clear()
        auto = choices[1]
        self.species_box.addItem(f"Automatic: largest piece ({formula_plain(auto[1])})", "")
        for key, label, _info in choices:
            self.species_box.addItem(formula_plain(label), key)
        index = self.species_box.findData(self._species_key)
        self.species_box.setCurrentIndex(max(index, 0))
        self.species_box.blockSignals(False)

    def refresh_calculation(self) -> None:
        mol = self.project.read_structure(self.compound)
        auto = None
        try:
            auto = checks.hrms_species(mol, {"species": self._species_key})
        except chem.ChemError:
            pass
        self._fill_species(auto)
        manual = bool(self.formula_edit.text().strip())
        auto_formula = species_text = ""
        if auto is not None:
            auto_formula = auto.info.formula
            species_text = checks.species_label(auto.info)
        self.formula_edit.setPlaceholderText(f"auto: {formula_plain(species_text)}"
                                             if species_text else "e.g. C12H13NO")
        self.formula_edit.setToolTip(
            "Type M's formula to override the one from the structure (the adduct is still "
            "applied). Leave empty to use the structure." +
            (f"\nFrom the structure: {species_text}" if species_text else ""))
        self.formula_edit.setStyleSheet(self.MANUAL_STYLE if manual else "")

        try:
            sp = checks.hrms_species(mol, self._current_value())
        except chem.ChemError as e:
            self.formula_preview.setText(f"<span style='color:#d35400'>⚠ {e}</span>")
            self.calc.setText("")
            return
        if manual:
            differs = (f" (structure gives {formula_html(auto_formula)})"
                       if auto_formula and auto_formula != sp.info.formula else "")
            self.formula_preview.setText(f"✎ <b>manual</b>: {formula_html(sp.info.formula)}"
                                         f"{differs}")
        else:
            self.formula_preview.setText("")
        adduct = get_part_value(self.widgets["adduct"]) or "[M+H]+"
        if sp is None:
            self.calc.setText("<i>Add a structure (or type an M formula) to calculate the "
                              "expected m/z.</i>")
            return
        try:
            r = checks.hrms_calc(sp.info, adduct, get_part_value(self.widgets["found"]),
                                 float(self.project.settings.get("hrms_tolerance_ppm", 5.0)))
        except chem.ChemError as e:
            self.calc.setText(f"<span style='color:#d35400'>⚠ {e}</span>")
            return
        display = chem.adduct_display(adduct)
        shown = [p for p in r.peaks if p.abundance >= 5.0][:6]
        pattern = ", ".join(f"{p.mz:.4f} ({p.abundance:.1f}%)" for p in shown)
        mono = r.monoisotopic
        star = " ✎" if manual else ""
        text = (f"<b>Calcd</b> for {formula_html(mono.formula)}{star} {display}: "
                f"<b>{mono.mz:.4f}</b>"
                f"<br><span style='color:#888'>Isotope pattern (m/z): {pattern}</span>")
        hint = checks.charged_adduct_hint(sp.info, adduct)
        if hint:
            text += f"<br><span style='color:#d35400'>⚠ {hint}</span>"
        if r.found is not None:
            ppm_text = f"Δ = {r.ppm:+.1f} ppm"
            if r.out_of_tolerance:
                ppm_text = (f"<span style='color:#d35400'>⚠ {ppm_text} — outside your "
                            f"±{r.tolerance:g} ppm tolerance (Org. Lett. allows ±10 ppm)</span>")
            else:
                ppm_text = f"✔ {ppm_text}"
            peak = ""
            if r.matched is not mono:
                peak = (f" (compared with the {r.matched.mz:.4f} peak, "
                        f"{formula_html(r.matched.formula)})")
            text += f"<br>Found {r.found:.4f}{peak} &nbsp;·&nbsp; {ppm_text}"
        self.calc.setText(text)
